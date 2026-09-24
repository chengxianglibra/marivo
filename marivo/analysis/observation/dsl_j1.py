"""Private, source-free J1 construction over the existing Dataset logical roots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo._temporal import TimeScope
from marivo.analysis.datasets.descriptors import (
    AnalysisDomain,
    DatasetField,
    DatasetFieldId,
    DatasetRowContract,
    DatasetRowSetContract,
    QuantityState,
    _complete_from_schema,
    _deferred_type,
    _entity_domain,
    _generated_identity,
    _group_domain,
    _keyed_cardinality,
    _make_field,
    _make_field_id,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _observed_quantity,
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
    _row_statistic_quantity,
    _singleton_cardinality,
    _singleton_domain,
    _StableIdRegistry,
    _unknown_row_bound,
    _unordered_ordering,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import (
    DefinitionInput,
    LogicalInputToken,
    LogicalRootHandle,
    _make_logical_root,
)
from marivo.analysis.observation.contracts import MetricComponentPlan, derive_metric_components
from marivo.analysis.observation.coordinates import path_is_functional
from marivo.refs import DimensionKind, EntityKind, MetricKind, Ref, RelationshipKind, SemanticKind
from marivo.semantic._dsl_authoring import AdditiveAllV1
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import TargetDimensionContract
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import Registry, normalize_target_dimension, normalize_target_entity

_KINDS = ("members", "read", "where", "group", "observe", "rollup", "summarize")
_IDS = _StableIdRegistry(
    families=frozenset({"dsl_j1"}),
    shapes=frozenset(("dsl_j1", kind, 1) for kind in _KINDS),
    roles=frozenset({"member", "group", "value", "cell"}),
    logical_types=frozenset({"int64", "float64", "string", "unknown"}),
    physical_types=frozenset({"int64", "float64", "string", "unknown"}),
    admitted_types=frozenset({"int64", "float64", "string", "unknown"}),
)
J1_SUM_PARTS = ("value.sum", "value.non_null_count", "value.row_count")


def _reject(expected: str, received: str, *, repair: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair=repair,
        location="dsl.j1",
    )


def _j1_contracts(
    context: J1Context,
    kind: str,
    entity_path: str,
    input_root: LogicalRootHandle | None,
    parameters: tuple[object, ...],
) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    entity = normalize_target_entity(context.registry, entity_path)
    member_type = entity.identity_signature[0][1]
    if member_type not in ("unknown", "string", "int64"):
        raise _reject(
            "string or int64 member identity",
            member_type,
            repair="Use a first-round single-column Entity identity.",
        )
    group_type = "string"
    if kind == "group" or (
        kind == "observe"
        and input_root is not None
        and input_root.shape_id.local_shape_id == "group"
    ):
        group_path = (
            parameters[0]
            if kind == "group"
            else input_root.parameters[0]
            if input_root is not None and type(input_root.parameters) is tuple
            else None
        )
        if isinstance(group_path, str) and group_path in context.registry.dimensions:
            group_type = normalize_target_dimension(context.registry, group_path).logical_type
        if group_type not in ("unknown", "string"):
            raise _reject(
                "string categorical group",
                group_type,
                repair="Use a first-round string Dimension for grouping.",
            )
    if kind in ("members", "where", "read"):
        key = "member"
        key_id = _make_field_id("member." + entity_path + "." + entity.primary_key[0])
        key_type = member_type
    elif kind == "group" or (
        kind == "observe"
        and input_root is not None
        and input_root.shape_id.local_shape_id == "group"
    ):
        key = "group"
        path = (
            parameters[0]
            if kind == "group"
            else input_root.parameters[0]
            if input_root is not None and type(input_root.parameters) is tuple
            else None
        )
        key_id = _make_field_id(
            (
                "contribution."
                if kind == "group" and len(parameters) > 1 and parameters[1] == "contribution"
                else "dimension."
            )
            + str(path)
        )
        key_type = group_type
    elif kind == "observe":
        key = "member"
        key_id = _make_field_id("member." + entity_path + "." + entity.primary_key[0])
        key_type = member_type
    else:
        key = None
        key_id = None
        key_type = None

    def field(name: str, field_id: str, role: str, logical: str, nullable: bool) -> DatasetField:
        identifier = _make_field_id(field_id)
        return _make_field(
            field_id=identifier,
            name=name,
            role_id=role,
            identity=_generated_identity(identifier),
            derivation_identity="dsl.j1." + field_id,
            logical_type_id=logical,
            physical_type_state=_deferred_type(logical, ids=_IDS),
            nullable=nullable,
            ids=_IDS,
        )

    columns = []
    if key is not None and key_id is not None and key_type is not None:
        columns.append(field(key, key_id.value, key, key_type, False))
    if kind in ("read", "observe", "rollup", "summarize") or (
        kind == "group"
        and input_root is not None
        and input_root.shape_id.local_shape_id == "observe"
    ):
        if kind == "read":
            value_type = normalize_target_dimension(
                context.registry, str(parameters[0])
            ).logical_type
        elif kind == "summarize" and parameters[0] == "count":
            value_type = "int64"
        elif kind == "summarize" and parameters[0] == "mean":
            value_type = "float64"
        else:
            value_type = "unknown"
        columns.extend(
            (
                field("value", "j1.value", "value", value_type, True),
                field("cell_tag", "j1.cell_tag", "cell", "string", False),
                field("cell_reason", "j1.cell_reason", "cell", "string", True),
            )
        )
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("dsl_j1", kind, 1, ids=_IDS),
        schema=_make_schema(tuple(columns)),
        coordinate_field_ids=(key_id,) if key_id is not None else (),
        key_field_ids=(key_id,) if key_id is not None else (),
        family_semantics=_complete_from_schema(),
    )
    rows = _make_row_set_contract(
        schema_version=1,
        cardinality=_keyed_cardinality(_unknown_row_bound())
        if key_id is not None
        else _singleton_cardinality(),
        ordering=_unordered_ordering(),
    )
    return row, rows


def j1_row_contracts(
    context: J1Context, root: LogicalRootHandle
) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    """Recover the complete row contract bound into an exact J1 root."""
    kind = root.shape_id.local_shape_id
    parameters = root.parameters
    if type(parameters) is not tuple:
        raise _reject("canonical J1 parameters", "invalid root", repair="Rebuild the J1 node.")
    first = root
    while first.inputs:
        input_root = first.inputs[0].root
        if not isinstance(input_root, LogicalRootHandle):
            raise _reject(
                "J1 logical ancestry", "materialized leaf", repair="Use the exact J1 input."
            )
        first = input_root
    member_path = first.parameters[0] if type(first.parameters) is tuple else None
    if not isinstance(member_path, str):
        raise _reject("bound member Entity", "invalid root", repair="Rebuild the J1 node.")
    parent = root.inputs[0].root if root.inputs else None
    return _j1_contracts(
        context,
        kind,
        member_path,
        parent if isinstance(parent, LogicalRootHandle) else None,
        parameters,
    )


@dataclass(frozen=True, slots=True, eq=False)
class J1Context:
    """One invocation's declared semantic facts and logical-root authority."""

    registry: Registry
    sidecar: CompiledExpressionSidecar
    session_id: str
    store_id: str

    def _node(
        self,
        kind: str,
        *,
        entity: str,
        input_root: LogicalRootHandle | None = None,
        parameters: tuple[str | int | None | tuple[str, ...], ...] = (),
        dependency: str = "",
        requirements: tuple[str, ...] = (),
    ) -> LogicalRootHandle:
        shape = _make_shape_id("dsl_j1", kind, 1, ids=_IDS)
        inputs = (
            (
                DefinitionInput(
                    "input", LogicalInputToken(input_root.definition_fingerprint), input_root
                ),
            )
            if input_root is not None
            else ()
        )
        row, rows = _j1_contracts(self, kind, entity, input_root, parameters)
        return _make_logical_root(
            session_id=self.session_id,
            store_id=self.store_id,
            shape_id=shape,
            row_contract_fingerprint=_row_contract_fingerprint(row),
            row_set_contract_fingerprint=_row_set_contract_fingerprint(rows),
            operator_id=f"dsl.j1.{kind}",
            inputs=inputs,
            parameters=parameters,
            dependency_facts=(dependency,) if dependency else (),
            contract_versions=(("dsl.j1", "v1"),),
            requirements=requirements,
        )

    def members(self, entity: Ref[EntityKind]) -> J1Members:
        if type(entity) is not Ref or entity.kind is not SemanticKind.ENTITY:
            raise _reject("Entity Ref", type(entity).__name__, repair="Pass ms.ref.entity(...).")
        contract = normalize_target_entity(self.registry, entity.path)
        if contract.version is not None or len(contract.identity_signature) != 1:
            raise _reject(
                "non-versioned, single-column Entity identity",
                entity.path,
                repair="Use a first-round Entity with one declared identity column.",
            )
        identity = _make_field_id("member." + entity.path + "." + contract.primary_key[0])
        domain = _entity_domain(entity, identity)
        root = self._node(
            "members",
            entity=entity.path,
            parameters=(entity.path, contract.dependency_fingerprint),
            dependency="entity:" + entity.path,
        )
        return J1Members(self, entity, domain, root)


