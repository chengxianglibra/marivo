"""Current-row fold lowering and immutable required-state key reconciliation."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.fold_contracts import (
    RetainedFoldPayload,
    coverage_columns,
)
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from marivo.semantic.ir import CumulativeComposition
from tests.lazy_execution_fixtures import (
    execution_fixture,
)

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
RATIO = ref.metric("sales.cross_root_ratio")
REGION = ref.dimension("sales.customers.region")
DAY = ref.time_dimension("sales.orders.order_time")


def test_logical_rollup_merges_current_selected_components(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        observed = (
            fixture.sources.observe(
                (REVENUE, MEAN, RATIO),
                population=fixture.sources.population(ref.entity("sales.customers")),
            )
            .with_dimensions(REGION)
            .aggregate()
        )
        selected = observed.where(gt(REVENUE, 50))
        folded = selected.rollup(drop_dimensions=(REGION,))
        assert isinstance(folded._root, LogicalRootHandle)
        assert isinstance(folded._root.payload, RetainedFoldPayload)
        compiled = compile_dataset(folded, fixture.tables(folded))
        row = compiled.expression.to_pyarrow().to_pylist()[0]
        assert row["revenue"] == 100
        assert row["mean_amount"] == 100
        assert row["cross_root_ratio"] == 0.5
        assert len(compiled.retained_parts) == 3


def test_combined_rollup_is_the_same_time_then_dimension_graph(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = (
            fixture.sources.observe(
                (REVENUE, MEAN),
                time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
            )
            .with_dimensions(REGION)
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        combined = source.rollup(drop_dimensions=(REGION,), grain=grain("month"))
        chained = source.rollup(grain=grain("month")).rollup(drop_dimensions=(REGION,))
        assert combined.definition_fingerprint == chained.definition_fingerprint
        left = compile_dataset(combined, fixture.tables(combined)).expression.to_pyarrow()
        right = compile_dataset(chained, fixture.tables(chained)).expression.to_pyarrow()
        assert left.equals(right)
        row = left.to_pylist()[0]
        assert row["revenue"] == 140
        assert row["mean_amount"] == pytest.approx(35)


@pytest.mark.runtime
def test_retained_filter_aggregate_joins_exact_component_parts(retained_r54_case) -> None:
    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import from_arrow

    case = retained_r54_case
    members = case.session.members(ms.ref.entity("sales.customer"))
    revenue = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    mean = members.observe(
        ms.ref.metric("sales.mean_amount"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed_revenue, fixed_mean = revenue.execute(), mean.execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    selected = fixed_mean.where(fixed_revenue.value.gt(10))
    assert (
        fixed_revenue.where(fixed_revenue.value.gt(10))
        .rollup()
        .execute()
        .to_pandas()
        .iloc[0]["value"]
        == 140
    )
    assert selected.rollup().execute().to_pandas().iloc[0]["value"] == pytest.approx(140 / 3)
    exchange = fixed_mean._dataset.verified()
    state = next(p for p in exchange.parts if p.role == "original_state")
    for violation in ("duplicate", "foreign", "value"):
        damaged = state.table.to_pylist()
        if violation == "value":
            damaged[0]["original_state__sum"] += 1
        else:
            damaged[0]["key_0"] = damaged[1]["key_0"] if violation == "duplicate" else "foreign"
        parts = tuple(
            replace(p, table=pa.Table.from_pylist(damaged, schema=p.table.schema))
            if p.role == state.role
            else p
            for p in exchange.parts
        )
        with pytest.raises(AnalysisError):
            from_arrow(
                exchange.primary,
                exchange.contract,
                parts=parts,
                method_state=exchange.method_state,
                completed_checks=exchange.completed_checks,
            )


def test_empty_current_rows_rollup_keeps_scalar_identity(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = fixture.sources.observe((REVENUE, ref.metric("sales.order_count")))
        source = source.with_dimensions(REGION).aggregate().where(gt(REVENUE, 1000))
        folded = source.rollup(drop_dimensions=(REGION,))
        result = compile_dataset(folded, fixture.tables(folded)).expression.to_pyarrow()
        assert result.num_rows == 1
        assert result["revenue"][0].as_py() is None
        assert result["order_count"][0].as_py() == 0


def test_cumulative_last_retains_endpoint_and_selected_period_coverage(tmp_path: Path) -> None:
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_observation_fixtures import NoIoActionPort

    with execution_fixture(tmp_path) as fixture:
        metrics = dict(fixture.registry.metrics)
        metrics["sales.running"] = replace(
            metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition(REVENUE.path, DAY.path, "all_history"),
        )
        registry = replace(fixture.registry, metrics=metrics)
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="cumulative-fold",
            store_id="cumulative-fold",
        )
        running = ref.metric("sales.running")
        daily = (
            sources.observe(running, time_scope=time_scope(start="2026-02-02", end="2026-02-05"))
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        endpoint, _, _, seconds, complete = coverage_columns(
            daily.row_contract.family_semantics.metric_folds[0]
        )
        for selected, expected_seconds, expected_complete in (
            (daily, 3 * 86400, True),
            (daily.where(gt(running, 120)), 2 * 86400, False),
        ):
            folded = selected.rollup(drop_time=True)
            row = (
                compile_dataset(folded, fixture.tables(folded))
                .expression.to_pyarrow()
                .to_pylist()[0]
            )
            assert row["running"] == 140
            assert row[endpoint] == datetime(2026, 2, 5)
            assert row[seconds] == expected_seconds
            assert row[complete] is expected_complete
        monthly = daily.rollup(grain=grain("month"))
        row = (
            compile_dataset(monthly, fixture.tables(monthly)).expression.to_pyarrow().to_pylist()[0]
        )
        assert row["running"] == 140 and row[endpoint] == datetime(2026, 2, 5)
        assert row[complete] is False
