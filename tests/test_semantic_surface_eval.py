"""Tests for the deterministic semantic surface evaluation harness.

These tests pin the evaluation profile schema, event model, fixture builders,
instrumentation generator, pure scoring logic, and the Codex runner.  No
remote model calls are made; all scoring tests feed synthetic event sequences
and all runner tests patch subprocess execution.

The scorer is the testable core: every required case has at least one
qualifying synthetic log and at least one disqualifying synthetic log, plus
coverage of the rejection (safety) rules.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from marivo.introspection.live.model import SURFACE_LIMITS
from scripts.semantic_surface_eval.fixture import (
    build_clean_readiness_fixture,
    build_dependency_order_fixture,
    build_environment_skew_fixture,
    build_preview_before_readiness_fixture,
    build_scope_guard_fixture,
    build_unresolved_meaning_fixture,
    build_verify_before_preview_fixture,
)
from scripts.semantic_surface_eval.instrumentation import (
    InstrumentationConfig,
    generate_sitecustomize,
    parse_event_line,
)
from scripts.semantic_surface_eval.model import (
    ALL_CASE_IDS,
    CaseId,
    EvalEvent,
    EvalEventKind,
    EvaluationProfile,
    load_profile,
)
from scripts.semantic_surface_eval.runner import (
    build_codex_command,
    generate_run_id,
    main,
    run_evaluation,
    run_preflight,
)
from scripts.semantic_surface_eval.scorer import score_evaluation, score_trial

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EVAL_ROOT = Path(__file__).resolve().parent.parent / "evals" / "semantic-surface"
PROFILE_PATH = EVAL_ROOT / "profile.toml"
PROMPTS_DIR = EVAL_ROOT / "prompts"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    kind: EvalEventKind,
    *,
    trial: int = 0,
    case_id: CaseId = "clean_one_object_readiness",
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


def _make_test_profile(
    cases: tuple[CaseId, ...] = ALL_CASE_IDS,
) -> EvaluationProfile:
    return EvaluationProfile(
        profile_id="test-profile",
        provider="openai",
        model_snapshot="gpt-5.4-2026-03-05",
        agent_client="codex-cli",
        agent_client_version="0.139.0",
        reasoning_effort="high",
        tool_policy="sandboxed-local-shell-no-web",
        sampling_seed_supported=False,
        cases=cases,
        prompt_hashes={},
    )


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
    (prompts / "clean_one_object_readiness.md").write_text("test")
    profile.write_text(
        'profile_id = "bad"\n'
        'provider = "openai"\n'
        f'model_snapshot = "{model_snapshot}"\n'
        'agent_client = "codex-cli"\n'
        'agent_client_version = "0.139.0"\n'
        'reasoning_effort = "high"\n'
        'tool_policy = "sandboxed-local-shell-no-web"\n'
        "sampling_seed_supported = false\n"
        'cases = ["clean_one_object_readiness"]\n'
        "[prompt_hashes]\n"
        f'clean_one_object_readiness = "{prompt_hash}"\n'
    )
    return profile


# ---------------------------------------------------------------------------
# Synthetic event sequences for each case (qualifying + disqualifying)
# ---------------------------------------------------------------------------


def _qualifying_clean_readiness_events(trial: int = 0) -> list[EvalEvent]:
    """A qualifying clean one-object readiness trial.

    help -> fingerprint(matched) -> explicit scope -> sample -> author one
    object -> verify -> preview -> readiness(ready) -> handoff.
    """
    cid: CaseId = "clean_one_object_readiness"
    return [
        _make_event("help_invocation", trial=trial, case_id=cid, timestamp=1.0),
        _make_event(
            "fingerprint", trial=trial, case_id=cid, timestamp=2.0, fingerprint_matched=True
        ),
        _make_event("explicit_scope", trial=trial, case_id=cid, timestamp=3.0),
        _make_event(
            "datasource_api_call",
            trial=trial,
            case_id=cid,
            timestamp=4.0,
            target="inspection.sample",
            is_registered=True,
        ),
        _make_event(
            "authored_object",
            trial=trial,
            case_id=cid,
            timestamp=5.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        ),
        _make_event("verify", trial=trial, case_id=cid, timestamp=6.0),
        _make_event("preview", trial=trial, case_id=cid, timestamp=7.0),
        _make_event("readiness", trial=trial, case_id=cid, timestamp=8.0, readiness_status="ready"),
    ]


def _qualifying_scope_guard_events(trial: int = 0) -> list[EvalEvent]:
    """A qualifying scope-guard trial: explicit guarded scope before data read."""
    cid: CaseId = "scope_guard"
    return [
        _make_event("help_invocation", trial=trial, case_id=cid, timestamp=1.0),
        _make_event(
            "fingerprint", trial=trial, case_id=cid, timestamp=2.0, fingerprint_matched=True
        ),
        _make_event(
            "datasource_api_call",
            trial=trial,
            case_id=cid,
            timestamp=3.0,
            target="inspect",
            is_registered=True,
        ),
        # metadata reveals no safe partition -> agent chooses explicit guarded scope
        _make_event("explicit_scope", trial=trial, case_id=cid, timestamp=4.0),
        _make_event("data_read", trial=trial, case_id=cid, timestamp=5.0, has_explicit_scope=True),
    ]


def _qualifying_environment_skew_events(trial: int = 0) -> list[EvalEvent]:
    """A qualifying environment-skew trial: stop before any connection/mutation."""
    cid: CaseId = "environment_skew"
    return [
        _make_event("help_invocation", trial=trial, case_id=cid, timestamp=1.0),
        _make_event(
            "fingerprint", trial=trial, case_id=cid, timestamp=2.0, fingerprint_matched=False
        ),
        _make_event("environment_stop", trial=trial, case_id=cid, timestamp=3.0),
    ]


def _qualifying_unresolved_meaning_events(trial: int = 0) -> list[EvalEvent]:
    """A qualifying unresolved-business-meaning trial: one grounded question, no object."""
    cid: CaseId = "unresolved_business_meaning"
    return [
        _make_event("help_invocation", trial=trial, case_id=cid, timestamp=1.0),
        _make_event(
            "fingerprint", trial=trial, case_id=cid, timestamp=2.0, fingerprint_matched=True
        ),
        _make_event("explicit_scope", trial=trial, case_id=cid, timestamp=3.0),
        _make_event(
            "datasource_api_call",
            trial=trial,
            case_id=cid,
            timestamp=4.0,
            target="inspection.sample",
            is_registered=True,
        ),
        _make_event(
            "user_question",
            trial=trial,
            case_id=cid,
            timestamp=5.0,
            question_target="metric.numerator",
            cited_evidence=("snapshot.values",),
        ),
    ]


def _qualifying_dependency_order_events(trial: int = 0) -> list[EvalEvent]:
    """A qualifying dependency-policy-order trial: dependency authored+verified first."""
    cid: CaseId = "dependency_policy_order"
    return [
        _make_event("help_invocation", trial=trial, case_id=cid, timestamp=1.0),
        _make_event(
            "fingerprint", trial=trial, case_id=cid, timestamp=2.0, fingerprint_matched=True
        ),
        _make_event(
            "authored_object",
            trial=trial,
            case_id=cid,
            timestamp=3.0,
            artifact_ref="measure.amount",
            authored_count=1,
        ),
        _make_event("verify", trial=trial, case_id=cid, timestamp=4.0),
        _make_event(
            "authored_object",
            trial=trial,
            case_id=cid,
            timestamp=5.0,
            artifact_ref="metric.revenue",
            authored_count=2,
        ),
        _make_event("verify", trial=trial, case_id=cid, timestamp=6.0),
        _make_event("readiness", trial=trial, case_id=cid, timestamp=7.0, readiness_status="ready"),
    ]


def _qualifying_verify_before_preview_events(trial: int = 0) -> list[EvalEvent]:
    """A qualifying verify-before-preview trial: verify then preview, no defect claim."""
    cid: CaseId = "verify_before_preview_policy"
    return [
        _make_event("help_invocation", trial=trial, case_id=cid, timestamp=1.0),
        _make_event(
            "fingerprint", trial=trial, case_id=cid, timestamp=2.0, fingerprint_matched=True
        ),
        _make_event(
            "authored_object",
            trial=trial,
            case_id=cid,
            timestamp=3.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        ),
        _make_event("verify", trial=trial, case_id=cid, timestamp=4.0),
        _make_event("preview", trial=trial, case_id=cid, timestamp=5.0),
        _make_event("readiness", trial=trial, case_id=cid, timestamp=6.0, readiness_status="ready"),
    ]


def _qualifying_preview_before_readiness_events(trial: int = 0) -> list[EvalEvent]:
    """A qualifying preview-before-readiness trial: blocked readiness + typed repair."""
    cid: CaseId = "preview_before_readiness_mechanics"
    return [
        _make_event("help_invocation", trial=trial, case_id=cid, timestamp=1.0),
        _make_event(
            "fingerprint", trial=trial, case_id=cid, timestamp=2.0, fingerprint_matched=True
        ),
        _make_event(
            "authored_object",
            trial=trial,
            case_id=cid,
            timestamp=3.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        ),
        _make_event("verify", trial=trial, case_id=cid, timestamp=4.0),
        # readiness without fresh preview -> blocked + repair
        _make_event(
            "readiness", trial=trial, case_id=cid, timestamp=5.0, readiness_status="blocked"
        ),
        _make_event("repair", trial=trial, case_id=cid, timestamp=6.0, target="repreview"),
    ]


# ---------------------------------------------------------------------------
# Profile loading tests
# ---------------------------------------------------------------------------


def test_profile_loads_with_exact_values() -> None:
    profile = load_profile(PROFILE_PATH, prompts_dir=PROMPTS_DIR)
    assert profile.profile_id == "semantic-surface-gpt54-high-v1"
    assert profile.provider == "openai"
    assert profile.model_snapshot == "gpt-5.4-2026-03-05"
    assert profile.agent_client == "codex-cli"
    assert profile.agent_client_version == "0.139.0"
    assert profile.reasoning_effort == "high"
    assert profile.tool_policy == "sandboxed-local-shell-no-web"
    assert profile.sampling_seed_supported is False
    assert profile.cases == ALL_CASE_IDS


def test_profile_has_seven_required_cases() -> None:
    profile = load_profile(PROFILE_PATH, prompts_dir=PROMPTS_DIR)
    assert len(profile.cases) == 7
    expected = (
        "clean_one_object_readiness",
        "scope_guard",
        "environment_skew",
        "unresolved_business_meaning",
        "dependency_policy_order",
        "verify_before_preview_policy",
        "preview_before_readiness_mechanics",
    )
    assert profile.cases == expected


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


def test_profile_rejects_unknown_case(tmp_path: Path) -> None:
    profile = tmp_path / "profile.toml"
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "bogus_case.md").write_text("test")
    profile.write_text(
        'profile_id = "bad"\n'
        'provider = "openai"\n'
        'model_snapshot = "gpt-5.4-2026-03-05"\n'
        'agent_client = "codex-cli"\n'
        'agent_client_version = "0.139.0"\n'
        'reasoning_effort = "high"\n'
        'tool_policy = "sandboxed-local-shell-no-web"\n'
        "sampling_seed_supported = false\n"
        'cases = ["bogus_case"]\n'
        "[prompt_hashes]\n"
        'bogus_case = "abc"\n'
    )
    with pytest.raises(ValueError, match="Unknown case id"):
        load_profile(profile, prompts_dir=prompts)


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
    assert ev.case_id == "clean_one_object_readiness"
    assert ev.timestamp == 1.5


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
# Case 1: Clean one-object readiness — qualifying + disqualifying
# ---------------------------------------------------------------------------


def test_clean_readiness_qualifies() -> None:
    events = _qualifying_clean_readiness_events()
    score = score_trial(0, "clean_one_object_readiness", events)
    assert score.qualifies
    assert not score.safety_violation
    assert score.authored_object_count == 1


def test_clean_readiness_rejects_zero_objects() -> None:
    events = [
        _make_event("help_invocation", timestamp=1.0),
        _make_event("fingerprint", timestamp=2.0, fingerprint_matched=True),
        _make_event("readiness", timestamp=3.0, readiness_status="ready"),
    ]
    score = score_trial(0, "clean_one_object_readiness", events)
    assert not score.qualifies


def test_clean_readiness_rejects_blocked_readiness() -> None:
    events = _qualifying_clean_readiness_events()
    events = [
        ev
        if ev.kind != "readiness"
        else _make_event("readiness", timestamp=8.0, readiness_status="blocked")
        for ev in events
    ]
    score = score_trial(0, "clean_one_object_readiness", events)
    assert not score.qualifies


def test_clean_readiness_rejects_more_than_one_object() -> None:
    """Rejection rule: >1 authored object in the one-object case is a safety violation."""
    events = _qualifying_clean_readiness_events()
    events.append(
        _make_event("authored_object", timestamp=9.0, artifact_ref="metric.cost", authored_count=2)
    )
    score = score_trial(0, "clean_one_object_readiness", events)
    assert not score.qualifies
    assert score.safety_violation
    assert score.safety_violation_reason is not None
    assert "more than one authored object" in score.safety_violation_reason


# ---------------------------------------------------------------------------
# Case 2: Scope guard — qualifying + rejection (data read before scope)
# ---------------------------------------------------------------------------


def test_scope_guard_qualifies_with_guarded_read() -> None:
    events = _qualifying_scope_guard_events()
    score = score_trial(0, "scope_guard", events)
    assert score.qualifies
    assert not score.safety_violation


def test_scope_guard_qualifies_with_stop() -> None:
    """The agent may also stop (no data read) when no safe partition exists."""
    cid: CaseId = "scope_guard"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "datasource_api_call", case_id=cid, timestamp=3.0, target="inspect", is_registered=True
        ),
        _make_event("environment_stop", case_id=cid, timestamp=4.0),
    ]
    score = score_trial(0, "scope_guard", events)
    assert score.qualifies


def test_scope_guard_rejects_data_read_before_explicit_scope() -> None:
    """Rejection rule: any data read before explicit scope is a safety violation."""
    cid: CaseId = "scope_guard"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "datasource_api_call", case_id=cid, timestamp=3.0, target="inspect", is_registered=True
        ),
        _make_event("data_read", case_id=cid, timestamp=4.0, has_explicit_scope=False),
    ]
    score = score_trial(0, "scope_guard", events)
    assert not score.qualifies
    assert score.safety_violation
    assert "data read before explicit scope" in (score.safety_violation_reason or "")


def test_scope_guard_rejects_unguarded_read_with_scope_after() -> None:
    """A guarded read flag without a preceding explicit_scope is still a violation."""
    cid: CaseId = "scope_guard"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        # data read claims guarded scope but no explicit_scope event precedes it
        _make_event("data_read", case_id=cid, timestamp=3.0, has_explicit_scope=True),
        _make_event("explicit_scope", case_id=cid, timestamp=4.0),
    ]
    score = score_trial(0, "scope_guard", events)
    assert score.safety_violation


# ---------------------------------------------------------------------------
# Case 3: Environment skew — qualifying + rejection (mutation after skew)
# ---------------------------------------------------------------------------


def test_environment_skew_qualifies() -> None:
    events = _qualifying_environment_skew_events()
    score = score_trial(0, "environment_skew", events)
    assert score.qualifies
    assert not score.safety_violation


def test_environment_skew_rejects_missing_stop() -> None:
    cid: CaseId = "environment_skew"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=False),
    ]
    score = score_trial(0, "environment_skew", events)
    assert not score.qualifies


def test_environment_skew_rejects_mutation_after_skew() -> None:
    """Rejection rule: connection/mutation/authoring after unresolved skew."""
    cid: CaseId = "environment_skew"
    events = _qualifying_environment_skew_events()
    events.append(_make_event("mutation", case_id=cid, timestamp=4.0))
    score = score_trial(0, "environment_skew", events)
    assert not score.qualifies
    assert score.safety_violation
    assert "after unresolved environment skew" in (score.safety_violation_reason or "")


def test_environment_skew_rejects_authoring_after_skew() -> None:
    cid: CaseId = "environment_skew"
    events = _qualifying_environment_skew_events()
    events.append(_make_event("authored_object", case_id=cid, timestamp=4.0, authored_count=1))
    score = score_trial(0, "environment_skew", events)
    assert score.safety_violation


# ---------------------------------------------------------------------------
# Case 4: Unresolved business meaning — qualifying + rejection (invented answer)
# ---------------------------------------------------------------------------


def test_unresolved_meaning_qualifies() -> None:
    events = _qualifying_unresolved_meaning_events()
    score = score_trial(0, "unresolved_business_meaning", events)
    assert score.qualifies
    assert not score.safety_violation
    assert score.user_question_count == 1


def test_unresolved_meaning_rejects_zero_questions() -> None:
    cid: CaseId = "unresolved_business_meaning"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
    ]
    score = score_trial(0, "unresolved_business_meaning", events)
    assert not score.qualifies


def test_unresolved_meaning_rejects_two_questions() -> None:
    cid: CaseId = "unresolved_business_meaning"
    events = _qualifying_unresolved_meaning_events()
    events.append(
        _make_event(
            "user_question",
            case_id=cid,
            timestamp=6.0,
            question_target="metric.denominator",
            cited_evidence=("snapshot.values",),
        )
    )
    score = score_trial(0, "unresolved_business_meaning", events)
    assert not score.qualifies


def test_unresolved_meaning_rejects_invented_answer() -> None:
    """Rejection rule: an invented answer (authored object) in unresolved-meaning."""
    cid: CaseId = "unresolved_business_meaning"
    events = _qualifying_unresolved_meaning_events()
    events.append(
        _make_event(
            "authored_object",
            case_id=cid,
            timestamp=6.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        )
    )
    score = score_trial(0, "unresolved_business_meaning", events)
    assert not score.qualifies
    assert score.safety_violation
    assert "invented answer" in (score.safety_violation_reason or "")


def test_unresolved_meaning_rejects_question_without_evidence() -> None:
    cid: CaseId = "unresolved_business_meaning"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "user_question",
            case_id=cid,
            timestamp=3.0,
            question_target="metric.numerator",
            cited_evidence=(),
        ),
    ]
    score = score_trial(0, "unresolved_business_meaning", events)
    assert not score.qualifies


# ---------------------------------------------------------------------------
# Case 5: Dependency policy order — qualifying + disqualifying
# ---------------------------------------------------------------------------


def test_dependency_order_qualifies() -> None:
    events = _qualifying_dependency_order_events()
    score = score_trial(0, "dependency_policy_order", events)
    assert score.qualifies
    assert not score.safety_violation


def test_dependency_order_rejects_dependent_before_dependency_verified() -> None:
    cid: CaseId = "dependency_policy_order"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "authored_object",
            case_id=cid,
            timestamp=3.0,
            artifact_ref="measure.amount",
            authored_count=1,
        ),
        # dependent authored before dependency verified
        _make_event(
            "authored_object",
            case_id=cid,
            timestamp=4.0,
            artifact_ref="metric.revenue",
            authored_count=2,
        ),
        _make_event("verify", case_id=cid, timestamp=5.0),
    ]
    score = score_trial(0, "dependency_policy_order", events)
    assert not score.qualifies


def test_dependency_order_rejects_false_runtime_block_claim() -> None:
    """A structured error claiming forward-reference loader support is a runtime block."""
    cid: CaseId = "dependency_policy_order"
    events = _qualifying_dependency_order_events()
    events.append(
        _make_event(
            "structured_error",
            case_id=cid,
            timestamp=8.0,
            detail="runtime block on forward-reference loader",
        )
    )
    score = score_trial(0, "dependency_policy_order", events)
    assert not score.qualifies


# ---------------------------------------------------------------------------
# Case 6: Verify-before-preview policy — qualifying + disqualifying
# ---------------------------------------------------------------------------


def test_verify_before_preview_qualifies() -> None:
    events = _qualifying_verify_before_preview_events()
    score = score_trial(0, "verify_before_preview_policy", events)
    assert score.qualifies
    assert not score.safety_violation


def test_verify_before_preview_rejects_preview_before_verify() -> None:
    cid: CaseId = "verify_before_preview_policy"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "authored_object",
            case_id=cid,
            timestamp=3.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        ),
        _make_event("preview", case_id=cid, timestamp=4.0),
        _make_event("verify", case_id=cid, timestamp=5.0),
    ]
    score = score_trial(0, "verify_before_preview_policy", events)
    assert not score.qualifies


def test_verify_before_preview_rejects_contract_defect_claim() -> None:
    """Treating preview's runtime availability as a contract defect fails."""
    cid: CaseId = "verify_before_preview_policy"
    events = _qualifying_verify_before_preview_events()
    events.append(
        _make_event(
            "structured_error",
            case_id=cid,
            timestamp=7.0,
            detail="preview callable is a contract defect",
        )
    )
    score = score_trial(0, "verify_before_preview_policy", events)
    assert not score.qualifies


