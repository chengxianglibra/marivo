"""Event Journey evidence extraction and digest contracts."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from marivo._compat import UTC
from marivo.analysis.evidence.digest import build_artifact_digest
from marivo.analysis.evidence.extraction.event import (
    extract_event_funnel_finding,
    extract_event_journey_finding,
    extract_event_time_to_event_finding,
)
from marivo.analysis.evidence.extraction.subject import extract_subject_set_finding
from marivo.analysis.evidence.types import (
    EventAnalysisScope,
    EventFunnelObservationValue,
    EventJourneyObservationValue,
    EventSubject,
    EventTimeToEventObservationValue,
    ObservationFact,
    OperatorSemantics,
    SubjectSetObservationValue,
    SubjectSetSubject,
)
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory


def _event_subject() -> EventSubject:
    return EventSubject(
        subject_entity_ref=RefPayloadV1.from_ref(ref_factory.entity("commerce.customers")),
        subject_identity_signature=("commerce.customers.customer_id",),
    )


def _event_scope() -> EventAnalysisScope:
    return EventAnalysisScope(
        pattern={"fingerprint": "sha256:pattern"},
        roles=({"step_key": "checkout", "participant_name": "buyer"},),
        matching={"kind": "first_per_subject"},
        cohort_window={"start": "2026-07-01", "end": "2026-07-08"},
        completion_through="2026-07-15",
        coverage={"basis": "unknown"},
    )


def test_event_journey_finding_counts_attempts_without_persisting_identities() -> None:
    finding = extract_event_journey_finding(
        df=pd.DataFrame(
            {
                "journey_id": ["journey-a", "journey-a", "journey-b", "journey-c"],
                "completion_status": [
                    "complete",
                    "complete",
                    "incomplete",
                    "coverage_censored",
                ],
                "subject_identity": [
                    ("customer-raw-a",),
                    ("customer-raw-a",),
                    ("customer-raw-b",),
                    ("customer-raw-c",),
                ],
            }
        ),
        artifact_id="art_event",
        session_id="sess_1",
        subject=_event_subject(),
        committed_at=datetime.now(UTC),
        unused_event_count=4,
        source_refs=("event:commerce.checkout_started",),
    )

    value = finding.value.value
    assert isinstance(value, EventJourneyObservationValue)
    assert value.model_dump() == {
        "shape": "event_journey",
        "attempt_count": 3,
        "complete_count": 1,
        "incomplete_count": 1,
        "coverage_censored_count": 1,
        "unused_event_count": 4,
    }
    assert "customer-raw" not in finding.model_dump_json()


def test_event_journey_digest_uses_bounded_observation_variant() -> None:
    finding = extract_event_journey_finding(
        df=pd.DataFrame(
            {
                "journey_id": ["journey-a"],
                "completion_status": ["complete"],
            }
        ),
        artifact_id="art_event",
        session_id="sess_1",
        subject=_event_subject(),
        committed_at=datetime.now(UTC),
        unused_event_count=0,
        source_refs=("event:commerce.checkout_started",),
    )

    digest = build_artifact_digest(
        artifact_ref="art_event",
        operator=OperatorSemantics(
            operator="events.match",
            operator_version="v1",
            artifact_family="event_frame",
            semantic_shape="journey",
        ),
        subject=_event_subject(),
        scope=_event_scope(),
        findings=(finding,),
        quality=None,
        rows_available=True,
    )

    assert len(digest.items) == 1
    item = digest.items[0]
    assert isinstance(item, ObservationFact)
    assert isinstance(item.value, EventJourneyObservationValue)
    assert len(digest.boundaries) <= 3


def test_event_funnel_finding_recomputes_rates_without_axis_values() -> None:
    finding = extract_event_funnel_finding(
        df=pd.DataFrame(
            {
                "channel": ["organic", "paid", "organic", "paid"],
                "step_key": ["cart", "cart", "payment", "payment"],
                "cohort_count": [2, 1, 2, 1],
                "resolved_cohort_count": [2, 1, 2, 1],
                "entry_count": [2, 1, 2, 1],
                "resolved_entry_count": [2, 1, 2, 1],
                "reached_count": [2, 1, 1, 0],
                "lost_count": [0, 0, 1, 1],
                "coverage_censored_count": [0, 0, 0, 0],
                "conversion_from_first": [1.0, 1.0, 0.5, 0.0],
                "conversion_from_previous": [None, None, 0.5, 0.0],
            }
        ),
        artifact_id="art_funnel",
        session_id="sess_1",
        subject=_event_subject().model_copy(update={"analysis_axis": "funnel"}),
        committed_at=datetime.now(UTC),
        step_order=("cart", "payment"),
        axis_columns=("channel",),
        reconciliation_passed=True,
        source_unused_event_count=4,
        source_refs=("art_journey",),
    )

    value = finding.value.value
    assert isinstance(value, EventFunnelObservationValue)
    assert value.cohort_count == 3
    assert value.axis_tuple_count == 2
    assert value.source_unused_event_count == 4
    assert value.steps[1].reached_count == 1
    assert value.steps[1].conversion_from_previous == 1 / 3
    assert "organic" not in finding.model_dump_json()
    assert "paid" not in finding.model_dump_json()


def test_event_time_to_event_finding_is_bounded_and_identity_safe() -> None:
    finding = extract_event_time_to_event_finding(
        df=pd.DataFrame(
            {
                "completion_status": ["complete", "incomplete"],
                "duration": [pd.Timedelta(hours=2), pd.NaT],
                "subject_identity": [("raw-a",), ("raw-b",)],
            }
        ),
        artifact_id="art_elapsed",
        session_id="sess_1",
        subject=_event_subject().model_copy(update={"analysis_axis": "time_to_event"}),
        committed_at=datetime.now(UTC),
        source_unused_end_count=3,
        source_refs=("art_journey",),
    )

    value = finding.value.value
    assert isinstance(value, EventTimeToEventObservationValue)
    assert value.qualifying_count == 2
    assert value.duration_count == 1
    assert value.source_unused_end_count == 3
    assert value.median_duration_seconds == 7200
    assert "raw-a" not in finding.model_dump_json()


def test_subject_set_finding_discloses_censoring_without_identities() -> None:
    finding = extract_subject_set_finding(
        df=pd.DataFrame({"subject_identity": [("raw-subject",)]}),
        artifact_id="art_subjects",
        session_id="sess_1",
        subject=SubjectSetSubject(
            subject_entity_ref=RefPayloadV1.from_ref(ref_factory.entity("commerce.customers")),
            subject_identity_signature=("commerce.customers.customer_id",),
        ),
        committed_at=datetime.now(UTC),
        excluded_coverage_censored_count=2,
        coverage_status="coverage_censored",
        source_refs=("art_journey",),
    )

    value = finding.value.value
    assert isinstance(value, SubjectSetObservationValue)
    assert value.selected_count == 1
    assert value.excluded_coverage_censored_count == 2
    assert "raw-subject" not in finding.model_dump_json()
