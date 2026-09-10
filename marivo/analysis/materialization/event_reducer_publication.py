"""Shape-owned Event reducer validation and complete selection publication."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

import pyarrow as pa

from marivo.analysis.domains.completeness import EventCoverageResolution
from marivo.analysis.domains.contracts import EventFunnelSemantics, EventTimeToEventSemantics
from marivo.analysis.materialization.contracts import ArtifactDescriptor, invalid, parse_timestamp
from marivo.analysis.materialization.event_reducer_codec import (
    EventFunnelEvidenceSummary,
    EventReducerEvidenceSummary,
    EventSelectionEvidenceSummary,
    EventTimeToEventEvidenceSummary,
    decode_evidence,
    decode_selection_evidence,
    evidence_payload,
    selection_evidence_payload,
    step_index,
    validate_semantics,
)
from marivo.analysis.materialization.storage import _compare, _Value, _value


def _zero_findings(descriptor: ArtifactDescriptor) -> None:
    if (
        any(part.contract_id != "population_sampling_state" for part in descriptor.retained_parts)
        or descriptor.comparison_inputs
        or descriptor.dataset_materialization_contract.finding_extractor_id != "none"
        or descriptor.dataset_materialization_contract.finding_policy_id != "zero_findings@v1"
    ):
        raise invalid("unregistered Event reducer retained authority or Finding policy")


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    from marivo.analysis.materialization.event_publication import validate_coverage

    semantics = descriptor.row_contract.family_semantics
    if not isinstance(semantics, (EventFunnelSemantics, EventTimeToEventSemantics)):
        raise invalid("missing Event reducer authority")
    validate_semantics(semantics)
    summary = descriptor.event_evidence
    if not isinstance(summary, (EventFunnelEvidenceSummary, EventTimeToEventEvidenceSummary)):
        raise invalid("missing Event reducer Evidence")
    if summary.row_count != descriptor.storage_receipt.realized_row_count:
        raise invalid("Event reducer Evidence row count differs from its receipt")
    decode_evidence(evidence_payload(summary))
    validate_coverage(semantics.journey, summary.coverage)
    if (
        descriptor.population_authority.entity_ref != semantics.journey.subject_entity_ref
        or descriptor.population_authority.identity_signature
        != semantics.journey.subject_identity_signature
    ):
        raise invalid("Event reducer subject differs from its retained governed authority")
    if isinstance(semantics, EventFunnelSemantics):
        if not isinstance(summary, EventFunnelEvidenceSummary):
            raise invalid("Event funnel carries another result shape's Evidence")
        steps = len(semantics.journey.pattern.steps)
        if (
            summary.group_count > summary.row_count
            or summary.row_count > steps * summary.group_count
            or (summary.group_count == 0) != (summary.row_count == 0)
            or (not semantics.axis_refs and summary.group_count > 1)
        ):
            raise invalid("Event funnel Evidence contradicts its bounded groups")
        if descriptor.dataset_materialization_contract.producer_id == "event.funnel" and (
            summary.row_count != steps * summary.group_count
            or (not semantics.axis_refs and summary.group_count != 1)
            or (semantics.axis_refs and summary.group_count > summary.cohort_count)
        ):
            raise invalid("Event funnel Evidence contradicts dense realized groups")
    elif not isinstance(summary, EventTimeToEventEvidenceSummary):
        raise invalid("Event time-to-event carries another result shape's Evidence")
    else:
        coverage = {fact.event_ref: fact.complete for fact in summary.coverage.events}
        start = step_index(semantics.journey, semantics.from_step)
        end = step_index(semantics.journey, semantics.to_step)
        # A missing earlier step can make entry unknown even when from_step is covered.
        # The initial step is the observed journey anchor and cannot have unknown entry.
        entry = tuple(
            coverage[step.event.key] for step in semantics.journey.pattern.steps[1 : start + 1]
        )
        target = tuple(
            coverage[step.event.key]
            for step in semantics.journey.pattern.steps[start + 1 : end + 1]
        )
        if (
            (summary.coverage_censored_count and all(target))
            or (summary.entry_unknown_count and all(entry))
            or (summary.not_entered_count and start == 0)
        ):
            raise invalid(
                "Event time-to-event statuses contradict the selected pair's exact coverage"
            )
    _zero_findings(descriptor)


def validate_selection_descriptor(descriptor: ArtifactDescriptor) -> None:
    from marivo.analysis.materialization.event_publication import validate_coverage

    summary = descriptor.subject_selection_evidence
    if summary is None or decode_selection_evidence(selection_evidence_payload(summary)) != summary:
        raise invalid("missing complete Event subject-selection Evidence")
    if (
        descriptor.row_contract.shape_id.family_id != "population"
        or descriptor.event_evidence is not None
        or descriptor.population_authority.entity_ref != summary.journey.subject_entity_ref
        or descriptor.population_authority.identity_signature
        != summary.journey.subject_identity_signature
        or descriptor.population_authority.definition_fingerprint
        != descriptor.definition_fingerprint
        or summary.row_count != descriptor.storage_receipt.realized_row_count
        or (
            descriptor.dataset_materialization_contract.producer_id == "event.select_subjects"
            and summary.row_count != summary.selected_subject_count
        )
    ):
        raise invalid("Event selection does not own its complete Population membership")
    validate_coverage(summary.journey, summary.coverage)
    _zero_findings(descriptor)


def bind_selection_summary(
    descriptor: ArtifactDescriptor, summary: EventSelectionEvidenceSummary
) -> ArtifactDescriptor:
    """Replace structural journey Evidence with the selection's own complete proof."""
    result = replace(descriptor, event_evidence=None, subject_selection_evidence=summary)
    validate_selection_descriptor(result)
    return result


