"""Immutable replay-based Lifecycle analysis artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marivo._compat import UTC
from marivo.analysis.event import CompletenessDeclaration
from marivo.analysis.frames.base import (
    BaseFrame,
    BaseFrameMeta,
    _ArtifactSemanticBinding,
    _display_column_names,
    _FrameAuxiliaryReceipt,
    _FrameAuxiliaryTable,
)
from marivo.analysis.frames.event import CoverageBasis, EventInputCoverage
from marivo.analysis.frames.subject import SubjectCohortBinding
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.windows.spec import TimeScope
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.render import Card

LIFECYCLE_HISTORY_COLUMNS = (
    "subject_identity",
    "model_state",
    "valid_from",
    "valid_to",
    "entered_by_event_ref",
    "entered_by_event_identity",
    "exited_by_event_ref",
    "exited_by_event_identity",
    "interval_status",
)
LIFECYCLE_DISTRIBUTION_VALUE_COLUMNS = (
    "as_of",
    "model_state",
    "subject_count",
    "share",
)
LIFECYCLE_TRANSITIONS_COLUMNS = (
    "from_model_state",
    "to_model_state",
    "transition_status",
    "transition_count",
    "share_of_modeled_transitions",
)
LIFECYCLE_DWELL_COLUMNS = (
    "model_state",
    "interval_count",
    "completed_count",
    "right_censored_count",
    "coverage_censored_count",
    "mean_duration",
    "median_duration",
    "p90_duration",
)
LIFECYCLE_VIOLATIONS_COLUMNS = (
    "subject_identity",
    "trigger_event_ref",
    "trigger_event_identity",
    "occurred_at",
    "model_state_at_event",
    "violation_kind",
)

_TRACE_FILENAME = "violations.parquet"
_STATE_NAME_FIELDS = ("model_state", "from_model_state", "to_model_state")


class PersistedModelStateHandle(BaseModel):
    """Stable JSON payload for one project-neutral ModelStateHandle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: RefPayloadV1
    name: str

    @model_validator(mode="after")
    def _validate_handle(self) -> PersistedModelStateHandle:
        if self.model.kind is not SemanticKind.STATE_MODEL:
            raise ValueError("persisted model-state handle requires a StateModel ref")
        if not self.name.strip():
            raise ValueError("persisted model-state handle name must be non-empty")
        return self


class LifecycleStateBinding(BaseModel):
    """One ordered normative StateModel state retained by an artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: PersistedModelStateHandle
    initial: bool = False
    terminal: bool = False


class LifecycleTriggerBinding(BaseModel):
    """One canonical StateModel trigger retained as replay authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["inception", "transition"]
    event_ref: RefPayloadV1
    participant_role: str
    from_state: str | None = None
    to_state: str

    @model_validator(mode="after")
    def _validate_trigger(self) -> LifecycleTriggerBinding:
        if self.event_ref.kind is not SemanticKind.EVENT:
            raise ValueError("Lifecycle trigger requires an exact Event ref")
        if not self.participant_role.strip() or not self.to_state.strip():
            raise ValueError("Lifecycle trigger role and target state must be non-empty")
        if self.kind == "inception" and self.from_state is not None:
            raise ValueError("inception trigger cannot carry from_state")
        if self.kind == "transition" and (self.from_state is None or not self.from_state.strip()):
            raise ValueError("transition trigger requires from_state")
        return self

    @property
    def key(self) -> str:
        """Return the canonical occurrence-stream key shared by trigger uses."""
        return f"{self.event_ref.path}#{self.participant_role}"


class LifecycleTraceManifest(BaseModel):
    """Private replay violation-trace receipt bound into history identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: Literal["violations.parquet"] = "violations.parquet"
    row_count: int = Field(ge=0)
    schema_version: Literal["lifecycle-replay-trace/v1"] = "lifecycle-replay-trace/v1"
    content_hash: str | None = None

    @field_validator("content_hash")
    @classmethod
    def _validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("sha256:"):
            raise ValueError("Lifecycle trace content_hash must use the sha256: prefix")
        return value


class LifecycleAxisBinding(BaseModel):
    """One exact point-in-time subject Dimension used by distribution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension_ref: RefPayloadV1
    output_column: str
    relationship_path: tuple[RefPayloadV1, ...] = ()
    versioning_resolution: Literal["ordinary", "snapshot", "changes", "validity"]
    anchor: Literal["as_of"] = "as_of"
    null_group: Literal["explicit"] = "explicit"

    @model_validator(mode="after")
    def _validate_axis(self) -> LifecycleAxisBinding:
        if self.dimension_ref.kind is not SemanticKind.DIMENSION:
            raise ValueError("Lifecycle axis requires an exact Dimension ref")
        if not self.output_column.strip():
            raise ValueError("Lifecycle axis output_column must be non-empty")
        if any(item.kind is not SemanticKind.RELATIONSHIP for item in self.relationship_path):
            raise ValueError("Lifecycle axis path must contain Relationship refs")
        return self


