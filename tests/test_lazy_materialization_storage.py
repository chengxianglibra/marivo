"""Real PyArrow acceptance of the private immutable local storage protocol."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.materialization import storage
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
)
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    PartWriteSpec,
    ReadPolicy,
    StoragePolicy,
)
from marivo.refs import ref

_STORAGE_POLICY = StoragePolicy()
_READ_POLICY = ReadPolicy()


def _contracts(
    columns: tuple[tuple[str, str, bool], ...],
    *,
    keys: tuple[str, ...] = ("id",),
    identity: tuple[tuple[str, str], ...] | None = None,
) -> tuple[d.DatasetRowContract, d.DatasetRowSetContract]:
    fields = []
    for name, kind, nullable in columns:
        field_id = d._make_field_id(name)
        field_identity = (
            d._EntityFieldIdentity(
                _token=d._CORE_TOKEN,
                entity_ref=ref.entity("sales.customers"),
                identity_signature=identity,
            )
            if name == "identity" and identity is not None
            else d._generated_identity(field_id)
        )
        fields.append(
            d.DatasetField(
                _token=d._CORE_TOKEN,
                field_id=field_id,
                name=name,
                role_id="value",
                identity=field_identity,
                derivation_identity=f"test.{name}",
                logical_type_id=kind,
                physical_type_state=d._ResolvedPhysicalType(
                    _token=d._CORE_TOKEN, physical_type_id=kind
                ),
                nullable=nullable,
            )
        )
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d.DatasetShapeId(
            _token=d._CORE_TOKEN, family_id="test", local_shape_id="rows", semantic_version=1
        ),
        schema=d._make_schema(tuple(fields)),
        coordinate_field_ids=tuple(d._make_field_id(key) for key in keys),
        key_field_ids=tuple(d._make_field_id(key) for key in keys),
        family_semantics=d._complete_from_schema(),
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound())
        if keys
        else d._singleton_cardinality(),
        ordering=d._unordered_ordering(),
    )
    return row, rows


def _write(
    tmp_path: Path,
    table: pa.Table,
    contracts: tuple[d.DatasetRowContract, d.DatasetRowSetContract],
    *,
    parts: tuple[PartWriteSpec, ...] = (),
    policy: StoragePolicy = _STORAGE_POLICY,
) -> DatasetWriteResult[LocalReceipt]:
    row, rows = contracts
    return storage.write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=table.to_batches(max_chunksize=2),
        row_contract=row,
        row_set_contract=rows,
        event=lambda _name: None,
        parts=parts,
        policy=policy,
    )


def _read(
    tmp_path: Path,
    result: DatasetWriteResult[LocalReceipt],
    contracts: tuple[d.DatasetRowContract, d.DatasetRowSetContract],
    *,
    policy: ReadPolicy = _READ_POLICY,
) -> pd.DataFrame:
    row, rows = contracts
    return storage._read_primary(
        project_root=tmp_path,
        receipt=result.primary_receipt,
        row_contract=row,
        row_set_contract=rows,
        policy=policy,
    )


def test_primary_and_parts_split_one_stream_with_exact_receipts(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False), ("amount", "float64", True)))
    table = pa.table(
        {
            "id": [1, 2, 3],
            "amount": [10.0, None, 30.0],
            "sum": [10.0, None, 30.0],
            "count": [1, 0, 1],
        }
    )
    calls: list[int] = []
    hooks: list[str] = []

    def batches() -> Iterator[pa.RecordBatch]:
        for batch in table.to_batches(max_chunksize=1):
            calls.append(batch.num_rows)
            yield batch

    def event(name: str) -> None:
        hooks.append(name)
        assert (tmp_path / "run" / "staging").exists() == (name != "after_rename")
        assert (tmp_path / "artifacts" / "result").exists() == (name == "after_rename")

    result = storage.write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=batches(),
        row_contract=contracts[0],
        row_set_contract=contracts[1],
        parts=(PartWriteSpec("components", "metric.components", 1, ("id", "sum", "count")),),
        event=event,
    )
    assert calls == [1, 1, 1]
    assert hooks == [
        "parquet_payload_create",
        "parquet_payload_create",
        "before_rename",
        "after_rename",
    ]
    assert result.realized_row_count == 3
    assert result.realized_schema.columns[0].physical_type_state.kind == "resolved"
    part = result.retained_parts[0]
    assert part.role == "components"
    assert isinstance(part.storage_receipt, LocalReceipt)
    part_file = tmp_path / part.storage_receipt.project_relative_path / "data.parquet"
    assert pq.read_table(part_file).to_pydict() == {
        "id": [1, 2, 3],
        "sum": [10.0, None, 30.0],
        "count": [1, 0, 1],
    }
    for receipt in (result.primary_receipt, part.storage_receipt):
        assert isinstance(receipt, LocalReceipt)
        target = tmp_path / receipt.project_relative_path
        assert receipt.realized_byte_count == sum(file.stat().st_size for file in target.iterdir())
        assert receipt.realized_row_count == 3
    data = _read(tmp_path, result, contracts)
    assert list(data.columns) == ["id", "amount"]
    assert data["id"].tolist() == [1, 2, 3]
    data.loc[0, "amount"] = 99
    assert _read(tmp_path, result, contracts).loc[0, "amount"] == 10


def test_temporal_decimal_boolean_and_identity_roundtrip(tmp_path: Path) -> None:
    contracts = _contracts(
        (
            ("identity", "identity_tuple", False),
            ("day", "date", False),
            ("instant", "timestamp", True),
            ("amount", "decimal", True),
            ("active", "boolean", False),
        ),
        keys=("identity",),
        identity=(("tenant", "string"), ("id", "int64")),
    )
    instant = datetime(2026, 2, 1, 8, 30, tzinfo=timezone.utc)
    table = pa.table(
        {
            "identity": pa.array(
                [{"tenant": "a", "id": 1}, {"tenant": "b", "id": 2}],
                type=pa.struct([("tenant", pa.string()), ("id", pa.int64())]),
            ),
            "day": pa.array([date(2026, 2, 1), date(2026, 2, 2)], type=pa.date32()),
            "instant": pa.array([instant, None], type=pa.timestamp("us", tz="UTC")),
            "amount": pa.array([Decimal("123.45"), None], type=pa.decimal128(12, 2)),
            "active": [True, False],
        }
    )
    result = _write(tmp_path, table, contracts)
    value = _read(tmp_path, result, contracts)
    assert value["identity"].tolist() == [("a", 1), ("b", 2)]
    assert value.loc[0, "day"] == date(2026, 2, 1)
    assert value.loc[0, "instant"] == instant
    assert value.loc[0, "amount"] == Decimal("123.45")
    assert pd.isna(value.loc[1, "amount"])
    assert value["active"].tolist() == [True, False]


def test_dictionary_normalization_is_lossless(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False), ("label", "string", True)))
    table = pa.table({"id": [1, 2, 3], "label": pa.array(["a", None, "b"]).dictionary_encode()})
    result = _write(tmp_path, table, contracts)
    values = _read(tmp_path, result, contracts)["label"]
    assert values.iloc[0] == "a" and values.iloc[2] == "b" and pd.isna(values.iloc[1])
    assert (
        pq.read_schema(tmp_path / result.primary_receipt.project_relative_path / "data.parquet")
        .field("label")
        .type
        == pa.string()
    )


@pytest.mark.parametrize("ids", [[1, 1], [2, 1]])
def test_duplicate_and_out_of_order_keys_publish_nothing(tmp_path: Path, ids: list[int]) -> None:
    contracts = _contracts((("id", "int64", False),))
    with pytest.raises(MaterializationError, match="duplicate or unordered"):
        _write(tmp_path, pa.table({"id": ids}), contracts)
    assert not (tmp_path / "artifacts" / "result").exists()


@pytest.mark.parametrize("data", [pa.array([None], type=pa.int64()), pa.array([1.0])])
def test_required_null_and_wrong_type_fail_before_publication(
    tmp_path: Path, data: pa.Array
) -> None:
    contracts = _contracts((("id", "int64", False),))
    with pytest.raises(MaterializationError):
        _write(tmp_path, pa.table({"id": data}), contracts)
    assert not (tmp_path / "artifacts" / "result").exists()


def test_null_identity_component_fails(tmp_path: Path) -> None:
    contracts = _contracts(
        (("identity", "identity_tuple", False),), keys=("identity",), identity=(("id", "int64"),)
    )
    table = pa.table({"identity": pa.array([{"id": None}], type=pa.struct([("id", pa.int64())]))})
    with pytest.raises(MaterializationError, match="null identity"):
        _write(tmp_path, table, contracts)


def test_empty_keyed_stream_retains_schema(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    batch = pa.record_batch([pa.array([], type=pa.int64())], names=["id"])
    result = storage.write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "staging",
        final_path=tmp_path / "result",
        batches=(batch,),
        row_contract=contracts[0],
        row_set_contract=contracts[1],
        event=lambda _name: None,
    )
    assert result.realized_row_count == 0
    assert _read(tmp_path, result, contracts).empty


@pytest.mark.parametrize("count", [0, 2])
def test_singleton_requires_exactly_one_row(tmp_path: Path, count: int) -> None:
    contracts = _contracts((("amount", "float64", True),), keys=())
    batch = pa.record_batch([pa.array([1.0] * count, type=pa.float64())], names=["amount"])
    with pytest.raises(MaterializationError):
        storage.write_local_dataset(
            project_root=tmp_path,
            staging_path=tmp_path / "staging",
            final_path=tmp_path / "result",
            batches=(batch,),
            row_contract=contracts[0],
            row_set_contract=contracts[1],
            event=lambda _name: None,
        )
    assert not (tmp_path / "result").exists()


def test_retained_projection_requires_the_same_complete_keys(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    with pytest.raises(MaterializationError, match="shared row key"):
        _write(
            tmp_path,
            pa.table({"id": [1], "sum": [1]}),
            contracts,
            parts=(PartWriteSpec("state", "metric.state", 1, ("sum",)),),
        )
    assert not (tmp_path / "run").exists()


def test_stream_schema_cannot_change_between_batches(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    with pytest.raises(MaterializationError, match="schema changed"):
        storage.write_local_dataset(
            project_root=tmp_path,
            staging_path=tmp_path / "staging",
            final_path=tmp_path / "result",
            batches=(pa.record_batch({"id": [1]}), pa.record_batch({"id": [2.0]})),
            row_contract=contracts[0],
            row_set_contract=contracts[1],
            event=lambda _name: None,
        )


def test_preview_reads_only_twenty_rows_without_full_content_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contracts = _contracts((("id", "int64", False),))
    result = _write(tmp_path, pa.table({"id": list(range(50))}), contracts)
    monkeypatch.setattr(
        storage, "_hash_file", lambda _path: pytest.fail("preview must not hash complete data")
    )
    preview = storage.read_preview(
        project_root=tmp_path,
        receipt=result.primary_receipt,
        row_contract=contracts[0],
        row_set_contract=contracts[1],
    )
    assert preview.column("id").to_pylist() == list(range(20))


@pytest.mark.parametrize("target", ["manifest.json", "data.parquet"])
def test_mutated_selected_files_fail_without_origin_work(tmp_path: Path, target: str) -> None:
    contracts = _contracts((("id", "int64", False),))
    result = _write(tmp_path, pa.table({"id": [1, 2]}), contracts)
    path = tmp_path / result.primary_receipt.project_relative_path / target
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(IntegrityError):
        _read(tmp_path, result, contracts)


def test_foreign_and_symlink_paths_are_rejected(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "run").symlink_to(outside, target_is_directory=True)
    with pytest.raises(IntegrityError, match="symlink"):
        _write(tmp_path, pa.table({"id": [1]}), contracts)
    assert not list(outside.iterdir())


def test_selected_receipt_must_match_exact_metadata(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    result = _write(tmp_path, pa.table({"id": [1]}), contracts)
    changed = replace(result, primary_receipt=replace(result.primary_receipt, realized_row_count=2))
    with pytest.raises(IntegrityError, match="row count"):
        _read(tmp_path, changed, contracts)


def test_existing_final_output_is_never_replaced(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    _write(tmp_path, pa.table({"id": [1]}), contracts)
    with pytest.raises(MaterializationError, match="existing or identical"):
        _write(tmp_path, pa.table({"id": [2]}), contracts)


def test_nullable_int64_preserves_exact_large_values(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False), ("count", "int64", True)))
    table = pa.table({"id": [1, 2], "count": pa.array([2**63 - 1, None], type=pa.int64())})
    value = _read(tmp_path, _write(tmp_path, table, contracts), contracts)
    assert value.loc[0, "count"] == 2**63 - 1
    assert isinstance(value["count"].dtype, pd.ArrowDtype)
    assert pd.isna(value.loc[1, "count"])


@pytest.mark.parametrize("hook", ["before_rename", "after_rename"])
def test_finalize_fault_preserves_exact_journal_owned_location(tmp_path: Path, hook: str) -> None:
    contracts = _contracts((("id", "int64", False),))

    def event(name: str) -> None:
        if name == hook:
            raise RuntimeError("injected finalization failure")

    with pytest.raises(RuntimeError, match="injected"):
        storage.write_local_dataset(
            project_root=tmp_path,
            staging_path=tmp_path / "staging",
            final_path=tmp_path / "result",
            batches=pa.table({"id": [1]}).to_batches(),
            row_contract=contracts[0],
            row_set_contract=contracts[1],
            event=event,
        )
    assert (tmp_path / "staging").exists() == (hook == "before_rename")
    assert (tmp_path / "result").exists() == (hook == "after_rename")


def test_bounded_preview_does_not_read_unselected_corrupt_pages(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    result = _write(tmp_path, pa.table({"id": list(range(100))}), contracts)
    path = tmp_path / result.primary_receipt.project_relative_path / "data.parquet"
    with pq.ParquetFile(path) as parquet:
        column = parquet.metadata.row_group(parquet.metadata.num_row_groups - 1).column(0)
        offset = column.data_page_offset + column.total_compressed_size - 1
    payload = bytearray(path.read_bytes())
    payload[offset] ^= 0x40
    path.write_bytes(payload)
    preview = storage.read_preview(
        project_root=tmp_path,
        receipt=result.primary_receipt,
        row_contract=contracts[0],
        row_set_contract=contracts[1],
    )
    assert preview["id"].to_pylist() == list(range(20))
    with pytest.raises(IntegrityError):
        _read(tmp_path, result, contracts)


def test_accessed_page_corruption_fails_bounded_preview(tmp_path: Path) -> None:
    contracts = _contracts((("id", "int64", False),))
    result = _write(tmp_path, pa.table({"id": [1, 2]}), contracts)
    path = tmp_path / result.primary_receipt.project_relative_path / "data.parquet"
    with pq.ParquetFile(path) as parquet:
        column = parquet.metadata.row_group(0).column(0)
        offset = column.data_page_offset + column.total_compressed_size - 1
    payload = bytearray(path.read_bytes())
    payload[offset] ^= 0x40
    path.write_bytes(payload)
    with pytest.raises(IntegrityError):
        storage.read_preview(
            project_root=tmp_path,
            receipt=result.primary_receipt,
            row_contract=contracts[0],
            row_set_contract=contracts[1],
        )


def test_complete_wide_primary_and_binary_part_without_batch_rejection(tmp_path: Path) -> None:
    payload = "x" * (8_388_608 + 1)
    binary = b"y" * (8_388_608 + 1)
    contracts = _contracts((("id", "int64", False), ("payload", "string", True)))
    table = pa.table({"id": [1], "payload": [payload], "state": [binary]})
    result = _write(
        tmp_path,
        table,
        contracts,
        parts=(PartWriteSpec("binary_state", "fixture.binary", 1, ("id", "state")),),
    )
    assert _read(tmp_path, result, contracts)["payload"].tolist() == [payload]
    selected = result.retained_parts[0]
    batches = list(
        storage.read_part_batches(
            tmp_path,
            selected,
            expected_schema=table.select(["id", "state"]).schema,
        )
    )
    assert pa.Table.from_batches(batches)["state"].to_pylist() == [binary]


def test_writer_cleanup_failure_preserves_original_stream_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contracts = _contracts((("id", "int64", False), ("amount", "float64", True)))
    original = ValueError("stream failed")
    cause = RuntimeError("upstream failure")
    original_close = pq.ParquetWriter.close
    close_failures: list[str] = []

    def failed_close(writer: pq.ParquetWriter) -> None:
        original_close(writer)
        if not close_failures:
            close_failures.append("cleanup failed")
            raise RuntimeError("cleanup failed")

    def batches() -> Iterator[pa.RecordBatch]:
        yield pa.record_batch({"id": [1], "amount": [1.0]})
        raise original from cause

    monkeypatch.setattr(pq.ParquetWriter, "close", failed_close)
    with pytest.raises(ValueError) as caught:
        storage.write_local_dataset(
            project_root=tmp_path,
            staging_path=tmp_path / "run" / "staging",
            final_path=tmp_path / "artifacts" / "result",
            batches=batches(),
            row_contract=contracts[0],
            row_set_contract=contracts[1],
            event=lambda name: None,
        )
    assert close_failures == ["cleanup failed"]
    assert caught.value is original and caught.value.__cause__ is cause
    assert not (tmp_path / "artifacts" / "result").exists()
