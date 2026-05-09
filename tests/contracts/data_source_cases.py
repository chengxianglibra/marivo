from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts.errors import DomainError, ErrorCode, NotFoundError, ValidationError
from app.contracts.ids import DatasourceId
from app.contracts.values import LogicalQuery, SourceRef
from tests.contracts.contract_cases import ContractCase


def _run_execute_query(adapter, _: Path) -> None:
    result = adapter.execute(LogicalQuery(sql="SELECT 42 AS answer", params={}))
    assert result.row_count == 1
    assert result.rows[0]["answer"] == 42


def _expect_validation_error(adapter, _: Path) -> None:
    with pytest.raises(ValidationError) as exc_info:
        adapter.execute("SELECT SELECT")
    assert exc_info.value.code == ErrorCode.VALIDATION


def _expect_schema_columns(adapter, _: Path) -> None:
    adapter.execute("CREATE TABLE test_tbl (id INTEGER, name VARCHAR)")
    schema = adapter.schema(
        SourceRef(
            datasource_id=DatasourceId("local"),
            schema_name="main",
            table_name="test_tbl",
        )
    )
    assert len(schema.columns) == 2
    assert {column.name for column in schema.columns} == {"id", "name"}


def _expect_not_found(adapter, _: Path) -> None:
    with pytest.raises(NotFoundError) as exc_info:
        adapter.schema(
            SourceRef(
                datasource_id=DatasourceId("local"),
                schema_name="main",
                table_name="missing_tbl",
            )
        )
    assert exc_info.value.code == ErrorCode.NOT_FOUND


# --- datasource_id routing cases (RoutingDataSource only) ---


def _run_duckdb_default_routing(adapter, _: Path) -> None:
    """Query with no datasource_id routes to the default DuckDB engine."""
    result = adapter.execute(LogicalQuery(sql="SELECT 42 AS answer", params={}, datasource_id=None))
    assert result.row_count == 1
    assert result.rows[0]["answer"] == 42


def _run_unknown_datasource_raises(adapter, _: Path) -> None:
    """Query with an unknown datasource_id raises DATASOURCE_UNAVAILABLE."""
    with pytest.raises(DomainError) as exc_info:
        adapter.execute(
            LogicalQuery(sql="SELECT 1", params={}, datasource_id=DatasourceId("nonexistent"))
        )
    assert exc_info.value.code == ErrorCode.DATASOURCE_UNAVAILABLE


def _run_resolve_tables(adapter, _: Path) -> None:
    """resolve_tables delegates to the query router and returns a result."""
    result = adapter.resolve_tables([], session_id=None)
    assert result is not None


DATA_SOURCE_CASES = [
    ContractCase(name="execute_logical_query", run=_run_execute_query),
    ContractCase(name="execute_invalid_sql_raises_validation", run=_expect_validation_error),
    ContractCase(name="schema_returns_columns", run=_expect_schema_columns),
    ContractCase(name="schema_missing_table_raises_not_found", run=_expect_not_found),
]

ROUTING_DATA_SOURCE_CASES = [
    ContractCase(name="duckdb_default_routing", run=_run_duckdb_default_routing),
    ContractCase(name="unknown_datasource_raises", run=_run_unknown_datasource_raises),
    ContractCase(name="resolve_tables", run=_run_resolve_tables),
]
