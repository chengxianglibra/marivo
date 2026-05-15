"""Semantic Model V2 API: OSI document workflow routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from marivo.adapters.server.semantic_service_adapter import SemanticServiceAdapter
from marivo.contracts.generated import OSI_MARIVO_SPEC_VERSION as OSI_SPEC_VERSION
from marivo.contracts.generated import OSIDocument
from marivo.identity import require_user, resolve_user
from marivo.runtime.semantic.import_export import ImportOsiDocumentReport

router = APIRouter(prefix="/semantic-models", tags=["semantic-models"])

_T = TypeVar("_T")


class SemanticValidationIssueContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str | None = None
    datasource_id: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None
    column: str | None = None
    field: str | None = None
    name: str | None = None
    source: str | None = None


class SemanticValidationIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    json_pointer: str
    severity: str = "error"
    hint: str | None = None
    context: SemanticValidationIssueContextResponse = Field(
        default_factory=SemanticValidationIssueContextResponse
    )


class SemanticValidationResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    schema_version: str = OSI_SPEC_VERSION
    errors: list[SemanticValidationIssueResponse] = Field(default_factory=list)
    warnings: list[SemanticValidationIssueResponse] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class SemanticImportResponse(SemanticValidationResultResponse):
    import_report: ImportOsiDocumentReport | None = None


def _get_service(request: Request) -> SemanticServiceAdapter:
    return cast("SemanticServiceAdapter", request.app.state.semantic_v2_service)


def _dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_none=True)


def _osi_model_wrap(model_data: dict[str, Any]) -> OSIDocument:
    return OSIDocument.model_validate({"version": OSI_SPEC_VERSION, "semantic_model": [model_data]})


def _osi_list_wrap(models: list[dict[str, Any]]) -> OSIDocument:
    return OSIDocument.model_validate({"version": OSI_SPEC_VERSION, "semantic_model": models})


def _run(fn: Callable[[], _T]) -> _T:  # noqa: UP047
    try:
        return fn()
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _resolve_owner_user() -> str:
    try:
        return require_user()
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=OSIDocument)
def list_semantic_models(request: Request) -> OSIDocument:
    """List semantic models as an OSI document envelope."""
    svc = _get_service(request)
    results = svc.list_semantic_models(requesting_user=resolve_user())
    return _osi_list_wrap(results)


@router.post("/validate", response_model=SemanticValidationResultResponse)
async def validate_osi_semantic_models(request: Request) -> SemanticValidationResultResponse:
    """Validate an OSI semantic document without writing it."""
    svc = _get_service(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")
    result = _run(lambda: svc.validate_osi_semantic_models(payload))
    return SemanticValidationResultResponse.model_validate(result)


@router.post("/import", response_model=SemanticImportResponse)
async def import_osi_semantic_models(request: Request) -> SemanticImportResponse:
    """Validate and import an OSI document into the caller's private working copy."""
    svc = _get_service(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")
    result = _run(lambda: svc.import_osi_semantic_models(payload))
    return SemanticImportResponse.model_validate(result)


@router.get("/export", response_model=OSIDocument)
def export_osi_semantic_models(
    request: Request,
    semantic_model_name: str | None = None,
) -> OSIDocument:
    """Export the caller's private working copy as an OSI document."""
    svc = _get_service(request)
    result = _run(lambda: svc.export_osi_semantic_models(semantic_model_name))
    return OSIDocument.model_validate(result)


@router.get("/{model}", response_model=OSIDocument)
def get_semantic_model(model: str, request: Request) -> OSIDocument:
    """Get a semantic model as an OSI document."""
    svc = _get_service(request)
    result = _run(lambda: svc.get_semantic_model(model, requesting_user=resolve_user()))
    return _osi_model_wrap(result)


@router.delete("/{model}", status_code=204)
def delete_semantic_model(model: str, request: Request) -> None:
    """Delete the caller's private semantic model working copy."""
    svc = _get_service(request)
    owner_user = _resolve_owner_user()
    _run(lambda: svc.delete_semantic_model(model, owner_user=owner_user))
