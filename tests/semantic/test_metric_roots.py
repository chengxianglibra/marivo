"""Metric computation roots, unit facts, and explicit entity identity boundaries."""

from __future__ import annotations

import marivo.semantic as ms
from marivo._definition.expressions import DeclaredMetricRef
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.expressions import entity_exact
from tests.shared_fixtures import resolve_test_symbol as resolve

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", owner="Analytics", default=True)
"""

_OBJECTS_PY = """import marivo.datasource as md
import marivo.semantic as ms

orders_ref = ms.entity(
    name="orders",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"),
    primary_key=["order_id"],
)
users_ref = ms.entity(
    name="users",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("users"),
    primary_key=["user_id"],
)
paid_at_ref = ms.time_dimension_column(
    name="paid_at",
    entity=orders_ref,
    column="paid_at",
    parse=ms.timestamp(timezone="UTC"),
    is_default=True,
)
amount_ref = ms.measure_column(
    name="amount",
    entity=orders_ref,
    column="amount",
    additivity=ms.additive_all(),
    unit="CNY",
)

orders = orders_ref
users = users_ref
paid_at = paid_at_ref
amount = amount_ref


@ms.metric()
def revenue():
    return amount.sum(time=paid_at)


@ms.metric()
def order_count():
    return orders.count(time=paid_at)


@ms.metric()
def user_count():
    return users.count()


@ms.metric()
def revenue_per_user():
    return revenue / user_count


@ms.metric()
def user_revenue_share():
    return user_count / revenue
"""


def _catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": _OBJECTS_PY}
    )
    return SemanticCatalog(project)


def test_versioned_entity_load_rejects_identity_version_overlap_without_source_access(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "daily = ms.entity(name='daily', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('daily'), primary_key=['id', 'dt'], "
                "versioning=ms.snapshot(partition_field="
                "ms.ref.time_dimension('sales.daily.dt'), timezone='UTC', "
                "))\n"
                "dt = ms.time_dimension_column(name='dt', entity=daily, column='dt', "
                "parse=ms.strptime('%Y%m%d'))\n"
            ),
        },
        load=False,
    )

    result = project.load()
    assert result.status == "errored"
    identity_errors = [error for error in result.errors if error.semantic_refs == ("sales.daily",)]
    assert len(identity_errors) == 1
    assert identity_errors[0].kind == "identity_version_overlap"
    assert "dt" in (identity_errors[0].received or "")


def test_single_entity_metric_roots_follow_its_source_reductions(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    revenue = catalog.get("metric:sales.revenue")
    order_count = resolve(catalog, ms.ref.metric("sales.order_count"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))

    assert isinstance(revenue, ms.Metric)
    revenue = consume_metric(revenue)
    order_count = consume_metric(order_count)
    assert isinstance(revenue.node, DeclaredMetricRef)
    assert revenue.node.ref.ref == ref_factory.metric("sales.revenue")
    assert revenue.node.ref.version.startswith("sha256:")
    assert revenue.roots == (entity_exact(orders),)
    assert revenue.unit == "CNY"
    assert consume_metric(resolve(catalog, ref_factory.metric("sales.revenue"))) == revenue
    assert order_count.roots == (entity_exact(orders),)
    assert order_count.unit == "1"


def test_multi_root_metric_covers_each_contribution_root_in_expression_order(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    users = resolve(catalog, ms.ref.entity("sales.users"))

    per_user = consume_metric(catalog.get("metric:sales.revenue_per_user"))
    share = consume_metric(catalog.get("metric:sales.user_revenue_share"))

    assert per_user.roots == (entity_exact(orders), entity_exact(users))
    assert share.roots == (entity_exact(users), entity_exact(orders))
    assert per_user.unit == "CNY"
    assert share.unit == "1/CNY"


def test_metric_reads_are_silent_with_bounded_repr_and_show_prints_contract(
    semantic_project_factory, capsys
) -> None:
    catalog = _catalog(semantic_project_factory)
    revenue = catalog.get("metric:sales.revenue")
    contract = catalog.get("metric:sales.revenue")
    rendered = repr(contract)

    assert rendered.startswith("Metric metric:sales.revenue")
    assert ".show()" in rendered
    assert "\n" not in rendered and len(rendered) < 120
    assert capsys.readouterr().out == ""

    contract.show()

    shown = capsys.readouterr().out
    assert "Metric metric:sales.revenue" in shown
    assert "formula:" in shown
    assert "unit: CNY" in shown
    assert capsys.readouterr().out == ""


def test_snapshot_versioning_stays_a_valued_representation_on_the_entity(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "user_profile_daily = ms.entity(\n"
                "    name='user_profile_daily',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.table('user_profile_daily', columns={'user_id': 'user_id', 'dt': 'dt'}),\n"
                "    primary_key=['user_id'],\n"
                "    versioning=ms.snapshot(\n"
                "        partition_field=ms.ref.time_dimension('sales.user_profile_daily.dt'),\n"
                "        timezone='Asia/Shanghai',\n"
                "    ),\n"
                ")\n"
                "dt = ms.time_dimension_column(name='dt', entity=user_profile_daily, column='dt', parse=ms.strptime('%Y%m%d'))\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    entity = catalog.get("entity:sales.user_profile_daily")
    stored = catalog._state.registry.entities["sales.user_profile_daily"]
    versioning = stored.versioning

    assert entity.primary_key == ("user_id",)
    assert versioning is not None
    assert versioning.kind == "snapshot"
    assert versioning.partition_field == "sales.user_profile_daily.dt"
    assert not hasattr(versioning, "grain")
    assert versioning.timezone == "Asia/Shanghai"
    assert not hasattr(versioning, "format")
    assert entity.definition.versioning == versioning
