"""Validate and complete typed payloads returned by source reads."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Literal, NoReturn

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.query_stages import (
    AttributeFrequencyRead,
    ReductionRead,
    read_attribute,
    read_components,
    read_finish,
    read_members,
    read_reduction,
    require_components,
    require_finish,
    require_reduction,
)

if TYPE_CHECKING:
    from marivo.analysis.model.attribute_failures import AttributeFailureContext

from marivo._definition.expressions import (
    SourceAggregate,
    SourceWeightedMean,
)
from marivo.analysis.compiler.attribute_frequencies import (
    AttributeFrequencyCapture,
    AttributeStateCapture,
)
from marivo.analysis.compiler.observation import common_slot
from marivo.analysis.compiler.query_stages import (
    SourceReadStage,
)
from marivo.analysis.core.data_validation import normalize_stage_columns
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.materialization.native_query_checks import (
    finish_native_query,
)
from marivo.analysis.materialization.query_components import (
    attribute_failure_context,
    attribute_witness_order,
    complete_components,
)
from marivo.analysis.methods.attribute_quantiles import FrequencyRankIndex, frequency_state
from marivo.analysis.methods.carriers.stage_components import ComponentFrame
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.numeric_state import typed_null_mask
from marivo.analysis.model.attribute_failures import AttributeCheckWitness
from marivo.analysis.model.data_types import (
    DataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.kernels import FrequencyState, FrequencyValue
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.spi import (
    NumericCheck,
    numeric_failure,
)


def capture_attribute_state(
    stage: SourceReadStage, raw: pd.DataFrame, output: StageOutput, provider: str
) -> StageOutput:
    if not stage.attribute_state and not stage.attribute_native:
        return output
    members = read_members(stage) or (stage,)
    slots = tuple(
        reduction.value_slot
        for member in members
        if (reduction := read_reduction(member)) is not None
    )
    frequencies = output.attribute_frequencies
    route: Literal["sufficient_statistics", "typed_frequencies", "typed_membership", "key_domain"]
    route = (
        ("typed_membership" if frequencies[0].kind == "distinct" else "typed_frequencies")
        if frequencies
        else "sufficient_statistics"
        if slots
        else "key_domain"
    )
    capture = AttributeStateCapture(
        stage.stage_id,
        provider,
        route,
        slots or stage.keys,
        frequencies[0].state_entries if frequencies else len(raw) * len(slots or stage.keys),
        pa.Table.from_pandas(
            raw.loc[
                :,
                list(
                    dict.fromkeys(
                        (
                            *stage.keys,
                            *(
                                ("__attribute_frequency_value", "__attribute_frequency")
                                if frequencies
                                else slots
                            ),
                        )
                    )
                ),
            ],
            preserve_index=False,
        ).nbytes,
        len(raw),
        pa.Table.from_pandas(raw, preserve_index=False).nbytes,
        stage.attribute_input_types,
        tuple(
            (column.slot_id, column.dtype) for column in output.columns if column.slot_id in slots
        ),
    )
    return replace(output, attribute_state_captures=(capture,))


def _decode_frequency(rows: pd.DataFrame, dtype: DataType) -> FrequencyState:
    entries: list[tuple[FrequencyValue | None, int]] = []
    missing_values = typed_null_mask(rows["__attribute_frequency_value"])
    missing_counts = typed_null_mask(rows["__attribute_frequency"])
    for value, count, missing_value, missing_count in zip(
        rows["__attribute_frequency_value"],
        rows["__attribute_frequency"],
        missing_values,
        missing_counts,
        strict=True,
    ):
        if missing_count:
            continue
        numeric: FrequencyValue | None = None
        if not missing_value:
            scalar = value if type(value) in (int, float, Decimal) else pa.scalar(value).as_py()
            if not isinstance(scalar, (int, float, Decimal)) or isinstance(scalar, bool):
                raise MaterializationError(
                    expected="a native numeric frequency value",
                    received=repr(scalar),
                    repair="Retain the original numeric operand type during source grouping.",
                    stage="attribute_frequency_capture",
                    help_target="analysis_frame.attribute",
                )
            numeric = scalar
        entries.append((numeric, int(count)))
    try:
        return frequency_state(dtype, entries)
    except ValueError as error:
        raise MaterializationError(
            expected="native typed value-frequency states with exact positive counts",
            received=str(error),
            repair="Inspect the original numeric values and captured frequency counts before retrying attribution.",
            stage="attribute_frequency_capture",
            help_target="analysis_frame.attribute",
        ) from error


def complete_attribute_frequencies(
    stage: SourceReadStage,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
) -> StageOutput:
    """Retain typed tall frequencies and restore the ordinary scalar stage shape."""

    assert isinstance(stage.program, AttributeFrequencyRead)
    reduction = stage.program.reduction
    assert isinstance(reduction.node, SourceAggregate)
    assert stage.program.input_type is not None
    validate_source_checks(stage, frame, numeric_checks)
    members = stage.program.members or (stage,)
    reductions = tuple(require_reduction(member) for member in members)
    output_types = {c.slot_id: c.dtype for c in stage.columns}
    finishers = dict(stage.program.finishers)
    key_arrays = tuple(pa.array(frame[name], from_pandas=True) for name in stage.keys)
    positions: dict[tuple[pa.Scalar | None, ...], list[int]] = {}
    for position in range(len(frame)):
        token = tuple(
            cell if cell.is_valid else None for array in key_arrays for cell in (array[position],)
        )
        positions.setdefault(token, []).append(position)
    if not stage.keys and not positions:
        positions[()] = []
    records: list[dict[str, object]] = []
    groups: list[tuple[tuple[object, ...], FrequencyState]] = []
    for token, indexes in positions.items():
        rows = frame.iloc[indexes]
        state = _decode_frequency(rows, stage.program.input_type)
        index = FrequencyRankIndex((state,))
        index.add(state)
        key = tuple(cell.as_py() if cell is not None else None for cell in token)
        groups.append((key, state))
        record: dict[str, object] = {
            name: value
            for name, value in (rows.iloc[0].to_dict().items() if len(rows) else ())
            if isinstance(name, str)
            and name not in ("__attribute_frequency_value", "__attribute_frequency")
        }
        record.update(zip(stage.keys, key, strict=True))
        for requested in reductions:
            assert isinstance(requested.node, SourceAggregate)
            if requested.node.reducer == "count_distinct":
                record[requested.value_slot] = index.distinct_count
            else:
                q = 0.5 if requested.node.reducer == "median" else requested.node.q
                assert q is not None
                record[requested.value_slot] = index.quantile(
                    q, output_types[requested.value_slot], finisher=finishers[requested.value_slot]
                )
        for name in (
            *(name for name, _ in numeric_checks),
            "__numeric_present",
            "__sample_present",
        ):
            if name in rows:
                record[name] = bool(rows[name].astype("boolean").fillna(False).any())
        records.append(record)
    scalar_frame = pd.DataFrame(
        records,
        columns=list(
            dict.fromkeys(
                (
                    *(
                        name
                        for name in frame.columns
                        if name not in ("__attribute_frequency_value", "__attribute_frequency")
                    ),
                    *(requested.value_slot for requested in reductions),
                )
            )
        ),
        dtype=object,
    )
    for requested in reductions:
        if output_types[requested.value_slot] == ScalarDataType("float64"):
            scalar_frame[requested.value_slot] = pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        scalar_frame[requested.value_slot].tolist(),
                        type=pa.float64(),
                        from_pandas=False,
                    )
                ),
                index=scalar_frame.index,
            )
    output = complete_source_frame(
        replace(stage, program=ReductionRead(reduction)), scalar_frame, numeric_checks
    )
    captured_groups = tuple(groups)
    captures = []
    for member, requested in zip(members, reductions, strict=True):
        assert isinstance(requested.node, SourceAggregate)
        distinct = requested.node.reducer == "count_distinct"
        q = None if distinct else 0.5 if requested.node.reducer == "median" else requested.node.q
        captures.append(
            AttributeFrequencyCapture(
                stage.stage_id,
                member.occurrence,
                stage.keys,
                tuple(c for c in output.columns if c.slot_id in stage.keys),
                captured_groups,
                next(c.dtype for c in output.columns if c.slot_id == requested.value_slot),
                q,
                requested.value_slot,
                "distinct" if distinct else "quantile",
                sum(len(state.values) + int(state.null_count > 0) for _, state in groups),
                len(frame),
                pa.Table.from_pandas(frame, preserve_index=False).nbytes,
                finishers[requested.value_slot],
            )
        )
    return replace(output, attribute_frequencies=tuple(captures), source_rows=len(frame))


def validate_source_checks(
    stage: SourceReadStage,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
) -> None:
    """Dispatch source checks with their original owner, coordinates and repair."""
    retained_checks = {column.slot_id for column in stage.columns}
    for name in (
        "__numeric_present",
        "__sample_present",
        *(name for name, _ in numeric_checks),
    ):
        if name in retained_checks and name in frame.columns:
            frame[name] = frame[name].astype("boolean").fillna(False)
    for name, check in numeric_checks:
        orphaned_predicate = (
            name in retained_checks
            and name.startswith("predicate_check.")
            and "__numeric_present" in frame.columns
        )
        if name in retained_checks and not orphaned_predicate:
            continue
        layout = read_attribute(stage)
        if layout is not None:
            for descriptor in (item for item in layout.checks if item.name == name):
                kind = (
                    4
                    if descriptor.scope == "preparation"
                    else 1
                    if descriptor.scope == "parent"
                    else 0
                )
                scoped = frame.loc[
                    (frame[layout.kind] == kind)
                    & (frame[layout.fiber] == descriptor.leaves[0].fiber)
                ]
                failing = scoped.loc[scoped[name].astype("boolean").fillna(False)]
                if len(failing):
                    columns = descriptor.columns
                    coordinates = normalize_stage_columns(
                        failing.loc[:, [column.slot_id for column in columns]], columns
                    )
                    coordinates = coordinates.iloc[
                        list(attribute_witness_order(coordinates, columns))[:6]
                    ].reset_index(drop=True)
                    witness = AttributeCheckWitness(
                        name, stage.source_id, tuple(frozen_key_rows(coordinates, columns)), columns
                    )
                    context = attribute_failure_context(
                        layout,
                        coordinates,
                        descriptor.leaves,
                        columns,
                        witness,
                        fine=descriptor.scope == "fine",
                    )
                    _attribute_source_check_failure(
                        stage, name, check.expected, check.received, coordinates, context
                    )
            continue
        violation = frame[name].astype("boolean").fillna(False)
        if orphaned_predicate:
            # Failed input predicates can eliminate every value at this key.
            # Such checks cannot travel through a sparse result's row domain;
            # checks on represented values remain demand-aware in the formula.
            violation &= ~frame["__numeric_present"].astype("boolean").fillna(False)
        if violation.any():
            finish = read_finish(stage)
            witness_slots = (
                dict(finish.frame_top.numeric_witnesses).get(name)
                if finish is not None and finish.frame_top is not None
                else None
            )
            witness_slots = witness_slots or tuple(
                sorted(
                    (column for column in frame.columns if column.startswith(name + ".witness.")),
                    key=lambda column: int(column.rsplit(".", 1)[1]),
                )
            )
            coordinates = (
                frame.loc[violation, list(witness_slots)]
                .head(1)
                .rename(columns=dict(zip(witness_slots, stage.keys, strict=False)))
                if witness_slots
                else frame.loc[violation, list(stage.keys)].head(6)
            )
            _source_check_failure(
                stage,
                name,
                check.expected,
                check.received,
                coordinates,
            )


def _source_check_failure(
    stage: SourceReadStage,
    name: str,
    expected: str,
    received: str,
    coordinates: pd.DataFrame,
) -> NoReturn:
    """Keep the owning preparation/numeric failure for actual or known-empty input."""
    received = (
        f"{received}; source={stage.source_id}; coordinates={coordinates.to_dict(orient='records')}"
    )
    if any(name == column for column, _, _ in stage.preparation_checks):
        if expected == "an output Population covering every required complete coordinate":
            raise MaterializationError(
                expected=expected,
                received=received,
                repair="Include every observed output coordinate in by=Population, or explicitly filter contributions before choosing that output domain.",
                stage="population_coverage",
                help_target="analysis_frame.compute",
            )
        if expected == "non-null complete identity keys":
            raise MaterializationError(
                expected=expected,
                received=received,
                repair="Supply every non-null primary-key component for each represented Entity identity.",
                stage="population_identity",
                help_target="analysis_frame.compute",
            )
        if expected == "a non-null complete identity for every input tuple":
            raise MaterializationError(
                expected=expected,
                received=received,
                repair="Supply a complete target key for each input identity, or use image/preimage to select existing mappings.",
                stage="population_identity",
                help_target="population.classify",
            )
    numeric_failure(expected, received)


def _attribute_source_check_failure(
    stage: SourceReadStage,
    name: str,
    expected: str,
    received: str,
    coordinates: pd.DataFrame,
    context: AttributeFailureContext,
) -> NoReturn:
    """Attach typed evidence without replacing the original source failure owner."""
    try:
        _source_check_failure(stage, name, expected, received, coordinates)
    except MaterializationError as error:
        error.attribute_context = context
        raise
    except SemanticExpressionError as error:
        error.details["attribute_context"] = context
        raise


def complete_source_frame(
    stage: SourceReadStage,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
    *,
    value_keys: tuple[str, ...] = (),
    ordered: bool = True,
) -> StageOutput:
    """Complete one logical source occurrence and its original fine checks."""
    source_rows = len(frame)
    components: tuple[ComponentFrame, ...] = ()
    if read_components(stage) is not None:
        frame, component = complete_components(require_components(stage), frame)
        components = (component,)
    validate_source_checks(stage, frame, numeric_checks)
    retained_checks = {column.slot_id for column in stage.columns}
    if "__numeric_present" in frame.columns and "__numeric_present" not in retained_checks:
        frame = frame.loc[frame["__numeric_present"].astype("boolean").fillna(False)]
    if read_finish(stage) is not None:
        frame = finish_native_query(require_finish(stage), frame)

    reduction = read_reduction(stage)
    if (
        reduction is not None
        and reduction.duration is not None
        and isinstance(reduction.node, SourceAggregate)
    ):
        node = reduction.node
        if node.reducer == "mean":
            finished: list[int | None] = []
            for row in frame.to_dict("records"):
                count = int(row["__duration_count"])
                finished.append(
                    None if count == 0 else round(Fraction(int(row[reduction.value_slot]), count))
                )
            frame[reduction.value_slot] = pd.Series(
                finished, dtype="int64[pyarrow]", index=frame.index
            )
    if (
        reduction is not None
        and reduction.duration is not None
        and isinstance(reduction.node, SourceWeightedMean)
    ):
        values: list[int | None] = []
        for numerator, denominator in zip(
            frame[reduction.value_slot], frame["__duration_weight_sum"], strict=True
        ):
            ticks = None if denominator == 0 else round(Fraction(numerator) / Fraction(denominator))
            if ticks is not None and not -(2**63) <= ticks < 2**63:
                numeric_failure("signed int64 Duration ticks", "weighted_mean output overflow")
            values.append(ticks)
        frame[reduction.value_slot] = pd.Series(values, dtype="int64[pyarrow]", index=frame.index)
    for column in stage.columns:
        if isinstance(column.dtype, DurationDataType):
            try:
                tick_array = pa.array(frame[column.slot_id], type=pa.int64(), from_pandas=True)
                frame[column.slot_id] = pd.Series(
                    pd.arrays.ArrowExtensionArray(tick_array.cast(pa.duration(column.dtype.unit))),
                    index=frame.index,
                )
            except (pa.ArrowException, OverflowError, ValueError, TypeError) as error:
                numeric_failure("lossless signed int64 Duration ticks", str(error))
    output = frame.loc[:, [c.slot_id for c in stage.columns]]
    if ordered and stage.keys and read_finish(stage) is None:
        output = output.sort_values(
            list(stage.keys), kind="stable", na_position="last"
        ).reset_index(drop=True)
    completed = StageOutput.from_source_frame(
        output,
        stage.columns,
        stage.keys,
        components=components,
        value_keys=value_keys,
        source_rows=source_rows,
        unique_keys=stage.unique_keys,
    )
    if read_finish(stage) is not None:
        completed = replace(completed, layout=require_finish(stage).layout)
    return completed


def _complete_common_member(
    member: SourceReadStage,
    index: int,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
    *,
    ordered: bool,
) -> StageOutput:
    assert read_reduction(member) is not None
    names = {
        column.slot_id: column.slot_id
        if column.slot_id in member.keys or column.slot_id == require_reduction(member).value_slot
        else common_slot(index, column.slot_id)
        for column in member.columns
    }
    presence = common_slot(index, "__numeric_present")
    if presence in frame.columns and "__numeric_present" not in names:
        names["__numeric_present"] = presence
    suffix = f".block{index}"
    names.update(
        {name.removesuffix(suffix): name for name, _ in numeric_checks if name.endswith(suffix)}
    )
    if read_components(member) is not None:
        names.update(
            {
                column.slot_id: common_slot(index, column.slot_id)
                for column in require_components(member).raw_columns
                if column.slot_id not in member.keys
            }
        )
        names[require_components(member).present] = common_slot(
            index, require_components(member).present
        )
    local = (
        frame.loc[:, list(names.values())]
        .rename(columns={value: key for key, value in names.items()})
        .copy()
    )
    checks = tuple(
        (name.removesuffix(suffix), check)
        for name, check in numeric_checks
        if name.endswith(suffix)
    )
    return complete_source_frame(member, local, checks, ordered=ordered)


def complete_common_read(
    stage: SourceReadStage,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
) -> StageOutput:
    """Complete logical occurrences while retaining an unchanged joint row layout."""
    members = read_members(stage)
    preparation_checks = {name for name, _, _ in stage.preparation_checks}
    if preparation_checks:
        validate_source_checks(
            stage, frame, tuple(item for item in numeric_checks if item[0] in preparation_checks)
        )
    same_rows = all(
        read_components(member) is None
        and read_finish(member) is None
        and member.keys == stage.keys
        and (
            common_slot(index, "__numeric_present") not in frame.columns
            or "__numeric_present" in {column.slot_id for column in member.columns}
        )
        for index, member in enumerate(members)
    )
    tables: list[pd.DataFrame] = []
    components: list[ComponentFrame] = []
    for index, member in enumerate(members):
        output = _complete_common_member(
            member, index, frame, numeric_checks, ordered=not same_rows
        )
        components.extend(output.components)
        tables.append(
            output.table.rename(
                columns={
                    column.slot_id: common_slot(index, column.slot_id)
                    for column in member.columns
                    if column.slot_id not in member.keys
                    and column.slot_id != require_reduction(member).value_slot
                }
            )
        )
    if same_rows:
        # Every member finished the same physical rows without reordering or
        # filtering. Preserve that joint layout instead of rebuilding it by key.
        result = frame.loc[:, [column.slot_id for column in stage.columns]].reset_index(drop=True)
        for table in tables:
            for name in table.columns:
                if name not in stage.keys:
                    result[name] = table[name]
    else:
        result = tables[0]
        for other in tables[1:]:
            result = (
                result.merge(
                    other,
                    on=list(stage.keys),
                    how="outer",
                    # Members were normalized on their own exact keys. A finer
                    # member key does not prove the common alignment key.
                    validate=None
                    if all(member.keys == stage.keys for member in members)
                    else "one_to_one",
                    sort=False,
                )
                if stage.keys
                else result.join(other)
            )
    result = result.loc[:, [column.slot_id for column in stage.columns]]
    retained_checks = {name for name, _ in numeric_checks}
    retained_checks.update(
        common_slot(index, name)
        for index in range(len(read_members(stage)))
        for name in ("__numeric_present", "__sample_present")
    )
    for column in stage.columns:
        if column.slot_id in retained_checks and column.dtype == ScalarDataType("boolean"):
            result[column.slot_id] = result[column.slot_id].astype("boolean").fillna(False)
    if stage.keys:
        result = result.sort_values(
            list(stage.keys), kind="stable", na_position="last"
        ).reset_index(drop=True)
    return StageOutput.from_source_frame(
        result,
        stage.columns,
        stage.keys,
        components=tuple(components),
        source_rows=len(frame),
        unique_keys=stage.unique_keys,
    )
