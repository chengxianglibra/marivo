"""Private J1 methods with exact W2 source and local route qualifications."""

from __future__ import annotations

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.dsl_j1 import J1_SUM_PARTS
from marivo.analysis.operators.registry import (
    MethodContract,
    MethodDomain,
    MethodImplementation,
    MethodRegistration,
)


def _source(
    method_id: str,
    *,
    parts: tuple[str, ...],
    checks: tuple[str, ...],
    domains: tuple[MethodDomain, ...] = ("entity",),
    types: tuple[str, ...] = ("int64", "float64"),
) -> MethodImplementation:
    return MethodImplementation(
        method_id=method_id,
        version=1,
        route="source",
        backend="duckdb",
        input_domains=domains,
        logical_types=types,
        supported_parts=parts,
        supported_checks=checks,
        batch_mode="stream",
        resource_owner="producer",
    )


def _local(
    method_id: str,
    *,
    parts: tuple[str, ...],
    checks: tuple[str, ...],
    domains: tuple[MethodDomain, ...] = ("entity",),
    types: tuple[str, ...] = ("int64", "float64"),
) -> MethodImplementation:
    return MethodImplementation(
        method_id=method_id,
        version=1,
        route="local",
        backend="pandas",
        input_domains=domains,
        logical_types=types,
        supported_parts=parts,
        supported_checks=checks,
        batch_mode="complete",
        resource_owner="caller",
    )


J1_OBSERVE_SUM = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.observe_sum",
        version=1,
        input_kinds=("domain",),
        input_domains=("entity", "group"),
        output_kind="observed",
        domain_policy="same",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="int64_or_float64",
        capabilities=("bind_project", "part_transport"),
        part_effect="preserve",
        required_parts=J1_SUM_PARTS,
        required_checks=("complete_coverage", "contribution_partition"),
        continuations=("rollup",),
        cell_reasons=(("null", ("empty_contribution",)),),
    ),
    (
        _source(
            "dsl.j1.observe_sum",
            parts=J1_SUM_PARTS,
            checks=("complete_coverage", "contribution_partition"),
            domains=("entity", "group"),
        ),
    ),
)
J1_ROLLUP_SUM = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.rollup_sum",
        version=1,
        input_kinds=("observed",),
        input_domains=("entity", "group"),
        output_kind="observed",
        domain_policy="mapped",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="int64_or_float64",
        capabilities=("original_state_reduction",),
        part_effect="merge_original",
        required_parts=J1_SUM_PARTS,
        required_checks=("complete_coverage", "contribution_partition"),
        continuations=(),
        cell_reasons=(("null", ("empty_contribution",)),),
    ),
    (
        _source(
            "dsl.j1.rollup_sum",
            parts=J1_SUM_PARTS,
            checks=("complete_coverage", "contribution_partition"),
        ),
        _local(
            "dsl.j1.rollup_sum",
            parts=J1_SUM_PARTS,
            checks=("complete_coverage", "contribution_partition"),
        ),
    ),
)

J1_GROUP_SUM = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.group_sum",
        version=1,
        input_kinds=("observed",),
        input_domains=("entity",),
        output_kind="observed",
        domain_policy="mapped",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="int64_or_float64",
        capabilities=("original_state_reduction",),
        part_effect="merge_original",
        required_parts=J1_SUM_PARTS,
        required_checks=("complete_coverage", "contribution_partition"),
        continuations=(),
        cell_reasons=(("null", ("empty_contribution",)),),
    ),
    (
        _source(
            "dsl.j1.group_sum",
            parts=J1_SUM_PARTS,
            checks=("complete_coverage", "contribution_partition"),
        ),
        _local(
            "dsl.j1.group_sum",
            parts=J1_SUM_PARTS,
            checks=("complete_coverage", "contribution_partition"),
        ),
    ),
)


def _row_statistic(method: str, parts: tuple[str, ...]) -> MethodRegistration:
    method_id = "dsl.j1.current_row_" + method
    checks = ("strict_current_row_cell",) if method != "count" else ()
    return MethodRegistration(
        MethodContract(
            method_id=method_id,
            version=1,
            input_kinds=("observed",),
            input_domains=("entity", "group"),
            output_kind="row_statistic",
            domain_policy="new",
            unit_policy="count"
            if method == "count"
            else "mean"
            if method == "mean"
            else "preserve",
            cell_policy="strict",
            numeric_policy=(
                "float64_finite"
                if method == "mean"
                else "int64_checked"
                if method == "count"
                else "int64_or_float64"
            ),
            capabilities=("current_row_state",),
            part_effect="build_current",
            required_parts=parts,
            required_checks=checks,
            continuations=(),
            cell_reasons=(("undefined", ("empty_mean",)),) if method == "mean" else (),
        ),
        (
            _source(method_id, parts=parts, checks=checks, domains=("entity", "group")),
            _local(method_id, parts=parts, checks=checks, domains=("entity", "group")),
        ),
    )


J1_ROW_SUM = _row_statistic("sum", ("sum",))
J1_ROW_COUNT = _row_statistic("count", ("count",))
J1_ROW_MEAN = _row_statistic("mean", ("sum", "count"))


def j1_numeric_method(root: LogicalRootHandle) -> MethodRegistration | None:
    """Resolve only the numerically implemented private J1 node shapes."""
    if root.operator_id == "dsl.j1.observe":
        return J1_OBSERVE_SUM
    if root.operator_id == "dsl.j1.rollup":
        return J1_ROLLUP_SUM
    if (
        root.operator_id == "dsl.j1.group"
        and type(root.parameters) is tuple
        and len(root.parameters) == 2
        and root.parameters[1] == "contribution"
    ):
        return J1_GROUP_SUM
    if root.operator_id == "dsl.j1.summarize" and type(root.parameters) is tuple:
        method = root.parameters[0] if root.parameters else None
        if not isinstance(method, str):
            return None
        return {
            "sum": J1_ROW_SUM,
            "count": J1_ROW_COUNT,
            "mean": J1_ROW_MEAN,
        }.get(method)
    return None