class LifecycleStatePair(BaseModel):
    """One distinct modeled transition pair in declaration order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_state: str
    to_state: str

    @model_validator(mode="after")
    def _validate_pair(self) -> LifecycleStatePair:
        if not self.from_state.strip() or not self.to_state.strip():
            raise ValueError("Lifecycle modeled state pair names must be non-empty")
        return self


class LifecycleFrameMetaBase(BaseFrameMeta):
    """Shared exact StateModel identity inherited by every Lifecycle shape."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["lifecycle_frame"] = "lifecycle_frame"
    catalog_definition_fingerprint: str
    state_model_ref: RefPayloadV1
    state_model_fingerprint: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    states: tuple[LifecycleStateBinding, ...]

    @model_validator(mode="after")
    def _validate_model_identity(self) -> LifecycleFrameMetaBase:
        if not self.catalog_definition_fingerprint.strip():
            raise ValueError("catalog_definition_fingerprint must be non-empty")
        if self.state_model_ref.kind is not SemanticKind.STATE_MODEL:
            raise ValueError("state_model_ref must be an exact StateModel ref")
        if not self.state_model_fingerprint.strip():
            raise ValueError("state_model_fingerprint must be non-empty")
        if self.subject_entity_ref.kind is not SemanticKind.ENTITY:
            raise ValueError("subject_entity_ref must be an exact Entity ref")
        if not self.subject_identity or any(not item.strip() for item in self.subject_identity):
            raise ValueError("subject_identity must contain ordered non-empty components")
        if not self.states:
            raise ValueError("Lifecycle metadata requires ordered StateModel states")
        names = tuple(item.state.name for item in self.states)
        if len(set(names)) != len(names):
            raise ValueError("Lifecycle metadata state names must be unique")
        if sum(item.initial for item in self.states) != 1:
            raise ValueError("Lifecycle metadata requires exactly one initial state")
        if any(item.state.model != self.state_model_ref for item in self.states):
            raise ValueError("Lifecycle state handles must reference state_model_ref")
        return self


