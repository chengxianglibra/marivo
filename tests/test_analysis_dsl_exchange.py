"""Counterexamples for the inactive Analysis DSL exchange and stream boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import ibis
import pyarrow as pa
import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.materialization import storage
from marivo.analysis.materialization.contracts import (
    ExchangeBinding,
    ExchangePart,
    canonical_json,
    decode_exchange,
    encode_exchange,
    exchange_payload,
)
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.execution import ValidatedExchangeStream
from marivo.analysis.materialization.ibis_batches import IbisBatchStream, ProjectedBatchStream
from marivo.analysis.materialization.reads import open_receipt_batch_stream
from marivo.analysis.materialization.storage import PartWriteSpec, check_exchange_parts
from marivo.analysis.observation.contracts import ContractEvidence
from marivo.analysis.operators.registry import MethodContract
from marivo.refs import ref


def _binding(value_type: pa.DataType | None = None) -> ExchangeBinding:
    if value_type is None:
        value_type = pa.int64()
    key = d._make_field_id("id")
    domain = d._entity_domain(ref.entity("sales.customers"), key)
    quantity = d._observed_quantity(domain, "metric:sales.revenue", (), ("sum",))
    value_kind = (
        "decimal(18, 2)"
        if pa.types.is_decimal(value_type)
        else "timestamp"
        if pa.types.is_timestamp(value_type)
        else "int64"
    )
    fields = (
        ("id", "int64", False),
        ("value", value_kind, True),
        ("cell_tag", "string", False),
        ("cell_reason", "string", True),
    )
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d.DatasetShapeId(
            _token=d._CORE_TOKEN,
            family_id="test",
            local_shape_id="dsl_exchange",
            semantic_version=1,
        ),
        schema=d._make_schema(
            tuple(
                d.DatasetField(
                    _token=d._CORE_TOKEN,
                    field_id=d._make_field_id(name),
                    name=name,
                    role_id="value",
                    identity=d._generated_identity(d._make_field_id(name)),
                    derivation_identity=f"dsl.{name}",
                    logical_type_id=kind,
                    physical_type_state=d._ResolvedPhysicalType(
                        _token=d._CORE_TOKEN, physical_type_id=kind
                    ),
                    nullable=nullable,
                )
                for name, kind, nullable in fields
            )
        ),
        coordinate_field_ids=(key,),
        key_field_ids=(key,),
        family_semantics=d._complete_from_schema(),
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._unordered_ordering(),
    )
    schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("value", value_type, nullable=True),
            pa.field("cell_tag", pa.string(), nullable=False),
            pa.field("cell_reason", pa.string(), nullable=True),
        ]
    )
    method = MethodContract(
        method_id="dsl.observe_sum",
        version=1,
        input_kinds=("observed",),
        input_domains=("entity",),
        output_kind="observed",
        domain_policy="same",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="int64_checked",
        capabilities=("original_state_reduction", "part_transport"),
        part_effect="merge_original",
        required_parts=("sum",),
        required_checks=(),
        continuations=("rollup",),
        cell_reasons=(
            ("null", ("source_null",)),
            ("undefined", ("zero_denominator",)),
            ("unknown", ("coverage_unknown",)),
        ),
    )
    part_schema = pa.schema(
        [pa.field("id", pa.int64(), nullable=False), pa.field("state_sum", value_type)]
    )
    return ExchangeBinding(
        row=row,
        rows=rows,
        domain=domain,
        quantity=quantity,
        method=method,
        evidence=ContractEvidence(("entity_identity",), (), (), ("coverage",)),
        input_binding="binding.sales",
        schema=schema,
        parts=(ExchangePart("sum", "dsl.sum", 1, part_schema, ("id",)),),
    )


def _batch(binding: ExchangeBinding) -> pa.RecordBatch:
    return pa.RecordBatch.from_arrays(
        [
            pa.array([1, 2, 3, 4], type=pa.int64()),
            pa.array([2**53 + 7, None, None, None], type=binding.schema.field("value").type),
            pa.array(["defined", "null", "undefined", "unknown"]),
            pa.array([None, "source_null", "zero_denominator", "coverage_unknown"]),
        ],
        schema=binding.schema,
    )


class _MemoryStream:
    def __init__(
        self,
        schema: pa.Schema,
        batches: tuple[pa.RecordBatch, ...],
        *,
        fail: bool = False,
        close_fail: bool = False,
    ):
        self.schema = schema
        self.batches = batches
        self.fail = fail
        self.close_fail = close_fail
        self.closed = False

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        yield from self.batches
        if self.fail:
            raise ValueError("private-key-canary")

    def close(self) -> None:
        self.closed = True
        if self.close_fail:
            raise ValueError("private-close-canary")


def test_exchange_metadata_round_trip_is_versioned_and_does_not_promote_evidence() -> None:
    binding = _binding()
    encoded = encode_exchange(binding)
    recovered = decode_exchange(encoded)
    assert recovered == binding.record
    assert recovered.completed_checks == ()
    assert recovered.pending_checks == ("coverage",)
    assert encode_exchange(binding) == encoded
    payload = exchange_payload(binding)
    payload["schema"] = "marivo.analysis_exchange/v2"
    with pytest.raises(IntegrityError):
        decode_exchange(canonical_json(payload))
    payload = exchange_payload(binding)
    payload["input_binding"] = "binding.other"
    with pytest.raises(IntegrityError):
        binding.require_record(decode_exchange(canonical_json(payload)))
    payload = exchange_payload(binding)
    payload["completed_checks"] = ["coverage"]
    payload["pending_checks"] = []
    with pytest.raises(IntegrityError):
        binding.require_record(decode_exchange(canonical_json(payload)))
    with pytest.raises(DatasetRegistrationError):
        replace(
            binding.method,
            cell_reasons=(("undefined", ("zero_denominator",)), ("undefined", ("other",))),
        )


def test_four_cells_nullable_int64_and_empty_stream_share_one_schema() -> None:
    binding = _binding()
    source = _MemoryStream(binding.schema, (_batch(binding),))
    stream = ValidatedExchangeStream(source, binding)
    assert stream.schema == binding.schema
    batches = list(stream)
    assert len(batches) == 1
    assert batches[0].column("value").to_pylist() == [2**53 + 7, None, None, None]
    assert source.closed and stream.completed

    empty_source = _MemoryStream(binding.schema, ())
    empty = ValidatedExchangeStream(empty_source, binding)
    assert empty.schema == binding.schema
    assert [batch.num_rows for batch in empty] == [0]
    assert empty_source.closed and empty.completed


def test_real_ibis_and_receipt_producers_preserve_the_same_cell_vector(tmp_path: Path) -> None:
    binding = _binding()
    batch = _batch(binding)
    backend = ibis.duckdb.connect(":memory:")
    try:
        expression = backend.create_table("exchange", obj=pa.Table.from_batches((batch,)))
        native = backend.to_pyarrow_batches(expression.order_by("id"), chunk_size=2)
        projected = ProjectedBatchStream(
            IbisBatchStream(native, native.schema), binding.schema, binding.schema
        )
        source = ValidatedExchangeStream(projected, binding)
        source_table = pa.Table.from_batches(tuple(source), schema=binding.schema)
        assert source.completed
        assert source_table["value"].to_pylist() == [2**53 + 7, None, None, None]
        empty = pa.RecordBatch.from_arrays(
            [pa.array([], type=field.type) for field in binding.schema], schema=binding.schema
        )
        empty_expression = backend.create_table(
            "empty_exchange", obj=pa.Table.from_batches((empty,))
        )
        empty_native = backend.to_pyarrow_batches(empty_expression, chunk_size=2)
        empty_source = ValidatedExchangeStream(
            ProjectedBatchStream(
                IbisBatchStream(empty_native, empty_native.schema), binding.schema, binding.schema
            ),
            binding,
        )
        assert [item.num_rows for item in empty_source] == [0]
        assert empty_source.completed
    finally:
        backend.disconnect()

    wide = batch.append_column("state_sum", pa.array([7, 8, 9, 10], type=pa.int64()))
    written = storage.write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "real-producer-stage",
        final_path=tmp_path / "real-producer-artifact",
        batches=(wide,),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    retained = ValidatedExchangeStream(
        open_receipt_batch_stream(tmp_path, written.primary_receipt, binding.row, binding.rows),
        binding,
    )
    receipt_table = pa.Table.from_batches(tuple(retained), schema=binding.schema)
    assert retained.completed
    assert receipt_table.equals(source_table)


def test_ibis_projection_rejects_physical_type_drift_before_cast() -> None:
    binding = _binding()
    narrow_schema = binding.schema.set(1, pa.field("value", pa.int32()))
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([1], type=pa.int64()),
            pa.array([7], type=pa.int32()),
            pa.array(["defined"]),
            pa.array([None], type=pa.string()),
        ],
        schema=narrow_schema,
    )

    class DriftingReader(IbisBatchStream):
        def __init__(self) -> None:
            self.closed = False

        @property
        def schema(self) -> pa.Schema:
            return binding.schema

        def __iter__(self) -> Iterator[pa.RecordBatch]:
            yield pa.RecordBatch.from_arrays(
                [
                    pa.array([0], type=pa.int64()),
                    pa.array([6], type=pa.int64()),
                    pa.array(["defined"]),
                    pa.array([None], type=pa.string()),
                ],
                schema=binding.schema,
            )
            yield batch

        def close(self) -> None:
            self.closed = True

    native = DriftingReader()
    projected = ProjectedBatchStream(native, binding.schema, binding.schema)
    stream = ValidatedExchangeStream(projected, binding)
    with pytest.raises(MaterializationError, match="physical types"):
        list(stream)
    assert native.closed and not stream.completed


@pytest.mark.parametrize(
    ("physical", "value"),
    [
        (pa.decimal128(18, 2), Decimal("9007199254740993.25")),
        (
            pa.timestamp("us", tz="UTC"),
            datetime(2026, 9, 24, 0, 30, 12, 123456, tzinfo=timezone.utc),
        ),
    ],
)
def test_real_ibis_exchange_retains_decimal_or_timestamp_precision(
    physical: pa.DataType, value: Decimal | datetime
) -> None:
    binding = _binding(physical)
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([1], type=pa.int64()),
            pa.array([value], type=physical),
            pa.array(["defined"]),
            pa.array([None], type=pa.string()),
        ],
        schema=binding.schema,
    )
    backend = ibis.duckdb.connect(":memory:")
    try:
        expression = backend.create_table("exchange", obj=pa.Table.from_batches((batch,)))
        native = backend.to_pyarrow_batches(expression, chunk_size=1)
        projected = ProjectedBatchStream(
            IbisBatchStream(native, native.schema), binding.schema, binding.schema
        )
        stream = ValidatedExchangeStream(projected, binding)
        result = pa.Table.from_batches(tuple(stream), schema=binding.schema)
        assert stream.completed
        assert result["value"].to_pylist() == [value]
        assert result.schema.field("value").type == physical
    finally:
        backend.disconnect()
    with pytest.raises(MaterializationError):
        binding.require_method_type()


def test_singleton_domain_uses_one_row_without_a_fabricated_key() -> None:
    base = _binding()
    domain = d._singleton_domain(base.domain)
    row = d._make_row_contract(
        schema_version=1,
        shape_id=base.row.shape_id,
        schema=d._make_schema(base.row.schema.columns[1:]),
        coordinate_field_ids=(),
        key_field_ids=(),
        family_semantics=d._complete_from_schema(),
    )
    binding = replace(
        base,
        row=row,
        rows=d._make_row_set_contract(
            schema_version=1,
            cardinality=d._singleton_cardinality(),
            ordering=d._unordered_ordering(),
        ),
        domain=domain,
        quantity=d._observed_quantity(domain, "metric:sales.revenue", (), ("sum",)),
        method=replace(base.method, input_domains=("singleton",)),
        schema=pa.schema(list(base.schema)[1:]),
        parts=(
            ExchangePart("sum", "dsl.sum", 1, pa.schema([pa.field("state_sum", pa.int64())]), ()),
        ),
    )
    batch = pa.RecordBatch.from_arrays(
        [pa.array([7]), pa.array(["defined"]), pa.array([None], type=pa.string())],
        schema=binding.schema,
    )
    stream = ValidatedExchangeStream(_MemoryStream(binding.schema, (batch,)), binding)
    primary = pa.Table.from_batches(list(stream), schema=binding.schema)
    check_exchange_parts(
        primary,
        {"sum": pa.table({"state_sum": pa.array([7])}, schema=binding.parts[0].schema)},
        binding,
    )
    assert stream.completed


@pytest.mark.parametrize(
    "values,tags,reasons",
    [
        ([None], ["defined"], [None]),
        ([17], ["null"], ["source_null"]),
        ([None], ["undefined"], [None]),
        ([None], ["unknown"], ["invented_reason"]),
        ([None], ["missing"], ["source_null"]),
    ],
)
def test_malformed_cells_fail_without_exposing_values(
    values: list[int | None], tags: list[str], reasons: list[str | None]
) -> None:
    binding = _binding()
    batch = pa.RecordBatch.from_arrays(
        [pa.array([12345]), pa.array(values, type=pa.int64()), pa.array(tags), pa.array(reasons)],
        schema=binding.schema,
    )
    source = _MemoryStream(binding.schema, (batch,))
    stream = ValidatedExchangeStream(source, binding)
    with pytest.raises(MaterializationError) as caught:
        list(stream)
    assert "12345" not in str(caught.value)
    assert source.closed and not stream.completed


def test_schema_drift_unsorted_keys_early_close_and_native_failure() -> None:
    binding = _binding()
    valid = _batch(binding)
    drift = valid.set_column(1, "value", pa.array([1, 2, 3, 4], type=pa.int32()))
    for batches in ((valid, drift), (valid.take(pa.array([1, 0, 2, 3])),)):
        source = _MemoryStream(binding.schema, batches)
        stream = ValidatedExchangeStream(source, binding)
        with pytest.raises(MaterializationError):
            list(stream)
        assert source.closed and not stream.completed

    source = _MemoryStream(binding.schema, (valid, valid))
    stream = ValidatedExchangeStream(source, binding)
    iterator = iter(stream)
    next(iterator)
    stream.close()
    assert source.closed and not stream.completed

    source = _MemoryStream(binding.schema, (valid,), fail=True)
    stream = ValidatedExchangeStream(source, binding)
    with pytest.raises(MaterializationError) as caught:
        list(stream)
    assert "private-key-canary" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert source.closed and not stream.completed

    source = _MemoryStream(binding.schema, (valid,), close_fail=True)
    stream = ValidatedExchangeStream(source, binding)
    with pytest.raises(MaterializationError) as caught:
        list(stream)
    assert "private-close-canary" not in str(caught.value)
    assert not stream.completed

    class CancelledStream(_MemoryStream):
        def __iter__(self) -> Iterator[pa.RecordBatch]:
            yield valid
            raise asyncio.CancelledError

    cancelled_source = CancelledStream(binding.schema, ())
    cancelled = ValidatedExchangeStream(cancelled_source, binding)
    with pytest.raises(asyncio.CancelledError):
        list(cancelled)
    assert cancelled_source.closed and not cancelled.completed


def test_decimal_codec_precision_and_method_admission_remain_separate() -> None:
    binding = _binding(pa.decimal128(18, 2))
    assert decode_exchange(encode_exchange(binding)) == binding.record
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([1]),
            pa.array([Decimal("9007199254740993.25")], type=pa.decimal128(18, 2)),
            pa.array(["defined"]),
            pa.array([None], type=pa.string()),
        ],
        schema=binding.schema,
    )
    batches = list(ValidatedExchangeStream(_MemoryStream(binding.schema, (batch,)), binding))
    assert batches[0]["value"].to_pylist() == [Decimal("9007199254740993.25")]
    with pytest.raises(MaterializationError):
        binding.require_method_type()


def test_timestamp_timezone_and_microsecond_precision_survive_governed_parquet(
    tmp_path: Path,
) -> None:
    from zoneinfo import ZoneInfo

    binding = _binding(pa.timestamp("us", tz="Asia/Shanghai"))
    moment = datetime(2026, 9, 24, 8, 30, 12, 123456, tzinfo=ZoneInfo("Asia/Shanghai"))
    schema = binding.schema.append(pa.field("state_sum", pa.timestamp("us", tz="Asia/Shanghai")))
    wide = pa.RecordBatch.from_arrays(
        [
            pa.array([1], type=pa.int64()),
            pa.array([moment], type=schema.field("value").type),
            pa.array(["defined"]),
            pa.array([None], type=pa.string()),
            pa.array([moment], type=schema.field("state_sum").type),
        ],
        schema=schema,
    )
    result = storage.write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=(wide,),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    stream = ValidatedExchangeStream(
        open_receipt_batch_stream(tmp_path, result.primary_receipt, binding.row, binding.rows),
        binding,
    )
    batches = list(stream)
    assert batches[1]["value"].to_pylist() == [moment]
    assert batches[1].schema.field("value").type == pa.timestamp("us", tz="Asia/Shanghai")
    assert stream.completed
    drift_schema = binding.schema.set(1, pa.field("value", pa.timestamp("ms", tz="Asia/Shanghai")))
    drift = pa.RecordBatch.from_arrays(
        [
            pa.array([1]),
            pa.array([moment], type=drift_schema.field("value").type),
            pa.array(["defined"]),
            pa.array([None], type=pa.string()),
        ],
        schema=drift_schema,
    )
    with pytest.raises(MaterializationError):
        list(ValidatedExchangeStream(_MemoryStream(binding.schema, (drift,)), binding))
    with pytest.raises(MaterializationError):
        binding.require_method_type()


def test_governed_parquet_matches_source_and_parts_join_by_keys(tmp_path: Path) -> None:
    binding = _binding()
    batch = _batch(binding)
    wide = pa.RecordBatch.from_arrays(
        [*batch.columns, pa.array([7, 8, 9, 10], type=pa.int64())],
        schema=binding.schema.append(pa.field("state_sum", pa.int64())),
    )
    result = storage.write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=(wide,),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    receipt_stream = open_receipt_batch_stream(
        tmp_path, result.primary_receipt, binding.row, binding.rows
    )
    stream = ValidatedExchangeStream(receipt_stream, binding)
    assert stream.schema == binding.schema
    primary = pa.Table.from_batches(list(stream), schema=binding.schema)
    part = result.retained_parts[0]
    retained_binding = replace(
        binding, storage_receipt=result.primary_receipt, retained_parts=result.retained_parts
    )
    assert decode_exchange(encode_exchange(retained_binding)) == retained_binding.record
    with pytest.raises(IntegrityError):
        replace(retained_binding, retained_parts=(replace(part, role="wrong"),))
    part_batches = tuple(
        storage.read_part_batches(tmp_path, part, expected_schema=binding.parts[0].schema)
    )
    part_table = pa.Table.from_batches(part_batches, schema=binding.parts[0].schema)
    shuffled = part_table.take(pa.array([2, 0, 3, 1]))
    aligned = check_exchange_parts(primary, {"sum": shuffled}, binding)
    assert aligned["sum"]["state_sum"].to_pylist() == [7, 8, 9, 10]
    assert primary["value"].to_pylist() == batch["value"].to_pylist()
    assert stream.completed
    with pytest.raises(MaterializationError):
        check_exchange_parts(primary, {}, binding)
    with pytest.raises(MaterializationError):
        check_exchange_parts(primary, {"sum": part_table.slice(1)}, binding)

    damaged = replace(result.primary_receipt, bytes_hash="0" * 64)
    damaged_stream = ValidatedExchangeStream(
        open_receipt_batch_stream(tmp_path, damaged, binding.row, binding.rows), binding
    )
    with pytest.raises(IntegrityError):
        list(damaged_stream)
    assert not damaged_stream.completed


def test_zero_row_parquet_keeps_schema_and_early_close_is_not_completion(tmp_path: Path) -> None:
    binding = _binding()
    wide_schema = binding.schema.append(pa.field("state_sum", pa.int64()))
    empty = pa.RecordBatch.from_arrays(
        [pa.array([], type=field.type) for field in wide_schema], schema=wide_schema
    )
    result = storage.write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "run" / "staging",
        final_path=tmp_path / "artifacts" / "result",
        batches=(empty,),
        row_contract=binding.row,
        row_set_contract=binding.rows,
        parts=(PartWriteSpec("sum", "dsl.sum", 1, ("id", "state_sum")),),
        event=lambda _name: None,
    )
    early = ValidatedExchangeStream(
        open_receipt_batch_stream(tmp_path, result.primary_receipt, binding.row, binding.rows),
        binding,
    )
    next(iter(early))
    early.close()
    assert not early.completed
    complete = ValidatedExchangeStream(
        open_receipt_batch_stream(tmp_path, result.primary_receipt, binding.row, binding.rows),
        binding,
    )
    assert complete.schema == binding.schema
    assert [batch.num_rows for batch in complete] == [0]
    assert complete.completed
