"""Physical axis layouts for binary Frame alignment and comparison."""

from __future__ import annotations

from dataclasses import dataclass, replace

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.frame_operations import AlignFrameValues, NamedFrameComparison
from marivo.analysis.core.frozen_calendar import require_calendar_match_rule
from marivo.analysis.model.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    OrdinaryComparisonBinding,
    PairedDataAxis,
    require_compatible_calendar_axes,
)
from marivo.analysis.model.data_types import ScalarDataType
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_structure import (
    AxisRole,
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    axis_role,
    column_kind,
    pending_column,
)


@dataclass(frozen=True, slots=True)
class AlignedAxes:
    axes: tuple[DataAxis | PendingDataAxis, ...]
    columns: tuple[tuple[DataColumn | PendingDataColumn, ...], ...]
    partners: dict[int, int]
    matched: int | None


def _paired_layout(
    left: DataSchema | PendingDataSchema, right: DataSchema | PendingDataSchema, index: int
) -> tuple[str, DataColumn]:
    tokens = {axis.token for schema in (left, right) for axis in schema.axes}
    slots = {column.slot_id for schema in (left, right) for column in schema.columns}
    suffix = 0
    while True:
        token = f"frame.paired.{index}" + (f".{suffix}" if suffix else "")
        slot = f"{token}.key"
        if not {token, f"{token}.left", f"{token}.right"} & tokens and slot not in slots:
            return token, DataColumn(slot, "pair", ScalarDataType("int64"), False)
        suffix += 1


def paired_axis(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    index: int,
    partner: int,
) -> PairedDataAxis:
    a, b = left.axes[index], right.axes[partner]
    assert isinstance(a, (CalendarDataAxis, IntervalDataAxis)) and isinstance(
        b, (CalendarDataAxis, IntervalDataAxis)
    )
    assert a.frozen is not None and b.frozen is not None
    token, key = _paired_layout(left, right, index)
    used = {
        axis.frozen.directory_name
        for schema in (left, right)
        for axis in schema.axes
        if isinstance(axis, (CalendarDataAxis, IntervalDataAxis)) and axis.frozen is not None
    }

    def name(base: str) -> str:
        candidate, n = base, 0
        while candidate in used:
            n += 1
            candidate = f"{base}.{n}"
        used.add(candidate)
        return candidate

    def endpoint(
        axis: CalendarDataAxis | IntervalDataAxis, side: str
    ) -> CalendarDataAxis | IntervalDataAxis:
        assert axis.frozen is not None
        directory = name(f"{axis_role(axis)}.{token}.{side}")
        if isinstance(axis, CalendarDataAxis):
            return replace(
                axis,
                token=f"{token}.{side}",
                frozen=replace(axis.frozen, directory_name=directory, direct=None),
            )
        return replace(
            axis, token=f"{token}.{side}", frozen=replace(axis.frozen, directory_name=directory)
        )

    return PairedDataAxis(
        token,
        (key,),
        endpoint(a, "left"),
        endpoint(b, "right"),
        name(f"mapping.{token}"),
        name=a.name,
    )


def _valid_axis_pairs(pairs: object) -> bool:
    return (
        isinstance(pairs, tuple)
        and type(pairs) is tuple
        and all(
            type(pair) is tuple and len(pair) == 2 and all(type(index) is int for index in pair)
            for pair in pairs
        )
    )


def _align_axis(
    axis: DataAxis | PendingDataAxis,
    other: DataAxis | PendingDataAxis,
    left_role: AxisRole,
    right_role: AxisRole,
    match: CalendarCorrespondence | None,
) -> tuple[DataAxis | PendingDataAxis, tuple[DataColumn | PendingDataColumn, ...]]:
    if left_role != right_role or len(axis.columns) != len(other.columns) or axis.at != other.at:
        reject(
            "matching roles, complete key arity and time interpretation",
            repr((axis, other)),
            "Bind compatible complete axes.",
            "frame.combine",
        )
    columns: list[DataColumn | PendingDataColumn] = []
    left_components: tuple[DataColumn | PendingDataColumn, ...] = axis.columns
    right_components: tuple[DataColumn | PendingDataColumn, ...] = other.columns
    for a, b in zip(left_components, right_components, strict=True):
        if isinstance(a, DataColumn) and isinstance(b, DataColumn):
            compatible = a.dtype == b.dtype
        else:
            ak, bk = column_kind(a), column_kind(b)
            compatible = ak == bk or "unresolved" in (ak, bk)
        if not compatible:
            reject(
                "equal complete physical key types",
                repr((a, b)),
                "Bind axes with matching complete key component types.",
                "frame.combine",
            )
        nullable = (
            True
            if a.nullable is True or b.nullable is True
            else None
            if a.nullable is None or b.nullable is None
            else False
        )
        columns.append(
            replace(a, nullable=nullable)
            if isinstance(a, PendingDataColumn)
            else replace(pending_column(a), nullable=nullable)
            if nullable is None
            else replace(a, nullable=nullable)
        )
    if isinstance(axis, CalendarDataAxis) and isinstance(other, CalendarDataAxis):
        require_compatible_calendar_axes(axis, other)
        if match is not None:
            require_calendar_match_rule(axis, match)
    if isinstance(axis, IntervalDataAxis) and match is not None and match.kind != "ordinal":
        reject(
            "ordinal for interval coordinates",
            repr(match),
            "Use ordinal for one declared interval on each side.",
            "frame.combine",
        )
    if isinstance(axis, IntervalDataAxis):
        assert isinstance(other, IntervalDataAxis)
        axis = replace(axis, timezone=axis.timezone if axis.timezone == other.timezone else "UTC")
    return axis, tuple(columns)


