"""Snapshot binding and row-free evidence for semantic runtime previews."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.authoring_store import (
    CHECK_FORMAT_VERSION,
    AuthoringStore,
    datasource_spec_fingerprint,
    preview_check_scope_payload,
    snapshot_identity,
)
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
from marivo.datasource.snapshot import DiscoverySnapshot
from marivo.datasource.source import AuthoringScope, PartitionScope, UnprunedScope
from marivo.preview import PreviewCoverage, PreviewResult
from marivo.refs import SemanticRef, SymbolKind
from marivo.semantic._authoring_validation import _compute_body_ast_hash
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.ir import (
    CumulativeComposition,
    EntityIR,
    SemiAdditive,
    composition_components,
)

if TYPE_CHECKING:
    from marivo.semantic.catalog import Entity
    from marivo.semantic.validator import Registry, Sidecar

type PreviewUsing = DiscoverySnapshot | Mapping[Entity | SemanticRef, DiscoverySnapshot]


@dataclass(frozen=True)
class PreviewCheck:
    id: str
    semantic_ref: str
    semantic_fingerprint: str
    dependency_fingerprint: str
    snapshot_ids: tuple[str, ...]
    backend: str
    status: Literal["passed"]
    scopes: tuple[tuple[str, AuthoringScope], ...]
    rows_observed: int
    scope_exhaustion: Literal["exhaustive", "truncated"]
    types: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class NormalizedPreviewBindings:
    semantic_ref: str
    entity_ids: tuple[str, ...]
    snapshots: tuple[DiscoverySnapshot, ...]
    scopes: tuple[tuple[str, AuthoringScope], ...]
    backend: str
    datasource_id: str
    timeout_seconds: int
    semantic_fingerprint: str
    dependency_fingerprint: str

    @property
    def entity_scopes(self) -> Mapping[str, AuthoringScope]:
        return dict(self.scopes)


@dataclass(frozen=True)
class PreviewEvidenceRequirement:
    """Query-free readiness state for one directly requested executable ref."""

    status: Literal["matched", "snapshot_missing", "runtime_preview_missing"]
    suggested_action: str


def _blocked(ref: str, message: str, *, details: Mapping[str, object]) -> NoReturn:
    _raise(
        ErrorKind.MATERIALIZE_FAILED,
        message,
        cls=SemanticRuntimeError,
        refs=(ref,),
        details={"query_executed": False, **details},
    )


def _dependency_entities(ref: str, kind: SymbolKind, registry: Registry) -> tuple[str, ...]:
    entities = registry.entities
    dimensions = registry.dimensions
    measures = registry.measures
    metrics = registry.metrics
    if kind == SymbolKind.ENTITY:
        return (ref,)
    if kind in {SymbolKind.DIMENSION, SymbolKind.TIME_DIMENSION}:
        return (dimensions[ref].entity,)
    if kind == SymbolKind.MEASURE:
        return (measures[ref].entity,)
    if kind == SymbolKind.RELATIONSHIP:
        relationship = registry.relationships[ref]
        return tuple(dict.fromkeys((relationship.from_entity, relationship.to_entity)))
    if kind != SymbolKind.METRIC:
        return ()

    ordered: list[str] = []
    visited_metrics: set[str] = set()

    def visit(metric_id: str) -> None:
        if metric_id in visited_metrics:
            return
        visited_metrics.add(metric_id)
        metric = metrics[metric_id]
        for entity_id in metric.entities:
            if entity_id in entities and entity_id not in ordered:
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
    from marivo.semantic.catalog import Entity

    if isinstance(key, Entity):
        return key.ref.id
    if isinstance(key, SemanticRef):
        if key.kind != SymbolKind.ENTITY:
            _blocked(
                preview_ref,
                "catalog.preview(..., using=...) Mapping keys must be entity-kind SemanticRef values.",
                details={"received_kind": str(key.kind)},
            )
        return key.id
    _blocked(
        preview_ref,
        "catalog.preview(..., using=...) Mapping requires Entity or entity SemanticRef keys; bare strings are not entity keys.",
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


def _stable_value(value: object) -> object:
    if isinstance(value, SemanticRef):
        return {"id": value.id, "kind": value.kind.value}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _stable_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _stable_value(item)) for key, item in value.items()))
    if isinstance(value, tuple | list):
        return tuple(_stable_value(item) for item in value)
    return value


def _fingerprint(value: object) -> str:
    return hashlib.sha256(repr(_stable_value(value)).encode("utf-8")).hexdigest()


def _preview_check_id(
    *,
    semantic_fingerprint: str,
    dependency_fingerprint: str,
    snapshot_ids: tuple[str, ...],
    backend: str | None,
) -> str:
    return _fingerprint(
        (
            semantic_fingerprint,
            dependency_fingerprint,
            snapshot_ids,
            backend,
            CHECK_FORMAT_VERSION,
        )
    )


def _semantic_ir(ref: str, kind: SymbolKind, registry: Registry) -> object:
    collection_name = {
        SymbolKind.ENTITY: "entities",
        SymbolKind.DIMENSION: "dimensions",
        SymbolKind.TIME_DIMENSION: "dimensions",
        SymbolKind.MEASURE: "measures",
        SymbolKind.METRIC: "metrics",
        SymbolKind.RELATIONSHIP: "relationships",
    }.get(kind)
    if collection_name is None:
        return ref
    return getattr(registry, collection_name)[ref]


def _semantic_kind(ref: str, registry: Registry) -> SymbolKind:
    if ref in registry.entities:
        return SymbolKind.ENTITY
    if ref in registry.dimensions:
        return (
            SymbolKind.TIME_DIMENSION
            if registry.dimensions[ref].is_time_dimension
            else SymbolKind.DIMENSION
        )
    if ref in registry.measures:
        return SymbolKind.MEASURE
    if ref in registry.metrics:
        return SymbolKind.METRIC
    if ref in registry.relationships:
        return SymbolKind.RELATIONSHIP
    raise KeyError(ref)


def _semantic_payload(
    ref: str,
    *,
    registry: Registry,
    sidecar: Sidecar,
) -> tuple[str, str, object, str | None]:
    kind = _semantic_kind(ref, registry)
    callable_ = sidecar.get(ref)
    body_hash = _compute_body_ast_hash(callable_) if callable_ is not None else None
    return (kind.value, ref, _semantic_ir(ref, kind, registry), body_hash)


def _semantic_dependency_payloads(
    ref: str,
    kind: SymbolKind,
    *,
    registry: Registry,
    sidecar: Sidecar,
) -> tuple[tuple[str, str, object, str | None], ...]:
    """Return the narrow executable dependency closure for one preview ref."""
    dependency_ids: set[str] = set()
    visited_metrics: set[str] = set()

    def add_dimension(dimension_id: str) -> None:
        dimension = registry.dimensions.get(dimension_id)
        if dimension is None:
            return
        dependency_ids.add(dimension_id)
        dependency_ids.add(dimension.entity)

    def add_measure(measure_id: str) -> None:
        measure = registry.measures.get(measure_id)
        if measure is None:
            return
        dependency_ids.add(measure_id)
        dependency_ids.add(measure.entity)
        if isinstance(measure.additivity, SemiAdditive):
            add_dimension(measure.additivity.over)

    def add_metric(metric_id: str) -> None:
        if metric_id in visited_metrics:
            return
        metric = registry.metrics.get(metric_id)
        if metric is None:
            return
        visited_metrics.add(metric_id)
        if metric_id != ref:
            dependency_ids.add(metric_id)
        dependency_ids.update(metric.entities)
        target_id = metric.aggregation_target or metric.measure
        if target_id is not None:
            if metric.aggregation_target_kind == "entity":
                dependency_ids.add(target_id)
            else:
                add_measure(target_id)
        if isinstance(metric.additivity, SemiAdditive):
            add_dimension(metric.additivity.over)
        if metric.composition is not None:
            for component_id in composition_components(metric.composition).values():
                add_metric(component_id)
            if (
                isinstance(metric.composition, CumulativeComposition)
                and metric.composition.over is not None
            ):
                add_dimension(metric.composition.over)

    if kind in {SymbolKind.DIMENSION, SymbolKind.TIME_DIMENSION}:
        dependency_ids.add(registry.dimensions[ref].entity)
    elif kind == SymbolKind.MEASURE:
        measure = registry.measures[ref]
        dependency_ids.add(measure.entity)
        if isinstance(measure.additivity, SemiAdditive):
            add_dimension(measure.additivity.over)
    elif kind == SymbolKind.METRIC:
        add_metric(ref)
    elif kind == SymbolKind.RELATIONSHIP:
        relationship = registry.relationships[ref]
        dependency_ids.update((relationship.from_entity, relationship.to_entity))
        for key in relationship.keys:
            for dimension_id in key.to_tuple():
                add_dimension(dimension_id)

    entity_ids = {item for item in dependency_ids if item in registry.entities}
    for relationship_id, relationship in registry.relationships.items():
        if (
            relationship_id != ref
            and relationship.from_entity in entity_ids
            and relationship.to_entity in entity_ids
        ):
            dependency_ids.add(relationship_id)

    return tuple(
        _semantic_payload(item, registry=registry, sidecar=sidecar)
        for item in sorted(
            dependency_ids, key=lambda item: (_semantic_kind(item, registry).value, item)
        )
    )


def preview_fingerprints(
    ref: str,
    *,
    registry: Registry,
    sidecar: Sidecar,
) -> tuple[str, str]:
    """Return current semantic and transitive dependency fingerprints without I/O."""
    kind = _semantic_kind(ref, registry)
    return (
        _fingerprint(_semantic_payload(ref, registry=registry, sidecar=sidecar)),
        _fingerprint(_semantic_dependency_payloads(ref, kind, registry=registry, sidecar=sidecar)),
    )


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
    return f"md.inspect(md.ref({_quoted(datasource)}), {_source_call(source)})"


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
            datasource=DatasourceRef.from_id(entity.datasource),
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
    sidecar: Sidecar,
    project_root: Path,
) -> PreviewEvidenceRequirement:
    """Read persisted row-free evidence for readiness without acquiring or executing."""
    kind = _semantic_kind(ref, registry)
    entity_ids = _dependency_entities(ref, kind, registry)
    semantic_fingerprint, dependency_fingerprint = preview_fingerprints(
        ref, registry=registry, sidecar=sidecar
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
            if payload is None or payload.get("semantic_ref") != ref:
                continue
            try:
                check_id = payload["id"]
                expires_at = datetime.fromisoformat(str(payload["expires_at"]))
                created_at = datetime.fromisoformat(str(payload["created_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(check_id, str):
                continue
            snapshot_ids = payload.get("snapshot_ids")
            snapshots_match = (
                isinstance(snapshot_ids, list)
                and len(snapshot_ids) == len(entity_ids)
                and all(
                    isinstance(snapshot_id, str)
                    and snapshot_id in snapshot_ids_by_entity[entity_id]
                    for entity_id, snapshot_id in zip(entity_ids, snapshot_ids, strict=True)
                )
            )
            bound_snapshots = (
                tuple(snapshots_by_id[snapshot_id] for snapshot_id in snapshot_ids)
                if snapshots_match and isinstance(snapshot_ids, list)
                else ()
            )
            expected_scopes = (
                json.loads(
                    json.dumps(
                        [
                            [entity_id, preview_check_scope_payload(snapshot.scope)]
                            for entity_id, snapshot in zip(entity_ids, bound_snapshots, strict=True)
                        ]
                    )
                )
                if bound_snapshots
                else []
            )
            expected_id = _preview_check_id(
                semantic_fingerprint=semantic_fingerprint,
                dependency_fingerprint=dependency_fingerprint,
                snapshot_ids=tuple(snapshot_ids) if isinstance(snapshot_ids, list) else (),
                backend=expected_backend,
            )
            if (
                payload.get("status") == "passed"
                and path == store.check_dir / f"{check_id}.json"
                and check_id == expected_id
                and expires_at.tzinfo is not None
                and created_at.tzinfo is not None
                and expires_at > now
                and created_at <= now
                and created_at <= expires_at
                and payload.get("semantic_fingerprint") == semantic_fingerprint
                and payload.get("dependency_fingerprint") == dependency_fingerprint
                and payload.get("backend") == expected_backend
                and snapshots_match
                and payload.get("scopes") == expected_scopes
                and bound_snapshots
                and created_at >= max(snapshot.created_at for snapshot in bound_snapshots)
                and expires_at == min(snapshot.expires_at for snapshot in bound_snapshots)
            ):
                return PreviewEvidenceRequirement("matched", "")
    missing_entities = tuple(entity_id for entity_id in entity_ids if entity_id not in snapshots)
    if missing_entities:
        calls = tuple(_inspect_call(registry.entities[entity_id]) for entity_id in missing_entities)
        return PreviewEvidenceRequirement("snapshot_missing", "\n".join(calls))

    sample_calls = {
        entity_id: _snapshot_sample_call(registry.entities[entity_id], snapshots[entity_id])
        for entity_id in entity_ids
    }
    typed_ref = f"catalog.get({_quoted(kind.value + '.' + ref)})"
    if len(entity_ids) == 1:
        using = sample_calls[entity_ids[0]]
    else:
        mapping_items = "\n".join(
            f"        catalog.get({_quoted('entity.' + entity_id)}): {sample_calls[entity_id]},"
            for entity_id in entity_ids
        )
        using = "{\n" + mapping_items + "\n    }"
    return PreviewEvidenceRequirement(
        "runtime_preview_missing",
        f"catalog.preview(\n    {typed_ref},\n    using={using},\n)",
    )


def _validate_snapshot(
    snapshot: DiscoverySnapshot,
    *,
    entity_id: str,
    preview_ref: str,
    project_root: Path,
    registry: Registry,
) -> None:
    entity = registry.entities[entity_id]
    if snapshot._project_root.resolve() != project_root.resolve():
        _blocked(
            preview_ref,
            f"Snapshot {snapshot.id!r} belongs to a different project.",
            details={"entity": entity_id},
        )
    expected_datasource = DatasourceRef.from_id(entity.datasource)
    if snapshot.datasource != expected_datasource:
        _blocked(
            preview_ref,
            f"Snapshot datasource does not match entity {entity_id!r}.",
            details={"expected": expected_datasource.id, "received": snapshot.datasource.id},
        )
    if snapshot.source != entity.source:
        _blocked(
            preview_ref,
            f"Snapshot physical source does not match entity {entity_id!r}.",
            details={"expected": entity.source.to_dict(), "received": snapshot.source.to_dict()},
        )
    _validate_scope(snapshot.scope, preview_ref=preview_ref)
    now = datetime.now(UTC)
    if snapshot.expires_at <= now:
        _blocked(
            preview_ref,
            f"Snapshot {snapshot.id!r} is not fresh.",
            details={"expires_at": snapshot.expires_at.isoformat()},
        )
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
            f"Snapshot {snapshot.id!r} is missing, stale, or mismatched in the authoring store.",
            details={"cache_status": lookup.status},
        )
    if stored.created_at != snapshot.created_at or stored.expires_at != snapshot.expires_at:
        _blocked(
            preview_ref,
            f"Snapshot freshness metadata does not match persisted evidence for entity {entity_id!r}.",
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


def normalize_preview_bindings(
    *,
    ref: str,
    kind: SymbolKind,
    using: PreviewUsing,
    registry: Registry,
    sidecar: Sidecar,
    project_root: Path,
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
                "Multi-entity preview requires a Mapping keyed by Entity or entity SemanticRef.",
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

    for entity_id, snapshot in zip(entity_ids, snapshots, strict=True):
        _validate_snapshot(
            snapshot,
            entity_id=entity_id,
            preview_ref=ref,
            project_root=project_root,
            registry=registry,
        )
    datasource_ids = tuple(registry.entities[entity_id].datasource for entity_id in entity_ids)
    if len(set(datasource_ids)) != 1:
        _blocked(
            ref,
            "Scoped preview requires all dependency entities to share one datasource backend.",
            details={"datasources": datasource_ids},
        )
    datasource = registry.datasources[datasource_ids[0]]
    semantic_payload = _semantic_payload(ref, registry=registry, sidecar=sidecar)
    dependencies = _semantic_dependency_payloads(
        ref,
        kind,
        registry=registry,
        sidecar=sidecar,
    )
    return NormalizedPreviewBindings(
        semantic_ref=ref,
        entity_ids=entity_ids,
        snapshots=snapshots,
        scopes=tuple(
            (entity_id, snapshot.scope)
            for entity_id, snapshot in zip(entity_ids, snapshots, strict=True)
        ),
        backend=datasource.backend_type,
        datasource_id=datasource_ids[0],
        timeout_seconds=min(snapshot.scope.timeout_seconds for snapshot in snapshots),
        semantic_fingerprint=_fingerprint(semantic_payload),
        dependency_fingerprint=_fingerprint(dependencies),
    )


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
            "cached"
            if any(snapshot.cache_status == "cached" for snapshot in bindings.snapshots)
            else "fresh"
        ),
    )
    enriched = replace(result, status="passed", coverage=coverage)
    created_at = datetime.now(UTC)
    expires_at = min(snapshot.expires_at for snapshot in bindings.snapshots)
    check = PreviewCheck(
        id=_preview_check_id(
            semantic_fingerprint=bindings.semantic_fingerprint,
            dependency_fingerprint=bindings.dependency_fingerprint,
            snapshot_ids=coverage.snapshot_ids,
            backend=bindings.backend,
        ),
        semantic_ref=bindings.semantic_ref,
        semantic_fingerprint=bindings.semantic_fingerprint,
        dependency_fingerprint=bindings.dependency_fingerprint,
        snapshot_ids=coverage.snapshot_ids,
        backend=bindings.backend,
        status="passed",
        scopes=coverage.scopes,
        rows_observed=coverage.rows_observed,
        scope_exhaustion=coverage.scope_exhaustion,
        types=tuple(sorted(enriched.types.items())),
        warnings=tuple(warning.message for warning in enriched.warnings),
        created_at=created_at,
        expires_at=expires_at,
    )
    AuthoringStore(project_root).write_preview_check(check)
    return enriched
