"""Captured axis, clock, and temporal facts independent of source execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from typing import Literal, TypeAlias

from typing_extensions import override

from marivo._kernel.render import Card
from marivo._kernel.timezones import normalize_timezone, same_timezone
from marivo.analysis.model.frame_facts import AxisRole, FrameField, _Fact, _instant, _invalid, _text
from marivo.analysis.model.frozen_keys import FrameKeyScalar, is_frame_key_scalar
from marivo.analysis.model.lineage import SemanticObjectIdentity, union
from marivo.analysis.model.time_authority import (
    CivilDateSourceTime,
    SourceTimeAuthority,
    WallClockSourceTime,
)


class _TimeFact(_Fact):
    @override
    def __post_init__(self) -> None:
        _Fact.__post_init__(self)
        _validate_contributions(self)
        _validate_axis(self)
        _validate_time(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameInstantSourceTime(_TimeFact):
    """Captured instant source interpretation, acquired from FrameSchema.source_times."""

    axis: str
    physical_type: str
    consumption_timezone: str
    kind: Literal["instant"] = "instant"


@dataclass(frozen=True, slots=True, repr=False)
class FrameWallClockSourceTime(_TimeFact):
    """Captured naive source interpretation and declaration origin, acquired from schema."""

    axis: str
    physical_type: str
    consumption_timezone: str
    timezone: str
    origin: Literal["field", "datasource"]
    inherited_from: str | None = None
    kind: Literal["wall_clock"] = "wall_clock"


@dataclass(frozen=True, slots=True, repr=False)
class FrameCivilDateSourceTime(_TimeFact):
    """Captured business-day source interpretation, acquired from FrameSchema.source_times."""

    axis: str
    physical_type: str
    consumption_timezone: str
    timezone: str
    origin: Literal["field", "datasource"]
    kind: Literal["civil_date"] = "civil_date"


FrameSourceTime: TypeAlias = (
    FrameInstantSourceTime | FrameWallClockSourceTime | FrameCivilDateSourceTime
)


def merge_source_times(*groups: tuple[FrameSourceTime, ...]) -> tuple[FrameSourceTime, ...]:
    """Retain distinct complete consumed interpretations in deterministic order."""
    return tuple(
        sorted(
            {item for group in groups for item in group},
            key=lambda item: (
                item.axis,
                item.kind,
                getattr(item, "timezone", ""),
                item.consumption_timezone,
                item.physical_type,
                getattr(item, "origin", ""),
                getattr(item, "inherited_from", None) or "",
            ),
        )
    )


def capture_source_times(
    authorities: tuple[SourceTimeAuthority, ...], *, consumption_timezone: str
) -> tuple[FrameSourceTime, ...]:
    """Copy resolved source authority as inert scalars at the actual consumption boundary."""
    consumption_timezone = normalize_timezone(consumption_timezone).name

    result: list[FrameSourceTime] = []
    for authority in authorities:
        if isinstance(authority, WallClockSourceTime):
            result.append(
                FrameWallClockSourceTime(
                    authority.axis,
                    authority.physical_type,
                    consumption_timezone,
                    authority.timezone,
                    authority.origin,
                    authority.inherited_from,
                )
            )
        elif isinstance(authority, CivilDateSourceTime):
            result.append(
                FrameCivilDateSourceTime(
                    authority.axis,
                    authority.physical_type,
                    consumption_timezone,
                    authority.timezone,
                    authority.origin,
                )
            )
        else:
            result.append(
                FrameInstantSourceTime(
                    authority.axis,
                    authority.physical_type,
                    consumption_timezone,
                )
            )
    return merge_source_times(tuple(result))


def validate_source_times(values: tuple[FrameSourceTime, ...]) -> None:
    """Require exact immutable inert variants without resolving a source."""
    if type(values) is not tuple or any(
        type(value)
        not in (FrameInstantSourceTime, FrameWallClockSourceTime, FrameCivilDateSourceTime)
        for value in values
    ):
        _invalid("an immutable tuple of closed source-time interpretation facts")
    if merge_source_times(values) != values:
        _invalid("distinct source-time interpretation facts in deterministic order")


def source_time_summary(value: FrameSourceTime) -> str:
    """Describe one consumed field, including the civil-day assignment limitation."""

    text = f"{value.axis}: {value.kind}"
    if isinstance(value, FrameInstantSourceTime):
        return f"{text} in {value.consumption_timezone}"
    if isinstance(value, (FrameWallClockSourceTime, FrameCivilDateSourceTime)):
        text += f" {value.timezone} ({value.origin})"
    if isinstance(value, FrameWallClockSourceTime) and value.inherited_from is not None:
        text += f"; inherited from {value.inherited_from}"
    text += f"; report {value.consumption_timezone}"
    if isinstance(value, FrameCivilDateSourceTime) and not same_timezone(
        value.timezone, value.consumption_timezone
    ):
        text += "; assigned by business-day start; cannot rebuild event-day totals"
    return text


@dataclass(frozen=True, slots=True, repr=False)
class FrameBuiltinGrain(_TimeFact):
    """Captured nominal builtin grain, acquired from an axis or window."""

    unit: Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]
    step: int
    kind: Literal["builtin"] = field(default="builtin")


FrameGrain: TypeAlias = FrameBuiltinGrain


@dataclass(frozen=True, slots=True, repr=False)
class FrameCalendarEndpoint(_TimeFact):
    """Captured calendar endpoint description without an operational axis handle."""

    name: str
    at: datetime | None
    timezone: str
    grain: FrameGrain = field(
        metadata={
            "meaning": "Captured nominal grain; see marivo.help('analysis.frame_calendar_endpoint') for builtin and named variants."
        }
    )
    kind: Literal["calendar"] = field(default="calendar")


@dataclass(frozen=True, slots=True, repr=False)
class FrameIntervalEndpoint(_TimeFact):
    """Captured interval endpoint description."""

    name: str
    at: datetime | None
    timezone: str
    kind: Literal["interval"] = field(default="interval")


FrameTimeEndpoint: TypeAlias = FrameCalendarEndpoint | FrameIntervalEndpoint


@dataclass(frozen=True, slots=True, repr=False)
class FrameRowAttributeTime(_TimeFact):
    """Per-fact attribute selection, acquired from an ordinary Frame axis's at field."""

    field: SemanticObjectIdentity
    kind: Literal["row"] = "row"

    @override
    def __repr__(self) -> str:
        return f"<FrameRowAttributeTime kind=row field={self.field.path[:120]!r}; use .show()>"

    @override
    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Print the captured control-field identity.

        Args: max_output_bytes: Optional display budget. Returns: None.

        Example: ``frame.schema.axes[0].at.show()`` after checking its row kind.
        Constraints: No source, Catalog or version matching is accessed.
        """
        print(self._card().render(max_output_bytes=max_output_bytes))


@dataclass(frozen=True, slots=True, repr=False)
class FrameAxisSchema(_TimeFact):
    """A complete current logical axis, acquired from FrameSchema.axes."""

    token: str
    name: str
    role: AxisRole
    keys: tuple[FrameField, ...]
    at: datetime | FrameRowAttributeTime | None = field(default=None, kw_only=True)

    @override
    def render(self, *, max_output_bytes: int | None = 8192) -> str:
        """Return bounded captured axis facts without reading payloads."""
        return self._card().render(max_output_bytes=max_output_bytes)

    @override
    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Print captured axis facts. Args: max_output_bytes: Optional display budget.

        Returns: None. Example: ``frame.schema.axes[0].show()``.
        Constraints: Metadata only; complete fields remain available in typed attributes.
        """
        print(self.render(max_output_bytes=max_output_bytes))


