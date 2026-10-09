"""Public member execution and exact Run recovery across failure boundaries."""

from typing import NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_publication
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.execution_logs import execution_records

pytestmark = pytest.mark.runtime


def _members(case: DslCase) -> mv.LogicalAnalysisDomain:
    return case.session.members(ms.ref.entity(f"{case.names.domain}.{case.names.customer}"))


def test_member_construction_is_lazy_and_execution_retains_complete_keys(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j1")

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Member construction read business rows")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "batches", forbidden)
        logical = _members(case)
        logical.contract().show()
        assert case.session.runs().items == ()
    captured = logical.execute()
    frame = captured.to_pandas()
    assert set(frame["member"]) == {"A", "B", "C", "D"}
    submitted = [
        record for record in execution_records(case.root) if record["event"] == "query.submitted"
    ]
    assert submitted
    assert all("LIMIT" not in str(record["sql"]).upper() for record in submitted)
    capsys.readouterr()
    captured.show(n=1)
    assert "omitted" in capsys.readouterr().out
    assert len(frame) == 4
    assert len(case.session.runs().items) == 1


def test_source_failure_retries_have_exact_readable_runs_and_retained_history(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("synthetic source failure")

    monkeypatch.setattr(SourceSession, "batches", fail)
    run_ids: list[str] = []
    for _ in range(2):
        with pytest.raises(MaterializationError) as caught:
            logical.execute()
        error = caught.value
        assert isinstance(error.__cause__, RuntimeError)
        assert error.stage == "graph_source"
        assert error.run_ref is not None
        run_ids.append(error.run_ref)
        assert case.session.get_run(error.run_ref).lifecycle == "failed"
        assert error.repair is not None
        assert error.repair.help_target.canonical_id == "session.get_run"
        assert "create a new Run" in error.repair.action
        for text in (str(error), render_help_text(error)[0]):
            assert error.run_ref in text
            inspection = next(
                line.strip().removeprefix("Inspect: ")
                for line in text.splitlines()
                if line.strip().startswith("Inspect: ")
            )
            exec(inspection, {"session": case.session})
            assert error.run_ref in capsys.readouterr().out
    assert len(set(run_ids)) == 2
    assert case.session.graph().artifacts == ()
    assert case.session._runtime.store.resources(case.session.id) == ()
    mv.session.abandon_run(session_id=case.session.id, run_id=run_ids[0])
    assert {run.run_id for run in case.session.runs(status="failed").items} == set(run_ids)


def test_real_identity_check_failure_is_bound_to_its_failed_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("missing_key")
    with pytest.raises(MaterializationError) as caught:
        _members(case).execute()
    error = caught.value
    assert error.stage == "graph_check"
    assert error.expected == "complete non-null Entity identity"
    assert error.run_ref is not None
    assert case.session.get_run(error.run_ref).lifecycle == "failed"
    assert case.session.graph().artifacts == ()
    assert case.session._runtime.store.resources(case.session.id) == ()


def test_typed_execution_failure_keeps_original_fields_and_repair(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)
    original = MaterializationError(
        expected="the exact synthetic source contract",
        received="an incompatible source",
        repair="Restore the exact synthetic source contract.",
        stage="graph_check",
        help_target="dsl.LogicalAnalysisDomain.execute",
    )
    repair = original.repair

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(SourceSession, "batches", fail)
    with pytest.raises(MaterializationError) as caught:
        logical.execute()
    assert caught.value is original
    assert original.expected == "the exact synthetic source contract"
    assert original.received == "an incompatible source"
    assert original.repair is repair
    assert original.run_ref is not None
    assert case.session.get_run(original.run_ref).lifecycle == "failed"


@pytest.mark.parametrize("typed", (False, True))
def test_pre_admission_failure_never_inherits_the_previous_run(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    typed: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)
    previous = logical.execute()
    before = case.session.runs().items
    original = (
        IntegrityError(
            expected="a valid graph", received="invalid graph", repair="Rebuild the graph."
        )
        if typed
        else ValueError("synthetic admission failure")
    )

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(graph_publication, "graph_document", fail)
    with pytest.raises(AnalysisError) as caught:
        logical.execute()
    error = caught.value
    assert error.run_ref is None
    assert "session.get_run(" not in str(error)
    assert "session.get_run(" not in render_help_text(error)[0]
    assert case.session.runs().items == before
    assert case.session._runtime.last_run_ref == previous.state.producing_run_ref
    if typed:
        assert error is original
    else:
        assert error.__cause__ is original


def test_pre_admission_recovery_failure_preserves_its_original_run(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)
    previous = logical.execute()
    original = RecoveryPendingError(
        expected="an original publication acknowledgement",
        received="unconfirmed acknowledgement",
        repair="Inspect the original Run without replaying computation.",
        run_ref=previous.state.producing_run_ref,
    )

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(graph_publication, "reconcile_session", fail)
    with pytest.raises(RecoveryPendingError) as caught:
        logical.execute()
    assert caught.value is original
    assert original.run_ref == previous.state.producing_run_ref
    assert len(case.session.runs().items) == 1


@pytest.mark.parametrize("kind", (KeyboardInterrupt, SystemExit))
def test_control_exception_keeps_its_type_and_cleans_the_admitted_run(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    kind: type[BaseException],
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)
    original = kind("synthetic interruption")

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(SourceSession, "batches", fail)
    with pytest.raises(kind) as caught:
        logical.execute()
    assert caught.value is original
    runs = case.session.runs().items
    assert len(runs) == 1 and runs[0].lifecycle == "failed"
    assert case.session.graph().artifacts == ()
    assert case.session._runtime.store.resources(case.session.id) == ()