def _int_value(value: _Value) -> int:
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise invalid("Event reducer count or duration is outside non-negative int64")
    return value


def _duration(start: datetime, end: datetime) -> int:
    delta = end - start
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


class EventReducerRowValidator:
    """Check independent result rows with only one previous ordering coordinate."""

    def __init__(self, semantics: EventFunnelSemantics | EventTimeToEventSemantics) -> None:
        self.semantics = semantics
        self.previous: tuple[_Value, ...] | None = None
        self.previous_funnel: tuple[tuple[_Value, ...], int, int, int] | None = None
        self.steps = tuple(step.key for step in semantics.journey.pattern.steps)
        self.through = parse_timestamp(semantics.journey.completion_through)

    def _ordered(self, current: tuple[_Value, ...]) -> None:
        if self.previous is not None and _compare(self.previous, current, nulls="last") >= 0:
            raise invalid("Event reducer rows have duplicate or unordered canonical coordinates")
        self.previous = current

    def _funnel(self, batch: pa.RecordBatch, offset: int, semantics: EventFunnelSemantics) -> None:
        def value(name: str) -> _Value:
            return _value(batch.column(name)[offset])

        axes = tuple(value(name) for name in batch.schema.names[: len(semantics.axis_refs)])
        step = value("step_key")
        if not isinstance(step, str) or step not in self.steps:
            raise invalid("Event funnel row names an unknown retained step")
        ordinal = self.steps.index(step)
        self._ordered((*axes, ordinal))
        cohort, resolved, entry, resolved_entry, reached, lost, censored = (
            _int_value(value(name))
            for name in (
                "cohort_count",
                "resolved_cohort_count",
                "entry_count",
                "resolved_entry_count",
                "reached_count",
                "lost_count",
                "coverage_censored_count",
            )
        )
        if (
            resolved > cohort
            or entry > cohort
            or resolved_entry > resolved
            or reached > resolved
            or censored > entry
            or resolved_entry != entry - censored
            or lost + reached != resolved_entry
            or censored > cohort - resolved
            or (
                ordinal == 0
                and (entry != cohort or reached != cohort or resolved != cohort or lost or censored)
            )
        ):
            raise invalid("Event funnel count equations do not reconcile")
        if self.previous_funnel is not None:
            previous_axes, previous_ordinal, previous_cohort, previous_reached = (
                self.previous_funnel
            )
            if axes == previous_axes and (
                cohort != previous_cohort
                or (ordinal == previous_ordinal + 1 and entry != previous_reached)
            ):
                raise invalid("Event funnel adjacent cells contradict their group cohort or entry")
        self.previous_funnel = (axes, ordinal, cohort, reached)
        for name, numerator, denominator in (
            ("conversion_from_first", reached, resolved),
            ("conversion_from_previous", reached, resolved_entry if ordinal else 0),
            ("loss_rate_from_previous", lost, resolved_entry if ordinal else 0),
        ):
            rate = value(name)
            expected = None if denominator == 0 else float(numerator) / float(denominator)
            if rate != expected:
                raise invalid("Event funnel rate differs from its exact count components")

    @staticmethod
    def _time_to_event_schema(batch: pa.RecordBatch, semantics: EventTimeToEventSemantics) -> None:
        from marivo.analysis.materialization.event_publication import _identity_type

        for name, signature in (
            ("entity_identity", semantics.journey.subject_identity_signature),
            (
                "from_event_identity",
                tuple(
                    (f"k{i}", kind)
                    for i, kind in enumerate(semantics.journey.occurrence_identity_types)
                ),
            ),
            (
                "to_event_identity",
                tuple(
                    (f"k{i}", kind)
                    for i, kind in enumerate(semantics.journey.occurrence_identity_types)
                ),
            ),
        ):
            physical = batch.schema.field(name).type
            if (
                not pa.types.is_struct(physical)
                or len(physical) != len(signature)
                or any(
                    field.name != key or not _identity_type(kind, field.type)
                    for field, (key, kind) in zip(physical, signature, strict=True)
                )
            ):
                raise invalid("Event time-to-event identity differs from its governed signature")
        for name in ("from_time", "to_time", "followup_until"):
            physical = batch.schema.field(name).type
            if not pa.types.is_timestamp(physical) or physical.tz not in (
                "UTC",
                "Etc/UTC",
                "+00:00",
            ):
                raise invalid("Event time-to-event timestamp is not normalized to UTC")

    def _time_to_event(
        self, batch: pa.RecordBatch, offset: int, semantics: EventTimeToEventSemantics
    ) -> None:
        def value(name: str) -> _Value:
            return _value(batch.column(name)[offset])

        journey, subject = value("journey_id"), value("entity_identity")
        start_id, end_id = value("from_event_identity"), value("to_event_identity")
        start, end, followup = value("from_time"), value("to_time"), value("followup_until")
        duration, observed, status = (
            value("duration"),
            value("observed_duration"),
            value("completion_status"),
        )
        if not isinstance(journey, str) or not re.fullmatch(r"journey_[0-9a-f]{64}", journey):
            raise invalid("Event time-to-event journey coordinate is malformed")
        if not isinstance(subject, tuple) or any(item is None for item in subject):
            raise invalid("Event time-to-event lacks complete subject identity")
        self._ordered((subject, start, start_id, journey))
        if status in ("not_entered", "entry_unknown"):
            if any(
                item is not None
                for item in (start_id, end_id, start, end, followup, duration, observed)
            ):
                raise invalid(
                    "Event unentered attempt carries entered occurrence or exposure fields"
                )
            return
        if status not in ("complete", "incomplete", "coverage_censored"):
            raise invalid("Event time-to-event status is outside its closed vocabulary")
        if (
            not isinstance(start_id, tuple)
            or any(item is None for item in start_id)
            or not isinstance(start, datetime)
            or not isinstance(followup, datetime)
            or not start <= followup <= self.through
            or _int_value(observed) != _duration(start, followup)
        ):
            raise invalid("Event time-to-event entered exposure is inconsistent")
        if status == "complete":
            if (
                not isinstance(end_id, tuple)
                or any(item is None for item in end_id)
                or not isinstance(end, datetime)
                or not start <= end <= self.through
                or followup != end
                or _int_value(duration) != _duration(start, end)
            ):
                raise invalid("Event completed duration is inconsistent")
        elif (
            end_id is not None
            or end is not None
            or duration is not None
            or (status == "incomplete" and followup != self.through)
        ):
            raise invalid("Event unresolved attempt carries completion or invalid follow-up fields")

    def accept(self, batch: pa.RecordBatch) -> None:
        if isinstance(self.semantics, EventTimeToEventSemantics):
            self._time_to_event_schema(batch, self.semantics)
        for offset in range(batch.num_rows):
            if isinstance(self.semantics, EventFunnelSemantics):
                self._funnel(batch, offset, self.semantics)
            else:
                self._time_to_event(batch, offset, self.semantics)

    def finish(self) -> None:
        """Result filters may retain any independently meaningful subset of rows."""


