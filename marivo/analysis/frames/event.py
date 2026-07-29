"""Immutable shape-specific Event analysis artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marivo.analysis.event import (
    CompletenessDeclaration,
    EventMatchingPolicy,
    EventPattern,
    EventWatermarkReceipt,
    FirstPerSubject,
    PatternStep,
)
from marivo.analysis.frames.base import (
    BaseFrame,
    BaseFrameMeta,
    _ArtifactSemanticBinding,
    _display_column_names,
)
from marivo.analysis.frames.subject import SubjectCohortBinding
from marivo.analysis.windows.spec import TimeScope
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.render import Card

CoverageBasis = Literal[
    "observed_watermark",
    "declared_complete",
    "mixed",
    "unknown",
]
AxisVersioningResolution = Literal["ordinary", "snapshot", "changes", "validity"]

_FUNNEL_ADDITIVE_COLUMNS = (
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    "coverage_censored_count",
)


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


class SubjectAxisBinding(BaseModel):
    """Exact governed subject-Dimension enrichment retained by an Event funnel."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension_ref: RefPayloadV1
    output_column: str
    relationship_path: tuple[RefPayloadV1, ...] = ()
    anchor: Literal["cohort_entry"] = "cohort_entry"
    versioning_resolution: AxisVersioningResolution
    null_group: Literal["explicit"] = "explicit"

    @model_validator(mode="after")
    def _validate_binding(self) -> SubjectAxisBinding:
        if self.dimension_ref.kind is not SemanticKind.DIMENSION:
            raise ValueError("subject axis requires an exact Dimension ref")
        if not self.output_column.strip():
            raise ValueError("subject axis output_column must be non-empty")
        if any(item.kind is not SemanticKind.RELATIONSHIP for item in self.relationship_path):
            raise ValueError("subject axis relationship_path must contain Relationship refs")
        return self


