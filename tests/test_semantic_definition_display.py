"""Normalized, non-executing expression display contract."""

import ast
import json

import pytest

from marivo import semantic as ms
from marivo.semantic._definition_expression import describe_expression
from marivo.semantic.definition import _expression_display


def display(expression: str) -> dict[str, object]:
    function = ast.parse("def value(rows):\n    return " + expression).body[0]
    assert isinstance(function, ast.FunctionDef)
    node = describe_expression(
        function, entities={"rows": ms.ref.entity("sales.rows")}, bindings={}
    )
    assert node.display is not None
    return dict(_expression_display(node.display))


def test_cast_display_and_alias_identity() -> None:
    result = display('rows.spend_cny.cast("float64")')
    assert result["text"] == "t1['spend_cny'].cast('float64')"
    assert result["bindings"] == [
        {
            "alias": "t1",
            "ref": {"schema": "marivo.semantic_ref/v1", "kind": "entity", "path": "sales.rows"},
        }
    ]
    assert result["form"] == "normalized_ibis"
    assert result["redacted_literals"] is False


@pytest.mark.parametrize(
    "expression",
    [
        "rows.amount + rows.other * rows.amount",
        "~((rows.amount >= 12345) & (rows.other != 56789))",
        '(rows.amount > 0).ifelse("PRIVATE_TOKEN", None)',
        "-rows.amount / +rows.other",
    ],
)
def test_precedence_redaction_and_valid_python_syntax(expression: str) -> None:
    result = display(expression)
    assert isinstance(result["text"], str)
    ast.parse(result["text"], mode="eval")
    encoded = json.dumps(result)
    assert "PRIVATE_TOKEN" not in encoded
    assert "12345" not in encoded and "56789" not in encoded
    assert isinstance(result["bindings"], list)
    assert len(result["bindings"]) == 1


def test_escaped_column_name_stays_a_string() -> None:
    result = display('rows["<script>bad</script>\\"quote"]')
    assert isinstance(result["text"], str)
    ast.parse(result["text"], mode="eval")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("rows.order_id.count()", "t1['order_id'].count()"),
        ('rows.is_cancelled.cast("int64").sum()', "t1['is_cancelled'].cast('int64').sum()"),
        ("rows.count()", "t1.count()"),
        ("rows.amount.sum(where=rows.paid)", "t1['amount'].sum(where=t1['paid'])"),
        ("rows.amount.fill_null(198765).mean()", "t1['amount'].fill_null(REDACTED_INT).mean()"),
    ],
)
def test_display_does_not_require_structural_support(expression: str, expected: str) -> None:
    function = ast.parse("def value(rows):\n    return " + expression).body[0]
    assert isinstance(function, ast.FunctionDef)
    original = ast.dump(function, include_attributes=True)
    node = describe_expression(
        function, entities={"rows": ms.ref.entity("sales.rows")}, bindings={}
    )
    assert node.status == "unsupported"
    assert node.display is not None and node.display.text == expected
    assert ast.dump(function, include_attributes=True) == original
    assert "198765" not in node.display.text


def test_display_text_limit_preserves_supported_structure() -> None:
    terms = [f"rows.column_{index}_{'x' * 125}" for index in range(128)]
    while len(terms) > 1:
        terms = [f"({terms[index]} + {terms[index + 1]})" for index in range(0, len(terms), 2)]
    function = ast.parse("def value(rows):\n    return " + terms[0]).body[0]
    assert isinstance(function, ast.FunctionDef)
    original = ast.dump(function, include_attributes=True)
    node = describe_expression(
        function, entities={"rows": ms.ref.entity("sales.rows")}, bindings={}
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
        function, entities={"rows": ms.ref.entity("sales.rows")}, bindings={}
    )
    assert node.status == "unsupported"
    assert node.reason == "limit_exceeded"
    assert node.display is not None
    assert node.display.text == "+" * 40 + "t1['amount']"
