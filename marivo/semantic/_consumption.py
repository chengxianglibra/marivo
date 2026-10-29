"""Bind typed semantic inputs in the explicit receiving definition scope."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ibis.common.deferred import Deferred

from marivo._definition.deferred import inspect_capture, resolver_of
from marivo._definition.types import DefinitionGraphError, InputContext, Owner, merge_input_contexts
from marivo.refs import Ref, SemanticKindTag
from marivo.semantic._expression_values import (
    FieldExpression,
    MetricPredicate,
    NumericMetric,
    NumericRow,
    RowPredicate,
    _as_deferred,
)
from marivo.semantic.errors import ErrorKind, SemanticExpressionError

if TYPE_CHECKING:
    from marivo.semantic._compiled_state import CompiledSemanticState
    from marivo.semantic.catalog import Entity, Relationship, SemanticCatalog
    from marivo.semantic.expressions import _Entity, _Relationship


def validate_context(
    context: InputContext, *, project_root: Path, owner: Owner | None = None
) -> None:
    """Check captured project and existing owner constraints before normalization."""
    try:
        merge_input_contexts(context, InputContext(project_root.resolve(), owner))
        if context.owner is not None and owner is None:
            raise DefinitionGraphError(
                "A Session-owned input requires its owning Session receiver.",
                kind="ownership_conflict",
            )
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(
            error, operation="semantic input binding"
        ) from error


def _bind_recipe(
    value: FieldExpression | NumericMetric | RowPredicate | MetricPredicate,
    *,
    project_root: Path,
    owner: Owner | None,
    lookup: Callable[[Ref[SemanticKindTag]], object],
) -> Deferred:
    from marivo.semantic._symbolic_binding import bind_recipe

    validate_context(value._context, project_root=project_root, owner=owner)
    recipe = _as_deferred(value)
    inspection = inspect_capture(recipe)
    if inspection.symbols:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            message="Consumption requires closed semantic inputs; the expression contains authoring symbols.",
            refs=tuple(reference.key for reference in inspection.symbols),
            expected="loaded semantic objects or a closed typed expression",
            received="unbound authoring symbols",
            action="Save the declaration in the model and load its complete scope, then obtain the object from the receiving Catalog.",
        )
    from marivo.refs import ref as ref_factory
    from marivo.semantic.expressions import _Entity

    for identity, captured_keys in inspection.identity_keys:
        entity = lookup(ref_factory.entity(identity.removeprefix("entity:")))
        if not isinstance(entity, _Entity) or entity.primary_key != captured_keys:
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                message="Literal Population primary-key interpretation changed in the receiving scope.",
                refs=(identity,),
                expected=f"the captured ordered complete primary key {captured_keys!r}",
                received=repr(entity.primary_key)
                if isinstance(entity, _Entity)
                else type(entity).__name__,
                action="Recreate the literal Population with inputs and complete keys from the receiving Catalog.",
            )
    return Deferred(bind_recipe(resolver_of(recipe), lookup=lookup, declarations={}))


def _bind(
    value: FieldExpression | NumericMetric | RowPredicate | MetricPredicate,
    catalog: SemanticCatalog,
    *,
    owner: Owner | None,
) -> Deferred:
    from marivo.semantic.expressions import author_value

    def lookup(reference: Ref[SemanticKindTag]) -> object:
        entry = catalog.get(reference.key)
        return author_value(catalog._index.values.get(entry._ref))

    return _bind_recipe(value, project_root=catalog.workspace_dir, owner=owner, lookup=lookup)


def _bind_in_state(
    value: FieldExpression | NumericMetric | RowPredicate,
    state: CompiledSemanticState,
    *,
    project_root: Path,
    owner: Owner | None = None,
) -> Deferred:
    """Bind a new classification in one Frame target's captured source scope."""
    from marivo.semantic.expressions import _Resolver, author_value

    resolver = _Resolver(state)

    def lookup(reference: Ref[SemanticKindTag]) -> object:
        if reference not in state.definitions:
            candidates = tuple(
                item.key for item in state.definitions if item.kind is reference.kind
            )
            raise SemanticExpressionError(
                kind=ErrorKind.NOT_FOUND,
                message="The semantic input is outside this target's captured source scope.",
                refs=(reference.key,),
                expected="the exact kind:path in the target's captured definitions",
                received=reference.key,
                action="Choose an available captured input: "
                + (", ".join(sorted(candidates)[:8]) or "(none)"),
            )
        return author_value(resolver.value(reference))

    result = _bind_recipe(value, project_root=project_root, owner=owner, lookup=lookup)
    return result


