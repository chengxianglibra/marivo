"""Event-owned loss-rate attribution construction and complete journey admission."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, LogicalDataset
from marivo.analysis.datasets.handles import CanonicalValue, LogicalRootHandle, _LogicalNodePayload
from marivo.analysis.domains.contracts import EventFunnelPayload
from marivo.analysis.domains.event_comparison import (
    FunnelComparePayload,
    FunnelCompareSpec,
    FunnelDeltaSemantics,
    compare,
)
from marivo.analysis.domains.event_reducers import funnel, step_index
from marivo.analysis.funnel import FunnelLossRate
from marivo.analysis.observation.contracts import DimensionInput, owner_of, producer_contract
from marivo.analysis.operators.attribute import _generated
from marivo.analysis.operators.attribution import LogicalAttributionDataset
from marivo.analysis.operators.errors import attribution_error

REPAIR = "Rebuild both funnels logically from complete journeys or engine journey scans, then compare and attribute before materializing aggregate rows."
METHOD = "funnel_ratio_mix@v1"
COMPONENT_ROLE = "event_funnel_components"
COMPONENT_CONTRACT = "event_funnel.additive_components"
COMPONENT_NAMES = tuple(
    f"{side}_{name}"
    for side in ("current", "baseline")
    for name in ("lost_count", "resolved_entry_count")
)
COMPONENT_COLUMNS = tuple(
    f"__event_{prefix}{name}" for prefix in ("", "total_") for name in COMPONENT_NAMES
)
GENERATED = (
    ("active_axis_mask", "attribution_partition_identity", "mask", False),
    ("other_mask", "attribution_partition_identity", "mask", False),
    ("contribution_kind", "attribution_partition_identity", "string", False),
    ("current_value", "comparison_value", "float64", False),
    ("baseline_value", "comparison_value", "float64", False),
    ("overall_delta", "comparison_value", "float64", False),
    ("contribution", "effect_value", "float64", False),
    ("share_of_total_delta", "effect_value", "float64", True),
    ("share_of_positive_pool", "effect_value", "float64", True),
    ("share_of_negative_pool", "effect_value", "float64", True),
    ("contribution_rank", "rank", "int64", False),
    ("method", "method_identity", "string", False),
    ("causal_claim", "method_identity", "string", False),
    ("status", "status", "string", False),
)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class FunnelAttributionSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    delta_current_json: str
    delta_baseline_json: str
    step_key: str
    top_k: int | None
    axis_field_ids: tuple[d.DatasetFieldId, ...]
    resolution_prefixes: tuple[tuple[d.DatasetFieldId, ...], ...]
    kind: Literal["attribution/funnel-loss-rate@v1"] = field(
        default="attribution/funnel-loss-rate@v1", init=False
    )

    @property
    def delta(self) -> FunnelDeltaSemantics:
        return FunnelDeltaSemantics(
            _token=d._CORE_TOKEN,
            current_json=self.delta_current_json,
            baseline_json=self.delta_baseline_json,
        )


@dataclass(frozen=True, slots=True, repr=False)
class FunnelAttributeSpec:
    input_row: d.DatasetRowContract
    expanded: FunnelCompareSpec
    output_row: d.DatasetRowContract
    output_rows: d.DatasetRowSetContract
    axis_fields: tuple[d.DatasetField, ...]
    step_key: str
    mode: Literal["joint", "hierarchy"]
    top_k: int | None

    def identity_payload(self) -> CanonicalValue:
        return (
            "attribute/event_funnel_delta@v1",
            d._descriptor_payload(self.input_row),
            self.expanded.identity_payload(),
            d._descriptor_payload(self.output_row),
            d._descriptor_payload(self.output_rows),
            self.step_key,
            self.mode,
            self.top_k,
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class FunnelAttributePayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    spec: FunnelAttributeSpec

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


def attribute(
    dataset: Dataset,
    *,
    target: FunnelLossRate,
    axes: list[DimensionInput] | tuple[DimensionInput, ...],
    mode: Literal["joint", "hierarchy"],
    top_k: int | None,
) -> LogicalAttributionDataset:
    semantics = dataset.row_contract.family_semantics
    if (
        not isinstance(dataset, LogicalDataset)
        or not isinstance(semantics, FunnelDeltaSemantics)
        or semantics.current.axis_refs
    ):
        raise attribution_error(
            "an ungrouped logical Event Delta with complete journey inputs",
            "aggregate checkpoint or grouped scope",
            repair=REPAIR,
        )
    if (
        type(target) is not FunnelLossRate
        or step_index(semantics.current.journey, target.step) == 0
    ):
        raise attribution_error(
            "an exact non-initial FunnelLossRate target", "invalid target", repair=REPAIR
        )
    if (
        mode not in ("joint", "hierarchy")
        or type(axes) not in (tuple, list)
        or not axes
        or (mode == "hierarchy" and len(axes) < 2)
        or (top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 1000))
    ):
        raise attribution_error(
            "unique nonempty axes, at least two for hierarchy, and top_k in [1, 1000]",
            "invalid attribution options",
        )
    original: Dataset = dataset
    while (
        isinstance(original._root, LogicalRootHandle)
        and original._root.operator_id == "delta.where"
    ):
        original = original._inputs[0]
    if not isinstance(original._root, LogicalRootHandle) or not isinstance(
        original._root.payload, FunnelComparePayload
    ):
        raise attribution_error(
            "exact logical comparison inputs", "missing comparison authority", repair=REPAIR
        )
    expanded = []
    for operand in original._inputs:
        # Summary filtering cannot be undone to recover a larger membership scope.
        if not isinstance(operand._root, LogicalRootHandle) or not isinstance(
            operand._root.payload, EventFunnelPayload
        ):
            raise attribution_error(
                "unfiltered logical funnels retaining journey assignments",
                "filtered or materialized funnel",
                repair=REPAIR,
            )
        expanded.append(funnel(operand._inputs[0], axes))
    comparison = compare(expanded[0], expanded[1])
    assert isinstance(comparison._root, LogicalRootHandle) and isinstance(
        comparison._root.payload, FunnelComparePayload
    )
    ids = dataset._registration.ids
    axis_fields = expanded[0].schema.columns[: len(axes)]
    output_axes = tuple(replace(f, _token=d._CORE_TOKEN, nullable=True) for f in axis_fields)
    generated = tuple(
        _generated(name, role, f"bool_tuple:{len(axes)}" if kind == "mask" else kind, nullable, ids)
        for name, role, kind, nullable in GENERATED
    )
    kind_index = 2
    kind_id = d._make_field_id("generated.event_funnel.attribute.contribution_kind@v1")
    generated = (
        *generated[:kind_index],
        replace(
            generated[kind_index],
            _token=d._CORE_TOKEN,
            field_id=kind_id,
            identity=d._generated_identity(kind_id),
            derivation_identity=kind_id.value,
        ),
        *generated[kind_index + 1 :],
    )
    fields = (*output_axes, *generated)
    if len({f.name for f in fields}) != len(fields):
        raise attribution_error(
            "non-colliding authored axes and contribution fields", "axis name collision"
        )
    axis_ids = tuple(f.field_id for f in axis_fields)
    keys = (generated[0].field_id, *axis_ids, generated[1].field_id, kind_id)
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("attribution", "funnel-loss-rate", 1, ids=ids),
        schema=d._make_schema(fields),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=FunnelAttributionSemantics(
            _token=d._CORE_TOKEN,
            delta_current_json=semantics.current_json,
            delta_baseline_json=semantics.baseline_json,
            step_key=target.step.key,
            top_k=top_k,
            axis_field_ids=axis_ids,
            resolution_prefixes=(axis_ids,)
            if mode == "joint"
            else tuple(axis_ids[:i] for i in range(1, len(axis_ids) + 1)),
        ),
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._unordered_ordering(),
    )
    spec = FunnelAttributeSpec(
        dataset.row_contract,
        comparison._root.payload.spec,
        row,
        rows,
        axis_fields,
        target.step.key,
        mode,
        top_k,
    )
    result = construct_operator(
        owner=owner_of(expanded[0]),
        registry=dataset._registry,
        operator_id="delta.funnel_attribute",
        inputs=(dataset, *expanded),
        row_contract=row,
        row_set_contract=rows,
        contract_versions=producer_contract("delta.funnel_attribute").versions,
        payload=FunnelAttributePayload(_token=d._CORE_TOKEN, spec=spec),
    )
    if not isinstance(result, LogicalAttributionDataset):
        raise attribution_error("the shared Attribution family", "invalid registration")
    return result


def validate_attribution(
    row: d.DatasetRowContract, rows: d.DatasetRowSetContract, ids: d._StableIdRegistry
) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, FunnelAttributionSemantics):
        raise attribution_error("exact Event Attribution semantics", "invalid semantics")
    from marivo.analysis.domains.event_comparison import compatible

    compatible(semantics.delta.current, semantics.delta.baseline)
    axes = row.schema.columns[: len(semantics.axis_field_ids)]
    count = len(axes)
    if (
        not count
        or (
            semantics.top_k is not None
            and (type(semantics.top_k) is not int or not 1 <= semantics.top_k <= 1000)
        )
        or tuple(f.field_id for f in axes) != semantics.axis_field_ids
        or any(f.role_id != "dimension" or not f.nullable for f in axes)
        or semantics.step_key
        not in tuple(step.key for step in semantics.delta.current.journey.pattern.steps[1:])
        or semantics.resolution_prefixes
        not in (
            (semantics.axis_field_ids,),
            tuple(semantics.axis_field_ids[:i] for i in range(1, count + 1)),
        )
        or len(row.schema.columns) != count + len(GENERATED)
        or str(row.shape_id) != "attribution/funnel-loss-rate@v1"
    ):
        raise attribution_error(
            "closed Event attribution axes, target and resolutions", "invalid retained attribution"
        )
    for actual, (name, role, logical, nullable) in zip(
        row.schema.columns[count:], GENERATED, strict=True
    ):
        expected = _generated(
            name, role, f"bool_tuple:{count}" if logical == "mask" else logical, nullable, ids
        )
        if name == "contribution_kind":
            identity = d._make_field_id("generated.event_funnel.attribute.contribution_kind@v1")
            expected = replace(
                expected,
                _token=d._CORE_TOKEN,
                field_id=identity,
                identity=d._generated_identity(identity),
                derivation_identity=identity.value,
            )
        if actual != expected:
            raise attribution_error(
                "exact Event attribution generated fields", "changed generated contract"
            )
    generated = row.schema.columns[count:]
    keys = (
        generated[0].field_id,
        *semantics.axis_field_ids,
        generated[1].field_id,
        generated[2].field_id,
    )
    if (
        row.key_field_ids != keys
        or row.coordinate_field_ids != keys
        or rows.ordering.kind != "unordered"
    ):
        raise attribution_error("canonical Event contribution coordinates", "invalid keys or order")


def admits_attribute(dataset: Dataset) -> bool:
    """Advertise the shared attribute method only while exact journeys remain."""
    semantics = dataset.row_contract.family_semantics
    if (
        not isinstance(dataset, LogicalDataset)
        or not isinstance(semantics, FunnelDeltaSemantics)
        or semantics.current.axis_refs
    ):
        return False
    value: Dataset = dataset
    while isinstance(value._root, LogicalRootHandle) and value._root.operator_id == "delta.where":
        value = value._inputs[0]
    return (
        isinstance(value._root, LogicalRootHandle)
        and isinstance(value._root.payload, FunnelComparePayload)
        and all(
            isinstance(v._root, LogicalRootHandle)
            and isinstance(v._root.payload, EventFunnelPayload)
            for v in value._inputs
        )
    )
