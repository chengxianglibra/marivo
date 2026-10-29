"""Pure Measure composition and retained restricted Ibis field binding."""

from __future__ import annotations

from collections.abc import Callable

import ibis
import pytest

import marivo.datasource as md
from marivo._definition.graph import computation_fingerprint
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind
from marivo.semantic.materializer import Materializer
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import patch_semantic_connections

ProjectFactory = Callable[[dict[str, str]], SemanticProject]
_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
discount_ref = ms.measure_column(name="discount", entity=orders, column="discount", additivity=ms.additive_all())
@ms.dimension(entity=orders)
def country(rows):
    return rows.country
@ms.time_dimension(entity=orders, is_default=True)
def ordered_at(rows):
    return rows.ordered_at
amount = amount_ref
discount = discount_ref
@ms.measure(additivity=ms.additive_all())
def net_amount():
    return amount * 0.9
@ms.metric()
def revenue():
    return net_amount.sum()
"""


def _project(factory: ProjectFactory, source: str) -> SemanticProject:
    return factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/model.py": source,
        }
    )


@pytest.mark.parametrize("use_locals", (False, True))
def test_pure_measure_composition_and_physical_fields_execute(
    semantic_project_factory: ProjectFactory, use_locals: bool
) -> None:
    source = _MODEL
    if use_locals:
        source = source.replace(
            "return amount * 0.9", "value = amount\n    net = value * 0.9\n    return net"
        )
        source = source.replace(
            "return net_amount.sum()", "value = net_amount\n    return value.sum()"
        )
    project = _project(semantic_project_factory, source)
    assert project.is_ready(), project.errors()
    connection = ibis.duckdb.connect(":memory:")
    connection.con.execute(
        "CREATE TABLE orders(amount DOUBLE, discount DOUBLE, country VARCHAR, ordered_at DATE)"
    )
    connection.con.execute(
        "INSERT INTO orders VALUES (10,1,'US','2026-01-01'),(20,2,'CA','2026-01-02'),(30,3,'US','2026-01-03')"
    )
    try:
        materializer = Materializer(project, lambda _name: connection)
        assert connection.execute(materializer.dimension("sales.orders.country")).tolist() == [
            "US",
            "CA",
            "US",
        ]
        assert len(connection.execute(materializer.dimension("sales.orders.ordered_at"))) == 3
        catalog = SemanticCatalog(project)
        metric = catalog.get("metric:sales.revenue")
        assert "callable" not in catalog.get("metric:sales.revenue").render()
        dependencies = catalog._state.dependencies[ref_factory.metric("sales.revenue")]
        assert ref_factory.measure("sales.orders.amount") in dependencies
        with patch_semantic_connections(project, lambda _name: connection):
            assert catalog.preview(
                metric, scope=md.unpruned(max_rows=10, timeout_seconds=30)
            ).rows == ({"value": 54.0},)
    finally:
        connection.disconnect()


def test_field_bind_rejects_wrong_positional_entity(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = """\
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
users = ms.entity(name="users", datasource=ms.ref.datasource("warehouse"), source=md.table("users"))
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
@ms.dimension(entity=users)
def revenue(rows):
    return ms.bind(amount, rows)
"""
    project = _project(semantic_project_factory, source)
    error = next(
        error for error in project.errors() if error.kind == ErrorKind.BINDING_ENTITY_MISMATCH
    )
    assert error.expected == ref_factory.entity("sales.users").key
    assert error.received == ref_factory.entity("sales.orders").key
    assert error.repair is not None and error.repair.help_target.canonical_id == "bind"


def test_computation_identity_ignores_local_aliases_but_tracks_consumed_fields(
    semantic_project_factory: ProjectFactory,
) -> None:
    sources = (
        _MODEL,
        _MODEL.replace("return amount * 0.9", "value = amount\n    return value * 0.9"),
        _MODEL.replace("return amount * 0.9", "return discount * 0.9"),
        _MODEL.replace("return amount * 0.9", "return amount * 0.8"),
    )
    identities = []
    for source in sources:
        project = _project(semantic_project_factory, source)
        assert project.is_ready(), project.errors()
        metric = SemanticCatalog(project)._state.registry.declared_metrics["sales.revenue"]
        identities.append(computation_fingerprint(consume_metric(metric).node))
    assert identities[0] == identities[1]
    assert identities[2] != identities[0]
    assert identities[3] != identities[0]
