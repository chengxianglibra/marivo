from __future__ import annotations

from typing import get_args, get_type_hints

import pytest

from marivo._definition.expressions import Reducer
from marivo._definition.value_ir import (
    CoordinateSlot,
    FilterValues,
    PairStatistic,
    PresenceSlot,
    SelectValues,
    Statistic,
    ValueBinary,
    ValueLiteral,
    ValueShare,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.model.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.model.local_laws import (
    select_fusion_issue,
    selection_predicate_columns,
    short_circuit_selection_issue,
    total_selection_issue,
)
from marivo.analysis.model.optimization_rules import (
    FUSION_LAW_IDS,
    OPTIMIZATION_MILESTONE_RULES,
    OPTIMIZATION_RULE_IDS,
    SOURCE_KEY_RULE_IDS,
)
from marivo.analysis.model.summary_traits import L6Class, SummaryReducer, summary_trait


def test_summary_lookup_covers_the_closed_reducer_vocabulary() -> None:
    assert set(get_args(SummaryReducer)) == {
        *get_args(Reducer),
        *get_args(get_type_hints(Statistic)["method"]),
        *get_args(get_type_hints(PairStatistic)["method"]),
        "entity.count",
        "weighted_mean",
    }


@pytest.mark.parametrize(
    ("reducer", "l6_class", "singleton_identity", "exact_reassociation"),
    (
        ("count", "additive_count", False, False),
        ("entity.count", "additive_count", False, False),
        ("row_count", "additive_count", False, False),
        ("pair_count", "additive_count", False, False),
        ("sum", "three_state_sum", True, True),
        ("min", "extremum", True, False),
        ("max", "extremum", True, False),
        ("mean", "mean_pair", False, True),
        ("weighted_mean", "weighted_pair", False, True),
        ("first", "ordered_extremum", False, False),
        ("last", "ordered_extremum", False, False),
        ("count_distinct", "exact_set", False, False),
        ("median", "exact_multiset", False, False),
        ("quantile", "exact_multiset", False, False),
        ("pearson", "pearson_moments", False, True),
        ("spearman", "non_compressible", False, False),
        ("approx_nunique", "sketch_opaque", False, False),
        ("approx_median", "sketch_opaque", False, False),
        ("approx_quantile", "sketch_opaque", False, False),
    ),
)
def test_summary_lookup_preserves_state_class_and_nullable_singleton_identity(
    reducer: SummaryReducer,
    l6_class: L6Class,
    singleton_identity: bool,
    exact_reassociation: bool,
) -> None:
    trait = summary_trait(reducer)
    assert trait.l6_class == l6_class
    assert trait.nulls_policy == "ignore"
    assert trait.singleton_identity is singleton_identity
    assert trait.requires_exact_arithmetic_for_reassoc is exact_reassociation
    qualified = summary_trait(reducer, carrier_compatible=True, singleton_finishing_preserved=True)
    assert qualified.singleton_identity is (
        singleton_identity or reducer in ("mean", "median", "quantile")
    )


@pytest.mark.parametrize("reducer", ("mean", "median", "quantile"))
def test_scalar_finish_and_carrier_are_independent_singleton_requirements(
    reducer: SummaryReducer,
) -> None:
    # A matching Float64 carrier still permits native interpolation to yield
    # NaN for a singleton infinity. A value-preserving finish may change type.
    assert not summary_trait(reducer, carrier_compatible=True).singleton_identity
    assert not summary_trait(reducer, singleton_finishing_preserved=True).singleton_identity


@pytest.mark.parametrize("reducer", ("sum", "mean", "min", "max", "median", "quantile", "count"))
def test_strict_null_failure_blocks_singleton_erasure(reducer: SummaryReducer) -> None:
    trait = summary_trait(
        reducer,
        nulls_policy="error",
        carrier_compatible=True,
        singleton_finishing_preserved=True,
    )
    assert trait.nulls_policy == "error"
    assert not trait.singleton_identity
    assert not trait.implicit_single_value_collapse_pairs
    ignoring = summary_trait(reducer)
    assert trait.l6_class == ("strict_sum" if reducer == "sum" else ignoring.l6_class)
    assert (
        trait.requires_exact_arithmetic_for_reassoc
        == ignoring.requires_exact_arithmetic_for_reassoc
    )


def test_implicit_collapse_preserves_reducer_and_policy_pairs() -> None:
    assert {
        pair
        for reducer in get_args(SummaryReducer)
        for pair in summary_trait(reducer).implicit_single_value_collapse_pairs
    } == {
        ("count", "sum", "ignore"),
        ("entity.count", "sum", "ignore"),
        ("sum", "sum", "ignore"),
        ("min", "min", "ignore"),
        ("max", "max", "ignore"),
    }


_KEY = DataColumn("key", "key", ScalarDataType("int64"), False)
_VALUE = DataColumn("amount", "amount", DecimalDataType(38, 0), True)
_VALUE_POSITIVE = FilterValues(ValueBinary("gt", ValueSlot(0), ValueLiteral(0)))
_KEY_EQUAL = FilterValues(ValueBinary("eq", CoordinateSlot(0), ValueLiteral(2**63 - 1)))


def test_total_selection_requires_qualified_predicates() -> None:
    options = {"single_consumer": True, "same_layout": True, "original_boundary": False}
    values, columns, keys = (_VALUE,), (_KEY, _VALUE), (("key",),)
    assert (
        total_selection_issue(_VALUE_POSITIVE, _KEY_EQUAL, values, columns, keys, **options) is None
    )
    assert selection_predicate_columns(_KEY_EQUAL.predicate, values, columns, keys) == frozenset(
        ("key",)
    )
    assert selection_predicate_columns(
        _VALUE_POSITIVE.predicate, values, columns, keys
    ) == frozenset(("amount",))
    for unqualified in (
        FilterValues(PresenceSlot(("left",))),
        FilterValues(ValueBinary("eq", ValueSlot(0), ValueLiteral(10**38))),
        FilterValues(
            ValueBinary("gt", ValueBinary("div", ValueSlot(0), ValueSlot(0)), ValueLiteral(0))
        ),
    ):
        assert total_selection_issue(
            _VALUE_POSITIVE, unqualified, values, columns, keys, **options
        ) == (
            "total scalar predicates with the same drop-NA mask policy",
            "unqualified predicate or numeric operation",
        )
    assert selection_predicate_columns(
        ValueBinary("lt", ValueSlot(0), ValueLiteral(2**63)), values, columns, keys
    ) == frozenset(("amount",))
    # The ordered route gates evaluation on surviving rows, including fallible predicates.
    assert short_circuit_selection_issue(**options) is None


def test_selection_laws_preserve_intermediate_boundaries() -> None:
    for boundary, received in (
        ({"single_consumer": False}, "multiple consumers"),
        ({"same_layout": False}, "different layout"),
        ({"original_boundary": True}, "original completion"),
    ):
        options = {"single_consumer": True, "same_layout": True, "original_boundary": False}
        options.update(boundary)
        ordered_issue = short_circuit_selection_issue(**options)
        total_issue = total_selection_issue(
            _VALUE_POSITIVE, _KEY_EQUAL, (_VALUE,), (_KEY, _VALUE), (("key",),), **options
        )
        assert ordered_issue is not None and ordered_issue[1] == received
        assert total_issue == ordered_issue


def test_select_composition_requires_value_checks_and_demand_to_be_preserved() -> None:
    first = SelectValues((("source", ValueSlot(0)),))
    second = SelectValues((("shifted", ValueBinary("add", ValueSlot(0), ValueLiteral(1))),))
    options = {
        "single_consumer": True,
        "same_key_domain": True,
        "original_boundary": False,
        "protected_intermediate": False,
        "value_policy_preserved": True,
        "checks_preserved": True,
        "demand_preserved": True,
    }
    assert select_fusion_issue(first, second, **options) is None
    for removed, received in (
        ("single_consumer", "multiple consumers"),
        ("same_key_domain", "different key domain"),
        ("original_boundary", "original completion"),
        ("protected_intermediate", "output or key checkpoint"),
        ("value_policy_preserved", "changed value policy"),
        ("checks_preserved", "lost check obligation"),
        ("demand_preserved", "changed demand mask"),
    ):
        issue = select_fusion_issue(first, second, **(options | {removed: not options[removed]}))
        assert issue is not None and issue[1] == received
    cross_row = SelectValues((("partition", ValueUnary("neg", ValueShare(ValueSlot(0), ()))),))
    assert select_fusion_issue(first, cross_row, **options) == (
        "only row-local expressions",
        "partition or share expression",
    )


def test_optimization_registry_covers_milestones_and_preserves_existing_labels() -> None:
    assert {"L1", "L2", "L3", "L4", "L5", "L6", "L7", "source_block"} == FUSION_LAW_IDS
    assert set(OPTIMIZATION_MILESTONE_RULES) == {f"O{index}" for index in range(1, 10)}
    assert OPTIMIZATION_MILESTONE_RULES["O2"] == ("L1", "L5_select_fusion")
    assert {
        rule for rules in OPTIMIZATION_MILESTONE_RULES.values() for rule in rules
    } <= OPTIMIZATION_RULE_IDS
    assert {
        "source_key_fusion",
        "source_parameter_block",
        "source_population_check_fusion",
        "source_shared_key_fusion",
        "source_member_count",
        "source_output_population_coverage",
        "dead_population_transport",
    } == SOURCE_KEY_RULE_IDS
