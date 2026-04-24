from __future__ import annotations

import json
from dataclasses import dataclass
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


@dataclass(slots=True)
class EngineValidationResult:
    is_valid: bool
    readiness_status: str
    failure_code: str | None = None

    def to_dict(self, *, engine_id: str) -> dict[str, Any]:
        return {
            "engine_id": engine_id,
            "is_valid": self.is_valid,
            "readiness_status": self.readiness_status,
            "failure_code": self.failure_code,
        }


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


def _normalize_auth(auth: dict[str, Any] | None) -> dict[str, Any]:
    payload = auth if isinstance(auth, dict) else {}
    mode = payload.get("mode")
    normalized: dict[str, Any] = {
        "mode": mode if mode in {"none", "username_only"} else "none",
    }
    if normalized["mode"] == "username_only":
        username_source = payload.get("username_source")
        if username_source in {"session_user", "fixed"}:
            normalized["username_source"] = username_source
        fallback_username = payload.get("fallback_username")
        if isinstance(fallback_username, str):
            normalized["fallback_username"] = fallback_username
    return normalized


class EngineRegistry:
    """Engine registry and analytics factory boundary."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def register_engine(
        self,
        engine_type: str,
        display_name: str,
        connection: dict[str, Any],
        auth: dict[str, Any] | None = None,
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
                auth_json,
                default_namespace_json,
                intrinsic_capabilities_json,
                deployment_capabilities_json,
                policy_json,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                engine_id,
                engine_type,
                display_name,
                json.dumps(connection),
                json.dumps(_normalize_auth(auth)),
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

    def get_engine(self, engine_id: str, *, include_mappings: bool = True) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM engines WHERE engine_id = ?", [engine_id])
        if row is None:
            raise KeyError(f"Unknown engine: {engine_id}")
        return self._row_to_engine(row, include_mappings=include_mappings)

    def list_engines(self) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows("SELECT * FROM engines ORDER BY created_at")
        return [self._row_to_engine(row, include_mappings=True) for row in rows]

    def ensure_engine(
        self,
        engine_type: str,
        display_name: str,
        connection: dict[str, Any],
        auth: dict[str, Any] | None = None,
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
                auth=auth,
                default_namespace=default_namespace,
                deployment_capabilities=deployment_capabilities,
                policy=policy,
            )

        self.metadata.execute(
            """
            UPDATE engines
            SET engine_type = ?, connection_json = ?, auth_json = ?, default_namespace_json = ?,
                intrinsic_capabilities_json = ?, deployment_capabilities_json = ?,
                policy_json = ?, updated_at = ?
            WHERE engine_id = ?
            """,
            [
                engine_type,
                json.dumps(connection),
                json.dumps(_normalize_auth(auth)),
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

    def validate_engine(self, engine_id: str) -> dict[str, Any]:
        engine = self.get_engine(engine_id)
        return self.evaluate_engine(engine).to_dict(engine_id=engine_id)

    def get_engine_readiness(self, engine_id: str) -> dict[str, Any]:
        validation = self.validate_engine(engine_id)
        return {
            "engine_id": engine_id,
            "readiness_status": validation["readiness_status"],
            "failure_code": validation["failure_code"],
        }

    def evaluate_engine(self, engine: dict[str, Any]) -> EngineValidationResult:
        if engine["status"] != "active":
            return EngineValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="engine_inactive",
            )

        engine_type = engine["engine_type"]
        try:
            validate_engine_type(engine_type)
        except ValueError:
            return EngineValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="engine_invalid_type",
            )

        connection = engine.get("connection")
        if not isinstance(connection, dict):
            return EngineValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="engine_invalid_connection",
            )

        try:
            build_analytics_engine(engine_type, connection)
        except (KeyError, TypeError, ValueError):
            return EngineValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="engine_invalid_connection",
            )

        namespace = engine.get("default_namespace")
        if not self._is_valid_default_namespace(engine_type, namespace):
            return EngineValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="engine_invalid_namespace",
            )

        deployment_capabilities = engine.get("deployment_capabilities")
        if not self._is_valid_deployment_capabilities(deployment_capabilities):
            return EngineValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="engine_invalid_deployment_capabilities",
            )

        policy = engine.get("policy")
        if not self._is_valid_policy(policy):
            return EngineValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="engine_invalid_policy",
            )

        return EngineValidationResult(
            is_valid=True,
            readiness_status="ready",
        )

    def _row_to_engine(self, row: dict[str, Any], *, include_mappings: bool) -> dict[str, Any]:
        engine_type = str(row["engine_type"])
        raw_connection = json.loads(str(row["connection_json"]))
        connection = raw_connection if isinstance(raw_connection, dict) else {}

        raw_auth = row.get("auth_json")
        parsed_auth: dict[str, Any] | None = None
        if raw_auth:
            try:
                auth_payload = json.loads(str(raw_auth))
            except (TypeError, ValueError):
                auth_payload = None
            if isinstance(auth_payload, dict):
                parsed_auth = auth_payload
        auth = _normalize_auth(parsed_auth)

        raw_default_namespace = json.loads(str(row["default_namespace_json"]))
        default_namespace = raw_default_namespace if isinstance(raw_default_namespace, dict) else {}
        catalog = default_namespace.get("catalog")
        schema = default_namespace.get("schema")
        normalized_default_namespace = {
            "catalog": catalog if isinstance(catalog, str) or catalog is None else None,
            "schema": schema if isinstance(schema, str) or schema is None else None,
        }

        raw_intrinsic_capabilities = json.loads(str(row["intrinsic_capabilities_json"]))
        intrinsic_capabilities = (
            raw_intrinsic_capabilities
            if isinstance(raw_intrinsic_capabilities, dict)
            else _build_intrinsic_capabilities(engine_type)
        )
        for key, value in _build_intrinsic_capabilities(engine_type).items():
            intrinsic_capabilities.setdefault(key, value)

        raw_deployment_capabilities = json.loads(str(row["deployment_capabilities_json"]))
        deployment_capabilities = (
            raw_deployment_capabilities if isinstance(raw_deployment_capabilities, dict) else {}
        )

        raw_policy = json.loads(str(row["policy_json"]))
        policy = _normalize_policy(raw_policy if isinstance(raw_policy, dict) else None)
        engine = {
            "engine_id": row["engine_id"],
            "engine_type": engine_type,
            "display_name": row["display_name"],
            "connection": connection,
            "auth": auth,
            "default_namespace": normalized_default_namespace,
            "intrinsic_capabilities": intrinsic_capabilities,
            "deployment_capabilities": deployment_capabilities,
            "policy": policy,
            "status": row["status"],
            "mappings": (
                self._list_mapping_summaries(str(row["engine_id"])) if include_mappings else []
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        validation = self.evaluate_engine(engine)
        engine["readiness_status"] = validation.readiness_status
        engine["failure_code"] = validation.failure_code
        return engine

    def _list_mapping_summaries(self, engine_id: str) -> list[dict[str, Any]]:
        from app.registry.mapping_registry import list_mapping_summaries

        return list_mapping_summaries(self.metadata, engine_id=engine_id)

    def _is_valid_default_namespace(self, engine_type: str, namespace: Any) -> bool:
        if not isinstance(namespace, dict):
            return False

        catalog = namespace.get("catalog")
        schema = namespace.get("schema")
        if engine_type == "duckdb":
            return catalog is None and schema is None

        if engine_type != "trino":
            return False
        return self._is_nullable_non_blank_str(catalog) and self._is_nullable_non_blank_str(schema)

    def _is_valid_deployment_capabilities(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False

        supported_step_types = payload.get("supported_step_types")
        if supported_step_types is not None and not self._is_valid_string_list(
            supported_step_types
        ):
            return False

        min_staleness_minutes = payload.get("min_staleness_minutes")
        return min_staleness_minutes is None or (
            isinstance(min_staleness_minutes, int) and min_staleness_minutes >= 0
        )

    def _is_valid_policy(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False

        allowed_step_types = payload.get("allowed_step_types")
        if allowed_step_types is None or not self._is_valid_string_list(allowed_step_types):
            return False

        required_policy_support = payload.get("required_policy_support")
        return required_policy_support is not None and self._is_valid_string_list(
            required_policy_support
        )

    def _is_valid_string_list(self, value: Any) -> bool:
        if not isinstance(value, list):
            return False
        return all(isinstance(item, str) and item.strip() for item in value)

    def _is_nullable_non_blank_str(self, value: Any) -> bool:
        return value is None or (isinstance(value, str) and bool(value.strip()))
