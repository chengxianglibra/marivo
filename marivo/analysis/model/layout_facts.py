"""Captured current value contexts and comparison, alignment and decomposition facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias, get_args

from marivo.analysis.model.frame_facts import _Fact, _invalid, _text
from marivo.analysis.model.lineage import SemanticObjectIdentity, union
from marivo.analysis.model.time_facts import (
    FrameCalendarEndpoint,
    FrameFixedAxis,
    FrameIntervalEndpoint,
    FrameObservationContext,
)


class _LayoutFact(_Fact):
    def __post_init__(self) -> None:
        _Fact.__post_init__(self)
        _validate_groups(self)
        _validate_layout(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameAxisGroup(_LayoutFact):
    """One complete current parent axis."""

    axis_token: str
    kind: Literal["axis"] = field(default="axis")


@dataclass(frozen=True, slots=True, repr=False)
class FrameFixedGroup(_LayoutFact):
    """One fixed parent coordinate, retaining its exact removed axis description."""

    fixed: FrameFixedAxis
    kind: Literal["fixed"] = field(default="fixed")

    def _identity_detail(self) -> str:
        return f"axis={self.fixed.axis.token[:40]}"


FrameGroup: TypeAlias = FrameAxisGroup | FrameFixedGroup


@dataclass(frozen=True, slots=True, repr=False)
class FrameStatisticContext(_LayoutFact):
    """Current-row statistic and its complete parent groups."""

    statistic: Literal["sum", "mean", "row_count"]
    groups: tuple[FrameGroup, ...] = field(
        metadata={
            "meaning": "Complete parent groups; see marivo.help('analysis.frame_statistic_context') for axis and fixed variants."
        }
    )
    row_scope: Literal["complete", "restricted"] = field(
        default="complete",
        metadata={
            "meaning": "Complete retains the represented statistic's full parent row scope; restricted means the current result may represent only part of that scope. Filtering or ranking does not recompute the captured statistic."
        },
    )
    kind: Literal["statistic"] = field(default="statistic")


@dataclass(frozen=True, slots=True, repr=False)
class FrameNamedValueInput(_LayoutFact):
    """The complete captured name and optional directly observed subject of an input."""

    name: str = field(
        metadata={
            "meaning": "Complete input value name captured when this operation was built; a snapshot, not a current field reference."
        }
    )
    subject: SemanticObjectIdentity | None = field(
        metadata={
            "meaning": "Direct observation subject when explicitly present on the input; None means no named subject was available."
        }
    )
    kind: Literal["named"] = field(default="named")


@dataclass(frozen=True, slots=True, repr=False)
class FrameDerivedValueInput(_LayoutFact):
    """The captured value origins of an expression, without its formula or execution."""

    origins: tuple[SemanticObjectIdentity, ...] = field(
        metadata={
            "meaning": "Sorted unique value origins of this operand; excludes context origins and does not identify a formula."
        }
    )
    kind: Literal["derived"] = field(default="derived")


FrameValueInput: TypeAlias = FrameNamedValueInput | FrameDerivedValueInput


@dataclass(frozen=True, slots=True, repr=False)
class FrameShareContext(_LayoutFact):
    """Explicit partition-sum normalization over the captured numerator and axes."""

    numerator: FrameValueInput = field(
        metadata={
            "meaning": "Captured normalization input; see marivo.help('analysis.frame_named_value_input') and marivo.help('analysis.frame_derived_value_input') for variants."
        }
    )
    groups: tuple[FrameGroup, ...] = field(
        metadata={
            "meaning": "Complete denominator partition axes or fixed coordinates; see marivo.help('analysis.frame_axis_group') and marivo.help('analysis.frame_fixed_group')."
        }
    )
    along: tuple[FrameGroup, ...] = field(
        metadata={
            "meaning": "Nonempty normalization axes in original input order, or their retained fixed coordinates; disjoint from groups."
        }
    )
    row_scope: Literal["complete", "restricted"] = field(
        default="complete",
        metadata={
            "meaning": "Whether current valid endpoint rows still represent the full captured normalization domain; selection restricts without recomputing the denominator."
        },
    )
    kind: Literal["share"] = field(default="share")


@dataclass(frozen=True, slots=True, repr=False)
class FramePairStatisticContext(_LayoutFact):
    """A current-row pair statistic over two ordered captured inputs."""

    statistic: Literal["pearson", "spearman", "pair_count"]
    x: FrameValueInput = field(
        metadata={
            "meaning": "First ordered actual operand captured as a named input or derived expression; see analysis.frame_named_value_input and analysis.frame_derived_value_input."
        }
    )
    y: FrameValueInput = field(
        metadata={
            "meaning": "Second ordered actual operand captured independently of x; equality of descriptions does not certify the same calculation or sample."
        }
    )
    groups: tuple[FrameGroup, ...] = field(
        metadata={
            "meaning": "Complete parent grouping axes or fixed coordinates; see marivo.help('analysis.frame_axis_group') and marivo.help('analysis.frame_fixed_group')."
        }
    )
    row_scope: Literal["complete", "restricted"] = field(
        default="complete",
        metadata={
            "meaning": "Whether current valid endpoint rows still represent the full captured statistic domain; does not certify source coverage, sample sufficiency or numerical validity."
        },
    )
    kind: Literal["pair_statistic"] = field(default="pair_statistic")


FrameValueContext: TypeAlias = (
    FrameObservationContext | FrameStatisticContext | FrameShareContext | FramePairStatisticContext
)


@dataclass(frozen=True, slots=True, repr=False)
class FrameAlignmentAxis(_LayoutFact):
    """A complete current coordinate and its exact time endpoint when paired."""

    axis_token: str
    endpoint: Literal["self", "left", "right"] = field(
        metadata={
            "meaning": "self selects an ordinary/unpaired axis; left and right select the corresponding endpoint of a paired time axis."
        }
    )


FrameAlignmentCoordinate: TypeAlias = FrameAlignmentAxis | FrameFixedGroup


@dataclass(frozen=True, slots=True, repr=False)
class FrameAlignmentSide(_LayoutFact):
    """Direct current values, enclosing presence and complete coordinates of one side."""

    values: tuple[str, ...]
    presence: tuple[str, ...] = field(
        metadata={
            "meaning": "Nonempty current boolean presence names in outer-to-inner order; all must be True for this side to exist."
        }
    )
    coordinates: tuple[FrameAlignmentCoordinate, ...] = field(
        metadata={
            "meaning": "Complete coordinates in relation creation order, using FrameAlignmentAxis or retained FrameFixedGroup variants."
        }
    )


@dataclass(frozen=True, slots=True, repr=False)
class FrameAlignment(_LayoutFact):
    """A current local alignment relationship, acquired from FrameSchema.alignments."""

    input_id: str
    alignment_id: str
    left: FrameAlignmentSide
    right: FrameAlignmentSide


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparisonValue(_LayoutFact):
    """One current value's comparison role; direct copies may share a role."""

    role: Literal["current", "baseline", "absolute", "relative"]
    name: str


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparisonGroup(_LayoutFact):
    """One compared quantity and independently captured endpoint contexts."""

    group_id: str
    name: str
    values: tuple[FrameComparisonValue, ...]
    current_context: FrameValueContext | None
    baseline_context: FrameValueContext | None


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparisonPresence(_LayoutFact):
    """Current endpoint flags and enclosing flags, applied before endpoint flags."""

    current: str
    baseline: str
    enclosing: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class FrameOrdinaryComparisonBinding(_LayoutFact):
    """Comparison without a time-coordinate relationship."""

    kind: Literal["ordinary"] = field(default="ordinary")


