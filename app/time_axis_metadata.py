from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from app.storage.metadata import MetadataStore

TimeGrain = Literal["day", "hour"]

PHASE1_TIMEZONE_STRATEGY = "session_consistent_naive"
PHASE1_TIMEZONE_NOTE = (
    "Phase 1 assumes session-consistent naive timestamps only. Hour-grain "
    "time_scope boundaries and metadata-resolved timestamp columns must not "
    "encode timezone offsets."
)


@dataclass(slots=True)
class TimeAxisMetadataContext:
    entity_time_capabilities: dict[str, Any] | None = None
    source_time_capabilities: dict[str, Any] | None = None
    available_columns: list[str] = field(default_factory=list)
    timezone_strategy: str = PHASE1_TIMEZONE_STRATEGY
    timezone_note: str = PHASE1_TIMEZONE_NOTE
    has_time_binding: bool = False


def normalize_time_capabilities(
    payload: Mapping[str, Any] | None,
    *,
    label: str = "time_capabilities",
) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be an object")

    analysis_time = payload.get("analysis_time")
    partition_time = payload.get("partition_time")
    if analysis_time is not None and not isinstance(analysis_time, Mapping):
        raise ValueError(f"{label}.analysis_time must be an object")
    if partition_time is not None and not isinstance(partition_time, Mapping):
        raise ValueError(f"{label}.partition_time must be an object")

    normalized_analysis = _normalize_analysis_time_section(
        analysis_time, label=f"{label}.analysis_time"
    )
    normalized_partition = _normalize_partition_time_section(
        partition_time, label=f"{label}.partition_time"
    )

    default_compare_grain = _optional_str(payload.get("default_compare_grain"))
    if default_compare_grain is not None and default_compare_grain not in {"day", "hour"}:
        raise ValueError(f"{label}.default_compare_grain must be 'day' or 'hour'")

    normalized: dict[str, Any] = {}
    if normalized_analysis:
        normalized["analysis_time"] = normalized_analysis
    if normalized_partition:
        normalized["partition_time"] = normalized_partition
    if default_compare_grain is not None:
        normalized["default_compare_grain"] = default_compare_grain
    return normalized or None


