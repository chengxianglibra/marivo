"""Event Journey evidence extraction and digest contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from marivo.analysis.evidence.digest import build_artifact_digest
from marivo.analysis.evidence.extraction.event import extract_event_journey_finding
from marivo.analysis.evidence.types import (
    EventAnalysisScope,
    EventJourneyObservationValue,
    EventSubject,
    ObservationFact,
    OperatorSemantics,
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
