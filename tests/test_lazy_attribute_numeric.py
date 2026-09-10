"""Independent numeric examples for complete retained Attribution partitions."""

from dataclasses import replace
from fractions import Fraction
from itertools import permutations

import pandas as pd
import pytest

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.operators.attribute_values import _sum, execute_attribute
from tests.lazy_attribute_fixtures import inputs, signed_basis_inputs


def test_finite_exact_binary_fold_survives_intermediate_floating_overflow() -> None:
    values = [1e308, 1e308, -1e308]
    expected = float(sum((Fraction(value) for value in values), Fraction(0)))
    assert expected == 1e308
    for order in set(permutations(values)):
        assert _sum(list(order)) == expected
    with pytest.raises(DatasetConstructionError, match="floating component sum overflow"):
        _sum([1e308, 1e308])


def test_additive_signed_pools_zero_total_and_typed_tie_order() -> None:
    frame, spec, parts = inputs(
        "revenue", [("b",), ("a",), (None,)], [(6, 1), (1, 1), (3, 1)], [(2, 1), (5, 1), (3, 1)]
    )
    result = execute_attribute(frame, spec, parts)
    by_region = {row.region: row for row in result.itertuples()}
    assert by_region["b"].contribution == 4 and by_region["a"].contribution == -4
    assert by_region["a"].contribution_rank == 1 and by_region["b"].contribution_rank == 2
    assert result.share_of_total_delta.isna().all()
    assert result.status.tolist() == ["zero_total_delta"] * 3
    assert by_region["a"].share_of_positive_pool == 0 and by_region["b"].share_of_negative_pool == 0
    assert by_region["a"].share_of_negative_pool == 1 and by_region["b"].share_of_positive_pool == 1


@pytest.mark.parametrize("name", ["mean_amount", "weighted_amount", "conversion_rate"])
def test_component_mix_publishes_side_terms_and_exact_scope_endpoint(name: str) -> None:
    frame, spec, parts = inputs(name, [("a",), ("b",)], [(12, 2), (18, 3)], [(6, 1), (8, 4)])
    result = execute_attribute(frame, spec, parts)
    assert result.current_value.tolist() == pytest.approx([12 / 5, 18 / 5])
    assert result.baseline_value.tolist() == pytest.approx([6 / 5, 8 / 5])
    assert result.contribution.tolist() == pytest.approx([6 / 5, 10 / 5])
    assert result.overall_delta.tolist() == pytest.approx([30 / 5 - 14 / 5] * 2)
    assert result.share_of_negative_pool.isna().all()


def test_hierarchy_reuses_mapping_including_other_parent_and_real_null() -> None:
    frame, spec, parts = inputs(
        "order_count",
        [("a", "x"), ("a", "y"), ("b", "x"), ("b", "y"), (None, "x")],
        [(20, 1), (10, 1), (3, 1), (7, 1), (5, 1)],
        [(10, 1), (5, 1), (1, 1), (2, 1), (2, 1)],
        mode="hierarchy",
        top_k=1,
    )
    result = execute_attribute(frame, spec, parts)
    records = result.to_dict(orient="records")
    for active in ((True, False), (True, True)):
        rows = [row for row in records if tuple(row["active_axis_mask"]) == active]
        assert sum(row["contribution"] for row in rows) == 25
    leaves = [row for row in records if tuple(row["active_axis_mask"]) == (True, True)]
    assert any(tuple(row["other_mask"]) == (True, True) for row in leaves)
    assert any(
        tuple(row["other_mask"]) == (True, False) and row["channel"] == "x" for row in leaves
    )


def test_missing_corrupt_and_undefined_component_states_fail_closed() -> None:
    frame, spec, parts = inputs(
        "mean_amount", [("a",), ("b",)], [(12, 2), (18, 3)], [(6, 1), (8, 4)]
    )
    with pytest.raises(DatasetConstructionError, match="missing"):
        execute_attribute(frame, spec, parts[:1])
    corrupt = parts[0].frame.copy()
    column = next(name for name in corrupt.columns if name.endswith("_sum"))
    corrupt.loc[0, column] = 999
    with pytest.raises(DatasetConstructionError, match="endpoint mismatch"):
        execute_attribute(frame, spec, (replace(parts[0], frame=corrupt), parts[1]))
    frame, spec, parts = inputs("mean_amount", [("a",)], [(0, 0)], [(0, 0)])
    with pytest.raises(DatasetConstructionError, match="null or non-finite"):
        execute_attribute(frame, spec, parts)


def test_unrequested_dimension_remains_an_independent_scope() -> None:
    frame, spec, parts = inputs(
        "revenue",
        [("a", "x"), ("b", "x"), ("a", "y"), ("b", "y")],
        [(8, 1), (2, 1), (6, 1), (4, 1)],
        [(3, 1), (2, 1), (4, 1), (5, 1)],
        decompose="first",
    )
    result = execute_attribute(frame, spec, parts)
    assert result.groupby("channel").contribution.sum().to_dict() == {"x": 5, "y": 1}
    assert result.groupby("channel").contribution_rank.max().to_dict() == {"x": 2, "y": 2}


