"""Structured construction errors for semantic Row/Metric expressions (R1.2)."""

from __future__ import annotations

import pytest
from ibis.common.deferred import const

from marivo._definition.deferred import inspect_deferred
from marivo._definition.types import DefinitionGraphError
from marivo.semantic.errors import ErrorKind, SemanticError, SemanticExpressionError


def test_expression_error_carries_expected_received_and_reauthor_repair() -> None:
    error = SemanticExpressionError(
        kind=ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
        message="amount + amount_usd adds incompatible units.",
        expected="operands with one unit",
        received="CNY + USD",
        action="Convert one operand explicitly, then combine the expressions again.",
        refs=("sales.orders.amount", "sales.orders.amount_usd"),
    )

    assert isinstance(error, SemanticError)
    assert error.kind == "incommensurable_linear_units"
    assert error.expected == "operands with one unit"
    assert error.received == "CNY + USD"
    assert error.repair is not None
    assert error.repair.kind == "reauthor"
    rendered = str(error)
    assert rendered.startswith("[incommensurable_linear_units] amount + amount_usd")
    assert "  refs: sales.orders.amount, sales.orders.amount_usd" in rendered
    assert "  expected: operands with one unit" in rendered
    assert "  received: CNY + USD" in rendered
    assert "Repair:\n  Convert one operand explicitly" in rendered
    assert "Help: marivo.help('semantic.authoring')" in rendered


def test_definition_failure_translates_with_its_location() -> None:
    with pytest.raises(DefinitionGraphError) as raised:
        inspect_deferred(const(float("nan")))

    error = SemanticExpressionError.from_definition(raised.value, operation="row constant")

    assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert error.received == str(raised.value)
    assert error.details["definition_kind"] == "invalid_value"
    assert error.repair is not None
    assert "row constant" in error.repair.action


def test_translated_error_names_the_definition_path() -> None:
    failure = DefinitionGraphError(
        "unsupported rule version",
        kind="unknown_rule_version",
        path=("MetricExpression.operand[0]",),
    )

    error = SemanticExpressionError.from_definition(failure, operation="metric divide")

    assert error.location_label == "MetricExpression.operand[0]"
    assert error.details == {
        "definition_kind": "unknown_rule_version",
        "definition_path": ["MetricExpression.operand[0]"],
    }
    assert "  at: MetricExpression.operand[0]" in str(error)
