"""Observation binding and execution preserve explicit member and calendar domains."""

from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


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
        session.members(ms.ref.entity("sales.customer"))
        if domain == "source"
        else mv.identities(customer, keys=(("A",), ("C",)) if domain == "literal" else ())
    )
    frame = (
        session.observe({"n": mv.member_count()}, members=members, by=(region,) if grouped else ())
        .execute()
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
    members = mv.identities(
        session.catalog.entities.get("sales.customer"), keys=(("A",), ("missing",))
    )
    frame = session.observe({"n": mv.member_count()}, members=members).execute().to_pandas()
    assert frame[("value", "n")].tolist() == [2]


@pytest.mark.parametrize("population_condition", (False, True))
@pytest.mark.parametrize("mixed", (False, True))
def test_member_count_rejects_multivalued_common_condition(
    analysis_dsl_case_factory: DslCaseFactory, population_condition: bool, mixed: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = session.members(ms.ref.entity("sales.customer"))
    channel = session.catalog.fields.get("sales.order.channel")
    condition = (
        mv.in_population(mv.coordinates(by=(channel,), keys=(("web",),)))
        if population_condition
        else channel == "web"
    )
    metrics = {"n": mv.member_count()}
    if mixed:
        metrics["revenue"] = session.catalog.metrics.get("sales.revenue")
    with pytest.raises(AnalysisError) as captured:
        session.observe(metrics, members=members, where=condition)
    assert "sales.customer" in str(captured.value)
    assert captured.value.repair is not None


@pytest.mark.parametrize("population_condition", (False, True))
def test_member_count_condition_checks_do_not_reselect_explicit_members(
    analysis_dsl_case_factory: DslCaseFactory, population_condition: bool
) -> None:
    session = analysis_dsl_case_factory("j1").session
    members = session.members(ms.ref.entity("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    condition = (
        mv.in_population(mv.coordinates(by=(region,), keys=(("east",),)))
        if population_condition
        else region == "east"
    )
    frame = (
        session.observe(
            {"n": mv.member_count(), "revenue": session.catalog.metrics.get("sales.revenue")},
            members=members,
            where=condition,
        )
        .execute()
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
    selected = producer.where(producer["revenue"] > 500).population()
    frame = (
        session.observe(
            {"n": mv.member_count(), "average": revenue / count},
            members=session.members(ms.ref.entity("sales.order")),
            where=mv.in_population(selected),
        )
        .execute()
        .to_pandas()
    )
    assert frame[("value", "n")].tolist() == [5]
    assert frame[("value", "average")].tolist() == [309.0]


def test_common_population_condition_rejects_foreign_query_dependency(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    other = mv.session.get_or_create("foreign-observation", report_timezone="UTC")
    selected = other.members(ms.ref.entity("sales.customer"))
    with pytest.raises(AnalysisError, match="Session owner"):
        session.observe(
            {"n": mv.member_count()},
            members=session.members(ms.ref.entity("sales.customer")),
            where=mv.in_population(selected),
        )


@pytest.mark.parametrize("same_calendar", (False, True))
def test_observe_complete_domain_requires_compatible_during_calendar(
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
    if not same_calendar:
        with pytest.raises(AnalysisError) as captured:
            session.observe(
                {"revenue": session.catalog.metrics.get("sales.revenue")},
                during=august,
                by=requested,
            )
        assert captured.value.location == "session.observe"
        assert captured.value.repair is not None
    else:
        result = session.observe(
            {"revenue": session.catalog.metrics.get("sales.revenue")},
            during=august,
            by=requested,
        )
        frame = result.execute().to_pandas()
        assert dict(zip(frame.iloc[:, 1], frame[("value", "revenue")], strict=True)) == {
            "mobile": 150,
            "web": 850,
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
    requested = mv.product(august, other)
    # Pure coordinate products remain independent of observation time binding.
    assert len(session.query(requested).output_names) == 0
    with pytest.raises(AnalysisError) as captured:
        session.observe(
            {"revenue": session.catalog.metrics.get("sales.revenue")},
            during=august,
            by=requested,
        )
    assert captured.value.location == "session.observe"
    assert captured.value.repair is not None


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
            during=calendar,
            by=mv.product(calendar, mv.coordinates(by=(channel,), keys=(("web",), ("mobile",)))),
        )
        .execute()
        .to_pandas()
    )
    assert len(frame) == (62 if grain == "day" else 2)
    assert frame[("value", "revenue")].sum() == 1000
