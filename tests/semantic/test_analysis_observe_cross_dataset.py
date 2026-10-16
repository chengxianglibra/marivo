"""Ontology reverse indexes include every consumed Metric contribution root."""

from collections.abc import Callable

import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject


def test_ontology_reverse_index_includes_every_effective_entity(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/metrics.py": """\
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("warehouse"), source=md.table("refunds"))
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
refund_ref = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all())
finalized = ms.finalize()
amount = finalized.resolve(amount_ref)
refunded = finalized.resolve(refund_ref)
@ms.metric()
def net_revenue():
    return amount.sum() - refunded.sum()
""",
        }
    )
    catalog = SemanticCatalog(project)
    for endpoint in (ms.ref.entity("sales.orders"), ms.ref.entity("sales.refunds")):
        assert catalog._ontology_metrics_for_endpoint(endpoint) == (
            ms.ref.metric("sales.net_revenue"),
        )
