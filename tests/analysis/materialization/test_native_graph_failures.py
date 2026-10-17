"""Graph failure atomicity and source errors without an execution clock."""

import pandas as pd
import pytest

import marivo.semantic as ms
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    "phase", ("source_execute", "query_admitted", "before_commit", "after_commit")
)
@pytest.mark.parametrize("interruption", (False, True))
def test_source_and_python_failures_preserve_atomic_publication(
    phase: str,
    interruption: bool,
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    logical = session.read(
        session.members(ms.ref.entity("sales.order")),
        {"value": session.catalog.fields.get("sales.order.amount")},
    )
    prior = logical.execute()
    prior_frame = prior.to_pandas()
    runtime = session._runtime
    with runtime.store._connection() as connection:
        before = connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0]

    def fail() -> None:
        if interruption:
            raise KeyboardInterrupt("injected caller interruption")
        raise TimeoutError("injected native timeout")

    if phase == "source_execute":

        def failed_execute(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            del owner, read
            fail()
            raise AssertionError("injected source failure unexpectedly returned")

        monkeypatch.setattr(SourceSession, "execute", failed_execute)
    else:

        def event(point: str) -> None:
            if point == phase:
                fail()

        runtime._hook = event
    try:
        if phase == "after_commit":
            committed = logical.execute()
            assert committed.to_pandas().equals(prior_frame)
        else:
            with pytest.raises(KeyboardInterrupt if interruption else MaterializationError):
                logical.execute()
    finally:
        runtime._hook = None
    assert prior.to_pandas().equals(prior_frame)
    assert runtime.store.resources(session.id) == ()
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[
            0
        ] == before + (phase == "after_commit")


def test_graph_has_no_hidden_600_second_expiry(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    case = analysis_dsl_case_factory("j2")
    logical = case.session.read(
        case.session.members(ms.ref.entity("sales.order")),
        {"value": case.session.catalog.fields.get("sales.order.amount")},
    )
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 700
        return ticks[0]

    execute = SourceSession.execute

    def delayed(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        time.monotonic()
        result = execute(owner, read)
        time.monotonic()
        return result

    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(SourceSession, "execute", delayed)
    result = logical.execute()
    assert ticks[0] > 600
    assert len(result.to_pandas()) > 0
    assert case.session._runtime.store.resources(case.session.id) == ()
