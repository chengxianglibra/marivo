"""Write-time validation for semantic model objects.

Runs on every create/update to enforce OSI and MARIVO constraints before
data is persisted to storage.
"""

from __future__ import annotations

from typing import Any


class SemanticValidationError(Exception):
    """Raised when semantic model validation fails.

    Carries a list of ``{message, path}`` dicts describing each violation.
    """

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        messages = "; ".join(e["message"] for e in errors)
        super().__init__(messages)


def validate_semantic_model(model_data: dict[str, Any]) -> None:
    """Validate a semantic model dict before writing.

    Checks:
    - visibility must be 'public' or 'private'
    - private requires owner_user
    - Relationship from/to must reference existing datasets
    - Metric observed_dataset must reference existing dataset
    - observation_grain fields must exist in observed_dataset
    - additive_dimensions must reference existing fields when present
    """
    errors: list[dict[str, str]] = []

    visibility = model_data.get("visibility", "public")
    if visibility not in ("public", "private"):
        errors.append(
            {
                "message": f"visibility must be 'public' or 'private', got {visibility!r}",
                "path": "visibility",
            }
        )

    if visibility == "private" and not model_data.get("owner_user"):
        errors.append(
            {
                "message": "owner_user is required when visibility is private",
                "path": "owner_user",
            }
        )

    # Build dataset name -> dataset dict lookup
    datasets_list = model_data.get("datasets") or []
    dataset_names = {ds["name"] for ds in datasets_list}
    dataset_by_name: dict[str, dict[str, Any]] = {ds["name"]: ds for ds in datasets_list}

    # Validate relationships
    relationships = model_data.get("relationships") or []
    for rel in relationships:
        rel_errors = _validate_relationship_refs(rel, dataset_names)
        errors.extend(rel_errors)

    # Build field lookup: dataset_name -> {field_name: field_dict}
    fields_by_dataset: dict[str, dict[str, dict[str, Any]]] = {}
    for ds in datasets_list:
        dataset_name = str(ds.get("name") or "<unnamed>")
        source = str(ds.get("source") or "").strip()
        if not source:
            errors.append(
                {
                    "message": "Dataset source is required and must be a non-empty relation FQN",
                    "path": f"datasets[{dataset_name}].source",
                }
            )
        datasource_id = ds.get("datasource_id")
        if not isinstance(datasource_id, str) or not datasource_id.strip():
            errors.append(
                {
                    "message": "Dataset MARIVO extension datasource_id is required",
                    "path": f"datasets[{dataset_name}].custom_extensions.datasource_id",
                }
            )
        fields_by_dataset[ds["name"]] = {}
        for field in ds.get("fields") or []:
            fields_by_dataset[ds["name"]][field["name"]] = field

    # Validate metrics
    metrics = model_data.get("metrics") or []
    for metric in metrics:
        metric_errors = _validate_metric_refs(
            metric, dataset_names, dataset_by_name, fields_by_dataset
        )
        errors.extend(metric_errors)

    if errors:
        raise SemanticValidationError(errors)


def validate_relationship(rel_data: dict[str, Any], datasets: list[dict[str, Any]]) -> None:
    """Validate a single relationship against known datasets."""
    dataset_names = {ds["name"] for ds in datasets}
    errors = _validate_relationship_refs(rel_data, dataset_names)
    if errors:
        raise SemanticValidationError(errors)


