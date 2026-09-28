"""Resolve existing J1 consumers without a second owner for connected methods."""

from __future__ import annotations

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.methods import registry as method_registry
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.operators.dsl_j1_contracts import (
    J1_OBSERVE_COUNT,
    J1_OBSERVE_SUM,
    J1_SELECT_DIFFERENCE,
    J3_RATIO_OBSERVE,
    J3_RATIO_ROLLUP,
    J4_SPEARMAN,
)
from marivo.analysis.operators.registry import MethodRegistration


def difference_method() -> MethodRegistration:
    return method_registry.REGISTRY.execution(MethodKey("cell.difference"), "j1.difference")


def j1_numeric_method(root: LogicalRootHandle) -> MethodRegistration | None:
    """Resolve only the numerically implemented private J1 node shapes."""
    if root.operator_id == "dsl.j1.observe":
        return J1_OBSERVE_COUNT if "count_observation@v1" in root.requirements else J1_OBSERVE_SUM
    if root.operator_id in ("dsl.j1.correlate", "dsl.j1.correlate_where"):
        return J4_SPEARMAN
    if root.operator_id == "dsl.j1.ratio_observe":
        return J3_RATIO_OBSERVE
    if root.operator_id == "dsl.j1.ratio_rollup":
        return J3_RATIO_ROLLUP
    if root.operator_id == "dsl.j1.compare":
        return difference_method()
    if (
        root.operator_id == "dsl.j1.where"
        and type(root.parameters) is tuple
        and len(root.parameters) == 3
        and root.parameters[0] == "numeric"
    ):
        return J1_SELECT_DIFFERENCE
    if root.operator_id == "dsl.j1.rollup":
        return method_registry.REGISTRY.execution(MethodKey("state_rollup"), "j1.rollup")
    if (
        root.operator_id == "dsl.j1.group"
        and type(root.parameters) is tuple
        and len(root.parameters) == 2
        and root.parameters[1] == "contribution"
    ):
        return method_registry.REGISTRY.execution(MethodKey("state_rollup"), "j1.group")
    if (
        root.operator_id in ("dsl.j1.summarize", "dsl.j1.correlate_summarize")
        and type(root.parameters) is tuple
    ):
        method = root.parameters[0] if root.parameters else None
        if not isinstance(method, str):
            return None
        keys = {
            "sum": MethodKey("row.sum"),
            "count": MethodKey("row.count"),
            "mean": MethodKey("row.mean"),
        }
        key = keys.get(method)
        return None if key is None else method_registry.REGISTRY.execution(key, "j1.rows")
    return None
