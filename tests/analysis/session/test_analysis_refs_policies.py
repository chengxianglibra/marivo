"""Semantic refs retain their canonical public owner."""

import marivo.semantic as ms
from tests.support.refs import as_ref


def test_semantic_refs_stay_on_the_semantic_surface() -> None:
    metric = as_ref(ms.ref.metric("sales.revenue"))
    dimension = as_ref(ms.ref.dimension("sales.orders.region"))
    assert metric is not None and metric.path == "sales.revenue"
    assert dimension is not None and dimension.path == "sales.orders.region"
