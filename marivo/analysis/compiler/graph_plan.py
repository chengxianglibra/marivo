"""Pure R3 graph admission and R4 stage handoff, with no execution imports."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, NoReturn, TypeAlias
from weakref import ReferenceType, ref

from marivo.analysis.core.graph import (
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
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
    HistoryViewPart,
    InstanceRetentionPart,
    Obligation,
    PairInputsPart,
    RunCellsPart,
    SubjectRetentionPart,
    TrainingInputsPart,
    reject,
)
from marivo.analysis.core.rules import (
    AnchorBind,
    AnchorObserve,
    AnchorRetention,
    AssociationFit,
    AssociationRead,
    AttachCategory,
    AttributionDerive,
    CellDerive,
    DeviationFit,
    DeviationRead,
    DisplayRank,
    DisplayTable,
    ForecastFit,
    ForecastRead,
    FunnelAttribute,
    FunnelAxesPrepare,
    FunnelCompare,
    FunnelRead,
    FunnelReduce,
    HistoryAxesPrepare,
    HistoryRead,
    HistoryReplay,
    HistoryView,
    JourneyCompleted,
    JourneyDuration,
    JourneyMatch,
    JourneyRead,
    MapCorrespond,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    RetentionBySubject,
    RowState,
    TimeProduct,
    TimeRunRead,
    TimeRuns,
)
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Route,
    SourceShape,
    ValueType,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry


@dataclass(frozen=True, slots=True)
class InputClassification:
    kind: Literal["source", "artifact", "mixed"]
    sources: tuple[SourceLeaf, ...]
    artifacts: tuple[FixedLeaf, ...]


def classify_inputs(root: Node, *, registry: MethodRegistry = REGISTRY) -> InputClassification:
    """Traverse actual dependencies only; fixed origin history has no graph edge."""
    return _classify(topology(root, registry=registry))


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
    _handoff: _PlanHandoff | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _PlanHandoff:
    plan: ReferenceType[GraphPlan]
    captured: _CapturedGraph


def admitted_capture(admitted: GraphPlan, registry: MethodRegistry = REGISTRY) -> _CapturedGraph:
    """Check the exact compiler handoff without selecting or deriving again."""
    if type(admitted) is not GraphPlan:
        _refuse(
            "an unchanged admitted plan",
            type(admitted).__name__,
            "Build through the graph planner.",
        )
    try:
        handoff = admitted._handoff
    except AttributeError:
        _refuse("an unchanged admitted plan", "missing handoff", "Build through the graph planner.")
    if (
        type(handoff) is not _PlanHandoff
        or handoff.plan() is not admitted
        or handoff.captured.registry is not registry
    ):
        _refuse(
            "an unchanged admitted plan",
            "altered plan, definition or registry interpretation",
            "Rebuild the plan from the exact definition and registry.",
        )
    return handoff.captured


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
    # A saved category can carry grid coordinates without reading timestamps.
    # Display and classification pair complete keys without reading timestamps.
    fixed_keyed = (
        classification.kind == "artifact"
        and len(shapes) == 2
        and FixedShape(NoTime()) in shapes
        and len(timed_shapes) == 1
        and all(
            leaf.signature.domain.time_grid is not None
            and leaf.signature.domain.time_grid
            == classification.artifacts[0].signature.domain.time_grid
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
                and node.inputs[0].node.identity == node.inputs[1].node.identity
            ) or any(prepared_ancestors.get(edge.node.identity, False) for edge in node.inputs)
            prepared_consumer = (
                isinstance(node.parameters, (OriginalReduce, CellDerive, AttributionDerive))
                and prepared_ancestors[node.identity]
            )
            if (
                not prepared_consumer
                and any(edge.node.identity in local for edge in node.inputs)
                and route != "artifact_python"
                and not isinstance(
                    node.parameters,
                    (
                        AssociationFit,
                        AssociationRead,
                        ForecastFit,
                        ForecastRead,
                        TimeRuns,
                        TimeRunRead,
                        DeviationFit,
                        DeviationRead,
                        TimeProduct,
                        AnchorRetention,
                        RetentionBySubject,
                        AnchorBind,
                        AnchorObserve,
                        PreparedObservation,
                        HistoryView,
                        HistoryRead,
                        JourneyDuration,
                        JourneyCompleted,
                        JourneyRead,
                        FunnelReduce,
                        FunnelCompare,
                        FunnelRead,
                        FunnelAttribute,
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
                and not (
                    isinstance(node.parameters, PartsTransport)
                    and node.parameters.mode == "cohort"
                    and node.parameters.opportunity_domain is not None
                    and node.parameters.opportunity_domain.kind == "journey"
                )
                and not (
                    node.inputs[0].node.signature.domain.kind in ("journey", "interval", "anchor")
                    and isinstance(node.parameters, (MapCorrespond, PartsTransport, RowState))
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
                            HistoryViewPart,
                            InstanceRetentionPart,
                            SubjectRetentionPart,
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
                            "cell.relative_change",
                            "cell.ratio",
                            "reference.share",
                            "reference.penetration",
                            "reference.standardize",
                            "attribution.additive_difference",
                            "attribution.component_mix",
                            "display.rank",
                            "display.table",
                            "funnel.reduce",
                            "funnel.compare",
                            "funnel.read",
                            "funnel_ratio_mix",
                        )
                    )
                    for edge in node.inputs
                )
            ):
                _refuse(
                    "source inputs for Ibis preparation",
                    "a local predecessor",
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
            subject_image = (
                isinstance(node.parameters, MapCorrespond)
                and node.parameters.mode == "subjects"
                and node.inputs[0].node.signature.domain.kind
                in ("occurrence", "journey", "interval", "anchor")
            )
            local_consumer = (
                prepared_consumer
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
                or (isinstance(node.parameters, AnchorRetention) and route != "ibis")
                or isinstance(node.parameters, RetentionBySubject)
                or (isinstance(node.parameters, AnchorBind) and route != "ibis")
                or (
                    isinstance(node.parameters, PartsTransport)
                    and node.parameters.mode == "cohort"
                    and node.parameters.opportunity_domain is not None
                    and node.parameters.opportunity_domain.kind == "journey"
                )
                or subject_image
                or (
                    not isinstance(node.parameters, HistoryAxesPrepare)
                    and any(
                        isinstance(
                            p,
                            (
                                ConditionCellsPart,
                                RunCellsPart,
                                PairInputsPart,
                                TrainingInputsPart,
                                FitInputsPart,
                                FunnelPart,
                                FunnelComparisonPart,
                                FunnelAllocationPart,
                                InstanceRetentionPart,
                                SubjectRetentionPart,
                                HistoryViewPart,
                            ),
                        )
                        for e in node.inputs
                        for p in e.node.signature.parts
                    )
                )
                or isinstance(
                    node.parameters,
                    (
                        AssociationFit,
                        AssociationRead,
                        ForecastFit,
                        ForecastRead,
                        TimeRuns,
                        TimeRunRead,
                        DeviationFit,
                        DeviationRead,
                        HistoryReplay,
                        JourneyMatch,
                        HistoryView,
                        HistoryRead,
                        JourneyDuration,
                        JourneyCompleted,
                        JourneyRead,
                        FunnelReduce,
                        FunnelCompare,
                        FunnelRead,
                        FunnelAttribute,
                    ),
                )
                or (
                    node.inputs[0].node.signature.domain.kind in ("journey", "interval", "anchor")
                    and isinstance(node.parameters, (PartsTransport, RowState))
                )
            )
            prepared_observation = (
                isinstance(node.parameters, (PreparedObservation, AnchorObserve))
                and route != "ibis"
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
    if any(
        isinstance(node.parameters, (PreparedObservation, AnchorObserve, FunnelAxesPrepare))
        for node in methods
    ):
        stages = [
            *(stage for stage in stages if not isinstance(stage, LocalMethodStage)),
            *(stage for stage in stages if isinstance(stage, LocalMethodStage)),
        ]
    admitted = GraphPlan(
        root, classification, tuple(stages), tuple(checks), tuple(physical), outputs[root.identity]
    )
    object.__setattr__(
        admitted,
        "_handoff",
        _PlanHandoff(ref(admitted), captured),
    )
    return admitted
