"""Compile and evaluate task-local semantic expression bindings."""

from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import math
import textwrap
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from types import CellType, FunctionType, MappingProxyType
from typing import Literal, cast

import ibis.expr.types as ir

from marivo.refs import (
    EntityKind,
    FieldKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
    _decode_ref_payload,
)
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    SemanticRuntimeError,
)
from marivo.semantic.validator import validate_event_body_ast, validate_metric_body_ast

_FIELD_KINDS = frozenset(
    {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }
)
type EventConstant = str | int | float | bool


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

    def __post_init__(self) -> None:
        if not callable(self.callable):
            raise TypeError("expression body callable must be callable")
        if type(self.body_ast_hash) is not str or not self.body_ast_hash:
            raise ValueError("expression body hash must be a non-empty string")
        if type(self.parameter_count) is not int or self.parameter_count < 0:
            raise ValueError("expression body parameter_count must be a non-negative int")
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
        )


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


def _find_function_node(tree: ast.Module, fn_name: str) -> ast.FunctionDef:
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    for node in functions:
        if node.name == fn_name:
            return node
    if functions:
        return functions[0]
    raise SemanticLoadError(
        kind=ErrorKind.COMPILE_ERROR,
        message=f"Expression body {fn_name!r} source does not contain a function definition.",
        expected="one inspectable Python function definition",
        received="no function definition",
    )


def _load_function_ast(fn: Callable[..., object]) -> tuple[ast.Module, ast.FunctionDef]:
    try:
        source = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(source)
    except (OSError, TypeError, IndentationError, SyntaxError) as exc:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Expression body {fn.__name__!r} source is unavailable or invalid.",
            expected="inspectable Python source for the decorated function",
            received=type(exc).__name__,
        ) from exc
    return tree, _find_function_node(tree, fn.__name__)


def _resolved_symbols(fn: Callable[..., object]) -> dict[str, object]:
    closure = inspect.getclosurevars(fn)
    symbols: dict[str, object] = dict(fn.__globals__)
    symbols.update(closure.globals)
    symbols.update(closure.nonlocals)
    return symbols


def _is_bind_target(node: ast.expr, symbols: Mapping[str, object]) -> bool:
    if isinstance(node, ast.Name):
        return symbols.get(node.id) is bind
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        owner = symbols.get(node.value.id)
        return getattr(owner, node.attr, None) is bind
    return False


def _event_constant_value(
    name: str,
    value: object,
    *,
    owning_ref: Ref[SemanticKindTag],
) -> EventConstant:
    if type(value) not in {str, int, float, bool}:
        raise SemanticLoadError(
            kind=ErrorKind.INVALID_EVENT_PREDICATE,
            message=(
                f"Event predicate {owning_ref.key!r} uses unsupported external name {name!r}."
            ),
            refs=(owning_ref.key,),
            expected="a closed immutable str, int, float, or bool constant",
            received=f"{name}={type(value).__name__}",
            hint="Replace the external value with an immutable scalar constant or a literal.",
        )
    if type(value) is float and not math.isfinite(value):
        raise SemanticLoadError(
            kind=ErrorKind.INVALID_EVENT_PREDICATE,
            message=(
                f"Event predicate {owning_ref.key!r} uses non-finite external constant {name!r}."
            ),
            refs=(owning_ref.key,),
            expected="a finite float constant",
            received=repr(value),
            hint="Replace the value with one finite immutable scalar constant.",
        )
    return cast("EventConstant", value)


