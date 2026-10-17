"""Independent numerical and complete-calendar regression oracles for review R1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.binding.correspondence import ComparisonEndpoint, broadcast_mapping
from marivo.analysis.compiler.graph_plan import LocalCompute, plan_query
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.dsl.query import Query
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.graph_source_execution import open_query_sources
from marivo.analysis.methods import query_comparison
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject


@pytest.fixture
def core_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "core.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, day DATE, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'A','2026-01-30',10),(2,'A','2026-01-31',30),"
            "(3,'B','2026-01-30',20),(4,'A','2026-02-01',60),(5,'B','2026-02-01',40)"
        )
    duration_file = tmp_path / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2, 3], "elapsed": pa.array([3, None, 0], type=pa.duration("ns"))}),
        duration_file,
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            + "ms.domain(name='sales', owner='Core Review', default=True)\n",
            "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + "orders=ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])\n"
            + "region=ms.dimension_column(name='region', entity=orders, column='region')\n"
            + "day=ms.time_dimension_column(name='day', entity=orders, column='day', granularity='day')\n"
            + "amount=ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
            + f"durations=ms.entity(name='durations', datasource=ms.ref.datasource('warehouse'), source=md.parquet({str(duration_file)!r}), primary_key=['id'])\n"
            + "elapsed=ms.measure_column(name='elapsed', entity=durations, column='elapsed', additivity=ms.additive_all(), unit='s')\n",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("round1-core", report_timezone="UTC")


@pytest.mark.parametrize("precision", (28, 7))
def test_decimal38_negation_preserves_every_digit_under_caller_context(
    core_session: Session,
    precision: int,
) -> None:
    source = Decimal("12345678901234567890123456789012345678")
    with localcontext() as context:
        context.prec = precision
        query = core_session.values(by=(), columns={"x": None}, rows=(((), {"x": source}),))
        result = (
            query.select({"negative": -query["x"], "original": query["x"]}).execute().to_pandas()
        )
    assert result[("value", "negative")].tolist() == [source.copy_negate()]
    assert result[("value", "original")].tolist() == [source]
    assert str(result[("value", "negative")].dtype) == "decimal128(38, 0)[pyarrow]"


def test_column_na_over_zero_propagates_but_nonmissing_zero_divisions_fail(
    core_session: Session,
) -> None:
    base = core_session.values(by=(), columns={"zero": None}, rows=(((), {"zero": 0}),))
    nullable = base.select({"x": base["zero"] / base["zero"], "zero": base["zero"]})
    result = (
        nullable.select({"v": nullable["x"].divide(nullable["zero"], zero_denominator="error")})
        .execute()
        .to_pandas()
    )
    assert len(result) == 1 and result[("value", "v")].isna().all()
    for numerator in (0, 1):
        query = core_session.values(by=(), columns={"x": None}, rows=(((), {"x": numerator}),))
        with pytest.raises(AnalysisError) as error:
            query.select({"v": query["x"].divide(0, zero_denominator="error")}).execute()
        assert isinstance(error.value.__cause__, SemanticExpressionError)
        assert error.value.__cause__.expected == "a nonzero denominator"


def test_literal_duration_sum_and_mean_keep_declared_microseconds(core_session: Session) -> None:
    query = core_session.values(
        by=(), columns={"x": None}, rows=(((), {"x": timedelta(microseconds=3)}),)
    )
    result = (
        query.reduce({"sum": query["x"].sum(), "mean": query["x"].mean()}).execute().to_pandas()
    )
    for name in ("sum", "mean"):
        assert result[("value", name)].tolist() == [timedelta(microseconds=3)]
        assert pa.array(result[("value", name)]).type == pa.duration("us")


@pytest.mark.parametrize("ticks", ((0, 1), (1, 2), (-1, -2), (2**53 + 1, 2**53 + 3)))
def test_duration_statistics_accumulate_exact_nanoseconds_and_finish_half_even(
    core_session: Session,
    ticks: tuple[int, ...],
) -> None:
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table(
            {"id": list(range(len(ticks))), "elapsed": pa.array(ticks, type=pa.duration("ns"))}
        ),
        path,
    )
    population = core_session.members(ms.ref.entity("sales.durations"))
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"elapsed": elapsed})
    result = (
        query.reduce({"sum": query["elapsed"].sum(), "mean": query["elapsed"].mean()})
        .execute()
        .to_pandas()
    )
    expected = {"sum": sum(ticks), "mean": round(Fraction(sum(ticks), len(ticks)))}
    for name, value in expected.items():
        assert pa.array(result[("value", name)]).type == pa.duration("ns")
        assert pa.array(result[("value", name)]).cast(pa.int64()).to_pylist() == [value]


def test_duration_empty_and_na_statistics_and_checked_middle_sum(core_session: Session) -> None:
    population = core_session.members(ms.ref.entity("sales.durations"))
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"elapsed": elapsed})
    for selected in (query.where(query["elapsed"].is_null()), query.top(0, by=query["elapsed"])):
        result = (
            selected.reduce({"sum": selected["elapsed"].sum(), "mean": selected["elapsed"].mean()})
            .execute()
            .to_pandas()
        )
        assert pa.array(result[("value", "sum")]).cast(pa.int64()).to_pylist() == [0]
        assert result[("value", "mean")].isna().all()
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2], "elapsed": pa.array([2**63 - 1, 1], type=pa.duration("ns"))}), path
    )
    mean = query.reduce({"mean": query["elapsed"].mean()}).execute().to_pandas()
    assert pa.array(mean[("value", "mean")]).cast(pa.int64()).to_pylist() == [2**62]
    middle = query.reduce({"sum": query["elapsed"].sum()})
    with pytest.raises(AnalysisError) as error:
        middle.select({"cancelled": middle["sum"] - middle["sum"]}).execute()
    assert isinstance(error.value.__cause__, SemanticExpressionError)
    assert error.value.__cause__.received is not None
    assert "Duration finish tick overflow" in error.value.__cause__.received


def test_grouped_duration_mean_keeps_large_ticks_beside_na(core_session: Session) -> None:
    ticks = 2**53 + 1
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2], "elapsed": pa.array([ticks, None], type=pa.duration("ns"))}),
        path,
    )
    population = core_session.members(ms.ref.entity("sales.durations"))
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"elapsed": elapsed})
    result = (
        query.reduce(
            {"sum": query["elapsed"].sum(), "mean": query["elapsed"].mean()},
            by=(population.axes[0],),
        )
        .execute()
        .to_pandas()
    )
    assert pa.array(result[("value", "sum")]).cast(pa.int64()).to_pylist() == [ticks, 0]
    assert pa.array(result[("value", "mean")]).cast(pa.int64()).to_pylist() == [ticks, None]


def _daily(session: Session, start: str = "2026-01-30") -> tuple[Query, mv.CalendarAxis]:
    amount = session.catalog.fields.get("sales.orders.amount")
    day = session.catalog.fields.get("sales.orders.day")
    region = session.catalog.fields.get("sales.orders.region")
    calendar = session.time_scope(start=start, end="2026-02-03").by(mv.grain("day"))
    return session.observe({"sales": amount.sum(time=day)}, during=calendar, by=(region,)), calendar


def test_broadcast_executes_prepared_day_to_month_image_with_complete_coverage(
    core_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, calendar = _daily(core_session)
    reference = daily.rollup(by=(calendar.coarsen(mv.grain("month")),))
    query = daily.select({"reference": mv.broadcast(reference["sales"], to=daily)})
    with open_query_sources(
        core_session._runtime.connection_service(), query._graph, core_session.catalog._state
    ) as prepared:
        plan = plan_query(query._graph, prepared.schemas, context=prepared.context)
    program = next(
        stage.result
        for stage in plan.stages
        if isinstance(stage, LocalCompute) and stage.result is not None and stage.result.references
    )
    assert program.references[0].mapping.calendars[0].buckets == (0, 0, 1, 1)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Runtime must execute the prepared calendar mapping")

    monkeypatch.setattr(query_comparison, "broadcast_mapping", forbidden)
    result = query.execute().to_pandas()
    assert len(result) == 8
    axes = result.attrs["marivo"]["axes"]
    calendar_column = next(axis["column"] for axis in axes if axis["kind"] == "calendar")
    for day, value in zip(
        result[("coordinate", calendar_column)], result[("value", "reference")], strict=True
    ):
        assert value == (60 if day.month == 1 else 100)
    missing = reference.where(reference["sales"] < 100)
    with pytest.raises(AnalysisError, match="reference coverage"):
        daily.select({"reference": mv.broadcast(missing["sales"], to=daily)}).execute()


def test_broadcast_refuses_changed_scope_before_read_or_run(
    core_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily, _ = _daily(core_session)
    shifted, calendar = _daily(core_session, "2026-01-31")
    reference = shifted.rollup(by=(calendar.coarsen(mv.grain("month")),))
    query = daily.select({"reference": mv.broadcast(reference["sales"], to=daily)})
    before = core_session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("An inadmissible calendar map crossed preparation")

    monkeypatch.setattr(SourceSession, "compile", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    monkeypatch.setattr(terminal_store, "admit", forbidden)
    with pytest.raises(AnalysisError, match="complete target-to-reference"):
        query.execute()
    assert core_session.runs().items == before


def test_calendar_mapping_refuses_finer_timezone_and_snapshot_mismatches(
    core_session: Session,
) -> None:
    _, days = _daily(core_session)
    from marivo.analysis.dsl.population import _calendar_population

    fine = _calendar_population(days).axes[0]
    coarse = _calendar_population(days.coarsen(mv.grain("month"))).axes[0]
    assert isinstance(fine, CalendarCoordinate) and isinstance(coarse, CalendarCoordinate)

    def endpoint(axis: CalendarCoordinate) -> ComparisonEndpoint:
        return ComparisonEndpoint(
            (DataColumn("key.0", "day", ScalarDataType("int64"), False),),
            ("key.0",),
            (axis,),
            (("key.0",),),
        )

    with pytest.raises(AnalysisError, match="complete target-to-reference"):
        broadcast_mapping(endpoint(fine), endpoint(coarse))
    for invalid in (
        replace(coarse, timezone="Asia/Shanghai"),
        replace(coarse, snapshot_digest="different"),
    ):
        with pytest.raises(AnalysisError, match="complete target-to-reference"):
            broadcast_mapping(endpoint(invalid), endpoint(fine))


@pytest.mark.parametrize("operation", ("add", "sub", "mul", "div"))
def test_duration_binary_expression_keeps_type_before_sum_and_mean(
    core_session: Session,
    operation: str,
) -> None:
    query = core_session.values(
        by=(), columns={"d": "s"}, rows=(((), {"d": timedelta(microseconds=3)}),)
    )
    if operation == "add":
        expression, expected = query["d"] + query["d"], 6
    elif operation == "sub":
        expression, expected = query["d"] - query["d"], 0
    elif operation == "mul":
        expression, expected = query["d"] * 2, 6
    else:
        expression, expected = query["d"] / 3, 1
    result = (
        query.reduce({"sum": expression.sum(), "mean": expression.mean()}).execute().to_pandas()
    )
    for name in ("sum", "mean"):
        assert pa.array(result[("value", name)]).type == pa.duration("us")
        assert pa.array(result[("value", name)]).cast(pa.int64()).to_pylist() == [expected]


def test_duration_nested_unary_and_broadcast_arithmetic_retains_ticks(
    core_session: Session,
) -> None:
    region = core_session.catalog.fields.get("sales.orders.region")
    query = core_session.values(
        by=(region,),
        columns={"d": "s"},
        rows=tuple(
            ((key,), {"d": timedelta(microseconds=ticks)})
            for key, ticks in (("A", 3), ("B", 6), ("C", 0))
        ),
    )
    reference = core_session.values(
        by=(), columns={"d": "s"}, rows=(((), {"d": timedelta(microseconds=3)}),)
    )
    expression = -(query["d"] + mv.broadcast(reference["d"], to=query)) / 3
    selected = query.select({"d": expression})
    frame = selected.execute().to_pandas()
    assert pa.array(frame[("value", "d")]).cast(pa.int64()).to_pylist() == [-2, -3, -1]
    reduced = (
        selected.reduce({"sum": selected["d"].sum(), "mean": selected["d"].mean()})
        .execute()
        .to_pandas()
    )
    assert pa.array(reduced[("value", "sum")]).cast(pa.int64()).to_pylist() == [-6]
    assert pa.array(reduced[("value", "mean")]).cast(pa.int64()).to_pylist() == [-2]
    ratio = (
        query.reduce({"ratio": (query["d"] / mv.broadcast(reference["d"], to=query)).mean()})
        .execute()
        .to_pandas()
    )
    assert ratio[("value", "ratio")].tolist() == [1.0]
    assert pa.array(ratio[("value", "ratio")]).type == pa.float64()
    reverse = query.select({"d": 2 * query["d"]}).execute().to_pandas()
    assert pa.array(reverse[("value", "d")]).cast(pa.int64()).to_pylist() == [6, 12, 0]


@pytest.mark.parametrize(
    ("values", "unit", "ticks"),
    (
        ((timedelta(0), timedelta(seconds=3), timedelta(0)), "s", [0, 3, 0]),
        ((timedelta(0), timedelta(milliseconds=3), timedelta(0)), "us", [0, 3000, 0]),
        ((timedelta(0), timedelta(0), timedelta(0)), "us", [0, 0, 0]),
    ),
)
def test_zero_duration_literal_retains_nonzero_unit_and_refuses_missing_values(
    core_session: Session,
    values: tuple[timedelta, ...],
    unit: str,
    ticks: list[int | None],
) -> None:
    region = core_session.catalog.fields.get("sales.orders.region")
    query = core_session.values(
        by=(region,),
        columns={"d": "s"},
        rows=tuple(((key,), {"d": value}) for key, value in zip("ABC", values, strict=True)),
    )
    result = query.execute().to_pandas()
    physical = pa.array(result[("value", "d")])
    assert str(physical.type) == f"duration[{unit}]"
    assert physical.cast(pa.int64()).to_pylist() == ticks
    with pytest.raises(AnalysisError, match="non-null literal values"):
        core_session.values(by=(), columns={"d": "s"}, rows=(((), {"d": None}),))


def test_duration_binary_exact_ns_finish_na_and_overflow_barriers(core_session: Session) -> None:
    tick = 2**53 + 1
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2, 3], "elapsed": pa.array([tick, None, 3], type=pa.duration("ns"))}),
        path,
    )
    population = core_session.members(ms.ref.entity("sales.durations"))
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"d": elapsed})
    expression = query["d"] / 2
    selected = query.select(
        {"half": expression, "twice": query["d"] * 2, "decimal_half": query["d"] * Decimal("0.5")}
    )
    result = selected.execute().to_pandas()
    halves = [round(Fraction(tick, 2)), None, round(Fraction(3, 2))]
    assert pa.array(result[("value", "half")]).cast(pa.int64()).to_pylist() == halves
    assert pa.array(result[("value", "decimal_half")]).cast(pa.int64()).to_pylist() == halves
    assert pa.array(result[("value", "twice")]).cast(pa.int64()).to_pylist() == [tick * 2, None, 6]
    # A Duration-valued binary node finishes in its existing unit before its parent.
    nested = query.select({"d": (query["d"] / 2) * 2}).execute().to_pandas()
    assert pa.array(nested[("value", "d")]).cast(pa.int64()).to_pylist() == [
        2 * round(Fraction(tick, 2)),
        None,
        4,
    ]
    missing = query.select({"d": query["d"] / 0})
    finished = (
        missing.reduce({"sum": missing["d"].divide(0, zero_denominator="error").sum()})
        .execute()
        .to_pandas()
    )
    assert pa.array(finished[("value", "sum")]).cast(pa.int64()).to_pylist() == [0]
    with pytest.raises(AnalysisError) as zero:
        query.reduce({"d": query["d"].divide(0, zero_denominator="error").sum()}).execute()
    assert isinstance(zero.value.__cause__, SemanticExpressionError)
    assert zero.value.__cause__.expected == "a nonzero denominator"
    pq.write_table(
        pa.table({"id": [1], "elapsed": pa.array([2**62], type=pa.duration("ns"))}), path
    )
    with pytest.raises(AnalysisError) as overflow:
        query.reduce({"d": ((query["d"] * 2) / 2).sum()}).execute()
    assert isinstance(overflow.value.__cause__, SemanticExpressionError)
    assert overflow.value.__cause__.expected == "exact signed int64 Duration arithmetic"


def test_duration_binary_rejects_mixed_tick_units_before_source_or_run(
    core_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    population = core_session.members(ms.ref.entity("sales.durations"))
    source = core_session.read(
        population, {"d": core_session.catalog.fields.get("sales.durations.elapsed")}
    )
    reference = core_session.values(
        by=(), columns={"d": "s"}, rows=(((), {"d": timedelta(microseconds=3)}),)
    )
    invalid = source.reduce({"d": (source["d"] + mv.broadcast(reference["d"], to=source)).sum()})
    before = core_session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Incompatible Duration units crossed preparation")

    monkeypatch.setattr(SourceSession, "compile", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    monkeypatch.setattr(terminal_store, "admit", forbidden)
    with pytest.raises(AnalysisError, match="matching exact Duration units"):
        invalid.execute()
    assert core_session.runs().items == before


def _paired_daily(session: Session) -> tuple[Query, Query, Query, mv.CalendarAxis, mv.CalendarAxis]:
    with duckdb.connect(str(session.catalog.workspace_dir / "core.duckdb")) as connection:
        connection.execute(
            "INSERT INTO orders SELECT id+10, region, day-INTERVAL '1 year', amount*2 FROM orders"
        )
    current, days = _daily(session)
    old_days = session.time_scope(start="2025-01-30", end="2025-02-03").by(mv.grain("day"))
    amount = session.catalog.fields.get("sales.orders.amount")
    day = session.catalog.fields.get("sales.orders.day")
    region = session.catalog.fields.get("sales.orders.region")
    baseline = session.observe({"sales": amount.sum(time=day)}, during=old_days, by=(region,))
    return current.compare(baseline, match=mv.previous_year()), current, baseline, days, old_days


def test_coarsened_broadcast_uses_unique_retained_current_and_baseline_projections(
    core_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paired, current, baseline, days, old_days = _paired_daily(core_session)
    reference = current.rollup(by=(days.coarsen(mv.grain("month")),))
    old_reference = baseline.rollup(by=(old_days.coarsen(mv.grain("month")),))
    query = paired.select(
        {
            "current": mv.broadcast(reference["sales"], to=paired),
            "baseline": mv.broadcast(old_reference["sales"], to=paired),
        }
    )
    with open_query_sources(
        core_session._runtime.connection_service(), query._graph, core_session.catalog._state
    ) as prepared:
        plan = plan_query(query._graph, prepared.schemas, context=prepared.context)
    program = next(
        stage.result
        for stage in plan.stages
        if isinstance(stage, LocalCompute)
        and stage.result is not None
        and len(stage.result.references) == 2
    )
    for role, binding in zip(("current", "baseline"), program.references, strict=True):
        assert binding.mapping.presence == (f"presence.{role}",)
        assert all(source.startswith(f"projection.{role}.") for source, _ in binding.mapping.keys)
        assert binding.mapping.calendars[0].buckets == (0, 0, 1, 1)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Runtime must consume the prepared retained projection")

    monkeypatch.setattr(query_comparison, "broadcast_mapping", forbidden)
    frame = query.execute().to_pandas()
    assert len(frame) == 8
    projection = next(
        item
        for axis in frame.attrs["marivo"]["axes"]
        for item in axis["projections"]
        if item["kind"] == "calendar" and item["path"] == ("current",)
    )
    for key, actual, previous in zip(
        projection["values"], frame[("value", "current")], frame[("value", "baseline")], strict=True
    ):
        expected = 60 if projection["bounds"][key[0]][2].month == 1 else 100
        assert actual == expected and previous == expected * 2
    missing = reference.where(reference["sales"] < 100)
    with pytest.raises(AnalysisError, match="reference coverage"):
        paired.select({"current": mv.broadcast(missing["sales"], to=paired)}).execute()


def test_retained_calendar_broadcast_rejects_missing_current_instead_of_using_baseline(
    core_session: Session,
) -> None:
    _, current, baseline, days, _ = _paired_daily(core_session)
    positive = current.where(current["sales"] > 0)
    paired = positive.compare(baseline, match=mv.previous_year(), how="outer")
    reference = current.rollup(by=(days.coarsen(mv.grain("month")),))
    with pytest.raises(
        AnalysisError, match="a present endpoint for every demanded broadcast projection"
    ):
        paired.select({"current": mv.broadcast(reference["sales"], to=paired)}).execute()
    selected = paired.where(paired["current_present"])
    frame = (
        selected.select({"current": mv.broadcast(reference["sales"], to=selected)})
        .execute()
        .to_pandas()
    )
    assert len(frame) == 5


def test_retained_calendar_coarsening_rejects_ambiguous_endpoint_authority(
    core_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, days = _daily(core_session)
    paired = current.compare(current, match=mv.ordinal())
    reference = current.rollup(by=(days.coarsen(mv.grain("month")),))
    invalid = paired.select({"reference": mv.broadcast(reference["sales"], to=paired)})
    before = core_session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Ambiguous retained authority crossed preparation")

    with monkeypatch.context() as guarded:
        guarded.setattr(SourceSession, "compile", forbidden)
        guarded.setattr(SourceSession, "execute", forbidden)
        guarded.setattr(terminal_store, "admit", forbidden)
        with pytest.raises(AnalysisError, match="complete target-to-reference"):
            invalid.execute()
        assert core_session.runs().items == before
    explicit_current = current.compare(current, match=mv.ordinal(), time_axis="current")
    frame = (
        explicit_current.select(
            {"reference": mv.broadcast(reference["sales"], to=explicit_current)}
        )
        .execute()
        .to_pandas()
    )
    assert len(frame) == 8
