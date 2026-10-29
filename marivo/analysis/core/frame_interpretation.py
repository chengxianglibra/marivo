"""Closed inert facts describing the current Frame result, without execution authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, TypeAlias

import pyarrow as pa

from marivo.analysis.core.data_types import DataType, require_data_text
from marivo.analysis.core.lineage import SemanticObjectIdentity
from marivo.analysis.core.model import reject
from marivo.render import Card, RenderableResult

ValueKind: TypeAlias = Literal[
    "numeric", "boolean", "string", "date", "timestamp", "duration", "unresolved"
]
AxisRole: TypeAlias = Literal["field", "identity", "calendar", "interval", "paired"]
FrameKeyScalar: TypeAlias = (
    bool
    | str
    | int
    | float
    | Decimal
    | pa.Date32Scalar
    | pa.TimestampScalar
    | pa.DurationScalar
    | None
)


@dataclass(frozen=True, slots=True, repr=False)
class UnresolvedDataType:
    """Captured value kind whose physical type is unresolved; acquired from pending schema."""

    kind: ValueKind

    def __post_init__(self) -> None:
        if self.kind not in (
            "numeric",
            "boolean",
            "string",
            "date",
            "timestamp",
            "duration",
            "unresolved",
        ):
            _invalid("an explicit pending value kind")

    def __repr__(self) -> str:
        return f"<UnresolvedDataType kind={self.kind}; use .show()>"

    def show(self) -> None:
        """Print the known kind. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].dtype.show()``.
        Constraints: Does not execute or infer a type from data.
        """
        print(repr(self))


FrameDataType: TypeAlias = DataType | UnresolvedDataType


def _invalid(received: str) -> None:
    reject(
        "a captured immutable Frame structure",
        received,
        "Read schema from the exact Frame whose fields you need.",
        "frame.schema",
        canonical_id="analysis_frame.schema",
    )


@dataclass(frozen=True, slots=True, repr=False)
class FrameField:
    """One exact-input slot, acquired from a FrameSchema directory or complete axis key."""

    slot_id: str
    name: str
    dtype: FrameDataType
    nullable: bool | None
    unit: str | None

    def __post_init__(self) -> None:
        require_data_text(self.slot_id, "frame.schema.slot_id")
        require_data_text(self.name, "frame.schema.name")
        if self.nullable is not None and type(self.nullable) is not bool:
            _invalid("nullability must be a boolean or explicitly unresolved")
        if self.nullable is None and not isinstance(self.dtype, UnresolvedDataType):
            _invalid("only unresolved pending fields may have unknown nullability")
        if self.unit is not None:
            require_data_text(self.unit, "frame.schema.unit")

    def __repr__(self) -> str:
        return f"<FrameField slot={self.slot_id[:64]!r} name={self.name[:64]!r}; use .show()>"

    def show(self) -> None:
        """Print captured slot facts. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].show()``.
        Constraints: Names and slots belong only to this exact input.
        """
        print(repr(self))
        unit = None if self.unit is None else self.unit[:160]
        print(f"dtype={self.dtype!r} nullable={self.nullable} unit={unit!r}")


class _Fact(RenderableResult):
    def __post_init__(self) -> None:
        _validate_fact(self)

    def _repr_identity(self) -> str:
        from dataclasses import fields, is_dataclass

        assert is_dataclass(self)
        parts = [type(self).__name__]
        for item in fields(self):
            if item.name in (
                "kind",
                "role",
                "token",
                "axis_token",
                "name",
                "comparison_id",
                "decomposition_id",
                "statistic",
                "row_scope",
                "unit",
                "step",
                "count",
                "endpoint",
                "group_id",
                "start",
                "end",
                "timezone",
                "level",
            ):
                parts.append(f"{item.name}={str(getattr(self, item.name))[:40]}")
            elif item.name in ("keys", "groups", "outputs", "values", "key"):
                parts.append(f"{item.name}={len(getattr(self, item.name))}")
        if isinstance(self, FrameFixedAxis):
            parts.append(f"axis={self.axis.token[:40]}")
        elif isinstance(self, FrameFixedGroup):
            parts.append(f"axis={self.fixed.axis.token[:40]}")
        elif isinstance(self, FrameObservationContext) and self.subject is not None:
            parts.append(f"subject={self.subject.path[:64]}")
        elif isinstance(self, FrameNamedGrain):
            parts.append(f"ref={self.ref.path[:64]}")
        return " ".join(parts)[:220]

    def _card(self) -> Card:
        from dataclasses import fields, is_dataclass

        assert is_dataclass(self)
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, tuple):
                summary = ", ".join(repr(v)[:100] for v in value[:12])
                if len(value) > 12:
                    summary += f", ... {len(value) - 12} omitted; read .{item.name}"
            else:
                summary = repr(value)[:240]
            card.field(item.name, summary)
        return card


@dataclass(frozen=True, slots=True, repr=False)
class FrameBuiltinGrain(_Fact):
    """Captured nominal builtin grain, acquired from an axis or window."""

    unit: Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]
    step: int
    kind: Literal["builtin"] = field(default="builtin")


@dataclass(frozen=True, slots=True, repr=False)
class FrameNamedGrain(_Fact):
    """Captured named calendar identity and level; details require explicit Catalog lookup."""

    ref: SemanticObjectIdentity
    level: str
    kind: Literal["named"] = field(default="named")


FrameGrain: TypeAlias = FrameBuiltinGrain | FrameNamedGrain


@dataclass(frozen=True, slots=True, repr=False)
class FrameCalendarEndpoint(_Fact):
    """Captured calendar endpoint description without an operational axis handle."""

    name: str
    at: datetime | None
    timezone: str
    grain: FrameGrain
    kind: Literal["calendar"] = field(default="calendar")


@dataclass(frozen=True, slots=True, repr=False)
class FrameIntervalEndpoint(_Fact):
    """Captured interval endpoint description."""

    name: str
    at: datetime | None
    timezone: str
    kind: Literal["interval"] = field(default="interval")


FrameTimeEndpoint: TypeAlias = FrameCalendarEndpoint | FrameIntervalEndpoint


@dataclass(frozen=True, slots=True, repr=False)
class FrameAxisSchema(_Fact):
    """A complete current logical axis, acquired from FrameSchema.axes."""

    token: str
    name: str
    role: AxisRole
    keys: tuple[FrameField, ...]
    at: datetime | None = field(default=None, kw_only=True)

    def render(self, *, max_output_bytes: int | None = 8192) -> str:
        """Return bounded captured axis facts without reading payloads."""
        return self._card().render(max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Print captured axis facts. Args: max_output_bytes: Optional display budget.

        Returns: None. Example: ``frame.schema.axes[0].show()``.
        Constraints: Metadata only; complete fields remain available in typed attributes.
        """
        print(self.render(max_output_bytes=max_output_bytes))


