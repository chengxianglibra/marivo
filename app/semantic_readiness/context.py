"""Evaluation context for semantic readiness computation.

Provides lazy loaders for querying dependencies, bindings, and profiles
from MetadataStore. Evaluators use these loaders to inspect related
objects without requiring all data upfront.
"""

from __future__ import annotations

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
        if ref.startswith(prefix) and (len(ref) == len(prefix) or ref[len(prefix)] == "."):
            return object_kind
    if ref.startswith("compiler_profile.") and (
        len(ref) == len("compiler_profile.") or ref[len("compiler_profile.")] == "."
    ):
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
        return None

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
            SELECT binding_id, binding_ref, binding_scope, bound_object_ref, status, revision
            FROM typed_bindings
            WHERE bound_object_ref = ?
            ORDER BY binding_ref
            """,
            [subject_ref],
        )
        return [dict(row) for row in rows]

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
        return [dict(row) for row in rows]

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
            WHERE subject_kind = ? AND subject_ref = ?
            ORDER BY profile_ref
            """,
            [subject_kind, subject_ref],
        )
        return [dict(row) for row in rows]


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