@dataclass(frozen=True, slots=True, repr=False)
class FrameOrdinaryAxisSchema(FrameAxisSchema):
    """Complete field or identity key description."""


@dataclass(frozen=True, slots=True, repr=False)
class FrameCalendarAxisSchema(FrameAxisSchema):
    """Calendar key, timezone and captured nominal grain."""

    timezone: str
    grain: FrameGrain = field(
        metadata={
            "meaning": "Captured nominal grain; see marivo.help('analysis.frame_calendar_axis_schema') for builtin and named variants."
        }
    )


@dataclass(frozen=True, slots=True, repr=False)
class FrameIntervalAxisSchema(FrameAxisSchema):
    """Interval key and common export timezone."""

    timezone: str


@dataclass(frozen=True, slots=True, repr=False)
class FramePairedAxisSchema(FrameAxisSchema):
    """One complete paired key with two explicit time endpoint descriptions."""

    left: FrameTimeEndpoint
    right: FrameTimeEndpoint


@dataclass(frozen=True, slots=True, repr=False)
class FrameFixedAxis(_TimeFact):
    """A removed ordinary axis, its exact ordered key and captured semantic origins."""

    axis: FrameOrdinaryAxisSchema
    key: tuple[FrameKeyScalar, ...]
    origins: tuple[SemanticObjectIdentity, ...]

    @override
    def _identity_detail(self) -> str:
        return f"axis={self.axis.token[:40]}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameAxisTime(_TimeFact):
    """Observation time resolved by a current time axis endpoint."""

    axis_token: str
    endpoint: Literal["self", "left", "right"] = "self"
    kind: Literal["axis"] = field(default="axis")


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparedTime(_TimeFact):
    """Observation time resolved by a current comparison endpoint."""

    comparison_id: str
    endpoint: Literal["current", "baseline"]
    kind: Literal["compared"] = field(default="compared")


