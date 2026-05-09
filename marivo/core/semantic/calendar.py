"""Pure calendar alignment computation for the semantic layer.

Extracted from ``marivo.analysis_core.calendar_*`` modules as part of Phase 3c.

This module contains all pure computation for calendar alignment:
- Baseline window resolution
- Calendar date shifting
- Bucket pairing resolution
- Calendar annotation row building
- Policy definitions and resolution
- Strictness level and rollup-safety evaluation

The I/O-bound parts (loading holiday/event data from databases) remain in
``marivo.analysis_core``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Calendar policy types (from calendar_policy.py)
# ---------------------------------------------------------------------------

CalendarComparisonBasis = Literal["yoy", "mom", "wow"]
CalendarPolicyRef = Literal[
    "calendar_policy.natural_yoy",
    "calendar_policy.weekday_yoy",
    "calendar_policy.calendar_yoy",
    "calendar_policy.natural_mom",
    "calendar_policy.weekday_mom",
    "calendar_policy.calendar_mom",
    "calendar_policy.weekday_wow",
]
ResolutionSource = Literal["explicit_request", "injected_binding", "planner_candidate"]
CalendarTieBreaker = Literal["prefer_backward", "prefer_forward"]


@dataclass(frozen=True, slots=True)
class CalendarBaselineGenerationRule:
    strategy: Literal["previous_year", "previous_period"]
    offset_value: int | None = None
    offset_unit: Literal["day", "week", "month", "quarter", "year"] | None = None


@dataclass(frozen=True, slots=True)
class CalendarMatchingStep:
    matcher: Literal[
        "holiday_cluster",
        "year_relative_holiday_key",
        "event_cluster",
        "year_relative_event_key",
        "same_weekday_nearest",
        "natural_date_shift",
    ]
    requires_annotation: bool
    tie_breaker: CalendarTieBreaker | None = None
    max_shift_days: int | None = None


@dataclass(frozen=True, slots=True)
class CalendarPolicyDefinition:
    policy_ref: CalendarPolicyRef
    comparison_basis: CalendarComparisonBasis
    window_tags: tuple[str, ...]
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    resolved_alignment_mode: str
    resolved_baseline_generation_rule: CalendarBaselineGenerationRule
    matching_strategy: tuple[CalendarMatchingStep, ...]
    fallback_strategy: tuple[str, ...]
    coverage_behavior: str


@dataclass(frozen=True, slots=True)
class ResolvedCalendarPolicyBinding:
    policy: CalendarPolicyDefinition
    resolution_source: ResolutionSource


@dataclass(frozen=True, slots=True)
class CalendarPolicyCatalogEntry:
    policy_ref: CalendarPolicyRef
    object_id: str
    name: str
    display_name: str
    description: str
    comparison_basis: CalendarComparisonBasis
    resolved_alignment_mode: str
    window_tags: tuple[str, ...]
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    matching_strategy_summary: tuple[str, ...]
    fallback_strategy: tuple[str, ...]
    coverage_behavior: str
    detail_path: str | None
    resolve_path: str | None
    status: Literal["published"] = "published"
    lifecycle_status: Literal["active"] = "active"
    readiness_status: Literal["ready"] = "ready"
    blocker_count: Literal[0] = 0
    revision: Literal[1] = 1
    created_at: Literal["builtin"] = "builtin"
    updated_at: Literal["builtin"] = "builtin"
    system_managed: Literal[True] = True
    catalog_source: Literal["builtin_calendar_policy"] = "builtin_calendar_policy"


class CalendarPolicyResolutionError(ValueError):
    def __init__(
        self, message: str, *, code: str, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


# ---------------------------------------------------------------------------
# Calendar pairing types (from calendar_alignment_pairing.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalendarAnnotationRow:
    calendar_date: date
    weekday: int
    holiday_group_id: str | None = None
    year_relative_holiday_key: str | None = None
    event_group_id: str | None = None
    year_relative_event_key: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarPairingResolution:
    bucket_pairing: list[dict[str, Any]]
    comparability_warnings: list[str]
    rollup_safe: bool


# ---------------------------------------------------------------------------
# Baseline window resolution (from calendar_alignment_baseline.py)
# ---------------------------------------------------------------------------


def resolve_calendar_baseline_window(
    *,
    current_window: tuple[date, date],
    rule: CalendarBaselineGenerationRule,
) -> tuple[date, date]:
    """Resolve the baseline window from a current window and generation rule."""
    current_start, current_end = current_window
    if rule.strategy == "previous_year":
        shift_years = -(rule.offset_value or 1)
        return (
            shift_calendar_date(current_start, years=shift_years),
            shift_calendar_date(current_end, years=shift_years),
        )
    if rule.strategy == "previous_period":
        if rule.offset_unit == "week":
            shift_days = 7 * (rule.offset_value or 1)
            delta = timedelta(days=shift_days)
            return current_start - delta, current_end - delta
        period_days = current_end - current_start
        return current_start - period_days, current_end - period_days
    raise ValueError(f"Unsupported calendar baseline strategy '{rule.strategy}'")


def shift_calendar_date(d: date, *, months: int = 0, years: int = 0) -> date:
    """Shift a calendar date by the given number of months and/or years.

    Handles month-end clamping (e.g. Jan 31 + 1 month -> Feb 28/29).
    """
    from calendar import monthrange

    target_month = d.month + months
    target_year = d.year + years + (target_month - 1) // 12
    target_month = (target_month - 1) % 12 + 1
    target_day = min(d.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


# ---------------------------------------------------------------------------
# Bucket pairing resolution (from calendar_alignment_pairing.py)
# ---------------------------------------------------------------------------

_WARNING_ISSUE_CODES = frozenset(
    {
        "holiday_cluster_unmapped",
        "event_cluster_unmapped",
        "fallback_applied",
    }
)


def resolve_calendar_bucket_pairing(
    *,
    current_window: tuple[date, date],
    baseline_window: tuple[date, date],
    matching_strategy: tuple[CalendarMatchingStep, ...],
    fallback_strategy: tuple[str, ...],
    annotation_rows: Sequence[CalendarAnnotationRow],
) -> CalendarPairingResolution:
    """Resolve bucket pairing between current and baseline windows.

    Pure computation: takes pre-loaded annotation rows (the caller handles
    database I/O for holiday/event data) and produces the pairing resolution.
    """
    resolver = _CalendarPairingResolver(
        current_window=current_window,
        baseline_window=baseline_window,
        matching_strategy=matching_strategy,
        fallback_strategy=fallback_strategy,
        annotation_rows=annotation_rows,
    )
    return resolver.resolve()


def build_calendar_annotation_rows(
    *,
    current_window: tuple[date, date],
    baseline_window: tuple[date, date],
    raw_rows: Sequence[Mapping[str, Any]] | None,
) -> list[CalendarAnnotationRow]:
    """Build annotation rows from raw database rows.

    This function fills in missing dates with default rows (weekday-only).
    The ``raw_rows`` parameter comes from database I/O, but the function
    itself is pure computation.
    """
    rows_by_date: dict[date, CalendarAnnotationRow] = {}
    for raw_row in raw_rows or ():
        row = _coerce_annotation_row(raw_row)
        rows_by_date[row.calendar_date] = row

    all_rows: list[CalendarAnnotationRow] = []
    seen_dates: set[date] = set()
    for window in (baseline_window, current_window):
        cursor = window[0]
        while cursor < window[1]:
            if cursor not in seen_dates:
                seen_dates.add(cursor)
                all_rows.append(
                    rows_by_date.get(
                        cursor,
                        CalendarAnnotationRow(
                            calendar_date=cursor,
                            weekday=cursor.weekday() + 1,
                        ),
                    )
                )
            cursor += timedelta(days=1)
    return all_rows


def strictness_level_for_bucket(
    *,
    issues: Any,
    is_reused_baseline_bucket: bool,
) -> str:
    """Compute the strictness level for a single bucket pairing."""
    issue_list = (
        [issue for issue in issues if isinstance(issue, str)] if isinstance(issues, list) else []
    )
    if "alignment_coverage_insufficient" in issue_list:
        return "coverage_incomplete"
    if is_reused_baseline_bucket:
        return "reused_baseline"
    if "fallback_applied" in issue_list:
        return "fallback"
    return "strict"


def is_rollup_safe(pairings: Sequence[Mapping[str, Any]]) -> bool:
    """Check whether all bucket pairings are strict (rollup-safe)."""
    for pairing in pairings:
        strictness_level = pairing.get("strictness_level")
        if strictness_level != "strict":
            return False
    return True


# ---------------------------------------------------------------------------
# Policy definitions and resolution (from calendar_policy.py)
# ---------------------------------------------------------------------------

_POLICIES: tuple[CalendarPolicyDefinition, ...] = (
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.natural_yoy",
        comparison_basis="yoy",
        window_tags=("natural_date",),
        use_when=("普通同比", "未提节假日", "未提活动窗口"),
        avoid_when=("明确要求周几对周几", "明确要求节假日口径", "明确要求活动口径"),
        resolved_alignment_mode="natural_date",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_year",
            offset_value=1,
            offset_unit="year",
        ),
        matching_strategy=(CalendarMatchingStep("natural_date_shift", requires_annotation=False),),
        fallback_strategy=(),
        coverage_behavior="require_full_natural_date_pairing",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.weekday_yoy",
        comparison_basis="yoy",
        window_tags=("same_weekday",),
        use_when=("工作日效应强", "周一对周一", "周末对周末"),
        avoid_when=("明确要求节假日窗口", "明确要求活动窗口"),
        resolved_alignment_mode="same_weekday",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_year",
            offset_value=1,
            offset_unit="year",
        ),
        matching_strategy=(
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("natural_date_shift",),
        coverage_behavior="warn_when_weekday_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.calendar_yoy",
        comparison_basis="yoy",
        window_tags=("calendar_aware", "event_cluster", "holiday_cluster", "same_weekday_fallback"),
        use_when=("节假日", "活动窗口", "春节", "618", "双11", "同比需日历对齐"),
        avoid_when=(),
        resolved_alignment_mode="calendar_aware",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_year",
            offset_value=1,
            offset_unit="year",
        ),
        matching_strategy=(
            CalendarMatchingStep("event_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_event_key", requires_annotation=True),
            CalendarMatchingStep("holiday_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_holiday_key", requires_annotation=True),
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("same_weekday_nearest", "natural_date_shift"),
        coverage_behavior="warn_when_calendar_annotation_missing_or_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.natural_mom",
        comparison_basis="mom",
        window_tags=("natural_date",),
        use_when=("普通月环比", "上月对本月", "未提活动窗口"),
        avoid_when=("明确要求周几对齐", "明确要求活动窗口"),
        resolved_alignment_mode="natural_date",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
        ),
        matching_strategy=(CalendarMatchingStep("natural_date_shift", requires_annotation=False),),
        fallback_strategy=(),
        coverage_behavior="require_full_natural_date_pairing",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.weekday_mom",
        comparison_basis="mom",
        window_tags=("same_weekday",),
        use_when=("周几对齐月环比", "工作日效应强"),
        avoid_when=("明确要求活动窗口",),
        resolved_alignment_mode="same_weekday",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
        ),
        matching_strategy=(
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("natural_date_shift",),
        coverage_behavior="warn_when_weekday_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.calendar_mom",
        comparison_basis="mom",
        window_tags=("calendar_aware", "event_cluster", "holiday_cluster"),
        use_when=("活动期月环比", "节假日月环比", "活动窗口对活动窗口"),
        avoid_when=("普通自然月环比",),
        resolved_alignment_mode="calendar_aware",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
        ),
        matching_strategy=(
            CalendarMatchingStep("event_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_event_key", requires_annotation=True),
            CalendarMatchingStep("holiday_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_holiday_key", requires_annotation=True),
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("same_weekday_nearest", "natural_date_shift"),
        coverage_behavior="warn_when_calendar_annotation_missing_or_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.weekday_wow",
        comparison_basis="wow",
        window_tags=("same_weekday", "weekly_period"),
        use_when=("周环比", "上周同周几", "工作日/周末结构需稳定"),
        avoid_when=("月环比", "同比", "活动窗口优先"),
        resolved_alignment_mode="same_weekday",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
            offset_value=1,
            offset_unit="week",
        ),
        matching_strategy=(
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
        ),
        fallback_strategy=(),
        coverage_behavior="require_same_weekday_pairing",
    ),
)

_POLICY_BY_REF: dict[str, CalendarPolicyDefinition] = {
    policy.policy_ref: policy for policy in _POLICIES
}

_BASIS_LABELS: dict[CalendarComparisonBasis, str] = {
    "yoy": "year-over-year",
    "mom": "month-over-month",
    "wow": "week-over-week",
}


def get_calendar_policy(policy_ref: str) -> CalendarPolicyDefinition:
    """Look up a calendar policy by its ref string."""
    policy = _POLICY_BY_REF.get(policy_ref)
    if policy is None:
        raise CalendarPolicyResolutionError(
            f"Unknown calendar_policy_ref '{policy_ref}'",
            code="calendar_policy_unknown",
            details={
                "policy_ref": policy_ref,
                "allowed_policy_refs": sorted(_POLICY_BY_REF),
            },
        )
    return policy


def list_calendar_policies() -> tuple[CalendarPolicyDefinition, ...]:
    """Return all builtin calendar policy definitions."""
    return _POLICIES


def validate_calendar_policy_ref(
    policy_ref: str | None,
    *,
    comparison_basis: CalendarComparisonBasis | None = None,
) -> str | None:
    """Validate a calendar policy ref against a comparison basis."""
    if policy_ref is None:
        return None
    policy = get_calendar_policy(policy_ref)
    if comparison_basis is not None and policy.comparison_basis != comparison_basis:
        raise CalendarPolicyResolutionError(
            (
                f"calendar_policy_ref '{policy_ref}' is not valid for comparison_basis "
                f"'{comparison_basis}'"
            ),
            code="calendar_policy_basis_mismatch",
            details={
                "policy_ref": policy_ref,
                "comparison_basis": comparison_basis,
                "policy_comparison_basis": policy.comparison_basis,
            },
        )
    return policy.policy_ref


def resolve_calendar_policy(
    *,
    explicit_policy_ref: str | None = None,
    injected_policy_ref: str | None = None,
    planner_candidate_refs: list[str] | None = None,
    comparison_basis: CalendarComparisonBasis | None = None,
    required: bool = False,
) -> ResolvedCalendarPolicyBinding | None:
    """Resolve a calendar policy from explicit, injected, or planner sources."""
    explicit = validate_calendar_policy_ref(explicit_policy_ref, comparison_basis=comparison_basis)
    if explicit is not None:
        return ResolvedCalendarPolicyBinding(
            policy=get_calendar_policy(explicit),
            resolution_source="explicit_request",
        )

    injected = validate_calendar_policy_ref(injected_policy_ref, comparison_basis=comparison_basis)
    if injected is not None:
        return ResolvedCalendarPolicyBinding(
            policy=get_calendar_policy(injected),
            resolution_source="injected_binding",
        )

    unique_candidates: list[str] = []
    for candidate in planner_candidate_refs or []:
        normalized_candidate = validate_calendar_policy_ref(
            candidate,
            comparison_basis=comparison_basis,
        )
        if normalized_candidate is not None and normalized_candidate not in unique_candidates:
            unique_candidates.append(normalized_candidate)

    if len(unique_candidates) == 1:
        return ResolvedCalendarPolicyBinding(
            policy=get_calendar_policy(unique_candidates[0]),
            resolution_source="planner_candidate",
        )
    if len(unique_candidates) > 1:
        raise CalendarPolicyResolutionError(
            "Multiple planner calendar policy candidates are equally valid",
            code="calendar_policy_ambiguous",
            details={
                "comparison_basis": comparison_basis,
                "candidate_policy_refs": unique_candidates,
            },
        )
    if required:
        raise CalendarPolicyResolutionError(
            "A calendar policy is required but none could be resolved",
            code="calendar_policy_missing",
            details={"comparison_basis": comparison_basis},
        )
    return None


def calendar_policy_catalog_entry(policy_ref: str) -> CalendarPolicyCatalogEntry:
    """Build a catalog entry for a calendar policy."""
    policy = get_calendar_policy(policy_ref)
    return CalendarPolicyCatalogEntry(
        policy_ref=policy.policy_ref,
        object_id=policy.policy_ref,
        name=policy.policy_ref.split(".", 1)[1],
        display_name=f"{policy.policy_ref.split('.', 1)[1].replace('_', ' ')} ({_BASIS_LABELS[policy.comparison_basis]})",
        description=(
            f"Builtin calendar alignment policy for {policy.comparison_basis} comparisons using "
            f"{policy.resolved_alignment_mode}; use when {', '.join(policy.use_when[:2]) if policy.use_when else 'calendar-aligned comparison'}."
        ),
        comparison_basis=policy.comparison_basis,
        resolved_alignment_mode=policy.resolved_alignment_mode,
        window_tags=policy.window_tags,
        use_when=policy.use_when,
        avoid_when=policy.avoid_when,
        matching_strategy_summary=tuple(
            _matching_step_summary(step) for step in policy.matching_strategy
        ),
        fallback_strategy=policy.fallback_strategy,
        coverage_behavior=policy.coverage_behavior,
        detail_path=None,
        resolve_path=None,
    )


def list_calendar_policy_catalog_entries() -> tuple[CalendarPolicyCatalogEntry, ...]:
    """Return catalog entries for all builtin calendar policies."""
    return tuple(calendar_policy_catalog_entry(policy.policy_ref) for policy in _POLICIES)


def policy_registry_summary(
    *,
    comparison_basis: CalendarComparisonBasis | None = None,
) -> list[dict[str, object]]:
    """Return a summary dict of all (or basis-filtered) policies."""
    policies = _POLICIES
    if comparison_basis is not None:
        policies = tuple(
            policy for policy in policies if policy.comparison_basis == comparison_basis
        )
    return [
        {
            "policy_ref": policy.policy_ref,
            "comparison_basis": policy.comparison_basis,
            "window_tags": list(policy.window_tags),
            "use_when": list(policy.use_when),
            "avoid_when": list(policy.avoid_when),
        }
        for policy in policies
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _matching_step_summary(step: CalendarMatchingStep) -> str:
    summary: str = step.matcher
    if step.max_shift_days is not None:
        summary = f"{summary}(max_shift_days={step.max_shift_days})"
    if step.tie_breaker is not None:
        summary = f"{summary}[{step.tie_breaker}]"
    if step.requires_annotation:
        summary = f"{summary}:annotation_required"
    return summary


def _coerce_annotation_row(raw_row: Mapping[str, Any]) -> CalendarAnnotationRow:
    raw_date = raw_row.get("calendar_date")
    if isinstance(raw_date, date):
        calendar_date = raw_date
    else:
        calendar_date = date.fromisoformat(str(raw_date or "")[:10])
    weekday = int(raw_row.get("weekday") or 0)
    if weekday < 1 or weekday > 7:
        raise ValueError("calendar annotation weekday must be in 1..7")
    return CalendarAnnotationRow(
        calendar_date=calendar_date,
        weekday=weekday,
        holiday_group_id=_optional_str(raw_row.get("holiday_group_id")),
        year_relative_holiday_key=_optional_str(raw_row.get("year_relative_holiday_key")),
        event_group_id=_optional_str(raw_row.get("event_group_id")),
        year_relative_event_key=_optional_str(raw_row.get("year_relative_event_key")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class _CalendarPairingResolver:
    """Internal resolver for calendar bucket pairing."""

    def __init__(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        matching_strategy: tuple[CalendarMatchingStep, ...],
        fallback_strategy: tuple[str, ...],
        annotation_rows: Sequence[CalendarAnnotationRow],
    ) -> None:
        self.current_window = current_window
        self.baseline_window = baseline_window
        self.matching_strategy = matching_strategy
        self.fallback_strategy = fallback_strategy
        self.rows_by_date = {row.calendar_date: row for row in annotation_rows}
        self.baseline_rows = self._window_rows(baseline_window)
        (
            self.baseline_by_holiday_group,
            self.duplicate_holiday_groups,
        ) = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.holiday_group_id,
        )
        self.baseline_by_holiday_key, _ = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.year_relative_holiday_key,
        )
        self.baseline_by_event_group, self.duplicate_event_groups = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.event_group_id,
        )
        self.baseline_by_event_key, _ = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.year_relative_event_key,
        )

    def resolve(self) -> CalendarPairingResolution:
        pairings: list[dict[str, Any]] = []
        comparability_warnings: list[str] = []
        cursor = self.current_window[0]
        index = 0
        while cursor < self.current_window[1]:
            pairing = self._resolve_one_bucket(current_day=cursor, offset=index)
            pairings.append(pairing)
            for issue_code in pairing["issues"]:
                if issue_code in _WARNING_ISSUE_CODES and issue_code not in comparability_warnings:
                    comparability_warnings.append(issue_code)
            cursor += timedelta(days=1)
            index += 1
        _finalize_bucket_pairing_metadata(pairings)
        return CalendarPairingResolution(
            bucket_pairing=pairings,
            comparability_warnings=comparability_warnings,
            rollup_safe=is_rollup_safe(pairings),
        )

    def _resolve_one_bucket(self, *, current_day: date, offset: int) -> dict[str, Any]:
        current_row = self.rows_by_date.get(
            current_day,
            CalendarAnnotationRow(calendar_date=current_day, weekday=current_day.weekday() + 1),
        )
        issues: list[str] = []
        fallback_started = False
        baseline_day: date | None = None
        pairing_reason: str | None = None

        for step_index, step in enumerate(self.matching_strategy):
            baseline_day, step_issues = self._apply_matcher(
                step=step,
                current_row=current_row,
                offset=offset,
            )
            for issue in step_issues:
                if issue not in issues:
                    issues.append(issue)
            if baseline_day is None:
                continue
            if self._is_fallback_match(step_index) and not fallback_started:
                fallback_started = True
                if "fallback_applied" not in issues:
                    issues.append("fallback_applied")
            pairing_reason = step.matcher
            break

        if baseline_day is None:
            issues.append("alignment_coverage_insufficient")

        return {
            "current_bucket_start": current_day.isoformat(),
            "baseline_bucket_start": baseline_day.isoformat() if baseline_day is not None else None,
            "pairing_reason": pairing_reason,
            "shift_days": (current_day - baseline_day).days if baseline_day is not None else None,
            "issues": issues,
            "strictness_level": "strict",
            "is_reused_baseline_bucket": False,
        }

    def _apply_matcher(
        self,
        *,
        step: CalendarMatchingStep,
        current_row: CalendarAnnotationRow,
        offset: int,
    ) -> tuple[date | None, list[str]]:
        matcher = step.matcher
        if matcher == "holiday_cluster":
            if current_row.holiday_group_id is None:
                return None, []
            candidate = self.baseline_by_holiday_group.get(current_row.holiday_group_id)
            if candidate is not None:
                return candidate, []
            if current_row.holiday_group_id in self.duplicate_holiday_groups:
                return None, []
            return None, ["holiday_cluster_unmapped"]
        if matcher == "year_relative_holiday_key":
            if current_row.holiday_group_id is None:
                return None, []
            if current_row.year_relative_holiday_key is None:
                return None, ["holiday_cluster_unmapped"]
            candidate = self.baseline_by_holiday_key.get(current_row.year_relative_holiday_key)
            return candidate, [] if candidate is not None else ["holiday_cluster_unmapped"]
        if matcher == "event_cluster":
            if current_row.event_group_id is None:
                return None, []
            candidate = self.baseline_by_event_group.get(current_row.event_group_id)
            if candidate is not None:
                return candidate, []
            if current_row.event_group_id in self.duplicate_event_groups:
                return None, []
            return None, ["event_cluster_unmapped"]
        if matcher == "year_relative_event_key":
            if current_row.event_group_id is None:
                return None, []
            if current_row.year_relative_event_key is None:
                return None, ["event_cluster_unmapped"]
            candidate = self.baseline_by_event_key.get(current_row.year_relative_event_key)
            return candidate, [] if candidate is not None else ["event_cluster_unmapped"]
        if matcher == "same_weekday_nearest":
            target_day = self.baseline_window[0] + timedelta(days=offset)
            candidate = _nearest_same_weekday(
                target_day=target_day,
                baseline_window=self.baseline_window,
                weekday=current_row.weekday,
                tie_breaker=step.tie_breaker,
                max_shift_days=step.max_shift_days,
            )
            return candidate, []
        if matcher == "natural_date_shift":
            baseline_day = self.baseline_window[0] + timedelta(days=offset)
            if baseline_day >= self.baseline_window[1]:
                return None, []
            return baseline_day, []
        raise ValueError(f"Unsupported calendar matcher '{matcher}'")

    def _is_fallback_match(self, step_index: int) -> bool:
        if step_index == 0:
            return False
        prior_matchers = self.matching_strategy[:step_index]
        current_matcher = self.matching_strategy[step_index].matcher
        return any(step.requires_annotation for step in prior_matchers) and (
            current_matcher in self.fallback_strategy
            or not self.matching_strategy[step_index].requires_annotation
        )

    def _window_rows(self, window: tuple[date, date]) -> list[CalendarAnnotationRow]:
        rows: list[CalendarAnnotationRow] = []
        cursor = window[0]
        while cursor < window[1]:
            rows.append(
                self.rows_by_date.get(
                    cursor,
                    CalendarAnnotationRow(calendar_date=cursor, weekday=cursor.weekday() + 1),
                )
            )
            cursor += timedelta(days=1)
        return rows

    @staticmethod
    def _index_unique(
        rows: Sequence[CalendarAnnotationRow],
        *,
        key_fn: Callable[[CalendarAnnotationRow], str | None],
    ) -> tuple[dict[str, date], set[str]]:
        indexed: dict[str, date] = {}
        duplicates: set[str] = set()
        for row in rows:
            key = key_fn(row)
            if key is None:
                continue
            if key in indexed:
                duplicates.add(key)
                continue
            indexed[key] = row.calendar_date
        for duplicate in duplicates:
            indexed.pop(duplicate, None)
        return indexed, duplicates


def _nearest_same_weekday(
    *,
    target_day: date,
    baseline_window: tuple[date, date],
    weekday: int,
    tie_breaker: CalendarTieBreaker | None,
    max_shift_days: int | None,
) -> date | None:
    candidates: list[date] = []
    cursor = baseline_window[0]
    while cursor < baseline_window[1]:
        shift_days = (cursor - target_day).days
        if cursor.weekday() + 1 == weekday and (
            max_shift_days is None or abs(shift_days) <= max_shift_days
        ):
            candidates.append(cursor)
        cursor += timedelta(days=1)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            abs((candidate - target_day).days),
            candidate,
        ),
    )


def _finalize_bucket_pairing_metadata(pairings: list[dict[str, Any]]) -> None:
    baseline_usage: dict[str, int] = {}
    for pairing in pairings:
        baseline_bucket_start = pairing.get("baseline_bucket_start")
        if isinstance(baseline_bucket_start, str) and baseline_bucket_start:
            baseline_usage[baseline_bucket_start] = baseline_usage.get(baseline_bucket_start, 0) + 1

    for pairing in pairings:
        baseline_bucket_start = pairing.get("baseline_bucket_start")
        reused_baseline_bucket = (
            isinstance(baseline_bucket_start, str)
            and baseline_bucket_start
            and baseline_usage.get(baseline_bucket_start, 0) > 1
        )
        pairing["is_reused_baseline_bucket"] = bool(reused_baseline_bucket)
        pairing["strictness_level"] = strictness_level_for_bucket(
            issues=pairing.get("issues"),
            is_reused_baseline_bucket=bool(reused_baseline_bucket),
        )
