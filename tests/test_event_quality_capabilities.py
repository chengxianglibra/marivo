"""Event Journey quality, capability, and discovery contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import ValidationError

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis._capabilities.validation import (
    classify_input_family,
    validate_capability_inputs,
)
from marivo.analysis.errors import AnalysisError
from marivo.analysis.event import first_per_subject, sequence, step
from marivo.analysis.frames.event import EventFrame, EventFrameMeta, EventInputCoverage
from marivo.analysis.intents._quality_checks import run_event_journey_checks
from marivo.analysis.lineage import Lineage
from marivo.refs import RefPayloadV1


def _event_frame(session: mv.Session) -> EventFrame:
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    user = ms.ref.entity("commerce.users")
    cart_step = step(
        participant=ms.participant_role(event=cart, name="user"),
        key="cart",
    )
    payment_step = step(
        participant=ms.participant_role(event=payment, name="buyer"),
        key="payment",
    )
    pattern = sequence(cart_step, payment_step)
    rows = pd.DataFrame(
        [
            {
                "journey_id": "journey_1",
                "completion_status": "coverage_censored",
                "subject_identity": ("user_1",),
                "step_key": "cart",
                "event_identity": ("cart_1",),
                "occurred_at": pd.Timestamp("2026-07-01T10:00:00Z"),
                "elapsed_from_start": pd.Timedelta(0),
                "elapsed_from_previous": pd.Timedelta(0),
            },
            {
                "journey_id": "journey_1",
                "completion_status": "coverage_censored",
                "subject_identity": ("user_1",),
                "step_key": "payment",
                "event_identity": None,
                "occurred_at": pd.NaT,
                "elapsed_from_start": pd.NaT,
                "elapsed_from_previous": pd.NaT,
            },
        ]
    )
    meta = EventFrameMeta(
        ref="frame_event_quality",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_event_match",
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        row_count=len(rows),
        byte_size=0,
        lineage=Lineage(),
        catalog_definition_fingerprint="sha256:catalog",
        subject_entity_ref=RefPayloadV1.from_ref(user),
        subject_identity=("commerce.users.user_id",),
        pattern=pattern,
        matching=first_per_subject(),
        cohort_window=mv.TimeScope(
            start="2026-07-01T00:00:00Z",
            end="2026-07-02T00:00:00Z",
        ),
        completion_through="2026-07-03T00:00:00Z",
        input_coverage=(
            EventInputCoverage(
                event_ref=RefPayloadV1.from_ref(cart),
                basis="unknown",
            ),
            EventInputCoverage(
                event_ref=RefPayloadV1.from_ref(payment),
                basis="unknown",
            ),
        ),
        coverage_basis="unknown",
        event_fingerprints={
            cart.path: "sha256:cart",
            payment.path: "sha256:payment",
        },
        event_identity_components={
            cart.path: (RefPayloadV1.from_ref(ms.ref.dimension("commerce.events.event_id")),),
            payment.path: (RefPayloadV1.from_ref(ms.ref.dimension("commerce.events.event_id")),),
        },
        role_endpoints={
            "cart": RefPayloadV1.from_ref(user),
            "payment": RefPayloadV1.from_ref(user),
        },
        unused_event_count=1,
    )
    return EventFrame(_df=rows, meta=meta)


def test_event_journey_quality_report_is_typed_and_discloses_coverage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name="event_quality",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    frame = _event_frame(session)

    report = session.assess_quality(frame)

    assert report.meta.report_shape == "event_journey"
    assert report.meta.target_kind == "event_frame"
    assert report.meta.target_semantic_kind == "journey"
    assert report.meta.target_event_pattern_fingerprint == frame.meta.pattern.fingerprint
    assert report.meta.target_coverage_basis == "unknown"
    assert report.meta.overall_status == "warning"
    assert report.evidence_status == "complete"
    assert report.meta.analysis_scope is not None
    assert report.meta.analysis_scope.kind == "event"
    assert set(report.to_pandas()["check_kind"]) == {
        "event_row_contract",
        "event_identity",
        "event_participant",
        "event_ordering",
        "event_coverage",
        "declared_completeness_used",
        "event_censoring",
    }
    assert {issue.kind for issue in report.meta.issues} == {
        "event_coverage_unknown",
        "event_censoring_present",
    }
    persisted = json.dumps(report.meta.model_dump(mode="json"), sort_keys=True)
    assert "user_1" not in persisted
    assert "cart_1" not in persisted


def test_event_capability_family_gate_and_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_capability", use_datasources=False)
    frame = _event_frame(session)

    assert classify_input_family(frame) == "EventFrame"
    assert classify_input_family(frame.meta.pattern) == "EventPattern"
    assert classify_input_family(frame.meta.matching) == "EventMatchingPolicy"
    validate_capability_inputs("assess_quality", target=frame)
    validate_capability_inputs(
        "events.match",
        pattern=frame.meta.pattern,
        cohort_window=frame.meta.cohort_window,
        matching=frame.meta.matching,
        completeness=frame.meta.completeness,
    )

    with pytest.raises(AnalysisError) as captured:
        validate_capability_inputs(
            "events.match",
            pattern=frame.meta.pattern.steps[0].event,
        )
    assert captured.value.location == "events.match.pattern"

    affordances = {item.capability_id for item in frame.contract().affordances}
    assert affordances == {"assess_quality"}


def test_event_quality_blocks_dense_row_and_coverage_contract_violations(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_quality_invalid", use_datasources=False)
    frame = _event_frame(session)
    broken_rows = frame._dataframe_copy()
    broken_rows.loc[1, "occurred_at"] = pd.Timestamp("2026-07-01T11:00:00Z")
    broken_rows.loc[1, "event_identity"] = ("payment_1",)
    broken_rows.loc[1, "elapsed_from_start"] = pd.Timedelta(minutes=5)
    broken_rows.loc[1, "elapsed_from_previous"] = pd.Timedelta(hours=1)
    broken = EventFrame(
        _df=broken_rows,
        meta=frame.meta.model_copy(update={"input_coverage": frame.meta.input_coverage[:1]}),
    )

    checks = {row["check_id"]: row for row in run_event_journey_checks(broken)}

    assert checks["event_row_contract"]["severity"] == "blocking"
    assert checks["event_coverage:commerce.payment_succeeded"]["severity"] == "blocking"


def test_event_coverage_rejects_unsupported_authority_and_aggregate_claims(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_coverage_invariants", use_datasources=False)
    frame = _event_frame(session)
    cart = ms.ref.event("commerce.cart_created")

    with pytest.raises(ValidationError, match="requires a receipt"):
        EventInputCoverage(
            event_ref=RefPayloadV1.from_ref(cart),
            basis="observed_watermark",
        )

    receipt = mv.EventWatermarkReceipt(
        complete_through=frame.meta.completion_through,
        authority="warehouse_reconciliation",
        observed_at="2026-07-03T01:00:00Z",
    )
    observed = tuple(
        EventInputCoverage(
            event_ref=item.event_ref,
            basis="observed_watermark",
            receipt=receipt,
            observed_complete_through=receipt.complete_through,
        )
        for item in frame.meta.input_coverage
    )
    with pytest.raises(ValidationError, match="coverage_basis must be 'observed_watermark'"):
        EventFrameMeta.model_validate(
            {
                **frame.meta.model_dump(mode="python"),
                "input_coverage": observed,
                "coverage_basis": "unknown",
            }
        )
    stale_receipt = receipt.model_copy(update={"complete_through": "2026-07-02T23:59:59Z"})
    stale_observed = tuple(
        EventInputCoverage(
            event_ref=item.event_ref,
            basis="observed_watermark",
            receipt=stale_receipt,
            observed_complete_through=stale_receipt.complete_through,
        )
        for item in frame.meta.input_coverage
    )
    with pytest.raises(ValidationError, match="must cover completion_through"):
        EventFrameMeta.model_validate(
            {
                **frame.meta.model_dump(mode="python"),
                "input_coverage": stale_observed,
                "coverage_basis": "observed_watermark",
            }
        )


def test_event_quality_blocks_forged_observed_coverage_without_receipts(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_coverage_quality", use_datasources=False)
    frame = _event_frame(session)
    forged = tuple(
        EventInputCoverage.model_construct(
            event_ref=item.event_ref,
            basis="observed_watermark",
            receipt=None,
            declaration_fingerprint=None,
            declaration_rationale=None,
            observed_complete_through=None,
        )
        for item in frame.meta.input_coverage
    )
    frame.meta = frame.meta.model_copy(
        update={
            "input_coverage": forged,
            "coverage_basis": "observed_watermark",
        }
    )

    checks = {row["check_id"]: row for row in run_event_journey_checks(frame)}

    assert checks["event_coverage:commerce.cart_created"]["severity"] == "blocking"
    assert checks["event_coverage:commerce.payment_succeeded"]["severity"] == "blocking"
    assert (
        "observed_watermark_receipt_missing"
        in checks["event_coverage:commerce.cart_created"]["details_json"]
    )


def test_event_watermark_types_have_registered_public_help() -> None:
    request_help = mv.help_text(mv.EventWatermarkRequest)
    receipt_help = mv.help_text("EventWatermarkReceipt")

    assert request_help.startswith("EventWatermarkRequest")
    assert "event_fingerprint" in request_help
    assert "required_through" in request_help
    assert receipt_help.startswith("EventWatermarkReceipt")
    assert "complete_through" in receipt_help
    assert "authority" in receipt_help


def test_only_phase_one_event_capabilities_are_discoverable(tmp_path, monkeypatch) -> None:
    expected = {
        "events.match",
        "step",
        "sequence",
        "first_per_subject",
        "every_start",
        "declared_complete_through",
    }
    assert expected.issubset(REGISTRY.capability_ids)
    assert {
        "events.funnel",
        "events.time_to_event",
        "select_subjects",
        "lifecycle.match",
        "lifecycle.transition",
    }.isdisjoint(REGISTRY.capability_ids)

    rendered = mv.help_text("events.match")
    assert "session.events.match" in rendered
    assert "EventFrame" in rendered
    assert "QualityReport[event_journey]" in mv.help_text("QualityReport")

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_discovery", use_datasources=False)
    assert ".events.match()" in session.render()
    assert callable(session.events.match)
    assert mv.help_text(session.events.match).startswith("events.match")
    assert not hasattr(session.events, "funnel")
    assert not hasattr(session.events, "time_to_event")
    assert not hasattr(session, "select_subjects")
    assert not hasattr(session, "lifecycle")


@pytest.mark.parametrize("assignment", ["exclusive", "shared"])
def test_event_frame_render_discloses_complete_matching_policy(
    tmp_path,
    monkeypatch,
    assignment: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name=f"event_render_{assignment}",
        use_datasources=False,
    )
    frame = _event_frame(session)
    frame.meta = frame.meta.model_copy(
        update={
            "matching": mv.every_start(
                completion_assignment=assignment,  # type: ignore[arg-type]
            )
        }
    )

    rendered = frame.render()

    assert "matching=every_start" in rendered
    assert f"completion_assignment={assignment}" in rendered
