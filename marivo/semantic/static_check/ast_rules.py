"""AST checks for admitted semantic declarations."""

from __future__ import annotations

import ast
import builtins
import hashlib
import inspect
import sys
import textwrap
from collections.abc import Callable, Mapping
from typing import Literal, cast

from marivo.semantic.constraints import ASTSpec, ConstraintId, get_constraint
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticExpressionError,
    SemanticLoadError,
)
from marivo.semantic.expressions.syntax import (
    _return_expr,
)
from marivo.semantic.ir import (
    DateParse,
    DimensionIR,
    HourPrefixParse,
    StrptimeParse,
    TimestampParse,
)

_PARTITION_TIME_COLUMN_NAMES = {
    "dt",
    "date",
    "ds",
    "log_date",
    "event_date",
    "order_date",
    "biz_date",
    "stat_date",
    "partition_date",
    "hh",
    "hour",
    "log_hour",
    "event_hour",
}

_PUSHDOWN_UNFRIENDLY_CALLS = {"cast", "as_date", "as_timestamp"}


def _source_column_name(node: ast.AST) -> str | None:
    """Return the base table column name for simple chained column expressions."""
    current = node
    while isinstance(current, ast.Call):
        current = current.func
    while isinstance(current, ast.Attribute):
        value = current.value
        if isinstance(value, ast.Name):
            return current.attr
        current = value
        while isinstance(current, ast.Call):
            current = current.func
    return None


def _has_pushdown_unfriendly_time_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr in _PUSHDOWN_UNFRIENDLY_CALLS:
            return True
    return False


def _time_dimension_pushdown_advisory(
    field_ir: DimensionIR,
    fn: Callable[..., object] | None,
    function: ast.FunctionDef | None = None,
) -> bool:
    if not field_ir.is_time_dimension:
        return False
    parse = field_ir.parse
    _temporal_parse_kinds = (DateParse, TimestampParse)
    if not isinstance(parse, _temporal_parse_kinds):
        return False
    if fn is None:
        return False
    expr = _return_expr(fn, function)
    if expr is None or not _has_pushdown_unfriendly_time_call(expr):
        return False
    source_column = _source_column_name(expr)
    if source_column is None:
        return False
    return source_column.lower() in _PARTITION_TIME_COLUMN_NAMES


_CAST_TARGET_TO_DECLARED: dict[str, set[str]] = {
    "date": {"date"},
    "timestamp": {"datetime", "timestamp"},
    "string": {"string"},
    "int32": {"integer"},
    "int64": {"integer"},
}


def _infer_terminal_cast(expr: ast.AST) -> str | None:
    """Walk a chained expression to find the terminal type-producing call.

    For table.col.cast("timestamp").cast("date"), the terminal call
    is .cast("date") — the root Call node evaluated last in the chain.
    Also detects .as_date() → "date" and .as_timestamp() → "timestamp".
    """
    current = expr
    while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
        method = current.func.attr
        if method == "cast" and current.args:
            arg = current.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        if method == "as_date":
            return "date"
        if method == "as_timestamp":
            return "timestamp"
        current = current.func.value
    return None


def _time_dimension_dtype_advisory(
    field_ir: DimensionIR,
    fn: Callable[..., object] | None,
    function: ast.FunctionDef | None = None,
) -> str | None:
    """Return a cast target that conflicts with the Dimension's declared parse result."""
    if not field_ir.is_time_dimension:
        return None
    parse = field_ir.parse
    _temporal_parse_kinds = (DateParse, TimestampParse)
    if not isinstance(parse, _temporal_parse_kinds):
        return None
    if fn is None:
        return None
    expr = _return_expr(fn, function)
    if expr is None:
        return None
    inferred = _infer_terminal_cast(expr)
    if inferred is None:
        return None
    compatible = _CAST_TARGET_TO_DECLARED.get(inferred)
    if compatible is None:
        return None
    parse = field_ir.parse
    data_type_val: str | None = None
    if parse is None:
        return None  # deferred parse — cannot validate at this stage
    if isinstance(parse, DateParse):
        data_type_val = "date"
    elif isinstance(parse, TimestampParse):
        data_type_val = "timestamp"
    elif isinstance(parse, StrptimeParse):
        data_type_val = "strptime"
    elif isinstance(parse, HourPrefixParse):
        data_type_val = "hour_prefix"
    if data_type_val not in compatible:
        return inferred
    return None


