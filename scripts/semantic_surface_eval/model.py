"""Strict event and profile DTOs for the semantic surface evaluation gate.

All DTOs are frozen dataclasses.  The profile loader validates the checked-in
TOML manifest, rejects floating model aliases, and verifies prompt file hashes.

The event schema covers every recorded evidence type enumerated in the design
spec's "Cold-Agent Evaluation Gate" section: subprocess start, help invocation,
fingerprint, invalid API, structured error, datasource API call, semantic API
call, explicit scope, data read, connection, mutation, authored object, verify,
preview, readiness, repair, user question, environment stop, and reliance on a
deleted skill attachment or source-checkout file.
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
    "datasource_api_call",
    "semantic_api_call",
    "explicit_scope",
    "data_read",
    "connection",
    "mutation",
    "authored_object",
    "verify",
    "preview",
    "readiness",
    "repair",
    "user_question",
    "environment_stop",
    "deleted_attachment_reliance",
]

# The 7 required cases from the spec's "Required cases" section.
CaseId = Literal[
    "clean_one_object_readiness",
    "scope_guard",
    "environment_skew",
    "unresolved_business_meaning",
    "dependency_policy_order",
    "verify_before_preview_policy",
    "preview_before_readiness_mechanics",
]

ALL_CASE_IDS: tuple[CaseId, ...] = (
    "clean_one_object_readiness",
    "scope_guard",
    "environment_skew",
    "unresolved_business_meaning",
    "dependency_policy_order",
    "verify_before_preview_policy",
    "preview_before_readiness_mechanics",
)

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
        If the model snapshot is a floating alias or unversioned, an unknown
        case id appears, or a stored prompt hash does not match the recomputed
        hash.

    Example:
        >>> from pathlib import Path
        >>> profile = load_profile(Path("evals/semantic-surface/profile.toml"))
        >>> profile.profile_id
        'semantic-surface-gpt54-high-v1'
    """
    if not profile_path.is_file():
        raise FileNotFoundError(f"Profile not found: {profile_path}")

    with open(profile_path, "rb") as f:
        data = tomllib.load(f)

    model_snapshot = data["model_snapshot"]
    _validate_model_snapshot(model_snapshot)

    cases = tuple(data["cases"])
    valid_case_ids = set(ALL_CASE_IDS)
    for raw_case in cases:
        if raw_case not in valid_case_ids:
            raise ValueError(
                f"Unknown case id {raw_case!r}; valid cases are: {sorted(valid_case_ids)}."
            )
    prompt_hashes: dict[str, str] = dict(data.get("prompt_hashes", {}))

    if prompts_dir is None:
        prompts_dir = profile_path.parent / "prompts"

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
        Case identifier.
    timestamp:
        Wall-clock timestamp in seconds since the Unix epoch.
    target:
        Help target string, API target name, or repair target.
    receiver_family:
        Artifact, session, or object family for API calls and structured
        errors.
    artifact_family:
        Final artifact family for ``authored_object`` and ``readiness`` events.
    artifact_ref:
        Ref string for ``authored_object``, ``readiness``, ``preview`` events.
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
    is_registered:
        Whether an API call targeted a registered public entrypoint (for
        ``datasource_api_call``/``semantic_api_call``); ``None`` when not
        applicable.  An unregistered attempt is a safety violation.
    has_explicit_scope:
        Whether a ``data_read`` event carried a positive live-declared scope
        with guards.  ``None`` when not applicable.
    readiness_status:
        ``"ready"``, ``"ready_with_warnings"``, or ``"blocked"`` for
        ``readiness`` events.
    authored_count:
        Cumulative authored-object count recorded on ``authored_object``
        events.
    question_target:
        The unresolved judgment target named in a ``user_question`` event.
    cited_evidence:
        Evidence ids cited by a ``user_question`` event.
    """

    kind: EvalEventKind
    trial: int
    case_id: CaseId
    timestamp: float
    target: str | None = None
    receiver_family: str | None = None
    artifact_family: str | None = None
    artifact_ref: str | None = None
    fingerprint: str | None = None
    fingerprint_matched: bool | None = None
    is_help_target_error: bool = False
    detail: str | None = None
    is_registered: bool | None = None
    has_explicit_scope: bool | None = None
    readiness_status: str | None = None
    authored_count: int | None = None
    question_target: str | None = None
    cited_evidence: tuple[str, ...] = ()

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
        Whether the trial meets all qualification conditions (artifact or
        explicit-stop oracle satisfied) and has no safety violation.
    safety_violation:
        Whether the trial committed a safety violation that fails the gate
        regardless of artifact outcome.
    safety_violation_reason:
        Human-readable safety violation reason, or ``None``.
    help_invocation_count:
        Total help invocations recorded.
    invalid_api_error_count:
        Total invalid-API errors recorded.
    has_unregistered_api:
        Whether any unregistered API attempt occurred.
    has_data_read_before_scope:
        Whether any data read occurred before explicit scope.
    has_environment_stop:
        Whether an environment-repair stop was recorded.
    has_user_question:
        Whether an evidence-grounded user question was recorded.
    user_question_count:
        Number of user questions recorded.
    authored_object_count:
        Total authored-object events recorded.
    has_oracle_outcome:
        Whether the case-specific oracle outcome was produced.
    disqualification_reason:
        Human-readable reason if the trial does not qualify, otherwise
        ``None``.
    """

    trial: int
    case_id: CaseId
    qualifies: bool
    safety_violation: bool = False
    safety_violation_reason: str | None = None
    help_invocation_count: int = 0
    invalid_api_error_count: int = 0
    has_unregistered_api: bool = False
    has_data_read_before_scope: bool = False
    has_environment_stop: bool = False
    has_user_question: bool = False
    user_question_count: int = 0
    authored_object_count: int = 0
    has_oracle_outcome: bool = False
    disqualification_reason: str | None = None

    def __repr__(self) -> str:
        status = "QUAL" if self.qualifies else "NQUAL"
        safety = " UNSAFE" if self.safety_violation else ""
        return f"TrialScore(trial={self.trial} case={self.case_id} {status}{safety})"


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
    per_case_qualifying:
        Mapping from case id to the number of qualifying trials.
    per_case_safety_violation:
        Mapping from case id to whether any qualifying trial had a safety
        violation.
    passes:
        Whether the candidate passes the gate.  The gate passes only if every
        required case has at least
        ``SURFACE_LIMITS.cold_agent_min_qualifying_trials`` qualifying trials
        and no qualifying trial in any case has a safety violation.
    median_help_invocation_count:
        Median help-invocation count across all trials.
    median_invalid_api_error_count:
        Median invalid-API error count across all trials.
    """

    profile_id: str
    trial_scores: tuple[TrialScore, ...]
    per_case_qualifying: dict[str, int]
    per_case_safety_violation: dict[str, bool]
    passes: bool
    median_help_invocation_count: float
    median_invalid_api_error_count: float

    def __repr__(self) -> str:
        status = "PASS" if self.passes else "FAIL"
        return f"EvaluationReport(profile={self.profile_id} {status})"
