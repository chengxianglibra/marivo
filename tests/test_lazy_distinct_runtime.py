"""Immutable Parquet checkpoints keep private membership native through recovery and inspection."""

import os
import time
import traceback
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from ibis.backends.duckdb import Backend

from marivo._compat import Never
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.materialization import admission, inspection
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget
from marivo.analysis.observation.contracts import source_owner_of
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.analysis.operators.errors import ComparisonError
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
    runtime = DatasetRuntime.create(project, "distinct-runtime", target=LocalTarget(), event=event)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = (
        sources.observe(ref.metric("sales.order_count"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    return runtime, metric, database


def test_membership_checkpoint_requires_explicit_available_object_binding(tmp_path: Path) -> None:
    runtime, metric, _ = _setup(tmp_path)
    runtime.target = ObjectTarget("unavailable")
    with pytest.raises(MaterializationError):
        metric.execute()
    assert not runtime.statistics.statements
    assert runtime.graph().artifacts == ()
    assert runtime.store.resources(runtime.session_ref) == ()


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
    assert isinstance(member.storage_receipt, LocalReceipt)
    assert (
        member.storage_receipt.realized_row_count
        > record.descriptor.storage_receipt.realized_row_count
    )
    database.rename(tmp_path / "source.offline")
    with patch.object(
        inspection, "payload_batches", side_effect=AssertionError("membership transferred")
    ):
        inspection._payload_check(tmp_path, record.descriptor, member, (), ReadPolicy())
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
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
    with pytest.raises(MaterializationError) as caught:
        metric.execute()
    assert "private-member-canary" not in str(caught.value)
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert runtime.store.resources(runtime.session_ref) == ()
    assert runtime.graph().artifacts == ()


@pytest.mark.parametrize("fault", ["missing", "replaced"])
def test_damaged_delta_membership_keeps_primary_readable_but_blocks_consumption(
    tmp_path: Path, fault: str
) -> None:
    runtime, metric, database = _setup(tmp_path)
    delta = metric.compare(metric).execute()
    expected = delta.to_pandas()
    record = runtime.store.artifact(delta.state.artifact_ref.ref)
    assert record is not None
    receipt = next(
        part.storage_receipt
        for part in record.descriptor.retained_parts
        if part.role == "delta_membership.current"
    )
    assert isinstance(receipt, LocalReceipt)
    path = tmp_path / receipt.project_relative_path / "data.parquet"
    if fault == "missing":
        path.unlink()
    else:
        os.chmod(path, 0o600)
        path.write_bytes(b"private-member-physical-canary")
    database.rename(tmp_path / "origin.offline")
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    recovered = cold.artifact(delta.state.artifact_ref)
    assert isinstance(recovered, MaterializedDeltaDataset)
    assert recovered.to_pandas().equals(expected)
    checked = cold.revalidate(delta.state.artifact_ref)
    assert checked.artifact_integrity == checked.evidence_integrity == "valid"
    assert checked.storage_authority == ("missing" if fault == "missing" else "mutated")
    with pytest.raises(MaterializationError) as caught:
        recovered.attribute(axes=(ref.dimension("sales.orders.channel"),)).execute()
    assert cold.last_run_ref is not None
    failed = cold.store.run(cold.last_run_ref)
    assert (
        failed is not None and failed.lifecycle == "failed" and failed.output_artifact_ref is None
    )
    assert cold.store.resources(cold.session_ref) == ()
    assert cold.statistics.events.get("profile_resolution", 0) == 0
    with cold.store._read() as connection:
        dump = "\n".join(connection.iterdump())
    assert "private-member-physical-canary" not in dump + "".join(
        traceback.format_exception(caught.value)
    )
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("retained", [False, True])
def test_distinct_foreign_domain_contracts_fail_before_data_work(
    tmp_path: Path, retained: bool
) -> None:
    runtime, current, _ = _setup(tmp_path)
    foreign_path = tmp_path / "foreign.duckdb"
    seed_execution_database(foreign_path)
    original, sidecar = make_execution_registry(foreign_path)
    metrics = dict(original.metrics)
    metrics["sales.order_count"] = replace(
        metrics["sales.order_count"], aggregation="count_distinct"
    )
    registry = replace(original, metrics=metrics)
    registry.freeze()
    foreign_sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    foreign = (
        foreign_sources.observe(ref.metric("sales.order_count"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    baseline = foreign.execute() if retained else foreign
    before = runtime.graph()
    previous_run = runtime.last_run_ref
    previous_statements = tuple(runtime.statistics.statements)
    with pytest.raises(ComparisonError, match="incompatible Metric contracts"):
        current.compare(baseline).attribute(axes=(ref.dimension("sales.orders.channel"),)).execute()
    assert runtime.last_run_ref == previous_run
    assert tuple(runtime.statistics.statements) == previous_statements
    assert runtime.graph() == before
    assert runtime.store.resources(runtime.session_ref) == ()


def test_distinct_local_frontier_rejects_separate_source_owners_before_run(tmp_path: Path) -> None:
    runtime, current, _ = _setup(tmp_path)
    owner = source_owner_of(current)
    independent = runtime.sources(semantic_registry=owner.semantic_registry, sidecar=owner.sidecar)
    baseline = (
        independent.observe(ref.metric("sales.order_count"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    with pytest.raises(DatasetCompilationError, match="source-required"):
        current.compare(baseline).attribute(axes=(ref.dimension("sales.orders.channel"),)).execute()
    assert runtime.last_run_ref is None
    assert not runtime.statistics.statements
    assert runtime.graph().artifacts == ()
    assert runtime.store.resources(runtime.session_ref) == ()


def test_mixed_source_input_membership_is_rechecked_before_publication(tmp_path: Path) -> None:
    runtime, current, _ = _setup(tmp_path)
    baseline = current.execute()
    record = runtime.store.artifact(baseline.state.artifact_ref.ref)
    assert record is not None
    receipt = next(
        part.storage_receipt
        for part in record.descriptor.retained_parts
        if part.contract_id == "metric.distinct_membership"
    )
    assert isinstance(receipt, LocalReceipt)
    changed: list[str] = []

    def mutate(point: str) -> None:
        if point == "after_rename":
            path = tmp_path / receipt.project_relative_path / "data.parquet"
            os.chmod(path, 0o600)
            with path.open("ab") as stream:
                stream.write(b"private-member-mutation-canary")
            changed.append(point)

    runtime._hook = mutate
    with pytest.raises(MaterializationError, match="backing size changed"):
        current.compare(baseline).attribute(axes=(ref.dimension("sales.orders.channel"),)).execute()
    assert changed == ["after_rename"]
    assert runtime.last_run_ref is not None
    failed = runtime.store.run(runtime.last_run_ref)
    assert (
        failed is not None and failed.lifecycle == "failed" and failed.output_artifact_ref is None
    )
    assert runtime.store.resources(runtime.session_ref) == ()
    assert len(runtime.graph().artifacts) == 1


def test_distinct_native_deadline_cancels_query_and_redacts_failure(tmp_path: Path) -> None:
    runtime, metric, _ = _setup(tmp_path)
    delta = metric.compare(metric).execute()
    elapsed: list[float] = []
    interrupted: list[bool] = []

    def slow(backend: Backend, *_: object) -> Never:
        started = time.monotonic()
        try:
            with (
                patch.object(admission, "_SOURCE_EXECUTION_DEADLINE_SECONDS", 0.05),
                admission._engine_deadline(backend),
            ):
                backend.raw_sql(
                    "SELECT sum(i), 'private-member-timeout-canary' FROM range(1000000000000) AS rows(i)"
                )
        except duckdb.InterruptException:
            interrupted.append(True)
            raise
        finally:
            elapsed.append(time.monotonic() - started)
        raise AssertionError("Unbounded source query completed without cancellation")

    with (
        patch.object(runtime, "_attribution_source_summary", side_effect=slow),
        pytest.raises(MaterializationError) as caught,
    ):
        delta.attribute(axes=(ref.dimension("sales.orders.channel"),)).execute()
    assert interrupted == [True]
    assert len(elapsed) == 1 and elapsed[0] < 5
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert runtime.last_run_ref is not None
    failed = runtime.store.run(runtime.last_run_ref)
    assert (
        failed is not None and failed.lifecycle == "failed" and failed.output_artifact_ref is None
    )
    assert runtime.store.resources(runtime.session_ref) == ()
    assert len(runtime.graph().artifacts) == 1
    with runtime.store._read() as connection:
        dump = "\n".join(connection.iterdump())
    assert "private-member-timeout-canary" not in dump + "".join(
        traceback.format_exception(caught.value)
    ) + repr(runtime.statistics.statements)