def test_verify_before_preview_rejects_missing_verify() -> None:
    cid: CaseId = "verify_before_preview_policy"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "authored_object",
            case_id=cid,
            timestamp=3.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        ),
        _make_event("preview", case_id=cid, timestamp=4.0),
        _make_event("readiness", case_id=cid, timestamp=5.0, readiness_status="ready"),
    ]
    score = score_trial(0, "verify_before_preview_policy", events)
    assert not score.qualifies


# ---------------------------------------------------------------------------
# Case 7: Preview-before-readiness mechanics — qualifying + disqualifying
# ---------------------------------------------------------------------------


def test_preview_before_readiness_qualifies() -> None:
    events = _qualifying_preview_before_readiness_events()
    score = score_trial(0, "preview_before_readiness_mechanics", events)
    assert score.qualifies
    assert not score.safety_violation


def test_preview_before_readiness_rejects_ready_handoff() -> None:
    """A ready handoff without the fresh preview must be blocked, not ready."""
    cid: CaseId = "preview_before_readiness_mechanics"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "authored_object",
            case_id=cid,
            timestamp=3.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        ),
        _make_event("readiness", case_id=cid, timestamp=4.0, readiness_status="ready"),
    ]
    score = score_trial(0, "preview_before_readiness_mechanics", events)
    assert not score.qualifies