def _event_closed_constants(
    function: ast.FunctionDef,
    *,
    symbols: Mapping[str, object],
    owning_ref: Ref[SemanticKindTag],
) -> dict[str, EventConstant]:
    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    if len(returns) != 1 or returns[0].value is None:
        return {}
    value = returns[0].value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "ms"
        and value.func.attr == "all_rows"
    ):
        return {}
    allowed_name_nodes: set[int] = set()
    for node in ast.walk(value):
        if not isinstance(node, ast.Call) or not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ms"
            and node.func.attr == "bind"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Name)
        ):
            continue
        allowed_name_nodes.update((id(node.func.value), id(node.args[0]), id(node.args[1])))
    constants: dict[str, EventConstant] = {}
    for node in ast.walk(value):
        if not isinstance(node, ast.Name) or id(node) in allowed_name_nodes:
            continue
        if node.id not in symbols:
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_EVENT_PREDICATE,
                message=(
                    f"Event predicate {owning_ref.key!r} uses unresolved external name {node.id!r}."
                ),
                refs=(owning_ref.key,),
                expected="a closed immutable str, int, float, or bool constant",
                received=node.id,
                hint="Define one immutable scalar constant in the Event module or use a literal.",
            )
        constants[node.id] = _event_constant_value(
            node.id,
            symbols[node.id],
            owning_ref=owning_ref,
        )
    return constants


def _closure_cell(value: object) -> CellType:
    def capture() -> object:
        return value

    closure = capture.__closure__
    if closure is None:
        raise AssertionError("captured value did not create a closure cell")
    return closure[0]


def _freeze_event_callable(
    fn: Callable[..., object],
    constants: Mapping[str, EventConstant],
) -> Callable[..., object]:
    if not constants:
        return fn
    frozen_globals = dict(fn.__globals__)
    frozen_globals.update(
        (name, value) for name, value in constants.items() if name in frozen_globals
    )
    closure = fn.__closure__
    if closure is not None:
        frozen_cells = tuple(
            _closure_cell(constants[name]) if name in constants else cell
            for name, cell in zip(fn.__code__.co_freevars, closure, strict=True)
        )
    else:
        frozen_cells = None
    frozen = FunctionType(
        fn.__code__,
        frozen_globals,
        fn.__name__,
        fn.__defaults__,
        frozen_cells,
    )
    frozen.__kwdefaults__ = fn.__kwdefaults__
    frozen.__annotations__ = dict(fn.__annotations__)
    frozen.__module__ = fn.__module__
    frozen.__qualname__ = fn.__qualname__
    return frozen


class _BindingCollector(ast.NodeVisitor):
    def __init__(
        self,
        *,
        symbols: Mapping[str, object],
        parameter_positions: Mapping[str, int],
        owning_ref: Ref[SemanticKindTag],
    ) -> None:
        self._symbols = symbols
        self._parameter_positions = parameter_positions
        self._owning_ref = owning_ref
        self.bindings: list[ExpressionBindingV1] = []
        self.index_by_key: dict[tuple[SemanticKind, str, int], int] = {}

    def visit_Call(self, node: ast.Call) -> None:
        if not _is_bind_target(node.func, self._symbols):
            if isinstance(node.func, ast.Name) and type(self._symbols.get(node.func.id)) is Ref:
                legacy_ref = cast("Ref[SemanticKindTag]", self._symbols[node.func.id])
                raise SemanticLoadError(
                    kind=ErrorKind.INVALID_BINDING_REF,
                    message=(
                        f"Expression body {self._owning_ref.key!r} calls Ref "
                        f"{legacy_ref.key!r} directly. Ref values are immutable identities, "
                        "not expression callables."
                    ),
                    refs=(self._owning_ref.key, legacy_ref.key),
                    expected="ms.bind(field_ref, entity_parameter)",
                    received="field_ref(entity_parameter)",
                )
            self.generic_visit(node)
            return
        if len(node.args) != 2 or node.keywords or not isinstance(node.args[0], ast.Name):
            raise SemanticLoadError(
                kind=ErrorKind.BINDING_ALIAS_NOT_DIRECT,
                message=(
                    f"Expression body {self._owning_ref.key!r} must call "
                    "ms.bind(field_ref, entity_parameter) with two direct arguments."
                ),
                refs=(self._owning_ref.key,),
                expected="ms.bind(field_ref, entity_parameter)",
                received=ast.dump(node, include_attributes=False),
            )
        value = self._symbols.get(node.args[0].id)
        if type(value) is not Ref:
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_BINDING_REF,
                message=(
                    f"Expression body {self._owning_ref.key!r} binds a value that is not "
                    "an exact field ref."
                ),
                refs=(self._owning_ref.key,),
                expected="a dimension, time_dimension, or measure Ref",
                received=type(value).__name__,
            )
        field_ref = cast("Ref[SemanticKindTag]", value)
        if field_ref.kind not in _FIELD_KINDS:
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_BINDING_REF,
                message=(
                    f"Expression body {self._owning_ref.key!r} calls non-field ref "
                    f"{field_ref.key!r}."
                ),
                refs=(self._owning_ref.key, field_ref.key),
                expected="a dimension, time_dimension, or measure Ref",
                received=field_ref.kind.value,
            )
        if (
            not isinstance(node.args[1], ast.Name)
            or node.args[1].id not in self._parameter_positions
        ):
            raise SemanticLoadError(
                kind=ErrorKind.BINDING_ALIAS_NOT_DIRECT,
                message=(
                    f"Field ref {field_ref.key!r} must bind to one direct "
                    "expression-body entity parameter."
                ),
                refs=(self._owning_ref.key, field_ref.key),
                expected="ms.bind(field_ref, entity_parameter)",
                received=ast.dump(node, include_attributes=False),
            )
        entity_position = self._parameter_positions[node.args[1].id]
        key = (field_ref.kind, field_ref.path, entity_position)
        if key not in self.index_by_key:
            self.index_by_key[key] = len(self.bindings)
            self.bindings.append(
                ExpressionBindingV1(
                    field_ref=RefPayloadV1.from_ref(field_ref),
                    entity_position=entity_position,
                )
            )
        self.generic_visit(node)


