"""Typed source and fixed relation operations for the existing public receivers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, TypeAlias

from marivo._temporal import BeforeEndBoundary, TimeScope
from marivo.analysis._cohort import AllInstances, AnyInstance, AtLeast, CohortRule
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    MethodNode,
    Node,
    SourceLeaf,
    method_node,
    retained_inclusion,
    retained_nodes,
    topology,
)
from marivo.analysis.core.model import (
    Coordinate,
    CoordinateStatePart,
    DerivedQuantity,
    DomainSignature,
    ObservedQuantity,
    OriginalStatePart,
    RolledQuantity,
    RowStatePart,
    RowStatisticQuantity,
    SubjectPart,
    part_role,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AnchorObserve,
    AssociationScore,
    AttachCategory,
    BindProject,
    CellDerive,
    CompleteGroups,
    FunnelAxesPrepare,
    JourneyRead,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    OccurrencePrepare,
    OriginalReduce,
    PartsTransport,
    RowState,
    TimeProduct,
)
from marivo.analysis.core.time_grid import BoundTimeGrid, GridPoint
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError, StatisticalErrorCode, StatisticalRelationError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_composition import (
    combine_observations,
    comparison_empty_rules,
    period_mapping,
    validate_time_comparison,
)
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
    digest,
    fixed_signature,
    validate_descriptor,
)
from marivo.analysis.materialization.graph_snapshot import same_node_definition
from marivo.analysis.methods.comparison import output_type
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    FixedShape,
    NoTime,
    ScalarType,
)
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
from marivo.semantic.ir import TargetRelationshipContract
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


def _shared_root(first: Node, second: Node, known: dict[str, Node] | None = None) -> Node:
    """Share exact nodes, including a read's unbound temporal source shape."""
    if known is None:
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
            if not same_node_definition(prior, node):
                raise _reject("shared node identity has conflicting frozen definitions")
            continue
        known[node.identity] = node
    return known[second.identity]