@dataclass(frozen=True, slots=True, repr=False)
class FrameOrdinaryAxisSchema(FrameAxisSchema):
    """Complete field or identity key description."""

    pass


@dataclass(frozen=True, slots=True, repr=False)
class FrameCalendarAxisSchema(FrameAxisSchema):
    """Calendar key, timezone and captured nominal grain."""

    timezone: str
    grain: FrameGrain


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
class FrameFixedAxis(_Fact):
    """A removed ordinary axis, its exact ordered key and captured semantic origins."""

    axis: FrameOrdinaryAxisSchema
    key: tuple[FrameKeyScalar, ...]
    origins: tuple[SemanticObjectIdentity, ...]


@dataclass(frozen=True, slots=True, repr=False)
class FrameAxisTime(_Fact):
    """Observation time resolved by a current time axis endpoint."""

    axis_token: str
    endpoint: Literal["self", "left", "right"] = "self"
    kind: Literal["axis"] = field(default="axis")


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparedTime(_Fact):
    """Observation time resolved by a current comparison endpoint."""

    comparison_id: str
    endpoint: Literal["current", "baseline"]
    kind: Literal["compared"] = field(default="compared")


@dataclass(frozen=True, slots=True, repr=False)
class FrameFixedTime(_Fact):
    """Exact UTC scalar observation bounds and boundary timezone."""

    start: datetime
    end: datetime
    timezone: str
    kind: Literal["fixed"] = field(default="fixed")


@dataclass(frozen=True, slots=True, repr=False)
class FrameAllHistoryWindow(_Fact):
    """An explicitly attached all-history window."""

    kind: Literal["all_history"] = field(default="all_history")


@dataclass(frozen=True, slots=True, repr=False)
class FrameSinceWindow(_Fact):
    """An explicitly attached exact lower bound."""

    start: datetime
    kind: Literal["since"] = field(default="since")


