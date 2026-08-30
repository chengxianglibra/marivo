"""Lifecycle quality and identity-safe evidence contracts."""

from __future__ import annotations

import json
from datetime import datetime
from typing import cast

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._compat import UTC
from marivo.analysis.event import EventWatermarkReceipt
from marivo.analysis.evidence.digest import build_artifact_digest
from marivo.analysis.evidence.extraction.lifecycle import extract_lifecycle_finding
from marivo.analysis.evidence.pipeline import lifecycle_subject_for_frame
from marivo.analysis.evidence.types import (
    LifecycleAnalysisScope,
    LifecycleSubject,
    OperatorSemantics,
)
from marivo.analysis.frames._meta_defaults import compute_analysis_scope
from marivo.analysis.frames._quality import evaluate_frame_quality
from marivo.analysis.frames._quality_checks import run_lifecycle_checks
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
from marivo.analysis.intents._lifecycle_distribution import (
    reduce_lifecycle_distribution,
)
from marivo.analysis.intents._lifecycle_dwell import reduce_lifecycle_dwell
from marivo.analysis.intents._lifecycle_transitions import (
    reduce_lifecycle_transitions,
)
from marivo.analysis.intents._lifecycle_violations import (
    reduce_lifecycle_violations,
)
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._runtime import persist_frame
from marivo.refs import RefPayloadV1, ref


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mv.session._reset_process_state()
    yield
    mv.session._reset_process_state()


def _payload(value):
    return RefPayloadV1.from_ref(value)


def _key(value: RefPayloadV1) -> str:
    return f"{value.kind.value}:{value.path}"


def _coverage(event_ref: RefPayloadV1) -> EventInputCoverage:
    receipt = EventWatermarkReceipt(
        complete_through="2026-08-01T00:00:00Z",
        authority="warehouse_reconciliation",
        observed_at="2026-08-02T00:00:00Z",
    )
    return EventInputCoverage(
        event_ref=event_ref,
        basis="observed_watermark",
        receipt=receipt,
        observed_complete_through=receipt.complete_through,
    )


def _history_frame(session: mv.Session) -> LifecycleFrame:
    model_ref = _payload(ref.state_model("commerce.order_lifecycle"))
    subject_ref = _payload(ref.entity("commerce.orders"))
    created_event = _payload(ref.event("commerce.order_created"))
    paid_event = _payload(ref.event("commerce.payment_captured"))
    identity_ref = _payload(ref.dimension("commerce.order_events.event_id"))
    created = LifecycleStateBinding(
        state=PersistedModelStateHandle(model=model_ref, name="created"),
        initial=True,
    )
    paid = LifecycleStateBinding(
        state=PersistedModelStateHandle(model=model_ref, name="paid"),
        terminal=True,
    )
    inception = LifecycleTriggerBinding(
        kind="inception",
        event_ref=created_event,
        participant_role="order",
        to_state="created",
    )
    transition = LifecycleTriggerBinding(
        kind="transition",
        event_ref=paid_event,
        participant_role="order",
        from_state="created",
        to_state="paid",
    )
    history = pd.DataFrame(
        [
            {
                "subject_identity": ("order_secret",),
                "model_state": "created",
                "valid_from": pd.Timestamp("2026-07-01T00:00:00Z"),
                "valid_to": pd.Timestamp("2026-07-10T00:00:00Z"),
                "entered_by_event_ref": _key(created_event),
                "entered_by_event_identity": ("created_secret",),
                "exited_by_event_ref": _key(paid_event),
                "exited_by_event_identity": ("paid_secret",),
                "interval_status": "completed",
            },
            {
                "subject_identity": ("order_secret",),
                "model_state": "paid",
                "valid_from": pd.Timestamp("2026-07-10T00:00:00Z"),
                "valid_to": pd.Timestamp("2026-08-01T00:00:00Z"),
                "entered_by_event_ref": _key(paid_event),
                "entered_by_event_identity": ("paid_secret",),
                "exited_by_event_ref": None,
                "exited_by_event_identity": None,
                "interval_status": "right_censored",
            },
        ],
        columns=LIFECYCLE_HISTORY_COLUMNS,
    )
    trace = pd.DataFrame(
        [
            {
                "subject_identity": ("order_secret",),
                "trigger_event_ref": _key(paid_event),
                "trigger_event_identity": ("violation_secret",),
                "occurred_at": pd.Timestamp("2026-07-20T00:00:00Z"),
                "model_state_at_event": "paid",
                "violation_kind": "transition_from_terminal",
            }
        ],
        columns=LIFECYCLE_VIOLATIONS_COLUMNS,
    )
    return LifecycleFrame(
        _df=history,
        meta=LifecycleHistoryFrameMeta(
            ref="frame_lifecycle_history",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_lifecycle_replay",
            created_at=datetime(2026, 7, 25, tzinfo=UTC),
            row_count=len(history),
            byte_size=0,
            lineage=Lineage(),
            catalog_definition_fingerprint="sha256:catalog",
            state_model_ref=model_ref,
            state_model_fingerprint="sha256:model",
            subject_entity_ref=subject_ref,
            subject_identity=("commerce.orders.order_id",),
            states=(created, paid),
            seed=mv.from_inception(),
            window=mv.time_scope(
                start="2026-07-01T00:00:00Z",
                end="2026-08-01T00:00:00Z",
            ),
            triggers=(inception, transition),
            input_coverage=(
                _coverage(created_event),
                _coverage(paid_event),
            ),
            coverage_basis="observed_watermark",
            event_fingerprints={
                created_event.path: "sha256:created",
                paid_event.path: "sha256:paid",
            },
            event_identity_components={
                created_event.path: (identity_ref,),
                paid_event.path: (identity_ref,),
            },
            query_refs=(created_event.path, paid_event.path),
            population_count=1,
            seeded_subject_count=1,
            coverage_censored_subject_count=0,
            interval_count=len(history),
            violation_count=len(trace),
            pre_inception_ignored_counts={
                inception.key: 0,
                transition.key: 0,
            },
            violation_trace=LifecycleTraceManifest(row_count=len(trace)),
        ),
        _auxiliary_frames={"violations.parquet": trace},
    )