class TimeAxisMetadataProvider:
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def load_available_columns(self, table_name: str) -> list[str]:
        table_row = self._find_source_table_object(table_name)
        if table_row is None:
            return []
        return self._load_table_columns(table_row["object_id"])

    def load_for_windowed_query(
        self,
        *,
        table_name: str,
        metric_name: str | None = None,
    ) -> TimeAxisMetadataContext:
        table_row = self._find_source_table_object(table_name)
        available_columns: list[str] = []
        if table_row is not None:
            available_columns = self._load_table_columns(table_row["object_id"])
        if metric_name:
            return self._load_metric_binding_context(
                table_name=table_name,
                metric_name=metric_name,
                available_columns=available_columns,
            )
        return self._load_table_binding_context(
            table_name=table_name,
            available_columns=available_columns,
        )

    def _find_metric_entity(self, metric_name: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            """
            SELECT m.metric_ref, m.primary_time_ref, e.entity_contract_id, e.entity_ref, e.primary_time_ref AS entity_primary_time_ref
            FROM semantic_metric_contracts m
            JOIN semantic_entity_contracts e ON e.entity_ref = m.observed_entity_ref
            WHERE m.metric_ref = ? AND m.status = 'published' AND e.status = 'published'
            """,
            [f"metric.{metric_name}"],
        )
        if row is None:
            return None
        return {
            "metric_ref": row["metric_ref"],
            "primary_time_ref": row["primary_time_ref"],
            "entity_contract_id": row["entity_contract_id"],
            "entity_ref": row["entity_ref"],
            "entity_primary_time_ref": row["entity_primary_time_ref"],
        }

    def _find_source_table_object(self, table_name: str) -> dict[str, Any] | None:
        short_name = table_name.split(".")[-1]
        row = self.metadata.query_one(
            """
            SELECT object_id, fqn, native_name, properties_json
            FROM source_objects
            WHERE object_type = 'table' AND (fqn = ? OR native_name = ?)
            ORDER BY CASE WHEN fqn = ? THEN 0 ELSE 1 END, updated_at DESC
            """,
            [table_name, short_name, table_name],
        )
        return dict(row) if row is not None else None

    def _load_table_columns(self, table_object_id: str) -> list[str]:
        rows = self.metadata.query_rows(
            """
            SELECT native_name
            FROM source_objects
            WHERE parent_id = ? AND object_type = 'column'
            ORDER BY native_name
            """,
            [table_object_id],
        )
        return [str(row["native_name"]) for row in rows if row.get("native_name")]

    def _load_metric_binding_context(
        self,
        *,
        table_name: str,
        metric_name: str,
        available_columns: list[str],
    ) -> TimeAxisMetadataContext:
        metric_row = self._find_metric_entity(metric_name)
        if metric_row is None:
            return TimeAxisMetadataContext(available_columns=available_columns)

        metric_ref = str(metric_row["metric_ref"])
        metric_primary_time_ref = _optional_str(metric_row.get("primary_time_ref"))
        metric_bindings = self._published_bindings_for_object_ref(metric_ref)
        binding_context = self._select_binding_time_context(
            bindings=metric_bindings,
            table_name=table_name,
            primary_time_ref=metric_primary_time_ref,
            ambiguity_label=f"metric {metric_ref}",
        )
        if binding_context is not None:
            return TimeAxisMetadataContext(
                entity_time_capabilities=binding_context["analysis_caps"],
                source_time_capabilities=binding_context["partition_caps"],
                available_columns=available_columns,
                has_time_binding=True,
            )

        entity_ref = _optional_str(metric_row.get("entity_ref"))
        entity_primary_time_ref = _optional_str(metric_row.get("entity_primary_time_ref"))
        if entity_ref is None:
            raise ValueError(
                f"Metric '{metric_ref}' does not expose an observed entity for time binding"
            )
        entity_bindings = self._published_bindings_for_object_ref(entity_ref)
        binding_context = self._select_binding_time_context(
            bindings=entity_bindings,
            table_name=table_name,
            primary_time_ref=entity_primary_time_ref,
            ambiguity_label=f"entity {entity_ref}",
        )
        if binding_context is None:
            raise ValueError(
                f"No published time binding matched {table_name} for metric {metric_ref}"
            )
        return TimeAxisMetadataContext(
            entity_time_capabilities=binding_context["analysis_caps"],
            source_time_capabilities=binding_context["partition_caps"],
            available_columns=available_columns,
            has_time_binding=True,
        )

    def _load_table_binding_context(
        self,
        *,
        table_name: str,
        available_columns: list[str],
    ) -> TimeAxisMetadataContext:
        bindings = self._published_bindings_matching_table(table_name)
        candidates = []
        for binding in bindings:
            primary_time_ref = self._primary_time_ref_for_binding(binding)
            if primary_time_ref is None:
                continue
            derived = self._derive_binding_time_context(binding, primary_time_ref=primary_time_ref)
            if derived is not None:
                candidates.append(derived)
        if not candidates:
            raise ValueError(f"No published time binding matched {table_name}")
        if len(candidates) > 1:
            refs = ", ".join(sorted({str(item["binding_ref"]) for item in candidates}))
            raise ValueError(
                f"Table {table_name} has multiple published time bindings ({refs}); provide an explicit time_axis override"
            )
        selected = candidates[0]
        return TimeAxisMetadataContext(
            entity_time_capabilities=selected["analysis_caps"],
            source_time_capabilities=selected["partition_caps"],
            available_columns=available_columns,
            has_time_binding=True,
        )

    def _select_binding_time_context(
        self,
        *,
        bindings: list[dict[str, Any]],
        table_name: str,
        primary_time_ref: str | None,
        ambiguity_label: str,
    ) -> dict[str, Any] | None:
        matching = [
            binding for binding in bindings if self._binding_matches_table(binding, table_name)
        ]
        candidates: list[dict[str, Any]] = []
        for binding in matching:
            derived = self._derive_binding_time_context(binding, primary_time_ref=primary_time_ref)
            if derived is not None:
                candidates.append(derived)
        if not candidates:
            return None
        if len(candidates) > 1:
            refs = ", ".join(sorted({str(item["binding_ref"]) for item in candidates}))
            raise ValueError(f"{ambiguity_label} matched multiple published time bindings ({refs})")
        return candidates[0]

    def _published_bindings_for_object_ref(self, object_ref: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT binding_id
            FROM typed_bindings
            WHERE bound_object_ref = ? AND status = 'published'
            ORDER BY binding_ref
            """,
            [object_ref],
        )
        return [self._read_binding(str(row["binding_id"])) for row in rows]

    def _published_bindings_matching_table(self, table_name: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT binding_id
            FROM typed_bindings
            WHERE status = 'published'
            ORDER BY binding_ref
            """,
        )
        bindings = [self._read_binding(str(row["binding_id"])) for row in rows]
        return [binding for binding in bindings if self._binding_matches_table(binding, table_name)]

    def _primary_time_ref_for_binding(self, binding: dict[str, Any]) -> str | None:
        header = dict(binding.get("header") or {})
        binding_scope = _optional_str(header.get("binding_scope"))
        bound_object_ref = _optional_str(header.get("bound_object_ref"))
        if binding_scope is None or bound_object_ref is None:
            return None
        if binding_scope == "metric":
            row = self.metadata.query_one(
                """
                SELECT primary_time_ref
                FROM semantic_metric_contracts
                WHERE metric_ref = ? AND status = 'published'
                """,
                [bound_object_ref],
            )
            return _optional_str(row["primary_time_ref"]) if row is not None else None
        if binding_scope == "entity":
            row = self.metadata.query_one(
                """
                SELECT primary_time_ref
                FROM semantic_entity_contracts
                WHERE entity_ref = ? AND status = 'published'
                """,
                [bound_object_ref],
            )
            return _optional_str(row["primary_time_ref"]) if row is not None else None
        return None

    def _derive_binding_time_context(
        self, binding: dict[str, Any], *, primary_time_ref: str | None
    ) -> dict[str, Any] | None:
        interface_contract = dict(binding.get("interface_contract") or {})
        carrier_bindings = list(interface_contract.get("carrier_bindings") or [])
        field_bindings = list(interface_contract.get("field_bindings") or [])
        time_bindings = list(interface_contract.get("time_bindings") or [])
        carrier_surfaces = self._carrier_surface_map(carrier_bindings)

        analysis_caps = self._analysis_caps_from_time_bindings(
            time_bindings,
            carrier_surfaces=carrier_surfaces,
            primary_time_ref=primary_time_ref,
        )
        if analysis_caps is None:
            analysis_caps = self._analysis_caps_from_field_bindings(
                field_bindings,
                carrier_surfaces=carrier_surfaces,
                primary_time_ref=primary_time_ref,
            )
        if analysis_caps is None:
            return None

        partition_caps = self._partition_caps_from_time_bindings(
            time_bindings,
            carrier_surfaces=carrier_surfaces,
        )
        if partition_caps is None and analysis_caps.get("partition_time"):
            partition_caps = {"partition_time": dict(analysis_caps["partition_time"])}
        if partition_caps is None:
            partition_caps = self._partition_caps_from_analysis_caps(analysis_caps)

        return {
            "binding_ref": binding.get("binding_ref")
            or (binding.get("header") or {}).get("binding_ref"),
            "analysis_caps": normalize_time_capabilities(analysis_caps),
            "partition_caps": normalize_time_capabilities(partition_caps),
        }

    @staticmethod
    def _carrier_surface_map(carrier_bindings: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
        surfaces: dict[tuple[str, str], str] = {}
        for carrier in carrier_bindings:
            binding_key = _optional_str(carrier.get("binding_key"))
            if binding_key is None:
                continue
            for field_surface in carrier.get("field_surfaces") or []:
                surface_ref = _optional_str(field_surface.get("surface_ref"))
                physical_name = _optional_str(field_surface.get("physical_name"))
                if surface_ref is None or physical_name is None:
                    continue
                surfaces[(binding_key, surface_ref)] = physical_name
        return surfaces

    def _analysis_caps_from_time_bindings(
        self,
        time_bindings: list[dict[str, Any]],
        *,
        carrier_surfaces: dict[tuple[str, str], str],
        primary_time_ref: str | None,
    ) -> dict[str, Any] | None:
        if primary_time_ref is None:
            return None
        matches = [
            time_binding
            for time_binding in time_bindings
            if _optional_str(time_binding.get("semantic_ref")) == primary_time_ref
            and _optional_str((time_binding.get("target") or {}).get("target_kind"))
            == "primary_time"
        ]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous time binding for {primary_time_ref}")
        if not matches:
            return None
        return self._caps_from_time_binding(matches[0], carrier_surfaces=carrier_surfaces)

    def _analysis_caps_from_field_bindings(
        self,
        field_bindings: list[dict[str, Any]],
        *,
        carrier_surfaces: dict[tuple[str, str], str],
        primary_time_ref: str | None,
    ) -> dict[str, Any] | None:
        if primary_time_ref is None:
            return None
        matches = [
            field_binding
            for field_binding in field_bindings
            if _optional_str(field_binding.get("semantic_ref")) == primary_time_ref
            and _optional_str((field_binding.get("target") or {}).get("target_kind"))
            == "primary_time"
        ]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous legacy field binding for {primary_time_ref}")
        if not matches:
            return None
        binding = matches[0]
        carrier_binding_key = _optional_str(binding.get("carrier_binding_key"))
        surface_ref = _optional_str(binding.get("surface_ref"))
        if carrier_binding_key is None or surface_ref is None:
            return None
        physical_name = carrier_surfaces.get((carrier_binding_key, surface_ref))
        if physical_name is None:
            return None
        if _looks_like_day_column_name(physical_name):
            return normalize_time_capabilities(
                {
                    "analysis_time": {"fallback_date_column": physical_name},
                    "partition_time": {"date_column": physical_name},
                }
            )
        return normalize_time_capabilities({"analysis_time": {"timestamp_column": physical_name}})

    def _partition_caps_from_time_bindings(
        self,
        time_bindings: list[dict[str, Any]],
        *,
        carrier_surfaces: dict[tuple[str, str], str],
    ) -> dict[str, Any] | None:
        matches = [
            time_binding
            for time_binding in time_bindings
            if _optional_str(time_binding.get("semantic_ref")) == "time.partition_time"
            and _optional_str((time_binding.get("target") or {}).get("target_kind"))
            == "primary_time"
        ]
        if len(matches) > 1:
            raise ValueError("Ambiguous time binding for time.partition_time")
        if not matches:
            return None
        return self._partition_caps_only_from_time_binding(
            matches[0], carrier_surfaces=carrier_surfaces
        )

    def _caps_from_time_binding(
        self,
        time_binding: dict[str, Any],
        *,
        carrier_surfaces: dict[tuple[str, str], str],
    ) -> dict[str, Any]:
        carrier_binding_key = _optional_str(time_binding.get("carrier_binding_key"))
        if carrier_binding_key is None:
            raise ValueError("time_binding is missing carrier_binding_key")
        resolution_kind = _optional_str(time_binding.get("resolution_kind"))
        if resolution_kind is None:
            raise ValueError("time_binding is missing resolution_kind")

        def field_name(key: str) -> str | None:
            surface_ref = _optional_str(time_binding.get(key))
            if surface_ref is None:
                return None
            return carrier_surfaces.get((carrier_binding_key, surface_ref))

        date_format = _optional_str(time_binding.get("date_format"))
        hour_format = _optional_str(time_binding.get("hour_format"))
        if resolution_kind == "timestamp_column":
            timestamp_column = field_name("timestamp_surface_ref")
            if timestamp_column is None:
                raise ValueError("time_binding timestamp surface is not grounded")
            timestamp_format = _normalize_timestamp_format(time_binding.get("timestamp_format"))
            return (
                normalize_time_capabilities(
                    {
                        "analysis_time": {
                            "timestamp_column": timestamp_column,
                            "timestamp_format": timestamp_format,
                        }
                    }
                )
                or {}
            )
        if resolution_kind == "date_column":
            date_column = field_name("date_surface_ref")
            if date_column is None:
                raise ValueError("time_binding date surface is not grounded")
            payload = {
                "analysis_time": {"fallback_date_column": date_column},
                "partition_time": {"date_column": date_column},
            }
            if date_format is not None:
                payload["partition_time"]["date_format"] = date_format
            return normalize_time_capabilities(payload) or {}

        date_column = field_name("date_surface_ref")
        hour_column = field_name("hour_surface_ref")
        if date_column is None or hour_column is None:
            raise ValueError("time_binding date/hour surfaces are not grounded")
        payload = {
            "analysis_time": {
                "fallback_date_column": date_column,
                "fallback_hour_column": hour_column,
            },
            "partition_time": {
                "date_column": date_column,
                "hour_column": hour_column,
            },
        }
        if date_format is not None:
            payload["partition_time"]["date_format"] = date_format
        if hour_format is not None:
            payload["partition_time"]["hour_format"] = hour_format
        return normalize_time_capabilities(payload) or {}

    def _partition_caps_only_from_time_binding(
        self,
        time_binding: dict[str, Any],
        *,
        carrier_surfaces: dict[tuple[str, str], str],
    ) -> dict[str, Any]:
        payload = self._caps_from_time_binding(time_binding, carrier_surfaces=carrier_surfaces)
        partition_time = dict(payload.get("partition_time") or {})
        return normalize_time_capabilities({"partition_time": partition_time}) or {}

    @staticmethod
    def _partition_caps_from_analysis_caps(
        analysis_caps: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not analysis_caps:
            return None
        analysis = dict(analysis_caps.get("analysis_time") or {})
        if analysis.get("fallback_date_column") and analysis.get("fallback_hour_column"):
            partition = {
                "date_column": analysis["fallback_date_column"],
                "hour_column": analysis["fallback_hour_column"],
            }
            return normalize_time_capabilities({"partition_time": partition})
        if analysis.get("fallback_date_column"):
            partition = {"date_column": analysis["fallback_date_column"]}
            existing = dict(analysis_caps.get("partition_time") or {})
            partition.update(existing)
            return normalize_time_capabilities({"partition_time": partition})
        return None

    def _binding_matches_table(self, binding: dict[str, Any], table_name: str) -> bool:
        interface_contract = dict(binding.get("interface_contract") or {})
        carrier_bindings = list(interface_contract.get("carrier_bindings") or [])
        return any(
            self._table_name_matches_locator(
                table_name,
                _optional_str(carrier_binding.get("carrier_locator"))
                or _optional_str(carrier_binding.get("source_object_ref")),
            )
            for carrier_binding in carrier_bindings
        )

    @staticmethod
    def _table_name_matches_locator(table_name: str, locator: str | None) -> bool:
        normalized_table = table_name.strip()
        normalized_locator = str(locator or "").strip()
        if not normalized_table or not normalized_locator:
            return False
        if normalized_table == normalized_locator:
            return True
        return normalized_locator.endswith(f".{normalized_table}") or normalized_table.endswith(
            f".{normalized_locator}"
        )

    def _read_binding(self, binding_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_id = ?", [binding_id]
        )
        if row is None:
            raise KeyError(f"Unknown binding_id: {binding_id}")
        import_rows = self.metadata.query_rows(
            """
            SELECT import_key, imported_binding_ref, required_ref_prefixes_json
            FROM binding_imports
            WHERE binding_id = ?
            ORDER BY id
            """,
            [binding_id],
        )
        carrier_rows = self.metadata.query_rows(
            """
            SELECT *
            FROM carrier_bindings
            WHERE binding_id = ?
            ORDER BY binding_key
            """,
            [binding_id],
        )
        carriers: list[dict[str, Any]] = []
        for carrier_row in carrier_rows:
            field_surface_rows = self.metadata.query_rows(
                """
                SELECT surface_ref, physical_name, field_type
                FROM carrier_field_surfaces
                WHERE carrier_binding_id = ?
                ORDER BY position
                """,
                [carrier_row["carrier_binding_id"]],
            )
            time_surface_rows = self.metadata.query_rows(
                """
                SELECT surface_ref, physical_name, time_granularity
                FROM carrier_time_surfaces
                WHERE carrier_binding_id = ?
                ORDER BY position
                """,
                [carrier_row["carrier_binding_id"]],
            )
            carriers.append(
                {
                    "binding_key": carrier_row["binding_key"],
                    "source_object_ref": carrier_row["source_object_ref"],
                    "carrier_kind": carrier_row["carrier_kind"],
                    "carrier_locator": carrier_row["carrier_locator"],
                    "binding_role": carrier_row["binding_role"],
                    "semantic_role_ref": carrier_row["semantic_role_ref"],
                    "grain_ref": carrier_row["grain_ref"],
                    "primary_entity_ref": carrier_row["primary_entity_ref"],
                    "field_surfaces": [dict(surface_row) for surface_row in field_surface_rows]
                    or None,
                    "time_surfaces": [dict(surface_row) for surface_row in time_surface_rows]
                    or None,
                }
            )
        field_binding_rows = self.metadata.query_rows(
            """
            SELECT carrier_binding_key, target_kind, target_key, context_ref, semantic_ref,
                   surface_ref, field_type_ref, nullability_policy, repeated_value_policy
            FROM field_bindings
            WHERE binding_id = ?
            ORDER BY carrier_binding_key, target_kind, target_key
            """,
            [binding_id],
        )
        time_binding_rows = self.metadata.query_rows(
            """
            SELECT carrier_binding_key, target_kind, target_key, context_ref, semantic_ref,
                   resolution_kind, timestamp_surface_ref, timestamp_format,
                   date_surface_ref, date_format,
                   hour_surface_ref, hour_format, timezone_strategy
            FROM time_bindings
            WHERE binding_id = ?
            ORDER BY carrier_binding_key, target_kind, target_key, semantic_ref
            """,
            [binding_id],
        )
        return {
            "binding_id": row["binding_id"],
            "binding_ref": row["binding_ref"],
            "header": {
                "binding_ref": row["binding_ref"],
                "binding_scope": row["binding_scope"],
                "bound_object_ref": row["bound_object_ref"],
            },
            "interface_contract": {
                "imports": [
                    {
                        "import_key": import_row["import_key"],
                        "binding_ref": import_row["imported_binding_ref"],
                        "required_ref_prefixes": json.loads(
                            import_row["required_ref_prefixes_json"]
                        ),
                    }
                    for import_row in import_rows
                ],
                "carrier_bindings": carriers,
                "field_bindings": [
                    {
                        "carrier_binding_key": field_binding_row["carrier_binding_key"],
                        "target": {
                            "target_kind": field_binding_row["target_kind"],
                            "target_key": field_binding_row["target_key"],
                            "context_ref": field_binding_row["context_ref"],
                        },
                        "semantic_ref": field_binding_row["semantic_ref"],
                        "surface_ref": field_binding_row["surface_ref"],
                        "field_type_ref": field_binding_row["field_type_ref"],
                        "nullability_policy": field_binding_row["nullability_policy"],
                        "repeated_value_policy": field_binding_row["repeated_value_policy"],
                    }
                    for field_binding_row in field_binding_rows
                ],
                "time_bindings": [
                    {
                        "carrier_binding_key": time_binding_row["carrier_binding_key"],
                        "target": {
                            "target_kind": time_binding_row["target_kind"],
                            "target_key": time_binding_row["target_key"],
                            "context_ref": time_binding_row["context_ref"],
                        },
                        "semantic_ref": time_binding_row["semantic_ref"],
                        "resolution_kind": time_binding_row["resolution_kind"],
                        "timestamp_surface_ref": time_binding_row["timestamp_surface_ref"],
                        "timestamp_format": time_binding_row["timestamp_format"],
                        "date_surface_ref": time_binding_row["date_surface_ref"],
                        "date_format": time_binding_row["date_format"],
                        "hour_surface_ref": time_binding_row["hour_surface_ref"],
                        "hour_format": time_binding_row["hour_format"],
                        "timezone_strategy": time_binding_row["timezone_strategy"],
                    }
                    for time_binding_row in time_binding_rows
                ],
            },
        }


def _normalize_analysis_time_section(
    payload: Mapping[str, Any] | None,
    *,
    label: str,
) -> dict[str, str]:
    if not payload:
        return {}
    timestamp_format = _normalize_timestamp_format(payload.get("timestamp_format"))
    normalized = {
        key: value
        for key, value in {
            "timestamp_column": _optional_str(payload.get("timestamp_column")),
            "timestamp_format": timestamp_format,
            "fallback_date_column": _optional_str(payload.get("fallback_date_column")),
            "fallback_hour_column": _optional_str(payload.get("fallback_hour_column")),
        }.items()
        if value is not None
    }
    if normalized.get("fallback_hour_column") and not normalized.get("fallback_date_column"):
        raise ValueError(f"{label}.fallback_hour_column requires fallback_date_column")
    return normalized


def _normalize_partition_time_section(
    payload: Mapping[str, Any] | None,
    *,
    label: str,
) -> dict[str, str]:
    if not payload:
        return {}
    normalized = {
        key: value
        for key, value in {
            "date_column": _optional_str(payload.get("date_column")),
            "date_format": _optional_str(payload.get("date_format")),
            "hour_column": _optional_str(payload.get("hour_column")),
            "hour_format": _optional_str(payload.get("hour_format")),
        }.items()
        if value is not None
    }
    if normalized.get("hour_column") and not normalized.get("date_column"):
        raise ValueError(f"{label}.hour_column requires date_column")
    return normalized


def _normalize_timestamp_format(value: Any) -> str | None:
    normalized = _optional_str(value)
    if normalized is None:
        return None
    if normalized not in {"native", "iso8601_t_naive", "YYYYMMDD hh:mm:ss"}:
        raise ValueError(
            "analysis_time.timestamp_format must be 'native', "
            "'iso8601_t_naive', or 'YYYYMMDD hh:mm:ss'"
        )
    return normalized


def _decode_properties_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _looks_like_day_column_name(column: str) -> bool:
    return column in {"log_date", "event_date", "dt", "date", "day"}
