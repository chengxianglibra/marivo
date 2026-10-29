"""Committed Frame discovery survives lost producer output without source replay."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError, SessionStateError
from marivo.analysis.materialization import terminal_publication, terminal_store
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.frame_services import LocalFrameServices
from tests.shared_fixtures import DslCaseFactory, terminal_runs


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("discover", timezone="UTC")


def literal(session: mv.Session, value: int = 7) -> mv.AnalysisFrame:
    return session.values(columns={"amount": "USD"}, rows=(((), {"amount": value}),))


@pytest.mark.parametrize(
    "description", [None, "", "  monthly revenue\nUSD €  ", "Revenue € " * 1000]
)
def test_description_round_trip_and_stored_idempotence(
    session: mv.Session, description: str | None
) -> None:
    frame = literal(session)
    for pending in (frame, frame.compute()):
        with pytest.raises(AnalysisError) as caught:
            _ = pending.description
        assert caught.value.repair is not None
        assert "persist()" in caught.value.repair.action
    saved = frame.persist(description=description)
    page = session.persisted_frames()
    summary = page.items[0]
    reopened = session.persisted_frame(summary.artifact_id)
    assert summary.description == reopened.description == saved.description == description
    assert summary.schema == reopened.schema == saved.schema
    assert tuple(summary.schema.values) == ("amount",)
    assert summary.schema.values["amount"].unit == "USD"
    assert len(page.render(max_output_bytes=512).encode()) <= 512
    assert len(summary.render(max_output_bytes=512).encode()) <= 512
    assert saved.persist() is saved
    assert saved.persist(description=None) is saved
    assert saved.persist(description=description) is saved
    before = terminal_runs(session)
    with pytest.raises(AnalysisError, match="immutable saved description") as caught:
        saved.persist(description="different purpose")
    assert caught.value.repair is not None
    assert "compute().persist" in caught.value.repair.action
    assert terminal_runs(session) == before
    # A new publication describes its own commit, rather than inheriting a purpose.
    duplicate = saved.compute().persist()
    assert duplicate.description is None
    assert duplicate.artifact_id != saved.artifact_id


def test_directory_is_source_and_payload_free_read_only_metadata(
    session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert session.persisted_frames().items == ()
    assert ".persisted_frames()" not in session.render()
    saved = literal(session).persist()
    described = literal(session).persist(description="Monthly revenue")
    assert ".persisted_frames()" in session.render()
    before = session.updated_at, terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Frame discovery opened a payload, source, or another metadata snapshot")

    monkeypatch.setattr(mv.Session, "catalog", property(forbidden))
    monkeypatch.setattr(LocalFrameServices, "open_frame", forbidden)
    monkeypatch.setattr(LocalFrameServices, "read_data", forbidden)
    # Even absent result files must not prevent metadata discovery.
    import shutil

    shutil.rmtree(session._runtime.store.layout.artifact_dir(session.id, saved.artifact_id))
    original_read = session._runtime.store._read
    snapshots: list[bool] = []

    def tracked_read() -> AbstractContextManager[sqlite3.Connection]:
        snapshots.append(True)
        return original_read()

    monkeypatch.setattr(session._runtime.store, "_read", tracked_read)
    page = session.persisted_frames()
    assert len(snapshots) == 1
    assert {item.artifact_id for item in page.items} == {saved.artifact_id, described.artifact_id}
    assert page.items[0].description == "Monthly revenue"
    assert (session.updated_at, terminal_runs(session)) == before
    text = page.render()
    assert "Monthly revenue" in text and "amount" in text and "(not provided)" in text
    assert len(page.render(max_output_bytes=512).encode()) <= 512
    assert len(repr(page.items[0])) <= 512 and ".show()" in repr(page.items[0])


def test_pagination_is_stable_for_equal_times_and_scoped_to_selection(
    session: mv.Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(terminal_store, "_now", lambda: "2030-01-01T00:00:00.000000+00:00")
    saved = [literal(session, value).persist(description=f"Purpose {value}") for value in range(3)]
    expected = sorted((frame.artifact_id for frame in saved), reverse=True)
    page = session.persisted_frames(limit=1)
    cursor = page.next_cursor
    assert cursor is not None
    found = [page.items[0].artifact_id]
    while page.has_more:
        page = session.persisted_frames(limit=1, cursor=page.next_cursor)
        found.extend(item.artifact_id for item in page.items)
    assert found == expected and page.next_cursor is None
    other = mv.session.get_or_create("other", timezone="UTC")
    assert other.persisted_frames().items == ()
    for read in (
        lambda: session.persisted_frames(limit=2, cursor=cursor),
        lambda: other.persisted_frames(limit=1, cursor=cursor),
        lambda: session.persisted_frames(cursor="invalid"),
        lambda: session.persisted_frames(limit=1, cursor=mv.session.recent(limit=1).next_cursor),
    ):
        with pytest.raises(SessionStateError) as caught:
            read()
        assert caught.value.repair is not None
        assert caught.value.repair.help_target is not None
        assert caught.value.repair.help_target.canonical_id == "session.persisted_frames"
    second_root = tmp_path / "second-project"
    second_root.mkdir()
    monkeypatch.chdir(second_root)
    foreign = mv.session.get_or_create("discover", timezone="UTC")
    with pytest.raises(SessionStateError):
        foreign.persisted_frames(limit=1, cursor=cursor)


@pytest.mark.parametrize("limit", [0, 101, True])
def test_invalid_limits_fail_without_publication(session: mv.Session, limit: int) -> None:
    with pytest.raises(SessionStateError):
        session.persisted_frames(limit=limit)
    assert terminal_runs(session) == ()


def test_failed_publication_is_absent_and_corrupt_metadata_is_not_skipped(
    session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(event: str) -> None:
        if event == "terminal_primary_written":
            raise OSError("publication interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(terminal_publication, "_event", fail)
        with pytest.raises(MaterializationError):
            literal(session).persist(description="Never committed")
    assert session.persisted_frames().items == ()
    saved = literal(session).persist(description="Committed")
    with sqlite3.connect(session._runtime.store.db_path) as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            ("{}", saved.artifact_id),
        )
    with pytest.raises(IntegrityError):
        session.persisted_frames()


def test_lost_id_is_discovered_in_fresh_process_without_models_or_source(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    producer = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import marivo.analysis as mv
session = mv.session.get_or_create('lost-id', timezone='UTC')
metric = session.catalog.get('metric:sales.order_count').value
month = session.time_scope(start='2026-08-01', end='2026-09-01').by(mv.grain('month'))
session.observe({'orders': metric}, by=(month,)).persist(description='Four orders for recovery')
raise RuntimeError('after commit')
""",
        ],
        cwd=case.root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert producer.returncode != 0 and "after commit" in producer.stderr
    assert producer.stdout == ""
    (case.root / "models").rename(case.root / "hidden_models")
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    consumer = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import marivo.analysis as mv
session = mv.session.get_or_create('lost-id')
page = session.persisted_frames()
assert len(page.items) == 1
summary = page.items[0]
assert summary.description == 'Four orders for recovery'
assert tuple(summary.schema.values) == ('orders',)
assert summary.schema.axes[0].role == 'calendar'
assert 'calendar' in page.render()
frame = session.persisted_frame(summary.artifact_id)
assert frame.schema == summary.schema
assert frame.description == summary.description
assert frame.to_pandas()['orders'].tolist() == [4]
assert 'sales.order_count' in {origin.path for origin in frame.lineage.values['orders'].value_origins}
print('recovered original commit')
""",
        ],
        cwd=case.root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert consumer.returncode == 0, consumer.stdout + consumer.stderr
    assert "recovered original commit" in consumer.stdout
