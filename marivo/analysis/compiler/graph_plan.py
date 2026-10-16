"""Pure R3 graph admission and R4 stage handoff, with no execution imports."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias

from marivo._definition.expressions import FieldPredicate
from marivo.analysis.core.data_types import DataColumn, DataType, DurationDataType, ScalarDataType
from marivo.analysis.core.graph import (
    BoundGraph,
    FixedLeaf,
    MethodNode,
    Node,
    SourceLeaf,
    _CapturedGraph,
    capture_graph,
    topology,
)
from marivo.analysis.core.model import (
    ConditionCellsPart,
    CoveragePart,
    FitInputsPart,
    Obligation,
    PairInputsPart,
    RunCellsPart,
    TrainingInputsPart,
    reject,
)
from marivo.analysis.core.rules import (
    AssociationFit,
    AssociationRead,
    AttachCategory,
    AttributionDerive,
    CellCompare,
    CellDerive,
    ComparisonRead,
    DeviationFit,
    DeviationRead,
    DisplayRank,
    DisplayTable,
    ForecastFit,
    ForecastRead,
    MapCorrespond,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    RowState,
    TimeProduct,
    TimeRunRead,
    TimeRuns,
)
from marivo.analysis.methods.physical import (
    QUERY_COMBINE_KERNEL,
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Route,
    SourceShape,
    ValueType,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry

if TYPE_CHECKING:
    from marivo.analysis.compiler.observation import FormulaProgram, ReductionInput


@dataclass(frozen=True, slots=True)
class InputClassification:
    kind: Literal["source", "artifact", "mixed"]
    sources: tuple[SourceLeaf, ...]
    artifacts: tuple[FixedLeaf, ...]


def classify_inputs(root: Node) -> InputClassification:
    """Traverse actual dependencies only; fixed origin history has no graph edge."""
    return _classify(topology(root))


def _classify(nodes: tuple[Node, ...]) -> InputClassification:
    sources = tuple(n for n in nodes if isinstance(n, SourceLeaf))
    artifacts = tuple(n for n in nodes if isinstance(n, FixedLeaf))
    kind: Literal["source", "artifact", "mixed"] = (
        "mixed" if sources and artifacts else "source" if sources else "artifact"
    )
    return InputClassification(kind, sources, artifacts)


@dataclass(frozen=True, slots=True)
class RouteChoice:
    node_id: str
    route: Route


@dataclass(frozen=True, slots=True)
class SourceInputStage:
    """Declared binding only; R4 must verify physical metadata before consumption."""

    output: str
    leaf: SourceLeaf


@dataclass(frozen=True, slots=True)
class ArtifactReadStage:
    output: str
    leaf: FixedLeaf


@dataclass(frozen=True, slots=True)
class SourceMethodStage:
    output: str
    inputs: tuple[str, ...]
    node: MethodNode
    implementation: Implementation
    operation: Literal["ibis", "prepare"]


@dataclass(frozen=True, slots=True)
class LocalMethodStage:
    output: str
    inputs: tuple[str, ...]
    node: MethodNode
    implementation: Implementation


Stage: TypeAlias = SourceInputStage | ArtifactReadStage | SourceMethodStage | LocalMethodStage


@dataclass(frozen=True, slots=True)
class CheckRequirement:
    """Bound semantic obligation; the executor must honor its consume/publish deadline."""

    node_id: str
    stage_output: str
    obligation: Obligation


@dataclass(frozen=True, slots=True)
class PhysicalRequirement:
    """Static type/time/shape and resources still need invocation-time verification."""

    node_id: str
    key: QualificationKey
    output_type: ValueType
    implementation: Implementation


@dataclass(frozen=True)
class GraphPlan:
    root: Node
    classification: InputClassification
    stages: tuple[Stage, ...]
    checks: tuple[CheckRequirement, ...]
    physical_requirements: tuple[PhysicalRequirement, ...]
    primary_output: str
    captured: _CapturedGraph = field(repr=False, compare=False)


def admitted_capture(admitted: GraphPlan, registry: MethodRegistry = REGISTRY) -> _CapturedGraph:
    """Preserve the selected registry interpretation without re-admitting the plan."""
    if admitted.captured.registry is not registry:
        _refuse(
            "an unchanged admitted plan",
            "different registry interpretation",
            "Rebuild the plan from the exact definition and registry.",
        )
    return admitted.captured


def _refuse(expected: str, received: str, repair: str) -> NoReturn:
    reject(expected, received, repair, "analysis.graph_plan")


def plan(
    root: Node,
    *,
    routes: tuple[RouteChoice, ...],
    registry: MethodRegistry = REGISTRY,
) -> GraphPlan:
    """Select explicit routes before any data work; unavailable keys remain unavailable."""
    return _plan_captured(capture_graph(root, registry=registry), routes=routes)


def _plan_captured(captured: _CapturedGraph, *, routes: tuple[RouteChoice, ...]) -> GraphPlan:
    root, registry, nodes = captured.root, captured.registry, captured.nodes
    classification = _classify(nodes)
    if classification.kind == "mixed":
        _refuse(
            "source-only or fixed Artifact-only dependencies",
            "mixed live and fixed inputs",
            "Use live inputs throughout, or materialize the source separately and use fixed inputs.",
        )
    domains = {leaf.definition.datasource for leaf in classification.sources}
    if len(domains) > 1:
        _refuse(
            "one live datasource", "multiple live datasources", "Use one qualified source domain."
        )
    shapes: set[SourceShape | FixedShape] = {
        *(leaf.definition.shape for leaf in classification.sources),
        *(leaf.shape for leaf in classification.artifacts),
    }
    fixed_cohort = (
        classification.kind == "artifact"
        and isinstance(root, MethodNode)
        and isinstance(root.parameters, PartsTransport)
        and root.parameters.mode == "cohort"
        and root.parameters.opportunity_domain is not None
        and root.parameters.opportunity_domain.time_grid is not None
        and all(isinstance(edge.node, FixedLeaf) for edge in root.inputs)
        and isinstance(root.inputs[0].node, FixedLeaf)
        and root.inputs[0].node.shape == FixedShape(NoTime())
        and len(shapes) == 2
        and FixedShape(NoTime()) in shapes
        and len({edge.node.shape for edge in root.inputs[1:] if isinstance(edge.node, FixedLeaf)})
        == 1
    )
    timed_shapes = shapes - {FixedShape(NoTime())}
    timed_grid = next(
        (
            leaf.signature.domain.time_grid
            for leaf in classification.artifacts
            if leaf.shape in timed_shapes
        ),
        None,
    )
    # Saved classifications pair full grid keys or retained Subject keys without
    # reading timestamps; display still requires complete grid keys.
    fixed_keyed = (
        classification.kind == "artifact"
        and len(shapes) == 2
        and FixedShape(NoTime()) in shapes
        and len(timed_shapes) == 1
        and timed_grid is not None
        and all(
            (
                leaf.signature.domain.time_grid == timed_grid
                or (
                    leaf.shape == FixedShape(NoTime())
                    and leaf.signature.domain.time_grid is None
                    and all(
                        isinstance(node.parameters, AttachCategory)
                        and node.parameters.subject_mapping
                        and node.inputs[1].node is leaf
                        and node.inputs[0].node.signature.domain.time_grid == timed_grid
                        for node in nodes
                        if isinstance(node, MethodNode)
                        and any(edge.node is leaf for edge in node.inputs)
                    )
                )
            )
            and (
                leaf.shape != FixedShape(NoTime())
                or (
                    leaf.signature.quantity is None
                    and all(
                        isinstance(node.parameters, (DisplayRank, DisplayTable))
                        or (
                            isinstance(node.parameters, AttachCategory)
                            and node.inputs[1].node is leaf
                        )
                        for node in nodes
                        if isinstance(node, MethodNode)
                        and any(edge.node is leaf for edge in node.inputs)
                    )
                )
            )
            for leaf in classification.artifacts
        )
    )
    if len(shapes) != 1 and not (fixed_cohort or fixed_keyed):
        _refuse(
            "one exact input physical shape",
            "different time or source shapes",
            "Qualify a common exact shape first.",
        )
    # Cohort reads the retained opportunity grid, not physical timestamp values.
    # ArtifactReadStage retains each leaf's exact shape and publication contract.
    shape = (
        FixedShape(NoTime())
        if fixed_cohort
        else next(iter(timed_shapes if fixed_keyed else shapes))
    )
    methods = tuple(n for n in nodes if isinstance(n, MethodNode))
    if type(routes) is not tuple or any(type(r) is not RouteChoice for r in routes):
        _refuse("immutable route choices", repr(routes), "Choose one route per method node.")
    choices = {r.node_id: r.route for r in routes}
    if len(choices) != len(routes) or set(choices) != {n.identity for n in methods}:
        _refuse(
            "one route for every reachable method",
            repr(tuple(choices)),
            "Remove duplicate or unrelated choices and name every method.",
        )
    if not methods and classification.sources:
        _refuse(
            "a registered source method",
            "bare source leaf",
            "Bind a registered method before planning a source read.",
        )
    for leaf in classification.artifacts:
        if leaf.signature.obligations:
            _refuse(
                "a completed fixed Artifact contract",
                "pending Artifact obligations",
                "Use an Artifact with completed publication checks.",
            )
    if not methods and classification.artifacts:
        _refuse(
            "a registered fixed-input method",
            "bare fixed Artifact leaf",
            "Bind a registered local method before planning an Artifact read.",
        )
    stages: list[Stage] = []
    checks: list[CheckRequirement] = []
    physical: list[PhysicalRequirement] = []
    outputs: dict[str, str] = {}
    local: set[str] = set()
    prepared_ancestors: dict[str, bool] = {}
    for node in nodes:
        output = f"stage:{len(stages)}"
        if isinstance(node, SourceLeaf):
            stages.append(SourceInputStage(output, node))
        elif isinstance(node, FixedLeaf):
            stages.append(ArtifactReadStage(output, node))
            local.add(node.identity)
        else:
            route = choices[node.identity]
            if (classification.kind == "artifact") != (route == "artifact_python"):
                _refuse(
                    "a route matching the input class",
                    route,
                    "Select fixed Python or a qualified source route explicitly.",
                )
            prepared_ancestors[node.identity] = (
                isinstance(node.parameters, PreparedObservation)
                and (
                    node.inputs[0].node.identity == node.inputs[1].node.identity
                    or any(
                        isinstance(ancestor, MethodNode)
                        and isinstance(ancestor.parameters, ComparisonRead)
                        for ancestor in topology(node)
                    )
                )
            ) or any(prepared_ancestors.get(edge.node.identity, False) for edge in node.inputs)
            prepared_consumer = (
                isinstance(node.parameters, (OriginalReduce, CellDerive, AttributionDerive))
                and prepared_ancestors[node.identity]
            )
            if (
                not prepared_consumer
                and not (
                    route == "ibis_python"
                    and isinstance(
                        node.parameters,
                        (
                            PartsTransport,
                            RowState,
                            MapCorrespond,
                            CellDerive,
                            DisplayRank,
                            DisplayTable,
                        ),
                    )
                    and any(edge.node.identity in local for edge in node.inputs)
                )
                and any(edge.node.identity in local for edge in node.inputs)
                and route != "artifact_python"
                and not isinstance(
                    node.parameters,
                    (
                        CellCompare,
                        ComparisonRead,
                        AssociationFit,
                        AssociationRead,
                        ForecastFit,
                        ForecastRead,
                        TimeRuns,
                        TimeRunRead,
                        DeviationFit,
                        DeviationRead,
                        TimeProduct,
                        PreparedObservation,
                    ),
                )
                and not (
                    isinstance(node.parameters, RowState)
                    and isinstance(node.inputs[0].node, MethodNode)
                    and isinstance(node.inputs[0].node.parameters, PreparedObservation)
                )
                and not (
                    isinstance(node.parameters, PartsTransport)
                    and (
                        node.parameters.mode == "business_coverage"
                        or any(
                            isinstance(p, CoveragePart) and p.business_windows is not None
                            for p in node.inputs[0].node.signature.parts
                        )
                    )
                )
                and not any(
                    isinstance(
                        p,
                        (
                            ConditionCellsPart,
                            RunCellsPart,
                            PairInputsPart,
                            TrainingInputsPart,
                            FitInputsPart,
                        ),
                    )
                    for e in node.inputs
                    for p in e.node.signature.parts
                )
                and not all(
                    edge.node.identity not in local
                    or (
                        isinstance(edge.node, MethodNode)
                        and edge.node.method.name
                        in (
                            "cell.difference",
                            "cell.compare",
                            "comparison.read",
                            "cell.relative_change",
                            "cell.ratio",
                            "reference.share",
                            "reference.penetration",
                            "reference.standardize",
                            "attribution.additive_difference",
                            "attribution.component_mix",
                            "display.rank",
                            "display.table",
                        )
                    )
                    for edge in node.inputs
                )
            ):
                _refuse(
                    "source inputs for Ibis preparation",
                    f"a local predecessor for {node.method}",
                    "Keep the source prefix in Ibis; local-to-source transport is not qualified.",
                )
            key = QualificationKey(
                node.method,
                tuple(e.node.value_type for e in node.inputs),
                tuple(e.node.signature.domain.kind for e in node.inputs),
                replace(shape, time=NoTime())
                if isinstance(node.parameters, (DeviationFit, DeviationRead))
                and node.signature.domain.time_grid is None
                else shape,
                route,
            )
            selected = registry._select_derived(
                key, tuple(e.node.signature for e in node.inputs), node.parameters, node.derivation
            )
            implementation = selected.implementation
            inputs = tuple(outputs[e.node.identity] for e in node.inputs) + tuple(
                outputs[s.identity] for s in node.sources
            )
            local_consumer = (
                prepared_consumer
                or (
                    isinstance(
                        node.parameters, (CellDerive, ReferenceDerive, DisplayRank, DisplayTable)
                    )
                    and route != "ibis"
                )
                or (
                    isinstance(node.parameters, (PartsTransport, RowState, MapCorrespond))
                    and route != "ibis"
                    and any(edge.node.identity in local for edge in node.inputs)
                )
                or (
                    isinstance(node.parameters, AttributionDerive)
                    and route != "ibis"
                    and any(edge.node.identity in local for edge in node.inputs)
                )
                or (
                    isinstance(node.parameters, PartsTransport)
                    and (
                        node.parameters.mode == "business_coverage"
                        or any(
                            isinstance(p, CoveragePart) and p.business_windows is not None
                            for p in node.inputs[0].node.signature.parts
                        )
                    )
                )
                or (
                    isinstance(node.parameters, TimeProduct)
                    and node.inputs[0].node.identity in local
                )
                or (
                    isinstance(node.parameters, RowState)
                    and isinstance(node.inputs[0].node, MethodNode)
                    and isinstance(node.inputs[0].node.parameters, PreparedObservation)
                )
                or (
                    any(
                        isinstance(
                            p,
                            (
                                ConditionCellsPart,
                                RunCellsPart,
                                PairInputsPart,
                                TrainingInputsPart,
                                FitInputsPart,
                            ),
                        )
                        for e in node.inputs
                        for p in e.node.signature.parts
                    )
                )
                or isinstance(
                    node.parameters,
                    (
                        CellCompare,
                        ComparisonRead,
                        AssociationFit,
                        AssociationRead,
                        ForecastFit,
                        ForecastRead,
                        TimeRuns,
                        TimeRunRead,
                        DeviationFit,
                        DeviationRead,
                    ),
                )
            )
            prepared_observation = (
                isinstance(node.parameters, (PreparedObservation,)) and route != "ibis"
            )
            if prepared_observation and node.inputs[1].node.identity in local:
                _refuse(
                    "a source original member envelope",
                    "local candidate envelope",
                    "Bind the original members before local selection.",
                )
            physical.append(
                PhysicalRequirement(node.identity, key, node.value_type, implementation)
            )
            consume_output = output
            if route in ("ibis", "ibis_python") and not local_consumer:
                stages.append(
                    SourceMethodStage(
                        output,
                        (
                            outputs[node.inputs[1].node.identity],
                            *(outputs[s.identity] for s in node.sources),
                        )
                        if prepared_observation
                        else inputs,
                        node,
                        implementation,
                        "ibis" if route == "ibis" else "prepare",
                    )
                )
            if route != "ibis":
                local_inputs = (
                    (outputs[node.inputs[0].node.identity], output)
                    if prepared_observation
                    else inputs
                    if local_consumer
                    else (output,)
                    if route == "ibis_python"
                    else inputs
                )
                output = f"stage:{len(stages)}"
                stages.append(LocalMethodStage(output, local_inputs, node, implementation))
                local.add(node.identity)
            checks.extend(
                CheckRequirement(
                    node.identity,
                    consume_output if obligation.before == "consume" else output,
                    obligation,
                )
                for obligation in node.derivation.obligations
                if classification.kind != "artifact"
                or not any(prior.obligation == obligation for prior in checks)
            )
        outputs[node.identity] = output
    if any(isinstance(node.parameters, (PreparedObservation,)) for node in methods):
        stages = [
            *(stage for stage in stages if not isinstance(stage, LocalMethodStage)),
            *(stage for stage in stages if isinstance(stage, LocalMethodStage)),
        ]
    return GraphPlan(
        root,
        classification,
        tuple(stages),
        tuple(checks),
        tuple(physical),
        outputs[root.identity],
        captured,
    )


# R2.3 Query planning. The legacy GraphPlan above keeps serving the graph DSL;
# QueryPlan is the only plan the BoundGraph Query path consumes. Planning is
# pure: execute preparation supplies schema-only SourceSchema facts first.


@dataclass(frozen=True, slots=True)
class SourceField:
    """One physical column admitted at execute preparation, without its values."""

    field: str
    column: str
    dtype: DataType
    nullable: bool


@dataclass(frozen=True, slots=True)
class SourceSchema:
    """Schema-only facts for one contribution root Entity output."""

    source_id: str
    keys: tuple[SourceField, ...]
    fields: tuple[SourceField, ...]
    occurrence: tuple[int, ...] | None = None
    preparation_checks: tuple[tuple[str, str, str], ...] = ()

    def field(self, path: str) -> SourceField:
        for item in self.fields:
            if item.field == path:
                return item
        reject(
            f"an admitted source column for {path}",
            f"columns of {self.source_id}: {tuple(f.field for f in self.fields)!r}",
            "Prepare every field read by this contribution before planning.",
            "analysis.query_plan.source_schema",
        )


@dataclass(frozen=True, slots=True)
class PeriodFilter:
    """Half-open instant filter on one native timezone-aware time column."""

    column: str
    start: datetime
    end: datetime
    timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class SourceRead:
    """One governed source read; its slots are the only columns it returns."""

    stage_id: str
    source_id: str
    occurrence: tuple[int, ...]
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    projections: tuple[tuple[str, str], ...]
    field_columns: tuple[tuple[str, str], ...]
    predicates: tuple[FieldPredicate, ...]
    period: PeriodFilter | None
    reduction: ReductionInput | None = None
    preparation_checks: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class LocalCompute:
    """One registered pandas kernel over completed stage outputs."""

    stage_id: str
    inputs: tuple[str, ...]
    kernel: str
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    program: FormulaProgram | None = None


QueryStage: TypeAlias = SourceRead | LocalCompute


@dataclass(frozen=True, slots=True)
class SourceCheckRoute:
    """An implemented check bound to one exact contribution occurrence."""

    obligation: Obligation
    occurrence: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PlannedCheck:
    """A check with its fixed stage and consume/publish deadline."""

    obligation: Obligation
    stage_id: str

    @property
    def check_id(self) -> str:
        return self.obligation.check_id

    @property
    def deadline(self) -> Literal["consume", "publish"]:
        return self.obligation.before


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Topologically ordered stages, consumers, checks and the published stage."""

    stages: tuple[QueryStage, ...]
    checks: tuple[PlannedCheck, ...]
    consumers: tuple[tuple[str, tuple[str, ...]], ...]
    publish: str

    def consumers_of(self, stage_id: str) -> tuple[str, ...]:
        return dict(self.consumers).get(stage_id, ())