@dataclass(frozen=True, slots=True, repr=False)
class FrameToDateWindow(_Fact):
    """An explicitly attached reset grain."""

    grain: FrameGrain
    kind: Literal["to_date"] = field(default="to_date")


@dataclass(frozen=True, slots=True, repr=False)
class FrameTrailingDurationWindow(_Fact):
    """An explicitly attached elapsed duration."""

    duration: timedelta
    kind: Literal["trailing_duration"] = field(default="trailing_duration")


@dataclass(frozen=True, slots=True, repr=False)
class FrameTrailingPeriodsWindow(_Fact):
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
class FrameObservationContext(_Fact):
    """Direct observation subject, time basis and explicitly attached window."""

    subject: SemanticObjectIdentity | None
    time: FrameAxisTime | FrameComparedTime | FrameFixedTime | None
    window: FrameObservationWindow | None
    kind: Literal["observation"] = field(default="observation")


@dataclass(frozen=True, slots=True, repr=False)
class FrameAxisGroup(_Fact):
    """One complete current parent axis."""

    axis_token: str
    kind: Literal["axis"] = field(default="axis")


@dataclass(frozen=True, slots=True, repr=False)
class FrameFixedGroup(_Fact):
    """One fixed parent coordinate, retaining its exact removed axis description."""

    fixed: FrameFixedAxis
    kind: Literal["fixed"] = field(default="fixed")


FrameGroup: TypeAlias = FrameAxisGroup | FrameFixedGroup


@dataclass(frozen=True, slots=True, repr=False)
class FrameStatisticContext(_Fact):
    """Current-row statistic and its complete parent groups."""

    statistic: Literal["sum", "mean", "row_count", "pearson", "spearman", "pair_count"]
    groups: tuple[FrameGroup, ...]
    row_scope: Literal["complete", "restricted"] = "complete"
    kind: Literal["statistic"] = field(default="statistic")


FrameValueContext: TypeAlias = FrameObservationContext | FrameStatisticContext


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparisonValue(_Fact):
    """One current value's comparison role; direct copies may share a role."""

    role: Literal["current", "baseline", "absolute", "relative"]
    name: str


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparisonGroup(_Fact):
    """One compared quantity and independently captured endpoint contexts."""

    group_id: str
    name: str
    values: tuple[FrameComparisonValue, ...]
    current_context: FrameValueContext | None
    baseline_context: FrameValueContext | None


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparisonPresence(_Fact):
    """Current endpoint flags and enclosing flags, applied before endpoint flags."""

    current: str
    baseline: str
    enclosing: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class FrameOrdinaryComparisonBinding(_Fact):
    """Comparison without a time-coordinate relationship."""

    kind: Literal["ordinary"] = field(default="ordinary")


@dataclass(frozen=True, slots=True, repr=False)
class FrameCalendarComparisonBinding(_Fact):
    """Current calendar endpoint and captured baseline description."""

    current_axis_token: str
    current_endpoint: Literal["self", "left", "right"]
    baseline: FrameCalendarEndpoint
    kind: Literal["calendar"] = field(default="calendar")


@dataclass(frozen=True, slots=True, repr=False)
class FrameIntervalComparisonBinding(_Fact):
    """Current interval endpoint and captured baseline description."""

    current_axis_token: str
    current_endpoint: Literal["self", "left", "right"]
    baseline: FrameIntervalEndpoint
    kind: Literal["interval"] = field(default="interval")


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparison(_Fact):
    """Current-input comparison with quantity roles and independent endpoint choices."""

    input_id: str
    comparison_id: str
    alias: str | None
    groups: tuple[FrameComparisonGroup, ...]
    binding: (
        FrameOrdinaryComparisonBinding
        | FrameCalendarComparisonBinding
        | FrameIntervalComparisonBinding
    )
    presence: FrameComparisonPresence | None
    current_fixed_axes: tuple[FrameFixedAxis, ...]
    baseline_fixed_axes: tuple[FrameFixedAxis, ...]


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelTarget(_Fact):
    """Captured level target and its local current meaning."""

    name: str
    context: FrameValueContext | None
    kind: Literal["level"] = field(default="level")


@dataclass(frozen=True, slots=True, repr=False)
class FrameChangeTarget(_Fact):
    """A change target bound to one current comparison quantity."""

    kind: Literal["absolute", "relative"]
    comparison_id: str
    group_id: str


