"""Recompute original Metrics from the current invocation's actual inputs."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import ibis.expr.datatypes as dt
import pandas as pd
import pyarrow as pa

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import (
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    RowField,
    RowNode,
    SourceWeightedMean,
)
from marivo._definition.types import AxisBinding, ExactRef
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.compiler.original import (
    AggregatePayload,
    ComponentLayout,
    CountPayload,
    WeightedPayload,
    require_original_formula,
    share_additive,
)
from marivo.analysis.core.data_types import DataColumn, DurationDataType
from marivo.analysis.materialization.query_components import ComponentFrame
from marivo.analysis.methods._sample_numeric import reduce_samples
from marivo.analysis.methods.local import _combine_program
from marivo.analysis.methods.numeric_state import nullable_arithmetic
from marivo.semantic.metric_graph_lowering import numeric_failure


def original_inputs(
    program: FormulaProgram,
    components: tuple[ComponentFrame, ...],
    source_keys: tuple[str, ...],
    population_frames: tuple[pd.DataFrame, ...],
    source_columns: tuple[DataColumn, ...],
) -> tuple[FormulaProgram, tuple[ComponentFrame, ...]]:
    """Materialize count leaves from complete explicit member identities."""
    from marivo._definition.graph import children
    from marivo.analysis.compiler.observation import FormulaInput, ReductionInput, data_type
    from marivo.analysis.core.data_types import ScalarDataType
    from marivo.analysis.dsl.population import population_keys

    occurrences: list[tuple[int, ...]] = []
    declarations = dict(program.declared)

    def visit(node: MetricNode, occurrence: tuple[int, ...]) -> None:
        if isinstance(node, MemberCount):
            occurrences.append(occurrence)
        elif isinstance(node, DeclaredMetricRef):
            visit(declarations[node.ref], (*occurrence, 0))
        else:
            for edge in children(node):
                if isinstance(edge.child, MetricNode):
                    visit(edge.child, (*occurrence, edge.index))

    for index, (_, node) in enumerate(program.outputs):
        visit(node, (index,))
    if not occurrences:
        return program, components
    member = program.members
    if member is None:
        if all(
            any(binding.occurrence == occurrence for binding in program.values)
            for occurrence in occurrences
        ):
            return program, components
        numeric_failure(
            "complete explicit member identities for member_count rollup", "missing member binding"
        )
    slot = "__original_member_count"
    existing = next((item for item in components if item.layout.reduction.value_slot == slot), None)
    column = DataColumn(slot, slot, ScalarDataType("int64"), False)
    if existing is None:
        domain_binding = next(
            (
                (binding, frame)
                for binding, frame in zip(program.populations, population_frames, strict=True)
                if binding.definition == member.population
            ),
            None,
        )
        if domain_binding is not None:
            binding, frame = domain_binding
            data = frame.loc[:, list(binding.keys)].copy()
            data.columns = list(member.keys)
            identity_width = len(binding.key_groups[0]) if binding.key_groups else 1
        else:
            finite = population_keys(member.population)
            if finite is None:
                numeric_failure(
                    "actual explicit member population input", "missing member source stage"
                )
            records = [
                tuple(
                    component
                    for axis, value in zip(member.population.axes, row, strict=True)
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
            data = pd.DataFrame(records, columns=member.keys)
            first = next(iter(finite), None)
            identity_width = (
                len(first[0])
                if first is not None and isinstance(first[0], tuple)
                else len(member.keys) - len(member.population.axes) + 1
            )
        axis = member.population.axes[0]
        if not isinstance(axis, AxisBinding) or axis.role != "identity":
            numeric_failure("a complete leading member identity axis", repr(axis))
        for output, field in member.output_mapping:
            data[output] = data[field]
        if program.calendar is not None and program.calendar_key not in data.columns:
            data = data.merge(
                pd.DataFrame({program.calendar_key: range(len(program.calendar.cells))}),
                how="cross",
            )
        if not set(source_keys) <= set(data.columns):
            numeric_failure(
                "member classification for every original output axis", repr(source_keys)
            )
        identity_names = tuple(f"__original_member.{index}" for index in range(identity_width))
        for target, source in zip(identity_names, member.keys[:identity_width], strict=True):
            data[target] = data[source]
        data = data.loc[:, [*source_keys, *identity_names]].drop_duplicates().reset_index(drop=True)
        known_columns = {column.slot_id: column for column in source_columns}
        known_columns.update(
            {
                column.slot_id: column
                for component in components
                for column in component.layout.columns
            }
        )

        def typed(name: str) -> DataColumn:
            if name in known_columns:
                return known_columns[name]
            return DataColumn(
                name,
                name,
                data_type(dt.dtype(pa.array(data[name]).type)),
                bool(data[name].isna().any()),
            )

        layout = ComponentLayout(
            ReductionInput(EntityCount(axis.entity, None), (), slot),
            (*(typed(key) for key in source_keys), column),
            tuple((typed(name), name) for name in identity_names),
            CountPayload(),
        )
        existing = ComponentFrame(layout, data)
        components = (*components, existing)
    values = (
        *program.values,
        *(
            FormulaInput(
                occurrence,
                slot,
                column,
                True,
                keys=existing.layout.keys,
                key_groups=tuple((key,) for key in existing.layout.keys),
            )
            for occurrence in occurrences
        ),
    )
    return replace(program, values=values, members=None), components


def _root(node: RowNode) -> ExactRef:
    if isinstance(node, RowField):
        return node.root
    if isinstance(node, MeasureAssertion):
        return _root(node.operand)
    return _root(node.inputs[0])


def _reduce(
    component: ComponentFrame, frame: pd.DataFrame, keys: tuple[str, ...], domain: pd.DataFrame
) -> tuple[pd.DataFrame, tuple[tuple[str, str, str], ...]]:
    layout, node = component.layout, component.layout.reduction.node
    slot = layout.reduction.value_slot
    if isinstance(node, EntityCount):
        counted = (
            frame.groupby(list(keys), dropna=False, sort=False).size().rename(slot).reset_index()
            if keys
            else pd.DataFrame({slot: [len(frame)]})
        )
        if keys:
            counted = domain.merge(counted, on=list(keys), how="left", validate="one_to_one")
            counted[slot] = counted[slot].fillna(0)
        return counted, ()
    assert isinstance(layout.payload, (AggregatePayload, WeightedPayload))
    root = _root(node.value if isinstance(node, SourceWeightedMean) else node.operand)
    axis = AxisBinding(root, None, (), "identity")
    sample: PerReduce | PerWeightedMean
    names: tuple[str, ...]
    if isinstance(node, SourceWeightedMean):
        assert isinstance(layout.payload, WeightedPayload)
        sample = PerWeightedMean(node, node, (axis,), node.nulls, node.zero_denominator)
        names = (layout.payload.value.slot_id, layout.payload.weight.slot_id)
    else:
        sample = PerReduce(
            node, (axis,), node.reducer, node.q, node.nulls, node.rule_version, node.empty
        )
        names = (layout.payload.value.slot_id,)
    column = next(item for item in layout.columns if item.slot_id == slot)
    reduced, checks = reduce_samples(
        sample,
        frame,
        keys,
        domain,
        names,
        (),
        exact_duration=isinstance(column.dtype, DurationDataType),
        output_type=column.dtype,
    )
    if isinstance(column.dtype, DurationDataType):
        reduced["value"] = pd.Series(
            [round(value) if pd.notna(value) else None for value in reduced["value"]],
            index=reduced.index,
            dtype="int64[pyarrow]",
        )
    renamed = {name: f"__original_check.{index}" for index, (name, _, _) in enumerate(checks)}
    reduced = reduced.rename(columns={"value": slot, **renamed})
    for key in keys:
        # Group reduction rebuilds rows as Python records. Restore the checked
        # coordinate carrier before the original formula joins its domain.
        try:
            values = pa.array(reduced[key], type=pa.array(domain[key]).type, from_pandas=True)
        except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
            numeric_failure("the checked original coordinate type", f"{key}: {error}")
        reduced[key] = pd.Series(pd.arrays.ArrowExtensionArray(values), index=reduced.index)
    metadata = tuple((renamed[name], expected, received) for name, expected, received in checks)
    return reduced, metadata


def _mapping(
    mapping: pd.DataFrame, source_keys: tuple[str, ...], target_keys: tuple[str, ...]
) -> pd.DataFrame:
    targets = tuple(f"__target.{index}" for index in range(len(target_keys)))
    required = (*source_keys, *targets)
    if set(required) != set(mapping.columns):
        numeric_failure("complete unique original coordinate mapping", repr(tuple(mapping.columns)))
    if source_keys and mapping.duplicated(list(source_keys)).any():
        numeric_failure("one target for each selected fine coordinate", "duplicate fine mapping")
    return mapping.loc[:, list(required)].copy()


def rollup_components(
    program: FormulaProgram,
    components: tuple[ComponentFrame, ...],
    source_keys: tuple[str, ...],
    target_keys: tuple[str, ...],
    mapping: pd.DataFrame,
    outputs: tuple[DataColumn, ...],
    *,
    population_frames: tuple[pd.DataFrame, ...] = (),
    source_columns: tuple[DataColumn, ...] = (),
    target_columns: tuple[DataColumn, ...] = (),
) -> tuple[pd.DataFrame, tuple[ComponentFrame, ...]]:
    """Reclassify actual contributions, reduce at full sample grain, then finish.

    ``mapping`` contains the complete selected source keys and ``__target.N``
    columns. Sample keys remain independent from the displayed output keys.
    Returned components permit a subsequent original rollup in this invocation.
    """
    program, components = original_inputs(
        program, components, source_keys, population_frames, source_columns
    )
    for _, node in program.outputs:
        require_original_formula(node, dict(program.declared))
    selected = _mapping(mapping, source_keys, target_keys)
    target_names = tuple(f"__target.{index}" for index in range(len(target_keys)))
    targets = selected.loc[:, list(target_names)].rename(
        columns=dict(zip(target_names, target_keys, strict=True))
    )
    domain = targets.drop_duplicates().reset_index(drop=True)
    if not target_keys:
        domain = pd.DataFrame(index=pd.RangeIndex(1))
    frames: list[pd.DataFrame] = []
    values = []
    retained: list[ComponentFrame] = []
    for binding in program.values:
        component = next(
            (
                item
                for item in components
                if item.layout.reduction.value_slot == binding.column.slot_id
            ),
            None,
        )
        if component is None:
            numeric_failure(
                "actual sufficient inputs for every original Metric dependency",
                f"missing original source input {binding.stage_id}",
            )
        layout = component.layout
        sample_keys = tuple(key for key in layout.keys if key not in source_keys)
        new_samples = tuple(f"key.{len(target_keys) + index}" for index in range(len(sample_keys)))
        sample_rename = dict(zip(sample_keys, new_samples, strict=True))
        # Join by complete fine keys, then erase their old names before target
        # slots are installed. This also handles retaining/reordering key.0.
        raw = (
            component.table.merge(
                selected, on=list(source_keys), how="inner", validate="many_to_one"
            )
            if source_keys
            else component.table.merge(selected, how="cross")
        )
        raw = raw.drop(columns=list(source_keys)).rename(
            columns={**sample_rename, **dict(zip(target_names, target_keys, strict=True))}
        )
        keys = (*target_keys, *new_samples)
        raw = raw.loc[
            :,
            [
                *keys,
                *(
                    column.slot_id
                    for column in layout.raw_columns
                    if column.slot_id not in layout.keys
                ),
            ],
        ]
        identity = [column.slot_id for column, _ in layout.identity]
        if binding.column.slot_id == "__original_member_count":
            raw = raw.drop_duplicates([*keys, *identity])
        elif raw.duplicated([*keys, *identity]).any():
            numeric_failure(
                "disjoint complete contributions within every original target coordinate",
                "a contribution appears in more than one selected fine coordinate",
            )
        support = (
            component.coordinates.merge(
                selected, on=list(source_keys), how="inner", validate="many_to_one"
            )
            if source_keys
            else component.coordinates.merge(selected, how="cross")
        )
        local_domain = (
            support.drop(columns=list(source_keys))
            .rename(columns={**sample_rename, **dict(zip(target_names, target_keys, strict=True))})
            .loc[:, list(keys)]
            .drop_duplicates()
            .reset_index(drop=True)
        )
        if not keys:
            local_domain = pd.DataFrame(index=pd.RangeIndex(1))
        reduced, checks = _reduce(component, raw, keys, local_domain)
        frames.append(reduced)
        # Recover the original internal group widths, including composite
        # identities, independently of the number of current output columns.
        original_output_width = len(binding.keys) - len(sample_keys)
        internal_groups = tuple(
            group
            for group in binding.key_groups
            if any(int(key.split(".")[1]) >= original_output_width for key in group)
        )
        offset = len(target_keys)
        groups: list[tuple[str, ...]] = [(key,) for key in target_keys]
        for group in internal_groups:
            groups.append(tuple(f"key.{offset + index}" for index in range(len(group))))
            offset += len(group)
        if not internal_groups:
            groups.extend((key,) for key in new_samples)
        coverage_rename = {
            **sample_rename,
            **{
                key: target_keys[index]
                for index, key in enumerate(source_keys)
                if index < len(target_keys)
            },
        }
        values.append(
            replace(
                binding,
                checks=checks,
                keys=keys,
                key_groups=tuple(groups),
                coverage=tuple(
                    replace(
                        coverage,
                        key=coverage_rename.get(coverage.key, coverage.key),
                        output_slot=coverage_rename.get(coverage.output_slot, coverage.output_slot),
                    )
                    for coverage in binding.coverage
                ),
            )
        )
        key_columns = tuple(
            DataColumn(key, key, raw_column.dtype, raw_column.nullable)
            for key, raw_column in (
                *tuple(
                    (
                        key,
                        next(
                            (
                                column
                                for column in layout.columns
                                if column.slot_id == source_keys[index]
                            ),
                            layout.columns[0],
                        ),
                    )
                    for index, key in enumerate(target_keys)
                ),
                *tuple(
                    (
                        sample_rename[key],
                        next(column for column in layout.columns if column.slot_id == key),
                    )
                    for key in sample_keys
                ),
            )
        )
        if target_columns:
            if tuple(column.slot_id for column in target_columns) != target_keys:
                numeric_failure("the exact typed original target keys", repr(target_columns))
            key_columns = (*target_columns, *key_columns[len(target_keys) :])
        retained_layout = replace(layout, columns=(*key_columns, binding.column))
        retained.append(ComponentFrame(retained_layout, raw.reset_index(drop=True), local_domain))
    recompute = replace(program, values=tuple(values), output_population=None)
    completed, checks = _combine_program(
        recompute, (*frames, *population_frames), target_keys, outputs, domain
    )
    for name, expected, received in checks:
        violation = completed[name].astype("boolean").fillna(False)
        if violation.any():
            coordinates = (
                completed.loc[violation, list(target_keys)].head(6).to_dict(orient="records")
            )
            numeric_failure(expected, f"{received}; original target coordinates={coordinates}")
    return completed.loc[:, [*target_keys, *(column.slot_id for column in outputs)]], tuple(
        retained
    )


def share_frame(
    program: FormulaProgram,
    components: tuple[ComponentFrame, ...],
    receiver: pd.DataFrame,
    source_keys: tuple[str, ...],
    partitions: tuple[str, ...],
    value_index: int,
    output_slot: str,
    zero_denominator: Literal["null", "error"] = "null",
    *,
    population_frames: tuple[pd.DataFrame, ...] = (),
    source_columns: tuple[DataColumn, ...] = (),
    merged_axes: tuple[ComparisonAxis, ...] | None = None,
    partition_mapping: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Append a share using a denominator from this exact stage's actual input."""
    from marivo.analysis.compiler.observation import formula_columns

    if not 0 <= value_index < len(program.outputs):
        numeric_failure("an original numeric Metric column", str(value_index))
    name, node = program.outputs[value_index]
    if not share_additive(node, dict(program.declared), merged_axes):
        numeric_failure(
            "a contribution-additive Metric for share", f"{name}: non-additive original formula"
        )
    if not set(partitions) <= set(source_keys):
        numeric_failure("share partitions among the complete receiver axes", repr(partitions))
    result = receiver.copy()
    if output_slot in result.columns:
        numeric_failure("a fresh share output column", output_slot)
    if len(result) == 0:
        result[output_slot] = pd.Series([], dtype=object)
        return result
    program, components = original_inputs(
        program, components, source_keys, population_frames, source_columns
    )
    group_keys = tuple(f"__target.{index}" for index in range(len(partitions)))
    mapping = receiver.loc[:, list(source_keys)].copy()
    if partition_mapping is None:
        for target, key in zip(group_keys, partitions, strict=True):
            mapping[target] = receiver[key]
    else:
        if tuple(partition_mapping.columns) != (
            *source_keys,
            *group_keys,
        ) or not partition_mapping.loc[:, list(source_keys)].reset_index(drop=True).equals(
            mapping.reset_index(drop=True)
        ):
            numeric_failure(
                "one complete denominator mapping per receiver key",
                "changed share partition mapping",
            )
        mapping = partition_mapping.copy()
    supports: dict[tuple[int, ...], list[pd.DataFrame]] = {}
    for binding in program.values:
        if binding.occurrence[0] != value_index:
            continue
        component = next(
            (
                item
                for item in components
                if item.layout.reduction.value_slot == binding.column.slot_id
            ),
            None,
        )
        if component is None:
            numeric_failure("actual common contribution support for share", binding.stage_id)
        identity = [column.slot_id for column, _ in component.layout.identity]
        selected = result.loc[:, list(source_keys)].drop_duplicates()
        actual = (
            component.table.merge(
                selected, on=list(source_keys), how="inner", validate="many_to_one"
            )
            if source_keys
            else component.table.merge(selected, how="cross")
        )
        actual = (
            actual.merge(mapping, on=list(source_keys), how="inner", validate="many_to_one")
            if source_keys
            else actual.merge(mapping, how="cross")
        )
        support = actual.loc[:, [*group_keys, *identity]].copy()
        support.columns = [*group_keys, *(f"identity.{index}" for index in range(len(identity)))]
        supports.setdefault(binding.occurrence, []).append(support)
    for occurrence, frames in supports.items():
        support = pd.concat(frames, ignore_index=True)
        if support.duplicated().any():
            numeric_failure(
                "disjoint fine contribution support within every share denominator group",
                f"overlapping contribution identity at original occurrence {occurrence!r}",
            )
    target_keys = tuple(f"key.{index}" for index in range(len(partitions)))
    # Recompute just the selected original Metric. Other columns' policies have
    # already been checked by the receiver, and share does not demand them again.
    selected_bindings = tuple(item for item in program.values if item.occurrence[0] == value_index)
    selected_program = replace(
        program,
        outputs=((name, node),),
        values=tuple(
            replace(item, occurrence=(0, *item.occurrence[1:])) for item in selected_bindings
        ),
    )
    columns = formula_columns(selected_program)
    denominator, _ = rollup_components(
        selected_program,
        components,
        source_keys,
        target_keys,
        mapping,
        columns,
        population_frames=population_frames,
        source_columns=source_columns,
        target_columns=tuple(
            replace(column, slot_id=target_keys[index])
            for index, partition in enumerate(partitions)
            for column in source_columns
            if column.slot_id == partition
        ),
    )
    denominator_slot = columns[0].slot_id
    if partitions:
        denominator = denominator.rename(
            columns={
                **dict(zip(target_keys, group_keys, strict=True)),
                denominator_slot: "__share_denominator",
            }
        )
        mapped = mapping.loc[:, list(group_keys)].merge(
            denominator, on=list(group_keys), how="left", validate="many_to_one"
        )
        reference = mapped["__share_denominator"]
    else:
        if len(denominator) != 1:
            numeric_failure("one complete automatic share denominator", str(len(denominator)))
        reference = pd.Series([denominator[denominator_slot].iloc[0]] * len(result), dtype=object)
    original_columns = formula_columns(program)
    numerator = receiver[original_columns[value_index].slot_id].reset_index(drop=True)
    divided = nullable_arithmetic(
        "divide", numerator, reference.reset_index(drop=True), zero_denominator=zero_denominator
    )
    from marivo.analysis.methods.numeric_state import finish_float64

    result[output_slot] = finish_float64(divided, "share").set_axis(result.index)
    return result


