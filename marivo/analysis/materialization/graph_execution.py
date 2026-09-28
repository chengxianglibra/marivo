"""Private R4 graph coordination before exchange and Store cutover."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from marivo.analysis.compiler.graph_plan import (
    ArtifactReadStage,
    CheckRequirement,
    GraphPlan,
    LocalMethodStage,
    RouteChoice,
    SourceInputStage,
    SourceMethodStage,
    Stage,
    plan,
)
from marivo.analysis.core.graph import Node, topology
from marivo.analysis.core.model import reject
from marivo.analysis.methods.builtin import admit
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry


class GraphStageConsumer(Protocol):
    """Private test and future exchange consumer; no publication authority."""

    def run_stage(self, stage: Stage, inputs: tuple[object, ...]) -> object: ...

    def run_check(
        self,
        requirement: CheckRequirement,
        inputs: tuple[object, ...],
        output: object | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedGraph:
    """One admitted invocation schedule, without Run, Store or source ownership."""

    admitted: GraphPlan

    def run(self, consumer: GraphStageConsumer) -> object:
        """Consume each stage once and fulfill checks at their declared deadline."""
        results: dict[str, object] = {}
        before: dict[str, list[CheckRequirement]] = {}
        after: dict[str, list[CheckRequirement]] = {}
        for requirement in self.admitted.checks:
            target = before if requirement.obligation.before == "consume" else after
            target.setdefault(requirement.stage_output, []).append(requirement)
        for stage in self.admitted.stages:
            inputs = (
                ()
                if isinstance(stage, (SourceInputStage, ArtifactReadStage))
                else tuple(results[reference] for reference in stage.inputs)
            )
            for requirement in before.get(stage.output, ()):
                consumer.run_check(requirement, inputs, None)
            output = consumer.run_stage(stage, inputs)
            results[stage.output] = output
            for requirement in after.get(stage.output, ()):
                consumer.run_check(requirement, inputs, output)
        return results[self.admitted.primary_output]


def prepare_graph(
    root: Node,
    *,
    session_ref: str,
    routes: tuple[RouteChoice, ...],
    registry: MethodRegistry = REGISTRY,
) -> PreparedGraph:
    """Admit the reachable graph without opening sources or reading Artifacts."""
    if type(session_ref) is not str or not session_ref:
        reject(
            "one bound Session ref",
            repr(session_ref),
            "Use the owning Session for this graph.",
            "analysis.graph_execution.session",
        )
    nodes = topology(root, registry=registry)
    for node in nodes:
        if node.signature.domain.binding.session_id != session_ref:
            reject(
                f"a graph owned by Session {session_ref}",
                node.signature.domain.binding.session_id,
                "Rebuild the graph from this Session's exact inputs.",
                "analysis.graph_execution.session",
            )
    admitted = plan(root, routes=routes, registry=registry)
    stage_owners = {
        stage.output: stage.node.identity
        for stage in admitted.stages
        if isinstance(stage, (SourceMethodStage, LocalMethodStage))
    }
    for requirement in admitted.checks:
        if stage_owners.get(requirement.stage_output) != requirement.node_id:
            reject(
                "a check attached to its exact producing method stage",
                requirement.stage_output,
                "Rebuild the plan from the unchanged graph and method registration.",
                "analysis.graph_execution.check",
            )
    methods = {
        stage.node.identity: stage.node
        for stage in admitted.stages
        if isinstance(stage, (SourceMethodStage, LocalMethodStage))
    }
    for physical in admitted.physical_requirements:
        admit(physical.implementation, methods[physical.node_id].parameters)
    return PreparedGraph(admitted)
