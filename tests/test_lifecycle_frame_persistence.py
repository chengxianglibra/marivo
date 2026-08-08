"""LifecycleFrame row and private replay-trace persistence contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import FrameCacheCorruptedError, ModelStateMismatchError
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.event import EventInputCoverage
from marivo.analysis.frames.lifecycle import (
    LIFECYCLE_HISTORY_COLUMNS,
    LIFECYCLE_VIOLATIONS_COLUMNS,
    LifecycleDistributionFrameMeta,
    LifecycleDwellFrameMeta,
    LifecycleFrame,
    LifecycleHistoryFrameMeta,
    LifecycleStateBinding,
    LifecycleStatePair,
    LifecycleTraceManifest,
    LifecycleTransitionsFrameMeta,
    LifecycleTriggerBinding,
    LifecycleViolationsFrameMeta,
    PersistedModelStateHandle,
)
from marivo.analysis.lifecycle import from_inception, in_state
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._runtime import (
    _validate_lifecycle_history_payload,
    persist_frame,
    persist_job_record,
)
from marivo.refs import RefPayloadV1, ref


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _payload(value):
    return RefPayloadV1.from_ref(value)


def _history_frame(session, *, trace_rows=()):
    model_ref = _payload(ref.state_model("commerce.order_lifecycle"))
    subject_ref = _payload(ref.entity("commerce.orders"))
    event_ref = _payload(ref.event("commerce.order_created"))
    identity_ref = _payload(ref.dimension("commerce.order_events.event_id"))
    state = LifecycleStateBinding(
        state=PersistedModelStateHandle(model=model_ref, name="created"),
        initial=True,
    )
    trigger = LifecycleTriggerBinding(
        kind="inception",
        event_ref=event_ref,
        participant_role="order",
        to_state="created",
    )
    trace = pd.DataFrame(trace_rows, columns=LIFECYCLE_VIOLATIONS_COLUMNS)
    if trace.empty:
        trace["occurred_at"] = pd.to_datetime(trace["occurred_at"], utc=True)
    history = pd.DataFrame(
        [
            {
                "subject_identity": ("o1",),
                "model_state": "created",
                "valid_from": pd.Timestamp("2026-07-01T00:00:00Z"),
                "valid_to": pd.Timestamp("2026-08-01T00:00:00Z"),
                "entered_by_event_ref": event_ref.path,
                "entered_by_event_identity": ("e1",),
                "exited_by_event_ref": None,
                "exited_by_event_identity": None,
                "interval_status": "coverage_censored",
            }
        ],
        columns=LIFECYCLE_HISTORY_COLUMNS,
    )
    return LifecycleFrame(
        _df=history,
        meta=LifecycleHistoryFrameMeta(
            ref="frame_lifecycle_history",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_lifecycle",
            created_at=datetime(2026, 7, 25, tzinfo=UTC),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            catalog_definition_fingerprint="sha256:catalog",
            state_model_ref=model_ref,
            state_model_fingerprint="sha256:model",
            subject_entity_ref=subject_ref,
            subject_identity=(identity_ref.path,),
            states=(state,),
            seed=from_inception(),
            window=mv.time_scope(
                start="2026-07-01T00:00:00Z",
                end="2026-08-01T00:00:00Z",
            ),
            triggers=(trigger,),
            input_coverage=(EventInputCoverage(event_ref=event_ref, basis="unknown"),),
            coverage_basis="unknown",
            event_fingerprints={event_ref.path: "sha256:event"},
            event_identity_components={event_ref.path: (identity_ref,)},
            query_refs=(event_ref.path,),
            population_count=1,
            seeded_subject_count=1,
            coverage_censored_subject_count=0,
            interval_count=1,
            violation_count=len(trace),
            pre_inception_ignored_counts={trigger.key: 0},
            violation_trace=LifecycleTraceManifest(row_count=len(trace)),
        ),
        _auxiliary_frames={"violations.parquet": trace},
    )


def test_lifecycle_values_are_frozen_fingerprinted_and_structured():
    seed = from_inception()
    assert seed.fingerprint.startswith("sha256:")
    assert seed.model_dump(mode="json") == {"kind": "from_inception"}

    with pytest.raises(ModelStateMismatchError) as exc_info:
        in_state("paid", as_of="2026-07-01T00:00:00Z")
    error = exc_info.value
    assert error.expected
    assert error.received
    assert error.location == "mv.in_state(state)"
    assert error.repair.kind == "user_choice"
    assert error.repair.help_target.canonical_id == "in_state"


def test_lifecycle_history_persists_empty_trace_and_cold_recovers():
    session = session_attach.get_or_create(name="lifecycle")
    frame = _history_frame(session)

    frame.meta = persist_frame(session, frame)
    loaded = session.get_frame(frame.ref)

    assert isinstance(loaded, LifecycleFrame)
    assert loaded.meta.semantic_kind == "history"
    assert loaded.meta.violation_trace.content_hash.startswith("sha256:")
    assert loaded.meta.content_hash.startswith("sha256:")
    assert loaded.meta.byte_size > 0
    assert loaded.to_pandas().equals(frame.to_pandas())
    assert tuple(loaded.to_pandas().columns) == LIFECYCLE_HISTORY_COLUMNS
    assert "trigger_event_identity" not in loaded.to_pandas()
    assert loaded._auxiliary_frames["violations.parquet"].empty
    assert "shape=history" in repr(loaded)
    assert len(loaded.render(max_output_bytes=4096).encode()) <= 4096


def test_lifecycle_history_persists_nonempty_private_trace():
    session = session_attach.get_or_create(name="lifecycle")
    trace_row = (
        ("o1",),
        "commerce.order_paid",
        ("e2",),
        pd.Timestamp("2026-07-05T00:00:00Z"),
        "created",
        "illegal_transition",
    )
    frame = _history_frame(session, trace_rows=(trace_row,))

    frame.meta = persist_frame(session, frame)
    loaded = session.get_frame(frame.ref)

    trace = loaded._auxiliary_frames["violations.parquet"]
    assert tuple(trace.columns) == LIFECYCLE_VIOLATIONS_COLUMNS
    assert trace.iloc[0]["subject_identity"] == ("o1",)
    assert trace.iloc[0]["trigger_event_identity"] == ("e2",)
    assert loaded.meta.violation_count == 1
    assert "violation_kind" not in loaded.to_pandas()


def test_lifecycle_history_rejects_tampered_trace_hash():
    session = session_attach.get_or_create(name="lifecycle")
    frame = _history_frame(session)
    frame.meta = persist_frame(session, frame)
    trace_path = session._layout.frames_dir / frame.ref / "violations.parquet"
    pd.DataFrame(
        [
            (
                ("o1",),
                "commerce.order_paid",
                ("e2",),
                pd.Timestamp("2026-07-05T00:00:00Z"),
                "created",
                "illegal_transition",
            )
        ],
        columns=LIFECYCLE_VIOLATIONS_COLUMNS,
    ).to_parquet(trace_path, index=False)

    with pytest.raises(FrameCacheCorruptedError, match="trace hash"):
        session.get_frame(frame.ref)


def test_lifecycle_history_rejects_missing_trace():
    session = session_attach.get_or_create(name="lifecycle")
    frame = _history_frame(session)
    frame.meta = persist_frame(session, frame)
    trace_path = session._layout.frames_dir / frame.ref / "violations.parquet"
    trace_path.unlink()

    with pytest.raises(FrameCacheCorruptedError, match="trace is missing"):
        session.get_frame(frame.ref)


def test_lifecycle_history_rejects_trace_path_escape():
    session = session_attach.get_or_create(name="lifecycle")
    frame = _history_frame(session)
    frame.meta = persist_frame(session, frame)
    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["violation_trace"]["filename"] = "../outside.parquet"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameCacheCorruptedError, match="escaped Lifecycle trace path"):
        session.get_frame(frame.ref)


def test_lifecycle_history_rejects_invalid_public_row_contract():
    session = session_attach.get_or_create(name="lifecycle")
    frame = _history_frame(session)
    frame._df = frame._df.drop(columns=["interval_status"])

    with pytest.raises(ValueError, match="columns must be exactly"):
        LifecycleFrame(
            _df=frame._df,
            meta=frame.meta,
            _auxiliary_frames=frame._auxiliary_frames,
        )


def test_lifecycle_history_emits_typed_v2_job_semantics():
    session = session_attach.get_or_create(name="lifecycle")
    frame = _history_frame(session)
    frame.meta = persist_frame(session, frame)

    semantics = job_semantics_from_frames(frame)
    _validate_lifecycle_history_payload(semantics["lifecycle_history"])
    record = {
        "id": "job_lifecycle",
        "intent": "lifecycle.replay",
        "status": "succeeded",
        "started_at": "2026-07-25T00:00:00+00:00",
        "finished_at": "2026-07-25T00:00:01+00:00",
        "output_frame_ref": frame.ref,
        **semantics,
    }

    persist_job_record(session, record)

    persisted = session.job("job_lifecycle")
    assert persisted["schema"] == "marivo.analysis_job/v2"
    assert persisted["subject"]["kind"] == "lifecycle"
    assert persisted["lifecycle_history"]["violation_trace"]["content_hash"].startswith("sha256:")
    assert "subject_identity" not in persisted["lifecycle_history"]


def test_lifecycle_reducer_shapes_enforce_closed_public_rows():
    session = session_attach.get_or_create(name="lifecycle")
    history = _history_frame(session)
    history.meta = persist_frame(session, history)
    common = {
        "session_id": session.id,
        "project_root": str(session.project_root),
        "produced_by_job": "job_reducer",
        "created_at": datetime(2026, 7, 25, tzinfo=UTC),
        "byte_size": 0,
        "lineage": Lineage(),
        "catalog_definition_fingerprint": (history.meta.catalog_definition_fingerprint),
        "state_model_ref": history.meta.state_model_ref,
        "state_model_fingerprint": history.meta.state_model_fingerprint,
        "subject_entity_ref": history.meta.subject_entity_ref,
        "subject_identity": history.meta.subject_identity,
        "states": history.meta.states,
        "source_history_ref": history.ref,
        "source_history_fingerprint": history.meta.content_hash,
    }
    distribution = LifecycleFrame(
        _df=pd.DataFrame(
            [
                {
                    "as_of": "2026-07-15T00:00:00Z",
                    "model_state": "created",
                    "subject_count": 1,
                    "share": 1.0,
                }
            ]
        ),
        meta=LifecycleDistributionFrameMeta(
            **common,
            ref="frame_distribution",
            row_count=1,
            at=("2026-07-15T00:00:00Z",),
            known_subject_counts={"2026-07-15T00:00:00Z": 1},
            coverage_censored_subject_counts={"2026-07-15T00:00:00Z": 0},
            grouped_reconciliation_hash="sha256:distribution",
        ),
    )
    transitions = LifecycleFrame(
        _df=pd.DataFrame(
            [
                {
                    "from_model_state": "created",
                    "to_model_state": "created",
                    "transition_status": "modeled",
                    "transition_count": 0,
                    "share_of_modeled_transitions": None,
                }
            ]
        ),
        meta=LifecycleTransitionsFrameMeta(
            **common,
            ref="frame_transitions",
            row_count=1,
            modeled_pairs=(LifecycleStatePair(from_state="created", to_state="created"),),
            modeled_transition_count=0,
        ),
    )
    dwell = LifecycleFrame(
        _df=pd.DataFrame(
            [
                {
                    "model_state": "created",
                    "interval_count": 1,
                    "completed_count": 0,
                    "right_censored_count": 1,
                    "coverage_censored_count": 0,
                    "mean_duration": None,
                    "median_duration": None,
                    "p90_duration": None,
                }
            ]
        ),
        meta=LifecycleDwellFrameMeta(
            **common,
            ref="frame_dwell",
            row_count=1,
            source_interval_count=1,
        ),
    )
    violations = LifecycleFrame(
        _df=pd.DataFrame(columns=LIFECYCLE_VIOLATIONS_COLUMNS).assign(
            occurred_at=pd.Series(dtype="datetime64[ns, UTC]")
        ),
        meta=LifecycleViolationsFrameMeta(
            **common,
            ref="frame_violations",
            row_count=0,
            violation_count=0,
            source_trace_content_hash=history.meta.violation_trace.content_hash,
        ),
    )

    assert distribution.semantic_shape == "distribution"
    assert transitions.semantic_shape == "transitions"
    assert dwell.semantic_shape == "dwell"
    assert violations.semantic_shape == "violations"
    assert tuple(distribution.columns) == (
        "as_of",
        "model_state",
        "subject_count",
        "share",
    )
    exported = dwell.to_pandas()
    exported.loc[0, "interval_count"] = 99
    assert dwell.to_pandas().loc[0, "interval_count"] == 1


def test_lifecycle_history_evidence_commit_writes_and_reuses_trace(tmp_path):
    session = session_attach.get_or_create(name="lifecycle")
    frames_dir = tmp_path / "committed"
    first = _history_frame(session)
    anchors = CommitSemanticAnchors.from_frame(first)

    committed = commit_result(
        store=None,
        frames_dir=frames_dir,
        frame=first,
        step_type="lifecycle.replay",
        inputs=CommitInputs(input_refs=[]),
        params=CommitParams(values={"model": "commerce.order_lifecycle"}),
        semantic_anchors=anchors,
        subject=Subject(analysis_axis="scalar"),
        extractor_family="lifecycle_frame",
        emit_evidence=False,
    )

    artifact_dir = frames_dir / committed.ref
    assert (artifact_dir / "violations.parquet").is_file()
    assert committed.meta.violation_trace.content_hash.startswith("sha256:")
    assert committed.meta.content_hash.startswith("sha256:")

    reused = commit_result(
        store=None,
        frames_dir=frames_dir,
        frame=_history_frame(session),
        step_type="lifecycle.replay",
        inputs=CommitInputs(input_refs=[]),
        params=CommitParams(values={"model": "commerce.order_lifecycle"}),
        semantic_anchors=anchors,
        subject=Subject(analysis_axis="scalar"),
        extractor_family="lifecycle_frame",
        emit_evidence=False,
    )

    assert reused.ref == committed.ref
    assert reused.meta.content_hash == committed.meta.content_hash
    assert reused._auxiliary_frames["violations.parquet"].empty
