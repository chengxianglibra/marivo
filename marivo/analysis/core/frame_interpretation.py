"""Closed inert facts describing the current Frame result, without execution authority."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa

from marivo._definition.query import (
    FilterValues,
    PairStatistic,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValuePartition,
    ValueShare,
    ValueSlot,
)
from marivo.analysis.core.arrow_keys import FrameKeyScalar
from marivo.analysis.core.frame_fix import validate_fixed_key
from marivo.analysis.core.frame_operations import FixFrameValues
from marivo.analysis.core.frozen_calendar import frozen_key_table
from marivo.analysis.model.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAlignment,
    DataAxis,
    DataComparison,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.model.frame_facts import (
    _invalid,
)
from marivo.analysis.model.frame_schema import capture_axis, capture_comparisons, comparison_id
from marivo.analysis.model.layout_facts import (
    FrameAlignmentAxis,
    FrameAlignmentCoordinate,
    FrameAlignmentSide,
    FrameAxisGroup,
    FrameChangeTarget,
    FrameDecomposition,
    FrameDecompositionField,
    FrameDerivedValueInput,
    FrameFixedGroup,
    FrameGroup,
    FrameLevelTarget,
    FrameNamedValueInput,
    FramePairStatisticContext,
    FrameShareContext,
    FrameStatisticContext,
    FrameValueContext,
    FrameValueInput,
)
from marivo.analysis.model.lineage import expression_lineage
from marivo.analysis.model.time_facts import (
    FrameAxisTime,
    FrameComparedTime,
    FrameDerivedTimeSemantics,
    FrameFixedAxis,
    FrameObservationContext,
    FrameOrdinaryAxisSchema,
    merge_source_times,
)
from marivo.analysis.model.value_time import unary_time_semantics


def restrict_context(context: FrameValueContext) -> FrameValueContext:
    """Restrict represented statistics without recomputing any numeric value."""

    return (
        replace(context, row_scope="restricted")
        if isinstance(
            context, (FrameStatisticContext, FrameShareContext, FramePairStatisticContext)
        )
        else context
    )


def remap_context(
    context: FrameValueContext | None,
    tokens: dict[str, tuple[str, Literal["self", "left", "right"]]],
    comparisons: dict[str, str] | None = None,
) -> FrameValueContext | None:
    """Keep only explanations whose current references have an exact local binding."""

    if isinstance(context, (FrameStatisticContext, FrameShareContext, FramePairStatisticContext)):

        def remapped(items: tuple[FrameGroup, ...]) -> tuple[FrameGroup, ...] | None:
            if any(isinstance(g, FrameAxisGroup) and g.axis_token not in tokens for g in items):
                return None
            return tuple(
                FrameAxisGroup(tokens[g.axis_token][0]) if isinstance(g, FrameAxisGroup) else g
                for g in items
            )

        groups = remapped(context.groups)
        if groups is None:
            return None
        if isinstance(context, FrameShareContext):
            along = remapped(context.along)
            return None if along is None else replace(context, groups=groups, along=along)
        return context if groups == context.groups else replace(context, groups=groups)
    if isinstance(context, FrameObservationContext):
        time = context.time
        if isinstance(time, FrameAxisTime):
            target = tokens.get(time.axis_token)
            if target is None:
                return replace(context, time=None, window=None)
            time = FrameAxisTime(target[0], time.endpoint if target[1] == "self" else target[1])
        elif isinstance(time, FrameComparedTime):
            target_id = (comparisons or {}).get(time.comparison_id)
            if target_id is None:
                return replace(context, time=None, window=None)
            time = FrameComparedTime(target_id, time.endpoint)
        return context if time == context.time else replace(context, time=time)
    return None


def capture_value_inputs(
    schema: LogicalSchema,
    lineage: FrameLineage,
    output: LogicalSchema,
    operation: FrameUnaryOperationKind,
) -> LogicalSchema:
    """Capture new explanation inputs once, while the original logical input is available."""

    if not isinstance(operation, (SelectValues, ReduceValues)):
        return output

    def captured(value: ValueExpression) -> FrameValueInput:
        if isinstance(value, ValueSlot):
            name = schema.values[value.index].name
            context = dict(schema.value_contexts).get(name)
            subject = context.subject if isinstance(context, FrameObservationContext) else None
            return FrameNamedValueInput(name, subject)
        return FrameDerivedValueInput(expression_lineage(lineage, value).value_origins)

    contexts = dict(output.value_contexts)
    for name, value in operation.values:
        if isinstance(value, ValueShare):
            contexts[name] = FrameShareContext(
                captured(value.value),
                tuple(
                    FrameAxisGroup(a.token)
                    for p, a in enumerate(schema.axes)
                    if p not in value.positions
                ),
                tuple(FrameAxisGroup(schema.axes[p].token) for p in value.positions),
            )
        else:
            statistic = value.statistic if isinstance(value, ValuePartition) else value
            if isinstance(statistic, PairStatistic):
                positions = (
                    value.positions
                    if isinstance(value, ValuePartition)
                    else operation.positions
                    if isinstance(operation, ReduceValues)
                    else ()
                )
                contexts[name] = FramePairStatisticContext(
                    statistic.method,
                    captured(statistic.left),
                    captured(statistic.right),
                    tuple(FrameAxisGroup(schema.axes[p].token) for p in positions),
                )
    return replace(output, value_contexts=tuple(contexts.items()))


def transport_alignments(
    schema: LogicalSchema,
    output: LogicalSchema,
    *,
    namespace: str,
    value_prefix: str,
    presence_prefix: str,
    enclosing: str,
    tokens: dict[str, tuple[str, Literal["self", "left", "right"]]],
    fixed: tuple[FrameFixedAxis, ...],
) -> tuple[DataAlignment, ...]:
    """Transport representable local relations through an actual binary endpoint mapping."""

    axes = {a.token: a for a in output.axes}
    values = {v.name for v in output.values}
    presence = {v.name for v in output.presence}

    def side(value: FrameAlignmentSide) -> FrameAlignmentSide | None:
        coordinates: list[FrameAlignmentCoordinate] = []
        for coordinate in value.coordinates:
            if isinstance(coordinate, FrameFixedGroup):
                if coordinate.fixed not in fixed:
                    return None
                coordinates.append(coordinate)
                continue
            target = tokens.get(coordinate.axis_token)
            if target is None:
                return None
            token, endpoint = target
            if coordinate.endpoint != "self":
                if endpoint != "self" or not isinstance(axes.get(token), PairedDataAxis):
                    return None
                endpoint = coordinate.endpoint
            coordinates.append(FrameAlignmentAxis(token, endpoint))
        names = tuple(value_prefix + n for n in value.values)
        flags = (enclosing, *(presence_prefix + n for n in value.presence))
        if any(n not in values for n in names) or any(n not in presence for n in flags):
            return None
        return FrameAlignmentSide(names, flags, tuple(coordinates))

    result: list[DataAlignment] = []
    for relationship in schema.alignments:
        left, right = side(relationship.left), side(relationship.right)
        if left is not None and right is not None:
            result.append(DataAlignment(namespace + "." + relationship.alignment_id, left, right))
    return tuple(result)


def _fix_axis_facts(
    schema: LogicalSchema, output: LogicalSchema, operation: FixFrameValues
) -> tuple[
    dict[str, FrameValueContext],
    tuple[FrameFixedAxis, ...],
    tuple[DataAlignment, ...],
    tuple[DataComparison, ...],
    tuple[FrameDecomposition, ...],
]:
    contexts = dict(schema.value_contexts)
    fixed = schema.fixed_axes
    alignments = schema.alignments
    comparisons = output.comparisons
    decompositions = schema.decompositions
    axis = schema.axes[operation.position]
    description = capture_axis(axis)
    assert isinstance(description, FrameOrdinaryAxisSchema)
    exact = validate_fixed_key(axis, operation.key)
    if exact is not None and isinstance(axis, DataAxis):
        table = frozen_key_table(axis.columns, (exact,))
        scalars = tuple(pa.array(table[c.slot_id])[0] for c in axis.columns)
    else:
        scalars = tuple(
            pa.array(pd.Series([v]))[0] if isinstance(v, (date, timedelta)) else pa.scalar(v)
            for v in operation.key
        )
    key: tuple[FrameKeyScalar, ...] = tuple(
        v if isinstance(v, (pa.Date32Scalar, pa.TimestampScalar, pa.DurationScalar)) else v.as_py()
        for v in scalars
    )
    selected = FrameFixedAxis(description, key, operation.origins)
    fixed = (*fixed, selected)

    def fixed_side(side: FrameAlignmentSide) -> FrameAlignmentSide:
        return replace(
            side,
            coordinates=tuple(
                FrameFixedGroup(selected)
                if isinstance(c, FrameAlignmentAxis) and c.axis_token == axis.token
                else c
                for c in side.coordinates
            ),
        )

    alignments = tuple(
        replace(a, left=fixed_side(a.left), right=fixed_side(a.right)) for a in alignments
    )

    def groups(items: tuple[FrameGroup, ...]) -> tuple[FrameGroup, ...]:
        return tuple(
            FrameFixedGroup(selected)
            if isinstance(g, FrameAxisGroup) and g.axis_token == axis.token
            else g
            for g in items
        )

    def fixed_context(context: FrameValueContext | None) -> FrameValueContext | None:
        if isinstance(context, FrameShareContext):
            return replace(
                context,
                groups=groups(context.groups),
                along=groups(context.along),
                row_scope="restricted"
                if any(
                    isinstance(g, FrameAxisGroup) and g.axis_token == axis.token
                    for g in context.along
                )
                else context.row_scope,
            )
        if isinstance(context, FramePairStatisticContext):
            return replace(
                context,
                groups=groups(context.groups),
                row_scope=context.row_scope
                if any(
                    isinstance(g, FrameAxisGroup) and g.axis_token == axis.token
                    for g in context.groups
                )
                else "restricted",
            )
        return (
            replace(context, groups=groups(context.groups))
            if isinstance(context, FrameStatisticContext)
            else context
        )

    contexts = {n: c for n, value in contexts.items() if (c := fixed_context(value)) is not None}
    comparisons = tuple(
        replace(
            f, contexts=tuple((p, fixed_context(a), fixed_context(b)) for p, a, b in f.contexts)
        )
        for f in comparisons
    )
    decompositions = tuple(
        replace(
            d,
            groups=groups(d.groups),
            row_scope=d.row_scope
            if any(isinstance(g, FrameAxisGroup) and g.axis_token == axis.token for g in d.groups)
            else "restricted",
        )
        for d in decompositions
    )
    return contexts, fixed, alignments, comparisons, decompositions


def unary_facts(
    schema: LogicalSchema, output: LogicalSchema, operation: FrameUnaryOperationKind
) -> LogicalSchema:
    """Propagate current facts with the exact expression/axis correspondence."""

    contexts = dict(schema.value_contexts)
    fixed = schema.fixed_axes
    decompositions = schema.decompositions
    alignments = schema.alignments
    comparisons = output.comparisons
    if isinstance(operation, FixFrameValues):
        contexts, fixed, alignments, comparisons, decompositions = _fix_axis_facts(
            schema, output, operation
        )
    elif isinstance(operation, (FilterValues, TopValues)):
        contexts = {n: restrict_context(c) for n, c in contexts.items()}
        comparisons = tuple(
            replace(
                f,
                contexts=tuple(
                    (
                        p,
                        restrict_context(a) if a is not None else None,
                        restrict_context(b) if b is not None else None,
                    )
                    for p, a, b in f.contexts
                ),
            )
            for f in comparisons
        )
        decompositions = tuple(replace(d, row_scope="restricted") for d in decompositions)
    else:
        selected_contexts: dict[str, FrameValueContext] = {}
        direct: dict[str, tuple[str, ...]] = {}
        for name, expression in operation.values:
            if isinstance(expression, ValueSlot):
                original = schema.values[expression.index].name
                direct[original] = (*direct.get(original, ()), name)
                if original in contexts:
                    selected_contexts[name] = contexts[original]
            elif isinstance(expression, ValuePartition) and isinstance(
                expression.statistic, Statistic
            ):
                selected_contexts[name] = FrameStatisticContext(
                    expression.statistic.method,
                    tuple(FrameAxisGroup(schema.axes[p].token) for p in expression.positions),
                )
            elif isinstance(operation, ReduceValues) and isinstance(expression, Statistic):
                selected_contexts[name] = FrameStatisticContext(
                    expression.method,
                    tuple(FrameAxisGroup(schema.axes[p].token) for p in operation.positions),
                )
        contexts = selected_contexts
        if isinstance(operation, SelectValues):

            def selected_side(side: FrameAlignmentSide) -> FrameAlignmentSide:
                retained_names = {n for old in side.values for n in direct.get(old, ())}
                return replace(
                    side, values=tuple(c.name for c in output.values if c.name in retained_names)
                )

            alignments = tuple(
                DataAlignment(a.alignment_id, left, right)
                for a in alignments
                for left, right in ((selected_side(a.left), selected_side(a.right)),)
                if left.values or right.values
            )
            retained: list[FrameDecomposition] = []
            for decomposition in decompositions:
                fields = tuple(
                    replace(f, name=n)
                    for f in decomposition.outputs
                    for n in (direct.get(f.name, ()) if f.field_kind == "value" else (f.name,))
                )
                if any(f.field_kind == "value" for f in fields):
                    retained.append(replace(decomposition, outputs=fields))
            decompositions = tuple(retained)
        else:
            decompositions = ()
            alignments = ()
    tokens: dict[str, tuple[str, Literal["self", "left", "right"]]] = {
        a.token: (a.token, "self") for a in output.axes
    }
    ids = {comparison_id(f): comparison_id(f) for f in output.comparisons}
    resolved = tuple(
        (n, c)
        for n, value in contexts.items()
        if (c := remap_context(value, tokens, ids)) is not None
    )
    required = {
        d.target.comparison_id for d in decompositions if isinstance(d.target, FrameChangeTarget)
    }
    required.update(
        d.target.context.time.comparison_id
        for d in decompositions
        if isinstance(d.target, FrameLevelTarget)
        and isinstance(d.target.context, FrameObservationContext)
        and isinstance(d.target.context.time, FrameComparedTime)
    )
    retained_comparisons = []
    for fact in comparisons:
        identity = comparison_id(fact)
        if not fact.values and identity not in required:
            continue
        paths = {value.source_path for value in fact.values}
        paths.update(
            tuple(d.target.group_id.split("."))
            for d in decompositions
            if isinstance(d.target, FrameChangeTarget) and d.target.comparison_id == identity
        )
        if identity in required and not paths:
            paths.update(path for path, _, _ in fact.contexts)
        retained_comparisons.append(
            replace(fact, contexts=tuple(c for c in fact.contexts if c[0] in paths))
        )
    comparisons = tuple(retained_comparisons)
    return replace(
        output,
        fixed_axes=fixed,
        source_times=merge_source_times(schema.source_times, output.source_times),
        value_contexts=resolved,
        value_time_semantics=unary_time_semantics(schema, operation),
        decompositions=decompositions,
        comparisons=comparisons,
        alignments=alignments,
    )


if TYPE_CHECKING:
    from marivo._definition.query import ValueExpression
    from marivo.analysis.core.frame_operations import (
        AlignFrameValues,
        CompareFrameValues,
        FrameAttributeTarget,
        FrameUnaryOperationKind,
    )
    from marivo.analysis.model.frame_schema import LogicalSchema
    from marivo.analysis.model.lineage import FrameLineage


def comparison_facts(
    inputs: tuple[LogicalSchema, ...], output: LogicalSchema, operation: CompareFrameValues
) -> LogicalSchema:
    """Bind actual comparison endpoints before old relations can obscure their meaning."""

    current = inputs[0]
    contexts: dict[str, FrameValueContext] = {}
    time_facts = dict(current.value_time_semantics)
    facts = list(output.comparisons)
    alignments: list[DataAlignment] = []
    common = tuple(f for f in current.fixed_axes if all(f in b.fixed_axes for b in inputs[1:]))

    def shared_context(context: FrameValueContext | None) -> FrameValueContext | None:
        if isinstance(
            context, (FrameStatisticContext, FrameShareContext, FramePairStatisticContext)
        ) and any(
            isinstance(group, FrameFixedGroup) and group.fixed not in common
            for group in (
                *context.groups,
                *(context.along if isinstance(context, FrameShareContext) else ()),
            )
        ):
            return None
        return context

    for i, (item, baseline) in enumerate(zip(operation.baselines, inputs[1:], strict=True)):
        fact = facts[i]
        time_binding = isinstance(
            fact.binding, (CalendarComparisonBinding, IntervalComparisonBinding)
        )
        current_tokens: dict[str, tuple[str, Literal["self", "left", "right"]]] = {
            a.token: (a.token, "self") for a in current.axes
        }
        baseline_tokens: dict[str, tuple[str, Literal["self", "left", "right"]]] = {
            baseline.axes[j].token: (output.axes[k].token, "self") for k, j in item.pairs
        }
        if i == 0:
            alignments.extend(
                transport_alignments(
                    current,
                    output,
                    namespace="current",
                    value_prefix="",
                    presence_prefix="current.presence.",
                    enclosing="current.present",
                    tokens=current_tokens,
                    fixed=common,
                )
            )
        alignment_baseline_tokens = dict(baseline_tokens)
        for current_position, baseline_position in item.pairs:
            baseline_axis = baseline.axes[baseline_position]
            if not isinstance(baseline_axis, (CalendarDataAxis, IntervalDataAxis)):
                continue
            identity_mapping = item.match is None or (
                baseline_axis == current.axes[current_position]
                and (item.match.kind == "ordinal" or item.match.by == 0)
            )
            if not identity_mapping:
                alignment_baseline_tokens.pop(baseline_axis.token, None)
        alignments.extend(
            transport_alignments(
                baseline,
                output,
                namespace=item.alias,
                value_prefix=item.alias + ".baseline.",
                presence_prefix=item.alias + ".presence.",
                enclosing=item.alias + ".present",
                tokens=alignment_baseline_tokens,
                fixed=common,
            )
        )
        captured: list[
            tuple[tuple[str, ...], FrameValueContext | None, FrameValueContext | None]
        ] = []
        for path in current.value_paths:
            name = ".".join(path)
            current_time = dict(current.value_time_semantics).get(name)
            baseline_time = dict(baseline.value_time_semantics).get(name)
            if baseline_time is not None:
                time_facts[".".join((item.alias, "baseline", *path))] = baseline_time
            branches = tuple(
                dict.fromkeys(
                    branch
                    for description in (current_time, baseline_time)
                    if description is not None
                    for branch in description.contributions
                )
            )
            if branches:
                for role in ("absolute", "relative"):
                    time_facts[".".join((item.alias, role, *path))] = FrameDerivedTimeSemantics(
                        branches
                    )
            current_ids = {
                comparison_id(f): "current." + comparison_id(f) for f in current.comparisons
            }
            baseline_ids = {
                comparison_id(f): item.alias + "." + comparison_id(f) for f in baseline.comparisons
            }
            a = remap_context(dict(current.value_contexts).get(name), current_tokens, current_ids)
            b = remap_context(
                dict(baseline.value_contexts).get(name), baseline_tokens, baseline_ids
            )
            if time_binding:
                if isinstance(a, FrameObservationContext) and isinstance(a.time, FrameAxisTime):
                    a = replace(a, time=FrameComparedTime(comparison_id(fact), "current"))
                if isinstance(b, FrameObservationContext) and isinstance(b.time, FrameAxisTime):
                    b = replace(b, time=FrameComparedTime(comparison_id(fact), "baseline"))
            captured.append((path, a, b))
            shared_a = shared_context(a)
            shared_b = shared_context(b)
            if shared_a is not None:
                contexts[name] = shared_a
            if shared_b is not None:
                contexts[".".join((item.alias, "baseline", *path))] = shared_b
        facts[i] = replace(
            fact,
            contexts=tuple(captured),
            current_fixed_axes=current.fixed_axes,
            baseline_fixed_axes=baseline.fixed_axes,
            named=not operation.unnamed,
        )
    return replace(
        output,
        comparisons=tuple(facts),
        source_times=merge_source_times(
            *(schema.source_times for schema in inputs), output.source_times
        ),
        value_time_semantics=tuple(
            (column.name, time_facts[column.name])
            for column in output.values
            if column.name in time_facts
        ),
        fixed_axes=common,
        value_contexts=tuple(contexts.items()),
        decompositions=(),
        alignments=tuple(alignments),
    )


def align_facts(
    left: LogicalSchema, right: LogicalSchema, output: LogicalSchema, operation: AlignFrameValues
) -> LogicalSchema:
    """Transport only local current contexts and common fixed choices through align."""

    result: list[tuple[str, FrameValueContext]] = []
    alignments: list[DataAlignment] = []
    common = tuple(f for f in left.fixed_axes if f in right.fixed_axes)
    visible_comparisons = {
        fact.comparison_id for fact in capture_comparisons("align-output", output)
    }
    for side, schema, positions in (
        ("left", left, tuple((i, i) for i in range(len(left.axes)))),
        ("right", right, tuple((j, i) for i, j in operation.pairs)),
    ):
        tokens: dict[str, tuple[str, Literal["self", "left", "right"]]] = {}
        for before, after in positions:
            target = output.axes[after]
            endpoint: Literal["self", "left", "right"] = "left" if side == "left" else "right"
            tokens[schema.axes[before].token] = (
                target.token,
                endpoint if isinstance(target, PairedDataAxis) else "self",
            )
        alignments.extend(
            transport_alignments(
                schema,
                output,
                namespace=side,
                value_prefix=side + ".",
                presence_prefix=side + ".",
                enclosing=side + "_present",
                tokens=tokens,
                fixed=common,
            )
        )
        ids = {
            comparison_id(f): identity
            for f in schema.comparisons
            if (identity := ".".join((side, comparison_id(f)))) in visible_comparisons
        }
        for name, context in schema.value_contexts:
            rebound = remap_context(context, tokens, ids)
            if isinstance(
                rebound, (FrameStatisticContext, FrameShareContext, FramePairStatisticContext)
            ) and any(
                isinstance(group, FrameFixedGroup) and group.fixed not in common
                for group in (
                    *rebound.groups,
                    *(rebound.along if isinstance(rebound, FrameShareContext) else ()),
                )
            ):
                rebound = None
            if rebound is not None:
                result.append((side + "." + name, rebound))
    if output.values:

        def aligned_side(
            side: Literal["left", "right"], schema: LogicalSchema
        ) -> FrameAlignmentSide:
            return FrameAlignmentSide(
                tuple(side + "." + c.name for c in schema.values),
                (side + "_present",),
                tuple(
                    FrameAlignmentAxis(a.token, side if isinstance(a, PairedDataAxis) else "self")
                    for a in output.axes
                ),
            )

        alignments.append(
            DataAlignment("alignment", aligned_side("left", left), aligned_side("right", right))
        )
    return replace(
        output,
        fixed_axes=common,
        source_times=merge_source_times(left.source_times, right.source_times, output.source_times),
        value_contexts=tuple(result),
        value_time_semantics=tuple(
            (side + "." + name, fact)
            for side, schema in (("left", left), ("right", right))
            for name, fact in schema.value_time_semantics
        ),
        decompositions=(),
        alignments=tuple(alignments),
    )


def decomposition_facts(
    output: LogicalSchema,
    parent: LogicalSchema,
    target: FrameAttributeTarget,
    fields: tuple[FrameDecompositionField, ...],
) -> LogicalSchema:
    """Attach roles from actual output construction, never from a historical method record."""

    observed = parent.value_paths[target.observed]
    name = ".".join(target.path)
    current_target: FrameLevelTarget | FrameChangeTarget

    comparisons: tuple[DataComparison, ...] = ()
    if target.mode == "level":
        level_context = dict(parent.value_contexts).get(".".join(observed))
        current_target = FrameLevelTarget(name, level_context)
        if isinstance(level_context, FrameObservationContext) and isinstance(
            level_context.time, FrameComparedTime
        ):
            source = next(
                f
                for f in parent.comparisons
                if comparison_id(f) == level_context.time.comparison_id
            )
            paths = {v.source_path for v in source.values if v.value_path == observed}
            comparisons = (
                replace(
                    source,
                    values=(),
                    contexts=tuple(c for c in source.contexts if c[0] in paths),
                    current_presence=None,
                    baseline_presence=None,
                    enclosing_presence=(),
                ),
            )
    else:
        candidates = tuple(
            (f, v)
            for f in (
                *parent.comparisons,
                *((target.comparison,) if target.comparison is not None else ()),
            )
            for v in f.values
            if v.value_path == observed and v.role == target.mode
        )
        if len(candidates) != 1:
            # The target construction must have an exact quantity relationship.
            _invalid("one current comparison quantity for the decomposition target")
        fact, value = candidates[0]
        current_target = FrameChangeTarget(
            target.mode, comparison_id(fact), ".".join(value.source_path)
        )
        comparisons = (
            replace(
                fact,
                values=(),
                contexts=tuple(c for c in fact.contexts if c[0] == value.source_path),
                current_presence=None,
                baseline_presence=None,
                enclosing_presence=(),
            ),
        )
    groups: tuple[FrameGroup, ...] = (
        *tuple(FrameAxisGroup(a.token) for a in parent.axes),
        *tuple(FrameFixedGroup(f) for f in parent.fixed_axes),
    )
    decomposition = FrameDecomposition(
        "decomposition." + name, current_target, groups, "complete", fields
    )
    return replace(
        output,
        fixed_axes=parent.fixed_axes,
        source_times=merge_source_times(parent.source_times, output.source_times),
        value_contexts=(),
        comparisons=comparisons,
        decompositions=(decomposition,),
    )
