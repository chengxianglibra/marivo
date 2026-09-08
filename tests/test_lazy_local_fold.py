"""Independent numeric and complete-part tests for the exact local fold executor."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

from marivo._temporal import builtin_grain, certify_period_calendar, semantic_grain
from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import (
    LocalBudget,
    LocalPolicy,
    collect_part,
    collect_primary,
    execute_retained_suffix,
    to_local_frame,
    to_part_frame,
)
from marivo.analysis.materialization.local_worker import (
    LocalPartInput,
    LocalRequest,
    StreamInput,
    supervise,
)
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricPayload,
)
from marivo.analysis.observation.fold_contracts import (
    FoldSpecV1,
    RetainedFoldPayload,
    coverage_columns,
)
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.rollup import bucket_bounds, validate_parts
from marivo.analysis.operators.row import PartFrame, RowCall, row_key_names
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    LinearTerm,
    RatioComposition,
)
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import ExecutionFixture, execution_fixture
from tests.lazy_observation_fixtures import NoIoActionPort

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
WEIGHTED = ref.metric("sales.weighted_amount")
CUSTOMERS = ref.entity("sales.customers")


def _source(fixture: ExecutionFixture) -> LogicalMetricDataset:
    return fixture.sources.observe(
        [REVENUE, MEAN, WEIGHTED], population=fixture.sources.population(CUSTOMERS)
    )


def _call(
    source: LogicalMetricDataset, output: LogicalMetricDataset, *, fold: bool = False
) -> RowCall:
    root = output._root
    assert isinstance(root, LogicalRootHandle)
    if isinstance(root.payload, RetainedFoldPayload):
        return RowCall(
            root.operator_id,
            source.row_contract,
            source.row_set_contract,
            output.row_contract,
            output.row_set_contract,
            fold=root.payload.spec,
        )
    assert isinstance(root.payload, MetricPayload)
    payload = root.payload
    return RowCall(
        root.operator_id,
        source.row_contract,
        source.row_set_contract,
        output.row_contract,
        output.row_set_contract,
        payload.predicate,
        payload.rank,
        payload.limit_count,
        FoldSpecV1(
            "entity",
            source.row_contract.key_field_ids,
            None,
            False,
            source.row_contract,
            output.row_contract,
        )
        if fold
        else None,
    )


def _wide(
    fixture: ExecutionFixture, source: LogicalMetricDataset
) -> tuple[pa.Table, tuple[LocalPartInput, ...]]:
    compiled = compile_dataset(source, fixture.tables(source))
    table = compiled.expression.to_pyarrow()
    keys = row_key_names(source.row_contract)
    return table, tuple(
        LocalPartInput(
            part.role,
            part.contract_id,
            part.contract_version,
            pa.schema([table.schema.field(name) for name in part.column_names]),
            keys,
        )
        for part in compiled.retained_parts
    )


def _frames(
    fixture: ExecutionFixture,
    source: LogicalMetricDataset,
) -> tuple[pd.DataFrame, tuple[PartFrame, ...], LocalBudget]:
    table, specs = _wide(fixture, source)
    budget = LocalBudget(LocalPolicy(), time.monotonic() + 60)
    primary = collect_primary(
        table.select([field.name for field in source.schema.columns]).to_batches(),
        source.row_contract,
        source.row_set_contract,
        budget,
    )
    frame = to_local_frame(primary, source.row_contract, budget)
    parts = tuple(
        PartFrame(
            spec.role,
            spec.contract_id,
            spec.contract_version,
            spec.schema,
            spec.keys,
            to_part_frame(
                collect_part(
                    table.select(spec.schema.names).to_batches(), spec.schema, spec.keys, budget
                ),
                source.row_contract,
                budget,
            ),
        )
        for spec in specs
    )
    return frame, parts, budget


def _rebind(fixture: ExecutionFixture, registry: Registry) -> ExecutionFixture:
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=fixture.sidecar,
        action_port=NoIoActionPort(),
        session_id="local-fold-reference",
        store_id="local-fold-reference",
    )
    return replace(fixture, registry=registry, sources=sources)


def test_selected_mean_and_weighted_mean_use_exact_current_contributions(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = _source(fixture)
        selected = source.where(gt(REVENUE, 10))
        output = selected.aggregate()
        frame, parts, budget = _frames(fixture, source)
        # Part physical order is independent of primary order; keys own the join.
        parts = tuple(
            replace(part, frame=part.frame.iloc[::-1].reset_index(drop=True)) for part in parts
        )
        result, retained, handoffs = execute_retained_suffix(
            frame, parts, (_call(source, selected), _call(selected, output, fold=True)), budget
        )
        assert result["revenue"].tolist() == [140]
        assert result["mean_amount"].tolist() == pytest.approx([140 / 3])
        assert result["weighted_amount"].tolist() == [50]
        assert len(retained) == 3 and all(
            len(part.frame) == 1 and part.keys == () for part in retained
        )
        assert handoffs[0][1] == handoffs[1][0]


@pytest.mark.parametrize(
    "aggregation,expected", [("sum", 147), ("count", 5), ("min", 0), ("max", 100), ("mean", 29.4)]
)
def test_each_simple_component_fold_matches_independent_numbers(
    tmp_path: Path, aggregation: Literal["sum", "count", "min", "max", "mean"], expected: float
) -> None:
    with execution_fixture(tmp_path) as original:
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics[REVENUE.path] = replace(
            registry.metrics[REVENUE.path], aggregation=aggregation
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(REVENUE, population=fixture.sources.population(CUSTOMERS))
        frame, parts, budget = _frames(fixture, source)
        result, _, _ = execute_retained_suffix(
            frame, parts, (_call(source, source.aggregate(), fold=True),), budget
        )
        assert result["revenue"].tolist() == pytest.approx([expected])


def test_linear_fold_finalizes_independent_component_totals(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics["sales.weight_total"] = replace(
            registry.metrics[REVENUE.path],
            semantic_id="sales.weight_total",
            name="weight_total",
            measure="sales.orders.weight",
            aggregation_target="sales.orders.weight",
        )
        registry.metrics["sales.net"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.net",
            name="net",
            composition=LinearComposition(
                (LinearTerm("+", REVENUE.path), LinearTerm("-", "sales.weight_total"))
            ),
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(
            ref.metric("sales.net"), population=fixture.sources.population(CUSTOMERS)
        )
        frame, parts, budget = _frames(fixture, source)
        result, _, _ = execute_retained_suffix(
            frame, parts, (_call(source, source.aggregate(), fold=True),), budget
        )
        assert result["net"].tolist() == [137]


def test_combined_time_dimension_fold_passes_current_state_directly(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        region = ref.dimension("sales.customers.region")
        day = ref.time_dimension("sales.orders.order_time")
        source = (
            fixture.sources.observe(
                [REVENUE, MEAN, WEIGHTED],
                population=fixture.sources.population(CUSTOMERS),
                time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
            )
            .with_dimensions(region)
            .with_time_axis(day, grain=grain("day"))
            .aggregate()
        )
        output = source.rollup(drop_dimensions=(region,), grain=grain("month"))
        intermediate = output._inputs[0]
        assert isinstance(intermediate, LogicalMetricDataset)
        frame, parts, budget = _frames(fixture, source)
        result, retained, handoffs = execute_retained_suffix(
            frame, parts, (_call(source, intermediate), _call(intermediate, output)), budget
        )
        assert result["order_time"].tolist() == [date(2026, 2, 1)]
        assert result["revenue"].tolist() == [140]
        assert result["mean_amount"].tolist() == [35]
        assert result["weighted_amount"].tolist() == [50]
        assert all(part.keys == ("order_time",) for part in retained)
        assert handoffs[0][1] == handoffs[1][0]


def test_rank_limit_selects_parts_by_contribution_key_and_projection_drops_roles(
    tmp_path: Path,
) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = _source(fixture)
        rank = source.rank(source.fields.metric(REVENUE))
        limited = rank.limit(2)
        projected = limited.metric(MEAN)
        frame, parts, budget = _frames(fixture, source)
        result, retained, handoffs = execute_retained_suffix(
            frame,
            parts,
            (_call(source, rank), _call(rank, limited), _call(limited, projected)),
            budget,
        )
        assert result["entity_identity"].tolist() == [(2,), (1,)]
        assert result["mean_amount"].tolist() == [100, 20]
        assert len(retained) == 1
        assert retained[0].frame["entity_identity"].tolist() == [(2,), (1,)]
        assert "rank" not in retained[0].schema.names
        assert handoffs[0][1] == handoffs[1][0] and handoffs[1][1] == handoffs[2][0]


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "foreign"])
def test_fold_rejects_incomplete_current_part_keys(tmp_path: Path, corruption: str) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = _source(fixture)
        frame, parts, budget = _frames(fixture, source)
        first = parts[0]
        changed = first.frame.iloc[:-1].copy() if corruption == "missing" else first.frame.copy()
        if corruption == "duplicate":
            changed.iloc[0, 0] = changed.iloc[1, 0]
        elif corruption == "foreign":
            changed["entity_identity"] = pd.Series(
                [(999,), *changed["entity_identity"].tolist()[1:]], dtype=object
            )
        with pytest.raises(DatasetCompilationError, match="alignment"):
            execute_retained_suffix(
                frame,
                (replace(first, frame=changed), *parts[1:]),
                (_call(source, source.aggregate(), fold=True),),
                budget,
            )


def test_consumed_part_value_must_reconcile_before_row_selection(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = _source(fixture)
        frame, parts, budget = _frames(fixture, source)
        first = parts[0]
        changed = first.frame.copy(deep=True)
        total = next(name for name in changed.columns if name.endswith("_sum"))
        changed[total] = changed[total] + 1
        selected = source.where(gt(REVENUE, 1000))
        with pytest.raises(DatasetCompilationError, match="differs from primary"):
            execute_retained_suffix(
                frame,
                (replace(first, frame=changed), *parts[1:]),
                (_call(source, selected),),
                budget,
            )


def test_empty_selected_scalar_has_owned_null_and_empty_semantics(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = _source(fixture)
        selected = source.where(gt(REVENUE, 1000))
        output = selected.aggregate()
        frame, parts, budget = _frames(fixture, source)
        actual, retained, _ = execute_retained_suffix(
            frame, parts, (_call(source, selected), _call(selected, output, fold=True)), budget
        )
        expected = (
            compile_dataset(output, fixture.tables(output))
            .expression.to_pyarrow()
            .select([field.name for field in output.schema.columns])
            .to_pandas(types_mapper=pd.ArrowDtype)
        )
        assert actual.equals(expected)
        assert all(len(part.frame) == 1 for part in retained)


def test_selected_ratio_uses_selected_computational_denominator(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql("DELETE FROM orders")
        original.backend.raw_sql(
            "INSERT INTO orders (id,customer_id,amount,weight) VALUES (1,1,8,10),(2,2,1,10)"
        )
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics["sales.denominator"] = replace(
            registry.metrics[REVENUE.path],
            semantic_id="sales.denominator",
            name="denominator",
            measure="sales.orders.weight",
            aggregation_target="sales.orders.weight",
        )
        registry.metrics["sales.conversion_rate"] = replace(
            registry.metrics["sales.conversion_rate"],
            composition=RatioComposition(REVENUE.path, "sales.denominator"),
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(
            [REVENUE, ref.metric("sales.conversion_rate")],
            population=fixture.sources.population(CUSTOMERS),
        )
        selected = source.where(gt(REVENUE, 5))
        frame, parts, budget = _frames(fixture, source)
        result, _, _ = execute_retained_suffix(
            frame,
            parts,
            (_call(source, selected), _call(selected, selected.aggregate(), fold=True)),
            budget,
        )
        assert result["conversion_rate"].tolist() == [0.8]


@pytest.mark.parametrize("selection", ["complete", "partial", "empty"])
def test_cumulative_time_fold_selects_latest_state_and_tracks_selected_coverage(
    tmp_path: Path, selection: str
) -> None:
    with execution_fixture(tmp_path) as original:
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        day = ref.time_dimension("sales.orders.order_time")
        registry.metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition(REVENUE.path, day.path),
        )
        fixture = _rebind(original, registry)
        running = ref.metric("sales.running")
        source = (
            fixture.sources.observe(
                running, time_scope=time_scope(start="2026-02-02", end="2026-02-05")
            )
            .with_time_axis(day, grain=grain("day"))
            .aggregate()
        )
        frame, parts, budget = _frames(fixture, source)
        calls: tuple[RowCall, ...] = ()
        current = source
        selected = selection != "complete"
        if selected:
            current = source.where(gt(running, 1000 if selection == "empty" else 120))
            calls = (_call(source, current),)
        output = current.rollup(drop_time=True)
        result, retained, _ = execute_retained_suffix(
            frame, parts, (*calls, _call(current, output)), budget
        )
        validate_parts(result, retained, output.row_contract)
        semantics = source.row_contract.family_semantics
        assert isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics))
        authority = semantics.metric_folds[0]
        endpoint, start, end, seconds, complete = coverage_columns(authority)
        if selection == "empty":
            assert len(result) == 1 and result["running"].isna().all()
            assert retained[0].frame[[endpoint, start, end]].isna().all().all()
            assert retained[0].frame[seconds].tolist() == [0.0]
            assert retained[0].frame[complete].tolist() == [False]
            return
        assert result["running"].tolist() == [140]
        assert retained[0].frame[start].tolist() == [
            pd.Timestamp("2026-02-03" if selected else "2026-02-02")
        ]
        assert retained[0].frame[end].tolist() == [pd.Timestamp("2026-02-05")]
        assert retained[0].frame[seconds].tolist() == [86400.0 * (2 if selected else 3)]
        assert retained[0].frame[complete].tolist() == [not selected]


@pytest.mark.parametrize("has_gap", [False, True])
def test_cumulative_dimension_fold_requires_contiguous_aligned_observed_interval(
    tmp_path: Path, has_gap: bool
) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql(
            "INSERT INTO orders (id, customer_id, amount, day) VALUES (7, 2, 0, DATE '2026-02-04')"
        )
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        day = ref.time_dimension("sales.orders.order_time")
        region = ref.dimension("sales.customers.region")
        registry.metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition(REVENUE.path, day.path),
        )
        fixture = _rebind(original, registry)
        source = (
            fixture.sources.observe(
                ref.metric("sales.running"),
                population=fixture.sources.population(CUSTOMERS),
                time_scope=time_scope(start="2026-02-02", end="2026-02-05"),
            )
            .with_dimensions(region)
            .with_time_axis(day, grain=grain("day"))
            .aggregate()
        )
        intermediate = source.rollup(grain=grain("month"))
        output = intermediate.rollup(drop_dimensions=(region,))
        frame, parts, budget = _frames(fixture, source)
        current, retained, _ = execute_retained_suffix(
            frame, parts, (_call(source, intermediate),), budget
        )
        semantics = intermediate.row_contract.family_semantics
        assert isinstance(semantics, EntityReducedMetricSemantics)
        _, _, _, seconds, complete = coverage_columns(semantics.metric_folds[0])
        assert retained[0].frame[complete].tolist() == [False, False]
        if has_gap:
            changed = retained[0].frame.copy(deep=True)
            changed[seconds] = changed[seconds] - 86400
            retained = (replace(retained[0], frame=changed),)
            with pytest.raises(DatasetCompilationError, match="noncontiguous"):
                execute_retained_suffix(current, retained, (_call(intermediate, output),), budget)
        else:
            result, folded, _ = execute_retained_suffix(
                current, retained, (_call(intermediate, output),), budget
            )
            assert result["running"].tolist() == [140]
            assert folded[0].frame[seconds].tolist() == [259200.0]
            assert folded[0].frame[complete].tolist() == [False]


@pytest.mark.parametrize("excess", [0, 1])
def test_one_wide_source_stream_counts_every_part_before_fold(tmp_path: Path, excess: int) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = _source(fixture)
        table, parts = _wide(fixture, source)
        batches = tuple(table.to_batches(max_chunksize=1))
        combined = table.select([field.name for field in source.schema.columns]).nbytes + sum(
            table.select(part.schema.names).nbytes for part in parts
        )
        # Conversion also has its independent cap; the Arrow cap is tested by overflow.
        policy = replace(
            LocalPolicy(),
            max_input_bytes=combined - excess if excess else LocalPolicy().max_input_bytes,
        )
        request = LocalRequest(
            StreamInput(source.row_contract, source.row_set_contract, wide_parts=True),
            (_call(source, source.aggregate(), fold=True),),
            policy,
            time.monotonic() + 60,
            parts,
        )
        terminal: list[bool] = []
        if excess:
            with pytest.raises(MaterializationError, match="combined input overflow"):
                supervise(
                    request,
                    batches,
                    cancel_source=lambda: None,
                    terminal=lambda: terminal.append(True),
                )
        else:
            result = supervise(
                request,
                batches,
                cancel_source=lambda: None,
                terminal=lambda: terminal.append(True),
            )
            assert result.table["revenue"].to_pylist() == [147]
            assert result.table["mean_amount"].to_pylist() == pytest.approx([147 / 5])
            assert result.table["weighted_amount"].to_pylist() == [50]
            # Arrow IPC can add bitmap rounding per split batch; it must never
            # account less than the complete independent primary/part payloads.
            assert result.input_bytes >= combined
            assert len(result.parts) == 3
            assert set(result.table.column_names) > {"revenue", "mean_amount", "weighted_amount"}
        assert terminal == [True]


@pytest.mark.parametrize(
    "unit,count,value,start,end",
    [
        ("week", 1, date(2026, 2, 8), "2026-02-02", "2026-02-09"),
        ("day", 1, date(2026, 2, 8), "2026-02-08", "2026-02-09"),
        ("month", 1, date(2026, 2, 8), "2026-02-01", "2026-03-01"),
        ("quarter", 1, date(2026, 2, 8), "2026-01-01", "2026-04-01"),
    ],
)
def test_builtin_fold_buckets_have_explicit_admitted_anchors(
    unit: str, count: int, value: date, start: str, end: str
) -> None:
    actual = bucket_bounds(value, builtin_grain(unit, count=count), None)
    assert actual == (pd.Timestamp(start), pd.Timestamp(end))


def test_semantic_fold_uses_only_certified_snapshot_periods() -> None:
    calendar = ref.period_calendar("sales.fiscal")
    snapshot = certify_period_calendar(
        calendar_ref=calendar,
        boundary_timezone="UTC",
        coverage=(date(2026, 2, 1), date(2026, 2, 5)),
        rows=tuple(
            {"date": date(2026, 2, day), "period": "A" if day < 3 else "B"} for day in range(1, 5)
        ),
        levels={"reporting_period": "period"},
    )
    selected = semantic_grain(calendar=calendar, level="reporting_period")
    assert bucket_bounds(date(2026, 2, 4), selected, snapshot) == (
        pd.Timestamp("2026-02-03"),
        pd.Timestamp("2026-02-05"),
    )
    with pytest.raises(DatasetCompilationError, match="uncovered"):
        bucket_bounds(date(2026, 2, 5), selected, snapshot)