def _stage_id(occurrence: tuple[int, ...]) -> str:
    return "read:" + ".".join(str(index) for index in occurrence)


def _plan_source_reads(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    checks: tuple[SourceCheckRoute, ...] = (),
) -> QueryPlan:
    """Fix stages, slots, consumers and check deadlines without any I/O.

    Each contribution occurrence becomes one SourceRead. A single contribution
    publishes its read directly; several are combined by one LocalCompute whose
    output slots the registered kernel declares. Implemented source checks must
    identify their exact occurrence; a check ID alone does not establish scope.
    """
    from marivo.analysis.errors import AnalysisStageUnavailableError

    observation = graph.signature.observation
    contributions = () if observation is None else observation.contributions
    if not contributions:
        raise AnalysisStageUnavailableError(
            stage="query_execution",
            expected="at least one source contribution for an R2.3 plan",
            received="source-free Metric outputs",
            repair="Source-free constants are computed by the R2.4 local numeric route; keep the bound Query.",
            target="Query.execute",
        )
    by_source = {schema.source_id: schema for schema in schemas}
    from marivo._definition.types import FieldRead, field_read_key

    reads: list[SourceRead] = []
    for contribution in contributions:
        source_id = contribution.root.ref.path
        schema = next(
            (
                s
                for s in schemas
                if s.source_id == source_id and s.occurrence == contribution.occurrence
            ),
            by_source.get(source_id),
        )
        if schema is None:
            reject(
                f"prepared schema facts for {source_id}",
                repr(tuple(by_source)),
                "Prepare every contribution root before planning.",
                "analysis.query_plan.source_schema",
            )
        stage_id = _stage_id(contribution.occurrence)
        paths = list(
            dict.fromkeys(
                (
                    *(field_read_key(read) for read in contribution.reads),
                    *(field_read_key(read) for read in contribution.coordinates),
                )
            )
        )
        fields = tuple(schema.field(path) for path in paths)
        key_slots = tuple(f"{stage_id}/k{index}" for index in range(len(schema.keys)))
        columns = (
            *(
                DataColumn(slot, key.column, key.dtype, False)
                for slot, key in zip(key_slots, schema.keys, strict=True)
            ),
            *(
                DataColumn(f"{stage_id}/f{index}", item.field, item.dtype, item.nullable)
                for index, item in enumerate(fields)
            ),
        )
        projections = tuple(
            zip(
                (column.slot_id for column in columns),
                (*(key.column for key in schema.keys), *(item.column for item in fields)),
                strict=True,
            )
        )
        period = None
        if contribution.period is not None and contribution.time is not None:
            period = PeriodFilter(
                schema.field(
                    field_read_key(
                        FieldRead(
                            contribution.time.field,
                            contribution.root,
                            contribution.time.path,
                            contribution.time.at,
                        )
                    )
                ).column,
                contribution.period.start,
                contribution.period.end,
                contribution.period.timezone,
            )
        reads.append(
            SourceRead(
                stage_id,
                source_id,
                contribution.occurrence,
                columns,
                key_slots,
                projections,
                tuple((item.field, item.column) for item in fields),
                (
                    *contribution.intrinsic_conditions,
                    *(
                        (contribution.common_condition,)
                        if contribution.common_condition is not None
                        else ()
                    ),
                ),
                period,
                preparation_checks=schema.preparation_checks,
            )
        )
    stages: tuple[QueryStage, ...] = tuple(reads)
    consumers: tuple[tuple[str, tuple[str, ...]], ...] = ()
    publish = reads[0].stage_id
    if len(reads) > 1:
        combine = LocalCompute(
            "local:combine", tuple(read.stage_id for read in reads), QUERY_COMBINE_KERNEL, (), ()
        )
        stages = (*reads, combine)
        consumers = tuple((read.stage_id, (combine.stage_id,)) for read in reads)
        publish = combine.stage_id
    planned: list[PlannedCheck] = []
    by_occurrence = {read.occurrence: read for read in reads}
    for route in checks:
        if (
            route.obligation not in graph.signature.obligations
            or route.occurrence not in by_occurrence
        ):
            reject(
                "a Query obligation and its exact source contribution occurrence",
                repr(route),
                "Bind the implemented check to a contribution in this Query before planning.",
                "analysis.query_plan.check",
            )
    for obligation in graph.signature.obligations:
        routes = tuple(route for route in checks if route.obligation == obligation)
        if not routes:
            raise AnalysisStageUnavailableError(
                stage="query_execution",
                expected=f"an implemented {obligation.check_id} check bound to its source occurrence",
                received=f"{obligation.fact.kind} premise without a Query check route",
                repair="This check route is not delivered yet; no business row was read.",
                target="Query.execute",
            )
        planned.extend(
            PlannedCheck(obligation, by_occurrence[route.occurrence].stage_id) for route in routes
        )
    return QueryPlan(stages, tuple(planned), consumers, publish)


