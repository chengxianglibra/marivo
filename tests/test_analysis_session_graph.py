from __future__ import annotations

import importlib
import json

import pytest

import marivo.analysis as mv
from tests.runtime_read_fixtures import RuntimeReadHarness


def _linear(harness: RuntimeReadHarness) -> None:
    harness.produced("run_observe", "artifact_metric")
    harness.produced(
        "run_compare",
        "artifact_delta",
        capability_id="compare",
        inputs=("artifact_metric",),
    )
    harness.produced(
        "run_attribute",
        "artifact_attribution",
        capability_id="attribute",
        inputs=("artifact_delta",),
    )


def test_empty_and_linear_graph_are_deterministic(tmp_path) -> None:
    empty = RuntimeReadHarness.create(tmp_path / "empty")
    assert empty.session.graph().artifacts == ()

    harness = RuntimeReadHarness.create(tmp_path / "linear")
    _linear(harness)
    reads = harness.session

    first = reads.graph()
    second = reads.graph()

    assert first == second
    assert first.root_run_ids == ("run_observe",)
    assert first.head_artifact_refs == ("artifact_attribution",)
    assert len(first.runs) == 3
    assert len(first.artifacts) == 3
    assert len(first.edges) == 5
    assert "source freshness is not checked" in first.render()


