"""Typed source and fixed relation operations for the existing public receivers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, TypeAlias

from marivo._temporal import BeforeEndBoundary, TimeScope
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    MethodNode,
    Node,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    Coordinate,
    CoordinateStatePart,
    DomainSignature,
    ObservedQuantity,
    OriginalStatePart,
    RolledQuantity,
    RowStatisticQuantity,
    SubjectPart,
    part_role,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AssociationScore,
    AttachCategory,
    BindProject,
    CellDerive,
    CompleteGroups,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    OriginalReduce,
    PartsTransport,
    RowState,
)
from marivo.analysis.core.time_grid import BoundTimeGrid, GridPoint
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_composition import combine_observations
from marivo.analysis.materialization.graph_dataset import GraphDataset
from marivo.analysis.materialization.graph_fields import RootRoutesValue
from marivo.analysis.materialization.graph_members import MemberGraph, construct_members
from marivo.analysis.materialization.graph_observation import (
    normalize_metric_input,
    observe_linear_members,
    observe_members,
    observe_ratio_members,
)
from marivo.analysis.materialization.graph_protocol import (
    NODE,
    digest,
    encode,
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
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.validator import Registry


def _resolves_linear(live: LiveBinding, metric: Ref[MetricKind] | RuntimeMetricExpr) -> bool:
    """Report whether an input resolves to a signed linear combination."""
    from marivo.semantic.metric_graph import LinearNodeV1

    contract = normalize_metric_input(live.graph.registry, metric, sidecar=live.sidecar)
    roots = contract.graph.roots
    return any(
        isinstance(record.node, LinearNodeV1) and record.node_id in roots
        for record in contract.graph.nodes
    )


def _reject(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="one qualified typed relation with exact Session, member and method binding",
        received=received,
        repair="Use matching logical inputs or exact Artifacts from this Session.",
        location="analysis.graph_relation",
    )


def _shared_root(first: Node, second: Node) -> Node:
    """Share exact nodes, including a read's unbound temporal source shape."""
    known = {node.identity: node for node in topology(first)}
    for node in topology(second):
        prior = known.get(node.identity)
        if (
            isinstance(node, SourceLeaf)
            and isinstance(prior, SourceLeaf)
            and isinstance(node.definition.shape.time, NoTime)
        ):
            node = replace(
                node,
                definition=replace(
                    node.definition,
                    shape=replace(node.definition.shape, time=prior.definition.shape.time),
                ),
            )
        if isinstance(node, MethodNode):
            sources = tuple(known[source.identity] for source in node.sources)
            if any(not isinstance(source, SourceLeaf) for source in sources):
                raise _reject("shared source dependency is not a SourceLeaf")
            node = replace(
                node,
                inputs=tuple(Edge(edge.role, known[edge.node.identity]) for edge in node.inputs),
                sources=tuple(source for source in sources if isinstance(source, SourceLeaf)),
            )
        if prior is not None:
            if encode(prior, NODE) != encode(node, NODE):
                raise _reject("shared node identity has conflicting frozen definitions")
            continue
        known[node.identity] = node
    return known[second.identity]


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

    def each(self, grid: BoundTimeGrid) -> Relation:
        from marivo.analysis.core.rules import TimeProduct

        domain = self.root.signature.domain
        owner = domain.instance_key[0].entity_ref
        coordinate = Coordinate(owner, "time:" + grid.identity, "anchor")
        target = replace(
            domain,
            instance_key=(*domain.instance_key, coordinate),
            target_key=(*domain.target_key, coordinate),
            definition_id=domain.definition_id + ":time:" + grid.identity,
            correspondence=None,
            time_grid=grid,
        )
        root = method_node(
            (self._edge(),), TimeProduct(target, "time_product"), value_type=self.root.value_type
        )
        return self._with(root)

    def read(
        self,
        dimension: Ref[DimensionKind] | Ref[MeasureKind] | Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridPoint | None = None,
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

    def where(self, predicate: ValuePredicate, dependency: Relation | None = None) -> Relation:
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
            (self._edge(),)
            if dependency is None
            else (self._edge(), Edge("quantity", _shared_root(self.root, dependency.root))),
            PartsTransport(
                "where",
                self.root.signature.domain,
                tuple(part_role(part) for part in self.root.signature.parts),
                True,
                (predicate,),
                field_kind,
                dependency is not None,
                self.classification_coordinate() if field_kind == "dimension" else None,
            ),
            value_type=self.root.value_type,
        )
        result = self._with(root)
        return result._with_sources(dependency) if dependency is not None else result

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
        metric: Ref[MetricKind] | RuntimeMetricExpr,
        *,
        during: TimeScope | BoundTimeGrid | None,
        via: Ref[RelationshipKind] | tuple[Ref[RelationshipKind], ...],
        coordinates: tuple[Ref[DimensionKind], ...] = (),
        at: datetime | GridPoint | None = None,
    ) -> Relation:
        live = self._live()
        graph = observe_members(
            live.graph,
            metric,
            during=during,
            at=at,
            via=via,
            coordinates=coordinates,
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
        )
        return Relation(self.runtime, graph.root, replace(live, graph=graph))

    def resolves_multiple_components(self, metric: Ref[MetricKind] | RuntimeMetricExpr) -> bool:
        """Report whether an input binds more than one canonical occurrence."""
        live = self._live()
        contract = normalize_metric_input(live.graph.registry, metric, sidecar=live.sidecar)
        return len(contract.components) > 1

    def observe_routes(
        self,
        metric: Ref[MetricKind] | RuntimeMetricExpr,
        *,
        during: TimeScope | BoundTimeGrid | None,
        paths: tuple[tuple[Ref[RelationshipKind], ...], ...],
        coordinates: tuple[Ref[DimensionKind], ...] = (),
        at: datetime | GridPoint | None = None,
    ) -> Relation:
        """Observe an ordered multi-root quantity under explicitly bound routes."""
        live = self._live()
        if _resolves_linear(live, metric):
            linear = observe_linear_members(
                live.graph,
                metric,
                during=during,
                at=at,
                paths=paths,
                coordinates=coordinates,
                sidecar=live.sidecar,
                report_timezone=live.report_timezone,
            )
            return Relation(self.runtime, linear.root, replace(live, graph=linear))
        graph = observe_ratio_members(
            live.graph,
            metric,
            during=during,
            at=at,
            paths=paths,
            coordinates=coordinates,
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
        )
        return Relation(self.runtime, graph.root, replace(live, graph=graph))

    def observe_ratio(
        self,
        metric: Ref[MetricKind] | RuntimeMetricExpr,
        *,
        during: TimeScope | BoundTimeGrid | None,
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

    def rollup(
        self, *coordinates: Ref[DimensionKind] | Ref[EntityKind] | BoundTimeGrid
    ) -> Relation:
        signature = self.root.signature
        quantity = signature.quantity
        if not isinstance(quantity, (ObservedQuantity, RolledQuantity)):
            raise _reject("rollup requires original Metric state")
        state = next((p for p in signature.parts if isinstance(p, OriginalStatePart)), None)
        if state is None:
            raise _reject("original components are absent")
        methods: dict[
            str,
            Literal[
                "sum",
                "sum_zero",
                "mean",
                "min",
                "max",
                "count",
                "ratio",
                "weighted_mean",
                "linear",
                "fold",
            ],
        ] = {
            "min@v1": "min",
            "max@v1": "max",
            "sum@v1": "sum",
            "fold@v1": "fold",
            "sum_zero@v1": "sum_zero",
            "count@v1": "count",
            "mean@v1": "mean",
            "ratio@v1": "ratio",
            "weighted_mean@v1": "weighted_mean",
            "linear@v1": "linear",
        }
        if state.method_version not in methods:
            raise _reject("original method is not qualified")
        selected: list[Coordinate] = []
        time_mapping: tuple[tuple[str, str], ...] = ()
        target_grid: BoundTimeGrid | None = None
        for coordinate in coordinates:
            if isinstance(coordinate, BoundTimeGrid):
                from marivo.analysis.core.time_grid import coarsening

                source_grid = signature.domain.time_grid
                if source_grid is None:
                    raise _reject("time grouping needs a retained grid")
                if coordinate != source_grid:
                    time_mapping = coarsening(source_grid, coordinate)
                target_grid = coordinate
                selected.extend(
                    replace(c, field="time:" + coordinate.identity)
                    for c in signature.domain.instance_key
                    if c.role == "anchor"
                )
                continue
            # Entity references retain their entire composite identity.
            matches = tuple(
                c
                for c in signature.domain.instance_key
                if (
                    c.entity_ref == coordinate and c.role == "identity"
                    if coordinate.kind == "entity"
                    else c.field == coordinate.path
                )
            )
            if not matches:
                retained = next(
                    (p for p in signature.parts if isinstance(p, CoordinateStatePart)), None
                )
                matches = (
                    ()
                    if retained is None
                    else tuple(c for c in retained.coordinates if c.field == coordinate.path)
                )
            if not matches or (coordinate.kind == "dimension" and len(matches) != 1):
                raise _reject("group key was not uniquely retained with this relation")
            selected.extend(matches)
        keys = tuple(selected)
        if len(set(keys)) != len(keys):
            raise _reject("group keys repeat a retained coordinate")
        target = DomainSignature(
            signature.domain.binding,
            "group" if keys else "singleton",
            keys,
            keys,
            digest("group:" + repr(keys) + self.root.fingerprint),
            time_grid=target_grid,
        )
        return self._with(
            method_node(
                (self._edge(),),
                OriginalReduce(
                    target,
                    "source.contribution_partition@v1",
                    "source.complete_coverage@v1",
                    methods[state.method_version],
                    keys,
                    time_mapping,
                ),
                value_type=self.root.value_type,
            )
        )

    def summarize(
        self,
        method: Literal["sum", "min", "max", "mean", "count", "count_defined"],
        *,
        coordinates: tuple[Coordinate, ...] = (),
    ) -> Relation:
        definition = digest("row." + method + self.root.fingerprint)
        domain = DomainSignature(
            self.root.signature.domain.binding,
            "group" if coordinates else "singleton",
            coordinates,
            coordinates,
            definition,
        )
        value_type = (
            ScalarType("int64")
            if method in ("count", "count_defined")
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
                    "count_all"
                    if method == "count"
                    else "defined_only"
                    if method == "count_defined"
                    else "strict",
                    numeric_check_id=(
                        None
                        if method == "count"
                        else "source.cell_policy@v1"
                        if method == "count_defined"
                        else "source.finite_numeric@v1"
                    ),
                ),
                value_type=value_type,
            )
        )

    def rollup_statistic(self, *coordinates: Ref[DimensionKind] | Ref[EntityKind]) -> Relation:
        quantity = self.root.signature.quantity
        if not isinstance(quantity, RowStatisticQuantity):
            raise _reject("row rollup requires a retained RowStatistic")
        from marivo.analysis.core.rules import RowMethod

        methods: dict[str, RowMethod] = {
            "row.sum@v1": "sum",
            "row.mean@v1": "mean",
            "row.min@v1": "min",
            "row.max@v1": "max",
            "row.count@v1": "count",
            "row.count_defined@v1": "count_defined",
        }
        method = methods.get(quantity.method_version)
        if method is None or method == "weighted_mean":
            raise _reject("statistic method is not qualified for retained merge")
        keys: list[Coordinate] = []
        for reference in coordinates:
            matched = tuple(
                c
                for c in self.root.signature.domain.instance_key
                if (
                    c.entity_ref == reference and c.role == "identity"
                    if reference.kind == "entity"
                    else c.field == reference.path
                )
            )
            if not matched or (reference.kind == "dimension" and len(matched) != 1):
                raise _reject("statistic group coordinate was not uniquely retained")
            keys.extend(matched)
        if len(keys) != len(set(keys)):
            raise _reject("statistic grouping repeats a coordinate")
        domain = DomainSignature(
            self.root.signature.domain.binding,
            "group" if keys else "singleton",
            tuple(keys),
            tuple(keys),
            digest("row.rollup:" + repr(keys) + self.root.fingerprint),
        )
        return self._with(
            method_node(
                (self._edge(),),
                RowState(method, domain, quantity.definition_id, quantity.value_policy, merge=True),
                value_type=self.root.value_type,
            )
        )

    def classification_coordinate(self) -> Coordinate:
        params = self.definition.parameters
        if isinstance(params, PartsTransport) and params.classification is not None:
            return params.classification
        if (
            isinstance(params, BindProject)
            and params.field_contract is not None
            and params.ref.kind == "dimension"
        ):
            return Coordinate(params.field_owner, params.ref.path, "group")
        raise _reject("classification requires an explicit retained Dimension identity")

    def _with_sources(self, dependency: Relation) -> Relation:
        if isinstance(self.binding, LiveBinding) and isinstance(dependency.binding, LiveBinding):
            sources = {
                leaf.identity: (schema, leaf)
                for schema, leaf in (*dependency.binding.graph.sources, *self.binding.graph.sources)
            }
            return replace(
                self,
                binding=replace(
                    self.binding,
                    graph=replace(self.binding.graph, sources=tuple(sources.values())),
                ),
            )
        return self

    def attach_category(self, category: Relation) -> Relation:
        coordinate = category.classification_coordinate()
        source = self.root.signature.domain
        target = replace(
            source,
            instance_key=(*source.instance_key, coordinate),
            definition_id=digest(source.definition_id + category.root.fingerprint),
        )
        node = method_node(
            (self._edge(), Edge(category._edge().role, _shared_root(self.root, category.root))),
            AttachCategory(
                coordinate,
                target,
                category.root.signature.domain.instance_key != source.instance_key,
            ),
            value_type=self.root.value_type,
        )
        return self._with(node)._with_sources(category)

    def complete_groups(self, target: Relation) -> Relation:
        source_domain = self.root.signature.domain
        definition_id = digest("target:" + self.root.fingerprint + target.root.fingerprint)
        domain = replace(
            source_domain,
            definition_id=definition_id,
            correspondence=replace(source_domain.correspondence, target_definition_id=definition_id)
            if source_domain.correspondence is not None
            else None,
        )
        node = method_node(
            (self._edge(), Edge(target._edge().role, _shared_root(self.root, target.root))),
            CompleteGroups(domain),
            value_type=self.root.value_type,
        )
        return self._with(node)._with_sources(target)

    def group_domain(self, coordinates: tuple[Coordinate, ...]) -> Relation:
        source = self.root.signature.domain
        target = DomainSignature(
            source.binding,
            "group" if coordinates else "singleton",
            coordinates,
            coordinates,
            digest("target-groups:" + repr(coordinates) + self.root.fingerprint),
        )
        return self._with(
            method_node(
                (self._edge(),),
                MapCorrespond("group_keys", target),
                value_type=self.root.value_type,
            )
        )