def validate_decorator_call(kind: str, payload: Mapping[str, object]) -> None:
    """Layer 1: decorator-time validation.  Raises SemanticDecoratorError.

    Currently a passthrough — decorator-time validation is handled inline
    in the authoring module.  This function exists as an extension point
    for future decorator-level checks.
    """


def _ast_spec_for(constraint_id: ConstraintId) -> ASTSpec:
    constraint = get_constraint(constraint_id)
    assert constraint is not None and constraint.ast_spec is not None
    return constraint.ast_spec


_EXPR_BODY_AST_SPEC = _ast_spec_for(ConstraintId.AST_SINGLE_RETURN)

_SQL_ESCAPE_ATTRS = frozenset(_EXPR_BODY_AST_SPEC.forbidden_attributes)

try:
    import ibis as _ibis

    _IBIS_TABLE_ATTRS: frozenset[str] = frozenset(
        name for name in dir(_ibis.Table) if not name.startswith("_")
    )
    del _ibis
except ImportError:
    _IBIS_TABLE_ATTRS = frozenset()

_AST_TRY_STAR = cast("type[ast.stmt]", getattr(ast, "TryStar", ast.Try))

_FORBIDDEN_STMT_TYPES: frozenset[type[ast.stmt]] = frozenset(
    {
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.Import,
        ast.ImportFrom,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.If,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        _AST_TRY_STAR,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Delete,
        ast.Global,
        ast.Nonlocal,
        ast.Raise,
        ast.Assert,
        ast.Pass,
        ast.Break,
        ast.Continue,
    }
)


