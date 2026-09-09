"""Retained distinct folds preserve the final evaluated cohort, not earlier members."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import ibis
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.distinct import membership_validations
from marivo.analysis.compiler.distinct_fold import fold_memberships
from marivo.analysis.compiler.lowering import lower_fold
from marivo.analysis.compiler.nodes import RetainedRelationSpec
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.distinct_contracts import DISTINCT_KEY_COLUMN as KEY
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.ir import CumulativeComposition
from tests.lazy_distinct_fixtures import (
    DISTINCT_ORDERS,
    guard_membership_transport,
    make_distinct_registry,
    seed_distinct_database,
)
from tests.lazy_execution_fixtures import ExecutionFixture, assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort

DAY = ref.time_dimension("sales.orders.order_time")
RUNNING = ref.metric("sales.cumulative_distinct_buyers")


@contextmanager
def _fixture(path: Path, anchor: str) -> Iterator[ExecutionFixture]:
    database = path / "distinct-fold.duckdb"
    seed_distinct_database(database)
    original, sidecar = make_distinct_registry(database)
    metrics = dict(original.metrics)
    metrics[RUNNING.path] = replace(
        metrics[RUNNING.path],
        composition=CumulativeComposition(
            "sales.distinct_buyers",
            DAY.path,
            ("trailing", 1, "day") if anchor == "trailing" else ("grain_to_date", "month"),
        ),
    )
    registry = replace(original, metrics=metrics)
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="session-distinct-fold",
        store_id="store-distinct-fold",
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        backend.raw_sql("DELETE FROM orders")
        backend.con.executemany(
            "INSERT INTO orders (id, tenant, customer_id, channel, day) VALUES (?, ?, 1, 'web', ?)",
            (
                (1, "old-first", "2026-01-30"),
                (2, "old-last", "2026-01-31"),
                (3, "fresh-first", "2026-02-02"),
                (4, "fresh-middle", "2026-02-03"),
                (5, "fresh-last", "2026-02-04"),
            ),
        )
        yield ExecutionFixture(database, registry, sidecar, sources, backend)
    finally:
        backend.disconnect()


@pytest.mark.parametrize("anchor", ["trailing", "reset"])
@pytest.mark.parametrize("operation", ["coarsen", "drop_time"])
def test_time_fold_uses_final_original_evaluation_membership(
    tmp_path: Path, anchor: str, operation: str
) -> None:
    with _fixture(tmp_path, anchor) as fixture:
        daily = (
            fixture.sources.observe(
                RUNNING, time_scope=time_scope(start="2026-01-30", end="2026-02-05")
            )
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        folded = (
            daily.rollup(grain=grain("month"))
            if operation == "coarsen"
            else daily.rollup(drop_time=True)
        )
        assert isinstance(folded._root, LogicalRootHandle)
        payload = folded._root.payload
        assert isinstance(payload, RetainedFoldPayload)
        compiled = compile_dataset(daily, fixture.tables(daily))
        parts = tuple(
            (part.role, part.expression)
            for part in compiled.retained_parts
            if isinstance(part, RetainedRelationSpec)
        )
        result, checks = lower_fold(compiled.expression, payload.spec)
        memberships = fold_memberships(parts, compiled.expression, result, payload.spec)
        with guard_membership_transport():
            assert_compiled_validations(
                (
                    *compiled.validations,
                    *checks,
                    *membership_validations(payload.spec.output_row, result, dict(memberships)),
                )
            )
            observed = result.select(
                *(field.name for field in folded.row_contract.schema.columns)
            ).to_pyarrow()
            # Source compilation exercises the integration independently of the helper call.
            integrated = compile_dataset(folded, fixture.tables(folded))
            assert_compiled_validations(integrated.validations)
            actual = integrated.expression.select(*integrated.primary_columns).to_pyarrow()
        assert actual.equals(observed)
        assert len(memberships) == 1
        relation = memberships[0][1]
        expected_latest = (
            {"fresh-last"}
            if anchor == "trailing"
            else {"fresh-first", "fresh-middle", "fresh-last"}
        )
        if operation == "drop_time":
            assert set(relation[KEY].execute().tolist()) == expected_latest
            assert actual["cumulative_distinct_buyers"].to_pylist() == [len(expected_latest)]
        else:
            time_name = next(
                field.name
                for field in folded.row_contract.schema.columns
                if field.role_id == "time_dimension"
            )
            january = relation.filter(relation[time_name] == datetime(2026, 1, 1))
            february = relation.filter(relation[time_name] == datetime(2026, 2, 1))
            assert set(january[KEY].execute().tolist()) == (
                {"old-last"} if anchor == "trailing" else {"old-first", "old-last"}
            )
            assert set(february[KEY].execute().tolist()) == expected_latest
            assert sorted(actual["cumulative_distinct_buyers"].to_pylist()) == (
                [1, 1] if anchor == "trailing" else [2, 3]
            )


def test_admitted_entity_distinct_spatial_fold_projects_original_support(tmp_path: Path) -> None:
    with _fixture(tmp_path, "trailing") as fixture:
        source = fixture.sources.observe(DISTINCT_ORDERS)
        folded = source.aggregate()
        compiled = compile_dataset(folded, fixture.tables(folded))
        with guard_membership_transport():
            assert_compiled_validations(compiled.validations)
            primary = compiled.expression.select(*compiled.primary_columns).to_pyarrow()
        assert primary["distinct_orders"].to_pylist() == [5]
        part = next(
            part for part in compiled.retained_parts if isinstance(part, RetainedRelationSpec)
        )
        assert part.expression.columns == (KEY,)
        assert part.expression.count().execute() == 5
