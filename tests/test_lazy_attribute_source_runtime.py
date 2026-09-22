"""Actual source execution of logical expansion, proof continuations and Entity privacy."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.predicates import eq
from marivo.analysis.session._lazy_sources import LazySources
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic.ir import RatioComposition
from tests.lazy_attribute_runtime_evidence import record as record_evidence
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import snapshot
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime

REGION = ref.dimension("sales.customers.region")
CHANNEL = ref.dimension("sales.orders.channel")


def _setup(project: Path) -> tuple[DatasetRuntime, LazySources]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(project, "source-attribution", target=LocalTarget())
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar)


def test_daily_hidden_axis_attribution_fits_native_validation_memory(tmp_path: Path) -> None:
    runtime, sources = _setup(tmp_path)
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders(id, customer_id, amount, weight, channel, day) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    40 * (month - 1) + 2 * (day - 1) + group,
                    1,
                    float(day * group + month + day % 3 * 2),
                    float(day % 4 + 1),
                    "web" if group == 1 else "store",
                    date(2026, month, day),
                )
                for month in (1, 2)
                for day in range(1, 21)
                for group in (1, 2)
            ],
        )
    axis = ref.time_dimension("sales.orders.order_time")
    current = (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-02-01", end="2026-02-21"),
            time_dimension=axis,
        )
        .with_time_axis(axis, grain=grain("day"))
        .aggregate()
    )
    baseline = (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-01-01", end="2026-01-21"),
            time_dimension=axis,
        )
        .with_time_axis(axis, grain=grain("day"))
        .aggregate()
    )
    result = current.compare(baseline).attribute(axes=(CHANNEL,)).execute()
    rows = result.to_pandas()
    assert len(rows) == 40
    assert rows.contribution.tolist() == [1.0] * 40
    assert rows.overall_delta.tolist() == [2.0] * 40
    assert rows.groupby("comparison_ordinal").contribution.sum().tolist() == [2.0] * 20
    assert runtime.statistics.primary_queries == 1


def test_entity_expansion_reduces_only_global_proof_in_source(tmp_path: Path) -> None:
    candidate = _manifest()
    runtime, sources = _setup(tmp_path)
    before = snapshot(runtime)
    metric = sources.observe(ref.metric("sales.order_count"))
    logical = metric.compare(metric).attribute(axes=(REGION, CHANNEL), mode="hierarchy", top_k=1)
    with patch(
        "marivo.analysis.materialization.reads.payload_batches",
        side_effect=AssertionError("Entity Attribution must not enter Python publication rows"),
    ):
        result = logical.execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.attribution_evidence is not None
    proof = record.descriptor.attribution_evidence
    assert proof.scope_count == 6 and proof.resolution_count == 12
    assert proof.max_reconciliation_error == 0 and proof.complete
    assert proof.emitted_finding_count == 0 and result.findings().items == ()
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    assert any(name == "attribution.source_summary" for name, _ in runtime.statistics.statements)
    record_evidence(
        "source-entity-global-proof",
        candidate,
        {
            "before": before,
            "after": snapshot(runtime),
            "artifact_ref": result.state.artifact_ref.ref,
            "scope_count": proof.scope_count,
            "resolution_count": proof.resolution_count,
            "max_reconciliation_error": proof.max_reconciliation_error,
            "mapped_membership_digest": proof.mapped_membership_digest,
            "entity_rows_collected": False,
            "local_executions": runtime.statistics.events.get("local_execution_started", 0),
            "emitted_findings": 0,
        },
        kind="source",
    )


def test_all_logical_where_keeps_complete_unsliced_attribution_proof(tmp_path: Path) -> None:
    candidate = _manifest()
    runtime, sources = _setup(tmp_path)
    before = snapshot(runtime)
    metric = sources.observe(ref.metric("sales.order_count")).aggregate()
    attribution = metric.compare(metric).attribute(axes=(REGION,))
    selected = attribution.where(eq(attribution.fields.get("region"), "EU")).execute()
    record = runtime.store.artifact(selected.state.artifact_ref.ref)
    assert record is not None and record.descriptor.attribution_evidence is not None
    proof = record.descriptor.attribution_evidence
    assert (
        not proof.complete
        and proof.origin_definition_fingerprint == attribution.definition_fingerprint
    )
    assert (
        proof.complete_row_count == 2 and record.descriptor.storage_receipt.realized_row_count == 1
    )
    assert selected.findings().items == ()
    record_evidence(
        "source-logical-suffix-proof",
        candidate,
        {
            "before": before,
            "after": snapshot(runtime),
            "artifact_ref": selected.state.artifact_ref.ref,
            "proof_definition": proof.origin_definition_fingerprint,
            "complete_row_count": proof.complete_row_count,
            "selected_row_count": record.descriptor.storage_receipt.realized_row_count,
            "complete": proof.complete,
            "emitted_findings": 0,
        },
        kind="source",
    )


def test_decimal_source_summary_preserves_exact_attribution_values(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    current = Decimal("12345678901234567890.123456")
    baseline = Decimal("12345678901234567890.023455")
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute("ALTER TABLE orders ALTER COLUMN amount TYPE DECIMAL(38,6)")
        connection.executemany(
            "INSERT INTO orders (id, amount, day, channel) VALUES (?, ?, ?, 'web')",
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
                (name, replace(binding, data_type="decimal") if name == "amount" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    runtime = DatasetRuntime.create(
        tmp_path,
        "decimal-source-attribution",
        target=LocalTarget(),
    )
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    left, right = (
        sources.observe(ref.metric("sales.revenue"), time_scope=time_scope(start=start, end=end))
        .with_dimensions(CHANNEL)
        .aggregate()
        for start, end in (("2026-02-02", "2026-02-03"), ("2026-02-03", "2026-02-04"))
    )
    result = left.compare(right).attribute(axes=(CHANNEL,)).execute()
    rows = result.to_pandas()
    assert rows.current_value.tolist() == [current]
    assert rows.baseline_value.tolist() == [baseline]
    assert rows.contribution.tolist() == rows.overall_delta.tolist() == [current - baseline]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.attribution_evidence is not None
    proof = record.descriptor.attribution_evidence
    assert proof.complete and proof.max_reconciliation_error == 0.0
    assert runtime.statistics.events.get("local_execution_started", 0) == 0


def test_component_hierarchy_keeps_validation_memory_bounded(tmp_path: Path) -> None:
    runtime, sources = _setup(tmp_path)
    registry, sidecar = make_execution_registry(tmp_path / "warehouse.duckdb")
    weight = replace(
        registry.metrics["sales.revenue"],
        semantic_id="sales.weight_total",
        name="weight_total",
        measure="sales.orders.weight",
        aggregation_target="sales.orders.weight",
    )
    ratio = replace(
        registry.metrics["sales.conversion_rate"],
        composition=RatioComposition("sales.revenue", "sales.weight_total"),
    )
    registry = replace(
        registry, metrics={**registry.metrics, weight.semantic_id: weight, ratio.semantic_id: ratio}
    )
    registry.freeze()
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders(id, customer_id, amount, weight, channel, day) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    40 * (month - 1) + 2 * (day - 1) + group,
                    1 if day <= 10 else 2,
                    float(day * group + month + day % 3 * 2),
                    float(day % 4 + 1),
                    "web" if group == 1 else "store",
                    date(2026, month, day),
                )
                for month in (1, 2)
                for day in range(1, 21)
                for group in (1, 2)
            ],
        )
    current = (
        sources.observe(
            ref.metric("sales.conversion_rate"),
            time_scope=time_scope(start="2026-02-01", end="2026-02-21"),
        )
        .with_dimensions(CHANNEL, REGION)
        .aggregate()
    )
    baseline = (
        sources.observe(
            ref.metric("sales.conversion_rate"),
            time_scope=time_scope(start="2026-01-01", end="2026-01-21"),
        )
        .with_dimensions(CHANNEL, REGION)
        .aggregate()
    )
    result = current.compare(baseline).attribute(axes=(CHANNEL, REGION), mode="hierarchy").execute()
    rows = result.to_pandas()
    assert len(rows) == 6
    assert rows.overall_delta.tolist() == pytest.approx([0.4] * 6)
    parents = [bool(mask[0]) and not bool(mask[1]) for mask in rows.active_axis_mask]
    assert rows.loc[parents, "contribution"].tolist() == pytest.approx([0.2, 0.2])
    assert rows.loc[[not parent for parent in parents], "contribution"].tolist() == pytest.approx(
        [0.1] * 4
    )
    assert runtime.statistics.primary_queries == 1
