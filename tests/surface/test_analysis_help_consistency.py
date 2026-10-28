"""Analysis Help agrees with real symbolic, numeric and temporal boundaries."""

from collections.abc import Callable
from datetime import timedelta
from typing import Literal

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import CallableInput
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.core.data_types import DurationDataType
from marivo.analysis.errors import AnalysisError
from marivo.semantic.catalog import PeriodCalendarEntry, SemanticCatalog
from marivo.semantic.errors import SemanticError
from marivo.semantic.expressions import Entity
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import (
    DslCaseFactory,
    catalog_entry,
    catalog_value,
    fiscal_analysis_project_files,
    publish_fiscal_calendar_artifact,
)


@pytest.mark.parametrize(
    ("target", "output"),
    (
        ("identity", "Grouping | Deferred"),
        ("coordinates", "Population | Deferred"),
        ("membership", "Grouping | Deferred"),
        ("product", "Population | Deferred"),
        *(
            ("population." + method, "Population | Deferred")
            for method in (
                "project",
                "classify",
                "union",
                "intersection",
                "difference",
                "where",
                "image",
                "preimage",
            )
        ),
    ),
)
def test_symbolic_return_help_matches_callable_annotation(target: str, output: str) -> None:
    descriptor = REGISTRY.by_canonical_id(target)
    assert isinstance(descriptor, CallableInput)
    text = render_help_text("analysis." + target)[0]
    assert "Returns: " + output in text
    assert "loader binding before analysis" in text
    for binding in descriptor.bindings:
        assert str(binding.signature.return_annotation) == output
        assert callable(binding.implementation)
        assert render_help_text(binding.implementation)[0] == text


