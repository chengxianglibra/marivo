"""Compile and evaluate task-local semantic expression bindings."""

from __future__ import annotations

import ast
import copy
import hashlib
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal, cast

import ibis
import ibis.expr.types as ibis_ir
import ibis.expr.types as ir
from ibis.common.deferred import Deferred
from typing_extensions import override

from marivo._kernel.paths import relationship_binding
from marivo._kernel.refs import (
    EntityKind,
    FieldKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
)
from marivo._kernel.symbolic import native_symbol_ref
from marivo.semantic._declarative_expression_compiler import _load_function_ast, _resolved_symbols
from marivo.semantic._deferred_semantics import (
    consume_field,
)
from marivo.semantic._definition_expression import describe_expression
from marivo.semantic._entity_symbols import (
    entity_table_body_nodes,
    validate_entity_table_closed_symbols,
)
from marivo.semantic._ibis_builtin_udf import builtin_udf_token
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
)
from marivo.semantic.expressions.ir import (
    _EXPRESSION_BINDING_CONTEXT,
    _FIELD_KINDS,
    CompiledExpressionSidecar,
    ExpressionBindingContext,
    ExpressionBindingV1,
    ExpressionBody,
    ExpressionBodyFrame,
    ScalarConstant,
    _binding_runtime_error,
    _require_exact_ref,
)
from marivo.semantic.expressions.objects import Dimension, Measure, TimeField, entity_exact
from marivo.semantic.expressions.objects import _Entity as Entity
from marivo.semantic.expressions.objects import _Relationship as Relationship
from marivo.semantic.static_check.ast_rules import (
    _stable_ast_dump,
    validate_entity_table_body_ast,
    validate_metric_body_ast,
)
from marivo.telemetry import traced

if TYPE_CHECKING:
    from marivo.semantic.definition_resolution import Field


def _binding_field_ref(value: object) -> Ref[SemanticKindTag] | None:
    if isinstance(value, Deferred):
        native = native_symbol_ref(value)
        if native is not None:
            return native
        field = consume_field(value)
        if field.field_read is not None and field.field_read.path:
            return None
        return field.reference.ref
    if isinstance(value, (Dimension, TimeField, Measure)):
        value.require_native(operation="an Ibis field binding")
    if isinstance(value, (Dimension, Entity, Measure, Relationship, TimeField)):
        return (
            entity_exact(value).ref
            if isinstance(value, Entity)
            else relationship_binding(value).ref.ref
            if isinstance(value, Relationship)
            else value.ref.ref
        )
    return None


@traced(surface="semantic", operation="bind")
def bind(field: Deferred, entity_alias: ibis_ir.Table) -> ibis_ir.Value:
    """Apply a physical semantic field in a restricted Ibis field body.

    Args:
        field: Constructed physical field or native lookup symbol, supplied as a bare name.
        entity_alias: Direct Entity table parameter of the Dimension/TimeDimension body.
    Returns: The owning field's Ibis value on that Entity alias.
    Example: ``return ms.bind(region, rows).upper()`` inside @ms.dimension(entity=orders).
    Constraints: Loader binding checks field kind and ownership in the completed consumer scope; lookup symbols allow forward dependencies. Pure Measure/Metric bodies use constructed fields directly.
    """
    return _bind_physical_field(field, entity_alias)


