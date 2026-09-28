"""Transient schema-first exchange for private graph method execution."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_plan import CheckRequirement
from marivo.analysis.core.model import CoordinateStatePart, Signature, part_role
from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import BatchStream
from marivo.analysis.materialization.reads import open_receipt_batch_stream
from marivo.analysis.materialization.storage import _hash_file, _open_payload
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.methods.state_validation import (
    coordinate_state_matches,
    difference_matches,
    state_matches,
)


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="a complete schema-bound private graph exchange",
        received=received,
        repair="Use the exact registered method, input binding and verified producer.",
        stage="graph_exchange",
    )


@dataclass(frozen=True, slots=True)
class PartContract:
    role: str
    schema: pa.Schema
    key_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.role
            or not isinstance(self.schema, pa.Schema)
            or len(set(self.key_fields)) != len(self.key_fields)
            or not set(self.key_fields) <= set(self.schema.names)
        ):
            raise _invalid("invalid part role, schema or complete key")


@dataclass(frozen=True, slots=True)
class ExchangeContract:
    signature: Signature
    method: MethodKey
    input_binding: str
    schema: pa.Schema
    key_fields: tuple[str, ...]
    parts: tuple[PartContract, ...] = ()
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()
    state_kind: str = "none"
    state_schema: pa.Schema | None = None
    pending_checks: tuple[CheckRequirement, ...] = ()
    allow_empty_singleton: bool = False

    def __post_init__(self) -> None:
        method_states = {
            "cell.difference": "difference",
            "metric.observe": "original_sum",
            "metric.ratio": "original_ratio",
            "state_rollup.ratio": "original_ratio",
            "metric.sum_zero": "original_sum_zero",
            "state_rollup.sum_zero": "original_sum_zero",
            "metric.count": "original_count",
            "state_rollup.count": "original_count",
            "state_rollup": "original_sum",
            "row.sum": "row_sum",
            "row.count": "row_count",
            "row.count_defined": "row_count_defined",
            "row.mean": "row_mean",
            "association.spearman": "spearman",
        }
        if (
            not isinstance(self.signature, Signature)
            or not isinstance(self.method, MethodKey)
            or not self.input_binding
            or not isinstance(self.schema, pa.Schema)
            or len(set(self.key_fields)) != len(self.key_fields)
            or not set(self.key_fields) <= set(self.schema.names)
            or len({part.role for part in self.parts}) != len(self.parts)
            or not {part_role(part) for part in self.signature.parts}
            <= {part.role for part in self.parts}
            or any(part.key_fields != self.key_fields for part in self.parts)
            or len({tag for tag, _ in self.cell_reasons}) != len(self.cell_reasons)
            or any(tag not in ("null", "undefined", "unknown") for tag, _ in self.cell_reasons)
            or self.state_kind
            not in (
                "none",
                "original_sum",
                "original_sum_zero",
                "original_count",
                "original_ratio",
                "row_sum",
                "row_count",
                "row_count_defined",
                "row_mean",
                "ratio",
                "difference",
                "spearman",
            )
            or (self.state_kind == "none") != (self.state_schema is None)
            or self.state_kind != method_states.get(self.method.name, "none")
            or any(not isinstance(item, CheckRequirement) for item in self.pending_checks)
            or type(self.allow_empty_singleton) is not bool
            or (
                self.allow_empty_singleton
                and (self.key_fields or self.method.name != "parts_transport")
            )
        ):
            raise _invalid("invalid method, binding, schema or ordered keys")
        if len(self.key_fields) != len(self.signature.domain.instance_key):
            raise _invalid("physical keys differ from the complete domain identity")
        names = self.schema.names
        if self.signature.quantity is not None and names[-3:] != [
            "value",
            "cell_tag",
            "cell_reason",
        ]:
            raise _invalid("quantity lacks the complete Cell vector")
        if self.state_schema is not None and (
            not isinstance(self.state_schema, pa.Schema)
            or self.state_schema.names != [*self.key_fields, "status"]
            or self.state_schema.field("status").type != pa.string()
            or any(
                self.state_schema.field(key).type != self.schema.field(key).type
                for key in self.key_fields
            )
        ):
            raise _invalid("method state lacks exact keys and status")


class _TableStream:
    def __init__(self, table: pa.Table) -> None:
        self._reader = table.to_reader()
        self.schema = table.schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return iter(self._reader)

    def close(self) -> None:
        self._reader.close()


class _PartStream:
    def __init__(self, batches: Iterator[pa.RecordBatch], schema: pa.Schema) -> None:
        self._batches = batches
        self.schema = schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return self._batches

    def close(self) -> None:
        close = getattr(self._batches, "close", None)
        if close is not None:
            close()


class CheckedStream:
    """Validate one producer; completion requires exhaustion and successful close."""

    def __init__(
        self,
        source: BatchStream,
        schema: pa.Schema,
        keys: tuple[str, ...],
        cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = (),
        validate_cells: bool = True,
    ) -> None:
        if not source.schema.equals(schema, check_metadata=False):
            source.close()
            raise _invalid("producer schema differs from selected exchange")
        self._source = source
        self.schema = schema
        self._keys = keys
        self._cell_reasons = dict(cell_reasons)
        self._validate_cell_values = validate_cells
        self._started = False
        self._closed = False
        self.completed = False

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._started or self._closed:
            raise _invalid("producer was consumed twice or already closed")
        self._started = True
        return self._batches()

    def _batches(self) -> Iterator[pa.RecordBatch]:
        seen_keys: set[tuple[object, ...]] = set()
        exhausted = False
        try:
            for batch in self._source:
                if not batch.schema.equals(self.schema, check_metadata=False):
                    raise _invalid("batch schema changed")
                if self._keys:
                    columns = tuple(batch.column(name) for name in self._keys)
                    for index in range(batch.num_rows):
                        key = tuple(column[index].as_py() for column in columns)
                        if any(value is None for value in key) or key in seen_keys:
                            raise _invalid("null or duplicate complete key")
                        seen_keys.add(key)
                if self._validate_cell_values and {"value", "cell_tag", "cell_reason"} <= set(
                    self.schema.names
                ):
                    self._validate_cells(batch)
                yield batch
            exhausted = True
        finally:
            self._closed = True
            self._source.close()
            self.completed = exhausted

    def _validate_cells(self, batch: pa.RecordBatch) -> None:
        values = batch.column("value")
        tags = batch.column("cell_tag")
        reasons = batch.column("cell_reason")
        for index in range(batch.num_rows):
            tag = tags[index].as_py()
            value = values[index]
            reason = reasons[index].as_py()
            if tag == "defined":
                if not value.is_valid or reason is not None:
                    raise _invalid("invalid Defined Cell")
            elif (
                tag not in ("null", "undefined", "unknown")
                or value.is_valid
                or reason not in self._cell_reasons.get(tag, ())
            ):
                raise _invalid("invalid non-Defined Cell")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._source.close()


@dataclass(frozen=True, slots=True)
class ExchangePart:
    role: str
    table: pa.Table


@dataclass(frozen=True, slots=True)
class ExchangeResult:
    contract: ExchangeContract
    primary: pa.Table
    parts: tuple[ExchangePart, ...]
    completed_checks: tuple[CompletedCheck, ...]
    method_state: pa.Table | None = None


@dataclass(frozen=True, slots=True)
class CompletedCheck:
    """Invocation-local proof, without durable Run or publication authority."""

    requirement: CheckRequirement
    result_digest: str


def collect(
    source: BatchStream,
    contract: ExchangeContract,
    *,
    parts: tuple[ExchangePart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
) -> ExchangeResult:
    """Exhaust one producer and validate every independently keyed state part."""
    stream = CheckedStream(source, contract.schema, contract.key_fields, contract.cell_reasons)
    try:
        primary = pa.Table.from_batches(tuple(stream), schema=contract.schema)
    finally:
        stream.close()
    if not stream.completed:
        raise _invalid("producer did not complete")
    if not contract.key_fields and primary.num_rows not in (
        (0, 1) if contract.allow_empty_singleton else (1,)
    ):
        raise _invalid("singleton result has an invalid row count")
    if tuple(part.role for part in parts) != tuple(part.role for part in contract.parts):
        raise _invalid("missing, reordered or extra method state part")
    primary_keys = _table_keys(primary, contract.key_fields)
    for declared, part in zip(contract.parts, parts, strict=True):
        if not part.table.schema.equals(declared.schema, check_metadata=False):
            raise _invalid(f"{declared.role} schema differs")
        if any(
            part.table.schema.field(key).type != primary.schema.field(key).type
            for key in declared.key_fields
        ):
            raise _invalid(f"{declared.role} key types differ")
        if _table_keys(part.table, declared.key_fields) != primary_keys:
            raise _invalid(f"{declared.role} complete keys differ")
    coordinate = next(
        (part for part in contract.signature.parts if isinstance(part, CoordinateStatePart)), None
    )
    if coordinate is not None:
        by_role = {part.role: part.table for part in parts}
        if "original_state" not in by_role or "coordinate_state" not in by_role:
            raise _invalid("coordinate partition lacks original or coordinate components")
        original = {
            tuple(row[name] for name in contract.key_fields): row
            for row in by_role["original_state"].to_pylist()
        }
        for row in by_role["coordinate_state"].to_pylist():
            key = tuple(row[name] for name in contract.key_fields)
            if not coordinate_state_matches(
                coordinate.components,
                coordinate.value_type,
                row.get("coordinate_state__groups"),
                original[key],
                coordinate.columns,
            ):
                raise _invalid("coordinate partition differs from its complete original state")
    if contract.state_schema is None:
        if method_state is not None:
            raise _invalid("unexpected method state vector")
    else:
        if (
            method_state is None
            or not method_state.schema.equals(contract.state_schema, check_metadata=False)
            or _table_keys(method_state, contract.key_fields) != primary_keys
        ):
            raise _invalid("missing or mismatched method state vector")
        states = {
            tuple(row[name] for name in contract.key_fields): row["status"]
            for row in method_state.to_pylist()
        }
        if contract.state_kind == "difference":
            endpoints = {
                part.role: {
                    tuple(row[name] for name in contract.key_fields): row
                    for row in part.table.to_pylist()
                }
                for part in parts
                if part.role in ("current_endpoint", "baseline_endpoint")
            }
            if set(endpoints) != {"current_endpoint", "baseline_endpoint"}:
                raise _invalid("missing ordered Difference endpoints")
            for row in primary.to_pylist():
                key = tuple(row[name] for name in contract.key_fields)
                if (
                    not difference_matches(
                        row,
                        endpoints["current_endpoint"][key],
                        endpoints["baseline_endpoint"][key],
                    )
                    or states[key] != row["cell_tag"]
                ):
                    raise _invalid("Difference endpoints and primary Cell disagree")
        else:
            _verify_single_state_part(contract, parts, primary, states)
    if any(
        not any(check.requirement == pending for pending in contract.pending_checks)
        for check in completed_checks
    ):
        raise _invalid("completed check is not pending in this exchange")
    if any(
        not any(check.requirement == pending for check in completed_checks)
        for pending in contract.pending_checks
    ):
        raise _invalid("method result retains an uncompleted check")
    return ExchangeResult(contract, primary, parts, completed_checks, method_state)


def _verify_single_state_part(
    contract: ExchangeContract,
    parts: tuple[ExchangePart, ...],
    primary: pa.Table,
    states: dict[tuple[object, ...], object],
) -> None:
    role = (
        "pair_counts"
        if contract.state_kind == "spearman"
        else "original_state"
        if contract.state_kind
        in ("original_sum", "original_sum_zero", "original_count", "original_ratio")
        else "row_state"
    )
    state_part = next((part.table for part in parts if part.role == role), None)
    if state_part is None:
        raise _invalid("missing required numerical state part")
    keyed_parts = {
        tuple(row[name] for name in contract.key_fields): row for row in state_part.to_pylist()
    }
    for row in primary.to_pylist():
        key = tuple(row[name] for name in contract.key_fields)
        if not state_matches(contract.state_kind, row, keyed_parts[key]):
            raise _invalid("method part and primary numerical state disagree")
        status = states[key]
        if not isinstance(status, str) or not status:
            raise _invalid("missing method state status")
        if contract.state_kind == "spearman":
            if (
                row.get("status") != status
                or (status == "valid") != (row["cell_tag"] == "defined")
                or (status != "valid" and row["cell_reason"] != status)
            ):
                raise _invalid("Spearman state and primary Cell disagree")
        elif status != row.get("cell_tag"):
            raise _invalid("method state and primary Cell disagree")


def _table_keys(table: pa.Table, fields: tuple[str, ...]) -> set[tuple[object, ...]]:
    columns = tuple(table.column(name) for name in fields)
    result: set[tuple[object, ...]] = set()
    for index in range(table.num_rows):
        key = tuple(column[index].as_py() for column in columns)
        if any(value is None for value in key) or key in result:
            raise _invalid("null or duplicate complete part key")
        result.add(key)
    return result


def from_pandas(
    frame: pd.DataFrame,
    contract: ExchangeContract,
    *,
    parts: tuple[ExchangePart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
) -> ExchangeResult:
    """Round-trip a selected local method through the common Arrow contract."""
    try:
        table = pa.Table.from_pandas(frame, schema=contract.schema, preserve_index=False, safe=True)
    except (pa.ArrowException, ValueError, TypeError) as error:
        raise _invalid(f"lossy pandas-to-Arrow conversion: {type(error).__name__}") from error
    return collect(
        _TableStream(table),
        contract,
        parts=parts,
        completed_checks=completed_checks,
        method_state=method_state,
    )


def from_arrow(
    table: pa.Table,
    contract: ExchangeContract,
    *,
    parts: tuple[ExchangePart, ...] = (),
    completed_checks: tuple[CompletedCheck, ...] = (),
    method_state: pa.Table | None = None,
) -> ExchangeResult:
    """Validate an owned Arrow result through the same producer contract."""
    return collect(
        _TableStream(table),
        contract,
        parts=parts,
        completed_checks=completed_checks,
        method_state=method_state,
    )


@dataclass(frozen=True, slots=True)
class VerifiedFixedInput:
    """Invocation-owned v7 input, already exhausted by the receipt owner."""

    artifact_ref: str
    receipt: LocalReceipt
    result: ExchangeResult


@dataclass(frozen=True, slots=True)
class FixedPartInput:
    role: str
    receipt: LocalReceipt


@dataclass(frozen=True, slots=True)
class FixedInput:
    artifact_ref: str
    root: Path
    receipt: LocalReceipt
    row: DatasetRowContract
    rows: DatasetRowSetContract
    parts: tuple[FixedPartInput, ...]


def from_receipts(selected: FixedInput, contract: ExchangeContract) -> ExchangeResult:
    """Exhaust exact local receipts without opening a source or native scan."""
    if selected.artifact_ref != contract.input_binding:
        raise _invalid("fixed receipt does not match the exact input binding")
    if tuple(item.role for item in selected.parts) != tuple(part.role for part in contract.parts):
        raise _invalid("missing, reordered or extra fixed part receipt")
    checked_parts: list[ExchangePart] = []
    for item, declared in zip(selected.parts, contract.parts, strict=True):
        reader = _PartStream(
            _verified_part_batches(selected.root, item.receipt, declared.schema),
            declared.schema,
        )
        stream = CheckedStream(reader, declared.schema, declared.key_fields)
        try:
            table = pa.Table.from_batches(tuple(stream), schema=declared.schema)
        finally:
            stream.close()
        if not stream.completed:
            raise _invalid(f"{declared.role} receipt did not complete")
        checked_parts.append(ExchangePart(item.role, table))
    primary_reader = open_receipt_batch_stream(
        selected.root, selected.receipt, selected.row, selected.rows
    )
    return collect(primary_reader, contract, parts=tuple(checked_parts))


def _verified_part_batches(
    root: Path, receipt: LocalReceipt, schema: pa.Schema
) -> Iterator[pa.RecordBatch]:
    """Verify a physical part without borrowing a legacy method codec."""
    parquet, path = _open_payload(root, receipt)
    rows = 0
    try:
        if (
            not parquet.schema_arrow.equals(schema, check_metadata=False)
            or receipt.schema_fingerprint
            != hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()
        ):
            raise _invalid("fixed part schema or fingerprint differs")
        for batch in parquet.iter_batches(batch_size=1024):
            rows += batch.num_rows
            yield batch
        if (
            rows != receipt.realized_row_count
            or _hash_file(path) != receipt.bytes_hash
            or receipt.file_manifest[0].sha256 != receipt.bytes_hash
        ):
            raise _invalid("fixed part content or cardinality differs")
    finally:
        parquet.close()
