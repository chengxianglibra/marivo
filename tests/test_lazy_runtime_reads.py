"""v3 read projections select only their actual metadata dependencies."""

import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from marivo.analysis.datasets.descriptors import DatasetShapeId
from marivo.analysis.errors import RunNotFoundError, SessionStateError
from marivo.analysis.materialization.contracts import (
    RunRecord as StoredRunRecord,
)
from marivo.analysis.materialization.contracts import (
    canonical_json,
    decode_failure,
    failure_payload,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session._lazy_read_model import (
    FailedRun,
    IncompleteRun,
    RunLifecycle,
    SucceededRun,
)
from marivo.analysis.session._lazy_runtime_reads import artifact_summary, get_run, runs
from tests.lazy_runtime_read_fixtures import failure, input_value, publish


def test_concrete_run_variants_preserve_typed_values_and_closed_lifecycle(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "succeeded", "artifact")
    store.admit("session", "failed-key", input_value(), run_ref="failed")
    store.fail("failed", failure())
    store.admit("session", "pending-key", input_value(), run_ref="pending")
    page = runs(store, "session")
    assert tuple(type(value) for value in page.items) == (IncompleteRun, FailedRun, SucceededRun)
    assert all(type(value.dataset_input.shape_id) is DatasetShapeId for value in page.items)
    assert not hasattr(page.items[0], "finished_at")
    assert not hasattr(page.items[0], "capability_id")
    assert not hasattr(page.items[2], "output_mode")
    assert isinstance(page.items[1], FailedRun) and page.items[1].failure == failure()
    assert decode_failure(canonical_json(failure_payload(failure()))) == failure()
    assert all(".show()" in repr(value) and "\n" not in repr(value) for value in page.items)
    identity_field = "run_id"
    with pytest.raises(FrozenInstanceError):
        setattr(page.items[0], identity_field, "changed")
    statuses: tuple[RunLifecycle, ...] = ("incomplete", "succeeded", "failed")
    for status, expected in zip(statuses, (IncompleteRun, SucceededRun, FailedRun), strict=True):
        assert type(runs(store, "session", status=status).items[0]) is expected


def test_run_page_sentinel_and_foreign_body_are_not_decoded(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "older", "old")
    publish(store, "newer", "new")
    store.create_session("foreign", session_ref="foreign")
    store.admit("foreign", "foreign-key", input_value(), run_ref="foreign-run")
    with store._write() as conn:
        conn.execute(
            "UPDATE analysis_action_runs SET dataset_input_payload='broken' WHERE run_ref IN ('older','foreign-run')"
        )
    first = runs(store, "session", limit=1)
    assert first.has_more and first.items[0].run_id == "newer"
    with pytest.raises(IntegrityError):
        runs(store, "session", limit=1, cursor=first.next_cursor)
    with pytest.raises(RunNotFoundError):
        get_run(store, "session", "foreign-run")


def test_selected_runtime_reads_ignore_unrelated_finding_corruption(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "run", "artifact")
    with store._write() as conn:
        conn.execute("INSERT INTO findings VALUES('bad','artifact',0,'identity','broken')")
    assert artifact_summary(store, "artifact").evidence.finding_count == 0
    assert get_run(store, "session", "run").run_id == "run"


def test_run_page_uses_one_snapshot_during_concurrent_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "first", "one")
    original = store._run
    published = False

    def racing(conn: sqlite3.Connection, ref: str) -> StoredRunRecord | None:
        nonlocal published
        value = original(conn, ref)
        if not published:
            published = True
            other = SessionStore(tmp_path)
            publish(other, "second", "two")
        return value

    monkeypatch.setattr(store, "_run", racing)
    page = runs(store, "session")
    assert [item.run_id for item in page.items] == ["first"]
    assert [item.run_id for item in runs(SessionStore(tmp_path), "session")] == ["second", "first"]


def test_read_survives_writer_transaction_and_does_not_mutate_state(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "run", "artifact")
    before = store.session("session")
    with store._write() as conn:
        conn.execute("UPDATE sessions SET question='uncommitted' WHERE session_ref='session'")
        assert get_run(store, "session", "run").run_id == "run"
        assert store.session("session") == before
    assert artifact_summary(store, "artifact").artifact_session_ref == "session"