def _member_dimension(members: J1Members, dimension: Ref[DimensionKind]) -> TargetDimensionContract:
    if type(dimension) is not Ref or dimension.kind is not SemanticKind.DIMENSION:
        raise _reject(
            "Dimension Ref", type(dimension).__name__, repair="Pass ms.ref.dimension(...)."
        )
    bound = normalize_target_dimension(members.context.registry, dimension.path)
    if bound.entity_ref.path != members.entity.path:
        raise _reject(
            "single-valued Dimension on the member Entity",
            dimension.path,
            repair="Read or group by an attribute owned by this Entity; contribution coordinates belong to observe().",
        )
    return bound


@dataclass(frozen=True, slots=True, eq=False)
class J1Members:
    context: J1Context
    entity: Ref[EntityKind]
    domain: AnalysisDomain
    root: LogicalRootHandle

    def read(self, dimension: Ref[DimensionKind]) -> J1Read:
        bound = _member_dimension(self, dimension)
        root = self.context._node(
            "read",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(dimension.path, bound.source_column),
        )
        return J1Read(self, bound, root)

    def group_by(self, dimension: Ref[DimensionKind]) -> J1Group:
        bound = _member_dimension(self, dimension)
        field = _make_field_id("dimension." + dimension.path)
        root = self.context._node(
            "group",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(dimension.path, bound.source_column),
        )
        return J1Group(self, bound, _group_domain(self.domain, (field,)), root)

    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind],
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> J1Observed:
        return _observe(self, self.domain, self.root, metric, during, via, coordinates)


