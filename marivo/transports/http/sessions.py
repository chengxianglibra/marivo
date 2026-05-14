from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from marivo.contracts.envelope import ExecutionEnvelope
from marivo.contracts.errors import ExecutionError, ForbiddenError, NotFoundError, ValidationError
from marivo.contracts.generated import aoi
from marivo.contracts.ids import SessionId, UserId
from marivo.contracts.session import SessionState
from marivo.runtime import SemanticRuntimeNotReadyError
from marivo.transports.http.deps import get_services, http_error
from marivo.transports.http.models import (
    ArtifactRuntimeStatusResponse,
    AttributeRequest,
    AttributeResponse,
    DiagnoseRequest,
    DiagnoseResponse,
    PropositionContextView,
    PropositionRuntimeStatusResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionRuntimeStatusResponse,
    SessionStateQueryRequest,
    SessionStateView,
    SessionTerminateRequest,
    SessionTerminateResponse,
)

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _session_state_to_api_dict(result: SessionState) -> dict[str, Any]:
    """Convert a SessionState to the API-facing AnalysisSession-shaped dict."""
    # Map event-sourced status to API status:
    #   "active" -> "open", "terminated" -> "closed"
    api_status = result.status
    if api_status == "active":
        api_status = "open"
    elif api_status == "terminated":
        api_status = "closed"
    return {
        "session_id": str(result.session_id),
        "goal": {"question": result.goal or ""},
        "scope": {
            "constraints": result.constraints or {},
        },
        "owner_user": str(result.owner_user) if result.owner_user else None,
        "lifecycle": {
            "status": api_status,
            "terminal_reason": result.terminal_reason,
            "ended_at": result.ended_at,
        },
        "state_summary": {
            "state_view_ref": {
                "session_id": str(result.session_id),
                "view_type": "session_state_view",
            }
        },
        "created_at": result.created_at,
        "updated_at": result.updated_at,
        "schema_version": "analysis_session.v1",
    }


# ── Session lifecycle ─────────────────────────────────────────────────────────


@router.post(
    "/sessions",
    response_model=SessionCreateResponse,
)
def create_session(payload: SessionCreateRequest, request: Request) -> SessionCreateResponse:
    try:
        from marivo.identity import resolve_user

        current_user = resolve_user()
        actor = UserId(current_user) if current_user else None
        result = get_services(request).runtime.create_session(
            goal=payload.goal,
            actor=actor,
            budget=payload.budget.model_dump(exclude_none=True),
        )
        # Runtime.create_session returns SessionState; build the API response
        # from it.  The full AnalysisSession shape is constructed here until
        # the API response models are simplified in a later phase.
        if isinstance(result, dict):
            return SessionCreateResponse.model_validate(result)
        # result is a SessionState
        if isinstance(result, SessionState):
            return SessionCreateResponse.model_validate(_session_state_to_api_dict(result))
        return SessionCreateResponse.model_validate(result)
    except ValueError as error:
        if "user_required" in str(error):
            raise HTTPException(status_code=401, detail=str(error)) from error
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(
    "/sessions",
    response_model=SessionListResponse,
)
def list_sessions(
    request: Request,
    status: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    page_token: str | None = Query(default=None),
) -> SessionListResponse:
    try:
        result = get_services(request).runtime.list_sessions(
            status=status,
            session_id=session_id,
            limit=limit,
            page_token=page_token,
        )
        return SessionListResponse.model_validate(result)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetailResponse,
)
def get_session(session_id: str, request: Request) -> SessionDetailResponse:
    try:
        result = get_services(request).runtime.get_session(SessionId(session_id))
        if isinstance(result, dict):
            return SessionDetailResponse.model_validate(result)
        # result is a SessionState — build the API response shape
        return SessionDetailResponse.model_validate(_session_state_to_api_dict(result))
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}/runtime-status",
    response_model=SessionRuntimeStatusResponse,
)
def get_session_runtime_status(session_id: str, request: Request) -> SessionRuntimeStatusResponse:
    try:
        result = get_services(request).runtime.get_session_runtime_status(SessionId(session_id))
        # Strip extra fields not in the API response model
        api_result = {
            "session_id": result["session_id"],
            "overall_status": result["overall_status"],
            "last_successful_stage": result.get("last_successful_stage"),
            "blocked_reason": result["blocked_reason"],
            "backlog_summary": result["backlog_summary"],
            "updated_at": result["updated_at"],
            "schema_version": result["schema_version"],
        }
        return SessionRuntimeStatusResponse.model_validate(api_result)
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/terminate",
    response_model=SessionTerminateResponse,
)
def terminate_session(
    session_id: str,
    payload: SessionTerminateRequest,
    request: Request,
) -> SessionTerminateResponse:
    """Terminate a session, preventing further intent write operations."""
    try:
        from marivo.identity import require_user

        actor = UserId(require_user())
        get_services(request).runtime.terminate_session(
            SessionId(session_id),
            actor=actor,
            terminal_reason=payload.terminal_reason,
        )
        # Fetch the updated session to build the full API response
        result = get_services(request).runtime.get_session(SessionId(session_id))
        if isinstance(result, dict):
            return SessionTerminateResponse.model_validate(result)
        return SessionTerminateResponse.model_validate(_session_state_to_api_dict(result))
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ForbiddenError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# ── Canonical state surface (Phase 5b) ───────────────────────────────────────
# These endpoints expose externally visible canonical state only.
# Runtime scheduling truth must not be read from these endpoints.
# Registered before parameterised intent routes to avoid routing ambiguity.


