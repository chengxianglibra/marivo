"""SemanticModelV2Service document surface for OSI-Marivo semantic models."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from marivo.adapters.metadata import MetadataStore
from marivo.contracts.errors import ErrorCode, ForbiddenError, NotFoundError
from marivo.contracts.errors import ValidationError as DomainValidationError
from marivo.contracts.generated import OSIDocument
from marivo.identity import require_user
from marivo.runtime.semantic.import_export import (
    DatasourceBinder,
    OsiDocumentExporter,
    OsiSemanticDocumentValidator,
    SemanticImportExecutor,
    SemanticImportPlanner,
)
from marivo.runtime.semantic.osi_storage import (
    _storage_to_dataset,
    _storage_to_metric,
    _storage_to_relationship,
    storage_to_model,
)


class SemanticModelV2Service:
    """Document-first service for OSI-aligned semantic models."""

    def __init__(self, store: MetadataStore, datasource_service: Any = None) -> None:
        self.store = store
        self.datasource_service = datasource_service

    def _get_model_row_by_name(
        self, name: str, requesting_user: str | None = None
    ) -> dict[str, Any] | None:
        """Resolve a visible model by name, preferring the requester's private copy."""
        if requesting_user is not None:
            row = self.store.query_one(
                """
                SELECT * FROM semantic_models
                WHERE name = ? AND visibility = 'private' AND owner_user = ?
                """,
                [name, requesting_user],
            )
            if row is not None:
                return row
        return self.store.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'public'",
            [name],
        )

    def _require_visible_model(
        self, name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        row = self._get_model_row_by_name(name, requesting_user=requesting_user)
        if row is None:
            raise NotFoundError(
                ErrorCode.MODEL_NOT_FOUND,
                f"Semantic model '{name}' not found",
            )
        if row["visibility"] == "private" and (
            requesting_user is None or requesting_user != row["owner_user"]
        ):
            raise NotFoundError(
                ErrorCode.MODEL_NOT_FOUND,
                f"Semantic model '{name}' not found",
            )
        return row

    def _require_private_model(self, name: str, owner_user: str | None = None) -> dict[str, Any]:
        if owner_user is not None:
            row = self.store.query_one(
                """
                SELECT * FROM semantic_models
                WHERE name = ? AND visibility = 'private' AND owner_user = ?
                """,
                [name, owner_user],
            )
            if row is not None:
                return row

        public_row = self.store.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'public'",
            [name],
        )
        if public_row is not None:
            raise ForbiddenError(
                ErrorCode.FORBIDDEN,
                f"Cannot delete official semantic model '{name}' via private model delete.",
            )

        raise NotFoundError(
            ErrorCode.MODEL_NOT_FOUND,
            f"Semantic model '{name}' not found",
        )

    def _assemble_model(self, model_row: dict[str, Any]) -> dict[str, Any]:
        """Assemble a full OSI-conformant model dict from storage rows."""
        model_id = model_row["model_id"]
        ds_rows = self.store.query_rows(
            "SELECT * FROM semantic_datasets WHERE model_id = ? ORDER BY dataset_id",
            [model_id],
        )
        datasets: list[dict[str, Any]] = []
        for ds_row in ds_rows:
            field_rows = self.store.query_rows(
                "SELECT * FROM semantic_fields WHERE dataset_id = ? ORDER BY position",
                [ds_row["dataset_id"]],
            )
            ds_dict = dict(ds_row)
            ds_dict["_fields"] = [dict(field_row) for field_row in field_rows]
            datasets.append(_storage_to_dataset(ds_dict))

        rel_rows = self.store.query_rows(
            "SELECT * FROM semantic_relationships WHERE model_id = ? ORDER BY relationship_id",
            [model_id],
        )
        relationships = [_storage_to_relationship(dict(row)) for row in rel_rows]

        metric_rows = self.store.query_rows(
            "SELECT * FROM semantic_metrics WHERE model_id = ? ORDER BY metric_id",
            [model_id],
        )
        metrics = [_storage_to_metric(dict(row)) for row in metric_rows]

        return storage_to_model(dict(model_row), datasets, relationships, metrics)

    def get_semantic_model(self, name: str, requesting_user: str | None = None) -> dict[str, Any]:
        """Get a semantic model by name with visibility filtering."""
        model_row = self._require_visible_model(name, requesting_user)
        return self._assemble_model(model_row)

    def list_semantic_models(self, requesting_user: str | None = None) -> list[dict[str, Any]]:
        """List public models plus private models owned by requesting_user."""
        results: list[dict[str, Any]] = []

        public_rows = self.store.query_rows(
            "SELECT * FROM semantic_models WHERE visibility = 'public' ORDER BY name"
        )
        for row in public_rows:
            results.append(self._assemble_model(row))

        if requesting_user:
            private_rows = self.store.query_rows(
                """
                SELECT * FROM semantic_models
                WHERE visibility = 'private' AND owner_user = ?
                ORDER BY name
                """,
                [requesting_user],
            )
            for row in private_rows:
                results.append(self._assemble_model(row))

        return results

    def validate_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        result = OsiSemanticDocumentValidator(datasource_service=self.datasource_service).validate(
            doc_data
        )
        return result.model_dump()

    def import_osi_semantic_models(self, doc_data: dict[str, Any]) -> None:
        """Validate and import an OSI document into current-user private models."""
        validation = OsiSemanticDocumentValidator(
            datasource_service=self.datasource_service
        ).validate(doc_data)
        if not validation.valid:
            raise DomainValidationError(
                code=ErrorCode.VALIDATION,
                message="OSI semantic document validation failed",
                detail={"errors": [issue.model_dump() for issue in validation.errors]},
            )

        owner_user = require_user()
        doc = OSIDocument.model_validate(doc_data)
        document = doc.model_dump(by_alias=True, exclude_none=True)
        binder = (
            DatasourceBinder(self.datasource_service)
            if self.datasource_service is not None
            else None
        )
        plan = SemanticImportPlanner(binder).preflight(document)
        SemanticImportExecutor(self.store).execute(
            document=plan.document,
            owner_user=owner_user,
            bindings=plan.bindings,
        )

    def export_osi_semantic_models(self, semantic_model_name: str | None = None) -> dict[str, Any]:
        """Export the current user's private semantic working copies."""
        owner_user = require_user()
        document = OsiDocumentExporter(self.store).export(
            owner_user=owner_user,
            semantic_model_name=semantic_model_name,
        )
        try:
            OSIDocument.model_validate(document)
        except PydanticValidationError as exc:
            raise DomainValidationError(
                code=ErrorCode.VALIDATION,
                message=str(exc),
                detail={"errors": exc.errors()},
            ) from exc
        return document

    def delete_semantic_model(self, name: str, owner_user: str | None = None) -> None:
        """Delete the caller's private semantic model working copy."""
        model_row = self._require_private_model(name, owner_user=owner_user)
        self.store.execute(
            "DELETE FROM semantic_models WHERE model_id = ?",
            [model_row["model_id"]],
        )
