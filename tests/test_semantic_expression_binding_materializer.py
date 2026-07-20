"""Integration coverage for compiled Tier-2 expression binding execution."""

from __future__ import annotations

import ibis

from marivo.refs import Ref
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind
from marivo.semantic.materializer import Materializer
from marivo.semantic.metric_graph_lowering import dependency_digest

_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(
    name="orders",
    datasource=ms.Ref.datasource("warehouse"),
    source=md.table("orders"),
)

@ms.measure(entity=orders, additivity="additive")
def amount(order_rows):
    return order_rows.amount

@ms.measure(entity=orders, additivity="additive")
def net_amount(order_rows):
    return amount(order_rows) * 0.9

@ms.dimension(entity=orders)
def country(order_rows):
    return order_rows.country

@ms.time_dimension(entity=orders, granularity="day", is_default=True)
def ordered_at(order_rows):
    return order_rows.ordered_at

@ms.metric(entities=[orders], additivity="additive")
def revenue(order_rows):
    return net_amount(order_rows).sum()
"""


def test_materializer_executes_nested_field_binding(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/model.py": _MODEL,
        }
    )
    assert project.is_ready(), project.errors()

    connection = ibis.duckdb.connect(":memory:")
    connection.con.execute("CREATE TABLE orders (amount DOUBLE, country VARCHAR, ordered_at DATE)")
    connection.con.execute(
        "INSERT INTO orders VALUES "
        "(10.0, 'US', DATE '2026-01-01'), "
        "(20.0, 'CA', DATE '2026-01-02'), "
        "(30.0, 'US', DATE '2026-01-03')"
    )
    materializer = Materializer(project, lambda _datasource: connection)

    assert connection.execute(materializer.metric("sales.revenue")) == 54.0
    assert connection.execute(materializer.measure("sales.orders.net_amount")).tolist() == [
        9.0,
        18.0,
        27.0,
    ]
    assert connection.execute(materializer.dimension("sales.orders.country")).tolist() == [
        "US",
        "CA",
        "US",
    ]
    assert len(connection.execute(materializer.dimension("sales.orders.ordered_at"))) == 3

    catalog = SemanticCatalog(project)
    net_details = catalog.require(Ref.measure("sales.orders.net_amount")).details()
    metric_details = catalog.require(Ref.metric("sales.revenue")).details()
    assert Ref.measure("sales.orders.amount") in net_details.parents
    assert Ref.measure("sales.orders.net_amount") in metric_details.parents
    assert "callable" not in repr(net_details)
    assert "callable" not in net_details.render()


def test_loader_rejects_field_bound_to_wrong_positional_entity(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/model.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.Ref.datasource('warehouse'), source=md.table('orders'))\n"
                "users = ms.entity(name='users', datasource=ms.Ref.datasource('warehouse'), source=md.table('users'))\n"
                "@ms.measure(entity=orders, additivity='additive')\n"
                "def amount(order_rows):\n"
                "    return order_rows.amount\n"
                "@ms.metric(entities=[users], additivity='additive')\n"
                "def revenue(user_rows):\n"
                "    return amount(user_rows).sum()\n"
            ),
        }
    )
    assert not project.is_ready()
    error = next(
        error for error in project.errors() if error.kind == ErrorKind.BINDING_ENTITY_MISMATCH
    )
    assert error.expected == Ref.entity("sales.users").key
    assert error.received == Ref.entity("sales.orders").key
    assert "direct parameter" in str(error)


def test_binding_identity_ignores_names_but_tracks_ref_and_definition_changes(
    semantic_project_factory,
) -> None:
    def load(metric_body: str, *, amount_expression: str = "order_rows.amount"):
        return semantic_project_factory(
            {
                "datasources/warehouse.py": (
                    "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
                ),
                "sales/_domain.py": (
                    "import marivo.semantic as ms\n"
                    "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
                ),
                "sales/model.py": (
                    "import marivo.datasource as md\n"
                    "import marivo.semantic as ms\n"
                    "orders = ms.entity(name='orders', datasource=ms.Ref.datasource('warehouse'), source=md.table('orders'))\n"
                    "@ms.measure(entity=orders, additivity='additive')\n"
                    "def amount(order_rows):\n"
                    f"    return {amount_expression}\n"
                    "@ms.measure(entity=orders, additivity='additive')\n"
                    "def discount(order_rows):\n"
                    "    return order_rows.discount\n"
                    f"{metric_body}\n"
                ),
            }
        )

    original = load(
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(order_rows):\n"
        "    return amount(order_rows).sum()"
    )
    renamed = load(
        "amount_alias = amount\n"
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(rows):\n"
        "    return amount_alias(rows).sum()"
    )
    rebound = load(
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(order_rows):\n"
        "    return discount(order_rows).sum()"
    )
    redefined = load(
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(order_rows):\n"
        "    return amount(order_rows).sum()",
        amount_expression="order_rows.amount * 0.9",
    )
    for project in (original, renamed, rebound, redefined):
        assert project.is_ready(), project.errors()

    def identities(project, expected_binding) -> tuple[str, str]:
        registry = project._registry
        sidecar = project._expression_sidecar
        compiled_state = project._compiled_state
        assert registry is not None and sidecar is not None and compiled_state is not None
        digest = dependency_digest(
            registry,
            sidecar=sidecar,
            semantic_refs=(Ref.metric("sales.revenue"),),
        )
        assert expected_binding in {
            binding.to_ref() for entry in digest.entries for binding in entry.bindings
        }
        return compiled_state.definition_fingerprint, digest.digest

    amount_ref = Ref.measure("sales.orders.amount")
    discount_ref = Ref.measure("sales.orders.discount")
    original_identity = identities(original, amount_ref)
    assert identities(renamed, amount_ref) == original_identity
    assert identities(rebound, discount_ref) != original_identity
    assert identities(redefined, amount_ref) != original_identity
