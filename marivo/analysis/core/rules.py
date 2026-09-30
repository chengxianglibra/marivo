"""Closed, source-free R3.1 rule derivation and fixed-input semantic helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

from marivo.analysis.core.model import (
    Binding,
    Cell,
    CheckId,
    CohortDecisionPart,
    Coordinate,
    CoordinateStatePart,
    Correspondence,
    CorrespondencePart,
    CoveragePart,
    Defined,
    DerivedQuantity,
    DomainSignature,
    EndpointPart,
    Evidence,
    Fact,
    FactInput,
    FactKind,
    MissingCoordinate,
    Obligation,
    ObservedQuantity,
    OriginalStatePart,
    PairCountsPart,
    Part,
    PartRole,
    Quantity,
    ReferenceStatePart,
    RolledQuantity,
    RowStatePart,
    RowStatisticQuantity,
    Signature,
    StatisticalWeightPart,
    SubjectPart,
    Undefined,
    available_facts,
    part_role,
    reject,
    require_part,
)
from marivo.analysis.core.predicates import ValuePredicate, leaves
from marivo.analysis.core.time_grid import CumulativeBinding, GridVersionSelection
from marivo.refs import (
    DimensionKind,
    EntityKind,
    MeasureKind,
    MetricKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    TimeDimensionKind,
)
from marivo.refs import (
    ref as semantic_ref,
)
from marivo.semantic.ir import (
    DIRECT_ONLY_AGGREGATES,
    TargetDimensionContract,
    TargetEntityContract,
    TargetRelationshipContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    MetricExpressionGraphV1,
    SliceOperatorV1,
    TargetMetricContract,
)
from marivo.semantic.runtime_metric import RuntimeMetricExpr, SliceValue

RuleId: TypeAlias = Literal[
    "bind_project@v1",
    "map_correspond@v1",
    "cell_derive@v1",
    "row_state@v1",
    "original_reduce@v1",
    "occurrence_combine@v1",
    "parts_transport@v1",
    "domain.cohort@v1",
    "association_score@v1",
    "reference@v1",
]


@dataclass(frozen=True, slots=True)
class BindProject:
    ref: Ref[DimensionKind] | Ref[TimeDimensionKind] | Ref[MetricKind] | Ref[MeasureKind]
    field_owner: Ref[EntityKind]
    field_contract: TargetDimensionContract | None
    metric_contract: TargetMetricContract | None
    path: tuple[Ref[RelationshipKind], ...]
    path_contracts: tuple[TargetRelationshipContract, ...]
    quantity: ObservedQuantity | None = None
    resolved_versions: tuple[str, ...] = ()
    expression_bodies: tuple[tuple[str, str, str], ...] = ()
    measure_unit: str | None = None
    attribute_time: str = "untimed"


@dataclass(frozen=True, slots=True)
class DirectMetricDefinition:
    """Persistable computation facts without live authoring constructor tokens."""

    metric_ref: Ref[MetricKind] | RuntimeMetricExpr
    graph: MetricExpressionGraphV1
    component_node_id: str
    bound_graph_fingerprint: str
    dependency_fingerprint: str
    contribution: Ref[EntityKind]
    event_ref: Ref[TimeDimensionKind]
    unit: str | None
    empty_rule: Literal["null", "zero"]
    event_path: tuple[Ref[RelationshipKind], ...]


@dataclass(frozen=True, slots=True)
class EntityObservationTarget:
    domain: DomainSignature


@dataclass(frozen=True, slots=True)
class GroupObservationTarget:
    domain: DomainSignature
    field: TargetDimensionContract


ObservationTarget: TypeAlias = EntityObservationTarget | GroupObservationTarget


@dataclass(frozen=True, slots=True)
class OccurrenceFilter:
    """One occurrence's own contribution branch restriction."""

    dimension: TargetDimensionContract
    operator: SliceOperatorV1
    value: SliceValue


@dataclass(frozen=True, slots=True)
class ObserveMetric:
    """A frozen direct aggregate, contribution route and half-open UTC window."""

    metric: DirectMetricDefinition
    target: ObservationTarget
    quantity: ObservedQuantity
    contribution: Ref[EntityKind]
    path: tuple[TargetRelationshipContract, ...]
    event: TargetDimensionContract
    start: str | None
    end: str | None
    amount_column: str
    amount_type: str
    coordinates: tuple[TargetDimensionContract, ...] = ()
    filters: tuple[OccurrenceFilter, ...] = ()

    method: Literal[
        "sum",
        "mean",
        "min",
        "max",
        "count_distinct",
        "approx_count_distinct",
        "median",
        "approx_median",
        "percentile",
        "approx_percentile",
    ] = "sum"
    quantile: float | None = None
    distinct_columns: tuple[str, ...] = ()
    fold: Literal["first", "last", "mean", "min", "max"] | None = None
    grid_window: bool = False
    cumulative: CumulativeBinding | None = None
    report_timezone: str = "UTC"
    window_timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class ObserveWeightedMean:
    """A governed paired value/weight observation with exact int64 state."""

    metric: DirectMetricDefinition
    target: ObservationTarget
    quantity: ObservedQuantity
    contribution: Ref[EntityKind]
    path: tuple[TargetRelationshipContract, ...]
    event: TargetDimensionContract
    start: str | None
    end: str | None
    amount_column: str
    amount_type: str
    weight_column: str
    coordinates: tuple[TargetDimensionContract, ...] = ()
    filters: tuple[OccurrenceFilter, ...] = ()
    grid_window: bool = False
    cumulative: CumulativeBinding | None = None
    report_timezone: str = "UTC"
    window_timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class ObserveCount:
    """A frozen Entity count over the same governed contribution window."""

    metric: DirectMetricDefinition
    target: ObservationTarget
    quantity: ObservedQuantity
    contribution: Ref[EntityKind]
    path: tuple[TargetRelationshipContract, ...]
    event: TargetDimensionContract
    start: str | None
    end: str | None
    coordinates: tuple[TargetDimensionContract, ...] = ()
    filters: tuple[OccurrenceFilter, ...] = ()
    grid_window: bool = False
    cumulative: CumulativeBinding | None = None
    report_timezone: str = "UTC"
    window_timezone: str = "UTC"


MapMode: TypeAlias = Literal[
    "exact_keys", "one_to_one", "union_keys", "group", "group_keys", "subjects"
]


@dataclass(frozen=True, slots=True)
class MapCorrespond:
    mode: MapMode
    output_domain: DomainSignature
    check_id: CheckId | None = None


@dataclass(frozen=True, slots=True)
class CompleteGroups:
    """Complete an explicitly bound target using qualified empty reduction states."""

    output_domain: DomainSignature


@dataclass(frozen=True, slots=True)
class TimeProduct:
    output_domain: DomainSignature
    kind: Literal["time_product"]


@dataclass(frozen=True, slots=True)
class AttachCategory:
    """Transport a complete keyed relation through one explicit classification."""

    coordinate: Coordinate
    output_domain: DomainSignature
    subject_mapping: bool


@dataclass(frozen=True, slots=True)
class CellDerive:
    method: Literal["difference", "relative_change", "ratio"]
    definition_id: str
    value_policy: str
    unit: str | None
    time_scope: str
    pairing_check_id: CheckId | None = None
    numeric_check_id: CheckId | None = None
    design: Literal["time", "cohort", "period", "ratio"] = "time"
    pairing: Literal["exact", "keep", "metric_empty"] = "exact"
    empty_rules: tuple[Literal["null", "zero", "zero_denominator"], ...] = ()
    time_index: int | None = None
    bucket_mapping: tuple[tuple[str, str], ...] = ()
    relationship: TargetRelationshipContract | None = None


RowMethod: TypeAlias = Literal[
    "sum", "min", "max", "mean", "count", "count_defined", "weighted_mean"
]


@dataclass(frozen=True, slots=True)
class RowState:
    method: RowMethod
    output_domain: DomainSignature
    definition_id: str
    value_policy: str
    weighting: str = "equal_weight"
    numeric_check_id: CheckId | None = None
    merge: bool = False
    retain_error: bool = False


@dataclass(frozen=True, slots=True)
class OriginalRatio:
    """Compose two independently observed additive component states in endpoint order."""

    quantity: ObservedQuantity
    numerator: Ref[MetricKind] | RuntimeMetricExpr
    denominator: Ref[MetricKind] | RuntimeMetricExpr


@dataclass(frozen=True, slots=True)
class OccurrenceCombine:
    """Combine independently reduced occurrences into one signed linear result."""

    quantity: ObservedQuantity
    terms: tuple[tuple[ObservedQuantity, int], ...]
    output_domain: DomainSignature
    pairing_check_id: CheckId | None = None
    numeric_check_id: CheckId | None = None


@dataclass(frozen=True, slots=True)
class OriginalReduce:
    output_domain: DomainSignature
    partition_check_id: CheckId | None = None
    coverage_check_id: CheckId | None = None
    method: Literal[
        "sum", "sum_zero", "mean", "min", "max", "count", "ratio", "weighted_mean", "linear", "fold"
    ] = "sum"
    coordinates: tuple[Coordinate, ...] = ()
    time_mapping: tuple[tuple[str, str], ...] = ()


TransportMode: TypeAlias = Literal[
    "where", "projection", "compare", "view", "materialize", "cohort"
]


@dataclass(frozen=True, slots=True)
class PartsTransport:
    mode: TransportMode
    output_domain: DomainSignature
    retained_roles: tuple[PartRole, ...]
    keep_quantity: bool
    predicates: tuple[ValuePredicate, ...] = ()
    field_kind: Literal["measure", "dimension", "time_dimension"] | None = None
    external_predicate: bool = False
    inclusion_inputs: tuple[int, ...] = ()
    classification: Coordinate | None = None
    cohort_rule: Literal["any", "at_least", "all"] | None = None
    cohort_count: int = 1
    cohort_empty: Literal["true", "false", "undefined"] = "false"
    opportunity_domain: DomainSignature | None = None


@dataclass(frozen=True, slots=True)
class AssociationScore:
    output_domain: DomainSignature
    definition_id: str
    pairing_check_id: CheckId
    numeric_check_id: CheckId


@dataclass(frozen=True, slots=True)
class ReferenceDerive:
    kind: Literal["share", "penetration", "standardize"]
    output_domain: DomainSignature
    reference_id: str
    unit: str | None
    time_scope: str
    strata: tuple[Coordinate, ...] = ()
    statistical_unit: Ref[EntityKind] | None = None
    share_state: OriginalStatePart | None = None
    strata_dependencies: tuple[str, ...] = ()


RuleParameters: TypeAlias = (
    BindProject
    | ObserveMetric
    | ObserveCount
    | ObserveWeightedMean
    | MapCorrespond
    | AttachCategory
    | TimeProduct
    | CompleteGroups
    | CellDerive
    | RowState
    | OriginalReduce
    | OriginalRatio
    | OccurrenceCombine
    | PartsTransport
    | AssociationScore
    | ReferenceDerive
)


@dataclass(frozen=True, slots=True)
class PartTransform:
    retained: tuple[PartRole, ...]
    created: tuple[PartRole, ...]
    removed: tuple[PartRole, ...]