def test_branch_merge_and_reuse_edges_preserve_runtime_truth(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")
    harness.produced(
        "run_left",
        "artifact_left",
        capability_id="compare",
        inputs=("artifact_root",),
    )
    harness.produced(
        "run_right",
        "artifact_right",
        capability_id="compare",
        inputs=("artifact_root",),
    )
    harness.produced(
        "run_merge",
        "artifact_merge",
        capability_id="compare",
        inputs=("artifact_left", "artifact_right"),
    )
    harness.begin_run("run_reuse")
    harness.succeed("run_reuse", "artifact_root", output_mode="reused")

    graph = harness.session.graph()

    assert graph.head_artifact_refs == ("artifact_merge",)
    assert any(edge.kind == "reuses" and edge.run_id == "run_reuse" for edge in graph.edges)
    assert sum(edge.kind == "consumes" for edge in graph.edges) == 4
    root = next(item for item in graph.artifacts if item.ref == "artifact_root")
    assert root.produced_by_run == "run_root"


def test_failed_and_incomplete_consumers_do_not_remove_materialized_head(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")
    harness.begin_run("run_failed", capability_id="compare", inputs=("artifact_root",))
    harness.fail("run_failed")
    harness.begin_run("run_incomplete", capability_id="compare", inputs=("artifact_root",))

    graph = harness.session.graph()

    assert graph.head_artifact_refs == ("artifact_root",)
    assert graph.failed_run_ids == ("run_failed",)
    assert graph.incomplete_run_ids == ("run_incomplete",)
    assert any(isinstance(run, mv.FailedRun) for run in graph.runs)
    assert any(isinstance(run, mv.IncompleteRun) for run in graph.runs)


def test_focused_ancestor_and_descendant_reads_use_index_without_overall_scan(
    tmp_path, monkeypatch
) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    _linear(harness)
    harness.begin_run(
        "run_failed_downstream",
        capability_id="compare",
        inputs=("artifact_metric",),
    )
    harness.fail("run_failed_downstream")

    def reject_overall(*_args, **_kwargs):
        raise AssertionError("focused graph must not scan the overall runtime")

    monkeypatch.setattr(harness.store, "runtime_snapshot", reject_overall)
    reads = harness.session

    ancestors = reads.graph(artifact_ref="artifact_attribution", direction="ancestors")
    descendants = reads.graph(artifact_ref="artifact_metric", direction="descendants")

    assert tuple(item.ref for item in ancestors.artifacts) == (
        "artifact_metric",
        "artifact_delta",
        "artifact_attribution",
    )
    assert "run_failed_downstream" in descendants.failed_run_ids
    assert {item.ref for item in descendants.artifacts} == {
        "artifact_metric",
        "artifact_delta",
        "artifact_attribution",
    }


def test_focused_truncation_retains_focus_and_reports_boundary(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    _linear(harness)

    graph = harness.session.graph(
        artifact_ref="artifact_attribution",
        direction="ancestors",
        max_nodes=1,
    )

    assert tuple(item.ref for item in graph.artifacts) == ("artifact_attribution",)
    assert graph.truncated is True
    assert graph.boundary_artifact_refs == ("artifact_attribution",)


def test_overall_attention_first_truncation_has_closed_edges(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    _linear(harness)
    harness.begin_run("run_failed", inputs=("artifact_metric",))
    harness.fail("run_failed")

    graph = harness.session.graph(max_nodes=2)
    selected_runs = {run.run_id for run in graph.runs}
    selected_artifacts = {artifact.ref for artifact in graph.artifacts}

    assert "run_failed" in selected_runs
    assert graph.truncated is True
    assert all(edge.run_id in selected_runs for edge in graph.edges)
    assert all(edge.artifact_ref in selected_artifacts for edge in graph.edges)
    assert graph.boundary_run_ids or graph.boundary_artifact_refs


def test_sidecars_are_excluded_from_graph_artifacts(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")
    harness.add_artifact(
        "artifact_component",
        producer="run_root",
        kind="coverage_frame",
        inputs=("artifact_root",),
    )

    graph = harness.session.graph()

    assert tuple(item.ref for item in graph.artifacts) == ("artifact_root",)


def test_graph_fails_closed_on_metadata_mismatch_and_cycle(tmp_path) -> None:
    mismatch = RuntimeReadHarness.create(tmp_path / "mismatch")
    mismatch.produced("run_root", "artifact_root")
    row = mismatch.store.get_artifact(mismatch.session_id, "artifact_root")
    assert row is not None
    meta_path = mismatch.project_root / str(row["meta_path"])
    payload = json.loads(meta_path.read_text())
    payload["content_hash"] = "sha256:wrong"
    meta_path.write_text(json.dumps(payload))
    with pytest.raises(mv.errors.SessionGraphIntegrityError, match="disagree"):
        mismatch.session.graph()

    cyclic = RuntimeReadHarness.create(tmp_path / "cycle")
    cyclic.produced("run_1", "artifact_1")
    cyclic.produced(
        "run_2",
        "artifact_2",
        capability_id="compare",
        inputs=("artifact_1",),
    )
    with cyclic.store._connect() as connection:
        connection.execute(
            "INSERT INTO run_inputs (session_id, run_id, artifact_ref, position) "
            "VALUES (?, 'run_1', 'artifact_2', 0)",
            (cyclic.session_id,),
        )
    row = cyclic.store.get_artifact(cyclic.session_id, "artifact_1")
    assert row is not None
    meta_path = cyclic.project_root / str(row["meta_path"])
    payload = json.loads(meta_path.read_text())
    payload["lineage"]["steps"][-1]["inputs"] = ["artifact_2"]
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(mv.errors.SessionGraphIntegrityError, match="cycle"):
        cyclic.session.graph()


def test_graph_rejects_metadata_that_fails_its_concrete_v13_model(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")
    row = harness.store.get_artifact(harness.session_id, "artifact_root")
    assert row is not None
    meta_path = harness.project_root / str(row["meta_path"])
    payload = json.loads(meta_path.read_text())
    del payload["catalog_definition_fingerprint"]
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(mv.errors.FrameMetaInvalidError, match="concrete v13 metadata"):
        harness.session.graph()


def test_focused_graph_fails_closed_on_missing_indexed_input(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")
    harness.produced(
        "run_child",
        "artifact_child",
        capability_id="compare",
        inputs=("artifact_root",),
    )
    with harness.store._connect() as connection:
        connection.execute(
            "UPDATE run_inputs SET artifact_ref = 'artifact_missing' "
            "WHERE session_id = ? AND run_id = 'run_child'",
            (harness.session_id,),
        )

    with pytest.raises(mv.errors.SessionGraphIntegrityError, match="missing canonical records"):
        harness.session.graph(
            artifact_ref="artifact_child",
            direction="ancestors",
        )


def test_descendant_graph_validates_unselected_co_inputs(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_left", "artifact_left")
    harness.produced("run_right", "artifact_right")
    harness.produced(
        "run_merge",
        "artifact_merge",
        capability_id="compare",
        inputs=("artifact_left", "artifact_right"),
    )
    with harness.store._connect() as connection:
        connection.execute(
            "UPDATE run_inputs SET artifact_ref = 'artifact_missing' "
            "WHERE session_id = ? AND run_id = 'run_merge' "
            "AND artifact_ref = 'artifact_right'",
            (harness.session_id,),
        )
    row = harness.store.get_artifact(harness.session_id, "artifact_merge")
    assert row is not None
    meta_path = harness.project_root / str(row["meta_path"])
    payload = json.loads(meta_path.read_text())
    payload["lineage"]["steps"][-1]["inputs"] = ["artifact_left", "artifact_missing"]
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(mv.errors.SessionGraphIntegrityError, match="missing canonical records"):
        harness.session.graph(
            artifact_ref="artifact_left",
            direction="descendants",
        )


def test_focused_selection_orders_same_distance_by_timestamp_then_id(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")
    harness.begin_run(
        "run_z_older",
        inputs=("artifact_root",),
        started_at="2026-08-30T00:01:00+00:00",
    )
    harness.fail("run_z_older")
    harness.begin_run(
        "run_a_newer",
        inputs=("artifact_root",),
        started_at="2026-08-30T00:02:00+00:00",
    )
    harness.fail("run_a_newer")

    graph = harness.session.graph(
        artifact_ref="artifact_root",
        direction="descendants",
        max_nodes=2,
    )

    assert tuple(run.run_id for run in graph.runs) == ("run_z_older",)
    assert graph.boundary_artifact_refs == ("artifact_root",)


def test_graph_purity_does_not_load_artifacts_evidence_or_runtime_services(
    tmp_path, monkeypatch
) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden graph dependency")

    monkeypatch.setattr(
        importlib.import_module("marivo.analysis.session._load"), "load_frame", forbidden
    )
    monkeypatch.setattr(
        importlib.import_module("marivo.analysis.evidence.store"),
        "open_evidence_store",
        forbidden,
    )
    monkeypatch.setattr(
        importlib.import_module("marivo.analysis._artifact_revalidation"),
        "evaluate_artifact_revalidation",
        forbidden,
    )
    monkeypatch.setattr(
        importlib.import_module("marivo.analysis.frames._quality"),
        "evaluate_frame_quality",
        forbidden,
    )

    graph = harness.session.graph()
    assert tuple(item.ref for item in graph.artifacts) == ("artifact_root",)


def test_graph_snapshot_is_stable_across_concurrent_terminal_transition(
    tmp_path, monkeypatch
) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_root", "artifact_root")
    harness.begin_run("run_pending", inputs=("artifact_root",))
    runtime_reads = importlib.import_module("marivo.analysis.session._runtime_reads")
    original = runtime_reads._read_artifact_facts
    transitioned = False

    def transition_after_snapshot(*args, **kwargs):
        nonlocal transitioned
        if not transitioned:
            transitioned = True
            harness.fail("run_pending")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_reads, "_read_artifact_facts", transition_after_snapshot)

    graph = harness.session.graph()

    assert graph.incomplete_run_ids == ("run_pending",)
    assert isinstance(harness.session.get_run("run_pending"), mv.FailedRun)


@pytest.mark.parametrize("max_nodes", [0, -1, 501, True])
def test_graph_rejects_invalid_bounds(tmp_path, max_nodes) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    with pytest.raises(mv.errors.SessionGraphLimitError):
        harness.session.graph(max_nodes=max_nodes)


def test_focused_graph_unknown_artifact_is_typed_not_found(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    with pytest.raises(mv.errors.ArtifactNotFoundError):
        harness.session.graph(
            artifact_ref="missing",
            direction="ancestors",
        )
