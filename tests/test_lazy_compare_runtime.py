"""Independent source, authority and complete-action comparison acceptance."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local_execution import (
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalStage,
    StreamInput,
)
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.operators.contracts import ComparePayload
from marivo.analysis.session._lazy_sources import LazySources
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from tests.lazy_compare_runtime_fixtures import independent_sources as _independent_sources
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import snapshot
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime

REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")
DAY = ref.time_dimension("sales.orders.order_time")
Shape = Literal["entity", "scalar", "dimension", "time", "dimension-time"]


def _record(name: str, before: dict[str, object], payload: dict[str, object]) -> None:
    retained = os.environ.get("MARIVO_SLICE5A_EVIDENCE_DIR")
    if retained is None:
        return
    after = _manifest()
    assert before == after
    directory = Path(retained)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "marivo.slice5a.focused-runtime/v1",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "candidate_before": before,
        "candidate_after": after,
        **payload,
    }
    (directory / f"{name}.json").write_text(
        json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )


def _metric(sources: LazySources, shape: Shape, *, current: bool) -> LogicalMetricDataset:
    value = sources.observe(
        REVENUE,
        time_scope=time_scope(
            start="2026-02-02" if current else "2026-02-03",
            end="2026-02-03" if current else "2026-02-04",
        ),
    )
    if shape in ("dimension", "dimension-time"):
        value = value.with_dimensions(CHANNEL)
    if shape in ("time", "dimension-time"):
        value = value.with_time_axis(DAY, grain=grain("day"))
    return value if shape == "entity" else value.aggregate()


def _setup(project: Path, *, engine: bool = False) -> tuple[DatasetRuntime, LazySources, Path]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = (
        DatasetRuntime.create(project, "comparison", target=LocalTarget())
        if engine
        else DatasetRuntime.create(project, "comparison")
    )
    if engine:
        runtime.target = LocalTarget()
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


@pytest.mark.parametrize("shape", ["entity", "scalar", "dimension", "time", "dimension-time"])
@pytest.mark.parametrize("states", ["LL", "LM", "ML", "MM"])
def test_all_admitted_shapes_and_operand_states(tmp_path: Path, shape: Shape, states: str) -> None:
    candidate = _manifest()
    runtime, sources, _ = _setup(tmp_path, engine=shape == "entity")
    left, right = _metric(sources, shape, current=True), _metric(sources, shape, current=False)
    current = left.execute() if states[0] == "M" else left
    baseline = right.execute() if states[1] == "M" else right
    before = snapshot(runtime)
    logical = current.compare(baseline)
    assert snapshot(runtime) == before
    assert logical.kind == "delta" and logical.row_contract.shape_id.local_shape_id == shape
    result = logical.execute()
    frame = result.to_pandas()
    assert result.kind == "delta"
    # Independent source totals: February 2 has 10+100; February 3 has 30+null.
    if shape != "entity":
        assert frame["current_value"].tolist() == [110.0]
        assert frame["baseline_value"].tolist() == [30.0]
        assert frame["delta"].tolist() == [80.0]
        assert frame["relative_delta"].tolist() == pytest.approx([80 / 30])
        assert frame["calculation_status"].tolist() == ["ok"]
        assert len(result.findings().items) == 1
    else:
        assert result.findings().items == ()
        # The governed Population keeps all six order identities in both windows.
        # A present identity without a contribution has null, not an absent-side zero.
        assert frame["current_value"].isna().tolist() == [False, True, False, True, True, True]
        assert frame["baseline_value"].isna().tolist() == [True, False, True, True, True, True]
        assert frame["current_value"].dropna().tolist() == [10.0, 100.0]
        assert frame["baseline_value"].dropna().tolist() == [30.0]
        assert frame["delta"].isna().all()
        assert frame["coordinate_presence"].tolist() == ["matched"] * 6
        assert frame["calculation_status"].tolist() == ["null_input"] * 6
        if states in ("LM", "ML"):
            statements = tuple(name for name, _ in runtime.statistics.statements)
            assert "engine_check.part_schema" in statements
            assert "validation_batch" in statements
            record = runtime.store.artifact(result.state.artifact_ref.ref)
            assert record is not None
            assert any(
                name.startswith("metric_components.")
                and name.endswith(".primary_value_reconciliation")
                for name, violations in record.descriptor.population_authority.validation_results
                if violations == 0
            )
    if "time" in shape:
        assert frame["comparison_ordinal"].nunique() == 1
        assert pd.Timestamp(frame["current_time"].iloc[0]).date().isoformat() == "2026-02-02"
        assert pd.Timestamp(frame["baseline_time"].iloc[0]).date().isoformat() == "2026-02-03"
    run = runtime.store.run(result.state.producing_run_ref)
    assert run is not None
    assert run.input_artifact_refs == tuple(
        item.state.artifact_ref.ref
        for item in (current, baseline)
        if isinstance(item, MaterializedDataset)
    )
    after = snapshot(runtime)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(runtime) == after
    assert (
        runtime.statistics.primary_queries == 0
        and runtime.statistics.events.get("local_execution_started", 0) == 0
    )
    _record(
        f"topology-{states}-{shape}",
        candidate,
        {
            "shape": shape,
            "states": states,
            "artifact": result.state.artifact_ref.ref,
            "input_refs": run.input_artifact_refs,
            "row_count": len(frame),
            "columns": list(frame.columns),
            "cold_hit_counts": after,
        },
    )


def test_repeated_checkpoint_keeps_two_operand_occurrences_and_immutable_rows(
    tmp_path: Path,
) -> None:
    runtime, sources, database = _setup(tmp_path)
    checkpoint = _metric(sources, "dimension", current=True).execute()
    rows = checkpoint.to_pandas()
    database.rename(tmp_path / "warehouse.offline")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Retained comparison construction attempted a Store read")

    with patch.object(runtime.store, "artifact", forbidden):
        delta = checkpoint.compare(checkpoint)
    ranked = delta.where(gt(delta.fields.get("current_value"), 0))
    result = ranked.rank(ranked.fields.get("delta")).limit(2).execute()
    assert result.to_pandas()["delta"].tolist() == [0.0]
    pd.testing.assert_frame_equal(rows, checkpoint.to_pandas())
    run = runtime.store.run(result.state.producing_run_ref)
    assert run is not None
    assert run.input_artifact_refs == (checkpoint.state.artifact_ref.ref,) * 2
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    selected = cold.get_run(result.state.producing_run_ref)
    expected_inputs = (checkpoint.state.artifact_ref,) * 2
    assert selected.input_artifact_refs == expected_inputs
    assert cold.runs(limit=1).items[0] == selected
    graph = cold.graph()
    consumes = tuple(
        edge.artifact_ref
        for edge in graph.edges
        if edge.kind == "consumes" and edge.run_id == selected.run_id
    )
    assert consumes == expected_inputs
    assert len(graph.artifacts) == 2 and not graph.truncated
    assert cold.statistics.primary_queries == 0


@pytest.mark.parametrize("shape", ["entity", "dimension"])
def test_delta_row_operations_preserve_source_and_retained_numerical_results(
    tmp_path: Path, shape: Shape
) -> None:
    candidate = _manifest()
    runtime, sources, database = _setup(tmp_path, engine=shape == "entity")
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE orders SET channel = CASE WHEN id <= 2 THEN 'a' ELSE 'b' END")
        connection.execute("UPDATE orders SET amount = 20 WHERE id = 4")
    if shape == "entity":
        population = sources.population(ref.entity("sales.customers"))
        current = sources.observe(
            REVENUE,
            population=population,
            time_scope=time_scope(start="2026-02-02", end="2026-02-03"),
        )
        baseline = sources.observe(
            REVENUE,
            population=population,
            time_scope=time_scope(start="2026-02-03", end="2026-02-04"),
        )
    else:
        current = _metric(sources, shape, current=True)
        baseline = _metric(sources, shape, current=False)
    logical = current.compare(baseline)
    filtered = logical.where(gt(logical.fields.get("current_value"), 0))
    source = filtered.rank(filtered.fields.get("delta")).limit(1).execute()
    source_frame = source.to_pandas()
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    retained = logical.execute()
    # Customer/channel a: 10 - 30 = -20; customer/channel b: 100 - 20 = 80.
    finite = retained.to_pandas().dropna(subset=["delta"])
    assert finite["delta"].tolist() == [-20.0, 80.0]
    assert finite["relative_delta"].tolist() == pytest.approx([-20 / 30, 4.0])
    if shape == "entity":
        with duckdb.connect(str(database)) as connection:
            connection.execute("DROP TABLE orders")
    else:
        database.rename(tmp_path / "warehouse.offline")
    selected = retained.where(gt(retained.fields.get("current_value"), 0))
    continued = selected.rank(selected.fields.get("delta")).limit(1).execute()
    continued_frame = continued.to_pandas()
    pd.testing.assert_frame_equal(source_frame, continued_frame)
    assert continued_frame["current_value"].tolist() == [100.0]
    assert continued_frame["baseline_value"].tolist() == [20.0]
    assert continued_frame["delta"].tolist() == [80.0]
    assert continued_frame["relative_delta"].tolist() == [4.0]
    if shape == "entity":
        assert runtime.statistics.events.get("local_execution_started", 0) == 0
        assert continued.findings().items == ()
    else:
        assert runtime.statistics.events.get("local_execution_started", 0) == 0
        assert runtime.statistics.primary_queries == 1
    _record(
        f"row-operation-parity-{shape}",
        candidate,
        {"shape": shape, "selected_delta": 80.0, "selected_relative_delta": 4.0},
    )


def test_sampled_self_comparison_and_independent_branches_do_not_share_bindings(
    tmp_path: Path,
) -> None:
    candidate = _manifest()
    runtime, sources, _ = _setup(tmp_path)
    population = sources.population(ref.entity("sales.orders"))

    def sampled() -> LogicalMetricDataset:
        return sources.observe(
            REVENUE, population=population.sample(engine_sample(target_rows=3))
        ).aggregate()

    shared = sampled()
    independent = sampled().compare(sampled())
    shared_comparison = shared.compare(shared)
    assert independent.definition_fingerprint != shared_comparison.definition_fingerprint
    independent_result = independent.execute()
    assert runtime.statistics.sampling_fences == 2
    shared_result = shared_comparison.execute()
    assert runtime.statistics.sampling_fences == 1
    assert shared_result.state.artifact_ref != independent_result.state.artifact_ref
    assert shared_result.to_pandas()["delta"].tolist() == [0.0]
    after = snapshot(runtime)
    reconstructed = sampled()
    assert (
        reconstructed.compare(reconstructed).execute().state.artifact_ref
        == shared_result.state.artifact_ref
    )
    assert snapshot(runtime) == after and runtime.statistics.primary_queries == 0
    _record(
        "realization-sharing",
        candidate,
        {
            "shared_definition": shared_comparison.definition_fingerprint,
            "independent_definition": independent.definition_fingerprint,
            "shared_artifact": shared_result.state.artifact_ref.ref,
            "independent_artifact": independent_result.state.artifact_ref.ref,
            "shared_fences": 1,
            "independent_fences": 2,
            "delta": 0.0,
            "cold_hit_counts": after,
        },
    )


def test_independent_equal_argument_connections_feed_one_local_comparison(tmp_path: Path) -> None:
    candidate = _manifest()
    with _independent_sources(tmp_path) as (runtime, first, second, calls):
        result = first.aggregate().compare(second.aggregate()).execute()
        assert result.to_pandas()["delta"].tolist() == [-18.0]
        assert len(calls) == len(set(calls)) == 2
        assert runtime.statistics.primary_queries == 2
        assert runtime.statistics.events.get("local_execution_started", 0) > 0
        assert runtime.store.resources(runtime.session_ref) == ()
        _record(
            "independent-connections",
            candidate,
            {
                "connection_arguments": [":memory:", ":memory:"],
                "connection_ids": calls,
                "current": 11,
                "baseline": 29,
                "delta": -18,
                "queries": runtime.statistics.primary_queries,
                "local_executions": runtime.statistics.events.get("local_execution_started", 0),
                "after": snapshot(runtime),
            },
        )


def test_independent_entity_comparison_is_rejected_before_source_work(tmp_path: Path) -> None:
    candidate = _manifest()
    with _independent_sources(tmp_path) as (runtime, first, second, calls):
        before = snapshot(runtime)
        with pytest.raises(DatasetCompilationError, match="source-required"):
            first.compare(second).execute()
        assert snapshot(runtime) == before and calls == []
        _record(
            "source-required-rejection",
            candidate,
            {"before": before, "after": snapshot(runtime), "connection_calls": calls},
        )


def test_cross_session_selected_checkpoint_preserves_original_owner(tmp_path: Path) -> None:
    producer, sources, database = _setup(tmp_path)
    checkpoint = _metric(sources, "dimension", current=True).execute()
    consumer = DatasetRuntime.create(tmp_path, "consumer")
    selected = consumer.artifact(checkpoint.state.artifact_ref)
    assert isinstance(selected, MaterializedMetricDataset)
    assert selected.state.artifact_session_ref == producer.session_ref
    registry, sidecar = make_execution_registry(database)
    current_sources = consumer.sources(semantic_registry=registry, sidecar=sidecar)
    mixed = _metric(current_sources, "dimension", current=False).compare(selected).execute()
    assert mixed.to_pandas()["delta"].tolist() == [-80.0]
    mixed_run = consumer.store.run(mixed.state.producing_run_ref)
    assert mixed_run is not None and mixed_run.input_artifact_refs == (
        checkpoint.state.artifact_ref.ref,
    )
    database.rename(tmp_path / "warehouse.offline")
    result = selected.compare(selected).execute()
    assert result.state.artifact_session_ref == consumer.session_ref
    assert result.to_pandas()["delta"].tolist() == [0.0]
    assert consumer.store.resources(consumer.session_ref) == ()


@pytest.mark.parametrize("failure", ["schema", "key"])
def test_later_complete_operand_failure_prevents_worker_consumer_invocation(
    tmp_path: Path, failure: str
) -> None:
    candidate = _manifest()
    _, sources, _ = _setup(tmp_path)
    metric = _metric(sources, "dimension", current=True)
    compared = metric.compare(metric)
    assert isinstance(compared._root, LogicalRootHandle)
    assert isinstance(compared._root.payload, ComparePayload)
    table = pa.table({"channel": ["a" * 100, "b" * 100], "revenue": [2.0, 3.0]})
    later = table
    if failure == "schema":
        later = pa.table({"channel": ["a", "b"], "revenue": ["2", "3"]})
    elif failure == "key":
        later = pa.table({"channel": ["a", "a"], "revenue": [2.0, 3.0]})
    stream = StreamInput(metric.row_contract, metric.row_set_contract)
    request = LocalGraphRequest(
        (LocalBoundary(0, stream), LocalBoundary(1, stream)),
        (LocalStage(2, (0, 1), compared._root.payload.spec),),
        2,
    )
    from marivo.analysis.materialization.local_execution import execute_local

    with pytest.raises(MaterializationError) as caught:
        execute_local(
            request,
            (
                LocalInputStreams(table.to_batches()),
                LocalInputStreams(later.to_batches()),
            ),
        )
    assert caught.value.stage in ("transfer_guard", "output_validation")
    received = caught.value.received
    assert received is not None
    assert any(
        word in received
        for word in {
            "schema": ("type", "schema"),
            "key": ("key", "duplicate"),
            "rows": ("row",),
            "combined": ("combined", "conversion"),
            "method_size": ("method size",),
        }[failure]
    )
    _record(
        f"later-operand-{failure}",
        candidate,
        {
            "failure": failure,
            "stage": caught.value.stage,
            "received": caught.value.received,
            "consumer_invoked": False,
        },
    )


@pytest.mark.parametrize("numeric", ["int64", "decimal"])
@pytest.mark.parametrize("retained", [False, True])
def test_registered_numeric_source_and_local_comparison_are_lossless(
    tmp_path: Path, numeric: str, retained: bool
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    current: int | Decimal = (
        2**53 + 7 if numeric == "int64" else Decimal("12345678901234567890.123456")
    )
    baseline: int | Decimal = (
        2**53 + 2 if numeric == "int64" else Decimal("12345678901234567890.023455")
    )
    physical = "BIGINT" if numeric == "int64" else "DECIMAL(38,6)"
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(f"ALTER TABLE orders ALTER COLUMN amount TYPE {physical}")
        connection.executemany(
            "INSERT INTO orders (id, amount, day) VALUES (?, ?, ?)",
            ((1, current, "2026-02-02"), (2, baseline, "2026-02-03")),
        )
    registry, sidecar = make_execution_registry(database)
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities = dict(registry.entities)
    entities[entity.semantic_id] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type=numeric) if name == "amount" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "numeric-comparison")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    left, right = (
        _metric(sources, "scalar", current=True),
        _metric(sources, "scalar", current=False),
    )
    if retained:
        a, b = left.execute(), right.execute()
        database.rename(tmp_path / "warehouse.offline")
        result = a.compare(b).execute()
    else:
        result = left.compare(right).execute()
    frame = result.to_pandas()
    assert frame["current_value"].iloc[0] == current
    assert frame["baseline_value"].iloc[0] == baseline
    assert frame["delta"].iloc[0] == current - baseline
    assert frame["relative_delta"].iloc[0] == float(current - baseline) / abs(float(baseline))
    assert len(result.findings().items) == 1


@pytest.mark.parametrize("retained", [False, True])
def test_finite_delta_with_unavailable_relative_value_publishes_finding(
    tmp_path: Path, retained: bool
) -> None:
    runtime, sources, database = _setup(tmp_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, amount, day) VALUES (?, ?, ?)",
            ((1, 1e300, "2026-02-02"), (2, 1e-300, "2026-02-03")),
        )
    left, right = (
        _metric(sources, "scalar", current=True),
        _metric(sources, "scalar", current=False),
    )
    result = (
        left.execute().compare(right.execute()).execute()
        if retained
        else left.compare(right).execute()
    )
    frame = result.to_pandas()
    assert frame["delta"].tolist() == [1e300]
    assert frame["relative_delta"].isna().all()
    assert frame["calculation_status"].tolist() == ["ok"]
    assert frame["relative_delta_status"].tolist() == ["delta_unavailable"]
    from marivo.analysis.evidence._dataset_types import (
        DeltaFindingValueV1,
        UndefinedRelativeDeltaV1,
    )

    finding = result.findings().items[0]
    assert isinstance(finding.value, DeltaFindingValueV1)
    assert isinstance(finding.value.relative_delta, UndefinedRelativeDeltaV1)
    assert finding.value.relative_delta.reason == "delta_unavailable"
