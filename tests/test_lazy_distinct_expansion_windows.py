"""Distinct expansion binds selected partial buckets to their original cumulative window."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

import ibis
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.observation.predicates import eq
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.ir import CumulativeComposition
from tests.lazy_distinct_fixtures import (
    CHANNEL,
    guard_membership_transport,
    make_distinct_registry,
    seed_distinct_database,
)
from tests.lazy_execution_fixtures import ExecutionFixture, assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort

DAY = ref.time_dimension("sales.orders.order_time")
RUNNING = ref.metric("sales.cumulative_distinct_buyers")


@contextmanager
def _fixture(path: Path, anchor: Literal["trailing", "reset"]) -> Iterator[ExecutionFixture]:
    database = path / "window.duckdb"
    seed_distinct_database(database)
    original, sidecar = make_distinct_registry(database)
    metrics = dict(original.metrics)
    metrics[RUNNING.path] = replace(
        metrics[RUNNING.path],
        composition=CumulativeComposition(
            "sales.distinct_buyers",
            DAY.path,
            ("trailing", 14, "day") if anchor == "trailing" else ("grain_to_date", "month"),
        ),
    )
    registry = replace(original, metrics=metrics)
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="session-distinct-window",
        store_id="store-distinct-window",
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        backend.raw_sql("DELETE FROM orders")
        backend.con.executemany(
            "INSERT INTO orders (id, tenant, customer_id, channel, day) VALUES (?, ?, 1, ?, ?)",
            (
                (1, "early-baseline", "legacy", "2025-12-05"),
                (2, "shared", "web", "2025-12-09"),
                (3, "shared", "store", "2025-12-15"),
                (4, "future-baseline", "future", "2025-12-19"),
                (5, "early-current", "legacy", "2026-01-02"),
                (6, "shared", "web", "2026-01-06"),
                (7, "shared", "store", "2026-01-12"),
                (8, "new", "store", "2026-01-14"),
                (9, "future-current", "future", "2026-01-16"),
            ),
        )
        yield ExecutionFixture(database, registry, sidecar, sources, backend)
    finally:
        backend.disconnect()


@pytest.mark.parametrize("anchor", ["trailing", "reset"])
def test_expansion_keeps_prior_partitions_and_clips_the_selected_partial_bucket(
    tmp_path: Path, anchor: Literal["trailing", "reset"]
) -> None:
    with _fixture(tmp_path, anchor) as fixture:
        bucket = grain("week" if anchor == "trailing" else "month")
        current = (
            fixture.sources.observe(
                RUNNING, time_scope=time_scope(start="2026-01-05", end="2026-01-15")
            )
            .with_time_axis(DAY, grain=bucket)
            .aggregate()
        )
        baseline = (
            fixture.sources.observe(
                RUNNING, time_scope=time_scope(start="2025-12-08", end="2025-12-18")
            )
            .with_time_axis(DAY, grain=bucket)
            .aggregate()
        )
        delta = current.compare(baseline)
        ordinal = 1 if anchor == "trailing" else 0
        selected = delta.where(eq(delta.fields.get("comparison_ordinal"), ordinal))
        attributed = selected.attribute(axes=(CHANNEL,))
        compiled = compile_dataset(attributed, fixture.tables(attributed))
        with guard_membership_transport():
            assert_compiled_validations(compiled.validations)
            rows = compiled.expression.select(*compiled.primary_columns).to_pyarrow()
        assert set(rows["comparison_ordinal"].to_pylist()) == {ordinal}
        assert {str(value) for value in rows["current_time"].to_pylist()} == {
            "2026-01-12" if anchor == "trailing" else "2026-01-01"
        }
        assert {str(value) for value in rows["baseline_time"].to_pylist()} == {
            "2025-12-15" if anchor == "trailing" else "2025-12-01"
        }
        # The original partial-window endpoint is three members versus two.
        # Earlier-only partitions survive expansion, and later source rows do not enter it.
        observed = {
            rows["channel"][index].as_py(): (
                rows["current_value"][index].as_py(),
                rows["baseline_value"][index].as_py(),
                rows["contribution"][index].as_py(),
            )
            for index in range(len(rows))
        }
        assert observed == {
            "legacy": (1.0, 1.0, 0.0),
            "web": (0.5, 0.5, 0.0),
            "store": (1.5, 0.5, 1.0),
        }
        assert set(rows["overall_delta"].to_pylist()) == {1.0}
