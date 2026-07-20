from __future__ import annotations

from contextlib import contextmanager

import ibis
import pytest

import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog, SemanticKind
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from tests.ref_helpers import make_ref


class _FakeConnections:
    def __init__(self, backend):
        self.backend = backend
        self.names: list[str] = []

    def session_backend(self, name: str):
        self.names.append(name)
        return self.backend

    @contextmanager
    def use_backend(self, name: str):
        self.names.append(name)
        yield self.backend

    def close_all(self) -> None:
        pass


def _catalog(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.Ref.datasource('warehouse'), source=md.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def amount(table):\n"
                "    return table.amount\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def total_amount(table):\n"
                "    return table.amount.sum()\n"
            ),
        }
    )
    return SemanticCatalog(project)


def test_resolver_table_uses_connection_service(semantic_project_factory):
    backend = ibis.duckdb.connect(":memory:")
    backend.con.execute("CREATE TABLE orders (amount DOUBLE)")
    connections = _FakeConnections(backend)
    resolver = _catalog(semantic_project_factory)._semantic_resolver(connections=connections)

    table = resolver.table(ms.Ref.entity("sales.orders"))

    assert "amount" in table.columns
    assert connections.names == ["warehouse"]


def test_resolver_dimension_on_accepts_semantic_ref(semantic_project_factory):
    resolver = _catalog(semantic_project_factory)._semantic_resolver(
        connections=_FakeConnections(None)
    )
    table = ibis.table({"amount": "float64"}, name="supplied_orders")

    value = resolver.dimension_on(
        make_ref("sales.orders.amount", SemanticKind.DIMENSION),
        table,
    )

    assert isinstance(value, ibis.expr.types.Value)


def test_resolver_metric_on_rejects_wrong_kind(semantic_project_factory):
    resolver = _catalog(semantic_project_factory)._semantic_resolver(
        connections=_FakeConnections(None)
    )
    table = ibis.table({"amount": "float64"}, name="supplied_orders")

    with pytest.raises(SemanticRuntimeError) as exc_info:
        resolver.metric_on(make_ref("sales.orders.amount", SemanticKind.DIMENSION), table)

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "expected metric" in str(exc_info.value)
