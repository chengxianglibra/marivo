"""Immutable Event Journey analysis artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis.event import (
    CompletenessDeclaration,
    EventMatchingPolicy,
    EventPattern,
    EventWatermarkReceipt,
)
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, _display_column_names
from marivo.analysis.windows.spec import TimeScope
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.render import Card


class EventInputCoverage(BaseModel):
    """Coverage evidence retained for one exact Event input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_ref: RefPayloadV1
    basis: Literal["observed_watermark", "declared_complete", "unknown"]
    receipt: EventWatermarkReceipt | None = None
    declaration_fingerprint: str | None = None
    declaration_rationale: str | None = None
    observed_complete_through: str | None = None

    @model_validator(mode="after")
    def _validate_basis_evidence(self) -> EventInputCoverage:
        if self.event_ref.kind is not SemanticKind.EVENT:
            raise ValueError("Event input coverage requires an exact Event ref")
        receipt_bound = self.receipt.complete_through if self.receipt is not None else None
        if self.observed_complete_through != receipt_bound:
            raise ValueError(
                "observed_complete_through must exactly match receipt.complete_through"
            )
        if self.basis == "observed_watermark":
            if self.receipt is None:
                raise ValueError("observed_watermark coverage requires a receipt")
            if self.declaration_fingerprint is not None or self.declaration_rationale is not None:
                raise ValueError("observed_watermark coverage cannot carry declaration evidence")
        elif self.basis == "declared_complete":
            if not self.declaration_fingerprint or not self.declaration_fingerprint.strip():
                raise ValueError("declared_complete coverage requires declaration_fingerprint")
            if not self.declaration_rationale or not self.declaration_rationale.strip():
                raise ValueError("declared_complete coverage requires declaration_rationale")
        elif self.declaration_fingerprint is not None or self.declaration_rationale is not None:
            raise ValueError("unknown coverage cannot carry declaration evidence")
        return self


def _parse_coverage_bound(value: str, *, field: str) -> datetime:
    raw = value.strip()
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 bound") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _aggregate_coverage_basis(
    values: tuple[EventInputCoverage, ...],
) -> Literal["observed_watermark", "declared_complete", "mixed", "unknown"]:
    bases = {item.basis for item in values}
    if "unknown" in bases:
        return "unknown"
    if bases == {"observed_watermark"}:
        return "observed_watermark"
    if bases == {"declared_complete"}:
        return "declared_complete"
    return "mixed"


class EventFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["event_frame"] = "event_frame"
    semantic_kind: Literal["journey"] = "journey"
    row_contract_version: Literal["event-journey-rows/v1"] = "event-journey-rows/v1"
    operator_version: Literal["events.match/v1"] = "events.match/v1"
    catalog_definition_fingerprint: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    pattern: EventPattern
    matching: EventMatchingPolicy
    cohort_window: TimeScope
    completion_through: str
    completeness: tuple[CompletenessDeclaration, ...] = ()
    input_coverage: tuple[EventInputCoverage, ...]
    coverage_basis: Literal[
        "observed_watermark",
        "declared_complete",
        "mixed",
        "unknown",
    ]
    event_fingerprints: dict[str, str]
    event_identity_components: dict[str, tuple[RefPayloadV1, ...]]
    role_endpoints: dict[str, RefPayloadV1]
    query_refs: tuple[str, ...] = ()
    unused_event_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_coverage_contract(self) -> EventFrameMeta:
        required = _parse_coverage_bound(
            self.completion_through,
            field="completion_through",
        )
        expected_events = tuple(dict.fromkeys(step.event.path for step in self.pattern.steps))
        coverage_by_event: dict[str, list[EventInputCoverage]] = {}
        for item in self.input_coverage:
            coverage_by_event.setdefault(item.event_ref.path, []).append(item)
        if set(coverage_by_event) != set(expected_events):
            raise ValueError("input_coverage must reference exactly the Event inputs in pattern")
        if any(len(items) != 1 for items in coverage_by_event.values()):
            raise ValueError("input_coverage must contain exactly one entry per Event input")
        aggregate = _aggregate_coverage_basis(self.input_coverage)
        if self.coverage_basis != aggregate:
            raise ValueError(
                f"coverage_basis must be {aggregate!r} for the retained input coverage"
            )

        declarations_by_event: dict[str, set[str]] = {}
        declaration_bounds: dict[str, datetime] = {}
        declaration_rationales: dict[str, str] = {}
        for declaration in self.completeness:
            declaration_bound = _parse_coverage_bound(
                declaration.through,
                field="completeness.through",
            )
            declaration_bounds[declaration.fingerprint] = declaration_bound
            declaration_rationales[declaration.fingerprint] = declaration.rationale
            for event_ref in declaration.inputs:
                declarations_by_event.setdefault(event_ref.path, set()).add(declaration.fingerprint)

        for event_path, entries in coverage_by_event.items():
            item = entries[0]
            if item.receipt is not None:
                receipt_bound = _parse_coverage_bound(
                    item.receipt.complete_through,
                    field="receipt.complete_through",
                )
                _parse_coverage_bound(
                    item.receipt.observed_at,
                    field="receipt.observed_at",
                )
                if item.basis == "observed_watermark" and receipt_bound < required:
                    raise ValueError("observed_watermark receipt must cover completion_through")
            if item.basis == "declared_complete":
                fingerprint = item.declaration_fingerprint
                if fingerprint is None:
                    raise ValueError("declared_complete coverage requires declaration_fingerprint")
                if fingerprint not in declarations_by_event.get(event_path, set()):
                    raise ValueError(
                        "declared_complete coverage must reference an exact retained declaration"
                    )
                if declaration_bounds[fingerprint] < required:
                    raise ValueError("declared_complete declaration must cover completion_through")
                if item.declaration_rationale != declaration_rationales[fingerprint]:
                    raise ValueError(
                        "declared_complete rationale must match the retained declaration"
                    )
        return self


def _identity_tuple(value: object) -> object:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        converted = tolist()
        if isinstance(converted, list):
            return tuple(converted)
    return value


@dataclass(repr=False)
class EventFrame(BaseFrame):
    """Canonical materialized Event journeys."""

    meta: EventFrameMeta

    def __post_init__(self) -> None:
        self._restore_persisted_identity_columns()

    def _restore_persisted_identity_columns(self) -> None:
        restore_event_identity_columns(self._df)

    def _repr_identity(self) -> str:
        return (
            f"EventFrame ref={self.meta.ref} shape=journey "
            f"coverage={self.meta.coverage_basis} rows={self.meta.row_count}"
        )

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        matching: str = self.meta.matching.kind
        if self.meta.matching.kind == "every_start":
            matching = (
                f"{matching} completion_assignment={self.meta.matching.completion_assignment}"
            )
        card = Card(identity=self._repr_identity(), available=self._AVAILABLE_ENTRIES).status(
            f"matching={matching} coverage={self.meta.coverage_basis}"
        )
        self._append_evidence_sections(card)
        return card.lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )


def restore_event_identity_columns(frame: Any) -> None:
    """Restore persisted Event identity arrays to the public tuple contract."""
    columns = getattr(frame, "columns", ())
    for column in ("subject_identity", "event_identity"):
        if column in columns:
            frame[column] = frame[column].map(_identity_tuple)


__all__ = ["EventFrame", "EventFrameMeta", "EventInputCoverage"]
