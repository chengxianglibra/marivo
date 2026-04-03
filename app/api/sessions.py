from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, http_error
from app.api.models import (
    ArtifactRef,
    AttributeRequest,
    CompareRequest,
    CorrelateRequest,
    DecomposeRequest,
    DetectRequest,
    DiagnoseRequest,
    EvidenceGraphResponse,
    ForecastRequest,
    IntentTestRequest,
    ObservationRef,
    ObserveRequest,
    SessionCreateRequest,
    SessionDebugResponse,
    SessionStateQueryRequest,
    ValidateRequest,
)
from app.reflection.context import build_reflection_context

router = APIRouter()


# ── Session lifecycle ─────────────────────────────────────────────────────────


@router.post("/sessions")
def create_session(payload: SessionCreateRequest, request: Request) -> dict[str, object]:
    return get_services(request).service.create_session(
        goal=payload.goal,
        constraints=payload.constraints,
        budget=payload.budget,
        policy=payload.policy,
        raw_filter=payload.raw_filter,
    )


@router.get("/sessions")
def list_sessions(
    request: Request, status: str | None = Query(default=None)
) -> list[dict[str, object]]:
    return get_services(request).service.list_sessions(status=status)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).service.get_session(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/runtime-status")
def get_session_runtime_status(session_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).service.get_session_runtime_status(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# ── Canonical state surface (Phase 5b) ───────────────────────────────────────
# These endpoints expose externally visible canonical state only.
# Runtime scheduling truth must not be read from these endpoints.
# Registered before parameterised intent routes to avoid routing ambiguity.


@router.get("/sessions/{session_id}/state")
def get_session_state(
    session_id: str,
    request: Request,
    metric: str | None = Query(default=None),
    entity: str | None = Query(default=None),
    proposition_type: list[str] | None = Query(default=None),
    origin_kind: list[str] | None = Query(default=None),
    assessment_presence: str | None = Query(default=None),
    assessment_status: list[str] | None = Query(default=None),
    has_blocking_gaps: bool | None = Query(default=None),
    limit: int | None = Query(default=None),
    page_token: str | None = Query(default=None),
) -> dict[str, object]:
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
        return get_services(request).service.get_session_state(session_id, query)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/state/query")
def query_session_state(
    session_id: str,
    payload: SessionStateQueryRequest,
    request: Request,
) -> dict[str, object]:
    """Return the canonical SessionStateView with a structured query body.

    Use this endpoint when ``slice`` filtering or multi-axis query composition
    is required.  Supports all ``SessionStateQuery`` fields.
    """
    try:
        query = {k: v for k, v in payload.model_dump().items() if v is not None}
        return get_services(request).service.query_session_state(session_id, query)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/artifacts/{artifact_id}/runtime-status")
def get_artifact_runtime_status(
    session_id: str,
    artifact_id: str,
    request: Request,
) -> dict[str, object]:
    """Return the operator-facing runtime status for a single artifact.

    Explains whether the artifact has been extracted and handed off to
    proposition seeding.  This is runtime truth only; do not use it as a
    canonical evidence read surface.
    """
    try:
        return get_services(request).service.get_artifact_runtime_status(session_id, artifact_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/reflection-context")
def get_reflection_context(
    session_id: str,
    request: Request,
    plan_id: str | None = Query(default=None),
) -> dict[str, object]:
    services = get_services(request)
    if not services.reflection_enabled:
        raise HTTPException(status_code=404, detail="Reflection context is disabled")
    try:
        return build_reflection_context(services.metadata_store, session_id, plan_id=plan_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# ── Intent API ────────────────────────────────────────────────────────────────
# Path acts as the intent discriminator. No step_type field in request bodies.
# Literal path routes must be registered before parameterised routes.


def _assert_same_session(session_id: str, *refs: ObservationRef | ArtifactRef) -> None:
    """Reject any ref whose session_id does not match the current session."""
    for ref in refs:
        if ref.session_id is not None and ref.session_id != session_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cross-session reference not allowed: "
                    f"ref.session_id={ref.session_id!r} != session_id={session_id!r}"
                ),
            )


def _run_intent(
    session_id: str, intent_type: str, params: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Dispatch an intent to SemanticLayerService.run_intent with uniform error handling."""
    try:
        return get_services(request).service.run_intent(session_id, intent_type, params)
    except KeyError as error:
        raise http_error(error) from error
    except NotImplementedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Intent execution error: {error}") from error


@router.post("/sessions/{session_id}/intents/observe")
def intent_observe(
    session_id: str,
    payload: ObserveRequest,
    request: Request,
) -> dict[str, object]:
    return _run_intent(session_id, "observe", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/compare")
def intent_compare(
    session_id: str,
    payload: CompareRequest,
    request: Request,
) -> dict[str, object]:
    _assert_same_session(session_id, payload.left_ref, payload.right_ref)
    return _run_intent(session_id, "compare", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/decompose")
def intent_decompose(
    session_id: str,
    payload: DecomposeRequest,
    request: Request,
) -> dict[str, object]:
    _assert_same_session(session_id, payload.compare_ref)
    return _run_intent(session_id, "decompose", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/correlate")
def intent_correlate(
    session_id: str,
    payload: CorrelateRequest,
    request: Request,
) -> dict[str, object]:
    _assert_same_session(session_id, payload.left_ref, payload.right_ref)
    return _run_intent(session_id, "correlate", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/detect")
def intent_detect(
    session_id: str,
    payload: DetectRequest,
    request: Request,
) -> dict[str, object]:
    return _run_intent(session_id, "detect", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/test")
def intent_test(
    session_id: str,
    payload: IntentTestRequest,
    request: Request,
) -> dict[str, object]:
    _assert_same_session(session_id, payload.left_ref, payload.right_ref)
    return _run_intent(session_id, "test", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/forecast")
def intent_forecast(
    session_id: str,
    payload: ForecastRequest,
    request: Request,
) -> dict[str, object]:
    _assert_same_session(session_id, payload.source_ref)
    return _run_intent(session_id, "forecast", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/attribute")
def intent_attribute(
    session_id: str,
    payload: AttributeRequest,
    request: Request,
) -> dict[str, object]:
    return _run_intent(session_id, "attribute", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/diagnose")
def intent_diagnose(
    session_id: str,
    payload: DiagnoseRequest,
    request: Request,
) -> dict[str, object]:
    return _run_intent(session_id, "diagnose", payload.model_dump(exclude_none=True), request)


@router.post("/sessions/{session_id}/intents/validate")
def intent_validate(
    session_id: str,
    payload: ValidateRequest,
    request: Request,
) -> dict[str, object]:
    return _run_intent(session_id, "validate", payload.model_dump(exclude_none=True), request)


# ── Evidence / debug read surfaces ───────────────────────────────────────────


@router.get("/sessions/{session_id}/evidence", response_model=EvidenceGraphResponse)
def evidence_graph(
    session_id: str,
    request: Request,
    claims_only: str | None = Query(default=None),
    edge_types: list[str] | None = Query(default=None),
    include_debug: bool = Query(default=False),
) -> dict[str, object]:
    try:
        return get_services(request).service.get_evidence_graph(
            session_id,
            claims_only=claims_only,
            edge_types=edge_types,
            include_debug=include_debug,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/debug", response_model=SessionDebugResponse)
def session_debug(session_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).service.get_session_debug(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
