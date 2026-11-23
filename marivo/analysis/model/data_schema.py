"""Pure closed data schemas, independent of receipts and publication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, NoReturn, Protocol

from typing_extensions import override

from marivo.analysis.model.data_types import DataColumn as DataColumn
from marivo.analysis.model.data_types import ScalarDataType, require_data_text
from marivo.analysis.model.errors import reject
from marivo.analysis.model.layout_facts import (
    FrameAlignmentSide,
    FrameDecomposition,
    FrameValueContext,
)
from marivo.analysis.model.time_facts import (
    FrameFixedAxis,
    FrameRowAttributeTime,
    FrameSourceTime,
    FrameValueTimeSemantics,
    validate_source_times,
)
from marivo.analysis.model.value_names import validate_paths


@dataclass(frozen=True, slots=True)
class DataAxis:
    """A data token and complete key columns, never an Entity or Field Ref."""

    token: str
    columns: tuple[DataColumn, ...]
    kind: Literal["field", "member", "correspondence"] = field(default="field", kw_only=True)
    name: str = field(default="", kw_only=True)
    at: datetime | FrameRowAttributeTime | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        require_data_text(self.token, "data.axis")
        if self.name:
            require_data_text(self.name, "data.axis.name")
        if (
            self.at is not None
            and not isinstance(self.at, FrameRowAttributeTime)
            and (type(self.at) is not datetime or self.at.tzinfo is not timezone.utc)
        ):
            reject(
                "an inert UTC datetime, captured FrameRowAttributeTime, or no attribute time",
                repr(self.at),
                "Freeze the fixed instant as a plain UTC datetime or retain the acquired row-time field identity.",
                "data.axis.at",
            )
        if (
            type(self.columns) is not tuple
            or not self.columns
            or any(type(c) is not DataColumn for c in self.columns)
            or self.kind not in ("field", "member", "correspondence")
        ):
            reject(
                "complete immutable axis columns",
                repr(self.columns),
                "Preserve the complete output key.",
                "data.axis",
            )


@dataclass(frozen=True, slots=True)
class BuiltinCalendarData:
    """Captured nominal builtin grain; never an executable calendar rule."""

    unit: Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]
    step: int

    def __post_init__(self) -> None:
        if (
            self.unit not in ("second", "minute", "hour", "day", "week", "month", "quarter", "year")
            or type(self.step) is not int
            or self.step < 1
        ):
            reject(
                "a captured builtin grain", repr(self), "Freeze the nominal grain.", "data.calendar"
            )


@dataclass(frozen=True, slots=True)
class FrozenCalendarData:
    """Small calendar descriptor referencing the required finite directory."""

    definition: BuiltinCalendarData
    directory_name: str
    rule_version: Literal["calendar.v3"] = "calendar.v3"
    direct: FrozenCalendarOrigin | None = None

    def __post_init__(self) -> None:
        if (type(self.definition), self.rule_version) != (BuiltinCalendarData, "calendar.v3") or (
            self.direct is not None and type(self.direct) is not FrozenCalendarOrigin
        ):
            reject(
                "a closed versioned frozen calendar descriptor",
                repr(self),
                "Freeze the natural calendar and its finite directory.",
                "data.calendar",
            )
        require_data_text(self.directory_name, "data.calendar.directory")


@dataclass(frozen=True, slots=True)
class CalendarDataAxis(DataAxis):
    """Saved calendar coordinates retain their timezone without calendar logic."""

    timezone: str
    frozen: FrozenCalendarData | None = field(default=None, kw_only=True)

    @override
    def __post_init__(self) -> None:
        DataAxis.__post_init__(self)
        require_data_text(self.timezone, "data.calendar.timezone")
        if self.frozen is not None and type(self.frozen) is not FrozenCalendarData:
            reject(
                "a frozen calendar descriptor",
                repr(self.frozen),
                "Freeze the calendar.",
                "data.calendar",
            )
        if self.frozen is not None and self.frozen.direct is not None:
            origin = self.frozen.direct.axis
            assert origin.frozen is not None
            if (origin.frozen.definition, origin.timezone, origin.at) != (
                self.frozen.definition,
                self.timezone,
                self.at,
            ) or tuple(c.dtype for c in origin.columns) != tuple(c.dtype for c in self.columns):
                reject(
                    "one compatible frozen direct origin calendar",
                    repr(origin),
                    "Retain the immediate origin grain, timezone and complete key types.",
                    "data.calendar.origin",
                )


@dataclass(frozen=True, slots=True)
class FrozenCalendarOrigin:
    """A direct origin directory and required finite map, without ancestor state."""

    axis: CalendarDataAxis
    mapping_name: str
    kind: Literal["years", "periods"]
    by: int

    def __post_init__(self) -> None:
        if (
            type(self.axis) is not CalendarDataAxis
            or self.axis.frozen is None
            or self.axis.frozen.direct is not None
            or self.kind not in ("years", "periods")
            or type(self.by) is not int
        ):
            reject(
                "a stripped immediate calendar origin and an integer offset rule",
                repr(self),
                "Freeze only the direct input directory and closed shift facts.",
                "data.calendar.origin",
            )
        require_data_text(self.mapping_name, "data.calendar.mapping")


@dataclass(frozen=True, slots=True)
class FrozenIntervalData:
    """A closed finite interval directory reference, without executable rules."""

    directory_name: str
    rule_version: Literal["interval.v1"] = "interval.v1"

    def __post_init__(self) -> None:
        require_data_text(self.directory_name, "data.interval.directory")
        if self.rule_version != "interval.v1":
            reject(
                "the interval.v1 descriptor",
                repr(self),
                "Freeze the interval directory.",
                "data.interval",
            )


@dataclass(frozen=True, slots=True)
class IntervalDataAxis(DataAxis):
    """Saved interval keys with exact directory bounds and a common export timezone."""

    frozen: FrozenIntervalData
    timezone: str

    @override
    def __post_init__(self) -> None:
        DataAxis.__post_init__(self)
        require_data_text(self.timezone, "data.interval.timezone")
        if type(self.frozen) is not FrozenIntervalData:
            reject(
                "a frozen interval descriptor",
                repr(self.frozen),
                "Freeze the interval.",
                "data.interval",
            )


@dataclass(frozen=True, slots=True)
class PairedDataAxis(DataAxis):
    """A single-level paired key with explicit time endpoint descriptors."""

    left: CalendarDataAxis | IntervalDataAxis
    right: CalendarDataAxis | IntervalDataAxis
    mapping_name: str

    @override
    def __post_init__(self) -> None:
        DataAxis.__post_init__(self)
        if type(self.left) not in (CalendarDataAxis, IntervalDataAxis) or type(
            self.left
        ) is not type(self.right):
            reject(
                "two nonpaired time endpoint descriptors of the same kind",
                repr((self.left, self.right)),
                "Retain the two complete time endpoints.",
                "data.paired",
            )
        require_data_text(self.mapping_name, "data.paired.mapping")
        if len({self.token, self.left.token, self.right.token}) != 3:
            reject(
                "distinct paired and endpoint tokens",
                repr((self.token, self.left.token, self.right.token)),
                "Assign a distinct local token to each coordinate descriptor.",
                "data.paired",
            )


@dataclass(frozen=True, slots=True)
class ComparisonValueBinding:
    """One exact retained comparison value, independent of its displayed name."""

    role: Literal["current", "baseline", "absolute", "relative"]
    source_path: tuple[str, ...]
    value_path: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.role not in ("current", "baseline", "absolute", "relative"):
            reject(
                "a closed comparison value role",
                repr(self.role),
                "Preserve the captured role of a directly selected comparison value.",
                "data.comparison.value",
            )
        validate_paths((self.source_path,), location="data.comparison.source")
        validate_paths((self.value_path,), location="data.comparison.value")


@dataclass(frozen=True, slots=True)
class OrdinaryComparisonBinding:
    """A comparison without a time-coordinate relationship."""


@dataclass(frozen=True, slots=True)
class CalendarComparisonBinding:
    """A current output axis and stripped baseline endpoint with a consumed map."""

    current_axis_token: str
    baseline: CalendarDataAxis
    mapping_name: str

    def __post_init__(self) -> None:
        require_data_text(self.current_axis_token, "data.comparison.current")
        require_data_text(self.mapping_name, "data.comparison.mapping")
        if (
            type(self.baseline) is not CalendarDataAxis
            or self.baseline.frozen is None
            or self.baseline.frozen.direct is not None
        ):
            reject(
                "a stripped frozen baseline calendar descriptor",
                repr(self.baseline),
                "Retain the baseline directory and the consumed comparison map only.",
                "data.comparison.calendar",
            )


@dataclass(frozen=True, slots=True)
class IntervalComparisonBinding:
    """A current interval output axis and a frozen baseline endpoint map."""

    current_axis_token: str
    baseline: IntervalDataAxis
    mapping_name: str

    def __post_init__(self) -> None:
        require_data_text(self.current_axis_token, "data.comparison.current")
        require_data_text(self.mapping_name, "data.comparison.mapping")
        if type(self.baseline) is not IntervalDataAxis:
            reject(
                "a frozen baseline interval descriptor",
                repr(self.baseline),
                "Retain the baseline interval directory and consumed comparison map.",
                "data.comparison.interval",
            )


@dataclass(frozen=True, slots=True)
class EndpointPresence:
    """One exact endpoint flag and the inherited flags that require it."""

    path: tuple[str, ...]
    inherited_paths: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        validate_paths((self.path,), location="data.comparison.presence")
        validate_paths(self.inherited_paths, location="data.comparison.presence.children")


@dataclass(frozen=True, slots=True)
class DataComparison:
    """An inert named explanation bound to exact current value paths."""

    alias: str
    values: tuple[ComparisonValueBinding, ...]
    binding: OrdinaryComparisonBinding | CalendarComparisonBinding | IntervalComparisonBinding
    current_presence: EndpointPresence | None
    baseline_presence: EndpointPresence | None
    enclosing_presence: tuple[EndpointPresence, ...] = ()
    namespace: tuple[str, ...] = ()
    contexts: tuple[
        tuple[tuple[str, ...], FrameValueContext | None, FrameValueContext | None], ...
    ] = ()
    current_fixed_axes: tuple[FrameFixedAxis, ...] = ()
    baseline_fixed_axes: tuple[FrameFixedAxis, ...] = ()
    named: bool = True

    def __post_init__(self) -> None:
        validate_paths(((self.alias,),), location="data.comparison.alias")
        if (
            type(self.values) is not tuple
            or any(type(value) is not ComparisonValueBinding for value in self.values)
            or type(self.binding)
            not in (OrdinaryComparisonBinding, CalendarComparisonBinding, IntervalComparisonBinding)
            or (
                self.current_presence is not None
                and type(self.current_presence) is not EndpointPresence
            )
            or (
                self.baseline_presence is not None
                and type(self.baseline_presence) is not EndpointPresence
            )
            or type(self.enclosing_presence) is not tuple
            or any(type(endpoint) is not EndpointPresence for endpoint in self.enclosing_presence)
            or type(self.namespace) is not tuple
        ):
            reject(
                "immutable exact value bindings and one closed comparison endpoint binding",
                repr(self),
                "Retain exact value bindings and endpoint-presence facts.",
                "data.comparison",
            )
        validate_paths(tuple(value.value_path for value in self.values), location="data.comparison")
        if self.namespace:
            validate_paths((self.namespace,), location="data.comparison.namespace")


@dataclass(frozen=True, slots=True)
class DataAlignment:
    """One local alignment with current references, independent of a public input ID."""

    alignment_id: str
    left: FrameAlignmentSide
    right: FrameAlignmentSide


@dataclass(frozen=True, slots=True)
class DataSchema:
    axes: tuple[DataAxis | CalendarDataAxis | IntervalDataAxis | PairedDataAxis, ...]
    values: tuple[DataColumn, ...]
    presence: tuple[DataColumn, ...] = ()
    projection_columns: tuple[DataColumn, ...] = ()
    value_paths: tuple[tuple[str, ...], ...] = ()
    presence_paths: tuple[tuple[str, ...], ...] = ()
    comparisons: tuple[DataComparison, ...] = ()
    fixed_axes: tuple[FrameFixedAxis, ...] = ()
    value_contexts: tuple[tuple[str, FrameValueContext], ...] = ()
    value_time_semantics: tuple[tuple[str, FrameValueTimeSemantics], ...] = ()
    decompositions: tuple[FrameDecomposition, ...] = ()
    alignments: tuple[DataAlignment, ...] = ()
    source_times: tuple[FrameSourceTime, ...] = ()

    def __post_init__(self) -> None:
        validate_source_times(self.source_times)
        if (
            type(self.axes) is not tuple
            or any(
                type(a) not in (DataAxis, CalendarDataAxis, IntervalDataAxis, PairedDataAxis)
                for a in self.axes
            )
            or any(
                type(columns) is not tuple or any(type(c) is not DataColumn for c in columns)
                for columns in (self.values, self.presence, self.projection_columns)
            )
        ):
            reject(
                "immutable typed output columns and axes",
                type(self).__name__,
                "Freeze the output schema.",
                "data.schema",
            )
        tokens = tuple(axis.token for axis in self.axes)
        slots = tuple(column.slot_id for column in self.columns)
        names = tuple(column.name for column in self.values)
        if (
            len(set(tokens)) != len(tokens)
            or len(set(slots)) != len(slots)
            or len(set(names)) != len(names)
        ):
            reject(
                "distinct axis tokens, slots and value names",
                repr((tokens, slots, names)),
                "Assign one identity to each output column.",
                "data.schema",
            )
        if any(
            column.dtype != ScalarDataType("boolean") or column.nullable for column in self.presence
        ):
            reject(
                "nonnullable boolean presence columns",
                repr(self.presence),
                "Store coordinate presence separately from nullable values.",
                "data.presence",
            )
        for paths, columns, role in (
            (self.value_paths, self.values, "values"),
            (self.presence_paths, self.presence, "presence"),
        ):
            validate_paths(paths, location=f"data.{role}")
            if len(paths) != len(columns) or tuple(".".join(path) for path in paths) != tuple(
                c.name for c in columns
            ):
                reject(
                    "one exact ordered path for each named column",
                    repr(paths),
                    "Preserve the current value and presence directories.",
                    f"data.{role}",
                )
        if set(self.value_paths) & set(self.presence_paths):
            reject(
                "disjoint value and presence paths",
                repr(self.value_paths),
                "Assign each path to one current directory.",
                "data.schema",
            )
        validate_comparisons(self)

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return (
            *tuple(c for axis in self.axes for c in axis.columns),
            *self.values,
            *self.presence,
            *self.projection_columns,
        )

    @property
    def key_fields(self) -> tuple[str, ...]:
        return tuple(c.slot_id for axis in self.axes for c in axis.columns)


class _AxisToken(Protocol):
    @property
    def token(self) -> str: ...


class _ComparisonSchema(Protocol):
    @property
    def axes(self) -> tuple[_AxisToken, ...]: ...
    @property
    def comparisons(self) -> tuple[DataComparison, ...]: ...
    @property
    def value_paths(self) -> tuple[tuple[str, ...], ...]: ...
    @property
    def presence_paths(self) -> tuple[tuple[str, ...], ...]: ...


def validate_comparisons(schema: _ComparisonSchema) -> None:
    """Resolve retained comparison values and current axes without name inference."""
    if type(schema.comparisons) is not tuple or any(
        type(fact) is not DataComparison for fact in schema.comparisons
    ):
        reject(
            "immutable closed named comparison facts",
            repr(schema.comparisons),
            "Retain exact typed comparison facts only.",
            "frame.comparisons",
        )
    aliases = tuple((fact.namespace, fact.alias) for fact in schema.comparisons)
    if len(set(aliases)) != len(aliases):
        reject(
            "distinct named comparison aliases",
            repr(aliases),
            "Assign each retained named comparison a distinct alias.",
            "frame.comparisons",
        )
    for fact in schema.comparisons:
        if any(value.value_path not in schema.value_paths for value in fact.values):
            reject(
                "comparison bindings resolving to exact current value paths",
                repr(fact.values),
                "Update comparison bindings when selecting or renaming values.",
                "frame.comparisons",
            )
        for endpoint in (
            fact.current_presence,
            fact.baseline_presence,
            *fact.enclosing_presence,
        ):
            if endpoint is not None and any(
                path not in schema.presence_paths
                for path in (endpoint.path, *endpoint.inherited_paths)
            ):
                reject(
                    "comparison endpoint and inherited flags resolving to exact presence paths",
                    repr(endpoint),
                    "Transport every endpoint-presence reference with its current columns.",
                    "frame.comparisons",
                )
        binding = fact.binding
        if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            axes = tuple(axis for axis in schema.axes if axis.token == binding.current_axis_token)
            expected = (
                CalendarDataAxis
                if isinstance(binding, CalendarComparisonBinding)
                else IntervalDataAxis
            )
            if len(axes) != 1 or type(axes[0]) is not expected:
                reject(
                    "one current output time axis of the comparison endpoint kind",
                    binding.current_axis_token,
                    "Retain the authoritative current time axis or drop its row-level comparison facts.",
                    "frame.comparisons",
                )
            current = axes[0]
            assert isinstance(current, (CalendarDataAxis, IntervalDataAxis))
            if current.token == binding.baseline.token or tuple(
                column.dtype for column in current.columns
            ) != tuple(column.dtype for column in binding.baseline.columns):
                reject(
                    "distinct current and baseline tokens with equal complete key types",
                    repr(binding),
                    "Namespace the frozen baseline descriptor and retain complete compatible keys.",
                    "frame.comparisons",
                )
            if isinstance(binding, CalendarComparisonBinding):
                assert isinstance(current, CalendarDataAxis)
                require_compatible_calendar_axes(current, binding.baseline)


def calendar_failure(expected: str, received: str, *, match: bool = False) -> NoReturn:
    reject(
        expected,
        received,
        "Construct compatible complete time coordinates before matching."
        if match
        else "Retain the exact frozen coordinate descriptors and complete typed attachments.",
        "frame.coordinates.match" if match else "frame.coordinates.data",
    )


def require_frozen_calendar(axis: CalendarDataAxis) -> None:
    if type(axis) is not CalendarDataAxis or axis.frozen is None:
        calendar_failure("a complete frozen calendar descriptor", repr(axis))
    if any(column.nullable for column in axis.columns):
        calendar_failure("nonnullable complete calendar bucket keys", repr(axis.columns))


def require_compatible_calendar_axes(left: CalendarDataAxis, right: CalendarDataAxis) -> None:
    """Validate captured interpretation without reading rows or reconstructing rules."""
    require_frozen_calendar(left)
    require_frozen_calendar(right)
    assert left.frozen is not None and right.frozen is not None
    if (left.frozen.definition, left.frozen.rule_version, left.timezone, left.at) != (
        right.frozen.definition,
        right.frozen.rule_version,
        right.timezone,
        right.at,
    ) or tuple(c.dtype for c in left.columns) != tuple(c.dtype for c in right.columns):
        calendar_failure(
            "compatible frozen calendar interpretations and complete key types",
            repr((left, right)),
            match=True,
        )
