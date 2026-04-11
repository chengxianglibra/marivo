"""Evaluation context for semantic readiness computation.

Provides lazy loaders for querying dependencies, bindings, and profiles
from MetadataStore. Evaluators use these loaders to inspect related
objects without requiring all data upfront.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .types import ObjectKind, ReadinessResult

if TYPE_CHECKING:
    from app.storage.metadata import MetadataStore


@dataclass(slots=True)
class ReadinessObjectSnapshot:
    """Immutable snapshot of a semantic object for readiness evaluation.

    Contains the essential fields needed by evaluators: identity (kind, id, ref),
    storage state (status, revision), and the full semantic_object for inspection.
    """

    object_kind: ObjectKind
    object_id: str
    ref: str
    status: str
    revision: int
    semantic_object: dict[str, Any]


DependencySnapshotLoader = Callable[[str], ReadinessObjectSnapshot | None]
DependencyResultLoader = Callable[[str], ReadinessResult | None]
SubjectBindingsLoader = Callable[[str], list[dict[str, Any]]]
BindingImportsLoader = Callable[[str], list[dict[str, Any]]]
CarrierSourceObjectLoader = Callable[[dict[str, Any]], dict[str, Any] | None]
ProfilesLoader = Callable[[str, str], list[dict[str, Any]]]
PreviouslyReadyLoader = Callable[[ReadinessObjectSnapshot], bool]


def _runtime_object_kind(ref: str) -> ObjectKind | None:
    """Derive object kind from a ref string by prefix matching.

    Uses delimiter-aware matching to prevent false positives like
    "metric_custom.special" incorrectly matching as "metric".
    """
    prefixes: tuple[tuple[str, ObjectKind], ...] = (
        ("entity.", "entity"),
        ("metric.", "metric"),
        ("process.", "process"),
        ("dimension.", "dimension"),
        ("time.", "time"),
        ("enum.", "enum"),
        ("binding.", "binding"),
    )
    for prefix, object_kind in prefixes:
        if ref.startswith(prefix):
            return object_kind
    if ref.startswith("compiler_profile."):
        return "compiler_profile"
    return None


@dataclass(slots=True)
class ReadinessEvaluationContext:
    """Context for readiness evaluation with lazy dependency loaders.

    Holds the object snapshot and optional loaders for querying:
    - Dependencies (other semantic objects this object depends on)
    - Subject bindings (bindings attached to this object)
    - Binding imports (imported bindings for a binding)
    - Carrier source objects (physical tables/views backing bindings)
    - Compatibility profiles (profile metadata for subject)
    - Previous readiness state (for stale detection)

    Loaders are lazy: they only query metadata when called. Default loaders
    use MetadataStore directly; custom loaders can be injected for testing.

    Attributes:
        snapshot: The object being evaluated.
        metadata: MetadataStore for default loader implementations.
        require_physical_grounding: Whether physical binding is required.
        required_capabilities: Capability keys required by intent.
        intent_kind: Analysis intent (observe, compare, etc).
    """

    snapshot: ReadinessObjectSnapshot
    metadata: MetadataStore | None = None
    require_physical_grounding: bool = False
    required_capabilities: list[str] = field(default_factory=list)
    intent_kind: str | None = None
    dependency_snapshot_loader: DependencySnapshotLoader | None = None
    dependency_result_loader: DependencyResultLoader | None = None
    subject_bindings_loader: SubjectBindingsLoader | None = None
    binding_imports_loader: BindingImportsLoader | None = None
    carrier_source_object_loader: CarrierSourceObjectLoader | None = None
    profiles_loader: ProfilesLoader | None = None
    previously_ready_loader: PreviouslyReadyLoader | None = None

    def load_dependency_snapshot(self, ref: str) -> ReadinessObjectSnapshot | None:
        if self.dependency_snapshot_loader is not None:
            return self.dependency_snapshot_loader(ref)
        return self._default_dependency_snapshot_loader(ref)

    def load_dependency_result(self, ref: str) -> ReadinessResult | None:
        if self.dependency_result_loader is not None:
            return self.dependency_result_loader(ref)
        return None

    def load_subject_bindings(self, subject_ref: str | None = None) -> list[dict[str, Any]]:
        if self.subject_bindings_loader is not None:
            return self.subject_bindings_loader(subject_ref or self.snapshot.ref)
        return self._default_subject_bindings_loader(subject_ref or self.snapshot.ref)

    def load_binding_imports(self, binding_ref: str) -> list[dict[str, Any]]:
        if self.binding_imports_loader is not None:
            return self.binding_imports_loader(binding_ref)
        return self._default_binding_imports_loader(binding_ref)

    def load_carrier_source_object(self, carrier_binding: dict[str, Any]) -> dict[str, Any] | None:
        if self.carrier_source_object_loader is not None:
            return self.carrier_source_object_loader(carrier_binding)
        return self._default_carrier_source_object_loader(carrier_binding)

    def load_profiles(self, subject_kind: str, subject_ref: str) -> list[dict[str, Any]]:
        if self.profiles_loader is not None:
            return self.profiles_loader(subject_kind, subject_ref)
        return self._default_profiles_loader(subject_kind, subject_ref)

    def previously_ready(self) -> bool:
        if self.previously_ready_loader is not None:
            return self.previously_ready_loader(self.snapshot)
        return False

    def _default_subject_bindings_loader(self, subject_ref: str) -> list[dict[str, Any]]:
        if self.metadata is None:
            return []
        rows = self.metadata.query_rows(
            """
            SELECT *
            FROM typed_bindings
            WHERE bound_object_ref = ?
            ORDER BY binding_ref
            """,
            [subject_ref],
        )
        return [self._build_binding_snapshot(dict(row)) for row in rows]

    def _default_binding_imports_loader(self, binding_ref: str) -> list[dict[str, Any]]:
        if self.metadata is None:
            return []
        binding_row = self.metadata.query_one(
            "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
            [binding_ref],
        )
        if binding_row is None:
            return []
        rows = self.metadata.query_rows(
            """
            SELECT import_key, imported_binding_ref, required_ref_prefixes_json
            FROM binding_imports
            WHERE binding_id = ?
            ORDER BY id
            """,
            [binding_row["binding_id"]],
        )
        return [
            {
                "import_key": row["import_key"],
                "imported_binding_ref": row["imported_binding_ref"],
                "required_ref_prefixes": json.loads(row["required_ref_prefixes_json"]),
            }
            for row in rows
        ]

    def _default_carrier_source_object_loader(
        self, carrier_binding: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self.metadata is None:
            return None
        source_object_ref = carrier_binding.get("source_object_ref")
        if isinstance(source_object_ref, str) and source_object_ref:
            row = self.metadata.query_one(
                "SELECT * FROM source_objects WHERE object_id = ? OR fqn = ?",
                [source_object_ref, source_object_ref],
            )
            return dict(row) if row is not None else None
        locator = carrier_binding.get("carrier_locator") or {}
        if not isinstance(locator, dict):
            return None
        if locator.get("object_id"):
            row = self.metadata.query_one(
                "SELECT * FROM source_objects WHERE object_id = ?",
                [locator["object_id"]],
            )
            return dict(row) if row is not None else None
        if locator.get("fqn"):
            row = self.metadata.query_one(
                "SELECT * FROM source_objects WHERE fqn = ?",
                [locator["fqn"]],
            )
            return dict(row) if row is not None else None
        return None

    def _default_profiles_loader(self, subject_kind: str, subject_ref: str) -> list[dict[str, Any]]:
        if self.metadata is None:
            return []
        rows = self.metadata.query_rows(
            """
            SELECT *
            FROM compiler_compatibility_profiles
            WHERE subject_kind = ? AND subject_ref = ? AND status = 'published'
            ORDER BY profile_ref
            """,
            [subject_kind, subject_ref],
        )
        profiles: list[dict[str, Any]] = []
        for row in rows:
            profiles.append(
                {
                    **dict(row),
                    "requirement": json.loads(row["requirement_json"] or "{}"),
                    "capability": json.loads(row["capability_json"] or "{}"),
                }
            )
        return profiles

    def _default_dependency_snapshot_loader(self, ref: str) -> ReadinessObjectSnapshot | None:
        if self.metadata is None:
            return None
        object_kind = _runtime_object_kind(ref)
        if object_kind is None:
            return None
        if object_kind == "entity":
            row = self.metadata.query_one(
                "SELECT * FROM semantic_entity_contracts WHERE entity_ref = ?",
                [ref],
            )
            if row is None:
                return None
            row_dict = dict(row)
            return ReadinessObjectSnapshot(
                object_kind="entity",
                object_id=str(row_dict["entity_contract_id"]),
                ref=str(row_dict["entity_ref"]),
                status=str(row_dict["status"]),
                revision=int(row_dict["revision"]),
                semantic_object=self._build_entity_snapshot(row_dict),
            )
        if object_kind == "metric":
            row = self.metadata.query_one(
                "SELECT * FROM semantic_metric_contracts WHERE metric_ref = ?",
                [ref],
            )
            if row is None:
                return None
            row_dict = dict(row)
            return ReadinessObjectSnapshot(
                object_kind="metric",
                object_id=str(row_dict["metric_contract_id"]),
                ref=str(row_dict["metric_ref"]),
                status=str(row_dict["status"]),
                revision=int(row_dict["revision"]),
                semantic_object=self._build_metric_snapshot(row_dict),
            )
        if object_kind == "process":
            row = self.metadata.query_one(
                "SELECT * FROM semantic_process_objects WHERE process_ref = ?",
                [ref],
            )
            if row is None:
                return None
            row_dict = dict(row)
            return ReadinessObjectSnapshot(
                object_kind="process",
                object_id=str(row_dict["process_contract_id"]),
                ref=str(row_dict["process_ref"]),
                status=str(row_dict["status"]),
                revision=int(row_dict["revision"]),
                semantic_object=self._build_process_snapshot(row_dict),
            )
        lookup: dict[ObjectKind, tuple[str, str, str]] = {
            "dimension": (
                "semantic_dimension_contracts",
                "dimension_contract_id",
                "dimension_ref",
            ),
            "time": (
                "semantic_time_objects",
                "time_contract_id",
                "time_ref",
            ),
            "enum": (
                "semantic_enum_sets",
                "enum_set_contract_id",
                "enum_set_ref",
            ),
            "binding": (
                "typed_bindings",
                "binding_id",
                "binding_ref",
            ),
        }
        # Assertion documents that object_kind comes from type-checked ObjectKind literal,
        # making SQL identifiers safe (values from hardcoded dict, not user input).
        assert object_kind in lookup, f"Invalid object_kind: {object_kind}"
        table, id_field, ref_field = lookup[object_kind]
        row = self.metadata.query_one(f"SELECT * FROM {table} WHERE {ref_field} = ?", [ref])
        if row is None:
            return None
        row_dict = dict(row)
        semantic_object: dict[str, Any]
        if object_kind == "binding":
            semantic_object = self._build_binding_snapshot(row_dict)
        else:
            semantic_object = {"header": {ref_field: row_dict[ref_field]}}
        return ReadinessObjectSnapshot(
            object_kind=object_kind,
            object_id=str(row_dict[id_field]),
            ref=str(row_dict[ref_field]),
            status=str(row_dict["status"]),
            revision=int(row_dict["revision"]),
            semantic_object=semantic_object,
        )

    def _build_entity_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        if self.metadata is None:
            return {"header": {"entity_ref": row["entity_ref"]}}
        key_rows = self.metadata.query_rows(
            """
            SELECT key_ref
            FROM semantic_entity_key_refs
            WHERE entity_contract_id = ?
            ORDER BY position
            """,
            [row["entity_contract_id"]],
        )
        descriptor_rows = self.metadata.query_rows(
            """
            SELECT dimension_ref, cardinality
            FROM semantic_entity_stable_descriptors
            WHERE entity_contract_id = ?
            ORDER BY position
            """,
            [row["entity_contract_id"]],
        )
        return {
            "header": {
                "entity_ref": row["entity_ref"],
                "entity_contract_version": row["entity_contract_version"],
            },
            "interface_contract": {
                "identity": {
                    "key_refs": [key_row["key_ref"] for key_row in key_rows],
                    "uniqueness_scope": row["uniqueness_scope"],
                    "id_stability": row["id_stability"],
                    "nullable_key_policy": row["nullable_key_policy"],
                },
                "primary_time_ref": row["primary_time_ref"],
                "stable_descriptors": [
                    {
                        "dimension_ref": descriptor_row["dimension_ref"],
                        "cardinality": descriptor_row["cardinality"],
                    }
                    for descriptor_row in descriptor_rows
                ],
            },
        }

    def _build_metric_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "header": {
                "metric_ref": row["metric_ref"],
                "metric_family": row["metric_family"],
                "population_subject_ref": row["population_subject_ref"],
                "observed_entity_ref": row["observed_entity_ref"],
                "observation_grain_ref": row["observation_grain_ref"],
                "sample_kind": row["sample_kind"],
                "value_semantics": row["value_semantics"],
                "aggregation_scope": row["aggregation_scope"],
                "primary_time_ref": row["primary_time_ref"],
                "additivity": row["additivity"],
                "metric_contract_version": row["metric_contract_version"],
            },
            "payload": json.loads(row["family_payload_json"]),
        }

    def _build_process_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        exported_dimension_rows = []
        if self.metadata is not None:
            exported_dimension_rows = self.metadata.query_rows(
                """
                SELECT dimension_ref
                FROM semantic_process_exported_dimension_refs
                WHERE process_contract_id = ?
                ORDER BY position
                """,
                [row["process_contract_id"]],
            )
        interface_contract: dict[str, Any] = {
            "contract_mode": row["contract_mode"],
            "population_subject_ref": row["population_subject_ref"],
            "anchor_time_ref": row["anchor_time_ref"],
            "exported_dimension_refs": [
                exported_dimension_row["dimension_ref"]
                for exported_dimension_row in exported_dimension_rows
            ],
        }
        if row["contract_mode"] == "context_provider":
            interface_contract["context_kind"] = row["context_kind"]
            interface_contract["membership_cardinality"] = row["membership_cardinality"]
        else:
            interface_contract["entity_ref"] = row["entity_ref"]
            interface_contract["emitted_grain_ref"] = row["emitted_grain_ref"]
            interface_contract["subject_cardinality"] = row["subject_cardinality"]
        return {
            "header": {
                "process_ref": row["process_ref"],
                "process_type": row["process_type"],
                "process_contract_version": row["process_contract_version"],
            },
            "interface_contract": interface_contract,
            "payload": json.loads(row["process_payload_json"]),
        }

    def _build_binding_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        if self.metadata is None:
            return {
                "binding_id": row["binding_id"],
                "binding_ref": row["binding_ref"],
                "binding_scope": row["binding_scope"],
                "bound_object_ref": row["bound_object_ref"],
                "status": row["status"],
                "revision": row["revision"],
            }
        import_rows = self.metadata.query_rows(
            """
            SELECT import_key, imported_binding_ref, required_ref_prefixes_json
            FROM binding_imports
            WHERE binding_id = ?
            ORDER BY id
            """,
            [row["binding_id"]],
        )
        carrier_rows = self.metadata.query_rows(
            """
            SELECT *
            FROM carrier_bindings
            WHERE binding_id = ?
            ORDER BY binding_key
            """,
            [row["binding_id"]],
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
                    "field_surfaces": [dict(surface_row) for surface_row in field_surface_rows],
                    "time_surfaces": [dict(surface_row) for surface_row in time_surface_rows],
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
            [row["binding_id"]],
        )
        return {
            "binding_id": row["binding_id"],
            "binding_ref": row["binding_ref"],
            "binding_scope": row["binding_scope"],
            "bound_object_ref": row["bound_object_ref"],
            "status": row["status"],
            "revision": row["revision"],
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
            },
        }


def build_snapshot(
    *,
    object_kind: ObjectKind,
    object_id: str,
    ref: str,
    status: str,
    revision: int,
    semantic_object: dict[str, Any],
) -> ReadinessObjectSnapshot:
    """Build a ReadinessObjectSnapshot from raw parameters.

    Validates that the ref prefix matches the declared object_kind.
    For example, ref="metric.watch_time" must have object_kind="metric".

    Args:
        object_kind: The semantic object type.
        object_id: Unique identifier.
        ref: Semantic reference string.
        status: Storage status (draft, published, deprecated).
        revision: Object revision.
        semantic_object: Full object dict.

    Returns:
        ReadinessObjectSnapshot ready for evaluation.

    Raises:
        ValueError: If ref prefix doesn't match object_kind.
    """
    resolved_kind = _runtime_object_kind(ref)
    if resolved_kind is not None and resolved_kind != object_kind:
        raise ValueError(f"Ref {ref!r} does not match object_kind {object_kind!r}")
    return ReadinessObjectSnapshot(
        object_kind=object_kind,
        object_id=object_id,
        ref=ref,
        status=status,
        revision=revision,
        semantic_object=semantic_object,
    )