FrameDecompositionRole: TypeAlias = Literal[
    "contribution",
    "total",
    "observed_total",
    "share",
    "value",
    "current",
    "baseline",
    "absolute",
    "numerator",
    "denominator",
    "valid_count",
    "current_present",
    "baseline_present",
    "value_present",
    "numerator_current",
    "numerator_baseline",
    "denominator_current",
    "denominator_baseline",
    "valid_count_current",
    "valid_count_baseline",
    "effect_within",
    "effect_mix",
    "effect_entry_exit",
    "effect_additive",
    "contribution_std_error",
]


@dataclass(frozen=True, slots=True, repr=False)
class FrameDecompositionField(_Fact):
    """One current value or presence field and its actual decomposition role."""

    field_kind: Literal["value", "presence"]
    role: FrameDecompositionRole
    name: str


@dataclass(frozen=True, slots=True, repr=False)
class FrameDecomposition(_Fact):
    """Current decomposition target, parent groups, retained roles and row scope."""

    decomposition_id: str
    target: FrameLevelTarget | FrameChangeTarget
    groups: tuple[FrameGroup, ...]
    row_scope: Literal["complete", "restricted"]
    outputs: tuple[FrameDecompositionField, ...]


def restrict_context(context: FrameValueContext) -> FrameValueContext:
    """Restrict represented statistics without recomputing any numeric value."""
    from dataclasses import replace

    return (
        replace(context, row_scope="restricted")
        if isinstance(context, FrameStatisticContext)
        else context
    )


def remap_context(
    context: FrameValueContext | None,
    tokens: dict[str, tuple[str, Literal["self", "left", "right"]]],
    comparisons: dict[str, str] | None = None,
) -> FrameValueContext | None:
    """Keep only explanations whose current references have an exact local binding."""
    from dataclasses import replace

    if isinstance(context, FrameStatisticContext):
        if any(
            isinstance(g, FrameAxisGroup) and g.axis_token not in tokens for g in context.groups
        ):
            return None
        groups = tuple(
            FrameAxisGroup(tokens[g.axis_token][0]) if isinstance(g, FrameAxisGroup) else g
            for g in context.groups
        )
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


def unary_facts(
    schema: LogicalSchema, output: LogicalSchema, operation: FrameUnaryOperationKind
) -> LogicalSchema:
    """Propagate current facts with the exact expression/axis correspondence."""
    from dataclasses import replace
    from datetime import date

    import pandas as pd

    from marivo._definition.query import (
        FilterValues,
        PairStatistic,
        ReduceValues,
        SelectValues,
        Statistic,
        TopValues,
        ValuePartition,
        ValueSlot,
    )
    from marivo.analysis.core.data_schema import DataAxis
    from marivo.analysis.core.frame_fix import FixFrameValues, validate_fixed_key
    from marivo.analysis.core.frame_schema import capture_axis, comparison_id
    from marivo.analysis.core.frozen_calendar import frozen_key_table

    contexts = dict(schema.value_contexts)
    fixed = schema.fixed_axes
    decompositions = schema.decompositions
    comparisons = output.comparisons
    if isinstance(operation, FixFrameValues):
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
            v
            if isinstance(v, (pa.Date32Scalar, pa.TimestampScalar, pa.DurationScalar))
            else v.as_py()
            for v in scalars
        )
        selected = FrameFixedAxis(description, key, operation.origins)
        fixed = (*fixed, selected)

        def groups(items: tuple[FrameGroup, ...]) -> tuple[FrameGroup, ...]:
            return tuple(
                FrameFixedGroup(selected)
                if isinstance(g, FrameAxisGroup) and g.axis_token == axis.token
                else g
                for g in items
            )

        contexts = {
            n: replace(c, groups=groups(c.groups)) if isinstance(c, FrameStatisticContext) else c
            for n, c in contexts.items()
        }
        comparisons = tuple(
            replace(
                f,
                contexts=tuple(
                    (
                        p,
                        replace(a, groups=groups(a.groups))
                        if isinstance(a, FrameStatisticContext)
                        else a,
                        replace(b, groups=groups(b.groups))
                        if isinstance(b, FrameStatisticContext)
                        else b,
                    )
                    for p, a, b in f.contexts
                ),
            )
            for f in comparisons
        )
        decompositions = tuple(
            replace(
                d,
                groups=groups(d.groups),
                row_scope=d.row_scope
                if any(
                    isinstance(g, FrameAxisGroup) and g.axis_token == axis.token for g in d.groups
                )
                else "restricted",
            )
            for d in decompositions
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
            elif isinstance(expression, ValuePartition):
                selected_contexts[name] = FrameStatisticContext(
                    expression.statistic.method,
                    tuple(FrameAxisGroup(schema.axes[p].token) for p in expression.positions),
                )
            elif isinstance(operation, ReduceValues) and isinstance(
                expression, (Statistic, PairStatistic)
            ):
                selected_contexts[name] = FrameStatisticContext(
                    expression.method,
                    tuple(FrameAxisGroup(schema.axes[p].token) for p in operation.positions),
                )
        contexts = selected_contexts
        if isinstance(operation, SelectValues):
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
        value_contexts=resolved,
        decompositions=decompositions,
        comparisons=comparisons,
    )


