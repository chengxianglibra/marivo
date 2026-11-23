"""Capture closed authored expression bodies as typed recipes."""

from __future__ import annotations

import ast
import inspect
import operator
import textwrap
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import FunctionType, ModuleType
from typing import Literal, NoReturn, cast

import ibis
import ibis.expr.types as ir
from ibis.common.deferred import Attr, Call, Deferred

from marivo._definition.deferred import (
    NATIVE_METHODS,
    SEMANTIC_METHODS,
    admitted_function,
    capture_function_call,
    inspect_capture,
    inspect_deferred,
    resolver_of,
)
from marivo._definition.population_parameters import parameter_role
from marivo._definition.relations import PopulationParameter
from marivo._definition.types import DefinitionGraphError, InputContext
from marivo._definition.windows import (
    AllHistory,
    Since,
    ToDate,
    TrailingDuration,
    TrailingPeriods,
    all_history,
    since,
    to_date,
    trailing_duration,
    trailing_periods,
)
from marivo._kernel.grouping import Grouping
from marivo._kernel.input_context import captured_context
from marivo._kernel.refs import (
    Ref,
    SemanticKind,
)
from marivo._kernel.symbolic import native_symbol_ref
from marivo._kernel.temporal import Grain, InstantAnchor, TimeScope
from marivo.semantic import _rescale
from marivo.semantic._deferred_semantics import (
    capture_source_time,
    consume_metric,
    consume_row,
)
from marivo.semantic._expression_carrier import _merge, _parameter
from marivo.semantic._expression_values import (
    MetricExpression,
    NumericMetric,
    NumericRow,
    RowExpression,
    _ExpressionCarrier,
)
from marivo.semantic._paths import Path, path
from marivo.semantic.catalog_model.objects import Entity as CatalogEntity
from marivo.semantic.catalog_model.objects import Relationship as CatalogRelationship
from marivo.semantic.catalog_model.objects import RelationshipStep
from marivo.semantic.errors import (
    ErrorKind,
    SemanticExpressionError,
    SemanticLoadError,
)
from marivo.semantic.expressions.inputs import _as_deferred
from marivo.semantic.expressions.objects import _Entity as Entity
from marivo.semantic.expressions.objects import _Relationship as Relationship
from marivo.semantic.loading_context import _LOADER_CTX
from marivo.semantic.static_check.ast_rules import (
    validate_declaration_body_ast,
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


def _load_function_ast(fn: Callable[..., object]) -> tuple[ast.FunctionDef, int]:
    ctx = _LOADER_CTX.get()
    try:
        source = None
        if ctx is not None and ctx.model_package is not None:
            original = fn
            if not isinstance(fn, FunctionType) or "__wrapped__" in fn.__dict__:
                original = inspect.unwrap(fn)
                if inspect.ismethod(original):
                    original = original.__func__
            if (
                isinstance(original, FunctionType)
                and isinstance(module_name := original.__globals__.get("__name__"), str)
                and module_name.startswith(f"{ctx.model_package}.")
            ):
                code = original.__code__
                model_source = ctx.sources.captured(code.co_filename)
                if model_source is not None:
                    if original is fn:
                        function = model_source.functions.get((code.co_firstlineno, fn.__name__))
                        if function is not None:
                            return function, code.co_firstlineno - 1
                    source = model_source.function_source(code.co_firstlineno)
        if source is None:
            source = inspect.getsource(fn)
        tree = ast.parse(textwrap.dedent(source))
    except (OSError, TypeError, IndentationError, SyntaxError) as exc:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Expression body {fn.__name__!r} source is unavailable or invalid.",
            expected="inspectable Python source for the decorated function",
            received=type(exc).__name__,
        ) from exc
    return _find_function_node(tree, fn.__name__), 0


def _resolved_symbols(fn: Callable[..., object]) -> dict[str, object]:
    closure = inspect.getclosurevars(fn)
    symbols: dict[str, object] = dict(fn.__globals__)
    symbols.update(closure.globals)
    symbols.update(closure.nonlocals)
    return symbols