@dataclass(frozen=True, slots=True, eq=False)
class J1CategoryField:
    root: LogicalRootHandle
    dimension: TargetDimensionContract

    def eq(self, value: str) -> J1Predicate:
        if type(value) is not str:
            raise _reject(
                "string category value",
                type(value).__name__,
                repair="Use a declared category value.",
            )
        return J1Predicate(self.root, self.dimension, value)


@dataclass(frozen=True, slots=True, eq=False)
class J1Predicate:
    root: LogicalRootHandle
    dimension: TargetDimensionContract
    value: str


@dataclass(frozen=True, slots=True, eq=False)
class J1Read:
    members_input: J1Members
    dimension: TargetDimensionContract
    root: LogicalRootHandle

    @property
    def value(self) -> J1CategoryField:
        return J1CategoryField(self.root, self.dimension)

    def where(self, predicate: J1Predicate) -> J1SelectedCategory:
        if type(predicate) is not J1Predicate or predicate.root is not self.root:
            raise _reject(
                "predicate on this exact read node",
                "foreign predicate",
                repair="Build the predicate from this read.value handle.",
            )
        root = self.members_input.context._node(
            "where",
            entity=self.members_input.entity.path,
            input_root=self.root,
            parameters=(self.dimension.ref.path, predicate.value),
            requirements=("strict_category_cell@v1",),
        )
        return J1SelectedCategory(self.members_input, root)

    def group_by(self) -> J1Group:
        field = _make_field_id("dimension." + self.dimension.ref.path)
        root = self.members_input.context._node(
            "group",
            entity=self.members_input.entity.path,
            input_root=self.root,
            parameters=(self.dimension.ref.path, self.dimension.source_column),
        )
        return J1Group(
            self.members_input,
            self.dimension,
            _group_domain(self.members_input.domain, (field,)),
            root,
        )


