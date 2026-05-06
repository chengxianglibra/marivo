"""Semantic Model V2 API — OSI-aligned semantic layer routes.

All endpoints that return OSI-conformant data wrap results in
{"version": OSI_SPEC_VERSION, "semantic_model": [...]}.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.models.json_contract import ScalarMap
from app.api.models.osi import (
    OSI_SPEC_VERSION,
    Dataset,
    Metric,
    OSIDocument,
    Relationship,
    SemanticModel,
)
from app.semantic_service_v2.service import SemanticModelV2Service
from app.semantic_service_v2.validation import SemanticValidationError

router = APIRouter(prefix="/semantic-models", tags=["semantic-models"])

_T = TypeVar("_T")


class SemanticModelReadinessResponse(BaseModel):
    """Readiness status for a semantic model."""

    status: str
    semantic_version_id: str | int | None = None
    evaluated_semantic_version_id: str | int | None = None
    blockers: list[ScalarMap] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SemanticModelUpdateRequest(BaseModel):
    """Partial update for a semantic model's top-level fields."""

    description: str | None = None

    model_config = ConfigDict(extra="forbid")


class DatasetUpdateRequest(BaseModel):
    """Partial update for a dataset's top-level fields."""

    description: str | None = None

    model_config = ConfigDict(extra="forbid")


def _get_service(request: Request) -> SemanticModelV2Service:
    return cast("SemanticModelV2Service", request.app.state.semantic_v2_service)


def _dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_none=True)


def _osi_model_wrap(model_data: dict[str, Any]) -> OSIDocument:
    """Wrap a single semantic model dict in the OSI document envelope."""
    return OSIDocument.model_validate({"version": OSI_SPEC_VERSION, "semantic_model": [model_data]})


def _osi_list_wrap(models: list[dict[str, Any]]) -> OSIDocument:
    """Wrap a list of semantic model dicts in the OSI document envelope."""
    return OSIDocument.model_validate({"version": OSI_SPEC_VERSION, "semantic_model": models})


def _run(fn: Callable[[], _T]) -> _T:  # noqa: UP047
    """Execute a service call, converting validation errors to HTTP 422."""
    try:
        return fn()
    except HTTPException:
        raise
    except SemanticValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _resolve_requesting_user(requesting_user: str | None) -> str | None:
    """Resolve requesting user, falling back to MARIVO_DEFAULT_USER env var."""
    if requesting_user is not None:
        return requesting_user
    return os.environ.get("MARIVO_DEFAULT_USER")


# ---------------------------------------------------------------------------
# SemanticModel CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=OSIDocument)
def create_semantic_model(request: Request, payload: SemanticModel) -> OSIDocument:
    """Create a semantic model from an OSI document fragment."""
    svc = _get_service(request)
    result = _run(lambda: svc.create_semantic_model(_dump_model(payload)))
    return _osi_model_wrap(result)


@router.get("", response_model=OSIDocument)
def list_semantic_models(request: Request, requesting_user: str | None = None) -> OSIDocument:
    """List semantic models (summary)."""
    svc = _get_service(request)
    results = svc.list_semantic_models(requesting_user=_resolve_requesting_user(requesting_user))
    return _osi_list_wrap(results)


@router.post("/import", response_model=OSIDocument)
def import_osi_document(request: Request, payload: OSIDocument) -> OSIDocument:
    """Import an OSI document as the latest public layer."""
    svc = _get_service(request)
    results = _run(lambda: svc.import_osi_document(_dump_model(payload)))
    return _osi_list_wrap(results)


@router.get("/{model}", response_model=OSIDocument)
def get_semantic_model(
    model: str, request: Request, requesting_user: str | None = None
) -> OSIDocument:
    """Get a semantic model as an OSI document."""
    svc = _get_service(request)
    result = _run(
        lambda: svc.get_semantic_model(
            model, requesting_user=_resolve_requesting_user(requesting_user)
        )
    )
    return _osi_model_wrap(result)


@router.put("/{model}", response_model=OSIDocument)
def update_semantic_model(
    model: str,
    request: Request,
    payload: SemanticModelUpdateRequest,
    requesting_user: str | None = None,
) -> OSIDocument:
    """Update top-level fields of a semantic model."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    result = _run(lambda: svc.update_semantic_model(model, _dump_model(payload), owner_user=owner))
    return _osi_model_wrap(result)


@router.delete("/{model}", status_code=204)
def delete_semantic_model(
    model: str,
    request: Request,
    requesting_user: str | None = None,
) -> None:
    """Delete a semantic model."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    _run(lambda: svc.delete_semantic_model(model, owner_user=owner))


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


@router.post("/{model}/datasets", response_model=Dataset)
def create_dataset(
    model: str,
    request: Request,
    payload: Dataset,
    requesting_user: str | None = None,
) -> Dataset:
    """Create a dataset within a model."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    return Dataset.model_validate(
        _run(lambda: svc.create_dataset(model, _dump_model(payload), owner_user=owner))
    )


@router.get("/{model}/datasets", response_model=list[Dataset])
def list_datasets(
    model: str, request: Request, requesting_user: str | None = None
) -> list[Dataset]:
    """List datasets in a model."""
    svc = _get_service(request)
    return [
        Dataset.model_validate(item)
        for item in svc.list_datasets(
            model, requesting_user=_resolve_requesting_user(requesting_user)
        )
    ]


@router.get("/{model}/datasets/{name}", response_model=Dataset)
def get_dataset(
    model: str, name: str, request: Request, requesting_user: str | None = None
) -> Dataset:
    """Get a dataset by name within a model."""
    svc = _get_service(request)
    return Dataset.model_validate(
        _run(
            lambda: svc.get_dataset(
                model, name, requesting_user=_resolve_requesting_user(requesting_user)
            )
        )
    )


@router.put("/{model}/datasets/{name}", response_model=Dataset)
def update_dataset(
    model: str,
    name: str,
    request: Request,
    payload: DatasetUpdateRequest,
    requesting_user: str | None = None,
) -> Dataset:
    """Update a dataset's top-level fields."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    return Dataset.model_validate(
        _run(lambda: svc.update_dataset(model, name, _dump_model(payload), owner_user=owner))
    )