class _DeclarationExpressionBuilder:
    """Construct closed expression graphs without invoking the authored function."""

    def __init__(
        self,
        symbols: dict[str, object],
        name: str,
        stage: Literal["measure", "metric"],
        line_offset: int,
    ) -> None:
        self.symbols = symbols
        self.name = name
        self.stage = stage
        self._line_offset = line_offset
        self.input_contexts: list[InputContext] = []

    def fail(
        self,
        node: ast.AST,
        expected: str,
        received: str,
        *,
        kind: ErrorKind = ErrorKind.INVALID_COMPONENT_BODY,
    ) -> NoReturn:
        raise SemanticExpressionError(
            kind=kind,
            message=f"{self.stage.title()} declaration {self.name!r} contains an invalid expression.",
            refs=(self.name,),
            expected=expected,
            received=received,
            action="Use constructed semantic dependencies and their stage-specific operations; move helper calls outside the declaration.",
            location_label=(
                f"line {getattr(node, 'lineno', self._line_offset) - self._line_offset}, "
                f"column {getattr(node, 'col_offset', 0)}"
            ),
        )

    def dependency(self, value: object, node: ast.Name) -> object:

        if type(value) is tuple:
            return tuple(self.dependency(item, node) for item in value)
        if isinstance(value, (Grouping, PopulationParameter)):
            self.input_contexts.append(captured_context(value))
            return value
        if isinstance(value, Grain):
            return value
        if isinstance(value, _ExpressionCarrier):
            self.input_contexts.append(captured_context(value))
            value = _as_deferred(value)

        if isinstance(value, (CatalogEntity, CatalogRelationship, Path, RelationshipStep)):
            # Preserve the named boundary; controlled count/path capture owns conversion.
            self.input_contexts.append(captured_context(value))
            return value
        if isinstance(value, Deferred):
            self.capture(value)
            return value
        if isinstance(value, Ref):
            self.fail(
                node,
                "a native symbolic lookup or a closed semantic expression",
                f"{node.id}: {value.key}",
                kind=ErrorKind.INVALID_EXPRESSION_STAGE,
            )
        if type(value) in (
            Entity,
            Relationship,
            str,
            int,
            float,
            Decimal,
            bool,
            date,
            datetime,
            timedelta,
            TimeScope,
            InstantAnchor,
            AllHistory,
            Since,
            ToDate,
            TrailingDuration,
            TrailingPeriods,
            type(None),
        ):
            return value
        if isinstance(value, ir.Scalar) and isinstance(value.op(), ibis.expr.operations.Literal):
            return value
        self.fail(
            node,
            "a controlled Deferred expression or immutable literal",
            f"{node.id}: {type(value).__name__}",
            kind=ErrorKind.INVALID_EXPRESSION_STAGE,
        )

    def capture(self, value: Deferred) -> None:

        try:
            self.input_contexts.append(inspect_capture(value).context)
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation=f"{self.stage} declaration {self.name}"
            ) from error

    def _function(self, node: ast.expr) -> Callable[..., object] | None:

        semantic_factories = (
            all_history,
            since,
            to_date,
            trailing_duration,
            trailing_periods,
            _rescale.rescale,
            path,
        )

        value: object = None
        if isinstance(node, ast.Name):
            value = self.symbols.get(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = self.symbols.get(node.value.id)
            if module is ibis:
                value = vars(ibis).get(node.attr)
            elif (
                isinstance(module, ModuleType)
                and module.__name__ == "marivo.analysis"
                and node.attr == "path"
            ):
                value = path
            elif isinstance(module, ModuleType) and module.__name__ in {
                "marivo.analysis",
                "marivo.semantic",
            }:
                value = vars(module).get(node.attr)
        if admitted_function(value) or any(value is factory for factory in semantic_factories):
            # Exact identity admits only controlled expression constructors.
            return cast("Callable[..., object]", value)
        return None

    def _rescale_divisor(self, node: ast.expr) -> int | float | Decimal:
        """Evaluate only closed numeric arithmetic in rescale's literal parameter."""
        operations: dict[
            type[ast.operator], Callable[[int | float | Decimal, int | float | Decimal], object]
        ] = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
        }
        try:
            if isinstance(node, ast.BinOp) and type(node.op) in operations:
                result = operations[type(node.op)](
                    self._rescale_divisor(node.left), self._rescale_divisor(node.right)
                )
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                operand = self._rescale_divisor(node.operand)
                result = -operand if isinstance(node.op, ast.USub) else operand
            elif isinstance(node, (ast.Name, ast.Constant)):
                result = self.value(node)
            else:
                raise ValueError("only numeric constants and arithmetic are admitted")
            if isinstance(result, (int, float, Decimal)) and not isinstance(result, bool):
                return result
            raise ValueError(f"received {type(result).__name__}")
        except (TypeError, ValueError, ArithmeticError) as error:
            self.fail(
                node,
                "closed numeric literal arithmetic for the rescale divisor",
                str(error),
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            )

    def value(self, node: ast.expr) -> object:
        if isinstance(node, ast.Name):
            if node.id not in self.symbols:
                self.fail(node, "an existing dependency or earlier local name", node.id)
            return self.dependency(self.symbols[node.id], node)
        if isinstance(node, ast.Constant) and (
            node.value is None or type(node.value) in (str, int, float, bool)
        ):
            return node.value
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(self.value(item) for item in node.elts)
        if isinstance(node, ast.BinOp):
            return self._binary_value(node)
        if isinstance(node, ast.UnaryOp):
            return self._unary_value(node)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            return self._comparison_value(node)
        if isinstance(node, ast.Call):
            return self._call_value(node)
        self.fail(node, "closed semantic expression syntax", ast.unparse(node))

    def _reflected_binary(self, node: ast.BinOp, left: object, right: Deferred) -> object:
        if isinstance(node.op, ast.Add):
            return right.__radd__(left)
        if isinstance(node.op, ast.Sub):
            return right.__rsub__(left)
        if isinstance(node.op, ast.Mult):
            return right.__rmul__(left)
        if isinstance(node.op, ast.Div):
            return right.__rtruediv__(left)
        if isinstance(node.op, ast.Pow):
            return right.__rpow__(left)
        if isinstance(node.op, ast.Mod):
            return right.__rmod__(left)
        self.fail(node, "native arithmetic with a controlled Deferred operand", ast.unparse(node))

    def _binary_value(self, node: ast.BinOp) -> object:
        left, right = self.value(node.left), self.value(node.right)
        if isinstance(left, Deferred):
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left**right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.BitAnd):
                return left & right
            if isinstance(node.op, ast.BitOr):
                return left | right
        elif isinstance(right, Deferred):
            return self._reflected_binary(node, left, right)
        self.fail(node, "native arithmetic with a controlled Deferred operand", ast.unparse(node))

    def _unary_value(self, node: ast.UnaryOp) -> object:
        value = self.value(node.operand)
        if isinstance(value, Deferred):
            if isinstance(node.op, ast.Invert):
                return ~value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return value
        if type(value) in (int, float, Decimal):
            number = cast("int | float | Decimal", value)
            if isinstance(node.op, ast.USub):
                return -number
            if isinstance(node.op, ast.UAdd):
                return number
        self.fail(node, "closed semantic expression syntax", ast.unparse(node))

    def _comparison_value(self, node: ast.Compare) -> object:
        left, right = self.value(node.left), self.value(node.comparators[0])
        if isinstance(left, Deferred):
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
            if isinstance(node.ops[0], ast.Lt):
                return left < right
            if isinstance(node.ops[0], ast.LtE):
                return left <= right
            if isinstance(node.ops[0], ast.Gt):
                return left > right
            if isinstance(node.ops[0], ast.GtE):
                return left >= right
        elif isinstance(right, Deferred):
            if isinstance(node.ops[0], ast.Eq):
                return right == left
            if isinstance(node.ops[0], ast.NotEq):
                return right != left
            if isinstance(node.ops[0], ast.Lt):
                return right > left
            if isinstance(node.ops[0], ast.LtE):
                return right >= left
            if isinstance(node.ops[0], ast.Gt):
                return right < left
            if isinstance(node.ops[0], ast.GtE):
                return right <= left
        self.fail(node, "closed semantic expression syntax", ast.unparse(node))

    def _call_value(self, node: ast.Call) -> object:
        if any(keyword.arg is None for keyword in node.keywords):
            self.fail(node, "explicit keyword arguments", "keyword unpacking")
        method = self._function(node.func)
        deferred_receiver: tuple[Deferred, str] | None = None
        if method is None and isinstance(node.func, ast.Attribute):
            receiver = self.value(node.func.value)
            name = node.func.attr
            if isinstance(receiver, Deferred) and name in NATIVE_METHODS | SEMANTIC_METHODS:
                if parameter_role(resolver_of(receiver)) in {"population", "grouping", "axes"}:
                    self.fail(
                        node,
                        "a precomputed Population result captured at module scope",
                        ast.unparse(node.func),
                    )
                # Deferred attributes construct a recipe; they do not invoke author code.
                method = cast("Callable[..., object]", getattr(receiver, name))
                deferred_receiver = (receiver, name)
            elif isinstance(receiver, (Entity, CatalogEntity)) and name == "count":
                method = receiver.count
            else:
                if isinstance(receiver, CatalogRelationship) and name == "at":
                    method = receiver.at
        if method is None:
            self.fail(
                node,
                "an admitted Ibis constructor or semantic expression method",
                ast.unparse(node.func),
            )
        args = [self.value(arg) for arg in node.args]

        kwargs = {
            keyword.arg: self._rescale_divisor(keyword.value)
            if method is _rescale.rescale and keyword.arg == "divisor"
            else self.value(keyword.value)
            for keyword in node.keywords
            if keyword.arg is not None
        }
        if deferred_receiver is not None:
            target, operation = deferred_receiver
            if operation == "via" and not kwargs:
                return Deferred(
                    Call(
                        Attr(resolver_of(target), operation),
                        *(_parameter(value) for value in args),
                    )
                )
            default = capture_source_time(
                resolver_of(target),
                _parameter(kwargs["time"]) if "time" in kwargs else None,
                operation=operation,
            )
            if default is not None:
                kwargs["time"] = Deferred(default)

        if admitted_function(method):
            try:
                capture_function_call(method, tuple(args), kwargs)
            except DefinitionGraphError as error:
                raise SemanticExpressionError.from_definition(
                    error, operation=f"{self.stage} declaration {self.name}"
                ) from error
            try:
                return method(*args, **kwargs)
            except (TypeError, ValueError) as error:
                self.fail(
                    node,
                    "valid scalar or expression arguments for the admitted Ibis constructor",
                    str(error),
                    kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                )
        result = method(*args, **kwargs)
        if isinstance(result, _ExpressionCarrier):
            self.input_contexts.append(captured_context(result))
            return _as_deferred(result)
        return result


