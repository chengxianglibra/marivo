"""Closed additive driver-axis search authority and identity-free evaluation facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.operators.contracts import CompareSpecV1

if TYPE_CHECKING:
    from marivo.analysis.operators.candidate_contracts import CandidateSemantics


@dataclass(frozen=True, slots=True, repr=False)
class DriverCandidateDefinition:
    input_state_kind: Literal["logical", "materialized"]
    input_authority: str
    limit: int
    approximation: str
    fold_authority: str
    baseline_fold_authority: str
    metric_key: str
    metric_unit: str | None
    search_space: tuple[str, ...]
    scope_fields: tuple[d.DatasetField, ...]
    paired_time_fields: tuple[d.DatasetField, ...]
    objective: Literal["driver_axes"] = "driver_axes"
    method_id: Literal["axis_concentration@v1"] = "axis_concentration@v1"

    def identity_payload(self) -> CanonicalValue:
        return (
            "candidate/driver-definition@v1",
            self.objective,
            self.method_id,
            self.input_state_kind,
            self.input_authority,
            self.limit,
            self.approximation,
            self.fold_authority,
            self.baseline_fold_authority,
            self.metric_key,
            self.metric_unit,
            self.search_space,
            tuple(d._descriptor_payload(v) for v in self.scope_fields),
            tuple(d._descriptor_payload(v) for v in self.paired_time_fields),
        )


@dataclass(frozen=True, slots=True, repr=False)
class DriverCandidateSpecV1:
    input_row: d.DatasetRowContract
    input_rows: d.DatasetRowSetContract
    output_row: d.DatasetRowContract
    output_rows: d.DatasetRowSetContract
    definition: DriverCandidateDefinition
    axis_fields: tuple[d.DatasetField, ...]
    scope_fields: tuple[d.DatasetField, ...]
    current_fold_authority: str
    baseline_fold_authority: str
    expanded_compare: CompareSpecV1 | None = None
    original_input_row: d.DatasetRowContract | None = None
    original_input_rows: d.DatasetRowSetContract | None = None

    @property
    def semantics(self) -> CandidateSemantics:
        from marivo.analysis.operators.candidate_contracts import CandidateSemantics
        from marivo.analysis.operators.errors import discovery_error

        result = self.output_row.family_semantics
        if not isinstance(result, CandidateSemantics):
            raise discovery_error("closed Candidate semantics", "invalid family")
        return result

    def identity_payload(self) -> CanonicalValue:
        return (
            "candidate/driver-spec@v1",
            *(
                d._descriptor_payload(v)
                for v in (self.input_row, self.input_rows, self.output_row, self.output_rows)
            ),
            self.definition.identity_payload(),
            tuple(field.field_id.value for field in self.axis_fields),
            tuple(field.field_id.value for field in self.scope_fields),
            self.current_fold_authority,
            self.baseline_fold_authority,
            None if self.expanded_compare is None else self.expanded_compare.identity_payload(),
            None
            if self.original_input_row is None
            else d._descriptor_payload(self.original_input_row),
            None
            if self.original_input_rows is None
            else d._descriptor_payload(self.original_input_rows),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class DriverCandidatePayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    spec: DriverCandidateSpecV1

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


@dataclass(frozen=True, slots=True)
class DriverCandidateEvaluationSummary:
    input_row_count: int
    scope_count: int
    searched_axis_count: int
    evaluated_axis_count: int
    zero_contribution_axis_count: int
    pre_limit_candidate_count: int
    emitted_candidate_count: int
    score_range: tuple[float, float] | None
    reason_counts: tuple[tuple[str, int], ...]
