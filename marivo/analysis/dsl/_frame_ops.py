"""Bind Frame operator inputs without owning or executing a Frame."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Literal

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._definition.query import (
    PairStatistic,
    ReduceValues,
    SelectValues,
    Statistic,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueSlot,
)
from marivo._definition.types import DefinitionGraphError, LiteralValue, check_literal
from marivo._kernel.validation import is_instance
from marivo.analysis.core.frame_value_analysis import infer_frame_schema
from marivo.analysis.dsl.column import (
    Column,
    _ColumnFrame,
    _Statistic,
    column_expression,
    column_frame,
    column_input_id,
    column_pair_statistic,
    frame_axis_index,
    require_frame_column,
    statistic_definition,
    statistic_input,
)
from marivo.analysis.dsl.frame_axis import FrameAxis, axis_frame, axis_input_id
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import frame_input_id, frame_local, frame_logical_schema
from marivo.analysis.model.frame_structure import column_kind
from marivo.analysis.model.value_names import value_name


def validate_pairing(on: object, match: object, *, location: str, canonical_id: str) -> None:
    if (match is not None and not isinstance(match, CalendarCorrespondence)) or (
        on is not None
        and (
            type(on) is not tuple
            or any(
                type(pair) is not tuple
                or len(pair) != 2
                or any(not isinstance(axis, FrameAxis) for axis in pair)
                for pair in on
            )
        )
    ):
        reject(
            "a tuple of complete FrameAxis pairs and an optional CalendarCorrespondence",
            repr((on, match)),
            "Pass on as a tuple pairing every complete axis exactly once; pass match as a CalendarCorrespondence or omit it.",
            location,
            canonical_id=canonical_id,
        )


def _default_axis_pairs(
    left_axes: tuple[FrameAxis, ...],
    right_axes: tuple[FrameAxis, ...],
    *,
    location: str,
    canonical_id: str,
) -> tuple[tuple[FrameAxis, FrameAxis], ...]:
    time_roles = ("calendar", "interval")
    left_time = tuple(axis for axis in left_axes if axis.role in time_roles)
    right_time = tuple(axis for axis in right_axes if axis.role in time_roles)
    if location == "frame.compare" and (len(left_time) > 1 or len(right_time) > 1):
        reject(
            "at most one compatible time axis pair",
            f"current={len(left_time)}, baseline={len(right_time)}",
            "Reduce or reconstruct each input with at most one time axis before compare.",
            location,
            canonical_id=canonical_id,
        )
    left_ordinary = tuple(axis for axis in left_axes if axis.role not in time_roles)
    right_ordinary = tuple(axis for axis in right_axes if axis.role not in time_roles)
    pairs: list[tuple[FrameAxis, FrameAxis]] = []
    valid = (
        len(left_time) == len(right_time)
        and len(left_time) <= 1
        and (not left_time or left_time[0].role == right_time[0].role)
    )
    for axis in left_axes:
        if axis.role in time_roles:
            if valid:
                pairs.append((axis, right_time[0]))
            continue
        left_matches = tuple(
            candidate for candidate in left_ordinary if candidate.name == axis.name
        )
        right_matches = tuple(
            candidate for candidate in right_ordinary if candidate.name == axis.name
        )
        if (
            not axis.name
            or len(left_matches) != 1
            or len(right_matches) != 1
            or axis.role != right_matches[0].role
            or axis.key_names != right_matches[0].key_names
        ):
            valid = False
        else:
            pairs.append((axis, right_matches[0]))
    if not valid or len(pairs) != len(left_axes):
        left_description = tuple((a.name, a.role, a.key_names) for a in left_axes)
        right_description = tuple((a.name, a.role, a.key_names) for a in right_axes)
        reject(
            "a complete explicit axis bijection",
            f"current={left_description!r}, other={right_description!r}",
            "Pass on with every complete axis exactly once. Automatic pairing requires unique same-name ordinary axes with matching roles and complete key names, and at most one same-kind time axis per side.",
            location,
            canonical_id=canonical_id,
        )
    return tuple(pairs)


def pair_axes(
    left_axes: tuple[FrameAxis, ...],
    right_axes: tuple[FrameAxis, ...],
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None,
    *,
    location: str,
    canonical_id: str,
) -> tuple[tuple[FrameAxis, FrameAxis], ...]:
    if any(axis.role == "paired" for axis in (*left_axes, *right_axes)):
        reject(
            "inputs without paired axes",
            repr((left_axes, right_axes)),
            "Reduce away paired axes or construct new source inputs.",
            location,
            canonical_id=canonical_id,
        )
    if len(left_axes) != len(right_axes):
        reject(
            "equal complete axis counts",
            repr((len(left_axes), len(right_axes))),
            "Reduce or reconstruct inputs with compatible axes.",
            location,
            canonical_id=canonical_id,
        )
    if on is None:
        on = _default_axis_pairs(
            left_axes, right_axes, location=location, canonical_id=canonical_id
        )
    if (
        len(on) != len(left_axes)
        or {a.token for a, _ in on} != {a.token for a in left_axes}
        or {b.token for _, b in on} != {b.token for b in right_axes}
    ):
        reject(
            "a complete explicit axis bijection",
            repr(on),
            f"Bind each complete axis once from {left_axes!r} to {right_axes!r}.",
            location,
            canonical_id=canonical_id,
        )
    return on


def bind_statistic(
    frame: _ColumnFrame,
    statistic: _Statistic,
    *,
    location: str = "frame.reduce",
    canonical_id: str | None = None,
) -> _Statistic:
    if not isinstance(statistic, _Statistic):
        reject(
            "an unfinished Column statistic or row_count()",
            type(statistic).__name__,
            "Construct the statistic from this Frame.",
            location,
            canonical_id=canonical_id,
        )
    input_id, bound_frame = statistic_input(statistic)
    definition = statistic_definition(statistic)
    if (
        input_id is None
        and isinstance(definition, Statistic)
        and definition.method == "row_count"
        and definition.value is None
    ):
        return _Statistic(frame_input_id(frame), definition, statistic.unit, frame)
    if (
        input_id != frame_input_id(frame)
        or bound_frame is None
        or frame_local(bound_frame).owner != frame_local(frame).owner
    ):
        reject(
            "a statistic from this exact Frame input",
            repr(input_id),
            "Reacquire the Column on this Frame before constructing the statistic.",
            location,
            canonical_id=canonical_id,
        )
    return statistic


def bind_selection(
    frame: _ColumnFrame, values: Mapping[str, Column | LiteralValue]
) -> SelectValues:
    if not isinstance(values, Mapping):
        reject(
            "a named value mapping (possibly empty)",
            type(values).__name__,
            "Pass select({'name': column}) or select({}) to retain only coordinates and presence.",
            "frame.select",
            canonical_id="analysis_frame.select",
        )
    expressions: list[tuple[str, ValueExpression]] = []
    for name, value in values.items():
        value_name(name, location="frame.select", canonical_id="analysis_frame.select")
        if isinstance(value, Column):
            require_frame_column(
                frame, value, location="frame.select", canonical_id="analysis_frame.select"
            )
            expression = column_expression(value)
        else:
            try:
                literal = check_literal(value, role="Frame select value")
            except DefinitionGraphError as error:
                reject(
                    "an immutable finite literal or current Column",
                    str(error),
                    "Select a current Column or finite typed literal.",
                    "frame.select",
                    canonical_id="analysis_frame.select",
                )
            if literal is None:
                reject(
                    "a non-null literal",
                    "None",
                    "Select a nullable Column or derive NA with a typed operation.",
                    "frame.select",
                    canonical_id="analysis_frame.select",
                )
            if isinstance(literal, datetime):
                if literal.utcoffset() is None:
                    reject(
                        "an aware timestamp literal",
                        repr(literal),
                        "Use an aware datetime or a date value.",
                        "frame.select",
                        canonical_id="analysis_frame.select",
                    )
                literal = literal.astimezone(timezone.utc)
            expression = ValueLiteral(literal)
        expressions.append((name, expression))
    return SelectValues(tuple(expressions))


def bind_reduction(
    frame: _ColumnFrame,
    values: _Statistic | tuple[_Statistic, ...] | Mapping[str, _Statistic],
    by: tuple[FrameAxis, ...],
) -> ReduceValues:
    if isinstance(values, Mapping) and values:
        named = True
        items = tuple(values.items())
    elif isinstance(values, _Statistic):
        named = False
        items = (("", values),)
    elif type(values) is tuple and values:
        named = False
        items = tuple(("", statistic) for statistic in values)
    else:
        reject(
            "one statistic, a nonempty statistic tuple, or a nonempty named mapping",
            type(values).__name__,
            "Pass reduce(row_count()) or reduce({'your_name': statistic}).",
            "frame.reduce",
            canonical_id="analysis_frame.reduce",
        )
    if type(by) is not tuple:
        reject(
            "a tuple of complete current axes",
            type(by).__name__,
            "Use by=() for a scalar result or a tuple of this Frame's complete axes.",
            "frame.reduce",
            canonical_id="analysis_frame.reduce",
        )
    positions = tuple(
        frame_axis_index(frame, axis, location="frame.reduce", canonical_id="analysis_frame.reduce")
        for axis in by
    )
    if len(set(positions)) != len(positions):
        reject(
            "distinct complete current axes",
            repr(positions),
            "Use each complete axis once.",
            "frame.reduce",
            canonical_id="analysis_frame.reduce",
        )
    statistics: list[tuple[str, Statistic | PairStatistic]] = []
    candidates: dict[str, int] = {}
    for position, (name, value) in enumerate(items, 1):
        if named:
            value_name(name, location="frame.reduce", canonical_id="analysis_frame.reduce")
        bound = bind_statistic(
            frame, value, location="frame.reduce", canonical_id="analysis_frame.reduce"
        )
        definition = statistic_definition(bound)
        if not named:
            if isinstance(definition, Statistic) and definition.method == "row_count":
                name = "row_count"
            elif isinstance(definition, Statistic) and isinstance(definition.value, ValueSlot):
                path = frame_logical_schema(frame).value_paths[definition.value.index]
                if len(path) != 1:
                    reject(
                        "a direct single-segment value path for an automatic statistic name",
                        f"input {position}: {'.'.join(path)!r}",
                        "Give this multi-segment path a single-segment output name with "
                        "reduce({'your_name': statistic}).",
                        "frame.reduce",
                        canonical_id="analysis_frame.reduce",
                    )
                name = f"{path[0]}_{definition.method}"
            else:
                reject(
                    "row_count() or sum/mean of a direct single-segment value Column",
                    f"input {position}: {type(definition).__name__}",
                    "Give this statistic an explicit name with reduce({'your_name': statistic}).",
                    "frame.reduce",
                    canonical_id="analysis_frame.reduce",
                )
            value_name(name, location="frame.reduce", canonical_id="analysis_frame.reduce")
        if name in candidates:
            reject(
                "distinct statistic output names",
                f"{name!r} at input positions {candidates[name]} and {position}",
                "Give each statistic a distinct explicit name with reduce({'your_name': statistic}).",
                "frame.reduce",
                canonical_id="analysis_frame.reduce",
            )
        candidates[name] = position
        statistics.append((name, definition))
    return ReduceValues(tuple(statistics), positions)


def bind_partition(frame: _ColumnFrame, statistic: _Statistic, by: tuple[FrameAxis, ...]) -> Column:
    if type(by) is not tuple:
        reject(
            "a tuple of complete current axes",
            type(by).__name__,
            "Pass over(statistic, by=(frame.axes[0],)).",
            "frame.over",
            canonical_id="analysis_frame.over",
        )
    requested = tuple(
        frame_axis_index(frame, axis, location="frame.over", canonical_id="analysis_frame.over")
        for axis in by
    )
    if len(set(requested)) != len(requested):
        reject(
            "distinct complete current axes",
            repr(requested),
            "Use each complete axis once.",
            "frame.over",
            canonical_id="analysis_frame.over",
        )
    positions = tuple(index for index in range(len(frame.axes)) if index in requested)
    bound = bind_statistic(
        frame, statistic, location="frame.over", canonical_id="analysis_frame.over"
    )
    definition = statistic_definition(bound)
    schema = infer_frame_schema(
        frame_logical_schema(frame), ReduceValues((("statistic", definition),), positions)
    )
    return Column(
        frame_input_id(frame),
        ValuePartition(definition, positions),
        bound.unit,
        column_kind(schema.values[0]),
        frame,
    )


def bind_correlation(
    frame: _ColumnFrame,
    x: Column,
    y: Column,
    method: Literal["pearson", "spearman"],
    by: tuple[FrameAxis, ...],
) -> ReduceValues:
    if method not in ("pearson", "spearman"):
        reject(
            "pearson or spearman",
            repr(method),
            "Choose an explicit supported correlation method.",
            "frame.correlate",
            canonical_id="analysis_frame.correlate",
        )
    for label, column in (("x", x), ("y", y)):
        bound_frame = column_frame(column) if is_instance(column, Column) else None
        if not is_instance(column, Column) or (
            column_input_id(column) != frame_input_id(frame)
            or bound_frame is None
            or frame_local(bound_frame).owner != frame_local(frame).owner
        ):
            reject(
                f"{label}: a Column from this exact Store/Session/Frame input",
                repr(column),
                "Acquire both Columns on this Frame; align independent Frames first.",
                "frame.correlate",
                canonical_id="analysis_frame.correlate",
            )
    if type(by) is not tuple:
        reject(
            "a tuple of complete same-input FrameAxis handles",
            repr(by),
            "Use by=() or by=(frame.axes[0],).",
            "frame.correlate",
            canonical_id="analysis_frame.correlate",
        )
    for axis in by:
        if not is_instance(axis, FrameAxis) or (
            axis_input_id(axis) != frame_input_id(frame)
            or frame_local(axis_frame(axis)).owner != frame_local(frame).owner
        ):
            reject(
                "complete axes from this exact Frame/Session input",
                repr(axis),
                "Acquire by axes from this Frame.",
                "frame.correlate",
                canonical_id="analysis_frame.correlate",
            )
    positions = tuple(
        frame_axis_index(
            frame, axis, location="frame.correlate", canonical_id="analysis_frame.correlate"
        )
        for axis in by
    )
    if len(set(positions)) != len(positions):
        reject(
            "distinct complete current axes",
            repr(positions),
            "Use each complete axis once.",
            "frame.correlate",
            canonical_id="analysis_frame.correlate",
        )
    coefficient = column_pair_statistic(
        x, y, method, location="frame.correlate", canonical_id="analysis_frame.correlate"
    )
    count = column_pair_statistic(
        x, y, "pair_count", location="frame.correlate", canonical_id="analysis_frame.correlate"
    )
    return ReduceValues(
        (
            ("coefficient", statistic_definition(coefficient)),
            ("n_pairs", statistic_definition(count)),
        ),
        positions,
    )


def bind_axis_pairs(
    frame: _ColumnFrame,
    other: _ColumnFrame,
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None,
    *,
    location: str,
    canonical_id: str,
) -> tuple[tuple[int, int], ...]:
    on = pair_axes(frame.axes, other.axes, on, location=location, canonical_id=canonical_id)
    return tuple(
        (
            frame_axis_index(frame, a, location=location, canonical_id=canonical_id),
            frame_axis_index(other, b, location=location, canonical_id=canonical_id),
        )
        for a, b in on
    )
