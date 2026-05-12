from __future__ import annotations

from pathlib import Path

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.adapters.server.data_source import RoutingDataSource
from marivo.datasources import DatasourceService
from marivo.routing import QueryRouter
from tests.contracts.contract_cases import ContractCase
from tests.contracts.contract_harness import run_contract_cases


def _make_routing_ds(_path: Path) -> RoutingDataSource:
    engine = DuckDBAnalyticsEngine(":memory:")
    metadata = SQLiteMetadataStore(_path / "harness-meta.sqlite")
    metadata.initialize()
    ds_service = DatasourceService(metadata)
    router = QueryRouter(metadata, ds_service)
    return RoutingDataSource(registry=ds_service, query_router=router, default_engine=engine)


def test_run_contract_cases_executes_named_cases(tmp_path: Path) -> None:
    def run_ok(adapter, _tmp_path: Path) -> None:
        from marivo.contracts.values import LogicalQuery

        result = adapter.execute(LogicalQuery(sql="SELECT 1 AS value", params={}))
        assert result.row_count == 1
        assert result.rows[0]["value"] == 1

    results = run_contract_cases(
        adapter_name="RoutingDataSource",
        factory=_make_routing_ds,
        cases=[ContractCase(name="execute_select_one", run=run_ok)],
        tmp_path=tmp_path,
    )

    assert results[0].case_name == "execute_select_one"
    assert results[0].status == "passed"
