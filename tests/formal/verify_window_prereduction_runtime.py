"""Exercise dense native windows through authored governed reads on five SQL engines."""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.compiler.query_stages import ActualWindowRead, SourceReadStage
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.materialization.actual_windows import prepare_actual_window_read
from marivo.analysis.materialization.frame_inputs import capture_frame_inputs
from marivo.analysis.materialization.frame_source_preparation import prepare_frame_sources
from marivo.analysis.materialization.graph_source_execution import execute_numeric_query
from marivo.analysis.materialization.native_calendar import _replace_calendar
from marivo.analysis.materialization.prepared_sources import PreparedWindowRead
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.errors import CoreRuleError
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_contracts import frame_backing
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.datasource.spi import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import CASES, _datasource_model
from tests.datasource.environment import mysql_analysis
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.formal.reference.evaluator import reduce_bag
from tests.formal.reference.model import Bag, Occurrence, Reducer
from tests.shared_fixtures import catalog_value
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime

_Window = Literal["trailing", "history", "year", "since", "duration"]
_Reducer = Literal["sum", "min", "max", "count", "row_count"]
_REPORT = date(2026, 1, 1)
_SINCE = date(2025, 12, 30)
_FACTS = (
    (1, date(2025, 12, 29), 7, 6),
    (2, date(2025, 12, 31), 7, -3),
    (3, date(2026, 1, 1), 7, None),
    (4, date(2026, 1, 3), 7, 9),
    (5, date(2026, 1, 3), None, 11),
    (6, date(2026, 1, 4), 9, None),
    (7, date(2026, 1, 5), 7, 9),
    (8, date(2026, 1, 11), 7, 50),
)
_WEIGHTS = (2, -1, 1, 1, None, 2, 0, 1)
_DISABLED = OptimizationPolicy.default().with_rule("l6_window_prereduction", enabled=False)


@dataclass(frozen=True)
class _WindowDataset:
    zone: str
    facts: tuple[tuple[int, date, int | None, int | None], ...]
    weights: tuple[int | None, ...]
    incomplete_day: str | None = None


_APIA = _WindowDataset(
    "Pacific/Apia",
    tuple(
        (index, date(2011, 12, day), 7, value)
        for index, day, value in ((1, 27, 1), (2, 29, 2), (3, 30, 4), (4, 31, 8))
    ),
    (1, 1, 1, 1),
)
_WEIGHT_ERROR = _WindowDataset(
    "UTC",
    ((1, date(2026, 1, 1), 1, 10), (2, date(2026, 1, 2), 2, 300), (3, date(2026, 1, 3), 1, 20)),
    (2, 300, 2),
)


def _literal(value: object, provider: str) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'" if provider == "trino" else f"'{value.isoformat()}'"
    return str(value)


