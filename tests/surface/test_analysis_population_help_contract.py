"""Population Help describes closed values and authoring symbols accurately."""

import pytest
from ibis.common.deferred import Deferred

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic._expression_values import MetricExpression, RowExpression


def test_population_count_help_describes_closed_metric(capsys: pytest.CaptureFixture[str]) -> None:
    population = mv.coordinates(by=(), keys=((),))
    assert isinstance(population.count(), MetricExpression)

    marivo.help("analysis.population.count")
    output = capsys.readouterr().out
    assert "Returns: NumericMetric" in output
    assert "Returns: Deferred" not in output


def test_in_population_help_distinguishes_closed_and_deferred(
    capsys: pytest.CaptureFixture[str],
) -> None:
    population = mv.coordinates(by=(), keys=((),))
    symbolic = mv.coordinates(by=(ms.ref.dimension("sales.orders.region"),), keys=(("east",),))
    assert isinstance(mv.in_population(population), RowExpression)
    assert isinstance(symbolic, Deferred)
    assert isinstance(mv.in_population(symbolic), Deferred)

    marivo.help("analysis.in_population")
    output = capsys.readouterr().out
    assert "Returns: RowPredicate | Deferred" in output
    assert "Closed inputs return the concrete value" in output
    assert "native lookup symbols return a Deferred parameter" in output
