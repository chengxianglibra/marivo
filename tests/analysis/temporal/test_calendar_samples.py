"""Calendar samples complete child Metrics over a finite, clipped calendar grid."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._authoring.model import AuthoringCapability
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic._capabilities.registry import REGISTRY
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._expression_values import FieldExpression, MetricExpression, RowExpression
from marivo.semantic.catalog import Entity, Metric
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value
from tests.support.documentation import _example as documentation_example


def _field(session: Session, path: str) -> FieldExpression:
    value = catalog_value(session.catalog, path, kind="field")
    assert isinstance(value, RowExpression)
    return value


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
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
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


def test_weekly_calendar_samples_include_empty_days_and_finish_daily_p95(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = _field(session, "sales.facts.amount")
    point = _field(session, "sales.facts.point")
    region = _field(session, "sales.facts.region")
    facts_entry = session.catalog.get("entity:sales.facts")
    assert isinstance(facts_entry, Entity)
    facts = facts_entry
    revenue = amount.sum(time=point)
    daily_p95 = revenue.per(point).quantile(0.95)
    weeks = session.time_scope(start="2026-10-05", end="2026-10-12").by(mv.grain("week"))
    rows = (
        session.observe(
            {
                "mean": revenue.per(mv.grain("day")).mean(),
                "p95_median": daily_p95.per(mv.grain("day")).median(),
                "ratio_mean": (revenue / facts.count(time=point)).per(mv.grain("day")).mean(),
                "weighted": revenue.per(mv.grain("day")).weighted_mean(
                    weight=facts.count(time=point)
                ),
                "first": revenue.per(mv.grain("day")).first(),
                "last": revenue.per(mv.grain("day")).last(),
                "nested": revenue.per(mv.grain("hour")).max().per(mv.grain("day")).mean(),
            },
            by=(weeks, region),
        )
        .compute()
        .to_pandas()
    )
    # Daily sums [40, 60, 0, 160, 0, 0, 300]; daily P95 [29, 39, NA, 89, NA, NA, 159].
    assert rows["mean"].tolist() == [80]
    assert rows["p95_median"].tolist() == [64]
    assert rows["ratio_mean"].tolist() == [70]
    assert rows["weighted"].tolist() == [140]
    assert rows["first"].tolist() == [40]
    assert rows["last"].tolist() == [300]
    assert rows["nested"].tolist() == pytest.approx([320 / 7])
    with pytest.raises(MaterializationError, match="non-NA internal samples"):
        session.observe(
            {"v": (revenue / facts.count(time=point)).per(mv.grain("day")).mean(nulls="error")},
            by=(weeks,),
        ).compute()


def test_calendar_samples_clip_partial_month_edges_and_plain_scope(
    calendar_session: Session,
) -> None:
    session = calendar_session
    amount = _field(session, "sales.facts.amount")
    point = _field(session, "sales.facts.point")
    metric = amount.sum(time=point).per(mv.grain("day")).mean()
    scope = session.time_scope(start="2026-10-05T06:00:00", end="2026-11-02T06:00:00")
    monthly = (
        session.observe({"v": metric}, by=(scope.by(mv.grain("month")),)).compute().to_pandas()
    )
    assert monthly["v"].tolist() == pytest.approx([550 / 27, 125])
    whole = session.observe({"v": metric}, by=(scope,)).compute().to_pandas()
    assert whole["v"].tolist() == pytest.approx([800 / 29])
    empty = session.time_scope(start="2026-10-12", end="2026-10-19").by(mv.grain("week"))
    assert session.observe({"v": metric}, by=(empty,)).compute().to_pandas()["v"].tolist() == [0]


def test_calendar_samples_bucket_each_component_by_its_own_clock(calendar_session: Session) -> None:
    session = calendar_session
    amount = _field(session, "sales.facts.amount")
    point = _field(session, "sales.facts.point")
    other = _field(session, "sales.facts.other")
    metric = (amount.sum(time=point) + amount.sum(time=other)).per(mv.grain("day")).max()
    scope = session.time_scope(start="2026-10-05", end="2026-10-12").by(mv.grain("week"))
    assert session.observe({"v": metric}, by=(scope,)).compute().to_pandas()["v"].tolist() == [300]


def test_calendar_sample_binding_errors_are_structured(calendar_session: Session) -> None:
    session = calendar_session
    amount = _field(session, "sales.facts.amount")
    point = _field(session, "sales.facts.point")
    metric = amount.sum(time=point)
    month = session.time_scope(start="2026-10-05", end="2026-11-02").by(mv.grain("month"))
    for value, by, expected, received in (
        (metric.per(mv.grain("day")).mean(), (), "finite", "None"),
        (metric.per(mv.grain("week")).mean(), (month,), "nest", "week"),
        (amount.sum().per(mv.grain("day")).mean(), (month,), "time role", "SourceAggregate"),
    ):
        with pytest.raises(CoreRuleError) as captured:
            session.observe({"v": value}, by=by)
        error = captured.value
        assert error.kind == "CoreRule"
        assert expected in (error.expected or "")
        assert received in (error.received or "")
        assert error.repair is not None and error.repair.action
    with pytest.raises(AttributeError):
        absent = metric.sum
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
    amount = _field(session, "sales.facts.amount")
    sampled = _field(session, "sales.facts.sampled")
    p95 = amount.sum(time=sampled).per(sampled).quantile(0.95).per(mv.grain("day")).mean()
    scope = session.time_scope(start="2026-10-05", end="2026-10-07")
    assert session.observe({"v": p95}, by=(scope,)).compute().to_pandas()["v"].tolist() == [34]
    missing = session.time_scope(start="2026-10-05", end="2026-10-08")
    assert session.observe({"v": p95}, by=(missing,)).compute().to_pandas()["v"].tolist() == [34]


def test_published_calendar_metric_can_be_nested_under_another_calendar_layer(
    calendar_session: Session,
) -> None:
    session = calendar_session
    daily_entry = session.catalog.get("metric:sales.daily_average")
    assert isinstance(daily_entry, Metric)
    daily = daily_entry
    scope = session.time_scope(start="2026-10-05", end="2026-11-02")
    rows = (
        session.observe({"v": daily.per(mv.grain("week")).max()}, by=(scope,)).compute().to_pandas()
    )
    assert rows["v"].tolist() == [80]


def test_calendar_sample_help_and_bilingual_examples_execute(calendar_session: Session) -> None:
    session = calendar_session
    amount = _field(session, "sales.facts.amount")
    point = _field(session, "sales.facts.point")
    region = _field(session, "sales.facts.region")
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
        assert folded.to_pandas()["daily_average"].tolist() == [80]


def test_calendar_sample_grid_preserves_dst_hour_count(calendar_session: Session) -> None:
    session = mv.session.get_or_create("calendar-new-york", timezone="America/New_York")
    amount = _field(session, "sales.facts.amount")
    point = _field(session, "sales.facts.point")
    day = session.time_scope(start="2026-11-01", end="2026-11-02").by(mv.grain("day"))
    rows = (
        session.observe({"v": amount.sum(time=point).per(mv.grain("hour")).mean()}, by=(day,))
        .compute()
        .to_pandas()
    )
    assert rows["v"].tolist() == [
        2
    ]  # UTC source clock: 50 over 25 New York hours, including empty hours.


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("placement", ["inside", "wrapping"])
@pytest.mark.parametrize("reset", [False, True])
def test_calendar_sample_windows_reject_instead_of_silently_ignoring_rules(
    calendar_session: Session, empty: bool, placement: str, reset: bool
) -> None:
    session = calendar_session
    revenue = _field(session, "sales.facts.amount").sum(time=_field(session, "sales.facts.point"))
    scope = session.time_scope(
        start="2026-10-12" if empty else "2026-10-05",
        end="2026-10-19" if empty else "2026-10-12",
    ).by(mv.grain("week"))
    rule = mv.to_date(grain=mv.grain("week")) if reset else mv.all_history()
    metric = (
        revenue.window(rule).per(mv.grain("day")).sum()
        if placement == "inside"
        else revenue.per(mv.grain("day")).sum().window(rule)
    )
    # Inner all-history values would be [40, 100, 100, 260, 260, 260, 560]
    # or seven copies of 560 in the empty week. Returning 560 or 0 ignores the rule.
    with pytest.raises(CoreRuleError) as captured:
        session.observe({"v": metric}, by=(scope,))
    error = captured.value
    assert "window" in (error.expected or "").lower()
    assert "calendar" in (error.received or "").lower()
    assert error.repair
    assert "CalendarAxis" in error.repair.action
    if placement == "wrapping":
        assert "cannot be windowed" in error.repair.action


def test_calendar_window_rejection_reaches_weighted_nested_and_declared_formulas(
    calendar_session: Session,
) -> None:
    session = calendar_session
    revenue = _field(session, "sales.facts.amount").sum(time=_field(session, "sales.facts.point"))
    daily = catalog_value(session.catalog, "sales.daily_average", kind="metric")
    assert isinstance(daily, MetricExpression)
    metrics = (
        revenue.per(mv.grain("day")).weighted_mean(weight=revenue.window(mv.all_history())),
        revenue.window(mv.all_history()).per(mv.grain("hour")).max().per(mv.grain("day")).mean(),
        daily.window(mv.all_history()),
    )
    scope = session.time_scope(start="2026-10-05", end="2026-10-12")
    for metric in metrics:
        with pytest.raises(CoreRuleError) as captured:
            session.observe({"v": metric}, by=(scope,))
        assert "window" in (captured.value.expected or "").lower()
        assert captured.value.repair


def test_calendar_folds_and_windows_on_independent_branches_keep_their_values(
    calendar_session: Session,
) -> None:
    session = calendar_session
    point = _field(session, "sales.facts.point")
    revenue = _field(session, "sales.facts.amount").sum(time=point)
    weeks = session.time_scope(start="2026-10-12", end="2026-10-19").by(mv.grain("week"))
    data = (
        session.observe(
            {
                "v": revenue.per(mv.grain("day")).sum()
                + revenue.window(mv.all_history()).per(point).sum()
            },
            by=(weeks,),
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
    assert ".per(Grain('day'))" in text
    assert "_BuiltinGrain" not in text
