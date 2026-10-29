"""Observation binding and execution preserve explicit member and calendar domains."""

from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from typing import Literal

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory, catalog_value


@pytest.mark.parametrize("formula", (False, True))
def test_decimal_observe_uses_actual_source_or_local_context(
    analysis_dsl_case_factory: DslCaseFactory, formula: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.get("metric:sales.revenue").value
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
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
        if formula:
            with pytest.raises(AnalysisError, match="valid demanded numeric values"):
                query.compute()
        else:
            rows = query.compute().to_pandas()
            assert rows["v"].tolist() == [235.2]
        assert context.prec == 1
        assert context.rounding == ROUND_DOWN
        assert context.traps[Inexact] and context.traps[Rounded]


@pytest.mark.parametrize("domain", ("source", "literal", "empty"))
@pytest.mark.parametrize("grouped", (False, True))
def test_member_count_without_source_metric_prepares_members(
    analysis_dsl_case_factory: DslCaseFactory,
    domain: Literal["source", "literal", "empty"],
    grouped: bool,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    customer = session.catalog.get("entity:sales.customer").value
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    members = (
        session.members(session.catalog.get("entity:sales.customer").value)
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
        assert dict(zip(frame.iloc[:, 0], frame["n"], strict=True)) == (
            {"east": 2, "south": 1, "west": 1}
            if domain == "source"
            else {"east": 1, "south": 1}
            if domain == "literal"
            else {}
        )
    else:
        assert frame["n"].tolist() == [4 if domain == "source" else 2 if domain == "literal" else 0]


def test_member_count_literal_absent_identity_needs_no_profile_confirmation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = mv.coordinates(
        by=(mv.identity(session.catalog.get("entity:sales.customer").value),),
        keys=((("A",),), (("missing",),)),
    )
    frame = session.observe({"n": members.count()}).compute().to_pandas()
    assert frame["n"].tolist() == [2]


@pytest.mark.parametrize("population_condition", (False, True))
@pytest.mark.parametrize("mixed", (False, True))
def test_member_count_rejects_multivalued_common_condition(
    analysis_dsl_case_factory: DslCaseFactory, population_condition: bool, mixed: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = session.members(session.catalog.get("entity:sales.customer").value)
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    condition = (
        mv.in_population(mv.coordinates(by=(channel,), keys=(("web",),)))
        if population_condition
        else channel == "web"
    )
    metrics = {"n": members.count()}
    if mixed:
        metrics["revenue"] = session.catalog.get("metric:sales.revenue").value
    if mixed:
        frame = session.observe(metrics, where=condition).compute().to_pandas()
        assert frame["n"].tolist() == [4]
        assert frame["revenue"].tolist() == [927]
    else:
        with pytest.raises(AnalysisError, match="condition"):
            session.observe(metrics, where=condition)


@pytest.mark.parametrize("population_condition", (False, True))
def test_member_count_condition_checks_do_not_reselect_explicit_members(
    analysis_dsl_case_factory: DslCaseFactory, population_condition: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = session.members(session.catalog.get("entity:sales.customer").value)
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    condition = (
        mv.in_population(mv.coordinates(by=(region,), keys=(("east",),)))
        if population_condition
        else region == "east"
    )
    frame = (
        session.observe(
            {"n": members.count(), "revenue": session.catalog.get("metric:sales.revenue").value},
            where=condition,
        )
        .compute()
        .to_pandas()
    )
    assert frame["n"].tolist() == [4]
    assert frame["revenue"].tolist() == [776]


def test_common_population_condition_preserves_formula_and_query_dependency(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.get("metric:sales.revenue").value
    count = session.catalog.get("metric:sales.order_count").value
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    producer = session.observe({"revenue": revenue}, by=(channel,))
    chosen = producer.where(producer["revenue"] > 500)
    selected = session.population_from(chosen, on=((chosen.axes[0], channel),))
    frame = (
        session.observe(
            {
                "n": session.members(session.catalog.get("entity:sales.order").value).count(),
                "average": revenue / count,
            },
            where=mv.in_population(selected)
            & mv.in_population(session.members(session.catalog.get("entity:sales.order").value)),
        )
        .compute()
        .to_pandas()
    )
    assert frame["n"].tolist() == [5]
    assert frame["average"].tolist() == [309.0]


def test_common_population_condition_rejects_foreign_query_dependency(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    other = mv.session.get_or_create("foreign-observation", timezone="UTC")
    selected = other.members(other.catalog.get("entity:sales.customer").value)
    with pytest.raises(AnalysisError, match="another Store or Session") as error:
        session.observe(
            {"n": session.members(session.catalog.get("entity:sales.customer").value).count()},
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
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    requested = mv.product(
        august if same_calendar else july,
        mv.coordinates(by=(channel,), keys=(("web",), ("mobile",))),
    )
    result = session.observe(
        {"revenue": session.catalog.get("metric:sales.revenue").value}, by=requested
    )
    frame = result.compute().to_pandas()
    values = frame["revenue"]
    if same_calendar:
        assert dict(
            zip(frame[frame.attrs["axes"][1]["name"].rsplit(".", 1)[-1]], values, strict=True)
        ) == {
            "mobile": 150,
            "web": 850,
        }
    else:
        assert dict(
            zip(frame[frame.attrs["axes"][1]["name"].rsplit(".", 1)[-1]], values, strict=True)
        ) == {
            "mobile": 0,
            "web": 77,
        }


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
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    frame = (
        session.observe(
            {"revenue": session.catalog.get("metric:sales.revenue").value},
            by=mv.product(calendar, mv.coordinates(by=(channel,), keys=(("web",), ("mobile",)))),
        )
        .compute()
        .to_pandas()
    )
    assert len(frame) == (62 if grain == "day" else 2)
    assert frame["revenue"].sum() == 1000
