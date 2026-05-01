"""Semantic Model V2 API — OSI-aligned semantic layer routes.

All endpoints that return OSI-conformant data wrap results in
{"version": OSI_SPEC_VERSION, "semantic_model": [...]}.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from fastapi import APIRouter, Body, HTTPException, Request

from app.api.models.osi import OSI_SPEC_VERSION
from app.semantic_service_v2.service import SemanticModelV2Service
from app.semantic_service_v2.validation import SemanticValidationError

router = APIRouter(prefix="/semantic-models", tags=["semantic-models"])

_T = TypeVar("_T")


def _get_service(request: Request) -> SemanticModelV2Service:
    return cast("SemanticModelV2Service", request.app.state.semantic_v2_service)


def _osi_model_wrap(model_data: dict[str, Any]) -> dict[str, Any]:
    """Wrap a single semantic model dict in the OSI document envelope."""
    return {"version": OSI_SPEC_VERSION, "semantic_model": [model_data]}


def _osi_list_wrap(models: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a list of semantic model dicts in the OSI document envelope."""
    return {"version": OSI_SPEC_VERSION, "semantic_model": models}


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


# ---------------------------------------------------------------------------
# SemanticModel CRUD
# ---------------------------------------------------------------------------


@router.post("")
def create_semantic_model(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Create a semantic model from an OSI document fragment."""
    svc = _get_service(request)
    result = _run(lambda: svc.create_semantic_model(payload))
    return _osi_model_wrap(result)


@router.get("")
def list_semantic_models(request: Request, requesting_user: str | None = None) -> dict[str, Any]:
    """List semantic models (summary)."""
    svc = _get_service(request)
    results = svc.list_semantic_models(requesting_user=requesting_user)
    return _osi_list_wrap(results)


@router.post("/import")
def import_osi_document(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Import an OSI document as the latest public layer."""
    svc = _get_service(request)
    results = _run(lambda: svc.import_osi_document(payload))
    return _osi_list_wrap(results)


@router.get("/{model}")
def get_semantic_model(
    model: str, request: Request, requesting_user: str | None = None
) -> dict[str, Any]:
    """Get a semantic model as an OSI document."""
    svc = _get_service(request)
    result = _run(lambda: svc.get_semantic_model(model, requesting_user=requesting_user))
    return _osi_model_wrap(result)


@router.put("/{model}")
def update_semantic_model(
    model: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Update top-level fields of a semantic model."""
    svc = _get_service(request)
    result = _run(lambda: svc.update_semantic_model(model, payload))
    return _osi_model_wrap(result)


@router.delete("/{model}", status_code=204)
def delete_semantic_model(model: str, request: Request) -> None:
    """Delete a semantic model."""
    svc = _get_service(request)
    _run(lambda: svc.delete_semantic_model(model))


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


@router.post("/{model}/datasets")
def create_dataset(
    model: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Create a dataset within a model."""
    svc = _get_service(request)
    return _run(lambda: svc.create_dataset(model, payload))


@router.get("/{model}/datasets")
def list_datasets(model: str, request: Request) -> list[dict[str, Any]]:
    """List datasets in a model."""
    svc = _get_service(request)
    return svc.list_datasets(model)


@router.get("/{model}/datasets/{name}")
def get_dataset(model: str, name: str, request: Request) -> dict[str, Any]:
    """Get a dataset by name within a model."""
    svc = _get_service(request)
    return _run(lambda: svc.get_dataset(model, name))


@router.put("/{model}/datasets/{name}")
def update_dataset(
    model: str, name: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Update a dataset's top-level fields."""
    svc = _get_service(request)
    return _run(lambda: svc.update_dataset(model, name, payload))


@router.delete("/{model}/datasets/{name}", status_code=204)
def delete_dataset(model: str, name: str, request: Request) -> None:
    """Delete a dataset."""
    svc = _get_service(request)
    _run(lambda: svc.delete_dataset(model, name))


# ---------------------------------------------------------------------------
# Relationship CRUD
# ---------------------------------------------------------------------------


@router.post("/{model}/relationships")
def create_relationship(
    model: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Create a relationship within a model."""
    svc = _get_service(request)
    return _run(lambda: svc.create_relationship(model, payload))


@router.get("/{model}/relationships")
def list_relationships(model: str, request: Request) -> list[dict[str, Any]]:
    """List relationships in a model."""
    svc = _get_service(request)
    return svc.list_relationships(model)


@router.get("/{model}/relationships/{name}")
def get_relationship(model: str, name: str, request: Request) -> dict[str, Any]:
    """Get a relationship by name within a model."""
    svc = _get_service(request)
    return _run(lambda: svc.get_relationship(model, name))


@router.put("/{model}/relationships/{name}")
def update_relationship(
    model: str, name: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Update a relationship's fields."""
    svc = _get_service(request)
    return _run(lambda: svc.update_relationship(model, name, payload))


@router.delete("/{model}/relationships/{name}", status_code=204)
def delete_relationship(model: str, name: str, request: Request) -> None:
    """Delete a relationship."""
    svc = _get_service(request)
    _run(lambda: svc.delete_relationship(model, name))


# ---------------------------------------------------------------------------
# Metric CRUD
# ---------------------------------------------------------------------------


@router.post("/{model}/metrics")
def create_metric(
    model: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Create a metric within a model."""
    svc = _get_service(request)
    return _run(lambda: svc.create_metric(model, payload))


@router.get("/{model}/metrics")
def list_metrics(model: str, request: Request) -> list[dict[str, Any]]:
    """List metrics in a model."""
    svc = _get_service(request)
    return svc.list_metrics(model)


@router.get("/{model}/metrics/{name}")
def get_metric(model: str, name: str, request: Request) -> dict[str, Any]:
    """Get a metric by name within a model."""
    svc = _get_service(request)
    return _run(lambda: svc.get_metric(model, name))


@router.put("/{model}/metrics/{name}")
def update_metric(
    model: str, name: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Update a metric's fields."""
    svc = _get_service(request)
    return _run(lambda: svc.update_metric(model, name, payload))


@router.delete("/{model}/metrics/{name}", status_code=204)
def delete_metric(model: str, name: str, request: Request) -> None:
    """Delete a metric."""
    svc = _get_service(request)
    _run(lambda: svc.delete_metric(model, name))


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


@router.get("/{model}/readiness")
def get_readiness(model: str, request: Request) -> dict[str, Any]:
    """Get readiness status for a semantic model."""
    svc = _get_service(request)
    return _run(lambda: svc.get_readiness(model))
