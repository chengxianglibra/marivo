"""Private J1 methods with exact W2 source and local route qualifications."""

from __future__ import annotations

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
        MethodImplementation(
            method_id="dsl.j4.spearman",
            version=1,
            route="source_numeric",
            backend="duckdb",
            input_domains=("entity",),
            logical_types=("int64", "float64"),
            supported_parts=("pair_counts",),
            supported_checks=("complete_pairing", "spearman_pairs"),
            batch_mode="stream",
            resource_owner="producer",
        ),
        _local(
            "dsl.j4.spearman",
            parts=("pair_counts",),
            checks=("complete_pairing", "spearman_pairs"),
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
