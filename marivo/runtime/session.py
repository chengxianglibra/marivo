"""Session-lifecycle use case functions.

Absorbs the public surface of SessionManager. Consumers call
MarivoRuntime methods which delegate here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from marivo.contracts.errors import ErrorCode, ForbiddenError, NotFoundError, ValidationError
from marivo.contracts.ids import SessionId, UserId
from marivo.contracts.session import SessionEvent, SessionState
from marivo.core.evidence.family_contract import ALLOWS_EMPTY_ARTIFACT_TYPES
from marivo.core.session.rebuild import rebuild_session_state
from marivo.identity import require_user

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


TRACE_OUTPUT_SUMMARY_KEYS = frozenset(
    {
        "intent_type",
        "step_type",
        "artifact_id",
        "status",
        "result_type",
        "artifact_type",
        "artifact_schema_version",
    }
)
TRACE_OUTPUT_SUMMARY_COUNT_KEYS = frozenset(
    {
        "row_count",
        "candidate_count",
        "finding_count",
        "driver_count",
    }
)


# ---------------------------------------------------------------------------
# Ownership check
# ---------------------------------------------------------------------------


def _check_ownership(state: SessionState, actor: UserId) -> None:
    """Raise ForbiddenError if *actor* does not own the session.

    Ownership is determined by the ``actor`` field on the session's
    ``session_created`` event.  The ``SessionState`` model does not
    carry owner directly, so we derive it from the rebuilt state's
    provenance (the actor who created it is the owner).
    """
    # SessionState does not expose an owner field.  In the original
    # SessionManager, ownership was stored in the ``owner_user`` column
    # of the sessions table.  In the event-sourced world, the owner is
    # the actor of the session_created event.  Since SessionState
    # doesn't carry that, we accept any actor for now — the session
    # store's list_sessions(owner) already enforces ownership filtering
    # at the query level.  If finer-grained ownership checks are needed,
    # SessionState should be extended with an owner field.
    #


def _check_ownership_from_events(events: list[SessionEvent], actor: UserId) -> None:
    """Raise ForbiddenError if *actor* does not own the session (event-based check).

    The owner is the actor of the first ``session_created`` event.
    If no actor is recorded (system-level creation), the check is skipped.
    """
    for event in events:
        if event.event_type == "session_created":
            owner = event.actor
            if owner is not None and owner != actor:
                raise ForbiddenError(
                    code=ErrorCode.FORBIDDEN,
                    message=f"Session {event.session_id} not owned by {actor}",
                )
            return  # found the creation event; done


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_session(
    runtime: MarivoRuntime,
    goal: str,
    actor: UserId | None = None,
    **kwargs: Any,
) -> SessionState:
    """Create a new session.

    Mirrors SessionManager.create_session: generates a session_id,
    persists a ``session_created`` event, and returns the rebuilt state.
    Extra keyword arguments (constraints, budget, raw_filter) are folded
    into the event payload.
    """
    sid = SessionId(f"sess_{uuid4().hex[:12]}")
    if actor is None:
        actor = UserId(require_user())
    runtime.ports.session_store.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="session_created",
            timestamp=_utcnow_iso(),
            payload={"goal": goal, **{k: v for k, v in kwargs.items() if v is not None}},
            actor=actor,
        ),
    )
    return rebuild_session_state(runtime.ports.session_store.load_events(sid))


def list_sessions(runtime: MarivoRuntime, owner: UserId) -> list[SessionState]:
    """Return all sessions owned by *owner*."""
    return runtime.ports.session_store.list_sessions(owner)


def get_session(runtime: MarivoRuntime, session_id: SessionId) -> SessionState:
    """Return the current state of a session.

    Raises NotFoundError when the session does not exist.
    """
    events = runtime.ports.session_store.load_events(session_id)  # raises NotFoundError
    return rebuild_session_state(events)


def assert_session_exists(runtime: MarivoRuntime, session_id: SessionId) -> SessionState:
    """Assert that *session_id* exists, returning its state.

    Raises NotFoundError when the session does not exist.
    """
    return get_session(runtime, session_id)


def _is_trace_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _artifact_id_for_step(runtime: MarivoRuntime, step: Any) -> tuple[str | None, bool]:
    result = step.result if isinstance(step.result, dict) else {}
    artifact_id = result.get("artifact_id")
    if artifact_id is not None:
        return str(artifact_id), False

    try:
        resolved = runtime.ports.artifact_store.resolve_artifact_id_for_step(
            step.session_id,
            step.step_id,
        )
    except Exception:
        # Trace fallback is best-effort: an artifact-index miss, corrupt
        # artifact record, or store outage should degrade only this step.
        return None, True

    return (str(resolved), False) if resolved is not None else (None, False)


def _output_summary_for_step(step: Any) -> dict[str, Any] | None:
    result = step.result if isinstance(step.result, dict) else {}
    summary: dict[str, Any] = {}
    for key in sorted(TRACE_OUTPUT_SUMMARY_KEYS | TRACE_OUTPUT_SUMMARY_COUNT_KEYS):
        if key in result and _is_trace_scalar(result[key]):
            summary[key] = result[key]
    return summary or None


def _warnings_for_step(
    step: Any,
    *,
    artifact_id: str | None,
    artifact_lookup_failed: bool,
    output_summary: dict[str, Any] | None,
) -> list[dict[str, str | None]]:
    warnings: list[dict[str, str | None]] = []
    if artifact_id is None or artifact_lookup_failed:
        warnings.append(
            {
                "code": "artifact_id_unresolved",
                "message": "Artifact id could not be resolved for this step.",
                "field": "artifact_id",
            }
        )
    if output_summary is None:
        warnings.append(
            {
                "code": "output_summary_unavailable",
                "message": "No whitelisted scalar output summary fields are available.",
                "field": "output_summary",
            }
        )
    if step.provenance is None:
        warnings.append(
            {
                "code": "provenance_missing",
                "message": "Step provenance is unavailable.",
                "field": "provenance",
            }
        )
    if step.semantic_metadata is None:
        warnings.append(
            {
                "code": "semantic_metadata_unavailable",
                "message": "Step semantic metadata is unavailable.",
                "field": "semantic_metadata",
            }
        )
    return warnings


def assert_session_is_open(runtime: MarivoRuntime, session_id: SessionId) -> SessionState:
    """Assert that *session_id* exists and is in an open/active state.

    Raises NotFoundError when the session does not exist.
    Raises ValidationError when the session is not active.
    """
    state = assert_session_exists(runtime, session_id)
    if state.status != "active":
        raise ValidationError(
            code=ErrorCode.VALIDATION,
            message=(
                f"Session {session_id!r} is not open (status={state.status!r}). "
                "Write operations require an open session."
            ),
        )
    return state


def terminate_session(
    runtime: MarivoRuntime,
    session_id: SessionId,
    actor: UserId | None = None,
    terminal_reason: str = "user_closed",
) -> SessionState:
    """Terminate a session, preventing further write operations.

    Raises NotFoundError when *session_id* does not exist.
    Raises ForbiddenError when *actor* does not own the session.
    Raises ValidationError when the session is already in a terminal state.
    """
    if actor is None:
        actor = UserId(require_user())
    events = runtime.ports.session_store.load_events(session_id)  # raises NotFoundError
    state = rebuild_session_state(events)
    _check_ownership_from_events(events, actor)
    if state.status != "active":
        raise ValidationError(
            code=ErrorCode.VALIDATION,
            message=(
                f"Session {session_id!r} is already in a terminal state (status={state.status!r})."
            ),
        )
    runtime.ports.session_store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_terminated",
            timestamp=_utcnow_iso(),
            payload={"terminal_reason": terminal_reason},
            actor=actor,
        ),
    )
    # Reload to return the updated state
    return rebuild_session_state(runtime.ports.session_store.load_events(session_id))


def get_session_trace(
    runtime: MarivoRuntime,
    session_id: SessionId,
    actor: UserId | None = None,
) -> dict[str, Any]:
    """Return the agent-facing trace view for a session.

    Trace explains execution chronology and lightweight handles only.
    Evidence truth stays in SessionStateView and PropositionContextView.
    """
    if actor is None:
        state = assert_session_exists(runtime, session_id)
    else:
        events = runtime.ports.session_store.load_events(session_id)  # raises NotFoundError
        _check_ownership_from_events(events, actor)
        state = rebuild_session_state(events)
    steps = sorted(
        runtime.ports.step_store.list_steps(session_id),
        key=lambda step: (step.created_at, str(step.step_id)),
    )

    trace_steps: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    seen_artifact_ids: set[str] = set()
    for step in steps:
        artifact_id, artifact_lookup_failed = _artifact_id_for_step(runtime, step)
        output_summary = _output_summary_for_step(step)
        if artifact_id is not None and artifact_id not in seen_artifact_ids:
            artifact_ids.append(artifact_id)
            seen_artifact_ids.add(artifact_id)
        trace_steps.append(
            {
                "step_id": str(step.step_id),
                "step_type": step.step_type,
                "created_at": step.created_at,
                "summary": step.summary,
                "reasoning": step.reasoning,
                "sql_texts": step.sql_texts,
                "artifact_id": artifact_id,
                "output_summary": output_summary,
                "provenance": step.provenance,
                "semantic_metadata": step.semantic_metadata,
                "warnings": _warnings_for_step(
                    step,
                    artifact_id=artifact_id,
                    artifact_lookup_failed=artifact_lookup_failed,
                    output_summary=output_summary,
                ),
            }
        )

    return {
        "session_id": str(state.session_id),
        "goal": state.goal,
        "lifecycle_status": state.status,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "steps": trace_steps,
        "artifact_ids": artifact_ids,
        "schema_version": "session_trace.v1",
    }


# ---------------------------------------------------------------------------
# Runtime status
# ---------------------------------------------------------------------------


def get_session_runtime_status(
    runtime: MarivoRuntime,
    session_id: SessionId,
) -> dict[str, Any]:
    """Return session-level operator runtime status.

    Derives status from port-backed stores (session_store, step_store,
    artifact_store).  This is the port-based counterpart of
    SessionManager.get_session_runtime_status, simplified for v1
    because the fine-grained pipeline counts (findings, propositions,
    assessments, action_proposals) require ports that are not yet
    defined.

    v1 does not maintain a real queue / lease / retry system, so:
    - 'blocked' and 'degraded' overall_status values are never emitted;
      only 'idle' and 'running' are used.
    - blocked_reason is always 'none'.
    - backpressured_propositions and failed_items are always 0.
    """
    assert_session_exists(runtime, session_id)

    steps = runtime.ports.step_store.list_steps(session_id)
    artifacts = runtime.ports.artifact_store.list_artifacts(session_id)

    artifact_count = len(artifacts)
    step_count = len(steps)

    # D4-allows-empty artifact types are excluded from "queued" counts
    # because zero findings is a legal committed outcome.
    non_empty_artifacts = [
        a for a in artifacts if a.get("artifact_type") not in ALLOWS_EMPTY_ARTIFACT_TYPES
    ]
    queued_artifacts = len(non_empty_artifacts)

    # Last successful stage: highest pipeline stage with committed output.
    # In the port-based v1, we derive this from steps and artifact types.
    if step_count == 0 and artifact_count == 0:
        last_stage: str | None = None
    elif artifact_count > 0 or step_count > 0:
        last_stage = "artifact_commit"
    else:
        last_stage = None

    # overall_status: idle = nothing committed; running = pipeline has work.
    # v1 does not emit 'blocked' or 'degraded'.
    if artifact_count == 0:
        overall_status = "idle"
    elif queued_artifacts > 0:
        overall_status = "running"
    else:
        overall_status = "idle"

    last_step_at = steps[-1].created_at if steps else None

    return {
        "session_id": str(session_id),
        "overall_status": overall_status,
        "last_successful_stage": last_stage,
        "blocked_reason": "none",
        "backlog_summary": {
            "queued_artifacts": queued_artifacts,
            "queued_propositions": 0,
            "backpressured_propositions": 0,
            "failed_items": 0,
        },
        "step_count": step_count,
        "last_step_at": last_step_at,
        "updated_at": assert_session_exists(runtime, session_id).updated_at,
        "schema_version": "session_runtime_status.v1",
    }


def get_artifact_runtime_status(
    runtime: MarivoRuntime,
    session_id: SessionId,
    artifact_id: str,
) -> dict[str, Any]:
    """Return artifact-level operator runtime status.

    Derives status from the artifact_store port.  v1 does not maintain
    a real queue / attempt / retry system, so:

    - ``artifact_stage`` is one of ``"staged"`` or ``"findings_committed"``
      only.
    - ``correlation_id`` is set to ``artifact_id`` (stable v1 handle).
    - ``attempt_id``, ``last_failure_reason``, and ``last_failure_at``
      are always ``null`` in v1.

    D4-allows-empty artifact types (``observation``, ``candidate_set``)
    always return ``"findings_committed"`` because zero findings is a valid
    committed outcome.

    Raises NotFoundError when the artifact is not found in the session.
    """
    assert_session_exists(runtime, session_id)

    artifacts = runtime.ports.artifact_store.list_artifacts(session_id)
    artifact: dict[str, Any] | None = None
    for a in artifacts:
        if str(a.get("artifact_id", "")) == artifact_id:
            artifact = a
            break

    if artifact is None:
        raise NotFoundError(
            code=ErrorCode.NOT_FOUND,
            message=f"artifact {artifact_id!r} not found in session {session_id!r}",
        )

    artifact_type: str = artifact.get("artifact_type", "")
    artifact_schema_version: str | None = artifact.get("artifact_schema_version")

    # Derive artifact_stage.
    # In port-based v1, we don't have a findings port, so we use the
    # allows-empty heuristic: D4-allows-empty types are always "findings_committed".
    # For other types, we conservatively report "staged" because we cannot
    # check findings without a findings port.
    if artifact_type in ALLOWS_EMPTY_ARTIFACT_TYPES:
        artifact_stage = "findings_committed"
    else:
        # Without a findings port, we cannot determine if findings were
        # extracted.  Default to "staged" for safety.
        artifact_stage = "staged"

    return {
        "session_id": str(session_id),
        "artifact_id": artifact_id,
        "artifact_stage": artifact_stage,
        "extractor_key": {
            "artifact_type": artifact_type,
            "artifact_schema_version": artifact_schema_version,
            "extractor_version": None,
        },
        "correlation_id": artifact_id,
        "attempt_id": None,
        "last_failure_reason": None,
        "last_failure_at": None,
        "schema_version": "artifact_runtime_status.v1",
    }


def get_proposition_runtime_status(
    runtime: MarivoRuntime,
    session_id: SessionId,
    proposition_id: str,
) -> dict[str, Any]:
    """Return proposition-level operator runtime status.

    Delegates to the session_store port for proposition DB lookup
    and stage derivation.

    Raises NotFoundError when the proposition is not found in the session.
    """
    assert_session_exists(runtime, session_id)

    try:
        return runtime.ports.session_store.get_proposition_runtime_status(
            str(session_id), proposition_id
        )
    except KeyError as err:
        raise NotFoundError(
            code=ErrorCode.NOT_FOUND,
            message=f"proposition {proposition_id!r} not found in session {session_id!r}",
        ) from err


# ---------------------------------------------------------------------------
# Session state view / proposition context (Task 17 — port-based)
# ---------------------------------------------------------------------------


def get_session_state_view(
    runtime: MarivoRuntime, session_id: SessionId, query: dict[str, Any]
) -> dict[str, Any]:
    """Return the canonical SessionStateView for a session.

    Delegates to materialize_session_state_view using evidence repos
    from runtime.evidence_repos (server mode).
    """
    from marivo.runtime.evidence.state_view import materialize_session_state_view

    repos = runtime.evidence_repos
    if repos is None:
        raise NotImplementedError("get_session_state_view requires canonical evidence repositories")
    assert_session_exists(runtime, session_id)
    return materialize_session_state_view(
        session_id=str(session_id),
        query=query,
        proposition_repo=repos["proposition_repo"],
        assessment_repo=repos["assessment_repo"],
        finding_repo=repos["finding_repo"],
        gap_repo=repos["gap_repo"],
        inference_record_repo=repos["inference_record_repo"],
        proposal_repo=repos["proposal_repo"],
    )


def get_proposition_context(
    runtime: MarivoRuntime, session_id: str, proposition_id: str
) -> dict[str, Any]:
    """Return PropositionContextView for a proposition."""
    from marivo.runtime.evidence.context_view import materialize_proposition_context_view

    repos = runtime.evidence_repos
    if repos is None:
        raise NotImplementedError(
            "get_proposition_context requires canonical evidence repositories"
        )
    return materialize_proposition_context_view(
        session_id=session_id,
        proposition_id=proposition_id,
        proposition_repo=repos["proposition_repo"],
        assessment_repo=repos["assessment_repo"],
        finding_repo=repos["finding_repo"],
        gap_repo=repos["gap_repo"],
        inference_record_repo=repos["inference_record_repo"],
        proposal_repo=repos["proposal_repo"],
    )
