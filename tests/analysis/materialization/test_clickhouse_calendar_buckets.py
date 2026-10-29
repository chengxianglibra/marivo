"""ClickHouse calendar CASE compatibility through public source observation."""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ibis
import pytest
import sqlglot
from ibis.common.deferred import Deferred
from sqlglot import expressions as sge

import marivo.analysis as mv
from marivo._definition.types import IntervalCoordinate
from marivo.analysis.materialization.graph_source_execution import _attribute_calendar_value
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.environment import clickhouse_analysis
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("calendar", (False, True))
def test_attribute_calendar_case_types_values_without_casting_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calendar: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("attribute-calendar-sql", timezone="UTC")
    grid = session.time_scope(start="2026-01-01", end="2026-01-04").by(mv.grain("day"))
    coordinate = grid._binding
    selection = (
        coordinate
        if calendar
        else IntervalCoordinate("interval", coordinate.cells[0][2], coordinate.cells[-1][3], "UTC")
    )
    table = ibis.table({"event_time": "timestamp('UTC')"}, name="events")
    value = _attribute_calendar_value(
        table, {"time": "event_time"}, "time", selection, None, "key.0"
    )
    sql = ibis.to_sql(table.select(bucket=value), dialect="clickhouse")
    bucket = next(sqlglot.parse_one(sql, read="clickhouse").find_all(sge.Case))
    assert isinstance(bucket.args["default"], sge.Null)
    for branch in bucket.args["ifs"]:
        typed = branch.args["true"]
        assert isinstance(typed, sge.Cast)
        assert typed.to.this == sge.DataType.Type.BIGINT
        assert not typed.to.args.get("nullable")
    assert value.type().is_int64() and value.type().nullable


@pytest.mark.parametrize("backend,profile", source_params([("clickhouse", "mergetree")]))
@pytest.mark.parametrize("physical_type", ("Date", "DateTime('UTC')", "DateTime64(6, 'UTC')"))
@pytest.mark.parametrize("nullable", (False, True))
def test_observe_calendar_preserves_bucket_types_boundaries_and_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    physical_type: str,
    nullable: bool,
) -> None:
    points: list[date | datetime | None] = [
        datetime(2025, 12, 31, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(
            2026,
            1,
            1,
            23,
            59,
            59,
            999999 if physical_type.startswith("DateTime64") else 0,
            tzinfo=timezone.utc,
        ),
        datetime(2026, 1, 3, tzinfo=timezone.utc),
        datetime(2026, 1, 4, tzinfo=timezone.utc),
    ]
    if physical_type == "Date":
        points = [point.date() if isinstance(point, datetime) else point for point in points]
    if nullable:
        points.append(None)
    amounts = (100, 10, 5, 30, 1000, 10000)
    rows: list[dict[str, object]] = [
        {"id": index, "amount": amounts[index], "event_time": point, "category": "A"}
        for index, point in enumerate(points)
    ]
    column_type = f"Nullable({physical_type})" if nullable else physical_type
    data = SourceData(
        "id BIGINT, amount BIGINT, event_time TIMESTAMP",
        "",
        f"id Int64, amount Int64, event_time {column_type}, category String",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
                "sales/events.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "event_time = ms.time_dimension_column(name='event_time', entity=events, column='event_time', is_default=True)\n"
                + "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())\n"
                + "category = ms.dimension_column(name='category', entity=events, column='category')\n",
            }
        )
        session = mv.session.get_or_create("calendar-compatibility", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.events.amount", kind="field")
        event_time = catalog_value(session.catalog, "sales.events.event_time", kind="field")
        category = catalog_value(session.catalog, "sales.events.category", kind="field")
        assert isinstance(amount, Deferred) and isinstance(event_time, Deferred)
        metric = amount.sum(time=event_time)
        scope = session.time_scope(start="2026-01-01", end="2026-01-04")
        grid = scope.by(mv.grain("day"))
        trace = capture_source(monkeypatch)
        frame = session.observe({"total": metric}, by=(grid,)).compute()
        result = frame.to_pandas(time_details=True)
        assert result["calendar:day.label"].tolist() == ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert result["total"].tolist() == [15, 0, 30]
        assert result.attrs["axes"][0]["descriptor"]["timezone"] == "UTC"
        assert len(result.attrs["frozen_coordinates"][0]["rows"]["nominal_start"]) == 3

        # Inspect the CASE actually submitted by observe, not a hand-built surrogate.
        buckets = [
            alias.this
            for sql in trace.native_sql
            for alias in sqlglot.parse_one(sql, read="clickhouse").find_all(sge.Alias)
            if alias.alias.startswith("__bucket_") and isinstance(alias.this, sge.Case)
        ]
        assert buckets
        for bucket in buckets:
            assert isinstance(bucket.args["default"], sge.Null)
            for branch in bucket.args["ifs"]:
                value = branch.args["true"]
                assert isinstance(value, sge.Cast)
                assert value.to.this == sge.DataType.Type.BIGINT
                assert not value.to.args.get("nullable")
        native_bucket = buckets[0].copy()
        for column in native_bucket.find_all(sge.Column):
            column.set("table", None)
        table = sge.Table(
            this=sge.to_identifier(case.source.table, quoted=True),
            db=sge.to_identifier(str(case.source.database), quoted=True),
        )
        query = (
            sge.select("id", native_bucket.as_("bucket"), "toTypeName(bucket) AS bucket_type")
            .from_(table)
            .order_by("id")
        )
        with clickhouse_analysis.connection() as reader:
            native = reader.query(query.sql(dialect="clickhouse")).result_rows
        expected_buckets = [None, 0, 0, 2, None] + ([None] if nullable else [])
        assert [row[1] for row in native] == expected_buckets
        assert {row[2] for row in native} == {"Nullable(Int64)"}

        report = frame.attribute(by=(category,)).compute().to_pandas(time_details=True)
        assert report["calendar:day.label"].tolist() == ["2026-01-01", "2026-01-03"]
        assert report["contribution.total"].tolist() == [15, 30]
        assert report["total.total"].tolist() == [15, 30]

        filtered = session.observe({"total": metric}, by=(scope,)).compute()
        assert filtered.to_pandas(time_details=True)["total"].tolist() == [45]
        report = filtered.attribute(by=(category,)).compute().to_pandas(time_details=True)
        assert report["contribution.total"].tolist() == [45]
        assert report["total.total"].tolist() == [45]
        window = (
            session.observe(
                {"total": metric.window(mv.trailing_duration(duration=timedelta(days=2)))},
                by=(grid,),
            )
            .compute()
            .to_pandas(time_details=True)
        )
        assert window["calendar:day.label"].tolist() == result["calendar:day.label"].tolist()
        assert window["total"].tolist() == [115, 15, 30]

        # Nonempty time selections must not introduce Bool constant operands:
        # old ClickHouse builds can reject the resulting mixed Bool/UInt8 data.
        time_predicates: list[sge.Expression] = []
        for sql in trace.native_sql:
            ast = sqlglot.parse_one(sql, read="clickhouse")
            time_predicates.extend(
                where.this
                for where in ast.find_all(sge.Where)
                if any(column.name == "event_time" for column in where.find_all(sge.Column))
            )
            time_predicates.extend(
                alias.this
                for alias in ast.find_all(sge.Alias)
                if alias.alias.startswith(("__time_selected", "__time_period"))
            )
        assert time_predicates
        for predicate in time_predicates:
            for combination in predicate.find_all(sge.And, sge.Or):
                assert not isinstance(combination.left.unnest(), sge.Boolean)
                assert not isinstance(combination.right.unnest(), sge.Boolean)