if TYPE_CHECKING:
    from marivo.analysis.core.frame_expression import (
        AlignFrameValues,
        CompareFrameValues,
        FrameAttributeTarget,
        FrameUnaryOperationKind,
    )
    from marivo.analysis.core.frame_schema import LogicalSchema


def comparison_facts(
    inputs: tuple[LogicalSchema, ...], output: LogicalSchema, operation: CompareFrameValues
) -> LogicalSchema:
    """Bind actual comparison endpoints before old relations can obscure their meaning."""
    from dataclasses import replace

    from marivo.analysis.core.data_schema import (
        CalendarComparisonBinding,
        IntervalComparisonBinding,
    )
    from marivo.analysis.core.frame_schema import comparison_id

    current = inputs[0]
    contexts: dict[str, FrameValueContext] = {}
    facts = list(output.comparisons)
    common = tuple(f for f in current.fixed_axes if all(f in b.fixed_axes for b in inputs[1:]))

    def shared_context(context: FrameValueContext | None) -> FrameValueContext | None:
        if isinstance(context, FrameStatisticContext) and any(
            isinstance(group, FrameFixedGroup) and group.fixed not in common
            for group in context.groups
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
        captured: list[
            tuple[tuple[str, ...], FrameValueContext | None, FrameValueContext | None]
        ] = []
        for path in current.value_paths:
            name = ".".join(path)
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
        fixed_axes=common,
        value_contexts=tuple(contexts.items()),
        decompositions=(),
    )


def align_facts(
    left: LogicalSchema, right: LogicalSchema, output: LogicalSchema, operation: AlignFrameValues
) -> LogicalSchema:
    """Transport only local current contexts and common fixed choices through align."""
    from dataclasses import replace

    from marivo.analysis.core.data_schema import PairedDataAxis
    from marivo.analysis.core.frame_schema import capture_comparisons, comparison_id

    result: list[tuple[str, FrameValueContext]] = []
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
        ids = {
            comparison_id(f): identity
            for f in schema.comparisons
            if (identity := ".".join((side, comparison_id(f)))) in visible_comparisons
        }
        for name, context in schema.value_contexts:
            rebound = remap_context(context, tokens, ids)
            if isinstance(rebound, FrameStatisticContext) and any(
                isinstance(group, FrameFixedGroup) and group.fixed not in common
                for group in rebound.groups
            ):
                rebound = None
            if rebound is not None:
                result.append((side + "." + name, rebound))
    return replace(output, fixed_axes=common, value_contexts=tuple(result), decompositions=())


def decomposition_facts(
    output: LogicalSchema,
    parent: LogicalSchema,
    target: FrameAttributeTarget,
    fields: tuple[FrameDecompositionField, ...],
) -> LogicalSchema:
    """Attach roles from actual output construction, never from a historical method record."""
    from dataclasses import replace

    from marivo.analysis.core.frame_schema import comparison_id

    observed = parent.value_paths[target.observed]
    name = ".".join(target.path)
    current_target: FrameLevelTarget | FrameChangeTarget
    from marivo.analysis.core.data_schema import DataComparison

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
        value_contexts=(),
        comparisons=comparisons,
        decompositions=(decomposition,),
    )


