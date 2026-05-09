from __future__ import annotations

from pathlib import Path

from app.adapters.local.sqlite_step_store import SqliteStepStore
from tests.contracts.contract_harness import run_contract_cases
from tests.contracts.step_store_cases import STEP_STORE_CASES


def _make_sqlite_step_store(tmp_path: Path) -> SqliteStepStore:
    return SqliteStepStore(tmp_path / "test_state.db")


def test_sqlite_step_store_contract_cases(tmp_path: Path) -> None:
    results = run_contract_cases(
        adapter_name="SqliteStepStore",
        factory=_make_sqlite_step_store,
        cases=STEP_STORE_CASES,
        tmp_path=tmp_path,
    )
    assert all(result.status == "passed" for result in results)
