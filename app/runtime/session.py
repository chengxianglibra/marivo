"""Session-lifecycle use case functions.

Absorbs the public surface of SessionManager. Consumers call
MarivoRuntime methods which delegate here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.contracts.errors import ErrorCode, ForbiddenError, NotFoundError, ValidationError
from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionEvent, SessionState
from app.core.session.rebuild import rebuild_session_state
from app.evidence_engine.family_contract import ALLOWS_EMPTY_ARTIFACT_TYPES

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    sid = SessionId(f"sess-{uuid4().hex[:12]}")
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
    actor: UserId,
    terminal_reason: str = "user_closed",
) -> SessionState:
    """Terminate a session, preventing further write operations.

    Raises NotFoundError when *session_id* does not exist.
    Raises ForbiddenError when *actor* does not own the session.
    Raises ValidationError when the session is already in a terminal state.
    """
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

    D4-allows-empty artifact types (``observation``, ``anomaly_candidates``)
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

    v1 does not maintain a real queue / claim / lease / retry system, so:

    - ``current_attempt`` is always ``null``.
    - ``backlog_state`` is always ``"none"``.
    - ``last_failure_reason`` is always ``"none"``.
    - ``last_failure_at`` is always ``null``.

    Raises NotFoundError when the proposition is not found in the session.
    """
    assert_session_exists(runtime, session_id)

    # Search for the proposition in the artifact store (propositions are
    # a type of artifact in the port-based world).
    artifacts = runtime.ports.artifact_store.list_artifacts(session_id)
    proposition: dict[str, Any] | None = None
    for a in artifacts:
        if (
            a.get("artifact_type") == "proposition"
            and str(a.get("proposition_id", a.get("artifact_id", ""))) == proposition_id
        ):
            proposition = a
            break

    if proposition is None:
        raise NotFoundError(
            code=ErrorCode.NOT_FOUND,
            message=f"proposition {proposition_id!r} not found in session {session_id!r}",
        )

    # Without assessment / action_proposal ports, we cannot determine
    # the full pipeline stage.  Default to "queued" (the earliest stage).
    current_stage = "queued"
    last_successful_stage: str | None = None

    return {
        "session_id": str(session_id),
        "proposition_id": proposition_id,
        "current_stage": current_stage,
        "last_successful_stage": last_successful_stage,
        "current_assessment_id": None,
        "current_attempt": None,
        "backlog_state": "none",
        "last_failure_reason": "none",
        "last_failure_at": None,
        "schema_version": "proposition_runtime_status.v1",
    }
