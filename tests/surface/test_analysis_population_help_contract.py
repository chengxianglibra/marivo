"""Population values stay closed or deferred according to their inputs."""

from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic._expression_values import MetricExpression, RowExpression


def test_population_count_help_describes_closed_metric() -> None:
    population = mv.coordinates(by=(), keys=((),))
    assert isinstance(population.count(), MetricExpression)


def test_in_population_help_distinguishes_closed_and_deferred() -> None:
    population = mv.coordinates(by=(), keys=((),))
    symbolic = mv.coordinates(by=(ms.ref.dimension("sales.orders.region"),), keys=(("east",),))
    assert isinstance(mv.in_population(population), RowExpression)
    assert isinstance(symbolic, Deferred)
    assert isinstance(mv.in_population(symbolic), Deferred)
