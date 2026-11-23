"""Immutable explanatory Attribute facts, without executable source authority.

These records validate closed data structure and associations. Numerical consistency
with source execution remains with the producer's existing arithmetic policies.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from typing import Literal, TypeAlias

from marivo.analysis.core.frame_level_facts import (
    FrameLevelAdditiveMethod,
    FrameLevelDivisionMethod,
    FrameLevelLinearMethod,
    FrameLevelRatioMethod,
    _level_method,
    _LevelMethod,
)
from marivo.analysis.core.frame_method_checks import _divisor, _quotient_unit, _total, _totals
from marivo.analysis.core.frame_method_coordinates import (
    FrameMethodCoordinate as FrameMethodCoordinate,
)
from marivo.analysis.core.frame_method_coordinates import _arrow_table
from marivo.analysis.core.frame_shapley_facts import (
    FrameShapleyCapture,
    FrameShapleyGame,
    FrameShapleyLeaf,
    FrameShapleyNativeQuantile,
    FrameShapleyQuantile,
    FrameShapleyQuery,
    FrameShapleyQueryGame,
    FrameShapleyReduction,
    state_metric_approximation,
)
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarDirectory,
    FrozenIntervalDirectory,
    frozen_key_rows,
    read_calendar_directory,
    read_comparison_mapping,
    read_interval_directory,
    validate_paired_mapping,
)
from marivo.analysis.model.coordinate_schema import coordinate_attachment_schemas
from marivo.analysis.model.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.model.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.model.method_targets import FrameMethodOutput, FrameMethodTarget
from marivo.analysis.model.method_values import (
    FrameMethodValue,
    _cell,
    _coefficient_text,
    _count,
    _FactRecord,
    _fail,
    _MethodScalar,
    _short,
    _text,
)

_ExactMethod: TypeAlias = (
    "FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod"
)
_Method: TypeAlias = "_ExactMethod | FrameShapleyMethod"
_StateRoute: TypeAlias = Literal[
    "typed_sufficient_state/v1", "typed_value_frequency/v1", "typed_sufficient_frequency/v1"
]


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodSupport(_FactRecord):
    """Explain a local sample root; counts certify no source qualification.

    Args: root_id, policy and four exact counts. Example: ``FrameMethodSupport('r', 'rows', 2, 2, 2, 2)``.
    Returns a frozen record; effective counts cannot exceed input counts.
    """

    root_id: str
    policy: Literal[
        "rows", "non_null_value", "co_valid_value_weight", "identity", "preserved_sample"
    ]
    left_input_count: int
    right_input_count: int
    left_effective_count: int
    right_effective_count: int

    def __post_init__(self) -> None:
        _text(self.root_id)
        if type(self.policy) is not str or self.policy not in (
            "rows",
            "non_null_value",
            "co_valid_value_weight",
            "identity",
            "preserved_sample",
        ):
            _fail("a closed sample policy", repr(self.policy))
        counts = (
            self.left_input_count,
            self.right_input_count,
            self.left_effective_count,
            self.right_effective_count,
        )
        if any(type(count) is not int or count < 0 for count in counts) or (
            self.left_effective_count > self.left_input_count
            or self.right_effective_count > self.right_input_count
        ):
            _fail(
                "nonnegative exact counts with effective <= input on each side",
                ", ".join(_coefficient_text(count) for count in counts),
            )

    def _summary(self) -> str:
        return f"kind=support, root={_short(self.root_id)!r}, policy={self.policy}"

    def _lines(self) -> Iterator[str]:
        yield (
            f"left input/effective={_coefficient_text(self.left_input_count)}/{_coefficient_text(self.left_effective_count)}; "
            f"right input/effective={_coefficient_text(self.right_input_count)}/{_coefficient_text(self.right_effective_count)}"
        )


def _supports(support: tuple[FrameMethodSupport, ...]) -> None:
    if (
        type(support) is not tuple
        or not support
        or any(type(s) is not FrameMethodSupport for s in support)
    ):
        _fail("a nonempty immutable tuple of exact root supports", type(support).__name__)
    if len({s.root_id for s in support}) != len(support):
        _fail("unique root supports within a method", "duplicate root_id")


def _method(value: _Method) -> None:
    if type(value) not in (
        FrameAdditiveMethod,
        FrameRatioMethod,
        FrameLinearMethod,
        FrameDivisionMethod,
        FrameShapleyMethod,
    ):
        _fail("a closed additive, ratio_component or linear method", type(value).__name__)


def _exact_method(value: _ExactMethod) -> None:
    if type(value) not in (
        FrameAdditiveMethod,
        FrameRatioMethod,
        FrameLinearMethod,
        FrameDivisionMethod,
    ):
        _fail(
            "an exact child rule; sampled replacement owns the complete target",
            type(value).__name__,
        )


def same_method_facts(left: FrameMethodFacts, right: FrameMethodFacts) -> bool:
    """Compare complete typed payloads, including preserved scalar representations."""

    def method_values(method: _Method) -> Iterator[str]:
        for value in (method.left, method.right, method.change):
            yield repr(value.value)
        if isinstance(method, FrameShapleyMethod):
            for leaf in method.leaves:
                if isinstance(leaf.reduction, (FrameShapleyQuantile, FrameShapleyNativeQuantile)):
                    yield repr(leaf.reduction.q)
        if isinstance(method, FrameRatioMethod):
            for value in (
                method.numerator_left,
                method.numerator_right,
                method.denominator_left,
                method.denominator_right,
            ):
                yield repr(value.value)
        if isinstance(method, FrameDivisionMethod):
            yield repr(method.divisor)
            yield from method_values(method.method)
        if isinstance(method, FrameLinearMethod):
            for term in method.terms:
                yield repr(term.coefficient)
                yield from method_values(term.method)

    def target_values(method: _Method | _LevelMethod) -> Iterator[str]:
        if isinstance(
            method,
            (
                FrameAdditiveMethod,
                FrameRatioMethod,
                FrameLinearMethod,
                FrameDivisionMethod,
                FrameShapleyMethod,
            ),
        ):
            yield from method_values(method)
        else:
            yield repr(method.value.value)
            if isinstance(method, FrameLevelRatioMethod):
                yield repr(method.numerator.value)
                yield repr(method.denominator.value)
            elif isinstance(method, FrameLevelDivisionMethod):
                yield repr(method.divisor)
                yield from target_values(method.method)
            elif isinstance(method, FrameLevelLinearMethod):
                for term in method.terms:
                    yield repr(term.coefficient)
                    yield from target_values(term.method)

    def scalar_values(facts: FrameMethodFacts) -> Iterator[str]:
        for parent in facts.parents:
            for key in parent.key:
                yield repr(key.value)
            for metric in parent.metrics:
                yield repr(metric.observed_total.value)
                yield repr(metric.total.value)
                yield from target_values(metric.method)
        for coordinate in facts.coordinates:
            for row in coordinate.rows:
                for value in row:
                    yield repr(value)

    return left == right and tuple(scalar_values(left)) == tuple(scalar_values(right))


def _method_lines(method: _Method) -> Iterator[str]:
    yield f"{method.kind}: left={_cell(method.left.value)}, right={_cell(method.right.value)}, change={_cell(method.change.value)}"
    if isinstance(method, FrameShapleyMethod):
        yield f"game=group_replacement; algorithm={method.algorithm_version}; route={method.state_route}"
        yield f"permutations={_coefficient_text(method.permutations)}; seed={_coefficient_text(method.seed)}; players={_coefficient_text(method.player_count)}; entries={_coefficient_text(method.state_entries)}"
        yield f"unique_coalitions={_coefficient_text(method.unique_coalitions)}; evaluations={_coefficient_text(method.coalition_evaluations)}; capture_reads={_coefficient_text(method.captured_source_reads)}; transitions={_coefficient_text(method.sampled_transitions)}"
    elif isinstance(method, FrameDivisionMethod):
        yield f"{method.operation} divisor={_coefficient_text(method.divisor)}"
        yield from _method_lines(method.method)
    elif isinstance(method, FrameLinearMethod):
        for index, term in enumerate(method.terms):
            yield f"term[{index}] coefficient={_coefficient_text(term.coefficient)}"
            yield from _method_lines(term.method)
    else:
        if isinstance(method, FrameRatioMethod):
            yield f"numerator left/right={_cell(method.numerator_left.value)}/{_cell(method.numerator_right.value)}; denominator left/right={_cell(method.denominator_left.value)}/{_cell(method.denominator_right.value)}"
        for support in method.support:
            yield repr(support)


@dataclass(frozen=True, slots=True, repr=False)
class FrameAdditiveMethod(_FactRecord):
    """Explain additive totals. Args: kind, left, right, change, support.

    Returns a frozen inert rule; e.g. ``FrameAdditiveMethod('additive', l, r, d, (s,))``.
    Source and arithmetic qualification belongs to the successful producer.
    """

    kind: Literal["additive"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    support: tuple[FrameMethodSupport, ...]

    def __post_init__(self) -> None:
        if self.kind != "additive" or type(self.kind) is not str:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            _fail("kind='additive'", repr(self.kind))
        _totals(self.left, self.right, self.change)
        _supports(self.support)

    def _summary(self) -> str:
        return f"kind=additive, column={_short(self.left.column.name)!r}, roots={len(self.support)}"

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameRatioMethod(_FactRecord):
    """Explain parent ratio components. Args: kind, totals, four components, support.

    Returns a frozen rule; e.g. ``FrameRatioMethod('ratio_component', l, r, d, nl, nr, dl, dr, (s,))``.
    Positive parent denominators and checked components use the versioned symmetric
    within/mix rule and zero-mass entry/exit boundary. Support retains each original
    effective NULL mask; the record grants no source execution authority.
    """

    kind: Literal["ratio_component"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    numerator_left: FrameMethodValue
    numerator_right: FrameMethodValue
    denominator_left: FrameMethodValue
    denominator_right: FrameMethodValue
    support: tuple[FrameMethodSupport, ...]
    rule_version: Literal["symmetric_rate_mix/v1"] = "symmetric_rate_mix/v1"
    component_policy: Literal["attribute_components/v1"] = "attribute_components/v1"
    boundary_policy: Literal["zero_mass_entry_exit/v1"] = "zero_mass_entry_exit/v1"

    def __post_init__(self) -> None:
        if self.kind != "ratio_component" or type(self.kind) is not str:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            _fail("kind='ratio_component'", repr(self.kind))
        if (self.rule_version, self.component_policy, self.boundary_policy) != (
            "symmetric_rate_mix/v1",
            "attribute_components/v1",
            "zero_mass_entry_exit/v1",
        ):
            _fail("closed versioned rate/mix policies", self.left.column.name)
        _totals(self.left, self.right, self.change)
        _totals(self.numerator_left, self.numerator_right, self.numerator_left)
        _totals(self.denominator_left, self.denominator_right, self.denominator_left)
        for numerator, denominator in (
            (self.numerator_left, self.denominator_left),
            (self.numerator_right, self.denominator_right),
        ):
            if not isinstance(denominator.value, (int, float, Decimal)) or denominator.value <= 0:
                _fail("positive parent ratio denominators", denominator.column.name)
            if (
                _quotient_unit(numerator.column.unit, denominator.column.unit)
                != self.left.column.unit
            ):
                _fail("component units whose ratio matches the metric", self.left.column.name)
        _supports(self.support)

    def _summary(self) -> str:
        return f"kind=ratio_component, column={_short(self.left.column.name)!r}, roots={len(self.support)}"

    def _lines(self) -> Iterator[str]:
        yield f"rule={self.rule_version}; components={self.component_policy}; boundary={self.boundary_policy}"
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyMethod(_FactRecord):
    """Explain sampled group replacement over captured states or independent native reads.

    Acquire through ``frame.method_facts`` after a successful paired attribute.
    Native endpoints and change retain their owning carriers; contributions and
    standard errors are Float64. Captured metadata contains no state or program.
    """

    kind: Literal["shapley"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    permutations: int
    seed: int
    algorithm_version: Literal["permutation/v1"]
    state_route: Literal[
        "typed_sufficient_state/v1",
        "typed_value_frequency/v1",
        "typed_sufficient_frequency/v1",
        "native_queries/v1",
    ]
    state_entries: int
    player_count: int
    unique_coalitions: int
    coalition_evaluations: int
    captured_source_reads: int
    sampled_transitions: int
    game: FrameShapleyGame | FrameShapleyQueryGame
    leaves: tuple[FrameShapleyLeaf, ...]
    captures: tuple[FrameShapleyCapture | FrameShapleyQuery, ...]

    def __post_init__(self) -> None:
        if self.kind != "shapley" or self.algorithm_version != "permutation/v1":  # type: ignore[redundant-expr]  # Preserve the runtime check.
            _fail("the versioned permutation sampling rule", repr(self.kind))
        _totals(self.left, self.right, self.change)
        if type(self.permutations) is not int or self.permutations < 2:
            _fail("at least two complete sampled permutations", repr(self.permutations))
        for value in (
            self.seed,
            self.state_entries,
            self.player_count,
            self.unique_coalitions,
            self.coalition_evaluations,
            self.captured_source_reads,
            self.sampled_transitions,
        ):
            _count(value)
        if self.sampled_transitions != self.permutations * self.player_count:
            _fail(
                "one transition per player in every complete permutation",
                repr(self.sampled_transitions),
            )
        minimum = 1 if self.player_count == 0 else 2
        maximum = 1 if self.player_count == 0 else 2 + self.permutations * (self.player_count - 1)
        if (
            not minimum <= self.unique_coalitions <= maximum
            or self.coalition_evaluations < self.unique_coalitions
            or (
                self.player_count < self.unique_coalitions.bit_length()
                and self.unique_coalitions != 1 << self.player_count
            )
        ):
            _fail(
                "actual coalition and evaluation counts within sampled path bounds",
                repr(self.unique_coalitions),
            )
        if (
            type(self.game) not in (FrameShapleyGame, FrameShapleyQueryGame)
            or type(self.leaves) is not tuple
            or not self.leaves
            or any(type(leaf) is not FrameShapleyLeaf for leaf in self.leaves)
        ):
            _fail("a closed game and immutable original leaf directory", type(self.game).__name__)
        native = isinstance(self.game, FrameShapleyQueryGame)
        expected_capture = FrameShapleyQuery if native else FrameShapleyCapture
        if (
            type(self.captures) is not tuple
            or not self.captures
            or any(type(capture) is not expected_capture for capture in self.captures)
        ):
            _fail(
                "immutable actual capture records matching the game route",
                type(self.captures).__name__,
            )
        if native:
            if self.state_route != "native_queries/v1" or self.state_entries != 0:
                _fail("native_queries/v1 without retained state entries", self.state_route)
            if self.coalition_evaluations != self.unique_coalitions:
                _fail(
                    "one cached evaluation per actual native coalition",
                    repr(self.coalition_evaluations),
                )
        elif self.state_route == "native_queries/v1":
            _fail("a typed captured-state route for the fixed-state game", self.state_route)
        directory = {capture.capture_id: capture for capture in self.captures}
        if len(directory) != len(self.captures) or len(
            {leaf.occurrence for leaf in self.leaves}
        ) != len(self.leaves):
            _fail("unique capture identities and original leaf occurrences", self.left.column.name)
        for leaf in self.leaves:
            if (
                not native
                and isinstance(leaf.reduction, FrameShapleyReduction)
                and leaf.reduction.reducer == "approx_nunique"
            ):
                _fail(
                    "original approximate distinct through native queries without merged estimates",
                    repr(leaf.occurrence),
                )
            if (native and isinstance(leaf.reduction, FrameShapleyQuantile)) or (
                not native and isinstance(leaf.reduction, FrameShapleyNativeQuantile)
            ):
                _fail(
                    "original quantile metadata matching the state or native query route",
                    repr(leaf.occurrence),
                )
            for capture_ids, endpoint in (
                (leaf.current_capture_ids, "current"),
                (leaf.baseline_capture_ids, "baseline"),
            ):
                resolved = tuple(directory.get(capture_id) for capture_id in capture_ids)
                for capture_id, capture in zip(capture_ids, resolved, strict=True):
                    if capture is None or capture.endpoint not in (endpoint, "both"):
                        _fail(
                            "original leaves resolving to the correct fixed endpoint captures",
                            capture_id,
                        )
                if (
                    not native
                    and (
                        isinstance(leaf.reduction, FrameShapleyQuantile)
                        or leaf.reduction.reducer == "count_distinct"
                    )
                    and not any(
                        isinstance(capture, FrameShapleyCapture)
                        and capture.state_route
                        in ("typed_value_frequency/v1", "typed_sufficient_frequency/v1")
                        for capture in resolved
                    )
                ):
                    _fail(
                        "an exact frequency input among the original leaf's captured components",
                        repr(capture_ids),
                    )
        if self.captured_source_reads != sum(capture.source_reads for capture in self.captures):
            _fail(
                "aggregate read costs counting each actual query or capture once",
                self.left.column.name,
            )
        if native:
            approximate = any(
                (
                    isinstance(leaf.reduction, FrameShapleyNativeQuantile)
                    and leaf.reduction.reducer in ("approx_median", "approx_quantile")
                )
                or (
                    isinstance(leaf.reduction, FrameShapleyReduction)
                    and leaf.reduction.reducer == "approx_nunique"
                )
                for leaf in self.leaves
            )
            expected_approximation = (
                "native_approximate_metric/v1" if approximate else "exact_metric/v1"
            )
            if self.game.metric_approximation != expected_approximation:
                _fail(
                    "metric approximation matching the original native reducers",
                    self.game.metric_approximation,
                )
        else:
            if self.game.metric_approximation != state_metric_approximation(self.leaves):
                _fail(
                    "state metric approximation matching the original typed reducer leaves",
                    self.game.metric_approximation,
                )
            state_captures = tuple(
                capture for capture in self.captures if isinstance(capture, FrameShapleyCapture)
            )
            if self.state_entries != sum(capture.state_entries for capture in state_captures):
                _fail(
                    "aggregate costs counting each actual state capture once", self.left.column.name
                )
            routes = {capture.state_route for capture in state_captures}
            expected_route = (
                next(iter(routes)) if len(routes) == 1 else "typed_sufficient_frequency/v1"
            )
            if self.state_route != expected_route:
                _fail("the state route matching the actual captured inputs", self.state_route)

    def _summary(self) -> str:
        return f"kind=shapley, column={_short(self.left.column.name)!r}, players={_coefficient_text(self.player_count)}, permutations={_coefficient_text(self.permutations)}"

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)
        yield from self.game._lines()
        for leaf in self.leaves:
            yield repr(leaf)
            yield from leaf._lines()
        for capture in self.captures:
            yield repr(capture)
            yield from capture._lines()


@dataclass(frozen=True, slots=True, repr=False)
class FrameDivisionMethod(_FactRecord):
    """Explain captured division. Args: kind, totals, divisor, operation, method.

    Returns an inert ordered rule retaining the original typed divisor.
    Example: read ``metric.method.divisor`` when kind is ``division``.
    Constraints: divide retains child units; rescale declares the result unit.
    """

    kind: Literal["division"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    divisor: int | float | Decimal
    operation: Literal["divide", "rescale"]
    method: _ExactMethod

    def __post_init__(self) -> None:
        if self.kind != "division":
            _fail("kind='division'", repr(self.kind))
        _totals(self.left, self.right, self.change)
        _divisor(self.divisor, self.operation)
        _exact_method(self.method)
        if self.operation == "divide" and self.method.left.column.unit != self.left.column.unit:
            _fail("division preserving the child unit", self.left.column.name)
        if self.operation == "rescale" and self.left.column.unit is None:
            _fail("an explicit rescale result unit", self.left.column.name)

    def _summary(self) -> str:
        return (
            f"kind=division, column={_short(self.left.column.name)!r}, operation={self.operation}"
        )

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLinearTerm(_FactRecord):
    """Explain a consumed coefficient and subrule. Args: coefficient, method.

    Returns a frozen term; e.g. ``FrameLinearTerm(-1, rule)``. Coefficients are finite,
    exact int/float/Decimal scalars excluding bool; child order remains unchanged.
    """

    coefficient: int | float | Decimal
    method: _ExactMethod

    def __post_init__(self) -> None:
        if (
            type(self.coefficient) not in (int, float, Decimal)
            or (isinstance(self.coefficient, float) and not isfinite(self.coefficient))
            or (isinstance(self.coefficient, Decimal) and not self.coefficient.is_finite())
        ):
            _fail("a finite non-bool original numeric coefficient", type(self.coefficient).__name__)
        _exact_method(self.method)

    def _summary(self) -> str:
        return f"kind=linear_term, child={self.method.kind}, column={_short(self.method.left.column.name)!r}"

    def _lines(self) -> Iterator[str]:
        yield f"coefficient={_coefficient_text(self.coefficient)}"
        yield from _method_lines(self.method)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLinearMethod(_FactRecord):
    """Explain ordered linear terms. Args: kind, left, right, change, terms.

    Returns a frozen rule; e.g. ``FrameLinearMethod('linear', l, r, d, (term,))``.
    No offsets, expression rewriting or executable recipe is retained.
    """

    kind: Literal["linear"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    terms: tuple[FrameLinearTerm, ...]

    def __post_init__(self) -> None:
        if self.kind != "linear" or type(self.kind) is not str:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            _fail("kind='linear'", repr(self.kind))
        _totals(self.left, self.right, self.change)
        if (
            type(self.terms) is not tuple
            or not self.terms
            or any(type(t) is not FrameLinearTerm for t in self.terms)
        ):
            _fail("nonempty ordered immutable linear terms", type(self.terms).__name__)
        if any(term.method.left.column.unit != self.left.column.unit for term in self.terms):
            _fail("term units matching the linear total", self.left.column.name)

    def _summary(self) -> str:
        return f"kind=linear, column={_short(self.left.column.name)!r}, terms={len(self.terms)}"

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameAttributeMetric(_FactRecord):
    """Complete source-free target facts, including observed and reread totals."""

    target: tuple[str, ...]
    kind: Literal["level", "absolute", "relative"]
    method: (
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
        | FrameAdditiveMethod
        | FrameRatioMethod
        | FrameLinearMethod
        | FrameDivisionMethod
        | FrameShapleyMethod
    )
    observed_total: FrameMethodValue
    total: FrameMethodValue
    outputs: tuple[FrameMethodOutput, ...]
    share_state: Literal["defined", "zero_total"]

    @property
    def name(self) -> str:
        return ".".join(self.target)

    def __post_init__(self) -> None:
        if (
            type(self.target) is not tuple
            or not self.target
            or any(type(part) is not str or not part for part in self.target)
        ):
            _fail("the complete original target path", repr(self.target))
        if self.kind == "level":
            if not isinstance(
                self.method,
                (
                    FrameLevelAdditiveMethod,
                    FrameLevelRatioMethod,
                    FrameLevelLinearMethod,
                    FrameLevelDivisionMethod,
                ),
            ):
                _fail("a one-endpoint level rule", self.name)
            _level_method(self.method)
        elif self.kind in ("absolute", "relative"):
            if not isinstance(
                self.method,
                (
                    FrameAdditiveMethod,
                    FrameRatioMethod,
                    FrameLinearMethod,
                    FrameDivisionMethod,
                    FrameShapleyMethod,
                ),
            ):
                _fail("a two-endpoint change rule", self.name)
            _method(self.method)
        else:
            _fail("level, absolute or relative target", repr(self.kind))
        support_by_root: dict[tuple[str, str], tuple[str, int, int]] = {}

        def supports(method: _Method | _LevelMethod) -> Iterator[tuple[str, str, str, int, int]]:
            if isinstance(method, FrameShapleyMethod):
                return
            if isinstance(method, (FrameDivisionMethod, FrameLevelDivisionMethod)):
                yield from supports(method.method)
            elif isinstance(method, (FrameLinearMethod, FrameLevelLinearMethod)):
                for term in method.terms:
                    yield from supports(term.method)
            elif isinstance(method, (FrameLevelAdditiveMethod, FrameLevelRatioMethod)):
                for item in method.support:
                    yield item.root_id, "level", item.policy, item.input_count, item.effective_count
            else:
                for paired_support in method.support:
                    yield (
                        paired_support.root_id,
                        "current",
                        paired_support.policy,
                        paired_support.left_input_count,
                        paired_support.left_effective_count,
                    )
                    yield (
                        paired_support.root_id,
                        "baseline",
                        paired_support.policy,
                        paired_support.right_input_count,
                        paired_support.right_effective_count,
                    )

        for root, endpoint, policy, input_count, effective_count in supports(self.method):
            key = (root, endpoint)
            value = (policy, input_count, effective_count)
            if key in support_by_root and support_by_root[key] != value:
                _fail("consistent support for every shared root and endpoint", root)
            support_by_root[key] = value
        _total(self.total)
        if (
            type(self.observed_total) is not FrameMethodValue
            or self.observed_total.column.dtype != self.total.column.dtype
            or self.observed_total.column.unit != self.total.column.unit
        ):
            _fail(
                "observed total with the exact report carrier and unit",
                f"{self.name}: observed={self.observed_total.column.dtype!r}/{self.observed_total.column.unit!r}, total={self.total.column.dtype!r}/{self.total.column.unit!r}",
            )
        if type(self.outputs) is not tuple or any(
            type(item) is not FrameMethodOutput for item in self.outputs
        ):
            _fail("immutable closed output mappings", self.name)
        roles = tuple(item.role for item in self.outputs)
        if (
            roles[:4] != ("contribution", "total", "observed_total", "share")
            or len(set(roles)) != len(roles)
            or len({item.path for item in self.outputs}) != len(self.outputs)
        ):
            _fail("four ordered main outputs and unique diagnostic mappings", self.name)

        def effect_roles(method: _Method | _LevelMethod) -> set[str]:
            if isinstance(method, FrameRatioMethod):
                return {"effect_within", "effect_mix", "effect_entry_exit"}
            if isinstance(method, FrameDivisionMethod):
                return effect_roles(method.method)
            if isinstance(method, FrameLinearMethod):
                children = tuple(effect_roles(term.method) for term in method.terms)
                combined = set().union(*children)
                if combined and any(not child for child in children):
                    combined.add("effect_additive")
                return combined
            return set()

        expected_effects = effect_roles(self.method)
        actual_effects = {role for role in roles if role.startswith("effect_")}
        if expected_effects != actual_effects:
            _fail("exact effect mappings selected by the closed rule", self.name)
        if ("contribution_std_error" in roles) != isinstance(self.method, FrameShapleyMethod):
            _fail("sampling standard error exactly for the group-replacement rule", self.name)
        if self.share_state != ("zero_total" if self.total.value == 0 else "defined"):
            _fail("share state matching the exact reread total", self.name)

    def _summary(self) -> str:
        return f"kind={self.kind}, target={_short(self.name)!r}, share_state={self.share_state}"

    def _lines(self) -> Iterator[str]:
        yield f"observed_total={_cell(self.observed_total.value)}, total={_cell(self.total.value)}"
        yield repr(self.method)
        yield from self.method._lines()


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodParent(_FactRecord):
    """Explain a complete parent key and ordered metrics. Args: key, metrics.

    Returns a frozen parent; e.g. ``FrameMethodParent((), (metric,))``.
    The owning facts record validates key columns and the original metric directory.
    """

    key: tuple[FrameMethodValue, ...]
    metrics: tuple[FrameAttributeMetric, ...]

    def __post_init__(self) -> None:
        if type(self.key) is not tuple or any(type(v) is not FrameMethodValue for v in self.key):
            _fail("immutable complete typed parent keys", type(self.key).__name__)
        if (
            type(self.metrics) is not tuple
            or not self.metrics
            or any(type(m) is not FrameAttributeMetric for m in self.metrics)
        ):
            _fail("a nonempty immutable ordered metric tuple", type(self.metrics).__name__)
        if len({m.name for m in self.metrics}) != len(self.metrics):
            _fail("unique parent metric names", "duplicate metric")

    def __repr__(self) -> str:
        key = ", ".join(_cell(value.value) for value in self.key[:2])
        if len(self.key) > 2:
            key += ", ..."
        return f"FrameMethodParent(kind=parent, key=({_short(key, 90)}), metrics={len(self.metrics)}; .show())"

    def _summary(self) -> str:
        return f"kind=parent, key_columns={len(self.key)}, metrics={len(self.metrics)}"

    def _lines(self) -> Iterator[str]:
        yield "key: " + " | ".join(f"{_short(v.column.name)}={_cell(v.value)}" for v in self.key)
        for metric in self.metrics:
            yield repr(metric)
            yield from metric._lines()


def _coordinates(
    schema: DataSchema,
    coordinates: tuple[FrameMethodCoordinate, ...],
    parents: tuple[FrameMethodParent, ...],
    parent_axis_count: int,
    parent_schema: DataSchema,
) -> None:
    expected = dict(coordinate_attachment_schemas(schema))
    for name, attachment_schema in coordinate_attachment_schemas(parent_schema):
        if name in expected and expected[name] != attachment_schema:
            _fail("consistent original parent/output coordinate schemas", name)
        expected[name] = attachment_schema
    names = tuple(c.name for c in coordinates)
    if names != tuple(sorted(expected)):
        _fail("exact required coordinate snapshots in canonical name order", repr(names))
    if any(c.schema != expected[c.name] for c in coordinates):
        _fail("exact required coordinate snapshot schemas", repr(names))
    tables = {
        c.name: FrozenAttachment(c.name, c.schema, _arrow_table(c.schema, c.rows))
        for c in coordinates
    }

    def directory(
        axis: CalendarDataAxis | IntervalDataAxis,
    ) -> FrozenCalendarDirectory | FrozenIntervalDirectory:
        assert axis.frozen is not None
        table = tables[axis.frozen.directory_name]
        return (
            read_interval_directory(axis, table.schema, table.rows)
            if isinstance(axis, IntervalDataAxis)
            else read_calendar_directory(axis, table.schema, table.rows)
        )

    for index, axis in enumerate(schema.axes):
        if isinstance(axis, PairedDataAxis):
            table = tables[axis.mapping_name]
            validate_paired_mapping(
                axis, table.schema, table.rows, directory(axis.left), directory(axis.right)
            )
            domain = set(frozen_key_rows(table.rows, axis.columns))
        elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
            domain = {bucket.key for bucket in directory(axis).buckets}
        else:
            continue
        if index < parent_axis_count:
            start = sum(len(a.columns) for a in schema.axes[:index])
            values = tuple(
                tuple(v.value for v in parent.key[start : start + len(axis.columns)])
                for parent in parents
            )
            keys = frozen_key_rows(_arrow_table(DataSchema((axis,), ()), values), axis.columns)
            if any(key not in domain for key in keys):
                _fail("parent keys resolving in their original coordinate snapshot", axis.token)

    for comparison in parent_schema.comparisons:
        binding = comparison.binding
        if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            current = next(
                axis for axis in parent_schema.axes if axis.token == binding.current_axis_token
            )
            assert isinstance(current, (CalendarDataAxis, IntervalDataAxis))
            table = tables[binding.mapping_name]
            read_comparison_mapping(
                binding, table.schema, table.rows, directory(current), directory(binding.baseline)
            )


def _key_order(parent: FrameMethodParent) -> tuple[tuple[bool, _MethodScalar], ...]:
    # Every key position has one fixed physical type. Nulls sort last, as Frame rows do.
    return tuple((v.value is None, v.value) for v in parent.key)


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodFacts(_FactRecord):
    """Own one original decomposition's complete explanatory facts.

    Read an item from ``fixed_frame.method_facts``. The returned frozen record
    contains complete original output schema, coordinates and ordered parents.
    Direct construction is not a supported public entrypoint.
    Float64 values remain finite captured facts without arithmetic recomputation.
    Structure, types, units, storage integrity and exact non-floating arithmetic remain checked.
    Constraints: no source authority or current-result certification.
    """

    kind: Literal["attribute"]
    origin_result_id: str
    output_schema: DataSchema = field(
        metadata={
            "meaning": "Read facts.output_schema for the complete original saved output layout. Read-only schema metadata; nested axes, comparisons and frozen descriptors remain internal."
        }
    )
    coordinates: tuple[FrameMethodCoordinate, ...]
    parent_axis_count: int
    parents: tuple[FrameMethodParent, ...]
    parent_schema: DataSchema = field(
        metadata={
            "meaning": "Read facts.parent_schema for the original receiver layout and frozen endpoint correspondence. This descriptive schema retains no original value table, source declaration or execution authority."
        }
    )
    targets: tuple[FrameMethodTarget, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind != "attribute":
            _fail("kind='attribute'", repr(self.kind))
        _text(self.origin_result_id)
        if type(self.output_schema) is not DataSchema:
            _fail("the exact original output DataSchema", type(self.output_schema).__name__)
        if type(self.parent_axis_count) is not int or not 0 <= self.parent_axis_count <= len(
            self.output_schema.axes
        ):
            _fail(
                "parent_axis_count within the original axis directory", repr(self.parent_axis_count)
            )
        if type(self.parents) is not tuple or any(
            type(p) is not FrameMethodParent for p in self.parents
        ):
            _fail("immutable exact parent records", type(self.parents).__name__)
        if type(self.coordinates) is not tuple or any(
            type(c) is not FrameMethodCoordinate for c in self.coordinates
        ):
            _fail("immutable exact coordinate snapshots", type(self.coordinates).__name__)
        columns = tuple(
            c for axis in self.output_schema.axes[: self.parent_axis_count] for c in axis.columns
        )
        if (
            type(self.parent_schema) is not DataSchema
            or self.parent_schema.axes != self.output_schema.axes[: self.parent_axis_count]
        ):
            _fail(
                "the exact original parent schema with complete parent axes",
                type(self.parent_schema).__name__,
            )
        if (
            type(self.targets) is not tuple
            or not self.targets
            or any(type(target) is not FrameMethodTarget for target in self.targets)
        ):
            _fail(
                "a nonempty immutable closed original target directory", type(self.targets).__name__
            )
        self._attribute_targets(columns)

    def _attribute_targets(self, columns: tuple[DataColumn, ...]) -> None:
        directory = dict(
            zip(self.output_schema.value_paths, self.output_schema.values, strict=True)
        )
        presence = set(self.output_schema.presence_paths)
        if len({target.target for target in self.targets}) != len(self.targets):
            _fail("unique complete target paths", "duplicate target")
        targets = tuple(target.target for target in self.targets)
        mapping = tuple(target.outputs for target in self.targets)
        mapped_values: list[tuple[str, ...]] = []
        mapped_presence: set[tuple[str, ...]] = set()
        for target in self.targets:
            roles = {output.role for output in target.outputs}
            required = (
                {"value", "present"}
                if target.kind == "level"
                else {"current", "baseline", "absolute", "current_present", "baseline_present"}
            )
            if not required <= roles:
                _fail("complete diagnostics for the target endpoint shape", repr(target.target))
            typed = {}
            for output in target.outputs:
                if output.role in ("present", "current_present", "baseline_present"):
                    if output.path not in presence:
                        _fail("presence roles mapped only to presence columns", repr(output.path))
                    mapped_presence.add(output.path)
                else:
                    column = directory.get(output.path)
                    if column is None:
                        _fail(
                            "numeric report roles mapped only to value columns", repr(output.path)
                        )
                    if not (
                        isinstance(column.dtype, (DecimalDataType, DurationDataType))
                        or column.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
                    ):
                        _fail("numeric or Duration report values", column.name)
                    if output.role.startswith("valid_count") and (
                        column.dtype != ScalarDataType("int64") or column.unit not in (None, "1")
                    ):
                        _fail("integer dimensionless valid sample counts", column.name)
                    typed[output.role] = column
                    mapped_values.append(output.path)
            total = typed["total"]
            for main_role in (
                "contribution",
                "observed_total",
                *(effect_role for effect_role in typed if effect_role.startswith("effect_")),
            ):
                expected_type = (
                    ScalarDataType("float64")
                    if main_role == "contribution" and "contribution_std_error" in typed
                    else total.dtype
                )
                if typed[main_role].dtype != expected_type or typed[main_role].unit != total.unit:
                    _fail(
                        "main report outputs matching their native or sampled carrier and target unit",
                        repr(target.target),
                    )
            if "contribution_std_error" in typed:
                error = typed["contribution_std_error"]
                if error.dtype != ScalarDataType("float64") or error.unit != total.unit:
                    _fail("Float64 permutation standard error in the target unit", error.name)
            share = typed["share"]
            if isinstance(share.dtype, DurationDataType) or share.unit != (
                None if total.unit is None else "1"
            ):
                _fail("numeric dimensionless share output", share.name)
        if tuple(mapped_values) != self.output_schema.value_paths or mapped_presence != presence:
            _fail(
                "complete ordered value and presence output mapping", "missing or extra output path"
            )
        for parent in self.parents:
            if tuple(value.column for value in parent.key) != columns:
                _fail("complete original parent key columns", "parent key columns differ")
            metrics = parent.metrics
            current = tuple(metric.target for metric in metrics)
            paths = tuple(metric.outputs for metric in metrics)
            if (
                targets != current
                or mapping != paths
                or tuple(metric.kind for metric in metrics)
                != tuple(target.kind for target in self.targets)
            ):
                _fail(
                    "identical ordered target/output directory for all parents",
                    "inconsistent targets",
                )
            for metric in metrics:
                by_role: dict[str, tuple[str, ...]] = {
                    output.role: output.path for output in metric.outputs
                }
                rule = metric.method
                if isinstance(
                    rule,
                    (
                        FrameLevelAdditiveMethod,
                        FrameLevelRatioMethod,
                        FrameLevelLinearMethod,
                        FrameLevelDivisionMethod,
                    ),
                ):
                    expected_columns = {"value": rule.value.column}
                    required_presence: tuple[str, ...] = ("present",)
                    if isinstance(rule, FrameLevelRatioMethod):
                        expected_columns.update(
                            numerator=rule.numerator.column, denominator=rule.denominator.column
                        )
                else:
                    expected_columns = {
                        "current": rule.left.column,
                        "baseline": rule.right.column,
                        "absolute": rule.change.column,
                    }
                    required_presence = ("current_present", "baseline_present")
                    if isinstance(rule, FrameRatioMethod):
                        expected_columns.update(
                            numerator_current=rule.numerator_left.column,
                            numerator_baseline=rule.numerator_right.column,
                            denominator_current=rule.denominator_left.column,
                            denominator_baseline=rule.denominator_right.column,
                        )
                for presence_role in required_presence:
                    if presence_role not in by_role:
                        _fail("all endpoint presence diagnostics", metric.name)
                for diagnostic_role, column in expected_columns.items():
                    diagnostic = directory.get(by_role.get(diagnostic_role, ()))
                    if (
                        diagnostic is None
                        or diagnostic.dtype != column.dtype
                        or diagnostic.unit != column.unit
                        or diagnostic.nullable != column.nullable
                    ):
                        _fail(
                            "diagnostic columns matching exact captured rule carriers and units",
                            diagnostic_role,
                        )
                for output in metric.outputs:
                    if output.path not in directory and output.path not in presence:
                        _fail("output mappings resolving in the saved schema", repr(output.path))
                for total_role, value in (
                    ("total", metric.total),
                    ("observed_total", metric.observed_total),
                ):
                    path = next(
                        output.path for output in metric.outputs if output.role == total_role
                    )
                    if directory.get(path) != value.column:
                        _fail("exact typed report columns for parent totals", repr(path))
        if (
            len({parent.key for parent in self.parents}) != len(self.parents)
            or tuple(sorted(self.parents, key=_key_order)) != self.parents
        ):
            _fail("unique canonically ordered complete parent keys", "invalid parent ordering")
        _coordinates(
            self.output_schema,
            self.coordinates,
            self.parents,
            self.parent_axis_count,
            self.parent_schema,
        )

    def _summary(self) -> str:
        return f"kind=attribute, origin={_short(self.origin_result_id)!r}, parents={len(self.parents)}, coordinates={len(self.coordinates)}, parent_axes={self.parent_axis_count}"

    def _lines(self) -> Iterator[str]:
        for parent in self.parents:
            yield repr(parent)
            yield from parent._lines()
        for coordinate in self.coordinates:
            yield repr(coordinate)
            yield from coordinate._lines()