def validate_metric(
    metric_data: dict[str, Any],
    datasets: list[dict[str, Any]],
    fields_by_dataset: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Validate a single metric against known datasets and fields."""
    dataset_names = {ds["name"] for ds in datasets}
    dataset_by_name: dict[str, dict[str, Any]] = {ds["name"]: ds for ds in datasets}
    errors = _validate_metric_refs(metric_data, dataset_names, dataset_by_name, fields_by_dataset)
    if errors:
        raise SemanticValidationError(errors)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_relationship_refs(
    rel: dict[str, Any], dataset_names: set[str]
) -> list[dict[str, str]]:
    """Check that relationship from/to reference existing datasets."""
    errors: list[dict[str, str]] = []
    from_ds = rel.get("from") or rel.get("from_dataset")
    to_ds = rel.get("to") or rel.get("to_dataset")
    rel_name = rel.get("name", "<unnamed>")

    if from_ds and from_ds not in dataset_names:
        errors.append(
            {
                "message": f"relationship '{rel_name}' references unknown from_dataset '{from_ds}'",
                "path": f"relationships[{rel_name}].from",
            }
        )
    if to_ds and to_ds not in dataset_names:
        errors.append(
            {
                "message": f"relationship '{rel_name}' references unknown to_dataset '{to_ds}'",
                "path": f"relationships[{rel_name}].to",
            }
        )
    return errors


def _validate_metric_refs(
    metric: dict[str, Any],
    dataset_names: set[str],
    dataset_by_name: dict[str, dict[str, Any]],
    fields_by_dataset: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, str]]:
    """Check metric references to datasets and fields."""
    errors: list[dict[str, str]] = []
    metric_name = metric.get("name", "<unnamed>")

    observed_dataset = metric.get("observed_dataset")
    if observed_dataset and observed_dataset not in dataset_names:
        errors.append(
            {
                "message": f"metric '{metric_name}' references unknown observed_dataset '{observed_dataset}'",
                "path": f"metrics[{metric_name}].observed_dataset",
            }
        )
        return errors  # no point checking field refs if dataset is invalid

    # observation_grain fields must exist in observed_dataset
    observation_grain = metric.get("observation_grain")
    if observation_grain is not None and not isinstance(observation_grain, list):
        errors.append(
            {
                "message": f"metric '{metric_name}' observation_grain must be a list of field names, got {type(observation_grain).__name__}",
                "path": f"metrics[{metric_name}].observation_grain",
            }
        )
        observation_grain = None
    if observation_grain:
        if not observed_dataset:
            return errors
        ds_fields = fields_by_dataset.get(observed_dataset, {})
        for grain_field in observation_grain:
            if grain_field not in ds_fields:
                errors.append(
                    {
                        "message": f"metric '{metric_name}' observation_grain references unknown field '{grain_field}' in dataset '{observed_dataset}'",
                        "path": f"metrics[{metric_name}].observation_grain",
                    }
                )

    # additive_dimensions must reference existing fields when present.
    # Support both the legacy nested additivity payload and the cutover top-level field.
    additive_dimensions = metric.get("additive_dimensions")
    additivity = metric.get("additivity")
    if additive_dimensions is None and isinstance(additivity, dict):
        additive_dimensions = additivity.get("additive_dimensions")
    if additive_dimensions is not None and not isinstance(additive_dimensions, list):
        errors.append(
            {
                "message": f"metric '{metric_name}' additive_dimensions must be a list of field names, got {type(additive_dimensions).__name__}",
                "path": f"metrics[{metric_name}].additive_dimensions",
            }
        )
        additive_dimensions = None
    if additive_dimensions:
        if observed_dataset:
            ds_fields = fields_by_dataset.get(observed_dataset, {})
            for dim in additive_dimensions:
                if dim not in ds_fields:
                    errors.append(
                        {
                            "message": f"metric '{metric_name}' additive_dimension '{dim}' does not exist in dataset '{observed_dataset}'",
                            "path": f"metrics[{metric_name}].additive_dimensions",
                        }
                    )
        else:
            all_fields = {
                field_name
                for dataset_fields in fields_by_dataset.values()
                for field_name in dataset_fields
            }
            for dim in additive_dimensions:
                if dim not in all_fields:
                    errors.append(
                        {
                            "message": f"metric '{metric_name}' additive_dimension '{dim}' does not exist in the semantic model",
                            "path": f"metrics[{metric_name}].additive_dimensions",
                        }
                    )

    return errors
