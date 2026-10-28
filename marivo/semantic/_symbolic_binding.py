"""Bind immutable authoring recipes against a complete private definition scope."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Deferred,
    Item,
    Just,
    Resolver,
    UnaryOperator,
)
from ibis.common.deferred import (
    Sequence as RecipeSequence,
)

from marivo._definition.deferred import (
    DeclarationResolver,
    RescaleResolver,
    SemanticInput,
    SemanticResolver,
    inspect_capture,
    resolver_of,
    root_declaration,
)
from marivo._definition.expressions import PredicateExpression
from marivo._definition.graph import direct_calendar_refs, walk
from marivo._definition.types import DefinitionGraphError, FieldRead, SumNone
from marivo._symbolic import SymbolResolver
from marivo.refs import Ref, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._compiled_state import CompilationGraph
from marivo.semantic._deferred_semantics import (
    consume_field,
    consume_metric,
    consume_predicate,
    consume_row,
)
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticExpressionError,
    SemanticLoadError,
)
from marivo.semantic.expressions import (
    Entity,
    Relationship,
    ResolvedValue,
    _Resolver,
    author_value,
    declare_measure,
)
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    from marivo.semantic.loader import LoaderContext


class _PublicationResolver(_Resolver):
    """Reuse complete physical facts while derived identities advance with publication."""

    def __init__(self, graph: CompilationGraph) -> None:
        super().__init__(graph)
        self._physical = frozenset(graph.definitions)
        self._physical_values: dict[Ref[SemanticKindTag], ResolvedValue] = {}

    def advance(self) -> None:
        self._versions = {
            ref: value for ref, value in self._versions.items() if ref in self._physical
        }

    def value(self, target: Ref[SemanticKindTag]) -> ResolvedValue:
        if target not in self._physical:
            return super().value(target)
        if target not in self._physical_values:
            self._physical_values[target] = super().value(target)
        return self._physical_values[target]


def _children(recipe: Resolver) -> tuple[Resolver, ...]:
    if isinstance(recipe, (DeclarationResolver, RescaleResolver)):
        return (recipe.recipe,)
    if isinstance(recipe, BinaryOperator):
        return (recipe.left, recipe.right)
    if isinstance(recipe, UnaryOperator):
        return (recipe.arg,)
    if isinstance(recipe, Call):
        return (recipe.func, *recipe.args, *recipe.kwargs.values())
    if isinstance(recipe, Attr):
        return (recipe.obj,)
    if isinstance(recipe, RecipeSequence):
        return tuple(recipe.values)
    from marivo._definition.population_parameters import ParameterResolver, parameter_inputs

    if isinstance(recipe, ParameterResolver):
        return tuple(resolver_of(value) for value in parameter_inputs(recipe))
    if isinstance(recipe, Item):
        indexer: object = recipe.indexer
        if isinstance(indexer, Resolver):
            return (recipe.obj, indexer)
    return ()


def bind_recipe(
    recipe: Resolver,
    *,
    lookup: Callable[[Ref[SemanticKindTag]], object],
    declarations: dict[int, Deferred],
    root: bool = False,
) -> Resolver:
    """Copy an admitted recipe, replacing only authoring leaves and parameters."""

    def bind(child: Resolver) -> Resolver:
        return bind_recipe(child, lookup=lookup, declarations=declarations)

    from marivo._definition.population_parameters import (
        ParameterResolver,
        bind_parameter,
        bind_parameter_call,
    )

    def bind_value(value: Deferred) -> Deferred:
        return Deferred(bind(resolver_of(value)))

    def field(value: Deferred) -> FieldRead:
        facts = consume_field(value)

        return facts.field_read or FieldRead(facts.reference, facts.root)

    def predicate(value: Deferred) -> PredicateExpression:
        return consume_predicate(value).node

    if isinstance(recipe, ParameterResolver):
        parameter_value = bind_parameter(
            recipe, bind_value, lookup, field=field, predicate=predicate
        )
        return parameter_value if isinstance(parameter_value, Resolver) else Just(parameter_value)
    if isinstance(recipe, (Call, Attr, Item)):
        parameter = bind_parameter_call(
            recipe, bind_value, lookup, field=field, predicate=predicate
        )
        if parameter is not None:
            return parameter
    if isinstance(recipe, SymbolResolver):
        value = lookup(recipe.reference)
        if isinstance(value, Deferred):
            return resolver_of(value)
        if isinstance(value, Entity):
            exact = value._exact_ref
            return SemanticResolver(
                SemanticInput(exact, exact, "1", SumNone(), value._default_time)
            )
        if isinstance(value, Relationship):
            return Just(value._parameter)
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_STAGE,
            message="This identity symbol cannot be used as a computed expression.",
            refs=(recipe.reference.key,),
            expected="an Entity, field, Metric, or relationship parameter",
            received=recipe.reference.kind.value,
            action="Use this identity only in its matching declaration parameter.",
        )
    if isinstance(recipe, RescaleResolver):
        return RescaleResolver(bind(recipe.recipe), recipe.divisor, recipe.unit)
    if isinstance(recipe, DeclarationResolver):
        if not root and id(recipe) in declarations:
            return resolver_of(declarations[id(recipe)])
        child = bind(recipe.recipe)
        declaration = recipe.declaration
        if declaration.pending_assertion:
            from marivo._symbolic import symbol_factory
            from marivo.semantic._dsl_authoring import (
                AdditivityPolicy,
                additive_all,
                additive_except,
                additive_over,
                non_additive,
            )
            from marivo.semantic.ir import SourceLocation

            policy = declaration.pending_additivity
            additivity: AdditivityPolicy | None = None
            if policy is not None:
                mode, paths = policy
                # The closed row owns the exact coordinate kinds; policy paths
                # are deliberately not inferred from their spelling.
                axes = {
                    axis.ref.path: axis.ref.kind
                    for axis in consume_row(Deferred(child)).coordinates
                }
                coordinates = tuple(
                    symbol_factory.time_dimension(path)
                    if axes.get(path) is SemanticKind.TIME_DIMENSION
                    else symbol_factory.dimension(path)
                    for path in paths
                )
                if mode == "over":
                    additivity = additive_over(*coordinates)
                elif mode == "all":
                    additivity = additive_except(*coordinates) if coordinates else additive_all()
                else:
                    additivity = non_additive()
            location = declaration.location
            return resolver_of(
                declare_measure(
                    Deferred(child),
                    name=declaration.name,
                    unit=declaration.unit,
                    additivity=additivity,
                    ai_context=declaration.ai_context,
                    location=SourceLocation(location.path, location.line)
                    if location is not None
                    else SourceLocation("<unknown>", 0),
                )
            )
        return DeclarationResolver(
            child, replace(declaration, reference=None) if root else declaration
        )
    if isinstance(recipe, BinaryOperator):
        return BinaryOperator(recipe.func, bind(recipe.left), bind(recipe.right))
    if isinstance(recipe, UnaryOperator):
        return UnaryOperator(recipe.func, bind(recipe.arg))
    if isinstance(recipe, Call):
        return Call(
            bind(recipe.func),
            *(bind(arg) for arg in recipe.args),
            **{key: bind(value) for key, value in recipe.kwargs.items()},
        )
    if isinstance(recipe, Attr):
        from marivo._definition.deferred import attribute_name

        return Attr(bind(recipe.obj), attribute_name(recipe))
    if isinstance(recipe, RecipeSequence):
        values = tuple(bind(value) for value in recipe.values)
        return RecipeSequence(list(values) if recipe.typ is list else values)
    if isinstance(recipe, Just) and isinstance(recipe.value, Relationship):
        return Just(recipe.value._parameter)
    return recipe


def _row_roots(recipe: Resolver, registry: Registry) -> set[str]:
    """Infer only row-root identity; symbols never supply units or permissions."""
    from marivo._definition.deferred import attribute_name

    if isinstance(recipe, SemanticResolver):
        return {recipe.input.root.ref.path}
    if isinstance(recipe, SymbolResolver):
        reference = recipe.reference
        if reference.kind in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
            SemanticKind.MEASURE,
        }:
            physical = registry.dimensions.get(reference.path) or registry.measures.get(
                reference.path
            )
            return {physical.entity if physical is not None else reference.path.rsplit(".", 1)[0]}
        return set()
    if isinstance(recipe, Call) and isinstance(recipe.func, Attr):
        if attribute_name(recipe.func) == "via" and len(recipe.args) == 1:
            path = recipe.args[0]
            if isinstance(path, RecipeSequence) and path.values:
                first = path.values[0]
                if isinstance(first, SymbolResolver):
                    relation = registry.relationships.get(first.reference.path)
                    if relation is not None:
                        return {relation.from_entity}
                elif isinstance(first, Just):
                    from marivo._definition.deferred import RelationshipParameter

                    value = first.value
                    relation_parameter = (
                        value._parameter if isinstance(value, Relationship) else value
                    )
                    if isinstance(relation_parameter, RelationshipParameter):
                        return {relation_parameter.from_entity.ref.path}
        return _row_roots(recipe.func.obj, registry) | set().union(
            *(_row_roots(argument, registry) for argument in recipe.args)
        )
    result: set[str] = set()
    for child in _children(recipe):
        result.update(_row_roots(child, registry))
    return result


def publish_declarations(
    contexts: Sequence[LoaderContext],
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    *,
    graph: CompilationGraph | None = None,
) -> list[SemanticError]:
    """Index, order, and bind all declarations before publishing the closed scope."""
    from marivo.semantic.loader import _closure_scope_error, _declaration_site, _defined_kinds

    values = tuple(value for context in contexts for value in context.declarations)
    context_by_id = {id(value): context for context in contexts for value in context.declarations}
    indexed: dict[Ref[SemanticKindTag], Deferred] = {}
    by_recipe: dict[int, Ref[SemanticKindTag]] = {}
    errors: list[SemanticError] = []
    physical = _defined_kinds(registry)

    target: Ref[SemanticKindTag]
    for value in values:
        declaration = root_declaration(value)
        assert declaration is not None
        inspection = inspect_capture(value)
        context = context_by_id[id(value)]
        if declaration.kind == "measure":
            roots = _row_roots(resolver_of(value), registry)
            if len(roots) != 1:
                errors.append(
                    SemanticExpressionError(
                        kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                        message=f"Measure {declaration.name!r} requires one contribution root.",
                        refs=(declaration.name,),
                        expected="one Entity root captured by row dependencies",
                        received=", ".join(sorted(roots)) or "no row root",
                        action="Use numeric row dependencies belonging to one Entity.",
                        location_label=_declaration_site(value),
                    )
                )
                continue
            target = ref_factory.measure(f"{next(iter(roots))}.{declaration.name}")
        else:
            domain = (
                declaration.domain.path
                if declaration.domain is not None
                else context.default_domain
            )
            if domain is None or domain not in registry.domains:
                errors.append(
                    SemanticExpressionError(
                        kind=ErrorKind.MISSING_DOMAIN,
                        message=f"Metric {declaration.name!r} requires a declared domain.",
                        refs=(declaration.name,),
                        expected="an explicit declared domain or the model default; available domains: "
                        + (", ".join(sorted(registry.domains)) or "(none)"),
                        received=domain or "no domain",
                        action="Declare a domain and use its native lookup symbol.",
                        location_label=_declaration_site(value),
                    )
                )
                continue
            target = ref_factory.metric(f"{domain}.{declaration.name}")
        if target in indexed or target.path in physical:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.DUPLICATE_NAME,
                    message=f"Duplicate semantic_id: {target.path!r}. First declaration: {_declaration_site(indexed[target]) if target in indexed else 'physical definition'}. Conflicting declaration: {_declaration_site(value)}.",
                    refs=(
                        target.path,
                        _declaration_site(indexed[target])
                        if target in indexed
                        else "physical definition",
                        _declaration_site(value),
                    ),
                    hint="Rename one declaration; discovery order never chooses a winner.",
                )
            )
            continue
        indexed[target] = value
        by_recipe[id(resolver_of(value))] = target
    if errors:
        return errors

    dependencies: dict[Ref[SemanticKindTag], set[Ref[SemanticKindTag]]] = {}
    available = {**physical, **{reference.path: reference.kind for reference in indexed}}
    for target, value in indexed.items():
        inspection = inspect_capture(value)
        dependencies[target] = set(inspection.symbols)
        pending = list(_children(resolver_of(value)))
        while pending:
            recipe = pending.pop()
            declared = by_recipe.get(id(recipe))
            if declared is not None:
                dependencies[target].add(declared)
            else:
                pending.extend(_children(recipe))
        for dependency in sorted(dependencies[target], key=lambda item: item.key):
            actual = available.get(dependency.path)
            if actual is not dependency.kind:
                errors.append(
                    SemanticExpressionError(
                        kind=ErrorKind.NOT_FOUND if actual is None else ErrorKind.INVALID_REF,
                        message=f"Declaration {target.key!r} has an unresolved symbolic dependency.",
                        refs=(target.key, dependency.key),
                        expected=f"a declared {dependency.kind.value}",
                        received=dependency.path if actual is None else actual.value,
                        action="Declare the exact dependency with the matching kind in the loaded scope.",
                        location_label=_declaration_site(value),
                    )
                )
    if errors:
        return errors

    order: list[Ref[SemanticKindTag]] = []
    active: list[Ref[SemanticKindTag]] = []
    visited: set[Ref[SemanticKindTag]] = set()

    def visit(target: Ref[SemanticKindTag]) -> None:
        if target in active:
            chain = (*active[active.index(target) :], target)
            raise SemanticExpressionError(
                kind=ErrorKind.DEFINITION_CYCLE,
                message="Symbolic declaration dependency cycle.",
                refs=tuple(reference.key for reference in chain),
                expected="an acyclic declaration graph",
                received=" -> ".join(reference.key for reference in chain),
                action="Remove one dependency in the reported cycle.",
                location_label=_declaration_site(indexed[target]),
            )
        if target in visited:
            return
        active.append(target)
        for dependency in sorted(dependencies[target], key=lambda item: item.key):
            if dependency in indexed:
                visit(dependency)
        active.pop()
        visited.add(target)
        order.append(target)

    try:
        for target in sorted(indexed, key=lambda item: item.key):
            visit(target)
    except SemanticError as error:
        return [error]

    graph = CompilationGraph.create(registry, sidecar) if graph is None else graph
    resolver = _PublicationResolver(graph)
    closed: dict[int, Deferred] = {}
    for target in order:
        original = indexed[target]

        def lookup(reference: Ref[SemanticKindTag]) -> object:
            return author_value(resolver.value(reference))

        try:
            value = Deferred(
                bind_recipe(resolver_of(original), lookup=lookup, declarations=closed, root=True)
            )
            row = consume_row(value) if target.kind is SemanticKind.MEASURE else None
            facts = row if row is not None else consume_metric(value)
            if row is not None and row.root.ref.path != target.path.rsplit(".", 1)[0]:
                raise SemanticExpressionError(
                    kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                    message="Measure root differs from its symbolic root hint.",
                    refs=(target.key,),
                    expected=target.path.rsplit(".", 1)[0],
                    received=row.root.ref.path,
                    action="Use a Measure lookup path matching the actual contribution Entity.",
                )
            from marivo.semantic._deferred_semantics import MetricFacts

            captured = dict(facts._declared) if isinstance(facts, MetricFacts) else {}
            closure = walk((facts.node,), resolve=captured.get)
            if closure.owner is not None:
                raise SemanticExpressionError(
                    kind=ErrorKind.INVALID_EXPRESSION_STAGE,
                    message=f"{target.path} captures an execution Session.",
                    refs=(target.key,),
                    expected="a source-free unowned publication recipe",
                    received=f"Session {closure.owner.session_id}/{closure.owner.owner_id} owns a captured parameter",
                    action="Keep Session-owned values in the analysis script.",
                )
            consumed = frozenset(
                {exact.ref for exact in closure.refs}
                | {ref for node in closure.order for ref in direct_calendar_refs(node)}
            )
            scope_error = _closure_scope_error(target.path, value, consumed, available)
            if scope_error is not None:
                raise scope_error
            declaration = root_declaration(value)
            assert declaration is not None
            graph.append(
                target,
                stage=declaration.kind,
                name=declaration.name,
                node=facts.node,
                unit=facts.unit,
                ai_context=declaration.ai_context,
            )
            resolver.advance()
            if target.kind is SemanticKind.MEASURE:
                captured = resolver_of(value)
                assert isinstance(captured, DeclarationResolver)
                value = Deferred(
                    DeclarationResolver(
                        captured.recipe,
                        replace(captured.declaration, reference=resolver.exact(target)),
                    )
                )
                registry.declared_measures[target.path] = value
                closed[id(resolver_of(original))] = value
            else:
                registry.declared_metrics[target.path] = value
                closed[id(resolver_of(original))] = resolver.metric(target.path)
        except DefinitionGraphError as error:
            errors.append(
                SemanticExpressionError.from_definition(error, operation=f"binding {target.key}")
            )
        except SemanticError as error:
            errors.append(error)
        if errors:
            break
    if errors:
        registry.declared_measures.clear()
        registry.declared_metrics.clear()
    return errors
