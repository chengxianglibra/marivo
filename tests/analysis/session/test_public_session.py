"""Public source wiring, local publication and exact generation admission."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from tests.shared_fixtures import terminal_runs


def test_new_public_session_starts_with_empty_v16_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("fresh-generation", timezone="UTC")
    assert terminal_runs(session) == ()
    assert session._runtime.store.db_path == MaterializationLayout(tmp_path).store_db
    assert terminal_runs(session) == ()
    pending = session.values(by=(), columns={"answer": None}, rows=(((), {"answer": 7}),))
    memory = pending.compute()
    assert isinstance(memory, mv.AnalysisFrame)
    assert memory.compute() is memory
    assert memory.to_pandas()["answer"].tolist() == [7]
    assert terminal_runs(session) == ()
    saved = memory.persist()
    assert isinstance(saved, mv.AnalysisFrame)
    artifact_id = saved.artifact_id
    restored = session.persisted_frame(artifact_id)
    assert isinstance(restored, mv.AnalysisFrame)
    assert restored.artifact_id == artifact_id
    assert restored.persist() is restored
    assert len(terminal_runs(session)) == 1


def test_public_session_rejects_existing_store_without_modifying_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = MaterializationLayout(tmp_path).store_db
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=8")
    acquisitions: tuple[Callable[[], mv.Session], ...] = (
        lambda: mv.session.get_or_create("new"),
        lambda: mv.session.resume("old"),
    )
    for acquire in acquisitions:
        with pytest.raises(IntegrityError) as caught:
            acquire()
        assert caught.value.stage == "store_generation"
