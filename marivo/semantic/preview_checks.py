"""Snapshot binding and row-free evidence for semantic runtime previews."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from marivo._authoring.model import AuthoringRepair
from marivo.datasource.authoring_store import (
    AuthoringStore,
    datasource_spec_fingerprint,
    snapshot_identity,
)
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
from marivo.datasource.snapshot import DiscoverySnapshot
from marivo.datasource.source import AuthoringScope, PartitionScope, UnprunedScope
from marivo.preview import PreviewCoverage, PreviewResult
from marivo.refs import (
    EntityKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.semantic._persistence import EntitySnapshotBindingV1
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise, repair
from marivo.semantic.ir import EntityIR, composition_components
from marivo.semantic.metric_graph import SemanticDependencyDigestV1
from marivo.semantic.metric_graph_canonical import canonical_value, fingerprint
from marivo.semantic.metric_graph_lowering import dependency_digest
from marivo.telemetry import staged

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar
    from marivo.semantic.validator import Registry

type PreviewUsing = DiscoverySnapshot | Mapping[Ref[EntityKind], DiscoverySnapshot]


@dataclass(frozen=True, slots=True)
class PreviewCheckV1:
    schema: Literal["marivo.semantic_preview_check/v1"]
    id: str
    checked_ref: RefPayloadV1
    catalog_definition_fingerprint: str
    semantic_dependency_digest: SemanticDependencyDigestV1
    entity_snapshot_bindings: tuple[EntitySnapshotBindingV1, ...]
    backend: str
    status: Literal["passed"]
    rows_observed: int
    scope_exhaustion: Literal["exhaustive", "truncated"]
    types: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.schema != "marivo.semantic_preview_check/v1":
            raise ValueError("preview check schema must be 'marivo.semantic_preview_check/v1'")
        if type(self.id) is not str or not self.id:
            raise ValueError("preview check id must be a non-empty string")
        if type(self.checked_ref) is not RefPayloadV1:
            raise TypeError("preview check checked_ref must be an exact RefPayloadV1")
        if (
            type(self.catalog_definition_fingerprint) is not str
            or not self.catalog_definition_fingerprint
        ):
            raise ValueError("preview check catalog fingerprint must be non-empty")
        if type(self.semantic_dependency_digest) is not SemanticDependencyDigestV1:
            raise TypeError(
                "preview check semantic_dependency_digest must be an exact "
                "SemanticDependencyDigestV1"
            )
        if type(self.entity_snapshot_bindings) is not tuple or any(
            type(binding) is not EntitySnapshotBindingV1
            for binding in self.entity_snapshot_bindings
        ):
            raise TypeError(
                "preview check entity_snapshot_bindings must contain EntitySnapshotBindingV1 values"
            )
        if type(self.backend) is not str or not self.backend:
            raise ValueError("preview check backend must be non-empty")
        if self.status != "passed":
            raise ValueError("preview check status must be 'passed'")
        if type(self.rows_observed) is not int or self.rows_observed < 0:
            raise ValueError("preview check rows_observed must be a non-negative int")
        if self.scope_exhaustion not in {"exhaustive", "truncated"}:
            raise ValueError("preview check scope_exhaustion is invalid")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("preview check timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class NormalizedPreviewBindings:
    checked_ref: Ref[SemanticKindTag]
    entity_refs: tuple[Ref[EntityKind], ...]
    snapshots: tuple[DiscoverySnapshot, ...]
    backend: str
    datasource_id: str
    timeout_seconds: int
    catalog_definition_fingerprint: str
    semantic_dependency_digest: SemanticDependencyDigestV1

    @property
    def entity_ids(self) -> tuple[str, ...]:
        return tuple(ref.path for ref in self.entity_refs)

    @property
    def scopes(self) -> tuple[tuple[str, AuthoringScope], ...]:
        return tuple(
            (ref.path, snapshot.scope)
            for ref, snapshot in zip(self.entity_refs, self.snapshots, strict=True)
        )

    @property
    def entity_scopes(self) -> Mapping[str, AuthoringScope]:
        return dict(self.scopes)


@dataclass(frozen=True)
class PreviewEvidenceRequirement:
    """Query-free readiness state for one directly requested executable ref."""

    status: Literal["matched", "snapshot_missing", "runtime_preview_missing"]
    repair: AuthoringRepair


def _blocked(ref: str, message: str, *, details: Mapping[str, object]) -> NoReturn:
    _raise(
        ErrorKind.MATERIALIZE_FAILED,
        message,
        cls=SemanticRuntimeError,
        refs=(ref,),
        details={"query_executed": False, **details},
    )


def _dependency_entities(ref: str, kind: SemanticKind, registry: Registry) -> tuple[str, ...]:
    entity_registry = registry.entities
    dimensions = registry.dimensions
    measures = registry.measures
    metrics = registry.metrics
    if kind == SemanticKind.ENTITY:
        return (ref,)
    if kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return (dimensions[ref].entity,)
    if kind == SemanticKind.MEASURE:
        return (measures[ref].entity,)
    if kind == SemanticKind.RELATIONSHIP:
        relationship = registry.relationships[ref]
        return tuple(dict.fromkeys((relationship.from_entity, relationship.to_entity)))
    if kind == SemanticKind.EVENT:
        event = registry.events[ref]
        event_entities = [event.source_entity]
        endpoint = event.source_entity
        for participant in event.participants:
            endpoint = event.source_entity
            for relationship_id in participant.path or ():
                endpoint = registry.relationships[relationship_id].to_entity
                if endpoint not in event_entities:
                    event_entities.append(endpoint)
        return tuple(event_entities)
    if kind != SemanticKind.METRIC:
        return ()

    ordered: list[str] = []
    visited_metrics: set[str] = set()

    def visit(metric_id: str) -> None:
        if metric_id in visited_metrics:
            return
        visited_metrics.add(metric_id)
        metric = metrics[metric_id]
        for entity_id in metric.entities:
            if entity_id in entity_registry and entity_id not in ordered:
                ordered.append(entity_id)
        if metric.composition is not None:
            for component in composition_components(metric.composition).values():
                if component in metrics:
                    visit(component)

    visit(ref)
    return tuple(ordered)


def preview_dependency_entities(ref: str, *, registry: Registry) -> tuple[str, ...]:
    """Return every entity required to execute one previewable semantic ref."""
    return _dependency_entities(ref, _semantic_kind(ref, registry), registry)


def _normalize_mapping_key(key: object, *, preview_ref: str) -> str:
    if type(key) is Ref and key.kind is SemanticKind.ENTITY:
        return key.path
    _blocked(
        preview_ref,
        "catalog.preview(..., using=...) Mapping requires exact Ref[entity] keys.",
        details={"received_type": type(key).__name__},
    )


def _validate_scope(scope: AuthoringScope, *, preview_ref: str) -> None:
    if not isinstance(scope, PartitionScope | UnprunedScope):
        _blocked(
            preview_ref,
            "Bound discovery snapshot has an invalid authoring scope.",
            details={"received_type": type(scope).__name__},
        )
    if type(scope.max_rows) is not int or scope.max_rows < 1:
        _blocked(preview_ref, "Bound discovery snapshot has an invalid scope max_rows.", details={})
    if type(scope.timeout_seconds) is not int or scope.timeout_seconds < 1:
        _blocked(
            preview_ref,
            "Bound discovery snapshot has an invalid scope timeout_seconds.",
            details={},
        )
    if isinstance(scope, PartitionScope) and (
        not scope.values
        or any(
            type(entry) is not tuple
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not entry[0]
            or not isinstance(entry[1], str)
            or not entry[1]
            for entry in scope.values
        )
    ):
        _blocked(
            preview_ref,
            "Bound discovery snapshot has an invalid partition scope.",
            details={},
        )


def _preview_check_id(
    *,
    checked_ref: RefPayloadV1,
    catalog_definition_fingerprint: str,
    semantic_dependency_digest: SemanticDependencyDigestV1,
    entity_snapshot_bindings: tuple[EntitySnapshotBindingV1, ...],
    backend: str | None,
) -> str:
    return fingerprint(
        {
            "schema": "marivo.semantic_preview_check/v1",
            "checked_ref": checked_ref,
            "catalog_definition_fingerprint": catalog_definition_fingerprint,
            "semantic_dependency_digest": semantic_dependency_digest,
            "entity_snapshot_bindings": entity_snapshot_bindings,
            "backend": backend,
        }
    )


def _semantic_kind(ref: str, registry: Registry) -> SemanticKind:
    if ref in registry.entities:
        return SemanticKind.ENTITY
    if ref in registry.dimensions:
        return (
            SemanticKind.TIME_DIMENSION
            if registry.dimensions[ref].is_time_dimension
            else SemanticKind.DIMENSION
        )
    if ref in registry.measures:
        return SemanticKind.MEASURE
    if ref in registry.metrics:
        return SemanticKind.METRIC
    if ref in registry.relationships:
        return SemanticKind.RELATIONSHIP
    if ref in registry.events:
        return SemanticKind.EVENT
    raise KeyError(ref)


def _exact_semantic_ref(ref: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
        SemanticKind.EVENT: ref_factory.event,
    }.get(kind)
    if factory is None:
        raise AssertionError(f"unsupported preview ref kind: {kind}")
    return factory(ref)


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _string_tuple(values: tuple[str, ...]) -> str:
    suffix = "," if len(values) == 1 else ""
    return "(" + ", ".join(_quoted(value) for value in values) + suffix + ")"


def _source_call(source: object) -> str:
    if isinstance(source, TableSourceIR):
        database = ""
        if source.database is not None:
            database = f", database={source.database!r}"
        return f"md.table({_quoted(source.table)}{database})"
    if isinstance(source, ParquetSourceIR):
        extras = f", hive_partitioning={source.hive_partitioning!r}"
        if source.columns is not None:
            extras += f", columns={_string_tuple(source.columns)}"
        return f"md.parquet({_quoted(source.path)}{extras})"
    if isinstance(source, CsvSourceIR):
        return (
            f"md.csv({_quoted(source.path)}, schema={dict(source.schema)!r}, "
            f"header={source.header!r}, delimiter={_quoted(source.delimiter)})"
        )
    if isinstance(source, JsonSourceIR):
        return (
            f"md.json({_quoted(source.path)}, schema={dict(source.schema)!r}, "
            f"format={_quoted(source.format)})"
        )
    raise TypeError(f"Unsupported entity source: {type(source).__name__}")


def _inspect_call(entity: EntityIR) -> str:
    datasource = entity.datasource
    source = entity.source
    return f"md.inspect(ms.ref.datasource({_quoted(datasource)}), {_source_call(source)})"


def _snapshot_sample_call(entity: EntityIR, snapshot: DiscoverySnapshot) -> str:
    scope = snapshot.scope
    if isinstance(scope, PartitionScope):
        scope_call = (
            "md.partition({"
            + ", ".join(f"{_quoted(key)}: {_quoted(value)}" for key, value in scope.values)
            + f"}}, max_rows={scope.max_rows}, "
            f"timeout_seconds={scope.timeout_seconds})"
        )
    else:
        scope_call = (
            f"md.unpruned(max_rows={scope.max_rows}, timeout_seconds={scope.timeout_seconds})"
        )
    return (
        f"{_inspect_call(entity)}.sample(\n"
        f"        scope={scope_call},\n"
        f"        columns={_string_tuple(snapshot.columns)},\n"
        "    )"
    )


def _matching_snapshot_payloads(
    *,
    entity_ids: tuple[str, ...],
    registry: Registry,
    store: AuthoringStore,
    now: datetime,
) -> tuple[
    dict[str, DiscoverySnapshot],
    dict[str, DiscoverySnapshot],
    dict[str, frozenset[str]],
]:
    newest: dict[str, DiscoverySnapshot] = {}
    by_id: dict[str, DiscoverySnapshot] = {}
    ids_by_entity: dict[str, frozenset[str]] = {}
    for entity_id in entity_ids:
        entity = registry.entities[entity_id]
        datasource = registry.datasources[entity.datasource]
        snapshots = store.valid_snapshots(
            datasource=ref_factory.datasource(entity.datasource),
            datasource_fingerprint=datasource_spec_fingerprint(datasource),
            source=entity.source,
            now=now,
        )
        if snapshots:
            newest[entity_id] = snapshots[0]
        ids_by_entity[entity_id] = frozenset(snapshot.id for snapshot in snapshots)
        by_id.update((snapshot.id, snapshot) for snapshot in snapshots)
    return newest, by_id, ids_by_entity


def preview_evidence_requirement(
    ref: str,
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    project_root: Path,
    catalog_definition_fingerprint: str,
) -> PreviewEvidenceRequirement:
    """Read persisted row-free evidence for readiness without acquiring or executing."""
    kind = _semantic_kind(ref, registry)
    checked_ref = _exact_semantic_ref(ref, kind)
    checked_payload = RefPayloadV1.from_ref(checked_ref)
    entity_ids = _dependency_entities(ref, kind, registry)
    current_dependency_digest = dependency_digest(
        registry,
        sidecar=sidecar,
        semantic_refs=(checked_ref,),
    )
    now = datetime.now(UTC)
    store = AuthoringStore(project_root)
    snapshots, snapshots_by_id, snapshot_ids_by_entity = _matching_snapshot_payloads(
        entity_ids=entity_ids,
        registry=registry,
        store=store,
        now=now,
    )
    current_backends = {
        registry.datasources[registry.entities[entity_id].datasource].backend_type
        for entity_id in entity_ids
    }
    expected_backend = next(iter(current_backends)) if len(current_backends) == 1 else None
    if store.check_dir.is_dir():
        for path in store.check_dir.glob("*.json"):
            payload = store._read_payload(path)
            if (
                payload is None
                or payload.get("schema") != "marivo.semantic_preview_check/v1"
                or payload.get("checked_ref") != checked_payload.to_dict()
            ):
                continue
            try:
                check_id = payload["id"]
                expires_at = datetime.fromisoformat(str(payload["expires_at"]))
                created_at = datetime.fromisoformat(str(payload["created_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(check_id, str):
                continue
            binding_payloads = payload.get("entity_snapshot_bindings")
            snapshot_ids = (
                tuple(
                    binding.get("snapshot_id")
                    for binding in binding_payloads
                    if isinstance(binding, dict)
                )
                if isinstance(binding_payloads, list)
                else ()
            )
            snapshots_match = (
                isinstance(binding_payloads, list)
                and len(binding_payloads) == len(entity_ids)
                and len(snapshot_ids) == len(entity_ids)
                and all(
                    isinstance(snapshot_id, str)
                    and snapshot_id in snapshot_ids_by_entity[entity_id]
                    and isinstance(binding, dict)
                    and binding.get("entity_ref")
                    == RefPayloadV1.from_ref(ref_factory.entity(entity_id)).to_dict()
                    for entity_id, snapshot_id, binding in zip(
                        entity_ids,
                        snapshot_ids,
                        binding_payloads,
                        strict=True,
                    )
                )
            )
            typed_snapshot_ids = cast("tuple[str, ...]", snapshot_ids)
            bound_snapshots = (
                tuple(snapshots_by_id[snapshot_id] for snapshot_id in typed_snapshot_ids)
                if snapshots_match
                else ()
            )
            expected_bindings = (
                tuple(
                    EntitySnapshotBindingV1(
                        entity_ref=RefPayloadV1.from_ref(ref_factory.entity(entity_id)),
                        snapshot_id=snapshot.id,
                    )
                    for entity_id, snapshot in zip(
                        entity_ids,
                        bound_snapshots,
                        strict=True,
                    )
                )
                if bound_snapshots
                else ()
            )
            expected_id = _preview_check_id(
                checked_ref=checked_payload,
                catalog_definition_fingerprint=catalog_definition_fingerprint,
                semantic_dependency_digest=current_dependency_digest,
                entity_snapshot_bindings=expected_bindings,
                backend=expected_backend,
            )
            if (
                payload.get("status") == "passed"
                and path == store.check_dir / f"{check_id}.json"
                and check_id == expected_id
                and expires_at.tzinfo is not None
                and created_at.tzinfo is not None
                and payload.get("catalog_definition_fingerprint") == catalog_definition_fingerprint
                and payload.get("semantic_dependency_digest")
                == canonical_value(current_dependency_digest)
                and payload.get("backend") == expected_backend
                and snapshots_match
                and binding_payloads == canonical_value(expected_bindings)
                and bound_snapshots
                and created_at >= max(snapshot.created_at for snapshot in bound_snapshots)
                and expires_at == min(snapshot.expires_at for snapshot in bound_snapshots)
            ):
                return PreviewEvidenceRequirement(
                    status="matched",
                    repair=repair(
                        kind="retry",
                        canonical_id="readiness",
                        action="Matching preview evidence is available; readiness may proceed.",
                    ),
                )
    missing_entities = tuple(entity_id for entity_id in entity_ids if entity_id not in snapshots)
    if missing_entities:
        calls = tuple(_inspect_call(registry.entities[entity_id]) for entity_id in missing_entities)
        return PreviewEvidenceRequirement(
            status="snapshot_missing",
            repair=repair(
                kind="reacquire",
                canonical_id="SourceInspection.sample",
                action="Acquire matching datasource snapshots before readiness.",
                snippet="\n".join(calls),
                preserves_evidence=False,
            ),
        )

    sample_calls = {
        entity_id: _snapshot_sample_call(registry.entities[entity_id], snapshots[entity_id])
        for entity_id in entity_ids
    }
    typed_ref = f"ms.ref.{kind.value}({_quoted(ref)})"
    if len(entity_ids) == 1:
        using = sample_calls[entity_ids[0]]
    else:
        mapping_items = "\n".join(
            f"        ms.ref.entity({_quoted(entity_id)}): {sample_calls[entity_id]},"
            for entity_id in entity_ids
        )
        using = "{\n" + mapping_items + "\n    }"
    return PreviewEvidenceRequirement(
        status="runtime_preview_missing",
        repair=repair(
            kind="repreview",
            canonical_id="preview",
            action="Run a scoped preview with matching snapshot bindings.",
            snippet=f"catalog.preview(\n    {typed_ref},\n    using={using},\n)",
            preserves_evidence=False,
        ),
    )


def _validate_snapshot(
    snapshot: DiscoverySnapshot,
    *,
    entity_id: str,
    preview_ref: str,
    project_root: Path,
    registry: Registry,
) -> DiscoverySnapshot:
    entity = registry.entities[entity_id]
    if snapshot._project_root.resolve() != project_root.resolve():
        _blocked(
            preview_ref,
            f"Snapshot {snapshot.id!r} belongs to a different project.",
            details={"entity": entity_id},
        )
    expected_datasource = ref_factory.datasource(entity.datasource)
    if snapshot.datasource != expected_datasource:
        _blocked(
            preview_ref,
            f"Snapshot datasource does not match entity {entity_id!r}.",
            details={
                "expected": expected_datasource.path,
                "received": snapshot.datasource.path,
            },
        )
    if snapshot.source != entity.source:
        _blocked(
            preview_ref,
            f"Snapshot physical source does not match entity {entity_id!r}.",
            details={"expected": entity.source.to_dict(), "received": snapshot.source.to_dict()},
        )
    _validate_scope(snapshot.scope, preview_ref=preview_ref)
    datasource = registry.datasources.get(entity.datasource)
    if datasource is None:
        _blocked(
            preview_ref,
            f"Datasource {entity.datasource!r} is not loaded for entity {entity_id!r}.",
            details={},
        )
    datasource_fingerprint = datasource_spec_fingerprint(datasource)
    expected_id = snapshot_identity(
        datasource_fingerprint=datasource_fingerprint,
        source=snapshot.source,
        scope=snapshot.scope,
        columns=snapshot.columns,
        schema_fingerprint=snapshot.schema_fingerprint,
        persist_values=snapshot.persist_values,
    )
    if expected_id != snapshot.id:
        _blocked(
            preview_ref,
            f"Snapshot schema fingerprint or scope identity does not match entity {entity_id!r}.",
            details={"expected_snapshot_id": expected_id, "received_snapshot_id": snapshot.id},
        )
    store = AuthoringStore(project_root)
    lookup = store.lookup_snapshot(
        snapshot_id=snapshot.id,
        datasource=snapshot.datasource,
        datasource_fingerprint=datasource_fingerprint,
        source=snapshot.source,
        scope=snapshot.scope,
        columns=snapshot.columns,
        schema_fingerprint=snapshot.schema_fingerprint,
        persist_values=snapshot.persist_values,
        refresh=False,
    )
    stored = lookup.snapshot
    if stored is None:
        _blocked(
            preview_ref,
            f"Snapshot {snapshot.id!r} is missing or mismatched in the authoring store.",
            details={"cache_status": lookup.status},
        )
    if stored.created_at != snapshot.created_at or stored.expires_at != snapshot.expires_at:
        _blocked(
            preview_ref,
            f"Snapshot timestamp metadata does not match persisted evidence for entity {entity_id!r}.",
            details={
                "expected_created_at": stored.created_at.isoformat(),
                "received_created_at": snapshot.created_at.isoformat(),
                "expected_expires_at": stored.expires_at.isoformat(),
                "received_expires_at": snapshot.expires_at.isoformat(),
            },
        )
    if stored.schema_fingerprint != snapshot.schema_fingerprint:
        _blocked(
            preview_ref,
            f"Snapshot schema fingerprint does not match persisted evidence for entity {entity_id!r}.",
            details={
                "expected": stored.schema_fingerprint,
                "received": snapshot.schema_fingerprint,
            },
        )
    if stored.scope != snapshot.scope:
        _blocked(
            preview_ref,
            f"Snapshot scope does not match persisted evidence for entity {entity_id!r}.",
            details={},
        )
    return replace(snapshot, cache_status="stale") if lookup.status == "stale" else snapshot


def normalize_preview_bindings(
    *,
    ref: str,
    kind: SemanticKind,
    using: PreviewUsing,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    project_root: Path,
    catalog_definition_fingerprint: str,
) -> NormalizedPreviewBindings:
    """Validate exact snapshot/entity bindings without opening a datasource connection."""
    entity_ids = _dependency_entities(ref, kind, registry)
    if not entity_ids:
        _blocked(
            ref,
            f"catalog.preview() does not support {kind} refs.",
            details={"kind": str(kind)},
        )
    snapshots: tuple[DiscoverySnapshot, ...]
    if len(entity_ids) == 1:
        if not isinstance(using, DiscoverySnapshot):
            _blocked(
                ref,
                "Single-entity preview requires one DiscoverySnapshot in using=; a Mapping or positional value is not accepted.",
                details={"received_type": type(using).__name__},
            )
        snapshots = (using,)
    else:
        if not isinstance(using, Mapping):
            _blocked(
                ref,
                "Multi-entity preview requires a Mapping keyed by exact Ref[entity].",
                details={"received_type": type(using).__name__},
            )
        by_entity: dict[str, DiscoverySnapshot] = {}
        for key, snapshot in using.items():
            entity_id = _normalize_mapping_key(key, preview_ref=ref)
            if not isinstance(snapshot, DiscoverySnapshot):
                _blocked(
                    ref,
                    "Multi-entity preview Mapping values must be DiscoverySnapshot instances.",
                    details={"entity": entity_id, "received_type": type(snapshot).__name__},
                )
            if entity_id in by_entity:
                _blocked(
                    ref,
                    f"Multi-entity preview repeats entity binding {entity_id!r}.",
                    details={},
                )
            by_entity[entity_id] = snapshot
        if set(by_entity) != set(entity_ids):
            missing = tuple(entity_id for entity_id in entity_ids if entity_id not in by_entity)
            extra = tuple(entity_id for entity_id in by_entity if entity_id not in entity_ids)
            _blocked(
                ref,
                "Multi-entity preview Mapping must cover exactly the dependency entities.",
                details={"missing": missing, "unrelated": extra},
            )
        snapshots = tuple(by_entity[entity_id] for entity_id in entity_ids)

    snapshots = tuple(
        _validate_snapshot(
            snapshot,
            entity_id=entity_id,
            preview_ref=ref,
            project_root=project_root,
            registry=registry,
        )
        for entity_id, snapshot in zip(entity_ids, snapshots, strict=True)
    )
    datasource_ids = tuple(registry.entities[entity_id].datasource for entity_id in entity_ids)
    if len(set(datasource_ids)) != 1:
        _blocked(
            ref,
            "Scoped preview requires all dependency entities to share one datasource backend.",
            details={"datasources": datasource_ids},
        )
    datasource = registry.datasources[datasource_ids[0]]
    checked_ref = _exact_semantic_ref(ref, kind)
    return NormalizedPreviewBindings(
        checked_ref=checked_ref,
        entity_refs=tuple(ref_factory.entity(entity_id) for entity_id in entity_ids),
        snapshots=snapshots,
        backend=datasource.backend_type,
        datasource_id=datasource_ids[0],
        timeout_seconds=min(snapshot.scope.timeout_seconds for snapshot in snapshots),
        catalog_definition_fingerprint=catalog_definition_fingerprint,
        semantic_dependency_digest=dependency_digest(
            registry,
            sidecar=sidecar,
            semantic_refs=(checked_ref,),
        ),
    )


def normalize_preview_batch_bindings(
    *,
    refs: Sequence[tuple[str, SemanticKind]],
    using: PreviewUsing,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    project_root: Path,
    catalog_definition_fingerprint: str,
) -> tuple[NormalizedPreviewBindings, ...]:
    """Validate one exact snapshot binding set for a semantic ref batch."""
    batch_refs = tuple(ref for ref, _kind in refs)
    entity_ids = tuple(
        dict.fromkeys(
            entity_id
            for ref, kind in refs
            for entity_id in _dependency_entities(ref, kind, registry)
        )
    )
    if not entity_ids:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "catalog.preview_many(refs, using=...) requires at least one executable semantic ref.",
            cls=SemanticRuntimeError,
            refs=batch_refs,
            details={"query_executed": False},
        )

    by_entity: dict[str, DiscoverySnapshot]
    if len(entity_ids) == 1:
        if not isinstance(using, DiscoverySnapshot):
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "A batch with one dependency entity requires one DiscoverySnapshot in using=.",
                cls=SemanticRuntimeError,
                refs=batch_refs,
                details={"query_executed": False, "received_type": type(using).__name__},
            )
        by_entity = {entity_ids[0]: using}
    else:
        if not isinstance(using, Mapping):
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "A batch with multiple dependency entities requires a Mapping keyed by exact Ref[entity].",
                cls=SemanticRuntimeError,
                refs=batch_refs,
                details={"query_executed": False, "received_type": type(using).__name__},
            )
        by_entity = {}
        for key, snapshot in using.items():
            entity_id = _normalize_mapping_key(key, preview_ref=batch_refs[0])
            if not isinstance(snapshot, DiscoverySnapshot):
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    "Batch preview Mapping values must be DiscoverySnapshot instances.",
                    cls=SemanticRuntimeError,
                    refs=batch_refs,
                    details={
                        "query_executed": False,
                        "entity": entity_id,
                        "received_type": type(snapshot).__name__,
                    },
                )
            if entity_id in by_entity:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Batch preview repeats entity binding {entity_id!r}.",
                    cls=SemanticRuntimeError,
                    refs=batch_refs,
                    details={"query_executed": False},
                )
            by_entity[entity_id] = snapshot
        if set(by_entity) != set(entity_ids):
            missing = tuple(entity_id for entity_id in entity_ids if entity_id not in by_entity)
            extra = tuple(entity_id for entity_id in by_entity if entity_id not in entity_ids)
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "Batch preview Mapping must cover exactly the dependency entities.",
                cls=SemanticRuntimeError,
                refs=batch_refs,
                details={
                    "query_executed": False,
                    "missing": missing,
                    "unrelated": extra,
                },
            )

    normalized: list[NormalizedPreviewBindings] = []
    for ref, kind in refs:
        dependency_entities = _dependency_entities(ref, kind, registry)
        ref_using: PreviewUsing
        if len(dependency_entities) == 1:
            ref_using = by_entity[dependency_entities[0]]
        else:
            ref_using = {
                ref_factory.entity(entity_id): by_entity[entity_id]
                for entity_id in dependency_entities
            }
        normalized.append(
            normalize_preview_bindings(
                ref=ref,
                kind=kind,
                using=ref_using,
                registry=registry,
                sidecar=sidecar,
                project_root=project_root,
                catalog_definition_fingerprint=catalog_definition_fingerprint,
            )
        )
    return tuple(normalized)


@staged("persist")
def persist_preview_check(
    result: PreviewResult,
    *,
    bindings: NormalizedPreviewBindings,
    project_root: Path,
) -> PreviewResult:
    """Attach concrete snapshot coverage and persist only row-free check evidence."""
    coverage = PreviewCoverage(
        scopes=bindings.scopes,
        rows_observed=result.returned_row_count,
        scope_exhaustion=(
            "truncated"
            if any(
                snapshot.coverage.scope_exhaustion == "truncated" for snapshot in bindings.snapshots
            )
            else "exhaustive"
        ),
        scope_exactness=(
            "sample_only"
            if any(
                snapshot.coverage.scope_exactness == "sample_only"
                for snapshot in bindings.snapshots
            )
            else "scope_exact"
        ),
        snapshot_ids=tuple(snapshot.id for snapshot in bindings.snapshots),
        cache_status=(
            "stale"
            if any(snapshot.cache_status == "stale" for snapshot in bindings.snapshots)
            else (
                "cached"
                if any(snapshot.cache_status == "cached" for snapshot in bindings.snapshots)
                else "fresh"
            )
        ),
    )
    enriched = replace(result, status="passed", coverage=coverage)
    created_at = datetime.now(UTC)
    expires_at = min(snapshot.expires_at for snapshot in bindings.snapshots)
    entity_snapshot_bindings = tuple(
        EntitySnapshotBindingV1(
            entity_ref=RefPayloadV1.from_ref(entity_ref),
            snapshot_id=snapshot.id,
        )
        for entity_ref, snapshot in zip(bindings.entity_refs, bindings.snapshots, strict=True)
    )
    checked_payload = RefPayloadV1.from_ref(bindings.checked_ref)
    check = PreviewCheckV1(
        schema="marivo.semantic_preview_check/v1",
        id=_preview_check_id(
            checked_ref=checked_payload,
            catalog_definition_fingerprint=bindings.catalog_definition_fingerprint,
            semantic_dependency_digest=bindings.semantic_dependency_digest,
            entity_snapshot_bindings=entity_snapshot_bindings,
            backend=bindings.backend,
        ),
        checked_ref=checked_payload,
        catalog_definition_fingerprint=bindings.catalog_definition_fingerprint,
        semantic_dependency_digest=bindings.semantic_dependency_digest,
        entity_snapshot_bindings=entity_snapshot_bindings,
        backend=bindings.backend,
        status="passed",
        rows_observed=coverage.rows_observed,
        scope_exhaustion=coverage.scope_exhaustion,
        types=tuple(sorted(enriched.types.items())),
        warnings=tuple(warning.message for warning in enriched.warnings),
        created_at=created_at,
        expires_at=expires_at,
    )
    AuthoringStore(project_root).write_preview_check(check)
    return enriched
