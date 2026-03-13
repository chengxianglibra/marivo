from __future__ import annotations

from typing import Any

__all__ = [
    "CostModel",
    "DefaultQueryTranslator",
    "ExecutionFailure",
    "FederationPlanner",
    "FederationRuntime",
    "RoutingRuntime",
    "WorkflowOrchestrator",
]


def __getattr__(name: str) -> Any:
    if name == "CostModel":
        from app.execution.costing import CostModel

        return CostModel
    if name == "ExecutionFailure":
        from app.execution.errors import ExecutionFailure

        return ExecutionFailure
    if name == "DefaultQueryTranslator":
        from app.execution.translation import DefaultQueryTranslator

        return DefaultQueryTranslator
    if name == "FederationPlanner":
        from app.execution.federation import FederationPlanner

        return FederationPlanner
    if name == "FederationRuntime":
        from app.execution.federation import FederationRuntime

        return FederationRuntime
    if name == "RoutingRuntime":
        from app.execution.routing_runtime import RoutingRuntime

        return RoutingRuntime
    if name == "WorkflowOrchestrator":
        from app.execution.orchestrator import WorkflowOrchestrator

        return WorkflowOrchestrator
    raise AttributeError(name)
