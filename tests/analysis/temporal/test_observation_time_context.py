"""Report scope, contribution clocks and fixed source predicates remain independent."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import (
    catalog_value,
    frozen_coordinates,
)
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace

_FACTS = (
    (1, "2026-11-01 08:00:00", "2026-11-03 10:00:00", 100, "east", "A"),
    (2, "2026-11-01 09:00:00", "2026-12-02 10:00:00", 200, "east", "A"),
    (3, "2026-10-31 09:00:00", "2026-11-03 10:00:00", 300, "south", "C"),
    (4, "2026-11-02 09:00:00", "2026-11-04 10:00:00", 40, "west", "B"),
    (5, None, "2026-11-03 10:00:00", 900, "south", "C"),
    (6, "2026-11-04 00:00:00", "2026-11-04 10:00:00", 800, "south", "C"),
    (7, "2026-11-02 11:00:00", "2026-11-04 11:00:00", None, "east", "A"),
    (8, "2026-09-30 00:00:00", "2026-09-30 00:00:00", 10, "window", "W"),
    (9, "2026-10-01 00:00:00", "2026-10-01 00:00:00", 20, "window", "W"),
    (10, "2026-09-30 12:00:00", "2026-10-01 00:00:00", 100, "split", "S"),
    (11, "2026-11-01 00:00:00", None, None, "split", "S"),
    (12, None, None, 10, "split", "S"),
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


@contextmanager
def _observation_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    *,
    backend: str = "duckdb",
    profile: str = "table",
) -> Iterator[Session]:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _source_data(backend)) as case:
        assert isinstance(case.source, TableSourceIR)
        factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'],default_time=ms.ref.time_dimension('sales.facts.created'))\n"
                + "created=ms.time_dimension_column(name='created',entity=facts,column='created')\n"
                + "paid=ms.time_dimension_column(name='paid',entity=facts,column='paid')\n"
                + "region=ms.dimension_column(name='region',entity=facts,column='region')\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
                + f"mirror=ms.entity(name='mirror',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "mirrored=ms.measure_column(name='amount',entity=mirror,column='amount',additivity=ms.additive_all())\n"
                + "fact_id=ms.dimension_column(name='id',entity=facts,column='id')\n"
                + "mirror_id=ms.dimension_column(name='id',entity=mirror,column='id')\n"
                + "mirror_paid=ms.time_dimension_column(name='paid',entity=mirror,column='paid')\n"
                + "link=ms.relationship(name='link',from_entity=mirror,to_entity=facts,keys=[ms.join_on(mirror_id,fact_id)])\n",
            }
        )
        yield mv.session.get_or_create("time-context", timezone="UTC")


def _fields(
    session: Session,
) -> tuple[ms.TimeDimension, ms.TimeDimension, ms.Dimension, ms.Measure]:
    created = catalog_value(session.catalog, "sales.facts.created", kind="field")
    paid = catalog_value(session.catalog, "sales.facts.paid", kind="field")
    region = catalog_value(session.catalog, "sales.facts.region", kind="field")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    assert isinstance(created, ms.TimeDimension) and isinstance(paid, ms.TimeDimension)
    assert isinstance(region, ms.Dimension) and isinstance(amount, ms.Measure)
    return created, paid, region, amount


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
def test_report_domains_align_distinct_clocks_and_keep_window_history(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with _observation_project(
        tmp_path, monkeypatch, semantic_project_factory, backend=backend, profile=profile
    ) as session:
        created, paid, region, amount = _fields(session)
        day, month = mv.grain("day"), mv.grain("month")
        split = region == "split"
        booked = amount.sum()
        settled = amount.sum(time=paid)
        outputs = {"booked": booked, "settled": settled, "net": settled - booked}
        all_time = session.observe(outputs, where=split).compute().to_pandas()
        assert all_time["booked"].tolist() == all_time["settled"].tolist() == [110]

        scope = session.time_scope(start="2026-09-01", end="2027-01-01")
        scope_only = session.observe(outputs, where=(scope, split)).compute()
        assert not scope_only.schema.axes
        scope_rows = scope_only.to_pandas()
        assert scope_rows["booked"].tolist() == scope_rows["settled"].tolist() == [100]

        with pytest.raises(AnalysisError):
            session.observe(outputs, by=(month,), where=split)  # type: ignore[arg-type]

        finite = session.observe(outputs, by=(scope.by(month),), where=split).compute()
        rows = finite.to_pandas().sort_values("calendar:month")
        assert rows["calendar:month"].dt.month.tolist() == [9, 10, 11, 12]
        assert rows["booked"].fillna(-1).tolist() == [100, 0, -1, 0]
        assert rows["settled"].tolist() == [0, 100, 0, 0]
        assert rows["net"].fillna(-1).tolist() == [-100, 100, -1, 0]

        october = session.time_scope(start="2026-10-01", end="2026-10-03")
        september = session.time_scope(start="2026-09-01", end="2026-10-01")
        cohort = (
            session.observe(
                {"settled": settled},
                by=(october.by(day),),
                where=(
                    ((created >= september.start) & (created < september.end)),
                    split,
                ),
            )
            .compute()
            .to_pandas()
            .sort_values("calendar:day")
        )
        assert cohort["settled"].tolist() == [100, 0]

        window = settled.window(mv.trailing_duration(duration=timedelta(days=7)))
        formula = {"ordinary": settled, "rolling": window, "prior": window - settled}
        history = (
            session.observe(formula, by=(october.by(day),), where=region == "window")
            .compute()
            .to_pandas()
            .sort_values("calendar:day")
        )
        restricted = (
            session.observe(
                formula,
                by=(october.by(day),),
                where=(
                    region == "window",
                    ((paid >= october.start) & (paid < october.end)),
                ),
            )
            .compute()
            .to_pandas()
            .sort_values("calendar:day")
        )
        assert (
            history["calendar:day"].dt.day.tolist()
            == restricted["calendar:day"].dt.day.tolist()
            == [1, 2]
        )
        assert history["ordinary"].tolist() == restricted["ordinary"].tolist() == [20, 0]
        assert history["rolling"].tolist() == [30, 30]
        assert restricted["rolling"].tolist() == [20, 20]
        assert history["prior"].tolist() == [10, 30]
        assert restricted["prior"].tolist() == [0, 20]

        unbucketed = session.observe(
            {"rolling": window}, where=(october, region == "window")
        ).compute()
        assert not unbucketed.schema.axes
        assert unbucketed.to_pandas()["rolling"].tolist() == [30]


def test_finite_calendar_windows_keep_empty_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with _observation_project(tmp_path, monkeypatch, semantic_project_factory) as session:
        _, paid, region, amount = _fields(session)
        day = session.time_scope(start="2026-09-30", end="2026-10-02").by(mv.grain("day"))
        settled = amount.sum(time=paid)
        groups = session.observe({"v": settled}, by=(region,), where=region == "window")
        selected = session.population_from(groups, on=((groups.axes[0], region),))
        rows = (
            session.observe(
                {
                    "two_periods": settled.window(mv.trailing_periods(count=2)),
                    "month_to_date": settled.window(mv.to_date(grain=mv.grain("month"))),
                    "last_half_day": settled.window(
                        mv.trailing_duration(duration=timedelta(hours=12))
                    ),
                },
                by=(region, day),
                where=mv.in_population(selected),
            )
            .compute()
            .to_pandas()
            .sort_values("calendar:day")
        )
        assert rows["region"].tolist() == ["window", "window"]
        assert rows["calendar:day"].dt.day.tolist() == [30, 1]
        assert rows["two_periods"].tolist() == [10, 30]
        assert rows["month_to_date"].tolist() == [10, 20]
        assert rows["last_half_day"].tolist() == [0, 0]

        empty = session.observe(
            {"v": settled.window(mv.all_history())}, by=(day,), where=region == "absent"
        ).compute()
        assert empty.to_pandas()["v"].tolist() == [0, 0]
        assert len(frozen_coordinates(empty)[0].rows) == 2


def test_time_context_validation_precedes_business_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    with _observation_project(tmp_path, monkeypatch, semantic_project_factory) as session:
        created, paid, region, amount = _fields(session)
        day = mv.grain("day")
        scope = session.time_scope(start="2026-11-01", end="2026-11-05")
        timed = amount.sum(time=paid)
        mirror = catalog_value(session.catalog, "sales.mirror.amount", kind="field")
        assert isinstance(mirror, ms.Measure)
        untimed = mirror.sum()
        with pytest.raises(AnalysisError):
            session.observe(
                {"v": timed.window(mv.all_history())},
                where=((created >= scope.start) & (created < scope.end)),
            )
        with pytest.raises(AnalysisError):
            session.observe({"v": timed.window(mv.trailing_periods(count=2))}, where=scope)

        for by in ((created,), (scope,), (day,), (scope.by(day), scope.by(day))):
            with pytest.raises(AnalysisError):
                session.observe({"v": timed}, by=by)  # type: ignore[arg-type]
        with pytest.raises(AnalysisError):
            session.observe({"v": timed}, where=(scope, scope))
        with pytest.raises(AnalysisError):
            session.observe({"v": timed}, where=(scope, timed > 0))  # type: ignore[arg-type]

        raw_domain = mv.coordinates(by=(created,), keys=((datetime(2026, 11, 2, 9),),))
        with pytest.raises(AnalysisError):
            session.observe({"v": timed}, by=raw_domain)
        regions = mv.coordinates(by=(region,), keys=(("east",),))
        with pytest.raises(AnalysisError):
            session.observe({"v": timed}, by=(regions, day))  # type: ignore[arg-type]
        with pytest.raises(AnalysisError):
            mv.product(regions, scope.by(day), scope)

        for end in ("2026-01-03", "2027-01-01"):
            report = session.time_scope(start="2026-01-01", end=end)
            domain = mv.product(regions, report.by(day))
            with pytest.raises(AnalysisError) as conflict:
                session.observe({"v": timed}, by=domain, where=report)
            error = conflict.value
            assert len(str(error)) < 800
            assert error.received == (
                "report time coordinates in by: 1; where TimeScope is present"
            )
            assert error.repair is not None
            assert error.repair.help_target.canonical_id == "session.observe"
            assert "Use the time coordinates in by" in error.repair.action

        for expr in (untimed, timed + untimed, timed + regions.count(), untimed * 0):
            session.observe({"v": expr}, where=scope)
            session.observe({"v": expr}, by=(scope.by(day),))
        session.observe({"v": timed * 2}, by=(scope.by(day),))
        session.observe({"v": untimed})
        assert source_trace.native_sql == []


def test_time_context_freezes_bounds_and_reopens_without_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with _observation_project(tmp_path, monkeypatch, semantic_project_factory) as session:
        created, _, region, amount = _fields(session)
        day = mv.grain("day")
        ordinary = (region != "window") & (region != "split")
        session = mv.session.get_or_create("time-context-shanghai", timezone="Asia/Shanghai")
        partial_scope = session.time_scope(start="2026-11-01T16:30:00", end="2026-11-02T18:00:00")
        frame = session.observe({"v": amount.sum()}, where=ordinary, by=(partial_scope.by(day),))
        saved = frame.persist()
        assert saved.artifact_id is not None
        assert saved.to_pandas().sort_values("calendar:day")["v"].tolist() == [200, 40]
        directory = frozen_coordinates(saved)[0].rows
        assert directory["selection"].tolist() == ["partial", "prefix"]
        assert directory["key.0"].iloc[0].isoformat() == "2026-10-31T16:00:00+00:00"

        two_instants = session.time_scope(start="2026-11-01T08:00:00Z", end="2026-11-01T10:00:00Z")
        offset_session = mv.session.get_or_create("time-context-offset", timezone="UTC-08:30")
        offset_scope = offset_session.time_scope(start="2026-10-31", end="2026-11-02")
        split = (
            offset_session.observe(
                {"v": amount.sum()},
                by=(offset_scope.by(day),),
                where=((created >= two_instants.start) & (created < two_instants.end)),
            )
            .compute()
            .to_pandas()
        )
        assert split.sort_values("calendar:day")["v"].tolist() == [100, 200]

        empty = session.time_scope(start="2026-11-05", end="2026-11-07")
        outputs = {"sum": amount.sum(), "count": amount.count(), "mean": amount.mean()}
        empty_cells = session.observe(outputs, by=(empty.by(day),)).compute().to_pandas()
        assert empty_cells["sum"].tolist() == empty_cells["count"].tolist() == [0, 0]
        assert empty_cells["mean"].isna().all()
        assert session.observe(outputs, by=(empty.by(day), region)).compute().to_pandas().empty
        assert session.observe(
            outputs, by=(empty.by(day),), where=((created >= empty.start) & (created < empty.end))
        ).compute().to_pandas()["sum"].tolist() == [0, 0]

        (tmp_path / "models").rename(tmp_path / "models.offline")
        (tmp_path / "source.duckdb").rename(tmp_path / "source.offline")
        worker = """
import sys
import marivo.analysis as mv
from tests.shared_fixtures import frozen_coordinates

frame = mv.session.resume(sys.argv[1]).persisted_frame(sys.argv[2])
assert [axis.timezone for axis in frame.schema.axes] == ['Asia/Shanghai']
assert frozen_coordinates(frame)[0].rows["selection"].tolist() == ["partial", "prefix"]
rows = frame.select({"twice": frame["v"] * 2}).compute().to_pandas()
assert rows.sort_values("calendar:day")["twice"].tolist() == [400, 80]
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
