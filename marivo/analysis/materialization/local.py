"""Runtime-owned complete input, allocation and output guards for local methods."""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field

import pandas as pd
import pyarrow as pa

from marivo._compat import Never
from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    DatasetRowSetContract,
    _bool_tuple_arity,
    _bool_tuple_value,
    _EntityFieldIdentity,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import (
    _matches_type,
    _normalize_batch,
    _realized_schema,
    _RowValidator,
    _to_dataframe,
    _value,
)
from marivo.analysis.operators.row import (
    PartFrame,
    RowCall,
    execute_row,
    frame_comparator,
    select_parts,
)
from marivo.analysis.operators.row_values import frame_keys


@dataclass(frozen=True, slots=True)
class LocalPolicy:
    max_input_rows: int = 100_000
    max_input_bytes: int = 67_108_864
    max_output_rows: int = 100_000
    max_output_bytes: int = 67_108_864
    max_batch_bytes: int = 8_388_608
    max_intermediate_bytes: int = 268_435_456
    max_worker_rss: int = 536_870_912
    max_method_rows: int = 100_000
    deadline_seconds: float = 60.0

    def __post_init__(self) -> None:
        integers = (
            self.max_input_rows,
            self.max_input_bytes,
            self.max_output_rows,
            self.max_output_bytes,
            self.max_batch_bytes,
            self.max_intermediate_bytes,
            self.max_worker_rss,
            self.max_method_rows,
        )
        if any(type(value) is not int or value <= 0 for value in integers) or (
            type(self.deadline_seconds) not in (int, float)
            or not math.isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0
        ):
            fail(
                "positive finite runtime budgets",
                "invalid local policy",
                "implementation_registration",
            )


def fail(expected: str, received: str, stage: str = "transfer_guard") -> Never:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair="Narrow the input or apply an admitted source reduction before this local method.",
        stage=stage,
    )


@dataclass(slots=True)
class LocalBudget:
    policy: LocalPolicy
    deadline: float
    input_bytes: int = 0
    live_bytes: int = 0

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            fail("transfer and local work within the deadline", "local deadline exceeded")

    def input(self, byte_count: int) -> None:
        self.check()
        self.input_bytes += byte_count
        if self.input_bytes > self.policy.max_input_bytes:
            fail(
                "complete combined primary and part inputs within budget", "combined input overflow"
            )
        # All collected roles remain live until their consumer is admitted.
        self.allocation(self.input_bytes)

    def allocation(self, byte_count: int) -> None:
        self.check()
        if self.live_bytes + byte_count > self.policy.max_intermediate_bytes:
            fail(
                "bounded live conversion and method allocations", "intermediate allocation overflow"
            )


def collect_primary(
    batches: Iterable[pa.RecordBatch],
    row: DatasetRowContract,
    rows: DatasetRowSetContract,
    budget: LocalBudget,
) -> pa.Table:
    validator = _RowValidator(row, rows, source_key_validation=True)
    retained: list[pa.RecordBatch] = []
    schema: pa.Schema | None = None
    decoded = 0
    for incoming in batches:
        budget.check()
        batch = _normalize_batch(incoming, budget.policy.max_batch_bytes)
        _realized_schema(row.schema, batch.schema)
        if schema is not None and not schema.equals(batch.schema, check_metadata=False):
            fail("one exact Arrow schema across all input batches", "input schema changed")
        schema = batch.schema
        if validator.count + batch.num_rows > budget.policy.max_input_rows:
            fail("complete input within row budget", "input row overflow")
        budget.input(batch.nbytes)
        decoded += batch.nbytes
        budget.allocation(decoded)
        validator.accept(batch)
        retained.append(batch)
    validator.finish()
    if schema is None:
        fail("complete Arrow input including an empty schema batch", "missing input schema")
    table = pa.Table.from_batches(retained, schema=schema)
    # The stream order is not a proof of key uniqueness for ranked inputs.
    keys = [field.name for field in row.schema.columns if field.field_id in row.key_field_ids]
    if keys:
        budget.allocation(table.nbytes + table.num_rows * (160 + 64 * len(keys)))
        seen: set[tuple[object, ...]] = set()
        for index in range(table.num_rows):
            key = tuple(_value(table[name][index]) for name in keys)
            if key in seen:
                fail("unique complete input row keys", "duplicate input key")
            seen.add(key)
    return table


