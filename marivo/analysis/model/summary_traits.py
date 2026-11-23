"""Algebraic facts about reducer state, shared by planning and verification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal, TypeAlias

from marivo._definition.types import NullPolicy

L6Class: TypeAlias = Literal[
    "additive_count",
    "three_state_sum",
    "strict_sum",
    "extremum",
    "mean_pair",
    "weighted_pair",
    "ordered_extremum",
    "exact_set",
    "exact_multiset",
    "pearson_moments",
    "non_compressible",
    "sketch_opaque",
]

SummaryReducer: TypeAlias = Literal[
    "count",
    "entity.count",
    "row_count",
    "pair_count",
    "sum",
    "min",
    "max",
    "mean",
    "weighted_mean",
    "first",
    "last",
    "count_distinct",
    "median",
    "quantile",
    "pearson",
    "spearman",
    "approx_nunique",
    "approx_median",
    "approx_quantile",
]

CollapsePair: TypeAlias = tuple[SummaryReducer, SummaryReducer, NullPolicy]


@dataclass(frozen=True, slots=True)
class SummaryTrait:
    """State class and laws to check against the bound domain and native carrier.

    The algebraic singleton law includes NA and valid NaN in a qualified value
    domain. A native replacement also needs to preserve finishing behavior,
    carrier, checks and unit. Collapse pairs require an implicit, single-valued
    field sample with no empty groups. The pair's policy is shared by both
    reductions where applicable.
    """

    l6_class: L6Class
    singleton_identity: bool = False
    requires_exact_arithmetic_for_reassoc: bool = False
    implicit_single_value_collapse_pairs: tuple[CollapsePair, ...] = ()
    nulls_policy: NullPolicy = "ignore"


_TRAITS: Mapping[SummaryReducer, SummaryTrait] = MappingProxyType(
    {
        "count": SummaryTrait(
            "additive_count", implicit_single_value_collapse_pairs=(("count", "sum", "ignore"),)
        ),
        "entity.count": SummaryTrait(
            "additive_count",
            implicit_single_value_collapse_pairs=(("entity.count", "sum", "ignore"),),
        ),
        "row_count": SummaryTrait("additive_count"),
        "pair_count": SummaryTrait("additive_count"),
        "sum": SummaryTrait(
            "three_state_sum",
            singleton_identity=True,
            requires_exact_arithmetic_for_reassoc=True,
            implicit_single_value_collapse_pairs=(("sum", "sum", "ignore"),),
        ),
        "min": SummaryTrait(
            "extremum",
            singleton_identity=True,
            implicit_single_value_collapse_pairs=(("min", "min", "ignore"),),
        ),
        "max": SummaryTrait(
            "extremum",
            singleton_identity=True,
            implicit_single_value_collapse_pairs=(("max", "max", "ignore"),),
        ),
        "mean": SummaryTrait("mean_pair", requires_exact_arithmetic_for_reassoc=True),
        "weighted_mean": SummaryTrait("weighted_pair", requires_exact_arithmetic_for_reassoc=True),
        "first": SummaryTrait("ordered_extremum"),
        "last": SummaryTrait("ordered_extremum"),
        "count_distinct": SummaryTrait("exact_set"),
        "median": SummaryTrait("exact_multiset"),
        "quantile": SummaryTrait("exact_multiset"),
        "pearson": SummaryTrait("pearson_moments", requires_exact_arithmetic_for_reassoc=True),
        "spearman": SummaryTrait("non_compressible"),
        "approx_nunique": SummaryTrait("sketch_opaque"),
        "approx_median": SummaryTrait("sketch_opaque"),
        "approx_quantile": SummaryTrait("sketch_opaque"),
    }
)


def summary_trait(
    reducer: SummaryReducer,
    *,
    nulls_policy: NullPolicy = "ignore",
    carrier_compatible: bool = False,
    singleton_finishing_preserved: bool = False,
) -> SummaryTrait:
    """Look up the state class under an already admitted NULL policy.

    Strict policies add an NA failure. For mean, median and quantile, the
    bound reducer must establish both carrier compatibility and value
    preservation on singleton completion over its full input domain.
    For example, native interpolation may turn singleton infinity into NaN
    even when the input and output are both Float64.
    Ordered and paired reductions have extra inputs beyond the scalar cell.
    """
    trait = _TRAITS[reducer]
    if nulls_policy == "error":
        return replace(
            trait,
            l6_class="strict_sum" if reducer == "sum" else trait.l6_class,
            singleton_identity=False,
            implicit_single_value_collapse_pairs=(),
            nulls_policy=nulls_policy,
        )
    if reducer in ("mean", "median", "quantile") and (
        carrier_compatible and singleton_finishing_preserved
    ):
        return replace(trait, singleton_identity=True)
    return trait
