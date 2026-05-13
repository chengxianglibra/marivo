"""SemanticModelV2Service — CRUD operations for OSI-aligned semantic models.

All data flows through the OSI Pydantic models on the write path and
returns OSI-conformant dicts on the read path.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from marivo.adapters.metadata import MetadataStore
from marivo.contracts.errors import (
    ConflictError,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
)
from marivo.contracts.errors import (
    ValidationError as DomainValidationError,
)
from marivo.contracts.generated import (
    Dataset,
    Field,
    Metric,
    OSIDocument,
    Relationship,
    SemanticModel,
)
from marivo.core.semantic.semantic_validation import (
    SemanticValidationError,
    validate_semantic_model,
)
from marivo.identity import require_user, resolve_user
from marivo.runtime.semantic.import_export import (
    DatasourceBinder,
    OsiDocumentExporter,
    SemanticMergeExecutor,
    SemanticMergePlanner,
)
from marivo.runtime.semantic.osi_storage import (
    _storage_to_dataset,
    _storage_to_field,
    _storage_to_metric,
    _storage_to_relationship,
    dataset_to_storage,
    field_to_storage,
    metric_to_storage,
    model_to_storage,
    relationship_to_storage,
    storage_to_model,
)


class SemanticModelV2Service:
    """CRUD service for OSI-aligned semantic models."""

    def __init__(self, store: MetadataStore, datasource_service: Any = None) -> None:
        self.store = store
        self.datasource_service = datasource_service

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_model_row_by_name(
        self, name: str, requesting_user: str | None = None
    ) -> dict[str, Any] | None:
        """Look up a semantic_models row by name with priority-based resolution.

        If requesting_user is provided, first try to find a private model owned by
        that user (private shadows public). If not found, fall back to the public
        model. Returns None if neither exists.
        """
        if requesting_user is not None:
            row = self.store.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [name, requesting_user],
            )
            if row is not None:
                return row
        return self.store.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'public'",
            [name],
        )

    def _require_model_row(self, name: str, requesting_user: str | None = None) -> dict[str, Any]:
        row = self._get_model_row_by_name(name, requesting_user=requesting_user)
        if row is None:
            raise NotFoundError(
                ErrorCode.MODEL_NOT_FOUND,
                f"Semantic model '{name}' not found",
            )
        return row

    def _require_private_model(self, name: str, owner_user: str | None = None) -> dict[str, Any]:
        """Look up a private model owned by owner_user. Raise 403 if not private or wrong owner."""
        if owner_user is not None:
            row = self.store.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [name, owner_user],
            )
            if row is not None:
                return row
        # Check if a public model exists (to give 403 rather than 404).
        public_row = self.store.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'public'",
            [name],
        )
        if public_row is not None:
            raise ForbiddenError(
                ErrorCode.FORBIDDEN,
                f"Cannot modify official semantic model '{name}' via CRUD; use /semantic-models/import",
            )
        raise NotFoundError(
            ErrorCode.MODEL_NOT_FOUND,
            f"Semantic model '{name}' not found",
        )

    def _require_visible_model(
        self, name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        """Look up a model and raise 404 if not visible to requesting_user.

        If requesting_user is provided and owns a private model with this name,
        that private model is returned (private shadows public).
        """
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

    def _assemble_model(self, model_row: dict[str, Any]) -> dict[str, Any]:
        """Assemble a full OSI-conformant model dict from storage rows."""
        model_id = model_row["model_id"]

        # Datasets
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
            ds_dict["_fields"] = [dict(f) for f in field_rows]
            datasets.append(_storage_to_dataset(ds_dict))

        # Relationships
        rel_rows = self.store.query_rows(
            "SELECT * FROM semantic_relationships WHERE model_id = ? ORDER BY relationship_id",
            [model_id],
        )
        relationships = [_storage_to_relationship(dict(r)) for r in rel_rows]

        # Metrics
        metric_rows = self.store.query_rows(
            "SELECT * FROM semantic_metrics WHERE model_id = ? ORDER BY metric_id",
            [model_id],
        )
        metrics = [_storage_to_metric(dict(r)) for r in metric_rows]

        return storage_to_model(dict(model_row), datasets, relationships, metrics)

    @staticmethod
    def _parse_marivo_data(ext: dict[str, Any]) -> dict[str, Any] | None:
        """Parse the 'data' field from a MARIVO custom_extension entry."""
        import json

        data = ext.get("data")
        if data is None:
            return None
        parsed: dict[str, Any] = json.loads(data) if isinstance(data, str) else data
        return parsed

    @staticmethod
    def _extract_marivo_from_exts(
        custom_extensions: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """Find and parse the MARIVO vendor extension from a list."""
        if not custom_extensions:
            return None
        for ext in custom_extensions:
            if ext.get("vendor_name") == "MARIVO":
                parsed = SemanticModelV2Service._parse_marivo_data(ext)
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _enrich_metric_with_marivo(metric_data: dict[str, Any]) -> dict[str, Any]:
        """Extract MARIVO metric extension fields into top-level dict keys."""
        enriched = dict(metric_data)
        marivo = SemanticModelV2Service._extract_marivo_from_exts(
            metric_data.get("custom_extensions")
        )
        if marivo:
            enriched["observed_dataset"] = marivo.get("observed_dataset")
            enriched["observation_grain"] = marivo.get("observation_grain")
            enriched["additive_dimensions"] = marivo.get("additive_dimensions")
            enriched["additivity"] = marivo.get("additivity")
            enriched["filters"] = marivo.get("filters")
        return enriched

    def _enrich_model_dict_with_marivo(self, model_data: dict[str, Any]) -> dict[str, Any]:
        """Extract MARIVO extension data from custom_extensions and add as top-level
        dict fields for validation.

        This does NOT modify the original model_data; it returns a new dict.
        """
        enriched = dict(model_data)

        # Enrich nested datasets
        datasets = enriched.get("datasets") or []
        enriched_datasets = []
        for ds in datasets:
            ds_enriched = dict(ds)
            ds_marivo = self._extract_marivo_from_exts(ds.get("custom_extensions"))
            if ds_marivo:
                ds_enriched["datasource_id"] = ds_marivo.get("datasource_id")
            # Enrich fields
            fields = ds_enriched.get("fields") or []
            enriched_fields = []
            for field in fields:
                f_enriched = dict(field)
                f_marivo = self._extract_marivo_from_exts(field.get("custom_extensions"))
                if f_marivo:
                    f_enriched["data_type"] = f_marivo.get("data_type")
                enriched_fields.append(f_enriched)
            ds_enriched["fields"] = enriched_fields
            enriched_datasets.append(ds_enriched)
        enriched["datasets"] = enriched_datasets

        # Enrich relationships
        relationships = enriched.get("relationships") or []
        enriched_relationships = []
        for rel in relationships:
            rel_enriched = dict(rel)
            rel_marivo = self._extract_marivo_from_exts(rel.get("custom_extensions"))
            if rel_marivo:
                rel_enriched["cardinality"] = rel_marivo.get("cardinality")
            enriched_relationships.append(rel_enriched)
        enriched["relationships"] = enriched_relationships

        # Enrich metrics
        metrics = enriched.get("metrics") or []
        enriched_metrics = []
        for metric in metrics:
            enriched_metrics.append(self._enrich_metric_with_marivo(metric))
        enriched["metrics"] = enriched_metrics

        return enriched

    def _build_fields_by_dataset(
        self, datasets: list[dict[str, Any]]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Build a {dataset_name: {field_name: field_dict}} lookup."""
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for ds in datasets:
            fields: dict[str, dict[str, Any]] = {}
            for field in ds.get("fields") or []:
                fields[field["name"]] = field
            result[ds["name"]] = fields
        return result

    # ------------------------------------------------------------------
    # SemanticModel CRUD
    # ------------------------------------------------------------------

    def create_semantic_model(self, model_data: dict[str, Any]) -> dict[str, Any]:
        """Create a semantic model from an OSI-conformant dict.

        owner_user and visibility are resolved from the call context only:
        resolve_user() → ContextVar (set by HTTP middleware or cmd_mcp) → MARIVO_DEFAULT_USER env var.
        """
        owner_user = resolve_user()
        visibility = "private" if owner_user else "public"

        if visibility != "private":
            raise ForbiddenError(
                ErrorCode.FORBIDDEN,
                "Public (official) models must be created via POST /semantic-models/import. "
                "Private models can be created via POST /semantic-models.",
            )

        # Reject private model name that conflicts with another private model for same owner
        if owner_user:
            private_conflict = self.store.query_one(
                "SELECT 1 FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ? LIMIT 1",
                [model_data.get("name"), owner_user],
            )
            if private_conflict:
                raise ConflictError(
                    ErrorCode.CONFLICT,
                    f"Private model '{model_data.get('name')}' already exists for user '{owner_user}'",
                )

        # Enrich for validation (adds MARIVO fields as top-level dict keys for datasets/metrics/etc.)
        enriched = self._enrich_model_dict_with_marivo(model_data)

        # Validate
        try:
            validate_semantic_model(enriched)
        except SemanticValidationError as exc:
            raise DomainValidationError(
                code=ErrorCode.VALIDATION,
                message=str(exc),
                detail={"errors": exc.errors},
            ) from exc

        # Parse with Pydantic
        model = SemanticModel.model_validate(model_data)

        # Insert model row — visibility/owner_user come from resolved context, not OSI model
        storage_data = model_to_storage(model, owner_user=owner_user, visibility=visibility)
        self.store.execute(
            """
            INSERT INTO semantic_models
                (name, description, ai_context, visibility, owner_user)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                storage_data["name"],
                storage_data["description"],
                storage_data["ai_context"],
                storage_data["visibility"],
                storage_data["owner_user"],
            ],
        )

        # Look up the just-inserted private model (name may not be unique across visibilities)
        model_row = self.store.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
            [model.name, storage_data["owner_user"]],
        )
        assert model_row is not None  # just inserted
        model_id = model_row["model_id"]

        # Insert datasets + fields
        for ds in model.datasets:
            ds_storage = dataset_to_storage(ds, model_id)
            self.store.execute(
                """
                INSERT INTO semantic_datasets
                    (model_id, name, source, primary_key, unique_keys, description,
                     ai_context, datasource_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ds_storage["model_id"],
                    ds_storage["name"],
                    ds_storage["source"],
                    ds_storage["primary_key"],
                    ds_storage["unique_keys"],
                    ds_storage["description"],
                    ds_storage["ai_context"],
                    ds_storage["datasource_id"],
                ],
            )

            ds_row = self.store.query_one(
                "SELECT dataset_id FROM semantic_datasets WHERE model_id = ? AND name = ?",
                [model_id, ds.name],
            )
            assert ds_row is not None  # just inserted
            dataset_id = ds_row["dataset_id"]

            for pos, field in enumerate(ds.fields or []):
                f_storage = field_to_storage(field, dataset_id, pos)
                self.store.execute(
                    """
                    INSERT INTO semantic_fields
                        (dataset_id, name, expression, is_time, is_dimension, label, description,
                         ai_context, data_type, position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        f_storage["dataset_id"],
                        f_storage["name"],
                        f_storage["expression"],
                        f_storage["is_time"],
                        f_storage["is_dimension"],
                        f_storage["label"],
                        f_storage["description"],
                        f_storage["ai_context"],
                        f_storage["data_type"],
                        f_storage["position"],
                    ],
                )

        # Insert relationships
        for rel in model.relationships or []:
            rel_storage = relationship_to_storage(rel, model_id)
            self.store.execute(
                """
                INSERT INTO semantic_relationships
                    (model_id, name, from_dataset, to_dataset, from_columns,
                     to_columns, ai_context, cardinality)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rel_storage["model_id"],
                    rel_storage["name"],
                    rel_storage["from_dataset"],
                    rel_storage["to_dataset"],
                    rel_storage["from_columns"],
                    rel_storage["to_columns"],
                    rel_storage["ai_context"],
                    rel_storage["cardinality"],
                ],
            )

        # Insert metrics
        for metric in model.metrics or []:
            metric_storage = metric_to_storage(metric, model_id)
            self.store.execute(
                """
                INSERT INTO semantic_metrics
                    (model_id, name, expression, description, ai_context,
                     additive_dimensions)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    metric_storage["model_id"],
                    metric_storage["name"],
                    metric_storage["expression"],
                    metric_storage["description"],
                    metric_storage["ai_context"],
                    metric_storage["additive_dimensions"],
                ],
            )

        # Initialize readiness status
        self.store.execute(
            """
            INSERT INTO semantic_readiness_status (model_id, status, blockers)
            VALUES (?, 'not_ready', '[]')
            """,
            [model_id],
        )

        return self._assemble_model(model_row)

    def get_semantic_model(self, name: str, requesting_user: str | None = None) -> dict[str, Any]:
        """Get a semantic model by name with visibility filtering."""
        model_row = self._require_visible_model(name, requesting_user)
        return self._assemble_model(model_row)

    def list_semantic_models(self, requesting_user: str | None = None) -> list[dict[str, Any]]:
        """List semantic models with visibility filtering.

        Returns all public models + private models owned by requesting_user.
        Same-name models can appear twice (one official, one private).
        No version-based filtering — semantic_models always contains the current state.
        """
        results: list[dict[str, Any]] = []

        # All public models
        public_rows = self.store.query_rows(
            "SELECT * FROM semantic_models WHERE visibility = 'public' ORDER BY name"
        )
        for row in public_rows:
            results.append(self._assemble_model(row))

        # Private models owned by requesting_user
        if requesting_user:
            private_rows = self.store.query_rows(
                "SELECT * FROM semantic_models WHERE visibility = 'private' AND owner_user = ? ORDER BY name",
                [requesting_user],
            )
            for row in private_rows:
                results.append(self._assemble_model(row))

        return results

    def update_semantic_model(
        self, name: str, updates: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        """Update top-level fields of a semantic model (description only for now)."""
        model_row = self._require_private_model(name, owner_user=owner_user)
        model_id = model_row["model_id"]

        allowed_fields = {"description"}
        update_parts: list[str] = []
        params: list[Any] = []

        for field in allowed_fields:
            if field in updates:
                update_parts.append(f"{field} = ?")
                params.append(updates[field])

        if not update_parts:
            return self._assemble_model(model_row)

        update_parts.append("updated_at = datetime('now')")
        params.append(model_id)

        self.store.execute(
            f"UPDATE semantic_models SET {', '.join(update_parts)} WHERE model_id = ?",
            params,
        )

        updated_row = self._require_model_row(name, requesting_user=owner_user)
        return self._assemble_model(updated_row)

    def delete_semantic_model(self, name: str, owner_user: str | None = None) -> None:
        """Delete a semantic model and all children (CASCADE)."""
        model_row = self._require_private_model(name, owner_user=owner_user)
        self.store.execute(
            "DELETE FROM semantic_models WHERE model_id = ?",
            [model_row["model_id"]],
        )

    # ------------------------------------------------------------------
    # Dataset CRUD
    # ------------------------------------------------------------------

    def create_dataset(
        self, model_name: str, ds_data: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        """Create a dataset within a model."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        model_id = model_row["model_id"]

        ds = Dataset.model_validate(ds_data)

        # Validate dataset name is unique within the model
        existing = self.store.query_one(
            "SELECT 1 FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_id, ds.name],
        )
        if existing:
            raise ConflictError(
                ErrorCode.CONFLICT,
                f"Dataset '{ds.name}' already exists in model",
            )

        ds_storage = dataset_to_storage(ds, model_id)

        self.store.execute(
            """
            INSERT INTO semantic_datasets
                (model_id, name, source, primary_key, unique_keys, description,
                 ai_context, datasource_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ds_storage["model_id"],
                ds_storage["name"],
                ds_storage["source"],
                ds_storage["primary_key"],
                ds_storage["unique_keys"],
                ds_storage["description"],
                ds_storage["ai_context"],
                ds_storage["datasource_id"],
            ],
        )

        ds_row = self.store.query_one(
            "SELECT dataset_id FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_id, ds.name],
        )
        assert ds_row is not None  # just inserted
        dataset_id = ds_row["dataset_id"]

        for pos, field in enumerate(ds.fields or []):
            f_storage = field_to_storage(field, dataset_id, pos)
            self.store.execute(
                """
                INSERT INTO semantic_fields
                    (dataset_id, name, expression, is_time, is_dimension, label, description,
                     ai_context, data_type, position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f_storage["dataset_id"],
                    f_storage["name"],
                    f_storage["expression"],
                    f_storage["is_time"],
                    f_storage["is_dimension"],
                    f_storage["label"],
                    f_storage["description"],
                    f_storage["ai_context"],
                    f_storage["data_type"],
                    f_storage["position"],
                ],
            )

        return self.get_dataset(model_name, ds.name, requesting_user=model_row["owner_user"])

    def get_dataset(
        self, model_name: str, dataset_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        """Get a dataset by name within a model."""
        model_row = self._require_visible_model(model_name, requesting_user)
        ds_row = self.store.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_row["model_id"], dataset_name],
        )
        if ds_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Dataset '{dataset_name}' not found in model '{model_name}'",
            )

        field_rows = self.store.query_rows(
            "SELECT * FROM semantic_fields WHERE dataset_id = ? ORDER BY position",
            [ds_row["dataset_id"]],
        )
        ds_dict = dict(ds_row)
        ds_dict["_fields"] = [dict(f) for f in field_rows]
        return _storage_to_dataset(ds_dict)

    def list_datasets(
        self, model_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        """List all datasets in a model."""
        model_row = self._require_visible_model(model_name, requesting_user)
        ds_rows = self.store.query_rows(
            "SELECT * FROM semantic_datasets WHERE model_id = ? ORDER BY dataset_id",
            [model_row["model_id"]],
        )
        result = []
        for ds_row in ds_rows:
            field_rows = self.store.query_rows(
                "SELECT * FROM semantic_fields WHERE dataset_id = ? ORDER BY position",
                [ds_row["dataset_id"]],
            )
            ds_dict = dict(ds_row)
            ds_dict["_fields"] = [dict(f) for f in field_rows]
            result.append(_storage_to_dataset(ds_dict))
        return result

    def update_dataset(
        self,
        model_name: str,
        dataset_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        """Update a dataset's top-level fields."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        ds_row = self.store.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_row["model_id"], dataset_name],
        )
        if ds_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Dataset '{dataset_name}' not found in model '{model_name}'",
            )

        allowed_fields = {"description", "source", "primary_key", "unique_keys"}
        import json

        update_parts: list[str] = []
        params: list[Any] = []

        for field in allowed_fields:
            if field in updates:
                update_parts.append(f"{field} = ?")
                value = updates[field]
                if field in ("primary_key", "unique_keys") and value is not None:
                    value = json.dumps(value)
                params.append(value)

        if not update_parts:
            return self.get_dataset(
                model_name, dataset_name, requesting_user=model_row["owner_user"]
            )

        update_parts.append("updated_at = datetime('now')")
        params.append(ds_row["dataset_id"])

        self.store.execute(
            f"UPDATE semantic_datasets SET {', '.join(update_parts)} WHERE dataset_id = ?",
            params,
        )

        return self.get_dataset(model_name, dataset_name, requesting_user=model_row["owner_user"])

    def delete_dataset(
        self, model_name: str, dataset_name: str, owner_user: str | None = None
    ) -> None:
        """Delete a dataset and all its fields (CASCADE)."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        ds_row = self.store.query_one(
            "SELECT dataset_id FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_row["model_id"], dataset_name],
        )
        if ds_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Dataset '{dataset_name}' not found in model '{model_name}'",
            )
        self.store.execute(
            "DELETE FROM semantic_datasets WHERE dataset_id = ?",
            [ds_row["dataset_id"]],
        )

    # ------------------------------------------------------------------
    # Field CRUD
    # ------------------------------------------------------------------

    def _require_dataset_row_for_model(
        self, model_row: dict[str, Any], model_name: str, dataset_name: str
    ) -> dict[str, Any]:
        ds_row = self.store.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_row["model_id"], dataset_name],
        )
        if ds_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND_DATASET,
                f"Dataset '{dataset_name}' not found in model '{model_name}'",
            )
        return ds_row

    def list_fields(
        self,
        model_name: str,
        dataset_name: str,
        requesting_user: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all fields in a dataset."""
        model_row = self._require_visible_model(model_name, requesting_user)
        ds_row = self._require_dataset_row_for_model(model_row, model_name, dataset_name)
        field_rows = self.store.query_rows(
            "SELECT * FROM semantic_fields WHERE dataset_id = ? ORDER BY position",
            [ds_row["dataset_id"]],
        )
        return [_storage_to_field(dict(row)) for row in field_rows]

    def get_field(
        self,
        model_name: str,
        dataset_name: str,
        field_name: str,
        requesting_user: str | None = None,
    ) -> dict[str, Any]:
        """Get a field by name within a dataset."""
        model_row = self._require_visible_model(model_name, requesting_user)
        ds_row = self._require_dataset_row_for_model(model_row, model_name, dataset_name)
        field_row = self.store.query_one(
            "SELECT * FROM semantic_fields WHERE dataset_id = ? AND name = ?",
            [ds_row["dataset_id"], field_name],
        )
        if field_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND_FIELD,
                f"Field '{field_name}' not found in dataset '{dataset_name}'",
            )
        return _storage_to_field(dict(field_row))

    def create_field(
        self,
        model_name: str,
        dataset_name: str,
        field_data: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        """Create a field within a dataset."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        ds_row = self._require_dataset_row_for_model(model_row, model_name, dataset_name)
        field = Field.model_validate(field_data)

        existing = self.store.query_one(
            "SELECT 1 FROM semantic_fields WHERE dataset_id = ? AND name = ?",
            [ds_row["dataset_id"], field.name],
        )
        if existing:
            raise ConflictError(
                ErrorCode.CONFLICT,
                f"Field '{field.name}' already exists in dataset '{dataset_name}'",
            )

        max_position_row = self.store.query_one(
            "SELECT COALESCE(MAX(position), -1) AS max_position FROM semantic_fields WHERE dataset_id = ?",
            [ds_row["dataset_id"]],
        )
        max_position = max_position_row["max_position"] if max_position_row is not None else -1
        field_storage = field_to_storage(field, ds_row["dataset_id"], max_position + 1)
        self.store.execute(
            """
            INSERT INTO semantic_fields
                (dataset_id, name, expression, is_time, is_dimension, label, description,
                 ai_context, data_type, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                field_storage["dataset_id"],
                field_storage["name"],
                field_storage["expression"],
                field_storage["is_time"],
                field_storage["is_dimension"],
                field_storage["label"],
                field_storage["description"],
                field_storage["ai_context"],
                field_storage["data_type"],
                field_storage["position"],
            ],
        )
        return self.get_field(
            model_name, dataset_name, field.name, requesting_user=model_row["owner_user"]
        )

    def update_field(
        self,
        model_name: str,
        dataset_name: str,
        field_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        """Patch a field while preserving its name and position."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        ds_row = self._require_dataset_row_for_model(model_row, model_name, dataset_name)
        field_row = self.store.query_one(
            "SELECT * FROM semantic_fields WHERE dataset_id = ? AND name = ?",
            [ds_row["dataset_id"], field_name],
        )
        if field_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND_FIELD,
                f"Field '{field_name}' not found in dataset '{dataset_name}'",
            )

        current = _storage_to_field(dict(field_row))
        patched = {**current, **updates, "name": field_name}
        field = Field.model_validate(patched)
        field_storage = field_to_storage(field, ds_row["dataset_id"], field_row["position"])

        self.store.execute(
            """
            UPDATE semantic_fields
            SET expression = ?, is_time = ?, is_dimension = ?, label = ?, description = ?,
                ai_context = ?, data_type = ?, updated_at = datetime('now')
            WHERE field_id = ?
            """,
            [
                field_storage["expression"],
                field_storage["is_time"],
                field_storage["is_dimension"],
                field_storage["label"],
                field_storage["description"],
                field_storage["ai_context"],
                field_storage["data_type"],
                field_row["field_id"],
            ],
        )

        return self.get_field(
            model_name, dataset_name, field_name, requesting_user=model_row["owner_user"]
        )

    def delete_field(
        self,
        model_name: str,
        dataset_name: str,
        field_name: str,
        owner_user: str | None = None,
    ) -> None:
        """Delete a field from a dataset."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        ds_row = self._require_dataset_row_for_model(model_row, model_name, dataset_name)
        field_row = self.store.query_one(
            "SELECT field_id FROM semantic_fields WHERE dataset_id = ? AND name = ?",
            [ds_row["dataset_id"], field_name],
        )
        if field_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND_FIELD,
                f"Field '{field_name}' not found in dataset '{dataset_name}'",
            )
        self.store.execute(
            "DELETE FROM semantic_fields WHERE field_id = ?",
            [field_row["field_id"]],
        )

    # ------------------------------------------------------------------
    # Relationship CRUD
    # ------------------------------------------------------------------

    def create_relationship(
        self, model_name: str, rel_data: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        """Create a relationship within a model. Validates from/to datasets exist."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        model_id = model_row["model_id"]

        # Validate from/to datasets exist
        datasets = self.list_datasets(model_name, requesting_user=model_row["owner_user"])
        from_ds = rel_data.get("from") or rel_data.get("from_dataset")
        to_ds = rel_data.get("to") or rel_data.get("to_dataset")
        dataset_names = {ds["name"] for ds in datasets}

        if from_ds and from_ds not in dataset_names:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"from_dataset '{from_ds}' does not exist in model '{model_name}'",
            )
        if to_ds and to_ds not in dataset_names:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"to_dataset '{to_ds}' does not exist in model '{model_name}'",
            )

        # Validate from_columns and to_columns have matching lengths
        from_cols = rel_data.get("from_columns")
        to_cols = rel_data.get("to_columns")
        if from_cols is not None and to_cols is not None and len(from_cols) != len(to_cols):
            raise ValueError(
                f"from_columns and to_columns must have matching lengths, "
                f"got {len(from_cols)} and {len(to_cols)}"
            )

        rel = Relationship.model_validate(rel_data)
        rel_storage = relationship_to_storage(rel, model_id)

        self.store.execute(
            """
            INSERT INTO semantic_relationships
                (model_id, name, from_dataset, to_dataset, from_columns,
                 to_columns, ai_context, cardinality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rel_storage["model_id"],
                rel_storage["name"],
                rel_storage["from_dataset"],
                rel_storage["to_dataset"],
                rel_storage["from_columns"],
                rel_storage["to_columns"],
                rel_storage["ai_context"],
                rel_storage["cardinality"],
            ],
        )

        return self.get_relationship(model_name, rel.name, requesting_user=model_row["owner_user"])

    def get_relationship(
        self, model_name: str, rel_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        """Get a relationship by name within a model."""
        model_row = self._require_visible_model(model_name, requesting_user)
        rel_row = self.store.query_one(
            "SELECT * FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [model_row["model_id"], rel_name],
        )
        if rel_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Relationship '{rel_name}' not found in model '{model_name}'",
            )
        return _storage_to_relationship(dict(rel_row))

    def list_relationships(
        self, model_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        """List all relationships in a model."""
        model_row = self._require_visible_model(model_name, requesting_user)
        rel_rows = self.store.query_rows(
            "SELECT * FROM semantic_relationships WHERE model_id = ? ORDER BY relationship_id",
            [model_row["model_id"]],
        )
        return [_storage_to_relationship(dict(r)) for r in rel_rows]

    def update_relationship(
        self,
        model_name: str,
        rel_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        """Update a relationship's fields."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        rel_row = self.store.query_one(
            "SELECT * FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [model_row["model_id"], rel_name],
        )
        if rel_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Relationship '{rel_name}' not found in model '{model_name}'",
            )

        import json

        allowed_fields = {"cardinality", "ai_context"}
        update_parts: list[str] = []
        params: list[Any] = []

        for field in allowed_fields:
            if field in updates:
                update_parts.append(f"{field} = ?")
                value = updates[field]
                if field == "ai_context" and value is not None:
                    value = json.dumps(value)
                params.append(value)

        if not update_parts:
            return self.get_relationship(
                model_name, rel_name, requesting_user=model_row["owner_user"]
            )

        update_parts.append("updated_at = datetime('now')")
        params.append(rel_row["relationship_id"])

        self.store.execute(
            f"UPDATE semantic_relationships SET {', '.join(update_parts)} WHERE relationship_id = ?",
            params,
        )

        return self.get_relationship(model_name, rel_name, requesting_user=model_row["owner_user"])

    def delete_relationship(
        self, model_name: str, rel_name: str, owner_user: str | None = None
    ) -> None:
        """Delete a relationship."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        rel_row = self.store.query_one(
            "SELECT relationship_id FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [model_row["model_id"], rel_name],
        )
        if rel_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Relationship '{rel_name}' not found in model '{model_name}'",
            )
        self.store.execute(
            "DELETE FROM semantic_relationships WHERE relationship_id = ?",
            [rel_row["relationship_id"]],
        )

    # ------------------------------------------------------------------
    # Metric CRUD
    # ------------------------------------------------------------------

    def create_metric(
        self, model_name: str, metric_data: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        """Create a metric within a model."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        model_id = model_row["model_id"]

        # Enrich metric data with MARIVO extension fields for validation
        enriched_metric = self._enrich_metric_with_marivo(metric_data)

        # Validate metric fields against existing datasets
        if (
            enriched_metric.get("observed_dataset")
            or enriched_metric.get("additive_dimensions")
            or enriched_metric.get("additivity")
        ):
            from marivo.core.semantic.semantic_validation import validate_metric

            datasets = self.list_datasets(model_name, requesting_user=model_row["owner_user"])
            fields_by_dataset = self._build_fields_by_dataset(datasets)
            try:
                validate_metric(enriched_metric, datasets, fields_by_dataset)
            except SemanticValidationError as exc:
                raise DomainValidationError(
                    code=ErrorCode.VALIDATION,
                    message=str(exc),
                    detail={"errors": exc.errors},
                ) from exc

        metric = Metric.model_validate(metric_data)
        metric_storage = metric_to_storage(metric, model_id)

        self.store.execute(
            """
            INSERT INTO semantic_metrics
                (model_id, name, expression, description, ai_context,
                 additive_dimensions)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                metric_storage["model_id"],
                metric_storage["name"],
                metric_storage["expression"],
                metric_storage["description"],
                metric_storage["ai_context"],
                metric_storage["additive_dimensions"],
            ],
        )

        return self.get_metric(model_name, metric.name, requesting_user=model_row["owner_user"])

    def get_metric(
        self, model_name: str, metric_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        """Get a metric by name within a model."""
        model_row = self._require_visible_model(model_name, requesting_user)
        metric_row = self.store.query_one(
            "SELECT * FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [model_row["model_id"], metric_name],
        )
        if metric_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Metric '{metric_name}' not found in model '{model_name}'",
            )
        return _storage_to_metric(dict(metric_row))

    def list_metrics(
        self, model_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        """List all metrics in a model."""
        model_row = self._require_visible_model(model_name, requesting_user)
        metric_rows = self.store.query_rows(
            "SELECT * FROM semantic_metrics WHERE model_id = ? ORDER BY metric_id",
            [model_row["model_id"]],
        )
        return [_storage_to_metric(dict(r)) for r in metric_rows]

    def update_metric(
        self,
        model_name: str,
        metric_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        """Update a metric's fields."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        metric_row = self.store.query_one(
            "SELECT * FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [model_row["model_id"], metric_name],
        )
        if metric_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Metric '{metric_name}' not found in model '{model_name}'",
            )

        import json

        allowed_fields = {
            "description",
            "ai_context",
            "additive_dimensions",
            "expression",
        }
        update_parts: list[str] = []
        params: list[Any] = []

        for field in allowed_fields:
            if field in updates:
                update_parts.append(f"{field} = ?")
                value = updates[field]
                if (
                    field in ("ai_context", "additive_dimensions", "expression")
                    and value is not None
                    and isinstance(value, (dict, list))
                ):
                    value = json.dumps(value)
                params.append(value)

        if not update_parts:
            return self.get_metric(model_name, metric_name, requesting_user=model_row["owner_user"])

        update_parts.append("updated_at = datetime('now')")
        params.append(metric_row["metric_id"])

        self.store.execute(
            f"UPDATE semantic_metrics SET {', '.join(update_parts)} WHERE metric_id = ?",
            params,
        )

        return self.get_metric(model_name, metric_name, requesting_user=model_row["owner_user"])

    def delete_metric(
        self, model_name: str, metric_name: str, owner_user: str | None = None
    ) -> None:
        """Delete a metric."""
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        metric_row = self.store.query_one(
            "SELECT metric_id FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [model_row["model_id"], metric_name],
        )
        if metric_row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND,
                f"Metric '{metric_name}' not found in model '{model_name}'",
            )
        self.store.execute(
            "DELETE FROM semantic_metrics WHERE metric_id = ?",
            [metric_row["metric_id"]],
        )

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def import_osi_document(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        """Import an OSI document into the current user's private working copies."""
        owner_user = require_user()
        try:
            doc = OSIDocument.model_validate(doc_data)
        except PydanticValidationError as exc:
            raise DomainValidationError(
                code=ErrorCode.VALIDATION,
                message=str(exc),
                detail={"errors": exc.errors()},
            ) from exc

        document = doc.model_dump(by_alias=True, exclude_none=True)
        binder = (
            DatasourceBinder(self.datasource_service)
            if self.datasource_service is not None
            else None
        )
        plan = SemanticMergePlanner(binder).preflight(document)
        try:
            doc = OSIDocument.model_validate(plan.document)
        except PydanticValidationError as exc:
            raise DomainValidationError(
                code=ErrorCode.VALIDATION,
                message=str(exc),
                detail={"errors": exc.errors()},
            ) from exc

        normalized_document = doc.model_dump(by_alias=True, exclude_none=True)
        execution_document = {**normalized_document, "semantic_model": []}
        for model_dict in normalized_document["semantic_model"]:
            existing_model = self._get_private_model_for_import(
                model_dict["name"],
                owner_user=owner_user,
            )
            validation_model = self._build_private_import_validation_model(
                model_dict,
                existing_model=existing_model,
                owner_user=owner_user,
            )
            execution_model = self._build_private_import_execution_model(
                model_dict,
                existing_model=existing_model,
            )
            execution_document["semantic_model"].append(execution_model)
            enriched = self._enrich_model_dict_with_marivo(validation_model)
            try:
                validate_semantic_model(enriched)
            except SemanticValidationError as exc:
                raise DomainValidationError(
                    code=ErrorCode.VALIDATION,
                    message=str(exc),
                    detail={"errors": exc.errors},
                ) from exc

        report = SemanticMergeExecutor(self.store).execute(
            document=execution_document,
            owner_user=owner_user,
            bindings=plan.bindings,
        )
        return report.model_dump()

    def export_osi_document(self, semantic_model_name: str | None = None) -> dict[str, Any]:
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

    def _build_private_import_validation_model(
        self,
        model_dict: dict[str, Any],
        *,
        existing_model: dict[str, Any] | None,
        owner_user: str,
    ) -> dict[str, Any]:
        if existing_model is None:
            merged = dict(model_dict)
        else:
            merged = self._merge_model_dict(existing_model, model_dict)
        merged["visibility"] = "private"
        merged["owner_user"] = owner_user
        return merged

    def _get_private_model_for_import(
        self,
        model_name: str,
        *,
        owner_user: str,
    ) -> dict[str, Any] | None:
        existing = self.store.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
            [model_name, owner_user],
        )
        return None if existing is None else self._assemble_model(existing)

    @staticmethod
    def _build_private_import_execution_model(
        model_dict: dict[str, Any],
        *,
        existing_model: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if existing_model is None:
            return dict(model_dict)

        patched = dict(model_dict)
        for key in ("description", "ai_context"):
            if key not in patched and key in existing_model:
                patched[key] = existing_model[key]

        existing_datasets = {
            dataset["name"]: dataset for dataset in existing_model.get("datasets") or []
        }
        patched_datasets = []
        for dataset in model_dict.get("datasets") or []:
            existing_dataset = existing_datasets.get(dataset["name"])
            if existing_dataset is None:
                patched_datasets.append(dict(dataset))
                continue
            patched_dataset = dict(dataset)
            for key in (
                "primary_key",
                "unique_keys",
                "description",
                "ai_context",
                "custom_extensions",
            ):
                if key not in patched_dataset and key in existing_dataset:
                    patched_dataset[key] = existing_dataset[key]
            patched_datasets.append(patched_dataset)
        patched["datasets"] = patched_datasets
        return patched

    @staticmethod
    def _merge_model_dict(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in incoming.items():
            if key not in {"datasets", "metrics", "relationships"}:
                merged[key] = value
        merged["datasets"] = _merge_named_children(
            base.get("datasets") or [],
            incoming.get("datasets") or [],
            nested_key="fields",
        )
        if "metrics" in incoming or base.get("metrics"):
            merged["metrics"] = _merge_named_children(
                base.get("metrics") or [],
                incoming.get("metrics") or [],
            )
        if "relationships" in incoming or base.get("relationships"):
            merged["relationships"] = _merge_named_children(
                base.get("relationships") or [],
                incoming.get("relationships") or [],
            )
        return merged

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def get_readiness(self, model_name: str, requesting_user: str | None = None) -> dict[str, Any]:
        """Return readiness status/blockers for a model."""
        model_row = self._require_visible_model(model_name, requesting_user)
        model = self._assemble_model(model_row)
        live_blockers: list[dict[str, Any]] = []
        for dataset in model.get("datasets") or []:
            live_blockers.extend(self._check_dataset_live_readiness(dataset))

        readiness_row = self.store.query_one(
            "SELECT status, blockers FROM semantic_readiness_status WHERE model_id = ?",
            [model_row["model_id"]],
        )
        stored_blockers: list[dict[str, Any]] = []
        if readiness_row is None:
            blockers = live_blockers
            return {
                "status": "ready" if not blockers else "not_ready",
                "semantic_version_id": None,
                "evaluated_semantic_version_id": None,
                "blockers": blockers,
            }
        import json

        blockers_raw = readiness_row["blockers"]
        stored_blockers = json.loads(blockers_raw) if blockers_raw else []
        blockers = [*stored_blockers, *live_blockers]
        return {
            "status": "ready" if not blockers else "not_ready",
            "semantic_version_id": None,
            "evaluated_semantic_version_id": None,
            "blockers": blockers,
        }

    def _check_dataset_live_readiness(self, dataset: dict[str, Any]) -> list[dict[str, Any]]:
        datasource_id = str(dataset.get("datasource_id") or "").strip()
        if not datasource_id:
            marivo = self._extract_marivo_from_exts(dataset.get("custom_extensions"))
            if marivo:
                datasource_id = str(marivo.get("datasource_id") or "").strip()
        source = str(dataset.get("source") or "").strip()
        dataset_name = str(dataset.get("name") or "")
        if not datasource_id or not source:
            return []
        datasource_service = self.datasource_service
        if datasource_service is None:
            return []
        try:
            datasource_service.get_datasource(datasource_id)
        except KeyError:
            return [
                {
                    "code": "datasource_not_found",
                    "message": f"Dataset {dataset_name} references missing datasource {datasource_id}",
                    "dataset": dataset_name,
                    "datasource_id": datasource_id,
                    "source": source,
                }
            ]
        parts = source.split(".")
        if len(parts) == 2:
            schema_name, table_name = parts
        elif len(parts) == 3:
            _, schema_name, table_name = parts
        else:
            return [
                {
                    "code": "relation_not_found",
                    "message": f"Dataset {dataset_name} source {source} is not a schema.table or catalog.schema.table FQN",
                    "dataset": dataset_name,
                    "datasource_id": datasource_id,
                    "source": source,
                }
            ]
        try:
            datasource_service.browse_catalog_columns(datasource_id, schema_name, table_name)
        except KeyError:
            return [
                {
                    "code": "relation_not_found",
                    "message": f"Dataset {dataset_name} source {source} was not found in datasource {datasource_id}",
                    "dataset": dataset_name,
                    "datasource_id": datasource_id,
                    "source": source,
                }
            ]
        except ValueError as error:
            return [
                {
                    "code": "datasource_not_ready",
                    "message": str(error),
                    "dataset": dataset_name,
                    "datasource_id": datasource_id,
                    "source": source,
                }
            ]
        return []


def _merge_named_children(
    base: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    nested_key: str | None = None,
) -> list[dict[str, Any]]:
    by_name = {str(item["name"]): dict(item) for item in base}
    order = [str(item["name"]) for item in base]
    for item in incoming:
        name = str(item["name"])
        if name not in by_name:
            order.append(name)
            by_name[name] = dict(item)
            continue
        merged = {**by_name[name], **dict(item)}
        if nested_key is not None:
            merged[nested_key] = _merge_named_children(
                by_name[name].get(nested_key) or [],
                item.get(nested_key) or [],
            )
        by_name[name] = merged
    return [by_name[name] for name in order]