def test_zero_contributions_keep_both_empty_pools_undefined() -> None:
    frame, spec, parts = inputs("order_count", [("a",), ("b",)], [(0, 0), (0, 0)], [(0, 0), (0, 0)])
    result = execute_attribute(frame, spec, parts)
    assert result.contribution.tolist() == [0, 0]
    assert (
        result[["share_of_total_delta", "share_of_positive_pool", "share_of_negative_pool"]]
        .isna()
        .all()
        .all()
    )


def test_scope_integer_overflow_fails_before_output() -> None:
    frame, spec, parts = inputs(
        "order_count", [("a",), ("b",)], [(2**62, 1), (2**62, 1)], [(0, 0), (0, 0)]
    )
    with pytest.raises(DatasetConstructionError, match="overflow"):
        execute_attribute(frame, spec, parts)


def test_topk_scores_the_folded_member_before_absolute_value() -> None:
    frame, spec, parts = inputs(
        "revenue",
        [("a", "x"), ("a", "y"), ("b", "x"), ("b", "y")],
        [(100, 1), (-100, 1), (10, 1), (10, 1)],
        [(0, 1)] * 4,
        top_k=1,
    )
    result = execute_attribute(frame, spec, parts)
    assert set(result.region.dropna()) == {"b"}


@pytest.mark.parametrize("name", ["order_count", "revenue"])
@pytest.mark.parametrize("top_k", [None, 1])
def test_one_sided_partitions_use_only_the_declared_exact_empty_value(
    name: str, top_k: int | None
) -> None:
    frame, spec, parts = inputs(
        name,
        [("a",), ("b",)],
        [(0, 0), (5, 1)],
        [(50, 1), (10, 1)],
        top_k=top_k,
        current_absent=(0,),
    )
    assert frame.coordinate_presence.tolist() == ["baseline_only", "matched"]
    if name == "revenue":
        with pytest.raises(DatasetConstructionError, match="null or non-finite"):
            execute_attribute(frame, spec, parts)
        return
    result = execute_attribute(frame, spec, parts)
    by_region = {row.region: row for row in result.itertuples()}
    assert by_region["a"].current_value == 0 and by_region["a"].baseline_value == 50
    assert by_region["a"].contribution == -50
    assert result.contribution.sum() == -55
    assert result.overall_delta.tolist() == [-55, -55]
    if top_k == 1:
        assert set(result.region.dropna()) == {"a"}


def test_component_mix_admits_signed_basis_with_defined_total_and_positive_support() -> None:
    frame, spec, parts = signed_basis_inputs()
    result = execute_attribute(frame, spec, parts)
    assert set(result.region.dropna()) == {"b"}
    assert result.current_value.tolist() == pytest.approx([6, 5])
    assert result.baseline_value.tolist() == pytest.approx([2, 1])
    assert result.contribution.tolist() == pytest.approx([4, 4])
    assert result.overall_delta.tolist() == pytest.approx([8, 8])
    assert result.share_of_positive_pool.tolist() == pytest.approx([0.5, 0.5])
    assert result.share_of_negative_pool.isna().all()


def test_structurally_empty_mean_partition_has_zero_side_term() -> None:
    frame, spec, parts = inputs(
        "mean_amount", [("a",), ("b",)], [(0, 0), (10, 2)], [(0, 0), (6, 2)]
    )
    result = execute_attribute(frame, spec, parts)
    assert result.current_value.tolist() == [0, 5]
    assert result.baseline_value.tolist() == [0, 3]


def test_fixed_bool_mask_equality_and_membership_use_tuple_identity() -> None:
    from marivo.analysis.observation.predicates import BoundPredicate
    from marivo.analysis.operators.row import predicate_mask

    frame, spec, parts = inputs(
        "order_count",
        [("a", "x"), ("a", "y")],
        [(4, 1), (2, 1)],
        [(1, 1), (1, 1)],
        mode="hierarchy",
    )
    result = execute_attribute(frame, spec, parts)
    field = next(
        field for field in spec.output_row.schema.columns if field.name == "active_axis_mask"
    )
    one = predicate_mask(result, BoundPredicate("eq", field, ("bool_tuple", (True, False))))
    others = predicate_mask(result, BoundPredicate("not_eq", field, ("bool_tuple", (True, False))))
    included = predicate_mask(
        result, BoundPredicate("is_in", field, (("bool_tuple", (True, False)),))
    )
    assert one.tolist() == included.tolist()
    assert one.sum() == 1 and others.sum() == 2