def _validate_fact(value: _Fact) -> None:
    """Validate this closed result family without consulting data or definitions."""
    from dataclasses import MISSING, fields, is_dataclass
    from datetime import timezone
    from typing import get_args

    from marivo.analysis.core.lineage import union

    assert is_dataclass(value)
    for descriptor in fields(value):
        if (
            descriptor.name == "kind"
            and descriptor.default is not MISSING
            and value.kind != descriptor.default
        ):
            _invalid("the exact kind tag of the closed result variant")

    def text(item: str) -> None:
        require_data_text(item, "frame.schema.fact")

    def instant(item: datetime | None) -> None:
        if item is not None and (type(item) is not datetime or item.tzinfo is not timezone.utc):
            _invalid("captured instants must be plain UTC datetimes")

    def groups(items: tuple[FrameGroup, ...]) -> None:
        if type(items) is not tuple or any(
            type(g) not in (FrameAxisGroup, FrameFixedGroup) for g in items
        ):
            _invalid("complete immutable axis or fixed parent groups")

    def context(item: FrameValueContext | None) -> None:
        if item is not None and type(item) not in (FrameObservationContext, FrameStatisticContext):
            _invalid("one closed observation or statistic context")

    if isinstance(value, FrameAxisSchema):
        text(value.token)
        text(value.name)
        instant(value.at)
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
            text(value.timezone)
    elif isinstance(value, (FrameCalendarEndpoint, FrameIntervalEndpoint)):
        text(value.name)
        text(value.timezone)
        instant(value.at)
    elif isinstance(value, FrameBuiltinGrain):
        if (
            value.unit
            not in ("second", "minute", "hour", "day", "week", "month", "quarter", "year")
            or type(value.step) is not int
            or value.step < 1
        ):
            _invalid("a positive builtin grain")
    elif isinstance(value, FrameNamedGrain):
        if type(value.ref) is not SemanticObjectIdentity or value.ref.kind != "period_calendar":
            _invalid("the actual consumed named calendar identity")
        text(value.level)
    elif isinstance(value, FrameFixedAxis):
        if (
            type(value.axis) is not FrameOrdinaryAxisSchema
            or type(value.key) is not tuple
            or len(value.key) != len(value.axis.keys)
            or value.origins != union(value.origins)
        ):
            _invalid("a complete ordered fixed ordinary key and captured origins")
        if any(
            v is not None
            and type(v)
            not in (
                bool,
                str,
                int,
                float,
                Decimal,
                pa.Date32Scalar,
                pa.TimestampScalar,
                pa.DurationScalar,
            )
            for v in value.key
        ):
            _invalid("typed exact fixed key scalars")
    elif isinstance(value, FrameAxisTime):
        text(value.axis_token)
        if value.endpoint not in ("self", "left", "right"):
            _invalid("a closed current time endpoint")
    elif isinstance(value, FrameComparedTime):
        text(value.comparison_id)
        if value.endpoint not in ("current", "baseline"):
            _invalid("a closed comparison endpoint")
    elif isinstance(value, FrameFixedTime):
        instant(value.start)
        instant(value.end)
        text(value.timezone)
        if value.start >= value.end:
            _invalid("a finite increasing scalar observation range")
    elif isinstance(value, FrameSinceWindow):
        instant(value.start)
    elif isinstance(value, FrameTrailingDurationWindow):
        if type(value.duration) is not timedelta or value.duration <= timedelta(0):
            _invalid("a positive elapsed window duration")
    elif isinstance(value, FrameTrailingPeriodsWindow):
        if type(value.count) is not int or value.count < 1:
            _invalid("a positive number of trailing periods")
    elif isinstance(value, FrameObservationContext):
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
    elif isinstance(value, FrameAxisGroup):
        text(value.axis_token)
    elif isinstance(value, FrameFixedGroup):
        if type(value.fixed) is not FrameFixedAxis:
            _invalid("an exact fixed parent coordinate")
    elif isinstance(value, FrameStatisticContext):
        if value.statistic not in (
            "sum",
            "mean",
            "row_count",
            "pearson",
            "spearman",
            "pair_count",
        ) or value.row_scope not in ("complete", "restricted"):
            _invalid("one supported current-row statistic and row scope")
        groups(value.groups)
    elif isinstance(value, FrameComparisonValue):
        text(value.name)
        if value.role not in ("current", "baseline", "absolute", "relative"):
            _invalid("a closed current comparison value role")
    elif isinstance(value, FrameComparisonGroup):
        text(value.group_id)
        text(value.name)
        context(value.current_context)
        context(value.baseline_context)
    elif isinstance(value, FrameComparisonPresence):
        text(value.current)
        text(value.baseline)
        for name in value.enclosing:
            text(name)
    elif isinstance(value, (FrameCalendarComparisonBinding, FrameIntervalComparisonBinding)):
        text(value.current_axis_token)
        if value.current_endpoint not in ("self", "left", "right"):
            _invalid("a closed current comparison time endpoint")
    elif isinstance(value, FrameComparison):
        text(value.input_id)
        text(value.comparison_id)
        if value.alias is not None:
            text(value.alias)
    elif isinstance(value, FrameLevelTarget):
        text(value.name)
        context(value.context)
    elif isinstance(value, FrameChangeTarget):
        text(value.comparison_id)
        text(value.group_id)
        if value.kind not in ("absolute", "relative"):
            _invalid("an absolute or relative current comparison target")
    elif isinstance(value, FrameDecompositionField):
        text(value.name)
        if value.field_kind not in ("value", "presence") or value.role not in get_args(
            FrameDecompositionRole
        ):
            _invalid("a closed decomposition output directory and role")
    elif isinstance(value, FrameDecomposition):
        text(value.decomposition_id)
        groups(value.groups)
        if value.row_scope not in ("complete", "restricted") or not any(
            f.field_kind == "value" for f in value.outputs
        ):
            _invalid("a current decomposition with retained values and a closed row scope")
    if isinstance(
        value, (FrameCalendarAxisSchema, FrameCalendarEndpoint, FrameToDateWindow)
    ) and type(value.grain) not in (FrameBuiltinGrain, FrameNamedGrain):
        _invalid("one closed captured nominal grain")
    if isinstance(value, FramePairedAxisSchema) and (
        type(value.left) not in (FrameCalendarEndpoint, FrameIntervalEndpoint)
        or type(value.left) is not type(value.right)
    ):
        _invalid("two closed paired time endpoints of the same kind")