@router.delete("/{model}/datasets/{name}", status_code=204)
def delete_dataset(
    model: str,
    name: str,
    request: Request,
    requesting_user: str | None = None,
) -> None:
    """Delete a dataset."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    _run(lambda: svc.delete_dataset(model, name, owner_user=owner))


# ---------------------------------------------------------------------------
# Relationship CRUD
# ---------------------------------------------------------------------------


@router.post("/{model}/relationships", response_model=Relationship)
def create_relationship(
    model: str,
    request: Request,
    payload: Relationship,
    requesting_user: str | None = None,
) -> Relationship:
    """Create a relationship within a model."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    return Relationship.model_validate(
        _run(lambda: svc.create_relationship(model, _dump_model(payload), owner_user=owner))
    )


@router.get("/{model}/relationships", response_model=list[Relationship])
def list_relationships(
    model: str, request: Request, requesting_user: str | None = None
) -> list[Relationship]:
    """List relationships in a model."""
    svc = _get_service(request)
    return [
        Relationship.model_validate(item)
        for item in svc.list_relationships(
            model, requesting_user=_resolve_requesting_user(requesting_user)
        )
    ]


@router.get("/{model}/relationships/{name}", response_model=Relationship)
def get_relationship(
    model: str, name: str, request: Request, requesting_user: str | None = None
) -> Relationship:
    """Get a relationship by name within a model."""
    svc = _get_service(request)
    return Relationship.model_validate(
        _run(
            lambda: svc.get_relationship(
                model, name, requesting_user=_resolve_requesting_user(requesting_user)
            )
        )
    )


@router.put("/{model}/relationships/{name}", response_model=Relationship)
def update_relationship(
    model: str,
    name: str,
    request: Request,
    payload: Relationship,
    requesting_user: str | None = None,
) -> Relationship:
    """Update a relationship's fields."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    return Relationship.model_validate(
        _run(lambda: svc.update_relationship(model, name, _dump_model(payload), owner_user=owner))
    )


@router.delete("/{model}/relationships/{name}", status_code=204)
def delete_relationship(
    model: str,
    name: str,
    request: Request,
    requesting_user: str | None = None,
) -> None:
    """Delete a relationship."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    _run(lambda: svc.delete_relationship(model, name, owner_user=owner))


# ---------------------------------------------------------------------------
# Metric CRUD
# ---------------------------------------------------------------------------


@router.post("/{model}/metrics", response_model=Metric)
def create_metric(
    model: str,
    request: Request,
    payload: Metric,
    requesting_user: str | None = None,
) -> Metric:
    """Create a metric within a model."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    return Metric.model_validate(
        _run(lambda: svc.create_metric(model, _dump_model(payload), owner_user=owner))
    )


@router.get("/{model}/metrics", response_model=list[Metric])
def list_metrics(model: str, request: Request, requesting_user: str | None = None) -> list[Metric]:
    """List metrics in a model."""
    svc = _get_service(request)
    return [
        Metric.model_validate(item)
        for item in svc.list_metrics(
            model, requesting_user=_resolve_requesting_user(requesting_user)
        )
    ]


@router.get("/{model}/metrics/{name}", response_model=Metric)
def get_metric(
    model: str, name: str, request: Request, requesting_user: str | None = None
) -> Metric:
    """Get a metric by name within a model."""
    svc = _get_service(request)
    return Metric.model_validate(
        _run(
            lambda: svc.get_metric(
                model, name, requesting_user=_resolve_requesting_user(requesting_user)
            )
        )
    )


@router.put("/{model}/metrics/{name}", response_model=Metric)
def update_metric(
    model: str,
    name: str,
    request: Request,
    payload: Metric,
    requesting_user: str | None = None,
) -> Metric:
    """Update a metric's fields."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    return Metric.model_validate(
        _run(lambda: svc.update_metric(model, name, _dump_model(payload), owner_user=owner))
    )


@router.delete("/{model}/metrics/{name}", status_code=204)
def delete_metric(
    model: str,
    name: str,
    request: Request,
    requesting_user: str | None = None,
) -> None:
    """Delete a metric."""
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    _run(lambda: svc.delete_metric(model, name, owner_user=owner))


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


@router.get("/{model}/readiness", response_model=SemanticModelReadinessResponse)
def get_readiness(
    model: str,
    request: Request,
    requesting_user: str | None = None,
) -> SemanticModelReadinessResponse:
    """Get readiness status for a semantic model."""
    svc = _get_service(request)
    user = _resolve_requesting_user(requesting_user)
    return SemanticModelReadinessResponse.model_validate(
        _run(lambda: svc.get_readiness(model, requesting_user=user))
    )
