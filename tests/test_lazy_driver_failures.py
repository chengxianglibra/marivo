"""Driver screening fails atomically across local guards and publication boundaries."""

from __future__ import annotations

import os
import signal
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization import local_worker
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import LocalPolicy
from tests.lazy_driver_runtime_fixtures import CHANNEL, driver_metric, setup_driver
from tests.lazy_local_fixtures import pandas_methods
from tests.lazy_materialization_crash_worker import snapshot

pytestmark = pytest.mark.runtime


def _unchanged(before: dict[str, object], after: dict[str, object]) -> None:
    old, new = before["tables"], after["tables"]
    assert isinstance(old, dict) and isinstance(new, dict)
    for table in ("dataset_artifacts", "dataset_evidence", "findings", "action_resource_journal"):
        assert old[table] == new[table]


@pytest.mark.parametrize(
    "policy",
    [
        replace(LocalPolicy(), max_input_rows=1),
        replace(LocalPolicy(), max_input_bytes=1),
        replace(LocalPolicy(), max_method_rows=1),
        # Twelve primary rows fit; both twelve-row side-state roles must also count.
        replace(LocalPolicy(), max_method_rows=12),
        replace(LocalPolicy(), max_intermediate_bytes=1),
        replace(LocalPolicy(), max_output_rows=1),
        replace(LocalPolicy(), max_output_bytes=1),
        replace(LocalPolicy(), max_worker_rss=1),
        replace(LocalPolicy(), deadline_seconds=0.001),
    ],
)
def test_retained_driver_guards_publish_no_partial_bundle(
    tmp_path: Path, policy: LocalPolicy
) -> None:
    with pandas_methods("discover.driver_axes"):
        fixture = setup_driver(tmp_path)
        runtime, sources = fixture.runtime, fixture.sources
        current = driver_metric(sources, temporal=True, region=True)
        baseline = driver_metric(sources, baseline=True, temporal=True, region=True)
        retained = current.compare(baseline).execute()
        fixture.database.rename(tmp_path / "origin.offline")
        runtime.local_policy = policy
        before = snapshot(runtime)
        with pytest.raises(MaterializationError):
            retained.discover.driver_axes(search_space=[CHANNEL]).execute()
        _unchanged(before, snapshot(runtime))
        assert runtime.last_run_ref is not None
        run = runtime.store.run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None


@pytest.mark.parametrize(
    "point",
    [
        "backend_compile",
        "source_statement",
        "transfer",
        "after_rename",
        "quality",
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
    ],
)
def test_driver_publication_fault_rolls_back_every_authority(tmp_path: Path, point: str) -> None:
    fixture = setup_driver(tmp_path)
    runtime, sources = fixture.runtime, fixture.sources
    current, baseline = driver_metric(sources), driver_metric(sources, baseline=True)
    observed: list[str] = []

    def fault(event: str) -> None:
        if event == point:
            observed.append(event)
            raise RuntimeError("private-driver-publication-canary")

    runtime._hook = fault
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        current.compare(baseline).discover.driver_axes(search_space=[CHANNEL]).execute()
    assert observed == [point]
    assert "private-driver-publication-canary" not in str(error.value)
    _unchanged(before, snapshot(runtime))


@pytest.mark.parametrize("point", ["transfer", "insert_findings"])
def test_driver_cancellation_keeps_no_partial_output(tmp_path: Path, point: str) -> None:
    fixture = setup_driver(tmp_path)
    runtime, sources = fixture.runtime, fixture.sources
    delta = driver_metric(sources).compare(driver_metric(sources, baseline=True))
    observed: list[str] = []

    def cancel(event: str) -> None:
        if event == point:
            observed.append(event)
            raise KeyboardInterrupt("private-driver-cancellation-canary")

    runtime._hook = cancel
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        delta.discover.driver_axes(search_space=[CHANNEL]).execute()
    assert observed == [point]
    assert "private-driver-cancellation-canary" not in str(error.value)
    _unchanged(before, snapshot(runtime))


def test_driver_local_worker_loss_is_terminal_and_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pandas_methods("discover.driver_axes"):
        fixture = setup_driver(tmp_path)
        runtime, sources = fixture.runtime, fixture.sources
        delta = driver_metric(sources).compare(driver_metric(sources, baseline=True)).execute()
        killed: list[int] = []

        def terminate(pid: int) -> int:
            assert pid != os.getpid()
            if not killed:
                killed.append(pid)
                os.kill(pid, signal.SIGKILL)
            return 0

        monkeypatch.setattr(local_worker, "_rss", terminate)
        before = snapshot(runtime)
        with pytest.raises(MaterializationError):
            delta.discover.driver_axes(search_space=[CHANNEL]).execute()
        assert len(killed) == 1
        with pytest.raises(ProcessLookupError):
            os.kill(killed[0], 0)
        _unchanged(before, snapshot(runtime))
        assert runtime.last_run_ref is not None
        run = runtime.store.run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None


@pytest.mark.parametrize("point", ["after_commit", "delivery"])
def test_driver_lost_ack_reuses_the_committed_zero_finding_bundle(
    tmp_path: Path, point: str
) -> None:
    fixture = setup_driver(tmp_path)
    runtime, sources = fixture.runtime, fixture.sources
    logical = (
        driver_metric(sources)
        .compare(driver_metric(sources, baseline=True))
        .discover.driver_axes(search_space=[CHANNEL])
    )
    observed: list[str] = []

    def lose(event: str) -> None:
        if event == point:
            observed.append(event)
            raise RuntimeError("lost-driver-acknowledgement")

    runtime._hook = lose
    result = logical.execute()
    assert observed == [point]
    assert result.findings().items == () and result.evidence_digest.finding_count == 0
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.candidate_evidence is not None
    before = snapshot(runtime)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(runtime) == before
    assert runtime.statistics.primary_queries == 0
