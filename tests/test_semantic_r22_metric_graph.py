"""R2.2 bound Metric graph identity examples."""

from __future__ import annotations

from marivo.semantic.metric_graph_canonical import fingerprint
from marivo.semantic.metric_graph_lowering import lower_catalog_metric, normalize_target_metric
from tests.shared_fixtures import load_inline_semantic


def test_bound_graph_fingerprint_includes_effective_definition_dependencies() -> None:
    source = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
first = ms.aggregate(name="first", measure=amount, agg="sum")
second = ms.aggregate(name="second", measure=amount, agg="sum")
"""
    with load_inline_semantic(source) as loaded:
        assert loaded.registry is not None
        first = lower_catalog_metric(loaded.registry, "test.first")
        second = lower_catalog_metric(loaded.registry, "test.second")
        assert fingerprint(first.graph) == fingerprint(second.graph)
        assert first.bound_graph_fingerprint != second.bound_graph_fingerprint
        assert normalize_target_metric(
            loaded.registry, "test.first", sidecar=loaded.expression_sidecar
        ).components[0].required_state == ("sum", "non_null_count", "row_count")
