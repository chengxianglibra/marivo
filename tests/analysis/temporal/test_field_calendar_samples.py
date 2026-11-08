"""Internal field calendar samples operate on represented contributions."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace

_PAID = "2026-11-10 12:00:00"
_F9 = (
    ("2026-11-01 09:00:00", _PAID, 10, "East", 1),
    ("2026-11-01 18:00:00", _PAID, 20, "East", 1),
    ("2026-11-03 09:00:00", _PAID, 50, "West", 1),
    ("2026-11-03 18:00:00", _PAID, 90, "West", 1),
    ("2026-11-09 12:00:00", "2026-12-10 12:00:00", 999, "West", 1),
)

Row = tuple[str | None, str, int | None, str, int]
ProjectFactory = Callable[[dict[str, str]], SemanticProject]


@contextmanager
def _sample_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: ProjectFactory,
    rows: tuple[Row, ...],
    *,
    backend: str = "duckdb",
    profile: str = "table",
) -> Iterator[None]:
    physical = "DATETIME(6)" if backend == "mysql" else "TIMESTAMP(6)"
    prefix = "TIMESTAMP " if backend == "trino" else ""
    data = SourceData(
        f"id BIGINT, created {physical}, paid {physical}, amount BIGINT, region VARCHAR(10), cost BIGINT",
        ",".join(
            "("
            + ",".join(
                (
                    str(i),
                    "NULL" if created is None else f"{prefix}{created!r}",
                    f"{prefix}{paid!r}",
                    "NULL" if amount is None else str(amount),
                    repr(region),
                    str(cost),
                )
            )
            + ")"
            for i, (created, paid, amount, region, cost) in enumerate(rows)
        ),
        "id Int64, created Nullable(DateTime64(6, 'UTC')), paid DateTime64(6, 'UTC'), "
        "amount Nullable(Int64), region String, cost Int64",
        [
            {
                "id": i,
                "created": None
                if created is None
                else datetime.fromisoformat(created).replace(tzinfo=timezone.utc),
                "paid": datetime.fromisoformat(paid).replace(tzinfo=timezone.utc),
                "amount": amount,
                "region": region,
                "cost": cost,
            }
            for i, (created, paid, amount, region, cost) in enumerate(rows)
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "created = ms.time_dimension_column(name='created', entity=facts, column='created')\n"
                + "paid = ms.time_dimension_column(name='paid', entity=facts, column='paid')\n"
                + "region = ms.dimension_column(name='region', entity=facts, column='region')\n"
                + "amount = ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
                + "cost = ms.measure_column(name='cost', entity=facts, column='cost', additivity=ms.additive_all())\n"
                + "status = ms.measure_column(name='status', entity=facts, column='amount', additivity=ms.additive_except(created), status_time_dimension=created)\n",
                "sales/refunds.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"refunds = ms.entity(name='refunds', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "reviewed = ms.time_dimension_column(name='reviewed', entity=refunds, column='created')\n"
                + "refunded = ms.time_dimension_column(name='refunded', entity=refunds, column='paid')\n"
                + "region = ms.dimension_column(name='region', entity=refunds, column='region')\n"
                + "amount = ms.measure_column(name='amount', entity=refunds, column='amount', additivity=ms.additive_all())\n",
            }
        )
        yield


@pytest.fixture
def sample_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> Iterator[Session]:
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, _F9):
        yield mv.session.get_or_create("field-samples", timezone="UTC")


def _row_values(frame: pd.DataFrame, columns: list[str]) -> list[object]:
    result = frame[columns].loc[0]
    assert isinstance(result, pd.Series)
    return result.tolist()


def _fields(
    session: Session,
) -> tuple[ms.TimeDimension, ms.TimeDimension, ms.Measure, ms.Dimension]:
    created = catalog_value(session.catalog, "sales.facts.created", kind="field")
    paid = catalog_value(session.catalog, "sales.facts.paid", kind="field")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    region = catalog_value(session.catalog, "sales.facts.region", kind="field")
    assert isinstance(created, ms.TimeDimension) and isinstance(paid, ms.TimeDimension)
    assert isinstance(amount, ms.Measure) and isinstance(region, ms.Dimension)
    return created, paid, amount, region


def test_definition_contribution_time_keeps_explicit_sample_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> None:
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, _F9):
        model = tmp_path / "models" / "semantic" / "sales" / "time_roles.py"
        model.write_text(
            """import marivo.analysis as mv
