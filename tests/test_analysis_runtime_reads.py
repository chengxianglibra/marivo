from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.session._read_errors import ArtifactNotFoundError, RunNotFoundError
from marivo.analysis.session._read_model import FailedRun, IncompleteRun, SucceededRun
from marivo.analysis.session._runtime_reads import SessionRuntimeReads
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.runtime_read_fixtures import RuntimeReadHarness
from tests.shared_fixtures import (
    bootstrap_sales_project_from_template,
    connect_sales_orders,
    sales_backends,
)


def test_empty_run_page_and_recap_are_bounded(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    reads = SessionRuntimeReads(harness.session)  # type: ignore[arg-type]

    page = reads.runs()
    recap = reads.recap()

    assert page.items == ()
    assert page.has_more is False
    assert recap.artifact_count == 0
    assert recap.head_artifact_count == 0
    assert recap.head_artifact_refs == ()
    assert recap.overall_graph_available is True
    assert len(recap.render().encode()) <= 8192


def test_run_paging_filters_and_exact_closed_variants(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_1", "artifact_1")
    harness.begin_run("run_2", capability_id="compare", inputs=("artifact_1",))
    harness.begin_run("run_3", capability_id="attribute", inputs=("artifact_1",))
    harness.fail("run_3")
    reads = SessionRuntimeReads(harness.session)  # type: ignore[arg-type]

    first = reads.runs(limit=2)
    second = reads.runs(limit=2, cursor=first.next_cursor)

    assert tuple(run.run_id for run in first.items) == ("run_3", "run_2")
    assert first.has_more is True
    assert tuple(run.run_id for run in second.items) == ("run_1",)
    assert isinstance(reads.get_run("run_1"), SucceededRun)
    assert isinstance(reads.get_run("run_2"), IncompleteRun)
    assert isinstance(reads.get_run("run_3"), FailedRun)
    assert tuple(run.run_id for run in reads.runs(status="failed").items) == ("run_3",)
    assert tuple(run.run_id for run in reads.runs(capability_id="compare").items) == ("run_2",)


def test_recap_keeps_exact_head_count_with_bounded_ref_preview(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    for index in range(5):
        harness.produced(f"run_{index}", f"artifact_{index}")

    recap = SessionRuntimeReads(harness.session).recap()  # type: ignore[arg-type]

    assert recap.artifact_count == 5
    assert recap.head_artifact_count == 5
    assert len(recap.head_artifact_refs) == 3


def test_run_argument_projection_is_typed_immutable_and_deterministic(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.begin_run(
        "run_args",
        arguments=[
            {"name": "alpha", "value": {"path": "sales.revenue"}},
            {"name": "limit", "value": 20},
        ],
    )
    run = SessionRuntimeReads(harness.session).get_run("run_args")  # type: ignore[arg-type]

    assert tuple(argument.name for argument in run.arguments) == ("alpha", "limit")
    assert repr(run) == repr(run)
    assert len(run.render().encode()) <= 8192
    with pytest.raises(FrozenInstanceError):
        run.run_id = "changed"  # type: ignore[misc]


def test_failed_run_rehydrates_typed_repair(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.begin_run("run_failed")
    harness.fail(
        "run_failed",
        repair={
            "kind": "retry",
            "action": "Retry with a bounded input.",
            "help_target": {"surface": "analysis", "canonical_id": "observe"},
            "snippet": "session.observe(metric_ref)",
            "candidates": [],
        },
    )

    run = SessionRuntimeReads(harness.session).get_run("run_failed")  # type: ignore[arg-type]

    assert isinstance(run, FailedRun)
    assert isinstance(run.failure.repair, AnalysisRepair)
    assert run.failure.repair.action == "Retry with a bounded input."
    assert run.failure.repair.help_target.canonical_id == "observe"


def test_unknown_exact_runtime_identities_raise_candidate_errors(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    reads = SessionRuntimeReads(harness.session)  # type: ignore[arg-type]

    with pytest.raises(RunNotFoundError) as run_error:
        reads.get_run("run_missing")
    with pytest.raises(ArtifactNotFoundError) as artifact_error:
        reads.artifact("artifact_missing")

    assert run_error.value.expected
    assert run_error.value.received == "run_missing"
    assert run_error.value.location == "session.get_run(run_id)"
    assert artifact_error.value.repair is not None


def test_exact_artifact_cold_recovery_and_ref_revalidation(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project_from_template(tmp_path)
    connection = connect_sales_orders()
    try:
        session = mv.session.get_or_create(
            name="slice2-runtime-reads",
            backends=sales_backends(connection),
        )
        frame = session.observe(make_ref("sales.revenue", SemanticKind.METRIC))
        reads = SessionRuntimeReads(session)

        recovered = reads.artifact(frame.ref)
        revalidation = reads.revalidate(frame.ref)

        assert type(recovered) is type(frame)
        assert recovered.ref == frame.ref
        assert recovered.meta.artifact_schema_version == "analysis-artifact/v13"
        assert revalidation.artifact_ref == frame.ref
    finally:
        connection.disconnect()
        session_attach._reset_process_state()


def test_slice2_candidate_surface_remains_private() -> None:
    assert not hasattr(mv, "SessionGraph")
    assert not hasattr(mv, "RunPage")
    assert not hasattr(mv.Session, "graph")
    assert not hasattr(mv.Session, "runs")