@dataclass(frozen=True, slots=True, eq=False)
class J1SelectedCategory:
    members_input: J1Members
    root: LogicalRootHandle

    def members(self) -> J1Members:
        original = self.members_input
        return J1Members(original.context, original.entity, original.domain, self.root)


@dataclass(frozen=True, slots=True, eq=False)
class J1Group:
    members_input: J1Members
    dimension: TargetDimensionContract
    domain: AnalysisDomain
    root: LogicalRootHandle

    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind],
    ) -> J1Observed:
        return _observe(self.members_input, self.domain, self.root, metric, during, via, ())


@dataclass(frozen=True, slots=True, eq=False)
class J1Observed:
    context: J1Context
    entity: Ref[EntityKind]
    domain: AnalysisDomain
    quantity: QuantityState
    root: LogicalRootHandle
    metric: Ref[MetricKind]
    plan: MetricComponentPlan
    coordinates: tuple[Ref[DimensionKind], ...]

    def group_by(self, dimension: Ref[DimensionKind]) -> J1Observed:
        if type(dimension) is not Ref or dimension not in self.coordinates:
            raise _reject(
                "an observed contribution coordinate",
                repr(dimension),
                repair="Name this Dimension in observe(coordinates=(...,)) first.",
            )
        field = _make_field_id("contribution." + dimension.path)
        domain = _group_domain(self.domain, (field,))
        root = self.context._node(
            "group",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(dimension.path, "contribution"),
        )
        quantity = _observed_quantity(
            domain, "metric:" + self.metric.path, (field,), self.plan.required_parts
        )
        return J1Observed(
            self.context,
            self.entity,
            domain,
            quantity,
            root,
            self.metric,
            self.plan,
            self.coordinates,
        )

    def rollup(self) -> J1Observed:
        if self.plan.method != "sum" or self.plan.required_parts != J1_SUM_PARTS:
            raise _reject(
                "sum observation with original retained state",
                self.plan.method,
                repair="Observe a builder-backed sum with complete state.",
            )
        domain = self.domain if self.domain.kind == "group" else _singleton_domain(self.domain)
        root = self.context._node(
            "rollup",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(self.metric.path, self.plan.required_parts),
            requirements=("contribution_partition@v1", "complete_coverage@v1"),
        )
        quantity = _observed_quantity(
            domain, "metric:" + self.metric.path, (), self.plan.required_parts
        )
        return J1Observed(
            self.context, self.entity, domain, quantity, root, self.metric, self.plan, ()
        )

    def summarize(self, method: Literal["sum", "count", "mean"]) -> J1Statistic:
        """Construct a new current-row statistic, distinct from Metric state."""
        if method not in ("sum", "count", "mean"):
            raise _reject(
                "current-row sum, count or mean",
                method,
                repair="Choose one registered current-row method.",
            )
        parts = {"sum": ("sum",), "count": ("count",), "mean": ("sum", "count")}[method]
        target = _singleton_domain(self.domain)
        quantity = _row_statistic_quantity(
            self.domain, target, self.root.definition_fingerprint, method, parts
        )
        root = self.context._node(
            "summarize",
            entity=self.entity.path,
            input_root=self.root,
            parameters=(method, self.root.definition_fingerprint),
            requirements=("strict_current_row_cell@v1",) if method != "count" else (),
        )
        return J1Statistic(self.context, target, quantity, root, method)


@dataclass(frozen=True, slots=True, eq=False)
class J1Statistic:
    context: J1Context
    domain: AnalysisDomain
    quantity: QuantityState
    root: LogicalRootHandle
    method: str


