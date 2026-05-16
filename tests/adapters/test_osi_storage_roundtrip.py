"""Phase B gate: OSI storage roundtrip preserves metric extension data."""

from __future__ import annotations

import json

from marivo.contracts.generated import DialectExpression, Expression, Metric
from marivo.contracts.semantic_extensions import MarivoMetricExtension
from marivo.runtime.semantic.osi_storage import (
    _storage_to_metric,
    build_custom_extensions,
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