class LifecycleHistoryFrameMeta(LifecycleFrameMetaBase):
    """Metadata for canonical from-inception Lifecycle replay history."""

    semantic_kind: Literal["history"] = "history"
    row_contract_version: Literal["lifecycle-history-rows/v1"] = "lifecycle-history-rows/v1"
    operator_version: Literal["lifecycle_replay/v1"] = "lifecycle_replay/v1"
    seed: FromInception
    violation_behavior_id: Literal["record_and_continue/v1"] = "record_and_continue/v1"
    window: TimeScope
    cohort: SubjectCohortBinding | None = None
    triggers: tuple[LifecycleTriggerBinding, ...]
    completeness: tuple[CompletenessDeclaration, ...] = ()
    input_coverage: tuple[EventInputCoverage, ...]
    coverage_basis: CoverageBasis
    event_fingerprints: dict[str, str]
    event_identity_components: dict[str, tuple[RefPayloadV1, ...]]
    query_refs: tuple[str, ...] = ()
    population_count: int = Field(ge=0)
    seeded_subject_count: int = Field(ge=0)
    coverage_censored_subject_count: int = Field(ge=0)
    interval_count: int = Field(ge=0)
    violation_count: int = Field(ge=0)
    pre_inception_ignored_counts: dict[str, int]
    violation_trace: LifecycleTraceManifest

    @model_validator(mode="after")
    def _validate_history(self) -> LifecycleHistoryFrameMeta:
        state_names = {item.state.name for item in self.states}
        if not self.triggers:
            raise ValueError("Lifecycle replay history requires canonical triggers")
        if any(
            trigger.to_state not in state_names
            or (trigger.from_state is not None and trigger.from_state not in state_names)
            for trigger in self.triggers
        ):
            raise ValueError("Lifecycle triggers must reference retained StateModel states")
        trigger_bindings = tuple(
            (
                trigger.kind,
                trigger.event_ref.path,
                trigger.participant_role,
                trigger.from_state,
                trigger.to_state,
            )
            for trigger in self.triggers
        )
        if len(set(trigger_bindings)) != len(trigger_bindings):
            raise ValueError("Lifecycle trigger bindings must be unique")
        trigger_keys = {trigger.key for trigger in self.triggers}
        if set(self.pre_inception_ignored_counts) != set(trigger_keys):
            raise ValueError("pre_inception_ignored_counts must reference every canonical trigger")
        if any(value < 0 for value in self.pre_inception_ignored_counts.values()):
            raise ValueError("pre-inception ignored counts must be non-negative")

        expected_events = {trigger.event_ref.path for trigger in self.triggers}
        if set(self.event_fingerprints) != expected_events:
            raise ValueError("event_fingerprints must reference every replay Event")
        if any(not value.strip() for value in self.event_fingerprints.values()):
            raise ValueError("event_fingerprints must be non-empty")
        if set(self.event_identity_components) != expected_events:
            raise ValueError("event_identity_components must reference every replay Event")
        if any(
            not components
            or any(component.kind is not SemanticKind.DIMENSION for component in components)
            for components in self.event_identity_components.values()
        ):
            raise ValueError("Event identity components must be non-empty Dimension refs")
        coverage_paths = tuple(item.event_ref.path for item in self.input_coverage)
        if set(coverage_paths) != expected_events or len(coverage_paths) != len(expected_events):
            raise ValueError("input_coverage must contain one receipt per replay Event")
        window_start = _utc_timestamp(self.window.start, field="window.start")
        window_end = _utc_timestamp(self.window.end, field="window.end")
        if window_start >= window_end:
            raise ValueError("Lifecycle replay window must be non-empty")
        declaration_by_event: dict[str, CompletenessDeclaration] = {}
        for declaration in self.completeness:
            through = _utc_timestamp(
                declaration.through,
                field="completeness.through",
            )
            if through < window_end:
                raise ValueError("Lifecycle completeness declarations must cover window.end")
            for event_ref in declaration.inputs:
                if event_ref.path not in expected_events:
                    raise ValueError("Lifecycle completeness may reference only replay Events")
                if event_ref.path in declaration_by_event:
                    raise ValueError("Lifecycle completeness declarations cannot overlap an Event")
                declaration_by_event[event_ref.path] = declaration
        for item in self.input_coverage:
            if item.basis == "observed_watermark":
                assert item.receipt is not None
                if (
                    _utc_timestamp(
                        item.receipt.complete_through,
                        field="receipt.complete_through",
                    )
                    < window_end
                ):
                    raise ValueError("Lifecycle observed watermark must cover window.end")
            elif item.basis == "declared_complete":
                matched_declaration = declaration_by_event.get(item.event_ref.path)
                if (
                    matched_declaration is None
                    or item.declaration_fingerprint != matched_declaration.fingerprint
                    or item.declaration_rationale != matched_declaration.rationale
                ):
                    raise ValueError(
                        "Lifecycle declared coverage must bind one retained declaration"
                    )
        bases = {item.basis for item in self.input_coverage}
        expected_basis: CoverageBasis
        if "unknown" in bases:
            expected_basis = "unknown"
        elif bases == {"observed_watermark"}:
            expected_basis = "observed_watermark"
        elif bases == {"declared_complete"}:
            expected_basis = "declared_complete"
        else:
            expected_basis = "mixed"
        if self.coverage_basis != expected_basis:
            raise ValueError(f"coverage_basis must be {expected_basis!r} for retained coverage")

        if (
            self.seeded_subject_count > self.population_count
            or self.coverage_censored_subject_count > self.population_count
        ):
            raise ValueError("Lifecycle subject counts cannot exceed population_count")
        if self.interval_count != self.row_count:
            raise ValueError("interval_count must equal public history row_count")
        if self.violation_count != self.violation_trace.row_count:
            raise ValueError("violation_count must equal violation trace row_count")
        if self.cohort is not None and (
            self.cohort.subject_entity_ref != self.subject_entity_ref
            or self.cohort.subject_identity != self.subject_identity
        ):
            raise ValueError("Lifecycle cohort must match the retained subject identity")
        return self


