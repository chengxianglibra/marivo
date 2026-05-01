from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


def _policy_name(policy: CalendarPolicyDefinition) -> str:
    return policy.policy_ref.split(".", 1)[1]


def _policy_display_name(policy: CalendarPolicyDefinition) -> str:
    basis_label = _BASIS_LABELS[policy.comparison_basis]
    return f"{_policy_name(policy).replace('_', ' ')} ({basis_label})"


def _policy_description(policy: CalendarPolicyDefinition) -> str:
    use_when = ", ".join(policy.use_when[:2]) if policy.use_when else "calendar-aligned comparison"
    return (
        f"Builtin calendar alignment policy for {policy.comparison_basis} comparisons using "
        f"{policy.resolved_alignment_mode}; use when {use_when}."
    )


def _matching_step_summary(step: CalendarMatchingStep) -> str:
    summary: str = step.matcher
    if step.max_shift_days is not None:
        summary = f"{summary}(max_shift_days={step.max_shift_days})"
    if step.tie_breaker is not None:
        summary = f"{summary}[{step.tie_breaker}]"
    if step.requires_annotation:
        summary = f"{summary}:annotation_required"
    return summary


def calendar_policy_catalog_entry(policy_ref: str) -> CalendarPolicyCatalogEntry:
    policy = get_calendar_policy(policy_ref)
    return CalendarPolicyCatalogEntry(
        policy_ref=policy.policy_ref,
        object_id=policy.policy_ref,
        name=_policy_name(policy),
        display_name=_policy_display_name(policy),
        description=_policy_description(policy),
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
    return tuple(calendar_policy_catalog_entry(policy.policy_ref) for policy in _POLICIES)


def list_calendar_policies() -> tuple[CalendarPolicyDefinition, ...]:
    return _POLICIES


def get_calendar_policy(policy_ref: str) -> CalendarPolicyDefinition:
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


def validate_calendar_policy_ref(
    policy_ref: str | None,
    *,
    comparison_basis: CalendarComparisonBasis | None = None,
) -> str | None:
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


def policy_registry_summary(
    *,
    comparison_basis: CalendarComparisonBasis | None = None,
) -> list[dict[str, object]]:
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


def resolve_calendar_policy(
    *,
    explicit_policy_ref: str | None = None,
    injected_policy_ref: str | None = None,
    planner_candidate_refs: list[str] | None = None,
    comparison_basis: CalendarComparisonBasis | None = None,
    required: bool = False,
) -> ResolvedCalendarPolicyBinding | None:
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