def _retained_definition(root: MethodNode, *, preserve_fixed: bool = False) -> MethodNode:
    """Isolate an Artifact's metadata closure without altering any definition hash.

    Source shape qualification may differ across snapshots of the same capture.
    Receipt data edges retain their original identities; this closure is evidence only.
    Preserve fixed leaves when reusing the executable Artifact input and its exact receipt.
    """
    from uuid import uuid4

    # Capture source identities are part of the frozen R7 authority and cannot be renamed.
    if any(
        isinstance(node, MethodNode)
        and isinstance(node.parameters, (OccurrencePrepare, FunnelAxesPrepare))
        for node in retained_nodes(root)
    ):
        return root
    known: dict[str, Node] = {}
    for original in retained_nodes(root):
        node = original
        if isinstance(node, MethodNode):
            node = replace(
                node,
                identity=uuid4().hex,
                inputs=tuple(Edge(edge.role, known[edge.node.identity]) for edge in node.inputs),
                sources=tuple(
                    source
                    for source in (known[source.identity] for source in node.sources)
                    if isinstance(source, SourceLeaf)
                ),
                retained_endpoints=tuple(
                    endpoint
                    for endpoint in (
                        known[endpoint.identity] for endpoint in node.retained_endpoints
                    )
                    if isinstance(endpoint, MethodNode)
                ),
            )
        elif not (preserve_fixed and isinstance(node, FixedLeaf)):
            node = replace(node, identity=uuid4().hex)
        # The original capture key is used only while rebuilding this isolated closure.
        known[original.identity] = node
    result = known[root.identity]
    assert isinstance(result, MethodNode) and result.fingerprint == root.fingerprint
    return result


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
        from marivo.analysis.materialization.graph_protocol import descriptor_plan

        captured_plan = descriptor_plan(descriptor, definition)
        captured_time = (
            captured_plan.physical_requirements[-1].key.shape.time
            if definition.signature.domain.time_grid is not None
            else NoTime()
        )
        root = FixedLeaf(
            ArtifactRef(ref=dataset.artifact.artifact_ref),
            descriptor.definition_fingerprint,
            fixed_signature(descriptor),
            definition.value_type,
            FixedShape(captured_time),
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
        if (
            isinstance(self.binding, FrozenBinding)
            and isinstance(self.root, FixedLeaf)
            and len(root.inputs) == 1
            and root.inputs[0].node is self.root
            and isinstance(root.parameters, (PartsTransport, OriginalReduce, RowState))
        ):
            root = replace(
                root,
                retained_endpoints=(self.definition,),
            )
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

    def _check_runs_input(
        self, operation: Literal["runs", "correlate", "forecast"] = "runs"
    ) -> None:
        from marivo.analysis.methods.deviation_physical import parse_type

        try:
            parse_type(self.root.value_type.name)
        except ValueError as error:
            raise StatisticalRelationError(
                code="r8.numeric_unqualified",
                operation=operation,
                method="time.runs@v1" if operation == "runs" else operation + "@v1",
                input_identity=self.root.fingerprint,
                expected="int64, float64 or Decimal numeric observation",
                received=self.root.value_type.name,
                repair="Use a numeric observation on its original complete grid.",
            ) from error
        roles = {part_role(p) for p in self.root.signature.parts}
        if not (
            roles & {"coverage", "grid_cells"} or {"current_endpoint", "baseline_endpoint"} <= roles
        ):
            raise StatisticalRelationError(
                code="r8.grid_incomplete",
                operation=operation,
                method="time.runs@v1" if operation == "runs" else operation + "@v1",
                input_identity=self.root.fingerprint,
                expected="captured original grid coverage parts",
                received=repr(tuple(sorted(roles))),
                repair="Use the original complete observation with its retained coverage; do not reconstruct missing fixed parts.",
            )
        grid = self.root.signature.domain.time_grid
        pending = [self.root, self.definition]
        visited: set[str] = set()
        selected = False
        while pending:
            node = pending.pop()
            if node.fingerprint in visited:
                continue
            visited.add(node.fingerprint)
            if not isinstance(node, MethodNode) or isinstance(node.parameters, TimeProduct):
                continue
            if isinstance(node.parameters, PartsTransport) and node.parameters.mode in (
                "where",
                "limit",
            ):
                selected = True
                break
            pending.extend(edge.node for edge in node.inputs)
            pending.extend(node.retained_endpoints)
        if grid is None or any(cell.partial for cell in grid.cells) or selected:
            raise StatisticalRelationError(
                code="r8.grid_incomplete",
                operation=operation,
                method="time.runs@v1" if operation == "runs" else operation + "@v1",
                input_identity=self.root.fingerprint,
                expected="the original complete time grid before selection",
                received="missing/partial grid or prior row selection",
                repair="Construct the statistical result before where; select its outputs afterwards.",
            )

    def runs(self, predicate: ValuePredicate, dependencies: tuple[Relation, ...]) -> Relation:
        from marivo.analysis.compiler.graph_plan import classify_inputs
        from marivo.analysis.core.rules import TimeRuns

        self._check_runs_input()
        edges = (self._edge(), *(item._edge() for item in dependencies))
        from pydantic import TypeAdapter

        identity = digest(
            canonical_json(
                [
                    self.root.fingerprint,
                    TypeAdapter(ValuePredicate).dump_json(predicate).decode(),
                    [item.root.fingerprint for item in dependencies],
                ]
            )
        )
        node = method_node(edges, TimeRuns(predicate, identity), value_type=ScalarType("int64"))
        if classify_inputs(node).kind == "mixed":
            raise _reject("runs cannot mix source and fixed predicate dependencies")
        result = self._with(node)
        for dependency in dependencies:
            result = result._with_sources(dependency)
        return result

    def run_field(self, field: Literal["start", "end", "count", "duration"]) -> Relation:
        from marivo.analysis.core.rules import TimeRunRead
        from marivo.analysis.methods.runs_physical import output_type

        return self._with(
            method_node((self._edge(),), TimeRunRead(field), value_type=output_type(field))
        )

    def deviation(
        self, method: Literal["zscore", "mad"], partitions: tuple[Relation, ...]
    ) -> Relation:
        from marivo.analysis.compiler.graph_plan import classify_inputs
        from marivo.analysis.core.rules import DeviationFit
        from marivo.analysis.methods.deviation_physical import parse_type

        def failure(
            code: Literal[
                "r8.input_identity", "r8.input_mode", "r8.numeric_unqualified", "r8.correspondence"
            ],
            expected: str,
            received: str,
            repair: str,
        ) -> StatisticalRelationError:
            return StatisticalRelationError(
                code=code,
                operation="deviation",
                method=f"deviation.{method}@v1",
                input_identity=self.root.fingerprint,
                expected=expected,
                received=received,
                repair=repair,
            )

        try:
            parse_type(self.root.value_type.name)
        except ValueError as error:
            raise failure(
                "r8.numeric_unqualified",
                "int64, float64 or Decimal(p,s)",
                self.root.value_type.name,
                "Use a numeric receiver with an admitted original type.",
            ) from error
        if method not in ("zscore", "mad"):
            raise failure(
                "r8.numeric_unqualified",
                "method='zscore' or method='mad'",
                repr(method),
                "Pass one of the two registered deviation methods.",
            )
        for partition in partitions:
            if partition.runtime is not self.runtime:
                raise failure(
                    "r8.input_identity",
                    self.runtime.session_ref,
                    partition.runtime.session_ref,
                    "Read partition categories in the receiver's Session.",
                )
            if partition.root.signature.domain != self.root.signature.domain:
                raise failure(
                    "r8.correspondence",
                    repr(self.root.signature.domain),
                    repr(partition.root.signature.domain),
                    "Read categories over the receiver's exact complete domain before deviation.",
                )
        identity = digest(
            canonical_json(
                [method, self.root.fingerprint, [item.root.fingerprint for item in partitions]]
            )
        )
        node = method_node(
            (self._edge(), *(item._edge() for item in partitions)),
            DeviationFit(method, self.root.value_type.name, identity),
            value_type=ScalarType("float64"),
        )
        if classify_inputs(node).kind == "mixed":
            raise failure(
                "r8.input_mode",
                "one source-only or fixed-only dependency closure",
                "mixed source and fixed dependencies",
                "Rebuild the categories and receiver in one input mode.",
            )
        result = self._with(node)
        for partition in partitions:
            result = result._with_sources(partition)
        return result

    def deviation_field(
        self, field: Literal["observed", "reference", "deviation", "score"]
    ) -> Relation:
        from marivo.analysis.core.model import FitInputsPart, require_part
        from marivo.analysis.core.rules import DeviationRead
        from marivo.analysis.methods.deviation_physical import output_type, parse_type

        retained = require_part(self.root.signature, "fit_inputs")
        assert isinstance(retained, FitInputsPart)
        return self._with(
            method_node(
                (self._edge(),),
                DeviationRead(retained.input_type, field),
                value_type=output_type(field, parse_type(retained.input_type)),
            )
        )

    def correlate(
        self,
        others: tuple[Relation, ...],
        method: Literal["pearson", "spearman", "kendall"],
        lag_range: range | None,
    ) -> Relation:
        from marivo.analysis.core.rules import AssociationFit
        from marivo.analysis.core.statistical_rules import corresponding_domains
        from marivo.analysis.methods.deviation_physical import parse_type

        inputs = (self, *others)
        identity = self.root.fingerprint

        def error(
            code: StatisticalErrorCode, expected: str, received: str, repair: str
        ) -> StatisticalRelationError:
            return StatisticalRelationError(
                code=code,
                operation="correlate",
                method="association." + method + "@v1",
                input_identity=identity,
                expected=expected,
                received=received,
                repair=repair,
            )

        if not 2 <= len(inputs) <= 16 or method not in ("pearson", "spearman", "kendall"):
            raise error(
                "r8.input_identity",
                "2..16 distinct numeric inputs and pearson/spearman/kendall",
                repr((len(inputs), method)),
                "Pass distinct corresponding numeric quantities and one registered correlation method.",
            )
        if len(
            {
                v.root.signature.quantity.definition_id
                if v.root.signature.quantity is not None
                else ""
                for v in inputs
            }
        ) != len(inputs):
            raise error(
                "r8.input_identity",
                "distinct quantity identities",
                "repeated quantity",
                "Pass each distinct corresponding quantity once.",
            )
        if any(v.runtime is not self.runtime for v in inputs):
            raise error(
                "r8.input_identity",
                "inputs from one Session",
                "foreign Session",
                "Reconstruct all inputs in the receiver Session.",
            )
        if lag_range is not None and (type(lag_range) is not range or not lag_range):
            raise error(
                "r8.correspondence",
                "a nonempty range or None",
                repr(lag_range),
                "Pass a nonempty signed integer range on the original time grid.",
            )
        if lag_range is not None and (
            (lag_range[-1] - lag_range[0]) // lag_range.step + 1 > 4096
            or any(not -(2**63) <= k < 2**63 for k in (lag_range[0], lag_range[-1]))
        ):
            raise error(
                "r8.candidate_ceiling",
                "signed int64 lags within 4096 candidates",
                repr(lag_range),
                "Reduce the explicitly requested lag range.",
            )
        if not self.root.signature.domain.instance_key:
            raise error(
                "r8.correspondence",
                "Entity/category/time statistical units",
                "Scalar",
                "Observe corresponding non-scalar quantities first.",
            )
        lags = (0,) if lag_range is None else tuple(lag_range)
        if len(inputs) * (len(inputs) - 1) // 2 * len(lags) > 4096:
            raise error(
                "r8.candidate_ceiling",
                "at most 4096 pair/lag/series candidates",
                str(len(inputs) * (len(inputs) - 1) // 2 * len(lags)),
                "Reduce the explicitly requested quantity or lag scope.",
            )
        for v in inputs:
            try:
                parse_type(v.root.value_type.name)
            except ValueError as invalid_type:
                raise error(
                    "r8.numeric_unqualified",
                    "original int64, float64 or Decimal quantity",
                    v.root.value_type.name,
                    "Use an admitted numeric quantity; Duration and Boolean are not statistical carriers.",
                ) from invalid_type
            if (
                not corresponding_domains(v.root.signature.domain, self.root.signature.domain)
                or v.root.signature.quantity is None
            ):
                raise error(
                    "r8.correspondence",
                    "exact common observation domain",
                    repr(v.root.signature.domain),
                    "Bind all quantities to one original complete observation domain.",
                )
            if v.root.signature.domain.time_grid is not None or lag_range is not None:
                v._check_runs_input("correlate")
        from marivo.analysis.compiler.graph_plan import classify_inputs

        modes = tuple(classify_inputs(v.root).kind for v in inputs)
        if len(set(modes)) != 1:
            raise error(
                "r8.input_mode",
                "one source or fixed closure",
                repr(modes),
                "Use source Logical inputs together, or retained fixed inputs together.",
            )
        known = {n.identity: n for n in topology(self.root)}
        edges = (
            self._edge(),
            *(Edge("quantity", _shared_root(self.root, v.root, known)) for v in others),
        )
        association_id = digest(
            repr((tuple(v.root.fingerprint for v in inputs), method, lags, lag_range is not None))
        )
        node = method_node(
            edges,
            AssociationFit(
                association_id,
                method,
                tuple(v.root.value_type.name for v in inputs),
                lags,
                lag_range is not None,
            ),
            value_type=ScalarType("float64"),
        )
        result = self._with(node)
        for other in others:
            result = result._with_sources(other)
        return result

    def association_field(self, field: Literal["coefficient", "selected"]) -> Relation:
        from marivo.analysis.core.rules import AssociationRead

        return self._with(
            method_node(
                (self._edge(),),
                AssociationRead(field),
                value_type=ScalarType("boolean" if field == "selected" else "float64"),
            )
        )

    def forecast(
        self,
        horizon: int,
        model: Literal["naive", "drift", "seasonal_naive"],
        season: int | None,
        level: float,
    ) -> Relation:
        import math

        from marivo.analysis.core.rules import ForecastFit
        from marivo.analysis.core.time_grid import continuation
        from marivo.analysis.methods.deviation_numeric import unit_type
        from marivo.analysis.methods.deviation_physical import parse_type

        if type(level) is not float or not math.isfinite(level) or not 0 < level < 1:
            raise StatisticalRelationError(
                code="r8.forecast_history",
                operation="forecast",
                method="forecast." + model + "@v1",
                input_identity=self.root.fingerprint,
                expected="finite float interval_level in (0,1)",
                received=repr(level),
                repair="Pass a finite interval level strictly between zero and one.",
            )
        self._check_runs_input("forecast")
        grid = self.root.signature.domain.time_grid
        assert grid is not None
        minimum = season + 1 if season is not None else 3 if model == "drift" else 2
        if len(grid.cells) < minimum:
            raise StatisticalRelationError(
                code="r8.forecast_history",
                operation="forecast",
                method="forecast." + model + "@v1",
                input_identity=self.root.fingerprint,
                expected=f"at least {minimum} complete history periods",
                received=str(len(grid.cells)),
                repair="Observe a complete history long enough for the requested model.",
            )
        try:
            future = continuation(grid, horizon)
        except DatasetConstructionError as error:
            raise StatisticalRelationError(
                code="r8.future_grid",
                operation="forecast",
                method="forecast." + model + "@v1",
                input_identity=self.root.fingerprint,
                expected="captured approved future continuation",
                received=str(error),
                repair="Capture the original observation with certified calendar coverage through the requested horizon.",
            ) from error
        identity = digest(
            repr((self.root.fingerprint, horizon, model, season, level, future.identity))
        )
        return self._with(
            method_node(
                (self._edge(),),
                ForecastFit(identity, self.root.value_type.name, model, season, level, future),
                value_type=unit_type(parse_type(self.root.value_type.name)),
            )
        )

    def forecast_field(self, field: Literal["prediction", "lower", "upper"]) -> Relation:
        from marivo.analysis.core.model import TrainingInputsPart, require_part
        from marivo.analysis.core.rules import ForecastRead
        from marivo.analysis.methods.deviation_numeric import unit_type
        from marivo.analysis.methods.deviation_physical import parse_type

        retained = require_part(self.root.signature, "training_inputs")
        assert isinstance(retained, TrainingInputsPart)
        return self._with(
            method_node(
                (self._edge(),),
                ForecastRead(field, retained.input_type),
                value_type=unit_type(parse_type(retained.input_type)),
            )
        )

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

    def where(
        self,
        predicate: ValuePredicate,
        dependency: Relation | None = None,
        *,
        dependencies: tuple[Relation, ...] = (),
    ) -> Relation:
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
        elif isinstance(definition, JourneyRead):
            field_kind = (
                "measure" if definition.field in ("duration", "observed_duration") else None
            )
        elif isinstance(definition, PartsTransport):
            field_kind = definition.field_kind
        dependencies = (dependency,) if dependency is not None else dependencies
        edges = [self._edge()]
        known = {node.identity: node for node in topology(self.root)}
        for item in dependencies:
            edges.append(Edge(item._edge().role, _shared_root(self.root, item.root, known)))
        inclusions = tuple(
            i
            for i, item in enumerate(dependencies, 1)
            if retained_inclusion(self.root, item.root)
            or retained_inclusion(self.definition, item.definition)
        )
        root = method_node(
            tuple(edges),
            PartsTransport(
                "where",
                self.root.signature.domain,
                tuple(part_role(part) for part in self.root.signature.parts),
                self.root.signature.quantity is not None
                or field_kind is not None
                or isinstance(definition, JourneyRead)
                or (isinstance(definition, PartsTransport) and definition.keep_quantity),
                (predicate,),
                field_kind,
                external_predicate=bool(dependencies),
                inclusion_inputs=inclusions,
                classification=self.classification_coordinate()
                if field_kind == "dimension"
                else None,
                display_view=definition.display_view
                if isinstance(definition, PartsTransport)
                else None,
            ),
            value_type=self.root.value_type,
            retained_endpoints=(
                self.definition,
                *(_retained_definition(item.definition) for item in dependencies),
            )
            if isinstance(self.binding, FrozenBinding) and inclusions
            else (),
        )
        result = self._with(root)
        for item in dependencies:
            result = result._with_sources(item)
        return result

    def cohort(
        self, predicate: ValuePredicate, dependencies: tuple[Relation, ...], rule: CohortRule
    ) -> Relation:
        if not dependencies:
            raise _reject("cohort needs a complete opportunity predicate")
        opportunity = dependencies[0].root.signature.domain
        known = {node.identity: node for node in topology(dependencies[0].root)}
        target_root = _shared_root(dependencies[0].root, self.root, known)
        edges = [Edge(self._edge().role, target_root)]
        for item in dependencies:
            edges.append(Edge(item._edge().role, _shared_root(target_root, item.root, known)))
        node = method_node(
            tuple(edges),
            PartsTransport(
                "cohort",
                self.root.signature.domain,
                ("subject",),
                False,
                (predicate,),
                external_predicate=True,
                cohort_rule="any"
                if isinstance(rule, AnyInstance)
                else "at_least"
                if isinstance(rule, AtLeast)
                else "all",
                cohort_count=rule.count if isinstance(rule, AtLeast) else 1,
                cohort_empty=rule.empty.decision if isinstance(rule, AllInstances) else "false",
                opportunity_domain=opportunity,
            ),
            value_type=self.root.value_type,
        )
        result = self._with(node)
        for item in dependencies:
            result = result._with_sources(item)
        return result

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
        relative = any(
            isinstance(node, MethodNode) and isinstance(node.parameters, AnchorObserve)
            for node in topology(self.root)
        )
        graph = observe_members(
            live.graph,
            metric,
            during=during,
            at=at,
            via=via,
            coordinates=coordinates,
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
            relative=relative,
        )
        return Relation(self.runtime, graph.root, replace(live, graph=graph))

    def resolves_multiple_components(self, metric: Ref[MetricKind] | RuntimeMetricExpr) -> bool:
        """Report whether an input binds more than one canonical occurrence."""
        live = self._live()
        contract = normalize_metric_input(live.graph.registry, metric, sidecar=live.sidecar)
        return len(contract.components) > 1

    def business_coverage(self, scopes: tuple[TimeScope, ...]) -> Relation:
        """Bind completeness to this exact observation, never to a reconstructed grid."""
        from marivo.analysis.core.business_coverage import invalid, normalize

        grid = self.root.signature.domain.time_grid
        if grid is None:
            raise invalid("the observation has no original TimeGrid")
        windows = normalize(scopes, grid)
        return self._with(
            method_node(
                (self._edge(),),
                PartsTransport(
                    "business_coverage",
                    self.root.signature.domain,
                    tuple(part_role(p) for p in self.root.signature.parts),
                    True,
                    business_windows=windows,
                ),
                value_type=self.root.value_type,
            )
        )

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
        relative = any(
            isinstance(node, MethodNode) and isinstance(node.parameters, AnchorObserve)
            for node in topology(self.root)
        )
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
                relative=relative,
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
            relative=relative,
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

    @property
    def comparison_error(self) -> str | None:
        """Return the static numerical qualification failure, if any."""
        quantity = self.root.signature.quantity
        if quantity is not None and quantity.method_version == "history.read@v1":
            return "History owned fields currently qualify selection and current-row statistics without comparison endpoints"
        if isinstance(self.root.value_type, DurationType) and isinstance(
            quantity, RowStatisticQuantity
        ):
            return "Duration row statistics currently qualify retained mean reduction only"
        if (
            self.root.value_type == ScalarType("float64")
            and quantity is not None
            and quantity.method_version
            in (
                "fold@v1",
                "median@v1",
                "percentile@v1",
                "approx_median@v1",
                "approx_percentile@v1",
            )
        ):
            return f"{quantity.method_version} has no qualified retained comparison error envelope"
        return None

    def combine(
        self,
        other: Relation,
        method: Literal["difference", "relative_change", "spearman", "relation_ratio"],
        *,
        design: Literal["time", "cohort", "period"] = "time",
        pairing: Literal["exact", "keep", "metric_empty"] = "exact",
        relationship: TargetRelationshipContract | None = None,
    ) -> Relation:
        if (
            self.runtime.session_ref != other.runtime.session_ref
            or self.runtime.store.store_id != other.runtime.store.store_id
        ):
            raise _reject("different Session owners")
        if method != "spearman":
            for endpoint in (self, other):
                if endpoint.comparison_error is not None:
                    raise DatasetConstructionError(
                        expected="a retained operand error envelope",
                        received=endpoint.comparison_error,
                        repair="Use comparison-qualified sum/mean/ratio/linear operands; keep unqualified folds and quantiles as terminal results.",
                        location="analysis.graph_relation.comparison",
                    )
        if method == "spearman" and any(
            isinstance(p, CoordinateStatePart)
            for relation in (self, other)
            for p in relation.root.signature.parts
        ):
            raise _reject(
                "Spearman requires one observation per member without contribution coordinates"
            )
        if isinstance(self.binding, LiveBinding) and isinstance(other.binding, LiveBinding):
            graph = combine_observations(
                self.binding.graph,
                other.binding.graph,
                method,
                design=design,
                pairing=pairing,
                relationship=relationship,
            )
            return Relation(self.runtime, graph.root, replace(self.binding, graph=graph))
        if isinstance(self.binding, LiveBinding) or isinstance(other.binding, LiveBinding):
            raise _reject("mixed live and materialized dependencies")
        current, baseline = self.definition, other.definition
        first, second = self.root.signature, other.root.signature
        left, right = first.quantity, second.quantity
        if left is None or right is None:
            raise _reject("comparison endpoints must retain quantities")
        if method in ("difference", "relative_change"):
            validate_time_comparison(current, baseline, design)
        elif method == "relation_ratio":
            if design != "period" and left.time_scope != right.time_scope:
                raise _reject("ordinary ratio requires corresponding time roles")
        else:
            if not isinstance(current.parameters, (ObserveMetric, ObserveCount)) or not isinstance(
                baseline.parameters, (ObserveMetric, ObserveCount)
            ):
                raise _reject("endpoints must retain their exact observation definitions")
            a, b = current.inputs[0].node, baseline.inputs[0].node
            if a.identity != b.identity or a.fingerprint != b.fingerprint:
                raise _reject("endpoints do not share the same frozen member node")
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
        if method in ("difference", "relative_change", "relation_ratio"):
            from marivo.semantic.unit_algebra import ratio_unit

            arithmetic: Literal["difference", "relative_change", "ratio"] = (
                "ratio" if method == "relation_ratio" else method
            )
            try:
                result_type = output_type(arithmetic, self.root.value_type, other.root.value_type)
            except ValueError as error:
                raise _reject(str(error)) from error
            root = method_node(
                (Edge("current", self.root), Edge("baseline", other.root)),
                CellDerive(
                    arithmetic,
                    definition,
                    "duration_ratio_unknown"
                    if arithmetic == "ratio"
                    and isinstance(self.root.value_type, DurationType)
                    and isinstance(other.root.value_type, DurationType)
                    else "strict",
                    ratio_unit(left.unit, right.unit) if arithmetic == "ratio" else left.unit,
                    left.time_scope,
                    "source.exact_pairing@v1" if pairing == "exact" else "source.unique_key@v1",
                    "source.finite_numeric@v1",
                    "ratio" if method == "relation_ratio" else design,
                    pairing,
                    comparison_empty_rules(current, baseline) if pairing == "metric_empty" else (),
                    time_index=period_mapping(current, baseline)[0] if design == "period" else None,
                    bucket_mapping=period_mapping(current, baseline)[1]
                    if design == "period"
                    else (),
                    relationship=relationship,
                ),
                value_type=result_type,
                retained_endpoints=(current, baseline),
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
                    (
                        p
                        for p in signature.parts
                        if isinstance(p, CoordinateStatePart) and not p.attribution_only
                    ),
                    None,
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
        from marivo.analysis.core.model import HistoryViewPart

        if isinstance(self.root.value_type, DurationType) and any(
            isinstance(part, HistoryViewPart) and part.request.kind == "dwell"
            for part in self.root.signature.parts
        ):
            raise _reject(
                "Dwell Duration summaries cannot be averaged or pooled; "
                "summarize the completed interval observed_duration rows instead"
            )
        quantity = self.root.signature.quantity
        if (
            isinstance(quantity, DerivedQuantity)
            and quantity.value_policy == "prediction_interval_bound"
            and method == "sum"
        ):
            raise StatisticalRelationError(
                code="r8.cell_policy",
                operation="forecast",
                method="forecast.read@v1",
                input_identity=self.root.fingerprint,
                expected="descriptive rows without interval addition",
                received="sum of PredictionIntervalBound",
                repair="Read individual lower/upper bounds or summarize prediction rows; interval bounds cannot be added.",
            )
        definition = digest("row." + method + self.root.fingerprint)
        domain = DomainSignature(
            self.root.signature.domain.binding,
            "group" if coordinates else "singleton",
            coordinates,
            coordinates,
            definition,
            version_selection=self.root.signature.domain.version_selection,
        )
        value_type = (
            ScalarType("int64")
            if method in ("count", "count_defined")
            else DecimalType(
                38,
                max(self.root.value_type.scale, 6)
                if method == "mean"
                else self.root.value_type.scale,
            )
            if method in ("sum", "mean") and isinstance(self.root.value_type, DecimalType)
            else self.root.value_type
            if method == "mean" and isinstance(self.root.value_type, DurationType)
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
                    retain_error=(
                        self.root.value_type == ScalarType("float64")
                        or isinstance(self.root.value_type, DurationType)
                    )
                    and method in ("sum", "mean", "min", "max"),
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
                RowState(
                    method,
                    domain,
                    quantity.definition_id,
                    quantity.value_policy,
                    merge=True,
                    retain_error=any(
                        isinstance(part, RowStatePart) and "error_bound" in part.components
                        for part in self.root.signature.parts
                    ),
                ),
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
