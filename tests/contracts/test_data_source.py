from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local.duckdb_data_source import DuckDBDataSource
from tests.contracts.contract_harness import run_contract_cases
from tests.contracts.data_source_cases import DATA_SOURCE_CASES


def _make_duckdb_data_source(tmp_path: Path) -> DuckDBDataSource:
    return DuckDBDataSource(path=None)


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
