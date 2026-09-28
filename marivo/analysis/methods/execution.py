"""Consumer-specific layouts of canonically registered method semantics.

These declarations qualify the existing J1 consumers only. Their physical
encodings and receipts do not grant any implementation to the definition graph.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from marivo.analysis.methods.errors import reject
from marivo.analysis.methods.semantics import MethodSemantics
from marivo.analysis.operators.registry import (
    MethodContract,
    MethodDomain,
    MethodImplementation,
    MethodRegistration,
)

ExecutionConsumer: TypeAlias = Literal["j1.rows", "j1.rollup", "j1.group", "j1.difference"]


def _cell_policy(semantics: MethodSemantics) -> Literal["strict", "count_all"]:
    policy = semantics.cell_policy
    if policy not in ("strict", "count_all"):
        reject(
            "strict or count-all consumer semantics",
            policy,
            "Qualify the method's Cell policy before using this consumer.",
        )
    return policy


def registration(semantics: MethodSemantics, consumer: ExecutionConsumer) -> MethodRegistration:
    """Project one registered semantic owner into its qualified execution layout."""
    name = semantics.key.name
    if consumer == "j1.rows" and name in ("row.sum", "row.count", "row.mean"):
        method = name.removeprefix("row.")
        checks = tuple(
            "strict_current_row_cell" if check == "source.finite_numeric@v1" else check
            for check in semantics.required_checks
        )
        contract = MethodContract(
            method_id=f"dsl.j1.current_row_{method}",
            version=semantics.key.version,
            input_kinds=("numeric_relation",),
            input_domains=("entity", "group", "singleton"),
            output_kind="row_statistic",
            domain_policy="new",
            unit_policy=semantics.unit_policy,
            cell_policy=_cell_policy(semantics),
            numeric_policy="float64_finite"
            if method == "mean"
            else "int64_checked"
            if method == "count"
            else "int64_or_float64",
            capabilities=("current_row_state",),
            part_effect="build_current",
            required_parts=semantics.state_components,
            required_checks=checks,
            continuations=(),
            cell_reasons=semantics.empty_cell_reasons,
        )
        source_domains: tuple[MethodDomain, ...] = ("entity", "group")
        local_domains: tuple[MethodDomain, ...] = ("entity", "group", "singleton")
    elif consumer in ("j1.rollup", "j1.group") and name == "state_rollup":
        # J1 retains row_count in addition to original components for coverage.
        parts = (*(f"value.{part}" for part in semantics.state_components), "value.row_count")
        contract = MethodContract(
            method_id="dsl.j1.rollup_sum" if consumer == "j1.rollup" else "dsl.j1.group_sum",
            version=semantics.key.version,
            input_kinds=("observed",),
            input_domains=("entity", "group") if consumer == "j1.rollup" else ("entity",),
            output_kind="observed",
            domain_policy="mapped",
            unit_policy=semantics.unit_policy,
            # This consumer qualifies only strict original observations.
            cell_policy="strict",
            numeric_policy="int64_or_float64",
            capabilities=("original_state_reduction",),
            part_effect="merge_original",
            required_parts=parts,
            required_checks=tuple(
                sorted(
                    check.removeprefix("source.").removesuffix("@v1")
                    for check in semantics.required_checks
                )
            ),
            continuations=(),
            cell_reasons=semantics.empty_cell_reasons,
        )
        source_domains = local_domains = ("entity",)
    elif consumer == "j1.difference" and name == "cell.difference":
        check_names = {
            "source.exact_pairing@v1": "complete_pairing",
            "source.finite_numeric@v1": "strict_numeric_cell",
        }
        contract = MethodContract(
            method_id="dsl.j1.compare_difference",
            version=semantics.key.version,
            input_kinds=("observed", "observed"),
            input_domains=("entity",),
            output_kind="difference",
            domain_policy="same",
            unit_policy=semantics.unit_policy,
            cell_policy=_cell_policy(semantics),
            numeric_policy="int64_or_float64",
            capabilities=("cell_calculation", "part_transport"),
            part_effect="transport",
            required_parts=semantics.output_parts,
            required_checks=tuple(check_names[check] for check in semantics.required_checks),
            continuations=("where", "summarize"),
            cell_reasons=semantics.empty_cell_reasons,
        )
        source_domains = local_domains = ("entity",)
    else:
        reject(
            "a qualified method/consumer pair",
            f"{semantics.key}: {consumer}",
            "Select the exact registered execution consumer.",
        )
    return MethodRegistration(
        contract,
        (
            MethodImplementation(
                contract.method_id,
                contract.version,
                "source",
                "duckdb",
                source_domains,
                ("int64", "float64"),
                contract.required_parts,
                contract.required_checks,
                "stream",
                "producer",
            ),
            MethodImplementation(
                contract.method_id,
                contract.version,
                "local",
                "pandas",
                local_domains,
                ("int64", "float64"),
                contract.required_parts,
                contract.required_checks,
                "complete",
                "caller",
            ),
        ),
    )
