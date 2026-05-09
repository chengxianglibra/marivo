from __future__ import annotations

import logging
from typing import Any

from app.contracts.errors import DomainError, ErrorCode
from app.contracts.ids import ModelId, RevisionId, UserId
from app.contracts.semantic import ModelSummary, SemanticModel
from app.storage.metadata import MetadataStore

logger = logging.getLogger(__name__)


class SqlModelStoreAdapter:
    """Wraps ``MetadataStore`` -> ``ModelStore``.

    Implements get/save/list by querying the metadata store directly,
    bypassing the HTTP-oriented SemanticModelV2Service so that
    contract-level parity with the local FileModelStore is achieved.
    """

    def __init__(
        self,
        service: Any,  # SemanticModelV2Service (kept for compatibility)
        metadata: MetadataStore,
    ) -> None:
        self._service = service
        self._metadata = metadata

    def get(self, selector: Any) -> SemanticModel | None:
        """Look up a model by name from the selector.

        Queries the metadata store directly to bypass the service's
        visibility filtering.
        """
        name = getattr(selector, "name", None)
        if name is None:
            return None

        # Try private model first (if owner context is available), then public
        owner = getattr(selector, "owner", None)
        if owner is not None:
            row = self._metadata.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [name, str(owner)],
            )
            if row is not None:
                return self._row_to_semantic_model(row)

        row = self._metadata.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'public'",
            [name],
        )
        if row is not None:
            return self._row_to_semantic_model(row)

        # Fallback: any model with this name
        row = self._metadata.query_one(
            "SELECT * FROM semantic_models WHERE name = ? LIMIT 1",
            [name],
        )
        if row is not None:
            return self._row_to_semantic_model(row)

        return None

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId:
        """Persist a semantic model directly via the metadata store."""
        visibility = model.visibility or "private"
        owner_user = str(model.owner) if model.owner else str(actor)
        description = model.description or None

        # Check for existing model with same name and visibility
        if visibility == "private":
            existing = self._metadata.query_one(
                "SELECT model_id, revision FROM semantic_models "
                "WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [model.name, owner_user],
            )
        else:
            existing = self._metadata.query_one(
                "SELECT model_id, revision FROM semantic_models "
                "WHERE name = ? AND visibility = 'public'",
                [model.name],
            )

        if existing is not None:
            model_id = ModelId(existing["model_id"])
            new_revision = (existing["revision"] or 0) + 1
            self._metadata.execute(
                "UPDATE semantic_models SET description = ?, revision = ?, updated_at = datetime('now') "
                "WHERE model_id = ?",
                [description, new_revision, int(model_id)],
            )
            return model_id

        self._metadata.execute(
            "INSERT INTO semantic_models (name, description, visibility, owner_user) "
            "VALUES (?, ?, ?, ?)",
            [model.name, description, visibility, owner_user],
        )

        # Look up the just-inserted row
        if visibility == "private":
            row = self._metadata.query_one(
                "SELECT model_id FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [model.name, owner_user],
            )
        else:
            row = self._metadata.query_one(
                "SELECT model_id FROM semantic_models WHERE name = ? AND visibility = 'public'",
                [model.name],
            )
        if row is None:
            raise DomainError(ErrorCode.MODEL_NOT_FOUND, f"Failed to create model '{model.name}'")
        return ModelId(row["model_id"])

    def list(self, query: Any) -> list[ModelSummary]:
        """List models according to the query criteria."""
        owner = getattr(query, "owner", None)
        include_public = getattr(query, "include_public", True)
        include_private = getattr(query, "include_private", False)

        conditions: list[str] = []
        params: list[Any] = []

        if owner is not None:
            if include_private:
                conditions.append("(visibility = 'private' AND owner_user = ?)")
                params.append(str(owner))
            if include_public:
                conditions.append("(visibility = 'public')")
        elif include_public:
            conditions.append("visibility = 'public'")

        if not conditions:
            return []

        where = " OR ".join(conditions)
        rows = self._metadata.query_rows(
            f"SELECT * FROM semantic_models WHERE {where}",
            params,
        )
        return [self._row_to_model_summary(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_semantic_model(row: dict[str, Any]) -> SemanticModel:
        """Convert a metadata store row to a domain SemanticModel."""
        return SemanticModel(
            model_id=ModelId(row["model_id"]) if row.get("model_id") is not None else None,
            name=row.get("name", ""),
            revision=RevisionId(str(row["revision"])) if row.get("revision") is not None else None,
            description=row.get("description"),
            osi_document={},
            visibility=row.get("visibility", "private"),
            owner=UserId(row["owner_user"]) if row.get("owner_user") else None,
        )

    @staticmethod
    def _row_to_model_summary(row: dict[str, Any]) -> ModelSummary:
        """Convert a metadata store row to a domain ModelSummary."""
        return ModelSummary(
            model_id=ModelId(row["model_id"]) if row.get("model_id") is not None else ModelId(0),
            name=row.get("name", ""),
            revision=RevisionId(str(row["revision"])) if row.get("revision") is not None else None,
            description=row.get("description"),
            visibility=row.get("visibility", "private"),
            owner=UserId(row["owner_user"]) if row.get("owner_user") else None,
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _dict_to_semantic_model(model_dict: dict[str, Any]) -> SemanticModel:
        """Convert a storage-level model dict to a domain SemanticModel."""
        marivo_exts = model_dict.get("custom_extensions") or []
        visibility = "private"
        owner: str | None = None
        revision: str | None = None
        for ext in marivo_exts:
            if ext.get("vendor_name") == "MARIVO":
                import json

                data = ext.get("data")
                parsed = json.loads(data) if isinstance(data, str) else data
                if parsed:
                    visibility = parsed.get("visibility", "private")
                    owner = parsed.get("owner_user")
                    revision = str(parsed.get("revision", "")) if parsed.get("revision") else None

        return SemanticModel(
            model_id=None,
            name=model_dict.get("name", ""),
            revision=RevisionId(revision) if revision else None,
            description=model_dict.get("description"),
            osi_document=model_dict,
            visibility=visibility,
            owner=UserId(owner) if owner else None,
        )

    @staticmethod
    def _dict_to_model_summary(model_dict: dict[str, Any]) -> ModelSummary:
        """Convert a storage-level model dict to a domain ModelSummary."""
        marivo_exts = model_dict.get("custom_extensions") or []
        visibility = "private"
        owner: str | None = None
        revision: str | None = None
        for ext in marivo_exts:
            if ext.get("vendor_name") == "MARIVO":
                import json

                data = ext.get("data")
                parsed = json.loads(data) if isinstance(data, str) else data
                if parsed:
                    visibility = parsed.get("visibility", "private")
                    owner = parsed.get("owner_user")
                    revision = str(parsed.get("revision", "")) if parsed.get("revision") else None

        # model_id from storage dict - extract from the dict if available
        model_id = ModelId(model_dict.get("model_id", 0))

        return ModelSummary(
            model_id=model_id,
            name=model_dict.get("name", ""),
            revision=RevisionId(revision) if revision else None,
            description=model_dict.get("description"),
            visibility=visibility,
            owner=UserId(owner) if owner else None,
            updated_at=model_dict.get("updated_at"),
        )
