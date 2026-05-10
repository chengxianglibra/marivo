"""OSI <-> storage row mapping functions.

Bidirectional conversion between OSI Pydantic models and the dict format
expected by / returned from the SQLite metadata store.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from marivo.core.semantic.extensions import (
    OsiCustomExtensionLike,
    extract_marivo_extension,
)
from marivo.transports.http.models.marivo_extensions import (
    MarivoDatasetExtension,
    MarivoFieldExtension,
    MarivoMetricExtension,
    MarivoRelationshipExtension,
    MarivoSemanticModelExtension,
)
from marivo.transports.http.models.osi import (
    Dataset,
    Field,
    Metric,
    Relationship,
    SemanticModel,
)


def build_custom_extensions(
    marivo_ext: BaseModel | None = None,
    *others: OsiCustomExtensionLike,
) -> list[OsiCustomExtensionLike]:
    """Build a custom_extensions list from a MARIVO extension model and optional other extensions."""
    from marivo.transports.http.models.osi import CustomExtension

    result: list[OsiCustomExtensionLike] = []
    if marivo_ext is not None:
        result.append(
            CustomExtension(
                vendor_name="MARIVO",
                data=marivo_ext.model_dump_json(exclude_none=True),
            )
        )
    result.extend(others)
    return result


def _ext_to_dicts(extensions: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of CustomExtension Pydantic objects to plain dicts."""
    return [ext.model_dump(exclude_none=True) for ext in extensions]


# ---------------------------------------------------------------------------
# OSI -> storage (write path)
# ---------------------------------------------------------------------------


def model_to_storage(model: SemanticModel) -> dict[str, Any]:
    """Extract fields for a ``semantic_models`` row from an OSI SemanticModel."""
    marivo_ext = extract_marivo_extension(model.custom_extensions, MarivoSemanticModelExtension)
    visibility = marivo_ext.visibility if marivo_ext else "public"
    owner_user = marivo_ext.owner_user if marivo_ext else None
    ai_context = json.dumps(model.ai_context.root) if model.ai_context is not None else None
    return {
        "name": model.name,
        "description": model.description,
        "ai_context": ai_context,
        "visibility": visibility,
        "owner_user": owner_user,
    }


def dataset_to_storage(ds: Dataset, model_id: int) -> dict[str, Any]:
    """Extract fields for a ``semantic_datasets`` row."""
    marivo_ext = extract_marivo_extension(ds.custom_extensions, MarivoDatasetExtension)
    datasource_id = marivo_ext.datasource_id if marivo_ext else None
    primary_key = json.dumps(ds.primary_key) if ds.primary_key is not None else None
    unique_keys = json.dumps(ds.unique_keys) if ds.unique_keys is not None else None
    ai_context = json.dumps(ds.ai_context.root) if ds.ai_context is not None else None
    return {
        "model_id": model_id,
        "name": ds.name,
        "source": ds.source,
        "primary_key": primary_key,
        "unique_keys": unique_keys,
        "description": ds.description,
        "ai_context": ai_context,
        "datasource_id": datasource_id,
    }


def field_to_storage(field: Field, dataset_id: int, position: int) -> dict[str, Any]:
    """Extract fields for a ``semantic_fields`` row."""
    marivo_ext = extract_marivo_extension(field.custom_extensions, MarivoFieldExtension)
    data_type = marivo_ext.data_type if marivo_ext else None
    is_dimension = field.dimension is not None
    is_time = field.dimension.is_time if field.dimension else False
    expression = json.dumps(field.expression.model_dump(exclude_none=True))
    ai_context = json.dumps(field.ai_context.root) if field.ai_context is not None else None
    return {
        "dataset_id": dataset_id,
        "name": field.name,
        "expression": expression,
        "is_time": 1 if is_time else 0,
        "is_dimension": 1 if is_dimension else 0,
        "label": field.label,
        "description": field.description,
        "ai_context": ai_context,
        "data_type": data_type,
        "position": position,
    }


def relationship_to_storage(rel: Relationship, model_id: int) -> dict[str, Any]:
    """Extract fields for a ``semantic_relationships`` row."""
    marivo_ext = extract_marivo_extension(rel.custom_extensions, MarivoRelationshipExtension)
    cardinality = marivo_ext.cardinality if marivo_ext else None
    from_columns = json.dumps(rel.from_columns)
    to_columns = json.dumps(rel.to_columns)
    ai_context = json.dumps(rel.ai_context.root) if rel.ai_context is not None else None
    return {
        "model_id": model_id,
        "name": rel.name,
        "from_dataset": rel.from_,
        "to_dataset": rel.to,
        "from_columns": from_columns,
        "to_columns": to_columns,
        "ai_context": ai_context,
        "cardinality": cardinality,
    }


