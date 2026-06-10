"""Tests for marivo.semantic.validator — AST whitelist validation.

Tests cover:
- Base metric: single return allowed
- Base metric: multiple statements -> error
- Base metric: import -> error
- Base metric: assignment -> error
- Base metric: for/while/with/try -> error
- Base metric: .sql/.raw_sql -> SQL_ESCAPE_HATCH
- Base metric: ms.component() -> INVALID_COMPONENT_BODY
- Base metric: calling metric ref -> error
- Base metric: valid arithmetic expression -> ok
- Base metric: method calls on dataset arg -> ok
- Base metric: field ref calls -> ok
- Base metric: lambda expression -> error
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from marivo.semantic.errors import ErrorKind, SemanticLoadError
from marivo.semantic.validator import validate_metric_body_ast

# ---------------------------------------------------------------------------
# Base metric: valid bodies
# ---------------------------------------------------------------------------


def test_base_single_return_allowed() -> None:
    """A single return expression is the canonical metric body."""

    def revenue(table):  # type: ignore[no-untyped-def]
        return table.amount.sum()

    result = validate_metric_body_ast(revenue, "base")
    assert isinstance(result, str)
    assert len(result) > 0


def test_base_method_calls_on_dataset_arg() -> None:
    """Method calls on the dataset argument are allowed."""

    def revenue(table):  # type: ignore[no-untyped-def]
        return table.filter(table.status == "active").amount.sum()

    result = validate_metric_body_ast(revenue, "base")
    assert isinstance(result, str)


def test_base_field_ref_calls() -> None:
    """Calling field refs (Name calls) is allowed in base metrics."""

    def revenue(table):  # type: ignore[no-untyped-def]
        return amount(table).sum()  # noqa: F821

    result = validate_metric_body_ast(revenue, "base")
    assert isinstance(result, str)


def test_base_arithmetic_expression() -> None:
    """Binary arithmetic in the return expression is allowed."""

    def profit(table):  # type: ignore[no-untyped-def]
        return table.revenue.sum() - table.cost.sum()

    result = validate_metric_body_ast(profit, "base")
    assert isinstance(result, str)


def test_base_conditional_expression() -> None:
    """Conditional (ternary) expression is allowed."""

    def adjusted_revenue(table):  # type: ignore[no-untyped-def]
        return table.revenue.sum() if table.is_active else 0

    result = validate_metric_body_ast(adjusted_revenue, "base")
    assert isinstance(result, str)


def test_base_comparison_and_boolop() -> None:
    """Comparison and boolean operations are allowed."""

    def filtered_count(table):  # type: ignore[no-untyped-def]
        return (table.amount > 0).sum()

    result = validate_metric_body_ast(filtered_count, "base")
    assert isinstance(result, str)


def test_base_unary_op() -> None:
    """Unary operations are allowed."""

    def negated(table):  # type: ignore[no-untyped-def]
        return -table.amount.sum()

    result = validate_metric_body_ast(negated, "base")
    assert isinstance(result, str)


def test_base_cast_call() -> None:
    """Calling .cast() on a dataset column is allowed."""

    def revenue(table):  # type: ignore[no-untyped-def]
        return table.amount.cast("float").sum()

    result = validate_metric_body_ast(revenue, "base")
    assert isinstance(result, str)


def test_base_none_literal() -> None:
    """None literal is allowed."""

    def fallback(table):  # type: ignore[no-untyped-def]
        return table.amount.sum() or None

    result = validate_metric_body_ast(fallback, "base")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Base metric: forbidden bodies
# ---------------------------------------------------------------------------


def test_base_multiple_statements_error() -> None:
    """Multiple statements (not just a single return) are forbidden."""

    def bad_metric(table):  # type: ignore[no-untyped-def]
        x = 1
        return table.amount.sum()

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_metric, "base")
    assert exc_info.value.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN


def test_base_import_error() -> None:
    """Import statements in metric body are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            import os
            return table.amount.sum()
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    assert len(validator.errors) > 0
    assert validator.errors[0].kind == ErrorKind.INVALID_COMPONENT_BODY