class _BaseMetricASTValidator(ast.NodeVisitor):
    """Walk a single-return ibis expression body AST and accumulate errors."""

    def __init__(
        self,
        fn_name: str,
        *,
        body_label: str = "Metric body",
        symbols: frozenset[str] | None = None,
        pure_declaration: bool = False,
        line_offset: int = 0,
    ) -> None:
        self.fn_name = fn_name
        self.body_label = body_label
        self._symbols = symbols
        self._pure_declaration = pure_declaration
        self._line_offset = line_offset
        self.errors: list[SemanticError] = []
        self._param_names: set[str] = set()
        self._parent_map: dict[ast.AST, ast.AST] = {}

    def _add_error(
        self,
        kind: ErrorKind,
        message: str,
        *,
        constraint_id: ConstraintId,
    ) -> None:
        if self._pure_declaration:
            self.errors.append(
                SemanticExpressionError(
                    kind=ErrorKind.INVALID_COMPONENT_BODY,
                    message=message,
                    refs=(self.fn_name,),
                    expected="a zero-argument body with fresh locals and one final expression return",
                    received=message,
                    action="Remove control flow and mutation; return the constructed expression directly.",
                )
            )
            return
        self.errors.append(
            SemanticLoadError(
                kind=kind.value,
                message=message,
                refs=(self.fn_name,),
                constraint_id=constraint_id,
            )
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._pure_declaration and (
            node.args.posonlyargs
            or node.args.args
            or node.args.kwonlyargs
            or node.args.vararg is not None
            or node.args.kwarg is not None
        ):
            self._local_error(
                node,
                "a zero-argument declaration over already constructed fields",
                ast.unparse(node.args),
            )
        self._param_names = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args)}
        self._parent_map = {
            child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)
        }

        # Must have exactly one top-level Return (no nested returns in if/else/etc.)
        all_returns: list[ast.Return] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Return):
                all_returns.append(child)

        if len(all_returns) == 0:
            self._add_error(
                ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN,
                f"{self.body_label} of {self.fn_name!r} must contain exactly one "
                f"return statement, found none.",
                constraint_id=ConstraintId.AST_SINGLE_RETURN,
            )
        elif len(all_returns) > 1:
            self._add_error(
                ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN,
                f"{self.body_label} of {self.fn_name!r} must contain exactly one "
                f"return statement, found {len(all_returns)}.",
                constraint_id=ConstraintId.AST_SINGLE_RETURN,
            )

        leading_docstring = node.body[0] if node.body and _is_docstring_stmt(node.body[0]) else None
        for child in ast.walk(node):
            if child is node:
                continue
            if not isinstance(child, ast.stmt):
                continue
            if child is leading_docstring:
                continue
            # Allow the single Return; every other statement violates the
            # expression-body contract.
            if isinstance(child, ast.Return):
                continue
            if self._symbols is not None and isinstance(child, ast.Assign) and child in node.body:
                continue
            for forbidden_type in (*_FORBIDDEN_STMT_TYPES, ast.Expr):
                if isinstance(child, forbidden_type):
                    # Every statement that reaches this loop is a forbidden
                    # statement: returns are skipped above and wrong return
                    # counts are handled separately (AST_SINGLE_RETURN). Classify
                    # them uniformly so the hint can route the author to the
                    # body-free constructors and ibis conditionals.
                    self._add_error(
                        ErrorKind.INVALID_COMPONENT_BODY,
                        f"{self.body_label} of {self.fn_name!r} contains a forbidden "
                        f"{type(child).__name__} statement.",
                        constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
                    )
                    break

        if self._symbols is not None:
            statements = [stmt for stmt in node.body if stmt is not leading_docstring]
            if statements and not isinstance(statements[-1], ast.Return):
                self._local_error(
                    statements[-1], "a final return expression", "statement after return"
                )
            defined = set(self._param_names) | set(self._symbols)
            for stmt in statements:
                if isinstance(stmt, ast.Assign):
                    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                        self._local_error(
                            stmt, "one fresh local name", "non-name or multiple targets"
                        )
                        continue
                    name = stmt.targets[0].id
                    if name in defined:
                        self._local_error(
                            stmt, "one fresh local name", f"reassignment or shadowing of {name!r}"
                        )
                    self._validate_local_expression(stmt.value, defined)
                    defined.add(name)
                elif isinstance(stmt, ast.Return):
                    if stmt.value is None:
                        self._local_error(
                            stmt,
                            "a final stage-compatible return expression",
                            "return without a value",
                        )
                    else:
                        self._validate_local_expression(stmt.value, defined)

        # Walk only the function body for deeper AST checks. Decorator calls
        # are normal Python and are not part of the captured expression DSL.
        for stmt in node.body:
            self.visit(stmt)

    def _local_error(self, node: ast.AST, expected: str, received: str) -> None:
        line = getattr(node, "lineno", self._line_offset) - self._line_offset
        column = getattr(node, "col_offset", 0)
        if self._pure_declaration:
            self.errors.append(
                SemanticExpressionError(
                    kind=ErrorKind.INVALID_COMPONENT_BODY,
                    message=f"{self.body_label} of {self.fn_name!r} has an invalid local expression.",
                    refs=(self.fn_name,),
                    expected=expected,
                    received=received,
                    action="Use fresh local names in definition order and finish with one expression return.",
                    location_label=f"line {line}, column {column}",
                )
            )
            return
        self.errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_COMPONENT_BODY,
                message=f"{self.body_label} of {self.fn_name!r} has an invalid local expression at "
                f"line {line}, column {column}.",
                refs=(self.fn_name,),
                expected=expected,
                received=received,
                hint="Use fresh local names in definition order and finish with one Ibis return expression.",
            )
        )

    def _validate_local_expression(self, value: ast.expr, defined: set[str]) -> None:
        if self._pure_declaration:
            # Typed expression binding validates the closed expression grammar and receivers.
            return
        for child in ast.walk(value):
            if (
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id not in defined
            ):
                self._local_error(
                    child, "a parameter, existing symbol, or previously defined local", child.id
                )
            if isinstance(
                child,
                (
                    ast.NamedExpr,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                    ast.Yield,
                    ast.YieldFrom,
                    ast.Await,
                ),
            ):
                self._local_error(
                    child,
                    "an expression without nested bindings or control flow",
                    type(child).__name__,
                )
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id not in (self._symbols or ())
            ):
                self._local_error(
                    child,
                    "an existing expression callable",
                    f"local callable {child.func.id!r}",
                )
        if (
            isinstance(value, ast.Name)
            and value.id in (self._symbols or frozenset()) | self._param_names
        ):
            self._local_error(value, "an Ibis value expression", f"bare symbol {value.id!r}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._pure_declaration:
            return
        if node.attr in _SQL_ESCAPE_ATTRS:
            self._add_error(
                ErrorKind.SQL_ESCAPE_HATCH,
                f"{self.body_label} of {self.fn_name!r} uses .{node.attr}(), "
                f"which is not allowed. Use an Ibis expression body instead.",
                constraint_id=ConstraintId.AST_SQL_ESCAPE_HATCH,
            )
        if (
            _IBIS_TABLE_ATTRS
            and isinstance(node.value, ast.Name)
            and node.value.id in self._param_names
            and node.attr in _IBIS_TABLE_ATTRS
        ):
            # Method calls like orders.filter(...) are valid ibis; only flag
            # bare attribute access (return orders.schema), not call-site func.
            parent = self._parent_map.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                self._add_error(
                    ErrorKind.IBIS_ATTR_SHADOW,
                    f"{self.body_label} of {self.fn_name!r} accesses .{node.attr} on the "
                    f"entity table parameter '{node.value.id}', which shadows an ibis "
                    f"Table method/property. Use bracket notation instead: "
                    f'{node.value.id}["{node.attr}"].',
                    constraint_id=ConstraintId.AST_IBIS_ATTR_SHADOW,
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._pure_declaration:
            return
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "ms"
            and func.attr == "component"
        ):
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"{self.body_label} of {self.fn_name!r} calls ms.component(), "
                "which is no longer supported. Use completed Metric arithmetic "
                "over constructed semantic inputs.",
                constraint_id=ConstraintId.METRIC_COMPONENT_SCOPE,
            )
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"{self.body_label} of {self.fn_name!r} contains a lambda expression, which is not allowed.",
            constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
        )


