"""Closed, source-free R3.1 rule derivation and fixed-input semantic helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

from marivo.analysis.core.model import (
    Binding,
    Cell,
    CheckId,
    Coordinate,
    Correspondence,
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
    Part,
    PartRole,
    Quantity,
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
from marivo.refs import (
    DimensionKind,
    EntityKind,
    MetricKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    TimeDimensionKind,
)
from marivo.semantic.ir import (
    TargetDimensionContract,
    TargetEntityContract,
    TargetRelationshipContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)
from marivo.semantic.metric_graph import CatalogMetricIdentity, TargetMetricContract

RuleId: TypeAlias = Literal[
    "bind_project@v1",
    "map_correspond@v1",
    "cell_derive@v1",
    "row_state@v1",
    "original_reduce@v1",
    "parts_transport@v1",
]


@dataclass(frozen=True, slots=True)
class BindProject:
    ref: Ref[DimensionKind] | Ref[TimeDimensionKind] | Ref[MetricKind]
    field_owner: Ref[EntityKind]
    field_contract: TargetDimensionContract | None
    metric_contract: TargetMetricContract | None
    path: tuple[Ref[RelationshipKind], ...]
    path_contracts: tuple[TargetRelationshipContract, ...]
    quantity: ObservedQuantity | None = None


MapMode: TypeAlias = Literal["exact_keys", "one_to_one", "union_keys", "group", "subjects"]


@dataclass(frozen=True, slots=True)
class MapCorrespond:
    mode: MapMode
    output_domain: DomainSignature
    check_id: CheckId | None = None


@dataclass(frozen=True, slots=True)
class CellDerive:
    method: Literal["difference", "ratio"]
    definition_id: str
    value_policy: str
    unit: str | None
    time_scope: str
    pairing_check_id: CheckId | None = None
    numeric_check_id: CheckId | None = None


RowMethod: TypeAlias = Literal["sum", "mean", "count", "count_defined", "weighted_mean"]


@dataclass(frozen=True, slots=True)
class RowState:
    method: RowMethod
    output_domain: DomainSignature
    definition_id: str
    value_policy: str
    weighting: str = "equal_weight"
    numeric_check_id: CheckId | None = None


@dataclass(frozen=True, slots=True)
class OriginalReduce:
    output_domain: DomainSignature
    partition_check_id: CheckId | None = None
    coverage_check_id: CheckId | None = None


TransportMode: TypeAlias = Literal["where", "projection", "compare", "view", "materialize"]


@dataclass(frozen=True, slots=True)
class PartsTransport:
    mode: TransportMode
    output_domain: DomainSignature
    retained_roles: tuple[PartRole, ...]
    keep_quantity: bool


RuleParameters: TypeAlias = (
    BindProject | MapCorrespond | CellDerive | RowState | OriginalReduce | PartsTransport
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
    version_selection: TargetSnapshotSelection | TargetValiditySelection | None = None,
) -> Signature:
    """Use the declared complete business key, without scanning or deduplicating rows."""
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
            or contract.to_version_resolution_required
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
    pre = (
        _fact("field_ownership", binding, params.ref.path),
        _fact("single_value", binding, params.ref.path),
    )
    obligations = (
        _premise(inputs, pre[1], check_id="source.single_value@v1", before="consume")
        if params.path
        else ()
    )
    return _result(
        "bind_project@v1",
        inputs,
        source.domain,
        params.quantity,
        source.parts,
        pre=pre,
        required=(),
        created=(),
        post=(_fact("output_key", binding, source.domain.definition_id),),
        obligations=obligations,
        eval_id="bind_project.projection@v1",
        established=(Evidence(pre[0], "builder", params.ref.path),),
    )


def _map_correspond(inputs: tuple[Signature, ...], params: MapCorrespond) -> RuleDerivation:
    if not inputs or len(inputs) > 2:
        reject("one or two domain inputs", str(len(inputs)), "Bind the mapped domains.", "core.map")
    binding = _binding(inputs, "core.map")
    _output_domain(binding, params.output_domain, "core.map")
    source = inputs[0]
    pre: tuple[Fact, ...]
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
    if left.domain.instance_key != right.domain.instance_key:
        reject(
            "matching typed coordinate keys",
            repr(right.domain.instance_key),
            "Map the two domains explicitly.",
            "core.cell.keys",
        )
    if left.quantity.unit != right.quantity.unit or left.quantity.unit != params.unit:
        reject(
            "matching bound units",
            repr((left.quantity.unit, right.quantity.unit)),
            "Use comparable quantities.",
            "core.cell.unit",
        )
    if params.method not in ("difference", "ratio"):
        reject(
            "difference or ratio",
            str(params.method),
            "Use a closed cell method.",
            "core.cell.method",
        )
    pair = _fact("key_set_equal", binding, params.definition_id, inputs)
    numeric = _fact("finite_numeric", binding, params.definition_id, inputs)
    obligations = (
        *_premise(inputs, pair, check_id=params.pairing_check_id, before="consume"),
        *_premise(inputs, numeric, check_id=params.numeric_check_id, before="consume"),
    )
    output = DerivedQuantity(
        params.definition_id,
        f"cell.{params.method}@v1",
        (left.quantity.definition_id, right.quantity.definition_id),
        params.unit if params.method == "difference" else "1",
        params.time_scope,
        params.value_policy,
    )
    parts: tuple[Part, ...] = (
        EndpointPart(binding, "current", left.quantity.definition_id, "v1"),
        EndpointPart(right.domain.binding, "baseline", right.quantity.definition_id, "v1"),
    )
    return _result(
        "cell_derive@v1",
        inputs,
        left.domain,
        output,
        parts,
        pre=(pair, numeric),
        required=(),
        created=("current_endpoint", "baseline_endpoint"),
        post=(_fact("cell_policy", binding, output.definition_id),),
        obligations=obligations,
        eval_id=f"cell_derive.{params.method}@v1",
    )


def _reduction_domain(source: DomainSignature, target: DomainSignature) -> None:
    """Only the whole-input singleton has an implemented reduction mapping."""
    if (
        target.kind != "singleton"
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
    if len(inputs) != 1 or inputs[0].quantity is None:
        reject(
            "one current-row quantity",
            str(len(inputs)),
            "Bind a single-quantity relation.",
            "core.row_state",
        )
    source = inputs[0]
    binding = _binding(inputs, "core.row_state")
    _output_domain(binding, params.output_domain, "core.row_state")
    _reduction_domain(source.domain, params.output_domain)
    if params.method not in ("sum", "mean", "count", "count_defined", "weighted_mean"):
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
    assert source.quantity is not None
    numeric = _fact(
        "cell_policy" if params.method == "count_defined" else "finite_numeric",
        binding,
        source.quantity.definition_id,
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
        source.quantity.definition_id,
        source.domain.definition_id,
        "count" if params.method in ("count", "count_defined") else source.quantity.unit,
        source.quantity.time_scope,
        params.value_policy,
        params.weighting,
    )
    state = RowStatePart(
        params.output_domain.binding,
        quantity.definition_id,
        method_version,
        source.domain.definition_id,
        method_semantics.state_components,
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


def _original_reduce(inputs: tuple[Signature, ...], params: OriginalReduce) -> RuleDerivation:
    from marivo.analysis.methods.registry import REGISTRY
    from marivo.analysis.methods.semantics import MethodKey

    method_semantics = REGISTRY.lookup(MethodKey("state_rollup")).semantics
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
    _reduction_domain(source.domain, params.output_domain)
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
    coverage = require_part(source, "coverage")
    if (
        not isinstance(state, OriginalStatePart)
        or state.quantity_id != quantity.definition_id
        or state.method_version != quantity.method_version
        or state.contribution_id != quantity.contribution_id
        or state.method_version != method_semantics.original_state_method
        or state.components != method_semantics.state_components
        or state.version != "v1"
    ):
        reject(
            "complete state bound to this original quantity",
            repr(state),
            "Retain sum@v1 state (sum, non_null_count), version v1; other states are not admitted.",
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
    return _result(
        "original_reduce@v1",
        inputs,
        params.output_domain,
        rolled,
        (new_state, new_coverage),
        pre=(partition, complete),
        required=("original_state", "coverage"),
        created=(),
        post=(_fact("state_binding", params.output_domain.binding, rolled.definition_id),),
        obligations=obligations,
        eval_id="original_reduce.merge_then_finish@v1",
    )


def _parts_transport(inputs: tuple[Signature, ...], params: PartsTransport) -> RuleDerivation:
    if len(inputs) != 1:
        reject("one relation input", str(len(inputs)), "Bind one relation.", "core.parts_transport")
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


def _checked_keys(keys: tuple[Key, ...], location: str) -> int | None:
    arities = {len(key) for key in keys if type(key) is tuple}
    if (
        len(arities) > 1
        or (arities and 0 in arities)
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
    left_arity = _checked_keys(current, "core.pair.current")
    right_arity = _checked_keys(baseline, "core.pair.baseline")
    if left_arity is not None and right_arity is not None and left_arity != right_arity:
        reject(
            "the same typed key arity",
            repr((left_arity, right_arity)),
            "Bind matching coordinate types.",
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


def derive_numeric_cell(method: Literal["difference", "ratio"], left: Cell, right: Cell) -> Cell:
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