class _NormalizedBody(ast.NodeTransformer):
    def __init__(
        self,
        *,
        symbols: Mapping[str, object],
        parameter_positions: Mapping[str, int],
        binding_indexes: Mapping[tuple[SemanticKind, str, int], int],
        constant_bindings: Mapping[str, EventConstant],
    ) -> None:
        self._symbols = symbols
        self._parameter_positions = parameter_positions
        self._binding_indexes = binding_indexes
        self._constant_bindings = constant_bindings

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = "expression_body"
        node.decorator_list = []
        node.returns = None
        node.type_comment = None
        if node.body and isinstance(node.body[0], ast.Expr):
            value = node.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                node.body = node.body[1:]
        node.args.defaults = []
        node.args.kw_defaults = []
        self.generic_visit(node)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        position = self._parameter_positions.get(node.arg)
        if position is not None:
            node.arg = f"entity_{position}"
        node.annotation = None
        node.type_comment = None
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        position = self._parameter_positions.get(node.id)
        if position is not None:
            node.id = f"entity_{position}"
        elif node.id in self._constant_bindings:
            return ast.copy_location(
                ast.Constant(value=self._constant_bindings[node.id]),
                node,
            )
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if (
            _is_bind_target(node.func, self._symbols)
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Name)
        ):
            value = self._symbols.get(node.args[0].id)
            argument = node.args[1]
            if type(value) is Ref and isinstance(argument, ast.Name):
                ref = cast("Ref[SemanticKindTag]", value)
                position = self._parameter_positions.get(argument.id)
                if position is not None:
                    index = self._binding_indexes.get((ref.kind, ref.path, position))
                    if index is not None:
                        return ast.copy_location(
                            ast.Call(
                                func=ast.Name(id=f"field_{index}", ctx=ast.Load()),
                                args=[ast.Name(id=f"entity_{position}", ctx=ast.Load())],
                                keywords=[],
                            ),
                            node,
                        )
        self.generic_visit(node)
        return node