def to_local_frame(table: pa.Table, row: DatasetRowContract, budget: LocalBudget) -> pd.DataFrame:
    # Conversion may materialize Python identities and index/column objects.
    estimate = table.nbytes * 4 + table.num_rows * (256 + 64 * table.num_columns)
    budget.allocation(estimate)
    frame = _to_dataframe(table, row)
    size = frame_bytes(frame)
    budget.allocation(table.nbytes + size)
    if budget.live_bytes + size > budget.policy.max_input_bytes:
        fail("complete converted input within decoded budget", "conversion overflow")
    budget.live_bytes += size
    return frame


def collect_part(
    batches: Iterable[pa.RecordBatch],
    schema: pa.Schema,
    keys: tuple[str, ...],
    budget: LocalBudget,
) -> pa.Table:
    """Validate a complete required role against its owner's exact physical contract."""
    collector = PartCollector(schema, keys, budget)
    for incoming in batches:
        collector.accept(incoming)
    return collector.finish()


@dataclass(slots=True)
class PartCollector:
    """Incrementally guard a named role while one wide source stream is consumed."""

    schema: pa.Schema
    keys: tuple[str, ...]
    budget: LocalBudget
    retained: list[pa.RecordBatch] = dataclass_field(default_factory=list)
    count: int = 0
    decoded: int = 0
    seen: set[tuple[object, ...]] = dataclass_field(default_factory=set)

    def accept(self, incoming: pa.RecordBatch) -> None:
        budget = self.budget
        budget.check()
        batch = _normalize_batch(incoming, budget.policy.max_batch_bytes)
        if not self.schema.equals(batch.schema, check_metadata=False):
            fail("exact required part schema", "part schema mismatch", "transfer_guard")
        self.count += batch.num_rows
        if self.count > budget.policy.max_input_rows:
            fail("complete required part within row budget", "part row overflow")
        budget.input(batch.nbytes)
        self.decoded += batch.nbytes
        budget.allocation(self.decoded)
        if self.keys:
            budget.allocation(budget.input_bytes + self.count * (160 + 64 * len(self.keys)))
        for column in self.schema:
            if not column.nullable and batch.column(column.name).null_count:
                fail("non-null required part fields", "null required part field", "transfer_guard")
        for index in range(batch.num_rows):
            key = tuple(_value(batch.column(name)[index]) for name in self.keys)
            if self.keys and key in self.seen:
                fail("unique required part keys", "duplicate part key", "transfer_guard")
            self.seen.add(key)
        self.retained.append(batch)

    def finish(self) -> pa.Table:
        return pa.Table.from_batches(self.retained, schema=self.schema)


def to_part_frame(table: pa.Table, row: DatasetRowContract, budget: LocalBudget) -> pd.DataFrame:
    """Convert exact named state with the same identity representation as primary rows."""
    budget.allocation(table.nbytes * 4 + table.num_rows * (256 + 64 * table.num_columns))
    frame: pd.DataFrame = table.to_pandas(types_mapper=pd.ArrowDtype)
    for column in row.schema.columns:
        if isinstance(column.identity, _EntityFieldIdentity) and column.name in frame:
            frame[column.name] = pd.Series(
                [_value(value) for value in table[column.name]], dtype=object
            )
    size = frame_bytes(frame)
    budget.allocation(table.nbytes + size)
    if budget.live_bytes + size > budget.policy.max_input_bytes:
        fail("complete converted inputs and parts within decoded budget", "conversion overflow")
    budget.live_bytes += size
    return frame


def frame_bytes(frame: pd.DataFrame) -> int:
    return int(frame.memory_usage(index=True, deep=True).sum())


def validate_frame(
    frame: pd.DataFrame, row: DatasetRowContract, rows: DatasetRowSetContract
) -> None:
    names = {field.field_id: field.name for field in row.schema.columns}
    if frame.columns.tolist() != [field.name for field in row.schema.columns]:
        fail("exact ordered local result fields", "local fields differ", "output_validation")
    for field in row.schema.columns:
        dtype = frame[field.name].dtype
        width = _bool_tuple_arity(field.logical_type_id)
        if width is not None:
            if any(_bool_tuple_value(value, arity=width) is None for value in frame[field.name]):
                fail("exact non-null boolean mask arity", "invalid local mask", "output_validation")
        elif not isinstance(field.identity, _EntityFieldIdentity) and (
            not isinstance(dtype, pd.ArrowDtype)
            or not _matches_type(field.logical_type_id, dtype.pyarrow_dtype)
        ):
            fail("exact registered local column types", "local type mismatch", "output_validation")
        if not field.nullable and frame[field.name].isna().any():
            fail("non-null required fields", "null local field", "output_validation")
    keys = tuple(names[key] for key in row.key_field_ids)
    normalized_keys = frame_keys(frame, keys)
    if keys and len(set(normalized_keys)) != len(normalized_keys):
        fail("unique local result keys", "duplicate local key", "output_validation")
    if rows.cardinality.kind == "singleton" and len(frame) != 1:
        fail("exact singleton result", "invalid singleton", "output_validation")
    bound = getattr(rows.cardinality, "row_bound", None)
    maximum = getattr(bound, "max_rows", None)
    if isinstance(maximum, int) and len(frame) > maximum:
        fail("registered output row bound", "local row bound exceeded", "output_validation")
    compare = frame_comparator(frame, row, rows)
    for index in range(1, len(frame)):
        if compare(index - 1, index) >= 0:
            fail(
                "strict registered total result ordering",
                "invalid local order",
                "output_validation",
            )


