"""Current Query sample combination over completed source contributions."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType


def query_combine(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    """Combine completed Metric samples and check only demanded numeric values."""
    from marivo.semantic._numeric_lowering import numeric_failure

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
    from marivo.semantic._numeric_lowering import (
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
        if binding.presence is not None:
            frame = frame.copy()
            frame["__numeric_present"] = frame[binding.presence]
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