@pytest.mark.parametrize("limit", [True, 0, 101, -1])
def test_pages_reject_invalid_limits(tmp_path: Path, limit: int) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    with pytest.raises(SessionStateError):
        runs(store, "session", limit=limit)


def test_artifact_issue_counts_use_declared_severity(tmp_path: Path) -> None:
    from dataclasses import replace

    from marivo.analysis.materialization.contracts import MaterializationIssue
    from tests.lazy_materialization_fixtures import descriptor

    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    value = replace(
        descriptor(),
        typed_issues=(
            MaterializationIssue("warning", "test_warning", "expected", "received", "inspect"),
            MaterializationIssue("blocking", "test_blocking", "expected", "received", "inspect"),
        ),
    )
    store.admit("session", "issue-key", input_value(value), run_ref="run")
    store.publish("run", "artifact", value)
    summary = artifact_summary(store, "artifact")
    assert summary.issue_counts.warning == 1 and summary.issue_counts.blocking == 1


def test_runtime_values_reject_unknown_variants_and_mutable_input_refs(tmp_path: Path) -> None:
    from dataclasses import replace

    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    store.admit("session", "pending-key", input_value(), run_ref="pending")
    value = get_run(store, "session", "pending")
    corrupted = replace(value)
    object.__setattr__(corrupted, "input_artifact_refs", [])
    with pytest.raises(IntegrityError):
        corrupted.__post_init__()
    with pytest.raises(IntegrityError):
        replace(value, admitted_at=value.admitted_at.replace(tzinfo=None))
    payload = failure_payload(failure())
    payload["phase"] = "unknown"
    with pytest.raises(IntegrityError):
        decode_failure(canonical_json(payload))
    assert ".show()" in repr(value.dataset_input)
    assert ".show()" in repr(failure())


def test_failure_json_projections_are_isolated_from_constructor_and_readers() -> None:
    from marivo.analysis.materialization.contracts import JsonValue, RunFailure

    authored: dict[str, JsonValue] = {"nested": [1, {"count": 2}]}
    value = RunFailure(
        "stage_execution", "execution_failed", "Safe message.", None, authored, authored, None
    )
    encoded = canonical_json(failure_payload(value))
    authored["nested"] = "changed"
    expected = value.expected
    assert isinstance(expected, dict)
    nested = expected["nested"]
    assert isinstance(nested, list)
    nested.append(3)
    received = value.received
    assert isinstance(received, dict)
    received.clear()
    assert canonical_json(failure_payload(value)) == encoded
    assert decode_failure(encoded) == value


@pytest.mark.parametrize(
    ("field", "unregistered"), [("kind", "unknown"), ("backend_class", "duckdb")]
)
def test_failure_codec_rejects_unregistered_failures(field: str, unregistered: str) -> None:
    payload = failure_payload(failure())
    payload[field] = unregistered
    with pytest.raises(IntegrityError, match="unregistered"):
        decode_failure(canonical_json(payload))


@pytest.mark.parametrize(
    "shape",
    [
        "population/entity-membership@v1",
        {"family_id": "unknown", "local_shape_id": "unknown", "semantic_version": 1},
    ],
)
def test_run_input_codec_rejects_legacy_and_unregistered_shapes(shape: object) -> None:
    from marivo.analysis.materialization.contracts import decode_run_input, run_input_payload

    payload = run_input_payload(input_value())
    payload["shape_id"] = shape
    with pytest.raises(IntegrityError):
        decode_run_input(canonical_json(payload))