def _normalized_body_hash(
    function: ast.FunctionDef,
    *,
    symbols: Mapping[str, object],
    parameter_positions: Mapping[str, int],
    binding_indexes: Mapping[tuple[SemanticKind, str, int], int],
    constant_bindings: Mapping[str, EventConstant],
) -> str:
    normalized = copy.deepcopy(function)
    transformed = _NormalizedBody(
        symbols=symbols,
        parameter_positions=parameter_positions,
        binding_indexes=binding_indexes,
        constant_bindings=constant_bindings,
    ).visit(normalized)
    ast.fix_missing_locations(transformed)
    encoded = ast.dump(transformed, include_attributes=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def compile_expression_body(
    fn: Callable[..., object],
    *,
    owning_ref: Ref[SemanticKindTag],
    ordered_entity_refs: tuple[Ref[EntityKind], ...],
) -> ExpressionBody:
    """Validate one decorator body and capture exact semantic field bindings."""
    owning = _require_exact_ref(owning_ref, parameter="owning_ref")
    for entity_ref in ordered_entity_refs:
        _require_exact_ref(
            entity_ref,
            allowed=frozenset({SemanticKind.ENTITY}),
            parameter="ordered_entity_refs item",
        )
    body_kind_by_ref: dict[
        SemanticKind,
        Literal["dimension", "time_dimension", "measure", "metric", "event"],
    ] = {
        SemanticKind.DIMENSION: "dimension",
        SemanticKind.TIME_DIMENSION: "time_dimension",
        SemanticKind.MEASURE: "measure",
        SemanticKind.METRIC: "metric",
        SemanticKind.EVENT: "event",
    }
    body_kind = body_kind_by_ref.get(owning.kind)
    if body_kind is None:
        raise SemanticLoadError(
            kind=ErrorKind.INVALID_BINDING_REF,
            message=f"Ref {owning.key!r} cannot own an expression body.",
            refs=(owning.key,),
            expected="dimension, time_dimension, measure, metric, or event",
            received=owning.kind.value,
        )
    if body_kind == "event":
        validate_event_body_ast(fn)
    else:
        validate_metric_body_ast(fn, "base", body_kind=body_kind)
    _, function = _load_function_ast(fn)
    if function.args.vararg is not None or function.args.kwarg is not None:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Expression body {owning.key!r} cannot use variadic parameters.",
            refs=(owning.key,),
            expected="only direct entity parameters",
            received="variadic parameter",
        )
    if function.args.kwonlyargs:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Expression body {owning.key!r} cannot use keyword-only parameters.",
            refs=(owning.key,),
            expected="only direct positional entity parameters",
            received="keyword-only parameter",
        )
    parameters = (*function.args.posonlyargs, *function.args.args)
    if len(parameters) != len(ordered_entity_refs):
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Expression body {owning.key!r} has the wrong entity arity.",
            refs=(owning.key,),
            expected=str(len(ordered_entity_refs)),
            received=str(len(parameters)),
        )
    parameter_positions = {parameter.arg: index for index, parameter in enumerate(parameters)}
    symbols = _resolved_symbols(fn)
    event_constants = (
        _event_closed_constants(
            function,
            symbols=symbols,
            owning_ref=owning,
        )
        if body_kind == "event"
        else {}
    )
    collector = _BindingCollector(
        symbols=symbols,
        parameter_positions=parameter_positions,
        owning_ref=owning,
    )
    collector.visit(function)
    if body_kind == "event":
        invalid = tuple(
            binding.to_ref()
            for binding in collector.bindings
            if binding.field_ref.kind is not SemanticKind.DIMENSION
        )
        if invalid:
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_EVENT_PREDICATE,
                message="Event predicates may bind categorical Dimensions only.",
                refs=(owning.key, *(ref.key for ref in invalid)),
                expected="Ref[dimension] bindings on the Event source Entity",
                received=", ".join(ref.kind.value for ref in invalid),
            )
    return ExpressionBody(
        callable=_freeze_event_callable(fn, event_constants) if body_kind == "event" else fn,
        body_ast_hash=_normalized_body_hash(
            function,
            symbols=symbols,
            parameter_positions=parameter_positions,
            binding_indexes=collector.index_by_key,
            constant_bindings=event_constants,
        ),
        parameter_count=len(parameters),
        bindings=tuple(collector.bindings),
    )


def _validate_body_result(
    result: object,
    *,
    owning_ref: Ref[SemanticKindTag],
) -> ir.Value:
    if not isinstance(result, ir.Value):
        raise _binding_runtime_error(
            ErrorKind.BINDING_RESULT_INVALID,
            f"Expression body {owning_ref.key!r} must return one Ibis value.",
            refs=(owning_ref,),
            expected="ibis.expr.types.Value",
            received=type(result).__name__,
        )
    return result