@dataclass(frozen=True, slots=True, repr=False)
class FrameCalendarComparisonBinding(_LayoutFact):
    """Current calendar endpoint and captured baseline description."""

    current_axis_token: str
    current_endpoint: Literal["self", "left", "right"]
    baseline: FrameCalendarEndpoint
    kind: Literal["calendar"] = field(default="calendar")


@dataclass(frozen=True, slots=True, repr=False)
class FrameIntervalComparisonBinding(_LayoutFact):
    """Current interval endpoint and captured baseline description."""

    current_axis_token: str
    current_endpoint: Literal["self", "left", "right"]
    baseline: FrameIntervalEndpoint
    kind: Literal["interval"] = field(default="interval")


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparison(_LayoutFact):
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
class FrameLevelTarget(_LayoutFact):
    """Captured level target and its local current meaning."""

    name: str
    context: FrameValueContext | None
    kind: Literal["level"] = field(default="level")


@dataclass(frozen=True, slots=True, repr=False)
class FrameChangeTarget(_LayoutFact):
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
class FrameDecompositionField(_LayoutFact):
    """One current value or presence field and its actual decomposition role."""

    field_kind: Literal["value", "presence"]
    role: FrameDecompositionRole = field(
        metadata={
            "meaning": "Role of this retained output: "
            + ", ".join(get_args(FrameDecompositionRole))
            + ". See marivo.help('analysis.analysis_frame.attribute') for the decomposition contract."
        }
    )
    name: str


@dataclass(frozen=True, slots=True, repr=False)
class FrameDecomposition(_LayoutFact):
    """Current decomposition target, parent groups, retained roles and row scope."""

    decomposition_id: str
    target: FrameLevelTarget | FrameChangeTarget
    groups: tuple[FrameGroup, ...] = field(
        metadata={
            "meaning": "Complete parent groups; see marivo.help('analysis.frame_decomposition') for axis and fixed variants."
        }
    )
    row_scope: Literal["complete", "restricted"] = field(
        metadata={
            "meaning": "Complete retains the represented decomposition's full parent row scope; restricted means the current result may represent only part of that scope. Filtering or ranking does not recompute the captured decomposition."
        }
    )
    outputs: tuple[FrameDecompositionField, ...]


def groups(items: tuple[FrameGroup, ...]) -> None:
    if (
        type(items) is not tuple
        or any(type(g) not in (FrameAxisGroup, FrameFixedGroup) for g in items)
        or len(set(items)) != len(items)
    ):
        _invalid("distinct complete immutable axis or fixed parent groups")