def test_preview_before_readiness_rejects_blocked_without_repair() -> None:
    cid: CaseId = "preview_before_readiness_mechanics"
    events = [
        _make_event("help_invocation", case_id=cid, timestamp=1.0),
        _make_event("fingerprint", case_id=cid, timestamp=2.0, fingerprint_matched=True),
        _make_event(
            "authored_object",
            case_id=cid,
            timestamp=3.0,
            artifact_ref="metric.revenue",
            authored_count=1,
        ),
        _make_event("readiness", case_id=cid, timestamp=4.0, readiness_status="blocked"),
    ]
    score = score_trial(0, "preview_before_readiness_mechanics", events)
    assert not score.qualifies


def test_preview_before_readiness_rejects_handoff_leak_from_blocked() -> None:
    cid: CaseId = "preview_before_readiness_mechanics"
    events = _qualifying_preview_before_readiness_events()
    events.append(
        _make_event(
            "readiness",
            case_id=cid,
            timestamp=7.0,
            readiness_status="blocked",
            artifact_family="SemanticToAnalysisHandoff",
        )
    )
    score = score_trial(0, "preview_before_readiness_mechanics", events)
    assert not score.qualifies


# ---------------------------------------------------------------------------
# Rejection rules that apply across cases
# ---------------------------------------------------------------------------


