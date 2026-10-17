"""Registered local methods over verified current-row Cells."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_plan import LocalCompute, LocalMethodStage
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.core.model import Cell, Defined, Null, Undefined, Unknown, reject
from marivo.analysis.core.rules import AssociationScore, RowState
from marivo.analysis.methods.association_numeric import score
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType


@dataclass(frozen=True, slots=True)
class CountResult:
    cell: Defined
    count: int


@dataclass(frozen=True, slots=True)
class ArithmeticResult:
    cell: Defined | Undefined
    current_sum: int | float
    current_count: int


@dataclass(frozen=True, slots=True)
class SpearmanResult:
    status: str
    coefficient: float | None
    input_observation_count: int
    matched_observation_count: int
    null_pair_count: int
    complete_pair_count: int


def score_spearman(
    stage: LocalMethodStage,
    left: pd.DataFrame,
    right: pd.DataFrame,
    keys: tuple[str, ...],
    schemas: tuple[pa.Schema, pa.Schema],
) -> SpearmanResult:
    """Pair exact retained keys and score complete finite Cells with average ranks."""
    if (
        not isinstance(stage.node.parameters, AssociationScore)
        or stage.implementation.key.route not in ("ibis_python",)
        or not keys
    ):
        reject(
            "a qualified local Spearman stage",
            str(stage.node.method),
            "Use registered pair inputs.",
            "analysis.local",
        )
    from marivo.analysis.materialization.cell_arrow import logical_schema
    from marivo.analysis.materialization.graph_exchange import _pandas_records

    expected = {*keys, "value", "cell_tag", "cell_reason"}
    if not expected <= set(logical_schema(schemas[0]).names) or not expected <= set(
        logical_schema(schemas[1]).names
    ):
        reject(
            "two complete keyed Cell inputs",
            "missing fields",
            "Retain the exact pair layout.",
            "analysis.local",
        )
    paired: list[dict[tuple[object, ...], tuple[str, int | float | None]]] = []
    for frame, schema in zip((left, right), schemas, strict=True):
        mapping: dict[tuple[object, ...], tuple[str, int | float | None]] = {}
        for row in _pandas_records(frame, schema):
            identity = tuple(row[key] for key in keys)
            tag, value, reason = row["cell_tag"], row["value"], row["cell_reason"]
            if any(item is None for item in identity) or identity in mapping:
                reject(
                    "unique complete pair keys",
                    repr(identity),
                    "Correct the retained key.",
                    "analysis.local",
                )
            if tag == "defined":
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or reason is not None
                ):
                    reject(
                        "finite Defined pair Cell",
                        repr((tag, value, reason)),
                        "Correct the Cell.",
                        "analysis.local",
                    )
            elif tag == "null":
                if value is not None or reason not in ("source_null", "empty_contribution"):
                    reject(
                        "ordinary Null pair Cell",
                        repr((tag, value, reason)),
                        "Correct the Cell.",
                        "analysis.local",
                    )
            else:
                reject(
                    "Defined or ordinary Null pair Cell",
                    str(tag),
                    "Use a qualified pair policy.",
                    "analysis.local",
                )
            assert isinstance(tag, str) and (value is None or isinstance(value, (int, float)))
            mapping[identity] = (tag, value)
        paired.append(mapping)
    a, b = paired
    if a.keys() != b.keys():
        reject(
            "the same complete pair key set",
            "unmatched keys",
            "Correct the paired observations.",
            "analysis.local",
        )
    xs: list[int | float] = []
    ys: list[int | float] = []
    null_count = 0
    for key, (tag_a, value_a) in a.items():
        tag_b, value_b = b[key]
        if tag_a == "null" or tag_b == "null":
            null_count += 1
            continue
        assert isinstance(value_a, (int, float)) and isinstance(value_b, (int, float))
        xs.append(value_a)
        ys.append(value_b)
    size = len(xs)
    scored = score(tuple(Fraction(x) for x in xs), tuple(Fraction(y) for y in ys), "spearman")
    return SpearmanResult(scored.status, scored.coefficient, len(a), len(a), null_count, size)


def arithmetic(stage: LocalMethodStage, cells: tuple[Cell, ...]) -> ArithmeticResult:
    """Finish admitted int64 or float64 rows after R4 verifies their input."""
    if (
        not isinstance(stage.node.parameters, RowState)
        or stage.node.parameters.method not in ("sum", "mean")
        or stage.implementation.key.route not in ("ibis_python",)
        or type(cells) is not tuple
    ):
        reject(
            "one qualified local current-row sum or mean",
            str(stage.node.method),
            "Use the exact registered local method and validated Cells.",
            "analysis.local",
        )
    input_type = stage.implementation.key.input_types[0]
    floating = input_type == ScalarType("float64")
    integer_values: list[int] = []
    float_values: list[float] = []
    for cell in cells:
        if not isinstance(cell, Defined):
            reject(
                "finite Defined numeric input Cells",
                type(cell).__name__,
                "Select complete numeric Cells.",
                "analysis.local",
            )
        value = cell.value
        if floating:
            if type(value) is not float or not math.isfinite(value):
                reject(
                    "finite float64 input",
                    repr(value),
                    "Correct the retained value.",
                    "analysis.local",
                )
            float_values.append(value)
        else:
            if type(value) is not int or not -(2**63) <= value < 2**63:
                reject(
                    "finite Defined int64 input Cells",
                    repr(value),
                    "Correct the retained value.",
                    "analysis.local",
                )
            if stage.node.parameters.method == "mean" and abs(value) > 2**53:
                reject(
                    "int64 mean operands within exact float64 integer range",
                    str(value),
                    "Use a separately qualified high-precision mean method.",
                    "analysis.local",
                )
            integer_values.append(value)
    total: int | float
    if floating:
        try:
            total = math.fsum(float_values)
        except OverflowError:
            reject(
                "finite float64 current sum",
                "overflow",
                "Use a qualified wider method.",
                "analysis.local",
            )
        if not math.isfinite(total):
            reject(
                "finite float64 current sum",
                repr(total),
                "Use a qualified wider method.",
                "analysis.local",
            )
    else:
        total = sum(integer_values)
        if not -(2**63) <= total < 2**63:
            reject(
                "checked int64 current sum",
                str(total),
                "Use a qualified wider method.",
                "analysis.local",
            )
    if stage.node.parameters.method == "mean":
        cell_result: Defined | Undefined = (
            Defined(float(total) / len(cells)) if cells else Undefined("empty_mean")
        )
    else:
        cell_result = Defined(total)
    return ArithmeticResult(cell_result, total, len(cells))


def count(stage: LocalMethodStage, cells: tuple[Cell, ...]) -> CountResult:
    """Consume validated retained or prepared rows without reading or publishing."""
    if (
        not isinstance(stage.node.parameters, RowState)
        or stage.node.parameters.method != "count"
        or stage.implementation.key.route not in ("ibis_python",)
    ):
        reject(
            "the registered local count stage",
            str(stage.node.method),
            "Use row.count over verified fixed or prepared inputs.",
            "analysis.local",
        )
    limit = stage.implementation.resources.max_rows
    if (
        type(cells) is not tuple
        or any(type(c) not in (Defined, Null, Undefined, Unknown) for c in cells)
        or any(
            isinstance(c, Defined)
            and not (
                (
                    (
                        stage.implementation.key.input_types[0] == ScalarType("int64")
                        or isinstance(stage.implementation.key.input_types[0], DurationType)
                    )
                    and type(c.value) is int
                    and -(2**63) <= c.value < 2**63
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("float64")
                    and type(c.value) is float
                    and math.isfinite(c.value)
                )
                or (
                    isinstance(stage.implementation.key.input_types[0], DecimalType)
                    and type(c.value) is Decimal
                    and c.value.is_finite()
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("string")
                    and type(c.value) is str
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("boolean")
                    and type(c.value) is bool
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("date")
                    and type(c.value) is date
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("timestamp")
                    and isinstance(c.value, datetime)
                )
            )
            for c in cells
        )
        or (limit is not None and len(cells) > limit)
    ):
        reject(
            f"validated Cells within {limit} rows",
            str(len(cells)),
            "Supply validated retained Cells within the registered resource limit.",
            "analysis.local",
        )
    return CountResult(Defined(len(cells)), len(cells))


def count_defined(stage: LocalMethodStage, cells: tuple[Cell, ...]) -> CountResult:
    """Count validated Defined Cells without a fixed row-cap qualification."""
    if (
        not isinstance(stage.node.parameters, RowState)
        or stage.node.parameters.method != "count_defined"
        or stage.implementation.key.route not in ("ibis_python",)
        or type(cells) is not tuple
        or any(type(cell) not in (Defined, Null, Undefined, Unknown) for cell in cells)
    ):
        reject(
            "a qualified fixed defined-count over validated Cells",
            str(stage.node.method),
            "Use the exact retained four-state input.",
            "analysis.local",
        )
    value = sum(isinstance(cell, Defined) for cell in cells)
    return CountResult(Defined(value), value)


def query_combine(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    """Combine completed Metric samples and check only demanded numeric values."""
    from marivo.semantic.metric_graph_lowering import numeric_failure

    program = stage.program
    assert program is not None
    frame, checks = _combine_program(program, inputs, stage.keys, stage.columns[len(stage.keys) :])
    for name, expected, received in checks:
        violation = frame[name].astype("boolean").fillna(False)
        if violation.any():
            coordinates = frame.loc[violation, list(stage.keys)].head(6).to_dict(orient="records")
            numeric_failure(expected, f"{received}; coordinates={coordinates}")
    return frame.loc[:, [column.slot_id for column in stage.columns]]


def _combine_program(
    program: FormulaProgram,
    inputs: tuple[pd.DataFrame, ...],
    key_slots: tuple[str, ...],
    outputs: tuple[DataColumn, ...],
    forced_domain: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, tuple[tuple[str, str, str], ...]]:
    """Keep sample checks as data until the enclosing formula demands them."""

    import ibis

    from marivo._definition.expressions import (
        DeclaredMetricRef,
        EntityCount,
        MemberCount,
        MetricNode,
        PerReduce,
        PerWeightedMean,
        SourceAggregate,
        SourceWeightedMean,
    )
    from marivo._definition.graph import children
    from marivo._definition.relations import (
        LiteralPopulation,
        PopulationDependency,
        PopulationProgram,
    )
    from marivo._definition.types import AxisBinding, CalendarCoordinate
    from marivo.analysis.compiler.observation import (
        FormulaInput,
        data_type,
        duration_type,
        formula_columns,
        ibis_type,
    )
    from marivo.analysis.core.data_types import DurationDataType
    from marivo.analysis.methods._ibis_numeric import evaluate_numeric
    from marivo.analysis.methods._sample_numeric import reduce_samples
    from marivo.analysis.methods.physical import stage_arrow_type
    from marivo.semantic.metric_graph_lowering import (
        NumericCheck,
        NumericValue,
        lower_metric_formula,
        numeric_failure,
    )

    keys = list(key_slots)
    population_frames = inputs[len(program.values) :]
    inputs = inputs[: len(program.values)]
    grouped: dict[tuple[int, ...], tuple[FormulaInput, list[pd.DataFrame]]] = {}
    for binding, frame in zip(program.values, inputs, strict=True):
        if binding.occurrence in grouped:
            original, frames = grouped[binding.occurrence]
            frames.append(frame.rename(columns={binding.column.slot_id: original.column.slot_id}))
        else:
            grouped[binding.occurrence] = (binding, [frame])
    bindings = [item[0] for item in grouped.values()]
    frames = [
        pd.concat(item[1], ignore_index=True) if len(item[1]) > 1 else item[1][0]
        for item in grouped.values()
    ]
    program = replace(program, values=tuple(bindings))
    inputs = tuple(frames)
    if keys:
        domain = (
            pd.concat(
                [
                    frame.loc[
                        frame[
                            "__sample_present"
                            if "__sample_present" in frame.columns
                            else "__numeric_present"
                        ]
                        .astype("boolean")
                        .fillna(False),
                        keys,
                    ]
                    if "__numeric_present" in frame.columns
                    else frame.loc[:, keys]
                    for frame in inputs
                ],
                ignore_index=True,
            ).drop_duplicates()
            if inputs
            else pd.DataFrame(columns=keys)
        )
        if program.calendar is not None and program.output_population is None:
            buckets = pd.DataFrame(
                {
                    program.calendar_key: pd.Series(
                        range(len(program.calendar.cells)), dtype="int64[pyarrow]"
                    )
                }
            )
            field_keys = [key for key in keys if key != program.calendar_key]
            domain = (
                domain.loc[:, field_keys].drop_duplicates().merge(buckets, how="cross")
                if field_keys
                else buckets
            )
            domain = domain.loc[:, keys]
        domain = domain.reset_index(drop=True)
    else:
        domain = pd.DataFrame(index=pd.RangeIndex(1))
    if forced_domain is not None:
        domain = (
            forced_domain.loc[:, keys].drop_duplicates().reset_index(drop=True)
            if keys
            else pd.DataFrame(index=pd.RangeIndex(1))
        )
    for binding in bindings:
        for coverage in binding.coverage:
            if coverage.key not in keys:
                continue
            remaining_keys = [key for key in keys if key != coverage.key]
            coordinates = domain.loc[:, remaining_keys].drop_duplicates()
            if coverage.output_key is not None:
                coordinates = coordinates.loc[
                    coordinates[coverage.output_slot] == coverage.output_key
                ]
            if not remaining_keys:
                coordinates = pd.DataFrame(index=pd.RangeIndex(1))
            expected_grid = pd.DataFrame({coverage.key: list(coverage.points)})
            complete = coordinates.merge(expected_grid, how="cross")
            domain = (
                pd.concat([domain, complete.loc[:, keys]], ignore_index=True)
                .drop_duplicates()
                .reset_index(drop=True)
            )

    def population_domain(definition: PopulationDependency, slots: tuple[str, ...]) -> pd.DataFrame:
        from marivo.analysis.dsl.population import population_keys

        if isinstance(definition, PopulationProgram):
            found = next(
                (
                    (binding, frame)
                    for binding, frame in zip(program.populations, population_frames, strict=True)
                    if binding.definition == definition
                ),
                None,
            )
            if found is not None:
                binding, frame = found
                selected = frame.loc[:, list(binding.keys)].copy()
                selected.columns = list(slots)
                return selected
        finite = population_keys(definition)
        if finite is None:
            numeric_failure(
                "a prepared independent Population domain", "missing source domain input"
            )
        records = [
            tuple(
                component
                for axis, value in zip(definition.axes, row, strict=True)
                for component in (
                    value
                    if isinstance(axis, AxisBinding)
                    and axis.role == "identity"
                    and isinstance(value, tuple)
                    else (value,)
                )
            )
            for row in finite
        ]
        return pd.DataFrame(records, columns=slots)

    member_data: pd.DataFrame | None = None
    if program.members is not None:
        member_data = population_domain(program.members.population, program.members.keys)
        member_coordinates = member_data.loc[
            :, [source for _, source in program.members.output_mapping]
        ].rename(columns={source: target for target, source in program.members.output_mapping})
        if program.calendar is not None:
            member_coordinates = member_coordinates.merge(
                pd.DataFrame({program.calendar_key: range(len(program.calendar.cells))}),
                how="cross",
            )
        if keys:
            member_coordinates = member_coordinates.loc[:, keys].drop_duplicates()
            domain = (
                pd.concat([domain, member_coordinates], ignore_index=True)
                .drop_duplicates()
                .reset_index(drop=True)
            )

    if program.output_population is not None:
        complete_domain = population_domain(program.output_population, key_slots)
        if keys:
            covered = domain.merge(
                complete_domain.drop_duplicates().assign(__covered=True), on=keys, how="left"
            )
            uncovered = bool(covered["__covered"].isna().any())
        else:
            uncovered = bool(inputs) and len(domain) > 0 and len(complete_domain) == 0
        if uncovered:
            from marivo.analysis.materialization.errors import MaterializationError

            raise MaterializationError(
                expected="an output Population covering every observed coordinate",
                received="observed coordinates outside by=Population",
                repair="Include every observed output coordinate in by=Population, or filter contributions before choosing that complete output domain.",
                stage="population_coverage",
                help_target="Query.execute",
            )
        domain = complete_domain.drop_duplicates().reset_index(drop=True)
    declarations = dict(program.declared)
    type_values = {
        binding.occurrence: NumericValue(ibis.literal(None).cast(ibis_type(binding.column.dtype)))
        for binding in bindings
    }

    def materialize_samples(node: MetricNode, occurrence: tuple[int, ...]) -> None:
        if isinstance(node, MemberCount):
            if any(binding.occurrence == occurrence for binding in bindings):
                return
            assert program.members is not None and member_data is not None
            selected = member_data.rename(
                columns={source: target for target, source in program.members.output_mapping}
            )
            if program.calendar is not None:
                selected = selected.merge(
                    pd.DataFrame({program.calendar_key: range(len(program.calendar.cells))}),
                    how="cross",
                )
            slot = "members:" + ".".join(str(part) for part in occurrence)
            counted = (
                selected.groupby(keys, dropna=False, sort=True).size().rename(slot).reset_index()
                if keys
                else pd.DataFrame({slot: [len(member_data)]})
            )
            bindings.append(
                FormulaInput(
                    occurrence,
                    slot,
                    DataColumn(slot, slot, ScalarDataType("int64"), False),
                    True,
                    keys=key_slots,
                )
            )
            frames.append(counted)
            return
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            return
        if isinstance(node, DeclaredMetricRef):
            materialize_samples(declarations[node.ref], (*occurrence, 0))
            return
        if isinstance(node, (PerReduce, PerWeightedMean)):
            sample_axes = node.samples if isinstance(node.samples, tuple) else node.samples.axes
            source_groups = next(
                (
                    binding.key_groups
                    for binding in bindings
                    if binding.occurrence[: len(occurrence)] == occurrence and binding.key_groups
                ),
                (),
            )
            remaining_groups = tuple(
                group for group in source_groups if not all(key in key_slots for key in group)
            )
            sample_groups = (
                remaining_groups[: len(sample_axes)]
                if remaining_groups
                else tuple((f"key.{len(keys) + i}",) for i in range(len(sample_axes)))
            )
            sample_keys = tuple(key for group in sample_groups for key in group)
            complete_keys = (*key_slots, *sample_keys)
            operands = (node.operand,) if isinstance(node, PerReduce) else (node.value, node.weight)
            selected_bindings: list[FormulaInput] = []
            selected_frames: list[pd.DataFrame] = []
            consumed: list[int] = []
            for operand_index, _ in enumerate(operands):
                prefix = (*occurrence, operand_index)
                for index, (binding, frame) in enumerate(zip(bindings, frames, strict=True)):
                    if binding.occurrence[: len(prefix)] == prefix:
                        selected_bindings.append(
                            replace(
                                binding,
                                occurrence=(operand_index, *binding.occurrence[len(prefix) :]),
                            )
                        )
                        selected_frames.append(frame)
                        consumed.append(index)
            child_program = FormulaProgram(
                tuple((f"sample_{i}", operand) for i, operand in enumerate(operands)),
                program.declared,
                tuple(selected_bindings),
                program.calendar,
                program.populations,
                calendar_key=program.calendar_key,
            )
            complete_domain = None
            if isinstance(node.samples, (LiteralPopulation, PopulationProgram)):
                sample_domain = population_domain(node.samples, sample_keys)
                complete_domain = (
                    domain.merge(sample_domain, how="cross") if keys else sample_domain
                )
            child_outputs = formula_columns(child_program)
            child_frame, child_checks = _combine_program(
                child_program,
                (*selected_frames, *population_frames),
                complete_keys,
                child_outputs,
                complete_domain,
            )
            for child_column in child_outputs:
                if isinstance(child_column.dtype, DurationDataType):
                    child_frame[child_column.slot_id] = pd.Series(
                        pd.arrays.ArrowExtensionArray(
                            pa.array(child_frame[child_column.slot_id]).cast(pa.int64())
                        )
                    )
            order_key = None
            if isinstance(node, PerReduce) and node.order_by is not None:
                order_key = next(
                    key
                    for group, axis in zip(sample_groups, sample_axes, strict=True)
                    for key in group
                    if isinstance(axis, AxisBinding) and axis.field == node.order_by
                )
            if (
                isinstance(node, PerReduce)
                and node.reducer in ("first", "last")
                and order_key is None
            ):
                calendar_keys = [
                    key
                    for group, axis in zip(sample_groups, sample_axes, strict=True)
                    if isinstance(axis, CalendarCoordinate)
                    for key in group
                ]
                if len(calendar_keys) == 1:
                    order_key = calendar_keys[0]
            dtype = duration_type(
                node,
                {},
                values={binding.occurrence: binding.column.dtype for binding in bindings},
                declared=declarations,
                occurrence=occurrence,
            ) or data_type(
                lower_metric_formula(node, occurrence, type_values, declarations).value.type()
            )
            reduced, reduced_checks = reduce_samples(
                node,
                child_frame,
                key_slots,
                domain,
                tuple(column.slot_id for column in child_outputs),
                child_checks,
                order_key=order_key,
                exact_duration=isinstance(dtype, DurationDataType),
                output_type=dtype,
            )
            for index in sorted(set(consumed), reverse=True):
                bindings.pop(index)
                frames.pop(index)
            slot = "sample:" + ".".join(str(part) for part in occurrence)
            reduced = reduced.rename(columns={"value": slot})
            bindings.append(
                FormulaInput(
                    occurrence,
                    slot,
                    DataColumn(slot, slot, dtype, True),
                    False,
                    reduced_checks,
                    key_slots,
                )
            )
            frames.append(reduced)
            return
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                materialize_samples(edge.child, (*occurrence, edge.index))

    for i, (_, node) in enumerate(program.outputs):
        materialize_samples(node, (i,))
    program = replace(program, values=tuple(bindings))
    inputs = tuple(frames)
    values: dict[tuple[int, ...], NumericValue] = {}
    physical: dict[str, pd.Series] = {}
    for input_index, (binding, frame) in enumerate(zip(program.values, inputs, strict=True)):
        slot = binding.column.slot_id
        input_dtype = frame[slot].dtype
        if (
            isinstance(binding.column.dtype, DurationDataType)
            and isinstance(input_dtype, pd.ArrowDtype)
            and pa.types.is_duration(input_dtype.pyarrow_dtype)
        ):
            frame = frame.copy()
            frame[slot] = pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array(frame[slot]).cast(pa.int64()))
            )
        if keys:
            marked = frame.assign(**{"__present": True})
            merged = domain.merge(marked, on=keys, how="left", validate="one_to_one", sort=False)
            value = merged[slot].astype(object)
            if binding.empty_zero:
                value = value.mask(
                    merged["__numeric_present"].astype("boolean").fillna(False).eq(False)
                    if "__numeric_present" in merged.columns
                    else merged["__present"].isna(),
                    pd.Series(
                        [Decimal(0) if isinstance(binding.column.dtype, DecimalDataType) else 0]
                        * len(value),
                        dtype=object,
                    ),
                )
        else:
            value = frame[slot].astype(object).reset_index(drop=True)
        name = f"input_{input_index}"
        active_coverage = tuple(coverage for coverage in binding.coverage if coverage.key in keys)
        check_names = tuple(f"check_{input_index}_{i}" for i in range(len(binding.checks)))
        coverage_names = tuple(f"coverage_{input_index}_{i}" for i in range(len(active_coverage)))
        table = ibis.table(
            {
                name: ibis_type(binding.column.dtype),
                **dict.fromkeys((*check_names, *coverage_names), "boolean"),
            },
            name=f"_completed_{input_index}",
        )
        physical[name] = value
        checks = []
        aligned = merged if keys else frame.reset_index(drop=True)
        for key, (column_name, expected, received) in zip(check_names, binding.checks, strict=True):
            physical[key] = aligned[column_name].fillna(False).astype(object)
            checks.append(NumericCheck(table[key], expected, received))
        for key, coverage in zip(coverage_names, active_coverage, strict=True):
            required = domain[coverage.key].isin(coverage.points)
            if coverage.output_key is not None:
                required &= domain[coverage.output_slot] == coverage.output_key
            present = (
                aligned["__sample_present"].astype("boolean").fillna(False)
                if "__sample_present" in aligned
                else aligned["__present"].astype("boolean").fillna(False)
            )
            physical[key] = (required & ~present).astype(object)
            checks.append(
                NumericCheck(
                    table[key],
                    "complete fixed-cadence state samples",
                    f"missing state sample on {coverage.key}",
                )
            )
        values[binding.occurrence] = NumericValue(table[name], tuple(checks))
    declarations = dict(program.declared)

    result = domain.copy()
    retained: list[tuple[str, str, str]] = []
    for i, (_, node) in enumerate(program.outputs):
        column = outputs[i]
        expression = lower_metric_formula(node, (i,), values, declarations)
        for check in expression.checks:
            violation = (
                evaluate_numeric(check.violation.op(), physical, domain.index)
                .astype("boolean")
                .fillna(False)
                .astype(bool)
            )
            check_name = f"formula.check.{len(retained)}"
            result[check_name] = violation
            retained.append((check_name, check.expected, check.received))
        series = evaluate_numeric(
            expression.value.op(),
            physical,
            domain.index,
            exact_division=isinstance(column.dtype, DurationDataType),
        )
        # Match native Ibis's numeric result type, including integer true division.
        if isinstance(column.dtype, DurationDataType):
            series = pd.Series(
                [round(value) if pd.notna(value) else None for value in series], dtype=object
            )
        if isinstance(column.dtype, ScalarDataType) and column.dtype.name == "float64":
            series = series.map(lambda value: float(value) if pd.notna(value) else None)
        try:
            result[column.slot_id] = pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(series, type=stage_arrow_type(column.dtype), from_pandas=True)
                )
            )
        except (pa.ArrowException, OverflowError, ValueError, TypeError) as error:
            numeric_failure(f"{column.name}: {column.dtype}", f"numeric output conversion: {error}")
    return result, tuple(retained)
