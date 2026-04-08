from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi.testclient import TestClient


def create_legacy_entity(
    client: TestClient,
    *,
    name: str,
    display_name: str,
    keys: Sequence[str],
    description: str = "",
    level: str | None = None,
    join_constraints: dict[str, Any] | None = None,
    upstream_dependencies: Sequence[str] | None = None,
    lineage: Sequence[str] | None = None,
    quality_expectations: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return client.app.state.semantic_service.create_entity(
        name=name,
        display_name=display_name,
        description=description,
        keys=list(keys),
        level=level,
        join_constraints=join_constraints,
        upstream_dependencies=list(upstream_dependencies or []),
        lineage=list(lineage or []),
        quality_expectations=quality_expectations,
        properties=properties,
    )


def publish_legacy_entity(client: TestClient, entity_id: str) -> dict[str, Any]:
    return client.app.state.semantic_service.publish_entity(entity_id)


def update_legacy_entity(
    client: TestClient,
    entity_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return client.app.state.semantic_service.update_entity(entity_id, **kwargs)


def patch_legacy_entity_properties(
    client: TestClient,
    entity_id: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return client.app.state.semantic_service.patch_entity_properties(entity_id, properties)


def create_legacy_metric(
    client: TestClient,
    *,
    name: str,
    display_name: str,
    definition_sql: str,
    dimensions: Sequence[str],
    description: str = "",
    entity_id: str | None = None,
    grain: str | None = None,
    measure_type: str | None = None,
    allowed_dimensions: Sequence[str] | None = None,
    lineage: Sequence[str] | None = None,
    quality_expectations: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
    desired_direction: str | None = None,
) -> dict[str, Any]:
    return client.app.state.semantic_service.create_metric(
        name=name,
        display_name=display_name,
        description=description,
        definition_sql=definition_sql,
        dimensions=list(dimensions),
        entity_id=entity_id,
        grain=grain,
        measure_type=measure_type,
        allowed_dimensions=list(allowed_dimensions) if allowed_dimensions is not None else None,
        lineage=list(lineage or []),
        quality_expectations=quality_expectations,
        properties=properties,
        desired_direction=desired_direction,
    )


def publish_legacy_metric(client: TestClient, metric_id: str) -> dict[str, Any]:
    return client.app.state.semantic_service.publish_metric(metric_id)
