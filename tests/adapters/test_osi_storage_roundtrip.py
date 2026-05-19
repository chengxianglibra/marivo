"""Phase B gate: OSI storage roundtrip preserves metric and field extension data."""

from __future__ import annotations

import json

from marivo.contracts.generated import DialectExpression, Expression, Field, Metric
from marivo.contracts.semantic_extensions import (
    MarivoFieldExtension,
    MarivoMetricExtension,
)
from marivo.runtime.semantic.osi_storage import (
    _storage_to_field,
    _storage_to_metric,
    build_custom_extensions,
    field_to_storage,
    metric_to_storage,
)


def test_metric_roundtrip_with_additive_dimensions() -> None:
    """metric_to_storage -> _storage_to_metric preserves additive_dimensions."""
    ext = MarivoMetricExtension(additive_dimensions=["region", "channel"])
    metric = Metric(
        name="revenue",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(amount)")]
        ),
        custom_extensions=build_custom_extensions(ext),
    )

    storage = metric_to_storage(metric, model_id=1)
    assert storage["additive_dimensions"] is not None
    assert json.loads(storage["additive_dimensions"]) == ["region", "channel"]

    reconstructed = _storage_to_metric(storage)
    assert reconstructed["name"] == "revenue"

    marivo_ext = None
    for ext_data in reconstructed.get("custom_extensions", []):
        if ext_data.get("vendor_name") == "MARIVO":
            marivo_ext = ext_data["data"]
            break

    assert marivo_ext is not None
    assert marivo_ext["additive_dimensions"] == ["region", "channel"]


def test_metric_roundtrip_preserves_aggregation_semantics_as_string() -> None:
    """Literal-backed aggregation semantics remains a plain string on storage/export paths."""
    ext = MarivoMetricExtension(
        additive_dimensions=["region"],
        aggregation_semantics="weighted_average",
    )
    metric = Metric(
        name="aov",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(revenue)/COUNT(*)")]
        ),
        custom_extensions=build_custom_extensions(ext),
    )

    extension_dump = metric.custom_extensions[0].model_dump(mode="json")
    assert extension_dump["data"]["aggregation_semantics"] == "weighted_average"

    storage = metric_to_storage(metric, model_id=1)
    assert storage["aggregation_semantics"] == "weighted_average"

    reconstructed = _storage_to_metric(storage)
    marivo_ext = reconstructed["custom_extensions"][0]["data"]
    assert marivo_ext["aggregation_semantics"] == "weighted_average"
    assert isinstance(marivo_ext["aggregation_semantics"], str)


def test_metric_roundtrip_preserves_all_additive_dimensions_sentinel() -> None:
    """Storage preserves ["__all"] as policy instead of expanding dimensions."""
    ext = MarivoMetricExtension(additive_dimensions=["__all"])
    metric = Metric(
        name="revenue",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(amount)")]
        ),
        custom_extensions=build_custom_extensions(ext),
    )

    storage = metric_to_storage(metric, model_id=1)
    assert storage["additive_dimensions"] is not None
    assert json.loads(storage["additive_dimensions"]) == ["__all"]

    reconstructed = _storage_to_metric(storage)
    marivo_ext = reconstructed["custom_extensions"][0]["data"]
    assert marivo_ext["additive_dimensions"] == ["__all"]


def test_metric_roundtrip_without_extensions() -> None:
    """Metric with no MARIVO extension roundtrips cleanly."""
    metric = Metric(
        name="count_all",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="COUNT(*)")]
        ),
    )

    storage = metric_to_storage(metric, model_id=1)
    assert storage.get("additive_dimensions") is None

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
