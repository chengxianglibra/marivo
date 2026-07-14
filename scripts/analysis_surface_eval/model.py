"""Strict event and profile DTOs for the analysis surface evaluation gate.

All DTOs are frozen dataclasses.  The profile loader validates the checked-in
TOML manifest, rejects floating model aliases, and verifies prompt file hashes.

The event schema covers every recorded evidence type enumerated in the design
spec: subprocess start, help invocation, fingerprint, invalid API, structured
error, analysis API call, correct observe, artifact, mismatch detection,
environment stop, and retired-name attribute error.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Enums / literal types
# ---------------------------------------------------------------------------

EvalEventKind = Literal[
    "subprocess_start",
    "help_invocation",
    "fingerprint",
    "invalid_api",
    "structured_error",
    "analysis_api_call",
    "correct_observe",
    "artifact",
    "mismatch_detection",
    "environment_stop",
    "retired_name_attribute_error",
]

ObservePhase = Literal["before_observe", "after_observe"]

CaseId = Literal["clean_convergence", "environment_skew"]

# ---------------------------------------------------------------------------
# Evaluation profile
# ---------------------------------------------------------------------------

_FORBIDDEN_MODEL_TOKENS = ("latest", "stable", "preview", "canary", "nightly")


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _validate_model_snapshot(model_snapshot: str) -> None:
    """Reject floating aliases and unversioned model names.

    Parameters
    ----------
    model_snapshot:
        The pinned model snapshot id from the profile TOML.

    Raises
    ------
    ValueError
        If the snapshot contains a forbidden alias token or lacks a date
        version suffix (``YYYY-MM-DD``).
    """
    lowered = model_snapshot.lower()
    for token in _FORBIDDEN_MODEL_TOKENS:
        if token in lowered:
            raise ValueError(
                f"model_snapshot {model_snapshot!r} contains forbidden "
                f"floating alias {token!r}; use a pinned dated snapshot."
            )
    # Require at least one date-like segment (YYYY-MM-DD).
    parts = model_snapshot.split("-")
    has_date = False
    for i in range(len(parts) - 2):
        if (
            parts[i].isdigit()
            and len(parts[i]) == 4
            and parts[i + 1].isdigit()
            and len(parts[i + 1]) == 2
            and parts[i + 2].isdigit()
            and len(parts[i + 2]) == 2
        ):
            has_date = True
            break
    if not has_date:
        raise ValueError(
            f"model_snapshot {model_snapshot!r} is unversioned; "
            "use a pinned dated snapshot such as 'gpt-5.4-2026-03-05'."
        )


@dataclass(frozen=True)
class EvaluationProfile:
    """Immutable evaluation profile loaded from the checked-in TOML manifest.

    Parameters
    ----------
    profile_id:
        Stable profile identifier.  Changing any field creates a new profile.
    provider:
        Model provider (e.g. ``"openai"``).
    model_snapshot:
        Pinned model snapshot id.  Floating aliases such as ``latest`` are
        forbidden.
    agent_client:
        Agent client name (e.g. ``"codex-cli"``).
    agent_client_version:
        Pinned agent client version.
    reasoning_effort:
        Pinned reasoning/effort tier.
    tool_policy:
        Pinned tool policy.
    sampling_seed_supported:
        Whether the provider supports deterministic sampling seeds.
    cases:
        Ordered tuple of case ids covered by this profile.
    prompt_hashes:
        Mapping from case id to the expected SHA-256 hex digest of the
        corresponding prompt file.
    """

    profile_id: str
    provider: str
    model_snapshot: str
    agent_client: str
    agent_client_version: str
    reasoning_effort: str
    tool_policy: str
    sampling_seed_supported: bool
    cases: tuple[CaseId, ...]
    prompt_hashes: dict[str, str]

    def __repr__(self) -> str:
        return (
            f"EvaluationProfile(id={self.profile_id} "
            f"model={self.model_snapshot} "
            f"cases={list(self.cases)})"
        )


def load_profile(
    profile_path: Path,
    *,
    prompts_dir: Path | None = None,
) -> EvaluationProfile:
    """Load and validate an evaluation profile from a TOML manifest.

    Parameters
    ----------
    profile_path:
        Path to the ``profile.toml`` manifest.
    prompts_dir:
        Directory containing prompt files.  Defaults to
        ``profile_path.parent / "prompts"``.

    Returns
    -------
    EvaluationProfile
        The validated immutable profile.

    Raises
    ------
    FileNotFoundError
        If the profile or a prompt file is missing.
    ValueError
        If the model snapshot is a floating alias or unversioned, or if a
        stored prompt hash does not match the recomputed hash.

    Example:
        >>> from pathlib import Path
        >>> profile = load_profile(Path("evals/analysis_surface/profile.toml"))
        >>> profile.profile_id
        'analysis-surface-gpt54-high-v1'
    """
    if not profile_path.is_file():
        raise FileNotFoundError(f"Profile not found: {profile_path}")

    with open(profile_path, "rb") as f:
        data = tomllib.load(f)

    model_snapshot = data["model_snapshot"]
    _validate_model_snapshot(model_snapshot)

    cases = tuple(data["cases"])
    valid_case_ids = {"clean_convergence", "environment_skew"}
    for raw_case in cases:
        if raw_case not in valid_case_ids:
            raise ValueError(
                f"Unknown case id {raw_case!r}; valid cases are: {sorted(valid_case_ids)}."
            )
    prompt_hashes: dict[str, str] = dict(data.get("prompt_hashes", {}))

    # Resolve prompts directory.
    if prompts_dir is None:
        prompts_dir = profile_path.parent / "prompts"

    # Validate prompt hashes.
    for case_id in cases:
        prompt_file = prompts_dir / f"{case_id}.md"
        if not prompt_file.is_file():
            raise FileNotFoundError(f"Prompt file for case {case_id!r} not found: {prompt_file}")
        stored_hash = prompt_hashes.get(case_id)
        if stored_hash is None:
            raise ValueError(f"Profile is missing prompt hash for case {case_id!r}.")
        actual_hash = _sha256_file(prompt_file)
        if actual_hash != stored_hash:
            raise ValueError(
                f"Prompt hash drift for case {case_id!r}: "
                f"stored {stored_hash!r} but recomputed {actual_hash!r}."
            )

    return EvaluationProfile(
        profile_id=data["profile_id"],
        provider=data["provider"],
        model_snapshot=model_snapshot,
        agent_client=data["agent_client"],
        agent_client_version=data["agent_client_version"],
        reasoning_effort=data["reasoning_effort"],
        tool_policy=data["tool_policy"],
        sampling_seed_supported=data["sampling_seed_supported"],
        cases=cases,
        prompt_hashes=prompt_hashes,
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalEvent:
    """A single recorded evaluation event.

    Every event carries a trial index, case id, and wall-clock timestamp.
    Optional payload fields are populated based on ``kind``.

    Parameters
    ----------
    kind:
        Event kind from the closed :data:`EvalEventKind` set.
    trial:
        Zero-based trial index within the case.
    case_id:
        Case identifier (``"clean_convergence"`` or ``"environment_skew"``).
    timestamp:
        Wall-clock timestamp in seconds since the Unix epoch.
    target:
        Help target string (for ``help_invocation`` and ``invalid_api``).
    receiver_family:
        Artifact or session family for ``analysis_api_call`` and
        ``retired_name_attribute_error`` events.
    artifact_family:
        Final artifact family for ``artifact`` events.
    artifact_ref:
        Final artifact ref for ``artifact`` events.
    observe_phase:
        ``"before_observe"`` or ``"after_observe"`` for events that can occur
        on either side of the first correct ``observe()``.
    fingerprint:
        Fingerprint string for ``fingerprint`` events.
    fingerprint_matched:
        Whether the fingerprint matched the authoritative source, for
        ``fingerprint`` events.
    is_help_target_error:
        Whether a ``help_invocation`` also raised ``HelpTargetError``
        (double-counted as ``invalid_api``).
    detail:
        Free-form detail string for additional context.
    """

    kind: EvalEventKind
    trial: int
    case_id: CaseId
    timestamp: float
    target: str | None = None
    receiver_family: str | None = None
    artifact_family: str | None = None
    artifact_ref: str | None = None
    observe_phase: ObservePhase = "before_observe"
    fingerprint: str | None = None
    fingerprint_matched: bool | None = None
    is_help_target_error: bool = False
    detail: str | None = None

    def __repr__(self) -> str:
        return (
            f"EvalEvent(kind={self.kind} trial={self.trial} "
            f"case={self.case_id} ts={self.timestamp:.3f})"
        )


# ---------------------------------------------------------------------------
# Trial score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrialScore:
    """Score for a single trial.

    Parameters
    ----------
    trial:
        Zero-based trial index.
    case_id:
        Case identifier.
    qualifies:
        Whether the trial meets all qualification conditions.
    help_invocation_count:
        Total help invocations recorded (all phases).
    help_invocation_count_before_observe:
        Help invocations before the first correct ``observe()``.
    invalid_api_error_count:
        Total invalid-API errors recorded (all phases).
    invalid_api_error_count_before_observe:
        Invalid-API errors before the first correct ``observe()``.
    has_oracle_artifact:
        Whether the oracle-expected final artifact was produced
        (convergence only).
    has_matching_fingerprint_before_analysis:
        Whether a matching fingerprint was established before the first
        analysis call (convergence only).
    used_native_reflection:
        Whether native reflection was used for Marivo contract discovery.
    mismatch_detected:
        Whether a fingerprint mismatch was detected (skew only).
    environment_stop_recorded:
        Whether an environment-repair stop was recorded (skew only).
    analysis_api_call_count:
        Total Marivo analysis API calls in the trial.
    retired_name_attribute_error_count:
        Diagnostic count of retired-name ``AttributeError`` events.
    disqualification_reason:
        Human-readable reason if the trial does not qualify, otherwise
        ``None``.
    """

    trial: int
    case_id: CaseId
    qualifies: bool
    help_invocation_count: int
    help_invocation_count_before_observe: int
    invalid_api_error_count: int
    invalid_api_error_count_before_observe: int
    has_oracle_artifact: bool = False
    has_matching_fingerprint_before_analysis: bool = False
    used_native_reflection: bool = False
    mismatch_detected: bool = False
    environment_stop_recorded: bool = False
    analysis_api_call_count: int = 0
    retired_name_attribute_error_count: int = 0
    disqualification_reason: str | None = None

    def __repr__(self) -> str:
        status = "QUAL" if self.qualifies else "NQUAL"
        return (
            f"TrialScore(trial={self.trial} case={self.case_id} "
            f"{status} help={self.help_invocation_count_before_observe} "
            f"err={self.invalid_api_error_count_before_observe})"
        )


# ---------------------------------------------------------------------------
# Evaluation report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregate evaluation report across all cases and trials.

    Parameters
    ----------
    profile_id:
        The evaluation profile id.
    trial_scores:
        All trial scores, ordered by case then trial index.
    convergence_qualifying_count:
        Number of qualifying convergence trials.
    skew_all_qualified:
        Whether every skew trial qualified.
    passes:
        Whether the candidate passes the gate.
    median_help_invocation_count:
        Median help-invocation count across convergence trials.
    median_invalid_api_error_count:
        Median invalid-API error count across convergence trials.
    aggregate_retired_name_count:
        Total retired-name ``AttributeError`` events across all trials.
    per_trial_retired_name_counts:
        Per-trial retired-name counts.
    """

    profile_id: str
    trial_scores: tuple[TrialScore, ...]
    convergence_qualifying_count: int
    skew_all_qualified: bool
    passes: bool
    median_help_invocation_count: float
    median_invalid_api_error_count: float
    aggregate_retired_name_count: int
    per_trial_retired_name_counts: tuple[tuple[int, str, int], ...] = ()

    def __repr__(self) -> str:
        status = "PASS" if self.passes else "FAIL"
        return (
            f"EvaluationReport(profile={self.profile_id} {status} "
            f"conv_qual={self.convergence_qualifying_count} "
            f"skew_all={self.skew_all_qualified})"
        )
