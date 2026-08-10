"""Focused Phase 2 value, metadata-variant, and cold-recovery contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import PatternStepMismatchError
from marivo.analysis.event import EventPattern, EventWatermarkReceipt, PatternStep
from marivo.analysis.frames.base import BaseFrameMeta
from marivo.analysis.frames.event import (
    EventFrame,
    EventFrameMeta,
    EventFrameMetaBase,
    EventFunnelFrameMeta,
    EventInputCoverage,
    EventTimeToEventFrameMeta,
    GroupedFunnelReconciliationReceipt,
    SubjectAxisBinding,
)
from marivo.analysis.frames.subject import (
    SubjectSet,
    SubjectSetMeta,
    SubjectSetSourceBinding,
)
from marivo.analysis.intents.subjects import _select_rows
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._runtime import persist_frame, persist_job_record
from marivo.analysis.subject import DroppedBefore, dropped_before
from marivo.refs import RefPayloadV1


def _pattern() -> tuple[EventPattern, PatternStep, PatternStep]:
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    cart_step = mv.step(
        participant=ms.participant_role(event=cart, name="user"),
        key="cart",
    )
    payment_step = mv.step(
        participant=ms.participant_role(event=payment, name="buyer"),
        key="payment",
    )
    return mv.sequence(cart_step, payment_step), cart_step, payment_step


def _event_common(
    *,
    session_id: str = "sess_phase2",
    project_root: str = "/tmp/marivo-phase2",
) -> dict[str, object]:
    pattern, _, _ = _pattern()
    user = ms.ref.entity("commerce.users")
    event_id = RefPayloadV1.from_ref(ms.ref.dimension("commerce.events.event_id"))
    return {
        "ref": "frame_event",
        "session_id": session_id,
        "project_root": project_root,
        "produced_by_job": "job_event",
        "created_at": datetime(2026, 7, 24, tzinfo=UTC),
        "row_count": 0,
        "byte_size": 0,
        "lineage": Lineage(),
        "catalog_definition_fingerprint": "sha256:catalog",
        "subject_entity_ref": RefPayloadV1.from_ref(user),
        "subject_identity": ("commerce.users.user_id",),
        "pattern": pattern,
        "matching": mv.first_per_subject(),
        "cohort_window": mv.time_scope(
            start="2026-07-01T00:00:00Z",
            end="2026-07-02T00:00:00Z",
        ),
        "completion_through": "2026-07-03T00:00:00Z",
        "input_coverage": tuple(
            EventInputCoverage(
                event_ref=RefPayloadV1.from_ref(step.event),
                basis="unknown",
            )
            for step in pattern.steps
        ),
        "coverage_basis": "unknown",
        "event_fingerprints": {step.event.path: f"sha256:{step.key}" for step in pattern.steps},
        "event_identity_components": {step.event.path: (event_id,) for step in pattern.steps},
        "role_endpoints": {step.key: RefPayloadV1.from_ref(user) for step in pattern.steps},
    }


def test_dropped_before_is_exact_frozen_and_fingerprint_stable() -> None:
    _, _, payment = _pattern()

    first = dropped_before(step=payment)
    second = dropped_before(step=payment.model_copy())

    assert isinstance(first, DroppedBefore)
    assert first.step == payment
    assert first.fingerprint == second.fingerprint
    assert first.model_dump(mode="json") == {
        "kind": "dropped_before",
        "step": payment.model_dump(mode="json"),
    }
    with pytest.raises(PatternStepMismatchError) as captured:
        dropped_before(step="payment")  # type: ignore[arg-type]
    assert captured.value.location == "mv.dropped_before(step)"
    assert captured.value.repair is not None
    assert captured.value.repair.help_target.canonical_id == "dropped_before"


def test_event_metadata_variants_are_closed_and_tagged() -> None:
    common = _event_common()
    pattern = common["pattern"]
    assert isinstance(pattern, EventPattern)
    _, cart, payment = _pattern()
    reconciliation = GroupedFunnelReconciliationReceipt(
        ungrouped_hash="sha256:ungrouped",
        grouped_hash="sha256:ungrouped",
    )
    axis = SubjectAxisBinding(
        dimension_ref=RefPayloadV1.from_ref(ms.ref.dimension("commerce.users.acquisition_channel")),
        output_column="acquisition_channel",
        versioning_resolution="ordinary",
    )

    journey = EventFrameMeta(
        **common,
        query_refs=("query_1",),
        unused_event_count=0,
        unused_event_counts_by_step={"cart": 0, "payment": 0},
    )
    funnel = EventFunnelFrameMeta(
        **common,
        source_journey_ref="frame_journey",
        source_journey_fingerprint="sha256:journey",
        source_unused_event_count=0,
        axes=(axis,),
        grouped_reconciliation=reconciliation,
    )
    time_to_event = EventTimeToEventFrameMeta(
        **common,
        source_journey_ref="frame_journey",
        source_journey_fingerprint="sha256:journey",
        source_unused_end_count=0,
        start_step=cart,
        end_step=payment,
    )

    assert isinstance(journey, EventFrameMetaBase)
    assert {
        journey.semantic_kind,
        funnel.semantic_kind,
        time_to_event.semantic_kind,
    } == {"journey", "funnel", "time_to_event"}
    assert EventFrameMeta.model_validate_json(journey.model_dump_json()) == journey
    assert EventFunnelFrameMeta.model_validate_json(funnel.model_dump_json()) == funnel
    assert (
        EventTimeToEventFrameMeta.model_validate_json(time_to_event.model_dump_json())
        == time_to_event
    )
    funnel_payload = funnel.model_dump(mode="json")
    assert "start_step" not in funnel_payload
    assert "query_refs" not in funnel_payload
    assert "axes" in funnel_payload
    time_payload = time_to_event.model_dump(mode="json")
    assert "axes" in time_payload
    assert "start_step" in time_payload
    assert "end_step" in time_payload
    assert "unused_event_count" not in time_payload
    assert "query_refs" not in time_payload
    with pytest.raises(ValueError, match="first_per_subject"):
        EventFunnelFrameMeta(
            **{**common, "matching": mv.every_start(completion_assignment="exclusive")},
            source_journey_ref="frame_journey",
            source_journey_fingerprint="sha256:journey",
            source_unused_event_count=0,
            grouped_reconciliation=reconciliation,
        )
    assert pattern.fingerprint == funnel.pattern.fingerprint


def test_grouped_funnel_reconciliation_receipt_rejects_mismatched_hashes() -> None:
    with pytest.raises(ValueError, match="hashes must match"):
        GroupedFunnelReconciliationReceipt(
            ungrouped_hash="sha256:ungrouped",
            grouped_hash="sha256:grouped",
        )


def test_dropped_before_uses_target_event_coverage_not_journey_status() -> None:
    common = _event_common()
    pattern = common["pattern"]
    assert isinstance(pattern, EventPattern)
    target_event = pattern.steps[1].event
    complete_through = str(common["completion_through"])
    common["input_coverage"] = (
        EventInputCoverage(
            event_ref=RefPayloadV1.from_ref(pattern.steps[0].event),
            basis="unknown",
        ),
        EventInputCoverage(
            event_ref=RefPayloadV1.from_ref(target_event),
            basis="observed_watermark",
            receipt=EventWatermarkReceipt(
                complete_through=complete_through,
                authority="test_reconciliation",
                observed_at=complete_through,
            ),
            observed_complete_through=complete_through,
        ),
    )
    rows = pd.DataFrame(
        [
            {
                "journey_id": "journey_1",
                "completion_status": "coverage_censored",
                "subject_identity": ("user_1",),
                "step_key": pattern.steps[0].key,
                "event_identity": ("cart_1",),
                "occurred_at": pd.Timestamp("2026-07-01T00:00:00Z"),
                "elapsed_from_start": pd.Timedelta(0),
                "elapsed_from_previous": pd.Timedelta(0),
            },
            {
                "journey_id": "journey_1",
                "completion_status": "coverage_censored",
                "subject_identity": ("user_1",),
                "step_key": pattern.steps[1].key,
                "event_identity": None,
                "occurred_at": None,
                "elapsed_from_start": None,
                "elapsed_from_previous": None,
            },
        ]
    )
    common["row_count"] = len(rows)
    frame = EventFrame(
        _df=rows,
        meta=EventFrameMeta(
            **common,
            query_refs=("query_1",),
            unused_event_count=0,
            unused_event_counts_by_step={"cart": 0, "payment": 0},
        ),
    )

    selected, censored_count = _select_rows(frame, target_index=1)

    assert selected.to_dict("records") == [{"subject_identity": ("user_1",)}]
    assert censored_count == 0


def test_time_to_event_identity_restoration_and_shape_repr() -> None:
    common = _event_common()
    _, cart, payment = _pattern()
    rows = pd.DataFrame(
        [
            {
                "journey_id": "journey_1",
                "subject_identity": ["user_1"],
                "start_event_identity": ["cart_1"],
                "start_time": pd.Timestamp("2026-07-01T00:00:00Z"),
                "end_event_identity": ["payment_1"],
                "end_time": pd.Timestamp("2026-07-01T00:05:00Z"),
                "duration": pd.Timedelta(minutes=5),
                "completion_status": "complete",
            }
        ]
    )
    common["row_count"] = len(rows)
    frame = EventFrame(
        _df=rows,
        meta=EventTimeToEventFrameMeta(
            **common,
            source_journey_ref="frame_journey",
            source_journey_fingerprint="sha256:journey",
            source_unused_end_count=0,
            start_step=cart,
            end_step=payment,
        ),
    )

    output = frame.to_pandas().iloc[0]
    assert output["subject_identity"] == ("user_1",)
    assert output["start_event_identity"] == ("cart_1",)
    assert output["end_event_identity"] == ("payment_1",)
    rendered = repr(frame)
    assert "shape=time_to_event" in rendered
    assert "start=cart" in rendered
    assert "end=payment" in rendered


def test_event_reducer_shapes_cold_recover_to_exact_metadata_variants(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name="phase2_event_reducers",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    common = _event_common(
        session_id=session.id,
        project_root=str(session.project_root),
    )
    _, cart, payment = _pattern()
    funnel_rows = pd.DataFrame(
        [
            {
                "step_key": "cart",
                "cohort_count": 1,
                "resolved_cohort_count": 1,
                "entry_count": 1,
                "resolved_entry_count": 1,
                "reached_count": 1,
                "lost_count": 0,
                "conversion_from_first": 1.0,
                "conversion_from_previous": None,
                "loss_rate_from_previous": None,
                "coverage_censored_count": 0,
            },
            {
                "step_key": "payment",
                "cohort_count": 1,
                "resolved_cohort_count": 1,
                "entry_count": 1,
                "resolved_entry_count": 1,
                "reached_count": 1,
                "lost_count": 0,
                "conversion_from_first": 1.0,
                "conversion_from_previous": 1.0,
                "loss_rate_from_previous": 0.0,
                "coverage_censored_count": 0,
            },
        ]
    )
    common["ref"] = "frame_funnel"
    common["row_count"] = len(funnel_rows)
    funnel = EventFrame(
        _df=funnel_rows,
        meta=EventFunnelFrameMeta(
            **common,
            source_journey_ref="frame_journey",
            source_journey_fingerprint="sha256:journey",
            source_unused_event_count=0,
            grouped_reconciliation=GroupedFunnelReconciliationReceipt(
                ungrouped_hash="sha256:ungrouped",
                grouped_hash="sha256:ungrouped",
            ),
        ),
    )
    funnel.meta = EventFunnelFrameMeta.model_validate(
        persist_frame(session, funnel).model_dump(mode="python")
    )

    time_rows = pd.DataFrame(
        [
            {
                "journey_id": "journey_1",
                "subject_identity": ["user_1"],
                "start_event_identity": ["cart_1"],
                "start_time": pd.Timestamp("2026-07-01T00:00:00Z"),
                "end_event_identity": ["payment_1"],
                "end_time": pd.Timestamp("2026-07-01T00:05:00Z"),
                "duration": pd.Timedelta(minutes=5),
                "completion_status": "complete",
            }
        ]
    )
    common["ref"] = "frame_time_to_event"
    common["row_count"] = len(time_rows)
    time_to_event = EventFrame(
        _df=time_rows,
        meta=EventTimeToEventFrameMeta(
            **common,
            source_journey_ref="frame_journey",
            source_journey_fingerprint="sha256:journey",
            source_unused_end_count=0,
            start_step=cart,
            end_step=payment,
        ),
    )
    time_to_event.meta = EventTimeToEventFrameMeta.model_validate(
        persist_frame(session, time_to_event).model_dump(mode="python")
    )
    for job_id, intent, output in (
        ("job_funnel", "events.funnel", funnel),
        ("job_time_to_event", "events.time_to_event", time_to_event),
    ):
        persist_job_record(
            session,
            {
                "id": job_id,
                "session_id": session.id,
                "intent": intent,
                **job_semantics_from_frames(output),
                "analysis_purpose": None,
                "params": {},
                "input_frame_refs": ["frame_journey"],
                "output_frame_ref": output.ref,
                "status": "succeeded",
                "started_at": "2026-07-24T00:00:00+00:00",
                "finished_at": "2026-07-24T00:00:01+00:00",
                "duration_ms": 1000,
            },
        )
        assert session.job(job_id)["event_reducer"]["kind"] == output.semantic_shape

    mv.session._reset_process_state()
    recovered_session = mv.session.get_or_create(
        name="phase2_event_reducers",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    recovered_funnel = recovered_session.get_frame(funnel.ref)
    recovered_time = recovered_session.get_frame(time_to_event.ref)

    assert isinstance(recovered_funnel, EventFrame)
    assert isinstance(recovered_funnel.meta, EventFunnelFrameMeta)
    assert recovered_funnel.semantic_shape == "funnel"
    assert recovered_funnel.meta.model_dump(mode="json") == funnel.meta.model_dump(mode="json")
    assert isinstance(recovered_time, EventFrame)
    assert isinstance(recovered_time.meta, EventTimeToEventFrameMeta)
    assert recovered_time.semantic_shape == "time_to_event"
    assert recovered_time.meta.model_dump(mode="json") == time_to_event.meta.model_dump(mode="json")
    assert recovered_time.to_pandas()["subject_identity"].iloc[0] == ("user_1",)


def test_subject_set_is_privacy_safe_and_cold_recovers(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name="phase2_subject_set",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    _, _, payment = _pattern()
    selection = dropped_before(step=payment)
    rows = pd.DataFrame(
        {
            "subject_identity": [
                ["private_user_1"],
                ["private_user_2"],
            ]
        }
    )
    meta = SubjectSetMeta(
        ref="frame_subjects",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_select_subjects",
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
        row_count=len(rows),
        byte_size=0,
        lineage=Lineage(),
        catalog_definition_fingerprint="sha256:catalog",
        subject_entity_ref=RefPayloadV1.from_ref(ms.ref.entity("commerce.users")),
        subject_identity=("commerce.users.user_id",),
        source=SubjectSetSourceBinding(
            artifact_ref="frame_journey",
            artifact_fingerprint="sha256:journey",
        ),
        selection=selection,
        selection_fingerprint=selection.fingerprint,
        selected_count=2,
        excluded_coverage_censored_count=0,
        coverage_status="ready",
    )
    frame = SubjectSet(_df=rows, meta=meta)

    assert frame.to_pandas()["subject_identity"].tolist() == [
        ("private_user_1",),
        ("private_user_2",),
    ]
    assert "private_user" not in repr(frame)
    assert "private_user" not in frame.render()
    persisted = persist_frame(session, frame)
    frame.meta = SubjectSetMeta.model_validate(persisted.model_dump(mode="python"))
    persist_job_record(
        session,
        {
            "id": "job_select_subjects",
            "session_id": session.id,
            "intent": "select_subjects",
            **job_semantics_from_frames(frame),
            "analysis_purpose": None,
            "params": {
                "selection_fingerprint": selection.fingerprint,
            },
            "input_frame_refs": ["frame_journey"],
            "output_frame_ref": frame.ref,
            "status": "succeeded",
            "started_at": "2026-07-24T00:00:00+00:00",
            "finished_at": "2026-07-24T00:00:01+00:00",
            "duration_ms": 1000,
        },
    )
    job_payload = session.job("job_select_subjects")
    assert job_payload["schema"] == "marivo.analysis_job/v2"
    assert "private_user" not in repr(job_payload)

    mv.session._reset_process_state()
    recovered_session = mv.session.get_or_create(
        name="phase2_subject_set",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    recovered = recovered_session.get_frame(frame.ref)

    assert isinstance(recovered, SubjectSet)
    assert recovered.meta.model_dump(mode="json") == frame.meta.model_dump(mode="json")
    assert recovered.to_pandas().to_dict("records") == frame.to_pandas().to_dict("records")
    binding = recovered.meta.cohort_binding()
    assert binding.artifact_ref == recovered.ref
    assert binding.artifact_fingerprint == recovered.meta.content_hash
    assert not hasattr(SubjectSet, "from_pandas")


def test_derived_event_and_subject_jobs_preserve_typed_authority_without_rows() -> None:
    common = _event_common()
    funnel = EventFrame(
        _df=pd.DataFrame(),
        meta=EventFunnelFrameMeta(
            **common,
            source_journey_ref="frame_journey",
            source_journey_fingerprint="sha256:journey",
            source_unused_event_count=0,
            grouped_reconciliation=GroupedFunnelReconciliationReceipt(
                ungrouped_hash="sha256:ungrouped",
                grouped_hash="sha256:ungrouped",
            ),
        ),
    )
    funnel_job = job_semantics_from_frames(funnel)

    assert funnel_job["event_reducer"]["kind"] == "funnel"
    assert funnel_job["event_reducer"]["source_artifact_ref"] == "frame_journey"
    assert "event_journey" not in funnel_job
    assert "query_refs" not in funnel_job["event_reducer"]

    _, _, payment = _pattern()
    selection = dropped_before(step=payment)
    subject_rows = pd.DataFrame({"subject_identity": [["private_subject"]]})
    subjects = SubjectSet(
        _df=subject_rows,
        meta=SubjectSetMeta(
            ref="frame_subjects",
            session_id="sess_subjects",
            project_root="/tmp/marivo-subjects",
            produced_by_job="job_subjects",
            created_at=datetime(2026, 7, 24, tzinfo=UTC),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            catalog_definition_fingerprint="sha256:catalog",
            subject_entity_ref=RefPayloadV1.from_ref(ms.ref.entity("commerce.users")),
            subject_identity=("commerce.users.user_id",),
            source=SubjectSetSourceBinding(
                artifact_ref="frame_journey",
                artifact_fingerprint="sha256:journey",
            ),
            selection=selection,
            selection_fingerprint=selection.fingerprint,
            selected_count=1,
            excluded_coverage_censored_count=0,
            coverage_status="ready",
        ),
    )
    subject_job = job_semantics_from_frames(subjects)

    assert subject_job["subject"]["kind"] == "subject_set"
    assert subject_job["subject_set"]["selection"]["kind"] == "dropped_before"
    assert "private_subject" not in repr(subject_job)


def test_subject_set_rejects_duplicate_or_unsorted_identities() -> None:
    _, _, payment = _pattern()
    selection = dropped_before(step=payment)

    def meta_for(count: int) -> SubjectSetMeta:
        return SubjectSetMeta(
            ref="frame_subjects",
            session_id="sess_subjects",
            project_root="/tmp/marivo-subjects",
            produced_by_job="job_subjects",
            created_at=datetime(2026, 7, 24, tzinfo=UTC),
            row_count=count,
            byte_size=0,
            lineage=Lineage(),
            catalog_definition_fingerprint="sha256:catalog",
            subject_entity_ref=RefPayloadV1.from_ref(ms.ref.entity("commerce.users")),
            subject_identity=("commerce.users.user_id",),
            source=SubjectSetSourceBinding(
                artifact_ref="frame_journey",
                artifact_fingerprint="sha256:journey",
            ),
            selection=selection,
            selection_fingerprint=selection.fingerprint,
            selected_count=count,
            excluded_coverage_censored_count=0,
            coverage_status="ready",
        )

    with pytest.raises(ValueError, match="unique"):
        SubjectSet(
            _df=pd.DataFrame({"subject_identity": [["u1"], ["u1"]]}),
            meta=meta_for(2),
        )
    with pytest.raises(ValueError, match="deterministically ordered"):
        SubjectSet(
            _df=pd.DataFrame({"subject_identity": [["u2"], ["u1"]]}),
            meta=meta_for(2),
        )


def test_subject_set_meta_requires_coverage_and_selection_consistency() -> None:
    _, _, payment = _pattern()
    selection = dropped_before(step=payment)
    base: dict[str, object] = {
        "ref": "frame_subjects",
        "session_id": "sess_subjects",
        "project_root": "/tmp/marivo-subjects",
        "produced_by_job": "job_subjects",
        "created_at": datetime(2026, 7, 24, tzinfo=UTC),
        "row_count": 0,
        "byte_size": 0,
        "lineage": Lineage(),
        "catalog_definition_fingerprint": "sha256:catalog",
        "subject_entity_ref": RefPayloadV1.from_ref(ms.ref.entity("commerce.users")),
        "subject_identity": ("commerce.users.user_id",),
        "source": SubjectSetSourceBinding(
            artifact_ref="frame_journey",
            artifact_fingerprint="sha256:journey",
        ),
        "selection": selection,
        "selection_fingerprint": selection.fingerprint,
        "selected_count": 0,
        "excluded_coverage_censored_count": 1,
        "coverage_status": "coverage_censored",
    }
    assert SubjectSetMeta(**base).coverage_status == "coverage_censored"
    with pytest.raises(ValueError, match="selection_fingerprint"):
        SubjectSetMeta(**{**base, "selection_fingerprint": "sha256:wrong"})
    with pytest.raises(ValueError, match="coverage_status"):
        SubjectSetMeta(**{**base, "coverage_status": "ready"})


def test_subject_set_meta_is_a_current_artifact_meta() -> None:
    assert issubclass(SubjectSetMeta, BaseFrameMeta)
