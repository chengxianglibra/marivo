"""Observation binding and execution preserve explicit member and calendar domains."""

from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from typing import Literal

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize("formula", (False, True))
def test_decimal_observe_ignores_caller_context(
    analysis_dsl_case_factory: DslCaseFactory, formula: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")
    region = session.catalog.fields.get("sales.customer.region")
    samples = mv.coordinates(
        by=(region,), keys=(("east",), ("south",), ("west",), ("absent",), ("other",))
    )
    metric = revenue.per(samples).mean()
    if formula:
        # An explicit output domain keeps Decimal multiplication in the local recipe.
        metric = revenue * Decimal("0.123")
    query = session.observe({"v": metric}, by=mv.coordinates(by=(), keys=((),)))
    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        before = repr(context)
        rows = query.compute().to_pandas()
        assert rows[("value", "v")].tolist() == [Decimal("145") if formula else Decimal("235")]
        assert repr(context) == before


@pytest.mark.parametrize("domain", ("source", "literal", "empty"))
@pytest.mark.parametrize("grouped", (False, True))
def test_member_count_without_source_metric_prepares_members(
    analysis_dsl_case_factory: DslCaseFactory,
    domain: Literal["source", "literal", "empty"],
    grouped: bool,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    customer = session.catalog.entities.get("sales.customer")
    region = session.catalog.fields.get("sales.customer.region")
    members = (
        session.members(session.catalog.entities.get("sales.customer"))
        if domain == "source"
        else mv.coordinates(
            by=(mv.identity(customer),), keys=((("A",),), (("C",),)) if domain == "literal" else ()
        )
    )
    if grouped:
        members = members.classify(by=(members.axes[0], region))
    frame = (
        session.observe({"n": members.count()}, by=(region,) if grouped else ())
        .compute()
        .to_pandas()
    )
    if grouped:
        assert dict(zip(frame.iloc[:, 0], frame[("value", "n")], strict=True)) == (
            {"east": 2, "south": 1, "west": 1}
            if domain == "source"
            else {"east": 1, "south": 1}
            if domain == "literal"
            else {}
        )
    else:
        assert frame[("value", "n")].tolist() == [
            4 if domain == "source" else 2 if domain == "literal" else 0
        ]


def test_member_count_literal_absent_identity_needs_no_profile_confirmation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.customer")),),
        keys=((("A",),), (("missing",),)),
    )
    frame = session.observe({"n": members.count()}).compute().to_pandas()
    assert frame[("value", "n")].tolist() == [2]


@pytest.mark.parametrize("population_condition", (False, True))
@pytest.mark.parametrize("mixed", (False, True))
def test_member_count_rejects_multivalued_common_condition(
    analysis_dsl_case_factory: DslCaseFactory, population_condition: bool, mixed: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = session.members(session.catalog.entities.get("sales.customer"))
    channel = session.catalog.fields.get("sales.order.channel")
    condition = (
        mv.in_population(mv.coordinates(by=(channel,), keys=(("web",),)))
        if population_condition
        else channel == "web"
    )
    metrics = {"n": members.count()}
    if mixed:
        metrics["revenue"] = session.catalog.metrics.get("sales.revenue")
    if mixed:
        frame = session.observe(metrics, where=condition).compute().to_pandas()
        assert frame[("value", "n")].tolist() == [4]
        assert frame[("value", "revenue")].tolist() == [927]
    else:
        with pytest.raises(AnalysisError, match="condition"):
            session.observe(metrics, where=condition)


@pytest.mark.parametrize("population_condition", (False, True))
def test_member_count_condition_checks_do_not_reselect_explicit_members(
    analysis_dsl_case_factory: DslCaseFactory, population_condition: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = session.members(session.catalog.entities.get("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    condition = (
        mv.in_population(mv.coordinates(by=(region,), keys=(("east",),)))
        if population_condition
        else region == "east"
    )
    frame = (
        session.observe(
            {"n": members.count(), "revenue": session.catalog.metrics.get("sales.revenue")},
            where=condition,
        )
        .compute()
        .to_pandas()
    )
    assert frame[("value", "n")].tolist() == [4]
    assert frame[("value", "revenue")].tolist() == [776]


def test_common_population_condition_preserves_formula_and_query_dependency(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")
    count = session.catalog.metrics.get("sales.order_count")
    channel = session.catalog.fields.get("sales.order.channel")
    producer = session.observe({"revenue": revenue}, by=(channel,))
    chosen = producer.where(producer["revenue"] > 500)
    selected = session.population_from(chosen, on=((chosen.axes[0], channel),))
    frame = (
        session.observe(
            {
                "n": session.members(session.catalog.entities.get("sales.order")).count(),
                "average": revenue / count,
            },
            where=mv.in_population(selected)
            & mv.in_population(session.members(session.catalog.entities.get("sales.order"))),
        )
        .compute()
        .to_pandas()
    )
    assert frame[("value", "n")].tolist() == [5]
    assert frame[("value", "average")].tolist() == [309.0]


def test_common_population_condition_rejects_foreign_query_dependency(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    other = mv.session.create("foreign-observation", timezone="UTC")
    selected = other.members(other.catalog.entities.get("sales.customer"))
    with pytest.raises(AnalysisError, match="another Store or Session") as error:
        session.observe(
            {"n": session.members(session.catalog.entities.get("sales.customer")).count()},
            where=mv.in_population(selected),
        )
    assert error.value.expected and error.value.received and error.value.repair
    assert "owning Session" in str(error.value)


@pytest.mark.parametrize("same_calendar", (False, True))
def test_observe_complete_domain_uses_its_own_calendar(
    analysis_dsl_case_factory: DslCaseFactory, same_calendar: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    august = session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("month"))
    july = session.time_scope(start="2026-07-01", end="2026-08-01").by(mv.grain("month"))
    channel = session.catalog.fields.get("sales.order.channel")
    requested = mv.product(
        august if same_calendar else july,
        mv.coordinates(by=(channel,), keys=(("web",), ("mobile",))),
    )
    result = session.observe(
        {"revenue": session.catalog.metrics.get("sales.revenue")}, by=requested
    )
    frame = result.compute().to_pandas()
    values = frame[("value", "revenue")]
    if same_calendar:
        assert dict(zip(frame.iloc[:, 1], values, strict=True)) == {"mobile": 150, "web": 850}
    else:
        assert dict(zip(frame.iloc[:, 1], values, strict=True)) == {"mobile": 0, "web": 77}


@pytest.mark.parametrize("conflict", ("period", "grain"))
def test_observe_rejects_conflicting_extra_calendar_even_when_one_matches(
    analysis_dsl_case_factory: DslCaseFactory, conflict: Literal["period", "grain"]
) -> None:
    session = analysis_dsl_case_factory("j1").session
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    august = scope.by(mv.grain("month"))
    other = (
        session.time_scope(start="2026-07-01", end="2026-08-01").by(mv.grain("month"))
        if conflict == "period"
        else scope.by(mv.grain("day"))
    )
    with pytest.raises(AnalysisError, match="at most one output time axis"):
        mv.product(august, other)


@pytest.mark.parametrize("grain", ("day", "month"))
def test_observe_accepts_exact_complete_calendar_binding(
    analysis_dsl_case_factory: DslCaseFactory, grain: Literal["day", "month"]
) -> None:
    session = analysis_dsl_case_factory("j1").session
    calendar = session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain(grain))
    channel = session.catalog.fields.get("sales.order.channel")
    frame = (
        session.observe(
            {"revenue": session.catalog.metrics.get("sales.revenue")},
            by=mv.product(calendar, mv.coordinates(by=(channel,), keys=(("web",), ("mobile",)))),
        )
        .compute()
        .to_pandas()
    )
    assert len(frame) == (62 if grain == "day" else 2)
    assert frame[("value", "revenue")].sum() == 1000