def _is_bind_target(node: ast.expr, symbols: Mapping[str, object]) -> bool:
    if isinstance(node, ast.Name):
        value = symbols.get(node.id)
        return value is bind or value is _bind_physical_field
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        owner = symbols.get(node.value.id)
        return getattr(owner, node.attr, None) in (bind, _bind_physical_field)
    return False


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

    @override
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
                    received=ast.unparse(node),
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
                expected="ms.bind(field_ref, entity_parameter) with two positional bare names",
                received=ast.unparse(node),
                hint=(
                    "Assign the exact field Ref to a "
                    "module-level name before the decorator, then bind that name to a direct "
                    "Entity parameter; do not construct refs or call factories inside ms.bind."
                ),
            )
        value = self._symbols.get(node.args[0].id)
        field_ref = _binding_field_ref(value)
        if field_ref is None:
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_BINDING_REF,
                message=(
                    f"Expression body {self._owning_ref.key!r} binds name "
                    f"{node.args[0].id!r}, which does not resolve to an exact field Ref."
                ),
                refs=(self._owning_ref.key,),
                expected="a dimension, time_dimension, or measure Ref",
                received=f"{node.args[0].id}: {type(value).__name__}",
                hint=(
                    f"Define {node.args[0].id} before the decorator from a declared field "
                    "or ms.ref.<kind>(path) with its exact existing "
                    "path. Cross-file fields do not require importing the declaration module."
                ),
                details={"binding_ref_name": node.args[0].id, "binding_call": ast.unparse(node)},
            )
        if field_ref.kind not in _FIELD_KINDS:
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_BINDING_REF,
                message=(
                    f"Expression body {self._owning_ref.key!r} calls non-field ref "
                    f"{field_ref.key!r}."
                ),
                refs=(self._owning_ref.key, field_ref.key),
                expected="a dimension, time_dimension, or measure Ref",
                received=f"{node.args[0].id}: {field_ref.kind.value}",
                hint=(
                    f"Replace {node.args[0].id} with a bare name holding the exact field "
                    "Ref to bind; Entity and Metric refs are not row-level fields."
                ),
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
                received=ast.unparse(node),
                hint=(
                    f"Bind {node.args[0].id} to one of the "
                    "direct Entity parameters: "
                    + ", ".join(self._parameter_positions)
                    + ". Move table filtering or transformation out of the binding argument."
                ),
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
        constant_bindings: Mapping[str, ScalarConstant],
        symbol_tokens: Mapping[str, str] | None = None,
    ) -> None:
        self._symbols = symbols
        self._parameter_positions = parameter_positions
        self._binding_indexes = binding_indexes
        self._constant_bindings = constant_bindings
        self._local_names: dict[str, str] = {}
        self._symbol_tokens = symbol_tokens or {}

    @override
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
        self._local_names = {
            statement.targets[0].id: f"local_{index}"
            for index, statement in enumerate(node.body)
            if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name)
        }
        self.generic_visit(node)
        return node

    @override
    def visit_arg(self, node: ast.arg) -> ast.AST:
        position = self._parameter_positions.get(node.arg)
        if position is not None:
            node.arg = f"entity_{position}"
        node.annotation = None
        node.type_comment = None
        return node

    @override
    def visit_Name(self, node: ast.Name) -> ast.AST:
        position = self._parameter_positions.get(node.id)
        if position is not None:
            node.id = f"entity_{position}"
        elif node.id in self._local_names:
            node.id = self._local_names[node.id]
        elif node.id in self._symbol_tokens:
            # Encode the resolved Ibis symbol identity, not the alias spelling:
            # two aliases for one symbol normalize alike, one alias rebound to a
            # different Ibis symbol produces a different normalized body.
            return ast.copy_location(
                ast.Name(id=self._symbol_tokens[node.id], ctx=ast.Load()),
                node,
            )
        elif node.id in self._constant_bindings:
            return ast.copy_location(
                ast.Constant(value=self._constant_bindings[node.id]),
                node,
            )
        return node

    @override
    def visit_Call(self, node: ast.Call) -> ast.AST:
        if (
            _is_bind_target(node.func, self._symbols)
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Name)
        ):
            value = self._symbols.get(node.args[0].id)
            argument = node.args[1]
            ref = _binding_field_ref(value)
            if ref is not None and isinstance(argument, ast.Name):
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
    constant_bindings: Mapping[str, ScalarConstant],
    symbol_tokens: Mapping[str, str] | None = None,
) -> str:
    normalized = copy.deepcopy(function)
    transformed = _NormalizedBody(
        symbols=symbols,
        parameter_positions=parameter_positions,
        binding_indexes=binding_indexes,
        constant_bindings=constant_bindings,
        symbol_tokens=symbol_tokens,
    ).visit(normalized)
    ast.fix_missing_locations(transformed)
    encoded = _stable_ast_dump(transformed).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _physical_source_columns(
    function: ast.FunctionDef,
    *,
    parameter_positions: Mapping[str, int],
) -> tuple[str, ...]:
    columns: list[str] = []
    parameter_names = frozenset(parameter_positions)
    for node in ast.walk(function):
        column: str | None = None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in parameter_names:
                column = node.attr
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in parameter_names
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            column = node.slice.value
        if column is not None and column not in columns:
            columns.append(column)
    return tuple(columns)


def _entity_table_source_columns(
    function: ast.FunctionDef,
    *,
    parameter_positions: Mapping[str, int],
) -> tuple[str, ...]:
    """Capture column reads on the injected Entity parameter.

    Method calls such as ``raw.filter(...)`` attribute the method name to the
    parameter; only value reads (attribute/subscript outside a call-func
    position) are source columns.
    """
    parameter_names = frozenset(parameter_positions)
    call_func_nodes: set[int] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            call_func_nodes.add(id(node.func))
    columns: list[str] = []
    for node in ast.walk(function):
        if not (
            isinstance(node, (ast.Attribute, ast.Subscript))
            and isinstance(node.value, ast.Name)
            and node.value.id in parameter_names
        ):
            continue
        if id(node) in call_func_nodes:
            continue
        column: str | None = None
        if isinstance(node, ast.Attribute):
            column = node.attr
        elif isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            column = node.slice.value
        if column is not None and column not in columns:
            columns.append(column)
    return tuple(columns)


