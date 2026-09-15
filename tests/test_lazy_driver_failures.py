"""Driver screening fails atomically across local guards and publication boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.lazy_driver_runtime_fixtures import CHANNEL, driver_metric, setup_driver
from tests.lazy_materialization_crash_worker import snapshot

pytestmark = pytest.mark.runtime


def _unchanged(before: dict[str, object], after: dict[str, object]) -> None:
    old, new = before["tables"], after["tables"]
    assert isinstance(old, dict) and isinstance(new, dict)
    for table in ("dataset_artifacts", "dataset_evidence", "findings", "action_resource_journal"):
        assert old[table] == new[table]


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
    with pytest.raises(RuntimeError) as error:
        current.compare(baseline).discover.driver_axes(search_space=[CHANNEL]).execute()
    assert observed == [point]
    assert "private-driver-publication-canary" in str(error.value)
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
    with pytest.raises(KeyboardInterrupt) as error:
        delta.discover.driver_axes(search_space=[CHANNEL]).execute()
    assert observed == [point]
    assert "private-driver-cancellation-canary" in str(error.value)
    _unchanged(before, snapshot(runtime))


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