def _call_body(
    body: ExpressionBody,
    aliases: tuple[ir.Table, ...],
    *,
    owning_ref: Ref[SemanticKindTag],
) -> ir.Value:
    try:
        result = body.callable(*aliases)
    except SemanticError:
        raise
    except Exception as exc:
        hint = (
            " Import every name used by the body in its defining module."
            if isinstance(exc, NameError)
            else ""
        )
        raise _binding_runtime_error(
            ErrorKind.MATERIALIZE_FAILED,
            f"Expression body {owning_ref.key!r} failed: {exc}.{hint}",
            refs=(owning_ref,),
            received=type(exc).__name__,
        ) from exc
    return _validate_body_result(result, owning_ref=owning_ref)


def evaluate_expression_body(
    *,
    catalog_definition_fingerprint: str,
    expression_sidecar: CompiledExpressionSidecar,
    owning_ref: Ref[SemanticKindTag],
    body: ExpressionBody,
    entity_refs: tuple[Ref[EntityKind], ...],
    aliases: tuple[ir.Table, ...],
) -> ir.Value:
    """Evaluate one root body inside a fresh task-local binding context."""
    owning = _require_exact_ref(owning_ref, parameter="owning_ref")
    if body.parameter_count != len(entity_refs) or len(entity_refs) != len(aliases):
        raise _binding_runtime_error(
            ErrorKind.BINDING_ENTITY_MISMATCH,
            f"Expression body {owning.key!r} received the wrong entity arity.",
            refs=(owning,),
            expected=str(body.parameter_count),
            received=str(len(aliases)),
        )
    frame = ExpressionBodyFrame(
        owning_ref=owning,
        ordered_entity_refs=entity_refs,
        ordered_entity_aliases=aliases,
        declared_bindings=body.bindings,
    )
    context = ExpressionBindingContext(
        catalog_definition_fingerprint=catalog_definition_fingerprint,
        expression_sidecar=expression_sidecar,
        body_frames=(frame,),
    )
    token = _EXPRESSION_BINDING_CONTEXT.set(context)
    try:
        return _call_body(body, aliases, owning_ref=owning)
    finally:
        _EXPRESSION_BINDING_CONTEXT.reset(token)