@router.get(
    "/sessions/{session_id}/state",
    response_model=SessionStateView,
)
def get_session_state(
    session_id: str,
    request: Request,
    metric: Annotated[str | None, Query()] = None,
    entity: Annotated[str | None, Query()] = None,
    proposition_type: Annotated[list[str] | None, Query()] = None,
    origin_kind: Annotated[list[str] | None, Query()] = None,
    assessment_presence: Annotated[str | None, Query()] = None,
    assessment_status: Annotated[list[str] | None, Query()] = None,
    has_blocking_gaps: Annotated[bool | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    page_token: Annotated[str | None, Query()] = None,
) -> SessionStateView:
    """Return the canonical SessionStateView for a session.

    ``slice`` is intentionally not supported on this endpoint.
    Use ``POST /sessions/{session_id}/state/query`` when ``slice`` filtering
    is required.
    """
    if "slice" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="'slice' is not supported on GET /state. Use POST /state/query instead.",
        )
    try:
        query: dict[str, Any] = {}
        if metric is not None:
            query["metric"] = metric
        if entity is not None:
            query["entity"] = entity
        if proposition_type:
            query["proposition_types"] = proposition_type
        if origin_kind:
            query["origin_kinds"] = origin_kind
        if assessment_presence is not None:
            if assessment_presence not in ("assessed", "unassessed"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid assessment_presence value: {assessment_presence!r}. "
                    "Must be 'assessed' or 'unassessed'.",
                )
            query["assessment_presence"] = assessment_presence
        if assessment_status:
            query["assessment_statuses"] = assessment_status
        if has_blocking_gaps is not None:
            query["has_blocking_gaps"] = has_blocking_gaps
        if limit is not None:
            query["limit"] = limit
        if page_token is not None:
            query["page_token"] = page_token
        # When there are query kwargs, use runtime.get_session_state which
        # falls through to svc for structured queries.  Without kwargs, use
        # the service directly to get the full SessionStateView shape -- the
        # runtime method returns a bare SessionState that lacks view fields.
        if query:
            result = get_services(request).runtime.get_session_state(SessionId(session_id), **query)
        else:
            result = get_services(request).runtime.query_session_state(session_id, query)
        if isinstance(result, dict):
            return SessionStateView.model_validate(result)
        # result is a SessionState — build minimal view
        if isinstance(result, SessionState):
            return SessionStateView.model_validate(
                {
                    "session_id": str(result.session_id),
                    "active_propositions": [],
                    "backing_findings": [],
                    "blocking_gaps": [],
                    "artifact_refs": [],
                    "focus_subjects": [],
                    "schema_version": "analysis_session.v1",
                }
            )
        return SessionStateView.model_validate(result)
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/state/query",
    response_model=SessionStateView,
)
def query_session_state(
    session_id: str,
    payload: SessionStateQueryRequest,
    request: Request,
) -> SessionStateView:
    """Return the canonical SessionStateView with a structured query body.

    Use this endpoint when ``slice`` filtering or multi-axis query composition
    is required.  Supports all ``SessionStateQuery`` fields.
    """
    try:
        query = payload.model_dump(exclude_none=True)
        return SessionStateView.model_validate(
            get_services(request).runtime.query_session_state(session_id, query)
        )
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}/artifacts/{artifact_id}/runtime-status",
    response_model=ArtifactRuntimeStatusResponse,
)
def get_artifact_runtime_status(
    session_id: str,
    artifact_id: str,
    request: Request,
) -> ArtifactRuntimeStatusResponse:
    """Return the operator-facing runtime status for a single artifact.

    Explains whether the artifact has been extracted and handed off to
    proposition seeding.  This is runtime truth only; do not use it as a
    canonical evidence read surface.
    """
    try:
        return ArtifactRuntimeStatusResponse.model_validate(
            get_services(request).runtime.get_artifact_runtime_status(
                SessionId(session_id), artifact_id
            )
        )
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}/propositions/{proposition_id}/context",
    response_model=PropositionContextView,
)
def get_proposition_context(
    session_id: str,
    proposition_id: str,
    request: Request,
) -> PropositionContextView:
    """Return PropositionContextView — canonical proposition-level minimal closure (Phase 5c).

    Exposes the externally visible canonical context for a single proposition:
    proposition object, creation-time seed hydration, latest assessment and its
    live evidence closure (findings, gaps, inference records, artifact handles).

    This is the canonical agent read path for single-proposition context.
    Runtime scheduling truth must not be read from this endpoint.
    """
    try:
        return PropositionContextView.model_validate(
            get_services(request).runtime.get_proposition_context(session_id, proposition_id)
        )
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}/propositions/{proposition_id}/runtime-status",
    response_model=PropositionRuntimeStatusResponse,
)
def get_proposition_runtime_status(
    session_id: str,
    proposition_id: str,
    request: Request,
) -> PropositionRuntimeStatusResponse:
    """Return operator-facing runtime status for a single proposition (Phase 5c).

    Explains which pipeline stage the proposition is currently at and, in future
    versions, why it may be blocked, failed, or waiting for publish.  This is
    runtime truth only; do not use it as a canonical evidence read surface.
    """
    try:
        return PropositionRuntimeStatusResponse.model_validate(
            get_services(request).runtime.get_proposition_runtime_status(
                SessionId(session_id), proposition_id
            )
        )
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# ── Intent API ────────────────────────────────────────────────────────────────
# Path acts as the intent discriminator. No step_type field in request bodies.
# Literal path routes must be registered before parameterised routes.


