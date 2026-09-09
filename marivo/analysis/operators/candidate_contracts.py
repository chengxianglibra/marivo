"""Closed private discovery invocations, row meaning and evaluation authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.operators.contracts import DeltaSemantics
from marivo.analysis.operators.errors import discovery_error

CandidateObjective = Literal[
    "point_anomalies", "interesting_windows", "period_shifts", "entity_outliers"
]
CandidateMethod = Literal[
    "point_zscore@v1", "global_zscore_runs@v1", "delta_window_zscore@v1", "entity_mad@v1"
]
METHODS: dict[CandidateObjective, CandidateMethod] = {
    "point_anomalies": "point_zscore@v1",
    "interesting_windows": "global_zscore_runs@v1",
    "period_shifts": "delta_window_zscore@v1",
    "entity_outliers": "entity_mad@v1",
}
SHAPES: dict[CandidateObjective, str] = {
    "point_anomalies": "point-anomaly",
    "interesting_windows": "interesting-window",
    "period_shifts": "period-shift",
    "entity_outliers": "entity-outlier",
}
REASON_CODES: dict[CandidateObjective, str] = {
    "point_anomalies": "point_zscore_threshold_met",
    "interesting_windows": "global_zscore_run",
    "period_shifts": "delta_window_zscore_run",
    "entity_outliers": "entity_mad_threshold_met",
}


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class CandidateSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    """Only the facts needed to interpret rows, referencing their exact fields."""

    objective: CandidateObjective
    method_id: CandidateMethod
    approximation: str
    item_id_field_id: d.DatasetFieldId
    score_field_id: d.DatasetFieldId
    reason_codes_field_id: d.DatasetFieldId
    kind: Literal["candidate/discovery@v1"] = field(default="candidate/discovery@v1", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class CandidateDefinition:
    """Original input and normalized search authority retained once in Evidence."""

    objective: CandidateObjective
    method_id: CandidateMethod
    input_state_kind: Literal["logical", "materialized"]
    input_authority: str
    threshold: float
    limit: int
    approximation: str
    fold_authority: str
    baseline_fold_authority: str | None
    metric_key: str
    metric_unit: str | None

    def identity_payload(self) -> CanonicalValue:
        return (
            "candidate/definition@v1",
            self.objective,
            self.method_id,
            self.input_state_kind,
            self.input_authority,
            self.threshold,
            self.limit,
            self.approximation,
            self.fold_authority,
            self.baseline_fold_authority,
            self.metric_key,
            self.metric_unit,
        )


@dataclass(frozen=True, slots=True, repr=False)
class CandidateSpecV1:
    input_row: d.DatasetRowContract
    input_rows: d.DatasetRowSetContract
    output_row: d.DatasetRowContract
    output_rows: d.DatasetRowSetContract
    definition: CandidateDefinition

    @property
    def semantics(self) -> CandidateSemantics:
        result = self.output_row.family_semantics
        if not isinstance(result, CandidateSemantics):
            raise discovery_error("closed Candidate semantics", "invalid family")
        return result

    @property
    def metric_name(self) -> str:
        if self.definition.objective == "period_shifts":
            return "delta"
        return next(f.name for f in self.input_row.schema.columns if f.role_id == "metric")

    @property
    def time_name(self) -> str:
        s = self.input_row.family_semantics
        if isinstance(s, DeltaSemantics) and s.current_time_field_name is not None:
            return s.current_time_field_name
        return next(f.name for f in self.input_row.schema.columns if f.role_id == "time_dimension")

    @property
    def baseline_time_name(self) -> str | None:
        s = self.input_row.family_semantics
        return s.baseline_time_field_name if isinstance(s, DeltaSemantics) else None

    @property
    def dimensions(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.input_row.schema.columns if f.role_id == "dimension")

    def identity_payload(self) -> CanonicalValue:
        return (
            "candidate/spec@v1",
            *(
                d._descriptor_payload(v)
                for v in (self.input_row, self.input_rows, self.output_row, self.output_rows)
            ),
            self.definition.identity_payload(),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class CandidatePayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    spec: CandidateSpecV1

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


@dataclass(frozen=True, slots=True)
class CandidateEvaluationSummary:
    """Bounded original evaluation facts preserved across retained selections."""

    input_row_count: int
    series_count: int
    evaluated_series_count: int
    insufficient_series_count: int
    constant_series_count: int
    unavailable_series_count: int
    searched_unit_count: int
    evaluated_unit_count: int
    pre_limit_candidate_count: int
    emitted_candidate_count: int
    score_range: tuple[float, float] | None
    reason_counts: tuple[tuple[str, int], ...]
    baseline_mean_range: tuple[float, float] | None
    baseline_stddev_range: tuple[float, float] | None
    window_size_range: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class EntityCandidateEvaluationSummary:
    """Identity-free complete-cohort facts for the Entity screening evaluation."""

    input_row_count: int
    non_null_value_count: int
    null_value_count: int
    center: float
    scale: float
    scale_method: Literal["mad", "mean_absolute_deviation"]
    pre_limit_candidate_count: int
    emitted_candidate_count: int
    score_range: tuple[float, float] | None
    reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True, repr=False)
class CandidateSearchSummary:
    """Carry original discovery authority and its evaluation as one immutable pair."""

    definition: CandidateDefinition
    evaluation: CandidateEvaluationSummary | EntityCandidateEvaluationSummary