def context(item: FrameValueContext | None) -> None:
    if item is not None and type(item) not in (
        FrameObservationContext,
        FrameStatisticContext,
        FrameShareContext,
        FramePairStatisticContext,
    ):
        _invalid("one closed current value context")


def _validate_groups(value: _LayoutFact) -> None:
    if isinstance(value, FrameAxisGroup):
        _text(value.axis_token)
    elif isinstance(value, FrameFixedGroup):
        if type(value.fixed) is not FrameFixedAxis:
            _invalid("an exact fixed parent coordinate")
    elif isinstance(value, FrameNamedValueInput):
        _text(value.name)
        if value.subject is not None and (
            type(value.subject) is not SemanticObjectIdentity or value.subject.kind != "metric"
        ):
            _invalid("a named input with its directly observed Metric subject or None")
    elif isinstance(value, FrameDerivedValueInput):
        if (
            type(value.origins) is not tuple
            or any(type(o) is not SemanticObjectIdentity for o in value.origins)
            or value.origins != union(value.origins)
        ):
            _invalid("stable sorted unique captured value origins")
    elif isinstance(value, (FrameStatisticContext, FrameShareContext, FramePairStatisticContext)):
        if value.row_scope not in ("complete", "restricted"):
            _invalid("one closed represented row scope")
        groups(value.groups)
        if isinstance(value, FrameStatisticContext):
            if value.statistic not in ("sum", "mean", "row_count"):
                _invalid("one supported current-row statistic")
        elif isinstance(value, FramePairStatisticContext):
            if value.statistic not in ("pearson", "spearman", "pair_count"):
                _invalid("one supported ordered pair statistic")
            if any(
                type(i) not in (FrameNamedValueInput, FrameDerivedValueInput)
                for i in (value.x, value.y)
            ):
                _invalid("two closed captured value inputs")
        else:
            if type(value.numerator) not in (FrameNamedValueInput, FrameDerivedValueInput):
                _invalid("one closed captured numerator input")
            groups(value.along)
            if not value.along or set(value.groups) & set(value.along):
                _invalid("nonempty normalization axes disjoint from denominator groups")


def _validate_layout(value: _LayoutFact) -> None:
    if isinstance(value, FrameAlignmentAxis):
        _text(value.axis_token)
        if value.endpoint not in ("self", "left", "right"):
            _invalid("one closed current coordinate endpoint")
    elif isinstance(value, FrameAlignmentSide):
        for items in (value.values, value.presence):
            if type(items) is not tuple or len(set(items)) != len(items):
                _invalid("unique ordered current field names")
            for name in items:
                _text(name)
        if not value.presence:
            _invalid("a nonempty ordered presence chain")
        if (
            type(value.coordinates) is not tuple
            or any(type(c) not in (FrameAlignmentAxis, FrameFixedGroup) for c in value.coordinates)
            or len(set(value.coordinates)) != len(value.coordinates)
        ):
            _invalid("unique complete current or fixed coordinates")
    elif isinstance(value, FrameAlignment):
        _text(value.input_id)
        _text(value.alignment_id)
        if (
            type(value.left) is not FrameAlignmentSide
            or type(value.right) is not FrameAlignmentSide
        ):
            _invalid("two closed alignment sides")
        if (not value.left.values and not value.right.values) or set(value.left.values) & set(
            value.right.values
        ):
            _invalid("disjoint alignment values with at least one retained value")
    elif isinstance(value, FrameComparisonValue):
        _text(value.name)
        if value.role not in ("current", "baseline", "absolute", "relative"):
            _invalid("a closed current comparison value role")
    elif isinstance(value, FrameComparisonGroup):
        _text(value.group_id)
        _text(value.name)
        context(value.current_context)
        context(value.baseline_context)
    elif isinstance(value, FrameComparisonPresence):
        _text(value.current)
        _text(value.baseline)
        for name in value.enclosing:
            _text(name)
    elif isinstance(value, (FrameCalendarComparisonBinding, FrameIntervalComparisonBinding)):
        _text(value.current_axis_token)
        if value.current_endpoint not in ("self", "left", "right"):
            _invalid("a closed current comparison time endpoint")
    elif isinstance(value, FrameComparison):
        _text(value.input_id)
        _text(value.comparison_id)
        if value.alias is not None:
            _text(value.alias)
    elif isinstance(value, FrameLevelTarget):
        _text(value.name)
        context(value.context)
    elif isinstance(value, FrameChangeTarget):
        _text(value.comparison_id)
        _text(value.group_id)
        if value.kind not in ("absolute", "relative"):
            _invalid("an absolute or relative current comparison target")
    elif isinstance(value, FrameDecompositionField):
        _text(value.name)
        if value.field_kind not in ("value", "presence") or value.role not in get_args(
            FrameDecompositionRole
        ):
            _invalid("a closed decomposition output directory and role")
    elif isinstance(value, FrameDecomposition):
        _text(value.decomposition_id)
        groups(value.groups)
        if value.row_scope not in ("complete", "restricted") or not any(
            f.field_kind == "value" for f in value.outputs
        ):
            _invalid("a current decomposition with retained values and a closed row scope")
