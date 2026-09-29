"""Typed source and fixed relation operations for the existing public receivers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, TypeAlias

from marivo._temporal import BeforeEndBoundary, TimeScope
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, FixedLeaf, MethodNode, Node, method_node, topology
from marivo.analysis.core.model import (
    Coordinate,
    CoordinateStatePart,
    DomainSignature,
    ObservedQuantity,
    OriginalStatePart,
    RolledQuantity,
    SubjectPart,
    part_role,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AssociationScore,
    BindProject,
    CellDerive,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    OriginalReduce,
    PartsTransport,
    RowState,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_composition import combine_observations
from marivo.analysis.materialization.graph_dataset import GraphDataset
from marivo.analysis.materialization.graph_fields import RootRoutesValue
from marivo.analysis.materialization.graph_members import MemberGraph, construct_members
from marivo.analysis.materialization.graph_observation import observe_members, observe_ratio_members
from marivo.analysis.materialization.graph_protocol import (
    digest,
    fixed_signature,
    validate_descriptor,
)
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
from marivo.analysis.refs import ArtifactRef
from marivo.refs import (
    DimensionKind,
    EntityKind,
    MeasureKind,
    MetricKind,
    Ref,
    RelationshipKind,
    TimeDimensionKind,
)
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry


def _reject(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="one qualified typed relation with exact Session, member and method binding",
        received=received,
        repair="Use matching logical inputs or exact Artifacts from this Session.",
        location="analysis.graph_relation",
    )


@dataclass(frozen=True, slots=True)
class LiveBinding:
    graph: MemberGraph
    sidecar: CompiledExpressionSidecar
    report_timezone: str


@dataclass(frozen=True, slots=True)
class FrozenBinding:
    definition: MethodNode


Binding: TypeAlias = LiveBinding | FrozenBinding


@dataclass(frozen=True, slots=True, eq=False)
class Relation:
    runtime: DatasetRuntime
    root: Node
    binding: Binding

    @classmethod
    def members(
        cls,
        runtime: DatasetRuntime,
        registry: Registry,
        sidecar: CompiledExpressionSidecar,
        report_timezone: str,
        entity: Ref[EntityKind],
        *,
        at: datetime | BeforeEndBoundary | None = None,
    ) -> Relation:
        graph = construct_members(runtime, registry, entity, at=at, report_timezone=report_timezone)
        return cls(runtime, graph.root, LiveBinding(graph, sidecar, report_timezone))

    @classmethod
    def restore(cls, dataset: GraphDataset) -> Relation:
        dataset.verified()
        descriptor = dataset.artifact.descriptor
        definition = validate_descriptor(descriptor)
        if not isinstance(definition, MethodNode):
            raise _reject("Artifact has no typed method result")
        root = FixedLeaf(
            ArtifactRef(ref=dataset.artifact.artifact_ref),
            descriptor.definition_fingerprint,
            fixed_signature(descriptor),
            definition.value_type,
            FixedShape(NoTime()),
        )
        return cls(dataset.runtime, root, FrozenBinding(definition))

    @property
    def definition(self) -> MethodNode:
        if isinstance(self.root, MethodNode):
            return self.root
        if isinstance(self.binding, FrozenBinding):
            return self.binding.definition
        raise _reject("relation has no typed definition")

    def _with(self, root: MethodNode) -> Relation:
        binding: Binding = (
            replace(self.binding, graph=replace(self.binding.graph, root=root))
            if isinstance(self.binding, LiveBinding)
            else FrozenBinding(root)
        )
        return Relation(self.runtime, root, binding)

    def _live(self) -> LiveBinding:
        if not isinstance(self.binding, LiveBinding):
            raise _reject("fixed selected members plus live Metric or Dimension")
        return self.binding

    def _edge(self) -> Edge:
        return Edge("subject" if self.root.signature.quantity is None else "quantity", self.root)

    def execute(self) -> GraphDataset:
        if isinstance(self.binding, LiveBinding):
            try:
                artifact = self.binding.graph.execute()
            except AnalysisError:
                raise
            except Exception as error:
                raise MaterializationError(
                    expected="an available source satisfying the admitted graph contract",
                    received="source execution failed",
                    repair="Inspect this Run, restore the source, and retry the logical relation.",
                    stage="graph_source",
                    run_ref=self.runtime.last_run_ref,
                ) from error
        else:
            artifact = self.runtime._execute_graph(
                self.root,
                tuple(
                    RouteChoice(node.identity, "artifact_python")
                    for node in topology(self.root)
                    if isinstance(node, MethodNode)
                ),
            )
        return GraphDataset(self.runtime, artifact)

    def read(
        self,
        dimension: Ref[DimensionKind] | Ref[MeasureKind] | Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | None = None,
        via: Ref[RelationshipKind] | RootRoutesValue | None = None,
    ) -> Relation:
        live = self._live()
        graph = live.graph.read(
            dimension, at=at, via=via, sidecar=live.sidecar, report_timezone=live.report_timezone
        )
        return Relation(self.runtime, graph.root, replace(live, graph=graph))

    def selected_members(self) -> Relation:
        if not any(isinstance(part, SubjectPart) for part in self.root.signature.parts):
            raise _reject("current result has no verified Subject map")
        subject = next(part for part in self.root.signature.parts if isinstance(part, SubjectPart))
        domain = self.root.signature.domain
        if subject.injective and subject.subject_key == domain.instance_key:
            parameters: PartsTransport | MapCorrespond = PartsTransport(
                "projection", domain, ("subject",), False
            )
        else:
            target = DomainSignature(
                domain.binding,
                "entity",
                subject.subject_key,
                subject.subject_key,
                f"entity:{subject.entity_ref.path}",
            )
            parameters = MapCorrespond("subjects", target, "source.unique_key@v1")
        root = method_node((self._edge(),), parameters, value_type=self.root.value_type)
        return self._with(root)

    def where(self, predicate: ValuePredicate) -> Relation:
        if predicate.binding != self.root.signature.domain.binding:
            raise _reject("predicate belongs to a different member realization")
        definition = self.definition.parameters
        field_kind: Literal["measure", "dimension", "time_dimension"] | None = None
        if isinstance(definition, BindProject) and definition.field_contract is not None:
            kind = definition.field_contract.ref.kind
            if kind == "measure":
                field_kind = "measure"
            elif kind == "dimension":
                field_kind = "dimension"
            elif kind == "time_dimension":
                field_kind = "time_dimension"
        elif isinstance(definition, PartsTransport):
            field_kind = definition.field_kind
        root = method_node(
            (self._edge(),),
            PartsTransport(
                "where",
                self.root.signature.domain,
                tuple(part_role(part) for part in self.root.signature.parts),
                True,
                (predicate,),
                field_kind,
            ),
            value_type=self.root.value_type,
        )
        return self._with(root)

    def group_members(self, dimension: Ref[DimensionKind]) -> Relation:
        live = self._live()
        graph = live.graph.group_by_value(dimension)
        return Relation(self.runtime, graph.root, replace(live, graph=graph))

    def group_read(self) -> Relation:
        self._live()
        params = self.definition.parameters
        if not isinstance(params, BindProject) or params.field_contract is None:
            raise _reject("grouping requires an exact member Dimension read")
        field = params.field_contract
        domain = self.root.signature.domain
        key = (Coordinate(params.field_owner, params.ref.path, "group"),)
        target = DomainSignature(domain.binding, "group", key, key, "group:" + field.ref.path)
        return self._with(
            method_node(
                (self._edge(),),
                MapCorrespond("group", target, "source.group_mapping@v1"),
                value_type=self.root.value_type,
            )
        )

    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind],
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> Relation:
        live = self._live()
        graph = observe_members(
            live.graph,
            metric,
            during=during,
            via=via,
            coordinates=coordinates,
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
        )
        return Relation(self.runtime, graph.root, replace(live, graph=graph))

    def observe_ratio(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        paths: tuple[tuple[Ref[RelationshipKind], ...], tuple[Ref[RelationshipKind], ...]],
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> Relation:
        live = self._live()
        graph = observe_ratio_members(
            live.graph,
            metric,
            during=during,
            paths=paths,
            coordinates=coordinates,
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
        )
        return Relation(self.runtime, graph.root, replace(live, graph=graph))

    def combine(self, other: Relation, method: Literal["difference", "spearman"]) -> Relation:
        if (
            self.runtime.session_ref != other.runtime.session_ref
            or self.runtime.store.store_id != other.runtime.store.store_id
        ):
            raise _reject("different Session owners")
        if method == "spearman" and any(
            isinstance(p, CoordinateStatePart)
            for relation in (self, other)
            for p in relation.root.signature.parts
        ):
            raise _reject(
                "Spearman requires one observation per member without contribution coordinates"
            )
        if isinstance(self.binding, LiveBinding) and isinstance(other.binding, LiveBinding):
            graph = combine_observations(self.binding.graph, other.binding.graph, method)
            return Relation(self.runtime, graph.root, replace(self.binding, graph=graph))
        if isinstance(self.binding, LiveBinding) or isinstance(other.binding, LiveBinding):
            raise _reject("mixed live and materialized dependencies")
        current, baseline = self.definition, other.definition
        if not isinstance(current.parameters, (ObserveMetric, ObserveCount)) or not isinstance(
            baseline.parameters, (ObserveMetric, ObserveCount)
        ):
            raise _reject("endpoints must retain their exact observation definitions")
        a, b = current.inputs[0].node, baseline.inputs[0].node
        if a.identity != b.identity or a.fingerprint != b.fingerprint:
            raise _reject("endpoints do not share the same frozen member node")
        if method == "difference" and (
            current.parameters.path != baseline.parameters.path
            or current.parameters.coordinates != baseline.parameters.coordinates
        ):
            raise _reject("comparison endpoints have different routes or coordinates")
        if method == "spearman" and (
            current.parameters.coordinates or baseline.parameters.coordinates
        ):
            raise _reject("Spearman requires exactly one observation per member")
        first, second = self.root.signature, other.root.signature
        left, right = first.quantity, second.quantity
        if (
            first.domain.binding != second.domain.binding
            or first.domain.instance_key != second.domain.instance_key
            or not isinstance(left, ObservedQuantity)
            or not isinstance(right, ObservedQuantity)
        ):
            raise _reject("endpoints lack one exact frozen member binding")
        definition = digest(method + self.root.fingerprint + other.root.fingerprint)
        if method == "difference":
            if (
                left.metric_ref != right.metric_ref
                or left.graph_fingerprint != right.graph_fingerprint
                or left.time_scope == right.time_scope
            ):
                raise _reject("comparison requires the same Metric and distinct windows")
            root = method_node(
                (Edge("current", self.root), Edge("baseline", other.root)),
                CellDerive(
                    "difference",
                    definition,
                    "strict",
                    left.unit,
                    left.time_scope,
                    "source.exact_pairing@v1",
                    "source.finite_numeric@v1",
                ),
                value_type=self.root.value_type,
            )
        else:
            domain = DomainSignature(first.domain.binding, "singleton", (), (), definition)
            root = method_node(
                (self._edge(), other._edge()),
                AssociationScore(
                    domain, definition, "source.exact_pairing@v1", "source.finite_numeric@v1"
                ),
                value_type=ScalarType("float64"),
            )
        return self._with(root)

    def rollup(self, coordinate: Ref[DimensionKind] | None = None) -> Relation:
        signature = self.root.signature
        quantity = signature.quantity
        if not isinstance(quantity, (ObservedQuantity, RolledQuantity)):
            raise _reject("rollup requires original Metric state")
        state = next((p for p in signature.parts if isinstance(p, OriginalStatePart)), None)
        if state is None:
            raise _reject("original components are absent")
        methods: dict[str, Literal["sum", "sum_zero", "count", "ratio"]] = {
            "sum@v1": "sum",
            "sum_zero@v1": "sum_zero",
            "count@v1": "count",
            "ratio@v1": "ratio",
        }
        if state.method_version not in methods:
            raise _reject("original method is not qualified")
        if coordinate is None:
            target = DomainSignature(
                signature.domain.binding,
                "singleton",
                (),
                (),
                digest("total:" + self.root.fingerprint),
            )
        else:
            retained = next(
                (
                    p
                    for p in signature.parts
                    if isinstance(p, CoordinateStatePart)
                    and any(c.field == coordinate.path for c in p.coordinates)
                ),
                None,
            )
            if retained is None:
                raise _reject("coordinate was not retained with this observation")
            key = tuple(c for c in retained.coordinates if c.field == coordinate.path)
            target = DomainSignature(
                signature.domain.binding,
                "group",
                key,
                key,
                digest("group:" + coordinate.path + self.root.fingerprint),
            )
        return self._with(
            method_node(
                (self._edge(),),
                OriginalReduce(
                    target,
                    "source.contribution_partition@v1",
                    "source.complete_coverage@v1",
                    methods[state.method_version],
                    coordinate,
                ),
                value_type=self.root.value_type,
            )
        )

    def summarize(self, method: Literal["sum", "mean", "count"]) -> Relation:
        definition = digest("row." + method + self.root.fingerprint)
        domain = DomainSignature(
            self.root.signature.domain.binding, "singleton", (), (), definition
        )
        value_type = (
            ScalarType("int64")
            if method == "count"
            else ScalarType("float64")
            if method == "mean"
            else self.root.value_type
        )
        return self._with(
            method_node(
                (self._edge(),),
                RowState(
                    method,
                    domain,
                    definition,
                    "count_all" if method == "count" else "strict",
                    numeric_check_id=None if method == "count" else "source.finite_numeric@v1",
                ),
                value_type=value_type,
            )
        )
