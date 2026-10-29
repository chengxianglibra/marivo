"""Graph failure atomicity and source errors without an execution clock."""

import json
import os
import subprocess
import sys

import pandas as pd
import pytest

from marivo.analysis.materialization import terminal_publication
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.shared_fixtures import DslCaseFactory, catalog_value, terminal_runs
from tests.support.paths import PROJECT_ROOT


@pytest.mark.parametrize(
    "phase", ("source_execute", "publication_admitted", "before_commit", "after_commit")
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
        session.members(session.catalog.get("entity:sales.order")),
        {"value": catalog_value(session.catalog, "sales.order.amount", kind="field")},
    )
    prior = logical.persist()
    prior_frame = prior.to_pandas()
    runtime = session._runtime
    before_runs = terminal_runs(session)
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
        logical = logical.compute()

        def event(point: str) -> None:
            if point == phase:
                fail()

        monkeypatch.setattr(terminal_publication, "_event", event)
    try:
        if phase == "after_commit":
            committed = logical.persist()
            assert committed.to_pandas().equals(prior_frame)
        else:
            with pytest.raises(KeyboardInterrupt if interruption else MaterializationError):
                logical.persist()
    finally:
        monkeypatch.undo()
    runs = terminal_runs(session)
    if phase == "source_execute":
        assert runs == before_runs
    else:
        assert len(runs) == len(before_runs) + 1
        added = [run for run in runs if run.run_ref not in {old.run_ref for old in before_runs}]
        assert len(added) == 1
        assert added[0].lifecycle == ("succeeded" if phase == "after_commit" else "failed")
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
        case.session.members(case.session.catalog.get("entity:sales.order")),
        {"value": catalog_value(case.session.catalog, "sales.order.amount", kind="field")},
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
    result = logical.compute()
    assert terminal_runs(case.session) == ()
    assert ticks[0] > 600
    assert len(result.to_pandas()) > 0
    assert case.session._runtime.store.resources(case.session.id) == ()


def test_fresh_worker_contender_only_rejects_publication(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    saved = case.session.observe(
        {"value": case.session.catalog.get("metric:sales.revenue")},
        by=(case.session.time_scope(start="2026-08-01", end="2026-09-01"),),
    ).persist()
    store = case.session._runtime.store
    with session_writer_guard(store.layout.lock_path(case.session.id), session_ref=case.session.id):
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "tests.analysis.materialization.concurrency_worker",
                "contend",
                str(case.root),
                "--session",
                case.session.id,
                "--metric",
                "sales.revenue",
                "--artifact",
                saved.artifact_id,
            ],
            cwd=PROJECT_ROOT,
            env={**os.environ, "MARIVO_TELEMETRY": "off"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(process.stdout.splitlines()[-1])
    assert report["session"] == case.session.id
    assert report["pid"] != os.getpid()
    assert report["artifact"] == saved.artifact_id
    assert report["before"] == report["after"]
    assert len(terminal_runs(case.session)) == 1
    assert store.resources(case.session.id) == ()
    assert saved.to_pandas()["value"].tolist() == [1000]