@pytest.fixture
def window_session(
    request: pytest.FixtureRequest,
    provider: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> Iterator[mv.Session]:
    choice = getattr(request, "param", "UTC")
    dataset = (
        choice if isinstance(choice, _WindowDataset) else _WindowDataset(choice, _FACTS, _WEIGHTS)
    )
    zone = dataset.zone
    names = ("id", "day", "bucket", "value", "weight")
    rows = [(*row, weight) for row, weight in zip(dataset.facts, dataset.weights, strict=True)]
    values = ",".join(
        "(" + ",".join(_literal(cell, provider) for cell in row) + ")" for row in rows
    )
    data = SourceData(
        "id INTEGER NOT NULL, day DATE NOT NULL, bucket INTEGER, value SMALLINT, weight SMALLINT",
        values,
        "id Int32, day Date, bucket Nullable(Int32), value Nullable(Int16), weight Nullable(Int16)",
        [dict(zip(names, row, strict=True)) for row in rows],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(provider, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        if dataset.incomplete_day is not None:
            assert provider == "mysql"
            with mysql_analysis.connection(admin=True) as db, db.cursor() as cursor:
                cursor.execute("SET SESSION sql_mode = ''")
                cursor.execute(
                    f"INSERT INTO `{case.source.table}` VALUES (100, %s, 1, 100, 1)",
                    (dataset.incomplete_day,),
                )
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(provider, case, monkeypatch).replace(
                    "wall_clock_timezone='UTC'", f"wall_clock_timezone={zone!r}"
                ),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)",
                "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])
bucket = ms.dimension_column(name='bucket', entity=orders, column='bucket')
day = ms.time_dimension_column(name='day', entity=orders, column='day')
value = ms.measure_column(name='value', entity=orders, column='value', additivity=ms.additive_all())
weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())
""",
            }
        )
        yield mv.session.get_or_create("native-window", timezone=zone)


def _reference(
    window: _Window, reducer: _Reducer, grouped: bool
) -> dict[tuple[date, int | None], object]:
    keys: tuple[int | None, ...] = (7, 9, None) if grouped else (None,)
    output: dict[tuple[date, int | None], object] = {}
    for offset in range(8):
        endpoint = _REPORT + timedelta(days=offset)
        start = {
            "trailing": endpoint - timedelta(days=2),
            "duration": endpoint - timedelta(days=2),
            "history": date.min,
            "year": _REPORT,
            "since": _SINCE,
        }[window]
        for key in keys:
            bag: Bag = tuple(
                Occurrence((identifier,), value, time=0)
                for identifier, day, bucket, value in _FACTS
                if start <= day <= endpoint and (not grouped or bucket == key)
            )
            if bag:
                value, failure = reduce_bag(Reducer(reducer), bag)
                assert failure is None
                output[endpoint, key] = value
    return output


def _instant(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


@pytest.mark.parametrize("provider, profile", source_params(CASES))
@pytest.mark.parametrize(
    "window, reducer, grouped",
    (
        ("trailing", "sum", False),
        ("history", "min", False),
        ("year", "max", False),
        ("since", "count", True),
        ("duration", "min", True),
        ("trailing", "row_count", True),
    ),
)
def test_live_native_windows_preserve_sparse_keys_and_exact_payloads(
    window_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    window: _Window,
    reducer: _Reducer,
    grouped: bool,
) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    entity = catalog_value(session.catalog, "sales.orders", kind="entity")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-09").by(mv.grain("day"))
    rule = {
        "trailing": mv.trailing_periods(count=3),
        "history": mv.all_history(),
        "year": mv.to_date(grain=mv.grain("year")),
        "since": mv.since(start=_instant(_SINCE)),
        "duration": mv.trailing_duration(duration=timedelta(days=3)),
    }[window]
    metric = {
        "sum": amount.sum(time=day),
        "min": amount.min(time=day),
        "max": amount.max(time=day),
        "count": amount.count(time=day),
        "row_count": entity.count(time=day),
    }[reducer]
    query = session.observe(
        {"value": metric.window(rule)}, by=(calendar, bucket) if grouped else (calendar,)
    )
    trace = capture_source(monkeypatch)
    original = compute_with_policy(query, _DISABLED).to_pandas()
    assert len(trace.native_sql) == 9
    trace.native_sql.clear()
    optimized = query.compute().to_pandas()
    pd.testing.assert_frame_equal(optimized, original)
    assert len(trace.native_sql) == 2
    assert sum(" OVER " in " ".join(sql.split()).upper() for sql in trace.native_sql) == 1
    actual = {}
    for row in optimized.itertuples(index=False, name=None):
        key = None if not grouped or pd.isna(row[1]) else int(row[1])
        actual[pd.Timestamp(row[0]).date(), key] = None if pd.isna(row[-1]) else row[-1]
    assert actual == _reference(window, reducer, grouped)


@pytest.mark.parametrize("provider, profile", source_params(CASES))
def test_window_exact_weighted_mean_reuses_original_finish(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-09").by(mv.grain("day"))
    query = session.observe(
        {"value": value.weighted_mean(weight, time=day).window(mv.trailing_periods(count=3))},
        by=(calendar,),
    )
    trace = capture_source(monkeypatch)
    expected = {}
    for offset in range(8):
        endpoint = _REPORT + timedelta(days=offset)
        bag: Bag = tuple(
            Occurrence((key,), operand, weight=mass)
            for (key, day, _, operand), mass in zip(_FACTS, _WEIGHTS, strict=True)
            if endpoint - timedelta(days=2) <= day <= endpoint
        )
        if bag:
            answer, failure = reduce_bag(Reducer("weighted_mean"), bag)
            assert failure is None
            assert answer is None or isinstance(answer, (Fraction, int, float))
            expected[endpoint] = None if answer is None else float(answer)
    baseline = compute_with_policy(query, _DISABLED).to_pandas()
    assert len(trace.native_sql) == 9
    trace.native_sql.clear()
    actual = query.compute().to_pandas()
    pd.testing.assert_frame_equal(actual, baseline, check_exact=True)
    # Iceberg widens stored SMALLINT fields to INTEGER, which removes the
    # native weighted accumulator proof for the ungrouped identity domain.
    assert len(trace.native_sql) == (9 if provider == "trino" else 2)
    records = {
        pd.Timestamp(point).date(): None if pd.isna(result) else result
        for point, result in actual.itertuples(index=False, name=None)
    }
    assert records == expected


@pytest.mark.parametrize("provider, profile", source_params(CASES))
def test_window_mean_retains_one_value_per_complete_identity(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = window_session
    entity = catalog_value(session.catalog, "sales.orders", kind="entity")
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-09").by(mv.grain("day"))
    query = session.observe(
        {"value": value.mean(time=day).window(mv.trailing_periods(count=3))},
        by=(calendar, mv.identity(entity)),
    )
    trace = capture_source(monkeypatch)
    baseline = compute_with_policy(query, _DISABLED).to_pandas()
    assert len(trace.native_sql) == 9
    trace.native_sql.clear()
    actual = query.compute().to_pandas()
    pd.testing.assert_frame_equal(actual, baseline, check_exact=True)
    assert len(trace.native_sql) == 2
    expected = {}
    for offset in range(8):
        endpoint = _REPORT + timedelta(days=offset)
        for key, fact_day, _, operand in _FACTS:
            if endpoint - timedelta(days=2) <= fact_day <= endpoint:
                expected[endpoint, key] = None if operand is None else float(operand)
    records = {
        (pd.Timestamp(point).date(), key): None if pd.isna(result) else result
        for point, key, result in actual.itertuples(index=False, name=None)
    }
    assert records == expected


@pytest.mark.parametrize("provider, profile", source_params(CASES))
def test_window_strict_sum_keeps_original_failure_witness(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-09").by(mv.grain("day"))
    query = session.observe(
        {"value": value.sum(time=day, nulls="error").window(mv.trailing_periods(count=3))},
        by=(calendar, bucket),
    )
    trace = capture_source(monkeypatch)
    with pytest.raises(MaterializationError) as original:
        compute_with_policy(query, _DISABLED)
    trace.native_sql.clear()
    with pytest.raises(MaterializationError) as optimized:
        query.compute()
    before, after = original.value, optimized.value
    assert before.stage == after.stage == "numeric_value"
    assert before.expected == after.expected == "non-NA source values"
    assert before.received == after.received
    assert "2026-01-01 00:00:00" in (after.received or "")
    assert "'key.1': 7" in (after.received or "")
    assert len(trace.native_sql) == 2


@pytest.mark.parametrize("provider, profile", source_params(CASES))
def test_window_explicit_output_population_preserves_its_complete_keys(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-09").by(mv.grain("day"))
    keys = (7, 9, None, 42)
    domain = mv.coordinates(by=(bucket,), keys=tuple((key,) for key in keys))
    assert isinstance(domain, mv.Population)
    query = session.observe(
        {"value": value.sum(time=day).window(mv.trailing_periods(count=3))},
        by=mv.product(calendar, domain),
    )
    trace = capture_source(monkeypatch)
    baseline = compute_with_policy(query, _DISABLED).to_pandas()
    trace.native_sql.clear()
    actual = query.compute().to_pandas()
    pd.testing.assert_frame_equal(actual, baseline, check_exact=True)
    assert sum(" OVER " in " ".join(sql.split()).upper() for sql in trace.native_sql) == 1
    reference = _reference("trailing", "sum", True)
    expected = {
        (_REPORT + timedelta(days=offset), key): reference.get(
            (_REPORT + timedelta(days=offset), key), 0
        )
        for offset in range(8)
        for key in keys
    }
    records = {
        (pd.Timestamp(point).date(), None if pd.isna(key) else int(key)): (
            None if pd.isna(result) else result
        )
        for point, key, result in actual.itertuples(index=False, name=None)
    }
    assert records == expected


@pytest.mark.parametrize("provider, profile", (("duckdb", "table"),))
@pytest.mark.parametrize("reducer", ("distinct", "per"))
def test_window_leaves_nonmergeable_shape_on_original_route(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch, reducer: str
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-09").by(mv.grain("day"))
    metric = (
        value.count_distinct(time=day)
        if reducer == "distinct"
        else value.min(time=day).per(bucket).mean()
    )
    query = session.observe({"value": metric.window(mv.trailing_periods(count=3))}, by=(calendar,))
    baseline = compute_with_policy(query, _DISABLED).to_pandas()
    trace = capture_source(monkeypatch)
    actual = query.compute().to_pandas()
    pd.testing.assert_frame_equal(actual, baseline, check_exact=True)
    assert trace.native_sql
    assert not any("__window_ordinal" in sql for sql in trace.native_sql)


def _actual_window_program(query: mv.AnalysisFrame) -> ActualWindowRead:
    backing = frame_backing(query)
    assert isinstance(backing, ExpressionFrameBacking)
    binding = backing.root
    assert isinstance(binding, SourceFrameBinding)
    axis = next(
        item for item in binding.graph.signature.axes if isinstance(item, CalendarCoordinate)
    )
    actual_axis = replace(axis, cells=(), civil_bounds=(), actual_only=True)
    graph = _replace_calendar(binding.graph, axis, actual_axis)
    assert isinstance(graph, BoundGraph) and graph.signature.observation is not None
    observation = graph.signature.observation
    contribution = observation.contributions[0]
    contribution = replace(
        contribution,
        occurrence=contribution.logical_occurrence or contribution.occurrence,
        logical_occurrence=None,
        window_key=None,
    )
    graph = replace(
        graph,
        signature=replace(
            graph.signature, observation=replace(observation, contributions=(contribution,))
        ),
    )
    return ActualWindowRead(graph, binding.state, "UTC", (), ())


def _run_actual_window(
    query: mv.AnalysisFrame, cells: tuple[tuple[object, ...], ...], policy: OptimizationPolicy
) -> tuple[StageOutput, str]:
    program = _actual_window_program(query)
    with ExitStack() as stack:
        opened = prepare_frame_sources(capture_frame_inputs(query), stack, policy=policy)
        prepared = next(iter(opened.prepared.values()))
        plan = next(iter(opened.plans.values())).lowered.plan
        stage = next(item for item in plan.stages if isinstance(item, SourceReadStage))
        table = prepared.bound[stage.source_id].source
        assert isinstance(table, TableSourceIR)
        columns = tuple(column for column in stage.columns if column.slot_id in stage.keys)
        captured = pd.DataFrame.from_records(cells, columns=list(stage.keys))
        authorized = StageOutput.from_frame(captured, columns, stage.keys)
        window = prepare_actual_window_read(prepared, stage, program, (authorized,))
        assert isinstance(window, PreparedWindowRead)
        return (
            execute_numeric_query(
                window.sources, window.lowered, report_domain=window.report_domain
            ),
            table.table,
        )


@pytest.mark.parametrize(
    "provider, profile", source_params((("duckdb", "table"), ("clickhouse", "mergetree")))
)
def test_actual_window_can_share_history_for_captured_sparse_keys(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-09").by(mv.grain("day"))
    query = session.observe({"value": value.min(time=day).window(mv.all_history())}, by=(calendar,))
    trace = capture_source(monkeypatch)
    counts = []
    points = (_instant(date(2026, 1, 1)), _instant(date(2026, 1, 4)))
    for policy in (_DISABLED, OptimizationPolicy.default()):
        trace.native_sql.clear()
        result, table_name = _run_actual_window(query, tuple((point,) for point in points), policy)
        counts.append(sum(table_name.lower() in sql.lower() for sql in trace.native_sql))
        assert list(result.table.itertuples(index=False, name=None)) == [
            (point, -3) for point in points
        ]
    assert counts == [2, 1]


@pytest.mark.parametrize("provider, profile", (("duckdb", "table"),))
@pytest.mark.parametrize("window_session", (_WEIGHT_ERROR,), ids=("range",), indirect=True)
@pytest.mark.parametrize("includes_overflow", (False, True))
def test_actual_weighted_input_keeps_only_authorized_complete_key_ranges(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch, includes_overflow: bool
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-05").by(mv.grain("day"))
    query = session.observe(
        {"value": value.weighted_mean(weight, time=day).window(mv.all_history())},
        by=(calendar, bucket),
    )
    trace = capture_source(monkeypatch)
    first = _instant(date(2026, 1, 1))
    last = _instant(date(2026, 1, 3))
    cells = ((first, 2), (last, 2 if includes_overflow else 1))
    errors = []
    for policy in (_DISABLED, OptimizationPolicy.default()):
        trace.native_sql.clear()
        if includes_overflow:
            with pytest.raises(MaterializationError) as failure:
                _run_actual_window(query, cells, policy)
            errors.append((failure.value.stage, failure.value.received))
        else:
            output, _ = _run_actual_window(query, cells, policy)
            result = output.table.iloc[:, -1].tolist()
            assert len(result) == 2 and pd.isna(result[0]) and result[1] == 15.0
            assert list(output.table.iloc[:, 1]) == [2, 1]
        assert any("__window_ordinal" in sql for sql in trace.native_sql) == policy.allows(
            "l6_window_prereduction"
        )
    if includes_overflow:
        assert errors[0] == errors[1]
        assert errors[0][0] == "source_execution"
        assert "300" in (errors[0][1] or "")


@pytest.mark.parametrize("provider, profile", (("duckdb", "table"),))
@pytest.mark.parametrize("window_session", ("America/New_York",), indirect=True)
def test_civil_window_keeps_dst_keys_and_rejects_unaligned_elapsed_days(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    calendar = session.time_scope(start="2026-03-07", end="2026-03-11").by(mv.grain("day"))
    base = value.min(time=day)
    cumulative = session.observe({"value": base.window(mv.all_history())}, by=(calendar,))
    elapsed = session.observe(
        {"value": base.window(mv.trailing_duration(duration=timedelta(days=3)))},
        by=(calendar,),
    )
    trace = capture_source(monkeypatch)
    ordinary = compute_with_policy(cumulative, _DISABLED).to_pandas()
    trace.native_sql.clear()
    fused = cumulative.compute().to_pandas()
    pd.testing.assert_frame_equal(fused, ordinary, check_exact=True)
    assert list(fused["value"]) == [-3] * 4
    assert [stamp.hour for stamp in pd.to_datetime(fused.iloc[:, 0], utc=True)] == [5, 5, 4, 4]
    assert len(trace.native_sql) == 2
    trace.native_sql.clear()
    with pytest.raises(CoreRuleError, match="not local midnight") as before:
        compute_with_policy(elapsed, _DISABLED)
    with pytest.raises(CoreRuleError, match="not local midnight") as after:
        elapsed.compute()
    assert after.value.received == before.value.received
    assert not any("__window_ordinal" in sql for sql in trace.native_sql)


@pytest.mark.parametrize("provider, profile", (("duckdb", "table"),))
@pytest.mark.parametrize("window_session", (_APIA,), ids=("skipped-civil-date",), indirect=True)
@pytest.mark.parametrize("monthly", (False, True))
def test_skipped_civil_date_preserves_native_time_boundary(
    window_session: mv.Session, monkeypatch: pytest.MonkeyPatch, monthly: bool
) -> None:
    session = window_session
    value = catalog_value(session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    rule = mv.to_date(grain=mv.grain("month")) if monthly else mv.all_history()
    calendar = session.time_scope(start="2011-12-28", end="2012-01-03").by(mv.grain("day"))
    query = session.observe({"value": value.sum(time=day).window(rule)}, by=(calendar,))
    baseline = compute_with_policy(query, _DISABLED).to_pandas()
    trace = capture_source(monkeypatch)
    actual = query.compute().to_pandas()
    pd.testing.assert_frame_equal(actual, baseline, check_exact=True)
    result = {point.date(): value for point, value in actual.itertuples(index=False, name=None)}
    assert result[date(2011, 12, 29)] == 3
    assert not any("__window_ordinal" in sql for sql in trace.native_sql)


@pytest.mark.parametrize(
    "window_session, start, end, expected, native",
    (
        (
            _WindowDataset(
                "UTC",
                ((1, date(2026, 2, 28), 1, 5), (2, date(2026, 3, 2), 1, 2)),
                (1, 1),
                "2026-03-00",
            ),
            "2026-02-28",
            "2026-03-04",
            [105, 105, 107, 107],
            True,
        ),
        (
            _WindowDataset(
                "America/New_York",
                ((1, date(2026, 3, 7), 1, 5), (2, date(2026, 3, 9), 1, 2)),
                (1, 1),
                "0000-00-00",
            ),
            "2026-03-07",
            "2026-03-11",
            [5, 5, 7, 107],
            False,
        ),
    ),
    ids=("partial-date-raw", "zero-date-mixed-predicates"),
    indirect=("window_session",),
)
@pytest.mark.parametrize("provider, profile", (("mysql", "innodb-table"),))
def test_mysql_incomplete_dates_keep_the_original_native_bounds(
    window_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    start: str,
    end: str,
    expected: list[int],
    native: bool,
) -> None:
    value = catalog_value(window_session.catalog, "sales.orders.value", kind="measure")
    day = catalog_value(window_session.catalog, "sales.orders.day", kind="time_dimension")
    calendar = window_session.time_scope(start=start, end=end).by(mv.grain("day"))
    query = window_session.observe(
        {"value": value.sum(time=day).window(mv.all_history())}, by=(calendar,)
    )
    before = compute_with_policy(query, _DISABLED).to_pandas()
    trace = capture_source(monkeypatch)
    after = query.compute().to_pandas()
    pd.testing.assert_frame_equal(before, after, check_exact=True)
    assert list(after["value"]) == expected
    assert any("__window_ordinal" in sql for sql in trace.native_sql) == native
