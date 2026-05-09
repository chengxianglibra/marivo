from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local.duckdb_data_source import DuckDBDataSource
from app.config import MarivoConfig
from app.contracts.ids import UserId
from app.contracts.semantic import SemanticModel
from app.profiles.server import ServerConfig, create_server_runtime
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.contracts.data_source_cases import DATA_SOURCE_CASES
from tests.contracts.model_store_cases import MODEL_STORE_CASES
from tests.contracts.parity import compare_contract_matrix


def test_datasource_local_server_parity(tmp_path: Path) -> None:
    local_factory = lambda _path: DuckDBDataSource(path=None)

    def remote_factory(path: Path):
        return create_server_runtime(
            ServerConfig(
                marivo_config=MarivoConfig(),
                db_path=":memory:",
                metadata_store=SQLiteMetadataStore(path / "server-meta.sqlite"),
                analytics_engine=DuckDBAnalyticsEngine(":memory:"),
            )
        ).runtime.ports.data_source

    results = compare_contract_matrix(
        local_name="DuckDBDataSource",
        local_factory=local_factory,
        remote_name="ServerDataSource",
        remote_factory=remote_factory,
        cases=DATA_SOURCE_CASES,
        tmp_path=tmp_path,
    )

    assert results
    assert {result.case_name for result in results} == {
        "execute_logical_query",
        "execute_invalid_sql_raises_validation",
        "schema_returns_columns",
        "schema_missing_table_raises_not_found",
    }
    assert any(result.detail for result in results)
    assert any(
        result.case_name == "schema_missing_table_raises_not_found" and result.detail
        for result in results
    )


@pytest.mark.xfail(reason="Phase 9 will replace the server model store adapter")
def test_model_store_remote_save_case(tmp_path: Path) -> None:
    remote_store = create_server_runtime(
        ServerConfig(
            marivo_config=MarivoConfig(),
            db_path=":memory:",
            metadata_store=SQLiteMetadataStore(tmp_path / "meta.sqlite"),
            analytics_engine=DuckDBAnalyticsEngine(":memory:"),
        )
    ).runtime.ports.model_store
    remote_store.save(
        SemanticModel(name="server-save"),
        actor=UserId("owner1"),
        expected_revision=None,
    )
