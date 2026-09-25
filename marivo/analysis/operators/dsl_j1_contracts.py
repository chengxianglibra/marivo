"""Private J1 methods with exact W2 source and local route qualifications."""

from __future__ import annotations

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.dsl_j1 import (
    J1_COMPARE_CHECKS,
    J1_COMPARE_PARTS,
    J1_COUNT_PARTS,
    J1_SUM_PARTS,
    J3_RATIO_CHECKS,
    J3_RATIO_PARTS,
)
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
J1_OBSERVE_COUNT = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.observe_count",
        version=1,
        input_kinds=("domain",),
        input_domains=("entity",),
        output_kind="observed",
        domain_policy="same",
        unit_policy="count",
        cell_policy="strict",
        numeric_policy="int64_checked",
        capabilities=("bind_project", "part_transport"),
        part_effect="preserve",
        required_parts=J1_COUNT_PARTS,
        required_checks=("complete_coverage", "contribution_partition"),
        continuations=(),
    ),
    (
        _source(
            "dsl.j1.observe_count",
            parts=J1_COUNT_PARTS,
            checks=("complete_coverage", "contribution_partition"),
            types=("int64",),
        ),
    ),
)

J4_SPEARMAN = MethodRegistration(
    MethodContract(
        method_id="dsl.j4.spearman",
        version=1,
        input_kinds=("observed", "observed"),
        input_domains=("entity",),
        output_kind="association",
        domain_policy="new",
        unit_policy="coefficient",
        cell_policy="spearman_pairs",
        numeric_policy="pair_ranks",
        capabilities=("cell_calculation", "part_transport"),
        part_effect="transport",
        required_parts=("pair_counts",),
        required_checks=("complete_pairing", "spearman_pairs"),
        continuations=("where", "summarize"),
    ),
    (
        _source(
            "dsl.j4.spearman",
            parts=("pair_counts",),
            checks=("complete_pairing", "spearman_pairs"),
        ),
        _local(
            "dsl.j4.spearman",
            parts=("pair_counts",),
            checks=("complete_pairing", "spearman_pairs"),
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
            input_kinds=("numeric_relation",),
            input_domains=("entity", "group", "singleton"),
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
            _local(method_id, parts=parts, checks=checks, domains=("entity", "group", "singleton")),
        ),
    )


J1_ROW_SUM = _row_statistic("sum", ("sum",))
J1_ROW_COUNT = _row_statistic("count", ("count",))
J1_ROW_MEAN = _row_statistic("mean", ("sum", "count"))

J1_COMPARE_DIFFERENCE = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.compare_difference",
        version=1,
        input_kinds=("observed", "observed"),
        input_domains=("entity",),
        output_kind="difference",
        domain_policy="same",
        unit_policy="difference",
        cell_policy="strict",
        numeric_policy="int64_or_float64",
        capabilities=("cell_calculation", "part_transport"),
        part_effect="transport",
        required_parts=J1_COMPARE_PARTS,
        required_checks=J1_COMPARE_CHECKS,
        continuations=("where", "summarize"),
        cell_reasons=(),
    ),
    (
        _source(
            "dsl.j1.compare_difference",
            parts=J1_COMPARE_PARTS,
            checks=J1_COMPARE_CHECKS,
        ),
        _local(
            "dsl.j1.compare_difference",
            parts=J1_COMPARE_PARTS,
            checks=J1_COMPARE_CHECKS,
        ),
    ),
)

J1_SELECT_DIFFERENCE = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.select_difference",
        version=1,
        input_kinds=("difference",),
        input_domains=("entity",),
        output_kind="difference",
        domain_policy="mapped",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="int64_or_float64",
        capabilities=("cell_calculation", "part_transport"),
        part_effect="transport",
        required_parts=J1_COMPARE_PARTS,
        required_checks=J1_COMPARE_CHECKS,
        continuations=("members", "summarize"),
        cell_reasons=(),
    ),
    (
        _source(
            "dsl.j1.select_difference",
            parts=J1_COMPARE_PARTS,
            checks=J1_COMPARE_CHECKS,
        ),
        _local(
            "dsl.j1.select_difference",
            parts=J1_COMPARE_PARTS,
            checks=J1_COMPARE_CHECKS,
        ),
    ),
)


J3_RATIO_OBSERVE = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.ratio_observe",
        version=1,
        input_kinds=("domain",),
        input_domains=("entity",),
        output_kind="observed",
        domain_policy="same",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="float64_finite",
        capabilities=("bind_project", "part_transport"),
        part_effect="preserve",
        required_parts=J3_RATIO_PARTS,
        required_checks=J3_RATIO_CHECKS,
        continuations=("rollup", "summarize"),
        cell_reasons=(("undefined", ("zero_denominator",)),),
    ),
    (
        _source(
            "dsl.j1.ratio_observe", parts=J3_RATIO_PARTS, checks=J3_RATIO_CHECKS, types=("float64",)
        ),
    ),
)

J3_RATIO_ROLLUP = MethodRegistration(
    MethodContract(
        method_id="dsl.j1.ratio_rollup",
        version=1,
        input_kinds=("observed",),
        input_domains=("entity", "group"),
        output_kind="observed",
        domain_policy="mapped",
        unit_policy="preserve",
        cell_policy="strict",
        numeric_policy="float64_finite",
        capabilities=("original_state_reduction",),
        part_effect="merge_original",
        required_parts=J3_RATIO_PARTS,
        required_checks=J3_RATIO_CHECKS,
        continuations=(),
        cell_reasons=(("undefined", ("zero_denominator",)),),
    ),
    (
        _source(
            "dsl.j1.ratio_rollup",
            parts=J3_RATIO_PARTS,
            checks=J3_RATIO_CHECKS,
            domains=("entity", "group"),
            types=("float64",),
        ),
        _local(
            "dsl.j1.ratio_rollup",
            parts=J3_RATIO_PARTS,
            checks=J3_RATIO_CHECKS,
            domains=("entity", "group"),
            types=("float64",),
        ),
    ),
)


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
        return J1_COMPARE_DIFFERENCE
    if (
        root.operator_id == "dsl.j1.where"
        and type(root.parameters) is tuple
        and len(root.parameters) == 3
        and root.parameters[0] == "numeric"
    ):
        return J1_SELECT_DIFFERENCE
    if root.operator_id == "dsl.j1.rollup":
        return J1_ROLLUP_SUM
    if (
        root.operator_id == "dsl.j1.group"
        and type(root.parameters) is tuple
        and len(root.parameters) == 2
        and root.parameters[1] == "contribution"
    ):
        return J1_GROUP_SUM
    if (
        root.operator_id in ("dsl.j1.summarize", "dsl.j1.correlate_summarize")
        and type(root.parameters) is tuple
    ):
        method = root.parameters[0] if root.parameters else None
        if not isinstance(method, str):
            return None
        return {
            "sum": J1_ROW_SUM,
            "count": J1_ROW_COUNT,
            "mean": J1_ROW_MEAN,
        }.get(method)
    return None
