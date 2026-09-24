"""W1 J1 method semantics, with no qualified execution routes."""

from __future__ import annotations

from marivo.analysis.observation.dsl_j1 import J1_SUM_PARTS
from marivo.analysis.operators.registry import MethodContract, MethodRegistration

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
        numeric_policy="int64_checked",
        capabilities=("bind_project", "part_transport"),
        part_effect="preserve",
        required_parts=J1_SUM_PARTS,
        required_checks=("complete_coverage", "contribution_partition"),
        continuations=("rollup",),
    ),
    (),
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
        numeric_policy="int64_checked",
        capabilities=("original_state_reduction",),
        part_effect="merge_original",
        required_parts=J1_SUM_PARTS,
        required_checks=("complete_coverage", "contribution_partition"),
        continuations=(),
    ),
    (),
)
