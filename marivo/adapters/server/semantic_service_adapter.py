"""HTTP adapter for SemanticModelV2Service.

Translates domain errors raised by the runtime service into
FastAPI ``HTTPException`` responses.
"""

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


class SemanticServiceAdapter:
    """Thin HTTP-error adapter around :class:`SemanticModelV2Service`.

    Every public method delegates to the underlying service and catches
    ``DomainError`` subclasses, re-raising them as ``HTTPException``.
    """

    def __init__(self, store: MetadataStore, datasource_service: Any = None) -> None:
        self._service = SemanticModelV2Service(store, datasource_service=datasource_service)

    # Expose the underlying service for callers that need direct access
    # (e.g. SqlModelStoreAdapter compatibility).
    @property
    def service(self) -> SemanticModelV2Service:
        return self._service

    # Forward attribute access to the underlying service so that existing
    # callers that access .store or .datasource_service keep working.
    @property
    def store(self) -> MetadataStore:
        return self._service.store

    # ------------------------------------------------------------------
    # SemanticModel CRUD
    # ------------------------------------------------------------------

    def create_semantic_model(self, model_data: dict[str, Any]) -> dict[str, Any]:
        return _translate(lambda: self._service.create_semantic_model(model_data))

    def get_semantic_model(self, name: str, requesting_user: str | None = None) -> dict[str, Any]:
        return _translate(
            lambda: self._service.get_semantic_model(name, requesting_user=requesting_user)
        )

    def list_semantic_models(self, requesting_user: str | None = None) -> list[dict[str, Any]]:
        return _translate(
            lambda: self._service.list_semantic_models(requesting_user=requesting_user)
        )

    def update_semantic_model(
        self, name: str, updates: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.update_semantic_model(name, updates, owner_user=owner_user)
        )

    def delete_semantic_model(self, name: str, owner_user: str | None = None) -> None:
        _translate(lambda: self._service.delete_semantic_model(name, owner_user=owner_user))

    # ------------------------------------------------------------------
    # Dataset CRUD
    # ------------------------------------------------------------------

    def create_dataset(
        self, model_name: str, ds_data: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.create_dataset(model_name, ds_data, owner_user=owner_user)
        )

    def get_dataset(
        self, model_name: str, dataset_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.get_dataset(
                model_name, dataset_name, requesting_user=requesting_user
            )
        )

    def list_datasets(
        self, model_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        return _translate(
            lambda: self._service.list_datasets(model_name, requesting_user=requesting_user)
        )

    def update_dataset(
        self,
        model_name: str,
        dataset_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.update_dataset(
                model_name, dataset_name, updates, owner_user=owner_user
            )
        )

    def delete_dataset(
        self, model_name: str, dataset_name: str, owner_user: str | None = None
    ) -> None:
        _translate(
            lambda: self._service.delete_dataset(model_name, dataset_name, owner_user=owner_user)
        )

    # ------------------------------------------------------------------
    # Relationship CRUD
    # ------------------------------------------------------------------

    def create_relationship(
        self, model_name: str, rel_data: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.create_relationship(model_name, rel_data, owner_user=owner_user)
        )

    def get_relationship(
        self, model_name: str, rel_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.get_relationship(
                model_name, rel_name, requesting_user=requesting_user
            )
        )

    def list_relationships(
        self, model_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        return _translate(
            lambda: self._service.list_relationships(model_name, requesting_user=requesting_user)
        )

    def update_relationship(
        self,
        model_name: str,
        rel_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.update_relationship(
                model_name, rel_name, updates, owner_user=owner_user
            )
        )

    def delete_relationship(
        self, model_name: str, rel_name: str, owner_user: str | None = None
    ) -> None:
        _translate(
            lambda: self._service.delete_relationship(model_name, rel_name, owner_user=owner_user)
        )

    # ------------------------------------------------------------------
    # Metric CRUD
    # ------------------------------------------------------------------

    def create_metric(
        self, model_name: str, metric_data: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.create_metric(model_name, metric_data, owner_user=owner_user)
        )

    def get_metric(
        self, model_name: str, metric_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.get_metric(
                model_name, metric_name, requesting_user=requesting_user
            )
        )

    def list_metrics(
        self, model_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        return _translate(
            lambda: self._service.list_metrics(model_name, requesting_user=requesting_user)
        )

    def update_metric(
        self,
        model_name: str,
        metric_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.update_metric(
                model_name, metric_name, updates, owner_user=owner_user
            )
        )

    def delete_metric(
        self, model_name: str, metric_name: str, owner_user: str | None = None
    ) -> None:
        _translate(
            lambda: self._service.delete_metric(model_name, metric_name, owner_user=owner_user)
        )

    # ------------------------------------------------------------------
    # Import & Readiness
    # ------------------------------------------------------------------

    def import_osi_document(self, doc_data: dict[str, Any]) -> list[dict[str, Any]]:
        return _translate(lambda: self._service.import_osi_document(doc_data))

    def get_readiness(self, model_name: str, requesting_user: str | None = None) -> dict[str, Any]:
        return _translate(
            lambda: self._service.get_readiness(model_name, requesting_user=requesting_user)
        )
