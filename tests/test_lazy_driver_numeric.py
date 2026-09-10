"""Independent numerical references for complete additive axis concentration."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import make_ids
from marivo.analysis.operators.contracts import DeltaSemantics
from marivo.analysis.operators.driver_contracts import DriverCandidatePayload
from marivo.analysis.operators.driver_values import _scalar_encoding, execute_driver
from marivo.refs import ref
from tests.lazy_attribute_fixtures import CHANNEL, REGION
from tests.lazy_driver_fixtures import driver_inputs
from tests.lazy_observation_fixtures import make_sources


def test_signed_cancellation_zero_and_null_members_count_in_cardinality() -> None:
    frame, spec, parts = driver_inputs(
        [("b",), ("a",), (None,)], [(6, 1), (1, 1), (3, 1)], [(2, 1), (5, 1), (3, 1)]
    )
    result, summary = execute_driver(frame, spec, parts=parts)
    assert result.axis_cardinality.tolist() == [3]
    assert result.concentration_member_count.tolist() == [1]
    assert result.concentration_share.tolist() == [0.5]
    assert result.score.tolist() == pytest.approx([1 / 1.003])
    assert summary.scope_count == summary.evaluated_axis_count == 1
    assert summary.reason_counts == (("axis_concentration", 1),)


def test_axes_share_scope_and_fold_signed_components_before_absolute_value() -> None:
    frame, spec, parts = driver_inputs(
        [("a", "x"), ("a", "y"), ("b", "x"), ("b", "y")],
        [(100, 1), (-100, 1), (10, 1), (10, 1)],
        [(0, 1)] * 4,
    )
    result, summary = execute_driver(frame, spec, parts=parts)
    by_axis = {row.axis_ref: row for row in result.itertuples()}
    region = by_axis[spec.definition.search_space[0]]
    channel = by_axis[spec.definition.search_space[1]]
    assert region.concentration_share == 1.0
    assert channel.concentration_share == pytest.approx(110 / 200)
    assert summary.scope_count == 1 and summary.searched_axis_count == 2
    assert result.axis_ref.tolist() == sorted(spec.definition.search_space)


def test_unsearched_dimensions_scope_limit_and_input_order_independence() -> None:
    frame, spec, parts = driver_inputs(
        [("a", "x"), ("b", "x"), ("a", "y"), ("b", "y")],
        [(8, 1), (2, 1), (6, 1), (4, 1)],
        [(3, 1), (2, 1), (4, 1), (5, 1)],
        search="first",
        limit=1,
    )
    result, summary = execute_driver(frame, spec, parts=parts)
    shuffled, shuffled_summary = execute_driver(
        frame.iloc[::-1].reset_index(drop=True), spec, parts=parts
    )
    pd.testing.assert_frame_equal(result, shuffled)
    assert summary == shuffled_summary
    assert result.channel.tolist() == ["x"]
    assert summary.scope_count == summary.pre_limit_candidate_count == 2
    assert summary.emitted_candidate_count == 1


def test_complete_zero_evaluates_but_empty_input_does_not() -> None:
    frame, spec, parts = driver_inputs(
        [("a",), ("b",)], [(0, 0), (0, 0)], [(0, 0), (0, 0)], name="order_count"
    )
    result, summary = execute_driver(frame, spec, parts=parts)
    assert (
        result.empty and summary.evaluated_axis_count == summary.zero_contribution_axis_count == 1
    )
    assert summary.score_range is None and summary.reason_counts == (("axis_concentration", 0),)
    with pytest.raises(DatasetConstructionError, match="no evaluable"):
        execute_driver(
            frame.iloc[:0], spec, parts=tuple(replace(p, frame=p.frame.iloc[:0]) for p in parts)
        )


def test_missing_corrupt_unavailable_and_nonfinite_state_never_score_as_zero() -> None:
    frame, spec, parts = driver_inputs([("a",), ("b",)], [(2, 1), (3, 1)], [(1, 1), (1, 1)])
    with pytest.raises(DatasetConstructionError, match="missing"):
        execute_driver(frame, spec, parts=parts[:1])
    for value in (999.0, float("inf")):
        corrupt = parts[0].frame.copy()
        column = next(name for name in corrupt.columns if name.endswith("_sum"))
        corrupt.loc[0, column] = value
        with pytest.raises(DatasetConstructionError):
            execute_driver(frame, spec, parts=(replace(parts[0], frame=corrupt), parts[1]))
    frame, spec, parts = driver_inputs([("a",)], [(0, 0)], [(0, 0)], current_absent=(0,))
    with pytest.raises(DatasetConstructionError, match="null or non-finite"):
        execute_driver(frame, spec, parts=parts)


def test_concentration_does_not_divide_by_tiny_net_delta() -> None:
    frame, spec, parts = driver_inputs(
        [("a",), ("b",)], [(1e200, 1), (-1e200, 1)], [(0, 1), (1e-200, 1)]
    )
    result, _ = execute_driver(frame, spec, parts=parts)
    assert result.concentration_share.tolist() == [0.5]
    assert result.score.tolist() == pytest.approx([1 / 1.002])


def test_deadline_callback_interrupts_before_result() -> None:
    frame, spec, parts = driver_inputs([("a",)], [(1, 1)], [(0, 1)])
    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise TimeoutError("test deadline")

    with pytest.raises(TimeoutError, match="deadline"):
        execute_driver(frame, spec, parts=parts, check=stop)


@pytest.mark.parametrize(
    "left,right,kind",
    [
        (-0.0, 0.0, "float64"),
        (Decimal("1.000"), Decimal("1"), "decimal"),
        (Decimal("-0.0"), Decimal("0"), "decimal"),
    ],
)
def test_digest_coordinate_encoding_has_one_representation(
    left: float | Decimal, right: float | Decimal, kind: str
) -> None:
    assert _scalar_encoding(left, kind) == _scalar_encoding(right, kind)


def test_decimal_folds_keep_more_than_28_significant_digits_before_concentration() -> None:
    frame, spec, parts = driver_inputs(
        [("a", "x"), ("a", "y"), ("b", "x"), ("b", "y")], [(1, 1)] * 4, [(0, 1)] * 4
    )
    values = [
        Decimal("10000000000000000000000000000001"),
        Decimal("-10000000000000000000000000000000"),
        Decimal(1),
        Decimal(1),
    ]
    dtype = pd.ArrowDtype(pa.decimal128(38, 0))
    for name in ("current_value", "delta"):
        frame[name] = pd.Series(values, dtype=dtype)
    frame["baseline_value"] = pd.Series([Decimal(0)] * 4, dtype=dtype)
    new_parts = []
    for part in parts:
        state = part.frame.copy()
        column = next(name for name in state.columns if name.endswith("_sum"))
        state[column] = pd.Series(
            values if part.role.endswith("current") else [Decimal(0)] * 4, dtype=dtype
        )
        new_parts.append(
            replace(part, frame=state, schema=pa.Schema.from_pandas(state, preserve_index=False))
        )
    meaning = spec.input_row.family_semantics
    assert isinstance(meaning, DeltaSemantics)
    ids = make_ids(())
    row = replace(
        spec.input_row,
        _token=d._CORE_TOKEN,
        family_semantics=replace(meaning, _token=d._CORE_TOKEN, numeric_type="decimal"),
        schema=d._make_schema(
            tuple(
                replace(
                    f,
                    _token=d._CORE_TOKEN,
                    logical_type_id="decimal",
                    physical_type_state=d._deferred_type("decimal", ids=ids),
                )
                if f.name in ("current_value", "baseline_value", "delta")
                else f
                for f in spec.input_row.schema.columns
            )
        ),
    )
    result, _ = execute_driver(frame, replace(spec, input_row=row), parts=tuple(new_parts))
    region = result[result.axis_ref == REGION.path]
    assert region.concentration_share.tolist() == pytest.approx([2 / 3])
    assert region.concentration_member_count.tolist() == [1]


def test_expanded_partition_checks_independent_fold_of_multiple_original_rows() -> None:
    frame, _, parts = driver_inputs(
        [("a", "x"), ("a", "y"), ("b", "x"), ("b", "y")],
        [(5, 1), (3, 1), (1, 1), (1, 1)],
        [(1, 1)] * 4,
    )
    original, _, original_parts = driver_inputs(
        [("a",), ("b",)], [(8, 2), (2, 2)], [(2, 2), (2, 2)]
    )
    metric = make_sources().observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    candidate = metric.compare(metric).discover.driver_axes(search_space=[REGION, CHANNEL])
    assert isinstance(candidate._root, LogicalRootHandle) and isinstance(
        candidate._root.payload, DriverCandidatePayload
    )
    spec = candidate._root.payload.spec
    result, summary = execute_driver(
        frame, spec, parts=parts, original=original, original_parts=original_parts
    )
    assert len(result) == 2 and summary.scope_count == 1
    original.loc[0, "current_value"] = 9
    original.loc[0, "delta"] = 7
    corrupt = original_parts[0].frame.copy()
    state_name = next(name for name in corrupt.columns if name.endswith("_sum"))
    corrupt.loc[0, state_name] = 9
    with pytest.raises(DatasetConstructionError, match="expanded endpoint mismatch"):
        execute_driver(
            frame,
            spec,
            parts=parts,
            original=original,
            original_parts=(replace(original_parts[0], frame=corrupt), original_parts[1]),
        )


@pytest.mark.parametrize(
    "value,text",
    [(5e-324, "0x1p-1074"), (1e-310, "0x1.2688b70e62bp-1030"), (-0.0, "0x0p+0"), (1.0, "0x1p+0")],
)
def test_subnormal_float_digest_vectors_match_source_c99_hex(value: float, text: str) -> None:
    assert _scalar_encoding(value, "float64") == "V" + text.encode().hex().upper()


@pytest.mark.parametrize(
    "values,expected_count",
    [
        ((0.1,) * 16, 8),
        ((0.30000000000000004,) * 2, 1),
        ((5e-324,) * 5, 3),
        ((8e307,) * 2, 1),
    ],
)
def test_minimal_half_boundary_uses_once_rounded_binary_prefixes(
    values: tuple[float, ...], expected_count: int
) -> None:
    frame, spec, parts = driver_inputs(
        [(str(index),) for index in range(len(values))],
        [(value, 1) for value in values],
        [(0, 1)] * len(values),
    )
    result, _ = execute_driver(frame, spec, parts=parts)
    exact_total = sum((Fraction(value) for value in values), Fraction(0))
    represented_total = Fraction(float(exact_total))
    prefixes = [
        Fraction(float(sum((Fraction(value) for value in values[:count]), Fraction(0))))
        for count in range(1, len(values) + 1)
    ]
    reference_count = next(
        count for count, prefix in enumerate(prefixes, 1) if prefix * 2 >= represented_total
    )
    assert reference_count == expected_count
    assert result.concentration_member_count.tolist() == [reference_count]
    assert result.concentration_share.tolist() == [
        float(prefixes[reference_count - 1] / represented_total)
    ]
    assert result.score.tolist() == [1 / (reference_count + len(values) / 1000)]


def test_exact_fold_cancellation_and_half_boundary_are_input_order_independent() -> None:
    values = [1e16, 0.1, -1e16]
    frame, spec, parts = driver_inputs(
        [(region, channel) for region in ("a", "b") for channel in ("x", "y", "z")],
        [(value, 1) for value in values * 2],
        [(0, 1)] * 6,
    )
    result, summary = execute_driver(frame, spec, parts=parts)
    reverse, reverse_summary = execute_driver(
        frame.iloc[::-1].reset_index(drop=True), spec, parts=parts
    )
    pd.testing.assert_frame_equal(result, reverse)
    assert summary == reverse_summary
    region = result[result.axis_ref == REGION.path]
    assert region.concentration_member_count.tolist() == [1]
    assert region.concentration_share.tolist() == [0.5]


def test_extreme_finite_fold_cancellation_preserves_both_axes_and_order() -> None:
    frame, spec, parts = driver_inputs(
        [(region, channel) for region in ("a", "b") for channel in ("x", "y", "z")],
        [(value, 1) for value in (1e308, 1e308, -1e308, -1e308, 0.0, 1e308)],
        [(0, 1)] * 6,
    )
    result, summary = execute_driver(frame, spec, parts=parts)
    reverse, reverse_summary = execute_driver(
        frame.iloc[::-1].reset_index(drop=True), spec, parts=parts
    )
    pd.testing.assert_frame_equal(result, reverse)
    assert summary == reverse_summary
    assert result.axis_ref.tolist() == [REGION.path, CHANNEL.path]
    assert result.concentration_member_count.tolist() == [1, 1]
    assert result.concentration_share.tolist() == [1.0, 1.0]
