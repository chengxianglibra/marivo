"""Field calendar ranges select facts independently of the Metric clock."""

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import (
    catalog_value,
    fiscal_analysis_project_files,
    frozen_coordinates,
    publish_fiscal_calendar_artifact,
)
from tests.support.paths import PROJECT_ROOT

_FACTS = (
    (1, "2026-11-01 08:00:00", "2026-11-03 10:00:00", 100, "east", "A"),
    (2, "2026-11-01 09:00:00", "2026-12-02 10:00:00", 200, "east", "A"),
    (3, "2026-10-31 09:00:00", "2026-11-03 10:00:00", 300, "south", "C"),
    (4, "2026-11-02 09:00:00", "2026-11-04 10:00:00", 40, "west", "B"),
    (5, None, "2026-11-03 10:00:00", 900, "south", "C"),
    (6, "2026-11-04 00:00:00", "2026-11-04 10:00:00", 800, "south", "C"),
    (7, "2026-11-02 11:00:00", "2026-11-04 11:00:00", None, "east", "A"),
)


def _source_data(backend: str) -> SourceData:
    def point_sql(value: str | None) -> str:
        if value is None:
            return "NULL"
        return ("TIMESTAMP " if backend == "trino" else "") + repr(value)

    def point_value(value: str | None) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

    timestamp = "DATETIME(6)" if backend == "mysql" else "TIMESTAMP(6)"
    return SourceData(
        f"id BIGINT, created {timestamp}, paid {timestamp}, amount BIGINT, region VARCHAR(10), tier VARCHAR(10)",
        ",".join(
            f"({id},{point_sql(created)},{point_sql(paid)},"
            f"{amount if amount is not None else 'NULL'},{region!r},{tier!r})"
            for id, created, paid, amount, region, tier in _FACTS
        ),
        "id Int64, created Nullable(DateTime64(6, 'UTC')), paid Nullable(DateTime64(6, 'UTC')), "
        "amount Nullable(Int64), region String, tier String",
        [
            {
                "id": id,
                "created": point_value(created),
                "paid": point_value(paid),
                "amount": amount,
                "region": region,
                "tier": tier,
            }
            for id, created, paid, amount, region, tier in _FACTS
        ],
    )


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_field_calendar_filters_before_reduction_and_completes_cross_grid(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _source_data(backend)) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "created=ms.time_dimension_column(name='created',entity=facts,column='created')\n"
                + "paid=ms.time_dimension_column(name='paid',entity=facts,column='paid')\n"
                + "region=ms.dimension_column(name='region',entity=facts,column='region')\n"
                + "tier=ms.dimension_column(name='tier',entity=facts,column='tier')\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
                + "status=ms.measure_column(name='status',entity=facts,column='amount',additivity=ms.additive_except(created),status_time_dimension=created)\n"
                + f"mirror=ms.entity(name='mirror',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + f"orphan=ms.entity(name='orphan',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "fact_id=ms.dimension_column(name='id',entity=facts,column='id')\n"
                + "mirror_id=ms.dimension_column(name='id',entity=mirror,column='id')\n"
                + "link=ms.relationship(name='link',from_entity=mirror,to_entity=facts,keys=[ms.join_on(mirror_id,fact_id)])\n",
            }
        )
        session = mv.session.get_or_create("field-calendar", timezone="UTC")
        created = catalog_value(session.catalog, "sales.facts.created", kind="field")
        paid = catalog_value(session.catalog, "sales.facts.paid", kind="field")
        region = catalog_value(session.catalog, "sales.facts.region", kind="field")
        tier = catalog_value(session.catalog, "sales.facts.tier", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        assert isinstance(created, ms.TimeDimension) and isinstance(paid, ms.TimeDimension)
        assert isinstance(region, ms.Dimension) and isinstance(tier, ms.Dimension)
        assert isinstance(amount, ms.Measure)
        created_scope = session.time_scope(start="2026-11-01", end="2026-11-04")
        paid_scope = session.time_scope(start="2026-11-03", end="2026-11-05")
        created_axis, paid_axis = (
            scope.by(mv.grain("day")) for scope in (created_scope, paid_scope)
        )
        creation = mv.time_group(created, axis=created_axis)
        metric = amount.sum(time=paid)
        frame = session.observe({"sum": metric}, by=(creation, paid_axis)).compute()
        rows = frame.to_pandas().sort_values(["created", "calendar:day"])
        assert rows["sum"].tolist() == [100, 0, 0, 40, 0, 0]
        assert len(frozen_coordinates(frame)) == 2
        if backend != "duckdb":
            return

        # A direct observation axis uses the Metric's payment clock; the same
        # scope passed to time_group selects the explicitly supplied field instead.
        payment = mv.time_group(paid, axis=paid_axis)
        for direct_time in (paid_scope, paid_axis):
            with pytest.raises(
                AnalysisError, match="a declared business time role"
            ) as missing_time:
                session.observe({"sum": amount.sum()}, by=(direct_time,))
            error = missing_time.value
            assert error.received is not None and "Metric 'sum'" in error.received
            assert "source 'sales.facts'" in error.received
            assert error.repair is not None
            assert "time=field on every source reduction" in error.repair.action
            assert "mv.time_group(field, axis=scope.by(grain))" in error.repair.action
            assert "where=field.within(scope)" in error.repair.action
            assert "does not supply Metric window endpoints" in error.repair.action
        filtered = session.observe({"sum": amount.sum()}, where=paid.within(paid_scope)).compute()
        assert filtered.to_pandas()["sum"].tolist() == [2140]
        matrix = session.observe({"sum": amount.sum()}, by=(creation, payment)).compute()
        assert matrix.to_pandas().sort_values(["created", "paid"])["sum"].tolist() == [
            100,
            0,
            0,
            40,
            0,
            0,
        ]
        created_only = (
            session.observe({"sum": metric, "mean": amount.mean()}, by=(creation,))
            .compute()
            .to_pandas()
            .sort_values("created")
        )
        assert created_only["sum"].tolist() == [300, 40, 0]
        assert created_only["mean"].fillna(-1).tolist() == [150, 40, -1]
        actual_paid_days = (
            session.observe({"mean": metric.per(mv.grain("day")).mean()}, by=(creation,))
            .compute()
            .to_pandas()
            .sort_values("created")
        )
        assert actual_paid_days["mean"].fillna(-1).tolist() == [150, 40, -1]
        explicitly_filtered = (
            session.observe({"sum": metric}, by=(creation,), where=created.within(created_scope))
            .compute()
            .to_pandas()
        )
        assert explicitly_filtered.sort_values("created")["sum"].tolist() == [300, 40, 0]
        categories = (
            session.observe({"sum": metric}, by=(creation, region, tier))
            .compute()
            .to_pandas()
            .sort_values(["created", "region", "tier"])
        )
        assert (
            list(zip(categories["region"], categories["tier"], strict=True))
            == [("east", "A"), ("west", "B")] * 3
        )
        assert categories["sum"].fillna(-1).tolist() == [300, 0, -1, 40, 0, 0]
        mirror = session.catalog.get("entity:sales.mirror")
        orphan = session.catalog.get("entity:sales.orphan")
        assert isinstance(mirror, ms.Entity) and isinstance(orphan, ms.Entity)
        linked = session.observe({"sum": amount.sum(), "count": mirror.count()}, by=(creation,))
        linked_rows = linked.compute().to_pandas().sort_values("created")
        assert linked_rows["sum"].tolist() == [300, 40, 0]
        assert linked_rows["count"].tolist() == [2, 2, 0]
        with pytest.raises(AnalysisError):
            session.observe({"sum": amount.sum(), "count": orphan.count()}, by=(creation,))
        raw = session.observe({"sum": metric}, by=(created,)).compute().to_pandas()
        assert raw.loc[raw["created"].isna(), "sum"].tolist() == [900]
        assert raw.loc[raw["created"].dt.day == 1, "sum"].sort_values().tolist() == [100, 200]

        partial_scope = session.time_scope(
            start="2026-11-01T16:30:00", end="2026-11-02T18:00:00", timezone="Asia/Shanghai"
        )
        partial = mv.time_group(created, axis=partial_scope.by(mv.grain("day")))
        partial_frame = session.observe({"sum": amount.sum()}, by=(partial,)).compute()
        assert partial_frame.to_pandas().sort_values("created")["sum"].tolist() == [200, 40]
        directory = frozen_coordinates(partial_frame)[0].rows
        assert directory["selection"].tolist() == ["partial", "prefix"]
        assert directory["key.0"].iloc[0].isoformat() == "2026-10-31T16:00:00+00:00"
        two_instants = session.time_scope(start="2026-11-01T08:00:00", end="2026-11-01T10:00:00")
        offset_axis = session.time_scope(
            start="2026-10-31", end="2026-11-02", timezone="UTC-08:30"
        ).by(mv.grain("day"))
        split = (
            session.observe(
                {"sum": amount.sum()},
                by=(mv.time_group(created, axis=offset_axis),),
                where=created.within(two_instants),
            )
            .compute()
            .to_pandas()
            .sort_values("created")
        )
        assert split["sum"].tolist() == [100, 200]

        empty = mv.time_group(
            created,
            axis=session.time_scope(start="2026-11-05", end="2026-11-07").by(mv.grain("day")),
        )
        empty_metrics = {"sum": amount.sum(), "count": amount.count(), "mean": amount.mean()}
        empty_cells = session.observe(empty_metrics, by=(empty,)).compute().to_pandas()
        assert empty_cells["sum"].tolist() == empty_cells["count"].tolist() == [0, 0]
        assert empty_cells["mean"].isna().all()
        assert session.observe(empty_metrics, by=(empty, region)).compute().to_pandas().empty

        trailing = metric.window(mv.trailing_duration(duration=timedelta(days=2)))
        rolling = session.observe({"sum": trailing}, by=(creation, paid_axis)).compute().to_pandas()
        assert rolling.sort_values(["calendar:day", "created"])["sum"].tolist() == [
            100,
            0,
            0,
            100,
            40,
            0,
        ]
        one_payment_day = session.time_scope(start="2026-11-04", end="2026-11-05")
        trimmed_history = (
            session.observe(
                {"sum": metric.window(mv.all_history())},
                by=(mv.time_group(paid, axis=one_payment_day.by(mv.grain("day"))), paid_scope),
                where=created.within(created_scope),
            )
            .compute()
            .to_pandas()
        )
        assert trimmed_history["sum"].tolist() == [40]
        for invalid in ((creation, created), (creation, creation), (payment, paid_axis)):
            with pytest.raises(AnalysisError):
                session.observe({"sum": metric}, by=invalid)
        with pytest.raises(AnalysisError, match="endpoint"):
            session.observe({"sum": trailing}, by=(creation,))
        with pytest.raises(AnalysisError):
            session.observe({"sum": metric}, by=(created_axis, paid_axis))
        with pytest.raises(AnalysisError):
            mv.product(creation)
        with pytest.raises(SemanticExpressionError, match="internal sample"):
            metric.per(creation)
        with pytest.raises(AnalysisError):
            mv.time_group(created, axis=created_scope)
        with pytest.raises(AnalysisError):
            mv.time_group(created, axis=mv.grain("day"))
        with pytest.raises(AnalysisError):
            mv.time_group(amount, axis=created_axis)
        assert creation.kind == "field_calendar"
        # Reusing an axis handle for a different field does not identify the
        # field and observation coordinates.
        same_handle = (
            session.observe(
                {"sum": metric},
                by=(paid_axis, mv.time_group(created, axis=paid_axis)),
            )
            .compute()
            .to_pandas()
        )
        assert len(same_handle) == 4
        assert same_handle["sum"].sum() == 800
        status = catalog_value(session.catalog, "sales.facts.status", kind="field")
        assert isinstance(status, ms.Measure)
        stock = status.sum()
        with pytest.raises(AnalysisError, match="exact native status-time"):
            session.observe({"stock": stock}, by=(payment,))
        stock_rows = (
            session.observe(
                {"stock": stock.per(created).last(include_null=False)},
                by=(payment,),
                where=created.within(created_scope),
            )
            .compute()
            .to_pandas()
            .sort_values("paid")
        )
        assert stock_rows["stock"].tolist() == [100, 40]

        shanghai_creation = mv.time_group(
            created,
            axis=session.time_scope(
                start="2026-11-01", end="2026-11-04", timezone="Asia/Shanghai"
            ).by(mv.grain("day")),
        )
        saved = session.observe({"sum": amount.sum()}, by=(shanghai_creation, payment)).persist()
        assert saved.artifact_id is not None
        with pytest.raises(AnalysisError):
            saved.compare(saved, match=mv.ordinal())
        pd.testing.assert_frame_equal(
            saved.to_pandas(), session.persisted_frame(saved.artifact_id).to_pandas()
        )
        (tmp_path / "models").rename(tmp_path / "models.offline")
        source_file = tmp_path / "source.duckdb"
        source_file.rename(tmp_path / "source.offline")
        worker = """
import sys
import marivo.analysis as mv
from tests.shared_fixtures import frozen_coordinates

frame = mv.session.resume(sys.argv[1]).persisted_frame(sys.argv[2])
assert [axis.timezone for axis in frame.schema.axes] == ['Asia/Shanghai', 'UTC']
assert [len(directory.rows) for directory in frozen_coordinates(frame)] == [3, 2]
assert frame.to_pandas().sort_values(['created', 'paid'])['sum'].tolist() == [100, 0, 0, 40, 0, 0]
"""
        completed = subprocess.run(
            [sys.executable, "-c", worker, session.id, saved.artifact_id],
            cwd=tmp_path,
            env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), TZ="America/Los_Angeles"),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


