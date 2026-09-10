"""Bounded journey validation and atomic identity-free Event Evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

import ibis.expr.datatypes as dt
import pyarrow as pa

from marivo.analysis.domains.completeness import BoundedCompletenessDeclarationV1
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.event import FirstPerSubject
from marivo.analysis.evidence._dataset_types import Finding
from marivo.analysis.materialization.contracts import ArtifactDescriptor, invalid, parse_timestamp
from marivo.analysis.materialization.event_codec import (
    EventEvidenceSummary,
    decode_evidence,
    evidence_payload,
    retained_declarations,
    validate_semantics,
)
from marivo.analysis.materialization.storage import _compare, _matches_type, _Value, _value


def _identity_type(logical: str, physical: pa.DataType) -> bool:
    """Keep parameterized identity components exact without widening their contract."""
    if _matches_type(logical, physical):
        return True
    try:
        expected: pa.DataType = dt.dtype(logical).to_pyarrow()
    except (ValueError, TypeError):
        return False
    return bool(expected.equals(physical))


def _semantics(descriptor: ArtifactDescriptor) -> EventJourneySemantics:
    result = descriptor.row_contract.family_semantics
    if not isinstance(result, EventJourneySemantics):
        raise invalid("missing Event journey authority")
    return result


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    semantics = _semantics(descriptor)
    validate_semantics(semantics)
    if (
        descriptor.population_authority.entity_ref != semantics.subject_entity_ref
        or descriptor.population_authority.identity_signature
        != semantics.subject_identity_signature
    ):
        raise invalid("Event subject identity differs from retained Population authority")
    summary = descriptor.event_evidence
    if summary is None or summary.row_count != descriptor.storage_receipt.realized_row_count:
        raise invalid("missing or inconsistent Event journey Evidence")
    # Decode again at publication so native, in-process values have the same closed contract.
    decode_evidence(evidence_payload(summary))
    count = len(semantics.pattern.steps)
    if (
        summary.row_count != count * summary.journey_count
        or summary.matched_row_count + summary.missing_row_count != summary.row_count
        or summary.complete_journey_count
        + summary.incomplete_journey_count
        + summary.censored_journey_count
        != summary.journey_count
        or not summary.subject_count <= summary.journey_count <= summary.matched_row_count
        or (summary.subject_count == 0) != (summary.journey_count == 0)
        or summary.missing_row_count
        < summary.incomplete_journey_count + summary.censored_journey_count
        or summary.missing_row_count
        > (summary.incomplete_journey_count + summary.censored_journey_count) * (count - 1)
        or (
            isinstance(semantics.matching, FirstPerSubject)
            and summary.subject_count != summary.journey_count
        )
    ):
        raise invalid("Event journey Evidence contradicts dense matching counts")
    if (summary.coverage.complete and summary.censored_journey_count) or (
        not summary.coverage.complete and summary.incomplete_journey_count
    ):
        raise invalid("Event journey completion status contradicts coverage authority")
    expected = tuple(
        dict.fromkeys(
            zip(
                (step.event.key for step in semantics.pattern.steps),
                semantics.step_event_fingerprints,
                semantics.source_origins,
                strict=True,
            )
        )
    )
    actual = tuple(
        (fact.event_ref, fact.event_fingerprint, fact.source_origin_ref)
        for fact in summary.coverage.events
    )
    if actual != expected:
        raise invalid("Event coverage is not bound to the exact retained source authority")
    start = parse_timestamp(semantics.cohort_start)
    through = parse_timestamp(semantics.completion_through)
    declarations = {
        event.key: declaration
        for declaration in retained_declarations(semantics)
        for event in declaration.inputs
    }
    for fact in summary.coverage.events:
        declaration = declarations.get(fact.event_ref)
        if (
            declaration is not None
            and fact.basis != "declared"
            and not (fact.basis == "observed" and fact.complete)
        ):
            raise invalid("Event coverage discards a complete retained declaration")
        original = fact.supplemented_observation
        if (
            original is not None
            and (original.complete_from is None or parse_timestamp(original.complete_from) <= start)
            and parse_timestamp(original.complete_through) >= through
        ):
            raise invalid("supplemented Event observation already covers the required source range")
        if fact.basis != "unknown":
            if fact.complete_through is None:
                raise invalid("Event coverage lacks its exact follow-up boundary")
            covers = (
                fact.complete_from is None or parse_timestamp(fact.complete_from) <= start
            ) and parse_timestamp(fact.complete_through) >= through
            if covers != fact.complete:
                raise invalid("Event coverage interval contradicts its completeness classification")
        if fact.basis == "declared" and (
            declaration is None
            or (
                fact.complete_from
                != (
                    declaration.complete_from.isoformat()
                    if isinstance(declaration, BoundedCompletenessDeclarationV1)
                    else None
                )
                or fact.complete_through != declaration.complete_through.isoformat()
                or fact.rationale != declaration.rationale
            )
        ):
            raise invalid("Event declared coverage differs from its exact retained assumption")
    if (
        any(part.contract_id != "population_sampling_state" for part in descriptor.retained_parts)
        or descriptor.comparison_inputs
        or descriptor.dataset_materialization_contract.finding_extractor_id != "none"
        or descriptor.dataset_materialization_contract.finding_policy_id != "zero_findings@v1"
    ):
        raise invalid("unregistered Event journey retained authority or Finding policy")


def bind_event_summary(
    descriptor: ArtifactDescriptor, summary: EventEvidenceSummary
) -> ArtifactDescriptor:
    """Bind source-side bounded proof to the exact staged descriptor before commit."""
    result = replace(descriptor, event_evidence=summary)
    validate_descriptor(result)
    return result


class EventRowValidator:
    """Validate dense ordered rows using only the current journey and previous anchor."""

    def __init__(self, semantics: EventJourneySemantics) -> None:
        self.semantics = semantics
        self.steps = semantics.pattern.steps
        self.step_keys = tuple(step.key for step in self.steps)
        self.start = parse_timestamp(semantics.cohort_start)
        self.end = parse_timestamp(semantics.cohort_end)
        self.through = parse_timestamp(semantics.completion_through)
        self.anchor: tuple[_Value, datetime, tuple[_Value, ...]] | None = None
        self.journey: str | None = None
        self.status: str | None = None
        self.ordinal = 0
        self.previous_time: datetime | None = None
        self.previous_identity: tuple[_Value, ...] | None = None
        self.occurrences: list[tuple[str, tuple[_Value, ...]]] = []
        self.missing = False
        self.row_count = 0
        self.journey_count = 0
        self.subject_count = 0
        self.matched_row_count = 0
        self.missing_row_count = 0
        self.complete_journey_count = 0
        self.incomplete_journey_count = 0
        self.censored_journey_count = 0

    def _finish_journey(self) -> None:
        if self.journey is None:
            return
        if self.ordinal != len(self.steps) or (self.status == "complete") == self.missing:
            raise invalid("Event journey density or completion status is inconsistent")

    def accept(self, batch: pa.RecordBatch) -> None:
        subject_type = batch.schema.field("entity_identity").type
        if (
            not pa.types.is_struct(subject_type)
            or len(subject_type) != len(self.semantics.subject_identity_signature)
            or any(
                field.name != name or not _identity_type(logical, field.type)
                for field, (name, logical) in zip(
                    subject_type, self.semantics.subject_identity_signature, strict=True
                )
            )
        ):
            raise invalid(
                "Event subject identity schema contradicts its complete governed signature"
            )
        identity_type = batch.schema.field("event_identity").type
        if (
            not pa.types.is_struct(identity_type)
            or len(identity_type) != len(self.semantics.occurrence_identity_types)
            or any(
                field.name != f"k{index}" or not _identity_type(logical, field.type)
                for index, (field, logical) in enumerate(
                    zip(identity_type, self.semantics.occurrence_identity_types, strict=True)
                )
            )
        ):
            raise invalid("Event occurrence identity schema contradicts governed tuple authority")
        # This internal stream is normalized by the compiler, not a source timezone parser.
        time_type = batch.schema.field("occurred_at").type
        if not pa.types.is_timestamp(time_type) or time_type.tz not in ("UTC", "Etc/UTC", "+00:00"):
            raise invalid("Event occurrence instant is not normalized to UTC")
        if any(
            not pa.types.is_int64(batch.schema.field(name).type)
            for name in ("elapsed_from_start", "elapsed_from_previous")
        ):
            raise invalid("Event duration requires exact signed microseconds")
        for offset in range(batch.num_rows):
            self._accept_row(
                {name: _value(batch.column(name)[offset]) for name in batch.schema.names}
            )

    def _accept_row(self, row: dict[str, _Value]) -> None:
        journey, status, subject, key = (
            row["journey_id"],
            row["completion_status"],
            row["entity_identity"],
            row["step_key"],
        )
        if (
            type(journey) is not str
            or re.fullmatch(r"journey_[0-9a-f]{64}", journey) is None
            or status not in ("complete", "incomplete", "coverage_censored")
            or not isinstance(subject, tuple)
            or any(value is None for value in subject)
        ):
            raise invalid("invalid Event journey coordinate or closed status")
        identity, occurred = row["event_identity"], row["occurred_at"]
        if identity is None:
            if any(
                row[name] is not None
                for name in ("occurred_at", "elapsed_from_start", "elapsed_from_previous")
            ):
                raise invalid("missing Event occurrence carries time or elapsed values")
        elif (
            not isinstance(identity, tuple)
            or any(value is None for value in identity)
            or not isinstance(occurred, datetime)
            or occurred.tzinfo is None
            or not self.start <= occurred < self.through
        ):
            raise invalid("Event occurrence has incomplete identity or invalid follow-up instant")
        if journey != self.journey:
            self._finish_journey()
            if (
                key != self.step_keys[0]
                or not isinstance(identity, tuple)
                or not isinstance(occurred, datetime)
            ):
                raise invalid("Event journey does not begin with its exact present initial step")
            if not self.start <= occurred < self.end:
                raise invalid("Event journey anchor lies outside the cohort window")
            anchor = (subject, occurred, identity)
            if self.anchor is not None:
                order = _compare(self.anchor, anchor)
                if order >= 0:
                    raise invalid("Event journey anchors are duplicate or out of canonical order")
                if subject == self.anchor[0] and isinstance(
                    self.semantics.matching, FirstPerSubject
                ):
                    raise invalid(
                        "first-per-subject Event matching contains repeated subject journeys"
                    )
            if self.anchor is None or self.anchor[0] != subject:
                self.subject_count += 1
            self.anchor, self.journey, self.status = anchor, journey, str(status)
            self.ordinal = 0
            self.previous_time = None
            self.previous_identity = None
            self.occurrences.clear()
            self.missing = False
            self.journey_count += 1
            self.complete_journey_count += status == "complete"
            self.incomplete_journey_count += status == "incomplete"
            self.censored_journey_count += status == "coverage_censored"
        if (
            self.ordinal >= len(self.steps)
            or key != self.step_keys[self.ordinal]
            or status != self.status
            or self.anchor is None
            or subject != self.anchor[0]
        ):
            raise invalid("Event journey steps are not dense in retained Pattern order")
        if isinstance(identity, tuple) and isinstance(occurred, datetime):
            if self.missing:
                raise invalid("Event journey contains an occurrence after a missing step")
            pair = (self.steps[self.ordinal].event.key, identity)
            if pair in self.occurrences:
                raise invalid("Event journey reuses the same governed occurrence")
            if (
                self.previous_time is not None
                and self.previous_identity is not None
                and _compare((self.previous_time, self.previous_identity), (occurred, identity))
                >= 0
            ):
                raise invalid("Event journey occurrence order is not strictly increasing")
            self.occurrences.append(pair)
            previous = self.previous_time if self.previous_time is not None else occurred
            for name, base in (
                ("elapsed_from_start", self.anchor[1]),
                ("elapsed_from_previous", previous),
            ):
                duration = occurred - base
                expected = (
                    duration.days * 86400 + duration.seconds
                ) * 1_000_000 + duration.microseconds
                if type(row[name]) is not int or row[name] != expected:
                    raise invalid(
                        "Event journey elapsed duration contradicts its occurrence instants"
                    )
            self.previous_time, self.previous_identity = occurred, identity
            self.matched_row_count += 1
        else:
            self.missing = True
            self.missing_row_count += 1
        self.row_count += 1
        self.ordinal += 1

    def finish(self) -> None:
        self._finish_journey()

    def summary(self, original: EventEvidenceSummary) -> EventEvidenceSummary:
        self.finish()
        return EventEvidenceSummary(
            self.row_count,
            self.journey_count,
            self.subject_count,
            self.matched_row_count,
            self.missing_row_count,
            self.complete_journey_count,
            self.incomplete_journey_count,
            self.censored_journey_count,
            original.coverage,
        )


def build_event_publication(
    descriptor: ArtifactDescriptor,
    batches: Iterable[pa.RecordBatch],
    *,
    summary: EventEvidenceSummary | None = None,
) -> tuple[ArtifactDescriptor, tuple[Finding, ...]]:
    """Cross-check an authorized stream against an already trusted native summary.

    This helper validates stream consistency; it does not establish source matching
    authority. Production executes the compiler's native proof in DatasetRuntime
    and binds that result directly, without transferring private rows to this helper.
    """
    original = summary or descriptor.event_evidence
    if original is None:
        raise invalid("missing Event journey native matching and coverage proof")
    result = bind_event_summary(descriptor, original)
    validator = EventRowValidator(_semantics(result))
    for batch in batches:
        validator.accept(batch)
    if validator.summary(original) != original:
        raise invalid("Event journey stream differs from its original native scalar proof")
    return result, ()