def align_axes(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    operation: AlignFrameValues,
) -> AlignedAxes:
    if not _valid_axis_pairs(operation.pairs):
        reject(
            "immutable integer axis-index pairs",
            repr(operation.pairs),
            "Resolve complete axis handles before planning.",
            "frame.combine",
        )
    if operation.match is not None and type(operation.match) is not CalendarCorrespondence:
        reject(
            "closed binary policies",
            repr(operation),
            "Use a closed calendar correspondence rule.",
            "frame.combine",
        )
    left_axes: list[DataAxis | PendingDataAxis] = list(left.axes)
    right_axes: tuple[DataAxis | PendingDataAxis, ...] = right.axes
    left_roles: tuple[AxisRole, ...] = tuple(
        a.role if isinstance(a, PendingDataAxis) else axis_role(a) for a in left_axes
    )
    right_roles: tuple[AxisRole, ...] = tuple(
        a.role if isinstance(a, PendingDataAxis) else axis_role(a) for a in right_axes
    )
    if "paired" in (*left_roles, *right_roles):
        reject(
            "inputs without paired axes",
            repr((left_roles, right_roles)),
            "Reduce away paired axes or construct new source inputs.",
            "frame.combine",
        )
    if len(left.axes) != len(right.axes):
        reject(
            "equal complete axis counts",
            repr((len(left.axes), len(right.axes))),
            "Reduce or reconstruct inputs with compatible axes.",
            "frame.combine",
        )
    if (
        len(operation.pairs) != len(left.axes)
        or {a for a, _ in operation.pairs} != set(range(len(left.axes)))
        or {b for _, b in operation.pairs} != set(range(len(right.axes)))
    ):
        reject(
            "a complete axis bijection",
            repr(operation.pairs),
            "Bind every complete axis exactly once.",
            "frame.combine",
        )
    partners = dict(operation.pairs)
    axis_columns: list[tuple[DataColumn | PendingDataColumn, ...]] = []
    for i, axis in enumerate(left_axes):
        position = partners[i]
        axis, columns = _align_axis(
            axis, right_axes[position], left_roles[i], right_roles[position], operation.match
        )
        left_axes[i] = axis
        axis_columns.append(columns)
    calendars = tuple(i for i, role in enumerate(left_roles) if role in ("calendar", "interval"))
    matched = calendars[0] if operation.match is not None and len(calendars) == 1 else None
    if operation.match is not None and matched is None:
        reject(
            "exactly one compatible calendar axis pair for match",
            repr((left_roles, right_roles)),
            "Use equality without match, or explicitly construct one compatible calendar axis pair.",
            "frame.combine",
        )
    if matched is not None:
        axis_columns[matched] = (_paired_layout(left, right, matched)[1],)
    return AlignedAxes(tuple(left_axes), tuple(axis_columns), partners, matched)


def baseline_binding(
    current: DataSchema | PendingDataSchema,
    baseline: DataSchema | PendingDataSchema,
    item: NamedFrameComparison,
) -> OrdinaryComparisonBinding | CalendarComparisonBinding | IntervalComparisonBinding:
    temporal = tuple(
        (i, j) for i, j in item.pairs if axis_role(current.axes[i]) in ("calendar", "interval")
    )
    if len(temporal) > 1 or (item.match is not None and not temporal):
        reject(
            "at most one compatible time axis pair, required for match",
            repr(temporal),
            "Construct inputs with one time axis or compare ordinary keys without match.",
            "frame.compare",
        )
    binding: OrdinaryComparisonBinding | CalendarComparisonBinding | IntervalComparisonBinding = (
        OrdinaryComparisonBinding()
    )
    if temporal:
        i, j = temporal[0]
        left, right = current.axes[i], baseline.axes[j]
        token = f"comparison.{item.alias}.baseline.{right.token}"
        components: tuple[DataColumn | PendingDataColumn, ...] = right.columns
        columns = tuple(
            replace(c, slot_id=f"{token}.key.{k}")
            for k, c in enumerate(components)
            if isinstance(c, DataColumn)
        )
        name = f"comparison.{item.alias}.directory"
        if isinstance(left, CalendarDataAxis) and isinstance(right, CalendarDataAxis):
            if item.match is not None:
                require_calendar_match_rule(left, item.match)
            assert right.frozen is not None
            endpoint = replace(
                right,
                token=token,
                columns=columns,
                frozen=replace(right.frozen, directory_name=name, direct=None),
            )
            binding = CalendarComparisonBinding(
                left.token, endpoint, f"comparison.{item.alias}.map"
            )
        else:
            assert isinstance(left, IntervalDataAxis) and isinstance(right, IntervalDataAxis)
            if item.match is not None and item.match.kind != "ordinal":
                reject(
                    "ordinal for interval coordinates",
                    repr(item.match),
                    "Use ordinal for declared interval endpoints.",
                    "frame.compare",
                )
            endpoint_interval = replace(
                right,
                token=token,
                columns=columns,
                frozen=replace(right.frozen, directory_name=name),
            )
            binding = IntervalComparisonBinding(
                left.token, endpoint_interval, f"comparison.{item.alias}.map"
            )
    elif item.match is not None:
        reject(
            "a time axis for match",
            repr(item.match),
            "Compare ordinary keys without match.",
            "frame.compare",
        )
    return binding