_ENTITY_EXECUTION_ATTRS: frozenset[str] = frozenset(
    {
        "compile",
        "execute",
        "sql",
        "raw_sql",
        "to_arrow",
        "to_csv",
        "to_delta",
        "to_json",
        "to_pandas",
        "to_parquet",
        "to_polars",
        "to_pyarrow",
        "to_torch",
    }
)


def _validate_entity_table_no_execution(
    function: ast.FunctionDef,
    *,
    owning_key: str,
) -> None:
    """Reject materialization or SQL escape attributes inside the Entity body."""
    for node in ast.walk(entity_table_body_nodes(function)):
        if isinstance(node, ast.Attribute) and node.attr in _ENTITY_EXECUTION_ATTRS:
            raise SemanticLoadError(
                kind=ErrorKind.SQL_ESCAPE_HATCH,
                message=(
                    f"Entity body {owning_key!r} uses .{node.attr}, which materializes "
                    "or rewrites the expression. Return the Ibis Table expression itself."
                ),
                refs=(owning_key,),
                expected="one returned Ibis Table expression over the injected parameter",
                received=f".{node.attr}",
                hint=(
                    "Marivo materializes the Entity relation through its resolver; "
                    "remove the execution or SQL call from the body."
                ),
            )


_BODY_KIND_BY_REF: dict[
    SemanticKind,
    Literal["dimension", "time_dimension", "measure", "metric"],
] = {
    SemanticKind.DIMENSION: "dimension",
    SemanticKind.TIME_DIMENSION: "time_dimension",
    SemanticKind.MEASURE: "measure",
    SemanticKind.METRIC: "metric",
}


def compile_expression_body(
    fn: Callable[..., object],
    *,
    owning_ref: Ref[SemanticKindTag],
    ordered_entity_refs: tuple[Ref[EntityKind], ...] = (),
    body_kind: Literal[
        "dimension",
        "time_dimension",
        "measure",
        "metric",
        "entity_table",
    ]
    | None = None,
) -> ExpressionBody:
    """Validate one decorator body and capture exact semantic field bindings."""
    owning = _require_exact_ref(owning_ref, parameter="owning_ref")
    for entity_ref in ordered_entity_refs:
        _require_exact_ref(
            entity_ref,
            allowed=frozenset({SemanticKind.ENTITY}),
            parameter="ordered_entity_refs item",
        )
    resolved_kind = body_kind if body_kind is not None else _BODY_KIND_BY_REF.get(owning.kind)
    if resolved_kind is None or (
        body_kind == "entity_table" and owning.kind is not SemanticKind.ENTITY
    ):
        raise SemanticLoadError(
            kind=ErrorKind.INVALID_BINDING_REF,
            message=f"Ref {owning.key!r} cannot own an expression body.",
            refs=(owning.key,),
            expected=(
                "dimension, time_dimension, measure, or metric"
                if body_kind != "entity_table"
                else "entity"
            ),
            received=owning.kind.value,
        )
    body_kind = resolved_kind
    function, line_offset = _load_function_ast(fn)
    if body_kind == "entity_table":
        parameters = (*function.args.posonlyargs, *function.args.args)
        parameter_positions = {parameter.arg: index for index, parameter in enumerate(parameters)}
        symbols = _resolved_symbols(fn)
        validate_entity_table_body_ast(function, name=fn.__name__, line_offset=line_offset)
        _validate_entity_table_no_execution(function, owning_key=owning.key)
        symbol_tokens = validate_entity_table_closed_symbols(
            function,
            symbols=symbols,
            parameter_names=frozenset(parameter_positions),
            is_bind_target=_is_bind_target,
            owning_key=owning.key,
        )
        parameter = parameters[0].arg if parameters else ""
        entities = {parameter: cast("Ref[EntityKind]", owning)} if parameter else {}
        description = describe_expression(
            function,
            entities=entities,
            bindings={},
        )
        return ExpressionBody(
            description=description,
            callable=fn,
            body_ast_hash=_normalized_body_hash(
                function,
                symbols=symbols,
                parameter_positions=parameter_positions,
                binding_indexes={},
                constant_bindings={},
                symbol_tokens=symbol_tokens,
            ),
            parameter_count=1,
            bindings=(),
            source_syntax=ast.unparse(function),
            syntax_tree=function,
            source_columns=_entity_table_source_columns(
                function,
                parameter_positions=parameter_positions,
            ),
        )

    validate_metric_body_ast(
        fn, "base", body_kind=body_kind, _function=function, _line_offset=line_offset
    )
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
    collector = _BindingCollector(
        symbols=symbols,
        parameter_positions=parameter_positions,
        owning_ref=owning,
    )
    collector.visit(function)
    description_bindings: dict[int, tuple[Ref[FieldKind], Ref[EntityKind]]] = {}
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and _is_bind_target(node.func, symbols)
            and (
                len(node.args) == 2
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Name)
            )
        ):
            position = parameter_positions[node.args[1].id]
            for binding in collector.bindings:
                if (
                    binding.to_ref() == _binding_field_ref(symbols[node.args[0].id])
                    and binding.entity_position == position
                ):
                    description_bindings[id(node)] = (
                        binding.to_ref(),
                        ordered_entity_refs[position],
                    )
    description = describe_expression(
        function,
        entities={
            name: ordered_entity_refs[position] for name, position in parameter_positions.items()
        },
        bindings=description_bindings,
    )
    symbol_tokens = {
        node.id: token
        for node in ast.walk(function)
        if isinstance(node, ast.Name)
        and (token := builtin_udf_token(symbols.get(node.id))) is not None
    }
    return ExpressionBody(
        description=description,
        callable=fn,
        body_ast_hash=_normalized_body_hash(
            function,
            symbols=symbols,
            parameter_positions=parameter_positions,
            binding_indexes=collector.index_by_key,
            constant_bindings={},
            symbol_tokens=symbol_tokens,
        ),
        parameter_count=len(parameters),
        bindings=tuple(collector.bindings),
        source_syntax=ast.unparse(function),
        syntax_tree=function,
        bound_call_ids=frozenset(
            id(node)
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and _is_bind_target(node.func, symbols)
        ),
        source_columns=_physical_source_columns(
            function,
            parameter_positions=parameter_positions,
        ),
    )


