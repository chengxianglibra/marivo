from __future__ import annotations

from pathlib import Path

from marivo.adapters.local.duckdb_data_source import DuckDBDataSource
from tests.contracts.contract_cases import ContractCase
from tests.contracts.contract_harness import run_contract_cases


def test_run_contract_cases_executes_named_cases(tmp_path: Path) -> None:
    def run_ok(adapter, _tmp_path: Path) -> None:
        result = adapter.execute("SELECT 1 AS value")
        assert result.row_count == 1
        assert result.rows[0]["value"] == 1

    results = run_contract_cases(
        adapter_name="DuckDBDataSource",
        factory=lambda _path: DuckDBDataSource(path=None),
        cases=[ContractCase(name="execute_select_one", run=run_ok)],
        tmp_path=tmp_path,
    )

    assert results[0].case_name == "execute_select_one"
    assert results[0].status == "passed"
