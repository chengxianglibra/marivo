"""Calendar samples reduce actual complete contribution keys without filling a grid."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._authoring.model import AuthoringCapability
from marivo._definition.windows import ObservationWindow
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic._capabilities.registry import REGISTRY
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._expression_values import MetricExpression
from marivo.semantic.catalog import Entity, Metric
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value, time_population
from tests.support.documentation import _example as documentation_example


@pytest.fixture
def calendar_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Session:
    points = [
        ("2026-10-05 00:00:00", 10),
        ("2026-10-05 12:00:00", 30),
        ("2026-10-06 00:00:00", 20),
        ("2026-10-06 12:00:00", 40),
        ("2026-10-08 00:00:00", 70),
        ("2026-10-08 12:00:00", 90),
        ("2026-10-11 00:00:00", 140),
        ("2026-10-11 12:00:00", 160),
        ("2026-11-01 00:00:00", 200),
        ("2026-11-02 00:00:00", 50),
        ("2026-11-02 12:00:00", 500),
    ]
    data = pd.DataFrame(
        {
            "id": range(len(points)),
            "point": [datetime.fromisoformat(point) for point, _ in points],
            "other": [datetime.fromisoformat(point) + timedelta(days=1) for point, _ in points],
            "amount": [value for _, value in points],
            "region": ["east"] * len(points),
        }
    )
    database = tmp_path / "warehouse.duckdb"
    backend = ibis.duckdb.connect(database)
    try:
        backend.create_table("facts", data)
    finally:
        backend.disconnect()
    monkeypatch.chdir(tmp_path)
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(wall_clock_timezone='UTC', name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nimport marivo.analysis as mv\n"
            "facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])\n"
            "point = ms.time_dimension_column(name='point', entity=facts, column='point')\n"
            "sampled = ms.time_dimension_column(name='sampled', entity=facts, column='point', sample_interval=(12,'hour'))\n"
            "other = ms.time_dimension_column(name='other', entity=facts, column='other')\n"
            "region = ms.dimension_column(name='region', entity=facts, column='region')\n"
            "amount = ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
            "day = mv.grain('day')\n"
            "@ms.metric()\ndef daily_average():\n    return amount.sum(time=point).per(day).mean()\n",
        }
    )
    return mv.session.get_or_create("calendar-samples", timezone="UTC")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("mean", 140),
        ("p95_median", 64),
        ("ratio_mean", 70),
        ("weighted", 140),
        ("first", 40),
        ("last", 300),
        ("nested", 320 / 4),
    ],
)
def test_weekly_calendar_samples_use_actual_days_and_finish_daily_p95(
    calendar_session: Session,
    name: str,
    expected: float,
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    facts_entry = session.catalog.get("entity:sales.facts")
    assert isinstance(facts_entry, Entity)
    facts = facts_entry
    revenue = amount.sum(time=point)
    daily_p95 = revenue.per(point).quantile(0.95)
    weeks_scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    # Each reducer has its own independent oracle. Compiling all seven into
    # one graph multiplies unrelated compiler work; mixed outputs are covered
    # by test_observe_different_fields_share_one_actual_aggregate.
    metrics = {
        "mean": revenue.per(mv.grain("day")).mean(),
        "p95_median": daily_p95.per(mv.grain("day")).median(),
        "ratio_mean": (revenue / facts.count(time=point)).per(mv.grain("day")).mean(),
        "weighted": revenue.per(mv.grain("day")).weighted_mean(weight=facts.count(time=point)),
        "first": revenue.per(mv.grain("day")).first(),
        "last": revenue.per(mv.grain("day")).last(),
        "nested": revenue.per(mv.grain("hour")).max().per(mv.grain("day")).mean(),
    }
    rows = (
        session.observe({name: metrics[name]}, by=(weeks_scope.by(mv.grain("week")), region))
        .compute()
        .to_pandas()
    )
    # Actual daily sums [40, 60, 160, 300]; daily P95 [29, 39, 89, 159].
    assert rows[name].tolist() == (pytest.approx([expected]) if name == "nested" else [expected])
    if name == "ratio_mean":
        assert session.observe(
            {"v": (revenue / facts.count(time=point)).per(mv.grain("day")).mean(nulls="error")},
            by=(weeks_scope.by(mv.grain("week")),),
        ).compute().to_pandas()["v"].tolist() == [70]


def test_calendar_samples_clip_partial_month_edges_and_plain_scope(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    metric = amount.sum(time=point).per(mv.grain("day")).mean()
    scope = session.time_scope(start="2026-10-05T06:00:00", end="2026-11-02T06:00:00")
    monthly = (
        session.observe({"v": metric}, by=(scope.by(mv.grain("month")),)).compute().to_pandas()
    )
    assert monthly["v"].tolist() == pytest.approx([550 / 4, 125])
    whole = session.observe({"v": metric}, where=scope).compute().to_pandas()
    assert whole["v"].tolist() == pytest.approx([800 / 6])
    empty_scope = session.time_scope(start="2026-10-12", end="2026-10-19")
    assert (
        session.observe({"v": metric}, by=(empty_scope.by(mv.grain("week")),))
        .compute()
        .to_pandas()["v"]
        .isna()
        .all()
    )


def test_calendar_samples_bucket_each_component_by_its_own_clock(calendar_session: Session) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    other = catalog_value(session.catalog, "sales.facts.other", kind="time_dimension")
    metric = (amount.sum(time=point) + amount.sum(time=other)).per(mv.grain("day")).max()
    scope_scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    assert session.observe(
        {"v": metric}, by=(scope_scope.by(mv.grain("week")),)
    ).compute().to_pandas()["v"].tolist() == [300]


def test_final_completion_keeps_empty_policies_and_historical_window_values(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    total = amount.sum(time=point)
    days_scope = session.time_scope(start="2026-10-12", end="2026-10-14")
    rows = (
        session.observe(
            {
                "sum_plus_one": total + 1,
                "mean_plus_one": amount.mean(time=point) + 1,
                "sample_mean": total.per(mv.grain("hour")).mean(),
                "history": total.window(mv.trailing_duration(duration=timedelta(days=2))),
            },
            by=(days_scope.by(mv.grain("day")),),
        )
        .compute()
        .to_pandas()
    )
    assert rows["sum_plus_one"].tolist() == [1]
    assert rows["mean_plus_one"].isna().all() and rows["sample_mean"].isna().all()
    assert rows["history"].tolist() == [300]


def test_actual_null_sample_is_retained_while_missing_samples_are_absent(
    calendar_session: Session, tmp_path: Path
) -> None:
    backend = ibis.duckdb.connect(tmp_path / "warehouse.duckdb")
    try:
        backend.raw_sql("INSERT INTO facts VALUES (100, '2027-01-01', '2027-01-02', NULL, 'east')")
    finally:
        backend.disconnect()
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    metric = amount.sum(time=point)
    days_scope = session.time_scope(start="2027-01-01", end="2027-01-03")
    rows = (
        session.observe({"v": metric + 1}, by=(days_scope.by(mv.grain("day")),))
        .compute()
        .to_pandas()
    )
    assert len(rows) == 1 and pd.isna(rows.v.iloc[0])
    strict = metric.per(mv.grain("hour")).mean(nulls="error")
    with pytest.raises(MaterializationError):
        session.observe(
            {"v": strict}, where=session.time_scope(start="2027-01-01", end="2027-01-02")
        ).compute()
    missing = (
        session.observe(
            {"v": strict}, where=session.time_scope(start="2027-01-02", end="2027-01-03")
        )
        .compute()
        .to_pandas()
    )
    assert missing.v.isna().all()


def test_calendar_sample_binding_errors_are_structured(calendar_session: Session) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    metric = amount.sum(time=point)
    scope = session.time_scope(start="2026-10-05", end="2026-11-02")
    month = scope.by(mv.grain("month"))
    with pytest.raises(CoreRuleError) as captured:
        session.observe(
            {"v": metric.per(mv.grain("week")).mean()}, by=(scope.by(mv.grain("month")),)
        )
    assert "static" in (captured.value.expected or "")
    assert "week" in (captured.value.received or "")
    assert captured.value.repair is not None and captured.value.repair.action
    with pytest.raises(SemanticExpressionError):
        amount.sum().per(mv.grain("day")).mean()
    with pytest.raises(AttributeError):
        absent = metric.sum  # type: ignore[attr-defined]  # Verify the missing Metric method.
        assert absent is None
    with pytest.raises(SemanticExpressionError) as semantic_error:
        consume_metric(metric.per(month).mean())
    assert semantic_error.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
    assert "Calendar" in (semantic_error.value.received or "")
    assert semantic_error.value.repair is not None
    assert 'mv.grain("day")' in semantic_error.value.repair.action


def test_calendar_folding_keeps_sparse_native_samples_and_empty_cells(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    sampled = catalog_value(session.catalog, "sales.facts.sampled", kind="time_dimension")
    p95 = amount.sum(time=sampled).per(sampled).quantile(0.95).per(mv.grain("day")).mean()
    scope = session.time_scope(start="2026-10-05", end="2026-10-07")
    assert session.observe({"v": p95}, where=scope).compute().to_pandas()["v"].tolist() == [34]
    missing = session.time_scope(start="2026-10-05", end="2026-10-08")
    assert session.observe({"v": p95}, where=missing).compute().to_pandas()["v"].tolist() == [34]


def test_published_calendar_metric_can_be_nested_under_another_calendar_layer(
    calendar_session: Session,
) -> None:
    session = calendar_session
    daily_entry = session.catalog.get("metric:sales.daily_average")
    assert isinstance(daily_entry, Metric)
    daily = daily_entry
    scope = session.time_scope(start="2026-10-05", end="2026-11-02")
    rows = (
        session.observe({"v": daily.per(mv.grain("week")).max()}, where=scope).compute().to_pandas()
    )
    assert rows["v"].tolist() == [200]


def test_calendar_sample_help_and_bilingual_examples_execute(calendar_session: Session) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    descriptor = REGISTRY.by_canonical_id("metric.per")
    assert isinstance(descriptor, AuthoringCapability) and descriptor.minimal_example is not None
    sources = [
        descriptor.minimal_example,
        *(
            documentation_example(edition, "calendar-sample-fold", page="analysis/time-series")
            for edition in ("docs", "zh-cn/docs")
        ),
    ]
    for source in sources:
        namespace: dict[str, object] = {
            "mv": mv,
            "session": session,
            "revenue": amount.sum(time=point),
            "region": region,
            "august": session.time_scope(start="2026-10-05", end="2026-10-12"),
        }
        exec(compile(source, "<calendar-sample-example>", "exec"), namespace)
        folded = namespace["folded"]
        assert isinstance(folded, mv.AnalysisFrame)
        assert folded.to_pandas()["daily_average"].tolist() == [140]


def test_calendar_sample_mean_uses_present_hours_during_dst(calendar_session: Session) -> None:
    session = mv.session.get_or_create("calendar-new-york", timezone="America/New_York")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    day_scope = session.time_scope(start="2026-11-01", end="2026-11-02")
    rows = (
        session.observe(
            {"v": amount.sum(time=point).per(mv.grain("hour")).mean()},
            by=(day_scope.by(mv.grain("day")),),
        )
        .compute()
        .to_pandas()
    )
    assert rows["v"].tolist() == [50]  # Only the one represented hour contributes.


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("placement", ["inside", "wrapping"])
@pytest.mark.parametrize("reset", [False, True])
def test_calendar_sample_windows_preserve_order_and_empty_sample_domains(
    calendar_session: Session, empty: bool, placement: str, reset: bool
) -> None:
    session = calendar_session
    revenue = catalog_value(session.catalog, "sales.facts.amount", kind="measure").sum(
        time=catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    )
    scope_scope = session.time_scope(
        start="2026-10-12" if empty else "2026-10-05",
        end="2026-10-19" if empty else "2026-10-12",
    )
    rule = mv.to_date(grain=mv.grain("week")) if reset else mv.all_history()
    metric = (
        revenue.window(rule).per(mv.grain("day")).sum()
        if placement == "inside"
        else revenue.per(mv.grain("day")).sum().window(rule)
    )
    rows = (
        session.observe({"v": metric}, by=(scope_scope.by(mv.grain("week")),)).compute().to_pandas()
    )
    expected = (0 if empty else 960) if placement == "inside" else (0 if empty and reset else 560)
    assert rows["v"].tolist() == ([] if empty and (placement == "inside" or reset) else [expected])


def test_calendar_windows_complete_weighted_nested_and_declared_formulas(
    calendar_session: Session,
) -> None:
    session = calendar_session
    revenue = catalog_value(session.catalog, "sales.facts.amount", kind="measure").sum(
        time=catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    )
    daily = catalog_value(session.catalog, "sales.daily_average", kind="metric")
    assert isinstance(daily, MetricExpression)
    metrics = (
        revenue.per(mv.grain("day")).weighted_mean(weight=revenue.window(mv.all_history())),
        revenue.window(mv.all_history()).per(mv.grain("hour")).max().per(mv.grain("day")).mean(),
        daily.window(mv.all_history()),
    )
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    for metric, expected in zip(metrics, (226.25, 240, 140), strict=True):
        rows = session.observe({"v": metric}, where=scope).compute().to_pandas()
        assert rows["v"].tolist() == [expected]


def test_calendar_folds_and_windows_on_independent_branches_keep_their_values(
    calendar_session: Session,
) -> None:
    session = calendar_session
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    revenue = catalog_value(session.catalog, "sales.facts.amount", kind="measure").sum(time=point)
    weeks_scope = session.time_scope(start="2026-10-12", end="2026-10-19")
    data = (
        session.observe(
            {
                "v": revenue.per(mv.grain("day")).sum()
                + revenue.window(mv.all_history()).per(point).sum()
            },
            by=(weeks_scope.by(mv.grain("week")),),
        )
        .compute()
        .to_pandas()
    )
    assert data["v"].tolist() == [560]


def test_calendar_sample_formula_discloses_public_builtin_grain(
    calendar_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = calendar_session.catalog.get("metric:sales.daily_average")
    assert isinstance(entry, Metric)
    entry.show(max_output_bytes=None)
    text = capsys.readouterr().out


def test_window_calendar_order_preserves_sparse_samples(
    calendar_session: Session, tmp_path: Path
) -> None:
    import duckdb

    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts VALUES (1, '2026-01-02', '2026-01-02', 20, 'east'), (2, '2026-01-03', '2026-01-03', 30, 'east'), (3, '2026-01-05', '2026-01-05', 50, 'east')"
        )
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    revenue = amount.sum(time=point)
    window = mv.trailing_duration(duration=timedelta(days=3))
    scope = session.time_scope(start="2026-01-03", end="2026-01-06")
    rows = (
        session.observe(
            {
                "outer": revenue.per(mv.grain("day")).mean().window(window),
                "inner": revenue.window(window).per(mv.grain("day")).mean(),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    assert rows["outer"].tolist() == [40]
    assert rows["inner"].tolist() == [65]


@pytest.mark.parametrize(
    "rule,expected",
    [
        (mv.all_history(), 240),
        (mv.trailing_duration(duration=timedelta(days=2)), 150),
        (mv.trailing_periods(count=2), 150),
        (mv.to_date(grain=mv.grain("day")), 140),
    ],
)
def test_inner_window_rules_use_sample_endpoints(
    calendar_session: Session, rule: ObservationWindow, expected: float
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    metric = amount.sum(time=point).window(rule).per(mv.grain("day")).mean()
    rows = session.observe({"v": metric}, by=(scope.by(mv.grain("week")),)).compute().to_pandas()
    assert rows["v"].tolist() == [expected]


def test_inner_window_preserves_fixed_conditions_and_independent_denominator(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    filtered = amount.sum(time=point, where=point >= "2026-10-08")
    fixed = amount.sum(time=None)
    window = mv.since(start=session.instant("2026-10-01"))
    rows = (
        session.observe(
            {
                "filtered": filtered.window(window).per(mv.grain("day")).mean(),
                "ratio": (amount.sum(time=point) / fixed)
                .window(window)
                .per(mv.grain("day"))
                .mean(),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    assert rows["filtered"].tolist() == [310]
    assert rows["ratio"].tolist() == pytest.approx([240 / 1310])


def test_sample_endpoints_work_without_a_report_range(calendar_session: Session) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    revenue = amount.sum(time=point)
    rows = (
        session.observe(
            {
                "v": revenue.window(mv.to_date(grain=mv.grain("day"))).per(mv.grain("day")).mean(),
            }
        )
        .compute()
        .to_pandas()
    )
    assert rows["v"].tolist() == pytest.approx([1310 / 6])


def test_explicit_sample_window_keeps_independent_values_on_report_axis(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    metric = amount.sum(time=None).window(mv.all_history()).per(point, grain=mv.grain("day")).mean()
    rows = (
        session.observe({"fixed": metric}, by=time_population(scope.by(mv.grain("week"))))
        .compute()
        .to_pandas()
    )
    assert rows["fixed"].tolist() == pytest.approx([1310 / 6])
    # An independent sample clock cannot add an otherwise unsupported report week.
    sparse_axis = session.time_scope(start="2026-10-05", end="2026-10-19").by(mv.grain("week"))
    mixed = (
        session.observe({"actual": amount.sum(time=point), "fixed": metric}, by=(sparse_axis,))
        .compute()
        .to_pandas()
    )
    assert mixed["calendar:week"].dt.day.tolist() == [5]
    assert mixed["actual"].tolist() == [560]
    assert mixed["fixed"].tolist() == pytest.approx([1310 / 6])
    counted = (
        session.observe(
            {"positions": time_population(sparse_axis).count(), "fixed": metric}, by=(sparse_axis,)
        )
        .compute()
        .to_pandas()
    )
    assert counted["positions"].tolist() == [1, 1]
    assert counted["fixed"].tolist() == pytest.approx([1310 / 6, 1310 / 6])


@pytest.mark.parametrize("edition", ["docs", "zh-cn/docs"])
def test_calendar_window_documentation_examples(calendar_session: Session, edition: str) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    namespace: dict[str, object] = {
        "mv": mv,
        "session": session,
        "revenue": amount.sum(time=point),
        "august": session.time_scope(start="2026-10-05", end="2026-10-12"),
    }
    source = documentation_example(edition, "calendar-sample-window", page="analysis/time-series")
    exec(compile(source, "<calendar-window-example>", "exec"), namespace)
    frame = namespace["window_comparison"]
    assert isinstance(frame, mv.AnalysisFrame)
    actual = frame.to_pandas()
    assert actual["window_daily_mean"].tolist() == [140]
    assert actual["mean_daily_window"].tolist() == [240]


def test_calendar_window_dst_distinguishes_elapsed_and_civil_days(
    calendar_session: Session, tmp_path: Path
) -> None:
    import duckdb

    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts VALUES (1, '2026-03-08 04:30:00', '2026-03-08 04:30:00', 10, 'east'), (2, '2026-03-08 16:00:00', '2026-03-08 16:00:00', 20, 'east')"
        )
    session = mv.session.get_or_create("sample-window-dst", timezone="America/New_York")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    revenue = amount.sum(time=point)
    scope = session.time_scope(start="2026-03-08", end="2026-03-09")
    rows = (
        session.observe(
            {
                "elapsed": revenue.window(mv.trailing_duration(duration=timedelta(days=1)))
                .per(mv.grain("day"))
                .mean(),
                "civil": revenue.window(mv.trailing_periods(count=1)).per(mv.grain("day")).mean(),
            },
            by=(scope.by(mv.grain("day")),),
        )
        .compute()
        .to_pandas()
    )
    assert rows["elapsed"].tolist() == [30]
    assert rows["civil"].tolist() == [20]


def test_partial_sample_endpoints_keep_empty_window_samples(calendar_session: Session) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    revenue = amount.sum(time=point)
    scope = session.time_scope(start="2026-10-05T06:00Z", end="2026-10-05T14:00Z")
    rows = (
        session.observe(
            {
                "empty": revenue.window(mv.trailing_duration(duration=timedelta(hours=1)))
                .per(mv.grain("day"))
                .mean(),
                "history": revenue.window(mv.all_history()).per(mv.grain("day")).mean(),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    assert rows["empty"].tolist() == [0]
    assert rows["history"].tolist() == [40]
    with pytest.raises(CoreRuleError):
        session.observe(
            {"v": revenue.window(mv.trailing_periods(count=1)).per(mv.grain("day")).mean()},
            where=scope,
        ).compute()


def test_sample_windows_keep_other_outputs_fixed_frame_parameters(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    revenue = amount.sum(time=point)
    captured = session.observe({"v": revenue}, by=(region,)).compute()
    selected = session.population_from(captured, on=((captured.axes[0], region),))
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    actual = (
        session.observe(
            {
                "window": revenue.window(mv.all_history()).per(mv.grain("day")).mean(),
                "selected": revenue.where(mv.in_population(selected)),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    assert actual["window"].tolist() == [240]
    assert actual["selected"].tolist() == [560]


def test_window_samples_retain_all_null_and_zero_days(
    calendar_session: Session, tmp_path: Path
) -> None:
    import duckdb

    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts VALUES (1, '2026-01-02', '2026-01-02', 20, 'east'), (2, '2026-01-03', '2026-01-03', NULL, 'east'), (3, '2026-01-05', '2026-01-05', 0, 'east')"
        )
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    revenue = amount.sum(time=point)
    scope = session.time_scope(start="2026-01-03", end="2026-01-06")
    rule = mv.trailing_duration(duration=timedelta(days=3))
    actual = (
        session.observe(
            {
                "inner": revenue.window(rule).per(mv.grain("day")).mean(),
                "outer": revenue.per(mv.grain("day")).mean().window(rule),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    assert actual["inner"].tolist() == [10]
    assert actual["outer"].tolist() == [0]


@pytest.mark.parametrize("captured_population", [False, True])
def test_calendar_window_samples_respect_enclosing_population_selection(
    calendar_session: Session, captured_population: bool
) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    selected = mv.coordinates(by=(region,), keys=(("west",),))
    if captured_population:
        captured = session.frame(selected).compute()
        selected = session.population_from(captured, on=((captured.axes[0], region),))
    metric = (
        amount.sum(time=point)
        .window(mv.since(start=session.instant("2026-10-09")))
        .per(mv.grain("day"))
        .mean()
        .per(selected)
        .mean()
    )
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    actual = session.observe({"v": metric}, where=scope).compute().to_pandas()
    assert actual["v"].isna().all()


def test_sample_windows_keep_each_outputs_sample_key_types(calendar_session: Session) -> None:
    session = calendar_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    facts = catalog_value(session.catalog, "sales.facts", kind="entity")
    daily = amount.sum(time=point).window(mv.all_history()).per(mv.grain("day")).mean()
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    actual = (
        session.observe(
            {
                "region_mean": daily.per(region).mean(),
                "entity_total": daily.per(session.members(facts)).sum(),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    assert actual["region_mean"].tolist() == [240]
    assert actual["entity_total"].tolist() == [560]
