"""Source-free projections of one immutable compiled catalog graph."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

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
from marivo._temporal import Grain
from marivo.datasource.ir import AiContextIR
from marivo.refs import Ref, SemanticKind, SemanticKindTag
from marivo.semantic._compiled_state import PublishedDeclaration
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic.catalog_entries import (
    DatasourceDefinition,
    DependencyContext,
    DomainDefinition,
    EntityDefinition,
    EntryDefinition,
    FieldDefinition,
    MetricDefinition,
    MetricTimeBinding,
    PeriodCalendarDefinition,
    RelationshipDefinition,
    TemporalSetDefinition,
    _freeze_json,
)
from marivo.semantic.ir import DimensionIR, EntityIR, RelationshipIR

if TYPE_CHECKING:
    from marivo.semantic.catalog import SemanticCatalog


def context_for(catalog: SemanticCatalog, ref: Ref[SemanticKindTag]) -> AiContextIR:
    definition = catalog._state.definitions.get(ref)
    context = getattr(definition, "ai_context", None)
    if isinstance(context, AiContextIR):
        return context
    entry = catalog._index.entry(ref)
    if entry is not None:
        details = entry._details
        context = getattr(details, "context", None)
        if isinstance(context, AiContextIR):
            return context
    return AiContextIR()


def dependency_context(
    catalog: SemanticCatalog, ref: Ref[SemanticKindTag]
) -> tuple[DependencyContext, ...]:
    return tuple(
        DependencyContext(
            item.key,
            context_for(catalog, item).business_definition,
            context_for(catalog, item).guardrails,
        )
        for item in catalog._state.dependencies.get(ref, ())
        if item in catalog._index._refs
    )


def _recipe_text(recipe: object, operands: tuple[str, ...]) -> str:
    """Render admitted native operations without exposing Marivo resolver records."""
    from ibis.common.deferred import (
        Attr,
        BinaryOperator,
        Call,
        Item,
        Just,
        Sequence,
        UnaryOperator,
        Variable,
    )

    from marivo._definition.deferred import RescaleResolver

    def render(value: object) -> str:
        if isinstance(value, RescaleResolver):
            return (
                f"rescale({render(value.recipe)}, divisor={value.divisor!r}, unit={value.unit!r})"
            )
        if isinstance(value, Variable):
            name = str(value.name)
            if name.startswith("slot_"):
                return operands[int(name.removeprefix("slot_"))]
            return name
        if isinstance(value, Just):
            literal = value.value
            return (
                getattr(literal, "__name__", type(literal).__name__)
                if callable(literal)
                else repr(literal)
            )
        if isinstance(value, BinaryOperator):
            operators = {
                "add": "+",
                "sub": "-",
                "mul": "*",
                "truediv": "/",
                "floordiv": "//",
                "pow": "**",
                "mod": "%",
                "eq": "==",
                "ne": "!=",
                "lt": "<",
                "le": "<=",
                "gt": ">",
                "ge": ">=",
                "and_": "&",
                "or_": "|",
                "xor": "^",
            }
            return f"({render(value.left)} {operators[value.func.__name__]} {render(value.right)})"
        if isinstance(value, UnaryOperator):
            operators = {"neg": "-", "pos": "+", "invert": "~"}
            return f"{operators[value.func.__name__]}{render(value.arg)}"
        if isinstance(value, Attr):
            attribute = value.name.value if isinstance(value.name, Just) else render(value.name)
            return f"{render(value.obj)}.{attribute}"
        if isinstance(value, Item):
            return f"{render(value.obj)}[{render(value.indexer)}]"
        if isinstance(value, Call):
            arguments = [
                *(render(item) for item in value.args),
                *(f"{name}={render(item)}" for name, item in value.kwargs.items()),
            ]
            return f"{render(value.func)}({', '.join(arguments)})"
        if isinstance(value, Sequence):
            return "(" + ", ".join(render(item) for item in value.values) + ")"
        raise TypeError(f"Unsupported compiled formula node: {type(value).__name__}")

    return render(recipe)


def formula(node: RowNode | MetricNode | PredicateExpression) -> str:
    """Readable native arithmetic with semantic identities in place of bound slots."""
    if isinstance(node, RowField):
        read = ""
        if node.read is not None and (node.read.path or node.read.at is not None):
            read = f".via({_inert_text(node.read.path)}).at({_inert_text(node.read.at)})"
        return node.ref.ref.key + read
    if isinstance(node, MeasureAssertion):
        return f"assert_measure({formula(node.operand)}, unit={node.unit!r}, permission={_inert_text(node.permission)}, authorization={_inert_text(node.authorization)})"
    if isinstance(node, RowExpression):
        return _recipe_text(node.recipe, tuple(formula(item) for item in node.inputs))
    if isinstance(node, MetricExpression):
        return _recipe_text(node.recipe, tuple(formula(item) for item in node.operands))
    if isinstance(node, PredicateExpression):
        return _recipe_text(
            node.recipe,
            tuple(
                formula(item) if isinstance(item, RowExpression) else _inert_text(item)
                for item in node.inputs
            ),
        )
    if isinstance(node, DeclaredMetricRef):
        return node.ref.ref.key
    if isinstance(node, SourceAggregate):
        time = f", time={_inert_text(node.time)}" if node.time is not None else ""
        q = f", q={node.q}" if node.q is not None else ""
        return f"{node.reducer}({formula(node.operand)}{q}{time}, nulls={node.nulls}, rule_version={node.rule_version})"
    if isinstance(node, EntityCount):
        time = f", time={_inert_text(node.time)}" if node.time is not None else ""
        return f"count({node.entity.ref.key}{time})"
    if isinstance(node, SourceWeightedMean):
        time = f", time={_inert_text(node.time)}" if node.time is not None else ""
        return f"weighted_mean({formula(node.value)}, weight={formula(node.weight)}{time}, nulls={node.nulls}, rule_version={node.rule_version})"
    if isinstance(node, ContributionFilter):
        return f"{formula(node.operand)}.where({formula(node.predicate)})"
    if isinstance(node, PerReduce):
        return (
            f"{formula(node.operand)}.per({_inert_text(node.samples)}).{node.reducer}("
            f"q={node.q!r}, nulls={node.nulls}, order_by={_inert_text(node.order_by)}, "
            f"include_null={node.include_null}, rule_version={node.rule_version})"
        )
    if isinstance(node, PerWeightedMean):
        return f"weighted_mean({formula(node.value)}, weight={formula(node.weight)}, per={_inert_text(node.samples)}, nulls={node.nulls}, rule_version={node.rule_version})"
    if isinstance(node, MetricWindow):
        return f"{formula(node.operand)}.window({_inert_text(node.rule)})"
    return f"member_count({_inert_text(node.population)})"


def _inert_text(value: object) -> str:
    """Only captured dataclass facts; never resolver or executable reprs."""
    from marivo._definition.types import ExactRef

    if isinstance(value, ExactRef):
        return value.ref.key
    if isinstance(value, Grain):
        return repr(value)
    if isinstance(value, Enum):
        return _inert_text(value.value)
    if isinstance(value, (date, datetime, timedelta, Decimal)):
        return repr(value)
    if isinstance(
        value,
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
            DeclaredMetricRef,
            MetricExpression,
            MetricWindow,
            PredicateExpression,
        ),
    ):
        return formula(value)
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
                if item.name not in {"recipe", "owner", "version"}
            )
            + ")"
        )
    return type(value).__name__


def _source_null_rule(node: SourceAggregate | SourceWeightedMean | EntityCount) -> str:
    """Describe the captured reducer's policy without qualifying execution."""
    if isinstance(node, EntityCount):
        return "count counts source rows; empty input returns zero"
    policy = (
        "ignore NULL contributions"
        if node.nulls == "ignore"
        else "assert non-NULL demanded contributions; a demanded NULL raises an error"
    )
    if isinstance(node, SourceWeightedMean):
        empty = "no valid pairs or zero paired weight sum returns NULL"
    elif node.reducer == "sum":
        empty = "empty input returns typed zero"
        if node.nulls == "ignore":
            empty += "; nonempty all-NULL input returns NULL"
    elif node.reducer in {"count", "count_distinct", "nunique"}:
        empty = "empty input returns zero"
        if node.nulls == "ignore":
            empty += "; all-NULL input returns zero"
    else:
        empty = "empty input returns NULL"
        if node.nulls == "ignore":
            empty += "; all-NULL input returns NULL"
    return f"nulls={node.nulls}: {policy}; {empty}"


