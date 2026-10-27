"""Runless evaluation errors and exact publication recovery boundaries."""

from typing import NoReturn

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import frame_evaluation, terminal_publication
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCase, DslCaseFactory, catalog_value, terminal_runs
from tests.support.execution_logs import execution_records


def _members(case: DslCase) -> mv.AnalysisFrame:
    return case.session.frame(
        case.session.members(
            catalog_value(
                case.session.catalog, f"{case.names.domain}.{case.names.customer}", kind="entity"
            )
        )
    )


def _result_files(case: DslCase) -> tuple[str, ...]:
    root = case.session._runtime.store.layout.session_dir(case.session.id)
    return tuple(
        sorted(
            str(path.relative_to(root))
            for parent in (root / "runs", root / "artifacts")
            for path in parent.rglob("*")
            if path.is_file()
        )
    )


def test_member_construction_is_lazy_and_execution_retains_complete_keys(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j1")

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Member construction read business rows")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "execute", forbidden)
        logical = _members(case)
        logical.show()
        assert terminal_runs(case.session) == ()
    captured = logical.compute()
    frame = captured.to_pandas()
    assert set(frame.iloc[:, 0]) == {"A", "B", "C", "D"}
    submitted = [
        record for record in execution_records(case.root) if record["event"] == "query.submitted"
    ]
    assert submitted
    import sqlglot

    assert all(
        sqlglot.parse_one(str(record["sql"]), read="duckdb").args.get("limit") is None
        for record in submitted
    )
    capsys.readouterr()
    captured.show(n=1)
    preview = capsys.readouterr().out
    assert "rows: 4" in preview and ".to_pandas()" in preview
    assert len(frame) == 4
    assert terminal_runs(case.session) == ()
    saved = captured.persist()
    pd.testing.assert_frame_equal(saved.to_pandas(), frame)
    assert len(terminal_runs(case.session)) == 1


def test_source_failure_retries_have_no_publication_runs(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)
    files_before = _result_files(case)

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("synthetic source failure")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "execute", fail)
        for operation in (logical.compute, logical.persist):
            with pytest.raises(MaterializationError) as caught:
                operation()
            assert isinstance(caught.value.__cause__, RuntimeError)
            assert not hasattr(caught.value, "run_ref")
            assert caught.value.expected and caught.value.received and caught.value.repair
            assert terminal_runs(case.session) == ()
            assert _result_files(case) == files_before
            assert terminal_runs(case.session, status="succeeded") == ()
            assert case.session._runtime.store.resources(case.session.id) == ()

    original = MaterializationError(
        expected="the exact synthetic source contract",
        received="an incompatible source",
        repair="Restore the exact synthetic source contract.",
        stage="graph_check",
    )
    repair = original.repair

    def typed(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(SourceSession, "execute", typed)
    with pytest.raises(MaterializationError) as caught:
        logical.compute()
    assert caught.value is original
    assert original.expected == "the exact synthetic source contract"
    assert original.received == "an incompatible source" and original.repair is repair
    assert not hasattr(original, "run_ref") and terminal_runs(case.session) == ()
    assert _result_files(case) == files_before


def test_publication_failure_retries_keep_internal_records_and_direct_repairs(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    fixed = _members(case).compute()
    before = _result_files(case)
    original = RuntimeError("synthetic publication failure")

    def event(point: str) -> None:
        if point == "publication_admitted":
            raise original

    monkeypatch.setattr(terminal_publication, "_event", event)
    ids: list[str] = []
    for _ in range(2):
        with pytest.raises(MaterializationError) as caught:
            fixed.persist()
        error = caught.value
        assert error.__cause__ is original
        assert not hasattr(error, "run_ref")
        record = terminal_runs(case.session)[0]
        ids.append(record.run_ref)
        assert record.lifecycle == "failed"
        assert error.repair is not None
        assert f"mv.session.resume({case.session.id!r})" in error.repair.action
        assert record.run_ref not in str(error)
        assert terminal_runs(case.session, status="succeeded") == ()
        assert case.session._runtime.store.resources(case.session.id) == ()
        assert _result_files(case) == before
    assert len(set(ids)) == 2
    mv.session.resume(case.session.id)
    assert {run.run_ref for run in terminal_runs(case.session, status="failed")} == set(ids)


def test_real_pairing_check_failure_has_no_publication_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j2").session
    members = session.members(session.catalog.get("entity:sales.customer").value)
    region = session.read(
        members, {"region": catalog_value(session.catalog, "sales.customer.region", kind="field")}
    )
    chosen = region.where(region["region"] == "east")
    selected = session.population_from(
        chosen, on=((chosen.axes[0], session.catalog.get("entity:sales.customer").value),)
    )
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    metric = session.catalog.get("metric:sales.revenue").value
    current = session.observe({"value": metric}, by=mv.product(scope, selected))
    baseline = session.observe({"value": metric}, by=mv.product(scope, members))
    with pytest.raises(AnalysisError) as caught:
        current.compare(
            {"base": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
        ).persist()
    assert caught.value.expected and caught.value.received
    assert not hasattr(caught.value, "run_ref")
    assert terminal_runs(session) == () and terminal_runs(session, status="succeeded") == ()
    assert session._runtime.store.resources(session.id) == ()


def test_evaluation_failure_never_inherits_previous_publication(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)
    previous = logical.persist()
    before = terminal_runs(case.session)
    for typed in (False, True):
        original = (
            IntegrityError(
                expected="a valid Frame plan",
                received="an invalid plan",
                repair="Rebuild the Frame.",
            )
            if typed
            else ValueError("synthetic evaluation failure")
        )

        def fail(*args: object, _original: BaseException = original, **kwargs: object) -> NoReturn:
            raise _original

        with monkeypatch.context() as patch:
            patch.setattr(frame_evaluation, "plan_frame", fail)
            with pytest.raises(type(original)) as caught:
                logical.persist()
        assert caught.value is original
        if isinstance(caught.value, AnalysisError):
            assert not hasattr(caught.value, "run_ref")
            assert "session.get_run(" not in str(caught.value)
        assert terminal_runs(case.session) == before
        pd.testing.assert_frame_equal(
            case.session.persisted_frame(previous.artifact_id).to_pandas(), previous.to_pandas()
        )


def test_recovery_error_is_preserved_without_publication_identity(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    fixed = _members(case).compute()
    fixed.persist()
    original = RecoveryPendingError(
        expected="an original publication acknowledgement",
        received="unconfirmed acknowledgement",
        repair="Resume the owning Session before explicitly resubmitting.",
    )

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(terminal_publication, "reconcile", fail)
    monkeypatch.setattr(frame_evaluation, "evaluate_frame", fail)
    with pytest.raises(RecoveryPendingError) as caught:
        fixed.persist()
    assert caught.value is original
    assert not hasattr(original, "run_ref")
    assert len(terminal_runs(case.session)) == 1


def test_control_exception_keeps_type_and_closes_source_without_run(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _members(case)
    original = KeyboardInterrupt("synthetic interruption")

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(SourceSession, "execute", fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        logical.persist()
    assert caught.value is original
    assert (
        terminal_runs(case.session) == () and terminal_runs(case.session, status="succeeded") == ()
    )
    assert case.session._runtime.store.resources(case.session.id) == ()