def bind_field_in_state(
    value: FieldExpression,
    state: CompiledSemanticState,
    *,
    project_root: Path,
    owner: Owner | None = None,
) -> Deferred:
    """Bind one typed field in an explicitly captured source scope."""
    from marivo.semantic._deferred_semantics import consume_field

    result = _bind_in_state(value, state, project_root=project_root, owner=owner)
    consume_field(result)
    return result


def bind_metric_in_state(
    value: NumericMetric,
    state: CompiledSemanticState,
    *,
    project_root: Path,
    owner: Owner | None = None,
) -> Deferred:
    """Bind one typed metric in an explicitly captured source scope."""
    from marivo.semantic._deferred_semantics import consume_metric

    result = _bind_in_state(value, state, project_root=project_root, owner=owner)
    consume_metric(result)
    return result


def bind_predicate_in_state(
    value: RowPredicate,
    state: CompiledSemanticState,
    *,
    project_root: Path,
    owner: Owner | None = None,
) -> Deferred:
    """Bind one typed predicate in an explicitly captured source scope."""
    from marivo.semantic._deferred_semantics import consume_predicate

    result = _bind_in_state(value, state, project_root=project_root, owner=owner)
    consume_predicate(result)
    return result


def bind_entity(value: Entity, catalog: SemanticCatalog, *, owner: Owner | None = None) -> _Entity:
    from marivo.semantic.catalog import Entity
    from marivo.semantic.expressions import _Entity

    if not isinstance(value, Entity):
        raise TypeError("Expected a loaded Entity object.")
    validate_context(value._context, project_root=catalog.workspace_dir, owner=owner)
    target = catalog.get(value.id)
    assert isinstance(target, Entity)
    result = catalog._index.values.get(target._ref)
    assert isinstance(result, _Entity)
    return result


def bind_relationship(
    value: Relationship, catalog: SemanticCatalog, *, owner: Owner | None = None
) -> _Relationship:
    from marivo.semantic.catalog import Relationship
    from marivo.semantic.expressions import _Relationship

    if not isinstance(value, Relationship):
        raise TypeError("Expected a loaded Relationship object.")
    validate_context(value._context, project_root=catalog.workspace_dir, owner=owner)
    target = catalog.get(value.id)
    assert isinstance(target, Relationship)
    result = catalog._index.values.get(target._ref)
    assert isinstance(result, _Relationship)
    return result


def bind_field(
    value: FieldExpression, catalog: SemanticCatalog, *, owner: Owner | None = None
) -> Deferred:
    from marivo.semantic._deferred_semantics import consume_field

    result = _bind(value, catalog, owner=owner)
    consume_field(result)
    return result


def bind_row(
    value: NumericRow, catalog: SemanticCatalog, *, owner: Owner | None = None
) -> Deferred:
    from marivo.semantic._deferred_semantics import consume_row

    result = _bind(value, catalog, owner=owner)
    consume_row(result)
    return result


def bind_metric(
    value: NumericMetric, catalog: SemanticCatalog, *, owner: Owner | None = None
) -> Deferred:
    from marivo.semantic._deferred_semantics import consume_metric

    result = _bind(value, catalog, owner=owner)
    consume_metric(result)
    return result


def bind_predicate(
    value: RowPredicate, catalog: SemanticCatalog, *, owner: Owner | None = None
) -> Deferred:
    from marivo.semantic._deferred_semantics import consume_predicate

    result = _bind(value, catalog, owner=owner)
    consume_predicate(result)
    return result
