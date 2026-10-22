"""Normalized, non-executing expression display contract."""

import ast

import pytest

from marivo.refs import ref as ref_factory
from marivo.semantic._definition_display import describe_display
from marivo.semantic._definition_expression import describe_expression
from marivo.semantic._definition_expression_types import _ExpressionDisplay, _Literal


def display(expression: str) -> _ExpressionDisplay:
    function = ast.parse("def value(rows):\n    return " + expression).body[0]
    assert isinstance(function, ast.FunctionDef)
    node = describe_expression(
        function, entities={"rows": ref_factory.entity("sales.rows")}, bindings={}
    )
    assert node.display is not None
    return node.display


def test_cast_display_and_alias_identity() -> None:
    result = display('rows.spend_cny.cast("float64")')
    assert result.text == "t1['spend_cny'].cast('float64')"
    assert result.bindings == (("t1", ref_factory.entity("sales.rows")),)


def test_describe_display_matches_expression_display_without_mutation() -> None:
    function = ast.parse("def value(rows):\n    return rows.amount * 2").body[0]
    assert isinstance(function, ast.FunctionDef)
    returned = function.body[0]
    assert isinstance(returned, ast.Return) and returned.value is not None
    original = ast.dump(function, include_attributes=True)
    direct = describe_display(
        returned.value, entities={"rows": ref_factory.entity("sales.rows")}, bindings={}
    )
    assert direct == display("rows.amount * 2")
    assert direct.text == "t1['amount'] * 2"
    assert ast.dump(function, include_attributes=True) == original


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (
            "rows.amount + rows.other * rows.amount",
            "t1['amount'] + t1['other'] * t1['amount']",
        ),
        (
            "~((rows.amount >= 12345) & (rows.other != 56789))",
            "~((t1['amount'] >= 12345) & (t1['other'] != 56789))",
        ),
        (
            '(rows.amount > 0).ifelse("PRIVATE_TOKEN", None)',
            "(t1['amount'] > 0).ifelse('PRIVATE_TOKEN', None)",
        ),
        ("-rows.amount / +rows.other", "-t1['amount'] / +t1['other']"),
    ],
)
def test_precedence_and_valid_python_syntax(expression: str, expected: str) -> None:
    result = display(expression)
    actual_tree = ast.parse(result.text, mode="eval")
    expected_tree = ast.parse(expected, mode="eval")
    assert ast.dump(actual_tree) == ast.dump(expected_tree)
    assert result.bindings == (("t1", ref_factory.entity("sales.rows")),)


def test_escaped_column_name_stays_a_string() -> None:
    result = display('rows["<script>bad</script>\\"quote"]')
    tree = ast.parse(result.text, mode="eval")
    assert isinstance(tree.body, ast.Subscript)
    assert isinstance(tree.body.slice, ast.Constant)
    assert tree.body.slice.value == '<script>bad</script>"quote'


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("rows.order_id.count()", "t1['order_id'].count()"),
        ('rows.is_cancelled.cast("int64").sum()', "t1['is_cancelled'].cast('int64').sum()"),
        ("rows.count()", "t1.count()"),
        ("rows.amount.sum(where=rows.paid)", "t1['amount'].sum(where=t1['paid'])"),
        ("rows.amount.fill_null(198765).mean()", "t1['amount'].fill_null(198765).mean()"),
    ],
)
def test_display_does_not_require_structural_support(expression: str, expected: str) -> None:
    function = ast.parse("def value(rows):\n    return " + expression).body[0]
    assert isinstance(function, ast.FunctionDef)
    original = ast.dump(function, include_attributes=True)
    node = describe_expression(
        function, entities={"rows": ref_factory.entity("sales.rows")}, bindings={}
    )
    assert node.status == "unsupported"
    assert node.display is not None and node.display.text == expected
    assert ast.dump(function, include_attributes=True) == original


def test_display_text_limit_preserves_supported_structure() -> None:
    terms = [f"rows.column_{index}_{'x' * 125}" for index in range(128)]
    while len(terms) > 1:
        terms = [f"({terms[index]} + {terms[index + 1]})" for index in range(0, len(terms), 2)]
    function = ast.parse("def value(rows):\n    return " + terms[0]).body[0]
    assert isinstance(function, ast.FunctionDef)
    original = ast.dump(function, include_attributes=True)
    node = describe_expression(
        function, entities={"rows": ref_factory.entity("sales.rows")}, bindings={}
    )
    # The balanced tree fits the structural budget, but its display exceeds 16 KiB.
    assert node.status == "supported"
    assert node.expression.kind == "binary"
    assert node.display is None
    assert ast.dump(function, include_attributes=True) == original


def test_structural_depth_limit_preserves_available_display() -> None:
    function = ast.parse("def value(rows):\n    return " + "+" * 40 + "rows.amount").body[0]
    assert isinstance(function, ast.FunctionDef)
    node = describe_expression(
        function, entities={"rows": ref_factory.entity("sales.rows")}, bindings={}
    )
    assert node.status == "unsupported"
    assert node.reason == "limit_exceeded"
    assert node.display is not None
    assert node.display.text == "+" * 40 + "t1['amount']"


@pytest.mark.parametrize(
    "literal", ['"paid"', '"quote\\"line\\n"', "100", "0", "1.25", "True", "False", "None"]
)
def test_literal_values_preserve_scalar_types(literal: str) -> None:
    function = ast.parse("def value(rows):\n    return " + literal).body[0]
    assert isinstance(function, ast.FunctionDef)
    node = describe_expression(function, entities={}, bindings={})
    assert node.status == "supported"
    value = node.expression
    expected = ast.literal_eval(literal)
    assert isinstance(value, _Literal)
    assert value.kind == "literal"
    assert value.value == expected
    assert type(value.value) is type(expected)
    assert value.value_type == ("none" if expected is None else type(expected).__name__)
    assert node.display is not None
    assert ast.literal_eval(node.display.text) == expected