@dataclass(frozen=True, slots=True)
class RuleDerivation:
    """A conditional output; neither Post nor a check ID proves execution support."""

    rule: RuleId
    output: Signature
    pre: tuple[Fact, ...]
    required_parts: tuple[PartRole, ...]
    part_transform: PartTransform
    post: tuple[Fact, ...]
    transport: tuple[Evidence, ...]
    eval_id: str
    obligations: tuple[Obligation, ...]


def _binding(inputs: tuple[Signature, ...], location: str) -> Binding:
    if not inputs:
        reject("at least one typed input", "none", "Bind a domain or relation.", location)
    binding = inputs[0].domain.binding
    for item in inputs[1:]:
        other = item.domain.binding
        if other.session_id != binding.session_id or other.owner_id != binding.owner_id:
            reject(
                "one Session and owner", repr(other), "Use inputs from one bound Session.", location
            )
    return binding


def _output_domain(binding: Binding, domain: DomainSignature, location: str) -> None:
    if (
        domain.binding.session_id != binding.session_id
        or domain.binding.owner_id != binding.owner_id
    ):
        reject(
            "an output bound to this Session and owner",
            repr(domain.binding),
            "Rebind the target domain.",
            location,
        )


def _fact(
    kind: FactKind, binding: Binding, subject: str, inputs: tuple[Signature, ...] = ()
) -> Fact:
    return Fact(
        kind,
        binding,
        subject,
        "v1",
        tuple(FactInput(item.domain, item.quantity) for item in inputs),
    )


def _premise(
    inputs: tuple[Signature, ...],
    fact: Fact,
    *,
    check_id: CheckId | None,
    before: Literal["consume", "publish"],
) -> tuple[Obligation, ...]:
    if any(fact in available_facts(item) for item in inputs):
        return ()
    if check_id is None:
        reject(
            f"evidence for {fact.kind} on {fact.subject_id}",
            "no matching evidence or typed check obligation",
            "Bind an applicable declaration or a typed check; execution admission must verify its implementation.",
            "core.premise",
        )
    return (Obligation(fact, check_id, before),)


def _result(
    rule: RuleId,
    inputs: tuple[Signature, ...],
    domain: DomainSignature,
    quantity: Quantity | None,
    parts: tuple[Part, ...],
    *,
    pre: tuple[Fact, ...],
    required: tuple[PartRole, ...],
    created: tuple[PartRole, ...],
    post: tuple[Fact, ...],
    obligations: tuple[Obligation, ...],
    eval_id: str,
    established: tuple[Evidence, ...] = (),
) -> RuleDerivation:
    input_parts = tuple(part_role(part) for item in inputs for part in item.parts)
    output_roles = tuple(part_role(part) for part in parts)
    retained = tuple(role for role in output_roles if role in input_parts and role not in created)
    removed = tuple(role for role in input_parts if role not in output_roles)
    # A premise bound to a different scope cannot be silently transported.
    transport = tuple(
        evidence
        for item in inputs
        for evidence in item.evidence
        if evidence.fact.binding == domain.binding
    )
    inherited = tuple(obligation for item in inputs for obligation in item.obligations)
    pending = tuple(dict.fromkeys((*inherited, *obligations)))
    output = Signature(domain, quantity, parts, (*transport, *established), pending)
    return RuleDerivation(
        rule,
        output,
        pre,
        required,
        PartTransform(retained, created, removed),
        post,
        transport,
        eval_id,
        pending,
    )


def entity_members(
    entity: TargetEntityContract,
    ref: Ref[EntityKind],
    binding: Binding,
    *,
    version_selection: TargetSnapshotSelection
    | TargetValiditySelection
    | GridVersionSelection
    | None = None,
) -> Signature:
    """Use the declared complete business key, without scanning or deduplicating rows."""
    if isinstance(version_selection, GridVersionSelection):
        signatures = tuple(
            entity_members(entity, ref, binding, version_selection=anchor)
            for _, anchor in version_selection.selections
        )
        base = signatures[0]
        return replace(
            base,
            domain=replace(base.domain, version_selection=version_selection),
            parts=tuple(
                replace(p, injective=False) if isinstance(p, SubjectPart) else p for p in base.parts
            ),
        )
    if type(ref) is not Ref or ref.kind is not SemanticKind.ENTITY or entity.ref.path != ref.path:
        reject(
            "the exact declared Entity Ref", repr(ref), "Bind the matching Entity.", "core.members"
        )
    if not entity.primary_key or len(set(entity.primary_key)) != len(entity.primary_key):
        reject(
            "a complete declared primary key",
            repr(entity.primary_key),
            "Correct the Entity declaration.",
            "core.members",
        )
    if tuple(name for name, _ in entity.identity_signature) != entity.primary_key:
        reject(
            "the complete normalized identity signature",
            repr(entity.identity_signature),
            "Reload the corrected Entity definition.",
            "core.members",
        )
    if (
        entity.version is not None
        and entity.version_row_key[: len(entity.primary_key)] != entity.primary_key
    ):
        reject(
            "version rows keyed by the complete Entity identity",
            repr(entity.version_row_key),
            "Reload the corrected Entity version definition.",
            "core.members",
        )
    if entity.version is not None and not version_selection:
        reject(
            "explicit version selection",
            "none",
            "Select the exact snapshot or validity point.",
            "core.members",
        )
    if entity.version is None and version_selection is not None:
        reject(
            "no version selection for an unversioned Entity",
            repr(version_selection),
            "Remove the version selection.",
            "core.members",
        )
    if isinstance(entity.version, TargetSnapshotVersion) and (
        not isinstance(version_selection, TargetSnapshotSelection)
        or version_selection.coordinate_ref != entity.version.coordinate_ref
    ):
        reject(
            "the matching snapshot selection",
            repr(version_selection),
            "Select this Entity's snapshot coordinate.",
            "core.members",
        )
    if isinstance(entity.version, TargetValidityVersion) and (
        not isinstance(version_selection, TargetValiditySelection)
        or version_selection.valid_from_ref != entity.version.valid_from_ref
        or version_selection.valid_to_ref != entity.version.valid_to_ref
    ):
        reject(
            "the matching validity selection",
            repr(version_selection),
            "Select this Entity's validity axes.",
            "core.members",
        )
    columns = {name for name, _ in entity.columns}
    if not set(entity.primary_key).issubset(columns):
        reject(
            "key fields in the declared Entity schema",
            repr(entity.primary_key),
            "Correct the Entity schema.",
            "core.members",
        )
    key = tuple(Coordinate(ref, name, "identity") for name in entity.primary_key)
    domain = DomainSignature(binding, "entity", key, key, f"entity:{ref.path}", version_selection)
    declared_key = _fact("declared_key", binding, domain.definition_id)
    source_key = _fact("unique_key", binding, domain.definition_id)
    subject = SubjectPart(binding, ref, key, key, True, True, "v1")
    return Signature(
        domain,
        parts=(subject,),
        evidence=(Evidence(declared_key, "declaration", entity.dependency_fingerprint),),
        obligations=(Obligation(source_key, "source.unique_key@v1", "consume"),),
    )


def _bind_project(inputs: tuple[Signature, ...], params: BindProject) -> RuleDerivation:
    if len(inputs) != 1:
        reject("one domain input", str(len(inputs)), "Bind one domain.", "core.bind_project")
    source = inputs[0]
    binding = _binding(inputs, "core.bind_project")
    if type(params.ref) is not Ref or params.ref.kind not in (
        SemanticKind.DIMENSION,
        SemanticKind.MEASURE,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.METRIC,
    ):
        reject(
            "a field or Metric Ref",
            repr(params.ref),
            "Use an exact field or Metric Ref.",
            "core.bind_project.ref",
        )
    if type(params.field_owner) is not Ref or params.field_owner.kind is not SemanticKind.ENTITY:
        reject(
            "an Entity field owner",
            repr(params.field_owner),
            "Bind the owning Entity.",
            "core.bind_project.owner",
        )
    if len(params.path) != len(params.path_contracts) or not source.domain.instance_key:
        reject(
            "a complete directed Relationship path",
            repr(params.path),
            "Bind each Relationship Ref with its normalized contract.",
            "core.bind_project.path",
        )
    current_entity = source.domain.instance_key[0].entity_ref.path
    for ref, contract in zip(params.path, params.path_contracts, strict=True):
        if (
            type(ref) is not Ref
            or ref.kind is not SemanticKind.RELATIONSHIP
            or contract.ref.path != ref.path
            or contract.from_entity_ref.path != current_entity
            or contract.cardinality not in ("one_to_one", "many_to_one")
            or (
                contract.to_version_resolution_required
                and contract.to_entity_ref.path not in params.resolved_versions
            )
        ):
            reject(
                "a directed single-valued, version-resolved Relationship",
                repr(ref),
                "Bind the exact to-one path and target version.",
                "core.bind_project.path",
            )
        current_entity = contract.to_entity_ref.path
    if current_entity != params.field_owner.path:
        reject(
            "a path ending at the field owner",
            current_entity,
            "Bind the exact owning Entity route.",
            "core.bind_project.owner",
        )
    if params.ref.kind is SemanticKind.METRIC:
        metric = params.metric_contract
        if (
            params.field_contract is not None
            or metric is None
            or not isinstance(metric.identity, CatalogMetricIdentity)
            or metric.identity.metric_ref.path != params.ref.path
            or params.field_owner.path not in {root.path for root in metric.computation_roots}
            or params.quantity is None
            or params.quantity.metric_ref != params.ref
            or params.quantity.graph_fingerprint != metric.bound_graph_fingerprint
            or params.quantity.unit != metric.unit
        ):
            reject(
                "a matching normalized Metric quantity definition",
                repr(params.quantity),
                "Bind the normalized Metric definition.",
                "core.bind_project.quantity",
            )
    else:
        field = params.field_contract
        if (
            params.metric_contract is not None
            or params.quantity is not None
            or field is None
            or field.ref.path != params.ref.path
            or field.ref.kind != params.ref.kind
            or field.entity_ref.path != params.field_owner.path
            or (field.is_time_dimension != (params.ref.kind is SemanticKind.TIME_DIMENSION))
        ):
            reject(
                "a field Ref and its normalized owning definition",
                repr(params.ref),
                "Bind the exact field definition and value role.",
                "core.bind_project.field",
            )
    if params.expression_bodies and (
        params.ref.kind is not SemanticKind.MEASURE
        or params.expression_bodies[0][:2] != (params.ref.kind.value, params.ref.path)
        or any(
            not kind or not path or not body_hash
            for kind, path, body_hash in params.expression_bodies
        )
        or len({(kind, path) for kind, path, _ in params.expression_bodies})
        != len(params.expression_bodies)
    ):
        reject(
            "one exact Measure expression and distinct bound body fingerprints",
            repr(params.expression_bodies),
            "Freeze the resolved Measure body and each bound field definition.",
            "core.bind_project.expression",
        )
    pre = (
        _fact("field_ownership", binding, params.ref.path),
        _fact("single_value", binding, params.ref.path),
    )
    obligations = (
        _premise(inputs, pre[1], check_id="source.single_value@v1", before="consume")
        if params.path
        else ()
    )
    quantity = params.quantity
    projected_quantity: ObservedQuantity | DerivedQuantity | None = quantity
    if params.ref.kind is SemanticKind.MEASURE:
        projected_quantity = DerivedQuantity(
            f"field:{params.ref.path}:{source.domain.definition_id}:{params.attribute_time}",
            "bind_project@v1",
            (params.ref.path,),
            params.measure_unit,
            params.attribute_time,
            "strict",
        )
    elif params.measure_unit is not None or params.attribute_time != "untimed":
        reject(
            "Measure-only numeric quantity metadata",
            repr(params.ref),
            "Bind numeric metadata only for a Measure read.",
            "core.bind_project.quantity",
        )
    return _result(
        "bind_project@v1",
        inputs,
        source.domain,
        projected_quantity,
        source.parts,
        pre=pre,
        required=(),
        created=(),
        post=(_fact("output_key", binding, source.domain.definition_id),),
        obligations=obligations,
        eval_id="bind_project.projection@v1",
        established=(Evidence(pre[0], "builder", params.ref.path),),
    )


