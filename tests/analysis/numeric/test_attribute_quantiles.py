"""Independent sorting witnesses for typed incremental quantile game states."""

import itertools
import math
from decimal import Decimal
from fractions import Fraction

import pytest

from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.methods.attribute_quantiles import (
    FrequencyGame,
    FrequencyRankIndex,
    FrequencyValue,
    frequency_state,
)


def test_frequency_rank_index_retains_decimal_and_uint64_order() -> None:
    for dtype, values in (
        (DecimalDataType(20, 0), [2**64 - 1, 2**64 - 3, 2**64 - 2]),
        (
            DecimalDataType(38, 10),
            [Decimal("9007199254740993.0000000001"), Decimal("9007199254740993.0000000002")],
        ),
    ):
        state = frequency_state(dtype, ((value, 3) for value in values))
        index = FrequencyRankIndex((state,))
        index.add(state)
        assert [index.at_rank(rank) for rank in range(index.count)] == sorted(values * 3)
        index.add(state, -1)
        assert index.count == 0
        assert index.quantile(0.5, dtype) is None


@pytest.mark.parametrize("q", [0.0, 5e-324, 0.25, 0.5, 0.95, 1.0])
def test_incremental_frequencies_match_independent_sort_for_every_coalition(q: float) -> None:
    baseline: tuple[tuple[int, ...], ...] = ((5, 5), (15, 25), (40,))
    current: tuple[tuple[int, ...], ...] = ((10, 20), (30,), (40,))
    dtype = ScalarDataType("int64")
    game = FrequencyGame(
        tuple(frequency_state(dtype, ((value, 1) for value in group)) for group in baseline),
        tuple(frequency_state(dtype, ((value, 1) for value in group)) for group in current),
    )
    # Repeated arbitrary transitions exercise deletion as well as complete permutations.
    for mask in (0, 7, 1, 4, 2, 6, 3, 5, 0, 7):
        values = sorted(
            value
            for player in range(3)
            for value in (current if mask & (1 << player) else baseline)[player]
        )
        rank = Fraction(str(q)) * (len(values) - 1)
        low = rank.numerator // rank.denominator
        high = -(-rank.numerator // rank.denominator)
        expected = float(Fraction(values[low]) + (rank - low) * (values[high] - values[low]))
        assert game.value(mask, q, ScalarDataType("float64")) == expected


def test_original_median_replacement_game_has_independent_shapley_oracle() -> None:
    dtype = ScalarDataType("int64")
    baseline = ((5,), (15, 25))
    current = ((10, 20), (30,))
    game = FrequencyGame(
        tuple(frequency_state(dtype, ((value, 1) for value in group)) for group in baseline),
        tuple(frequency_state(dtype, ((value, 1) for value in group)) for group in current),
    )
    contributions = [0.0, 0.0]
    for permutation in itertools.permutations(range(2)):
        mask = 0
        before = game.value(mask, 0.5, ScalarDataType("float64"))
        assert isinstance(before, float)
        for player in permutation:
            mask |= 1 << player
            after = game.value(mask, 0.5, ScalarDataType("float64"))
            assert isinstance(after, float)
            contributions[player] += (after - before) / 2
            before = after
    assert contributions == [2.5, 2.5]


def test_frequency_null_support_and_no_row_expansion() -> None:
    dtype = ScalarDataType("int64")
    state = frequency_state(dtype, [(None, 7), (9, 1_000_000), (9, 2)])
    assert state.values == (9,)
    assert state.frequencies == (1_000_002,)
    assert state.null_count == 7 and state.support_count == 1_000_009
    index = FrequencyRankIndex((state,))
    index.add(state)
    assert len(index.values) == 1
    assert index.quantile(0.95, ScalarDataType("float64")) == 9.0


@pytest.mark.parametrize("bad", [Decimal("Infinity"), Decimal("NaN")])
def test_frequency_game_rejects_nonfinite_domain(bad: FrequencyValue) -> None:
    with pytest.raises(ValueError, match="native numeric values"):
        frequency_state(ScalarDataType("float64"), [(bad, 1)])


def test_frequency_native_nonfinite_order_and_finishing() -> None:
    dtype = ScalarDataType("float64")
    state = frequency_state(dtype, [(float("nan"), 1), (10.0, 1), (float("inf"), 1)])
    index = FrequencyRankIndex((state,))
    index.add(state)
    assert index.quantile(0.0, dtype) == 10.0
    assert index.quantile(0.5, dtype) == float("inf")
    result = index.quantile(1.0, dtype)
    assert isinstance(result, float) and math.isnan(result)


def test_distinct_union_replaces_all_group_memberships_without_double_counting() -> None:
    dtype = DecimalDataType(20, 0)
    baseline = (frequency_state(dtype, [(2**64 - 1, 5), (3, 1)]), frequency_state(dtype, [(3, 7)]))
    current = (frequency_state(dtype, [(3, 1)]), frequency_state(dtype, [(4, 2), (None, 3)]))
    game = FrequencyGame(baseline, current)
    assert [game.distinct(mask) for mask in (0, 1, 2, 3, 0)] == [2, 1, 3, 2, 2]


@pytest.mark.parametrize(
    "values", [("1.00", "1.01"), ("-1.01", "-1.00"), ("9007199254740993.01", "9007199254740993.03")]
)
@pytest.mark.parametrize("q", [0.0, 5e-324, 0.25, 0.5, 0.95, 1.0])
def test_decimal_quantile_uses_original_rounding_after_exact_rank(
    values: tuple[str, str], q: float
) -> None:
    from decimal import ROUND_HALF_UP, localcontext

    dtype = DecimalDataType(22, 2)
    native = tuple(Decimal(value) for value in values)
    state = frequency_state(dtype, ((value, 1) for value in native))
    index = FrequencyRankIndex((state,))
    index.add(state)
    with localcontext() as context:
        context.prec = 80
        expected = (native[0] + (native[1] - native[0]) * Decimal(str(q))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    assert index.quantile(q, dtype) == expected


def test_empty_frequency_game_has_no_synthetic_player() -> None:
    dtype = ScalarDataType("int64")
    game = FrequencyGame((), (), input_type=dtype)
    assert game.baseline == () and game.current == ()
    assert game.value(0, 0.5, ScalarDataType("float64")) is None
    assert game.distinct(0) == 0
