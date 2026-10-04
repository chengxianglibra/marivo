"""Complete typed local method graphs executed in the calling process."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization.contracts import LocalReceipt, RetainedPart
from marivo.analysis.materialization.local import (
    PartCollector,
    collect_part,
    collect_primary,
    execute_retained_suffix,
    fail,
    frame_to_arrow,
    to_local_frame,
    to_part_frame,
)
from marivo.analysis.materialization.retained import checked_component_batches
from marivo.analysis.materialization.storage import (
    ReadPolicy,
    _normalize_batch,
    _read,
    read_part_batches,
)
from marivo.analysis.operators.row import PartFrame, RowCall


@dataclass(frozen=True, slots=True, repr=False)
class StreamInput:
    row: DatasetRowContract
    rows: DatasetRowSetContract
    wide_parts: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class LocalPartInput:
    role: str
    contract_id: str
    contract_version: int
    schema: pa.Schema
    keys: tuple[str, ...]
    receipt: LocalReceipt | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ArtifactInput:
    project_root: Path
    receipt: LocalReceipt
    row: DatasetRowContract
    rows: DatasetRowSetContract


@dataclass(frozen=True, slots=True, repr=False)
class LocalRequest:
    input: StreamInput | ArtifactInput
    calls: tuple[RowCall, ...]
    parts: tuple[LocalPartInput, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class LocalPartResult:
    role: str
    contract_id: str
    contract_version: int
    table: pa.Table


@dataclass(frozen=True, slots=True, repr=False)
class LocalResult:
    table: pa.Table
    handoffs: tuple[tuple[int, int], ...]
    input_rows: int
    parts: tuple[LocalPartResult, ...] = ()


def _part_to_arrow(part: PartFrame) -> pa.Table:
    arrays: list[pa.Array | pa.ChunkedArray] = []
    for column in part.schema:
        values = part.frame[column.name]
        if pa.types.is_struct(column.type):
            names = [field.name for field in column.type]
            arrays.append(
                pa.array(
                    [dict(zip(names, value, strict=True)) for value in values], type=column.type
                )
            )
        else:
            arrays.append(pa.array(values, type=column.type, from_pandas=True, safe=True))
    return pa.Table.from_arrays(arrays, schema=part.schema)


@dataclass(frozen=True, slots=True, repr=False)
class LocalBoundary:
    output: int
    input: StreamInput | ArtifactInput
    parts: tuple[LocalPartInput, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class LocalStage:
    output: int
    inputs: tuple[int, ...]
    call: RowCall


@dataclass(frozen=True, slots=True, repr=False)
class LocalGraphRequest:
    boundaries: tuple[LocalBoundary, ...]
    stages: tuple[LocalStage, ...]
    primary_output: int


@dataclass(frozen=True, slots=True, repr=False)
class LocalInputStreams:
    batches: Iterable[pa.RecordBatch]
    parts: tuple[Iterable[pa.RecordBatch], ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class _Frames:
    frame: pd.DataFrame
    parts: tuple[PartFrame, ...]
    schema: pa.Schema


def _collect_input(
    streams: LocalInputStreams,
    selected: StreamInput | ArtifactInput,
    parts: tuple[LocalPartInput, ...],
) -> tuple[_Frames, int]:
    if len({part.role for part in parts}) != len(parts):
        fail("one input per selected retained role", "duplicate part input")
    wide = isinstance(selected, StreamInput) and selected.wide_parts
    expected_streams = 0 if wide else sum(part.receipt is None for part in parts)
    if len(streams.parts) != expected_streams:
        fail("one complete stream per required part", "missing or extra part streams")
    collectors = tuple(PartCollector(part.schema, part.keys) for part in parts)
    if isinstance(selected, ArtifactInput):
        table = _read(
            project_root=selected.project_root,
            receipt=selected.receipt,
            row_contract=selected.row,
            row_set_contract=selected.rows,
            preview=False,
            policy=ReadPolicy(),
        )
        batches = table.to_batches() or [
            pa.RecordBatch.from_arrays(
                [pa.array([], type=field.type) for field in table.schema], schema=table.schema
            )
        ]
        table = collect_primary(batches, selected.row, selected.rows)
    else:

        def primary_batches() -> Iterable[pa.RecordBatch]:
            for incoming in streams.batches:
                if wide:
                    incoming = _normalize_batch(incoming)
                    for part, collector in zip(parts, collectors, strict=True):
                        for part_batch in checked_component_batches(
                            (incoming.select(part.schema.names),), selected.row, part.role
                        ):
                            collector.accept(part_batch)
                    yield incoming.select([field.name for field in selected.row.schema.columns])
                else:
                    yield incoming

        table = collect_primary(primary_batches(), selected.row, selected.rows)
    frame = to_local_frame(table, selected.row)
    schema = table.schema
    count = table.num_rows
    del table
    part_frames: list[PartFrame] = []
    incoming_parts = iter(streams.parts)
    for part, collector in zip(parts, collectors, strict=True):
        if wide:
            part_table = collector.finish()
        elif part.receipt is not None:
            if not isinstance(selected, ArtifactInput):
                fail("a local project for retained part receipts", "invalid part input")
            incoming_part: Iterable[pa.RecordBatch] = read_part_batches(
                selected.project_root,
                RetainedPart(part.role, part.contract_id, part.contract_version, part.receipt),
                expected_schema=part.schema,
                policy=ReadPolicy(),
            )
            part_table = collect_part(
                incoming_part
                if part.role == "population_sampling_state"
                else checked_component_batches(incoming_part, selected.row, part.role),
                part.schema,
                part.keys,
            )
        else:
            incoming_part = next(incoming_parts)
            part_table = collect_part(
                incoming_part
                if part.role == "population_sampling_state"
                else checked_component_batches(incoming_part, selected.row, part.role),
                part.schema,
                part.keys,
            )
        part_frames.append(
            PartFrame(
                part.role,
                part.contract_id,
                part.contract_version,
                part.schema,
                part.keys,
                to_part_frame(part_table, selected.row),
            )
        )
        del part_table
    for collector in collectors:
        collector.retained.clear()
        collector.seen.clear()
    collectors = ()
    return (_Frames(frame, tuple(part_frames), schema), count)


@dataclass(frozen=True, slots=True, repr=False)
class _GraphResult:
    frames: _Frames
    handoffs: tuple[tuple[int, int], ...]
    input_rows: int
    output_row: DatasetRowContract


def _execute_graph(
    streams: tuple[LocalInputStreams, ...], request: LocalGraphRequest
) -> _GraphResult:

    if len(streams) != len(request.boundaries):
        fail("one complete stream per graph boundary", "missing or extra graph streams")
    values: dict[int, _Frames] = {}
    total_rows = 0
    for boundary, stream in zip(request.boundaries, streams, strict=True):
        if boundary.output in values:
            fail("unique physical boundary identities", "duplicate graph input")
        value, count = _collect_input(stream, boundary.input, boundary.parts)
        total_rows += count
        values[boundary.output] = value
    # Every boundary and required role is complete before any method is invoked.
    users: dict[int, int] = {}
    for stage in request.stages:
        for key in stage.inputs:
            users[key] = users.get(key, 0) + 1
    users[request.primary_output] = users.get(request.primary_output, 0) + 1
    handoffs: list[tuple[int, int]] = []
    output_row: DatasetRowContract | None = None
    for stage in request.stages:
        if stage.output in values or any(key not in values for key in stage.inputs):
            fail("a complete ordered local dependency graph", "invalid stage dependencies")
        incoming = tuple(values[key] for key in stage.inputs)
        call = stage.call
        if isinstance(call, RowCall):
            if len(incoming) != 1:
                fail("one row method operand", "invalid row method arity")
            source = incoming[0]
            result, parts, transfers = execute_retained_suffix(source.frame, source.parts, (call,))
            value = _Frames(result, parts, source.schema)
            del parts
            handoffs.extend(transfers)
            output_row = call.output_row
            del source
        else:
            fail("a registered Dataset row method", "unsupported local method")
        values[stage.output] = value
        for key in stage.inputs:
            users[key] -= 1
            if users[key] == 0:
                del values[key]
        del incoming
    if output_row is None or request.primary_output not in values:
        fail("one complete local graph result", "missing graph output")
    return _GraphResult(
        values[request.primary_output],
        tuple(handoffs),
        total_rows,
        output_row,
    )


def execute_local(
    request: LocalRequest | LocalGraphRequest,
    streams: tuple[LocalInputStreams, ...],
) -> LocalResult:
    """Compute complete local inputs synchronously, preserving original exceptions."""
    if isinstance(request, LocalGraphRequest):
        completed = _execute_graph(streams, request)
        frame = completed.frames.frame
        output_parts, schema = completed.frames.parts, completed.frames.schema
        handoffs, count = completed.handoffs, completed.input_rows
        output_row = completed.output_row
    else:
        if not request.calls or len(streams) != 1:
            fail("one complete suffix input and its methods", "invalid suffix request")
        incoming, count = _collect_input(streams[0], request.input, request.parts)
        schema = incoming.schema
        frame, output_parts, handoffs = execute_retained_suffix(
            incoming.frame, incoming.parts, request.calls
        )
        output_row = request.calls[-1].output_row
    result = frame_to_arrow(frame, output_row, schema)
    completed_parts: list[LocalPartResult] = []
    for output_part in output_parts:
        part_table = _part_to_arrow(output_part)
        completed_parts.append(
            LocalPartResult(
                output_part.role, output_part.contract_id, output_part.contract_version, part_table
            )
        )
        if output_part.role != "population_sampling_state":
            tuple(checked_component_batches(part_table.to_batches(), output_row, output_part.role))
            if len(output_part.frame) != len(frame):
                fail(
                    "one retained state row per output row",
                    "part row count differs",
                    "output_validation",
                )
            for name in part_table.column_names:
                if name not in output_part.keys:
                    if name in result.column_names:
                        fail(
                            "independent retained state field names",
                            "duplicate retained output field",
                            "output_validation",
                        )
                    result = result.append_column(part_table.schema.field(name), part_table[name])
    return LocalResult(result, handoffs, count, tuple(completed_parts))
