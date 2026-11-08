from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import ibis
import pytest

from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError

if TYPE_CHECKING:
    from tests.conftest import _SemanticProjectFactory


class _FakeConnections:
    def __init__(self, backend: object) -> None:
        self.backend = backend
        self.names: list[str] = []

    def session_backend(self, name: str) -> object:
        self.names.append(name)
        return self.backend

    @contextmanager
    def use_backend(self, name: str) -> Iterator[object]:
        self.names.append(name)
        yield self.backend

    def close_all(self) -> None:
        pass


def _catalog(semantic_project_factory: _SemanticProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def amount(table):\n"
                "    return table.amount\n"
            ),
        }
    )
    return SemanticCatalog(project)


def test_resolver_table_uses_connection_service(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    backend = ibis.duckdb.connect(":memory:")
    backend.con.execute("CREATE TABLE orders (amount DOUBLE)")
    connections = _FakeConnections(backend)
    resolver = _catalog(semantic_project_factory)._semantic_resolver(connections=connections)

    table = resolver.entity(ref_factory.entity("sales.orders"))

    assert "amount" in table.columns
    assert connections.names == ["warehouse"]


def test_resolver_dimension_on_accepts_semantic_ref(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    resolver = _catalog(semantic_project_factory)._semantic_resolver(
        connections=_FakeConnections(None)
    )
    table = ibis.table({"amount": "float64"}, name="supplied_orders")

    value = resolver.dimension_on(
        ref_factory.dimension("sales.orders.amount"),
        table,
    )

    assert isinstance(value, ibis.expr.types.Value)
    assert value.get_name() == "amount"


def test_resolver_measure_on_rejects_wrong_kind(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    resolver = _catalog(semantic_project_factory)._semantic_resolver(
        connections=_FakeConnections(None)
    )
    table = ibis.table({"amount": "float64"}, name="supplied_orders")

    with pytest.raises(SemanticRuntimeError) as exc_info:
        resolver.measure_on(ref_factory.dimension("sales.orders.amount"), table)  # type: ignore[arg-type]  # Exercise wrong kind.

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "expected measure" in str(exc_info.value)