def definition_for(catalog: SemanticCatalog, ref: Ref[SemanticKindTag]) -> EntryDefinition:
    """Project complete captured facts without execution or authored-function replay."""
    from marivo.semantic.catalog import PeriodCalendar, TemporalSet

    registry = catalog._reg
    context = context_for(catalog, ref)
    dependencies = tuple(item.key for item in catalog._state.dependencies.get(ref, ()))
    if ref.kind is SemanticKind.DOMAIN:
        domain = registry.domains[ref.path]
        return DomainDefinition(
            context,
            domain.owner,
            domain.default,
            tuple(
                item.key
                for item in catalog._index._refs
                if item.kind not in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}
                and item.path.split(".")[0] == ref.path
            ),
        )
    if ref.kind is SemanticKind.DATASOURCE:
        from marivo.semantic.catalog import DatasourceDetails

        entry = catalog._index.entry(ref)
        assert entry is not None and isinstance(entry._details, DatasourceDetails)
        datasource = entry._details
        return DatasourceDefinition(
            context,
            datasource.backend_type,
            tuple((key, _freeze_json(value)) for key, value in sorted(datasource.fields.items())),
            tuple(sorted(datasource.env_refs.items())),
            tuple(
                f"entity:{item.semantic_id}"
                for item in registry.entities.values()
                if item.datasource == ref.path
            ),
        )
    if ref.kind is SemanticKind.PERIOD_CALENDAR:
        calendar = catalog._index.entry(ref)
        assert isinstance(calendar, PeriodCalendar)
        calendar_details = calendar._temporal_details()
        return PeriodCalendarDefinition(
            context,
            calendar_details._date.key,
            calendar_details.boundary_timezone,
            calendar_details.coverage,
            tuple((name, item.key) for name, item in calendar_details._level_bindings),
            calendar_details.snapshot_status,
            calendar_details.snapshot_digest,
            dependencies,
        )
    if ref.kind is SemanticKind.TEMPORAL_SET:
        temporal_set = catalog._index.entry(ref)
        assert isinstance(temporal_set, TemporalSet)
        set_details = temporal_set._temporal_details()
        return TemporalSetDefinition(
            context,
            set_details._occurrence_id.key,
            set_details._start.key,
            set_details._end.key,
            set_details._category.key if set_details._category is not None else None,
            set_details.boundary_timezone,
            set_details.coverage,
            set_details.snapshot_status,
            set_details.occurrence_count,
            dependencies,
        )
    captured = catalog._state.definitions[ref]
    if isinstance(captured, RelationshipIR):
        from marivo.semantic.validator import normalize_target_relationship

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
            tuple(item.key for item in catalog._entry_dependencies(ref)),
        )
    contract = catalog._describe(ref)
    if isinstance(captured, EntityIR):
        body = catalog._state.sidecar.bodies.get(ref)
        entity_expression = (
            (
                body.source_syntax
                or (ast.unparse(body._function_ast) if body._function_ast is not None else "")
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
            contract._default_time.ref.key if contract._default_time is not None else None,
            tuple(
                item.key
                for item in catalog._index._refs
                if item.kind
                in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
                and item.path.rsplit(".", 1)[0] == ref.path
            ),
            dependencies,
        )
    if ref.kind is SemanticKind.METRIC:
        metric = consume_metric(registry.declared_metrics[ref.path])
        reductions: list[str] = []
        conditions: list[str] = []
        bindings: list[MetricTimeBinding] = []
        from ibis.common.deferred import Call, Just

        from marivo.semantic._deferred_semantics import MetricFacts, _consume, _inspect

        for use in _inspect(registry.declared_metrics[ref.path]).semantic_calls:
            if not isinstance(use.recipe, Call):
                continue
            if use.symbol not in {
                "sum",
                "mean",
                "min",
                "max",
                "median",
                "quantile",
                "count",
                "count_distinct",
                "approx_nunique",
                "approx_median",
                "approx_quantile",
                "first",
                "last",
                "weighted_mean",
            }:
                continue
            consumed = _consume(use.recipe)
            if not isinstance(consumed, MetricFacts) or not isinstance(
                consumed.node, (SourceAggregate, SourceWeightedMean, EntityCount)
            ):
                continue
            time = consumed.node.time
            root = consumed.roots[0]
            entity_contract = catalog._describe(root.ref)
            default = (
                entity_contract._default_time.ref.key
                if entity_contract._default_time is not None
                else None
            )
            authored_time = use.recipe.kwargs.get("time")
            explicit = authored_time is not None and not (
                isinstance(authored_time, Just) and authored_time.value is None
            )
            bindings.append(
                MetricTimeBinding(
                    formula(consumed.node),
                    time.field.ref.key if time else None,
                    tuple(item.ref.key for item in time.path) if time else (),
                    "explicit" if explicit else "entity_default" if time is not None else "none",
                    default,
                )
            )
        nulls: list[str] = []
        seen: set[int] = set()

        def visit(node: MetricNode | RowNode | PredicateExpression) -> None:
            if id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, DeclaredMetricRef):
                visit(consume_metric(registry.declared_metrics[node.ref.ref.path]).node)
            if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
                branch = formula(node)
                reductions.append(branch)
                nulls.append(f"{branch}: {_source_null_rule(node)}")
            if isinstance(node, ContributionFilter):
                conditions.append(formula(node.predicate))
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
            formula(metric.node),
            tuple(reductions),
            tuple(conditions),
            tuple(bindings),
            metric.unit,
            tuple(nulls),
            dependencies,
            dependency_context(catalog, ref),
            contract.facts,
        )
    body = catalog._state.sidecar.bodies.get(ref)
    expression = body.source_syntax if body is not None else None
    if isinstance(captured, PublishedDeclaration):
        expression = formula(consume_row(registry.declared_measures[ref.path]).node)
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
        contract._default_time.ref.key if contract._default_time is not None else None,
        captured.parse if isinstance(captured, DimensionIR) else None,
        captured.sample_interval if isinstance(captured, DimensionIR) else None,
        captured.is_default if isinstance(captured, DimensionIR) else False,
        dependencies,
        dependency_context(catalog, ref),
        contract.facts,
    )