def metric_to_storage(metric: Metric, model_id: int) -> dict[str, Any]:
    """Extract fields for a ``semantic_metrics`` row."""
    marivo_ext = extract_marivo_extension(metric.custom_extensions, MarivoMetricExtension)
    observed_dataset = marivo_ext.observed_dataset if marivo_ext else None
    observation_grain = (
        json.dumps(marivo_ext.observation_grain)
        if marivo_ext and marivo_ext.observation_grain is not None
        else None
    )
    primary_time_field = marivo_ext.primary_time_field if marivo_ext else None
    additivity = (
        json.dumps(marivo_ext.additivity.model_dump(exclude_none=True))
        if marivo_ext and marivo_ext.additivity is not None
        else None
    )
    filters = (
        json.dumps([f.model_dump(exclude_none=True) for f in marivo_ext.filters])
        if marivo_ext and marivo_ext.filters is not None
        else None
    )
    expression = json.dumps(metric.expression.model_dump(exclude_none=True))
    ai_context = json.dumps(metric.ai_context.root) if metric.ai_context is not None else None
    return {
        "model_id": model_id,
        "name": metric.name,
        "expression": expression,
        "description": metric.description,
        "ai_context": ai_context,
        "observed_dataset": observed_dataset,
        "observation_grain": observation_grain,
        "primary_time_field": primary_time_field,
        "additivity": additivity,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Storage -> OSI (read path)
# ---------------------------------------------------------------------------


def _storage_to_dataset(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a dataset dict from a storage row."""
    marivo_ext = MarivoDatasetExtension(
        datasource_id=row.get("datasource_id"),
    )
    result: dict[str, Any] = {
        "name": row["name"],
        "source": row["source"],
        "custom_extensions": _ext_to_dicts(build_custom_extensions(marivo_ext)),
    }
    if row.get("primary_key") is not None:
        result["primary_key"] = json.loads(row["primary_key"])
    if row.get("unique_keys") is not None:
        result["unique_keys"] = json.loads(row["unique_keys"])
    if row.get("description") is not None:
        result["description"] = row["description"]
    if row.get("ai_context") is not None:
        result["ai_context"] = json.loads(row["ai_context"])

    # Fields sub-collection
    fields_raw = row.get("_fields")
    if fields_raw is not None:
        result["fields"] = [_storage_to_field(f) for f in fields_raw]

    return result


def _storage_to_field(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a field dict from a storage row."""
    marivo_ext = MarivoFieldExtension(data_type=row.get("data_type"))
    result: dict[str, Any] = {
        "name": row["name"],
        "expression": json.loads(row["expression"]),
        "custom_extensions": _ext_to_dicts(build_custom_extensions(marivo_ext)),
    }
    is_time = bool(row.get("is_time", 0))
    is_dimension = bool(row.get("is_dimension", 0))
    if is_time:
        result["dimension"] = {"is_time": True}
    elif is_dimension:
        result["dimension"] = {"is_time": False}
    if row.get("label") is not None:
        result["label"] = row["label"]
    if row.get("description") is not None:
        result["description"] = row["description"]
    if row.get("ai_context") is not None:
        result["ai_context"] = json.loads(row["ai_context"])
    return result


def _storage_to_relationship(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a relationship dict from a storage row."""
    marivo_ext = MarivoRelationshipExtension(cardinality=row.get("cardinality"))
    result: dict[str, Any] = {
        "name": row["name"],
        "from": row["from_dataset"],
        "to": row["to_dataset"],
        "from_columns": json.loads(row["from_columns"]),
        "to_columns": json.loads(row["to_columns"]),
        "custom_extensions": _ext_to_dicts(build_custom_extensions(marivo_ext)),
    }
    if row.get("ai_context") is not None:
        result["ai_context"] = json.loads(row["ai_context"])
    return result


def _storage_to_metric(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a metric dict from a storage row."""
    additivity_data = json.loads(row["additivity"]) if row.get("additivity") is not None else None
    filters_data = json.loads(row["filters"]) if row.get("filters") is not None else None
    observation_grain_data = (
        json.loads(row["observation_grain"]) if row.get("observation_grain") is not None else None
    )
    marivo_ext = MarivoMetricExtension.model_validate(
        {
            "observed_dataset": row.get("observed_dataset"),
            "observation_grain": observation_grain_data,
            "primary_time_field": row.get("primary_time_field"),
            "additivity": additivity_data,
            "filters": filters_data,
        }
    )
    result: dict[str, Any] = {
        "name": row["name"],
        "expression": json.loads(row["expression"]),
        "custom_extensions": _ext_to_dicts(build_custom_extensions(marivo_ext)),
    }
    if row.get("description") is not None:
        result["description"] = row["description"]
    if row.get("ai_context") is not None:
        result["ai_context"] = json.loads(row["ai_context"])
    return result


def storage_to_model(
    row: dict[str, Any],
    datasets: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    revision: int | None = None,
) -> dict[str, Any]:
    """Assemble a full OSI-conformant SemanticModel dict from storage rows.

    Re-creates custom_extensions with MARIVO vendor data.
    """
    marivo_ext = MarivoSemanticModelExtension(
        visibility=row.get("visibility", "public"),
        owner_user=row.get("owner_user"),
        revision=revision,
    )
    result: dict[str, Any] = {
        "name": row["name"],
        "datasets": datasets,
        "custom_extensions": _ext_to_dicts(build_custom_extensions(marivo_ext)),
    }
    if row.get("description") is not None:
        result["description"] = row["description"]
    if row.get("ai_context") is not None:
        result["ai_context"] = json.loads(row["ai_context"])
    if relationships:
        result["relationships"] = relationships
    if metrics:
        result["metrics"] = metrics
    return result
