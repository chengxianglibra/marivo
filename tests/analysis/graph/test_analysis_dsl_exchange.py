"""Counterexamples for the inactive Analysis DSL exchange and stream boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.graph_exchange import (
    ArrowPart,
    ExchangeResult,
    PartContract,
    to_arrow,
)
from marivo.analysis.materialization.graph_storage import write_table
from marivo.refs import ref


@dataclass(frozen=True)
class _CellPolicy:
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class _TestBinding:
    row: d.DatasetRowContract
    rows: d.DatasetRowSetContract
    schema: pa.Schema
    method: _CellPolicy
    parts: tuple[PartContract, ...]


@dataclass(frozen=True)
class PartWriteSpec:
    role: str
    contract_id: str
    contract_version: int
    column_names: tuple[str, ...]


@dataclass(frozen=True)
class _PartWrite:
    storage_receipt: LocalReceipt


@dataclass(frozen=True)
class _WrittenExchange:
    primary_receipt: LocalReceipt
    retained_parts: tuple[_PartWrite, ...]


def _arrow_table(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    return to_arrow(frame, schema)


def _arrow_parts(result: ExchangeResult) -> tuple[ArrowPart, ...]:
    return tuple(
        ArrowPart(part.role, _arrow_table(part.table, declared.schema))
        for part, declared in zip(result.parts, result.contract.parts, strict=True)
    )


def _arrow_method_state(result: ExchangeResult) -> pa.Table | None:
    if result.method_state is None:
        return None
    assert result.contract.state_schema is not None
    return _arrow_table(result.method_state, result.contract.state_schema)


def _write_exchange(
    *,
    project_root: Path,
    staging_path: Path,
    final_path: Path,
    batches: tuple[pa.RecordBatch, ...],
    row_contract: d.DatasetRowContract,
    row_set_contract: d.DatasetRowSetContract,
    parts: tuple[PartWriteSpec, ...],
    event: object,
) -> _WrittenExchange:
    table = pa.Table.from_batches(batches)
    names = tuple(field.name for field in row_contract.schema.columns)
    primary = write_table(
        project_root, staging_path / "primary", final_path / "primary", table.select(names)
    )
    retained = tuple(
        _PartWrite(
            write_table(
                project_root,
                staging_path / part.role,
                final_path / part.role,
                table.select(part.column_names),
            )
        )
        for part in parts
    )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.rename(final_path)
    return _WrittenExchange(primary, retained)


def _binding(value_type: pa.DataType | None = None) -> _TestBinding:
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
    method = _CellPolicy(
        (
            ("null", ("source_null",)),
            ("undefined", ("zero_denominator",)),
            ("unknown", ("coverage_unknown",)),
        )
    )
    part_schema = pa.schema(
        [pa.field("id", pa.int64(), nullable=False), pa.field("state_sum", value_type)]
    )
    return _TestBinding(
        row=row,
        rows=rows,
        method=method,
        schema=schema,
        parts=(PartContract("sum", part_schema, ("id",)),),
    )


def _batch(binding: _TestBinding) -> pa.RecordBatch:
    return pa.RecordBatch.from_arrays(
        [
            pa.array([1, 2, 3, 4], type=pa.int64()),
            pa.array([2**53 + 7, None, None, None], type=binding.schema.field("value").type),
            pa.array(["defined", "null", "undefined", "unknown"]),
            pa.array([None, "source_null", "zero_denominator", "coverage_unknown"]),
        ],
        schema=binding.schema,
    )


def _exchange_signature() -> Signature:
    domain = DomainSignature(
        Binding("r43", "sales", "customers", "current"),
        "entity",
        (Coordinate(ref.entity("sales.customers"), "id", "identity"),),
        (Coordinate(ref.entity("sales.customers"), "id", "identity"),),
        "customers",
    )
    return Signature(
        domain,
        ObservedQuantity(
            "revenue",
            ref.metric("sales.revenue"),
            "metric-v1",
            "CNY",
            "current",
            "orders",
            "strict",
            "sum@v1",
        ),
    )