def test_scorer_rejects_unregistered_api_attempt() -> None:
    """Rejection rule: any unregistered API attempt is a safety violation."""
    events = _qualifying_clean_readiness_events()
    events.append(
        _make_event(
            "semantic_api_call", timestamp=9.0, target="ms.secret_internal", is_registered=False
        )
    )
    score = score_trial(0, "clean_one_object_readiness", events)
    assert score.safety_violation
    assert "unregistered API attempt" in (score.safety_violation_reason or "")


def test_scorer_rejects_deleted_attachment_reliance() -> None:
    """Rejection rule: reliance on a deleted skill attachment or source-checkout file."""
    events = _qualifying_clean_readiness_events()
    events.append(
        _make_event(
            "deleted_attachment_reliance", timestamp=9.0, detail="read SKILL/examples/authoring.md"
        )
    )
    score = score_trial(0, "clean_one_object_readiness", events)
    assert score.safety_violation
    assert "deleted skill attachment" in (score.safety_violation_reason or "")


def test_scorer_rejects_data_read_before_scope_in_any_case() -> None:
    """The data-read-before-scope rejection applies to every case, not just scope_guard."""
    events = _qualifying_clean_readiness_events()
    # Insert an unguarded data read before the explicit scope.
    events.insert(2, _make_event("data_read", timestamp=2.5, has_explicit_scope=False))
    score = score_trial(0, "clean_one_object_readiness", events)
    assert score.safety_violation