def test_symbolic_population_branches_remain_deferred(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    customer_entity = catalog_value(case.catalog, "sales.customer", kind="entity")
    order_entity = catalog_value(case.catalog, "sales.order", kind="entity")
    assert isinstance(customer_entity, Entity) and isinstance(order_entity, Entity)
    customers = case.session.members(customer_entity)
    orders = case.session.members(order_entity)
    region = ms.ref.dimension("sales.customer.region")
    customer = ms.ref.entity("sales.customer")
    buyer = ms.ref.relationship("sales.buyer")
    symbolic = mv.coordinates(by=(region,), keys=(("east",),))
    identities = mv.coordinates(by=(mv.identity(customer),), keys=((("A",),),))
    for value in (
        mv.identity(customer),
        symbolic,
        mv.membership(region, via=((buyer,),), unmatched="exclude"),
        mv.product(symbolic, identities),
        customers.project(by=(region,)),
        customers.classify(by=(region,)),
        customers.union(identities),
        customers.intersection(identities),
        customers.difference(identities),
        customers.where(mv.in_population(identities)),
        orders.image(via=(buyer,)),
        orders.preimage(identities, via=(buyer,)),
    ):
        assert isinstance(value, Deferred)


def test_operator_help_preserves_duration_units_and_boolean_null_rules(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j2").session
    frame = session.values(
        by=(),
        columns={"elapsed": "s", "usd": "USD", "eur": "EUR"},
        rows=(((), {"elapsed": timedelta(seconds=3), "usd": 3, "eur": 2}),),
    ).compute()
    frame = frame.select(
        {
            "elapsed": frame["elapsed"],
            "usd": frame["usd"],
            "eur": frame["eur"],
            "unknown": (frame["usd"] / 0) > 0,
            "false": False,
            "true": True,
        }
    ).compute()
    result = frame.select(
        {
            "duration": frame["elapsed"] / 2,
            "product": frame["usd"] * frame["eur"],
            "and": frame["unknown"] & frame["false"],
            "or": frame["unknown"] | frame["true"],
        }
    ).compute()
    rows = result.to_pandas()
    assert rows["duration"].tolist() == [timedelta(seconds=2)]
    dtype = result.schema.values["duration"].dtype
    assert isinstance(dtype, DurationDataType) and dtype.unit == "s"
    assert result.schema.values["product"].unit == "USD*EUR"
    assert rows["product"].tolist() == [6]
    assert rows["and"].tolist() == [False]
    assert rows["or"].tolist() == [True]
    division = render_help_text(mv.Column.__truediv__)[0]
    assert "Duration / numeric retains the original Duration tick unit" in division
    assert "half-even rounding" in division
    assert "Known Column units multiply" in render_help_text(mv.Column.__mul__)[0]
    assert "False & NULL is False" in render_help_text(mv.Column.__and__)[0]
    assert "True | NULL is True" in render_help_text(mv.Column.__or__)[0]
    for name in ("__eq__", "__and__", "__or__", "__invert__"):
        text = render_help_text(getattr(mv.Column, name))[0]
        assert "division" not in text and "overflow" not in text
        assert "SUM" not in text


def test_read_help_requires_one_complete_identity_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    entity = catalog_value(case.catalog, "sales.customer", kind="entity")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    assert isinstance(entity, Entity) and isinstance(region, Deferred)
    members = case.session.members(entity)
    classified = members.classify(by=(*members.axes, region))
    assert isinstance(classified, mv.Population)
    with pytest.raises(AnalysisError, match="one Entity identity axis"):
        case.session.read(classified, {"region": region})
    projected = classified.project(by=(classified.axes[0],))
    assert isinstance(projected, mv.Population)
    assert case.session.read(projected, {"region": region}).output_names == ("region",)
    text = render_help_text(case.session.read)[0]
    assert "exactly one complete Entity identity axis" in text
    assert "Project that identity axis" in text


@pytest.mark.parametrize(
    ("rule_name", "start", "end", "grain", "rejection", "disclosure"),
    (
        (
            "trailing_periods",
            "2026-08-02",
            "2026-09-01",
            "month",
            "complete calendar buckets",
            "partial buckets and subday grains reject",
        ),
        (
            "trailing_periods",
            "2026-08-01",
            "2026-08-02",
            "hour",
            "day/week/month/quarter/year",
            "partial buckets and subday grains reject",
        ),
        (
            "since",
            "2026-08-01",
            "2026-09-01",
            None,
            "window start before each endpoint",
            "start must precede every endpoint",
        ),
        (
            "to_date",
            "2026-08-01",
            "2026-10-01",
            "quarter",
            "each nominal output bucket inside one reset period",
            "Each nominal output bucket must lie inside one reset period",
        ),
    ),
)
def test_window_help_discloses_consuming_restrictions(
    analysis_dsl_case_factory: DslCaseFactory,
    rule_name: str,
    start: str,
    end: str,
    grain: Literal["month", "hour", "quarter"] | None,
    rejection: str,
    disclosure: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    metric = catalog_value(case.catalog, "sales.revenue", kind="metric")
    assert isinstance(metric, Deferred)
    rule = (
        mv.trailing_periods(count=1)
        if rule_name == "trailing_periods"
        else mv.since(start=session.instant(end))
        if rule_name == "since"
        else mv.to_date(grain=mv.grain("month"))
    )
    scope = session.time_scope(start=start, end=end)
    # These deliberately select incompatible output grids, including an hourly one.
    domain = scope if grain is None else scope.by(mv.grain(grain))
    with pytest.raises(AnalysisError, match=rejection):
        session.observe({"value": metric.window(rule)}, by=(domain,))
    text = render_help_text("analysis." + rule_name)[0]
    assert disclosure in text
    assert "marivo.help('analysis.observation_window')" in text
    assert "marivo.help('semantic.metric.window')" in text


@pytest.fixture
def calendar_catalog(
    semantic_project_factory: Callable[..., SemanticProject],
) -> SemanticCatalog:
    return SemanticCatalog(semantic_project_factory(fiscal_analysis_project_files()))


def test_calendar_grain_help_distinguishes_missing_and_current_certification(
    calendar_catalog: SemanticCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = calendar_catalog
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(catalog.workspace_dir))
    session = mv.session.get_or_create("help-calendar", timezone="UTC")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    calendar = catalog_entry(catalog, "sales.fiscal", kind="period_calendar")
    assert isinstance(calendar, PeriodCalendarEntry)
    unbound = calendar.grain("fiscal_week")
    with pytest.raises(ValueError, match="captured semantic Grain"):
        scope.by(unbound)
    publish_fiscal_calendar_artifact(catalog)
    captured = calendar.grain("fiscal_week")
    assert captured != unbound
    assert isinstance(scope.by(captured), mv.CalendarAxis)
    for target in ("analysis.grain", "analysis.catalog.period_calendars.grain"):
        text = render_help_text(target)[0]
        assert "unbound lookup" in text
        assert "certif" in text
    assert "semantic.preview" in render_help_text("analysis.catalog.period_calendars.grain")[0]


def test_calendar_periods_help_discloses_page_bounds_and_cursor_ownership(
    calendar_catalog: SemanticCatalog,
) -> None:
    publish_fiscal_calendar_artifact(calendar_catalog)
    calendar = catalog_entry(calendar_catalog, "sales.fiscal", kind="period_calendar")
    assert isinstance(calendar, PeriodCalendarEntry)
    for invalid_limit in (0, 101, True):
        with pytest.raises(SemanticError, match="integer in"):
            calendar.periods("fiscal_week", limit=invalid_limit)
    first = calendar.periods("fiscal_week", limit=1)
    assert first.next_cursor is not None
    assert calendar.periods("fiscal_week", limit=1, cursor=first.next_cursor).items != first.items
    with pytest.raises(SemanticError, match="different certified snapshot/level"):
        calendar.periods("fiscal_month", limit=1, cursor=first.next_cursor)
    text = render_help_text("analysis.catalog.period_calendars.periods")[0]
    assert "limit=1..100, excluding bool" in text
    assert "same snapshot and level" in text
    assert "next_cursor is None" in text


def test_method_fact_help_keeps_details_with_their_owning_record() -> None:
    value = render_help_text("analysis.frame_method_value")[0]
    support = render_help_text("analysis.frame_method_support")[0]
    metric = render_help_text("analysis.frame_attribute_metric")[0]
    facts = render_help_text("analysis.frame_method_facts")[0]
    assert "observed_total retains" in metric and "observed_total retains" not in value
    assert "targets retains" in facts and "targets retains" not in support
    assert "Counts do not prove source qualification" in support
    assert "Decimal precision and integer time ticks" in value
    assert len(value) < 1500