@dataclass(frozen=True, slots=True, repr=False)
class FrameFixedTime(_TimeFact):
    """Exact UTC scalar observation bounds and boundary timezone."""

    start: datetime
    end: datetime
    timezone: str
    kind: Literal["fixed"] = field(default="fixed")


@dataclass(frozen=True, slots=True, repr=False)
class FrameAllHistoryWindow(_TimeFact):
    """An explicitly attached all-history window."""

    kind: Literal["all_history"] = field(default="all_history")


@dataclass(frozen=True, slots=True, repr=False)
class FrameSinceWindow(_TimeFact):
    """An explicitly attached exact lower bound."""

    start: datetime
    kind: Literal["since"] = field(default="since")


@dataclass(frozen=True, slots=True, repr=False)
class FrameToDateWindow(_TimeFact):
    """An explicitly attached reset grain."""

    grain: FrameGrain = field(
        metadata={
            "meaning": "Captured reset grain; see marivo.help('analysis.frame_to_date_window') for builtin and named variants."
        }
    )
    kind: Literal["to_date"] = field(default="to_date")


@dataclass(frozen=True, slots=True, repr=False)
class FrameTrailingDurationWindow(_TimeFact):
    """An explicitly attached elapsed duration."""

    duration: timedelta
    kind: Literal["trailing_duration"] = field(default="trailing_duration")


@dataclass(frozen=True, slots=True, repr=False)
class FrameTrailingPeriodsWindow(_TimeFact):
    """An explicitly attached number of output calendar periods."""

    count: int
    kind: Literal["trailing_periods"] = field(default="trailing_periods")


FrameObservationWindow: TypeAlias = (
    FrameAllHistoryWindow
    | FrameSinceWindow
    | FrameToDateWindow
    | FrameTrailingDurationWindow
    | FrameTrailingPeriodsWindow
)


@dataclass(frozen=True, slots=True, repr=False)
class FrameReportWindowEndpoint(_TimeFact):
    """A Metric window evaluated at the captured report endpoints."""

    kind: Literal["report"] = dataclass_field(default="report")


@dataclass(frozen=True, slots=True, repr=False)
class FrameContributionSampleWindowEndpoint(_TimeFact):
    """A window evaluated at actual contribution-clock sample endpoints."""

    layer: int
    grain: FrameBuiltinGrain
    timezone: str
    kind: Literal["contribution_sample"] = dataclass_field(default="contribution_sample")


@dataclass(frozen=True, slots=True, repr=False)
class FrameFieldSampleWindowEndpoint(_TimeFact):
    """A window evaluated at explicit-field sample endpoints, retaining its cohort."""

    layer: int
    field: SemanticObjectIdentity
    grain: FrameBuiltinGrain
    timezone: str
    kind: Literal["field_sample"] = dataclass_field(default="field_sample")


FrameWindowEndpoint: TypeAlias = (
    FrameReportWindowEndpoint
    | FrameContributionSampleWindowEndpoint
    | FrameFieldSampleWindowEndpoint
)


