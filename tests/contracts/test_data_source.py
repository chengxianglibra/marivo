from __future__ import annotations

from pathlib import Path

import pytest

from marivo.adapters.local.duckdb_data_source import DuckDBDataSource
from marivo.contracts.ids import DatasourceId
from marivo.contracts.values import LogicalQuery
from tests.contracts.contract_harness import run_contract_cases
from tests.contracts.data_source_cases import DATA_SOURCE_CASES, ROUTING_DATA_SOURCE_CASES


def _make_duckdb_data_source(tmp_path: Path) -> DuckDBDataSource:
    return DuckDBDataSource(path=None)


@pytest.fixture(scope="session")
def routing_ds():
    """Session-scoped RoutingDataSource shared across routing tests.

    Skips DuckDBAnalyticsEngine.initialize() (which seeds ~35s of demo data)
    because routing tests only run trivial SQL like ``SELECT 42``.
    """
    from marivo.adapters.server.data_source import RoutingDataSource
    from marivo.datasources import DatasourceService
    from marivo.routing import QueryRouter
    from marivo.storage.duckdb_analytics import DuckDBAnalyticsEngine
    from marivo.storage.sqlite_metadata import SQLiteMetadataStore

    engine = DuckDBAnalyticsEngine(":memory:")
    # No initialize() — routing tests only need query_rows() for trivial SQL.
    metadata = SQLiteMetadataStore(Path("/tmp/marivo_test_routing_ds.meta.sqlite"))
    metadata.initialize()
    ds_service = DatasourceService(metadata)
    router = QueryRouter(metadata, ds_service)
    return RoutingDataSource(default_engine=engine, registry=ds_service, query_router=router)


data_source_factories = [
    ("DuckDBDataSource", _make_duckdb_data_source),
]


def test_duckdb_data_source_contract_cases(tmp_path: Path) -> None:
    results = run_contract_cases(
        adapter_name="DuckDBDataSource",
        factory=_make_duckdb_data_source,
        cases=DATA_SOURCE_CASES,
        tmp_path=tmp_path,
    )
    assert all(result.status == "passed" for result in results)


@pytest.mark.parametrize("name,factory", data_source_factories)
def test_close_idempotent(name, factory, tmp_path):
    store = factory(tmp_path)
    store.execute("SELECT 1")
    store.close()
    store.close()


def test_routing_data_source_default_engine(routing_ds) -> None:
    """RoutingDataSource routes queries with no datasource_id to the default engine."""
    result = routing_ds.execute(LogicalQuery(sql="SELECT 42 AS answer", params={}))
    assert result.row_count == 1
    assert result.rows[0]["answer"] == 42


def test_routing_data_source_unknown_datasource(routing_ds) -> None:
    """RoutingDataSource raises DomainError for an unknown datasource_id."""
    from marivo.contracts.errors import DomainError, ErrorCode

    with pytest.raises(DomainError) as exc_info:
        routing_ds.execute(LogicalQuery(sql="SELECT 1", datasource_id=DatasourceId("nonexistent")))
    assert exc_info.value.code == ErrorCode.DATASOURCE_UNAVAILABLE


def test_routing_data_source_contract_cases(routing_ds) -> None:
    """RoutingDataSource satisfies the routing-specific contract cases."""
    for case in ROUTING_DATA_SOURCE_CASES:
        case.run(routing_ds, Path("/tmp"))
