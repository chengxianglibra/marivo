"""Resolve dependencies against a selected semantic load scope."""

from __future__ import annotations

from collections.abc import Mapping

from ibis.common.deferred import Deferred

from marivo._definition.deferred import root_declaration
from marivo._kernel.refs import Ref, SemanticKind, SemanticKindTag
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticExpressionError,
)
from marivo.semantic.static_check.registry import Registry


def _declaration_site(value: Deferred) -> str:
    declaration = root_declaration(value)
    assert declaration is not None
    location = declaration.location
    return f"{location.path}:{location.line}" if location is not None else "<unknown>"


def _defined_kinds(registry: Registry) -> dict[str, SemanticKind]:
    """The kind of every definition this model scope declares, keyed by full path."""
    kinds: dict[str, SemanticKind] = {}
    for kind, paths in (
        (SemanticKind.DOMAIN, registry.domains),
        (SemanticKind.DATASOURCE, registry.datasources),
        (SemanticKind.ENTITY, registry.entities),
        (SemanticKind.MEASURE, registry.measures),
        (SemanticKind.RELATIONSHIP, registry.relationships),
    ):
        kinds.update(dict.fromkeys(paths, kind))

    for path, dimension in registry.dimensions.items():
        kinds[path] = (
            SemanticKind.TIME_DIMENSION if dimension.is_time_dimension else SemanticKind.DIMENSION
        )
    return kinds


_MISSING_REF_KINDS: tuple[tuple[SemanticKind, ErrorKind], ...] = (
    (SemanticKind.ENTITY, ErrorKind.MISSING_ENTITY_REF),
    (SemanticKind.METRIC, ErrorKind.MISSING_METRIC_REF),
    (SemanticKind.MEASURE, ErrorKind.UNKNOWN_MEASURE),
    (SemanticKind.DIMENSION, ErrorKind.MISSING_DIMENSION_REF),
    (SemanticKind.TIME_DIMENSION, ErrorKind.MISSING_DIMENSION_REF),
)


def _closure_scope_error(
    full: str,
    value: Deferred,
    consumed: frozenset[Ref[SemanticKindTag]],
    defined: Mapping[str, SemanticKind],
) -> SemanticError | None:
    """Reject a closure that consumes a definition this model scope does not declare.

    Every consumed ref must name a definition of the same kind and path here;
    a value built from another scope cannot be published by reference alone.
    """

    missing = sorted(ref.key for ref in consumed if ref.path not in defined)
    miskinded = sorted(
        f"{ref.key} (declared here as {defined[ref.path].value})"
        for ref in consumed
        if ref.path in defined and defined[ref.path] is not ref.kind
    )
    if missing:
        missing_kinds = {key.split(":", 1)[0] for key in missing}
        kind = next(
            (
                error_kind
                for semantic_kind, error_kind in _MISSING_REF_KINDS
                if semantic_kind.value in missing_kinds
            ),
            ErrorKind.NOT_FOUND,
        )
        return SemanticExpressionError(
            kind=kind,
            message=f"{full} consumes definitions this model does not declare.",
            refs=(full,),
            expected="every consumed Entity, field, Metric and calendar declared in the loaded models",
            received=", ".join(missing[:8]),
            action=(
                "Declare every missing definition in the loaded model, or capture "
                "closed values whose dependencies belong to this scope."
            ),
            location_label=_declaration_site(value),
        )
    if miskinded:
        return SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            message=f"{full} consumes a definition under a different kind than this model declares.",
            refs=(full,),
            expected="each consumed ref with the kind its definition is declared as",
            received=", ".join(miskinded[:8]),
            action="Rebuild the expression from values resolved in this model.",
            location_label=_declaration_site(value),
        )
    return None
