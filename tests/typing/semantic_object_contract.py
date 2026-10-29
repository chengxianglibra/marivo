"""Positive concrete typing contract for named semantic objects and stage roles."""

from typing import Literal

from ibis.common.deferred import Deferred
from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms


def object_types(catalog: ms.SemanticCatalog) -> None:
    assert_type(catalog.get("metric:sales.revenue"), ms.SemanticObject)
    assert_type(catalog.search(kind="domain"), ms.CatalogPage[ms.Domain])
    assert_type(catalog.search(kind="datasource"), ms.CatalogPage[ms.Datasource])
    assert_type(catalog.search(kind="entity"), ms.CatalogPage[ms.Entity])
    assert_type(catalog.search(kind="dimension"), ms.CatalogPage[ms.Dimension])
    assert_type(catalog.search(kind="time_dimension"), ms.CatalogPage[ms.TimeDimension])
    assert_type(catalog.search(kind="measure"), ms.CatalogPage[ms.Measure])
    assert_type(catalog.search(kind="metric"), ms.CatalogPage[ms.Metric])
    assert_type(catalog.search(kind="relationship"), ms.CatalogPage[ms.Relationship])
    assert_type(catalog.search(kind="period_calendar"), ms.CatalogPage[ms.PeriodCalendar])
    assert_type(catalog.search(kind="temporal_set"), ms.CatalogPage[ms.TemporalSet])
    value = catalog.get("metric:sales.revenue")
    if value.kind == "metric":
        assert_type(value, ms.Metric)
    elif value.kind == "measure":
        assert_type(value, ms.Measure)
    elif value.kind == "entity":
        assert_type(value, ms.Entity)
    elif value.kind == "dimension":
        assert_type(value, ms.Dimension)
    elif value.kind == "time_dimension":
        assert_type(value, ms.TimeDimension)
    elif value.kind == "relationship":
        assert_type(value, ms.Relationship)
    elif value.kind == "period_calendar":
        assert_type(value, ms.PeriodCalendar)
    elif value.kind == "temporal_set":
        assert_type(value, ms.TemporalSet)
    elif value.kind == "datasource":
        assert_type(value, ms.Datasource)
    elif value.kind == "domain":
        assert_type(value, ms.Domain)


def expression_roles(
    session: mv.Session,
    amount: ms.Measure,
    cost: ms.Measure,
    status: ms.Dimension,
    paid_at: ms.TimeDimension,
    revenue: ms.Metric,
    orders: ms.Entity,
) -> None:
    assert_type(amount - cost, ms.RowExpression[Literal["numeric"]])
    assert_type(status == "paid", ms.RowExpression[Literal["boolean"]])
    assert_type(amount.sum(time=paid_at), ms.MetricExpression[Literal["numeric"]])
    assert_type(orders.count(time=paid_at), ms.MetricExpression[Literal["numeric"]])
    assert_type(revenue / 2, ms.MetricExpression[Literal["numeric"]])
    assert_type(revenue > 0, ms.MetricExpression[Literal["boolean"]])
    row_condition = status == "paid"
    metric_condition = revenue > 0
    assert_type(row_condition.ifelse(amount, cost), ms.RowExpression[Literal["numeric"]])
    assert_type(row_condition.ifelse(True, False), ms.RowExpression[Literal["boolean"]])
    assert_type(row_condition.ifelse("paid", "other"), ms.RowExpression[Literal["dimension"]])
    assert_type(row_condition.ifelse(paid_at, paid_at), ms.RowExpression[Literal["time"]])
    assert_type(metric_condition.ifelse(revenue, 0), ms.MetricExpression[Literal["numeric"]])
    assert_type(metric_condition.ifelse(True, False), ms.MetricExpression[Literal["boolean"]])
    assert_type(ms.rescale(amount, divisor=100, unit="USD"), ms.RowExpression[Literal["numeric"]])
    assert_type(
        ms.rescale(revenue, divisor=100, unit="USD"), ms.MetricExpression[Literal["numeric"]]
    )
    assert_type(session.observe([revenue]), mv.AnalysisFrame)
    assert_type(session.observe({"margin": (amount - cost).sum(time=paid_at)}), mv.AnalysisFrame)
    check: ms.SourceCheck = ms.source_check.not_null(status)
    assert check is not None
    assert_type(orders.default_time, ms.TimeDimension | None)


def declaration_stage(amount: ms.Measure, authoring: Deferred) -> None:
    @ms.measure()
    def closed_rows() -> ms.RowExpression[Literal["numeric"]]:
        return amount

    @ms.measure()
    def forward_rows() -> Deferred:
        return authoring

    @ms.metric()
    def closed_metric() -> ms.MetricExpression[Literal["numeric"]]:
        return amount.sum()

    @ms.metric()
    def forward_metric() -> Deferred:
        return authoring

    assert_type(closed_rows, ms.RowExpression[Literal["numeric"]])
    assert_type(forward_rows, ms.RowExpression[Literal["numeric"]])
    assert_type(closed_metric, ms.MetricExpression[Literal["numeric"]])
    assert_type(forward_metric, ms.MetricExpression[Literal["numeric"]])
