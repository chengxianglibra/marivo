"""Public source wiring, local publication and exact generation admission."""

import sqlite3
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import ArtifactNotFoundError, SessionNotFoundError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout


def test_new_public_session_starts_with_empty_v7_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("fresh-generation", report_timezone="UTC")
    assert session.runs().items == ()
    assert session._runtime.store.db_path == MaterializationLayout(tmp_path).store_db
    with pytest.raises(ArtifactNotFoundError):
        session.artifact("old-artifact")
    with pytest.raises(SessionNotFoundError):
        mv.session.resume("old-session", by="id")
    assert session.runs().items == ()


@pytest.mark.parametrize("version", [0, 2, 3])
@pytest.mark.parametrize("entry", ["get_or_create", "resume", "current"])
def test_public_session_rejects_existing_store_without_modifying_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int, entry: str
) -> None:
    monkeypatch.chdir(tmp_path)
    path = MaterializationLayout(tmp_path).store_db
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    before = path.read_bytes()
    files = tuple(path.parent.iterdir())
    with pytest.raises(IntegrityError) as caught:
        if entry == "get_or_create":
            mv.session.get_or_create("new")
        elif entry == "resume":
            mv.session.resume("old")
        else:
            mv.session.current()
    assert caught.value.stage == "store_generation"
    assert "new named Session" in str(caught.value)
    assert path.read_bytes() == before
    assert tuple(path.parent.iterdir()) == files


def test_public_calendar_snapshot_is_captured_before_pure_construction(
    semantic_project_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.semantic.catalog import SemanticCatalog
    from tests.shared_fixtures import (
        fiscal_analysis_project_files,
        publish_fiscal_calendar_artifact,
    )

    files = fiscal_analysis_project_files()
    files["sales/metrics.py"] = files["sales/metrics.py"].replace(
        "source=md.table('events')",
        "source=md.table('events', columns={'user_id': 'user_id', 'amount': 'amount', 'event_date': 'event_date'}), primary_key=['user_id']",
    )
    files["sales/calendar.py"] = files["sales/calendar.py"].replace(
        "source=md.table('calendar')",
        "source=md.table('calendar', columns={'calendar_date': 'calendar_date', 'fiscal_week': 'fiscal_week', 'fiscal_month': 'fiscal_month'}), primary_key=['calendar_date']",
    )
    catalog = SemanticCatalog(semantic_project_factory(files))
    publish_fiscal_calendar_artifact(catalog)
    monkeypatch.chdir(catalog.workspace_dir)
    session = mv.session.get_or_create("calendar", report_timezone="UTC")
    calendar = session.catalog.require(ms.ref.period_calendar("sales.fiscal"))
    grain = calendar.grain("fiscal_week")
    scope = calendar.period("fiscal_week", "M1-W1")
    metric = ms.ref.metric("sales.gmv")
    axis = ms.ref.time_dimension("sales.events.event_date")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction reread the certified calendar")

    from marivo._temporal import TemporalSnapshotStore

    monkeypatch.setattr(TemporalSnapshotStore, "inspect_current", forbidden)
    snapshots = session._sources().period_calendar_snapshots
    assert len(snapshots) == 1
    assert snapshots[0].period_scope("fiscal_week", "M1-W1") == scope
    assert session.runs().items == ()


def test_dataset_help_does_not_advertise_private_harness_execution() -> None:
    from marivo._help.render import render_help_text

    text = render_help_text("analysis.actions.execute")[0]
    assert "Store 7" in text
    assert "session.members" in text
    assert "Population" not in text
