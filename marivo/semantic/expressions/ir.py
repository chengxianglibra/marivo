"""Captured expression bodies and binding contexts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import textwrap
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeAlias, cast

import ibis.expr.types as ir

from marivo._kernel.refs import (
    EntityKind,
    FieldKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
    _decode_ref_payload,
)
from marivo.semantic._definition_expression_types import (
    ExpressionDescription,
    _UnsupportedExpression,
)
from marivo.semantic.errors import (
    ErrorKind,
    SemanticRuntimeError,
)
from marivo.semantic.expressions.syntax import _return_expr

_FIELD_KINDS = frozenset(
    {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }
)

ScalarConstant: TypeAlias = str | int | float | bool


@dataclass(frozen=True, slots=True)
class ExpressionBindingV1:
    """One declared field-ref application and its entity argument position."""

    field_ref: RefPayloadV1
    entity_position: int

    def __post_init__(self) -> None:
        if self.field_ref.kind not in _FIELD_KINDS:
            raise ValueError(
                "expression binding field_ref must be a dimension, time_dimension, or measure"
            )
        if type(self.entity_position) is not int or self.entity_position < 0:
            raise ValueError("expression binding entity_position must be a non-negative int")

    def to_ref(self) -> Ref[FieldKind]:
        return cast("Ref[FieldKind]", _decode_ref_payload(self.field_ref))


@dataclass(frozen=True, slots=True)
class ExpressionBody:
    """Process-local callable plus deterministic definition identity."""

    callable: Callable[..., object]
    body_ast_hash: str
    parameter_count: int
    bindings: tuple[ExpressionBindingV1, ...]
    source_column: str | None = None
    source_columns: tuple[str, ...] = ()
    source_syntax: str | None = field(default=None, repr=False)
    _function_ast: ast.FunctionDef | None = field(default=None, repr=False, compare=False)
    _bound_calls: frozenset[int] = field(default_factory=frozenset, repr=False, compare=False)
    description: ExpressionDescription = field(
        default_factory=lambda: _UnsupportedExpression("description_unavailable")
    )

    def __post_init__(self) -> None:
        if not callable(self.callable):
            raise TypeError("expression body callable must be callable")
        if type(self.body_ast_hash) is not str or not self.body_ast_hash:
            raise ValueError("expression body hash must be a non-empty string")
        if type(self.parameter_count) is not int or self.parameter_count < 0:
            raise ValueError("expression body parameter_count must be a non-negative int")
        if self.source_column is not None and (
            type(self.source_column) is not str or not self.source_column
        ):
            raise ValueError("expression body source_column must be non-empty when provided")
        if any(type(column) is not str or not column for column in self.source_columns):
            raise ValueError("expression body source_columns must contain non-empty strings")
        if len(set(self.source_columns)) != len(self.source_columns):
            raise ValueError("expression body source_columns must be unique")
        for binding in self.bindings:
            if binding.entity_position >= self.parameter_count:
                raise ValueError("expression binding entity_position must be below parameter_count")

    @classmethod
    def for_column(cls, column: str) -> ExpressionBody:
        """Create one deterministic source-free direct-column body."""
        if type(column) is not str or not column:
            raise ValueError("direct-column expression requires a non-empty column name")

        def accessor(entity_alias: ir.Table) -> ir.Value:
            return entity_alias[column]

        encoded = json.dumps(
            {
                "schema": "marivo.expression_body.column/v1",
                "column": column,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return cls(
            callable=accessor,
            body_ast_hash=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
            parameter_count=1,
            bindings=(),
            source_column=column,
            source_columns=(column,),
        )


def expression_column_accesses(body: ExpressionBody) -> tuple[tuple[int, str], ...]:
    """Resolve parameter-owned column syntax without invoking the captured function."""
    if body.source_column is not None:
        return ((0, body.source_column),)
    try:
        function = body._function_ast
        if function is None:
            tree = ast.parse(
                body.source_syntax
                if body.source_syntax is not None
                else textwrap.dedent(inspect.getsource(body.callable))
            )
            function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
        parameters = (*function.args.posonlyargs, *function.args.args)
        if len(parameters) != body.parameter_count:
            raise ValueError("expression arity mismatch")
        positions = {parameter.arg: index for index, parameter in enumerate(parameters)}
        parents = {
            id(child): parent
            for parent in ast.walk(function)
            for child in ast.iter_child_nodes(parent)
        }
        accesses: list[tuple[int, str]] = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Name) or node.id not in positions:
                continue
            parent = parents.get(id(node))
            column: str | None = None
            if isinstance(parent, ast.Attribute) and parent.value is node:
                column = parent.attr
            elif isinstance(parent, ast.Subscript) and parent.value is node:
                if isinstance(parent.slice, ast.Constant) and isinstance(parent.slice.value, str):
                    column = parent.slice.value
                else:
                    raise ValueError("dynamic column access")
            elif (
                isinstance(parent, ast.Call)
                and (
                    id(parent) in body._bound_calls
                    or (isinstance(parent.func, ast.Attribute) and parent.func.attr == "bind")
                )
                and body.bindings
            ):
                continue
            else:
                raise ValueError("unknown Entity expression")
            access = (positions[node.id], column)
            if access not in accesses:
                accesses.append(access)
        if not accesses and not body.bindings:
            returns = [node.value for node in function.body if isinstance(node, ast.Return)]
            if len(returns) != 1:
                raise ValueError("unknown constant expression")
            value = _return_expr(body.callable, function)
            if not (
                isinstance(value, ast.Constant)
                or (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and isinstance(value.func.value, ast.Name)
                    and (
                        (
                            value.func.value.id == "ibis"
                            and value.func.attr == "literal"
                            and all(isinstance(arg, ast.Constant) for arg in value.args)
                        )
                        or (
                            value.func.value.id == "ms"
                            and value.func.attr == "all_rows"
                            and not value.args
                        )
                    )
                )
            ):
                raise ValueError("unproven column-free expression")
        return tuple(accesses)
    except (OSError, TypeError, SyntaxError, StopIteration, ValueError) as exc:
        raise SemanticRuntimeError(
            kind=ErrorKind.COMPILE_ERROR,
            message="Expression column dependencies cannot be resolved.",
            expected="static column accesses on exact Entity parameters",
            received="an unknown or unavailable expression dependency",
            hint="Use declared column fields and statically bound Entity expressions.",
        ) from exc


@dataclass(frozen=True, slots=True)
class CompiledExpressionSidecar:
    """Immutable expression bodies and ownership facts for one catalog."""

    bodies: Mapping[Ref[SemanticKindTag], ExpressionBody]
    field_owners: Mapping[Ref[FieldKind], Ref[EntityKind]]
    catalog_refs: frozenset[Ref[SemanticKindTag]]

    def __post_init__(self) -> None:
        bodies = dict(self.bodies)
        owners = dict(self.field_owners)
        refs = frozenset(self.catalog_refs)
        for ref in bodies:
            _require_exact_ref(ref, parameter="bodies key")
        for field_ref, entity_ref in owners.items():
            _require_exact_ref(field_ref, allowed=_FIELD_KINDS, parameter="field owner key")
            _require_exact_ref(
                entity_ref,
                allowed=frozenset({SemanticKind.ENTITY}),
                parameter="field owner value",
            )
        for ref in refs:
            _require_exact_ref(ref, parameter="catalog ref")
        object.__setattr__(self, "bodies", MappingProxyType(bodies))
        object.__setattr__(self, "field_owners", MappingProxyType(owners))
        object.__setattr__(self, "catalog_refs", refs)


@dataclass(frozen=True, slots=True)
class ExpressionBodyFrame:
    """One active expression body and its exact entity aliases."""

    owning_ref: Ref[SemanticKindTag]
    ordered_entity_refs: tuple[Ref[EntityKind], ...]
    ordered_entity_aliases: tuple[ir.Table, ...]
    declared_bindings: tuple[ExpressionBindingV1, ...]

    def __post_init__(self) -> None:
        _require_exact_ref(self.owning_ref, parameter="owning_ref")
        if len(self.ordered_entity_refs) != len(self.ordered_entity_aliases):
            raise ValueError("expression frame entity refs and aliases must have equal arity")


@dataclass(frozen=True, slots=True)
class ExpressionBindingContext:
    """Task-local catalog and nested expression-body stack."""

    catalog_definition_fingerprint: str
    expression_sidecar: CompiledExpressionSidecar
    body_frames: tuple[ExpressionBodyFrame, ...]

    def __post_init__(self) -> None:
        if not self.catalog_definition_fingerprint:
            raise ValueError("catalog definition fingerprint must be non-empty")
        if not self.body_frames:
            raise ValueError("expression binding context requires one body frame")


_EXPRESSION_BINDING_CONTEXT: ContextVar[ExpressionBindingContext | None] = ContextVar(
    "_EXPRESSION_BINDING_CONTEXT",
    default=None,
)


def _require_exact_ref(
    value: object,
    *,
    allowed: frozenset[SemanticKind] | None = None,
    parameter: str,
) -> Ref[SemanticKindTag]:
    if type(value) is not Ref:
        raise TypeError(f"{parameter} must be an exact Ref; received {type(value).__name__}")
    ref = cast("Ref[SemanticKindTag]", value)
    if allowed is not None and ref.kind not in allowed:
        expected = ", ".join(sorted(kind.value for kind in allowed))
        raise TypeError(f"{parameter} must have kind in {{{expected}}}; received {ref.kind.value}")
    return ref


def _binding_runtime_error(
    kind: ErrorKind,
    message: str,
    *,
    refs: Sequence[Ref[SemanticKindTag]] = (),
    expected: str | None = None,
    received: str | None = None,
    details: dict[str, object] | None = None,
) -> SemanticRuntimeError:
    return SemanticRuntimeError(
        kind=kind,
        message=message,
        refs=tuple(ref.key for ref in refs),
        hint=(
            "Use ms.bind(field_ref, entity_alias) with one declared field-kind ref inside "
            "the active loaded semantic expression body."
        ),
        expected=expected,
        received=received,
        details=dict(details or {}),
    )