def plan_query(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    checks: tuple[SourceCheckRoute, ...] = (),
) -> QueryPlan:
    """Fix ordinary numeric source and local routes before any business read."""
    import ibis

    from marivo._definition.expressions import (
        ContributionFilter,
        DeclaredMetricRef,
        EntityCount,
        SourceAggregate,
    )
    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.compiler.observation import (
        FormulaInput,
        FormulaProgram,
        data_type,
        duration_type,
        formula_columns,
        ibis_type,
        lower_observation,
        reduction_input,
    )

    observation = graph.signature.observation
    contributions = () if observation is None else observation.contributions
    base = (
        _plan_source_reads(graph, schemas, checks=checks)
        if contributions
        else QueryPlan((), (), (), "")
    )
    calendar = next(
        (axis for axis in graph.signature.axes if isinstance(axis, CalendarCoordinate)), None
    )
    reads: list[SourceRead] = []
    values: list[FormulaInput] = []
    keys: tuple[DataColumn, ...] = (
        (DataColumn("key.0", f"calendar:{calendar.grain}", ScalarDataType("int64"), False),)
        if calendar is not None
        else ()
    )
    source_stages = tuple(stage for stage in base.stages if isinstance(stage, SourceRead))
    for stage, contribution in zip(source_stages, contributions, strict=True):
        reduction = reduction_input(contribution, f"{stage.stage_id}/value", calendar)
        schema = next(
            item
            for item in schemas
            if item.source_id == stage.source_id
            and (item.occurrence is None or item.occurrence == stage.occurrence)
        )
        reduction = replace(
            reduction,
            duration=duration_type(
                contribution.definition, {item.field: item.dtype for item in schema.fields}
            ),
        )
        table = ibis.table({item.column: ibis_type(item.dtype) for item in schema.fields})
        expression, _ = lower_observation(
            reduction, table, dict(stage.field_columns), stage.predicates
        )
        stage_keys = tuple(
            DataColumn(
                slot,
                f"calendar:{calendar.grain}"
                if slot == "key.0" and calendar is not None
                else next(path for key, path in reduction.axes if key == slot),
                next(
                    (
                        field.dtype
                        for field in schema.fields
                        if field.field
                        == next((path for key, path in reduction.axes if key == slot), "")
                        and isinstance(field.dtype, DurationDataType)
                    ),
                    data_type(expression[slot].type()),
                ),
                True,
            )
            for slot in expression.columns
            if slot.startswith("key.")
        )
        if reads and tuple(c.dtype for c in keys) != tuple(c.dtype for c in stage_keys):
            _refuse(
                "identical physical coordinate types across contributions",
                repr(stage_keys),
                "Choose fields with a common lossless coordinate type.",
            )
        keys = stage_keys
        column = DataColumn(
            reduction.value_slot,
            reduction.value_slot,
            reduction.duration or data_type(expression[reduction.value_slot].type()),
            True,
        )
        reads.append(
            replace(
                stage,
                columns=(*keys, column),
                keys=tuple(c.slot_id for c in keys),
                reduction=reduction,
            )
        )
        node = contribution.definition
        values.append(
            FormulaInput(
                contribution.occurrence,
                stage.stage_id,
                column,
                isinstance(node, EntityCount)
                or (
                    isinstance(node, SourceAggregate)
                    and node.reducer in ("sum", "count", "count_distinct")
                ),
            )
        )
    program = FormulaProgram(
        graph.root.metrics, graph.signature.declared_metrics, tuple(values), calendar
    )
    columns = tuple(
        replace(column, unit=quantity.unit)
        for column, quantity in zip(
            formula_columns(program), graph.signature.quantities, strict=True
        )
    )
    # A single bare reduction already is the requested final table. No empty
    # local stage is inserted into a pure source route.
    direct = graph.root.metrics[0][1] if len(graph.root.metrics) == 1 else None
    declarations = dict(program.declared)
    while isinstance(direct, (DeclaredMetricRef, ContributionFilter)):
        direct = (
            declarations[direct.ref] if isinstance(direct, DeclaredMetricRef) else direct.operand
        )
    if len(reads) == 1 and direct == contributions[0].definition and calendar is None:
        stage = reads[0]
        assert stage.reduction is not None
        stage = replace(
            stage,
            columns=(*keys, columns[0]),
            reduction=replace(stage.reduction, value_slot=columns[0].slot_id),
        )
        return replace(base, stages=(stage,), consumers=(), publish=stage.stage_id)
    combine = LocalCompute(
        "local:combine",
        tuple(stage.stage_id for stage in reads),
        QUERY_COMBINE_KERNEL,
        (*keys, *columns),
        tuple(c.slot_id for c in keys),
        program,
    )
    return QueryPlan(
        (*reads, combine),
        base.checks,
        tuple((stage.stage_id, (combine.stage_id,)) for stage in reads),
        combine.stage_id,
    )
