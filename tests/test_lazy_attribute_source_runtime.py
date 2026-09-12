"""Actual source execution of logical expansion, proof continuations and Entity privacy."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.predicates import eq
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.session._lazy_sources import LazySources
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
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
    assert runtime.statistics.worker_pid is None
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
            "worker_pid": runtime.statistics.worker_pid,
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


def test_logical_axis_expansion_reuses_one_sample_realization(tmp_path: Path) -> None:
    candidate = _manifest()
    runtime, sources = _setup(tmp_path)
    before = snapshot(runtime)
    population = sources.population(ref.entity("sales.orders")).sample(engine_sample(target_rows=3))
    metric = sources.observe(ref.metric("sales.order_count"), population=population).aggregate()
    result = metric.compare(metric).attribute(axes=(REGION,)).execute()
    assert runtime.statistics.sampling_fences == 1
    rows = result.to_pandas()
    assert rows.current_value.sum() <= 3
    assert rows.current_value.tolist() == rows.baseline_value.tolist()
    assert rows.contribution.sum() == 0
    record_evidence(
        "source-shared-sampling-expansion",
        candidate,
        {
            "before": before,
            "after": snapshot(runtime),
            "artifact_ref": result.state.artifact_ref.ref,
            "sampling_fences": runtime.statistics.sampling_fences,
            "current_total": int(rows.current_value.sum()),
            "baseline_total": int(rows.baseline_value.sum()),
            "contribution_total": int(rows.contribution.sum()),
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
    assert runtime.statistics.worker_pid is None
