from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.runtime_read_fixtures import RuntimeReadHarness
from tests.shared_fixtures import (
    bootstrap_sales_project_from_template,
    connect_sales_orders,
    sales_backends,
)


def _query_payload(
    query_id: str,
    *,
    status: str = "succeeded",
    sql: str = "SELECT 'sensitive-literal'",
) -> dict[str, object]:
    return {
        "query_id": query_id,
        "datasource": "warehouse",
        "dialect": "duckdb",
        "sql": sql,
        "sql_digest": "0123456789abcdef",
        "row_count": 1 if status == "succeeded" else 0,
        "duration_ms": 7,
        "started_at": "2026-08-30T00:00:00+00:00",
        "finished_at": "2026-08-30T00:00:00.007000+00:00",
        "status": status,
    }


def test_empty_run_page_is_bounded(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    reads = harness.session

    page = reads.runs()

    assert page.items == ()
    assert page.has_more is False


def test_run_paging_filters_and_exact_closed_variants(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_1", "artifact_1")
    harness.begin_run("run_2", capability_id="compare", inputs=("artifact_1",))
    harness.begin_run("run_3", capability_id="attribute", inputs=("artifact_1",))
    harness.fail("run_3")
    reads = harness.session

    first = reads.runs(limit=2)
    second = reads.runs(limit=2, cursor=first.next_cursor)

    assert tuple(run.run_id for run in first.items) == ("run_3", "run_2")
    assert first.has_more is True
    assert tuple(run.run_id for run in second.items) == ("run_1",)
    assert isinstance(reads.get_run("run_1"), mv.SucceededRun)
    assert isinstance(reads.get_run("run_2"), mv.IncompleteRun)
    assert isinstance(reads.get_run("run_3"), mv.FailedRun)
    assert tuple(run.run_id for run in reads.runs(status="failed").items) == ("run_3",)
    assert tuple(run.run_id for run in reads.runs(capability_id="compare").items) == ("run_2",)


def test_run_argument_projection_is_typed_immutable_and_deterministic(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.begin_run(
        "run_args",
        arguments=[
            {"name": "alpha", "value": {"path": "sales.revenue"}},
            {"name": "limit", "value": 20},
        ],
    )
    run = harness.session.get_run("run_args")

    assert tuple(argument.name for argument in run.arguments) == ("alpha", "limit")
    assert repr(run) == repr(run)
    assert len(run.render().encode()) <= 8192
    with pytest.raises(FrozenInstanceError):
        run.run_id = "changed"  # type: ignore[misc]


def test_terminal_runs_rehydrate_ordered_queries_without_rendering_sql(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.begin_run("run_succeeded")
    harness.add_artifact("artifact_1", producer="run_succeeded")
    harness.succeed(
        "run_succeeded",
        "artifact_1",
        queries=[
            _query_payload("query_00000001"),
            _query_payload("query_00000002", sql="SELECT 2"),
        ],
    )
    harness.begin_run("run_failed")
    harness.fail(
        "run_failed",
        queries=[_query_payload("query_00000003", status="failed")],
    )
    harness.begin_run("run_incomplete")

    reopened = RuntimeReadHarness.create(tmp_path)
    succeeded = reopened.session.get_run("run_succeeded")
    failed = reopened.session.get_run("run_failed")
    incomplete = reopened.session.get_run("run_incomplete")

    assert isinstance(succeeded, mv.SucceededRun)
    assert tuple(query.query_id for query in succeeded.queries) == (
        "query_00000001",
        "query_00000002",
    )
    assert succeeded.queries[0].sql == "SELECT 'sensitive-literal'"
    assert succeeded.queries[0].started_at.tzinfo is not None
    assert "sensitive-literal" not in succeeded.render()
    assert "query_count: 2" in succeeded.render()
    assert "dialect=" not in succeeded.render()
    assert ".queries" in succeeded.render()
    assert isinstance(failed, mv.FailedRun)
    assert failed.queries[0].status == "failed"
    assert not hasattr(incomplete, "queries")
    with pytest.raises(FrozenInstanceError):
        succeeded.queries[0].sql = "changed"  # type: ignore[misc]


def test_corrupt_run_query_payload_fails_closed(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_query", "artifact_query")
    duplicate = _query_payload("query_00000001")
    with sqlite3.connect(harness.store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET queries_json = ? WHERE run_id = 'run_query'",
            (json.dumps([duplicate, duplicate]),),
        )

    with pytest.raises(mv.errors.SessionGraphIntegrityError, match="duplicated"):
        harness.session.get_run("run_query")


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("missing", None),
        ("extra", None),
        ("status", "cancelled"),
        ("status", []),
        ("row_count", -1),
        ("duration_ms", -1),
        ("started_at", "2026-08-30T00:00:00"),
        ("finished_at", "2026-08-29T00:00:00+00:00"),
        ("sql_digest", "not-a-digest"),
    ),
)
def test_corrupt_run_query_fields_fail_closed(tmp_path, mutation, value) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_query", "artifact_query")
    query = _query_payload("query_00000001")
    if mutation == "missing":
        query.pop("sql")
    elif mutation == "extra":
        query["normalized_sql"] = "SELECT ?"
    else:
        query[mutation] = value
    with sqlite3.connect(harness.store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET queries_json = ? WHERE run_id = 'run_query'",
            (json.dumps([query]),),
        )

    with pytest.raises(mv.errors.SessionGraphIntegrityError) as exc_info:
        harness.session.get_run("run_query")
    assert "sensitive-literal" not in str(exc_info.value)


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

    run = harness.session.get_run("run_failed")

    assert isinstance(run, mv.FailedRun)
    assert isinstance(run.failure.repair, mv.errors.AnalysisRepair)
    assert run.failure.repair.action == "Retry with a bounded input."
    assert run.failure.repair.help_target.canonical_id == "observe"


def test_unknown_exact_runtime_identities_raise_candidate_errors(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    reads = harness.session

    with pytest.raises(mv.errors.RunNotFoundError) as run_error:
        reads.get_run("run_missing")
    with pytest.raises(mv.errors.ArtifactNotFoundError) as artifact_error:
        reads.artifact("artifact_missing")

    assert run_error.value.expected
    assert run_error.value.received == "run_missing"
    assert run_error.value.location == "session.get_run(run_id)"
    assert artifact_error.value.repair is not None


@pytest.mark.parametrize(
    "read",
    (
        lambda session: session.runs(status="succeeded"),
        lambda session: session.get_run("run_good"),
        lambda session: session.artifact("artifact_good"),
        lambda session: session.revalidate("artifact_good"),
        lambda session: session.graph(
            artifact_ref="artifact_good",
            direction="ancestors",
        ),
    ),
)
def test_every_public_runtime_read_rejects_hidden_incompatible_run(tmp_path, read) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    harness.produced("run_good", "artifact_good")
    harness.begin_run("run_hidden")
    with sqlite3.connect(harness.store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET payload_schema = 'marivo.analysis_run/v0' "
            "WHERE session_id = ? AND run_id = 'run_hidden'",
            (harness.session_id,),
        )

    with pytest.raises(mv.errors.SchemaVersionMismatchError) as exc_info:
        read(harness.session)

    assert exc_info.value.expected == "marivo.analysis_run/v2"
    assert exc_info.value.received == "marivo.analysis_run/v0"
    assert exc_info.value.location is not None
    assert exc_info.value.repair is not None


def test_public_runtime_read_rechecks_store_schema_after_activation(tmp_path) -> None:
    harness = RuntimeReadHarness.create(tmp_path)
    with sqlite3.connect(harness.store.db_path) as connection:
        connection.execute("PRAGMA user_version=0")

    with pytest.raises(mv.errors.SchemaVersionMismatchError) as exc_info:
        harness.session.runs()

    assert exc_info.value.expected == "Session Store user_version=2"
    assert exc_info.value.received == "Session Store user_version=0"


def test_exact_artifact_cold_recovery_and_ref_revalidation(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bootstrap_sales_project_from_template(tmp_path)
    connection = connect_sales_orders()
    try:
        session = mv.session.get_or_create(
            name="slice2-runtime-reads",
            backends=sales_backends(connection),
        )
        frame = session.observe(ms.ref.metric("sales.revenue"))
        recovered = session.artifact(frame.ref)
        revalidation = session.revalidate(frame.ref)

        assert type(recovered) is type(frame)
        assert recovered.ref == frame.ref
        assert recovered.meta.artifact_schema_version == "analysis-artifact/v13"
        assert revalidation.artifact_ref == frame.ref
    finally:
        connection.disconnect()


def test_slice3_runtime_surface_is_public() -> None:
    assert hasattr(mv, "SessionGraph")
    assert hasattr(mv, "RunPage")
    assert hasattr(mv.Session, "graph")
    assert hasattr(mv.Session, "runs")
    assert hasattr(mv.Session, "artifact")
    assert hasattr(mv.Session, "get_run")
    for stale in ("jobs", "recent_jobs", "job", "frame_summaries", "get_frame", "evidence"):
        assert not hasattr(mv.Session, stale)
