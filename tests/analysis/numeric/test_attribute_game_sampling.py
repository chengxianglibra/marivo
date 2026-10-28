"""The shared sampler caches typed values before taking complete path differences."""

from decimal import Decimal

import pytest

from marivo.analysis.core.attribute_game import CoalitionValue
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.core.frozen_calendar import FrozenKey
from marivo.analysis.methods.frame_attribute_shapley import sample_game

PLAYERS: tuple[FrozenKey, ...] = (
    ((ScalarDataType("string"), "A"),),
    ((ScalarDataType("string"), "B"),),
    ((ScalarDataType("string"), "C"),),
)


@pytest.mark.parametrize(
    "dtype,base",
    [
        (ScalarDataType("int64"), 2**53 + 1),
        (DecimalDataType(30, 2), Decimal("9007199254740993.01")),
    ],
)
def test_cached_typed_unit_marginals_survive_float64_endpoint_rounding(dtype, base) -> None:
    calls: list[int] = []
    column = DataColumn("coalition", "coalition", dtype, False)

    def evaluate(mask: int) -> CoalitionValue:
        calls.append(mask)
        return CoalitionValue(column, base + mask.bit_count())

    sample = sample_game((), PLAYERS, 64, 42, evaluate)
    assert sample.means == (1.0, 1.0, 1.0)
    assert sample.errors == (0.0, 0.0, 0.0)
    assert sample.baseline.value == base
    assert sample.current.value == base + 3
    assert len(calls) == len(set(calls)) == sample.unique_coalitions
    assert calls.count(0) == calls.count(7) == 1


def test_independent_changing_reads_are_cached_and_paths_close_on_shared_endpoints() -> None:
    calls: list[int] = []
    values: dict[int, int] = {}
    column = DataColumn("coalition", "coalition", ScalarDataType("int64"), False)

    def evaluate(mask: int) -> CoalitionValue:
        calls.append(mask)
        values[mask] = 100 * len(calls) + mask
        return CoalitionValue(column, values[mask])

    sample = sample_game((), PLAYERS, 64, 7, evaluate)
    assert len(calls) == len(set(calls)) == 8
    assert sample.baseline.value == values[0] == 100
    assert sample.current.value == values[7] == 207
    assert sum(sample.means) == pytest.approx(107, abs=sample.closure_budget)
    assert all(value >= 0 for value in sample.errors)


def test_callback_failure_rejects_whole_sample_and_does_not_retry() -> None:
    calls: list[int] = []
    column = DataColumn("coalition", "coalition", ScalarDataType("int64"), False)

    def evaluate(mask: int) -> CoalitionValue:
        calls.append(mask)
        if mask not in (0, 7):
            raise LookupError("source changed to an invalid demanded value")
        return CoalitionValue(column, mask)

    with pytest.raises(LookupError, match="invalid demanded"):
        sample_game((), PLAYERS, 64, 42, evaluate)
    assert len(calls) == 3
    assert calls[:2] == [0, 7]


def test_empty_player_domain_uses_one_shared_endpoint_read() -> None:
    calls: list[int] = []
    column = DataColumn("coalition", "coalition", ScalarDataType("int64"), False)

    def evaluate(mask: int) -> CoalitionValue:
        calls.append(mask)
        return CoalitionValue(column, 5)

    sample = sample_game((), (), 2, 0, evaluate)
    assert calls == [0]
    assert sample.means == sample.errors == ()
    assert sample.current == sample.baseline
