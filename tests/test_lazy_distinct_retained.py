"""Independent source-private membership integrity and immediate transfer boundaries."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.analysis.materialization import reads, storage
from marivo.analysis.materialization.contracts import (
    FileEntry,
    LocalReceipt,
    RetainedPart,
    manifest_digest,
)
from marivo.analysis.materialization.errors import MaterializationError
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
    entries = (FileEntry("data.parquet", 123, "a" * 64),)
    receipt = LocalReceipt(
        "parts/metric_membership.test",
        entries,
        manifest_digest(entries),
        "a" * 64,
        "b" * 64,
        3,
        123,
    )
    part = RetainedPart("metric_membership.test", "metric.distinct_membership", 1, receipt)
    with (
        patch.object(reads, "_payload_batches", side_effect=AssertionError("reader allocated")),
        pytest.raises(MaterializationError, match="retired Dataset"),
    ):
        if reader == "schema":
            reads.part_schema(tmp_path, part)
        elif reader == "generic_part":
            reads.read_part_batches(tmp_path, part, expected_schema=pa.schema([]))
        elif reader == "local_part":
            storage.read_part_batches(tmp_path, part, expected_schema=pa.schema([]))
        else:
            reads.payload_batches(tmp_path, receipt, policy=storage.ReadPolicy())


def test_owned_membership_payload_layout_rejects_each_generic_adapter_before_iteration(
    tmp_path: Path,
) -> None:
    prefix = "artifacts/example/parts/delta_membership.current"
    entries = (FileEntry("data.parquet", 123, "a" * 64),)
    receipt = LocalReceipt(prefix, entries, manifest_digest(entries), "a" * 64, "b" * 64, 3, 123)
    with (
        patch.object(
            reads, "_guarded_payload_batches", side_effect=AssertionError("iterator created")
        ),
        pytest.raises(MaterializationError, match="retired Dataset"),
    ):
        reads.payload_batches(tmp_path, receipt, policy=storage.ReadPolicy())


@pytest.mark.parametrize("parent", ["metric_membership.report", "parts/delta_membership.report"])
def test_primary_parquet_receipt_allows_membership_named_ancestor(
    tmp_path: Path, parent: str
) -> None:
    directory = tmp_path / parent / "primary"
    directory.mkdir(parents=True)
    path = directory / "data.parquet"
    pq.write_table(pa.table({"value": [42]}), path)
    fingerprint = storage._hash_file(path)
    entries = (FileEntry("data.parquet", path.stat().st_size, fingerprint),)
    manifest = storage._manifest_bytes(entries)
    (directory / "manifest.json").write_bytes(manifest)
    receipt = LocalReceipt(
        directory.relative_to(tmp_path).as_posix(),
        entries,
        manifest_digest(entries),
        fingerprint,
        "b" * 64,
        1,
        path.stat().st_size + len(manifest),
    )
    stream = reads.payload_batches(tmp_path, receipt, policy=storage.ReadPolicy())
    try:
        assert pa.Table.from_batches(list(stream)).column("value").to_pylist() == [42]
    finally:
        stream.close()
