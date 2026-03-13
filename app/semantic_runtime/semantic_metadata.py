from __future__ import annotations

import json
from typing import Any


def metric_runtime_metadata(
    *,
    grain: str | None,
    measure_type: str | None,
    allowed_dimensions_json: str | None,
    lineage_json: str | None,
    quality_expectations_json: str | None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "grain": grain,
        "measure_type": measure_type,
        "allowed_dimensions": list(json.loads(allowed_dimensions_json or "[]") or dimensions or []),
        "lineage": list(json.loads(lineage_json or "[]")),
        "quality_expectations": dict(json.loads(quality_expectations_json or "{}")),
    }


def entity_runtime_metadata(
    *,
    level: str | None,
    join_constraints_json: str | None,
    upstream_dependencies_json: str | None,
    lineage_json: str | None,
    quality_expectations_json: str | None,
) -> dict[str, Any]:
    return {
        "level": level,
        "join_constraints": dict(json.loads(join_constraints_json or "{}")),
        "upstream_dependencies": list(json.loads(upstream_dependencies_json or "[]")),
        "lineage": list(json.loads(lineage_json or "[]")),
        "quality_expectations": dict(json.loads(quality_expectations_json or "{}")),
    }
