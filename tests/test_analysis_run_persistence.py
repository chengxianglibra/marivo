from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from marivo._compat import UTC
from marivo.analysis.errors import AnalysisError, SessionStateError
from marivo.analysis.session._runs import admit_run, project_run_arguments, project_run_failure
from marivo.analysis.session._store import SessionStore


def _started() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def run_store(tmp_path):
    store = SessionStore(project_root=tmp_path)
    session = store.get_or_insert_session(name="run-tests", question=None, cwd=tmp_path)
    return store, str(session["id"])


def _begin(store: SessionStore, session_id: str, run_id: str = "run_1") -> None:
    store.begin_run(
        session_id=session_id,
        run_id=run_id,
        capability_id="observe",
        analysis_purpose="test",
        arguments=[{"name": "limit", "value": 10}],
        omitted_argument_names=("backend",),
        input_artifact_refs=(),
        started_at=_started(),
    )


def test_run_lifecycle_is_incomplete_then_succeeded(run_store) -> None:
    store, session_id = run_store
    _begin(store, session_id)
    assert store.get_run(session_id, "run_1")["lifecycle"] == "incomplete"
    store.record_artifact(
        session_id=session_id,
        artifact_id="art_1",
        kind="metric_frame",
        path="frames/art_1/data.parquet",
        meta_path="frames/art_1/meta.json",
        content_hash="sha256:test",
        produced_by_job="run_1",
    )
    store.complete_run(
        session_id=session_id,
        run_id="run_1",
        output_artifact_ref="art_1",
        output_mode="produced",
        finished_at=_started(),
    )
    row = store.get_run(session_id, "run_1")
    assert row["lifecycle"] == "succeeded"
    assert row["output_artifact_ref"] == "art_1"
    assert row["output_mode"] == "produced"
    with pytest.raises(SessionStateError, match="illegal persisted Run transition"):
        store.fail_run(
            session_id=session_id,
            run_id="run_1",
            failure={"error_type": "Nope"},
            failed_at=_started(),
        )


def test_complete_run_replaces_effective_arguments_with_terminal_transition(run_store) -> None:
    store, session_id = run_store
    _begin(store, session_id)
    store.record_artifact(
        session_id=session_id,
        artifact_id="art_1",
        kind="metric_frame",
        path="frames/art_1/data.parquet",
        meta_path="frames/art_1/meta.json",
        content_hash="sha256:test",
        produced_by_job="run_1",
    )

    store.complete_run(
        session_id=session_id,
        run_id="run_1",
        output_artifact_ref="art_1",
        output_mode="produced",
        finished_at=_started(),
        arguments=[{"name": "effective_limit", "value": 5}],
        omitted_argument_names=("query",),
    )

    row = store.get_run(session_id, "run_1")
    assert row["lifecycle"] == "succeeded"
    assert json.loads(row["arguments_json"]) == [{"name": "effective_limit", "value": 5}]
    assert json.loads(row["omitted_argument_names_json"]) == ["query"]


def test_failed_success_terminal_write_keeps_artifact_and_incomplete_run(
    run_store, monkeypatch
) -> None:
    store, session_id = run_store
    session = SimpleNamespace(id=session_id, _store=store)

    def fail_terminal(**_kwargs) -> None:
        raise OSError("store unavailable")

    with (
        pytest.raises(OSError),
        admit_run(
            session,
            capability_id="observe",
            analysis_purpose=None,
            arguments={"limit": 10},
            input_artifact_refs=(),
        ) as admission,
    ):
        store.record_artifact(
            session_id=session_id,
            artifact_id="art_committed",
            kind="metric_frame",
            path="frames/art_committed/data.parquet",
            meta_path="frames/art_committed/meta.json",
            content_hash="sha256:test",
            produced_by_job=admission.run_id,
        )
        monkeypatch.setattr(store, "complete_run", fail_terminal)
        admission.succeed(
            "art_committed",
            output_mode="produced",
            arguments=[{"name": "effective_limit", "value": 5}],
            omitted_argument_names=(),
        )

    run = store.list_runs(session_id)[0]
    assert run["lifecycle"] == "incomplete"
    assert store.get_artifact(session_id, "art_committed") is not None


def test_reused_run_does_not_rewrite_artifact_producer(run_store) -> None:
    store, session_id = run_store
    _begin(store, session_id, "run_original")
    store.record_artifact(
        session_id=session_id,
        artifact_id="art_1",
        kind="metric_frame",
        path="frames/art_1/data.parquet",
        meta_path="frames/art_1/meta.json",
        content_hash=None,
        produced_by_job="run_original",
    )
    store.complete_run(
        session_id=session_id,
        run_id="run_original",
        output_artifact_ref="art_1",
        output_mode="produced",
        finished_at=_started(),
    )
    _begin(store, session_id, "run_reuse")
    store.complete_run(
        session_id=session_id,
        run_id="run_reuse",
        output_artifact_ref="art_1",
        output_mode="reused",
        finished_at=_started(),
    )
    assert store.get_artifact(session_id, "art_1")["produced_by_job"] == "run_original"


