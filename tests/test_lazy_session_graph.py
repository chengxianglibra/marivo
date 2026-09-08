"""v3 graph scope, deterministic budgets, topology, and integrity boundaries."""

from pathlib import Path

import pytest

from marivo.analysis.errors import (
    ArtifactNotFoundError,
    SessionGraphIntegrityError,
    SessionGraphTooLargeError,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_graph import graph
from marivo.analysis.session._lazy_runtime_reads import artifact_summary, recap
from tests.lazy_runtime_read_fixtures import failure, input_value, publish


def test_foreign_inputs_preserve_owner_without_expanding_producer(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("origin", session_ref="origin")
    store.create_session("target", session_ref="target")
    publish(store, "origin-run", "origin-artifact", session_ref="origin")
    assert artifact_summary(store, "origin-artifact").artifact_session_ref == "origin"
    assert graph(store, "target").artifacts == ()
    with pytest.raises(ArtifactNotFoundError):
        graph(store, "target", artifact_ref=ArtifactRef(ref="origin-artifact"))
    publish(store, "local-run", "local-artifact", session_ref="target", inputs=("origin-artifact",))
    result = graph(store, "target")
    assert [run.run_id for run in result.runs] == ["local-run"]
    assert [(item.artifact_ref.ref, item.artifact_session_ref) for item in result.artifacts] == [
        ("origin-artifact", "origin"),
        ("local-artifact", "target"),
    ]
    assert result.root_run_ids == ()
    assert result.head_artifact_refs == (ArtifactRef(ref="local-artifact"),)
    assert result.boundary_artifact_refs == (ArtifactRef(ref="origin-artifact"),)
    assert not result.truncated
    assert graph(store, "origin").head_artifact_refs == (ArtifactRef(ref="origin-artifact"),)
    ancestor = graph(store, "target", artifact_ref=ArtifactRef(ref="origin-artifact"))
    assert ancestor.runs == () and not ancestor.truncated
    descendant = graph(
        store, "target", artifact_ref=ArtifactRef(ref="origin-artifact"), direction="descendants"
    )
    assert descendant == result


def test_attention_budget_and_complete_snapshot_heads(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "root", "a")
    publish(store, "child", "b", inputs=("a",))
    store.admit(
        "session", "failure-key", input_value(), run_ref="failure", input_artifact_refs=("b",)
    )
    store.fail("failure", failure())
    small = graph(store, "session", max_nodes=1)
    assert [run.run_id for run in small.runs] == ["failure"]
    assert small.boundary_run_ids == ("failure",) and small.truncated
    focused = graph(store, "session", artifact_ref=ArtifactRef(ref="a"), max_nodes=1)
    assert focused.head_artifact_refs == ()
    assert focused.boundary_artifact_refs == (ArtifactRef(ref="a"),) and focused.truncated
    result = graph(store, "session")
    assert result.root_run_ids == ("root",)
    assert result.head_artifact_refs == (ArtifactRef(ref="b"),)
    summary = recap(store, "session")
    assert summary.run_count == 3 and summary.artifact_count == 2
    assert summary.head_artifact_count == 1 and summary.failed_run_count == 1
    assert summary.attention_run_ids == ("failure",)


def test_graph_detects_selected_cycles(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "root", "a")
    publish(store, "child", "b", inputs=("a",))
    with store._write() as conn:
        conn.execute("INSERT INTO analysis_action_run_inputs VALUES('root','session',0,'b')")
    with pytest.raises(SessionGraphIntegrityError, match="cycle"):
        graph(store, "session")


def test_focused_graph_skips_unrelated_corrupt_body_and_uses_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "other", "unrelated")
    publish(store, "root", "a")
    with store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload='broken' WHERE artifact_ref='unrelated'"
        )
    selected = graph(store, "session", artifact_ref=ArtifactRef(ref="a"))
    assert [run.run_id for run in selected.runs] == ["root"]
    from marivo.analysis.session import _lazy_graph

    monkeypatch.setattr(_lazy_graph, "OVERALL_GRAPH_SCAN_LIMIT", 1)
    with pytest.raises(SessionGraphTooLargeError):
        graph(store, "session")
    assert graph(store, "session", artifact_ref=ArtifactRef(ref="a")) == selected
    with store._read() as conn:
        plans = conn.execute(
            "EXPLAIN QUERY PLAN SELECT run_ref FROM analysis_action_run_inputs WHERE artifact_ref=?",
            ("a",),
        ).fetchall()
        assert any("run_inputs_artifact" in str(tuple(row)) for row in plans)


def test_focus_order_prefers_nearest_nodes_and_no_findings_are_scanned(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "root", "a")
    publish(store, "child", "b", inputs=("a",))
    publish(store, "grandchild", "c", inputs=("b",))
    with store._write() as conn:
        conn.execute("INSERT INTO findings VALUES('bad','a',0,'bad','broken')")
    selected = graph(
        store, "session", artifact_ref=ArtifactRef(ref="a"), direction="descendants", max_nodes=3
    )
    assert [item.artifact_ref.ref for item in selected.artifacts] == ["a", "b"]
    assert [run.run_id for run in selected.runs] == ["child"]
    assert selected.boundary_artifact_refs == (ArtifactRef(ref="b"),)
    assert selected.truncated
