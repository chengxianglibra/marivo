"""Event-owned comparison authority over complete funnel cells."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.handles import CanonicalValue, LogicalRootHandle, _LogicalNodePayload
from marivo.analysis.domains.contracts import EventFunnelSemantics
from marivo.analysis.domains.event_reducers import _rows
from marivo.analysis.observation.contracts import RetainedRowsPayload, owner_of, producer_contract
from marivo.analysis.observation.predicates import BoundPredicate
from marivo.analysis.operators.delta import LogicalDeltaDataset
from marivo.analysis.operators.errors import comparison_error

COUNTS = (
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    "coverage_censored_count",
)
GENERATED = (
    ("step_key", "pattern_step_identity", "string", False),
    ("coordinate_presence", "status", "string", False),
    *(
        (f"{side}_{name}", "comparison_value", "int64", False)
        for name in COUNTS
        for side in ("current", "baseline")
    ),
    ("current_loss_rate_from_previous", "comparison_value", "float64", True),
    ("baseline_loss_rate_from_previous", "comparison_value", "float64", True),
    ("loss_rate_delta", "comparison_value", "float64", True),
    ("calculation_status", "status", "string", False),
)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class FunnelDeltaSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    current_json: str
    baseline_json: str
    kind: Literal["delta/funnel@v1"] = field(default="delta/funnel@v1", init=False)

    @property
    def current(self) -> EventFunnelSemantics:
        return decode_funnel(self.current_json)

    @property
    def baseline(self) -> EventFunnelSemantics:
        return decode_funnel(self.baseline_json)


def encode_funnel(value: EventFunnelSemantics) -> str:
    return json.dumps(asdict(value), sort_keys=True, separators=(",", ":"))


def decode_funnel(value: str) -> EventFunnelSemantics:
    try:
        raw: object = json.loads(value)
    except (TypeError, ValueError) as error:
        raise comparison_error(
            "canonical Event funnel authority", "invalid retained encoding"
        ) from error
    if (
        not isinstance(raw, dict)
        or set(raw) != {"kind", "journey_json", "axis_refs", "axis_dependency_fingerprints"}
        or raw.get("kind") != "event/funnel@v1"
    ):
        raise comparison_error("closed Event funnel authority", "invalid retained fields")
    journey_json: object = raw["journey_json"]
    if not isinstance(journey_json, str):
        raise comparison_error("canonical journey authority", "invalid retained journey")

    def strings(name: str) -> tuple[str, ...]:
        values: object = raw[name]
        if not isinstance(values, list) or any(type(v) is not str for v in values):
            raise comparison_error("ordered text axis facts", "invalid retained axes")
        return tuple(v for v in values if isinstance(v, str))

    result = EventFunnelSemantics(
        _token=d._CORE_TOKEN,
        journey_json=journey_json,
        axis_refs=strings("axis_refs"),
        axis_dependency_fingerprints=strings("axis_dependency_fingerprints"),
    )
    _ = result.journey
    if (
        encode_funnel(result) != value
        or len(result.axis_refs) != len(result.axis_dependency_fingerprints)
        or len(set(result.axis_refs)) != len(result.axis_refs)
    ):
        raise comparison_error("canonical Event funnel authority", "invalid retained funnel")
    return result


@dataclass(frozen=True, slots=True, repr=False)
class FunnelCompareSpec:
    current_row: d.DatasetRowContract
    baseline_row: d.DatasetRowContract
    output_row: d.DatasetRowContract
    output_rows: d.DatasetRowSetContract
    current_predicates: tuple[BoundPredicate, ...] = ()
    baseline_predicates: tuple[BoundPredicate, ...] = ()

    def identity_payload(self) -> CanonicalValue:
        return (
            "compare/event_funnel@v1",
            *(
                d._descriptor_payload(value)
                for value in (
                    self.current_row,
                    self.baseline_row,
                    self.output_row,
                    self.output_rows,
                )
            ),
            tuple(p.identity_payload() for p in self.current_predicates),
            tuple(p.identity_payload() for p in self.baseline_predicates),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class FunnelComparePayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    spec: FunnelCompareSpec

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


def compatible(current: EventFunnelSemantics, baseline: EventFunnelSemantics) -> None:
    a, b = current.journey, baseline.journey
    same = (
        "pattern_json",
        "matching_json",
        "subject_entity_ref",
        "subject_identity_signature",
        "occurrence_identity_types",
        "population_definition",
        "step_event_fingerprints",
        "sampling_authority",
    )
    if any(getattr(a, name) != getattr(b, name) for name in same) or (
        current.axis_refs != baseline.axis_refs
        or current.axis_dependency_fingerprints != baseline.axis_dependency_fingerprints
    ):
        raise comparison_error(
            "compatible exact Event funnel definitions and axes",
            "different Event authority",
            repair="Rebuild both funnels from complete journeys with matching Event pattern, matching policy, subject, population and axes.",
        )
    starts = tuple(datetime.fromisoformat(v.cohort_start) for v in (a, b))
    ends = tuple(datetime.fromisoformat(v.cohort_end) for v in (a, b))
    through = tuple(datetime.fromisoformat(v.completion_through) for v in (a, b))
    if (
        starts[0].tzinfo != starts[1].tzinfo
        or ends[0] - starts[0] != ends[1] - starts[1]
        or through[0] - ends[0] != through[1] - ends[1]
    ):
        raise comparison_error(
            "equal cohort duration, temporal domain and follow-up offset",
            "incompatible Event windows",
            repair="Rebuild both Event funnels with equal cohort duration, temporal domain and follow-up offset, using complete logical journeys or recovered journey checkpoints.",
        )


def generated_fields(ids: d._StableIdRegistry) -> tuple[d.DatasetField, ...]:
    result = []
    for name, role, logical, nullable in GENERATED:
        identity = d._make_field_id(f"generated.compare.{name}@v1")
        result.append(
            d._make_field(
                field_id=identity,
                name=name,
                role_id=role,
                identity=d._generated_identity(identity),
                derivation_identity=f"compare.{name}@v1",
                logical_type_id=logical,
                physical_type_state=d._deferred_type(logical, ids=ids),
                nullable=nullable,
                ids=ids,
            )
        )
    return tuple(result)


def compare(current: Dataset, baseline: Dataset) -> LogicalDeltaDataset:
    if not isinstance(baseline, Dataset):
        raise comparison_error("an Event funnel Dataset baseline", "invalid baseline type")
    a, b = current.row_contract.family_semantics, baseline.row_contract.family_semantics
    if type(a) is not EventFunnelSemantics or type(b) is not EventFunnelSemantics:
        raise comparison_error("two event/funnel@v1 operands", "non-funnel input")
    compatible(a, b)
    axes = current.schema.columns[: len(a.axis_refs)]
    if axes != baseline.schema.columns[: len(b.axis_refs)]:
        raise comparison_error("the same exact axis schema", "different axis contracts")
    ids = current._registration.ids
    fields = (*axes, *generated_fields(ids))
    keys = tuple(f.field_id for f in (*axes, fields[len(axes)]))
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("delta", "funnel", 1, ids=ids),
        schema=d._make_schema(fields),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=FunnelDeltaSemantics(
            _token=d._CORE_TOKEN, current_json=encode_funnel(a), baseline_json=encode_funnel(b)
        ),
    )
    rows = _rows(
        tuple(
            (key, "event.pattern_step@v1" if key == keys[-1] else "observation.scalar_order@v1")
            for key in keys
        ),
        ids,
    )

    def unfiltered(value: Dataset) -> tuple[Dataset, tuple[BoundPredicate, ...]]:
        predicates: list[BoundPredicate] = []
        while (
            isinstance(value._root, LogicalRootHandle) and value._root.operator_id == "event.where"
        ):
            payload = value._root.payload
            assert isinstance(payload, RetainedRowsPayload) and payload.predicate is not None
            predicates.append(payload.predicate)
            value = value._inputs[0]
        return value, tuple(reversed(predicates))

    raw_current, current_predicates = unfiltered(current)
    raw_baseline, baseline_predicates = unfiltered(baseline)
    result = construct_operator(
        owner=owner_of(current),
        registry=current._registry,
        operator_id="event.compare",
        inputs=(raw_current, raw_baseline),
        row_contract=row,
        row_set_contract=rows,
        contract_versions=producer_contract("event.compare").versions,
        payload=FunnelComparePayload(
            _token=d._CORE_TOKEN,
            spec=FunnelCompareSpec(
                current.row_contract,
                baseline.row_contract,
                row,
                rows,
                current_predicates,
                baseline_predicates,
            ),
        ),
    )
    if not isinstance(result, LogicalDeltaDataset):
        raise comparison_error("the shared Logical Delta family", "invalid comparison registration")
    return result


def validate_delta(
    row: d.DatasetRowContract, rows: d.DatasetRowSetContract, ids: d._StableIdRegistry
) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, FunnelDeltaSemantics):
        raise comparison_error("closed Event Delta semantics", "invalid Event Delta")
    compatible(semantics.current, semantics.baseline)
    axes = row.schema.columns[: len(semantics.current.axis_refs)]
    fields = generated_fields(ids)
    keys = tuple(f.field_id for f in (*axes, fields[0]))
    if (
        str(row.shape_id) != "delta/funnel@v1"
        or row.schema.columns != (*axes, *fields)
        or row.key_field_ids != keys
        or row.coordinate_field_ids != keys
        or len(axes) != len(semantics.current.axis_refs)
        or any(
            f.role_id != "dimension"
            or f.name != reference.rsplit(".", 1)[-1]
            or not isinstance(f.identity, d._CatalogFieldIdentity)
            or f.identity.identity_id != f"dimension:{reference}"
            for f, reference in zip(axes, semantics.current.axis_refs, strict=True)
        )
        or rows
        != _rows(
            tuple(
                (key, "event.pattern_step@v1" if key == keys[-1] else "observation.scalar_order@v1")
                for key in keys
            ),
            ids,
        )
    ):
        raise comparison_error(
            "the exact Event Delta schema and ordering", "changed Event Delta contract"
        )


def filterable_field(value: d.DatasetField) -> bool:
    return any(
        value.name == name
        and value.role_id == role
        and value.logical_type_id == logical
        and value.nullable is nullable
        and value.field_id.value == f"generated.compare.{name}@v1"
        and value.identity == d._generated_identity(value.field_id)
        and value.derivation_identity == f"compare.{name}@v1"
        for name, role, logical, nullable in GENERATED
    )
