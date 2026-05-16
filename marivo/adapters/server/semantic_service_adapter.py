"""HTTP adapter for the semantic document service surface."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import HTTPException

from marivo.adapters.metadata import MetadataStore
from marivo.contracts.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from marivo.runtime.semantic.semantic_service import SemanticModelV2Service

_T = TypeVar("_T")


def _translate(fn: Callable[[], _T]) -> _T:  # noqa: UP047
    """Call *fn* and convert domain errors to HTTPException."""
    try:
        return fn()
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ForbiddenError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class SemanticServiceAdapter:
    """Thin HTTP-error adapter around :class:`SemanticModelV2Service`."""

    def __init__(self, store: MetadataStore, datasource_service: Any = None) -> None:
        self._service = SemanticModelV2Service(store, datasource_service=datasource_service)

    @property
    def service(self) -> SemanticModelV2Service:
        return self._service

    @property
    def store(self) -> MetadataStore:
        return self._service.store

    def list_semantic_models(self, requesting_user: str | None = None) -> list[dict[str, Any]]:
        return _translate(
            lambda: self._service.list_semantic_models(requesting_user=requesting_user)
        )

    def get_semantic_model(self, name: str, requesting_user: str | None = None) -> dict[str, Any]:
        return _translate(
            lambda: self._service.get_semantic_model(name, requesting_user=requesting_user)
        )

    def validate_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        return _translate(lambda: self._service.validate_osi_semantic_models(doc_data))

    def import_osi_semantic_models(self, doc_data: dict[str, Any]) -> None:
        _translate(lambda: self._service.import_osi_semantic_models(doc_data))

    def export_osi_semantic_models(self, semantic_model_name: str | None = None) -> dict[str, Any]:
        return _translate(lambda: self._service.export_osi_semantic_models(semantic_model_name))

    def delete_semantic_model(self, name: str, owner_user: str | None = None) -> None:
        _translate(lambda: self._service.delete_semantic_model(name, owner_user=owner_user))