def _reducer_frames(
    session: mv.Session,
    history: LifecycleFrame,
) -> tuple[LifecycleFrame, LifecycleFrame, LifecycleFrame, LifecycleFrame]:
    history.meta = persist_frame(session, history)
    source_hash = cast("str", history.meta.content_hash)
    history_meta = cast("LifecycleHistoryFrameMeta", history.meta)
    common = {
        "session_id": session.id,
        "project_root": str(session.project_root),
        "produced_by_job": "job_lifecycle_reducer",
        "created_at": datetime(2026, 7, 25, tzinfo=UTC),
        "byte_size": 0,
        "lineage": Lineage(),
        "catalog_definition_fingerprint": history_meta.catalog_definition_fingerprint,
        "state_model_ref": history_meta.state_model_ref,
        "state_model_fingerprint": history_meta.state_model_fingerprint,
        "subject_entity_ref": history_meta.subject_entity_ref,
        "subject_identity": history_meta.subject_identity,
        "states": history_meta.states,
        "source_history_ref": history.ref,
        "source_history_fingerprint": source_hash,
    }
    instant = "2026-07-15T00:00:00+00:00"
    distribution_reduction = reduce_lifecycle_distribution(
        history.to_pandas(),
        instants=((instant, pd.Timestamp(instant)),),
        state_order=("created", "paid"),
        population_count=1,
    )
    distribution = LifecycleFrame(
        _df=distribution_reduction.rows,
        meta=LifecycleDistributionFrameMeta(
            **common,
            ref="frame_lifecycle_distribution",
            row_count=len(distribution_reduction.rows),
            at=(instant,),
            known_subject_counts=distribution_reduction.known_subject_counts,
            coverage_censored_subject_counts=(
                distribution_reduction.coverage_censored_subject_counts
            ),
            grouped_reconciliation_hash=(distribution_reduction.grouped_reconciliation_hash),
        ),
    )
    transition_reduction = reduce_lifecycle_transitions(
        history.to_pandas(),
        triggers=history_meta.triggers,
    )
    transitions = LifecycleFrame(
        _df=transition_reduction.rows,
        meta=LifecycleTransitionsFrameMeta(
            **common,
            ref="frame_lifecycle_transitions",
            row_count=len(transition_reduction.rows),
            modeled_pairs=tuple(
                LifecycleStatePair(from_state=source, to_state=target)
                for source, target in transition_reduction.modeled_pairs
            ),
            modeled_transition_count=transition_reduction.modeled_transition_count,
        ),
    )
    dwell_reduction = reduce_lifecycle_dwell(
        history.to_pandas(),
        state_order=("created", "paid"),
    )
    dwell = LifecycleFrame(
        _df=dwell_reduction.rows,
        meta=LifecycleDwellFrameMeta(
            **common,
            ref="frame_lifecycle_dwell",
            row_count=len(dwell_reduction.rows),
            source_interval_count=dwell_reduction.source_interval_count,
        ),
    )
    trace = history._auxiliary_frames["violations.parquet"]
    violation_reduction = reduce_lifecycle_violations(trace)
    violations = LifecycleFrame(
        _df=violation_reduction.rows,
        meta=LifecycleViolationsFrameMeta(
            **common,
            ref="frame_lifecycle_violations",
            row_count=len(violation_reduction.rows),
            violation_count=violation_reduction.violation_count,
            source_trace_content_hash=cast(
                "str",
                history_meta.violation_trace.content_hash,
            ),
        ),
    )
    return distribution, transitions, dwell, violations