def _construct_declaration_expression(
    fn: Callable[..., object], *, stage: Literal["measure", "metric"]
) -> tuple[object, InputContext]:
    try:
        function, line_offset = _load_function_ast(fn)
    except SemanticLoadError as error:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_COMPONENT_BODY,
            message=f"{stage.title()} declaration {fn.__name__!r} requires inspectable source.",
            expected="a zero-argument Python function with inspectable source",
            received=str(error.received),
            action="Define the declaration in a Python source file and decorate it again.",
        ) from error
    symbols = _resolved_symbols(fn)
    validate_declaration_body_ast(function, symbols=frozenset(symbols), line_offset=line_offset)
    builder = _DeclarationExpressionBuilder(symbols, fn.__name__, stage, line_offset)
    for stmt in function.body:
        if isinstance(stmt, ast.Assign):
            target = stmt.targets[0]
            assert isinstance(target, ast.Name)
            value = builder.value(stmt.value)
            builder.symbols[target.id] = value
        elif isinstance(stmt, ast.Return):
            assert stmt.value is not None
            value = builder.value(stmt.value)
            if isinstance(value, Deferred):
                builder.capture(value)

                native = native_symbol_ref(value)
                if native is not None and native.kind not in {
                    SemanticKind.DIMENSION,
                    SemanticKind.TIME_DIMENSION,
                    SemanticKind.MEASURE,
                    SemanticKind.METRIC,
                }:
                    builder.fail(
                        stmt.value, "a numeric row or completed Metric expression", native.key
                    )

            return value, _merge(*builder.input_contexts)
    raise AssertionError("validated declaration has no final return")


