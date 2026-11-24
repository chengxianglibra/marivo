"""Stable rule and law IDs for planning, diagnostics, and rewrite verification."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal, TypeAlias, get_args

FusionLawId: TypeAlias = Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "source_block"]

OptimizationRuleId: TypeAlias = Literal[
    # Current rules with plan decisions.
    "L1",
    "source_merge",
    "source_sample_reduction",
    "source_key_fusion",
    "source_parameter_block",
    "source_population_check_fusion",
    "source_member_count",
    "dead_population_transport",
    "source_output_population_coverage",
    "source_shared_key_fusion",
    "source_terminal_fusion",
    "pending_frame_top",
    "common_read_cse",
    "L5_select_fusion",
    "conditional_source_merge",
    "unique_keys_degenerate_per",
    "unique_keys_degenerate_over",
    "shared_output_coverage_check",
    "population_product_factorization",
    "L3_left_total_elimination",
    "classify_after_filter",
    "L7_key_pushdown",
    "implicit_per_collapse",
    "attribute_grouping_sets",
    "l6_window_prereduction",
    # Current rules without plan decisions.
    "required_slots",
    "attribute_frequency_sharing",
    "native_check_summary",
    "unique_keys",
    "encoded_time_predicate",
    "injective_time_bucket",
    "shapley_summary_state",
    "frame_evaluation_cache",
    # Reserved rules from O9.
    "frame_top_float_decimal",
    "frame_terminal_fix",
    "frame_terminal_reduce",
    "frame_terminal_over",
    "frame_terminal_align",
]

FUSION_LAW_IDS: Final[frozenset[FusionLawId]] = frozenset(get_args(FusionLawId))
OPTIMIZATION_RULE_IDS: Final[frozenset[OptimizationRuleId]] = frozenset(
    get_args(OptimizationRuleId)
)

# Source-key fusion invokes these rewrites and records their effects separately.
SOURCE_KEY_RULE_IDS: Final[frozenset[OptimizationRuleId]] = frozenset(
    {
        "source_key_fusion",
        "source_parameter_block",
        "source_population_check_fusion",
        "source_member_count",
        "dead_population_transport",
        "source_output_population_coverage",
        "source_shared_key_fusion",
    }
)

OptimizationMilestoneId: TypeAlias = Literal["O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8", "O9"]

OPTIMIZATION_MILESTONE_RULES: Final[
    Mapping[OptimizationMilestoneId, tuple[OptimizationRuleId, ...]]
] = MappingProxyType(
    {
        "O1": ("common_read_cse",),
        "O2": ("L1", "L5_select_fusion"),
        "O3": ("conditional_source_merge",),
        "O4": (
            "unique_keys_degenerate_per",
            "unique_keys_degenerate_over",
            "shared_output_coverage_check",
        ),
        "O5": (
            "population_product_factorization",
            "L3_left_total_elimination",
            "classify_after_filter",
        ),
        "O6": ("L7_key_pushdown",),
        "O7": ("l6_window_prereduction",),
        "O8": ("implicit_per_collapse", "attribute_grouping_sets"),
        "O9": (
            "frame_top_float_decimal",
            "frame_terminal_fix",
            "frame_terminal_reduce",
            "frame_terminal_over",
            "frame_terminal_align",
        ),
    }
)
