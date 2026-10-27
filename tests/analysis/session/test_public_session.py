"""Public source wiring, local publication and exact generation admission."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import ArtifactNotFoundError, SessionNotFoundError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs


def test_new_public_session_starts_with_empty_v16_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("fresh-generation", timezone="UTC")
    assert terminal_runs(session) == ()
    assert session._runtime.store.db_path == MaterializationLayout(tmp_path).store_db
    with pytest.raises(ArtifactNotFoundError):
        session.persisted_frame("old-artifact")
    with pytest.raises(SessionNotFoundError):
        mv.session.resume("old-session")
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
    before = path.read_bytes()
    files = tuple(path.parent.iterdir())
    acquisitions: tuple[Callable[[], mv.Session], ...] = (
        lambda: mv.session.get_or_create("new"),
        lambda: mv.session.resume("old"),
    )
    for acquire in acquisitions:
        with pytest.raises(IntegrityError) as caught:
            acquire()
        assert caught.value.stage == "store_generation"
        assert "cannot be resumed or migrated" in str(caught.value)
        assert path.read_bytes() == before
        assert tuple(path.parent.iterdir()) == files


def test_public_calendar_snapshot_is_captured_before_pure_construction(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.semantic.catalog import SemanticCatalog
    from tests.shared_fixtures import (
        fiscal_analysis_project_files,
        publish_fiscal_calendar_artifact,
    )

    files = fiscal_analysis_project_files()
    files["sales/metrics.py"] = (
        files["sales/metrics.py"]
        .replace(
            "source=md.table('events')",
            "source=md.table('events', columns={'user_id': 'user_id', 'amount': 'amount', 'event_date': 'event_date'}), primary_key=['user_id']",
        )
        .replace("column='event_date')", "column='event_date', is_default=True)")
    )
    files["sales/calendar.py"] = files["sales/calendar.py"].replace(
        "source=md.table('calendar')",
        "source=md.table('calendar', columns={'calendar_date': 'calendar_date', 'fiscal_week': 'fiscal_week', 'fiscal_month': 'fiscal_month'}), primary_key=['calendar_date']",
    )
    catalog = SemanticCatalog(semantic_project_factory(files))
    publish_fiscal_calendar_artifact(catalog)
    monkeypatch.chdir(catalog.workspace_dir)
    session = mv.session.get_or_create("calendar", timezone="UTC")
    calendar = session.catalog.get("period_calendar:sales.fiscal")
    grain = calendar.grain("fiscal_week")
    scope = calendar.period("fiscal_week", "M1-W1")
    metric = session.catalog.get("metric:sales.gmv").value

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction reread the certified calendar")

    from marivo._temporal import TemporalSnapshotStore

    monkeypatch.setattr(TemporalSnapshotStore, "inspect_current", forbidden)
    snapshots = session._sources().period_calendar_snapshots
    assert len(snapshots) == 1
    assert snapshots[0].period_scope("fiscal_week", "M1-W1") == scope
    frame = session.observe({"gmv": metric}, by=(scope.by(grain),))
    assert isinstance(frame, mv.AnalysisFrame)
    assert terminal_runs(session) == ()