def test_delta_selection_selects_both_side_parts_by_original_keys() -> None:
    from marivo.analysis.operators.row import RowCall, select_parts
    from marivo.analysis.operators.row_values import frame_keys, row_key_names

    frame, spec, parts = inputs(
        "mean_amount", [("a",), ("b",)], [(12, 2), (18, 3)], [(6, 1), (8, 4)]
    )
    selected = frame.iloc[[1]].reset_index(drop=True)
    call = RowCall("delta.where", spec.input_row, spec.input_rows, spec.input_row, spec.input_rows)
    retained = select_parts(frame, selected, call, parts)
    keys = frame_keys(selected, row_key_names(spec.input_row))
    assert len(retained) == 2
    assert all(frame_keys(part.frame, part.keys) == keys for part in retained)
    result = execute_attribute(selected, spec, retained)
    assert result.contribution.tolist() == [18 / 3 - 8 / 4]


def test_decimal_topk_and_rank_preserve_more_than_28_significant_digits() -> None:
    from decimal import Decimal

    import pyarrow as pa

    from marivo.analysis.datasets.descriptors import _CORE_TOKEN, _deferred_type, _make_schema
    from marivo.analysis.observation.contracts import make_ids
    from marivo.analysis.operators.attribution_contracts import AttributionSemantics
    from marivo.analysis.operators.contracts import DeltaSemantics

    frame, spec, parts = inputs(
        "revenue", [("a",), ("b",)], [(1, 1), (2, 1)], [(0, 1), (0, 1)], top_k=1
    )
    values = [
        Decimal("10000000000000000000000000000001"),
        Decimal("10000000000000000000000000000002"),
    ]
    dtype = pd.ArrowDtype(pa.decimal128(38, 0))
    for name in ("current_value", "delta"):
        frame[name] = pd.Series(values, dtype=dtype)
    frame["baseline_value"] = pd.Series([Decimal(0)] * 2, dtype=dtype)
    updated_parts = []
    for part in parts:
        state = part.frame.copy()
        column = next(name for name in state.columns if name.endswith("_sum"))
        state[column] = pd.Series(
            values if part.role.endswith("current") else [Decimal(0)] * 2, dtype=dtype
        )
        updated_parts.append(
            replace(part, frame=state, schema=pa.Schema.from_pandas(state, preserve_index=False))
        )
    input_semantics, output_semantics = (
        spec.input_row.family_semantics,
        spec.output_row.family_semantics,
    )
    assert isinstance(input_semantics, DeltaSemantics) and isinstance(
        output_semantics, AttributionSemantics
    )
    ids = make_ids(())
    input_row = replace(
        spec.input_row,
        _token=_CORE_TOKEN,
        family_semantics=replace(input_semantics, _token=_CORE_TOKEN, numeric_type="decimal"),
        schema=_make_schema(
            tuple(
                replace(
                    field,
                    _token=_CORE_TOKEN,
                    logical_type_id="decimal",
                    physical_type_state=_deferred_type("decimal", ids=ids),
                )
                if field.name in ("current_value", "baseline_value", "delta")
                else field
                for field in spec.input_row.schema.columns
            )
        ),
    )
    output_row = replace(
        spec.output_row,
        _token=_CORE_TOKEN,
        family_semantics=replace(output_semantics, _token=_CORE_TOKEN, numeric_type="decimal"),
        schema=_make_schema(
            tuple(
                replace(
                    field,
                    _token=_CORE_TOKEN,
                    logical_type_id="decimal",
                    physical_type_state=_deferred_type("decimal", ids=ids),
                )
                if field.name
                in ("current_value", "baseline_value", "overall_delta", "contribution")
                else field
                for field in spec.output_row.schema.columns
            )
        ),
    )
    result = execute_attribute(
        frame, replace(spec, input_row=input_row, output_row=output_row), tuple(updated_parts)
    )
    assert set(result.region.dropna()) == {"b"}
    assert result.loc[result.region == "b", "contribution_rank"].tolist() == [1]
    assert sorted(result.contribution.tolist()) == values


@pytest.mark.parametrize("name", ["order_count", "mean_amount", "revenue"])
@pytest.mark.parametrize("scoped", [False, True])
def test_empty_selection_validates_only_existing_comparison_scopes(name: str, scoped: bool) -> None:
    frame, spec, parts = inputs(
        name,
        [("a", "x")] if scoped else [("a",)],
        [(1, 1)],
        [(1, 1)],
        decompose="first" if scoped else "all",
    )
    empty = frame.iloc[:0]
    empty_parts = tuple(replace(part, frame=part.frame.iloc[:0]) for part in parts)
    if not scoped and name != "order_count":
        with pytest.raises(DatasetConstructionError, match="null or non-finite"):
            execute_attribute(empty, spec, empty_parts)
    else:
        result = execute_attribute(empty, spec, empty_parts)
        assert result.empty
        assert tuple(result.columns) == tuple(
            field.name for field in spec.output_row.schema.columns
        )
