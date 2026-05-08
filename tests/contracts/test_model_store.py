from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local.file_model_store import FileModelStore, _Selector
from app.contracts.ids import UserId
from app.contracts.semantic import SemanticModel
from tests.contracts.contract_cases import run_contract_cases
from tests.contracts.model_store_cases import MODEL_STORE_CASES


def _make_file_model_store(models_dir: Path) -> FileModelStore:
    models_dir.mkdir(parents=True, exist_ok=True)
    return FileModelStore(models_dir)


@pytest.fixture()
def store(tmp_path: Path) -> FileModelStore:
    return _make_file_model_store(tmp_path / "models")


def test_file_model_store_contract_cases(tmp_path: Path) -> None:
    store = FileModelStore(tmp_path / "models")
    results = run_contract_cases(
        adapter_name="FileModelStore",
        factory=lambda _path: store,
        cases=MODEL_STORE_CASES,
        tmp_path=tmp_path,
    )
    assert all(result.status == "passed" for result in results)


def test_mtime_cache_invalidated_on_change(store: FileModelStore) -> None:
    model_v1 = SemanticModel(
        name="cached",
        osi_document={"datasets": {"t": {"table": "s.t1"}}},
    )
    store.save(model_v1, actor=UserId("test_user"), expected_revision=None)
    result1 = store.get(_Selector(name="cached"))
    assert result1 is not None

    model_v2 = SemanticModel(
        name="cached",
        osi_document={"datasets": {"t": {"table": "s.t2"}}},
    )
    store.save(model_v2, actor=UserId("test_user"), expected_revision=None)

    result2 = store.get(_Selector(name="cached"))
    assert result2 is not None
    assert result2.osi_document["datasets"]["t"]["table"] == "s.t2"


def test_save_atomic_no_partial_reads(store: FileModelStore, tmp_path: Path) -> None:
    model = SemanticModel(
        name="atomic_test",
        osi_document={"datasets": {"t": {"table": "s.t"}}},
    )
    store.save(model, actor=UserId("test_user"), expected_revision=None)
    tmp_files = list((tmp_path / "models").glob("tmp-*"))
    assert len(tmp_files) == 0
