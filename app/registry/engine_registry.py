from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.execution.capabilities import (
    EngineCapabilityProfile,
    build_engine_capability_profile,
)
from app.registry.common import now_iso
from app.registry.factories import build_analytics_engine, validate_engine_type
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


def _build_intrinsic_capabilities(engine_type: str) -> dict[str, Any]:
    profile = build_engine_capability_profile(engine_type).to_dict()
    return {
        "materialization_support": profile["materialization_support"],
        "performance_class": profile["performance_class"],
        "federation_support": profile["federation_support"],
    }


def _normalize_default_namespace(
    engine_type: str,
    connection: dict[str, Any],
    default_namespace: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = {
        "catalog": None,
        "schema": None,
    }
    if engine_type == "trino":
        normalized["catalog"] = connection.get("catalog")
        normalized["schema"] = connection.get("schema")
    if default_namespace:
        normalized.update(
            {
                "catalog": default_namespace.get("catalog"),
                "schema": default_namespace.get("schema"),
            }
        )
    if engine_type == "duckdb" and (
        normalized["catalog"] is not None or normalized["schema"] is not None
    ):
        raise ValueError("duckdb default_namespace must be null for catalog and schema")
    return normalized


def _normalize_deployment_capabilities(
    deployment_capabilities: dict[str, Any] | None,
) -> dict[str, Any]:
    if deployment_capabilities is None:
        return {}

    normalized: dict[str, Any] = {}
    for key, value in deployment_capabilities.items():
        if key in {"supported_sql_features", "supported_step_types", "policy_support"}:
            normalized[key] = list(value)
        elif key == "metadata" and value is not None:
            normalized[key] = dict(value)
        else:
            normalized[key] = value
    return normalized


def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    payload = policy or {}
    return {
        "allowed_step_types": list(payload.get("allowed_step_types", [])),
        "required_policy_support": list(payload.get("required_policy_support", [])),
    }


class EngineRegistry:
    """Engine registry and analytics factory boundary."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def register_engine(
        self,
        engine_type: str,
        display_name: str,
        connection: dict[str, Any],
        default_namespace: dict[str, Any] | None = None,
        deployment_capabilities: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_engine_type(engine_type)
        engine_id = f"eng_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO engines (
                engine_id,
                engine_type,
                display_name,
                connection_json,
                default_namespace_json,
                intrinsic_capabilities_json,
                deployment_capabilities_json,
                policy_json,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                engine_id,
                engine_type,
                display_name,
                json.dumps(connection),
                json.dumps(
                    _normalize_default_namespace(engine_type, connection, default_namespace)
                ),
                json.dumps(_build_intrinsic_capabilities(engine_type)),
                json.dumps(_normalize_deployment_capabilities(deployment_capabilities)),
                json.dumps(_normalize_policy(policy)),
                now,
                now,
            ],
        )
        return self.get_engine(engine_id)

    def get_engine(self, engine_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM engines WHERE engine_id = ?", [engine_id])
        if row is None:
            raise KeyError(f"Unknown engine: {engine_id}")
        return self._row_to_engine(row)

    def list_engines(self) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows("SELECT * FROM engines ORDER BY created_at")
        return [self._row_to_engine(row) for row in rows]

    def ensure_engine(
        self,
        engine_type: str,
        display_name: str,
        connection: dict[str, Any],
        default_namespace: dict[str, Any] | None = None,
        deployment_capabilities: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_engine_type(engine_type)
        existing = self.metadata.query_one(
            "SELECT * FROM engines WHERE display_name = ?",
            [display_name],
        )
        if existing is None:
            return self.register_engine(
                engine_type,
                display_name,
                connection,
                default_namespace=default_namespace,
                deployment_capabilities=deployment_capabilities,
                policy=policy,
            )

        self.metadata.execute(
            """
            UPDATE engines
            SET engine_type = ?, connection_json = ?, default_namespace_json = ?,
                intrinsic_capabilities_json = ?, deployment_capabilities_json = ?,
                policy_json = ?, updated_at = ?
            WHERE engine_id = ?
            """,
            [
                engine_type,
                json.dumps(connection),
                json.dumps(
                    _normalize_default_namespace(engine_type, connection, default_namespace)
                ),
                json.dumps(_build_intrinsic_capabilities(engine_type)),
                json.dumps(_normalize_deployment_capabilities(deployment_capabilities)),
                json.dumps(_normalize_policy(policy)),
                now_iso(),
                existing["engine_id"],
            ],
        )
        return self.get_engine(str(existing["engine_id"]))

    def build_analytics_engine(self, engine_id: str) -> AnalyticsEngine:
        engine = self.get_engine(engine_id)
        return build_analytics_engine(engine["engine_type"], engine["connection"])

    def get_capability_profile(self, engine_id: str) -> EngineCapabilityProfile:
        engine = self.get_engine(engine_id)
        return build_engine_capability_profile(
            engine["engine_type"],
            engine["deployment_capabilities"],
        )

    def _row_to_engine(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "engine_id": row["engine_id"],
            "engine_type": row["engine_type"],
            "display_name": row["display_name"],
            "connection": json.loads(str(row["connection_json"])),
            "default_namespace": json.loads(str(row["default_namespace_json"])),
            "intrinsic_capabilities": json.loads(str(row["intrinsic_capabilities_json"])),
            "deployment_capabilities": json.loads(str(row["deployment_capabilities_json"])),
            "policy": json.loads(str(row["policy_json"])),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