def validate_current_facts(schema: LogicalSchema) -> None:
    """Resolve every current explanation reference using only captured directories."""
    from marivo.analysis.core.data_schema import CalendarDataAxis, IntervalDataAxis, PairedDataAxis
    from marivo.analysis.core.frame_schema import comparison_id

    values = {c.name for c in schema.values}
    presence = {c.name for c in schema.presence}
    axes = {a.token: a for a in schema.axes}
    comparisons = {comparison_id(f): f for f in schema.comparisons}
    if len(comparisons) != len(schema.comparisons):
        _invalid("unique current comparison identities")

    def context(item: FrameValueContext | None, selections: tuple[FrameFixedAxis, ...]) -> None:
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
        elif isinstance(item, FrameStatisticContext):
            group_refs(item.groups, selections)

    def group_refs(items: tuple[FrameGroup, ...], selections: tuple[FrameFixedAxis, ...]) -> None:
        for group in items:
            if isinstance(group, FrameAxisGroup) and group.axis_token not in axes:
                _invalid("a current complete parent axis")
            if isinstance(group, FrameFixedGroup) and group.fixed not in selections:
                _invalid("a retained exact fixed parent coordinate")

    names = tuple(n for n, _ in schema.value_contexts)
    if len(set(names)) != len(names) or any(n not in values for n in names):
        _invalid("unique context bindings to current value fields")
    for _, item in schema.value_contexts:
        context(item, schema.fixed_axes)
    for fact in schema.comparisons:
        for _, current, baseline in fact.contexts:
            context(current, (*schema.fixed_axes, *fact.current_fixed_axes))
            context(baseline, (*schema.fixed_axes, *fact.baseline_fixed_axes))
    if len({d.decomposition_id for d in schema.decompositions}) != len(schema.decompositions):
        _invalid("unique current decomposition identities")
    for decomposition in schema.decompositions:
        group_refs(decomposition.groups, schema.fixed_axes)
        for output in decomposition.outputs:
            if output.name not in (values if output.field_kind == "value" else presence):
                _invalid("decomposition roles resolving to exact current fields")
        target = decomposition.target
        if isinstance(target, FrameLevelTarget):
            context(target.context, schema.fixed_axes)
        else:
            target_fact = comparisons.get(target.comparison_id)
            if target_fact is None or target.group_id not in {
                ".".join(p) for p, _, _ in target_fact.contexts
            } | {".".join(v.source_path) for v in target_fact.values}:
                _invalid("a decomposition target resolving to a current comparison quantity")


def share_contexts(schema: LogicalSchema) -> LogicalSchema:
    """Intern only equal current immutable contexts; no ancestors or rows are retained."""
    from dataclasses import replace

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
