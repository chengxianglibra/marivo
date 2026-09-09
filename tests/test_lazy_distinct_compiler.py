"""Real source preparation preserves distinct identity, filters, and time anchors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import ibis
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.nodes import RetainedRelationSpec
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_distinct_fixtures import (
    CHANNEL,
    DISTINCT_BUYERS,
    REGION,
    guard_membership_transport,
    make_distinct_registry,
    seed_distinct_database,
)
from tests.lazy_execution_fixtures import ExecutionFixture, assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort


@contextmanager
def _fixture(path: Path) -> Iterator[ExecutionFixture]:
    database = path / "distinct.duckdb"
    seed_distinct_database(database)
    registry, sidecar = make_distinct_registry(database)
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="session-distinct-compiler",
        store_id="store-distinct-compiler",
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        yield ExecutionFixture(database, registry, sidecar, sources, backend)
    finally:
        backend.disconnect()


def _rows(dataset: LogicalDataset, fixture: ExecutionFixture) -> list[dict[str, object]]:
    compiled = compile_dataset(dataset, fixture.tables(dataset))
    with guard_membership_transport():
        assert_compiled_validations(compiled.validations)
        result = compiled.expression.select(*compiled.primary_columns).to_pyarrow()
    return [
        {name: result[name][index].as_py() for name in result.column_names}
        for index in range(len(result))
    ]


@pytest.mark.parametrize(
    "metric_name,endpoint",
    [("distinct_buyers", 1.0), ("distinct_orders", 1.0), ("distinct_web_buyers", 1.0)],
)
def test_source_scalar_entity_and_filtered_memberships_reconcile_independently(
    tmp_path: Path, metric_name: str, endpoint: float
) -> None:
    with _fixture(tmp_path) as fixture:
        metric_ref = ref.metric("sales." + metric_name)
        current = (
            fixture.sources.observe(
                metric_ref, time_scope=time_scope(start="2026-02-01", end="2026-02-05")
            )
            .with_dimensions(REGION, CHANNEL)
            .aggregate()
        )
        baseline = (
            fixture.sources.observe(
                metric_ref, time_scope=time_scope(start="2026-01-01", end="2026-01-05")
            )
            .with_dimensions(REGION, CHANNEL)
            .aggregate()
        )
        attribution = current.compare(baseline).attribute(
            axes=(REGION, CHANNEL), mode="hierarchy", top_k=1
        )
        rows = _rows(attribution, fixture)
        for mask in ((True, False), (True, True)):
            selected = [row for row in rows if row["active_axis_mask"] == list(mask)]
            assert selected and sum(
                float(str(row["contribution"])) for row in selected
            ) == pytest.approx(endpoint)
            assert {row["overall_delta"] for row in selected} == {endpoint}


def test_composite_entity_distinct_retains_complete_struct_identity_in_source(
    tmp_path: Path,
) -> None:
    with _fixture(tmp_path) as fixture:
        metric = fixture.sources.observe(ref.metric("sales.distinct_composite")).aggregate()
        compiled = compile_dataset(metric, fixture.tables(metric))
        membership = next(
            part for part in compiled.retained_parts if isinstance(part, RetainedRelationSpec)
        )
        key_type = membership.expression["__mv_distinct_key"].type()
        assert key_type.is_struct() and tuple(key_type.names) == ("tenant", "id")
        with guard_membership_transport():
            assert_compiled_validations(compiled.validations)
            counts = (
                membership.expression.aggregate(membership_count=membership.expression.count())
                .to_pyarrow()
                .to_pylist()
            )
            assert counts == [{"membership_count": 3}]
            result = compiled.expression.select(*compiled.primary_columns).to_pyarrow()
        metric_name = next(
            field.name for field in metric.schema.columns if field.role_id == "metric"
        )
        assert result[metric_name].to_pylist() == [3]


def test_distinct_logical_selection_then_axis_expansion_keeps_selected_support(
    tmp_path: Path,
) -> None:
    with _fixture(tmp_path) as fixture:
        metric = fixture.sources.observe(DISTINCT_BUYERS).with_dimensions(REGION).aggregate()
        selected = metric.where(gt(DISTINCT_BUYERS, 1))
        attribution = selected.compare(selected).attribute(axes=(CHANNEL,))
        rows = _rows(attribution, fixture)
        assert rows and all(row["region"] is not None for row in rows)
        assert all(row["contribution"] == row["overall_delta"] == 0.0 for row in rows)


def test_selected_daily_comparison_expansion_keeps_ordinal_and_fractional_membership(
    tmp_path: Path,
) -> None:
    with _fixture(tmp_path) as fixture:
        current = (
            fixture.sources.observe(
                DISTINCT_BUYERS, time_scope=time_scope(start="2026-02-01", end="2026-02-05")
            )
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        baseline = (
            fixture.sources.observe(
                DISTINCT_BUYERS, time_scope=time_scope(start="2026-01-01", end="2026-01-05")
            )
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        delta = current.compare(baseline)
        attribution = delta.where(eq(delta.fields.get("comparison_ordinal"), 1)).attribute(
            axes=(CHANNEL,)
        )
        rows = _rows(attribution, fixture)
        assert {row["comparison_ordinal"] for row in rows} == {1}
        assert {str(row["current_time"]) for row in rows} == {"2026-02-03"}
        assert {str(row["baseline_time"]) for row in rows} == {"2026-01-03"}
        by_channel = {row["channel"]: row for row in rows}
        assert by_channel["web"]["contribution"] == 0.5
        assert by_channel["store"]["contribution"] == -1.5
        assert {row["overall_delta"] for row in rows} == {-1.0}


@pytest.mark.parametrize("customer_population", [False, True])
def test_cumulative_distinct_with_selected_time_ordinal_expands_without_losing_anchor(
    tmp_path: Path,
    customer_population: bool,
) -> None:
    with _fixture(tmp_path) as fixture:
        fixture.backend.raw_sql(
            "INSERT INTO orders (id,tenant,customer_id,channel,day) "
            "VALUES (12,'historic-only',1,'legacy','2026-01-02')"
        )
        population = (
            fixture.sources.population(ref.entity("sales.customers"))
            if customer_population
            else None
        )
        metric = ref.metric("sales.cumulative_distinct_buyers")
        current = (
            fixture.sources.observe(
                metric,
                population=population,
                time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
            )
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        baseline = (
            fixture.sources.observe(
                metric,
                population=population,
                time_scope=time_scope(start="2026-01-01", end="2026-01-05"),
            )
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        delta = current.compare(baseline)
        selected = delta.where(eq(delta.fields.get("comparison_ordinal"), 2))
        attribution = selected.attribute(axes=(CHANNEL,))
        rows = _rows(attribution, fixture)
        assert rows and {row["comparison_ordinal"] for row in rows} == {2}
        assert {str(row["current_time"]) for row in rows} == {"2026-02-04"}
        assert {str(row["baseline_time"]) for row in rows} == {"2026-01-04"}
        assert sum(float(str(row["contribution"])) for row in rows) == pytest.approx(2.0)
        legacy = next(row for row in rows if row["channel"] == "legacy")
        assert legacy["current_value"] == legacy["baseline_value"] == 1.0


@pytest.mark.parametrize("scoped", [False, True])
def test_empty_selected_distinct_has_no_invented_partitions_or_scopes(
    tmp_path: Path, scoped: bool
) -> None:
    with _fixture(tmp_path) as fixture:
        metric = (
            fixture.sources.observe(DISTINCT_BUYERS).with_dimensions(REGION, CHANNEL).aggregate()
        )
        selected = metric.where(gt(DISTINCT_BUYERS, 100))
        delta = selected.compare(selected)
        attribution = delta.attribute(
            axes=(REGION,) if scoped else (REGION, CHANNEL),
            top_k=1,
            mode="joint" if scoped else "hierarchy",
        )
        assert _rows(attribution, fixture) == []
