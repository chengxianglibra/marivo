"""Shared governed historical Dimension capture for entry and checkpoint axes."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis.core.domain_captures import EntryAxisCapture
from marivo.analysis.core.graph import (
    SourceDefinition,
    SourceLeaf,
    topology,
)
from marivo.analysis.core.model import (
    Binding,
)
from marivo.analysis.core.rules import (
    entity_members,
)
from marivo.analysis.materialization.graph_preflight import preflight_entities
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.materialization.graph_relation import (
    LiveBinding,
    Relation,
)
from marivo.analysis.observation.coordinates import functional_path
from marivo.refs import DimensionKind, Ref, ref
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.validator import normalize_target_dimension, normalize_target_relationship


def capture_axes(
    relation: Relation,
    axes: tuple[Ref[DimensionKind], ...],
    subject: TargetEntityContract,
    binding: Binding,
    source_id: str,
) -> tuple[Relation, tuple[EntryAxisCapture, ...], tuple[SourceLeaf, ...]]:
    live = relation.binding
    assert isinstance(live, LiveBinding)
    registry = live.graph.registry
    dimensions = tuple(normalize_target_dimension(registry, a.path) for a in axes)
    paths = tuple(
        functional_path(
            registry,
            subject.ref.path,
            a.entity_ref.path,
            allow_versioned_target=True,
            allow_versioned_source=True,
            allow_versioned_intermediates=True,
        )
        for a in dimensions
    )
    routes = tuple(
        tuple(normalize_target_relationship(registry, name) for name in path) for path in paths
    )
    entity_paths = []
    for route in routes:
        names = [subject.ref.path]
        for hop in route:
            names.append(
                hop.to_entity_ref.path
                if hop.from_entity_ref.path == names[-1]
                else hop.from_entity_ref.path
            )
        entity_paths.append(tuple(names))
    schemas = preflight_entities(
        registry,
        relation.runtime.store.project_root,
        tuple(sorted({name for names in entity_paths for name in names})),
    )
    entries = {leaf.definition.ref.path: (schema, leaf) for schema, leaf in live.graph.sources}
    shape_leaf = next(
        n for n in topology(relation.root) if isinstance(n, SourceLeaf) and n.identity == source_id
    )
    for schema in schemas:
        if schema.contract.ref.path in entries:
            continue
        leaf = SourceLeaf(
            SourceDefinition(
                ref.entity(schema.contract.ref.path),
                digest(schema.contract.dependency_fingerprint + schema_text(schema.schema)),
                ref.datasource(schema.contract.datasource_ref.path),
                replace(schema.shape, time=shape_leaf.definition.shape.time),
                schema.contract.version,
            ),
            replace(
                entity_members(
                    replace(schema.contract, version=None),
                    ref.entity(schema.contract.ref.path),
                    binding,
                ),
                obligations=(),
            ),
            schema.identity_type,
        )
        entries[schema.contract.ref.path] = (schema, leaf)
    captures = tuple(
        EntryAxisCapture(
            replace(
                dimension,
                logical_type=str(
                    entries[dimension.entity_ref.path][0].schema.field(dimension.source_column).type
                ),
            ),
            subject,
            route,
            tuple(entries[name][0].contract for name in names),
            tuple(entries[name][1].identity for name in names),
        )
        for dimension, route, names in zip(dimensions, routes, entity_paths, strict=True)
    )
    relation = replace(
        relation,
        binding=replace(
            live,
            graph=replace(live.graph, sources=tuple(entries[name] for name in sorted(entries))),
        ),
    )
    sources = tuple(
        entries[name][1] for name in sorted({name for names in entity_paths for name in names})
    )
    return relation, captures, sources
