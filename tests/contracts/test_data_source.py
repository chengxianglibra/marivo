from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local.duckdb_data_source import DuckDBDataSource
from app.contracts.errors import ErrorCode, NotFoundError, ValidationError
from app.contracts.ids import DatasourceId
from app.contracts.values import LogicalQuery, SourceRef


def _make_duckdb_data_source(tmp_path: Path) -> DuckDBDataSource:
    return DuckDBDataSource(path=None)


data_source_factories = [
    ("DuckDBDataSource", _make_duckdb_data_source),
]


@pytest.mark.parametrize("name,factory", data_source_factories)
def test_execute_returns_query_result(name, factory, tmp_path):
    store = factory(tmp_path)
    result = store.execute("SELECT 1 AS value")
    assert result is not None
    assert result.row_count == 1
    assert len(result.rows) == 1
    assert result.rows[0]["value"] == 1


@pytest.mark.parametrize("name,factory", data_source_factories)
def test_execute_logical_query(name, factory, tmp_path):
    store = factory(tmp_path)
    query = LogicalQuery(sql="SELECT 42 AS answer", params={})
    result = store.execute(query)
    assert result is not None
    assert result.row_count == 1
    assert result.rows[0]["answer"] == 42


@pytest.mark.parametrize("name,factory", data_source_factories)
def test_execute_invalid_sql_raises(name, factory, tmp_path):
    store = factory(tmp_path)
    with pytest.raises(ValidationError) as exc_info:
        store.execute("SELECT SELECT")  # Syntax error triggers ParserException
    assert exc_info.value.code == ErrorCode.VALIDATION


@pytest.mark.parametrize("name,factory", data_source_factories)
def test_schema_returns_source_schema(name, factory, tmp_path):
    store = factory(tmp_path)
    store.execute("CREATE TABLE test_tbl (id INTEGER, name VARCHAR)")
    source_ref = SourceRef(
        datasource_id=DatasourceId("local"),
        schema_name="main",
        table_name="test_tbl",
    )
    schema = store.schema(source_ref)
    assert schema is not None
    assert len(schema.columns) == 2
    col_names = [c.name for c in schema.columns]
    assert "id" in col_names
    assert "name" in col_names


@pytest.mark.parametrize("name,factory", data_source_factories)
def test_schema_missing_table_raises_not_found(name, factory, tmp_path):
    store = factory(tmp_path)
    source_ref = SourceRef(
        datasource_id=DatasourceId("local"),
        schema_name="main",
        table_name="nonexistent",
    )
    with pytest.raises(NotFoundError) as exc_info:
        store.schema(source_ref)
    assert exc_info.value.code == ErrorCode.NOT_FOUND


@pytest.mark.parametrize("name,factory", data_source_factories)
def test_close_idempotent(name, factory, tmp_path):
    store = factory(tmp_path)
    store.execute("SELECT 1")
    store.close()
    store.close()  # Should not raise