def execute_suffix(
    frame: pd.DataFrame,
    calls: tuple[RowCall, ...],
    budget: LocalBudget,
) -> tuple[pd.DataFrame, tuple[tuple[int, int], ...]]:
    frame, _, handoffs = execute_retained_suffix(frame, (), calls, budget)
    return frame, handoffs


def execute_retained_suffix(
    frame: pd.DataFrame,
    parts: tuple[PartFrame, ...],
    calls: tuple[RowCall, ...],
    budget: LocalBudget,
) -> tuple[pd.DataFrame, tuple[PartFrame, ...], tuple[tuple[int, int], ...]]:
    from marivo.analysis.materialization.retained import (
        reject_source_private_transfer,
        source_private_role,
    )

    if any(source_private_role(part.role) for part in parts):
        reject_source_private_transfer()
    handoffs: list[tuple[int, int]] = []
    for call in calls:
        budget.check()
        validate_frame(frame, call.input_row, call.input_rows)
        if len(frame) > budget.policy.max_method_rows:
            fail("registered bounded row-method problem size", "method size overflow")
        size = frame_bytes(frame) + sum(frame_bytes(part.frame) for part in parts)
        # Covers row copies, comparison columns, masks, index arrays and sorting workspace.
        budget.allocation(size * 4 + len(frame) * (512 + 128 * len(frame.columns)))
        if call.input_row.shape_id.family_id == "delta":
            from marivo.analysis.operators.delta_state import validate_delta_parts

            validate_delta_parts(frame, parts, call.input_row)
        elif parts:
            from marivo.analysis.operators.rollup import validate_parts

            validate_parts(frame, parts, call.input_row)
        incoming = id(frame)
        if call.fold is not None:
            from marivo.analysis.operators.rollup import execute_fold

            result, output_parts = execute_fold(frame, parts, call)
        else:
            result = execute_row(frame, call)
            output_parts = select_parts(frame, result, call, parts) if parts else ()
        budget.check()
        output_bytes = frame_bytes(result) + sum(frame_bytes(part.frame) for part in output_parts)
        if (
            len(result) > budget.policy.max_output_rows
            or any(len(part.frame) > budget.policy.max_output_rows for part in output_parts)
            or output_bytes > budget.policy.max_output_bytes
        ):
            fail("complete local output within row and byte budgets", "local output overflow")
        budget.allocation(output_bytes)
        validate_frame(result, call.output_row, call.output_rows)
        handoffs.append((incoming, id(result)))
        budget.live_bytes += output_bytes - size
        frame = result
        parts = output_parts
    return frame, parts, tuple(handoffs)


def frame_to_arrow(
    frame: pd.DataFrame, row: DatasetRowContract, source_schema: pa.Schema
) -> pa.Table:
    arrays: list[pa.Array | pa.ChunkedArray] = []
    for field in row.schema.columns:
        if isinstance(field.identity, _EntityFieldIdentity):
            values = frame[field.name].tolist()
            names = [name for name, _ in field.identity.identity_signature]
            arrays.append(
                pa.array(
                    [dict(zip(names, value, strict=True)) for value in values],
                    type=source_schema.field(field.name).type,
                )
            )
        else:
            target = pa.int64() if field.role_id == "rank" else source_schema.field(field.name).type
            arrays.append(pa.array(frame[field.name], type=target, from_pandas=True, safe=True))
    schema = pa.schema(
        [
            pa.field(field.name, array.type, field.nullable)
            for field, array in zip(row.schema.columns, arrays, strict=True)
        ]
    )
    return pa.Table.from_arrays(arrays, schema=schema)
