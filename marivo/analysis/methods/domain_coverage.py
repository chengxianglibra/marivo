"""Receipt-bound Event coverage; row extrema and counts never prove absence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import TypeAdapter

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.rules import OccurrencePrepare
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    BoundedCoverageStartV1,
    EventCoverageReceiptV1,
    SourceOriginCoverageStartV1,
)


@dataclass(frozen=True, slots=True)
class CoverageFact:
    event: str
    fingerprint: str
    source_id: str
    source_fingerprint: str
    subject: str
    participant: str
    scope: str
    basis: Literal["observed", "declared", "mixed", "unknown"]
    complete: bool
    complete_from: str | None
    complete_through: str | None
    source_origin: str | None
    observed: EventCoverageReceiptV1 | None
    rationale: str | None


FACTS: TypeAdapter[tuple[CoverageFact, ...]] = TypeAdapter(tuple[CoverageFact, ...])


def coverage(
    params: OccurrencePrepare, receipts: tuple[EventCoverageReceiptV1, ...] = ()
) -> tuple[CoverageFact, ...]:
    events = {event.ref: event for event in params.events}
    declarations = {}
    for claim in params.completeness:
        for reference in claim.inputs:
            if reference not in events or reference in declarations:
                fail(
                    "coverage_binding",
                    f"unknown or contradictory duplicate claim for {reference.path}",
                )
            if (
                not isinstance(claim, BoundedCompletenessDeclarationV1)
                and claim.source_origin_ref.path != events[reference].source.datasource_ref.path
            ):
                fail("coverage_binding", "declaration source origin differs")
            declarations[reference] = claim
    observed = {}
    for observed_receipt in receipts:
        event = events.get(observed_receipt.event_ref)
        if (
            event is None
            or observed_receipt.event_ref in observed
            or observed_receipt.event_fingerprint != event.fingerprint
            or observed_receipt.source_entity_ref != event.source.ref.path
            or observed_receipt.source_origin_ref.path != event.source.datasource_ref.path
            or observed_receipt.occurred_at_ref != event.occurred_at.ref.path
            or observed_receipt.source_binding_fingerprint != event.source.dependency_fingerprint
            or observed_receipt.execution_domain_id != params.output.definition_id
        ):
            fail(
                "coverage_binding",
                "observed receipt differs from its exact Event/source/version/input binding",
                stage="prepare",
            )
        if (
            isinstance(observed_receipt.coverage_start, SourceOriginCoverageStartV1)
            and observed_receipt.coverage_start.source_origin_ref
            != observed_receipt.source_origin_ref
        ):
            fail("coverage_binding", "observed source origin differs", stage="prepare")
        observed[observed_receipt.event_ref] = observed_receipt
    required_start = None if params.start is None else datetime.fromisoformat(params.start)
    required_end = datetime.fromisoformat(params.end)
    result = []
    for event in params.events:
        declaration = declarations.get(event.ref)
        receipt = observed.get(event.ref)
        declared_from = (
            declaration.complete_from
            if isinstance(declaration, BoundedCompletenessDeclarationV1)
            else None
        )
        observed_from = (
            receipt.coverage_start.complete_from
            if receipt is not None and isinstance(receipt.coverage_start, BoundedCoverageStartV1)
            else None
        )

        def complete(start: datetime | None, end: datetime) -> bool:
            return (
                start is None or (required_start is not None and start <= required_start)
            ) and end >= required_end

        use_observed = receipt is not None and (
            complete(observed_from, receipt.complete_through) or declaration is None
        )
        lower = observed_from if use_observed else declared_from
        through = (
            receipt.complete_through
            if use_observed and receipt is not None
            else declaration.complete_through
            if declaration is not None
            else None
        )
        basis: Literal["observed", "declared", "mixed", "unknown"] = (
            "observed"
            if use_observed
            else "mixed"
            if receipt is not None and declaration is not None
            else "declared"
            if declaration is not None
            else "unknown"
        )
        origin = event.source.datasource_ref.path if through is not None and lower is None else None
        result.append(
            CoverageFact(
                event.ref.path,
                event.fingerprint,
                event.source_id,
                event.source.dependency_fingerprint,
                event.subject.ref.path,
                event.participant,
                params.output.definition_id,
                basis,
                through is not None and complete(lower, through),
                None if lower is None else lower.isoformat(),
                None if through is None else through.isoformat(),
                origin,
                receipt,
                None if declaration is None else declaration.rationale,
            )
        )
    return tuple(result)
