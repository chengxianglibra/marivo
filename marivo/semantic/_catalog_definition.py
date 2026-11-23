"""Source-free projections of one immutable compiled catalog graph."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Protocol

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import children
from marivo._definition.types import ExactRef
from marivo._kernel.refs import Ref, SemanticKind, SemanticKindTag
from marivo._kernel.temporal import Grain
from marivo.datasource.spi import AiContextIR
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic._formula_display import FormulaDisplay
from marivo.semantic.catalog_entries import (
    DatasourceDefinition,
    DependencyContext,
    DomainDefinition,
    EntityDefinition,
    EntryDefinition,
    FieldDefinition,
    MetricDefinition,
    RelationshipDefinition,
    _freeze_json,
)
from marivo.semantic.catalog_model.details import DatasourceDetails
from marivo.semantic.catalog_model.inputs import (
    CatalogInputLookup,
    catalog_entry_dependencies,
    require_catalog_index,
)
from marivo.semantic.catalog_model.objects import entry_details, lookup_registry, lookup_state
from marivo.semantic.compiled_types import PublishedDeclaration
from marivo.semantic.expressions.objects import ExpressionContract
from marivo.semantic.ir import DimensionIR, EntityIR, RelationshipIR
from marivo.semantic.metric_time_bindings import (
    metric_time_bindings,
)
from marivo.semantic.static_check.targets import normalize_target_relationship


def context_for(catalog: DefinitionLookup, ref: Ref[SemanticKindTag]) -> AiContextIR:
    definition = lookup_state(catalog).definitions.get(ref)
    context = getattr(definition, "ai_context", None)
    if isinstance(context, AiContextIR):
        return context
    entry = require_catalog_index(catalog).entry(ref)
    if entry is not None:
        details = entry_details(entry)
        context = getattr(details, "context", None)
        if isinstance(context, AiContextIR):
            return context
    return AiContextIR()


def dependency_context(
    catalog: DefinitionLookup, ref: Ref[SemanticKindTag]
) -> tuple[DependencyContext, ...]:
    return tuple(
        DependencyContext(
            item.key,
            context_for(catalog, item).business_definition,
            context_for(catalog, item).guardrails,
        )
        for item in lookup_state(catalog).dependencies.get(ref, ())
        if item in require_catalog_index(catalog)
    )


def _inert_text(value: object) -> str:
    """Only captured dataclass facts; never resolver or executable reprs."""

    if isinstance(value, ExactRef):
        return value.ref.key
    if isinstance(value, Grain):
        return repr(value)
    if isinstance(value, Enum):
        return _inert_text(value.value)
    if isinstance(value, (date, datetime, timedelta, Decimal)):
        return repr(value)
    if isinstance(value, Mapping):
        return (
            "{"
            + ", ".join(f"{_inert_text(key)}: {_inert_text(item)}" for key, item in value.items())
            + "}"
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return repr(value)
    if isinstance(value, tuple):
        return "(" + ", ".join(_inert_text(item) for item in value) + ")"
    if is_dataclass(value) and not isinstance(value, type):
        return (
            type(value).__name__
            + "("
            + ", ".join(
                f"{item.name}={_inert_text(getattr(value, item.name))}"
                for item in fields(value)
                if item.name not in {"recipe", "owner", "version", "_from_default_time"}
            )
            + ")"
        )
    return type(value).__name__


def _null_rule(
    node: SourceAggregate | SourceWeightedMean | EntityCount | PerReduce | PerWeightedMean,
) -> str:
    """Describe the captured reducer's policy without qualifying execution."""
    if isinstance(node, EntityCount):
        return "count counts source rows; empty input returns zero"
    domain = "sample results" if isinstance(node, (PerReduce, PerWeightedMean)) else "contributions"
    if node.nulls == "error":
        policy = f"assert non-NULL demanded {domain}; a demanded NULL raises an error"
    elif isinstance(node, PerReduce) and node.include_null:
        policy = "include NULL sample results in the ordered selection"
    else:
        policy = f"ignore NULL {domain}"
    if isinstance(node, (SourceWeightedMean, PerWeightedMean)):
        empty = "no valid pairs or zero paired weight sum returns NULL"
    elif node.reducer == "sum":
        empty = "empty input returns typed zero"
        if node.nulls == "ignore":
            empty += "; nonempty all-NULL input returns NULL"
    elif node.reducer in {"count", "count_distinct", "approx_nunique"}:
        empty = "empty input returns zero"
        if node.nulls == "ignore":
            empty += "; all-NULL input returns zero"
    else:
        empty = "empty input returns NULL"
        if node.nulls == "ignore":
            empty += "; all-NULL input returns NULL"
    return f"nulls={node.nulls}: {policy}; {empty}"