import marivo.semantic as ms
from .facts import amount, created, paid

day = mv.grain("day")

@ms.metric()
def last_by_paid_day():
    return amount.sum(time=paid).per(day).last()

@ms.metric()
def last_by_created_day():
    return amount.sum(time=paid).per(created, grain=day).last()

@ms.metric()
def last_by_booked_day():
    return amount.sum(time=created).per(day).last()
"""
        )
        session = mv.session.get_or_create("defined-fold", timezone="UTC")
        _, paid, _, _ = _fields(session)
        november = session.time_scope(start="2026-11-01", end="2026-12-01")
        follows = session.catalog.get("metric:sales.last_by_paid_day")
        fixed = session.catalog.get("metric:sales.last_by_created_day")
        booked = session.catalog.get("metric:sales.last_by_booked_day")
        assert isinstance(follows, ms.Metric) and isinstance(fixed, ms.Metric)
        assert isinstance(booked, ms.Metric)
        metrics = {"paid_sample": follows, "created_sample": fixed, "booked_sample": booked}
        rows = (
            session.observe(
                metrics, where=(november, ((paid >= november.start) & (paid < november.end)))
            )
            .compute()
            .to_pandas()
        )
        assert _row_values(rows, list(metrics)) == [170, 140, 140]

        rows = session.observe(metrics, where=november).compute().to_pandas()
        assert _row_values(rows, list(metrics)) == [170, 140, 999]


def test_window_recomputes_event_mean_and_daily_fold_weights_days_equally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> None:
    rows = (
        ("2026-11-01 09:00:00", "2026-11-01 10:00:00", 100, "East", 1),
        ("2026-11-02 09:00:00", "2026-11-02 10:00:00", 300, "East", 1),
        ("2026-11-02 09:00:00", "2026-11-02 11:00:00", 300, "East", 1),
        ("2026-11-02 09:00:00", "2026-11-02 12:00:00", 300, "East", 1),
    )
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, rows):
        session = mv.session.get_or_create("event-mean", timezone="UTC")
        _, paid, amount, _ = _fields(session)
        mean = amount.mean(time=paid)
        window = mean.window(mv.trailing_duration(duration=timedelta(hours=48)))
        endpoint = session.time_scope(start="2026-11-02", end="2026-11-03")
        actual = session.observe({"mean": window}, where=endpoint).compute().to_pandas()
        assert actual["mean"].tolist() == [250]
        daily = mean.per(mv.grain("day")).mean()
        folded = session.observe({"mean": daily}).compute().to_pandas()
        assert folded["mean"].tolist() == [200]


def test_calendar_fold_completes_multiclock_formula_per_actual_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> None:
    rows = (
        ("2026-11-01 09:00:00", "2026-11-02 10:00:00", 100, "Booked", 1),
        ("2026-10-30 09:00:00", "2026-11-02 10:00:00", 300, "Settled", 1),
    )
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, rows):
        session = mv.session.get_or_create("daily-gap", timezone="UTC")
        created, paid, amount, region = _fields(session)
        booked = amount.sum(time=created).where(region == "Booked")
        settled = amount.sum(time=paid).where(region == "Settled")
        gap = (settled - booked).per(mv.grain("day")).mean()
        scope = session.time_scope(start="2026-11-01", end="2027-01-01")
        result = session.observe({"mean": gap}, where=scope).compute().to_pandas()
        assert result["mean"].tolist() == [100]
        monthly = (
            session.observe({"mean": gap}, by=(scope.by(mv.grain("month")),))
            .compute()
            .to_pandas()
            .sort_values("calendar:month")
        )
        assert monthly["mean"].tolist() == [100]


def test_field_and_contribution_samples_are_independent(sample_session: Session) -> None:
    created, paid, amount, region = _fields(sample_session)
    day = mv.grain("day")
    november = sample_session.time_scope(start="2026-11-01", end="2026-12-01")
    revenue = amount.sum(time=paid)
    metrics = {
        "exact_last": revenue.per(created).last(),
        "created_last": revenue.per(by=created, grain=day).last(),
        "created_mean": revenue.per(created, grain=day).mean(),
        "paid_last": revenue.per(day).last(),
        "paid_last_explicit": revenue.per(paid, grain=day).last(),
    }
    rows = sample_session.observe(metrics, where=november).compute().to_pandas()
    assert _row_values(rows, list(metrics)) == [90, 140, 85, 170, 170]
    members = mv.coordinates(by=(region,), keys=(("East",), ("West",)))
    mixed = (revenue / members.count()).per(created, grain=day).mean()
    assert sample_session.observe({"ratio": mixed}, where=november).compute().to_pandas()[
        "ratio"
    ].tolist() == [42.5]
    rows = sample_session.observe(metrics).compute().to_pandas()
    assert _row_values(rows, ["paid_last", "paid_last_explicit"]) == [999, 999]
    amount.sum().per(created, grain=day).mean()
    with pytest.raises(SemanticExpressionError):
        amount.sum().per(day).mean()
    raw_untimed = amount.sum().per(created).last()
    assert sample_session.observe({"v": raw_untimed}).compute().to_pandas()["v"].tolist() == [999]
    assert sample_session.observe({"v": raw_untimed}, where=november).compute().to_pandas()[
        "v"
    ].tolist() == [999]

    categorized = (
        sample_session.observe(
            {"v": metrics["created_last"], "same_paid_day": metrics["paid_last"]},
            by=(region, november.by(mv.grain("month"))),
        )
        .compute()
        .to_pandas()
        .sort_values("region")
    )
    assert categorized["region"].tolist() == ["East", "West"]
    assert categorized["v"].tolist() == [30, 140]
    assert categorized["same_paid_day"].tolist() == [30, 140]

    month = mv.grain("month")
    different_field = revenue.per(created, grain=mv.grain("week")).last()
    assert sample_session.observe(
        {"v": different_field}, by=(november.by(month),)
    ).compute().to_pandas()["v"].tolist() == [140]
    with pytest.raises(CoreRuleError):
        sample_session.observe(
            {"v": revenue.per(paid, grain=mv.grain("week")).last()}, by=(november.by(month),)
        )


def test_new_sample_shape_validates_grain_and_windows(
    sample_session: Session, source_trace: SourceTrace
) -> None:
    created, paid, amount, region = _fields(sample_session)
    revenue = amount.sum(time=paid)
    day = mv.grain("day")
    scope = sample_session.time_scope(start="2026-11-01", end="2026-12-01")
    for field, grain in (
        (created, None),
        (created, "day"),
        (region, day),
        ((created,), day),
        (day, day),
        (scope.by(day), day),
        (mv.coordinates(by=(region,), keys=(("East",),)), day),
    ):
        with pytest.raises(SemanticExpressionError):
            # Invalid field and grain combinations exercise runtime validation.
            revenue.per(field, grain=grain)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        # A second positional argument is intentionally invalid.
        revenue.per(created, day)  # type: ignore[call-overload]
    window = mv.all_history()
    for expression in (
        revenue.window(window).per(created, grain=day).last(),
        revenue.per(created, grain=day).last().window(window),
    ):
        sample_session.observe({"v": expression}, where=scope)
    with pytest.raises(SemanticExpressionError):
        sample_session.observe({"v": revenue.per(created, grain=day).last(order_by=created)})
    sample_session.observe({"v": revenue.per(created, grain=day).last()})
    assert source_trace.native_sql == []
    assert sample_session.observe(
        {"v": revenue.per(created).last().window(window)}, where=scope
    ).compute().to_pandas()["v"].tolist() == [90]


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
def test_calendar_sample_completes_exact_status_then_folds_actual_days(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
    backend: str,
    profile: str,
) -> None:
    paid = "2026-11-05 12:00:00"
    rows = (
        ("2026-11-01 09:00:00", paid, 10, "A", 1),
        ("2026-11-01 09:00:00", paid, 20, "B", 1),
        ("2026-11-01 18:00:00", paid, 5, "A", 1),
        ("2026-11-01 18:00:00", paid, 40, "B", 1),
        ("2026-11-03 18:00:00", paid, 20, "A", 1),
        ("2026-11-03 18:00:00", paid, 30, "B", 1),
        ("2026-11-03 19:00:00", _PAID, 999, "A", 1),
    )
    with _sample_project(
        tmp_path, monkeypatch, semantic_project_factory, rows, backend=backend, profile=profile
    ):
        session = mv.session.get_or_create("status", timezone="UTC")
        created, paid_field, _, _ = _fields(session)
        status = catalog_value(session.catalog, "sales.facts.status", kind="field")
        assert isinstance(status, ms.Measure)
        day = mv.grain("day")
        period = session.time_scope(start="2026-11-05", end="2026-11-06")
        spatial = status.sum(time=paid_field)
        closing = spatial.per(created).last(include_null=True).per(created, grain=day)
        actual = (
            session.observe({"daily_mean": closing.mean(), "last": closing.last()}, where=period)
            .compute()
            .to_pandas()
        )
        assert _row_values(actual, ["daily_mean", "last"]) == [47.5, 50]
        for invalid in (spatial.per(created, grain=day).last(), spatial.per(paid_field).last()):
            with pytest.raises(CoreRuleError):
                session.observe({"v": invalid}, where=period)


def test_calendar_sample_keeps_null_quantities_and_checks_only_selected_clocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> None:
    rows = (
        ("2026-11-01 09:00:00", _PAID, 100, "East", 1),
        ("2026-11-03 09:00:00", _PAID, 900, "West", 3),
        ("2026-11-04 09:00:00", _PAID, None, "West", 4),
        (None, _PAID, 999, "West", 1),
    )
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, rows):
        session = mv.session.get_or_create("null-samples", timezone="UTC")
        created, paid, amount, _ = _fields(session)
        cost = catalog_value(session.catalog, "sales.facts.cost", kind="field")
        assert isinstance(cost, ms.Measure)
        ratio = amount.sum(time=paid) / cost.sum(time=paid)
        samples = ratio.per(created, grain=mv.grain("day"))
        skip = samples.last(include_null=False)
        keep = samples.last(include_null=True)
        with pytest.raises(AnalysisError):
            session.observe({"v": skip}).compute()
        filtered = created.notnull()
        actual = session.observe({"skip": skip, "keep": keep}, where=filtered).compute().to_pandas()
        assert actual["skip"].tolist() == [300]
        assert pd.isna(actual["keep"].iat[0])
        empty = (
            session.observe({"last": keep, "mean": samples.mean()}, where=created < "2026-11-01")
            .compute()
            .to_pandas()
        )
        assert empty[["last", "mean"]].isna().all().all()


def test_builtin_samples_freeze_session_timezone_independently_of_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = (
        ("2026-11-01 15:59:59", "2026-11-01 15:59:59", 7, "East", 1),
        ("2026-11-01 16:00:00", "2026-11-01 16:00:00", 11, "East", 1),
    )
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, rows):
        utc = mv.session.get_or_create("sample-utc", timezone="UTC")
        shanghai = mv.session.get_or_create("sample-shanghai", timezone="Asia/Shanghai")
        day = mv.grain("day")
        for session, values in ((utc, [18, 18]), (shanghai, [7, 11])):
            created, _, amount, _ = _fields(session)
            metric = amount.sum(time=created)
            explicit = metric.per(created, grain=day)
            following = metric.per(day)
            metrics = {
                "first": explicit.first(),
                "last": explicit.last(),
                "following_first": following.first(),
                "following_last": following.last(),
            }
            scope = session.time_scope(start="2026-11-01", end="2026-11-03")
            for where in (None, scope):
                pending = session.observe(metrics, where=where)
                pending.show(n=0, max_output_bytes=None)
                actual = pending.compute().to_pandas()
                assert _row_values(actual, list(metrics)) == values * 2
            if session is utc:
                for metric in metrics.values():
                    with pytest.raises(CoreRuleError):
                        foreign = shanghai.time_scope(start="2026-11-01", end="2026-11-03")
                        session.observe({"v": metric}, by=(foreign.by(day),))


@pytest.mark.parametrize(
    "backend,profile",
    source_params([("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]),
)
@pytest.mark.parametrize("unit", ["hour", "minute", "second"])
def test_calendar_sample_distinguishes_repeated_civil_hour_instants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
    backend: str,
    profile: str,
    unit: Literal["hour", "minute", "second"],
) -> None:
    rows = (
        ("2026-11-01 05:30:00", _PAID, 7, "East", 1),
        ("2026-11-01 06:30:00", _PAID, 11, "East", 1),
    )
    with _sample_project(
        tmp_path, monkeypatch, semantic_project_factory, rows, backend=backend, profile=profile
    ):
        session = mv.session.get_or_create("repeated-hour", timezone="America/New_York")
        created, _, amount, _ = _fields(session)
        repeated_hours = amount.sum(time=created).per(created, grain=mv.grain(unit))
        actual = (
            session.observe(
                {
                    "first": repeated_hours.first(),
                    "last": repeated_hours.last(),
                    "mean": repeated_hours.mean(),
                }
            )
            .compute()
            .to_pandas()
        )
        # 05:30 and 06:30 UTC both display as 01:30 locally.
        assert _row_values(actual, ["first", "last", "mean"]) == [7, 11, 9]
        assert actual["first"].dtype == actual["last"].dtype == pd.ArrowDtype(pa.int64())
        assert actual["mean"].dtype == pd.ArrowDtype(pa.float64())


@pytest.mark.parametrize(
    "backend,profile",
    source_params([("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]),
)
def test_ordered_calendar_samples_preserve_parent_nulls_and_empty_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
    backend: str,
    profile: str,
) -> None:
    rows = tuple(
        (f"2026-11-01 {hour:02}:00:00", _PAID, value, region, 1)
        for region, values in (
            ("A", (None, 7, 11)),
            ("B", (13, 17, None)),
            ("C", (None, None, None)),
        )
        for hour, value in enumerate(values)
    )
    with _sample_project(
        tmp_path, monkeypatch, semantic_project_factory, rows, backend=backend, profile=profile
    ):
        session = mv.session.get_or_create("ordered-null-samples", timezone="UTC")
        created, _, amount, region = _fields(session)
        samples = amount.sum(time=created).per(created, grain=mv.grain("hour"))
        floating = (amount.sum(time=created) / 2).per(created, grain=mv.grain("hour"))
        metrics = {
            "first": samples.first(),
            "last": samples.last(),
            "keep_first": samples.first(include_null=True),
            "keep_last": samples.last(include_null=True),
            "floating_last": floating.last(),
        }
        actual = session.observe(metrics, by=(region,)).compute().to_pandas()
        actual = actual.set_index(actual.columns[0]).sort_index()
        expected = {
            "first": [7, 13, None],
            "last": [11, 17, None],
            "keep_first": [None, 13, None],
            "keep_last": [11, None, None],
            "floating_last": [5.5, 8.5, None],
        }
        assert actual.index.tolist() == ["A", "B", "C"]
        for name, values in expected.items():
            dtype = pd.ArrowDtype(pa.float64() if name == "floating_last" else pa.int64())
            pd.testing.assert_series_equal(
                actual[name].reset_index(drop=True), pd.Series(values, dtype=dtype, name=name)
            )
        empty = session.observe(metrics, where=created < "2020-01-01").compute().to_pandas()
        assert len(empty) == 1 and empty.isna().all().all()
        assert empty.dtypes.to_dict() == actual.dtypes.to_dict()


@pytest.mark.parametrize("unit", ["hour", "minute", "second"])
@pytest.mark.parametrize(
    "zone,clock",
    [("UTC+05:30:15", "2026-11-01 18:29:44"), ("UTC-05:30:15", "2026-11-02 05:30:14")],
)
@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("mysql", "innodb-table"),
            ("postgres", "table"),
            ("clickhouse", "mergetree"),
            ("trino", "iceberg"),
        ]
    ),
)
def test_fixed_offset_samples_split_at_civil_midnight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
    backend: str,
    profile: str,
    unit: Literal["hour", "minute", "second"],
    zone: str,
    clock: str,
) -> None:
    final_second = datetime.fromisoformat(clock)
    rows = (
        (final_second.replace(microsecond=1).isoformat(sep=" "), _PAID, 7, "East", 1),
        (final_second.replace(microsecond=999999).isoformat(sep=" "), _PAID, 11, "East", 1),
        ((final_second + timedelta(seconds=1)).isoformat(sep=" "), _PAID, 23, "East", 1),
    )
    with _sample_project(
        tmp_path, monkeypatch, semantic_project_factory, rows, backend=backend, profile=profile
    ):
        session = mv.session.get_or_create("fixed-offset-midnight", timezone=zone)
        created, _, amount, _ = _fields(session)
        samples = amount.sum(time=created).per(created, grain=mv.grain(unit))
        actual = (
            session.observe(
                {
                    "minimum": samples.min(),
                    "maximum": samples.max(),
                    "mean": samples.mean(),
                    "count": samples.count(),
                }
            )
            .compute()
            .to_pandas()
        )
        # The first two events are in the final civil second of November 1.
        assert _row_values(actual, ["minimum", "maximum", "mean", "count"]) == [18, 23, 20.5, 2]


def test_forecast_versions_follow_target_range_and_explicit_release_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> None:
    target = "2026-12-01 12:00:00"
    rows = (
        ("2026-11-01 09:00:00", target, 1000, "A", 1),
        ("2026-11-05 09:00:00", target, 1200, "A", 1),
        ("2026-11-05 18:00:00", target, 1300, "A", 1),
        ("2026-11-12 09:00:00", target, 900, "A", 1),
        ("2026-11-09 09:00:00", "2026-12-02 00:00:00", 2000, "A", 1),
    )
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, rows):
        session = mv.session.get_or_create("forecast", timezone="UTC")
        issued, target_at, _, _ = _fields(session)
        forecast = catalog_value(session.catalog, "sales.facts.status", kind="field")
        assert isinstance(forecast, ms.Measure)
        day = mv.grain("day")
        period = session.time_scope(start="2026-12-01", end="2026-12-02")
        latest = forecast.sum(time=target_at).per(issued).last(include_null=True)
        daily = latest.per(issued, grain=day)
        cutoff = issued < "2026-11-10"
        actual = (
            session.observe(
                {"latest": latest, "mean": daily.mean(), "last": daily.last()},
                where=(period, cutoff),
            )
            .compute()
            .to_pandas()
        )
        assert _row_values(actual, ["latest", "mean", "last"]) == [1300, 1150, 1300]
        assert session.observe(
            {"v": latest}, where=(period, issued < "2026-11-05T18:00:00+00:00")
        ).compute().to_pandas()["v"].tolist() == [1200]
        joint = (
            session.observe(
                {"v": latest.per(issued, grain=mv.grain("month")).last()},
                by=(period.by(day),),
                where=cutoff,
            )
            .compute()
            .to_pandas()
        )
        assert joint["v"].tolist() == [1300]

        history = latest.window(mv.all_history())
        assert session.observe({"v": history}, where=(period, cutoff)).compute().to_pandas()[
            "v"
        ].tolist() == [1300]
        assert session.observe({"v": history}, where=period).compute().to_pandas()[
            "v"
        ].tolist() == [900]


def test_multiroot_field_folds_preserve_each_completed_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> None:
    rows = (
        ("2026-10-31 09:00:00", "2026-11-01 12:00:00", 100, "Receipt", 1),
        ("2026-11-02 09:00:00", "2026-11-02 12:00:00", 50, "Receipt", 1),
        ("2026-10-30 09:00:00", "2026-11-02 12:00:00", 20, "Refund", 1),
        ("2026-11-01 09:00:00", "2026-11-03 12:00:00", 5, "Refund", 1),
    )
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, rows):
        session = mv.session.get_or_create("cashflow", timezone="UTC")
        created, paid, amount, region = _fields(session)
        reviewed = catalog_value(session.catalog, "sales.refunds.reviewed", kind="field")
        refunded = catalog_value(session.catalog, "sales.refunds.refunded", kind="field")
        refund_amount = catalog_value(session.catalog, "sales.refunds.amount", kind="field")
        refund_kind = catalog_value(session.catalog, "sales.refunds.region", kind="field")
        assert isinstance(reviewed, ms.TimeDimension) and isinstance(refunded, ms.TimeDimension)
        assert isinstance(refund_amount, ms.Measure) and isinstance(refund_kind, ms.Dimension)
        receipts = amount.sum(time=paid).where(region == "Receipt")
        refunds = refund_amount.sum(time=refunded).where(refund_kind == "Refund")
        day = mv.grain("day")
        closing = receipts.per(created, grain=day).last() - refunds.per(reviewed, grain=day).last()
        period = session.time_scope(start="2026-11-01", end="2026-12-01")
        actual = (
            session.observe(
                {
                    "closing": closing,
                    "total": receipts - refunds,
                    "last_shared_day": (receipts - refunds).per(day).last(),
                },
                where=period,
            )
            .compute()
            .to_pandas()
        )
        assert _row_values(actual, ["closing", "total", "last_shared_day"]) == [45, 125, -5]
        with pytest.raises(SemanticExpressionError):
            session.observe(
                {"v": (receipts - refunds).per(created, grain=day).last()}, where=period
            )


def test_asynchronous_status_last_and_spatial_sum_are_order_sensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
) -> None:
    rows = (
        ("2026-11-01 09:00:00", _PAID, 10, "A", 1),
        ("2026-11-01 09:00:00", _PAID, 20, "B", 1),
        ("2026-11-03 18:00:00", _PAID, 15, "A", 1),
    )
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, rows):
        session = mv.session.get_or_create("asynchronous-status", timezone="UTC")
        snapshot, paid, _, region = _fields(session)
        status = catalog_value(session.catalog, "sales.facts.status", kind="field")
        assert isinstance(status, ms.Measure)
        last_snapshot = status.sum(time=paid).per(snapshot).last(include_null=True)
        actual = (
            session.observe(
                {
                    "whole_snapshot": last_snapshot,
                    "last_per_region": last_snapshot.per(region).sum(),
                }
            )
            .compute()
            .to_pandas()
        )
        assert _row_values(actual, ["whole_snapshot", "last_per_region"]) == [15, 35]


@pytest.mark.parametrize("windowed", [False, True])
def test_saved_field_calendar_fold_reopens_without_sources_or_sample_axes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
    capsys: pytest.CaptureFixture[str],
    windowed: bool,
) -> None:
    with _sample_project(tmp_path, monkeypatch, semantic_project_factory, _F9):
        model = tmp_path / "models" / "semantic" / "sales" / "folded.py"
        model.write_text(
            "import marivo.analysis as mv\n"
            "import marivo.semantic as ms\n"
            "from .facts import amount, created, paid\n"
            "day = mv.grain('day')\n"
            "@ms.metric()\ndef latest_creation_day():\n"
            f"    return amount.sum(time=paid){'.window(mv.all_history())' if windowed else ''}.per(created, grain=day).last()\n"
        )
        session = mv.session.get_or_create("persisted-sample", timezone="UTC")
        metric = catalog_value(session.catalog, "sales.latest_creation_day", kind="metric")
        assert isinstance(metric, ms.Metric)
        metric.show(max_output_bytes=None)
        definition = capsys.readouterr().out
        november = session.time_scope(start="2026-11-01", end="2026-12-01")
        saved = session.observe({"v": metric}, where=november).persist()
        assert saved.artifact_id is not None
    (tmp_path / "models").rename(tmp_path / "models.offline")
    (tmp_path / "source.duckdb").rename(tmp_path / "source.offline")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import marivo.analysis as mv

frame = mv.session.resume(sys.argv[1]).persisted_frame(sys.argv[2])
assert not frame.axes
expected = 0 if sys.argv[3] == "True" else 140
assert frame.to_pandas()["v"].tolist() == [expected]
if sys.argv[3] == "True":
    endpoint = frame.schema.value_time_semantics["v"].contributions[0].window_endpoint
    assert isinstance(endpoint, mv.FrameFieldSampleWindowEndpoint)
    assert endpoint.field.path == "sales.facts.created"
assert not hasattr(frame, "per")
""",
            session.id,
            saved.artifact_id,
            str(windowed),
        ],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), TZ="America/Los_Angeles"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "non-iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_calendar_window_order_and_cross_clock_cohorts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: ProjectFactory,
    backend: str,
    profile: str,
) -> None:
    rows: tuple[Row, ...] = (
        ("2026-01-01 12:00:00", "2026-01-02 12:00:00", 10, "East", 1),
        ("2026-01-03 12:00:00", "2026-01-03 12:00:00", 30, "East", 1),
        ("2026-01-03 12:00:00", "2026-01-05 12:00:00", 50, "East", 1),
        ("2026-01-05 12:00:00", "2026-01-05 12:00:00", 70, "West", 1),
    )
    with _sample_project(
        tmp_path, monkeypatch, semantic_project_factory, rows, backend=backend, profile=profile
    ):
        session = mv.session.get_or_create("sample-window-cohorts", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        paid = catalog_value(session.catalog, "sales.facts.paid", kind="time_dimension")
        created = catalog_value(session.catalog, "sales.facts.created", kind="time_dimension")
        region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
        scope = session.time_scope(start="2026-01-03", end="2026-01-06")
        # Explicit Float64 keeps this temporal comparison independent of Decimal scale.
        revenue = amount.sum(time=paid).cast("float64")
        day = mv.grain("day")
        observed = session.observe(
            {
                "cohort": revenue.window(mv.all_history()).per(created, grain=day).mean(),
                "clock": revenue.window(mv.all_history()).per(paid, grain=day).mean(),
                "clock_grain": revenue.window(mv.all_history()).per(day).mean(),
                "outer": revenue.per(created, grain=day).mean().window(mv.all_history()),
            },
            where=scope,
        ).compute()
        actual = observed.to_pandas()
        assert actual["cohort"].tolist() == [50]
        assert actual["clock"].tolist() == [100]
        assert actual["clock_grain"].tolist() == [100]
        assert [float(value) for value in actual["outer"]] == pytest.approx([160 / 3])
        facts = observed.schema.value_time_semantics
        cohort = facts["cohort"].contributions[0].window_endpoint
        assert isinstance(cohort, mv.FrameFieldSampleWindowEndpoint)
        assert cohort.field.path == "sales.facts.created"
        assert cohort.layer == 1
        assert isinstance(
            facts["outer"].contributions[0].window_endpoint, mv.FrameReportWindowEndpoint
        )
        grouped = (
            session.observe(
                {
                    "cohort": revenue.window(mv.all_history()).per(created, grain=day).mean(),
                    "clock": revenue.window(mv.all_history()).per(paid, grain=day).mean(),
                },
                by=(scope.by(mv.grain("month")), region),
            )
            .compute()
            .to_pandas()
        )
        assert sorted(grouped["cohort"].tolist()) == [30, 70]
        assert sorted(grouped["clock"].tolist()) == [65, 70]


def test_calendar_sample_windows_share_multiroot_endpoint_domain(sample_session: Session) -> None:
    session = sample_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    paid = catalog_value(session.catalog, "sales.facts.paid", kind="time_dimension")
    refund = catalog_value(session.catalog, "sales.refunds.amount", kind="measure")
    reviewed = catalog_value(session.catalog, "sales.refunds.reviewed", kind="time_dimension")
    net = amount.sum(time=paid) - refund.sum(time=reviewed)
    scope = session.time_scope(start="2026-11-01", end="2026-12-01")
    actual = (
        session.observe(
            {"net": net.window(mv.all_history()).per(mv.grain("day")).mean()}, where=scope
        )
        .compute()
        .to_pandas()
    )
    # Actual endpoint dates are Nov 1, 3, 9 and 10, shared across both clocks.
    assert actual["net"].tolist() == [-592]
