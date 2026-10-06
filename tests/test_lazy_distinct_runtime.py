"""Immutable Parquet checkpoints keep private membership native through recovery and inspection."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from marivo.analysis.materialization import inspection
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

pytestmark = pytest.mark.runtime


def _setup(
    project: Path, *, event: Callable[[str], None] | None = None
) -> tuple[DatasetRuntime, LogicalMetricDataset, Path]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    original, sidecar = make_execution_registry(database)
    metrics = dict(original.metrics)
    metrics["sales.order_count"] = replace(
        metrics["sales.order_count"], aggregation="count_distinct"
    )
    registry = replace(original, metrics=metrics)
    registry.freeze()
    runtime = DatasetRuntime.create(project, "distinct-runtime", event=event)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = (
        sources.observe(ref.metric("sales.order_count"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    return runtime, metric, database


def test_membership_checkpoint_has_independent_count_and_native_cold_inspection(
    tmp_path: Path,
) -> None:
    runtime, metric, database = _setup(tmp_path)
    result = metric.execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    member = next(
        part
        for part in record.descriptor.retained_parts
        if part.contract_id == "metric.distinct_membership"
    )
    from marivo.analysis.materialization.retained import guard_part_transfer

    with pytest.raises(MaterializationError, match="retired Dataset"):
        guard_part_transfer(member)
    assert isinstance(member.storage_receipt, LocalReceipt)
    assert (
        member.storage_receipt.realized_row_count
        > record.descriptor.storage_receipt.realized_row_count
    )
    database.rename(tmp_path / "source.offline")
    with patch.object(
        inspection, "payload_batches", side_effect=AssertionError("membership transferred")
    ):
        inspection._payload_check(tmp_path, record.descriptor, member, ReadPolicy())
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    recovered = cold.artifact(result.state.artifact_ref)
    assert isinstance(recovered, MaterializedMetricDataset)
    assert recovered.to_pandas().equals(result.to_pandas())
    status = cold.revalidate(result.state.artifact_ref)
    assert status.artifact_integrity == "valid"
    assert status.storage_authority == "readable"
    continued = recovered.rank(recovered.fields.get("order_count")).limit(1).execute()
    assert continued.to_pandas()[["channel", "order_count"]].equals(result.to_pandas())
    assert cold.store.resources(cold.session_ref) == ()


@pytest.mark.parametrize("point", ["output_reserved", "retained_part_write", "before_commit"])
def test_membership_failure_releases_whole_checkpoint_and_scrubs_native_diagnostics(
    tmp_path: Path, point: str
) -> None:
    calls = 0

    def fail(selected: str) -> None:
        nonlocal calls
        if selected == point:
            calls += 1
            raise RuntimeError("private-member-canary")

    runtime, metric, _ = _setup(tmp_path, event=fail)
    with pytest.raises(RuntimeError) as caught:
        metric.execute()
    assert "private-member-canary" in str(caught.value)
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert runtime.store.resources(runtime.session_ref) == ()
    assert runtime.graph().artifacts == ()