@dataclass(frozen=True, slots=True, repr=False)
class FrameClockedContribution(_TimeFact):
    """Captured clock and report effects of one original contribution occurrence."""

    output: str
    occurrence: tuple[str, ...]
    metric: SemanticObjectIdentity | None
    root: SemanticObjectIdentity | None
    field: SemanticObjectIdentity
    path: tuple[str, ...]
    anchor: datetime | None
    source: Literal["explicit", "entity_default"]
    report: FrameFixedTime | None
    report_effect: Literal["none", "range", "buckets"]
    window: FrameObservationWindow | None
    samples: tuple[str, ...]
    report_axis: FrameTimeEndpoint | None = None
    report_cells: tuple[tuple[datetime, datetime, datetime, datetime], ...] = ()
    window_endpoint: FrameWindowEndpoint | None = None
    kind: Literal["clocked"] = dataclass_field(default="clocked")


@dataclass(frozen=True, slots=True, repr=False)
class FrameIndependentContribution(_TimeFact):
    """Captured report-independent contribution; source predicates remain applicable."""

    output: str
    occurrence: tuple[str, ...]
    metric: SemanticObjectIdentity | None
    root: SemanticObjectIdentity | None
    source: Literal["explicit_none", "no_entity_default", "fixed_member_count"]
    report: FrameFixedTime | None
    report_effect: Literal["none", "range_ignored", "broadcast", "coordinate_match"]
    window: FrameObservationWindow | None
    samples: tuple[str, ...]
    report_axis: FrameTimeEndpoint | None = None
    report_cells: tuple[tuple[datetime, datetime, datetime, datetime], ...] = ()
    window_endpoint: FrameWindowEndpoint | None = None
    kind: Literal["independent"] = field(default="independent")


FrameTimeContribution: TypeAlias = FrameClockedContribution | FrameIndependentContribution


class _TimeSemantics(_TimeFact):
    contributions: tuple[FrameClockedContribution | FrameIndependentContribution, ...]

    @override
    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        card.table(
            ("branch", "clock", "report", "window", "endpoint"),
            [
                (
                    " / ".join(item.occurrence) or item.output,
                    item.field.path
                    if isinstance(item, FrameClockedContribution)
                    else "independent: " + item.source,
                    item.report_effect,
                    repr(item.window),
                    repr(item.window_endpoint),
                )
                for item in self.contributions
            ],
            row_count=len(self.contributions),
            bounded_row_limit=6,
            recovery="read .contributions",
        )

        return card


@dataclass(frozen=True, slots=True, repr=False)
class FrameObservedTimeSemantics(_TimeSemantics):
    """Original observed contribution clocks and effects, without source authority."""

    contributions: tuple[FrameTimeContribution, ...]
    kind: Literal["observed"] = field(default="observed")


@dataclass(frozen=True, slots=True, repr=False)
class FrameDerivedTimeSemantics(_TimeSemantics):
    """Input contribution snapshots of a fixed-data derivation, not a new source observation."""

    contributions: tuple[FrameTimeContribution, ...]
    kind: Literal["derived"] = field(default="derived")


FrameValueTimeSemantics: TypeAlias = FrameObservedTimeSemantics | FrameDerivedTimeSemantics


@dataclass(frozen=True, slots=True, repr=False)
class FrameObservationContext(_TimeFact):
    """Direct observation subject, time basis and explicitly attached window."""

    subject: SemanticObjectIdentity | None
    time: FrameAxisTime | FrameComparedTime | FrameFixedTime | None = field(
        metadata={
            "meaning": "Captured time basis, if available. See marivo.help('analysis.frame_observation_context') for the time variants."
        }
    )
    window: FrameObservationWindow | None = field(
        metadata={
            "meaning": "Explicitly attached window, if available. See marivo.help('analysis.frame_observation_context') for the window variants."
        }
    )
    kind: Literal["observation"] = field(default="observation")

    @override
    def _identity_detail(self) -> str | None:
        return None if self.subject is None else f"subject={self.subject.path[:64]}"


