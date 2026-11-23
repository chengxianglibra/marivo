"""Resolve captured explanation records against one current data schema."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import replace

from marivo.analysis.model.data_schema import (
    CalendarDataAxis,
    DataAlignment,
    DataAxis,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.model.frame_facts import _invalid
from marivo.analysis.model.frame_schema import LogicalSchema, comparison_id
from marivo.analysis.model.frame_structure import PendingDataAxis
from marivo.analysis.model.frame_structure import column_kind as column_kind_for_presence
from marivo.analysis.model.layout_facts import (
    FrameAlignment,
    FrameAlignmentAxis,
    FrameAxisGroup,
    FrameFixedGroup,
    FrameGroup,
    FrameLevelTarget,
    FramePairStatisticContext,
    FrameShareContext,
    FrameStatisticContext,
    FrameValueContext,
)
from marivo.analysis.model.time_facts import (
    FrameAxisTime,
    FrameComparedTime,
    FrameFixedAxis,
    FrameObservationContext,
)


def validate_current_facts(schema: LogicalSchema) -> None:
    """Resolve every current explanation reference using only captured directories."""
    values = {c.name for c in schema.values}
    presence = {c.name for c in schema.presence}
    axes = {a.token: a for a in schema.axes}
    comparisons = {comparison_id(f): f for f in schema.comparisons}
    if len(comparisons) != len(schema.comparisons):
        _invalid("unique current comparison identities")

    _validate_alignments(schema, axes, values)

    time_names = tuple(name for name, _ in schema.value_time_semantics)
    if len(set(time_names)) != len(time_names) or not set(time_names) <= {
        column.name for column in schema.values
    }:
        _invalid("unique contribution-time descriptions for current value names")
    names = tuple(n for n, _ in schema.value_contexts)
    if len(set(names)) != len(names) or any(n not in values for n in names):
        _invalid("unique context bindings to current value fields")
    for _, item in schema.value_contexts:
        _validate_value_context(
            item, schema.fixed_axes, axes, comparisons, complete_active_axes=True
        )
    for fact in schema.comparisons:
        for _, current, baseline in fact.contexts:
            _validate_value_context(
                current, (*schema.fixed_axes, *fact.current_fixed_axes), axes, comparisons
            )
            _validate_value_context(
                baseline, (*schema.fixed_axes, *fact.baseline_fixed_axes), axes, comparisons
            )
    if len({d.decomposition_id for d in schema.decompositions}) != len(schema.decompositions):
        _invalid("unique current decomposition identities")
    for decomposition in schema.decompositions:
        _validate_group_refs(decomposition.groups, schema.fixed_axes, axes)
        for output in decomposition.outputs:
            if output.name not in (values if output.field_kind == "value" else presence):
                _invalid("decomposition roles resolving to exact current fields")
        target = decomposition.target
        if isinstance(target, FrameLevelTarget):
            _validate_value_context(target.context, schema.fixed_axes, axes, comparisons)
        else:
            target_fact = comparisons.get(target.comparison_id)
            if target_fact is None or target.group_id not in {
                ".".join(p) for p, _, _ in target_fact.contexts
            } | {".".join(v.source_path) for v in target_fact.values}:
                _invalid("a decomposition target resolving to a current comparison quantity")


def _validate_value_context(
    item: FrameValueContext | None,
    selections: tuple[FrameFixedAxis, ...],
    axes: Mapping[str, DataAxis | PendingDataAxis],
    comparisons: Collection[str],
    *,
    complete_active_axes: bool = False,
) -> None:
    if isinstance(item, FrameObservationContext):
        time = item.time
        if isinstance(time, FrameAxisTime):
            axis = axes.get(time.axis_token)
            if not isinstance(axis, (CalendarDataAxis, IntervalDataAxis, PairedDataAxis)) or (
                (time.endpoint == "self") == isinstance(axis, PairedDataAxis)
            ):
                _invalid("an observation time resolving to a current complete time endpoint")
        elif isinstance(time, FrameComparedTime) and time.comparison_id not in comparisons:
            _invalid("an observation time resolving to a current comparison")
    elif isinstance(item, (FrameStatisticContext, FrameShareContext, FramePairStatisticContext)):
        _validate_group_refs(item.groups, selections, axes)
        if isinstance(item, FrameShareContext):
            _validate_group_refs(item.along, selections, axes)
            active = tuple(
                g.axis_token for g in (*item.groups, *item.along) if isinstance(g, FrameAxisGroup)
            )
            if complete_active_axes and (
                len(set(active)) != len(active) or set(active) != set(axes)
            ):
                _invalid(
                    "normalization groups and along covering every current complete active axis exactly once"
                )
    elif item is not None:
        _invalid("one closed current value context")


def _validate_group_refs(
    items: tuple[FrameGroup, ...],
    selections: tuple[FrameFixedAxis, ...],
    axes: Mapping[str, DataAxis | PendingDataAxis],
) -> None:
    for group in items:
        if isinstance(group, FrameAxisGroup) and group.axis_token not in axes:
            _invalid("a current complete parent axis")
        if isinstance(group, FrameFixedGroup) and group.fixed not in selections:
            _invalid("a retained exact fixed parent coordinate")


def _validate_alignments(
    schema: LogicalSchema, axes: Mapping[str, DataAxis | PendingDataAxis], values: set[str]
) -> None:
    if type(schema.alignments) is not tuple or any(
        type(a) is not DataAlignment for a in schema.alignments
    ):
        _invalid("an immutable directory of closed current alignments")
    if len({a.alignment_id for a in schema.alignments}) != len(schema.alignments):
        _invalid("unique current local alignment identities")
    for alignment in schema.alignments:
        FrameAlignment("validation", alignment.alignment_id, alignment.left, alignment.right)
        for side in (alignment.left, alignment.right):
            active = tuple(
                c.axis_token for c in side.coordinates if isinstance(c, FrameAlignmentAxis)
            )
            if len(set(active)) != len(active) or set(active) != set(axes):
                _invalid(
                    "alignment coordinates covering every current complete active axis exactly once"
                )
            if any(n not in values for n in side.values):
                _invalid("alignment values resolving to exact current fields")
            for name in side.presence:
                columns = tuple(c for c in schema.presence if c.name == name)
                if (
                    not columns
                    or columns[0].nullable is not False
                    or column_kind_for_presence(columns[0]) != "boolean"
                ):
                    _invalid("alignment presence resolving to non-nullable boolean fields")
            for coordinate in side.coordinates:
                if isinstance(coordinate, FrameFixedGroup):
                    if coordinate.fixed not in schema.fixed_axes:
                        _invalid("a retained exact fixed alignment coordinate")
                else:
                    axis = axes.get(coordinate.axis_token)
                    if axis is None or (
                        (coordinate.endpoint == "self") == isinstance(axis, PairedDataAxis)
                    ):
                        _invalid(
                            "an alignment coordinate resolving to the actual complete endpoint"
                        )


def share_contexts(schema: LogicalSchema) -> LogicalSchema:
    """Intern only equal current immutable contexts; no ancestors or rows are retained."""
    pool: dict[FrameValueContext, FrameValueContext] = {}

    def shared(context: FrameValueContext | None) -> FrameValueContext | None:
        if context is None:
            return None
        return pool.setdefault(context, context)

    contexts = []
    for name, context in schema.value_contexts:
        item = shared(context)
        assert item is not None
        contexts.append((name, item))
    comparisons = tuple(
        replace(f, contexts=tuple((p, shared(a), shared(b)) for p, a, b in f.contexts))
        for f in schema.comparisons
    )
    decompositions = tuple(
        replace(d, target=replace(d.target, context=shared(d.target.context)))
        if isinstance(d.target, FrameLevelTarget)
        else d
        for d in schema.decompositions
    )
    return replace(
        schema,
        value_contexts=tuple(contexts),
        comparisons=comparisons,
        decompositions=decompositions,
    )
