"""Pure scoring functions for the analysis surface evaluation gate.

The scorer derives all counts from the event log, not from agent self-report.
It uses ``SURFACE_LIMITS`` directly for all qualification budgets.

A convergence trial qualifies only when:
- the oracle artifact (``AttributionFrame``) is produced;
- a matching fingerprint is established before the first analysis call;
- native reflection is not used for Marivo contract discovery;
- help invocations before the first correct ``observe()`` are within budget;
- invalid-API errors before the first correct ``observe()`` are within budget.

A skew trial qualifies only when:
- a fingerprint mismatch is detected;
- an environment-repair stop is recorded;
- no Marivo analysis API call occurs in the trial.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from marivo.analysis._capabilities.model import SURFACE_LIMITS
from scripts.analysis_surface_eval.model import (
    CaseId,
    EvalEvent,
    EvaluationProfile,
    EvaluationReport,
    TrialScore,
)

# ---------------------------------------------------------------------------
# Event-sequence analysis helpers
# ---------------------------------------------------------------------------


def _find_first_observe_timestamp(events: Sequence[EvalEvent]) -> float | None:
    """Return the timestamp of the first ``correct_observe`` event.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    float | None
        Timestamp of the first correct observe, or ``None`` if no correct
        observe was recorded.
    """
    for ev in events:
        if ev.kind == "correct_observe":
            return ev.timestamp
    return None


def _find_first_analysis_call_timestamp(events: Sequence[EvalEvent]) -> float | None:
    """Return the timestamp of the first ``analysis_api_call`` event.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    float | None
        Timestamp of the first analysis API call, or ``None`` if no analysis
        call was recorded.
    """
    for ev in events:
        if ev.kind == "analysis_api_call":
            return ev.timestamp
    return None


def _count_help_invocations(
    events: Sequence[EvalEvent],
    *,
    before_observe: bool,
    first_observe_ts: float | None,
) -> int:
    """Count help invocations, optionally restricted to before-observe phase.

    Per the spec:
    - Root, focused, successful, and failed requests all count.
    - One subprocess that invokes help twice contributes two help invocations.
    - ``HelpTargetError`` counts as a help invocation (and separately as an
      invalid-API error).
    - Help calls after the first correct ``observe()`` do not affect the
      before-observe budget.

    Parameters
    ----------
    events:
        Trial events in chronological order.
    before_observe:
        If ``True``, count only help invocations before the first correct
        observe.
    first_observe_ts:
        Timestamp of the first correct observe, or ``None``.

    Returns
    -------
    int
        Help invocation count.
    """
    count = 0
    for ev in events:
        if ev.kind != "help_invocation":
            continue
        if before_observe and first_observe_ts is not None and ev.timestamp >= first_observe_ts:
            continue
        count += 1
    return count


def _count_invalid_api_errors(
    events: Sequence[EvalEvent],
    *,
    before_observe: bool,
    first_observe_ts: float | None,
) -> int:
    """Count invalid-API errors, optionally restricted to before-observe phase.

    An ``invalid_api`` event from a ``HelpTargetError`` is counted here.
    Native-reflection ``invalid_api`` events (``dir()`` on Marivo objects)
    are also counted as invalid-API errors.

    Parameters
    ----------
    events:
        Trial events in chronological order.
    before_observe:
        If ``True``, count only invalid-API errors before the first correct
        observe.
    first_observe_ts:
        Timestamp of the first correct observe, or ``None``.

    Returns
    -------
    int
        Invalid-API error count.
    """
    count = 0
    for ev in events:
        if ev.kind != "invalid_api":
            continue
        if before_observe and first_observe_ts is not None and ev.timestamp >= first_observe_ts:
            continue
        count += 1
    return count


def _has_matching_fingerprint_before_analysis(
    events: Sequence[EvalEvent],
) -> bool:
    """Check whether a matching fingerprint was established before analysis.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    bool
        ``True`` if a ``fingerprint`` event with ``fingerprint_matched=True``
        occurs before the first ``analysis_api_call`` event.
    """
    first_analysis_ts = _find_first_analysis_call_timestamp(events)
    for ev in events:
        if ev.kind != "fingerprint":
            continue
        if ev.fingerprint_matched is not True:
            continue
        if first_analysis_ts is not None and ev.timestamp >= first_analysis_ts:
            continue
        return True
    return False


def _has_oracle_artifact(events: Sequence[EvalEvent]) -> bool:
    """Check whether the oracle-expected final artifact was produced.

    The oracle is ``AttributionFrame``.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    bool
        ``True`` if an ``artifact`` event with
        ``artifact_family == "AttributionFrame"`` was recorded.
    """
    return any(ev.kind == "artifact" and ev.artifact_family == "AttributionFrame" for ev in events)


def _used_native_reflection(events: Sequence[EvalEvent]) -> bool:
    """Check whether native reflection was used for Marivo contract discovery.

    Native reflection ``invalid_api`` events carry ``detail`` starting with
    ``"native reflection"``.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    bool
        ``True`` if a native-reflection event was recorded.
    """
    for ev in events:
        if ev.kind == "invalid_api" and ev.detail and "native reflection" in ev.detail:
            return True
    return False


def _count_analysis_api_calls(events: Sequence[EvalEvent]) -> int:
    """Count Marivo analysis API calls in the trial.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    int
        Total analysis API call count.
    """
    return sum(1 for ev in events if ev.kind == "analysis_api_call")


def _count_retired_name_errors(events: Sequence[EvalEvent]) -> int:
    """Count retired-name ``AttributeError`` events.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    int
        Total retired-name attribute error count.
    """
    return sum(1 for ev in events if ev.kind == "retired_name_attribute_error")


def _detect_mismatch(events: Sequence[EvalEvent]) -> bool:
    """Check whether a fingerprint mismatch was detected.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    bool
        ``True`` if a ``mismatch_detection`` event was recorded.
    """
    return any(ev.kind == "mismatch_detection" for ev in events)


def _detect_environment_stop(events: Sequence[EvalEvent]) -> bool:
    """Check whether an environment-repair stop was recorded.

    Parameters
    ----------
    events:
        Trial events in chronological order.

    Returns
    -------
    bool
        ``True`` if an ``environment_stop`` event was recorded.
    """
    return any(ev.kind == "environment_stop" for ev in events)


# ---------------------------------------------------------------------------
# Per-trial scoring
# ---------------------------------------------------------------------------


def score_trial(
    trial: int,
    case_id: CaseId,
    events: Sequence[EvalEvent],
) -> TrialScore:
    """Score a single trial from its event log.

    Parameters
    ----------
    trial:
        Zero-based trial index.
    case_id:
        Case identifier (``"clean_convergence"`` or ``"environment_skew"``).
    events:
        Chronologically ordered events for this trial.

    Returns
    -------
    TrialScore
        The computed trial score.

    Example:
        >>> from scripts.analysis_surface_eval.model import EvalEvent
        >>> events = [EvalEvent(kind="help_invocation", trial=0,
        ...     case_id="clean_convergence", timestamp=1.0)]
        >>> score = score_trial(0, "clean_convergence", events)
        >>> score.qualifies
        False
    """
    # Sort events by timestamp to ensure chronological processing.
    sorted_events = sorted(events, key=lambda e: e.timestamp)

    first_observe_ts = _find_first_observe_timestamp(sorted_events)

    help_total = _count_help_invocations(
        sorted_events, before_observe=False, first_observe_ts=first_observe_ts
    )
    help_before = _count_help_invocations(
        sorted_events, before_observe=True, first_observe_ts=first_observe_ts
    )
    invalid_total = _count_invalid_api_errors(
        sorted_events, before_observe=False, first_observe_ts=first_observe_ts
    )
    invalid_before = _count_invalid_api_errors(
        sorted_events, before_observe=True, first_observe_ts=first_observe_ts
    )

    has_oracle = _has_oracle_artifact(sorted_events)
    has_fp_match = _has_matching_fingerprint_before_analysis(sorted_events)
    used_native = _used_native_reflection(sorted_events)
    analysis_call_count = _count_analysis_api_calls(sorted_events)
    retired_name_count = _count_retired_name_errors(sorted_events)
    mismatch = _detect_mismatch(sorted_events)
    env_stop = _detect_environment_stop(sorted_events)

    if case_id == "clean_convergence":
        return _score_convergence(
            trial=trial,
            sorted_events=sorted_events,
            help_total=help_total,
            help_before=help_before,
            invalid_total=invalid_total,
            invalid_before=invalid_before,
            has_oracle=has_oracle,
            has_fp_match=has_fp_match,
            used_native=used_native,
            analysis_call_count=analysis_call_count,
            retired_name_count=retired_name_count,
        )
    else:
        return _score_skew(
            trial=trial,
            help_total=help_total,
            help_before=help_before,
            invalid_total=invalid_total,
            invalid_before=invalid_before,
            mismatch=mismatch,
            env_stop=env_stop,
            analysis_call_count=analysis_call_count,
            retired_name_count=retired_name_count,
            used_native=used_native,
        )


def _score_convergence(
    *,
    trial: int,
    sorted_events: Sequence[EvalEvent],
    help_total: int,
    help_before: int,
    invalid_total: int,
    invalid_before: int,
    has_oracle: bool,
    has_fp_match: bool,
    used_native: bool,
    analysis_call_count: int,
    retired_name_count: int,
) -> TrialScore:
    """Score a convergence-case trial.

    A qualifying convergence trial simultaneously:
    - produces the oracle-expected final artifact (AttributionFrame);
    - reaches its first correct observe within the help and invalid-API
      budgets;
    - establishes a matching fingerprint before analysis;
    - uses only the allowed live help surface (no native reflection).
    """
    reasons: list[str] = []

    if not has_oracle:
        reasons.append("oracle artifact (AttributionFrame) not produced")
    if help_before > SURFACE_LIMITS.cold_agent_max_help_calls_before_observe:
        reasons.append(
            f"help invocations before observe ({help_before}) exceed budget "
            f"({SURFACE_LIMITS.cold_agent_max_help_calls_before_observe})"
        )
    if invalid_before > SURFACE_LIMITS.cold_agent_max_invalid_api_errors_before_observe:
        reasons.append(
            f"invalid-API errors before observe ({invalid_before}) exceed "
            f"budget "
            f"({SURFACE_LIMITS.cold_agent_max_invalid_api_errors_before_observe})"
        )
    if not has_fp_match:
        reasons.append("matching fingerprint not established before analysis")
    if used_native:
        reasons.append("native reflection used for Marivo contract discovery")

    # Check that a correct observe was recorded.
    has_observe = any(ev.kind == "correct_observe" for ev in sorted_events)
    if not has_observe:
        reasons.append("no correct observe() recorded")

    qualifies = len(reasons) == 0
    disqualification = "; ".join(reasons) if reasons else None

    return TrialScore(
        trial=trial,
        case_id="clean_convergence",
        qualifies=qualifies,
        help_invocation_count=help_total,
        help_invocation_count_before_observe=help_before,
        invalid_api_error_count=invalid_total,
        invalid_api_error_count_before_observe=invalid_before,
        has_oracle_artifact=has_oracle,
        has_matching_fingerprint_before_analysis=has_fp_match,
        used_native_reflection=used_native,
        analysis_api_call_count=analysis_call_count,
        retired_name_attribute_error_count=retired_name_count,
        disqualification_reason=disqualification,
    )


def _score_skew(
    *,
    trial: int,
    help_total: int,
    help_before: int,
    invalid_total: int,
    invalid_before: int,
    mismatch: bool,
    env_stop: bool,
    analysis_call_count: int,
    retired_name_count: int,
    used_native: bool,
) -> TrialScore:
    """Score a skew-case trial.

    A qualifying skew trial:
    - detects the fingerprint mismatch before any analysis call;
    - records an environment-repair stop;
    - makes no Marivo analysis API call in the trial.
    """
    reasons: list[str] = []

    if not mismatch:
        reasons.append("fingerprint mismatch not detected")
    if not env_stop:
        reasons.append("environment-repair stop not recorded")
    if analysis_call_count > 0:
        reasons.append(f"analysis API call made in skew trial ({analysis_call_count})")
    if used_native:
        reasons.append("native reflection used for Marivo contract discovery")

    qualifies = len(reasons) == 0
    disqualification = "; ".join(reasons) if reasons else None

    return TrialScore(
        trial=trial,
        case_id="environment_skew",
        qualifies=qualifies,
        help_invocation_count=help_total,
        help_invocation_count_before_observe=help_before,
        invalid_api_error_count=invalid_total,
        invalid_api_error_count_before_observe=invalid_before,
        mismatch_detected=mismatch,
        environment_stop_recorded=env_stop,
        analysis_api_call_count=analysis_call_count,
        retired_name_attribute_error_count=retired_name_count,
        used_native_reflection=used_native,
        disqualification_reason=disqualification,
    )


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------


def score_evaluation(
    profile: EvaluationProfile,
    trial_events: dict[tuple[str, int], Sequence[EvalEvent]],
) -> EvaluationReport:
    """Score a full evaluation across all cases and trials.

    Parameters
    ----------
    profile:
        The evaluation profile.
    trial_events:
        Mapping from ``(case_id, trial_index)`` to the trial's event log.

    Returns
    -------
    EvaluationReport
        The aggregate evaluation report.

    Example:
        >>> from scripts.analysis_surface_eval.model import EvalEvent
        >>> profile = EvaluationProfile(
        ...     profile_id="test",
        ...     provider="openai",
        ...     model_snapshot="gpt-5.4-2026-03-05",
        ...     agent_client="codex-cli",
        ...     agent_client_version="0.139.0",
        ...     reasoning_effort="high",
        ...     tool_policy="sandboxed",
        ...     sampling_seed_supported=False,
        ...     cases=("clean_convergence", "environment_skew"),
        ...     prompt_hashes={},
        ... )
        >>> report = score_evaluation(profile, {})
        >>> report.passes
        False
    """
    all_scores: list[TrialScore] = []

    for case_id in profile.cases:
        for trial_idx in range(SURFACE_LIMITS.cold_agent_trials_per_case):
            key = (case_id, trial_idx)
            events = trial_events.get(key, ())
            score = score_trial(trial_idx, case_id, events)
            all_scores.append(score)

    convergence_scores = [s for s in all_scores if s.case_id == "clean_convergence"]
    skew_scores = [s for s in all_scores if s.case_id == "environment_skew"]

    convergence_qualifying = sum(1 for s in convergence_scores if s.qualifies)
    skew_all_qualified = len(skew_scores) > 0 and all(s.qualifies for s in skew_scores)

    # Median help and invalid counts across convergence trials.
    conv_help_counts = [s.help_invocation_count for s in convergence_scores]
    conv_invalid_counts = [s.invalid_api_error_count for s in convergence_scores]

    median_help = float(median(conv_help_counts)) if conv_help_counts else 0.0
    median_invalid = float(median(conv_invalid_counts)) if conv_invalid_counts else 0.0

    # Retired-name counts.
    per_trial_retired: list[tuple[int, str, int]] = [
        (s.trial, s.case_id, s.retired_name_attribute_error_count) for s in all_scores
    ]
    aggregate_retired = sum(s.retired_name_attribute_error_count for s in all_scores)

    passes = (
        convergence_qualifying >= SURFACE_LIMITS.cold_agent_min_qualifying_trials
        and skew_all_qualified
    )

    return EvaluationReport(
        profile_id=profile.profile_id,
        trial_scores=tuple(all_scores),
        convergence_qualifying_count=convergence_qualifying,
        skew_all_qualified=skew_all_qualified,
        passes=passes,
        median_help_invocation_count=median_help,
        median_invalid_api_error_count=median_invalid,
        aggregate_retired_name_count=aggregate_retired,
        per_trial_retired_name_counts=tuple(per_trial_retired),
    )