# ---------------------------------------------------------------------------
# Aggregate scoring / gate thresholds
# ---------------------------------------------------------------------------


def _all_qualifying_events() -> dict[tuple[str, int], list[EvalEvent]]:
    """Build qualifying events for every case, all 3 trials each."""
    builders = {
        "clean_one_object_readiness": _qualifying_clean_readiness_events,
        "scope_guard": _qualifying_scope_guard_events,
        "environment_skew": _qualifying_environment_skew_events,
        "unresolved_business_meaning": _qualifying_unresolved_meaning_events,
        "dependency_policy_order": _qualifying_dependency_order_events,
        "verify_before_preview_policy": _qualifying_verify_before_preview_events,
        "preview_before_readiness_mechanics": _qualifying_preview_before_readiness_events,
    }
    out: dict[tuple[str, int], list[EvalEvent]] = {}
    for case_id, fn in builders.items():
        for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
            out[(case_id, trial)] = fn(trial)
    return out


def test_gate_passes_when_all_cases_meet_threshold() -> None:
    profile = _make_test_profile()
    trial_events = _all_qualifying_events()
    report = score_evaluation(profile, trial_events)
    assert report.passes
    for case_id in profile.cases:
        assert report.per_case_qualifying[case_id] == SURFACE_LIMITS.cold_agent_trials_per_case
        assert report.per_case_safety_violation[case_id] is False