def _observe(
    members: J1Members,
    domain: AnalysisDomain,
    input_root: LogicalRootHandle,
    metric: Ref[MetricKind],
    during: TimeScope,
    via: Ref[RelationshipKind],
    coordinates: tuple[Ref[DimensionKind], ...],
) -> J1Observed:
    if type(metric) is not Ref or metric.kind is not SemanticKind.METRIC:
        raise _reject("Metric Ref", type(metric).__name__, repair="Pass ms.ref.metric(...).")
    if not isinstance(during, TimeScope):
        raise _reject(
            "fixed TimeScope",
            type(during).__name__,
            repair="Use mv.time_scope(start=..., end=...).",
        )
    if type(via) is not Ref or via.kind is not SemanticKind.RELATIONSHIP:
        raise _reject(
            "Relationship Ref", type(via).__name__, repair="Pass ms.ref.relationship(...)."
        )
    relation = members.context.registry.relationships.get(via.path)
    if relation is None or relation.to_entity != members.entity.path:
        raise _reject(
            "declared contribution-to-member Relationship",
            via.path,
            repair="Choose the exact directed Relationship from the Metric root to this Entity.",
        )
    declaration = members.context.registry.metrics.get(metric.path)
    if declaration is None or declaration.aggregation != "sum":
        raise _reject(
            "builder-backed sum with retained component state",
            metric.path,
            repair="Declare this Metric with ms.aggregate(..., agg='sum').",
        )
    normalized = normalize_target_metric(
        members.context.registry, metric.path, sidecar=members.context.sidecar
    )
    plan = derive_metric_components(normalized)
    if (
        plan.method != "sum"
        or plan.required_parts != J1_SUM_PARTS
        or len(normalized.computation_roots) != 1
    ):
        raise _reject(
            "builder-backed single-root sum", plan.method, repair="Use an admitted J1 sum Metric."
        )
    root_entity = normalized.computation_roots[0].path
    if relation.from_entity != root_entity or not path_is_functional(
        members.context.registry, root_entity, (via.path,)
    ):
        raise _reject(
            "single-valued declared path from contribution to member",
            via.path,
            repair="Use a many-to-one Relationship with declared compatible keys.",
        )
    if (
        normalized.event_time_dimension is None
        or normalized.event_time_dimension.path not in members.context.registry.dimensions
    ):
        raise _reject(
            "sum builder with declared event time",
            metric.path,
            repair="Declare ms.aggregate(..., agg='sum', time=EventTime).",
        )
    if (
        type(normalized.authoring_additivity) is not AdditiveAllV1
        or normalized.authoring_additivity.exceptions
    ):
        raise _reject(
            "versioned additive-all Metric support",
            metric.path,
            repair="Declare the supported contribution additivity on the Measure.",
        )
    if normalized.null_policy is not None and normalized.null_policy.kind != "ignore":
        raise _reject(
            "authored ignore-Null input policy",
            normalized.null_policy.kind,
            repair="Declare nulls=ms.nulls.ignore() for this J1 sum.",
        )
    if normalized.empty_policy is not None and normalized.empty_policy.kind != "null":
        raise _reject(
            "authored empty-Null contribution policy",
            normalized.empty_policy.kind,
            repair="Declare empty=ms.empty.null() for this J1 sum.",
        )
    if plan.null_rule != "ignore_null_inputs" or plan.empty_rule != "null":
        raise _reject(
            "sum graph compatible with ignore-Null and empty-Null policy",
            metric.path,
            repair="Use a sum graph whose structural value rules match the declared J1 policy.",
        )
    if type(coordinates) is not tuple or len(set(coordinates)) != len(coordinates):
        raise _reject(
            "unique coordinate refs",
            "invalid coordinates",
            repair="Pass a tuple of unique Dimensions.",
        )
    coordinate_fields: list[DatasetFieldId] = []
    for coordinate in coordinates:
        if type(coordinate) is not Ref or coordinate.kind is not SemanticKind.DIMENSION:
            raise _reject(
                "Dimension coordinate",
                type(coordinate).__name__,
                repair="Use a categorical Dimension Ref.",
            )
        bound = normalize_target_dimension(members.context.registry, coordinate.path)
        if bound.entity_ref.path != root_entity:
            raise _reject(
                "coordinate on the contribution root",
                coordinate.path,
                repair="Choose a Dimension on the Metric computation root.",
            )
        coordinate_fields.append(_make_field_id("contribution." + coordinate.path))
    quantity = _observed_quantity(
        domain, "metric:" + metric.path, tuple(coordinate_fields), plan.required_parts
    )
    root = members.context._node(
        "observe",
        entity=members.entity.path,
        input_root=input_root,
        parameters=(
            metric.path,
            normalized.dependency_fingerprint,
            via.path,
            during.model_dump_json(),
            tuple(item.path for item in coordinates),
        ),
        dependency="metric:" + metric.path,
        requirements=("complete_coverage@v1", "contribution_partition@v1"),
    )
    return J1Observed(
        members.context, members.entity, domain, quantity, root, metric, plan, coordinates
    )