def bind(field: Ref[FieldKind], entity_alias: ir.Table, /) -> ir.Value:
    """Apply a semantic field ref to an entity alias in an expression body.

    Parameters
    ----------
    field:
        Exact dimension, time-dimension, or measure ref declared in the loaded
        semantic project.
    entity_alias:
        Direct entity parameter of the active decorated expression body.

    Returns
    -------
    ibis.expr.types.Value
        The referenced field expression evaluated on ``entity_alias``.

    Example
    -------
    >>> @ms.metric(entities=[orders], additivity="additive")
    ... def revenue(orders):
    ...     return ms.bind(amount, orders).sum()

    Constraints
    -----------
    Only valid inside a loaded semantic expression body. The field must belong
    to the bound entity and must be captured as a direct ``ms.bind`` argument.
    """
    ref = field
    if type(ref) is not Ref:
        raise _binding_runtime_error(
            ErrorKind.INVALID_BINDING_REF,
            "Semantic field application requires an exact Ref value.",
            expected="Ref[dimension | time_dimension | measure]",
            received=type(ref).__name__,
        )
    candidate = cast("Ref[SemanticKindTag]", ref)
    if candidate.kind not in _FIELD_KINDS:
        raise _binding_runtime_error(
            ErrorKind.INVALID_BINDING_REF,
            f"Ref {candidate.key!r} is not a bindable semantic field ref.",
            refs=(candidate,),
            expected="dimension, time_dimension, or measure Ref",
            received=candidate.kind.value,
        )
    field_ref = candidate
    context = _EXPRESSION_BINDING_CONTEXT.get()
    if context is None:
        raise _binding_runtime_error(
            ErrorKind.BINDING_CONTEXT_MISSING,
            f"Field ref {field_ref.key!r} has no active expression binding context.",
            refs=(field_ref,),
            expected="evaluation through the active catalog materializer",
            received="no active context",
        )
    frame = context.body_frames[-1]
    positions = [
        index
        for index, candidate in enumerate(frame.ordered_entity_aliases)
        if candidate is entity_alias
    ]
    if not positions:
        raise _binding_runtime_error(
            ErrorKind.BINDING_ALIAS_NOT_DIRECT,
            f"Field ref {field_ref.key!r} did not receive a direct body entity alias.",
            refs=(frame.owning_ref, field_ref),
            expected="the exact entity parameter object",
            received=type(entity_alias).__name__,
        )
    if len(positions) != 1:
        raise _binding_runtime_error(
            ErrorKind.BINDING_ALIAS_AMBIGUOUS,
            f"Field ref {field_ref.key!r} received an alias used at multiple positions.",
            refs=(frame.owning_ref, field_ref),
            expected="one unique entity parameter position",
            received=str(positions),
        )
    position = positions[0]
    declared = any(
        binding.entity_position == position
        and binding.field_ref.kind == field_ref.kind
        and binding.field_ref.path == field_ref.path
        for binding in frame.declared_bindings
    )
    if not declared:
        raise _binding_runtime_error(
            ErrorKind.BINDING_NOT_DECLARED,
            f"Field ref {field_ref.key!r} was not declared for entity position {position}.",
            refs=(frame.owning_ref, field_ref),
            expected="a binding captured from the expression body AST",
            received=f"position {position}",
        )
    sidecar = context.expression_sidecar
    if field_ref not in sidecar.catalog_refs:
        raise _binding_runtime_error(
            ErrorKind.BINDING_TARGET_MISSING,
            f"Field ref {field_ref.key!r} is absent from the active catalog.",
            refs=(frame.owning_ref, field_ref),
            received=context.catalog_definition_fingerprint,
        )
    owner = sidecar.field_owners.get(cast("Ref[FieldKind]", field_ref))
    expected_owner = frame.ordered_entity_refs[position]
    if owner != expected_owner:
        raise _binding_runtime_error(
            ErrorKind.BINDING_ENTITY_MISMATCH,
            f"Field ref {field_ref.key!r} does not belong to entity position {position}.",
            refs=(frame.owning_ref, field_ref, expected_owner),
            expected=expected_owner.key,
            received=owner.key if owner is not None else "missing owner",
        )
    body = sidecar.bodies.get(field_ref)
    if body is None:
        raise _binding_runtime_error(
            ErrorKind.BINDING_TARGET_MISSING,
            f"Field ref {field_ref.key!r} has no compiled expression body.",
            refs=(frame.owning_ref, field_ref),
        )
    if any(active.owning_ref == field_ref for active in context.body_frames):
        raise _binding_runtime_error(
            ErrorKind.BINDING_CYCLE,
            f"Expression binding cycle reached {field_ref.key!r}.",
            refs=(*(active.owning_ref for active in context.body_frames), field_ref),
        )
    if body.parameter_count != 1:
        raise _binding_runtime_error(
            ErrorKind.BINDING_ENTITY_MISMATCH,
            f"Field ref {field_ref.key!r} must have a one-entity expression body.",
            refs=(field_ref,),
            expected="1",
            received=str(body.parameter_count),
        )
    nested_frame = ExpressionBodyFrame(
        owning_ref=field_ref,
        ordered_entity_refs=(expected_owner,),
        ordered_entity_aliases=(entity_alias,),
        declared_bindings=body.bindings,
    )
    nested_context = ExpressionBindingContext(
        catalog_definition_fingerprint=context.catalog_definition_fingerprint,
        expression_sidecar=sidecar,
        body_frames=(*context.body_frames, nested_frame),
    )
    token = _EXPRESSION_BINDING_CONTEXT.set(nested_context)
    try:
        return _call_body(body, (entity_alias,), owning_ref=field_ref)
    finally:
        _EXPRESSION_BINDING_CONTEXT.reset(token)