def summary_from_batches(
    semantics: EventFunnelSemantics | EventTimeToEventSemantics,
    batches: Iterable[pa.RecordBatch],
    coverage: EventCoverageResolution,
) -> EventReducerEvidenceSummary:
    """Validate a permitted retained result stream and summarize its selected cells."""
    validator = EventReducerRowValidator(semantics)
    row_count = group_count = cohort_count = subject_count = 0
    previous_group: tuple[_Value, ...] | None = None
    previous_subject: _Value = None
    statuses = dict.fromkeys(
        ("complete", "incomplete", "coverage_censored", "not_entered", "entry_unknown"), 0
    )
    for batch in batches:
        validator.accept(batch)
        row_count += batch.num_rows
        for offset in range(batch.num_rows):
            if isinstance(semantics, EventFunnelSemantics):
                group = tuple(
                    _value(batch.column(index)[offset]) for index in range(len(semantics.axis_refs))
                )
                if previous_group is None or previous_group != group:
                    group_count += 1
                    cohort_count += _int_value(_value(batch.column("cohort_count")[offset]))
                    previous_group = group
            else:
                subject = _value(batch.column("entity_identity")[offset])
                if subject != previous_subject:
                    subject_count += 1
                    previous_subject = subject
                status = _value(batch.column("completion_status")[offset])
                if not isinstance(status, str) or status not in statuses:
                    raise invalid("unknown Event time-to-event status")
                statuses[status] += 1
    validator.finish()
    result: EventReducerEvidenceSummary
    if isinstance(semantics, EventFunnelSemantics):
        result = EventFunnelEvidenceSummary(row_count, group_count, cohort_count, coverage)
    else:
        result = EventTimeToEventEvidenceSummary(
            row_count,
            subject_count,
            statuses["complete"],
            statuses["incomplete"],
            statuses["coverage_censored"],
            statuses["not_entered"],
            statuses["entry_unknown"],
            coverage,
        )
    return decode_evidence(evidence_payload(result))
