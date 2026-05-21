"""Phase B gate: OSI storage roundtrip preserves metric and field extension data."""

from __future__ import annotations

import json

from marivo.contracts.generated import DialectExpression, Expression, Field, Metric
from marivo.contracts.semantic_extensions import (
    ExpressionComponent,
    MarivoFieldExtension,
    MarivoMetricExtension,
    MetricComponentRef,
    RatioDecomposition,
    WeightedAverageDecomposition,
)
from marivo.runtime.semantic.osi_storage import (
    _storage_to_field,
    _storage_to_metric,
    build_custom_extensions,
    field_to_storage,
    metric_to_storage,
)


def test_metric_roundtrip_with_component_refs() -> None:
    """metric_to_storage -> _storage_to_metric preserves numerator/denominator/weight."""
    ext = MarivoMetricExtension(
        decomposition_semantics=RatioDecomposition(
            numerator=MetricComponentRef(metric="metric.converted"),
            denominator=MetricComponentRef(metric="metric.total"),
        ),
    )
    metric = Metric(
        name="conversion_rate",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="converted / total")]
        ),
        custom_extensions=build_custom_extensions(ext),
    )

    storage = metric_to_storage(metric, model_id=1)
    assert storage["numerator"] is not None
    assert storage["denominator"] is not None
    assert storage["weight"] is None
    numerator_data = json.loads(storage["numerator"])
    assert numerator_data["metric"] == "metric.converted"
    denominator_data = json.loads(storage["denominator"])
    assert denominator_data["metric"] == "metric.total"

    reconstructed = _storage_to_metric(storage)
    assert reconstructed["name"] == "conversion_rate"

    marivo_ext = None
    for ext_data in reconstructed.get("custom_extensions", []):
        if ext_data.get("vendor_name") == "MARIVO":
            marivo_ext = ext_data["data"]
            break

    assert marivo_ext is not None
    agg = marivo_ext["decomposition_semantics"]
    assert agg["type"] == "ratio"
    assert agg["numerator"]["metric"] == "metric.converted"
    assert agg["denominator"]["metric"] == "metric.total"


def test_metric_roundtrip_preserves_weighted_average() -> None:
    """Weighted average aggregation semantics roundtrips with type discriminator."""
    ext = MarivoMetricExtension(
        decomposition_semantics=WeightedAverageDecomposition(
            numerator=MetricComponentRef(metric="metric.revenue"),
            weight=MetricComponentRef(metric="metric.orders"),
        ),
    )
    metric = Metric(
        name="aov",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(revenue)/COUNT(*)")]
        ),
        custom_extensions=build_custom_extensions(ext),
    )

    extension_dump = metric.custom_extensions[0].model_dump(mode="json")
    agg = extension_dump["data"]["decomposition_semantics"]
    assert agg["type"] == "weighted_average"
    assert agg["numerator"]["metric"] == "metric.revenue"
    assert agg["weight"]["metric"] == "metric.orders"

    storage = metric_to_storage(metric, model_id=1)
    assert storage["decomposition_semantics"] == "weighted_average"

    reconstructed = _storage_to_metric(storage)
    marivo_ext = reconstructed["custom_extensions"][0]["data"]
    agg = marivo_ext["decomposition_semantics"]
    assert agg["type"] == "weighted_average"


def test_metric_roundtrip_with_expression_component() -> None:
    """ExpressionComponent numerator roundtrips through storage."""
    ext = MarivoMetricExtension(
        decomposition_semantics=RatioDecomposition(
            numerator=ExpressionComponent(expression="SUM(price * quantity)"),
            denominator=MetricComponentRef(metric="metric.total"),
        ),
    )
    metric = Metric(
        name="custom_ratio",
        expression=Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="1")]),
        custom_extensions=build_custom_extensions(ext),
    )

    storage = metric_to_storage(metric, model_id=1)
    numerator_data = json.loads(storage["numerator"])
    assert numerator_data["expression"] == "SUM(price * quantity)"
    denominator_data = json.loads(storage["denominator"])
    assert denominator_data["metric"] == "metric.total"

    reconstructed = _storage_to_metric(storage)
    marivo_ext = reconstructed["custom_extensions"][0]["data"]
    agg = marivo_ext["decomposition_semantics"]
    assert agg["type"] == "ratio"
    assert agg["numerator"]["expression"] == "SUM(price * quantity)"
    assert agg["denominator"]["metric"] == "metric.total"


def test_metric_roundtrip_sum_without_component_refs() -> None:
    """sum metric without numerator/denominator/weight roundtrips cleanly."""
    ext = MarivoMetricExtension()
    metric = Metric(
        name="revenue",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(amount)")]
        ),
        custom_extensions=build_custom_extensions(ext),
    )

    storage = metric_to_storage(metric, model_id=1)
    assert storage.get("numerator") is None
    assert storage.get("denominator") is None
    assert storage.get("weight") is None
    assert storage["decomposition_semantics"] == "sum"

    reconstructed = _storage_to_metric(storage)
    assert reconstructed["name"] == "revenue"

    marivo_ext = reconstructed["custom_extensions"][0]["data"]
    agg = marivo_ext["decomposition_semantics"]
    assert agg["type"] == "sum"


def test_metric_roundtrip_without_extensions() -> None:
    """Metric with no MARIVO extension roundtrips cleanly."""
    metric = Metric(
        name="count_all",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="COUNT(*)")]
        ),
    )

    storage = metric_to_storage(metric, model_id=1)
    assert storage.get("numerator") is None
    assert storage.get("denominator") is None
    assert storage.get("weight") is None

    reconstructed = _storage_to_metric(storage)
    assert reconstructed["name"] == "count_all"


def test_field_roundtrip_preserves_required_prefix() -> None:
    """field_to_storage -> _storage_to_field preserves required_prefix."""
    ext = MarivoFieldExtension(
        support_min_granularity="hour",
        data_type="string",
        format="hh",
        required_prefix="log_date",
    )
    field = Field(
        name="log_hour",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="log_hour")]
        ),
        dimension={"is_time": True},
        custom_extensions=build_custom_extensions(ext),
    )

    storage = field_to_storage(field, dataset_id=1, position=1)
    assert storage["required_prefix"] == "log_date"
    assert storage["format"] == "hh"

    reconstructed = _storage_to_field(storage)
    assert reconstructed["name"] == "log_hour"
    assert reconstructed["dimension"] == {"is_time": True}
    marivo_ext = reconstructed["custom_extensions"][0]["data"]
    assert marivo_ext["required_prefix"] == "log_date"
    assert marivo_ext["format"] == "hh"


def test_field_roundtrip_without_required_prefix() -> None:
    """Time field without required_prefix roundtrips with None."""
    ext = MarivoFieldExtension(
        support_min_granularity="day",
        data_type="string",
        format="yyyymmdd",
    )
    field = Field(
        name="log_date",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="log_date")]
        ),
        dimension={"is_time": True},
        custom_extensions=build_custom_extensions(ext),
    )

    storage = field_to_storage(field, dataset_id=1, position=0)
    assert storage["required_prefix"] is None

    reconstructed = _storage_to_field(storage)
    marivo_ext = reconstructed["custom_extensions"][0]["data"]
    assert "required_prefix" not in marivo_ext