def _run_intent(session_id: str, intent_type: str, params: Any, request: Request) -> dict[str, Any]:
    """Dispatch an intent through MarivoRuntime with uniform error handling."""
    try:
        runtime = get_services(request).runtime
        method: Callable[[str, Any], dict[str, Any]] | None = getattr(runtime, intent_type, None)
        if method is None:
            raise ValueError(f"Unknown intent type: '{intent_type}'")
        return method(session_id, params)
    except KeyError as error:
        raise http_error(error) from error
    except NotImplementedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    except SemanticRuntimeNotReadyError as error:
        raise HTTPException(status_code=409, detail=error.detail_payload()) from error
    except ExecutionError as error:
        if error.category == "readiness":
            readiness_error = error.detail.get("readiness_error")
            if isinstance(readiness_error, dict):
                raise HTTPException(status_code=409, detail=readiness_error) from error
        if error.category == "compatibility":
            compatibility_error = error.detail.get("compatibility_error")
            if isinstance(compatibility_error, dict):
                raise HTTPException(status_code=409, detail=compatibility_error) from error
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Intent execution error: {error}") from error


@router.post("/sessions/{session_id}/intents/observe", response_model=ExecutionEnvelope)
def intent_observe(
    session_id: str,
    payload: aoi.Observe1 | aoi.Observe2 | aoi.Observe3 | aoi.Observe4,
    request: Request,
) -> ExecutionEnvelope:
    return ExecutionEnvelope.model_validate(_run_intent(session_id, "observe", payload, request))


@router.post("/sessions/{session_id}/intents/compare", response_model=ExecutionEnvelope)
def intent_compare(
    session_id: str,
    payload: aoi.Compare,
    request: Request,
) -> ExecutionEnvelope:
    return ExecutionEnvelope.model_validate(_run_intent(session_id, "compare", payload, request))


@router.post("/sessions/{session_id}/intents/decompose", response_model=ExecutionEnvelope)
def intent_decompose(
    session_id: str,
    payload: aoi.Decompose,
    request: Request,
) -> ExecutionEnvelope:
    return ExecutionEnvelope.model_validate(_run_intent(session_id, "decompose", payload, request))


@router.post("/sessions/{session_id}/intents/correlate", response_model=ExecutionEnvelope)
def intent_correlate(
    session_id: str,
    payload: aoi.Correlate,
    request: Request,
) -> ExecutionEnvelope:
    return ExecutionEnvelope.model_validate(_run_intent(session_id, "correlate", payload, request))


@router.post("/sessions/{session_id}/intents/detect", response_model=ExecutionEnvelope)
def intent_detect(
    session_id: str,
    payload: aoi.Detect,
    request: Request,
) -> ExecutionEnvelope:
    return ExecutionEnvelope.model_validate(_run_intent(session_id, "detect", payload, request))


@router.post("/sessions/{session_id}/intents/forecast", response_model=ExecutionEnvelope)
def intent_forecast(
    session_id: str,
    payload: aoi.Forecast,
    request: Request,
) -> ExecutionEnvelope:
    return ExecutionEnvelope.model_validate(_run_intent(session_id, "forecast", payload, request))


@router.post("/sessions/{session_id}/intents/attribute", response_model=AttributeResponse)
def intent_attribute(
    session_id: str,
    payload: AttributeRequest,
    request: Request,
) -> AttributeResponse:
    return AttributeResponse.model_validate(
        _run_intent(session_id, "attribute", payload.model_dump(exclude_none=True), request)
    )


@router.post("/sessions/{session_id}/intents/diagnose", response_model=DiagnoseResponse)
def intent_diagnose(
    session_id: str,
    payload: DiagnoseRequest,
    request: Request,
) -> DiagnoseResponse:
    return DiagnoseResponse.model_validate(
        _run_intent(session_id, "diagnose", payload.model_dump(exclude_none=True), request)
    )
