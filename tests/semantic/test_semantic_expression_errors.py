"""Structured construction errors for semantic Row/Metric expressions (R1.2)."""

from __future__ import annotations

import operator
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from ibis.common.deferred import const

import marivo.analysis as mv
from marivo._definition.deferred import inspect_deferred
from marivo._definition.types import DefinitionGraphError
from marivo.semantic.errors import ErrorKind, SemanticError, SemanticExpressionError
from tests.shared_fixtures import write_minimal_catalog_project


@pytest.mark.parametrize("operation", [operator.and_, operator.or_], ids=["and", "or"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reflected"])
@pytest.mark.parametrize("stage", ["row", "metric"])
def test_boolean_composition_rejects_invalid_operands_at_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: Callable[[object, object], object],
    reverse: bool,
    stage: Literal["row", "metric"],
) -> None:
    write_minimal_catalog_project(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("boolean-capture", timezone="UTC")
    status = session.catalog.get("dimension:sales.orders.status")
    revenue = session.catalog.get("metric:sales.revenue")
    assert status.kind == "dimension" and revenue.kind == "metric"
    row_predicate = status == "paid"
    metric_predicate = revenue > 0
    predicate = row_predicate if stage == "row" else metric_predicate
    other_stage = metric_predicate if stage == "row" else row_predicate
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")

    for invalid in (scope, 1, status, revenue, other_stage):
        left, right = (invalid, predicate) if reverse else (predicate, invalid)
        with pytest.raises(SemanticExpressionError) as raised:
            operation(left, right)
        assert raised.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
        assert raised.value.expected and raised.value.received
        assert raised.value.repair is not None
        if invalid is scope:
            assert raised.value.received == "TimeScope"
            assert raised.value.repair.help_target is not None
            assert raised.value.repair.help_target.canonical_id == "predicate"


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


def test_definition_failure_translates_with_its_location() -> None:
    with pytest.raises(DefinitionGraphError) as raised:
        inspect_deferred(const(float("nan")))

    error = SemanticExpressionError.from_definition(raised.value, operation="row constant")

    assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert error.received == str(raised.value)
    assert error.details["definition_kind"] == "invalid_value"
    assert error.repair is not None


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
