from __future__ import annotations

from typing import Any

__all__ = [
    "DefaultQueryTranslator",
    "ExecutionError",
    "FederationPlanner",
    "FederationRuntime",
    "RoutingRuntime",
    "WorkflowOrchestrator",
]


def __getattr__(name: str) -> Any:
    if name == "ExecutionError":
        from marivo.execution.errors import ExecutionError

        return ExecutionError
    if name == "DefaultQueryTranslator":
        from marivo.execution.translation import DefaultQueryTranslator

        return DefaultQueryTranslator
    if name == "FederationPlanner":
        from marivo.execution.federation import FederationPlanner

        return FederationPlanner
    if name == "FederationRuntime":
        from marivo.execution.federation import FederationRuntime

        return FederationRuntime
    if name == "RoutingRuntime":
        from marivo.execution.routing_runtime import RoutingRuntime

        return RoutingRuntime
    if name == "WorkflowOrchestrator":
        from marivo.execution.orchestrator import WorkflowOrchestrator

        return WorkflowOrchestrator
    raise AttributeError(name)
