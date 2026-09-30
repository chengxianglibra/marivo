"""Pure R3 graph admission and R4 stage handoff, with no execution imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.core.graph import FixedLeaf, MethodNode, Node, SourceLeaf, topology
from marivo.analysis.core.model import Obligation, reject
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
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
    nodes = topology(root, registry=registry)
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


@dataclass(frozen=True, slots=True)
class GraphPlan:
    root: Node
    classification: InputClassification
    stages: tuple[Stage, ...]
    checks: tuple[CheckRequirement, ...]
    physical_requirements: tuple[PhysicalRequirement, ...]
    primary_output: str


def _refuse(expected: str, received: str, repair: str) -> None:
    reject(expected, received, repair, "analysis.graph_plan")


def plan(
    root: Node,
    *,
    routes: tuple[RouteChoice, ...],
    registry: MethodRegistry = REGISTRY,
) -> GraphPlan:
    """Select explicit routes before any data work; unavailable keys remain unavailable."""
    classification = classify_inputs(root, registry=registry)
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
    if len(shapes) != 1:
        _refuse(
            "one exact input physical shape",
            "different time or source shapes",
            "Qualify a common exact shape first.",
        )
    shape = next(iter(shapes))
    nodes = topology(root, registry=registry)
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
            if (
                any(edge.node.identity in local for edge in node.inputs)
                and route != "artifact_python"
                and not all(
                    edge.node.identity not in local
                    or (
                        isinstance(edge.node, MethodNode)
                        and edge.node.method.name
                        in ("cell.difference", "cell.relative_change", "cell.ratio")
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
                shape,
                route,
            )
            selected = registry.select(
                key, tuple(e.node.signature for e in node.inputs), node.parameters
            )
            if selected.derivation != node.derivation:
                _refuse(
                    "the graph's registered derivation",
                    "different semantic owner",
                    "Rebuild with the same registry.",
                )
            implementation = selected.implementation
            inputs = tuple(outputs[e.node.identity] for e in node.inputs) + tuple(
                outputs[s.identity] for s in node.sources
            )
            physical.append(
                PhysicalRequirement(node.identity, key, node.value_type, implementation)
            )
            consume_output = output
            if route in ("ibis", "ibis_python"):
                stages.append(
                    SourceMethodStage(
                        output,
                        inputs,
                        node,
                        implementation,
                        "ibis" if route == "ibis" else "prepare",
                    )
                )
            if route != "ibis":
                local_inputs = (output,) if route == "ibis_python" else inputs
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
    return GraphPlan(
        root, classification, tuple(stages), tuple(checks), tuple(physical), outputs[root.identity]
    )
