"""Published Metric names remain separate from their computation identity."""

from __future__ import annotations

from collections.abc import Callable

import marivo.semantic as ms
from marivo._definition.expressions import DeclaredMetricRef
from marivo._definition.graph import computation_fingerprint
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject


def test_published_metrics_share_computation_without_sharing_business_identity(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/metrics.py": """\
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
finalized = ms.finalize()
amount = finalized.resolve(amount_ref)
@ms.metric()
def first():
    return amount.sum()
@ms.metric()
def second():
    return amount.sum()
""",
        }
    )
    catalog = SemanticCatalog(project)
    first = catalog._state.registry.declared_metrics["sales.first"]
    second = catalog._state.registry.declared_metrics["sales.second"]
    assert computation_fingerprint(first.node) == computation_fingerprint(second.node)
    published_first = catalog.metrics.get("sales.first")
    published_second = catalog.metrics.get("sales.second")
    assert isinstance(published_first.node, DeclaredMetricRef)
    assert isinstance(published_second.node, DeclaredMetricRef)
    assert published_first.node.ref.ref == ms.ref.metric("sales.first")
    assert published_second.node.ref.ref == ms.ref.metric("sales.second")
    assert published_first.node.ref != published_second.node.ref
    assert published_first.unit == published_second.unit == "CNY"