def test_base_assignment_error() -> None:
    """Assignment statements in metric body are forbidden."""

    def bad_metric(table):  # type: ignore[no-untyped-def]
        x = table.amount.sum()
        return x

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_metric, "base")
    assert exc_info.value.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN


def test_base_for_loop_error() -> None:
    """For loops in metric body are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            for x in table.items:
                pass
            return table.amount.sum()
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    assert len(validator.errors) > 0


def test_base_while_loop_error() -> None:
    """While loops in metric body are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            while False:
                pass
            return table.amount.sum()
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    assert len(validator.errors) > 0


def test_base_with_statement_error() -> None:
    """With statements in metric body are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            with something():
                return table.amount.sum()
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    assert len(validator.errors) > 0


def test_base_try_statement_error() -> None:
    """Try statements in metric body are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            try:
                return table.amount.sum()
            except Exception:
                return 0
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    assert len(validator.errors) > 0


def test_base_sql_escape_hatch() -> None:
    """Calling .sql() or .raw_sql() is forbidden -> SQL_ESCAPE_HATCH."""

    def bad_metric(backend):  # type: ignore[no-untyped-def]
        return backend.sql("SELECT SUM(amount) FROM orders")

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_metric, "base")
    assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH


def test_base_raw_sql_escape_hatch() -> None:
    """Calling .raw_sql() is also forbidden."""

    def bad_metric(backend):  # type: ignore[no-untyped-def]
        return backend.raw_sql("SELECT 1")

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_metric, "base")
    assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH


def test_base_ms_component_call_rejected() -> None:
    """ms.component() is not supported in base metric bodies."""

    def bad_metric(table):  # type: ignore[no-untyped-def]
        return ms.component("amount")  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_metric, "base")

    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert exc_info.value.constraint_id == "metric_component_scope"


def test_derived_validation_mode_rejected() -> None:
    """The old derived-body validation mode should fail fast."""

    def revenue(table):  # type: ignore[no-untyped-def]
        return table.amount.sum()

    with pytest.raises(ValueError, match="unsupported metric body AST validation mode"):
        validate_metric_body_ast(revenue, "derived")  # type: ignore[arg-type]


def test_base_lambda_error() -> None:
    """Lambda expressions in metric body are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            f = lambda x: x.amount
            return f(table).sum()
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    assert len(validator.errors) > 0


def test_base_no_return_error() -> None:
    """A metric body with no return statement is forbidden."""

    def bad_metric(table):  # type: ignore[no-untyped-def]
        table.amount.sum()

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_metric, "base")
    assert exc_info.value.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN


def test_base_multiple_returns_error() -> None:
    """Multiple return statements are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            if table.is_active:
                return table.amount.sum()
            return 0
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    has_multi_return = any(
        e.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN for e in validator.errors
    )
    assert has_multi_return


# ---------------------------------------------------------------------------
# Hash stability
# ---------------------------------------------------------------------------


def test_body_ast_hash_deterministic() -> None:
    """Same function body produces the same hash."""

    def revenue(table):  # type: ignore[no-untyped-def]
        return table.amount.sum()

    hash1 = validate_metric_body_ast(revenue, "base")
    hash2 = validate_metric_body_ast(revenue, "base")
    assert hash1 == hash2


def test_different_bodies_different_hash() -> None:
    """Different function bodies produce different hashes."""

    def revenue_a(table):  # type: ignore[no-untyped-def]
        return table.amount.sum()

    def revenue_b(table):  # type: ignore[no-untyped-def]
        return table.cost.sum()

    hash_a = validate_metric_body_ast(revenue_a, "base")
    hash_b = validate_metric_body_ast(revenue_b, "base")
    assert hash_a != hash_b


# ---------------------------------------------------------------------------
# _time_dimension_dtype_advisory tests
# ---------------------------------------------------------------------------


def test_time_dimension_dtype_advisory_cast_date_declared_datetime() -> None:
    """Body .cast('date') with data_type='datetime' triggers advisory."""

    def order_date(table):  # type: ignore[no-untyped-def]
        return table.dt.cast("timestamp").cast("date")

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.ir import DimensionIR, DimensionKind, SourceLocation
    from marivo.semantic.validator import _time_dimension_dtype_advisory

    field_ir = DimensionIR(
        semantic_id="model.order_date",
        domain="model",
        entity="orders",
        name="order_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="datetime",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date",
        location=SourceLocation(file="test.py", line=1),
    )
    assert _time_dimension_dtype_advisory(field_ir, order_date) == "date"


