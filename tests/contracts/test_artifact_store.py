from __future__ import annotations

from pathlib import Path

from marivo.adapters.local.file_artifact_store import FileArtifactStore
from tests.contracts.artifact_store_cases import ARTIFACT_STORE_CASES
from tests.contracts.contract_harness import run_contract_cases


def _make_file_artifact_store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "artifacts")


def test_file_artifact_store_contract_cases(tmp_path: Path) -> None:
    results = run_contract_cases(
        adapter_name="FileArtifactStore",
        factory=_make_file_artifact_store,
        cases=ARTIFACT_STORE_CASES,
        tmp_path=tmp_path,
    )
    assert all(result.status == "passed" for result in results)