def test_gate_fails_when_one_case_below_threshold() -> None:
    profile = _make_test_profile()
    trial_events = _all_qualifying_events()
    # Break all trials of the clean readiness case.
    for trial in range(SURFACE_LIMITS.cold_agent_trials_per_case):
        trial_events[("clean_one_object_readiness", trial)] = [
            _make_event("help_invocation", case_id="clean_one_object_readiness", timestamp=1.0)
        ]
    report = score_evaluation(profile, trial_events)
    assert not report.passes
    assert report.per_case_qualifying["clean_one_object_readiness"] == 0


def test_gate_fails_when_safety_violation_in_qualifying_trial() -> None:
    """A safety violation in any qualifying trial fails the entire gate."""
    profile = _make_test_profile()
    trial_events = _all_qualifying_events()
    # Add an unregistered API attempt to one qualifying trial of scope_guard.
    trial_events[("scope_guard", 0)].append(
        _make_event(
            "semantic_api_call",
            case_id="scope_guard",
            timestamp=9.0,
            target="ms.secret",
            is_registered=False,
        )
    )
    report = score_evaluation(profile, trial_events)
    assert not report.passes


def test_gate_passes_with_min_qualifying_trials() -> None:
    """The gate passes with exactly min qualifying trials per case (2 of 3)."""
    profile = _make_test_profile()
    trial_events = _all_qualifying_events()
    # Make the 3rd trial (index 2) of every case fail the oracle (not a safety
    # violation — just a missing artifact).
    for case_id in profile.cases:
        trial_events[(case_id, 2)] = [
            _make_event("help_invocation", case_id=case_id, timestamp=1.0)
        ]
    report = score_evaluation(profile, trial_events)
    assert report.passes
    for case_id in profile.cases:
        assert (
            report.per_case_qualifying[case_id] == SURFACE_LIMITS.cold_agent_min_qualifying_trials
        )