def _declared_slice(aggregate: object) -> tuple[tuple[str, str, object], ...]:
    """Read one component's canonical slice predicates as comparable facts."""
    from marivo.semantic.metric_graph import component_predicate

    facts: list[tuple[str, str, object]] = []
    for condition in getattr(aggregate, "filter", ()):
        operator, value = component_predicate(condition.value)
        facts.append((condition.dimension_ref.path, operator, value))
    return tuple(facts)


def _observe_metric(
    inputs: tuple[Signature, ...], params: ObserveMetric | ObserveCount | ObserveWeightedMean
) -> RuleDerivation:
    from datetime import datetime, timedelta

    from marivo.semantic.metric_graph import (
        AggregateNodeV1,
        WeightedMeanAggregateNodeV1,
        component_node,
    )

    if len(inputs) != 1 or inputs[0].domain.kind != "entity":
        reject("one Entity member domain", repr(inputs), "Bind Entity members.", "core.observe")
    source = inputs[0]
    binding = _binding(inputs, "core.observe")
    subject = require_part(source, "subject")
    metric, quantity = params.metric, params.quantity
    aggregate_method = (
        "weighted_mean"
        if isinstance(params, ObserveWeightedMean)
        else params.method
        if isinstance(params, ObserveMetric)
        else "count"
    )
    state_method = (
        "fold"
        if isinstance(params, ObserveMetric) and params.fold is not None
        else "sum_zero"
        if aggregate_method == "sum" and metric.empty_rule == "zero"
        else aggregate_method
    )
    if (
        not isinstance(subject, SubjectPart)
        or not subject.total
        or (not subject.injective and source.domain.time_grid is None)
        or (params.grid_window and source.domain.time_grid is None)
        or metric.metric_ref != quantity.metric_ref
        or quantity.graph_fingerprint != metric.bound_graph_fingerprint
        or quantity.unit != metric.unit
        or quantity.method_version != f"{state_method}@v1"
        or metric.contribution != params.contribution
        or metric.event_ref.path != params.event.ref.path
        or not params.event.is_time_dimension
        or (isinstance(params, ObserveMetric) and not params.amount_column)
    ):
        reject(
            "an exact single-root sum with UTC event time and original Subject map",
            repr(quantity),
            "Bind the qualified Metric, route and time axis.",
            "core.observe",
        )
    if params.cumulative is not None:
        cumulative = params.cumulative
        grid = source.domain.time_grid
        if (
            params.grid_window
            or params.start is not None
            or params.end is not None
            or not cumulative.windows
            or (
                cumulative.grid_identity is not None
                and (
                    grid is None
                    or cumulative.grid_identity != grid.identity
                    or tuple(w.key for w in cumulative.windows)
                    != tuple(c.identity for c in grid.cells)
                )
            )
        ):
            reject(
                "an exact endpoint binding on this receiver",
                repr(cumulative),
                "Bind at independently of during using this grid.",
                "core.observe.cumulative",
            )
    aggregate = component_node(metric.graph, metric.component_node_id)
    if (
        isinstance(params, ObserveMetric)
        and params.fold is not None
        and (
            not isinstance(aggregate, AggregateNodeV1)
            or aggregate.fold != params.fold
            or params.coordinates
            or params.cumulative is not None
            or aggregate_method != "sum"
        )
    ):
        reject(
            "one declared sum then scalar time fold",
            repr(params.fold),
            "Use the exact status fold without cumulative composition.",
            "core.observe.fold",
        )
    occurrence_slice = tuple(
        (item.dimension.ref.path, item.operator, item.value) for item in params.filters
    )
    if (
        not (
            isinstance(aggregate, WeightedMeanAggregateNodeV1)
            if isinstance(params, ObserveWeightedMean)
            else isinstance(aggregate, AggregateNodeV1)
            and aggregate.agg
            == (
                (aggregate_method, params.quantile)
                if isinstance(params, ObserveMetric) and params.quantile is not None
                else aggregate_method
            )
        )
        or _declared_slice(aggregate) != occurrence_slice
        or (
            isinstance(params, ObserveCount)
            and (
                not isinstance(aggregate, AggregateNodeV1)
                or aggregate.target_ref.kind != "entity"
                or aggregate.target_ref.path != params.contribution.path
            )
        )
    ):
        reject(
            "one sum or Entity-count component with its own declared branch filter",
            repr(aggregate),
            "Use a qualified sum Metric.",
            "core.observe",
        )
    current = params.contribution.path
    for relationship in params.path:
        if (
            relationship.from_entity_ref.path != current
            or relationship.cardinality not in ("one_to_one", "many_to_one")
            or relationship.from_version_resolution_required
            or relationship.to_version_resolution_required
            or not relationship.keys
        ):
            reject(
                "a directed unversioned to-one contribution path",
                repr(relationship),
                "Bind the exact contribution-to-member route.",
                "core.observe",
            )
        current = relationship.to_entity_ref.path
    event_owner = params.contribution.path
    if len(metric.event_path) > len(params.path):
        reject(
            "event time on a prefix of the contribution route",
            repr(metric.event_path),
            "Bind the declared time route.",
            "core.observe.time",
        )
    for event_ref, relationship in zip(
        metric.event_path, params.path[: len(metric.event_path)], strict=True
    ):
        if event_ref.path != relationship.ref.path:
            reject(
                "the exact declared event path",
                repr(event_ref),
                "Bind the declared time route.",
                "core.observe.time",
            )
        event_owner = relationship.to_entity_ref.path
    if params.event.entity_ref.path != event_owner:
        reject(
            "event time owned by the selected path endpoint",
            params.event.entity_ref.path,
            "Bind the exact event axis and path.",
            "core.observe.time",
        )
    if current != subject.entity_ref.path:
        reject(
            "a contribution route ending at the member Entity",
            current,
            "Bind the declared member relationship.",
            "core.observe",
        )
    if params.start is not None or params.end is not None:
        if params.start is None or params.end is None:
            reject(
                "both window bounds or neither",
                repr((params.start, params.end)),
                "Omit the window entirely or bind both bounds.",
                "core.observe",
            )
        try:
            start, end = datetime.fromisoformat(params.start), datetime.fromisoformat(params.end)
            valid = (
                start.utcoffset() == timedelta(0)
                and end.utcoffset() == timedelta(0)
                and start < end
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            reject(
                "an increasing half-open UTC instant window",
                repr((params.start, params.end)),
                "Resolve the Session report timezone before binding the window.",
                "core.observe",
            )
    output_domain = params.target.domain
    _output_domain(binding, output_domain, "core.observe.target")
    if isinstance(params.target, EntityObservationTarget):
        if output_domain != source.domain:
            reject(
                "the exact input Entity domain",
                repr(output_domain),
                "Preserve the member domain.",
                "core.observe.target",
            )
        retained: tuple[Part, ...] = (subject,)
    else:
        field = params.target.field
        expected_key = (Coordinate(subject.entity_ref, field.ref.path, "group"),)
        if (
            output_domain.kind != "group"
            or output_domain.binding != source.domain.binding
            or output_domain.instance_key != expected_key
            or output_domain.target_key != expected_key
            or field.entity_ref.path != subject.entity_ref.path
            or field.logical_type != "string"
            or _fact("field_ownership", binding, field.ref.path) not in available_facts(source)
        ):
            reject(
                "the bound string member Group projection",
                repr(output_domain),
                "Group by the exact member Dimension.",
                "core.observe.target",
            )
        retained = ()
    direct_only = aggregate_method in DIRECT_ONLY_AGGREGATES
    original = OriginalStatePart(
        binding,
        quantity.definition_id,
        f"{state_method}@v1",
        quantity.contribution_id,
        ("samples", "fold_kind")
        if state_method == "fold"
        else ("weighted_numerator", "weight_sum", "non_null_pair_count", "row_count")
        if aggregate_method == "weighted_mean"
        else (aggregate_method, "non_null_count")
        if aggregate_method in ("min", "max")
        else ("sum", "non_null_count", "row_count")
        if aggregate_method == "mean"
        else ("sum", "non_null_count")
        if aggregate_method == "sum"
        else ("count",),
        "v1",
        fold_kind=params.fold if isinstance(params, ObserveMetric) else None,
        temporal_policy="overlapping"
        if params.cumulative is not None and params.cumulative.overlapping
        else "partition"
        if params.grid_window
        or (params.cumulative is not None and params.cumulative.grid_identity is not None)
        else "repeated"
        if source.domain.time_grid is not None
        else "none",
    )
    if (
        isinstance(params, (ObserveMetric, ObserveWeightedMean))
        and params.amount_type == "float64"
        and state_method != "fold"
    ):
        extra = (
            ("absolute_sum",)
            if aggregate_method in ("sum", "mean")
            else ("absolute_weight_sum", "absolute_weighted_numerator")
            if aggregate_method == "weighted_mean"
            else ()
        )
        original = replace(original, components=(*original.components, *extra))
    coordinate_parts: tuple[Part, ...] = ()
    coordinate_type = (
        params.amount_type if isinstance(params, (ObserveMetric, ObserveWeightedMean)) else "int64"
    )
    if coordinate_type.startswith("decimal("):
        coordinate_type = "decimal(38," + coordinate_type.split(",")[1]
    for index, coordinate in enumerate(params.coordinates):
        if (
            coordinate.logical_type != "string"
            or coordinate.parse is not None
            or coordinate.is_time_dimension
            or coordinate.entity_ref.path
            not in {
                params.contribution.path,
                *(relationship.to_entity_ref.path for relationship in params.path),
            }
        ):
            reject(
                "a direct string coordinate on the contribution route",
                repr(coordinate),
                "Use a qualified contribution Dimension.",
                "core.observe.coordinates",
            )
        if index == 0:
            coordinate_parts = (
                CoordinateStatePart(
                    binding,
                    quantity.definition_id,
                    semantic_ref.dimension(coordinate.ref.path),
                    semantic_ref.entity(coordinate.entity_ref.path),
                    original.components,
                    coordinate_type,
                    "v1",
                    tuple(
                        Coordinate(semantic_ref.entity(c.entity_ref.path), c.ref.path, "group")
                        for c in params.coordinates[1:]
                    ),
                ),
            )
    coverage = CoveragePart(binding, quantity.definition_id, binding.scope_id, "v1")
    partition = _fact("contribution_partition", binding, quantity.contribution_id)
    complete = _fact("complete_coverage", binding, quantity.definition_id)
    return _result(
        "bind_project@v1",
        inputs,
        output_domain,
        quantity,
        (*retained, *((original,) if not direct_only else ()), coverage, *coordinate_parts),
        pre=(partition, complete),
        required=("subject",),
        created=(
            *(("original_state",) if not direct_only else ()),
            "coverage",
            *(part_role(p) for p in coordinate_parts),
        ),
        post=(_fact("state_binding", binding, quantity.definition_id),),
        obligations=(
            Obligation(partition, "source.contribution_partition@v1", "publish"),
            Obligation(complete, "source.complete_coverage@v1", "publish"),
            *(
                (
                    Obligation(complete, "source.calendar_members@v1", "publish"),
                    Obligation(complete, "source.calendar_contributions@v1", "publish"),
                )
                if (
                    (
                        source.domain.time_grid is not None
                        and source.domain.time_grid.snapshot_digest is not None
                    )
                    or (
                        params.cumulative is not None
                        and params.cumulative.snapshot_digest is not None
                    )
                )
                else ()
            ),
        ),
        eval_id=f"metric.observe.{aggregate_method}@v1",
    )


def _map_correspond(inputs: tuple[Signature, ...], params: MapCorrespond) -> RuleDerivation:
    if not inputs or len(inputs) > 2:
        reject("one or two domain inputs", str(len(inputs)), "Bind the mapped domains.", "core.map")
    binding = _binding(inputs, "core.map")
    _output_domain(binding, params.output_domain, "core.map")
    source = inputs[0]
    pre: tuple[Fact, ...]
    post: tuple[Fact, ...]
    required: tuple[PartRole, ...] = ()
    obligations: tuple[Obligation, ...] = ()
    parts: tuple[Part, ...] = ()
    quantity = source.quantity
    if params.mode == "subjects":
        if len(inputs) != 1:
            reject(
                "one subject-mapped input", str(len(inputs)), "Use one input.", "core.map.subjects"
            )
        subject = require_part(source, "subject")
        if not isinstance(subject, SubjectPart) or not subject.total:
            reject(
                "a total Subject map",
                repr(subject),
                "Retain a total Subject mapping.",
                "core.map.subjects",
            )
        if params.output_domain.instance_key != subject.subject_key:
            reject(
                "the exact Subject identity key",
                repr(params.output_domain.instance_key),
                "Use the complete Subject key.",
                "core.map.subjects",
            )
        parts = (
            replace(
                subject,
                binding=params.output_domain.binding,
                source_key=subject.subject_key,
                injective=True,
            ),
        )
        quantity = None
        pre = (_fact("mapping_total", binding, source.domain.definition_id),)
        post = (
            _fact(
                "subject_image", params.output_domain.binding, params.output_domain.definition_id
            ),
        )
        eval_id = "map_correspond.subject_set_image@v1"
        role: Literal["pair", "group", "subject", "union"] = "subject"
        multiplicity: Literal["preserve", "set_image", "group", "paired"] = "set_image"
    elif params.mode in ("exact_keys", "one_to_one", "union_keys"):
        if len(inputs) != 2 or source.domain.instance_key != inputs[1].domain.instance_key:
            reject(
                "two domains with the same complete typed key",
                repr(tuple(item.domain.kind for item in inputs)),
                "Bind matching identity types and keys.",
                "core.map.keys",
            )
        if params.output_domain.instance_key != source.domain.instance_key:
            reject(
                "the same complete output key",
                repr(params.output_domain.instance_key),
                "Preserve all key components.",
                "core.map.keys",
            )
        pre = (
            ()
            if params.mode == "union_keys"
            else (_fact("key_set_equal", binding, params.output_domain.definition_id, inputs),)
        )
        if params.mode == "one_to_one":
            pre = (
                *pre,
                _fact("single_value", binding, params.output_domain.definition_id, inputs),
                _fact("mapping_injective", binding, params.output_domain.definition_id, inputs),
            )
        obligations = tuple(
            obligation
            for fact in pre
            for obligation in _premise(inputs, fact, check_id=params.check_id, before="consume")
        )
        quantity = None
        post = (
            _fact("output_key", params.output_domain.binding, params.output_domain.definition_id),
        )
        eval_id = f"map_correspond.{params.mode}@v1"
        role = "union" if params.mode == "union_keys" else "pair"
        multiplicity = "preserve" if params.mode == "union_keys" else "paired"
    elif params.mode == "group_keys":
        if len(inputs) != 1 or not set(params.output_domain.instance_key) <= set(
            source.domain.instance_key
        ):
            reject(
                "retained complete group keys",
                repr(params.output_domain),
                "Select existing coordinates.",
                "core.map.group_keys",
            )
        pre, post, obligations = (), (), ()
        parts, quantity = (), None
        eval_id, role, multiplicity = "map_correspond.group_keys@v1", "group", "group"
    elif params.mode == "group":
        if len(inputs) != 1 or params.output_domain.kind != "group":
            reject(
                "one input and a Group target",
                repr(params.output_domain.kind),
                "Bind an explicit Group domain.",
                "core.map.group",
            )
        if not params.output_domain.instance_key or any(
            key.role != "group" for key in params.output_domain.instance_key
        ):
            reject(
                "complete Group coordinate tuples",
                repr(params.output_domain.instance_key),
                "Bind every group axis.",
                "core.map.group",
            )
        pre = (
            _fact("mapping_total", binding, params.output_domain.definition_id, inputs),
            _fact("single_value", binding, params.output_domain.definition_id, inputs),
        )
        obligations = tuple(
            obligation
            for fact in pre
            for obligation in _premise(inputs, fact, check_id=params.check_id, before="consume")
        )
        post = (
            _fact("output_key", params.output_domain.binding, params.output_domain.definition_id),
        )
        eval_id = "map_correspond.group@v1"
        role = "group"
        multiplicity = "group"
    else:
        reject(
            "a closed correspondence mode",
            str(params.mode),
            "Use an R3.1 mapping variant.",
            "core.map",
        )
    output_domain = replace(
        params.output_domain,
        correspondence=Correspondence(
            source.domain.definition_id,
            params.output_domain.definition_id,
            role,
            multiplicity,
            tuple(fact.kind for fact in pre),
        ),
    )
    return _result(
        "map_correspond@v1",
        inputs,
        output_domain,
        quantity,
        parts,
        pre=pre,
        required=required,
        created=("subject",) if params.mode == "subjects" else (),
        post=post,
        obligations=obligations,
        eval_id=eval_id,
    )


def _cell_derive(inputs: tuple[Signature, ...], params: CellDerive) -> RuleDerivation:
    if len(inputs) != 2 or any(item.quantity is None for item in inputs):
        reject(
            "two single-quantity relations",
            str(len(inputs)),
            "Bind current and baseline quantities.",
            "core.cell",
        )
    binding = _binding(inputs, "core.cell")
    left, right = inputs
    assert left.quantity is not None and right.quantity is not None
    equivalent_keys = left.domain.instance_key == right.domain.instance_key
    if params.bucket_mapping:
        from dataclasses import replace

        grids = (left.domain.time_grid, right.domain.time_grid)
        if (
            grids[0] is None
            or grids[1] is None
            or len(grids[0].cells) != len(grids[1].cells)
            or params.bucket_mapping
            != tuple(
                (a.identity, b.identity)
                for a, b in zip(grids[0].cells, grids[1].cells, strict=True)
            )
        ):
            reject(
                "complete equal-length frozen bucket correspondence",
                "invalid period map",
                "Retain both original grids.",
                "core.cell.period",
            )
        equivalent_keys = tuple(
            replace(key, field="time") if key.role == "anchor" else key
            for key in left.domain.instance_key
        ) == tuple(
            replace(key, field="time") if key.role == "anchor" else key
            for key in right.domain.instance_key
        )
    if params.relationship is not None:
        relationship = params.relationship
        left_keys = tuple(key for key in left.domain.instance_key if key.role != "anchor")
        right_keys = tuple(key for key in right.domain.instance_key if key.role != "anchor")
        if (
            params.method != "ratio"
            or params.pairing != "exact"
            or relationship.cardinality != "one_to_one"
            or not left_keys
            or not right_keys
            or len(left_keys) != len(right_keys)
            or any(key.entity_ref.path != relationship.from_entity_ref.path for key in left_keys)
            or any(key.entity_ref.path != relationship.to_entity_ref.path for key in right_keys)
            or relationship.keys
            != tuple((a.field, b.field) for a, b in zip(left_keys, right_keys, strict=True))
        ):
            reject(
                "declared one-to-one correspondence on complete retained identity keys",
                repr(relationship.ref),
                "Bind the exact ordered endpoint identities and relationship.",
                "core.cell.relationship",
            )
        equivalent_keys = len(left.domain.instance_key) == len(right.domain.instance_key)
    if not equivalent_keys:
        reject(
            "matching typed coordinate keys",
            repr(right.domain.instance_key),
            "Map the two domains explicitly.",
            "core.cell.keys",
        )
    from marivo.semantic.unit_algebra import ratio_unit

    expected_unit = (
        ratio_unit(left.quantity.unit, right.quantity.unit)
        if params.method == "ratio"
        else left.quantity.unit
    )
    if (
        params.method != "ratio" and left.quantity.unit != right.quantity.unit
    ) or expected_unit != params.unit:
        reject(
            "matching bound units",
            repr((left.quantity.unit, right.quantity.unit)),
            "Use comparable quantities.",
            "core.cell.unit",
        )
    if params.method not in ("difference", "relative_change", "ratio"):
        reject(
            "difference or ratio",
            str(params.method),
            "Use a closed cell method.",
            "core.cell.method",
        )
    pair = _fact(
        "key_set_equal" if params.pairing == "exact" else "unique_key",
        binding,
        params.definition_id,
        inputs,
    )
    numeric = _fact("finite_numeric", binding, params.definition_id, inputs)
    obligations = (
        *_premise(inputs, pair, check_id=params.pairing_check_id, before="consume"),
        *_premise(inputs, numeric, check_id=params.numeric_check_id, before="consume"),
    )
    output = DerivedQuantity(
        params.definition_id,
        f"cell.{params.method}@v1",
        (left.quantity.definition_id, right.quantity.definition_id),
        params.unit if params.method in ("difference", "ratio") else "1",
        params.time_scope,
        params.value_policy,
    )
    subjects = tuple(
        p
        for p in left.parts
        if isinstance(p, SubjectPart)
        and p in right.parts
        and p.binding == left.domain.binding == right.domain.binding
    )
    parts: tuple[Part, ...] = (
        *subjects,
        EndpointPart(
            binding,
            "current",
            left.quantity.definition_id,
            "v2" if params.method == "difference" else "v1",
        ),
        EndpointPart(
            right.domain.binding,
            "baseline",
            right.quantity.definition_id,
            "v2" if params.method == "difference" else "v1",
        ),
        CorrespondencePart(
            binding,
            left.domain.instance_key,
            right.domain.instance_key,
            "v2" if params.method == "difference" else "v1",
            params.pairing,
            params.empty_rules,
            params.time_index,
            params.bucket_mapping,
        ),
    )
    return _result(
        "cell_derive@v1",
        inputs,
        left.domain,
        output,
        parts,
        pre=(pair, numeric),
        required=(),
        created=("current_endpoint", "baseline_endpoint", "correspondence"),
        post=(_fact("cell_policy", binding, output.definition_id),),
        obligations=obligations,
        eval_id=f"cell_derive.{params.method}@v1",
    )


def _association_score(inputs: tuple[Signature, ...], params: AssociationScore) -> RuleDerivation:
    if len(inputs) != 2 or any(not isinstance(item.quantity, ObservedQuantity) for item in inputs):
        reject(
            "two original observed quantities",
            str(len(inputs)),
            "Observe two Metrics over the same Entity members.",
            "core.association",
        )
    left, right = inputs
    assert isinstance(left.quantity, ObservedQuantity)
    assert isinstance(right.quantity, ObservedQuantity)
    binding = _binding(inputs, "core.association")
    _output_domain(binding, params.output_domain, "core.association")
    _reduction_domain(left.domain, params.output_domain)
    if (
        left.domain.kind != "entity"
        or right.domain.kind != "entity"
        or left.domain.instance_key != right.domain.instance_key
        or left.domain.binding != right.domain.binding
        or left.quantity.time_scope != right.quantity.time_scope
    ):
        reject(
            "two exact same-Entity no-lag observations",
            repr((left.domain, right.domain)),
            "Bind both Metrics to one complete member realization.",
            "core.association.domain",
        )
    if (
        params.pairing_check_id != "source.exact_pairing@v1"
        or params.numeric_check_id != "source.finite_numeric@v1"
    ):
        reject(
            "registered pairing and numeric check IDs",
            repr((params.pairing_check_id, params.numeric_check_id)),
            "Use the Association method's exact checks.",
            "core.association.check",
        )
    pair = _fact("key_set_equal", binding, params.definition_id, inputs)
    numeric = _fact("finite_numeric", binding, params.definition_id, inputs)
    quantity = DerivedQuantity(
        params.definition_id,
        "association.spearman@v1",
        (left.quantity.definition_id, right.quantity.definition_id),
        "1",
        left.quantity.time_scope,
        "strict",
    )
    state = PairCountsPart(binding, left.quantity.definition_id, right.quantity.definition_id, "v1")
    return _result(
        "association_score@v1",
        inputs,
        params.output_domain,
        quantity,
        (state,),
        pre=(pair, numeric),
        required=(),
        created=("pair_counts",),
        post=(_fact("state_binding", binding, quantity.definition_id),),
        obligations=(
            Obligation(pair, params.pairing_check_id, "consume"),
            Obligation(numeric, params.numeric_check_id, "consume"),
        ),
        eval_id="association.spearman@v1",
    )


def _reduction_domain(source: DomainSignature, target: DomainSignature) -> None:
    """Only the whole-input singleton has an implemented reduction mapping."""
    if (
        (
            target.kind != "singleton"
            and (target.kind != "group" or not set(target.instance_key) <= set(source.instance_key))
        )
        or target.binding != source.binding
        or target.version_selection != source.version_selection
        or target.correspondence is not None
    ):
        reject(
            "a singleton over the exact input binding and version",
            repr(target),
            "Use the whole-input singleton; grouped reduction requires a registered mapping.",
            "core.reduction.domain",
        )


def _row_state(inputs: tuple[Signature, ...], params: RowState) -> RuleDerivation:
    if len(inputs) != 1:
        reject(
            "one current-row scalar relation",
            str(len(inputs)),
            "Bind a single-quantity relation.",
            "core.row_state",
        )
    source = inputs[0]
    binding = _binding(inputs, "core.row_state")
    _output_domain(binding, params.output_domain, "core.row_state")
    _reduction_domain(source.domain, params.output_domain)
    if params.merge:
        from marivo.analysis.methods.registry import REGISTRY
        from marivo.analysis.methods.semantics import key_for_parameters

        semantics = REGISTRY.lookup(key_for_parameters(params)).semantics
        quantity = source.quantity
        state = require_part(source, "row_state")
        if (
            not isinstance(quantity, RowStatisticQuantity)
            or not isinstance(state, RowStatePart)
            or state.quantity_id != quantity.definition_id
            or state.method_version != f"row.{params.method}@v1"
            or quantity.method_version != state.method_version
            or state.version != "v1"
            or state.components
            != (*semantics.state_components, *(("error_bound",) if params.retain_error else ()))
            or state.input_domain_id != quantity.input_domain_id
            or params.definition_id != quantity.definition_id
        ):
            reject(
                "this statistic's complete bound row state",
                repr(state),
                "Retain the original statistic's state before rollup.",
                "core.row_state.merge",
            )
        return _result(
            "row_state@v1",
            inputs,
            params.output_domain,
            quantity,
            (replace(state, binding=params.output_domain.binding),),
            pre=(),
            obligations=(),
            required=("row_state",),
            created=(),
            post=(_fact("state_binding", binding, quantity.definition_id),),
            eval_id=f"row_state.merge.{params.method}@v1",
        )
    if params.method not in (
        "sum",
        "min",
        "max",
        "mean",
        "count",
        "count_defined",
        "weighted_mean",
    ):
        reject(
            "a closed current-row method",
            str(params.method),
            "Use a registered row method.",
            "core.row_state.method",
        )
    if params.method == "weighted_mean":
        weight = require_part(source, "statistical_weight")
        if not isinstance(weight, StatisticalWeightPart) or weight.role_id != params.weighting:
            reject(
                "an exact statistical weight role",
                params.weighting,
                "Bind the declared weight role.",
                "core.row_state.weight",
            )
    elif params.weighting != "equal_weight":
        reject(
            "equal current-row weighting",
            params.weighting,
            "Use equal_weight or weighted_mean.",
            "core.row_state.weight",
        )
    input_quantity = source.quantity
    input_id = (
        input_quantity.definition_id if input_quantity is not None else source.domain.definition_id
    )
    numeric = _fact(
        "cell_policy" if params.method == "count_defined" else "finite_numeric",
        binding,
        input_id,
    )
    obligations = (
        ()
        if params.method == "count"
        else _premise(inputs, numeric, check_id=params.numeric_check_id, before="consume")
    )
    from marivo.analysis.methods.registry import REGISTRY
    from marivo.analysis.methods.semantics import key_for_parameters

    method_semantics = REGISTRY.lookup(key_for_parameters(params)).semantics
    method_version = str(method_semantics.key)
    quantity = RowStatisticQuantity(
        params.definition_id,
        method_version,
        input_id,
        source.domain.definition_id,
        "count"
        if params.method in ("count", "count_defined")
        else input_quantity.unit
        if input_quantity is not None
        else None,
        input_quantity.time_scope if input_quantity is not None else binding.scope_id,
        params.value_policy,
        params.weighting,
    )
    state = RowStatePart(
        params.output_domain.binding,
        quantity.definition_id,
        method_version,
        source.domain.definition_id,
        (*method_semantics.state_components, *(("error_bound",) if params.retain_error else ())),
        "v1",
    )
    return _result(
        "row_state@v1",
        inputs,
        params.output_domain,
        quantity,
        (state,),
        pre=() if params.method == "count" else (numeric,),
        required=("statistical_weight",) if params.method == "weighted_mean" else (),
        created=("row_state",),
        post=(_fact("state_binding", params.output_domain.binding, quantity.definition_id),),
        obligations=obligations,
        eval_id=f"row_state.{params.method}@v1",
    )


def _component_empty_rules(inputs: tuple[Signature, ...]) -> tuple[Literal["null", "zero"], ...]:
    """Retain each admitted additive component's declared empty policy."""
    policies: list[Literal["null", "zero"]] = []
    for source in inputs:
        quantity = source.quantity
        if not isinstance(quantity, ObservedQuantity) or quantity.method_version not in (
            "sum@v1",
            "sum_zero@v1",
            "count@v1",
        ):
            reject(
                "an admitted additive component",
                repr(quantity),
                "Observe each sum or count component independently.",
                "core.component.empty",
            )
        policies.append("null" if quantity.method_version == "sum@v1" else "zero")
    return tuple(policies)


def _original_ratio(inputs: tuple[Signature, ...], params: OriginalRatio) -> RuleDerivation:
    binding = _binding(inputs, "core.original_ratio")
    if len(inputs) != 2:
        reject(
            "two ordered original components",
            repr(inputs),
            "Bind numerator and denominator.",
            "core.original_ratio",
        )
    left, right = inputs
    quantity = params.quantity
    if (
        left.domain != right.domain
        or quantity.method_version != "ratio@v1"
        or not isinstance(left.quantity, ObservedQuantity)
        or not isinstance(right.quantity, ObservedQuantity)
        or left.quantity.metric_ref != params.numerator
        or right.quantity.metric_ref != params.denominator
        or left.quantity.time_scope != right.quantity.time_scope
        or quantity.time_scope != left.quantity.time_scope
    ):
        reject(
            "same-domain original numerator and denominator in one window",
            repr(inputs),
            "Observe both original components over the same members.",
            "core.original_ratio",
        )
    pre: list[Fact] = []
    obligations: list[Obligation] = []
    # Each named component retains the state its own observed method declared:
    # a sum-zero component carries sum/non-null-count, a count component carries
    # count. Requiring one fixed pair would reject a sliced sum numerator.
    for source in (left, right):
        state = require_part(source, "original_state")
        coverage = require_part(source, "coverage")
        source_quantity = source.quantity
        assert isinstance(source_quantity, ObservedQuantity)
        method = source_quantity.method_version
        components = {
            "sum_zero@v1": ("sum", "non_null_count"),
            "sum@v1": ("sum", "non_null_count"),
            "count@v1": ("count",),
        }.get(method)
        if (
            components is None
            or not isinstance(state, OriginalStatePart)
            or state.method_version != method
            or state.components not in (components, (*components, "absolute_sum"))
            or state.quantity_id != source_quantity.definition_id
            or state.contribution_id != source_quantity.contribution_id
            or not isinstance(coverage, CoveragePart)
            or coverage.quantity_id != source_quantity.definition_id
            or coverage.binding != binding
        ):
            reject(
                "each component's own registered original state and coverage",
                repr(state),
                "Retain the registered original states.",
                "core.original_ratio.state",
            )
        premises: tuple[tuple[Fact, CheckId], ...] = (
            (
                _fact("contribution_partition", binding, source_quantity.contribution_id),
                "source.contribution_partition@v1",
            ),
            (
                _fact("complete_coverage", binding, source_quantity.definition_id),
                "source.complete_coverage@v1",
            ),
        )
        for fact, check in premises:
            pre.append(fact)
            obligations.extend(_premise(inputs, fact, check_id=check, before="consume"))
    pair = _fact("key_set_equal", binding, quantity.definition_id, inputs)
    pre.append(pair)
    obligations.extend(_premise(inputs, pair, check_id="source.exact_pairing@v1", before="consume"))
    partition = _fact("contribution_partition", binding, quantity.contribution_id)
    complete = _fact("complete_coverage", binding, quantity.definition_id)
    obligations.extend(
        (
            Obligation(partition, "source.contribution_partition@v1", "publish"),
            Obligation(complete, "source.complete_coverage@v1", "publish"),
        )
    )
    state = OriginalStatePart(
        binding,
        quantity.definition_id,
        "ratio@v1",
        quantity.contribution_id,
        (
            "numerator_sum",
            "numerator_non_null_count",
            "denominator_sum",
            "denominator_non_null_count",
        ),
        "v1",
        _component_empty_rules(inputs),
        temporal_policy=_component_temporal_policy(inputs),
    )
    for side, source in (("numerator", left), ("denominator", right)):
        operand = require_part(source, "original_state")
        assert isinstance(operand, OriginalStatePart)
        if "absolute_sum" in operand.components:
            state = replace(state, components=(*state.components, side + "_absolute_sum"))
    first_coordinate = next((p for p in left.parts if isinstance(p, CoordinateStatePart)), None)
    second_coordinate = next((p for p in right.parts if isinstance(p, CoordinateStatePart)), None)
    coordinate_parts: tuple[Part, ...] = ()
    domain = left.domain
    if first_coordinate is not None or second_coordinate is not None:
        if (
            first_coordinate is None
            or second_coordinate is None
            or first_coordinate.dimension != second_coordinate.dimension
            or first_coordinate.coordinates != second_coordinate.coordinates
            or first_coordinate.value_type != second_coordinate.value_type
        ):
            reject(
                "matching exact coordinate states for both original roots",
                repr((first_coordinate, second_coordinate)),
                "Retain the same qualified coordinate on both roots.",
                "core.original_ratio.coordinate",
            )
        coordinate_parts = (
            replace(
                first_coordinate, quantity_id=quantity.definition_id, components=state.components
            ),
        )
        domain = replace(
            domain,
            instance_key=(*domain.instance_key, *first_coordinate.coordinates),
            definition_id=quantity.definition_id,
        )
    parts: tuple[Part, ...] = (
        *tuple(
            replace(p, source_key=domain.instance_key)
            for p in left.parts
            if isinstance(p, SubjectPart)
        ),
        state,
        CoveragePart(binding, quantity.definition_id, binding.scope_id, "v1"),
        *coordinate_parts,
    )
    return _result(
        "original_reduce@v1",
        inputs,
        domain,
        quantity,
        parts,
        pre=tuple(pre),
        required=("original_state", "coverage"),
        created=("original_state", "coverage"),
        post=(partition, complete),
        obligations=tuple(obligations),
        eval_id="original_ratio.finish@v1",
    )


def _occurrence_combine(inputs: tuple[Signature, ...], params: OccurrenceCombine) -> RuleDerivation:
    """Combine independently reduced occurrences under ordered signed terms."""
    binding = _binding(inputs, "core.occurrence_combine")
    if len(inputs) != len(params.terms) or len(inputs) < 2:
        reject(
            "one input per ordered signed term",
            repr(len(inputs)),
            "Bind every named occurrence.",
            "core.occurrence_combine",
        )
    quantity = params.quantity
    left = inputs[0]
    for source, (expected, sign) in zip(inputs, params.terms, strict=True):
        if (
            source.domain != left.domain
            or not isinstance(source.quantity, ObservedQuantity)
            or source.quantity != expected
            or sign not in (1, -1)
        ):
            reject(
                "same-domain original components with signed terms",
                repr(source.quantity),
                "Observe every named component over the same members.",
                "core.occurrence_combine",
            )
    if quantity.method_version != "linear@v1":
        reject(
            "an admitted linear combination",
            repr(quantity.method_version),
            "Use the registered linear method.",
            "core.occurrence_combine",
        )
    pre: list[Fact] = []
    obligations: list[Obligation] = []
    for source in inputs:
        state = require_part(source, "original_state")
        coverage = require_part(source, "coverage")
        source_quantity = source.quantity
        assert isinstance(source_quantity, ObservedQuantity)
        if (
            not isinstance(state, OriginalStatePart)
            or state.quantity_id != source_quantity.definition_id
            or state.contribution_id != source_quantity.contribution_id
            or not isinstance(coverage, CoveragePart)
            or coverage.quantity_id != source_quantity.definition_id
            or coverage.binding != binding
        ):
            reject(
                "each occurrence's own registered original state and coverage",
                repr(state),
                "Retain the registered original states.",
                "core.occurrence_combine.state",
            )
        premises: tuple[tuple[Fact, CheckId], ...] = (
            (
                _fact("contribution_partition", binding, source_quantity.contribution_id),
                "source.contribution_partition@v1",
            ),
            (
                _fact("complete_coverage", binding, source_quantity.definition_id),
                "source.complete_coverage@v1",
            ),
        )
        for fact, check in premises:
            pre.append(fact)
            obligations.extend(_premise(inputs, fact, check_id=check, before="consume"))
    pair = _fact("key_set_equal", binding, quantity.definition_id, inputs)
    pre.append(pair)
    obligations.extend(_premise(inputs, pair, check_id="source.exact_pairing@v1", before="consume"))
    # The combined quantity has no source rows of its own: each component's own
    # observation node already owns and publishes its partition/coverage facts,
    # which the combination consumes through the premises bound above.
    components = tuple(
        f"{'plus' if sign > 0 else 'minus'}_{index}_{name}"
        for index, (_source, sign) in enumerate(params.terms)
        for name in ("sum", "non_null_count")
    )
    components += tuple(
        f"{'plus' if sign > 0 else 'minus'}_{index}_absolute_sum"
        for index, (source, (_, sign)) in enumerate(zip(inputs, params.terms, strict=True))
        if isinstance((part := require_part(source, "original_state")), OriginalStatePart)
        and "absolute_sum" in part.components
    )
    state = OriginalStatePart(
        binding,
        quantity.definition_id,
        "linear@v1",
        quantity.contribution_id,
        components,
        "v1",
        _component_empty_rules(inputs),
        temporal_policy=_component_temporal_policy(inputs),
    )
    partition = _fact("contribution_partition", binding, quantity.contribution_id)
    complete = _fact("complete_coverage", binding, quantity.definition_id)
    obligations.extend(
        (
            Obligation(partition, "source.contribution_partition@v1", "publish"),
            Obligation(complete, "source.complete_coverage@v1", "publish"),
        )
    )
    coordinate = next((p for p in left.parts if isinstance(p, CoordinateStatePart)), None)
    coordinate_parts: tuple[Part, ...] = ()
    if coordinate is not None:
        for source in inputs[1:]:
            other = next((p for p in source.parts if isinstance(p, CoordinateStatePart)), None)
            if (
                other is None
                or other.coordinates != coordinate.coordinates
                or other.value_type != coordinate.value_type
            ):
                reject(
                    "the same complete typed coordinate tuple on every occurrence",
                    repr(other),
                    "Bind the same contribution coordinates.",
                    "core.occurrence_combine.coordinates",
                )
        coordinate_parts = (
            replace(coordinate, quantity_id=quantity.definition_id, components=components),
        )
    domain = replace(params.output_domain, definition_id=quantity.definition_id)
    if coordinate is not None:
        domain = replace(domain, instance_key=(*domain.instance_key, *coordinate.coordinates))
    parts: tuple[Part, ...] = (
        *coordinate_parts,
        *tuple(
            replace(p, source_key=domain.instance_key)
            for p in left.parts
            if isinstance(p, SubjectPart)
        ),
        state,
        CoveragePart(binding, quantity.definition_id, binding.scope_id, "v1"),
    )
    return _result(
        "occurrence_combine@v1",
        inputs,
        domain,
        quantity,
        parts,
        pre=tuple(pre),
        required=("original_state", "coverage"),
        created=("original_state", "coverage"),
        post=(partition, complete),
        obligations=tuple(obligations),
        eval_id="occurrence_combine.finish@v1",
    )


def _original_reduce(inputs: tuple[Signature, ...], params: OriginalReduce) -> RuleDerivation:
    from marivo.analysis.methods.registry import REGISTRY
    from marivo.analysis.methods.semantics import key_for_parameters

    method_semantics = REGISTRY.lookup(key_for_parameters(params)).semantics
    if len(inputs) != 1 or inputs[0].quantity is None:
        reject(
            "one observed relation",
            str(len(inputs)),
            "Bind an original quantity.",
            "core.original_reduce",
        )
    source = inputs[0]
    binding = _binding(inputs, "core.original_reduce")
    _output_domain(binding, params.output_domain, "core.original_reduce")
    if type(params.coordinates) is not tuple or any(
        type(c) is not Coordinate for c in params.coordinates
    ):
        reject(
            "an ordered tuple of complete coordinates",
            repr(params.coordinates),
            "Pass the retained typed axes as a tuple.",
            "core.original_reduce.coordinate",
        )
    retained_keys = source.domain.instance_key
    if params.time_mapping:
        from marivo.analysis.core.time_grid import coarsening

        if (
            source.domain.time_grid is None
            or params.output_domain.time_grid is None
            or params.time_mapping
            != coarsening(source.domain.time_grid, params.output_domain.time_grid)
        ):
            reject(
                "exact whole-cell coarsening",
                repr(params.time_mapping),
                "Retain the precise input grid and target boundaries.",
                "core.original_reduce.time",
            )
        retained_keys = tuple(
            replace(c, field="time:" + params.output_domain.time_grid.identity)
            if c.role == "anchor"
            else c
            for c in retained_keys
        )
    nested = bool(params.coordinates) and not set(params.coordinates) <= set(retained_keys)
    if not params.coordinates:
        _reduction_domain(source.domain, params.output_domain)
    else:
        available = retained_keys
        if nested:
            coordinate = require_part(source, "coordinate_state")
            if not isinstance(coordinate, CoordinateStatePart):
                reject(
                    "retained contribution coordinates",
                    repr(coordinate),
                    "Observe with the exact coordinates first.",
                    "core.original_reduce.coordinate",
                )
            available = (*retained_keys, *coordinate.coordinates)
        if (
            len(set(params.coordinates)) != len(params.coordinates)
            or not set(params.coordinates) <= set(available)
            or params.output_domain.kind != "group"
            or params.output_domain.instance_key != params.coordinates
            or params.output_domain.target_key != params.coordinates
            or params.output_domain.binding != source.domain.binding
            or params.output_domain.correspondence is not None
        ):
            reject(
                "the exact retained complete coordinate target",
                repr(params.output_domain),
                "Group by distinct retained coordinates.",
                "core.original_reduce.coordinate",
            )
    quantity = source.quantity
    assert quantity is not None
    if not isinstance(quantity, (ObservedQuantity, RolledQuantity)):
        reject(
            "an original Metric quantity",
            quantity.kind,
            "Use its original components, not current-row values.",
            "core.original_reduce.quantity",
        )
    state = require_part(source, "original_state")
    if (
        isinstance(state, OriginalStatePart)
        and state.temporal_policy in ("repeated", "overlapping")
        and (bool(params.time_mapping) or not any(c.role == "anchor" for c in params.coordinates))
    ):
        reject(
            "disjoint original contributions when removing time",
            "the fixed window repeats or cumulative windows overlap on time cells",
            "Keep the time axis or observe each grid.window.",
            "core.original_reduce.time",
        )
    coverage = require_part(source, "coverage")
    if (
        not isinstance(state, OriginalStatePart)
        or state.quantity_id != quantity.definition_id
        or state.method_version != quantity.method_version
        or state.contribution_id != quantity.contribution_id
        or state.method_version != method_semantics.original_state_method
        or (
            params.method != "linear"
            and state.components
            not in (
                method_semantics.state_components,
                (
                    *method_semantics.state_components,
                    "numerator_absolute_sum",
                    "denominator_absolute_sum",
                )
                if params.method == "ratio"
                else (),
                (
                    *method_semantics.state_components,
                    "absolute_weight_sum",
                    "absolute_weighted_numerator",
                )
                if params.method == "weighted_mean"
                else (),
                (
                    *method_semantics.state_components,
                    {
                        "sum": "absolute_sum",
                        "sum_zero": "absolute_sum",
                        "mean": "absolute_sum",
                        "ratio": "denominator_absolute_sum",
                        "weighted_mean": "absolute_weight_sum",
                    }.get(params.method, ""),
                ),
            )
        )
        or state.version != "v1"
    ):
        reject(
            "complete state bound to this original quantity",
            repr(state),
            f"Retain {method_semantics.original_state_method} state "
            f"{method_semantics.state_components}, version v1.",
            "core.original_reduce.state",
        )
    if (
        not isinstance(coverage, CoveragePart)
        or coverage.binding != source.domain.binding
        or coverage.quantity_id != quantity.definition_id
        or coverage.scope_id != source.domain.binding.scope_id
    ):
        reject(
            "coverage bound to the same quantity and range",
            repr(coverage),
            "Bind complete original coverage.",
            "core.original_reduce.coverage",
        )
    partition = _fact("contribution_partition", binding, quantity.contribution_id)
    complete = _fact("complete_coverage", binding, quantity.definition_id)
    obligations = (
        *_premise(inputs, partition, check_id=params.partition_check_id, before="consume"),
        *_premise(inputs, complete, check_id=params.coverage_check_id, before="consume"),
    )
    rolled = RolledQuantity(
        quantity.definition_id,
        quantity.definition_id,
        quantity.method_version,
        quantity.contribution_id,
        quantity.unit,
        quantity.time_scope,
        quantity.value_policy,
    )
    new_state = replace(state, binding=params.output_domain.binding)
    new_coverage = replace(
        coverage,
        binding=params.output_domain.binding,
        scope_id=params.output_domain.binding.scope_id,
    )
    subjects = tuple(
        replace(
            p,
            source_key=params.output_domain.instance_key,
            injective=set(p.subject_key) == set(params.output_domain.instance_key),
        )
        for p in source.parts
        if isinstance(p, SubjectPart)
        and set(p.subject_key) <= set(params.output_domain.instance_key)
    )
    return _result(
        "original_reduce@v1",
        inputs,
        params.output_domain,
        rolled,
        (*subjects, new_state, new_coverage),
        pre=(partition, complete),
        required=(
            "original_state",
            "coverage",
            *(("coordinate_state",) if nested else ()),
        ),
        created=(),
        post=(_fact("state_binding", params.output_domain.binding, rolled.definition_id),),
        obligations=obligations,
        eval_id="original_reduce.merge_then_finish@v1",
    )


def _parts_transport(inputs: tuple[Signature, ...], params: PartsTransport) -> RuleDerivation:
    if params.mode == "cohort":
        if len(inputs) < 2 or params.opportunity_domain is None or params.cohort_rule is None:
            reject(
                "complete target and opportunity inputs",
                repr(params),
                "Bind a full Entity opportunity predicate.",
                "analysis.cohort",
            )
        target, opportunity = inputs[0], params.opportunity_domain
        if (
            params.keep_quantity
            or params.output_domain != target.domain
            or params.retained_roles != ("subject",)
            or not params.predicates
            or any(
                leaf.input_index == 0 or leaf.right_index == 0
                for tree in params.predicates
                for leaf in leaves(tree)
            )
        ):
            reject(
                "a complete opportunity predicate and Subject output",
                repr(params),
                "Use the original targets and explicit opportunity inputs.",
                "analysis.cohort",
            )
        subject = require_part(target, "subject")
        if (
            not isinstance(subject, SubjectPart)
            or not subject.total
            or target.domain.instance_key != subject.subject_key
            or target.domain.time_grid is not None
        ):
            reject(
                "an Entity target with its complete Subject key",
                repr(target.domain),
                "Use the original Entity target for cohort.",
                "analysis.cohort",
            )
        if (
            any(item.domain != opportunity for item in inputs[1:])
            or tuple(k for k in opportunity.instance_key if k.role != "anchor")
            != target.domain.instance_key
        ):
            reject(
                "complete Entity or Entity/time opportunities for this target",
                repr(opportunity),
                "Bind all predicate inputs over one complete opportunity domain.",
                "analysis.cohort",
            )
        binding = _binding(inputs, "core.cohort")
        part = CohortDecisionPart(
            binding, opportunity, params.cohort_rule, params.cohort_count, params.cohort_empty
        )
        return _result(
            "domain.cohort@v1",
            inputs,
            target.domain,
            None,
            (subject, part),
            pre=(),
            required=("subject",),
            created=("cohort_decision",),
            post=(_fact("output_key", binding, target.domain.definition_id),),
            obligations=(),
            eval_id="domain.cohort@v1",
        )
    if not inputs:
        reject(
            "the exact receiver and optional predicate input",
            str(len(inputs)),
            "Bind explicit relation dependencies.",
            "core.parts_transport",
        )
    if params.external_predicate and (
        params.mode != "where"
        or not params.predicates
        or any(inputs[0].domain.instance_key != item.domain.instance_key for item in inputs[1:])
    ):
        reject(
            "a predicate on corresponding complete keys",
            repr(inputs),
            "Bind the same retained instance domain.",
            "core.parts_transport.predicate",
        )
    source = inputs[0]
    binding = _binding(inputs, "core.parts_transport")
    _output_domain(binding, params.output_domain, "core.parts_transport")
    if params.mode not in ("where", "projection", "compare", "view", "materialize"):
        reject(
            "a closed transport mode",
            str(params.mode),
            "Use a registered transport mode.",
            "core.parts_transport.mode",
        )
    if (
        type(params.predicates) is not tuple
        or any(type(p) is not ValuePredicate or p.binding != binding for p in params.predicates)
        or (params.predicates and params.mode != "where")
    ):
        reject(
            "where predicates bound to the exact input scope",
            repr(params.predicates),
            "Use closed predicates with the input binding.",
            "core.parts_transport.predicate",
        )
    if len(set(params.retained_roles)) != len(params.retained_roles):
        reject(
            "distinct retained part roles",
            repr(params.retained_roles),
            "List each part once.",
            "core.parts_transport.roles",
        )
    if "coverage" in params.retained_roles and (
        params.output_domain.binding.scope_id != source.domain.binding.scope_id
    ):
        reject(
            "coverage retained only in its original scope",
            params.output_domain.binding.scope_id,
            "Drop coverage or establish a new scoped coverage fact.",
            "core.parts_transport.coverage",
        )
    if params.output_domain != source.domain:
        reject(
            "transport within the exact input domain",
            repr(params.output_domain),
            "Preserve the domain; changed inputs or selections require a registered transport mapping.",
            "core.parts_transport.domain",
        )
    parts = tuple(require_part(source, role) for role in params.retained_roles)
    if not params.keep_quantity and any(
        role
        in (
            "original_state",
            "row_state",
            "coverage",
            "statistical_weight",
            "current_endpoint",
            "baseline_endpoint",
            "correspondence",
        )
        for role in params.retained_roles
    ):
        reject(
            "quantity-bound parts only with their quantity",
            repr(params.retained_roles),
            "Retain the quantity or drop its parts.",
            "core.parts_transport.quantity",
        )
    return _result(
        "parts_transport@v1",
        inputs,
        params.output_domain,
        source.quantity if params.keep_quantity else None,
        parts,
        pre=(),
        required=params.retained_roles,
        created=(),
        post=(
            _fact("output_key", params.output_domain.binding, params.output_domain.definition_id),
        ),
        obligations=(),
        eval_id=f"parts_transport.{params.mode}@v1",
    )


def derive(inputs: tuple[Signature, ...], params: RuleParameters) -> RuleDerivation:
    """Derive through the unique method owner without source or Runtime I/O."""
    from marivo.analysis.methods.registry import REGISTRY

    return REGISTRY.derive(inputs, params)


Key: TypeAlias = tuple[str | int, ...]


def _checked_keys(
    keys: tuple[Key, ...], location: str, *, allow_singleton: bool = False
) -> int | None:
    arities = {len(key) for key in keys if type(key) is tuple}
    if (
        len(arities) > 1
        or (arities and 0 in arities and not allow_singleton)
        or any(
            type(key) is not tuple or any(type(component) not in (str, int) for component in key)
            for key in keys
        )
    ):
        reject(
            "complete typed coordinate tuples of one arity",
            repr(keys),
            "Bind each full key with string or integer components.",
            location,
        )
    return next(iter(arities), None)


def union_full_tuples(*groups: tuple[Key, ...]) -> frozenset[Key]:
    """Union complete observed coordinates without taking a Cartesian product."""
    keys = tuple(key for group in groups for key in group)
    _checked_keys(keys, "core.coordinates")
    return frozenset(keys)


def subjects_image(
    instance_keys: tuple[Key, ...], assignments: tuple[tuple[Key, Key], ...]
) -> frozenset[Key]:
    """Return the set image of a total subject map; reject observed duplicate identities."""
    if not instance_keys and not assignments:
        return frozenset()
    instance_arity = _checked_keys(instance_keys, "core.subjects.instance")
    subject_arity = _checked_keys(
        tuple(subject for _, subject in assignments), "core.subjects.subject"
    )
    if (
        instance_arity is None
        or subject_arity is None
        or any(len(instance) != instance_arity for instance, _ in assignments)
    ):
        reject(
            "complete mapping keys",
            repr(assignments),
            "Bind the exact instance and Subject keys.",
            "core.subjects",
        )
    if len(set(instance_keys)) != len(instance_keys):
        reject(
            "unique observed instance identities",
            "duplicate identity",
            "Fix the source identity contract.",
            "core.subjects",
        )
    mapping: dict[Key, Key] = {}
    for instance, subject in assignments:
        if instance in mapping or not subject:
            reject(
                "one complete subject per instance",
                repr(instance),
                "Fix the Subject mapping.",
                "core.subjects",
            )
        mapping[instance] = subject
    if set(mapping) != set(instance_keys):
        reject(
            "a total Subject map over actual instances",
            repr(set(mapping)),
            "Bind every instance exactly once.",
            "core.subjects",
        )
    return frozenset(mapping.values())


@dataclass(frozen=True, slots=True)
class Pairing:
    keys: frozenset[Key]
    missing: tuple[MissingCoordinate, ...]


def pair_coordinates(
    current: tuple[Key, ...], baseline: tuple[Key, ...], *, exact: bool
) -> Pairing:
    """Keep missing sides as pairing facts rather than manufacturing Null cells."""
    left_arity = _checked_keys(current, "core.pair.current", allow_singleton=True)
    right_arity = _checked_keys(baseline, "core.pair.baseline", allow_singleton=True)
    if left_arity is not None and right_arity is not None and left_arity != right_arity:
        reject(
            "the same typed key arity",
            repr((left_arity, right_arity)),
            "Bind matching coordinate types.",
            "core.pair",
        )
    if len({tuple(type(component) for component in key) for key in (*current, *baseline)}) > 1:
        reject(
            "the same concrete type at every complete key position",
            repr((current, baseline)),
            "Bind corresponding keys without implicit type conversion.",
            "core.pair",
        )
    if len(set(current)) != len(current) or len(set(baseline)) != len(baseline):
        reject(
            "unique endpoint identities",
            "duplicate key",
            "Fix the endpoint key contract.",
            "core.pair",
        )
    left, right = set(current), set(baseline)
    missing = tuple(
        [MissingCoordinate("baseline", key) for key in sorted(left - right, key=repr)]
        + [MissingCoordinate("current", key) for key in sorted(right - left, key=repr)]
    )
    if exact and missing:
        reject(
            "equal complete endpoint key sets",
            repr(missing),
            "Repair the pairing or choose an explicit missing-side method.",
            "core.pair",
        )
    return Pairing(frozenset(left & right), missing)


def derive_numeric_cell(
    method: Literal["difference", "relative_change", "ratio"], left: Cell, right: Cell
) -> Cell:
    """Apply a strict two-value method; a zero denominator is Undefined."""
    if method not in ("difference", "ratio"):
        reject("difference or ratio", str(method), "Use a closed cell method.", "core.cell.eval")
    if not isinstance(left, Defined) or not isinstance(right, Defined):
        received = f"{type(left).__name__}/{type(right).__name__}"
        reject(
            "two Defined numeric Cells",
            received,
            "Resolve the method-specific Cell policy before arithmetic.",
            "core.cell.eval",
        )
    left_value, right_value = left.value, right.value
    if (
        not isinstance(left_value, (int, float))
        or not isinstance(right_value, (int, float))
        or isinstance(left_value, bool)
        or isinstance(right_value, bool)
    ):
        reject(
            "numeric Defined values",
            repr((left.value, right.value)),
            "Bind numeric quantities.",
            "core.cell.eval",
        )
    if not math.isfinite(left_value) or not math.isfinite(right_value):
        reject(
            "finite numeric cells",
            repr((left_value, right_value)),
            "Correct the numeric input.",
            "core.cell.eval",
        )
    if method == "ratio":
        if right_value == 0:
            return Undefined("zero_denominator")
        result = left_value / right_value
        if not math.isfinite(result):
            reject(
                "finite numeric result",
                repr(result),
                "Use a numeric method with supported precision.",
                "core.cell.eval",
            )
        return Defined(result)
    result = left_value - right_value
    if not math.isfinite(result):
        reject(
            "finite numeric result",
            repr(result),
            "Use a numeric method with supported precision.",
            "core.cell.eval",
        )
    return Defined(result)


def _attach_category(inputs: tuple[Signature, ...], params: AttachCategory) -> RuleDerivation:
    binding = _binding(inputs, "core.group.classification")
    if len(inputs) != 2:
        reject(
            "one relation and one classification",
            str(len(inputs)),
            "Bind an explicit CategoryRelation.",
            "core.group.classification",
        )
    source, category = inputs
    expected_key = source.domain.instance_key
    if params.subject_mapping:
        subject = require_part(source, "subject")
        if not isinstance(subject, SubjectPart) or not subject.total:
            reject(
                "a total retained Subject map",
                repr(subject),
                "Retain the full member mapping.",
                "core.group.classification",
            )
        expected_key = subject.subject_key
    if (
        category.quantity is not None
        or category.domain.instance_key != expected_key
        or params.coordinate in source.domain.instance_key
        or params.output_domain.instance_key != (*source.domain.instance_key, params.coordinate)
        or params.output_domain.binding != binding
    ):
        reject(
            "one unambiguous classification on complete input keys",
            repr(category.domain),
            "Bind a corresponding category and distinct keys.",
            "core.group.classification",
        )
    parts = tuple(
        replace(p, source_key=params.output_domain.instance_key)
        if isinstance(p, SubjectPart)
        else p
        for p in source.parts
    )
    return _result(
        "parts_transport@v1",
        inputs,
        params.output_domain,
        source.quantity,
        parts,
        pre=(),
        required=("subject",) if params.subject_mapping else (),
        created=(),
        post=(),
        obligations=(),
        eval_id="group.attach.complete_keys@v1",
    )


def _complete_groups(inputs: tuple[Signature, ...], params: CompleteGroups) -> RuleDerivation:
    binding = _binding(inputs, "core.group.target")
    if len(inputs) != 2:
        reject(
            "one reduced relation and one explicit target",
            str(len(inputs)),
            "Bind groups explicitly.",
            "core.group.target",
        )
    source, target = inputs
    if (
        source.domain.instance_key != target.domain.instance_key
        or target.quantity is not None
        or params.output_domain.binding != binding
        or params.output_domain.instance_key != source.domain.instance_key
    ):
        reject(
            "identical complete typed group coordinates",
            repr(target.domain),
            "Use the exact target group domain.",
            "core.group.target",
        )
    if source.quantity is None:
        if source.parts or source.domain.kind not in ("group", "singleton"):
            reject(
                "a projected group domain without value or state parts",
                repr(source),
                "Project complete group keys before binding a target domain.",
                "core.group.target",
            )
        return _result(
            "parts_transport@v1",
            inputs,
            params.output_domain,
            None,
            (),
            pre=(),
            required=(),
            created=(),
            post=(),
            obligations=(),
            eval_id="group.complete.empty_states@v1",
        )
    state_role: PartRole = (
        "row_state" if isinstance(source.quantity, RowStatisticQuantity) else "original_state"
    )
    require_part(source, state_role)
    required: tuple[PartRole, ...] = (state_role,)
    if state_role == "original_state":
        require_part(source, "coverage")
        required = (*required, "coverage")
    return _result(
        "parts_transport@v1",
        inputs,
        params.output_domain,
        source.quantity,
        source.parts,
        pre=(),
        required=required,
        created=(),
        post=(),
        obligations=(),
        eval_id="group.complete.empty_states@v1",
    )


def _time_product(inputs: tuple[Signature, ...], params: TimeProduct) -> RuleDerivation:
    binding = _binding(inputs, "core.time.product")
    if len(inputs) != 1:
        reject(
            "one member domain", str(len(inputs)), "Bind one member receiver.", "core.time.product"
        )
    source = inputs[0]
    domain = params.output_domain
    grid = domain.time_grid
    subject = require_part(source, "subject")
    if (
        params.kind != "time_product"
        or source.quantity is not None
        or source.domain.time_grid is not None
        or source.domain.kind != "entity"
        or grid is None
        or domain.binding != binding
        or domain.instance_key[:-1] != source.domain.instance_key
        or domain.instance_key[-1].role != "anchor"
        or domain.instance_key[-1].field != "time:" + grid.identity
        or not isinstance(subject, SubjectPart)
    ):
        reject(
            "one bounded member/time product",
            repr(domain),
            "Use each on members with one exact grid.",
            "core.time.product",
        )
    parts = (replace(subject, source_key=domain.instance_key, injective=False),)
    return _result(
        "parts_transport@v1",
        inputs,
        domain,
        None,
        parts,
        pre=(),
        required=("subject",),
        created=(),
        post=(),
        obligations=(),
        eval_id="time.product@v1",
    )


def _component_temporal_policy(
    inputs: tuple[Signature, ...],
) -> Literal["none", "partition", "repeated", "overlapping"]:
    policies = {
        part.temporal_policy
        for source in inputs
        for part in source.parts
        if isinstance(part, OriginalStatePart)
    }
    if len(policies) != 1:
        reject(
            "matching component temporal policies",
            repr(policies),
            "Bind the same grid windows for all components.",
            "core.observe.time",
        )
    return next(iter(policies))


def _reference(inputs: tuple[Signature, ...], params: ReferenceDerive) -> RuleDerivation:
    binding = _binding(inputs, "analysis.reference")
    if len(inputs) != (3 if params.kind == "share" else 2):
        reject(
            "exact ordered values/reference dependencies",
            str(len(inputs)),
            "Bind the original inputs.",
            "analysis.reference",
        )
    left, right = inputs[:2]
    _output_domain(binding, params.output_domain, "analysis.reference")
    if params.kind == "share":
        basis = inputs[2]
        state = next((part for part in basis.parts if isinstance(part, OriginalStatePart)), None)
        if (
            state is None
            or params.share_state != state
            or state.method_version not in ("sum@v1", "sum_zero@v1", "count@v1", "linear@v1")
            or basis.domain.instance_key != left.domain.instance_key
            or params.output_domain.instance_key != left.domain.instance_key
        ):
            reject(
                "the exact additive support and complete output keys",
                repr(params),
                "Retain the original additive definition and its state.",
                "analysis.reference",
            )
    elif params.output_domain.kind != "singleton" or params.output_domain.instance_key:
        reject(
            "one Singleton reference result",
            repr(params.output_domain),
            "Remove axes only through this registered reference method.",
            "analysis.reference",
        )
    if params.kind == "penetration":
        if (
            any(item.quantity is not None or item.domain.kind != "entity" for item in (left, right))
            or left.domain.instance_key != right.domain.instance_key
        ):
            reject(
                "complete compatible Entity member domains",
                repr(inputs),
                "Use members of the same Entity and key.",
                "analysis.reference",
            )
    else:
        if (
            left.quantity is None
            or right.quantity is None
            or left.quantity.time_scope != right.quantity.time_scope
            or params.time_scope != left.quantity.time_scope
            or params.unit != (left.quantity.unit if params.kind == "standardize" else None)
        ):
            reject(
                "numeric inputs with identical frozen time scopes",
                repr(inputs),
                "Bind matching observation scopes.",
                "analysis.reference",
            )
        if params.kind == "share" and (
            right.domain.kind != "singleton" or left.quantity.unit != right.quantity.unit
        ):
            reject(
                "a same-measure Singleton reference",
                repr(right),
                "Roll up the original additive quantity once.",
                "analysis.reference",
            )
        if params.kind == "standardize" and (
            left.domain.kind != "group"
            or right.domain.kind != "group"
            or not params.strata
            or left.domain.instance_key != params.strata
            or right.domain.instance_key != params.strata
            or len(params.strata_dependencies) != len(params.strata)
            or right.quantity.unit not in (None, "1")
            or params.statistical_unit is None
        ):
            reject(
                "complete matching strata, dimensionless weights and a statistical Entity",
                repr(params),
                "Bind the same ordered grouping axes and statistical unit.",
                "analysis.reference",
            )
    quantity = DerivedQuantity(
        params.output_domain.definition_id,
        "reference." + params.kind + "@v1",
        tuple(
            item.quantity.definition_id if item.quantity else item.domain.definition_id
            for item in inputs
        ),
        params.unit,
        params.time_scope,
        "fixed_reference",
    )

    def reasons(signature: Signature) -> tuple[tuple[str, tuple[str, ...]], ...]:
        from marivo.analysis.methods.semantics import quantity_cell_reasons

        return quantity_cell_reasons(signature.quantity)

    parts: tuple[Part, ...] = (
        ReferenceStatePart(
            binding,
            "fixed_reference",
            right.domain,
            params.reference_id,
            cell_reasons=reasons(right),
        ),
        ReferenceStatePart(
            binding,
            "reference_proof",
            inputs[2].domain if params.kind == "share" else left.domain,
            params.reference_id,
            params.share_state,
            reasons(inputs[2] if params.kind == "share" else left),
        ),
        ReferenceStatePart(
            binding, "stratum_values", left.domain, params.reference_id, cell_reasons=reasons(left)
        ),
    )
    if params.kind == "standardize":
        parts += (ReferenceStatePart(binding, "strata", right.domain, params.reference_id),)
    return _result(
        "reference@v1",
        inputs,
        params.output_domain,
        quantity,
        parts,
        pre=(),
        required=(),
        created=tuple(part_role(part) for part in parts),
        post=(),
        obligations=(),
        eval_id="reference." + params.kind + "@v1",
    )