def construct_measure_expression(fn: Callable[[], Deferred | NumericRow]) -> NumericRow:
    """Capture a pure declaration and validate its row-stage semantic facts."""

    result, context = _construct_declaration_expression(fn, stage="measure")
    if isinstance(result, Deferred):
        if not inspect_deferred(result).symbols:
            try:
                consume_row(result)
            except SemanticExpressionError as error:
                # Named inputs are rebound by the receiving scope. Captured business
                # facts cannot reject a formula whose current definitions may differ.
                if context.project_root is None or (
                    not error.details.get("defer_to_scope", False)
                    and error.kind
                    not in {
                        ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
                        ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                        ErrorKind.MISSING_SUM_PERMISSION,
                        ErrorKind.INVALID_WHERE_SCOPE,
                        ErrorKind.INVALID_TIME_PATH,
                    }
                ):
                    raise

        return RowExpression(resolver_of(result), context, "numeric")
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_EXPRESSION_STAGE,
        message="Measure declaration returned the wrong expression stage.",
        expected="a controlled Deferred row expression",
        received=type(result).__name__,
        action="Return the row expression before reduction; declare completed values with @ms.metric().",
    )


def construct_metric_expression(
    fn: Callable[[], Deferred | NumericMetric],
) -> NumericMetric:
    """Capture a pure declaration and validate its completed Metric stage."""

    result, context = _construct_declaration_expression(fn, stage="metric")
    if isinstance(result, Deferred):
        inspection = inspect_deferred(result)
        if not inspection.symbols:
            try:
                consume_metric(result)
            except SemanticExpressionError as error:
                # Named inputs are rebound by the receiving scope. Captured business
                # facts cannot reject a formula whose current definitions may differ.
                if context.project_root is None or (
                    not error.details.get("defer_to_scope", False)
                    and error.kind
                    not in {
                        ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
                        ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                        ErrorKind.MISSING_SUM_PERMISSION,
                        ErrorKind.INVALID_WHERE_SCOPE,
                        ErrorKind.INVALID_TIME_PATH,
                    }
                ):
                    raise

        return MetricExpression(resolver_of(result), context, "numeric")
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_EXPRESSION_STAGE,
        message="Metric declaration returned the wrong expression stage.",
        expected="a controlled Deferred completed Metric",
        received=type(result).__name__,
        action="Reduce rows or finish per(...).reducer() before declaring the Metric.",
    )