class GroupedFunnelReconciliationReceipt(BaseModel):
    """Exact grouped-to-ungrouped additive reconciliation receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    additive_columns: tuple[str, ...] = _FUNNEL_ADDITIVE_COLUMNS
    ungrouped_hash: str
    grouped_hash: str
    status: Literal["pass"] = "pass"

    @model_validator(mode="after")
    def _validate_receipt(self) -> GroupedFunnelReconciliationReceipt:
        if self.additive_columns != _FUNNEL_ADDITIVE_COLUMNS:
            raise ValueError("funnel reconciliation additive_columns must use the fixed contract")
        if not self.ungrouped_hash.strip() or not self.grouped_hash.strip():
            raise ValueError("funnel reconciliation hashes must be non-empty")
        if self.ungrouped_hash != self.grouped_hash:
            raise ValueError("funnel reconciliation hashes must match for status='pass'")
        return self


def _parse_coverage_bound(value: str, *, field: str) -> datetime:
    raw = value.strip()
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 bound") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _aggregate_coverage_basis(values: tuple[EventInputCoverage, ...]) -> CoverageBasis:
    bases = {item.basis for item in values}
    if "unknown" in bases:
        return "unknown"
    if bases == {"observed_watermark"}:
        return "observed_watermark"
    if bases == {"declared_complete"}:
        return "declared_complete"
    return "mixed"


class EventFrameMetaBase(BaseFrameMeta):
    """Shared exact Event semantics inherited by every EventFrame shape."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["event_frame"] = "event_frame"
    catalog_definition_fingerprint: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    pattern: EventPattern
    matching: EventMatchingPolicy
    cohort_window: TimeScope
    completion_through: str
    completeness: tuple[CompletenessDeclaration, ...] = ()
    input_coverage: tuple[EventInputCoverage, ...]
    coverage_basis: CoverageBasis
    event_fingerprints: dict[str, str]
    event_identity_components: dict[str, tuple[RefPayloadV1, ...]]
    role_endpoints: dict[str, RefPayloadV1]
    cohort: SubjectCohortBinding | None = None

    @field_validator("catalog_definition_fingerprint")
    @classmethod
    def _validate_catalog_fingerprint(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("catalog_definition_fingerprint must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_event_contract(self) -> EventFrameMetaBase:
        if self.subject_entity_ref.kind is not SemanticKind.ENTITY:
            raise ValueError("subject_entity_ref must be an exact Entity ref")
        if not self.subject_identity or any(not item.strip() for item in self.subject_identity):
            raise ValueError("subject_identity must contain non-empty ordered components")

        expected_steps = {step.key for step in self.pattern.steps}
        if set(self.role_endpoints) != expected_steps:
            raise ValueError("role_endpoints must reference exactly the PatternStep keys")
        if any(item.kind is not SemanticKind.ENTITY for item in self.role_endpoints.values()):
            raise ValueError("role_endpoints must contain exact Entity refs")

        expected_events = tuple(dict.fromkeys(step.event.path for step in self.pattern.steps))
        if set(self.event_fingerprints) != set(expected_events):
            raise ValueError(
                "event_fingerprints must reference exactly the Event inputs in pattern"
            )
        if any(not digest.strip() for digest in self.event_fingerprints.values()):
            raise ValueError("event_fingerprints must be non-empty")
        if set(self.event_identity_components) != set(expected_events):
            raise ValueError(
                "event_identity_components must reference exactly the Event inputs in pattern"
            )
        for components in self.event_identity_components.values():
            if not components or any(
                component.kind is not SemanticKind.DIMENSION for component in components
            ):
                raise ValueError("event_identity_components must contain non-empty Dimension refs")

        required = _parse_coverage_bound(
            self.completion_through,
            field="completion_through",
        )
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


class EventFrameMeta(EventFrameMetaBase):
    """Metadata for canonical Event journey materialization."""

    semantic_kind: Literal["journey"] = "journey"
    row_contract_version: Literal["event-journey-rows/v1"] = "event-journey-rows/v1"
    operator_version: Literal["events.match/v1"] = "events.match/v1"
    query_refs: tuple[str, ...] = ()
    unused_event_count: int = Field(ge=0)
    unused_event_counts_by_step: dict[str, int]

    @model_validator(mode="after")
    def _validate_unused_event_counts(self) -> EventFrameMeta:
        expected = tuple(step.key for step in self.pattern.steps)
        if set(self.unused_event_counts_by_step) != set(expected):
            raise ValueError(
                "unused_event_counts_by_step must reference every retained PatternStep exactly"
            )
        if any(value < 0 for value in self.unused_event_counts_by_step.values()):
            raise ValueError("unused Event counts must be non-negative")
        return self


class EventFunnelFrameMeta(EventFrameMetaBase):
    """Metadata for a pure or subject-axis-enriched Event funnel reduction."""

    semantic_kind: Literal["funnel"] = "funnel"
    row_contract_version: Literal["event-funnel-rows/v1"] = "event-funnel-rows/v1"
    operator_version: Literal["events.funnel/v1"] = "events.funnel/v1"
    source_journey_ref: str
    source_journey_fingerprint: str
    source_unused_event_count: int = Field(ge=0)
    axes: tuple[SubjectAxisBinding, ...] = ()
    grouped_reconciliation: GroupedFunnelReconciliationReceipt

    @model_validator(mode="after")
    def _validate_funnel_contract(self) -> EventFunnelFrameMeta:
        if type(self.matching) is not FirstPerSubject:
            raise ValueError("EventFrame[funnel] requires first_per_subject matching")
        if not self.source_journey_ref.strip() or not self.source_journey_fingerprint.strip():
            raise ValueError("EventFrame[funnel] source journey identity must be non-empty")
        refs = tuple(item.dimension_ref.path for item in self.axes)
        columns = tuple(item.output_column for item in self.axes)
        if len(set(refs)) != len(refs):
            raise ValueError("EventFrame[funnel] axes must have unique Dimension refs")
        if len(set(columns)) != len(columns):
            raise ValueError("EventFrame[funnel] axes must have unique output columns")
        return self


class EventTimeToEventFrameMeta(EventFrameMetaBase):
    """Metadata for a time-to-event projection over canonical journeys."""

    semantic_kind: Literal["time_to_event"] = "time_to_event"
    row_contract_version: Literal["event-time-to-event-rows/v1"] = "event-time-to-event-rows/v1"
    operator_version: Literal["events.time_to_event/v1"] = "events.time_to_event/v1"
    source_journey_ref: str
    source_journey_fingerprint: str
    source_unused_end_count: int = Field(ge=0)
    start_step: PatternStep
    end_step: PatternStep

    @model_validator(mode="after")
    def _validate_steps(self) -> EventTimeToEventFrameMeta:
        if not self.source_journey_ref.strip() or not self.source_journey_fingerprint.strip():
            raise ValueError("EventFrame[time_to_event] source journey identity must be non-empty")
        step_fingerprints = tuple(step.fingerprint for step in self.pattern.steps)
        if step_fingerprints.count(self.start_step.fingerprint) != 1:
            raise ValueError("start_step must occur exactly once in the retained EventPattern")
        if step_fingerprints.count(self.end_step.fingerprint) != 1:
            raise ValueError("end_step must occur exactly once in the retained EventPattern")
        if step_fingerprints.index(self.start_step.fingerprint) >= step_fingerprints.index(
            self.end_step.fingerprint
        ):
            raise ValueError("start_step must precede end_step in the retained EventPattern")
        return self


EventFrameMetaVariant = Annotated[
    EventFrameMeta | EventFunnelFrameMeta | EventTimeToEventFrameMeta,
    Field(discriminator="semantic_kind"),
]


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
    """Canonical materialized Event analysis artifact."""

    meta: EventFrameMetaVariant

    def __post_init__(self) -> None:
        self._restore_persisted_identity_columns()
        super().__post_init__()

    def _restore_persisted_identity_columns(self) -> None:
        restore_event_identity_columns(self._df)

    @property
    def semantic_shape(self) -> Literal["journey", "funnel", "time_to_event"]:
        """Return the exact closed Event artifact shape."""
        return self.meta.semantic_kind

    def _funnel_cohort_count(self) -> int:
        if (
            self.meta.semantic_kind != "funnel"
            or "step_key" not in self._df.columns
            or "cohort_count" not in self._df.columns
        ):
            return 0
        first_key = self.meta.pattern.steps[0].key
        return int(
            self._df.loc[
                self._df["step_key"] == first_key,
                "cohort_count",
            ].sum()
        )

    def _repr_identity(self) -> str:
        if self.meta.semantic_kind == "journey":
            return (
                f"EventFrame ref={self.meta.ref} shape=journey "
                f"coverage={self.meta.coverage_basis} rows={self.meta.row_count}"
            )
        if self.meta.semantic_kind == "funnel":
            return (
                f"EventFrame ref={self.meta.ref} shape=funnel "
                f"steps={len(self.meta.pattern.steps)} axes={len(self.meta.axes)} "
                f"cohort={self._funnel_cohort_count()} "
                f"coverage={self.meta.coverage_basis} rows={self.meta.row_count}"
            )
        return (
            f"EventFrame ref={self.meta.ref} shape=time_to_event "
            f"start={self.meta.start_step.key} end={self.meta.end_step.key} "
            f"attempts={self.meta.row_count} coverage={self.meta.coverage_basis}"
        )

    def _semantic_input_bindings(self) -> tuple[_ArtifactSemanticBinding, ...]:
        """Expose retained Event and subject-axis catalog acquisition paths."""
        bindings = [
            _ArtifactSemanticBinding(
                role=f"event[{step.key}]",
                semantic_kind=SemanticKind.EVENT,
                semantic_path=step.event.path,
            )
            for step in self.meta.pattern.steps
        ]
        if self.meta.semantic_kind == "funnel":
            bindings.extend(
                _ArtifactSemanticBinding(
                    role="dimension_axis",
                    semantic_kind=axis.dimension_ref.kind,
                    semantic_path=axis.dimension_ref.path,
                    output_column=axis.output_column,
                )
                for axis in self.meta.axes
            )
        return tuple(bindings)

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        if self.meta.semantic_kind == "journey":
            matching: str = self.meta.matching.kind
            if self.meta.matching.kind == "every_start":
                matching = (
                    f"{matching} completion_assignment={self.meta.matching.completion_assignment}"
                )
            status = f"matching={matching} coverage={self.meta.coverage_basis}"
        elif self.meta.semantic_kind == "funnel":
            axis_columns = ", ".join(item.output_column for item in self.meta.axes) or "none"
            status = (
                f"steps={len(self.meta.pattern.steps)} axes={axis_columns} "
                f"cohort={self._funnel_cohort_count()} "
                f"coverage={self.meta.coverage_basis}"
            )
        else:
            status = (
                f"start={self.meta.start_step.key} end={self.meta.end_step.key} "
                f"attempts={self.meta.row_count} coverage={self.meta.coverage_basis}"
            )
        card = Card(identity=self._repr_identity(), available=self._AVAILABLE_ENTRIES).status(
            status
        )
        self._append_artifact_interface_sections(card)
        self._append_evidence_sections(card)
        return card.lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )


def restore_event_identity_columns(frame: Any) -> None:
    """Restore persisted Event identity arrays to the public tuple contract."""
    columns = getattr(frame, "columns", ())
    for column in (
        "subject_identity",
        "event_identity",
        "start_event_identity",
        "end_event_identity",
    ):
        if column in columns:
            frame[column] = frame[column].map(_identity_tuple)


__all__ = [
    "EventFrame",
    "EventFrameMeta",
    "EventFrameMetaBase",
    "EventFrameMetaVariant",
    "EventFunnelFrameMeta",
    "EventInputCoverage",
    "EventTimeToEventFrameMeta",
    "GroupedFunnelReconciliationReceipt",
    "SubjectAxisBinding",
]
