"""Independent permutation and sorted empirical-percentile arithmetic."""

from __future__ import annotations

import math
from fractions import Fraction
from itertools import permutations

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.attribution_contracts import AttributePayload, AttributeSpecV1
from marivo.analysis.operators.distribution_values import execute_distribution
from marivo.analysis.operators.errors import AttributionError
from marivo.analysis.session._lazy_sources import make_lazy_sources
from tests.lazy_distribution_fixtures import CHANNEL, METRIC, make_distribution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def percentile(values: list[int], q: Fraction) -> Fraction:
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower, upper = math.floor(index), math.ceil(index)
    return Fraction(ordered[lower]) + (ordered[upper] - ordered[lower]) * (index - lower)


def spec() -> AttributeSpecV1:
    registry, sidecar = make_distribution_registry(q=0.7)
    source = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="numeric",
        store_id="numeric",
    )
    metric = source.observe(METRIC).with_dimensions(CHANNEL).aggregate()
    output = metric.compare(metric).attribute(axes=(CHANNEL,))
    assert isinstance(output._root, LogicalRootHandle) and isinstance(
        output._root.payload, AttributePayload
    )
    return output._root.payload.spec


def game(count: int) -> tuple[list[dict[str, object]], list[Fraction]]:
    before = [[index, index * 3 + 2] for index in range(count)]
    after = [[index - 3, index * 2 + 7, index + 8] for index in range(count)]
    values = {
        mask: percentile(
            [value for i in range(count) for value in (after[i] if mask & (1 << i) else before[i])],
            Fraction(7, 10),
        )
        for mask in range(1 << count)
    }
    expected = [Fraction(0) for _ in range(count)]
    for order in permutations(range(count)):
        mask = 0
        for index in order:
            next_mask = mask | (1 << index)
            expected[index] += values[next_mask] - values[mask]
            mask = next_mask
    expected = [value / math.factorial(count) for value in expected]
    players = [{"channel": str(i), "other_mask": [False]} for i in range(count)]
    rows = [
        {
            "__mv_player_count": count,
            "__mv_players": players,
            "__mv_coalition": mask,
            "__mv_coalition_value": float(value),
            "__mv_current_endpoint": float(values[(1 << count) - 1]),
            "__mv_baseline_endpoint": float(values[0]),
            "active_axis_mask": [True],
        }
        for mask, value in values.items()
    ]
    return rows, expected


def frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    result: pd.DataFrame = pa.Table.from_pylist(rows).to_pandas(types_mapper=pd.ArrowDtype)
    return result


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_all_coalitions_match_independent_permutations(count: int) -> None:
    rows, expected = game(count)
    result = execute_distribution(frame(rows), spec())
    contributions = {str(row.channel): float(str(row.contribution)) for row in result.itertuples()}
    for index, value in enumerate(expected):
        assert contributions[str(index)] == pytest.approx(float(value), abs=1e-12)
    assert sum(contributions.values()) == pytest.approx(float(sum(expected)))


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate", "endpoint", "nonfinite", "players", "inventory", "mask"]
)
def test_corrupt_complete_game_fails_without_output(damage: str) -> None:
    rows, _ = game(2)
    if damage == "missing":
        rows.pop(1)
    elif damage == "duplicate":
        rows[-1]["__mv_coalition"] = 0
    elif damage == "endpoint":
        for row in rows:
            row["__mv_current_endpoint"] = 999.0
    elif damage == "nonfinite":
        rows[-1]["__mv_coalition_value"] = float("nan")
    elif damage == "players":
        for row in rows:
            row["__mv_player_count"] = 9
    elif damage == "inventory":
        rows[-1]["__mv_players"] = [
            {"channel": "x", "other_mask": [False]},
            {"channel": "y", "other_mask": [False]},
        ]
    else:
        rows[-1]["active_axis_mask"] = [False]
    with pytest.raises(AttributionError):
        execute_distribution(frame(rows), spec())
