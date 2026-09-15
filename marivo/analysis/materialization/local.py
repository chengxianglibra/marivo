"""Complete input and semantic output validation for calling-process methods."""

from __future__ import annotations

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
from marivo.analysis.domains.lifecycle_reducers import is_fragment_duration
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import (
    _matches_type,
    _normalize_batch,
    _realized_schema,
    _reason_tuple,
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


def fail(expected: str, received: str, stage: str = "transfer_guard") -> Never:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair="Inspect the input schema, keys and the selected method contract.",
        stage=stage,
    )


def collect_primary(
    batches: Iterable[pa.RecordBatch],
    row: DatasetRowContract,
    rows: DatasetRowSetContract,
) -> pa.Table:
    validator = _RowValidator(row, rows, source_key_validation=True)
    retained: list[pa.RecordBatch] = []
    schema: pa.Schema | None = None
    for incoming in batches:
        batch = _normalize_batch(incoming)
        _realized_schema(row, batch.schema)
        if schema is not None and not schema.equals(batch.schema, check_metadata=False):
            fail("one exact Arrow schema across all input batches", "input schema changed")
        schema = batch.schema
        validator.accept(batch)
        retained.append(batch)
    validator.finish()
    if schema is None:
        fail("complete Arrow input including an empty schema batch", "missing input schema")
    table = pa.Table.from_batches(retained, schema=schema)
    # The stream order is not a proof of key uniqueness for ranked inputs.
    keys = [field.name for field in row.schema.columns if field.field_id in row.key_field_ids]
    if keys:
        seen: set[tuple[object, ...]] = set()
        for index in range(table.num_rows):
            key = tuple(_value(table[name][index]) for name in keys)
            if key in seen:
                fail("unique complete input row keys", "duplicate input key")
            seen.add(key)
    return table


def to_local_frame(table: pa.Table, row: DatasetRowContract) -> pd.DataFrame:
    # Conversion may materialize Python identities and index/column objects.
    frame = _to_dataframe(table, row)
    for field in row.schema.columns:
        if is_fragment_duration(row, field):
            frame[field.name] = pd.Series(table[field.name], dtype=pd.ArrowDtype(pa.float64()))
    return frame


def collect_part(
    batches: Iterable[pa.RecordBatch],
    schema: pa.Schema,
    keys: tuple[str, ...],
) -> pa.Table:
    """Validate a complete required role against its owner's exact physical contract."""
    collector = PartCollector(schema, keys)
    for incoming in batches:
        collector.accept(incoming)
    return collector.finish()


@dataclass(slots=True)
class PartCollector:
    """Incrementally guard a named role while one wide source stream is consumed."""

    schema: pa.Schema
    keys: tuple[str, ...]
    retained: list[pa.RecordBatch] = dataclass_field(default_factory=list)
    count: int = 0
    seen: set[tuple[object, ...]] = dataclass_field(default_factory=set)

    def accept(self, incoming: pa.RecordBatch) -> None:
        batch = _normalize_batch(incoming)
        if not self.schema.equals(batch.schema, check_metadata=False):
            fail("exact required part schema", "part schema mismatch", "transfer_guard")
        self.count += batch.num_rows
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


def to_part_frame(table: pa.Table, row: DatasetRowContract) -> pd.DataFrame:
    """Convert exact named state with the same identity representation as primary rows."""
    frame: pd.DataFrame = table.to_pandas(types_mapper=pd.ArrowDtype)
    for column in row.schema.columns:
        if isinstance(column.identity, _EntityFieldIdentity) and column.name in frame:
            frame[column.name] = pd.Series(
                [_value(value) for value in table[column.name]], dtype=object
            )
    return frame


def validate_frame(
    frame: pd.DataFrame, row: DatasetRowContract, rows: DatasetRowSetContract
) -> None:
    names = {field.field_id: field.name for field in row.schema.columns}
    if frame.columns.tolist() != [field.name for field in row.schema.columns]:
        fail("exact ordered local result fields", "local fields differ", "output_validation")
    for field in row.schema.columns:
        dtype = frame[field.name].dtype
        width = _bool_tuple_arity(field.logical_type_id)
        if field.logical_type_id == "candidate_reasons":
            if any(_reason_tuple(value) is None for value in frame[field.name]):
                fail(
                    "one bounded non-null reason code", "invalid local reasons", "output_validation"
                )
        elif width is not None:
            if any(_bool_tuple_value(value, arity=width) is None for value in frame[field.name]):
                fail("exact non-null boolean mask arity", "invalid local mask", "output_validation")
        elif field.logical_type_id == "identity_tuple":
            if any(
                value is not None and value is not pd.NA and not isinstance(value, tuple)
                for value in frame[field.name]
            ):
                fail("retained identity tuples", "invalid local identity", "output_validation")
        elif field.logical_type_id == "duration":
            if not isinstance(dtype, pd.ArrowDtype) or dtype.pyarrow_dtype != (
                pa.float64() if is_fragment_duration(row, field) else pa.duration("us")
            ):
                fail("microsecond duration values", "invalid local duration", "output_validation")
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
) -> tuple[pd.DataFrame, tuple[tuple[int, int], ...]]:
    frame, _, handoffs = execute_retained_suffix(frame, (), calls)
    return frame, handoffs


def execute_retained_suffix(
    frame: pd.DataFrame,
    parts: tuple[PartFrame, ...],
    calls: tuple[RowCall, ...],
) -> tuple[pd.DataFrame, tuple[PartFrame, ...], tuple[tuple[int, int], ...]]:
    from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics
    from marivo.analysis.materialization.retained import (
        reject_source_private_transfer,
        source_private_role,
    )

    if any(source_private_role(part.role) for part in parts):
        reject_source_private_transfer()
    handoffs: list[tuple[int, int]] = []
    for call in calls:
        validate_frame(frame, call.input_row, call.input_rows)
        if call.input_row.shape_id.family_id == "delta" and not isinstance(
            call.input_row.family_semantics, FunnelDeltaSemantics
        ):
            from marivo.analysis.operators.delta_state import validate_delta_parts

            validate_delta_parts(frame, parts, call.input_row)
        elif parts and (
            call.input_row.shape_id.family_id == "metric"
            or any(part.role != "population_sampling_state" for part in parts)
        ):
            from marivo.analysis.operators.rollup import validate_parts

            validate_parts(frame, parts, call.input_row)
        incoming = id(frame)
        if call.fold is not None:
            from marivo.analysis.operators.rollup import execute_fold

            result, output_parts = execute_fold(frame, parts, call)
        else:
            result = execute_row(frame, call)
            output_parts = select_parts(frame, result, call, parts) if parts else ()
        validate_frame(result, call.output_row, call.output_rows)
        handoffs.append((incoming, id(result)))
        frame = result
        parts = output_parts
    return frame, parts, tuple(handoffs)


def frame_to_arrow(
    frame: pd.DataFrame, row: DatasetRowContract, source_schema: pa.Schema
) -> pa.Table:
    arrays: list[pa.Array | pa.ChunkedArray] = []
    for field in row.schema.columns:
        if field.logical_type_id == "identity_tuple":
            values = frame[field.name].tolist()
            identity_type = source_schema.field(field.name).type
            if not pa.types.is_struct(identity_type):
                fail("the retained identity struct schema", "invalid identity schema")
            names = [component.name for component in identity_type]
            arrays.append(
                pa.array(
                    [
                        None
                        if value is None or value is pd.NA
                        else dict(zip(names, value, strict=True))
                        for value in values
                    ],
                    type=identity_type,
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