def drill_components(
    program: FormulaProgram,
    components: tuple[ComponentFrame, ...],
    receiver: pd.DataFrame,
    source_keys: tuple[str, ...],
    target_keys: tuple[str, ...],
    mappings: tuple[pd.DataFrame, ...],
    outputs: tuple[DataColumn, ...],
    *,
    population_frames: tuple[pd.DataFrame, ...] = (),
    source_columns: tuple[DataColumn, ...] = (),
    target_columns: tuple[DataColumn, ...] = (),
) -> tuple[pd.DataFrame, tuple[ComponentFrame, ...]]:
    """Classify selected complete contributions and finish the original formula.

    Each mapping corresponds to one ``program.values`` occurrence. Its key is
    the complete parent output key plus the contribution identity. New field
    reads have already completed their selected-fiber checks at this boundary.
    """
    from marivo.analysis.compiler.observation import data_type

    program, components = original_inputs(
        program, components, source_keys, population_frames, source_columns
    )
    if len(mappings) != len(program.values):
        numeric_failure(
            "one checked identity classification per original source occurrence",
            f"{len(mappings)} mappings for {len(program.values)} original occurrences",
        )
    selected = receiver.loc[:, list(source_keys)].drop_duplicates()
    target_names = tuple(f"__target.{index}" for index in range(len(target_keys)))
    classified: list[ComponentFrame] = []
    bindings = []
    domains: list[pd.DataFrame] = []
    for binding, mapping in zip(program.values, mappings, strict=True):
        component = next(
            (
                item
                for item in components
                if item.layout.reduction.value_slot == binding.column.slot_id
            ),
            None,
        )
        if component is None:
            numeric_failure("actual original inputs for drill", f"missing {binding.stage_id}")
        layout = component.layout
        identities = tuple(column.slot_id for column, _ in layout.identity)
        join_keys = (*source_keys, *identities)
        if (
            set(mapping.columns) != {*join_keys, *target_names}
            or mapping.duplicated(list(join_keys)).any()
        ):
            numeric_failure(
                "one complete new classification per selected contribution identity",
                repr(tuple(mapping.columns)),
            )
        raw = (
            component.table.merge(
                selected, on=list(source_keys), how="inner", validate="many_to_one"
            )
            if source_keys
            else component.table.merge(selected, how="cross")
        )
        raw = raw.merge(
            mapping,
            on=list(join_keys),
            how="left",
            validate="many_to_one",
            indicator="__original_mapping",
        )
        if not raw["__original_mapping"].eq("both").all():
            missing = (
                raw.loc[raw["__original_mapping"] != "both", list(join_keys)]
                .head(6)
                .to_dict(orient="records")
            )
            numeric_failure(
                "a complete checked new classification for every selected contribution",
                repr(missing),
            )
        samples = tuple(key for key in layout.keys if key not in source_keys)
        sample_rename = {
            key: f"key.{len(target_keys) + index}" for index, key in enumerate(samples)
        }
        raw = raw.drop(columns=[*source_keys, "__original_mapping"]).rename(
            columns={**sample_rename, **dict(zip(target_names, target_keys, strict=True))}
        )
        new_samples = tuple(sample_rename[key] for key in samples)
        keys = (*target_keys, *new_samples)
        raw = raw.loc[
            :,
            [
                *keys,
                *(
                    column.slot_id
                    for column in layout.raw_columns
                    if column.slot_id not in layout.keys
                ),
            ],
        ]
        support = raw.loc[:, list(keys)].drop_duplicates().reset_index(drop=True)
        domains.append(support.loc[:, list(target_keys)])
        new_targets = target_columns or tuple(
            DataColumn(
                key, key, data_type(dt.dtype(pa.array(raw[key]).type)), bool(raw[key].isna().any())
            )
            for key in target_keys
        )
        if tuple(column.slot_id for column in new_targets) != target_keys:
            numeric_failure("the exact typed drill target keys", repr(new_targets))
        sample_columns = tuple(
            replace(
                next(column for column in layout.columns if column.slot_id == key),
                slot_id=sample_rename[key],
            )
            for key in samples
        )
        new_layout = replace(layout, columns=(*new_targets, *sample_columns, binding.column))
        classified.append(ComponentFrame(new_layout, raw.reset_index(drop=True), support))
        original_output_width = len(binding.keys) - len(samples)
        internal = tuple(
            group
            for group in binding.key_groups
            if any(int(key.split(".")[1]) >= original_output_width for key in group)
        )
        groups: list[tuple[str, ...]] = [(key,) for key in target_keys]
        sample_offset = 0
        for group in internal:
            groups.append(new_samples[sample_offset : sample_offset + len(group)])
            sample_offset += len(group)
        if not internal:
            groups.extend((key,) for key in new_samples)
        original_samples = binding.keys[original_output_width:]
        coverage_rename = dict(zip(original_samples, new_samples, strict=True))
        bindings.append(
            replace(
                binding,
                keys=keys,
                key_groups=tuple(groups),
                coverage=tuple(
                    replace(coverage, key=coverage_rename.get(coverage.key, coverage.key))
                    for coverage in binding.coverage
                ),
            )
        )
    domain = (
        pd.concat(domains, ignore_index=True).drop_duplicates()
        if domains
        else pd.DataFrame(columns=target_keys)
    )
    identity_mapping = domain.copy()
    for index, key in enumerate(target_keys):
        identity_mapping[f"__target.{index}"] = domain[key]
    return rollup_components(
        replace(program, values=tuple(bindings)),
        tuple(classified),
        target_keys,
        target_keys,
        identity_mapping,
        outputs,
        population_frames=population_frames,
        source_columns=target_columns,
        target_columns=target_columns,
    )