def _metric_definition(
    catalog: DefinitionLookup,
    ref: Ref[SemanticKindTag],
    context: AiContextIR,
    dependencies: tuple[str, ...],
    contract: ExpressionContract,
) -> MetricDefinition:
    metric = consume_metric(lookup_registry(catalog).declared_metrics[ref.path])
    reductions: list[str] = []
    conditions: list[str] = []
    nulls: list[str] = []
    seen: set[int] = set()
    declared = dict(metric.declared)
    display = FormulaDisplay(metric.node, *declared.values())
    bindings = metric_time_bindings(metric, display)

    def visit(node: MetricNode | RowNode | PredicateExpression) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref])
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            reductions.append(display.formula(node))
        if isinstance(
            node, (SourceAggregate, SourceWeightedMean, EntityCount, PerReduce, PerWeightedMean)
        ):
            nulls.append(f"{display.formula(node)}: {_null_rule(node)}")
        if isinstance(node, ContributionFilter):
            conditions.append(display.formula(node.predicate))
        for edge in children(node):
            child = edge.child
            if isinstance(
                child,
                (
                    RowField,
                    MeasureAssertion,
                    RowExpression,
                    SourceAggregate,
                    SourceWeightedMean,
                    EntityCount,
                    ContributionFilter,
                    PerReduce,
                    PerWeightedMean,
                    MemberCount,
                    MetricExpression,
                    MetricWindow,
                    DeclaredMetricRef,
                    PredicateExpression,
                ),
            ):
                visit(child)

    visit(metric.node)
    return MetricDefinition(
        context,
        tuple(item.ref.key for item in metric.roots),
        display.formula(metric.node),
        display.explanation(metric.node),
        tuple(reductions),
        tuple(conditions),
        tuple(bindings),
        metric.unit,
        tuple(nulls),
        dependencies,
        dependency_context(catalog, ref),
        contract.facts,
    )


def definition_for(catalog: DefinitionLookup, ref: Ref[SemanticKindTag]) -> EntryDefinition:
    """Project complete captured facts without execution or authored-function replay."""

    registry = lookup_registry(catalog)
    context = context_for(catalog, ref)
    dependencies = tuple(item.key for item in lookup_state(catalog).dependencies.get(ref, ()))
    if ref.kind is SemanticKind.DOMAIN:
        domain = registry.domains[ref.path]
        return DomainDefinition(
            context,
            domain.owner,
            domain.default,
            tuple(
                item.key
                for item in require_catalog_index(catalog).refs
                if item.kind not in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}
                and item.path.split(".")[0] == ref.path
            ),
        )
    if ref.kind is SemanticKind.DATASOURCE:
        entry = require_catalog_index(catalog).entry(ref)
        assert entry is not None
        datasource = entry_details(entry)
        assert isinstance(datasource, DatasourceDetails)
        return DatasourceDefinition(
            context,
            datasource.backend_type,
            datasource.wall_clock_timezone,
            tuple((key, _freeze_json(value)) for key, value in sorted(datasource.fields.items())),
            tuple(sorted(datasource.env_refs.items())),
            tuple(
                f"entity:{item.semantic_id}"
                for item in registry.entities.values()
                if item.datasource == ref.path
            ),
        )
    captured = lookup_state(catalog).definitions[ref]
    if isinstance(captured, RelationshipIR):
        outside = tuple(
            path
            for path in (captured.from_entity, captured.to_entity)
            if path not in registry.entities
        )
        cardinality = (
            "outside_scope"
            if outside
            else normalize_target_relationship(registry, ref.path).cardinality
        )
        return RelationshipDefinition(
            context,
            f"entity:{captured.from_entity}",
            f"entity:{captured.to_entity}",
            tuple((item.from_key, item.to_key) for item in captured.keys),
            cardinality,
            tuple(item.key for item in catalog_entry_dependencies(catalog, ref)),
        )
    contract = describe_catalog_entry(catalog, ref)
    if isinstance(captured, EntityIR):
        body = lookup_state(catalog).sidecar.bodies.get(ref)
        entity_expression = (
            (
                body.source_syntax
                or (ast.unparse(body.syntax_tree) if body.syntax_tree is not None else "")
            )
            if body is not None
            else ""
        )
        entity_expression = entity_expression or f"source({_inert_text(captured.source)})"
        return EntityDefinition(
            context,
            f"datasource:{captured.datasource}",
            captured.source,
            entity_expression,
            captured.primary_key,
            captured.versioning,
            f"time_dimension:{captured.default_time}"
            if captured.default_time is not None
            else None,
            tuple(
                item.key
                for item in require_catalog_index(catalog).refs
                if item.kind
                in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
                and item.path.rsplit(".", 1)[0] == ref.path
            ),
            dependencies,
        )
    if ref.kind is SemanticKind.METRIC:
        return _metric_definition(catalog, ref, context, dependencies, contract)
    body = lookup_state(catalog).sidecar.bodies.get(ref)
    expression = body.source_syntax if body is not None else None
    if isinstance(captured, PublishedDeclaration):
        expression = FormulaDisplay().formula(
            consume_row(registry.declared_measures[ref.path]).node
        )
    if not expression:
        expression = (
            f"column({body.source_column!r})"
            if body is not None and body.source_column
            else ref.key
        )
    field_entity = ref.path.rsplit(".", 1)[0]
    return FieldDefinition(
        context,
        f"entity:{field_entity}",
        expression,
        contract.unit,
        str(contract.sum_permission) if contract.sum_permission is not None else None,
        captured.parse if isinstance(captured, DimensionIR) else None,
        captured.sample_interval if isinstance(captured, DimensionIR) else None,
        dependencies,
        dependency_context(catalog, ref),
        contract.facts,
    )


class DefinitionLookup(CatalogInputLookup, Protocol):
    def _describe(self, ref: Ref[SemanticKindTag], /) -> ExpressionContract: ...


def describe_catalog_entry(
    catalog: DefinitionLookup, ref: Ref[SemanticKindTag]
) -> ExpressionContract:
    return catalog._describe(ref)  # noqa: SLF001 - The Catalog validates and describes its exact entry.
