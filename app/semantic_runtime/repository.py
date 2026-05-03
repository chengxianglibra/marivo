"""Dataset-native semantic runtime repository."""

from __future__ import annotations

import json
from typing import Any

from app.semantic_runtime.errors import (
    SemanticRuntimeNotFoundError,
    SemanticRuntimeUnpublishedError,
)
from app.semantic_runtime.resolution import ResolvedSemanticObject, RuntimeSemanticAvailability
from app.storage.metadata import MetadataStore


class SemanticRuntimeRepository:
    """Resolve runtime-visible semantic refs from the current metadata schema."""

    def __init__(
        self,
        metadata: MetadataStore,
        *,
        resolver: Any = None,
        planner_context_provider: Any = None,
        **_kwargs: Any,
    ) -> None:
        self.metadata = metadata
        self.resolver = resolver or _StubResolver()
        self.planner_context_provider = planner_context_provider

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        if semantic_ref.startswith("metric."):
            return self.resolve_metric_ref(semantic_ref)
        raise KeyError(f"Semantic ref not found: {semantic_ref}")

    def inspect_ref(self, semantic_ref: str) -> Any:
        resolved = self.resolve_ref(semantic_ref)
        blockers: list[dict[str, Any]] = []
        if resolved.object_kind == "metric":
            semantic_object = resolved.semantic_object
            payload = semantic_object.get("payload") or {}
            if not payload.get("_dataset_grounding_ready"):
                blockers.append(
                    {
                        "code": "DATASET_GROUNDING_MISSING",
                        "message": "Metric has no dataset-native execution source.",
                        "subject_ref": semantic_ref,
                    }
                )
        return RuntimeSemanticAvailability(
            resolved=resolved,
            lifecycle_status="active",
            readiness_status="not_ready" if blockers else "ready",
            blocking_requirements=blockers,
            capabilities={"grounding": "dataset_native"},
            dependency_refs=[],
        )

    def resolve_entity_ref(self, entity_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Entity ref not found: {entity_ref}")

    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        row = self.metadata.query_one(
            """
            SELECT *
            FROM semantic_metric_contracts
            WHERE metric_ref = ?
            ORDER BY CASE WHEN status = 'published' THEN 0 ELSE 1 END, revision DESC
            LIMIT 1
            """,
            [metric_ref],
        )
        if row is None:
            raise SemanticRuntimeNotFoundError(
                f"Metric ref not found: {metric_ref}",
                semantic_ref=metric_ref,
            )
        if str(row["status"]) != "published":
            raise SemanticRuntimeUnpublishedError(
                f"Metric ref is not published: {metric_ref}",
                semantic_ref=metric_ref,
            )
        payload = json.loads(row["family_payload_json"] or "{}")
        dataset = self._metric_dataset(payload)
        payload["_dataset_grounding_ready"] = dataset is not None
        if dataset is not None:
            payload.setdefault("observed_dataset", dataset["name"])
            payload.setdefault("dataset_source", dataset["source"])
            payload.setdefault("datasource_id", dataset["datasource_id"])
            payload.setdefault("dataset_fields", self._dataset_fields(int(dataset["dataset_id"])))
        semantic_object = {
            "header": {
                "metric_ref": row["metric_ref"],
                "display_name": row["display_name"],
                "metric_family": row["metric_family"],
                "observed_entity_ref": row["observed_entity_ref"],
                "observation_grain_ref": row["observation_grain_ref"],
                "sample_kind": row["sample_kind"],
                "value_semantics": row["value_semantics"],
                "aggregation_scope": row["aggregation_scope"],
                "primary_time_ref": row["primary_time_ref"],
                "additivity_constraints": json.loads(row["additivity_constraints_json"] or "{}"),
                "metric_contract_version": row["metric_contract_version"],
            },
            "payload": payload,
        }
        return ResolvedSemanticObject(
            object_kind="metric",
            object_id=str(row["metric_contract_id"]),
            ref=metric_ref,
            semantic_object=semantic_object,
            status=str(row["status"]),
            revision=int(row["revision"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def resolve_process_ref(self, process_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Process ref not found: {process_ref}")

    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        return self._resolved_physical_dimension(dimension_ref)

    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Time ref not found: {time_ref}")

    def resolve_binding_ref(self, binding_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError(
            "binding_grounding_removed: v2 runtime uses dataset.datasource_id, "
            "dataset.source, and field.expression"
        )

    def resolve_relationship_ref(self, relationship_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Relationship ref not found: {relationship_ref}")

    def resolve_predicate_ref(self, predicate_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Predicate ref not found: {predicate_ref}")

    def resolve_metric(self, metric_name: str) -> Any:
        return None

    def resolve_entity(self, entity_name: str) -> Any:
        return None

    def resolve_metric_sql(self, metric_name: str) -> str | None:
        return None

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        return None

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        return {}

    def _metric_dataset(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        dataset_source = payload.get("dataset_source")
        datasource_id = payload.get("datasource_id")
        if (
            isinstance(dataset_source, str)
            and dataset_source.strip()
            and isinstance(datasource_id, str)
            and datasource_id.strip()
        ):
            row = self.metadata.query_one(
                """
                SELECT dataset_id, name, source, datasource_id
                FROM semantic_datasets
                WHERE source = ? AND datasource_id = ?
                ORDER BY updated_at DESC, dataset_id
                LIMIT 1
                """,
                [dataset_source.strip(), datasource_id.strip()],
            )
            return dict(row) if row is not None else None
        if isinstance(dataset_source, str) and dataset_source.strip():
            return None
        observed_dataset = payload.get("observed_dataset")
        if isinstance(observed_dataset, str) and observed_dataset.strip():
            row = self.metadata.query_one(
                """
                SELECT dataset_id, name, source, datasource_id
                FROM semantic_datasets
                WHERE name = ? AND datasource_id IS NOT NULL
                ORDER BY updated_at DESC, dataset_id
                LIMIT 1
                """,
                [observed_dataset.strip()],
            )
            if row is not None:
                return dict(row)
        row = self.metadata.query_one(
            """
            SELECT dataset_id, name, source, datasource_id
            FROM semantic_datasets
            WHERE datasource_id IS NOT NULL
            ORDER BY updated_at DESC, dataset_id
            LIMIT 1
            """
        )
        return dict(row) if row is not None else None

    def _dataset_fields(self, dataset_id: int) -> dict[str, str]:
        rows = self.metadata.query_rows(
            "SELECT name FROM semantic_fields WHERE dataset_id = ? ORDER BY position, field_id",
            [dataset_id],
        )
        return {str(row["name"]): str(row["name"]) for row in rows}

    def _resolved_physical_dimension(self, dimension_ref: str) -> ResolvedSemanticObject:
        now = ""
        physical_name = dimension_ref.removeprefix("dimension.")
        return ResolvedSemanticObject(
            object_kind="dimension",
            object_id=dimension_ref,
            ref=dimension_ref,
            semantic_object={
                "header": {
                    "dimension_ref": dimension_ref,
                    "display_name": physical_name.replace("_", " ").title(),
                },
                "payload": {
                    "physical_name": physical_name,
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "open",
                    },
                },
            },
            status="published",
            revision=1,
            created_at=now,
            updated_at=now,
        )


class _StubResolver:
    """Minimal resolver stub so SemanticRuntimeRepository.resolver is not None."""

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Semantic ref not found: {semantic_ref}")

    def resolve_metric(self, metric_name: str) -> Any:
        return None

    def resolve_entity(self, entity_name: str) -> Any:
        return None