def test_reused_run_requires_another_succeeded_canonical_producer(run_store) -> None:
    store, session_id = run_store
    store.record_artifact(
        session_id=session_id,
        artifact_id="art_orphan",
        kind="metric_frame",
        path="frames/art_orphan/data.parquet",
        meta_path="frames/art_orphan/meta.json",
        content_hash=None,
        produced_by_job=None,
    )
    _begin(store, session_id, "run_reuse")

    with pytest.raises(SessionStateError, match="canonical producing Run"):
        store.complete_run(
            session_id=session_id,
            run_id="run_reuse",
            output_artifact_ref="art_orphan",
            output_mode="reused",
            finished_at=_started(),
        )

    assert store.get_run(session_id, "run_reuse")["lifecycle"] == "incomplete"


def test_run_inputs_preserve_order_and_require_same_session_ownership(run_store) -> None:
    store, session_id = run_store
    for artifact_id in ("art_b", "art_a"):
        store.record_artifact(
            session_id=session_id,
            artifact_id=artifact_id,
            kind="metric_frame",
            path=f"frames/{artifact_id}/data.parquet",
            meta_path=f"frames/{artifact_id}/meta.json",
            content_hash=None,
            produced_by_job=None,
        )
    store.begin_run(
        session_id=session_id,
        run_id="run_ordered",
        capability_id="compare",
        analysis_purpose=None,
        arguments=[],
        omitted_argument_names=(),
        input_artifact_refs=("art_b", "art_a"),
        started_at=_started(),
    )
    assert store.run_input_refs(session_id, "run_ordered") == ("art_b", "art_a")
    with pytest.raises(SessionStateError, match="absent from the owning Session"):
        store.begin_run(
            session_id=session_id,
            run_id="run_missing",
            capability_id="compare",
            analysis_purpose=None,
            arguments=[],
            omitted_argument_names=(),
            input_artifact_refs=("art_missing",),
            started_at=_started(),
        )
    assert store.get_run(session_id, "run_missing") is None


def test_argument_projection_redacts_and_bounds_without_repr() -> None:
    class Dangerous:
        def __repr__(self) -> str:
            raise AssertionError("repr must not be called")

    projected, omitted = project_run_arguments(
        {
            "limit": 10,
            "password": "not-persisted",
            "sql": "select secret from users",
            "note": "authorization=abc",
            "filter": "SELECT secret FROM users",
            "nested": {"value": "Bearer hidden"},
            "backend": Dangerous(),
            "long": "界" * 2000,
            "many": list(range(65)),
        }
    )
    values = {item["name"]: item["value"] for item in projected}
    assert values["limit"] == 10
    assert len(str(values["long"]).encode("utf-8")) <= 1027
    assert set(omitted) == {
        "backend",
        "filter",
        "many",
        "nested",
        "note",
        "password",
        "sql",
    }
    assert len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= 8192


def test_unknown_failure_is_generic_and_known_failure_is_sanitized() -> None:
    assert project_run_failure(RuntimeError("token=abc select x from secret")) == {
        "error_type": "InternalExecutionError",
        "message": "The admitted analysis execution failed; inspect the original raised error.",
        "expected": None,
        "received": None,
        "location": None,
        "repair": None,
    }
    known = AnalysisError(
        message="authorization=abc",
        expected={"nested": ["Bearer hidden"]},
        received={"detail": "SELECT secret FROM users"},
    )
    projected = project_run_failure(known)
    assert projected["message"] == "authorization=<redacted>"
    assert projected["expected"] == {"nested": ["Bearer <redacted>"]}
    assert projected["received"] == {"detail": "<redacted-sql>"}


def test_admission_rethrows_original_and_persists_failed_run(run_store) -> None:
    store, session_id = run_store
    session = SimpleNamespace(id=session_id, _store=store)
    original = ValueError("do not persist this message")
    with (
        pytest.raises(ValueError) as exc_info,
        admit_run(
            session,
            capability_id="observe",
            analysis_purpose=None,
            arguments={},
            input_artifact_refs=(),
        ),
    ):
        raise original
    assert exc_info.value is original
    row = store.list_runs(session_id)[0]
    assert row["lifecycle"] == "failed"
    assert "do not persist this message" not in str(row["failure_json"])


def test_base_exception_leaves_incomplete_run(run_store) -> None:
    store, session_id = run_store
    session = SimpleNamespace(id=session_id, _store=store)
    with (
        pytest.raises(KeyboardInterrupt),
        admit_run(
            session,
            capability_id="observe",
            analysis_purpose=None,
            arguments={},
            input_artifact_refs=(),
        ),
    ):
        raise KeyboardInterrupt
    assert store.list_runs(session_id)[0]["lifecycle"] == "incomplete"


def test_failed_terminal_write_raises_session_state_error_with_original_cause(
    run_store, monkeypatch
) -> None:
    store, session_id = run_store
    session = SimpleNamespace(id=session_id, _store=store)
    original = ValueError("original")

    def fail_terminal(**_kwargs) -> None:
        raise OSError("store unavailable")

    monkeypatch.setattr(store, "fail_run", fail_terminal)
    with (
        pytest.raises(SessionStateError) as exc_info,
        admit_run(
            session,
            capability_id="observe",
            analysis_purpose=None,
            arguments={},
            input_artifact_refs=(),
        ),
    ):
        raise original
    assert exc_info.value.__cause__ is original
    assert exc_info.value._context["run_id"].startswith("run_")
