"""Private, source-free J1 construction over the existing Dataset logical roots."""

from __future__ import annotations

from dataclasses import dataclass

from marivo._temporal import TimeScope
from marivo.analysis.datasets.descriptors import (
    AnalysisDomain,
    DatasetFieldId,
    QuantityState,
    _canonical_digest,
    _entity_domain,
    _group_domain,
    _make_field_id,
    _make_shape_id,
    _observed_quantity,
    _singleton_domain,
    _StableIdRegistry,
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

_KINDS = ("members", "read", "where", "group", "observe", "rollup")
_IDS = _StableIdRegistry(
    families=frozenset({"dsl_j1"}),
    shapes=frozenset(("dsl_j1", kind, 1) for kind in _KINDS),
)
J1_SUM_PARTS = ("value.sum", "value.non_null_count", "value.row_count")


def _reject(expected: str, received: str, *, repair: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair=repair,
        location="dsl.j1",
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
        return _make_logical_root(
            session_id=self.session_id,
            store_id=self.store_id,
            shape_id=shape,
            row_contract_fingerprint="rc_" + _canonical_digest((kind, entity, parameters)),
            row_set_contract_fingerprint="rs_" + _canonical_digest((kind, entity)),
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
    if plan.null_rule != "ignore_null_inputs" or plan.empty_rule != "null":
        raise _reject(
            "registered ignore-null, empty-null sum policy",
            metric.path,
            repair="Use a sum builder with the first-round value policy.",
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
