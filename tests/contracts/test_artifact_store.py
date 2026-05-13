from __future__ import annotations

from pathlib import Path

import pytest

from marivo.adapters.local.file_artifact_store import FileArtifactStore
from tests.contracts.artifact_store_cases import ARTIFACT_STORE_CASES
from tests.contracts.contract_harness import run_contract_cases


def _make_file_artifact_store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "artifacts")


def _make_metadata_artifact_store(tmp_path: Path):
    from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
    from marivo.adapters.server.artifact_store import MetadataArtifactStoreAdapter

    metadata = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    metadata.initialize()
    return MetadataArtifactStoreAdapter(metadata)


artifact_store_factories = [
    ("FileArtifactStore", _make_file_artifact_store),
    ("MetadataArtifactStoreAdapter", _make_metadata_artifact_store),
]


@pytest.mark.parametrize("adapter_name,factory", artifact_store_factories)
def test_artifact_store_contract_cases(adapter_name, factory, tmp_path: Path) -> None:
    results = run_contract_cases(
        adapter_name=adapter_name,
        factory=factory,
        cases=ARTIFACT_STORE_CASES,
        tmp_path=tmp_path,
    )
    assert all(result.status == "passed" for result in results)
