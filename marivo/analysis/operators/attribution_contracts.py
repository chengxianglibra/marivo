"""Closed additive Attribution authority and side-state naming contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFamilyRowSemantics,
    DatasetField,
    DatasetFieldId,
    DatasetRowContract,
    DatasetRowSetContract,
    _descriptor_payload,
)
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.observation.fold_contracts import MetricFoldAuthorityV1, decode_fold_authority
from marivo.analysis.operators.contracts import CompareSpecV1, DeltaSemantics
from marivo.analysis.operators.errors import attribution_error

AttributionMethod = Literal["additive_difference@v1", "component_mix@v1", "distinct_membership@v1"]
AttributionMode = Literal["joint", "hierarchy"]


def delta_state_name(side: str, name: str) -> str:
    return f"__mv_{side}_{name}"


def delta_presence_name(side: str) -> str:
    return f"__mv_{side}_present"


def delta_part_authorities(
    row: DatasetRowContract,
) -> tuple[tuple[str, MetricFoldAuthorityV1], ...]:
    semantics = row.family_semantics
    if not isinstance(semantics, DeltaSemantics):
        raise attribution_error("exact Delta fold authority", "invalid Delta semantics")
    result: list[tuple[str, MetricFoldAuthorityV1]] = []
    for side, payload in (
        ("current", semantics.current_fold_authority),
        ("baseline", semantics.baseline_fold_authority),
    ):
        try:
            authority = decode_fold_authority(payload)
        except ValueError:
            raise attribution_error(
                "complete retained side fold authority", "invalid side authority"
            ) from None
        if len(authority.metrics) != 1 or authority.metrics[0].metric_ref != semantics.metric_ref:
            raise attribution_error("one matching side Metric authority", "invalid side Metric")
        result.append((f"delta_components.{side}", authority.metrics[0]))
    return tuple(result)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AttributionSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
    metric_ref: str
    metric_unit: str | None
    numeric_type: str
    scope_field_ids: tuple[DatasetFieldId, ...]
    axis_field_ids: tuple[DatasetFieldId, ...]
    resolution_prefixes: tuple[tuple[DatasetFieldId, ...], ...]
    current_time_field_name: str | None
    baseline_time_field_name: str | None
    method: AttributionMethod
    approximation_class: Literal["exact", "sampled_population"]
    resolution_semantics: Literal["rollup", "independent"] = "rollup"
    rollup_safe: bool = True
    kind: Literal["attribution/metric@v1"] = field(default="attribution/metric@v1", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class AttributeSpecV1:
    input_row: DatasetRowContract
    input_rows: DatasetRowSetContract
    output_row: DatasetRowContract
    output_rows: DatasetRowSetContract
    axis_fields: tuple[DatasetField, ...]
    scope_fields: tuple[DatasetField, ...]
    method: AttributionMethod
    mode: AttributionMode
    top_k: int | None
    current_fold_authority: str
    baseline_fold_authority: str
    expanded_compare: CompareSpecV1 | None = None
    original_input_row: DatasetRowContract | None = None

    def identity_payload(self) -> CanonicalValue:
        return (
            "attribute/metric@v1",
            _descriptor_payload(self.input_row),
            _descriptor_payload(self.input_rows),
            _descriptor_payload(self.output_row),
            _descriptor_payload(self.output_rows),
            tuple(field.field_id.value for field in self.axis_fields),
            tuple(field.field_id.value for field in self.scope_fields),
            self.method,
            self.mode,
            self.top_k,
            self.current_fold_authority,
            self.baseline_fold_authority,
            None if self.expanded_compare is None else self.expanded_compare.identity_payload(),
            None
            if self.original_input_row is None
            else _descriptor_payload(self.original_input_row),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class AttributePayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    spec: AttributeSpecV1

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


def attribution_filterable_field(field: DatasetField) -> bool:
    """Keep generated selector authority at the registered Attribution owner."""
    from marivo.analysis.operators.attribute import GENERATED

    return any(
        field.field_id.value == f"generated.attribute.{name}@v1"
        and field.name == name
        and field.role_id == role
        for name, role, _, _ in GENERATED
    )
