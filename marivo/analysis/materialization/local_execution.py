"""Complete typed local method graphs executed in the calling process."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.domains.event_attribution import FunnelAttributeSpec
from marivo.analysis.domains.event_comparison import FunnelCompareSpec
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
    validate_frame,
)
from marivo.analysis.materialization.retained import checked_component_batches
from marivo.analysis.materialization.storage import (
    ReadPolicy,
    _normalize_batch,
    _read,
    read_part_batches,
)
from marivo.analysis.operators.association_contracts import (
    AssociationSearchSummary,
    CorrelateSpecV1,
    candidate_count,
)
from marivo.analysis.operators.attribution_contracts import AttributeSpecV1
from marivo.analysis.operators.candidate_contracts import (
    CandidateSearchSummary,
    CandidateSpecV1,
)
from marivo.analysis.operators.contracts import CompareSpecV1
from marivo.analysis.operators.driver_contracts import DriverCandidateSpecV1
from marivo.analysis.operators.forecast_contracts import ForecastSpecV1, ForecastTrainingSummary
from marivo.analysis.operators.row import PartFrame, RowCall

if TYPE_CHECKING:
    from marivo.analysis.materialization.attribution_publication import AttributionSourceSummary


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
    input: StreamInput | ArtifactInput | CoalitionInput | PairInput
    calls: tuple[RowCall, ...]
    parts: tuple[LocalPartInput, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class LocalPartResult:
    role: str
    contract_id: str
    contract_version: int
    table: pa.Table


@dataclass(frozen=True, slots=True, repr=False)
class FamilySummaries:
    """Original producer summaries carried unchanged through local row successors."""

    attribution: AttributionSourceSummary | None = None
    association: AssociationSearchSummary | None = None
    forecast: ForecastTrainingSummary | None = None
    candidate: CandidateSearchSummary | None = None


@dataclass(frozen=True, slots=True, repr=False)
class LocalResult:
    table: pa.Table
    handoffs: tuple[tuple[int, int], ...]
    input_rows: int
    parts: tuple[LocalPartResult, ...] = ()
    summaries: FamilySummaries = FamilySummaries()


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
class PairInput:
    """Complete source-certified numeric pairs without Entity identity."""

    spec: CorrelateSpecV1
    expected_rows: int


@dataclass(frozen=True, slots=True, repr=False)
class CoalitionInput:
    """Closed numerical preparation input; never an Artifact or Dataset row contract."""

    spec: AttributeSpecV1
    expected_rows: int


@dataclass(frozen=True, slots=True, repr=False)
class LocalBoundary:
    output: int
    input: StreamInput | ArtifactInput | CoalitionInput | PairInput
    parts: tuple[LocalPartInput, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class LocalStage:
    output: int
    inputs: tuple[int, ...]
    call: (
        RowCall
        | FunnelCompareSpec
        | FunnelAttributeSpec
        | CompareSpecV1
        | AttributeSpecV1
        | CorrelateSpecV1
        | ForecastSpecV1
        | CandidateSpecV1
        | DriverCandidateSpecV1
    )


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
    pair_preparation: bool = False


def _collect_input(
    streams: LocalInputStreams,
    selected: StreamInput | ArtifactInput | CoalitionInput | PairInput,
    parts: tuple[LocalPartInput, ...],
) -> tuple[_Frames, int]:
    if isinstance(selected, (CoalitionInput, PairInput)):
        from marivo.analysis.operators.distribution_values import validate_coalition_schema

        input_kind = "pair" if isinstance(selected, PairInput) else "coalition"
        if parts:
            fail(f"{input_kind}-only numerical input", "unexpected retained parts")
        batches = []
        schema = None
        count = 0
        for batch in streams.batches:
            batch = _normalize_batch(batch)
            if isinstance(selected, PairInput):
                from marivo.analysis.operators.association_values import validate_pair_schema

                validate_pair_schema(batch.schema, selected.spec)
            else:
                validate_coalition_schema(batch.schema, selected.spec)
            if schema is not None and not schema.equals(batch.schema):
                fail(f"one stable {input_kind} schema", f"changed {input_kind} schema")
            schema = batch.schema
            count += batch.num_rows
            batches.append(batch)
        if schema is None or count != selected.expected_rows:
            fail(
                f"complete {input_kind} stream with source-certified row count",
                f"missing {input_kind} rows or schema",
            )
        table = pa.Table.from_batches(batches, schema=schema)
        frame = table.to_pandas(types_mapper=pd.ArrowDtype)
        return _Frames(frame, (), schema, isinstance(selected, PairInput)), count
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
    return _Frames(frame, tuple(part_frames), schema), count


@dataclass(frozen=True, slots=True, repr=False)
class _GraphResult:
    frames: _Frames
    handoffs: tuple[tuple[int, int], ...]
    input_rows: int
    output_row: DatasetRowContract
    summaries: FamilySummaries


def _execute_graph(
    streams: tuple[LocalInputStreams, ...], request: LocalGraphRequest
) -> _GraphResult:
    from marivo.analysis.operators.attribute_values import execute_attribute
    from marivo.analysis.operators.compare import execute_compare
    from marivo.analysis.operators.delta_state import execute_compare_parts, validate_delta_parts
    from marivo.analysis.operators.rollup import validate_parts

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
    summaries = FamilySummaries()
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
            # The suffix accounts for replacement; shared graph inputs remain alive.
            value = _Frames(result, parts, source.schema)
            del parts
            handoffs.extend(transfers)
            output_row = call.output_row
            del source
        elif isinstance(call, DriverCandidateSpecV1):
            from marivo.analysis.operators.driver_expansion import prepare_local_driver_expansion
            from marivo.analysis.operators.driver_values import execute_driver

            expanded = call.expanded_compare
            if len(incoming) != (3 if expanded is not None else 1):
                fail("complete driver screening operands", "invalid driver arity")
            if any(f.role_id == "entity_identity" for f in call.output_row.schema.columns):
                fail("source-native Entity driver scope", "source-required identity")
            original = incoming[0]
            if expanded is not None:
                original_row, original_rows = call.original_input_row, call.original_input_rows
                if original_row is None or original_rows is None:
                    fail("original selected Delta contract", "missing expansion authority")
                validate_frame(original.frame, original_row, original_rows)
                validate_delta_parts(original.frame, original.parts, original_row)
                for value, row, rows in (
                    (incoming[1], expanded.current_row, expanded.current_rows),
                    (incoming[2], expanded.baseline_row, expanded.baseline_rows),
                ):
                    validate_frame(value.frame, row, rows)
                    validate_parts(value.frame, value.parts, row)
                frame, parts = prepare_local_driver_expansion(
                    original.frame,
                    incoming[1].frame,
                    incoming[2].frame,
                    call,
                    incoming[1].parts,
                    incoming[2].parts,
                )
            else:
                frame, parts = original.frame, original.parts
            validate_frame(frame, call.input_row, call.input_rows)
            validate_delta_parts(frame, parts, call.input_row)
            result, driver_evaluation = execute_driver(
                frame,
                call,
                parts=parts,
                original=original.frame if expanded is not None else None,
                original_parts=original.parts if expanded is not None else (),
            )
            del frame, parts
            summaries = replace(
                summaries, candidate=CandidateSearchSummary(call.definition, driver_evaluation)
            )
            validate_frame(result, call.output_row, call.output_rows)
            value = _Frames(result, (), pa.Schema.from_pandas(result, preserve_index=False))
            handoffs.extend((id(item.frame), id(result)) for item in incoming)
            output_row = call.output_row
            del original
        elif isinstance(call, CandidateSpecV1):
            from marivo.analysis.operators.candidate_values import execute_candidate

            if len(incoming) != 1:
                fail("one complete discovery input", "invalid Candidate arity")
            source = incoming[0]
            validate_frame(source.frame, call.input_row, call.input_rows)
            # At most one provisional candidate per input point/window is retained.
            result, evaluation = execute_candidate(
                source.frame,
                call,
            )
            summaries = replace(
                summaries, candidate=CandidateSearchSummary(call.definition, evaluation)
            )
            validate_frame(result, call.output_row, call.output_rows)
            value = _Frames(result, (), pa.Schema.from_pandas(result, preserve_index=False))
            handoffs.append((id(source.frame), id(result)))
            output_row = call.output_row
            del source
        elif isinstance(call, ForecastSpecV1):
            from marivo.analysis.operators.forecast_values import execute_forecast, prepare_history

            if len(incoming) != 1:
                fail("one complete Forecast history", "invalid Forecast arity")
            source = incoming[0]
            validate_frame(source.frame, call.input_row, call.input_rows)
            prepared_history = prepare_history(source.frame, call)
            result, training = execute_forecast(prepared_history, call)
            summaries = replace(summaries, forecast=training)
            del prepared_history
            validate_frame(result, call.output_row, call.output_rows)
            value = _Frames(result, (), pa.Schema.from_pandas(result, preserve_index=False))
            handoffs.append((id(source.frame), id(result)))
            output_row = call.output_row
            del source
        elif isinstance(call, CorrelateSpecV1):
            from marivo.analysis.operators.association_values import execute_pairs, prepare_local

            if len(incoming) != 1:
                fail("one complete correlation input", "invalid correlation arity")
            source = incoming[0]
            candidate_count(len(call.metric_names), len(call.semantics.lag_offsets))
            prepared = source.pair_preparation
            if not prepared:
                validate_frame(source.frame, call.input_row, call.input_rows)
            pairs = source.frame if prepared else prepare_local(source.frame, call)
            result = execute_pairs(pairs, call)
            del pairs
            from marivo.analysis.operators.association_values import summarize_search

            summaries = replace(summaries, association=summarize_search(result, call.output_row))
            validate_frame(result, call.output_row, call.output_rows)
            schema = pa.Schema.from_pandas(result, preserve_index=False)
            value = _Frames(result, (), schema)
            handoffs.append((id(source.frame), id(result)))
            output_row = call.output_row
            del source
        elif isinstance(call, (FunnelCompareSpec, FunnelAttributeSpec)):
            from marivo.analysis.domains.event_attribution_values import (
                execute_attribute_with_parts as attribute_funnel,
            )
            from marivo.analysis.domains.event_comparison_values import (
                execute_compare as compare_funnel,
            )

            if isinstance(call, FunnelCompareSpec):
                result = compare_funnel(incoming[0].frame, incoming[1].frame, call)
                parts = ()
            else:
                result, parts = attribute_funnel(
                    incoming[0].frame, incoming[1].frame, incoming[2].frame, call
                )
            validate_frame(result, call.output_row, call.output_rows)
            schema = pa.Schema.from_pandas(result, preserve_index=False)
            value = _Frames(result, parts, schema)
            output_row = call.output_row
        elif isinstance(call, CompareSpecV1):
            if len(incoming) != 2:
                fail("ordered current and baseline operands", "invalid comparison arity")
            current, baseline = incoming
            validate_frame(current.frame, call.current_row, call.current_rows)
            validate_frame(baseline.frame, call.baseline_row, call.baseline_rows)
            from marivo.analysis.compiler.lowering import retained_part_specs

            for value, row in ((current, call.current_row), (baseline, call.baseline_row)):
                required = {part.role for part in retained_part_specs(row)}
                if not required.issubset(part.role for part in value.parts):
                    fail(
                        "complete comparison side component roles",
                        "missing side components",
                        "transfer_guard",
                    )
                validate_parts(value.frame, value.parts, row)
            result = execute_compare(current.frame, baseline.frame, call)
            parts = execute_compare_parts(
                current.frame, baseline.frame, call, current.parts, baseline.parts, result
            )
            validate_frame(result, call.output_row, call.output_rows)
            fields: list[pa.Field] = []
            for field in call.output_row.schema.columns:
                dtype = result[field.name].dtype
                if not isinstance(dtype, pd.ArrowDtype):
                    fail(
                        "exact Arrow comparison result types",
                        "untyped comparison output",
                        "output_validation",
                    )
                fields.append(pa.field(field.name, dtype.pyarrow_dtype, field.nullable))
            schema = pa.schema(fields)
            value = _Frames(result, parts, schema)
            handoffs.extend((id(item.frame), id(result)) for item in incoming)
            output_row = call.output_row
            del current, baseline
        else:
            if len(incoming) != 1 or (
                call.expanded_compare is not None and call.method != "distribution_shapley@v1"
            ):
                fail("one complete retained Attribution input", "source-required axis expansion")
            source = incoming[0]
            if call.method != "distribution_shapley@v1":
                validate_frame(source.frame, call.input_row, call.input_rows)
            if call.method == "distribution_shapley@v1":
                from marivo.analysis.operators.distribution_values import execute_distribution

                result = execute_distribution(source.frame, call)
            else:
                validate_delta_parts(source.frame, source.parts, call.input_row)
                result = execute_attribute(source.frame, call, parts=source.parts)
            validate_frame(result, call.output_row, call.output_rows)
            from marivo.analysis.materialization.attribution_publication import (
                summarize_attribution_frame,
            )

            summaries = replace(
                summaries, attribution=summarize_attribution_frame(result, call.output_row)
            )
            fields = []
            for field in call.output_row.schema.columns:
                dtype = result[field.name].dtype
                if not isinstance(dtype, pd.ArrowDtype):
                    fail(
                        "exact Arrow Attribution result types",
                        "untyped Attribution output",
                        "output_validation",
                    )
                fields.append(pa.field(field.name, dtype.pyarrow_dtype, field.nullable))
            value = _Frames(result, (), pa.schema(fields))
            handoffs.append((id(source.frame), id(result)))
            output_row = call.output_row
            del source
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
        summaries,
    )


def execute_local(
    request: LocalRequest | LocalGraphRequest,
    streams: tuple[LocalInputStreams, ...],
) -> LocalResult:
    """Compute complete local inputs synchronously, preserving original exceptions."""
    summaries = FamilySummaries()
    if isinstance(request, LocalGraphRequest):
        completed = _execute_graph(streams, request)
        frame = completed.frames.frame
        output_parts, schema = completed.frames.parts, completed.frames.schema
        handoffs, count = completed.handoffs, completed.input_rows
        output_row, summaries = completed.output_row, completed.summaries
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
    return LocalResult(result, handoffs, count, tuple(completed_parts), summaries)
