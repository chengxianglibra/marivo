"""Published Metric names remain separate from their computation identity."""

from __future__ import annotations

from collections.abc import Callable

from marivo._definition.expressions import DeclaredMetricRef
from marivo._definition.graph import computation_fingerprint
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject


def test_published_metrics_share_computation_without_sharing_business_identity(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/metrics.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
amount = amount_ref
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
    assert computation_fingerprint(consume_metric(first).node) == computation_fingerprint(
        consume_metric(second).node
    )
    published_first = catalog.get("metric:sales.first")
    published_second = catalog.get("metric:sales.second")
    assert published_first.kind == published_second.kind == "metric"
    first_node = consume_metric(published_first).node
    second_node = consume_metric(published_second).node
    assert isinstance(first_node, DeclaredMetricRef)
    assert isinstance(second_node, DeclaredMetricRef)
    assert first_node.ref.ref == ref_factory.metric("sales.first")
    assert second_node.ref.ref == ref_factory.metric("sales.second")
    assert first_node.ref != second_node.ref
    assert consume_metric(published_first).unit == consume_metric(published_second).unit == "CNY"