def _validate_contributions(value: _TimeFact) -> None:
    if isinstance(value, (FrameObservedTimeSemantics, FrameDerivedTimeSemantics)) and (
        type(value.contributions) is not tuple
        or any(
            type(item) not in (FrameClockedContribution, FrameIndependentContribution)
            for item in value.contributions
        )
    ):
        _invalid("complete immutable contribution time facts")
    if isinstance(value, (FrameContributionSampleWindowEndpoint, FrameFieldSampleWindowEndpoint)):
        if (
            type(value.layer) is not int
            or value.layer < 1
            or type(value.grain) is not FrameBuiltinGrain
        ):
            _invalid("a positive sample layer and builtin endpoint Grain")
        _text(value.timezone)
        if (
            isinstance(value, FrameFieldSampleWindowEndpoint)
            and type(value.field) is not SemanticObjectIdentity
        ):
            _invalid("an explicit captured sample field")
    if isinstance(value, (FrameClockedContribution, FrameIndependentContribution)):
        _validate_contribution(value)


def _validate_contribution(value: FrameClockedContribution | FrameIndependentContribution) -> None:
    if value.window_endpoint is not None and type(value.window_endpoint) not in (
        FrameReportWindowEndpoint,
        FrameContributionSampleWindowEndpoint,
        FrameFieldSampleWindowEndpoint,
    ):
        _invalid("a closed report or sample window endpoint")
    if (value.window is None) != (value.window_endpoint is None):
        _invalid("a window endpoint exactly when a window is captured")
    _text(value.output)
    if type(value.occurrence) is not tuple or any(
        type(item) is not str for item in value.occurrence
    ):
        _invalid("an immutable contribution occurrence path")
    if value.report is not None and type(value.report) is not FrameFixedTime:
        _invalid("a captured report selection")
    if value.report_axis is not None and type(value.report_axis) not in (
        FrameCalendarEndpoint,
        FrameIntervalEndpoint,
    ):
        _invalid("a captured report axis description")
    if type(value.report_cells) is not tuple:
        _invalid("immutable captured report cells")
    for cell in value.report_cells:
        if type(cell) is not tuple or len(cell) != 4:
            _invalid("nominal and selected report bounds")
        for bound in cell:
            _instant(bound)

    if isinstance(value, FrameClockedContribution):
        _instant(value.anchor)
        if (
            type(value.field) is not SemanticObjectIdentity
            or value.source not in ("explicit", "entity_default")
            or value.report_effect not in ("none", "range", "buckets")
        ):
            _invalid("a captured contribution clock and report effect")
    elif value.source not in (
        "explicit_none",
        "no_entity_default",
        "fixed_member_count",
    ) or value.report_effect not in ("none", "range_ignored", "broadcast", "coordinate_match"):
        _invalid("a report-independent contribution and its report effect")


def _validate_axis_schema(value: FrameAxisSchema) -> None:
    _text(value.token)
    _text(value.name)
    if not isinstance(value.at, FrameRowAttributeTime):
        _instant(value.at)
    elif not isinstance(value, FrameOrdinaryAxisSchema):
        _invalid("row attribute time on an ordinary classification axis")
    roles = {
        FrameOrdinaryAxisSchema: ("field", "identity"),
        FrameCalendarAxisSchema: ("calendar",),
        FrameIntervalAxisSchema: ("interval",),
        FramePairedAxisSchema: ("paired",),
    }
    if (
        value.role not in roles.get(type(value), ())
        or type(value.keys) is not tuple
        or not value.keys
        or any(type(k) is not FrameField for k in value.keys)
    ):
        _invalid("a closed axis variant and its complete immutable key")
    if isinstance(value, (FrameCalendarAxisSchema, FrameIntervalAxisSchema)):
        _text(value.timezone)