def test_time_dimension_dtype_advisory_cast_timestamp_declared_datetime_ok() -> None:
    """Body .cast('timestamp') with data_type='datetime' is compatible — no advisory."""

    def created_at(table):  # type: ignore[no-untyped-def]
        return table.ts.cast("timestamp")

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.ir import DimensionIR, DimensionKind, SourceLocation
    from marivo.semantic.validator import _time_dimension_dtype_advisory

    field_ir = DimensionIR(
        semantic_id="model.created_at",
        domain="model",
        entity="orders",
        name="created_at",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="datetime",
        granularity="day",
        required_prefix=None,
        python_symbol="created_at",
        location=SourceLocation(file="test.py", line=1),
    )
    assert _time_dimension_dtype_advisory(field_ir, created_at) is None


def test_time_dimension_dtype_advisory_cast_date_declared_date_ok() -> None:
    """Body .cast('date') with data_type='date' is compatible — no advisory."""

    def order_date(table):  # type: ignore[no-untyped-def]
        return table.dt.cast("date")

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.ir import DimensionIR, DimensionKind, SourceLocation
    from marivo.semantic.validator import _time_dimension_dtype_advisory

    field_ir = DimensionIR(
        semantic_id="model.order_date",
        domain="model",
        entity="orders",
        name="order_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="date",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date",
        location=SourceLocation(file="test.py", line=1),
    )
    assert _time_dimension_dtype_advisory(field_ir, order_date) is None


def test_time_dimension_dtype_advisory_no_cast_no_advisory() -> None:
    """Body with bare column ref has no cast — AST inference cannot determine dtype."""

    def order_date(table):  # type: ignore[no-untyped-def]
        return table.dt

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.ir import DimensionIR, DimensionKind, SourceLocation
    from marivo.semantic.validator import _time_dimension_dtype_advisory

    field_ir = DimensionIR(
        semantic_id="model.order_date",
        domain="model",
        entity="orders",
        name="order_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="datetime",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date",
        location=SourceLocation(file="test.py", line=1),
    )
    assert _time_dimension_dtype_advisory(field_ir, order_date) is None


# ---------------------------------------------------------------------------
# ibis Table attribute shadowing
# ---------------------------------------------------------------------------


def test_base_ibis_attr_shadow_schema() -> None:
    """Accessing .schema on a param named 'orders' shadows ibis Table.schema."""

    def schema(orders):  # type: ignore[no-untyped-def]
        return orders.schema

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(schema, "base")
    assert exc_info.value.kind == ErrorKind.IBIS_ATTR_SHADOW
    assert "bracket notation" in str(exc_info.value)
    assert 'orders["schema"]' in str(exc_info.value)


def test_base_ibis_attr_shadow_count() -> None:
    """Accessing .count on a param shadows ibis Table.count method."""

    def bad(orders):  # type: ignore[no-untyped-def]
        return orders.count

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad, "base")
    assert exc_info.value.kind == ErrorKind.IBIS_ATTR_SHADOW


def test_base_ibis_attr_shadow_method_call_allowed() -> None:
    """Calling a method like orders.filter(...) is valid ibis, not a column access."""

    def revenue(orders):  # type: ignore[no-untyped-def]
        return orders.filter(orders.status == "active").amount.sum()

    result = validate_metric_body_ast(revenue, "base")
    assert isinstance(result, str)


def test_base_ibis_attr_no_shadow_non_param() -> None:
    """Attribute access on a non-parameter name is not flagged."""

    def metric(table):  # type: ignore[no-untyped-def]
        return other.schema  # noqa: F821

    result = validate_metric_body_ast(metric, "base")
    assert isinstance(result, str)


def test_base_ibis_attr_no_shadow_safe_name() -> None:
    """Accessing a non-shadowing attribute like .amount is fine."""

    def metric(orders):  # type: ignore[no-untyped-def]
        return orders.amount.sum()

    result = validate_metric_body_ast(metric, "base")
    assert isinstance(result, str)
