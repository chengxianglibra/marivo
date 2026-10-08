"""Exact funnel components, period pairing and shared-basis ratio-mix allocation."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, TypeAlias

from pydantic import TypeAdapter

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.model import Cell, Defined, Undefined
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.methods.journey_matching import JourneyAssignment, Key

Axis: TypeAlias = str | int | None
Coordinates: TypeAlias = tuple[Axis, ...]
COUNTS = (
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    "coverage_censored_count",
)
RATES = ("conversion_from_first", "conversion_from_previous", "loss_rate_from_previous")
MAX_COUNT = 2**63 - 1


def count(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        fail(
            "funnel_count",
            "funnel components require checked nonnegative int64 counts",
            stage="consume",
        )
    return value


def axis_key(values: Coordinates) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, "") if v is None else (1, v) if type(v) is int else (2, str(v)) for v in values
    )


@dataclass(frozen=True, slots=True)
class Counts:
    cohort_count: int
    resolved_cohort_count: int
    entry_count: int
    resolved_entry_count: int
    reached_count: int
    lost_count: int
    coverage_censored_count: int

    def __post_init__(self) -> None:
        for name in COUNTS:
            count(getattr(self, name))
        if (
            self.resolved_entry_count != self.reached_count + self.lost_count
            or self.entry_count != self.resolved_entry_count + self.coverage_censored_count
            or not self.reached_count <= self.resolved_cohort_count <= self.cohort_count
            or self.entry_count > self.cohort_count
        ):
            fail(
                "funnel_count",
                "contradictory reached/lost/censored denominator components",
                stage="consume",
            )


ZERO = Counts(0, 0, 0, 0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class FunnelRow:
    step: int
    coordinates: Coordinates
    counts: Counts


@dataclass(frozen=True, slots=True)
class EntryAxisRow:
    event: str
    key: Key
    coordinates: Coordinates


@dataclass(frozen=True, slots=True)
class EntryAxisState:
    rows: tuple[EntryAxisRow, ...]
    authority: str


@dataclass(frozen=True, slots=True)
class FunnelState:
    assignments: tuple[JourneyAssignment, ...]
    axis_rows: tuple[EntryAxisRow, ...]
    complete: bool
    authority: str
    coverage: str


@dataclass(frozen=True, slots=True)
class ComparisonState:
    current: FunnelState
    baseline: FunnelState


@dataclass(frozen=True, slots=True)
class AllocationState:
    original: ComparisonState
    expanded: ComparisonState


AXES_STATE = TypeAdapter(EntryAxisState)
FUNNEL_STATE = TypeAdapter(FunnelState)
COMPARISON_STATE = TypeAdapter(ComparisonState)
ALLOCATION_STATE = TypeAdapter(AllocationState)


def components(state: FunnelState, steps: int, axis_count: int) -> tuple[FunnelRow, ...]:
    check()
    axes = {(r.event, r.key): r.coordinates for r in state.axis_rows}
    if len(axes) != len(state.axis_rows) or any(len(v) != axis_count for v in axes.values()):
        fail("funnel_axes", "entry-axis capture has duplicate or incomplete keys", stage="consume")
    groups: dict[Coordinates, list[JourneyAssignment]] = {}
    subjects: set[Key] = set()
    for item in state.assignments:
        check()
        if item.subject in subjects or len(item.reach) != steps:
            fail(
                "funnel_binding",
                "funnel requires the exact first-per-subject assignment",
                stage="consume",
            )
        subjects.add(item.subject)
        coordinate = axes.get((item.start.event, item.start.key)) if axis_count else ()
        if coordinate is None:
            fail(
                "funnel_axes",
                "retained entry axes are missing for an actual start",
                stage="consume",
            )
        groups.setdefault(coordinate, []).append(item)
    if not axis_count and not groups:
        groups[()] = []
    output = []
    for coordinate in sorted(groups, key=axis_key):
        journeys = groups[coordinate]
        for step in range(steps):
            check()
            entered = [j for j in journeys if step == 0 or j.reach[step - 1] == "reached"]
            reached = sum(j.reach[step] == "reached" for j in entered)
            lost = sum(j.reach[step] == "unreachable" for j in entered)
            censored = sum(j.reach[step] == "unknown" for j in entered)
            output.append(
                FunnelRow(
                    step,
                    coordinate,
                    Counts(
                        len(journeys),
                        sum(j.reach[step] != "unknown" for j in journeys),
                        len(entered),
                        reached + lost,
                        reached,
                        lost,
                        censored,
                    ),
                )
            )
    return tuple(output)


def ratio(numerator: int, denominator: int) -> Cell:
    if denominator == 0:
        return Undefined("zero_denominator")
    return Defined(float(Fraction(numerator, denominator)))


def rate(row: FunnelRow, field: str) -> Cell:
    c = row.counts
    if field == "conversion_from_first":
        return ratio(c.reached_count, c.resolved_cohort_count)
    if field == "conversion_from_previous":
        return ratio(c.reached_count, c.resolved_entry_count)
    if field == "loss_rate_from_previous":
        return (
            Undefined("initial_step")
            if row.step == 0
            else ratio(c.lost_count, c.resolved_entry_count)
        )
    raise ValueError("unregistered funnel rate")


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    step: int
    coordinates: Coordinates
    presence: Literal["matched", "current_only", "baseline_only"]
    current: FunnelRow
    baseline: FunnelRow

    @property
    def delta(self) -> Cell:
        left, right = (
            rate(self.current, "loss_rate_from_previous"),
            rate(self.baseline, "loss_rate_from_previous"),
        )
        if not isinstance(left, Defined):
            return left
        if not isinstance(right, Defined):
            return right
        a, b = self.current.counts, self.baseline.counts
        return Defined(
            float(
                Fraction(a.lost_count, a.resolved_entry_count)
                - Fraction(b.lost_count, b.resolved_entry_count)
            )
        )


def compare(state: ComparisonState, steps: int, axis_count: int) -> tuple[ComparisonRow, ...]:
    if not state.current.complete or not state.baseline.complete:
        fail(
            "funnel_coverage",
            "period comparison requires complete relevant follow-up on both captures",
            stage="consume",
        )
    endpoints = [components(s, steps, axis_count) for s in (state.current, state.baseline)]
    if any(row.counts.coverage_censored_count for rows in endpoints for row in rows):
        fail(
            "funnel_coverage",
            "a censored assignment cannot enter funnel-period comparison",
            stage="consume",
        )
    left, right = ({(r.step, r.coordinates): r for r in rows} for rows in endpoints)
    output = []
    for step, coordinate in sorted(
        left.keys() | right.keys(), key=lambda k: (k[0], axis_key(k[1]))
    ):
        check()
        key = (step, coordinate)
        output.append(
            ComparisonRow(
                step,
                coordinate,
                "matched"
                if key in left and key in right
                else "current_only"
                if key in left
                else "baseline_only",
                left.get(key, FunnelRow(step, coordinate, ZERO)),
                right.get(key, FunnelRow(step, coordinate, ZERO)),
            )
        )
    return tuple(output)


@dataclass(frozen=True, slots=True)
class AllocationRow:
    resolution: int
    coordinates: Coordinates
    other_mask: tuple[bool, ...]
    kind: Literal["loss", "denominator_mix"]
    current: float
    baseline: float
    contribution: float
    target: float
    current_error_bound: float
    baseline_error_bound: float
    contribution_error_bound: float
    share_total: float | None
    share_positive: float | None
    share_negative: float | None
    rank: int


def finish(value: Fraction) -> tuple[float, float]:
    from marivo.analysis.methods.comparison import _finish
    from marivo.analysis.methods.physical import ScalarType

    result = _finish(value, ScalarType("float64"))
    assert isinstance(result, float)
    error = abs(Fraction(result) - value)
    bound = float(error)
    if Fraction(bound) < error:
        from math import inf, nextafter

        bound = nextafter(bound, inf)
    return result, bound


def allocate(
    state: AllocationState,
    *,
    steps: int,
    axes: int,
    target_step: int,
    mode: Literal["joint", "hierarchy"],
    top_k: int | None,
    original_axes: int,
) -> tuple[AllocationRow, ...]:
    from marivo.analysis.methods.attribution import reconciles
    from marivo.analysis.methods.numeric_state import Number

    compare(state.original, steps, original_axes)
    paired = tuple(r for r in compare(state.expanded, steps, axes) if r.step == target_step)
    lc, lb, ec, eb = (
        count(sum(getattr(getattr(r, side).counts, field) for r in paired))
        for side, field in (
            ("current", "lost_count"),
            ("baseline", "lost_count"),
            ("current", "resolved_entry_count"),
            ("baseline", "resolved_entry_count"),
        )
    )
    if ec == 0 or eb == 0:
        fail(
            "funnel_allocation",
            "both total resolved-entry denominators must be positive",
            stage="consume",
        )
    original = tuple(
        r for r in compare(state.original, steps, original_axes) if r.step == target_step
    )
    expected = tuple(
        sum(getattr(getattr(r, side).counts, field) for r in original)
        for side, field in (
            ("current", "lost_count"),
            ("baseline", "lost_count"),
            ("current", "resolved_entry_count"),
            ("baseline", "resolved_entry_count"),
        )
    )
    if expected != (lc, lb, ec, eb):
        fail(
            "funnel_allocation",
            "expanded axis components do not reproduce the original target",
            stage="consume",
        )
    mapping: dict[Coordinates, tuple[tuple[Axis, bool], ...]] = {r.coordinates: () for r in paired}
    for index in range(axes):
        scores: dict[tuple[tuple[Axis, bool], ...], dict[Axis, int]] = {}
        for row in paired:
            prefix = mapping[row.coordinates]
            children = scores.setdefault(prefix, {})
            value = row.coordinates[index]
            children[value] = count(
                children.get(value, 0)
                + row.current.counts.resolved_entry_count
                + row.baseline.counts.resolved_entry_count
            )
        chosen = {
            prefix: set(
                sorted(children, key=lambda value: (-children[value], axis_key((value,))))[:top_k]
            )
            if top_k is not None
            else set(children)
            for prefix, children in scores.items()
        }
        for row in paired:
            prefix = mapping[row.coordinates]
            value = row.coordinates[index]
            mapping[row.coordinates] = (
                *prefix,
                (value, False) if value in chosen[prefix] else (None, True),
            )
    target = Fraction(lc, ec) - Fraction(lb, eb)
    output = []
    for resolution in range(1, axes + 1) if mode == "hierarchy" else (axes,):
        check()
        grouped: dict[tuple[tuple[Axis, bool], ...], tuple[int, int]] = {}
        for row in paired:
            key = mapping[row.coordinates][:resolution]
            old = grouped.get(key, (0, 0))
            grouped[key] = (
                count(old[0] + row.current.counts.lost_count),
                count(old[1] + row.baseline.counts.lost_count),
            )
        exact: list[tuple[Coordinates, tuple[bool, ...], str, Fraction, Fraction, Fraction]] = []
        for coordinate, (a, b) in grouped.items():
            values = tuple(v for v, _ in coordinate) + (None,) * (axes - resolution)
            mask = tuple(other for _, other in coordinate) + (False,) * (axes - resolution)
            mix = Fraction(b) * (Fraction(1, ec) - Fraction(1, eb))
            exact.extend(
                (
                    (values, mask, "loss", Fraction(a, ec), Fraction(b, ec), Fraction(a - b, ec)),
                    (values, mask, "denominator_mix", Fraction(0), -mix, mix),
                )
            )
        exact.sort(key=lambda r: (-abs(r[5]), axis_key(r[0]), r[1], r[2]))
        positive = sum((r[5] for r in exact if r[5] > 0), Fraction())
        negative = sum((-r[5] for r in exact if r[5] < 0), Fraction())
        local = []
        for rank, (
            allocated_coordinate,
            mask,
            kind,
            exact_current,
            exact_baseline,
            contribution,
        ) in enumerate(exact, 1):
            current, ae = finish(exact_current)
            baseline, be = finish(exact_baseline)
            allocated, error = finish(contribution)
            local.append(
                AllocationRow(
                    resolution,
                    allocated_coordinate,
                    mask,
                    "loss" if kind == "loss" else "denominator_mix",
                    current,
                    baseline,
                    allocated,
                    finish(target)[0],
                    ae,
                    be,
                    error,
                    None if target == 0 else finish(contribution / target)[0],
                    None if positive == 0 else finish(max(contribution, Fraction()) / positive)[0],
                    None if negative == 0 else finish(max(-contribution, Fraction()) / negative)[0],
                    rank,
                )
            )

        def reconciled(expected_value: Fraction, actual: list[float]) -> bool:
            numeric: list[Number] = list(actual)
            return reconciles(expected_value, numeric, False)

        if not all(
            reconciled(expected_value, actual)
            for expected_value, actual in (
                (target, [float(r.contribution) for r in local]),
                (Fraction(lc, ec), [r.current for r in local]),
                (Fraction(lb, eb), [r.baseline for r in local]),
            )
        ):
            fail(
                "funnel_reconciliation",
                "allocated sides or target failed per-resolution reconciliation",
                stage="consume",
            )
        output.extend(local)
    return tuple(output)