@pytest.mark.parametrize("corrupt", ["descriptor", "evidence"])
def test_run_and_focused_graph_do_not_decode_unselected_output_bodies(
    tmp_path: Path, corrupt: str
) -> None:
    from marivo.analysis.refs import ArtifactRef
    from marivo.analysis.session._lazy_graph import graph

    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "root", "a")
    publish(store, "child", "b", inputs=("a",))
    with store._write() as conn:
        if corrupt == "descriptor":
            conn.execute(
                "UPDATE dataset_artifacts SET descriptor_payload='broken' WHERE artifact_ref='b'"
            )
        else:
            conn.execute(
                "UPDATE dataset_evidence SET evidence_digest='broken' WHERE artifact_ref='b'"
            )
    assert isinstance(get_run(store, "session", "child"), SucceededRun)
    assert {run.run_id for run in runs(store, "session")} == {"root", "child"}
    selected = graph(
        store, "session", artifact_ref=ArtifactRef(ref="a"), direction="descendants", max_nodes=2
    )
    assert [run.run_id for run in selected.runs] == ["child"]
    assert selected.boundary_run_ids == ("child",) and selected.truncated
    with pytest.raises(IntegrityError):
        artifact_summary(store, "b")
    with pytest.raises(IntegrityError):
        graph(store, "session", artifact_ref=ArtifactRef(ref="b"), max_nodes=1)


def test_run_still_validates_normalized_output_identity_when_body_is_omitted(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "run", "artifact")
    with store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET execution_key_digest='different' WHERE artifact_ref='artifact'"
        )
    with pytest.raises(IntegrityError, match="identity"):
        get_run(store, "session", "run")


def _assert_safe_read_argument(error: SessionStateError, *, location: str, canary: str) -> None:
    assert error.expected and error.received and error.repair is not None
    assert error.location == location and error.repair.kind == "retry"
    assert error.repair.help_target.canonical_id == "runtime.runs"
    assert "next_cursor" in error.repair.action
    assert error.__cause__ is None and error.__context__ is None
    rendered = str(error)
    assert len(rendered.encode()) < 2048 and canary not in rendered


def test_invalid_run_limits_fail_safely_before_opening_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._compat import Never

    store = SessionStore(tmp_path)

    def forbidden() -> Never:
        pytest.fail("Invalid read arguments must fail before opening the Store")

    monkeypatch.setattr(store, "_read", forbidden)
    for limit in (True, 0, 101, -(10**10000), 10**10000):
        with pytest.raises(SessionStateError) as caught:
            runs(store, "session", limit=limit)
        _assert_safe_read_argument(
            caught.value, location="session.runs.limit", canary="untrusted limit"
        )


@pytest.mark.parametrize("status", ["unknown-secret-status", True, ["failed"]])
def test_invalid_run_status_is_safe_and_does_not_open_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: RunLifecycle
) -> None:
    from marivo._compat import Never

    store = SessionStore(tmp_path)

    def forbidden() -> Never:
        pytest.fail("Invalid status must fail before opening the Store")

    monkeypatch.setattr(store, "_read", forbidden)
    with pytest.raises(SessionStateError) as caught:
        runs(store, "session", status=status)
    _assert_safe_read_argument(
        caught.value, location="session.runs.status", canary="unknown-secret-status"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "malformed",
        "oversized",
        "deep_json",
        "wrong_key",
        "naive",
        "surrogate",
        "noncanonical",
    ],
)
def test_invalid_run_cursor_is_bounded_safe_and_does_not_open_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import base64

    from marivo._compat import Never
    from marivo.analysis._pages import encode_keyset_cursor

    canary = "cursor-secret-marker"
    timestamp = "2026-09-08T12:00:00.000000Z"
    cursors = {
        "empty": "",
        "malformed": canary,
        "oversized": canary + "x" * 16_384,
        "deep_json": base64.urlsafe_b64encode(("[" * 1300 + "0" + "]" * 1300).encode()).decode(),
        "wrong_key": encode_keyset_cursor(3, canary),
        "naive": encode_keyset_cursor("2026-09-08T12:00:00", canary),
        "surrogate": encode_keyset_cursor(timestamp, "\ud800"),
        "noncanonical": encode_keyset_cursor(timestamp, canary) + "=",
    }
    cursor = cursors[kind]
    store = SessionStore(tmp_path)

    def forbidden() -> Never:
        pytest.fail("Invalid cursor must fail before opening the Store")

    monkeypatch.setattr(store, "_read", forbidden)
    with pytest.raises(SessionStateError) as caught:
        runs(store, "session", cursor=cursor)
    _assert_safe_read_argument(caught.value, location="session.runs.cursor", canary=canary)
    if cursor:
        assert cursor not in str(caught.value)