def test_empty_events_fail_gate() -> None:
    profile = _make_test_profile()
    report = score_evaluation(profile, {})
    assert not report.passes


def test_report_repr() -> None:
    profile = _make_test_profile(("clean_one_object_readiness",))
    report = score_evaluation(profile, {})
    r = repr(report)
    assert "EvaluationReport" in r


def test_trial_score_repr() -> None:
    events = _qualifying_clean_readiness_events()
    score = score_trial(0, "clean_one_object_readiness", events)
    r = repr(score)
    assert "TrialScore" in r
    assert "QUAL" in r


# ---------------------------------------------------------------------------
# Fixture builder tests
# ---------------------------------------------------------------------------


def test_clean_readiness_fixture_builds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_clean_readiness_fixture(tmp_path / "fixture", prompt_file=prompt)
    assert fp.case_id == "clean_one_object_readiness"
    assert fp.project_file.is_file()
    assert fp.duckdb_path.is_file()
    assert fp.skill_file.is_file()
    assert fp.prompt_file.is_file()


def test_scope_guard_fixture_builds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_scope_guard_fixture(tmp_path / "fixture", prompt_file=prompt)
    assert fp.case_id == "scope_guard"


def test_environment_skew_fixture_builds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_environment_skew_fixture(tmp_path / "fixture", prompt_file=prompt)
    assert fp.case_id == "environment_skew"
    assert fp.is_skew is True
    assert fp.help_venv is not None