class LifecycleReducerFrameMetaBase(LifecycleFrameMetaBase):
    """Shared source authority for pure Lifecycle history reducers."""

    source_history_ref: str
    source_history_fingerprint: str

    @model_validator(mode="after")
    def _validate_source(self) -> LifecycleReducerFrameMetaBase:
        if not self.source_history_ref.strip() or not self.source_history_fingerprint.strip():
            raise ValueError("Lifecycle reducer source artifact identity must be non-empty")
        return self


class LifecycleDistributionFrameMeta(LifecycleReducerFrameMetaBase):
    semantic_kind: Literal["distribution"] = "distribution"
    row_contract_version: Literal["lifecycle-distribution-rows/v1"] = (
        "lifecycle-distribution-rows/v1"
    )
    operator_version: Literal["lifecycle.distribution/v1"] = "lifecycle.distribution/v1"
    at: tuple[str, ...]
    axes: tuple[LifecycleAxisBinding, ...] = ()
    known_subject_counts: dict[str, int]
    coverage_censored_subject_counts: dict[str, int]
    grouped_reconciliation_hash: str

    @model_validator(mode="after")
    def _validate_distribution(self) -> LifecycleDistributionFrameMeta:
        if not self.at or len(set(self.at)) != len(self.at):
            raise ValueError("Lifecycle distribution instants must be non-empty and unique")
        if set(self.known_subject_counts) != set(self.at):
            raise ValueError("known_subject_counts must reference every distribution instant")
        if set(self.coverage_censored_subject_counts) != set(self.at):
            raise ValueError(
                "coverage_censored_subject_counts must reference every distribution instant"
            )
        if any(value < 0 for value in self.known_subject_counts.values()) or any(
            value < 0 for value in self.coverage_censored_subject_counts.values()
        ):
            raise ValueError("Lifecycle distribution subject counts must be non-negative")
        if not self.grouped_reconciliation_hash.startswith("sha256:"):
            raise ValueError("grouped_reconciliation_hash must use the sha256: prefix")
        refs = tuple(axis.dimension_ref.path for axis in self.axes)
        columns = tuple(axis.output_column for axis in self.axes)
        if len(set(refs)) != len(refs) or len(set(columns)) != len(columns):
            raise ValueError("Lifecycle distribution axes and output columns must be unique")
        return self


class LifecycleTransitionsFrameMeta(LifecycleReducerFrameMetaBase):
    semantic_kind: Literal["transitions"] = "transitions"
    row_contract_version: Literal["lifecycle-transitions-rows/v1"] = "lifecycle-transitions-rows/v1"
    operator_version: Literal["lifecycle.transitions/v1"] = "lifecycle.transitions/v1"
    modeled_pairs: tuple[LifecycleStatePair, ...]
    modeled_transition_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_transitions(self) -> LifecycleTransitionsFrameMeta:
        pairs = tuple((item.from_state, item.to_state) for item in self.modeled_pairs)
        if len(set(pairs)) != len(pairs):
            raise ValueError("modeled Lifecycle transition pairs must be distinct")
        if self.row_count != len(self.modeled_pairs):
            raise ValueError("Lifecycle transitions rows must be dense over modeled pairs")
        return self


class LifecycleDwellFrameMeta(LifecycleReducerFrameMetaBase):
    semantic_kind: Literal["dwell"] = "dwell"
    row_contract_version: Literal["lifecycle-dwell-rows/v1"] = "lifecycle-dwell-rows/v1"
    operator_version: Literal["lifecycle.dwell/v1"] = "lifecycle.dwell/v1"
    source_interval_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_dwell(self) -> LifecycleDwellFrameMeta:
        if self.row_count != len(self.states):
            raise ValueError("Lifecycle dwell rows must be dense over modeled states")
        return self


class LifecycleViolationsFrameMeta(LifecycleReducerFrameMetaBase):
    semantic_kind: Literal["violations"] = "violations"
    row_contract_version: Literal["lifecycle-violations-rows/v1"] = "lifecycle-violations-rows/v1"
    operator_version: Literal["lifecycle.violations/v1"] = "lifecycle.violations/v1"
    violation_count: int = Field(ge=0)
    source_trace_content_hash: str

    @model_validator(mode="after")
    def _validate_violations(self) -> LifecycleViolationsFrameMeta:
        if self.violation_count != self.row_count:
            raise ValueError("violation_count must equal Lifecycle violations row_count")
        if not self.source_trace_content_hash.startswith("sha256:"):
            raise ValueError("source trace hash must use the sha256: prefix")
        return self