def _validate_axis(value: _TimeFact) -> None:
    if isinstance(
        value, (FrameInstantSourceTime, FrameWallClockSourceTime, FrameCivilDateSourceTime)
    ):
        _text(value.axis)
        _text(value.physical_type)
        for zone in (
            value.consumption_timezone,
            *(
                (value.timezone,)
                if isinstance(value, (FrameWallClockSourceTime, FrameCivilDateSourceTime))
                else ()
            ),
        ):
            # Producers validate and normalize declarations. These are captured
            # identities, just like saved axis timezones below; reopening must
            # not reinterpret them through the reader's current timezone data.
            _text(zone)
        if isinstance(
            value, (FrameWallClockSourceTime, FrameCivilDateSourceTime)
        ) and value.origin not in ("field", "datasource"):
            _invalid("a field or datasource timezone declaration origin")
        if isinstance(value, FrameWallClockSourceTime) and value.inherited_from is not None:
            _text(value.inherited_from)
    elif isinstance(value, FrameAxisSchema):
        _validate_axis_schema(value)
    elif isinstance(value, FrameRowAttributeTime):
        if type(value.field) is not SemanticObjectIdentity or value.field.kind != "time_dimension":
            _invalid("a captured row-time TimeDimension identity")
    elif isinstance(value, (FrameCalendarEndpoint, FrameIntervalEndpoint)):
        _text(value.name)
        _text(value.timezone)
        _instant(value.at)
    elif isinstance(value, FrameBuiltinGrain):
        if (
            value.unit
            not in ("second", "minute", "hour", "day", "week", "month", "quarter", "year")
            or type(value.step) is not int
            or value.step < 1
        ):
            _invalid("a positive builtin grain")
    if isinstance(
        value, (FrameCalendarAxisSchema, FrameCalendarEndpoint, FrameToDateWindow)
    ) and type(value.grain) not in (FrameBuiltinGrain,):
        _invalid("one closed captured nominal grain")
    if isinstance(value, FramePairedAxisSchema) and (
        type(value.left) not in (FrameCalendarEndpoint, FrameIntervalEndpoint)
        or type(value.left) is not type(value.right)
    ):
        _invalid("two closed paired time endpoints of the same kind")


def _validate_fixed_axis(value: FrameFixedAxis) -> None:
    if (
        type(value.axis) is not FrameOrdinaryAxisSchema
        or type(value.key) is not tuple
        or len(value.key) != len(value.axis.keys)
        or value.origins != union(value.origins)
    ):
        _invalid("a complete ordered fixed ordinary key and captured origins")
    if any(not is_frame_key_scalar(item) for item in value.key):
        _invalid("typed exact fixed key scalars")


def _validate_time(value: _TimeFact) -> None:
    if isinstance(value, FrameFixedAxis):
        _validate_fixed_axis(value)
    elif isinstance(value, FrameAxisTime):
        _text(value.axis_token)
        if value.endpoint not in ("self", "left", "right"):
            _invalid("a closed current time endpoint")
    elif isinstance(value, FrameComparedTime):
        _text(value.comparison_id)
        if value.endpoint not in ("current", "baseline"):
            _invalid("a closed comparison endpoint")
    elif isinstance(value, FrameFixedTime):
        _instant(value.start)
        _instant(value.end)
        _text(value.timezone)
        if value.start >= value.end:
            _invalid("a finite increasing scalar observation range")
    elif isinstance(value, FrameSinceWindow):
        _instant(value.start)
    elif isinstance(value, FrameTrailingDurationWindow):
        if type(value.duration) is not timedelta or value.duration <= timedelta(0):
            _invalid("a positive elapsed window duration")
    elif isinstance(value, FrameTrailingPeriodsWindow) and (
        type(value.count) is not int or value.count < 1
    ):
        _invalid("a positive number of trailing periods")
    if isinstance(value, FrameObservationContext):
        _validate_observation_context(value)


def _validate_observation_context(value: FrameObservationContext) -> None:
    if value.subject is not None and (
        type(value.subject) is not SemanticObjectIdentity or value.subject.kind != "metric"
    ):
        _invalid("a directly observed Metric identity or no named subject")
    if value.time is not None and type(value.time) not in (
        FrameAxisTime,
        FrameComparedTime,
        FrameFixedTime,
    ):
        _invalid("a closed captured observation time basis")
    if value.window is not None and type(value.window) not in (
        FrameAllHistoryWindow,
        FrameSinceWindow,
        FrameToDateWindow,
        FrameTrailingDurationWindow,
        FrameTrailingPeriodsWindow,
    ):
        _invalid("a closed captured explicit window")
    if value.time is None and value.window is not None:
        _invalid("a captured output time basis for an attached window")