def _is_docstring_stmt(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def validate_declaration_body_ast(
    function: ast.FunctionDef,
    *,
    symbols: frozenset[str],
    line_offset: int = 0,
) -> None:
    """Apply shared statement/local-binding rules to a pure expression declaration."""
    validator = _BaseMetricASTValidator(
        function.name,
        body_label="Expression declaration",
        symbols=symbols,
        pure_declaration=True,
        line_offset=line_offset,
    )
    validator.visit(function)
    if validator.errors:
        raise validator.errors[0]


def _stable_ast_dump(node: ast.AST) -> str:
    """Dump an AST using the Python 3.12 field layout.

    Identity hashes keep that text on every supported interpreter. Python 3.13
    omits empty lists unless ``show_empty`` is set, and Python 3.10 function
    and class nodes have no ``type_params`` field.
    """
    if sys.version_info >= (3, 13):
        text = ast.dump(node, include_attributes=False, show_empty=True)
    else:
        text = ast.dump(node, include_attributes=False)
    if sys.version_info < (3, 12):
        text = text.replace(", decorator_list=[])", ", decorator_list=[], type_params=[])")
    return text


def _body_hash_without_docstring(func_node: ast.FunctionDef) -> str:
    body = list(func_node.body)
    if body and _is_docstring_stmt(body[0]):
        body = body[1:]
    normalized = _stable_ast_dump(ast.Module(body=body, type_ignores=[]))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


_BODY_KIND_LABELS = {
    "dimension": "Dimension body",
    "time_dimension": "Time dimension body",
    "measure": "Measure body",
    "metric": "Metric body",
}


def validate_metric_body_ast(
    fn: Callable[..., object],
    mode: Literal["base"],
    *,
    body_kind: Literal["dimension", "time_dimension", "measure", "metric"] = "metric",
    _function: ast.FunctionDef | None = None,
    _line_offset: int = 0,
) -> str:
    """Layer 2: AST whitelist validation for base metric bodies.

    Returns the body AST hash excluding a leading docstring.

    Raises SemanticLoadError on validation failures.
    """
    if mode != "base":
        raise ValueError(f"unsupported metric body AST validation mode {mode!r}")
    body_label = _BODY_KIND_LABELS[body_kind]

    if _function is not None:
        func_node = _function
        body_hash = _body_hash_without_docstring(func_node)
    else:
        try:
            source = inspect.getsource(fn)
            source = textwrap.dedent(source)
            tree = ast.parse(source)
            func_node = None
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == fn.__name__:
                    func_node = node
                    break
            if func_node is None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        func_node = node
                        break

            if func_node is None:
                body_hash = hashlib.sha256(b"<no-function>").hexdigest()[:16]
            else:
                body_hash = _body_hash_without_docstring(func_node)
        except (OSError, TypeError, IndentationError):
            body_hash = hashlib.sha256(b"<unavailable>").hexdigest()[:16]
            return body_hash

    if func_node is None:
        return body_hash

    symbols: frozenset[str] | None = None
    if any(isinstance(stmt, ast.Assign) for stmt in func_node.body):
        closure = inspect.getclosurevars(fn)
        symbols = frozenset((*fn.__globals__, *closure.nonlocals, *vars(builtins)))
    base_validator = _BaseMetricASTValidator(
        fn.__name__, body_label=body_label, symbols=symbols, line_offset=_line_offset
    )
    base_validator.visit(func_node)
    if base_validator.errors:
        raise base_validator.errors[0]

    return body_hash


def validate_entity_table_body_ast(
    func_node: ast.FunctionDef, *, name: str, line_offset: int
) -> None:
    """Layer 2: AST validation for one Entity Table-expression body.

    The decorated function must take exactly one positional parameter and
    contain one optional docstring plus one ``return`` of an Ibis expression
    built from that parameter, supported Ibis expression symbols, and inline
    literals. Assignments, helper/nested functions, lambdas, control flow,
    SQL/execution escape hatches, and ``ms.bind`` are rejected.

    Raises SemanticLoadError on validation failures.
    """
    if func_node.name != name:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Entity body {name!r} has no function definition.",
            refs=(name,),
            expected="one function with one return",
            received="missing function",
        )

    # One positional parameter, no defaults, no variadic/keyword forms.
    args = func_node.args
    parameter_count = len(args.posonlyargs) + len(args.args)
    shape_errors: list[str] = []
    if parameter_count != 1:
        shape_errors.append(f"found {parameter_count} positional parameters")
    if args.vararg is not None or args.kwarg is not None:
        shape_errors.append("variadic parameters are not allowed")
    if args.kwonlyargs:
        shape_errors.append("keyword-only parameters are not allowed")
    if any(args.defaults) or any(args.kw_defaults):
        shape_errors.append("parameter defaults are not allowed")
    if shape_errors:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=(
                f"Entity body {name!r} must take exactly one positional "
                f"Table parameter: {'; '.join(shape_errors)}."
            ),
            refs=(name,),
            expected="def <name>(raw): return <one Ibis Table expression>",
            received="; ".join(shape_errors),
        )

    base_validator = _BaseMetricASTValidator(
        name, body_label="Entity body", line_offset=line_offset
    )
    base_validator.visit(func_node)
    if base_validator.errors:
        raise base_validator.errors[0]

    # Entity-specific expression nodes: assignment expressions (walrus) bind
    # local names and comprehensions iterate or rebind inside the expression,
    # so both violate the single-expression contract even though they are not
    # statements the shared validator checks.
    for node in ast.walk(func_node):
        if isinstance(node, ast.NamedExpr):
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_COMPONENT_BODY,
                message=(
                    f"Entity body {name!r} contains an assignment expression "
                    f"({ast.unparse(node)}), which is not allowed. Inline the value or "
                    "restructure as one expression."
                ),
                refs=(name,),
                expected="one return expression without name binding",
                received=ast.unparse(node),
                constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
            )
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_COMPONENT_BODY,
                message=(
                    f"Entity body {name!r} contains a {type(node).__name__}, which "
                    "is not allowed. Build the expression with Ibis methods over the "
                    "injected parameter instead of iterating Python collections."
                ),
                refs=(name,),
                expected="one return expression without iteration",
                received=ast.unparse(node),
                constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
            )