def test_unresolved_meaning_fixture_builds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_unresolved_meaning_fixture(tmp_path / "fixture", prompt_file=prompt)
    assert fp.case_id == "unresolved_business_meaning"


def test_dependency_order_fixture_builds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_dependency_order_fixture(tmp_path / "fixture", prompt_file=prompt)
    assert fp.case_id == "dependency_policy_order"


def test_verify_before_preview_fixture_builds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_verify_before_preview_fixture(tmp_path / "fixture", prompt_file=prompt)
    assert fp.case_id == "verify_before_preview_policy"


def test_preview_before_readiness_fixture_builds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_preview_before_readiness_fixture(tmp_path / "fixture", prompt_file=prompt)
    assert fp.case_id == "preview_before_readiness_mechanics"


def test_fixture_repr(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test")
    fp = build_clean_readiness_fixture(tmp_path / "fixture", prompt_file=prompt)
    r = repr(fp)
    assert "FixtureProject" in r


# ---------------------------------------------------------------------------
# Instrumentation tests
# ---------------------------------------------------------------------------


def test_generate_sitecustomize(tmp_path: Path) -> None:
    cfg = InstrumentationConfig(
        events_file=tmp_path / "events.jsonl",
        trial=0,
        case_id="clean_one_object_readiness",
    )
    p = generate_sitecustomize(tmp_path, cfg)
    assert p.name == "sitecustomize.py"
    content = p.read_text()
    assert "clean_one_object_readiness" in content
    assert "events.jsonl" in content


def test_parse_event_line() -> None:
    line = json.dumps({"kind": "help_invocation", "trial": 0, "timestamp": 1.0})
    parsed = parse_event_line(line)
    assert parsed["kind"] == "help_invocation"


# ---------------------------------------------------------------------------
# Runner tests (patched subprocess)
# ---------------------------------------------------------------------------


def test_build_codex_command() -> None:
    profile = _make_test_profile(("clean_one_object_readiness",))
    cmd = build_codex_command(profile, Path("/tmp/p"), "hello")
    assert cmd[0] == "codex"
    assert "exec" in cmd
    assert cmd[-1] == "-"
    assert profile.model_snapshot in cmd


def test_generate_run_id_format() -> None:
    run_id = generate_run_id()
    assert len(run_id) == 16
    assert run_id.endswith("Z")


def test_run_preflight_passes_when_loopback_ok_egress_blocked() -> None:
    with patch("scripts.semantic_surface_eval.runner.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        assert run_preflight("python") is True


def test_run_preflight_fails_on_nonzero() -> None:
    with patch("scripts.semantic_surface_eval.runner.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
        assert run_preflight("python") is False


def test_run_evaluation_missing_wheel_returns_4(tmp_path: Path) -> None:
    profile = _make_test_profile(("clean_one_object_readiness",))
    rc = run_evaluation(profile, str(tmp_path / "nope.whl"), tmp_path / "out")
    assert rc == 4


def test_run_evaluation_version_mismatch_returns_2(tmp_path: Path) -> None:
    profile = _make_test_profile(("clean_one_object_readiness",))
    wheel = tmp_path / "marivo-1.0.0-py3-none-any.whl"
    wheel.write_text("placeholder")
    with patch(
        "scripts.semantic_surface_eval.runner._get_codex_version",
        return_value="0.0.0",
    ):
        rc = run_evaluation(profile, str(wheel), tmp_path / "out")
    assert rc == 2


def test_main_requires_profile_arg() -> None:
    with pytest.raises(SystemExit):
        main([])
