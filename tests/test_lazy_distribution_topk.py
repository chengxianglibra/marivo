"""Independent mapped-player oracle distinguishes real null, Other and hierarchy."""

from __future__ import annotations

import math
import random
from collections import Counter
from itertools import permutations
from pathlib import Path

import duckdb
import ibis
import pandas as pd
import pytest

from marivo.analysis import time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.attribution_contracts import AttributePayload
from marivo.analysis.operators.distribution_values import execute_distribution
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.semantic._quantile import QuantileMethod, quantile_metric
from tests.lazy_distribution_fixtures import (
    CHANNEL,
    METRIC,
    REGION,
    make_distribution_registry,
    seed_distribution_database,
)
from tests.lazy_execution_fixtures import ExecutionFixture, assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort


@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
def test_null_other_and_independent_prefix_games(tmp_path: Path, method: QuantileMethod) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    data: list[tuple[int, int, str | None, str | None, float]] = []
    rng = random.Random(25)
    for side in (0, 1):
        for customer, region, channel, count in (
            (3, None, None, 6),
            (3, None, "web", 4),
            (1, "EU", "web", 3),
            (1, "EU", "store", 2),
            (2, "US", "store", 1),
        ):
            for index in range(count):
                data.append((side, customer, region, channel, float(rng.randrange(1, 100))))
    with duckdb.connect(str(database), config={"threads": 1}) as con:
        con.execute("delete from orders")
        con.executemany(
            "insert into orders (id,customer_id,channel,amount,day) values (?,?,?,?,?)",
            [
                (index, customer, channel, value, "2026-02-02" if side else "2026-01-02")
                for index, (side, customer, region, channel, value) in enumerate(data)
            ],
        )
    registry, sidecar = make_distribution_registry(database, q=0.7)
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="topk",
        store_id="topk",
    )
    backend = ibis.duckdb.connect(str(database))
    reference = duckdb.connect(config={"threads": 1})
    try:
        fixture = ExecutionFixture(database, registry, sidecar, sources, backend)
        a = (
            sources.observe(
                quantile_metric(METRIC, method=method),
                time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
            )
            .with_dimensions(REGION, CHANNEL)
            .aggregate()
        )
        b = (
            sources.observe(
                quantile_metric(METRIC, method=method),
                time_scope=time_scope(start="2026-01-01", end="2026-01-05"),
            )
            .with_dimensions(REGION, CHANNEL)
            .aggregate()
        )
        output = a.compare(b).attribute(axes=(REGION, CHANNEL), mode="hierarchy", top_k=1)
        compiled = compile_dataset(output, fixture.tables(output))
        assert_compiled_validations(compiled.validations)
        assert isinstance(output._root, LogicalRootHandle) and isinstance(
            output._root.payload, AttributePayload
        )
        result = execute_distribution(
            compiled.expression.to_pyarrow().to_pandas(types_mapper=pd.ArrowDtype),
            output._root.payload.spec,
        )
        mapped = []
        # Independently select the complete union's highest-frequency region.
        counts = Counter(row[2] for row in data)
        keep_region = sorted(counts, key=lambda key: (-counts[key], key is None, key or ""))[0]
        channels: dict[bool, Counter[str | None]] = {}
        for _, _, region, channel, _ in data:
            channels.setdefault(region != keep_region, Counter())[channel] += 1
        kept = {
            other: sorted(counts, key=lambda key: (-counts[key], key is None, key or ""))[0]
            for other, counts in channels.items()
        }
        for side, _, region, channel, value in data:
            r_other = region != keep_region
            c_other = channel != kept[r_other]
            mapped.append(
                (
                    side,
                    None if r_other else region,
                    None if c_other else channel,
                    r_other,
                    c_other,
                    value,
                )
            )

        def quantile(values: list[float]) -> float:
            if method == "duckdb_tdigest@v1":
                result = reference.execute(
                    "select approx_quantile(v, 0.7 order by v) from unnest(?::double[]) t(v)",
                    [values],
                ).fetchone()
                assert result is not None
                return float(result[0])
            values = sorted(values)
            position = (len(values) - 1) * 0.7
            low, high = math.floor(position), math.ceil(position)
            return values[low] + (values[high] - values[low]) * (position - low)

        parent_contributions = {}
        child_sum: dict[tuple[object, bool], float] = {}
        for length in (1, 2):

            def key(row: tuple[object, ...], length: int = length) -> tuple[object, ...]:
                return (
                    row[1],
                    row[2] if length == 2 else None,
                    (row[3], row[4] if length == 2 else False),
                )

            players = list(dict.fromkeys(key(row) for row in mapped))
            values = {
                mask: quantile(
                    [
                        row[5]
                        for row in mapped
                        if row[0] == int(bool(mask & (1 << players.index(key(row)))))
                    ]
                )
                for mask in range(1 << len(players))
            }
            expected = dict.fromkeys(players, 0.0)
            for order in permutations(range(len(players))):
                mask = 0
                for player_index in order:
                    changed = mask | (1 << player_index)
                    expected[players[player_index]] += (
                        values[changed] - values[mask]
                    ) / math.factorial(len(players))
                    mask = changed
            actual = {}
            for row in result.to_dict("records"):
                if tuple(row["active_axis_mask"]) != (True, length == 2):
                    continue
                player = (row["region"], row["channel"], tuple(row["other_mask"]))
                actual[player] = row["contribution"]
                if length == 1:
                    parent_contributions[(player[0], player[2][0])] = row["contribution"]
                else:
                    parent = (player[0], player[2][0])
                    child_sum[parent] = child_sum.get(parent, 0.0) + row["contribution"]
            assert actual == pytest.approx(expected)
        assert any(
            abs(parent_contributions[key] - child_sum[key]) > 1e-6 for key in parent_contributions
        )
        assert {next(iter(mask)) for mask in result.other_mask} == {False, True}
    finally:
        backend.disconnect()
        reference.close()
