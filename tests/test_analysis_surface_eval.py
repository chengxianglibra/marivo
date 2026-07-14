"""Tests for the deterministic analysis surface evaluation harness.

These tests pin the evaluation profile schema, event model, fixture builder,
instrumentation generator, pure scoring logic, and the Codex runner.  No
remote model calls are made; all scoring tests feed synthetic event sequences
and all runner tests patch subprocess execution.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from marivo.analysis._capabilities.model import SURFACE_LIMITS
from scripts.analysis_surface_eval.fixture import (
    build_convergence_fixture,
    build_skew_fixture,
)
from scripts.analysis_surface_eval.instrumentation import (
    InstrumentationConfig,
    generate_sitecustomize,
    parse_event_line,
)
from scripts.analysis_surface_eval.model import (
    CaseId,
    EvalEvent,
    EvalEventKind,
    EvaluationProfile,
    load_profile,
)
from scripts.analysis_surface_eval.runner import (
    build_codex_command,
    generate_run_id,
    main,
    run_evaluation,
    run_preflight,
)
from scripts.analysis_surface_eval.scorer import score_evaluation, score_trial

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EVAL_ROOT = Path(__file__).resolve().parent.parent / "evals" / "analysis_surface"
PROFILE_PATH = EVAL_ROOT / "profile.toml"
PROMPTS_DIR = EVAL_ROOT / "prompts"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    kind: EvalEventKind,
    *,
    trial: int = 0,
    case_id: CaseId = "clean_convergence",
    timestamp: float = 0.0,
    **kwargs: object,
) -> EvalEvent:
    """Create an EvalEvent with defaults."""
    return EvalEvent(
        kind=kind,
        trial=trial,
        case_id=case_id,
        timestamp=timestamp,
        **kwargs,  # type: ignore[arg-type]
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _make_test_profile() -> EvaluationProfile:
    return EvaluationProfile(
        profile_id="test-profile",
        provider="openai",
        model_snapshot="gpt-5.4-2026-03-05",
        agent_client="codex-cli",
        agent_client_version="0.139.0",
        reasoning_effort="high",
        tool_policy="sandboxed",
        sampling_seed_supported=False,
        cases=("clean_convergence", "environment_skew"),
        prompt_hashes={},
    )


def _qualifying_convergence_events(trial: int = 0) -> list[EvalEvent]:
    return [
        _make_event("help_invocation", trial=trial, timestamp=1.0),
        _make_event("help_invocation", trial=trial, timestamp=2.0),
        _make_event("fingerprint", trial=trial, timestamp=3.0, fingerprint_matched=True),
        _make_event("correct_observe", trial=trial, timestamp=4.0),
        _make_event("artifact", trial=trial, timestamp=5.0, artifact_family="AttributionFrame"),
    ]


def _qualifying_skew_events(trial: int = 0) -> list[EvalEvent]:
    return [
        _make_event("help_invocation", trial=trial, case_id="environment_skew", timestamp=1.0),
        _make_event("mismatch_detection", trial=trial, case_id="environment_skew", timestamp=2.0),
        _make_event("environment_stop", trial=trial, case_id="environment_skew", timestamp=3.0),
    ]


def _write_bad_profile(
    tmp_path: Path,
    *,
    model_snapshot: str = "gpt-5.4-2026-03-05",
    prompt_hash: str = "abc",
) -> Path:
    """Write a minimal profile TOML for rejection tests."""
    profile = tmp_path / "profile.toml"
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "clean_convergence.md").write_text("test")
    profile.write_text(
        'profile_id = "bad"\n'
        'provider = "openai"\n'
        f'model_snapshot = "{model_snapshot}"\n'
        'agent_client = "codex-cli"\n'
        'agent_client_version = "0.139.0"\n'
        'reasoning_effort = "high"\n'
        'tool_policy = "sandboxed"\n'
        "sampling_seed_supported = false\n"
        'cases = ["clean_convergence"]\n'
        "[prompt_hashes]\n"
        f'clean_convergence = "{prompt_hash}"\n'
    )
    return profile


# ---------------------------------------------------------------------------
# Profile loading tests
# ---------------------------------------------------------------------------


def test_profile_loads_with_exact_values() -> None:
    profile = load_profile(PROFILE_PATH, prompts_dir=PROMPTS_DIR)
    assert profile.profile_id == "analysis-surface-gpt54-high-v1"
    assert profile.provider == "openai"
    assert profile.model_snapshot == "gpt-5.4-2026-03-05"
    assert profile.agent_client == "codex-cli"
    assert profile.agent_client_version == "0.139.0"
    assert profile.reasoning_effort == "high"
    assert profile.tool_policy == "sandboxed-local-shell-no-web"
    assert profile.sampling_seed_supported is False
    assert profile.cases == ("clean_convergence", "environment_skew")


def test_profile_does_not_repeat_trial_count() -> None:
    """The manifest must not repeat trial count or qualification budgets."""
    with open(PROFILE_PATH, "rb") as f:
        raw = tomllib.load(f)
    assert "trials_per_case" not in raw
    assert "min_qualifying_trials" not in raw
    assert "max_help_calls" not in raw
    assert "max_invalid_api_errors" not in raw


def test_profile_prompt_hashes_match_files() -> None:
    profile = load_profile(PROFILE_PATH, prompts_dir=PROMPTS_DIR)
    for case_id, stored_hash in profile.prompt_hashes.items():
        prompt_file = PROMPTS_DIR / f"{case_id}.md"
        actual = _sha256_file(prompt_file)
        assert actual == stored_hash, (
            f"Hash drift for {case_id}: stored={stored_hash} actual={actual}"
        )


def test_profile_rejects_latest_model(tmp_path: Path) -> None:
    profile = _write_bad_profile(tmp_path, model_snapshot="gpt-5.4-latest")
    with pytest.raises(ValueError, match="floating alias"):
        load_profile(profile, prompts_dir=tmp_path / "prompts")


def test_profile_rejects_unversioned_model(tmp_path: Path) -> None:
    profile = _write_bad_profile(tmp_path, model_snapshot="gpt-5")
    with pytest.raises(ValueError, match="unversioned"):
        load_profile(profile, prompts_dir=tmp_path / "prompts")


def test_profile_rejects_prompt_hash_drift(tmp_path: Path) -> None:
    profile = _write_bad_profile(tmp_path, prompt_hash="wrong_hash_not_real")
    with pytest.raises(ValueError, match="hash drift"):
        load_profile(profile, prompts_dir=tmp_path / "prompts")


def test_profile_repr() -> None:
    profile = load_profile(PROFILE_PATH, prompts_dir=PROMPTS_DIR)
    r = repr(profile)
    assert "EvaluationProfile" in r
    assert profile.profile_id in r


# ---------------------------------------------------------------------------
# Event model tests
# ---------------------------------------------------------------------------


def test_event_carries_trial_case_timestamp() -> None:
    ev = _make_event("help_invocation", trial=2, timestamp=1.5)
    assert ev.trial == 2
    assert ev.case_id == "clean_convergence"
    assert ev.timestamp == 1.5


def test_event_default_observe_phase_is_before() -> None:
    ev = _make_event("help_invocation")
    assert ev.observe_phase == "before_observe"


def test_event_repr() -> None:
    ev = _make_event("help_invocation", trial=1, timestamp=2.0)
    r = repr(ev)
    assert "EvalEvent" in r
    assert "trial=1" in r


def test_event_is_frozen() -> None:
    ev = _make_event("help_invocation")
    with pytest.raises(Exception):
        ev.kind = "invalid_api"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Help counting tests
# ---------------------------------------------------------------------------


def test_single_help_counts_as_one() -> None:
    events = [_make_event("help_invocation", timestamp=1.0)]
    score = score_trial(0, "clean_convergence", events)
    assert score.help_invocation_count == 1
    assert score.help_invocation_count_before_observe == 1


def test_two_helps_in_one_subprocess_count_as_two() -> None:
    """One subprocess that invokes help twice contributes two help invocations."""
    events = [
        _make_event("subprocess_start", timestamp=0.0),
        _make_event("help_invocation", timestamp=1.0),
        _make_event("help_invocation", timestamp=2.0),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.help_invocation_count == 2
    assert score.help_invocation_count_before_observe == 2


def test_subprocess_start_does_not_add_help_count() -> None:
    events = [
        _make_event("subprocess_start", timestamp=0.0),
        _make_event("subprocess_start", timestamp=1.0),
        _make_event("help_invocation", timestamp=2.0),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.help_invocation_count == 1


def test_help_target_error_double_counts() -> None:
    """HelpTargetError increments BOTH help count AND invalid-API count."""
    events = [
        _make_event("help_invocation", timestamp=1.0, is_help_target_error=True),
        _make_event("invalid_api", timestamp=1.0, is_help_target_error=True),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.help_invocation_count == 1
    assert score.invalid_api_error_count == 1
    assert score.help_invocation_count_before_observe == 1
    assert score.invalid_api_error_count_before_observe == 1


def test_help_after_observe_excluded_from_before_budget() -> None:
    """Help calls after first correct observe do not affect before-observe budget."""
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event("correct_observe", timestamp=2.0),
        _make_event("help_invocation", timestamp=3.0),
        _make_event("help_invocation", timestamp=4.0),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.help_invocation_count == 3
    assert score.help_invocation_count_before_observe == 1


def test_invalid_after_observe_excluded_from_before_budget() -> None:
    events = [
        _make_event("invalid_api", timestamp=1.0),
        _make_event("correct_observe", timestamp=2.0),
        _make_event("invalid_api", timestamp=3.0),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.invalid_api_error_count == 2
    assert score.invalid_api_error_count_before_observe == 1


def test_root_and_focused_both_count() -> None:
    events = [
        _make_event("help_invocation", timestamp=1.0, target=None),
        _make_event("help_invocation", timestamp=2.0, target="observe"),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.help_invocation_count == 2


# ---------------------------------------------------------------------------
# Native reflection disqualification
# ---------------------------------------------------------------------------


def test_native_reflection_disqualifies() -> None:
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event(
            "invalid_api",
            timestamp=2.0,
            detail="native reflection via dir() on marivo object",
        ),
        _make_event("fingerprint", timestamp=3.0, fingerprint_matched=True),
        _make_event("correct_observe", timestamp=4.0),
        _make_event("artifact", timestamp=5.0, artifact_family="AttributionFrame"),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies
    assert score.used_native_reflection is True
    assert "native reflection" in (score.disqualification_reason or "")


def test_no_native_reflection_does_not_disqualify_alone() -> None:
    events = _qualifying_convergence_events()
    score = score_trial(0, "clean_convergence", events)
    assert score.used_native_reflection is False


# ---------------------------------------------------------------------------
# Retired-name diagnostic counts
# ---------------------------------------------------------------------------


def test_retired_name_counted_but_not_disqualifying() -> None:
    events = _qualifying_convergence_events()
    events.append(
        _make_event(
            "retired_name_attribute_error",
            timestamp=6.0,
            target="describe",
            receiver_family="MetricFrame",
        )
    )
    score = score_trial(0, "clean_convergence", events)
    assert score.retired_name_attribute_error_count == 1
    assert score.qualifies is True


def test_multiple_retired_names_counted() -> None:
    events = [
        _make_event(
            "retired_name_attribute_error",
            timestamp=1.0,
            target="describe",
            receiver_family="MetricFrame",
        ),
        _make_event(
            "retired_name_attribute_error",
            timestamp=2.0,
            target="plot",
            receiver_family="DeltaFrame",
        ),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.retired_name_attribute_error_count == 2


# ---------------------------------------------------------------------------
# Convergence qualification logic
# ---------------------------------------------------------------------------


def test_convergence_qualifying_trial() -> None:
    events = _qualifying_convergence_events()
    score = score_trial(0, "clean_convergence", events)
    assert score.qualifies is True
    assert score.disqualification_reason is None
    assert score.has_oracle_artifact is True
    assert score.has_matching_fingerprint_before_analysis is True


def test_convergence_missing_oracle_artifact_disqualifies() -> None:
    events = [e for e in _qualifying_convergence_events() if e.kind != "artifact"]
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies
    assert "oracle" in (score.disqualification_reason or "").lower()


def test_convergence_missing_fingerprint_match_disqualifies() -> None:
    events = [
        e
        if e.kind != "fingerprint"
        else _make_event("fingerprint", timestamp=3.0, fingerprint_matched=False)
        for e in _qualifying_convergence_events()
    ]
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies
    assert "fingerprint" in (score.disqualification_reason or "").lower()


def test_convergence_help_budget_exceeded_disqualifies() -> None:
    """Budget is cold_agent_max_help_calls_before_observe (2)."""
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event("help_invocation", timestamp=2.0),
        _make_event("help_invocation", timestamp=3.0),
        _make_event("fingerprint", timestamp=4.0, fingerprint_matched=True),
        _make_event("correct_observe", timestamp=5.0),
        _make_event("artifact", timestamp=6.0, artifact_family="AttributionFrame"),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies
    assert "help" in (score.disqualification_reason or "").lower()


def test_convergence_invalid_api_budget_exceeded_disqualifies() -> None:
    """Budget is cold_agent_max_invalid_api_errors_before_observe (1)."""
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event("invalid_api", timestamp=2.0),
        _make_event("invalid_api", timestamp=3.0),
        _make_event("fingerprint", timestamp=4.0, fingerprint_matched=True),
        _make_event("correct_observe", timestamp=5.0),
        _make_event("artifact", timestamp=6.0, artifact_family="AttributionFrame"),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies
    assert "invalid" in (score.disqualification_reason or "").lower()


def test_convergence_missing_observe_disqualifies() -> None:
    events = [e for e in _qualifying_convergence_events() if e.kind != "correct_observe"]
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies
    assert "observe" in (score.disqualification_reason or "").lower()


def test_convergence_help_exactly_at_budget_still_qualifies() -> None:
    """Help count equal to the budget (2) should still qualify."""
    events = _qualifying_convergence_events()
    score = score_trial(0, "clean_convergence", events)
    assert score.help_invocation_count_before_observe == 2
    assert score.qualifies


def test_convergence_invalid_exactly_at_budget_still_qualifies() -> None:
    """Invalid-API count equal to the budget (1) should still qualify."""
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event("help_invocation", timestamp=2.0),
        _make_event("invalid_api", timestamp=2.5),
        _make_event("fingerprint", timestamp=3.0, fingerprint_matched=True),
        _make_event("correct_observe", timestamp=4.0),
        _make_event("artifact", timestamp=5.0, artifact_family="AttributionFrame"),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert score.invalid_api_error_count_before_observe == 1
    assert score.qualifies


def test_convergence_fingerprint_after_analysis_does_not_count() -> None:
    """Fingerprint must be before the first analysis call."""
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event("help_invocation", timestamp=2.0),
        _make_event("analysis_api_call", timestamp=3.0),
        _make_event("fingerprint", timestamp=4.0, fingerprint_matched=True),
        _make_event("correct_observe", timestamp=5.0),
        _make_event("artifact", timestamp=6.0, artifact_family="AttributionFrame"),
    ]
    score = score_trial(0, "clean_convergence", events)
    assert not score.has_matching_fingerprint_before_analysis
    assert not score.qualifies


# ---------------------------------------------------------------------------
# Skew qualification logic
# ---------------------------------------------------------------------------


def test_skew_qualifying_trial() -> None:
    events = _qualifying_skew_events()
    score = score_trial(0, "environment_skew", events)
    assert score.qualifies
    assert score.mismatch_detected
    assert score.environment_stop_recorded
    assert score.analysis_api_call_count == 0


def test_skew_analysis_call_disqualifies() -> None:
    events = [
        _make_event("help_invocation", case_id="environment_skew", timestamp=1.0),
        _make_event("mismatch_detection", case_id="environment_skew", timestamp=2.0),
        _make_event("analysis_api_call", case_id="environment_skew", timestamp=3.0),
        _make_event("environment_stop", case_id="environment_skew", timestamp=4.0),
    ]
    score = score_trial(0, "environment_skew", events)
    assert not score.qualifies
    assert "analysis" in (score.disqualification_reason or "").lower()


def test_skew_no_mismatch_detected_disqualifies() -> None:
    events = [
        _make_event("help_invocation", case_id="environment_skew", timestamp=1.0),
        _make_event("environment_stop", case_id="environment_skew", timestamp=2.0),
    ]
    score = score_trial(0, "environment_skew", events)
    assert not score.qualifies
    assert "mismatch" in (score.disqualification_reason or "").lower()


def test_skew_no_environment_stop_disqualifies() -> None:
    events = [
        _make_event("help_invocation", case_id="environment_skew", timestamp=1.0),
        _make_event("mismatch_detection", case_id="environment_skew", timestamp=2.0),
    ]
    score = score_trial(0, "environment_skew", events)
    assert not score.qualifies
    assert "stop" in (score.disqualification_reason or "").lower()


def test_skew_native_reflection_disqualifies() -> None:
    events = [
        _make_event("help_invocation", case_id="environment_skew", timestamp=1.0),
        _make_event("mismatch_detection", case_id="environment_skew", timestamp=2.0),
        _make_event("environment_stop", case_id="environment_skew", timestamp=3.0),
        _make_event(
            "invalid_api",
            case_id="environment_skew",
            timestamp=4.0,
            detail="native reflection via dir()",
        ),
    ]
    score = score_trial(0, "environment_skew", events)
    assert not score.qualifies
    assert "native reflection" in (score.disqualification_reason or "")


# ---------------------------------------------------------------------------
# Aggregate evaluation report tests
# ---------------------------------------------------------------------------


def test_report_all_trials_qualifying_passes() -> None:
    profile = _make_test_profile()
    events: dict[tuple[str, int], list[EvalEvent]] = {}
    for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
        events[("clean_convergence", trial)] = _qualifying_convergence_events(trial)
        events[("environment_skew", trial)] = _qualifying_skew_events(trial)
    report = score_evaluation(profile, events)
    assert report.passes
    assert report.convergence_qualifying_count == 3
    assert report.skew_all_qualified


def test_report_convergence_below_min_qualifying_fails() -> None:
    profile = _make_test_profile()
    events: dict[tuple[str, int], list[EvalEvent]] = {}
    for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
        if trial < SURFACE_LIMITS.cold_agent_min_qualifying_trials - 1:
            events[("clean_convergence", trial)] = _qualifying_convergence_events(trial)
        else:
            events[("clean_convergence", trial)] = []
        events[("environment_skew", trial)] = _qualifying_skew_events(trial)
    report = score_evaluation(profile, events)
    assert not report.passes
    assert report.convergence_qualifying_count == 1


def test_report_one_skew_trial_not_qualifying_fails() -> None:
    profile = _make_test_profile()
    events: dict[tuple[str, int], list[EvalEvent]] = {}
    for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
        events[("clean_convergence", trial)] = _qualifying_convergence_events(trial)
        if trial == 0:
            events[("environment_skew", trial)] = [
                _make_event(
                    "mismatch_detection", trial=trial, case_id="environment_skew", timestamp=1.0
                ),
                _make_event(
                    "environment_stop", trial=trial, case_id="environment_skew", timestamp=2.0
                ),
                _make_event(
                    "analysis_api_call", trial=trial, case_id="environment_skew", timestamp=3.0
                ),
            ]
        else:
            events[("environment_skew", trial)] = _qualifying_skew_events(trial)
    report = score_evaluation(profile, events)
    assert not report.passes
    assert not report.skew_all_qualified


def test_report_median_help_reported_even_when_passing() -> None:
    profile = _make_test_profile()
    events: dict[tuple[str, int], list[EvalEvent]] = {}
    for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
        events[("clean_convergence", trial)] = _qualifying_convergence_events(trial)
        events[("environment_skew", trial)] = _qualifying_skew_events(trial)
    report = score_evaluation(profile, events)
    assert report.median_help_invocation_count == 2.0


def test_report_retired_name_counts_aggregated() -> None:
    profile = _make_test_profile()
    events: dict[tuple[str, int], list[EvalEvent]] = {}
    for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
        conv = _qualifying_convergence_events(trial)
        conv.append(
            _make_event(
                "retired_name_attribute_error",
                trial=trial,
                timestamp=6.0,
                target="describe",
                receiver_family="MetricFrame",
            )
        )
        events[("clean_convergence", trial)] = conv
        events[("environment_skew", trial)] = _qualifying_skew_events(trial)
    report = score_evaluation(profile, events)
    assert report.aggregate_retired_name_count == 3
    assert len(report.per_trial_retired_name_counts) == 6


def test_report_empty_events_report() -> None:
    profile = _make_test_profile()
    report = score_evaluation(profile, {})
    assert not report.passes
    assert report.convergence_qualifying_count == 0
    assert not report.skew_all_qualified
    assert report.median_help_invocation_count == 0.0


def test_report_repr() -> None:
    profile = _make_test_profile()
    report = score_evaluation(profile, {})
    r = repr(report)
    assert "EvaluationReport" in r
    assert "FAIL" in r


# ---------------------------------------------------------------------------
# Fixture builder tests
# ---------------------------------------------------------------------------


def test_convergence_fixture_structure(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("test prompt")
    root = tmp_path / "fixture"
    fp = build_convergence_fixture(root, prompt_file=prompt_file)
    assert fp.case_id == "clean_convergence"
    assert fp.is_skew is False
    assert fp.project_file.is_file()
    assert fp.duckdb_path.is_file()
    assert fp.datasource_file.is_file()
    assert fp.skill_file.is_file()
    assert fp.prompt_file.is_file()
    assert fp.analysis_venv.is_dir()
    assert fp.help_venv is None
    assert (fp.semantic_dir / "metrics.py").is_file()
    assert (fp.semantic_dir / "dimensions.py").is_file()


def test_skew_fixture_structure(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("test prompt")
    root = tmp_path / "fixture"
    fp = build_skew_fixture(root, prompt_file=prompt_file)
    assert fp.case_id == "environment_skew"
    assert fp.is_skew is True
    assert fp.help_venv is not None
    assert fp.help_venv.is_dir()
    assert (fp.help_venv / ".help_env_marker").is_file()
    assert (fp.analysis_venv / ".analysis_env_marker").is_file()


def test_fixture_duckdb_has_sales_orders(tmp_path: Path) -> None:
    import duckdb

    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("test")
    fp = build_convergence_fixture(tmp_path / "fx", prompt_file=prompt_file)
    con = duckdb.connect(str(fp.duckdb_path))
    try:
        result = con.execute("SELECT COUNT(*) FROM sales_orders").fetchone()
        assert result is not None
        assert result[0] == 12
    finally:
        con.close()


def test_fixture_skill_content(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("test")
    fp = build_convergence_fixture(tmp_path / "fx", prompt_file=prompt_file)
    content = fp.skill_file.read_text()
    assert "marivo-analysis" in content
    assert "mv.help" in content


def test_fixture_repr(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("test")
    fp = build_convergence_fixture(tmp_path / "fx", prompt_file=prompt_file)
    r = repr(fp)
    assert "FixtureProject" in r
    assert "clean_convergence" in r


# ---------------------------------------------------------------------------
# Instrumentation tests
# ---------------------------------------------------------------------------


def test_instrumentation_generated_file_exists(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "events.jsonl",
        trial=0,
        case_id="clean_convergence",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    assert path.is_file()
    assert path.name == "sitecustomize.py"


def test_instrumentation_generated_file_contains_trial_and_case(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "ev.jsonl",
        trial=2,
        case_id="environment_skew",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    content = path.read_text()
    assert "2" in content
    assert "environment_skew" in content


def test_instrumentation_generated_file_wraps_help(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "ev.jsonl",
        trial=0,
        case_id="clean_convergence",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    content = path.read_text()
    assert "_wrap_help" in content
    assert "help_invocation" in content
    assert "HelpTargetError" in content
    assert "invalid_api" in content


def test_instrumentation_generated_file_wraps_observe(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "ev.jsonl",
        trial=0,
        case_id="clean_convergence",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    content = path.read_text()
    assert "_wrap_observe" in content
    assert "correct_observe" in content
    assert "after_observe" in content


def test_instrumentation_generated_file_wraps_all_analysis_callables(
    tmp_path: Path,
) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "ev.jsonl",
        trial=0,
        case_id="clean_convergence",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    content = path.read_text()
    for method in (
        "compare",
        "attribute",
        "correlate",
        "hypothesis_test",
        "forecast",
        "assess_quality",
        "derive_metric_frame",
    ):
        assert f'"{method}"' in content, f"missing method name {method} in tuple"
    assert "_wrap_analysis_callable" in content
    assert "analysis_api_call" in content


def test_instrumentation_generated_file_probes_retired_names(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "ev.jsonl",
        trial=0,
        case_id="clean_convergence",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    content = path.read_text()
    assert "retired_name_attribute_error" in content
    assert "describe" in content
    assert "plot" in content
    # The probe must use safe getattr since BaseFrame does not define __getattr__.
    assert 'getattr(BaseFrame, "__getattr__", None)' in content
    assert "raise AttributeError(name)" in content


def test_instrumentation_generated_file_records_fingerprint(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "ev.jsonl",
        trial=0,
        case_id="clean_convergence",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    content = path.read_text()
    assert "fingerprint" in content
    assert "__version__" in content


def test_instrumentation_generated_file_detects_native_reflection(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "ev.jsonl",
        trial=0,
        case_id="clean_convergence",
    )
    path = generate_sitecustomize(tmp_path, cfg)
    content = path.read_text()
    assert "native reflection" in content
    assert "_probed_dir" in content
    assert "_probed_getmembers" in content
    assert "inspect.getmembers" in content


def test_instrumentation_parse_event_line() -> None:
    line = json.dumps(
        {
            "kind": "help_invocation",
            "trial": 0,
            "case_id": "clean_convergence",
            "timestamp": 1.5,
        }
    )
    event = parse_event_line(line)
    assert event["kind"] == "help_invocation"
    assert event["trial"] == 0


def test_instrumentation_config_repr() -> None:
    cfg = InstrumentationConfig(
        events_file=Path("/tmp/ev.jsonl"),
        trial=1,
        case_id="clean_convergence",
    )
    r = repr(cfg)
    assert "InstrumentationConfig" in r
    assert "trial=1" in r


# ---------------------------------------------------------------------------
# Surface limits integration
# ---------------------------------------------------------------------------


def test_surface_limits_trials_per_case() -> None:
    profile = _make_test_profile()
    report = score_evaluation(profile, {})
    assert len(report.trial_scores) == (SURFACE_LIMITS.cold_agent_trials_per_case * 2)


def test_surface_limits_help_budget() -> None:
    events = [
        _make_event("help_invocation", timestamp=float(i + 1))
        for i in range(SURFACE_LIMITS.cold_agent_max_help_calls_before_observe + 1)
    ]
    events.extend(
        [
            _make_event("fingerprint", timestamp=99.0, fingerprint_matched=True),
            _make_event("correct_observe", timestamp=100.0),
            _make_event("artifact", timestamp=101.0, artifact_family="AttributionFrame"),
        ]
    )
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies


def test_surface_limits_invalid_budget() -> None:
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event("help_invocation", timestamp=2.0),
    ]
    events.extend(
        [
            _make_event("invalid_api", timestamp=float(3 + i))
            for i in range(SURFACE_LIMITS.cold_agent_max_invalid_api_errors_before_observe + 1)
        ]
    )
    events.extend(
        [
            _make_event("fingerprint", timestamp=99.0, fingerprint_matched=True),
            _make_event("correct_observe", timestamp=100.0),
            _make_event("artifact", timestamp=101.0, artifact_family="AttributionFrame"),
        ]
    )
    score = score_trial(0, "clean_convergence", events)
    assert not score.qualifies


def test_surface_limits_min_qualifying() -> None:
    profile = _make_test_profile()
    events: dict[tuple[str, int], list[EvalEvent]] = {}
    for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
        if trial < SURFACE_LIMITS.cold_agent_min_qualifying_trials - 1:
            events[("clean_convergence", trial)] = _qualifying_convergence_events(trial)
        else:
            events[("clean_convergence", trial)] = []
        events[("environment_skew", trial)] = _qualifying_skew_events(trial)
    report = score_evaluation(profile, events)
    assert not report.passes
    assert report.convergence_qualifying_count == (
        SURFACE_LIMITS.cold_agent_min_qualifying_trials - 1
    )


# ---------------------------------------------------------------------------
# Runner: Codex command construction
# ---------------------------------------------------------------------------


def test_build_codex_command_has_ephemeral_flag() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    assert "exec" in cmd
    assert "--ephemeral" in cmd


def test_build_codex_command_ignores_user_config_and_rules() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    assert "--ignore-user-config" in cmd
    assert "--ignore-rules" in cmd


def test_build_codex_command_uses_json_mode() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    assert "--json" in cmd


def test_build_codex_command_pins_model_snapshot() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    assert "--model" in cmd
    idx = cmd.index("--model")
    assert cmd[idx + 1] == profile.model_snapshot


def test_build_codex_command_pins_reasoning_effort() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    config_arg = f'model_reasoning_effort="{profile.reasoning_effort}"'
    assert "--config" in cmd
    assert config_arg in cmd


def test_build_codex_command_uses_sandbox_workspace_write() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    assert "--sandbox" in cmd
    idx = cmd.index("--sandbox")
    assert cmd[idx + 1] == "workspace-write"


def test_build_codex_command_uses_cd_isolated_project() -> None:
    profile = _make_test_profile()
    project_dir = Path("/tmp/isolated-project")
    cmd = build_codex_command(profile, project_dir, "prompt text")
    assert "--cd" in cmd
    idx = cmd.index("--cd")
    assert cmd[idx + 1] == str(project_dir)


def test_build_codex_command_ends_with_stdin_dash() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    assert cmd[-1] == "-"


def test_build_codex_command_does_not_include_browsing_tools() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    cmd_str = " ".join(cmd)
    assert "web" not in cmd_str.lower()
    assert "browse" not in cmd_str.lower()


def test_build_codex_command_starts_with_codex() -> None:
    profile = _make_test_profile()
    cmd = build_codex_command(profile, Path("/tmp/project"), "prompt text")
    assert cmd[0] == "codex"


# ---------------------------------------------------------------------------
# Runner: CLI version mismatch abort
# ---------------------------------------------------------------------------


def _make_fake_wheel(tmp_path: Path) -> str:
    """Create a placeholder wheel file and return its path as a string."""
    wheel = tmp_path / "fake.whl"
    wheel.write_bytes(b"fake wheel content")
    return str(wheel)


def _make_fake_codex_jsonl(
    case_id: str,
    trial: int,
    *,
    qualifying: bool = True,
) -> str:
    """Build a fake JSONL event stream as a single string.

    Each line is a JSON object representing a probe event.  The runner will
    parse these and feed them to the scorer.
    """
    lines: list[str] = []

    def ev(kind: str, **extra: object) -> str:
        payload: dict[str, object] = {
            "kind": kind,
            "trial": trial,
            "case_id": case_id,
            "timestamp": float(len(lines) + 1),
        }
        payload.update(extra)
        return json.dumps(payload)

    if case_id == "clean_convergence":
        if qualifying:
            lines.append(ev("help_invocation"))
            lines.append(ev("help_invocation"))
            lines.append(ev("fingerprint", fingerprint_matched=True))
            lines.append(ev("correct_observe"))
            lines.append(ev("artifact", artifact_family="AttributionFrame"))
        else:
            # Missing oracle artifact.
            lines.append(ev("help_invocation"))
            lines.append(ev("fingerprint", fingerprint_matched=True))
            lines.append(ev("correct_observe"))
    else:  # environment_skew
        if qualifying:
            lines.append(ev("help_invocation"))
            lines.append(ev("mismatch_detection"))
            lines.append(ev("environment_stop"))
        else:
            # Has analysis call -- disqualifies.
            lines.append(ev("help_invocation"))
            lines.append(ev("mismatch_detection"))
            lines.append(ev("analysis_api_call"))
            lines.append(ev("environment_stop"))

    return "\n".join(lines) + "\n"


def test_cli_version_mismatch_aborts_before_trials(tmp_path: Path) -> None:
    """If codex CLI version differs from profile, abort before any trial."""
    profile = _make_test_profile()
    wheel = _make_fake_wheel(tmp_path)

    captured_runs: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> int:
        # Track subprocess calls; return 0 for codex version check,
        # but we will make the version mismatch.
        argv = args[0] if args else kwargs.get("args", [])
        if isinstance(argv, list):
            captured_runs.append(argv)
        return 0

    with (
        patch(
            "scripts.analysis_surface_eval.runner._run_subprocess",
            side_effect=fake_run,
        ),
        patch(
            "scripts.analysis_surface_eval.runner._get_codex_version",
            return_value="0.999.0",
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_preflight",
            return_value=True,
        ),
    ):
        exit_code = main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    assert exit_code == 2
    # No trial invocations should have been made.
    codex_trial_runs = [r for r in captured_runs if r and r[0] == "codex" and "exec" in r]
    assert len(codex_trial_runs) == 0


# ---------------------------------------------------------------------------
# Runner: event ingestion, transcript, report, exit status
# ---------------------------------------------------------------------------


def _setup_fake_codex(
    *,
    conv_qualifying: bool = True,
    skew_qualifying: bool = True,
) -> object:
    """Patch the runner's trial execution so that each trial writes a
    pre-built fake JSONL events file instead of invoking real Codex.

    Returns a context manager via :func:`patch.multiple`.
    """

    def fake_run_trial(
        profile: EvaluationProfile,
        case_id: str,
        trial_index: int,
        wheel_path: str,
        events_dir: Path,
    ) -> int:
        events_file = events_dir / f"{case_id}-trial-{trial_index}.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        if case_id == "clean_convergence":
            fake_jsonl = _make_fake_codex_jsonl(case_id, trial_index, qualifying=conv_qualifying)
        else:
            fake_jsonl = _make_fake_codex_jsonl(case_id, trial_index, qualifying=skew_qualifying)
        events_file.write_text(fake_jsonl)
        # Also write a placeholder transcript.
        transcript_file = events_dir / f"{case_id}-trial-{trial_index}-transcript.txt"
        transcript_file.write_text(f"fake transcript for {case_id}/{trial_index}\n")
        return 0

    return (
        patch(
            "scripts.analysis_surface_eval.runner._run_subprocess",
            return_value=0,
        ),
        patch(
            "scripts.analysis_surface_eval.runner._get_codex_version",
            return_value="0.139.0",
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_preflight",
            return_value=True,
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_trial",
            side_effect=fake_run_trial,
        ),
    )


def test_runner_passing_evaluation(tmp_path: Path) -> None:
    """All trials qualifying -> exit 0 and report.passes is True."""
    patches = _setup_fake_codex(conv_qualifying=True, skew_qualifying=True)
    wheel = _make_fake_wheel(tmp_path)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
    ):
        exit_code = main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    assert exit_code == 0

    run_dir = tmp_path / "eval-out"
    report_json = json.loads((run_dir / "report.json").read_text())
    assert report_json["passes"] is True
    assert report_json["convergence_qualifying_count"] == SURFACE_LIMITS.cold_agent_trials_per_case
    assert report_json["skew_all_qualified"] is True


def test_runner_failing_evaluation(tmp_path: Path) -> None:
    """Some trials not qualifying -> non-zero exit and report.passes is False."""
    patches = _setup_fake_codex(conv_qualifying=False, skew_qualifying=True)
    wheel = _make_fake_wheel(tmp_path)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
    ):
        exit_code = main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    assert exit_code == 1

    run_dir = tmp_path / "eval-out"
    report_json = json.loads((run_dir / "report.json").read_text())
    assert report_json["passes"] is False
    assert report_json["convergence_qualifying_count"] == 0


def test_runner_events_jsonl_written(tmp_path: Path) -> None:
    """The combined events.jsonl file is written to the output directory."""
    patches = _setup_fake_codex(conv_qualifying=True, skew_qualifying=True)
    wheel = _make_fake_wheel(tmp_path)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
    ):
        exit_code = main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    assert exit_code == 0
    events_file = tmp_path / "eval-out" / "events.jsonl"
    assert events_file.is_file()
    lines = [ln for ln in events_file.read_text().strip().split("\n") if ln]
    # 3 convergence trials + 3 skew trials, each with events.
    assert len(lines) >= SURFACE_LIMITS.cold_agent_trials_per_case * 2


def test_runner_transcripts_retained(tmp_path: Path) -> None:
    """Per-trial transcript files are retained in the output directory."""
    patches = _setup_fake_codex(conv_qualifying=True, skew_qualifying=True)
    wheel = _make_fake_wheel(tmp_path)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
    ):
        main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    run_dir = tmp_path / "eval-out"
    # Check that at least the trial event files exist.
    trial_files = sorted(run_dir.glob("*-trial-*.jsonl"))
    assert len(trial_files) == SURFACE_LIMITS.cold_agent_trials_per_case * 2


def test_runner_report_md_written(tmp_path: Path) -> None:
    """report.md is written alongside report.json."""
    patches = _setup_fake_codex(conv_qualifying=True, skew_qualifying=True)
    wheel = _make_fake_wheel(tmp_path)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
    ):
        main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    report_md = tmp_path / "eval-out" / "report.md"
    assert report_md.is_file()
    md_content = report_md.read_text()
    assert "PASS" in md_content or "FAIL" in md_content


def test_runner_diagnostic_counts_in_report(tmp_path: Path) -> None:
    """Report JSON includes median help and retired-name diagnostic counts."""
    patches = _setup_fake_codex(conv_qualifying=True, skew_qualifying=True)
    wheel = _make_fake_wheel(tmp_path)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
    ):
        main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    report_json = json.loads((tmp_path / "eval-out" / "report.json").read_text())
    assert "median_help_invocation_count" in report_json
    assert "median_invalid_api_error_count" in report_json
    assert "aggregate_retired_name_count" in report_json


def test_runner_preflight_failure_aborts(tmp_path: Path) -> None:
    """If preflight checks fail, the runner aborts before any trial."""
    wheel = _make_fake_wheel(tmp_path)
    with (
        patch(
            "scripts.analysis_surface_eval.runner._run_subprocess",
            return_value=0,
        ),
        patch(
            "scripts.analysis_surface_eval.runner._get_codex_version",
            return_value="0.139.0",
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_preflight",
            return_value=False,
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_trial",
        ) as mock_trial,
    ):
        exit_code = main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                wheel,
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    assert exit_code == 3
    mock_trial.assert_not_called()


def test_missing_wheel_aborts_before_trials(tmp_path: Path) -> None:
    """If the wheel file does not exist, abort with exit code 4."""
    with (
        patch(
            "scripts.analysis_surface_eval.runner._get_codex_version",
        ) as mock_version,
        patch(
            "scripts.analysis_surface_eval.runner.run_preflight",
        ) as mock_preflight,
        patch(
            "scripts.analysis_surface_eval.runner.run_trial",
        ) as mock_trial,
    ):
        exit_code = main(
            [
                "--profile",
                str(PROFILE_PATH),
                "--wheel",
                str(tmp_path / "nonexistent.whl"),
                "--output-dir",
                str(tmp_path / "eval-out"),
            ]
        )

    assert exit_code == 4
    mock_version.assert_not_called()
    mock_preflight.assert_not_called()
    mock_trial.assert_not_called()


# ---------------------------------------------------------------------------
# Runner: preflight subprocess execution
# ---------------------------------------------------------------------------


def test_preflight_success_returns_true() -> None:
    """When the venv subprocess exits 0, run_preflight returns True."""
    fake_result = subprocess.CompletedProcess(
        args=["python", "-c", "script"],
        returncode=0,
    )
    with patch(
        "scripts.analysis_surface_eval.runner.subprocess.run",
        return_value=fake_result,
    ) as mock_run:
        result = run_preflight(venv_python="/fake/venv/bin/python")

    assert result is True
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
    assert cmd[0] == "/fake/venv/bin/python"
    assert cmd[1] == "-c"


def test_preflight_failure_returns_false() -> None:
    """When the venv subprocess exits non-zero, run_preflight returns False."""
    fake_result = subprocess.CompletedProcess(
        args=["python", "-c", "script"],
        returncode=1,
    )
    with patch(
        "scripts.analysis_surface_eval.runner.subprocess.run",
        return_value=fake_result,
    ):
        result = run_preflight(venv_python="/fake/venv/bin/python")

    assert result is False


def test_preflight_timeout_returns_false() -> None:
    """When the preflight subprocess times out, run_preflight returns False."""
    with patch(
        "scripts.analysis_surface_eval.runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=30),
    ):
        result = run_preflight(venv_python="/fake/venv/bin/python")

    assert result is False


def test_run_subprocess_timeout_returns_124() -> None:
    """When a trial subprocess times out, _run_subprocess returns 124."""
    from scripts.analysis_surface_eval.runner import _run_subprocess

    with patch(
        "scripts.analysis_surface_eval.runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["codex"], timeout=600),
    ):
        exit_code = _run_subprocess(["codex", "exec"], timeout=600)

    assert exit_code == 124


# ---------------------------------------------------------------------------
# Runner: exactly one codex invocation per trial
# ---------------------------------------------------------------------------


def test_runner_makes_exactly_one_codex_call_per_trial(tmp_path: Path) -> None:
    """For N trials, exactly N codex subprocess calls are made.

    N = cold_agent_trials_per_case * len(cases) = 3 * 2 = 6.
    """
    profile = load_profile(PROFILE_PATH, prompts_dir=PROMPTS_DIR)
    expected_trials = SURFACE_LIMITS.cold_agent_trials_per_case * len(profile.cases)
    wheel = _make_fake_wheel(tmp_path)

    codex_call_count = 0

    def fake_run_subprocess(
        cmd: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: int | None = None,
        stdin_data: str | None = None,
        cwd: str | None = None,
    ) -> int:
        nonlocal codex_call_count
        if cmd and cmd[0] == "codex" and "exec" in cmd:
            codex_call_count += 1
            # Write fake qualifying events so the scorer sees a passing trial.
            # cwd is the fixture root: <events_dir>/fixture-<case>-<trial>
            if cwd is not None:
                fixture_dir = Path(cwd).name
                # fixture-<case_id>-<trial_index>
                parts = fixture_dir.split("-")
                trial_idx = int(parts[-1])
                case_id = "-".join(parts[1:-1])
                events_dir = Path(cwd).parent
                events_file = events_dir / f"{case_id}-trial-{trial_idx}.jsonl"
                events_file.write_text(_make_fake_codex_jsonl(case_id, trial_idx, qualifying=True))
        return 0

    with (
        patch(
            "scripts.analysis_surface_eval.runner._run_subprocess",
            side_effect=fake_run_subprocess,
        ),
        patch(
            "scripts.analysis_surface_eval.runner._get_codex_version",
            return_value=profile.agent_client_version,
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_preflight",
            return_value=True,
        ),
    ):
        exit_code = run_evaluation(
            profile,
            wheel,
            tmp_path / "eval-out",
        )

    assert exit_code == 0
    assert codex_call_count == expected_trials


def test_generate_run_id_is_string() -> None:
    rid = generate_run_id()
    assert isinstance(rid, str)
    assert len(rid) > 0


# ---------------------------------------------------------------------------
# Runner: run_evaluation direct call (no main/argv)
# ---------------------------------------------------------------------------


def test_run_evaluation_writes_report(tmp_path: Path) -> None:
    """run_evaluation writes report.json, report.md, events.jsonl."""
    profile = load_profile(PROFILE_PATH, prompts_dir=PROMPTS_DIR)
    output_dir = tmp_path / "eval-out"
    wheel = _make_fake_wheel(tmp_path)

    def fake_run_trial(
        profile: EvaluationProfile,
        case_id: str,
        trial_index: int,
        wheel_path: str,
        events_dir: Path,
    ) -> int:
        events_file = events_dir / f"{case_id}-trial-{trial_index}.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        fake_jsonl = _make_fake_codex_jsonl(case_id, trial_index, qualifying=True)
        events_file.write_text(fake_jsonl)
        return 0

    with (
        patch(
            "scripts.analysis_surface_eval.runner._get_codex_version",
            return_value=profile.agent_client_version,
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_preflight",
            return_value=True,
        ),
        patch(
            "scripts.analysis_surface_eval.runner.run_trial",
            side_effect=fake_run_trial,
        ),
    ):
        exit_code = run_evaluation(
            profile,
            wheel,
            output_dir,
        )

    assert exit_code == 0
    assert (output_dir / "report.json").is_file()
    assert (output_dir / "report.md").is_file()
    assert (output_dir / "events.jsonl").is_file()
