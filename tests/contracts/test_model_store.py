from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local.file_model_store import FileModelStore, _ListQuery, _Selector
from app.contracts.ids import UserId
from app.contracts.semantic import SemanticModel
from tests.contracts.contract_harness import run_contract_cases
from tests.contracts.model_store_cases import MODEL_STORE_CASES


def _make_file_model_store(models_dir: Path) -> FileModelStore:
    models_dir.mkdir(parents=True, exist_ok=True)
    return FileModelStore(models_dir)


@pytest.fixture()
def store(tmp_path: Path) -> FileModelStore:
    return _make_file_model_store(tmp_path / "models")


def test_file_model_store_contract_cases(store: FileModelStore, tmp_path: Path) -> None:
    results = run_contract_cases(
        adapter_name="FileModelStore",
        factory=lambda _path: store,
        cases=MODEL_STORE_CASES,
        tmp_path=tmp_path,
    )
    assert all(result.status == "passed" for result in results)


def test_get_returns_none_for_absent(store: FileModelStore) -> None:
    result = store.get(_Selector(name="nonexistent"))
    assert result is None


def test_save_and_get_roundtrip_yaml(store: FileModelStore) -> None:
    model = SemanticModel(
        name="test_model",
        osi_document={"datasets": {"orders": {"table": "analytics.orders"}}},
    )
    model_id = store.save(model, actor=UserId("test_user"), expected_revision=None)
    assert isinstance(model_id, int)

    result = store.get(_Selector(name="test_model"))
    assert result is not None
    assert result.name == "test_model"


def test_list_returns_all_models(store: FileModelStore) -> None:
    for i in range(3):
        model = SemanticModel(
            name=f"model_{i}",
            osi_document={"datasets": {"orders": {"table": f"schema.t{i}"}}},
        )
        store.save(model, actor=UserId("test_user"), expected_revision=None)

    results = store.list(_ListQuery())
    assert len(results) == 3
    names = {r.name for r in results}
    assert names == {"model_0", "model_1", "model_2"}


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


def test_save_returns_consistent_model_id(store: FileModelStore) -> None:
    model = SemanticModel(name="id_test")
    model_id = store.save(model, actor=UserId("test_user"), expected_revision=None)
    # Overwriting same name should return same id
    model_v2 = SemanticModel(name="id_test", description="updated")
    model_id_2 = store.save(model_v2, actor=UserId("test_user"), expected_revision=None)
    assert model_id == model_id_2


def test_different_names_get_different_ids(store: FileModelStore) -> None:
    id_a = store.save(SemanticModel(name="a"), actor=UserId("u"), expected_revision=None)
    id_b = store.save(SemanticModel(name="b"), actor=UserId("u"), expected_revision=None)
    assert id_a != id_b


def test_list_summary_fields(store: FileModelStore) -> None:
    model = SemanticModel(
        name="summary_test",
        description="a test model",
        visibility="public",
        owner=UserId("owner1"),
    )
    store.save(model, actor=UserId("test_user"), expected_revision=None)

    results = store.list(_ListQuery())
    assert len(results) == 1
    summary = results[0]
    assert summary.name == "summary_test"
    assert summary.description == "a test model"
    assert summary.visibility == "public"
    assert summary.owner == UserId("owner1")