def _validate_body_result(
    result: object,
    *,
    owning_ref: Ref[SemanticKindTag],
    table_result: bool = False,
) -> ir.Value:
    if not isinstance(result, ir.Value):
        if table_result and isinstance(result, ibis.Table):
            return cast("ir.Value", result)
        raise _binding_runtime_error(
            ErrorKind.BINDING_RESULT_INVALID,
            f"Expression body {owning_ref.key!r} must return one Ibis value.",
            refs=(owning_ref,),
            expected="ibis.expr.types.Table" if table_result else "ibis.expr.types.Value",
            received=type(result).__name__,
        )
    if table_result and not isinstance(result, ir.Table):
        raise _binding_runtime_error(
            ErrorKind.BINDING_RESULT_INVALID,
            f"Expression body {owning_ref.key!r} must return one Ibis Table relation.",
            refs=(owning_ref,),
            expected="ibis.expr.types.Table",
            received=type(result).__name__,
        )
    return result


def _call_body(
    body: ExpressionBody,
    aliases: tuple[ir.Table, ...],
    *,
    owning_ref: Ref[SemanticKindTag],
    table_result: bool = False,
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
    return _validate_body_result(result, owning_ref=owning_ref, table_result=table_result)


def evaluate_expression_body(
    *,
    catalog_definition_fingerprint: str,
    expression_sidecar: CompiledExpressionSidecar,
    owning_ref: Ref[SemanticKindTag],
    body: ExpressionBody,
    entity_refs: tuple[Ref[EntityKind], ...],
    aliases: tuple[ir.Table, ...],
    table_result: bool = False,
) -> ir.Value:
    """Evaluate one root body inside a fresh task-local binding context.

    With ``table_result`` the body must return one Ibis Table relation; the
    default accepts any Ibis value (scalars and columns for fields and
    metrics).
    """
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
        return _call_body(body, aliases, owning_ref=owning, table_result=table_result)
    finally:
        _EXPRESSION_BINDING_CONTEXT.reset(token)


def _bind_physical_field(field: Deferred | Field, entity_alias: ir.Table, /) -> ir.Value:
    """Evaluate a declared field binding on its owning Entity alias."""
    ref = _binding_field_ref(field)
    if ref is None:
        raise _binding_runtime_error(
            ErrorKind.INVALID_BINDING_REF,
            "Semantic field application requires an exact Ref value.",
            expected="Ref[dimension | time_dimension | measure]",
            received=type(ref).__name__,
        )
    if ref.kind not in _FIELD_KINDS:
        raise _binding_runtime_error(
            ErrorKind.INVALID_BINDING_REF,
            f"Ref {ref.key!r} is not a bindable semantic field ref.",
            refs=(ref,),
            expected="dimension, time_dimension, or measure Ref",
            received=ref.kind.value,
        )
    field_ref = ref
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
