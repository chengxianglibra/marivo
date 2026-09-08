"""Current-row fold lowering and immutable required-state key reconciliation."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import ibis
import pyarrow as pa
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.lowering import compile_retained_rows
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.fold_contracts import (
    RetainedFoldPayload,
    coverage_columns,
    fold_part_role,
)
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from marivo.semantic.ir import CumulativeComposition
from tests.lazy_execution_fixtures import (
    execution_fixture,
    make_execution_registry,
    seed_execution_database,
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


def test_retained_filter_aggregate_joins_exact_component_parts(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "retained-compiler")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    original = sources.observe(
        (REVENUE, MEAN), population=sources.population(ref.entity("sales.customers"))
    )
    retained = original.execute()
    backend = ibis.duckdb.connect(str(database), read_only=True)
    try:
        from marivo.analysis.compiler.normalize import required_entities
        from marivo.datasource.ir import TableSourceIR

        tables = {
            entity.ref.path: backend.table(entity.source.table).select(
                *(name for name, _ in entity.columns)
            )
            for entity in required_entities(original)
            if isinstance(entity.source, TableSourceIR)
        }
        source = compile_dataset(original, tables)
        data = ibis.memtable(source.expression.to_pyarrow())
    finally:
        backend.disconnect()
    database.rename(tmp_path / "origin-offline.duckdb")
    parts = {part.role: data.select(part.column_names) for part in source.retained_parts}
    compiled = compile_retained_rows(
        retained.where(gt(REVENUE, 10)).aggregate(),
        data.select(source.primary_columns),
        parts=parts,
    )
    for check in compiled.validations:
        assert check.expression.to_pyarrow()["violations"][0].as_py() == 0, check.name
    row = compiled.expression.to_pyarrow().to_pylist()[0]
    assert row["revenue"] == 140
    assert row["mean_amount"] == pytest.approx(140 / 3)
    assert len(compiled.retained_parts) == 2
    mean_role = fold_part_role(
        next(
            authority
            for authority in original.row_contract.family_semantics.metric_folds
            if authority.metric_ref == MEAN.path
        )
    )
    projected = compile_retained_rows(
        retained.metric(MEAN),
        data.select(source.primary_columns),
        parts={mean_role: parts[mean_role]},
    )
    assert projected.primary_columns == ("entity_identity", "mean_amount")
    assert len(projected.retained_parts) == 1
    selected_part = parts[mean_role].to_pyarrow()
    for violation in ("duplicate", "foreign", "value"):
        damaged = selected_part.to_pylist()
        if violation == "value":
            sum_name = next(name for name in selected_part.column_names if name.endswith("_sum"))
            damaged[0][sum_name] += 1
        else:
            damaged[0]["entity_identity"] = (
                damaged[1]["entity_identity"] if violation == "duplicate" else {"id": 99}
            )
        invalid_parts = {
            **parts,
            mean_role: ibis.memtable(pa.Table.from_pylist(damaged, schema=selected_part.schema)),
        }
        rejected = compile_retained_rows(
            retained.metric(MEAN),
            data.select(source.primary_columns),
            parts=invalid_parts,
        )
        assert any(
            check.expression.to_pyarrow()["violations"][0].as_py() > 0
            for check in rejected.validations
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
