"""Pure correspondence binding and schema derivation, before business reads."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, NoReturn

from marivo._definition.correspondence import (
    AxisProjection,
    CalendarCorrespondence,
    ComparisonAxis,
    Correspondence,
    CorrespondenceAxis,
    DeclaredCorrespondence,
)
from marivo._definition.deferred import RelationshipParameter
from marivo._definition.types import AxisBinding, CalendarCoordinate
from marivo._temporal import (
    PeriodCalendarSnapshotV1,
    anchor_from_datetime,
    builtin_grain,
    session_time_scope,
)
from marivo._temporal_grid import bind_grid, instant
from marivo._timezones import parse_timezone
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.model import reject
from marivo.semantic.validator import Registry, normalize_target_relationship


@dataclass(frozen=True, slots=True)
class PresenceBinding:
    path: tuple[str, ...]
    column: str


@dataclass(frozen=True, slots=True)
class ProjectionBinding:
    path: tuple[str, ...]
    axis: ComparisonAxis
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComparisonEndpoint:
    """Complete prepared schema for one endpoint, without data or source handles."""

    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    axes: tuple[ComparisonAxis, ...]
    key_groups: tuple[tuple[str, ...], ...]
    presence: tuple[PresenceBinding, ...] = ()
    projections: tuple[ProjectionBinding, ...] = ()

    def __post_init__(self) -> None:
        slots = tuple(column.slot_id for column in self.columns)
        if (
            len(set(slots)) != len(slots)
            or len(self.axes) != len(self.key_groups)
            or tuple(key for group in self.key_groups for key in group) != self.keys
            or any(not group for group in self.key_groups)
            or len(set(self.keys)) != len(self.keys)
            or not set(self.keys).issubset(slots)
        ):
            fail("one complete key group per ordered axis", repr((self.keys, self.key_groups)))
        if len({axis_identity(axis) for axis in self.axes}) != len(self.axes):
            fail("distinct unambiguous endpoint axes", repr(self.axes))
        for item in self.presence:
            column = self.column(item.column)
            if column.dtype != ScalarDataType("boolean") or column.nullable:
                fail("nonnullable boolean endpoint presence", repr(item))
        for projection in self.projections:
            if not projection.columns or not set(projection.columns).issubset(slots):
                fail("complete endpoint projection columns", repr(projection))
        if len({column.name for column in self.values}) != len(self.values):
            fail("distinct endpoint quantity names", repr(self.values))

    def column(self, slot: str) -> DataColumn:
        for column in self.columns:
            if column.slot_id == slot:
                return column
        fail("a slot in the endpoint schema", slot)

    @property
    def values(self) -> tuple[DataColumn, ...]:
        support = {
            *self.keys,
            *(item.column for item in self.presence),
            *(key for item in self.projections for key in item.columns),
        }
        return tuple(column for column in self.columns if column.slot_id not in support)


@dataclass(frozen=True, slots=True)
class CorrespondenceRelation:
    """Resolved one-to-one declaration path and its complete identity contract."""

    via: tuple[RelationshipParameter, ...]
    left_axis: AxisBinding
    right_axis: AxisBinding
    left_components: tuple[str, ...]
    right_components: tuple[str, ...]
    direct: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class BroadcastCalendar:
    """One prepared target-bucket to reference-bucket image."""

    destination: str
    buckets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BroadcastMapping:
    """Complete key projection and its already-qualified calendar transforms."""

    keys: tuple[tuple[str, str], ...]
    calendars: tuple[BroadcastCalendar, ...] = ()
    presence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationRequirement:
    """Explicit mapping input requested from the owning Query scheduler."""

    relation: CorrespondenceRelation
    columns: tuple[DataColumn, ...]
    left_keys: tuple[str, ...]
    right_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoundCorrespondence:
    left: ComparisonEndpoint
    right: ComparisonEndpoint
    layout: ComparisonEndpoint
    operation: Literal["align", "compare"]
    how: Literal["exact", "outer"]
    roles: tuple[str, str]
    pairs: tuple[tuple[int, int], ...]
    transformed: tuple[int, int] | None
    calendar_pairs: tuple[tuple[int | None, int | None], ...] = ()
    relation: CorrespondenceRelation | None = None
    relation_requirement: RelationRequirement | None = None
    time_axis: Literal["paired", "current"] = "paired"


def fail(expected: str, received: str) -> NoReturn:
    reject(
        expected,
        received,
        "Inspect both complete endpoint axes and keys; select compatible complete domains, an explicit correspondence, or how='outer' for missing keys.",
        "Query.correspondence",
    )


def axis_identity(axis: ComparisonAxis) -> str:
    """Semantic binding identity excludes stale captured definition versions."""
    if isinstance(axis, AxisBinding):
        return repr(
            (
                axis.entity.ref.key,
                axis.field.ref.key if axis.field else None,
                tuple(step.ref.key for step in axis.path),
                axis.role,
                axis.at,
            )
        )
    return repr((type(axis).__name__, axis.identity))


def _unique_axis(axes: tuple[ComparisonAxis, ...], role: str, kind: str) -> int:
    choices = tuple(
        index
        for index, axis in enumerate(axes)
        if (
            isinstance(axis, CalendarCoordinate)
            if kind == "calendar"
            else isinstance(axis, AxisBinding) and axis.role == "identity"
        )
    )
    if len(choices) != 1:
        fail(f"exactly one {kind} axis on {role}", repr(axes))
    return choices[0]


def comparison_axes(
    left_axes: tuple[ComparisonAxis, ...],
    right_axes: tuple[ComparisonAxis, ...],
    *,
    match: Correspondence | None = None,
    operation: Literal["align", "compare"] = "align",
    time_axis: Literal["paired", "current"] = "paired",
) -> tuple[ComparisonAxis, ...]:
    """Derive logical output axes without schema, Catalog or calendar coverage reads."""
    if operation not in ("align", "compare") or time_axis not in ("paired", "current"):
        fail("closed comparison operation and time-axis policy", repr((operation, time_axis)))
    roles = ("left", "right") if operation == "align" else ("current", "baseline")
    transformed: tuple[int, int] | None = None
    if isinstance(match, CalendarCorrespondence):
        transformed = (
            _unique_axis(left_axes, roles[0], "calendar"),
            _unique_axis(right_axes, roles[1], "calendar"),
        )
        mapping_identity = repr((match.kind, match.by))
    elif isinstance(match, DeclaredCorrespondence):
        transformed = (
            _unique_axis(left_axes, roles[0], "member"),
            _unique_axis(right_axes, roles[1], "member"),
        )
        mapping_identity = repr(tuple(step.ref.ref.key for step in match.via))
    elif match is None:
        mapping_identity = "identity"
    else:
        fail("an axis-free Correspondence or None", type(match).__name__)
    if len(left_axes) != len(right_axes):
        fail("equal complete endpoint axis counts", repr((left_axes, right_axes)))
    if time_axis == "current" and (
        operation != "compare" or not isinstance(match, CalendarCorrespondence)
    ):
        fail("calendar compare for time_axis='current'", repr(match))
    axes: list[ComparisonAxis] = []
    used: set[int] = set()
    for i, axis in enumerate(left_axes):
        choices = (
            (transformed[1],)
            if transformed is not None and i == transformed[0]
            else tuple(
                j
                for j, other in enumerate(right_axes)
                if (transformed is None or j != transformed[1])
                and axis_identity(axis) == axis_identity(other)
            )
        )
        if len(choices) != 1 or choices[0] in used:
            fail("one unambiguous counterpart per complete endpoint axis", repr((axis, right_axes)))
        j = choices[0]
        used.add(j)
        if transformed == (i, j) and time_axis == "paired":
            identity = hashlib.sha256(
                repr(
                    (axis_identity(axis), axis_identity(right_axes[j]), roles, mapping_identity)
                ).encode()
            ).hexdigest()
            axis = CorrespondenceAxis(
                identity,
                (AxisProjection((roles[0],), axis), AxisProjection((roles[1],), right_axes[j])),
            )
        axes.append(axis)
    return tuple(axes)


def bind_declared_relation(
    left: AxisBinding,
    right: AxisBinding,
    match: DeclaredCorrespondence,
    registry: Registry,
) -> CorrespondenceRelation:
    """Resolve current loaded path facts without reading schema or business rows."""
    if left.role != "identity" or right.role != "identity":
        fail("declared correspondence between identity axes", repr((left, right)))
    current = left.entity.ref.path
    direct = tuple((index, index) for index in range(len(registry.entities[current].primary_key)))
    origin = registry.entities[current].primary_key
    current_components = origin
    visited = {current}
    for step in match.via:
        if step.ref.ref.path not in registry.relationships:
            fail("a loaded declared Relationship", step.ref.ref.key)
        mapping = normalize_target_relationship(registry, step.ref.ref.path)
        if mapping.from_entity_ref.path != current or mapping.cardinality != "one_to_one":
            fail("a contiguous directed one-to-one identity path", mapping.ref.path)
        if mapping.from_version_resolution_required and left.at is None:
            fail("an explicit left identity version anchor", current)
        destination = mapping.to_entity_ref.path
        if mapping.to_version_resolution_required and right.at is None:
            fail("an explicit right identity version anchor", destination)
        if destination in visited:
            fail("an acyclic identity correspondence", destination)
        target_components = registry.entities[destination].primary_key
        # Only a complete PK-to-PK equality path can bypass a mapping input.
        step_pairs = tuple(
            (current_components.index(a), target_components.index(b))
            for a, b in mapping.keys
            if a in current_components and b in target_components
        )
        if (
            len(step_pairs) != len(mapping.keys)
            or {a for a, _ in step_pairs} != set(range(len(current_components)))
            or {b for _, b in step_pairs} != set(range(len(target_components)))
        ):
            direct = ()
        elif direct:
            direct = tuple((a, dict(step_pairs)[b]) for a, b in direct)
        current, current_components = destination, target_components
        visited.add(current)
    if current != right.entity.ref.path:
        fail("a path ending at the right identity owner", f"{current} != {right.entity.ref.path}")
    return CorrespondenceRelation(match.via, left, right, origin, current_components, direct)


def _complete_calendar(left: CalendarCoordinate, right: CalendarCoordinate) -> None:
    if (left.grain, left.timezone, left.snapshot_digest) != (
        right.grain,
        right.timezone,
        right.snapshot_digest,
    ):
        fail("equal resolved Grain, boundary timezone and calendar snapshot", repr((left, right)))
    for role, axis in (("left", left), ("right", right)):
        if not axis.cells or any((a, b) != (c, d) for a, b, c, d in axis.cells):
            fail("complete nominal calendar buckets on both endpoints", f"{role}: {axis!r}")
        if any(a[1] != b[0] for a, b in zip(axis.cells, axis.cells[1:], strict=False)):
            fail("an ordered complete nominal calendar partition", repr(axis))


def _builtin_shift(value: datetime, grain: str, by: int) -> datetime:
    if grain in ("month", "quarter", "year"):
        months = by * {"month": 1, "quarter": 3, "year": 12}[grain]
        ordinal = value.year * 12 + value.month - 1 + months
        return value.replace(year=ordinal // 12, month=ordinal % 12 + 1)
    if grain in ("day", "week"):
        return value + timedelta(days=by * (7 if grain == "week" else 1))
    fail("day/week/month/quarter/year period offsets", grain)
    raise AssertionError


def _subday_periods(axis: CalendarCoordinate, offset: int) -> tuple[tuple[datetime, datetime], ...]:
    """Extend the same finite calendar authority, including both DST folds."""
    parsed = re.fullmatch(r"([0-9]+)?(second|minute|hour)", axis.grain)
    if parsed is None:
        return ()
    count, unit = int(parsed.group(1) or 1), parsed.group(2)
    seconds = {"second": 1, "minute": 60, "hour": 3600}[unit] * count
    margin = timedelta(days=abs(offset) * seconds // 86400 + 2)
    zone = parse_timezone(axis.timezone)[1]
    first = axis.cells[0][0].astimezone(zone).date() - margin
    last = axis.cells[-1][1].astimezone(zone).date() + margin
    scope = session_time_scope(
        anchor_from_datetime(instant(first, axis.timezone)),
        anchor_from_datetime(instant(last, axis.timezone)),
        axis.timezone,
    )
    grid = bind_grid(scope, builtin_grain(unit, count=count), report_timezone=axis.timezone)
    return tuple((cell.original_start, cell.original_end) for cell in grid.cells)


def _calendar_pairs(
    left: CalendarCoordinate,
    right: CalendarCoordinate,
    match: CalendarCorrespondence,
    how: Literal["exact", "outer"],
    snapshot: PeriodCalendarSnapshotV1 | None,
) -> tuple[tuple[int | None, int | None], ...]:
    _complete_calendar(left, right)
    if match.kind == "ordinal":
        if how == "exact" and len(left.cells) != len(right.cells):
            fail(
                "equal complete calendar bucket counts for exact ordinal",
                f"{len(left.cells)} / {len(right.cells)}",
            )
        return tuple(
            (i if i < len(left.cells) else None, i if i < len(right.cells) else None)
            for i in range(max(len(left.cells), len(right.cells)))
        )
    zone = parse_timezone(left.timezone)[1]
    if match.kind == "previous_year" and (
        left.snapshot_digest is not None or left.grain not in ("day", "month", "quarter", "year")
    ):
        fail("builtin day/month/quarter/year for previous_year", left.grain)
    periods = _subday_periods(left, match.by) if match.kind == "period_offset" else ()
    if left.snapshot_digest is not None:
        if (
            snapshot is None
            or snapshot.snapshot_digest != left.snapshot_digest
            or snapshot.boundary_timezone != left.timezone
        ):
            fail("the complete frozen certified calendar for period_offset", left.grain)
        level = left.grain.rsplit("::", 1)[-1]
        if level == "day":
            start, end = snapshot.coverage
            periods = tuple(
                (
                    instant(start + timedelta(days=i), left.timezone),
                    instant(start + timedelta(days=i + 1), left.timezone),
                )
                for i in range((end - start).days)
            )
        else:
            periods = tuple(
                (instant(p.start_date, left.timezone), instant(p.end_date, left.timezone))
                for p in sorted(snapshot.periods, key=lambda p: p.start_date)
                if p.level_name == level
            )
    right_by_bounds: dict[tuple[datetime, datetime], int] = {
        (cell[0], cell[1]): i for i, cell in enumerate(right.cells)
    }
    result: list[tuple[int | None, int | None]] = []
    used: set[int] = set()
    for index, cell in enumerate(left.cells):
        if periods:
            try:
                position = periods.index((cell[0], cell[1])) + match.by
            except ValueError:
                fail("nominal periods in the certified calendar", repr(cell))
            if not 0 <= position < len(periods):
                fail("certified coverage for the requested period_offset", repr(cell))
            bounds: tuple[datetime, datetime] | None = periods[position]
        else:
            local = cell[0].astimezone(zone).replace(tzinfo=None)
            try:
                target = (
                    local.replace(year=local.year - 1)
                    if match.kind == "previous_year"
                    else _builtin_shift(local, left.grain, match.by)
                )
                bounds = (
                    instant(target, left.timezone),
                    instant(_builtin_shift(target, left.grain, 1), left.timezone),
                )
            except ValueError:
                # A leap day has no same-name day in the prior year.
                bounds = None
        other = right_by_bounds.get(bounds) if bounds is not None else None
        result.append((index, other))
        if other is not None:
            if other in used:
                fail("a partial injection between complete periods", repr(bounds))
            used.add(other)
    result.extend((None, index) for index in range(len(right.cells)) if index not in used)
    if how == "exact" and any(a is None or b is None for a, b in result):
        fail("complete exact period correspondence", repr(result))
    return tuple(result)


def _numeric_type(left: DataType, right: DataType) -> DataType:
    if isinstance(left, DurationDataType) or isinstance(right, DurationDataType):
        if left != right:
            fail("Duration endpoints with the same exact tick unit", repr((left, right)))
        return left

    def numeric(dtype: DataType) -> bool:
        return isinstance(dtype, DecimalDataType) or (
            isinstance(dtype, ScalarDataType) and dtype.name in ("int64", "float64")
        )

    if not numeric(left) or not numeric(right):
        fail("numeric comparable endpoint quantities", repr((left, right)))
    if left == ScalarDataType("float64") or right == ScalarDataType("float64"):
        return ScalarDataType("float64")
    decimals = tuple(dtype for dtype in (left, right) if isinstance(dtype, DecimalDataType))
    if decimals:
        scale = max(dtype.scale for dtype in decimals)
        integer = max(
            dtype.precision - dtype.scale if isinstance(dtype, DecimalDataType) else 19
            for dtype in (left, right)
        )
        return DecimalDataType(min(38, integer + scale), scale)
    return ScalarDataType("int64")


def bind_correspondence(
    left: ComparisonEndpoint,
    right: ComparisonEndpoint,
    *,
    match: Correspondence | None = None,
    how: Literal["exact", "outer"] = "exact",
    operation: Literal["align", "compare"] = "align",
    roles: tuple[str, str] | None = None,
    time_axis: Literal["paired", "current"] = "paired",
    relations: tuple[CorrespondenceRelation, ...] = (),
    calendar_snapshot: PeriodCalendarSnapshotV1 | None = None,
) -> BoundCorrespondence:
    """Freeze axes, complete key mapping, output schema and explicit dependencies."""
    if (
        how not in ("exact", "outer")
        or operation not in ("align", "compare")
        or time_axis not in ("paired", "current")
    ):
        fail(
            "closed alignment, operation and time-axis policies", repr((how, operation, time_axis))
        )
    roles = roles or (("left", "right") if operation == "align" else ("current", "baseline"))
    if roles != (("left", "right") if operation == "align" else ("current", "baseline")):
        fail("the operation's canonical endpoint roles", repr(roles))
    transformed: tuple[int, int] | None = None
    calendars: tuple[tuple[int | None, int | None], ...] = ()
    relation = None
    requirement = None
    if isinstance(match, CalendarCorrespondence):
        transformed = (
            _unique_axis(left.axes, roles[0], "calendar"),
            _unique_axis(right.axes, roles[1], "calendar"),
        )
        a, b = left.axes[transformed[0]], right.axes[transformed[1]]
        assert isinstance(a, CalendarCoordinate) and isinstance(b, CalendarCoordinate)
        calendars = _calendar_pairs(a, b, match, how, calendar_snapshot)
    elif isinstance(match, DeclaredCorrespondence):
        transformed = (
            _unique_axis(left.axes, roles[0], "member"),
            _unique_axis(right.axes, roles[1], "member"),
        )
        a, b = left.axes[transformed[0]], right.axes[transformed[1]]
        relation_candidates = tuple(
            item
            for item in relations
            if axis_identity(item.left_axis) == axis_identity(a)
            and axis_identity(item.right_axis) == axis_identity(b)
            and item.via == match.via
        )
        if len(relation_candidates) != 1:
            fail(
                "one resolved declared correspondence path for the actual identity axes",
                repr((a, b)),
            )
        relation = relation_candidates[0]
        if len(relation.left_components) != len(left.key_groups[transformed[0]]) or len(
            relation.right_components
        ) != len(right.key_groups[transformed[1]]):
            fail("all declared primary-key components on both identity axes", repr(relation))
        if relation.direct:
            if (
                {a for a, _ in relation.direct} != set(range(len(relation.left_components)))
                or {b for _, b in relation.direct} != set(range(len(relation.right_components)))
                or len(set(relation.direct)) != len(relation.direct)
            ):
                fail("a bijection over complete identity components", repr(relation.direct))
            for source, target in relation.direct:
                if (
                    left.column(left.key_groups[transformed[0]][source]).dtype
                    != right.column(right.key_groups[transformed[1]][target]).dtype
                ):
                    fail("equal types for declared identity key equalities", repr((source, target)))
        if not relation.direct:
            left_keys = tuple(
                f"mapping.left.{i}" for i in range(len(left.key_groups[transformed[0]]))
            )
            right_keys = tuple(
                f"mapping.right.{i}" for i in range(len(right.key_groups[transformed[1]]))
            )
            columns = tuple(
                replace(left.column(source), slot_id=target, nullable=False)
                for source, target in zip(left.key_groups[transformed[0]], left_keys, strict=True)
            ) + tuple(
                replace(right.column(source), slot_id=target, nullable=False)
                for source, target in zip(right.key_groups[transformed[1]], right_keys, strict=True)
            )
            requirement = RelationRequirement(relation, columns, left_keys, right_keys)
    elif match is not None:
        fail("an axis-free Correspondence or None", type(match).__name__)
    if len(left.axes) != len(right.axes):
        fail(
            "equal endpoint axis counts with complete correspondence", repr((left.axes, right.axes))
        )
    pairs: list[tuple[int, int]] = []
    for i, axis in enumerate(left.axes):
        candidates = (
            (transformed[1],)
            if transformed is not None and transformed[0] == i
            else tuple(
                j
                for j, other in enumerate(right.axes)
                if (transformed is None or j != transformed[1])
                and axis_identity(axis) == axis_identity(other)
            )
        )
        if len(candidates) != 1:
            fail(
                "one exact counterpart for every remaining endpoint axis", repr((axis, right.axes))
            )
        j = candidates[0]
        if transformed != (i, j) and tuple(
            left.column(key).dtype for key in left.key_groups[i]
        ) != tuple(right.column(key).dtype for key in right.key_groups[j]):
            fail(
                "equal complete coordinate component types",
                repr((left.key_groups[i], right.key_groups[j])),
            )
        pairs.append((i, j))
    if len({j for _, j in pairs}) != len(right.axes):
        fail("a unique full endpoint axis mapping", repr(pairs))
    if time_axis == "current" and (
        operation != "compare"
        or transformed is None
        or not isinstance(left.axes[transformed[0]], CalendarCoordinate)
    ):
        fail("calendar compare for time_axis='current'", repr(match))
    layout = _layout(
        left, right, tuple(pairs), transformed, operation, roles, time_axis, how, match
    )
    return BoundCorrespondence(
        left,
        right,
        layout,
        operation,
        how,
        roles,
        tuple(pairs),
        transformed,
        calendars,
        relation,
        requirement,
        time_axis,
    )


def _layout(
    left: ComparisonEndpoint,
    right: ComparisonEndpoint,
    pairs: tuple[tuple[int, int], ...],
    transformed: tuple[int, int] | None,
    operation: Literal["align", "compare"],
    roles: tuple[str, str],
    time_axis: Literal["paired", "current"],
    how: Literal["exact", "outer"],
    match: Correspondence | None,
) -> ComparisonEndpoint:
    columns: list[DataColumn] = []
    axes = comparison_axes(
        left.axes, right.axes, match=match, operation=operation, time_axis=time_axis
    )
    groups: list[tuple[str, ...]] = []
    projections: list[ProjectionBinding] = []
    for i, j in pairs:
        paired = transformed == (i, j)
        group: tuple[str, ...]
        if paired and time_axis == "paired":
            group = (f"key.{len(columns)}",)
            columns.append(DataColumn(group[0], f"axis.{i}", ScalarDataType("string"), False))
        else:
            group = tuple(f"key.{len(columns) + n}" for n in range(len(left.key_groups[i])))
            columns.extend(
                replace(left.column(source), slot_id=target)
                for source, target in zip(left.key_groups[i], group, strict=True)
            )
        groups.append(group)
    keys = tuple(key for group in groups for key in group)
    for role, endpoint in zip(roles, (left, right), strict=True):
        for index, (axis, group) in enumerate(zip(endpoint.axes, endpoint.key_groups, strict=True)):
            slots = tuple(f"projection.{role}.{index}.{n}" for n in range(len(group)))
            columns.extend(
                replace(endpoint.column(source), slot_id=target, nullable=True)
                for source, target in zip(group, slots, strict=True)
            )
            projections.append(ProjectionBinding((role,), axis, slots))
        for index, projection in enumerate(endpoint.projections):
            slots = tuple(
                f"projection.{role}.nested.{index}.{n}" for n in range(len(projection.columns))
            )
            columns.extend(
                replace(endpoint.column(source), slot_id=target, nullable=True)
                for source, target in zip(projection.columns, slots, strict=True)
            )
            projections.append(ProjectionBinding((role, *projection.path), projection.axis, slots))
    if operation == "align":
        for role, endpoint in zip(roles, (left, right), strict=True):
            columns.extend(
                replace(
                    column,
                    slot_id=f"value.{role}.{n}",
                    name=f"{role}.{column.name}",
                    nullable=column.nullable or how == "outer",
                )
                for n, column in enumerate(endpoint.values)
            )
    else:
        right_values = {column.name: column for column in right.values}
        if not left.values or set(right_values) != {column.name for column in left.values}:
            fail("equal nonempty endpoint quantity name sets", repr((left.values, right.values)))
        for index, current in enumerate(left.values):
            baseline = right_values[current.name]
            if current.unit != baseline.unit:
                fail("equal units for each comparable quantity", repr((current, baseline)))
            absolute = _numeric_type(current.dtype, baseline.dtype)
            columns.extend(
                (
                    replace(
                        current,
                        slot_id=f"value.{index}.current",
                        name=f"{current.name}.current",
                        nullable=True,
                    ),
                    replace(
                        baseline,
                        slot_id=f"value.{index}.baseline",
                        name=f"{current.name}.baseline",
                        nullable=True,
                    ),
                    DataColumn(
                        f"value.{index}.absolute",
                        f"{current.name}.absolute",
                        absolute,
                        True,
                        current.unit,
                    ),
                    DataColumn(
                        f"value.{index}.relative",
                        f"{current.name}.relative",
                        ScalarDataType("float64"),
                        True,
                    ),
                )
            )
    presence: list[PresenceBinding] = []
    for role, endpoint in zip(roles, (left, right), strict=True):
        presence.append(PresenceBinding((role,), f"presence.{role}"))
        for index, nested in enumerate(endpoint.presence):
            presence.append(PresenceBinding((role, *nested.path), f"presence.{role}.{index}"))
    columns.extend(
        DataColumn(item.column, ".".join(item.path), ScalarDataType("boolean"), False)
        for item in presence
    )
    return ComparisonEndpoint(
        tuple(columns), keys, tuple(axes), tuple(groups), tuple(presence), tuple(projections)
    )


def _coarsened_calendar(
    fine: CalendarCoordinate, coarse: CalendarCoordinate
) -> tuple[int, ...] | None:
    if (
        not fine.cells
        or not coarse.cells
        or fine.timezone != coarse.timezone
        or fine.snapshot_digest != coarse.snapshot_digest
        or (min(cell[2] for cell in fine.cells), max(cell[3] for cell in fine.cells))
        != (min(cell[2] for cell in coarse.cells), max(cell[3] for cell in coarse.cells))
    ):
        return None
    image: list[int] = []
    for nominal_start, nominal_end, start, end in fine.cells:
        choices = tuple(
            index
            for index, (nominal_a, nominal_b, a, b) in enumerate(coarse.cells)
            if nominal_a <= nominal_start and nominal_end <= nominal_b and a <= start and end <= b
        )
        if len(choices) != 1:
            return None
        image.append(choices[0])
    return tuple(image)


def broadcast_mapping(
    reference: ComparisonEndpoint, target: ComparisonEndpoint
) -> BroadcastMapping:
    """Resolve full bound-axis projection, never names or row positions."""
    pairs: list[tuple[str, str]] = []
    calendars: list[BroadcastCalendar] = []
    presence: list[str] = []
    for axis, group in zip(reference.axes, reference.key_groups, strict=True):
        candidates: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = tuple(
            (keys, ())
            for other, keys in zip(target.axes, target.key_groups, strict=True)
            if axis_identity(axis) == axis_identity(other)
        )
        if not candidates:
            candidates = tuple(
                (item.columns, item.path)
                for item in target.projections
                if axis_identity(item.axis) == axis_identity(axis)
            )
        if not candidates and isinstance(axis, CalendarCoordinate) and len(group) == 1:
            coarsened: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]], ...] = tuple(
                (keys, (), image)
                for other, keys in zip(target.axes, target.key_groups, strict=True)
                if isinstance(other, CalendarCoordinate)
                and len(keys) == 1
                and (image := _coarsened_calendar(other, axis)) is not None
            )
            if not coarsened:
                coarsened = tuple(
                    (item.columns, item.path, image)
                    for item in target.projections
                    if isinstance(item.axis, CalendarCoordinate)
                    and len(item.columns) == 1
                    and (image := _coarsened_calendar(item.axis, axis)) is not None
                )
            if len(coarsened) == 1:
                candidates = ((coarsened[0][0], coarsened[0][1]),)
                calendars.append(BroadcastCalendar(group[0], coarsened[0][2]))
        if len(candidates) != 1 or len(candidates[0][0]) != len(group):
            fail("one complete target-to-reference axis projection", repr((axis, target.axes)))
        sources, path = candidates[0]
        if path:
            required = tuple(item.column for item in target.presence if item.path == path)
            if not required:
                fail("presence facts for a retained broadcast projection", repr(path))
            presence.extend(required)
        for source, destination in zip(sources, group, strict=True):
            if target.column(source).dtype != reference.column(destination).dtype:
                fail("equal complete broadcast coordinate types", repr((source, destination)))
            pairs.append((source, destination))
    return BroadcastMapping(tuple(pairs), tuple(calendars), tuple(dict.fromkeys(presence)))
