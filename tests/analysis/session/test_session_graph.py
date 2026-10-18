"""Store 11 exposes bounded same-Session production history without Artifact inputs."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import fields, replace
from pathlib import Path
from typing import get_args, get_type_hints

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import (
    ArtifactNotFoundError,
    SessionGraphArgumentError,
    SessionGraphLimitError,
    SessionGraphTooLargeError,
)
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.contracts import RunFailure
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.session import _lazy_graph, _lazy_runtime_reads
from marivo.analysis.session._lazy_read_model import SessionGraphEdge
from tests.shared_fixtures import rendered_help


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("production", report_timezone="UTC")


def _publish(session: mv.Session) -> mv.Artifact:
    return session.values(by=(), columns={"answer": None}, rows=(((), {"answer": 7}),)).execute()


def test_production_history_preserves_run_variants_and_bounded_recap(session: mv.Session) -> None:
    artifacts = tuple(_publish(session) for _ in range(4))
    store = session._runtime.store
    terminal_store.admit(store, session.id, "failed")
    store.fail(
        "failed",
        RunFailure("stage_execution", "execution_failed", "stopped", None, "success", "stop", None),
    )
    terminal_store.admit(store, session.id, "pending")

    graph = session.graph()
    assert graph == session.graph()
    assert not graph.truncated
    assert graph.root_run_ids == tuple(run.run_id for run in graph.runs)
    assert graph.head_artifact_refs == tuple(item.artifact_ref for item in graph.artifacts)
    assert graph.failed_run_ids == ("failed",) and graph.incomplete_run_ids == ("pending",)
    assert graph.boundary_artifact_refs == graph.boundary_run_ids == ()
    assert {(edge.kind, edge.run_id, edge.artifact_ref.ref) for edge in graph.edges} == {
        ("produces", artifact._manifest.run_id, artifact.id) for artifact in artifacts
    }
    for run in session.runs().items:
        assert session.get_run(run.run_id) == run
        assert run.dataset_input.kind == "query"
        assert not hasattr(run, "input_artifact_refs")
        assert "inputs:" not in run.render()

    recap = _lazy_runtime_reads.recap(store, session.id)
    assert recap.run_count == 6
    assert recap.artifact_count == recap.head_artifact_count == 4
    assert {ref.ref for ref in recap.head_artifact_refs} == {item.id for item in artifacts[-3:]}
    assert recap.attention_run_ids == ("pending", "failed")
    assert recap.overall_graph_available
    limited = session.graph(max_nodes=1)
    assert limited.truncated and limited.incomplete_run_ids == ("pending",)
    assert limited.artifacts == limited.edges == ()


def test_focused_production_and_truncation(session: mv.Session) -> None:
    artifact = _publish(session)
    graph = session.graph(artifact_ref=artifact.id)
    assert tuple(run.run_id for run in graph.runs) == (artifact._manifest.run_id,)
    assert len(graph.artifacts) == len(graph.edges) == 1
    assert not graph.truncated and graph.boundary_artifact_refs == ()

    limited = session.graph(artifact_ref=artifact.id, max_nodes=1)
    assert limited.truncated
    assert limited.runs == limited.edges == ()
    assert limited.boundary_artifact_refs == limited.head_artifact_refs
    assert limited.boundary_run_ids == ()
    assert session.graph(max_nodes=1) == limited

    terminal = session.graph(artifact_ref=artifact.id, direction="descendants", max_nodes=1)
    assert terminal.artifacts == graph.artifacts
    assert terminal.runs == terminal.edges == ()
    assert not terminal.truncated
    assert terminal.boundary_artifact_refs == terminal.boundary_run_ids == ()
    with pytest.raises(SessionGraphArgumentError):
        session.graph(direction="descendants")
    with pytest.raises(SessionGraphLimitError):
        session.graph(max_nodes=0)
    with pytest.raises(SessionGraphLimitError):
        session.graph(max_nodes=501)


def test_graph_rejects_foreign_focus_and_bypasses_only_overall_scan_limit(
    session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _publish(session)
    foreign = mv.session.get_or_create("foreign", report_timezone="UTC")
    with pytest.raises(ArtifactNotFoundError):
        foreign.graph(artifact_ref=artifact.id)
    with pytest.raises(ArtifactNotFoundError):
        session.graph(artifact_ref="missing")
    assert foreign.graph().artifacts == ()
    monkeypatch.setattr(_lazy_graph, "OVERALL_GRAPH_SCAN_LIMIT", 1)
    monkeypatch.setattr(_lazy_runtime_reads, "OVERALL_GRAPH_SCAN_LIMIT", 1)
    assert not _lazy_runtime_reads.recap(session._runtime.store, session.id).overall_graph_available
    with pytest.raises(SessionGraphTooLargeError):
        session.graph()
    assert len(session.graph(artifact_ref=artifact.id).edges) == 1


def test_producer_precedes_output_when_timestamps_are_equal(
    session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import store as store_module

    monkeypatch.setattr(store_module, "_now", lambda: "2026-10-18T00:00:00.000000Z")
    monkeypatch.setattr(terminal_store, "_now", lambda: "2026-10-18T00:00:00.000000Z")
    artifacts = (_publish(session), _publish(session))
    graph = session.graph()
    assert tuple(run.run_id for run in graph.runs) == tuple(sorted(graph.root_run_ids))
    assert tuple(edge.artifact_ref.ref for edge in graph.edges) == tuple(
        run.output_artifact_ref.ref for run in graph.runs if isinstance(run, mv.SucceededRun)
    )
    with session._runtime.store._read() as conn:
        topology = _lazy_graph._Topology(conn, session.id, 100)
        selected, _ = _lazy_graph._overall(topology)
        ordered = _lazy_graph._ordered(topology, selected, graph.edges)
    for artifact in artifacts:
        assert ordered.index(("run", artifact._manifest.run_id)) < ordered.index(
            ("artifact", artifact.id)
        )


def test_run_and_edge_help_pin_production_only_fields(session: mv.Session) -> None:
    base = ("run_id", "session_id", "admitted_at", "dataset_input")
    for value, extra in (
        (mv.IncompleteRun, ()),
        (mv.FailedRun, ("failed_at", "failure")),
        (mv.SucceededRun, ("finished_at", "output_artifact_ref")),
    ):
        assert tuple(field.name for field in fields(value)) == base + extra
        text = rendered_help(value)
        assert text == rendered_help(value)
        assert "input_artifact_refs" not in text
    artifact = _publish(session)
    run = session.get_run(artifact._manifest.run_id)
    assert rendered_help(type(run)) == rendered_help(mv.SucceededRun)
    edge = session.graph().edges[0]
    text = rendered_help("analysis.runtime.session_graph_edge")
    assert text == rendered_help(SessionGraphEdge) == rendered_help(type(edge))
    assert "  kind: Literal['produces']\n" in text
    assert get_args(get_type_hints(SessionGraphEdge)["kind"]) == ("produces",)
    with pytest.raises(IntegrityError, match="unregistered graph edge kind"):
        replace(edge, kind="consumes")
    graph_help = rendered_help("analysis.session_graph")
    assert "every selected Run" in graph_help and "every selected Artifact" in graph_help
    assert "omitted requested neighbors" in graph_help
    text = rendered_help("analysis.session.graph")
    assert text == rendered_help(session.graph)
    assert "ancestors includes its producer" in text and "descendants returns only" in text


def test_store_11_schema_rejects_legacy_relation_and_preserves_output_codec(
    session: mv.Session,
) -> None:
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.materialization.terminal_protocol import FORMAT

    store = session._runtime.store
    assert MaterializationLayout(store.project_root).generation == 11
    with store._read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert "analysis_action_run_inputs" not in names and "run_inputs_artifact" not in names
    assert "analysis_action_run_terminals" in names
    assert FORMAT == "marivo.terminal_output/v1"
    with store._write() as conn:
        conn.execute("CREATE TABLE analysis_action_run_inputs (legacy TEXT) STRICT")
    with pytest.raises(IntegrityError, match="incomplete v11 schema"):
        SessionStore.open_existing(store.project_root)


def test_graph_and_run_reject_inconsistent_production_identity(session: mv.Session) -> None:
    artifact = _publish(session)
    with session._runtime.store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET invocation_id=? WHERE artifact_ref=?",
            ("different-invocation", artifact.id),
        )
    with pytest.raises(IntegrityError):
        session.get_run(artifact._manifest.run_id)
    with pytest.raises(IntegrityError):
        session.graph(artifact_ref=artifact.id, direction="descendants")


def test_store_10_rejection_preserves_complete_legacy_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.store import SessionStore

    monkeypatch.chdir(tmp_path)
    store = SessionStore(tmp_path)
    # Store 10 has the same retained relations plus this retired input relation/index.
    with closing(sqlite3.connect(store.db_path)) as conn:
        conn.executescript("""
            CREATE TABLE analysis_action_run_inputs (
                run_ref TEXT NOT NULL CHECK(length(run_ref)>0),
                session_ref TEXT NOT NULL CHECK(length(session_ref)>0),
                input_ordinal INTEGER NOT NULL CHECK(input_ordinal>=0),
                artifact_ref TEXT NOT NULL REFERENCES dataset_artifacts(artifact_ref) ON DELETE RESTRICT,
                PRIMARY KEY(run_ref,input_ordinal),
                FOREIGN KEY(session_ref,run_ref) REFERENCES analysis_action_runs(session_ref,run_ref) ON DELETE RESTRICT
            ) STRICT;
            CREATE INDEX run_inputs_artifact ON analysis_action_run_inputs(artifact_ref,run_ref);
            PRAGMA user_version=10;
        """)
    legacy = store.layout.generation_dir.with_name("v10")
    store.layout.generation_dir.rename(legacy)
    before = {path.name: path.read_bytes() for path in legacy.iterdir() if path.is_file()}
    assert mv.session.current() is None and mv.session.recent().items == ()
    for create in (lambda: mv.session.get_or_create("new-name"), lambda: mv.session.resume("old")):
        with pytest.raises(IntegrityError) as captured:
            create()
        assert captured.value.stage == "store_generation"
        assert captured.value.received == "Session Store user_version=10"
    assert not store.layout.generation_dir.exists()
    assert {path.name: path.read_bytes() for path in legacy.iterdir() if path.is_file()} == before