def test_lifecycle_quality_dispatches_every_shape_and_recomputes_source() -> None:
    session = mv.session.get_or_create(
        name="lifecycle_quality",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    history = _history_frame(session)
    reducers = _reducer_frames(session, history)

    for frame in (history, *reducers):
        source_history = history if frame is not history else None
        checks = run_lifecycle_checks(frame, source_history=source_history)
        assert checks
        assert {row["severity"] for row in checks} == {"ok"}
        evaluation = evaluate_frame_quality(
            frame,
            artifact_id="prospective",
            source_history=source_history,
        )
        assert evaluation is not None
        assert evaluation.summary.evaluated_check_count == len(checks)
        assert evaluation.overall_status == "ok"
        assert evaluation.dataframe["metric_id"].isna().all()

    reducers[0]._df.loc[0, "subject_count"] = 99
    tampered = {
        row["check_kind"]: row["severity"]
        for row in run_lifecycle_checks(reducers[0], source_history=history)
    }
    assert tampered["lifecycle_distribution_math"] == "blocking"
    assert tampered["lifecycle_distribution_reconciliation"] == "blocking"


def test_empty_lifecycle_result_is_warning_when_receipts_remain_valid() -> None:
    session = mv.session.get_or_create(
        name="empty_lifecycle_report",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    source = _history_frame(session)
    meta = cast("LifecycleHistoryFrameMeta", source.meta)
    empty = LifecycleFrame(
        _df=source.to_pandas().iloc[0:0],
        meta=meta.model_copy(
            update={
                "row_count": 0,
                "interval_count": 0,
                "seeded_subject_count": 0,
            }
        ),
        _auxiliary_frames={
            "violations.parquet": source._auxiliary_frames["violations.parquet"].copy(deep=True)
        },
    )
    report = evaluate_frame_quality(empty, artifact_id="prospective")
    assert report is not None
    row_count = report.dataframe.set_index("check_kind").loc["row_count"]

    assert row_count["severity"] == "warning"
    assert report.overall_status == "warning"
    assert report.blocking_issue_count == 0
    assert {issue.kind for issue in report.issues} == {"sample_size_low"}


def test_lifecycle_quality_discloses_unknown_coverage_and_censoring() -> None:
    session = mv.session.get_or_create(
        name="lifecycle_unknown_coverage",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    base = _history_frame(session)
    meta = cast("LifecycleHistoryFrameMeta", base.meta)
    censored_rows = base.to_pandas()
    censored_rows.loc[
        censored_rows["interval_status"] == "right_censored",
        "interval_status",
    ] = "coverage_censored"
    unknown_coverage = tuple(
        EventInputCoverage(event_ref=item.event_ref, basis="unknown")
        for item in meta.input_coverage
    )
    censored = LifecycleFrame(
        _df=censored_rows,
        meta=meta.model_copy(
            update={
                "input_coverage": unknown_coverage,
                "coverage_basis": "unknown",
            }
        ),
        _auxiliary_frames={
            "violations.parquet": base._auxiliary_frames["violations.parquet"].copy(deep=True)
        },
    )
    report = evaluate_frame_quality(censored, artifact_id="prospective")
    assert report is not None

    assert report.overall_status == "warning"
    assert {issue.kind for issue in report.issues} == {
        "lifecycle_coverage_unknown",
        "lifecycle_censoring_present",
    }


def test_lifecycle_declared_completeness_is_not_a_quality_warning() -> None:
    """A disclosed lifecycle declaration is a governed path, not a defect.

    Pins issue #83 on the Lifecycle surface: ``declared_completeness_used``
    must not force ``overall_status`` to ``warning`` when every replay Event
    is covered by a rationale-bearing declaration that covers ``window.end``.
    """
    session = mv.session.get_or_create(
        name="lifecycle_declared_coverage",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    base = _history_frame(session)
    meta = cast("LifecycleHistoryFrameMeta", base.meta)
    created = ref.event("commerce.order_created")
    paid = ref.event("commerce.payment_captured")
    declaration = mv.declared_complete_through(
        inputs=(created, paid),
        through="2026-08-01T00:00:00Z",
        rationale="Order lifecycle events reconciled through the window bound.",
    )
    declared_coverage = tuple(
        EventInputCoverage(
            event_ref=_payload(event_ref),
            basis="declared_complete",
            declaration_fingerprint=declaration.fingerprint,
            declaration_rationale=declaration.rationale,
        )
        for event_ref in (created, paid)
    )
    declared = LifecycleFrame(
        _df=base.to_pandas(),
        meta=meta.model_copy(
            update={
                "completeness": (declaration,),
                "input_coverage": declared_coverage,
                "coverage_basis": "declared_complete",
            }
        ),
        _auxiliary_frames={
            "violations.parquet": base._auxiliary_frames["violations.parquet"].copy(deep=True)
        },
    )
    rows = run_lifecycle_checks(declared)
    row = next(r for r in rows if r["check_kind"] == "declared_completeness_used")
    assert row["severity"] == "ok"

    report = evaluate_frame_quality(declared, artifact_id="prospective")
    assert report is not None
    assert report.overall_status == "ok"
    assert "declared_completeness_used" not in {issue.kind for issue in report.issues}


def test_lifecycle_findings_and_digests_are_closed_bounded_and_identity_safe() -> None:
    session = mv.session.get_or_create(name="lifecycle_evidence", use_datasources=False)
    history = _history_frame(session)
    reducers = _reducer_frames(session, history)
    committed_at = datetime(2026, 7, 25, tzinfo=UTC)

    for frame in (history, *reducers):
        subject = cast(
            "LifecycleSubject",
            lifecycle_subject_for_frame(frame),
        )
        scope = cast(
            "LifecycleAnalysisScope",
            compute_analysis_scope(frame),
        )
        finding = extract_lifecycle_finding(
            df=frame.to_pandas(),
            artifact_id=frame.ref,
            session_id=session.id,
            subject=subject,
            committed_at=committed_at,
            meta=frame.meta,
        )
        operator = (
            "lifecycle.replay"
            if frame.semantic_shape == "history"
            else f"lifecycle.{frame.semantic_shape}"
        )
        digest = build_artifact_digest(
            artifact_ref=frame.ref,
            operator=OperatorSemantics(
                operator=operator,
                operator_version="v1",
                artifact_family="lifecycle_frame",
                semantic_shape=frame.semantic_shape,
            ),
            subject=subject,
            scope=scope,
            findings=(finding,),
            quality=None,
            rows_available=True,
        )
        assert finding.value.value.shape == f"lifecycle_{frame.semantic_shape}"
        assert len(digest.items) == 1
        assert len(digest.items) <= 5
        assert len(digest.boundaries) <= 3
        rendered = digest.render(max_output_bytes=4096)
        assert len(rendered.encode()) <= 4096
        serialized = json.dumps(
            {
                "finding": finding.model_dump(mode="json"),
                "digest": digest.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        assert "order_secret" not in serialized
        assert "created_secret" not in serialized
        assert "paid_secret" not in serialized
        assert "violation_secret" not in serialized
        assert "order_secret" not in rendered

    history_scope = cast(
        "LifecycleAnalysisScope",
        compute_analysis_scope(history),
    )
    assert history_scope.coverage is not None
    assert history_scope.coverage["basis"] == "observed_watermark"
    assert history_scope.replay_semantics == {
        "operator_version": "lifecycle_replay/v1",
        "seed": {"kind": "from_inception"},
        "violation_behavior_id": "record_and_continue/v1",
    }
