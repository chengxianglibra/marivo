from __future__ import annotations

from pathlib import Path

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.file_model_store import FileModelStore
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.adapters.local.sqlite_session_store import SqliteSessionStore
from marivo.adapters.server.session_store import SqlSessionStore
from marivo.config import MarivoConfig
from marivo.profiles.server import ServerConfig, create_server_runtime
from tests.contracts.data_source_cases import DATA_SOURCE_CASES
from tests.contracts.model_store_cases import MODEL_STORE_CASES
from tests.contracts.parity import compare_contract_matrix
from tests.contracts.session_store_cases import SESSION_STORE_CASES


def test_datasource_local_server_parity(tmp_path: Path) -> None:
    # Use RoutingDataSource with DuckDBAnalyticsEngine as the local data source,
    # replacing the deleted DuckDBDataSource.
    def local_factory(path: Path):
        from marivo.adapters.server.data_source import RoutingDataSource
        from marivo.datasources import DatasourceService
        from marivo.routing import QueryRouter

        engine = DuckDBAnalyticsEngine(str(path / "local-analytics.duckdb"))
        metadata = SQLiteMetadataStore(path / "local-meta.sqlite")
        metadata.initialize()
        ds_service = DatasourceService(metadata)
        router = QueryRouter(metadata, ds_service)
        return RoutingDataSource(registry=ds_service, query_router=router, default_engine=engine)

    def remote_factory(path: Path):
        # Use a file-based DuckDB so tables persist across connections
        return create_server_runtime(
            ServerConfig(
                marivo_config=MarivoConfig(),
                db_path=":memory:",
                metadata_store=SQLiteMetadataStore(path / "server-meta.sqlite"),
                analytics_engine=DuckDBAnalyticsEngine(str(path / "server-analytics.duckdb")),
            )
        ).runtime.ports.data_source

    results = compare_contract_matrix(
        local_name="RoutingDataSource",
        local_factory=local_factory,
        remote_name="ServerDataSource",
        remote_factory=remote_factory,
        cases=DATA_SOURCE_CASES,
        tmp_path=tmp_path,
    )

    assert results
    assert {result.case_name for result in results} == {case.name for case in DATA_SOURCE_CASES}
    for result in results:
        assert result.local_status == "passed", f"Local {result.case_name} failed: {result.detail}"
        assert result.remote_status == "passed", (
            f"Remote {result.case_name} failed: {result.detail}"
        )


def test_model_store_local_server_parity(tmp_path: Path) -> None:
    """Local FileModelStore and server ModelStore must satisfy
    the same model store contract cases."""

    def local_factory(p: Path) -> FileModelStore:
        models_dir = p / "local-models"
        models_dir.mkdir(parents=True, exist_ok=True)
        return FileModelStore(models_dir)

    def remote_factory(p: Path):
        return create_server_runtime(
            ServerConfig(
                marivo_config=MarivoConfig(),
                db_path=":memory:",
                metadata_store=SQLiteMetadataStore(p / "remote-meta.sqlite"),
                analytics_engine=DuckDBAnalyticsEngine(str(p / "remote-analytics.duckdb")),
            )
        ).runtime.ports.model_store

    results = compare_contract_matrix(
        local_name="FileModelStore",
        local_factory=local_factory,
        remote_name="ServerModelStore",
        remote_factory=remote_factory,
        cases=MODEL_STORE_CASES,
        tmp_path=tmp_path,
    )

    assert results
    assert {result.case_name for result in results} == {case.name for case in MODEL_STORE_CASES}
    for result in results:
        assert result.local_status == "passed", f"Local {result.case_name} failed: {result.detail}"
        assert result.remote_status == "passed", (
            f"Remote {result.case_name} failed: {result.detail}"
        )


def test_session_store_local_server_parity(tmp_path: Path) -> None:
    """Local SqliteSessionStore and server SqlSessionStore must satisfy
    the same session store contract cases."""

    def local_factory(p: Path) -> SqliteSessionStore:
        return SqliteSessionStore(p / "local.sqlite")

    def remote_factory(p: Path) -> SqlSessionStore:
        m = SQLiteMetadataStore(p / "remote.meta.sqlite")
        m.initialize()
        return SqlSessionStore(m)

    results = compare_contract_matrix(
        local_name="SqliteSessionStore",
        local_factory=local_factory,
        remote_name="SqlSessionStore",
        remote_factory=remote_factory,
        cases=SESSION_STORE_CASES,
        tmp_path=tmp_path,
    )

    assert results
    assert {result.case_name for result in results} == {case.name for case in SESSION_STORE_CASES}
    for result in results:
        assert result.local_status == "passed", f"Local {result.case_name} failed: {result.detail}"
        assert result.remote_status == "passed", (
            f"Remote {result.case_name} failed: {result.detail}"
        )