LifecycleFrameMetaVariant = Annotated[
    LifecycleHistoryFrameMeta
    | LifecycleDistributionFrameMeta
    | LifecycleTransitionsFrameMeta
    | LifecycleDwellFrameMeta
    | LifecycleViolationsFrameMeta,
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


def _is_utc_instant(value: object) -> bool:
    if not isinstance(value, (datetime, pd.Timestamp)):
        return False
    timestamp = pd.Timestamp(value)
    offset = timestamp.utcoffset()
    return timestamp.tzinfo is not None and offset is not None and offset.total_seconds() == 0


def _utc_timestamp(value: object, *, field: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(cast("Any", value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 instant") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return timestamp.tz_convert(UTC)


def _is_missing(value: object) -> bool:
    try:
        missing = pd.isna(cast("Any", value))
    except (TypeError, ValueError):
        return False
    if isinstance(missing, bool):
        return missing
    item = getattr(missing, "item", None)
    if callable(item):
        scalar = item()
        return scalar if isinstance(scalar, bool) else False
    return missing is pd.NA


def _identity_order_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


@dataclass(repr=False)
class LifecycleFrame(BaseFrame):
    """Canonical materialized replay history or pure Lifecycle reduction."""

    meta: LifecycleFrameMetaVariant

    _AVAILABLE_ENTRIES = (".show()", ".contract()", ".to_pandas()")

    def __post_init__(self) -> None:
        self._restore_persisted_identity_columns()
        super().__post_init__()
        self._validate_rows()
        self._validate_auxiliary_trace()

    @property
    def semantic_shape(
        self,
    ) -> Literal["history", "distribution", "transitions", "dwell", "violations"]:
        """Return the exact closed Lifecycle artifact shape."""
        return self.meta.semantic_kind

    def _restore_persisted_identity_columns(self) -> None:
        for frame in (self._df, *self._auxiliary_frames.values()):
            for column in (
                "subject_identity",
                "entered_by_event_identity",
                "exited_by_event_identity",
                "trigger_event_identity",
            ):
                if column in frame.columns:
                    frame[column] = frame[column].map(_identity_tuple)

    def _expected_columns(self) -> tuple[str, ...]:
        if self.meta.semantic_kind == "history":
            return LIFECYCLE_HISTORY_COLUMNS
        if self.meta.semantic_kind == "distribution":
            return tuple(axis.output_column for axis in self.meta.axes) + (
                LIFECYCLE_DISTRIBUTION_VALUE_COLUMNS
            )
        if self.meta.semantic_kind == "transitions":
            return LIFECYCLE_TRANSITIONS_COLUMNS
        if self.meta.semantic_kind == "dwell":
            return LIFECYCLE_DWELL_COLUMNS
        return LIFECYCLE_VIOLATIONS_COLUMNS

    def _validate_rows(self) -> None:
        expected = self._expected_columns()
        if tuple(self._df.columns) != expected:
            raise ValueError(
                f"LifecycleFrame[{self.meta.semantic_kind}] columns must be exactly {expected!r}"
            )
        state_names = {item.state.name for item in self.meta.states}
        for column in _STATE_NAME_FIELDS:
            if column in self._df.columns and any(
                value not in state_names for value in self._df[column].dropna()
            ):
                raise ValueError(f"Lifecycle {column} rows must use retained model states")
        if self.meta.semantic_kind == "history":
            self._validate_history_rows()
        elif self.meta.semantic_kind == "distribution":
            self._validate_distribution_rows()
        elif self.meta.semantic_kind == "transitions":
            self._validate_transition_rows()
        elif self.meta.semantic_kind == "dwell":
            self._validate_dwell_rows()
        else:
            self._validate_violation_rows(self._df)

    def _validate_subject_identity(self, frame: pd.DataFrame) -> None:
        components = len(self.meta.subject_identity)
        if any(
            not isinstance(identity, tuple)
            or len(identity) != components
            or any(component is None for component in identity)
            for identity in frame["subject_identity"]
        ):
            raise ValueError(
                "Lifecycle subject_identity rows must match the ordered identity signature"
            )

    def _validate_history_rows(self) -> None:
        meta = cast("LifecycleHistoryFrameMeta", self.meta)
        self._validate_subject_identity(self._df)
        statuses = {"completed", "right_censored", "coverage_censored"}
        if not set(self._df["interval_status"]).issubset(statuses):
            raise ValueError("Lifecycle history interval_status is invalid")
        for column in ("valid_from", "valid_to"):
            if any(not _is_utc_instant(value) for value in self._df[column]):
                raise ValueError(f"Lifecycle history {column} must contain UTC instants")
        if any(
            pd.Timestamp(start) >= pd.Timestamp(end)
            for start, end in zip(
                self._df["valid_from"],
                self._df["valid_to"],
                strict=True,
            )
        ):
            raise ValueError("Lifecycle history intervals must be non-empty")
        for row in self._df.itertuples(index=False):
            entered_ref = row.entered_by_event_ref
            entered_identity = row.entered_by_event_identity
            if (
                not isinstance(entered_ref, str)
                or not entered_ref
                or not isinstance(entered_identity, tuple)
            ):
                raise ValueError("Lifecycle history entry Event identity must be complete")
            completed = row.interval_status == "completed"
            exit_values = (
                row.exited_by_event_ref,
                row.exited_by_event_identity,
            )
            exit_complete = all(not _is_missing(value) for value in exit_values)
            if completed != exit_complete:
                raise ValueError(
                    "Lifecycle completed intervals alone must carry exit Event identity"
                )
        window_start = _utc_timestamp(meta.window.start, field="window.start")
        window_end = _utc_timestamp(meta.window.end, field="window.end")
        if window_start >= window_end:
            raise ValueError("Lifecycle replay window must be non-empty")
        previous_by_subject: dict[str, tuple[pd.Timestamp, str]] = {}
        ordering: list[tuple[str, pd.Timestamp]] = []
        for subject, start, end, status in zip(
            self._df["subject_identity"],
            self._df["valid_from"],
            self._df["valid_to"],
            self._df["interval_status"],
            strict=True,
        ):
            subject_key = _identity_order_key(subject)
            valid_from = pd.Timestamp(start)
            valid_to = pd.Timestamp(end)
            if valid_from < window_start or valid_to > window_end:
                raise ValueError("Lifecycle history intervals must stay inside replay window")
            previous = previous_by_subject.get(subject_key)
            if previous is not None:
                previous_end, previous_status = previous
                if previous_status != "completed" or valid_from != previous_end:
                    raise ValueError(
                        "Lifecycle adjacent intervals must meet at a completed boundary"
                    )
            previous_by_subject[subject_key] = (valid_to, str(status))
            ordering.append((subject_key, valid_from))
        if ordering != sorted(ordering):
            raise ValueError("Lifecycle history rows must be deterministically ordered")
        expected_final_status = (
            "coverage_censored" if meta.coverage_basis == "unknown" else "right_censored"
        )
        if any(status != expected_final_status for _, status in previous_by_subject.values()):
            raise ValueError(
                f"Lifecycle final intervals must be {expected_final_status!r} for retained coverage"
            )

    def _validate_distribution_rows(self) -> None:
        if any(value < 0 for value in self._df["subject_count"]):
            raise ValueError("Lifecycle distribution subject_count must be non-negative")
        if any(pd.notna(value) and not 0 <= float(value) <= 1 for value in self._df["share"]):
            raise ValueError("Lifecycle distribution share must lie in [0, 1] or be null")
        meta = cast("LifecycleDistributionFrameMeta", self.meta)
        if not set(self._df["as_of"]).issubset(set(meta.at)):
            raise ValueError("Lifecycle distribution rows must use retained instants")

    def _validate_transition_rows(self) -> None:
        if set(self._df["transition_status"]) != ({"modeled"} if len(self._df) else set()):
            raise ValueError("Lifecycle transition_status must be modeled")
        if any(value < 0 for value in self._df["transition_count"]):
            raise ValueError("Lifecycle transition_count must be non-negative")
        actual = tuple(
            zip(
                self._df["from_model_state"],
                self._df["to_model_state"],
                strict=True,
            )
        )
        expected = tuple(
            (item.from_state, item.to_state)
            for item in cast("LifecycleTransitionsFrameMeta", self.meta).modeled_pairs
        )
        if actual != expected:
            raise ValueError("Lifecycle transition rows must follow modeled pair order")
        meta = cast("LifecycleTransitionsFrameMeta", self.meta)
        counts = pd.to_numeric(self._df["transition_count"])
        if int(counts.sum()) != meta.modeled_transition_count:
            raise ValueError("Lifecycle transition rows must reconcile to modeled_transition_count")
        denominator = meta.modeled_transition_count
        for count, share in zip(
            counts,
            self._df["share_of_modeled_transitions"],
            strict=True,
        ):
            if denominator == 0:
                if not _is_missing(share):
                    raise ValueError("zero modeled transitions require null shares")
            elif _is_missing(share) or abs(float(share) - float(count) / denominator) > 1e-12:
                raise ValueError("Lifecycle transition shares must reconcile to counts")

    def _validate_dwell_rows(self) -> None:
        expected = tuple(item.state.name for item in self.meta.states)
        if tuple(self._df["model_state"]) != expected:
            raise ValueError("Lifecycle dwell rows must follow modeled state order")
        count_columns = (
            "interval_count",
            "completed_count",
            "right_censored_count",
            "coverage_censored_count",
        )
        if any(value < 0 for column in count_columns for value in self._df[column]):
            raise ValueError("Lifecycle dwell counts must be non-negative")
        counts = self._df[
            [
                "interval_count",
                "completed_count",
                "right_censored_count",
                "coverage_censored_count",
            ]
        ].apply(pd.to_numeric)
        if any(
            counts["interval_count"]
            != counts[
                [
                    "completed_count",
                    "right_censored_count",
                    "coverage_censored_count",
                ]
            ].sum(axis=1)
        ):
            raise ValueError("Lifecycle dwell status counts must partition interval_count")
        meta = cast("LifecycleDwellFrameMeta", self.meta)
        if int(counts["interval_count"].sum()) != meta.source_interval_count:
            raise ValueError("Lifecycle dwell rows must reconcile to source_interval_count")
        duration_columns = ("mean_duration", "median_duration", "p90_duration")
        for row in self._df.itertuples(index=False):
            durations = tuple(getattr(row, column) for column in duration_columns)
            if row.completed_count == 0 and any(not _is_missing(value) for value in durations):
                raise ValueError("Lifecycle dwell duration statistics require completed intervals")

    def _validate_violation_rows(self, frame: pd.DataFrame) -> None:
        if tuple(frame.columns) != LIFECYCLE_VIOLATIONS_COLUMNS:
            raise ValueError("Lifecycle violation trace columns are invalid")
        self._validate_subject_identity(frame)
        if not set(frame["violation_kind"]).issubset(
            {"illegal_transition", "transition_from_terminal"}
        ):
            raise ValueError("Lifecycle violation_kind is invalid")
        if any(not _is_utc_instant(value) for value in frame["occurred_at"]):
            raise ValueError("Lifecycle violation occurred_at must contain UTC instants")
        if any(not isinstance(value, str) or not value for value in frame["trigger_event_ref"]):
            raise ValueError("Lifecycle violation trigger_event_ref must be non-empty")
        if any(not isinstance(value, tuple) for value in frame["trigger_event_identity"]):
            raise ValueError("Lifecycle violation trigger identities must be tuples")
        state_names = {item.state.name for item in self.meta.states}
        if any(value not in state_names for value in frame["model_state_at_event"]):
            raise ValueError(
                "Lifecycle violation model_state_at_event must use retained model states"
            )
        ordering = tuple(
            (
                _identity_order_key(subject),
                pd.Timestamp(occurred_at),
                str(event_ref),
                _identity_order_key(event_identity),
            )
            for subject, occurred_at, event_ref, event_identity in zip(
                frame["subject_identity"],
                frame["occurred_at"],
                frame["trigger_event_ref"],
                frame["trigger_event_identity"],
                strict=True,
            )
        )
        if ordering != tuple(sorted(ordering)):
            raise ValueError("Lifecycle violation rows must be deterministically ordered")

    def _validate_auxiliary_trace(self) -> None:
        if self.meta.semantic_kind != "history":
            if self._auxiliary_frames:
                raise ValueError("Lifecycle reducers cannot own replay auxiliary tables")
            return
        history_meta = self.meta
        if set(self._auxiliary_frames) != {history_meta.violation_trace.filename}:
            raise ValueError("Lifecycle history requires exactly one private violation trace")
        trace = self._auxiliary_frames[history_meta.violation_trace.filename]
        self._validate_violation_rows(trace)
        if len(trace) != history_meta.violation_trace.row_count:
            raise ValueError("Lifecycle violation trace row count does not match metadata")

    def _auxiliary_tables(self) -> tuple[_FrameAuxiliaryTable, ...]:
        if self.meta.semantic_kind != "history":
            return super()._auxiliary_tables()
        manifest = self.meta.violation_trace
        return (
            _FrameAuxiliaryTable(
                filename=manifest.filename,
                dataframe=self._auxiliary_frames[manifest.filename],
            ),
        )

    def _bind_auxiliary_receipts(
        self,
        meta: BaseFrameMeta,
        receipts: tuple[_FrameAuxiliaryReceipt, ...],
    ) -> BaseFrameMeta:
        if not isinstance(meta, LifecycleHistoryFrameMeta):
            return super()._bind_auxiliary_receipts(meta, receipts)
        if len(receipts) != 1 or receipts[0].filename != meta.violation_trace.filename:
            raise ValueError("Lifecycle history persistence requires one violation trace")
        receipt = receipts[0]
        if receipt.row_count != meta.violation_trace.row_count:
            raise ValueError("written Lifecycle violation trace row count changed")
        return meta.model_copy(
            update={
                "violation_trace": meta.violation_trace.model_copy(
                    update={"content_hash": receipt.content_hash}
                )
            }
        )

    def _repr_identity(self) -> str:
        if self.meta.semantic_kind == "history":
            meta = self.meta
            return (
                f"LifecycleFrame ref={meta.ref} shape=history "
                f"model={meta.state_model_ref.path} coverage={meta.coverage_basis} "
                f"rows={meta.row_count}"
            )
        return (
            f"LifecycleFrame ref={self.meta.ref} shape={self.meta.semantic_kind} "
            f"model={self.meta.state_model_ref.path} rows={self.meta.row_count}"
        )

    def _semantic_input_bindings(self) -> tuple[_ArtifactSemanticBinding, ...]:
        """Expose retained StateModel, Event, and reducer-axis acquisition paths."""
        bindings = [
            _ArtifactSemanticBinding(
                role="state_model",
                semantic_kind=SemanticKind.STATE_MODEL,
                semantic_path=self.meta.state_model_ref.path,
            )
        ]
        if self.meta.semantic_kind == "history":
            bindings.extend(
                _ArtifactSemanticBinding(
                    role=f"event[{trigger.key}]",
                    semantic_kind=SemanticKind.EVENT,
                    semantic_path=trigger.event_ref.path,
                )
                for trigger in self.meta.triggers
            )
        elif self.meta.semantic_kind == "distribution":
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
        # History discloses the exact business choices behind the artifact:
        # the seed, the completeness basis, and the fixed violation contract.
        fields: tuple[tuple[str, str], ...] = ()
        if self.meta.semantic_kind == "history":
            meta = self.meta
            status = (
                f"coverage={meta.coverage_basis} population={meta.population_count} "
                f"seeded={meta.seeded_subject_count} violations={meta.violation_count}"
            )
            fields = (
                ("seed", meta.seed.kind),
                ("violation_contract", meta.violation_behavior_id),
            )
        else:
            status = f"source={cast('LifecycleReducerFrameMetaBase', self.meta).source_history_ref}"
        card = Card(identity=self._repr_identity(), available=self._AVAILABLE_ENTRIES).status(
            status
        )
        for label, value in fields:
            card = card.field(label, value)
        self._append_artifact_interface_sections(card)
        self._append_evidence_sections(card)
        return card.lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )


__all__ = [
    "LIFECYCLE_DISTRIBUTION_VALUE_COLUMNS",
    "LIFECYCLE_DWELL_COLUMNS",
    "LIFECYCLE_HISTORY_COLUMNS",
    "LIFECYCLE_TRANSITIONS_COLUMNS",
    "LIFECYCLE_VIOLATIONS_COLUMNS",
    "LifecycleAxisBinding",
    "LifecycleDistributionFrameMeta",
    "LifecycleDwellFrameMeta",
    "LifecycleFrame",
    "LifecycleFrameMetaBase",
    "LifecycleFrameMetaVariant",
    "LifecycleHistoryFrameMeta",
    "LifecycleReducerFrameMetaBase",
    "LifecycleStateBinding",
    "LifecycleStatePair",
    "LifecycleTraceManifest",
    "LifecycleTransitionsFrameMeta",
    "LifecycleTriggerBinding",
    "LifecycleViolationsFrameMeta",
    "PersistedModelStateHandle",
]