def test_field_calendar_help_routes_to_observe(capsys: pytest.CaptureFixture[str]) -> None:
    marivo.help("analysis.time_group")
    help_text = capsys.readouterr().out
    assert "Target: analysis.time_group" in help_text
    assert "Session.observe.by" in help_text
    assert "window endpoints" in help_text
    assert "omit time=" in help_text
    assert "where=field.within(scope)" in help_text


def test_field_calendar_filters_before_custom_period_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    database = tmp_path / "fiscal.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE events (user_id BIGINT, event_date DATE, amount BIGINT)")
        connection.execute(
            "INSERT INTO events VALUES "
            "(1, DATE '2026-01-07', 10), (2, DATE '2026-01-08', 900), (3, NULL, 800)"
        )
    monkeypatch.chdir(tmp_path)
    files = fiscal_analysis_project_files()
    files["datasources/warehouse.py"] = (
        "import marivo.datasource as md\n"
        + f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')\n"
    )
    files["sales/metrics.py"] = files["sales/metrics.py"].replace(
        "source=md.table('events')", "source=md.table('events'), primary_key=['user_id']"
    )
    files["sales/calendar.py"] = files["sales/calendar.py"].replace(
        "source=md.table('calendar')", "source=md.table('calendar'), primary_key=['calendar_date']"
    )
    publish_fiscal_calendar_artifact(ms.SemanticCatalog(semantic_project_factory(files)))
    session = mv.session.get_or_create("custom-field-calendar", timezone="UTC")
    calendar = session.catalog.get("period_calendar:sales.fiscal")
    assert isinstance(calendar, ms.PeriodCalendar)
    week = calendar.grain("fiscal_week")
    events = catalog_value(session.catalog, "sales.events.event_date", kind="field")
    gmv = session.catalog.get("metric:sales.gmv")
    assert isinstance(events, ms.TimeDimension) and isinstance(gmv, ms.Metric)
    one_period = calendar.period("fiscal_week", "M1-W1").by(week)
    grouped = mv.time_group(events, axis=one_period)
    frame = session.observe(gmv, by=(grouped,)).compute()
    assert frame.to_pandas()["gmv"].tolist() == [10]
    assert frozen_coordinates(frame)[0].rows["selection"].tolist() == ["full"]
    for end in ("2026-01-05", "2026-01-15"):
        with pytest.raises(ValueError, match="one complete custom calendar period"):
            session.time_scope(start="2026-01-01", end=end).by(week)
    from marivo._temporal import certify_period_calendar_rows, semantic_grain
    from marivo.refs import ref

    calendar_ref = ref.period_calendar("sales.fiscal")
    start, end = date(2026, 1, 1), date(2026, 1, 8)
    conflicting = certify_period_calendar_rows(
        calendar_ref=calendar_ref,
        boundary_timezone="UTC",
        coverage=(start, end),
        columns=("day", "period"),
        date_column="day",
        levels={"fiscal_week": "period"},
        retained_values=tuple(
            {"day": (start + timedelta(days=index)).isoformat(), "period": "different"}
            for index in range((end - start).days)
        ),
    )
    conflicting_axis = session.time_scope(start="2026-01-01", end="2026-01-08").by(
        semantic_grain(calendar=calendar_ref, level="fiscal_week", snapshot=conflicting)
    )
    with pytest.raises(AnalysisError, match="one certified snapshot"):
        session.observe(gmv, by=(mv.time_group(events, axis=conflicting_axis),))
