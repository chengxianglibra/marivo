"""Independent source-private membership integrity and immediate transfer boundaries."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest

from marivo.analysis.materialization import reads, storage
from marivo.analysis.materialization.contracts import (
    EngineReceipt,
    FileEntry,
    LocalReceipt,
    ObjectReceipt,
    RetainedPart,
    StorageReceipt,
    manifest_digest,
)
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.retained import validate_source_private_relation
from marivo.analysis.observation.distinct_contracts import membership_part_authorities
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry


def _metric() -> LogicalMetricDataset:
    original, sidecar = make_semantic_registry()
    metrics = dict(original.metrics)
    metrics["sales.order_count"] = replace(
        metrics["sales.order_count"], aggregation="count_distinct"
    )
    registry = replace(original, metrics=metrics)
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="session-distinct-integrity",
        store_id="store-distinct-integrity",
    )
    return (
        sources.observe(ref.metric("sales.order_count"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )


@pytest.mark.parametrize("reader", ["schema", "generic_part", "local_part", "payload"])
def test_membership_rejected_before_generic_iterator_creation(tmp_path: Path, reader: str) -> None:
    receipt = EngineReceipt(
        "sales",
        "c" * 64,
        "parts/metric_membership.test/payload.duckdb",
        "a" * 64,
        "b" * 64,
        3,
        123,
    )
    part = RetainedPart("metric_membership.test", "metric.distinct_membership", 1, receipt)
    with (
        patch.object(reads, "_payload_batches", side_effect=AssertionError("reader allocated")),
        pytest.raises(MaterializationError, match="source-native"),
    ):
        if reader == "schema":
            reads.part_schema(tmp_path, part)
        elif reader == "generic_part":
            reads.read_part_batches(tmp_path, part, expected_schema=pa.schema([]))
        elif reader == "local_part":
            storage.read_part_batches(tmp_path, part, expected_schema=pa.schema([]))
        else:
            reads.payload_batches(tmp_path, receipt, policy=storage.ReadPolicy())


@pytest.mark.parametrize("kind", ["engine", "local", "object"])
def test_owned_membership_payload_layout_rejects_each_generic_adapter_before_iteration(
    tmp_path: Path, kind: str
) -> None:
    prefix = "artifacts/example/parts/delta_membership.current"
    receipt: StorageReceipt
    if kind == "engine":
        receipt = EngineReceipt(
            "sales", "c" * 64, prefix + "/payload.duckdb", "a" * 64, "b" * 64, 3, 123
        )
    elif kind == "local":
        entries = (FileEntry("data.parquet", 123, "a" * 64),)
        receipt = LocalReceipt(
            prefix, entries, manifest_digest(entries), "a" * 64, "b" * 64, 3, 123
        )
    else:
        receipt = ObjectReceipt(
            "objects", prefix + "/manifest.json", "version-1", "a" * 64, "b" * 64, 3, 123
        )
    with (
        patch.object(
            reads, "_guarded_payload_batches", side_effect=AssertionError("iterator created")
        ),
        pytest.raises(MaterializationError, match="source-native"),
    ):
        reads.payload_batches(tmp_path, receipt, policy=storage.ReadPolicy())


@pytest.mark.parametrize("parent", ["metric_membership.report", "parts/delta_membership.report"])
def test_primary_engine_receipt_allows_membership_named_ancestor(
    tmp_path: Path, parent: str
) -> None:
    path = tmp_path / parent / "primary" / "payload.duckdb"
    path.parent.mkdir(parents=True)
    backend = ibis.duckdb.connect(str(path))
    try:
        backend.create_table("rows", pa.table({"value": [42]}))
    finally:
        backend.disconnect()
    receipt = EngineReceipt(
        "sales",
        "c" * 64,
        path.relative_to(tmp_path).as_posix(),
        storage._hash_file(path),
        "b" * 64,
        1,
        path.stat().st_size,
    )
    stream = reads.payload_batches(tmp_path, receipt, policy=storage.ReadPolicy())
    try:
        assert pa.Table.from_batches(list(stream)).column("value").to_pylist() == [42]
    finally:
        stream.close()


@pytest.mark.parametrize("damage", ["none", "duplicate", "null", "foreign", "endpoint"])
def test_native_membership_checks_only_export_schema_and_scalar_violations(damage: str) -> None:
    metric = _metric()
    row = metric.row_contract
    role, authority = membership_part_authorities(row)[0]
    assert authority.membership is not None
    key_name = next(
        field.name for field in row.schema.columns if field.field_id in row.key_field_ids
    )
    metric_name = next(field.name for field in row.schema.columns if field.role_id == "metric")
    members: list[float | None] = [10.0, 20.0]
    coordinates = ["web", "web"]
    if damage == "duplicate":
        members = [10.0, 10.0]
    elif damage == "null":
        members[1] = None
    elif damage == "foreign":
        coordinates[1] = "unknown"
    backend = ibis.duckdb.connect()
    try:
        primary = backend.create_table(
            "primary_rows",
            pa.table(
                {key_name: ["web", "empty"], metric_name: [3 if damage == "endpoint" else 2, 0]}
            ),
        )
        membership = backend.create_table(
            "private_members",
            pa.table(
                {key_name: coordinates, "__mv_distinct_key": pa.array(members, type=pa.float64())}
            ),
        )
        original = backend.to_pyarrow

        def schema_only(expression: ir.Table) -> pa.Table:
            result: object = original(expression)
            assert isinstance(result, pa.Table)
            assert result.num_rows == 0
            return result

        with patch.object(backend, "to_pyarrow", side_effect=schema_only):
            if damage == "none":
                validate_source_private_relation(
                    backend, membership, primary, row, role, lambda *_: None
                )
            else:
                with pytest.raises(IntegrityError, match="membership"):
                    validate_source_private_relation(
                        backend, membership, primary, row, role, lambda *_: None
                    )
    finally:
        backend.disconnect()
