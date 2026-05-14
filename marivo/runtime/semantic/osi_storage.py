"""OSI <-> storage row mapping functions.

Bidirectional conversion between OSI Pydantic models and the dict format
expected by / returned from the SQLite metadata store.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from marivo.contracts.generated import (
    Dataset,
    Field,
    Metric,
    Relationship,
    SemanticModel,
)
from marivo.contracts.generated.osi import (
    MarivoDatasetExtension as OsiMarivoDatasetExtension,
)
from marivo.contracts.generated.osi import (
    MarivoMetricExtension as OsiMarivoMetricExtension,
)
from marivo.contracts.semantic_extensions import (
    MarivoDatasetExtension,
    MarivoMetricExtension,
)
from marivo.core.semantic.extensions import (
    OsiCustomExtensionLike,
    extract_marivo_extension,
)


def _ai_context_to_json(value: Any) -> str | None:
    """Serialize AI context values from generated or legacy models."""
    if value is None:
        return None
    if hasattr(value, "root"):
        value = value.root
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    return json.dumps(value)


def _ai_context_from_json(raw: Any) -> Any:
    """Deserialize AI context JSON into a shape accepted by generated models."""
    if raw is None:
        return None
    return json.loads(raw)


def build_custom_extensions(
    marivo_ext: BaseModel | None = None,
) -> list[OsiCustomExtensionLike]:
    """Build a custom_extensions list for schema-defined MARIVO extension payloads."""
    from marivo.contracts.generated.osi import (
        MarivoDatasetCustomExtension,
        MarivoMetricCustomExtension,
    )

    result: list[OsiCustomExtensionLike] = []
    if marivo_ext is None:
        return result
    if isinstance(marivo_ext, MarivoDatasetExtension):
        result.append(
            MarivoDatasetCustomExtension(
                vendor_name="MARIVO",
                data=OsiMarivoDatasetExtension.model_validate(
                    marivo_ext.model_dump(exclude_none=True)
                ),
            )
        )
    elif isinstance(marivo_ext, MarivoMetricExtension):
        result.append(
            MarivoMetricCustomExtension(
                vendor_name="MARIVO",
                data=OsiMarivoMetricExtension.model_validate(
                    marivo_ext.model_dump(exclude_none=True)
                ),
            )
        )
    return result


def _ext_to_dicts(extensions: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of CustomExtension Pydantic objects to plain dicts."""
    return [ext.model_dump(exclude_none=True) for ext in extensions]


# ---------------------------------------------------------------------------
# OSI -> storage (write path)
# ---------------------------------------------------------------------------


def model_to_storage(
    model: SemanticModel,
    *,
    owner_user: str | None,
    visibility: str,
) -> dict[str, Any]:
    """Extract fields for a ``semantic_models`` row from an OSI SemanticModel."""
    ai_context = _ai_context_to_json(model.ai_context)
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
    ai_context = _ai_context_to_json(ds.ai_context)
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
    is_dimension = field.dimension is not None
    is_time = field.dimension.is_time if field.dimension else False
    expression = json.dumps(field.expression.model_dump(exclude_none=True))
    ai_context = _ai_context_to_json(field.ai_context)
    return {
        "dataset_id": dataset_id,
        "name": field.name,
        "expression": expression,
        "is_time": 1 if is_time else 0,
        "is_dimension": 1 if is_dimension else 0,
        "label": field.label,
        "description": field.description,
        "ai_context": ai_context,
        "data_type": None,
        "position": position,
    }


def relationship_to_storage(rel: Relationship, model_id: int) -> dict[str, Any]:
    """Extract fields for a ``semantic_relationships`` row."""
    from_columns = json.dumps(rel.from_columns)
    to_columns = json.dumps(rel.to_columns)
    ai_context = _ai_context_to_json(rel.ai_context)
    return {
        "model_id": model_id,
        "name": rel.name,
        "from_dataset": rel.from_,
        "to_dataset": rel.to,
        "from_columns": from_columns,
        "to_columns": to_columns,
        "ai_context": ai_context,
        "cardinality": None,
    }


def metric_to_storage(metric: Metric, model_id: int) -> dict[str, Any]:
    """Extract fields for a ``semantic_metrics`` row."""
    marivo_ext = extract_marivo_extension(metric.custom_extensions, MarivoMetricExtension)
    additive_dimensions = (
        json.dumps(marivo_ext.additive_dimensions)
        if marivo_ext and marivo_ext.additive_dimensions is not None
        else None
    )
    expression = json.dumps(metric.expression.model_dump(exclude_none=True))
    ai_context = _ai_context_to_json(metric.ai_context)
    return {
        "model_id": model_id,
        "name": metric.name,
        "expression": expression,
        "description": metric.description,
        "ai_context": ai_context,
        "additive_dimensions": additive_dimensions,
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
        result["ai_context"] = _ai_context_from_json(row["ai_context"])

    # Fields sub-collection
    fields_raw = row.get("_fields")
    if fields_raw is not None:
        result["fields"] = [_storage_to_field(f) for f in fields_raw]

    return result


def _storage_to_field(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a field dict from a storage row."""
    result: dict[str, Any] = {
        "name": row["name"],
        "expression": json.loads(row["expression"]),
        "custom_extensions": [],
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
        result["ai_context"] = _ai_context_from_json(row["ai_context"])
    return result


def _storage_to_relationship(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a relationship dict from a storage row."""
    result: dict[str, Any] = {
        "name": row["name"],
        "from": row["from_dataset"],
        "to": row["to_dataset"],
        "from_columns": json.loads(row["from_columns"]),
        "to_columns": json.loads(row["to_columns"]),
        "custom_extensions": [],
    }
    if row.get("ai_context") is not None:
        result["ai_context"] = _ai_context_from_json(row["ai_context"])
    return result


def _storage_to_metric(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a metric dict from a storage row."""
    additive_dimensions = (
        json.loads(row["additive_dimensions"])
        if row.get("additive_dimensions") is not None
        else None
    )
    marivo_ext = MarivoMetricExtension(additive_dimensions=additive_dimensions or [])
    result: dict[str, Any] = {
        "name": row["name"],
        "expression": json.loads(row["expression"]),
        "custom_extensions": _ext_to_dicts(build_custom_extensions(marivo_ext)),
    }
    if row.get("description") is not None:
        result["description"] = row["description"]
    if row.get("ai_context") is not None:
        result["ai_context"] = _ai_context_from_json(row["ai_context"])
    return result


def storage_to_model(
    row: dict[str, Any],
    datasets: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble a full OSI-conformant SemanticModel dict from storage rows.

    Re-creates schema-defined custom_extensions only.
    """
    result: dict[str, Any] = {
        "name": row["name"],
        "datasets": datasets,
        "custom_extensions": [],
    }
    if row.get("description") is not None:
        result["description"] = row["description"]
    if row.get("ai_context") is not None:
        result["ai_context"] = _ai_context_from_json(row["ai_context"])
    if relationships:
        result["relationships"] = relationships
    if metrics:
        result["metrics"] = metrics
    return result
