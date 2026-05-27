"""Tests for marivo.semantic_py.validator — AST whitelist validation.

Tests cover:
- Base metric: single return allowed
- Base metric: multiple statements -> error
- Base metric: import -> error
- Base metric: assignment -> error
- Base metric: for/while/with/try -> error
- Base metric: .sql/.raw_sql -> SQL_ESCAPE_HATCH
- Base metric: calling metric ref -> error
- Base metric: valid arithmetic expression -> ok
- Base metric: method calls on dataset arg -> ok
- Base metric: field ref calls -> ok
- Base metric: lambda expression -> error
- Derived metric: ms.component() allowed
- Derived metric: non-component call -> error
- Derived metric: attribute access -> error
- Derived metric: string literal -> error
- Derived metric: comparison -> error
- Derived metric: boolean op -> error
- Derived metric: conditional expression -> error
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from marivo.semantic_py.errors import ErrorKind, SemanticLoadError
from marivo.semantic_py.validator import validate_metric_body_ast

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

    from marivo.semantic_py.validator import _BaseMetricASTValidator

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

    from marivo.semantic_py.validator import _BaseMetricASTValidator

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

    from marivo.semantic_py.validator import _BaseMetricASTValidator

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

    from marivo.semantic_py.validator import _BaseMetricASTValidator

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

    from marivo.semantic_py.validator import _BaseMetricASTValidator

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


def test_base_lambda_error() -> None:
    """Lambda expressions in metric body are forbidden."""

    code = textwrap.dedent("""\
        def bad_metric(table):
            f = lambda x: x.amount
            return f(table).sum()
    """)
    tree = ast.parse(code)
    func_node = tree.body[0]

    from marivo.semantic_py.validator import _BaseMetricASTValidator

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

    from marivo.semantic_py.validator import _BaseMetricASTValidator

    validator = _BaseMetricASTValidator("bad_metric")
    validator.visit(func_node)
    has_multi_return = any(
        e.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN for e in validator.errors
    )
    assert has_multi_return


# ---------------------------------------------------------------------------
# Derived metric: valid bodies
# ---------------------------------------------------------------------------


def test_derived_component_only() -> None:
    """A derived metric body with only ms.component() is valid."""

    def conversion_rate():  # type: ignore[no-untyped-def]
        return ms.component("numerator") / ms.component("denominator")  # noqa: F821

    result = validate_metric_body_ast(conversion_rate, "derived")
    assert isinstance(result, str)
    assert len(result) > 0


def test_derived_component_with_arithmetic() -> None:
    """Arithmetic on ms.component() results is valid."""

    def margin():  # type: ignore[no-untyped-def]
        return ms.component("gross") - ms.component("costs")  # noqa: F821

    result = validate_metric_body_ast(margin, "derived")
    assert isinstance(result, str)


def test_derived_component_with_numeric_literal() -> None:
    """Numeric literals in derived metrics are valid."""

    def scaled():  # type: ignore[no-untyped-def]
        return ms.component("revenue") * 100  # noqa: F821

    result = validate_metric_body_ast(scaled, "derived")
    assert isinstance(result, str)


def test_derived_unary_minus() -> None:
    """Unary minus in derived metrics is valid."""

    def negated():  # type: ignore[no-untyped-def]
        return -ms.component("value")  # noqa: F821

    result = validate_metric_body_ast(negated, "derived")
    assert isinstance(result, str)


def test_derived_parenthesized() -> None:
    """Parenthesized expressions are valid."""

    def combined():  # type: ignore[no-untyped-def]
        return (ms.component("a") + ms.component("b")) / ms.component("c")  # noqa: F821

    result = validate_metric_body_ast(combined, "derived")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Derived metric: forbidden bodies
# ---------------------------------------------------------------------------


def test_derived_non_component_call() -> None:
    """Any function call other than ms.component() is forbidden."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return abs(ms.component("value"))  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_attribute_access() -> None:
    """Attribute access in derived metrics is forbidden."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("value").something  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_string_literal() -> None:
    """String literals (outside ms.component() arg) are forbidden."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("value") + " dollars"  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_comparison() -> None:
    """Comparison operations are forbidden in derived metrics."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("a") > 0  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_boolean_op() -> None:
    """Boolean operations are forbidden in derived metrics."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("a") and ms.component("b")  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_conditional_expression() -> None:
    """Conditional expressions are forbidden in derived metrics."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("a") if True else 0  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_subscript() -> None:
    """Subscript access is forbidden in derived metrics."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("values")[0]  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_modulo_operator() -> None:
    """Modulo operator is not in the allowed set."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("a") % 2  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_not_operator() -> None:
    """Unary 'not' is not in the allowed set."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return not ms.component("a")  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_component_wrong_arg_count() -> None:
    """ms.component() with wrong number of arguments is forbidden."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component("a", "b")  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_component_non_string_arg() -> None:
    """ms.component() with non-string argument is forbidden."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component(42)  # type: ignore[no-untyped-def]  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_component_keyword_arg() -> None:
    """ms.component() with keyword argument is forbidden."""

    def bad_derived():  # type: ignore[no-untyped-def]
        return ms.component(name="a")  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY


def test_derived_no_return() -> None:
    """Derived metric without return statement is forbidden."""

    def bad_derived():  # type: ignore[no-untyped-def]
        ms.component("a")  # noqa: F821

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_metric_body_ast(bad_derived, "derived")
    assert exc_info.value.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN


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
