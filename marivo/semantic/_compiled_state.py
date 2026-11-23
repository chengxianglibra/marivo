"""Immutable compiled semantic state constructed by the loader."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from marivo._definition.deferred import root_declaration
from marivo._definition.expressions import MetricNode, RowNode
from marivo._definition.graph import computation_fingerprint, direct_refs
from marivo._kernel.refs import Ref, SemanticKindTag
from marivo._kernel.refs import ref as ref_factory
from marivo.datasource.spi import AiContextIR
from marivo.datasource.typing import AiContextValue
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic._definition_identity import definition_fingerprint
from marivo.semantic.compiled_types import (
    CompiledRootIdentity,
    CompiledSemanticState,
    PublishedDeclaration,
)
from marivo.semantic.expressions.ir import CompiledExpressionSidecar
from marivo.semantic.ir import (
    DimensionIR,
    EntityIR,
    HourPrefixParse,
    MeasureIR,
    RelationshipIR,
    SemiAdditive,
    SnapshotVersioningIR,
    ValidityVersioningIR,
)
from marivo.semantic.policies import (
    AdditiveAllV1,
    AdditiveOverV1,
    AdditivityPolicy,
)
from marivo.semantic.static_check.registry import Registry


def _captured_context(context: AiContextValue | None) -> AiContextIR:
    if context is None:
        return AiContextIR()
    return AiContextIR(
        business_definition=context.business_definition,
        guardrails=context.guardrails,
        aliases=context.aliases,
    )


def _published_nodes(registry: Registry) -> dict[Ref[SemanticKindTag], MetricNode | RowNode]:

    nodes: dict[Ref[SemanticKindTag], MetricNode | RowNode] = {}
    for key, measure in registry.declared_measures.items():
        nodes[ref_factory.measure(key)] = consume_row(measure).node
    for key, metric in registry.declared_metrics.items():
        nodes[ref_factory.metric(key)] = consume_metric(metric).node
    return nodes


def _definition_rows(registry: Registry) -> dict[Ref[SemanticKindTag], object]:
    rows: dict[Ref[SemanticKindTag], object] = {}
    rows.update((ref_factory.domain(key), value) for key, value in registry.domains.items())
    rows.update((ref_factory.datasource(key), value) for key, value in registry.datasources.items())
    rows.update((ref_factory.entity(key), value) for key, value in registry.entities.items())
    for key, value in registry.dimensions.items():
        ref = (
            ref_factory.time_dimension(key)
            if value.is_time_dimension
            else ref_factory.dimension(key)
        )
        rows[ref] = value
    rows.update((ref_factory.measure(key), value) for key, value in registry.measures.items())
    rows.update(
        (ref_factory.relationship(key), value) for key, value in registry.relationships.items()
    )

    for key, measure in registry.declared_measures.items():
        declaration = root_declaration(measure)
        assert declaration is not None
        facts = consume_row(measure)
        rows[ref_factory.measure(key)] = PublishedDeclaration(
            "measure",
            declaration.name,
            computation_fingerprint(facts.node),
            facts.unit,
            _captured_context(declaration.ai_context),
        )
    for key, metric in registry.declared_metrics.items():
        declaration = root_declaration(metric)
        assert declaration is not None
        metric_facts = consume_metric(metric)
        rows[ref_factory.metric(key)] = PublishedDeclaration(
            "metric",
            declaration.name,
            computation_fingerprint(metric_facts.node),
            metric_facts.unit,
            _captured_context(declaration.ai_context),
        )
    return rows


def _ref_for_path(registry: Registry, path: str) -> Ref[SemanticKindTag] | None:
    if path in registry.datasources:
        return ref_factory.datasource(path)
    if path in registry.entities:
        return ref_factory.entity(path)
    dimension = registry.dimensions.get(path)
    if dimension is not None:
        ref = (
            ref_factory.time_dimension(path)
            if dimension.is_time_dimension
            else ref_factory.dimension(path)
        )
        return ref
    if path in registry.measures:
        return ref_factory.measure(path)
    if path in registry.relationships:
        return ref_factory.relationship(path)
    if path in registry.domains:
        return ref_factory.domain(path)
    return None


def _additivity_axes(policy: AdditivityPolicy | None) -> tuple[str, ...]:
    """Coordinate paths a declared additivity policy names; they define its meaning."""
    if isinstance(policy, AdditiveOverV1):
        return policy.coordinates
    if isinstance(policy, AdditiveAllV1):
        return policy.exceptions
    return ()


def _dependencies_for(
    ref: Ref[SemanticKindTag],
    definition: object,
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
) -> tuple[Ref[SemanticKindTag], ...]:
    paths: list[str] = []
    if isinstance(definition, EntityIR):
        paths.append(definition.datasource)
        if definition.default_time is not None:
            paths.append(definition.default_time)
        if isinstance(definition.versioning, SnapshotVersioningIR):
            paths.append(definition.versioning.partition_field)
        elif isinstance(definition.versioning, ValidityVersioningIR):
            paths.extend((definition.versioning.valid_from, definition.versioning.valid_to))
    elif isinstance(definition, (DimensionIR, MeasureIR)):
        paths.append(definition.entity)
        if isinstance(definition, DimensionIR) and isinstance(definition.parse, HourPrefixParse):
            paths.append(definition.parse.prefix)
        if isinstance(definition, MeasureIR):
            if isinstance(definition.additivity, SemiAdditive):
                paths.append(definition.additivity.over)
            paths.extend(_additivity_axes(definition.dsl_additivity))
            if definition.status_time_dimension is not None:
                paths.append(definition.status_time_dimension)
    elif isinstance(definition, RelationshipIR):
        paths.extend((definition.from_entity, definition.to_entity))
        for key in definition.keys:
            paths.extend((key.from_key, key.to_key))
    body = sidecar.bodies.get(ref)
    if body is not None:
        paths.extend(binding.field_ref.path for binding in body.bindings)
    dependencies = {
        resolved
        for path in paths
        if (resolved := _ref_for_path(registry, path)) is not None and resolved != ref
    }
    return tuple(sorted(dependencies, key=lambda item: item.key))


def definition_graph(
    registry: Registry, sidecar: CompiledExpressionSidecar
) -> tuple[
    dict[Ref[SemanticKindTag], object],
    dict[Ref[SemanticKindTag], tuple[Ref[SemanticKindTag], ...]],
]:
    """Definition rows and their direct semantic dependencies for one registry."""
    definitions = _definition_rows(registry)
    dependencies = {
        ref: _dependencies_for(ref, definition, registry=registry, sidecar=sidecar)
        for ref, definition in definitions.items()
    }
    for ref, node in _published_nodes(registry).items():
        # Collection admits a declaration only when every consumed ref is
        # defined here, so no edge is filtered out.
        dependencies[ref] = tuple(
            sorted(
                ({exact.ref for exact in direct_refs(node)}) - {ref},
                key=lambda item: item.key,
            )
        )
    return definitions, dependencies


def dependent_closure(
    roots: set[Ref[SemanticKindTag]],
    dependencies: Mapping[Ref[SemanticKindTag], Iterable[Ref[SemanticKindTag]]],
) -> set[Ref[SemanticKindTag]]:
    """Return the roots and every definition that depends on them."""
    dependents: dict[Ref[SemanticKindTag], list[Ref[SemanticKindTag]]] = {}
    for ref, inputs in dependencies.items():
        for dependency in inputs:
            dependents.setdefault(dependency, []).append(ref)
    excluded = set(roots)
    pending = list(roots)
    while pending:
        for dependent in dependents.get(pending.pop(), ()):
            if dependent not in excluded:
                excluded.add(dependent)
                pending.append(dependent)
    return excluded


@dataclass(slots=True)
class CompilationGraph:
    """Load-owned append-only graph; never retained by a published Catalog."""

    registry: Registry
    sidecar: CompiledExpressionSidecar
    definitions: dict[Ref[SemanticKindTag], object]
    dependencies: dict[Ref[SemanticKindTag], tuple[Ref[SemanticKindTag], ...]]

    @classmethod
    def create(cls, registry: Registry, sidecar: CompiledExpressionSidecar) -> CompilationGraph:
        definitions, dependencies = definition_graph(registry, sidecar)
        return cls(registry, sidecar, definitions, dependencies)

    def append(
        self,
        reference: Ref[SemanticKindTag],
        *,
        stage: Literal["measure", "metric"],
        name: str,
        node: MetricNode | RowNode,
        unit: str | None,
        ai_context: AiContextValue | None,
        captured_dependencies: tuple[Ref[SemanticKindTag], ...] = (),
    ) -> None:
        self.definitions[reference] = PublishedDeclaration(
            stage, name, computation_fingerprint(node), unit, _captured_context(ai_context)
        )
        self.dependencies[reference] = tuple(
            sorted(
                ({exact.ref for exact in direct_refs(node)} | set(captured_dependencies))
                - {reference},
                key=lambda item: item.key,
            )
        )


def build_compiled_state(
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    selected_root_roles: Sequence[str],
    filtered_domains: Sequence[str],
    graph: CompilationGraph | None = None,
) -> CompiledSemanticState:
    """Copy and freeze all compiled values after successful validation."""
    if graph is None:
        definitions, dependencies = definition_graph(registry, sidecar)
    else:
        assert graph.registry is registry and graph.sidecar is sidecar
        definitions, dependencies = graph.definitions, graph.dependencies
    selected_roots = tuple(
        CompiledRootIdentity(role=role, order=index)
        for index, role in enumerate(selected_root_roles)
    )
    fingerprint = definition_fingerprint(
        selected_root_roles=selected_root_roles,
        filtered_domains=filtered_domains,
        definitions=definitions,
        dependencies=dependencies,
        sidecar=sidecar,
    )
    registry.freeze()
    return CompiledSemanticState(
        definition_fingerprint=fingerprint,
        selected_roots=selected_roots,
        filtered_domains=tuple(filtered_domains),
        registry=registry,
        definitions=definitions,
        dependencies=dependencies,
        sidecar=sidecar,
    )
