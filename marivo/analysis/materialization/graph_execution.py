"""Private R4 graph coordination before exchange and Store cutover."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from marivo.analysis.compiler.graph_plan import (
    ArtifactReadStage,
    CheckRequirement,
    GraphPlan,
    LocalCompute,
    PlannedCheck,
    QueryPlan,
    RouteChoice,
    SourceInputStage,
    SourceRead,
    Stage,
    _plan_captured,
)
from marivo.analysis.core.graph import Node, _CapturedGraph, capture_graph
from marivo.analysis.core.model import reject
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
    return _prepare_captured_graph(
        capture_graph(root, registry=registry), session_ref=session_ref, routes=routes
    )


def _prepare_captured_graph(
    captured: _CapturedGraph, *, session_ref: str, routes: tuple[RouteChoice, ...]
) -> PreparedGraph:
    binding = captured.root.signature.domain.binding
    if binding.session_id != session_ref:
        reject(
            f"a graph owned by Session {session_ref}",
            binding.session_id,
            "Rebuild the graph from this Session's exact inputs.",
            "analysis.graph_execution.session",
        )
    admitted = _plan_captured(captured, routes=routes)
    return PreparedGraph(admitted)


# R2.3 Query scheduling. The scheduler only consumes a fixed QueryPlan: it never
# opens a Store or historical Artifact, never re-selects a route and frees each
# stage output after its last planned consumer.

StageValue = TypeVar("StageValue")


def run_query_plan(
    plan: QueryPlan,
    read: Callable[[SourceRead], StageValue],
    local: Callable[[LocalCompute, tuple[StageValue, ...]], StageValue],
    check: Callable[[PlannedCheck, StageValue], None],
) -> StageValue:
    """Run every planned stage once in topological order and return the published output.

    Checks run as soon as their exact stage output is ready, before any consumer
    reads it. Both consume and publish deadlines are satisfied at that boundary.
    """
    outputs: dict[str, StageValue] = {}
    remaining = {stage_id: len(consumers) for stage_id, consumers in plan.consumers}
    for stage in plan.stages:
        if isinstance(stage, SourceRead):
            output = read(stage)
        else:
            missing = tuple(item for item in stage.inputs if item not in outputs)
            if missing:
                reject(
                    "stage inputs completed earlier in topological order",
                    repr(missing),
                    "Plan producers before their consumers.",
                    "analysis.query_schedule",
                )
            output = local(stage, tuple(outputs[item] for item in stage.inputs))
            for item in stage.inputs:
                remaining[item] -= 1
                if remaining[item] == 0 and item != plan.publish:
                    del outputs[item]
        for requirement in plan.checks:
            if requirement.stage_id == stage.stage_id:
                check(requirement, output)
        outputs[stage.stage_id] = output
    return outputs[plan.publish]
