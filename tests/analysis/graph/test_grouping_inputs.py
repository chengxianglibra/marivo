"""Public grouping capture preserves source bindings and complete-domain semantics."""

from dataclasses import FrozenInstanceError, replace

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.relations import LiteralPopulation, population_keys
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import SemanticError
from tests.analysis.graph.execution_fixtures import source_graph
from tests.shared_fixtures import DslCaseFactory


def test_identity_grouping_and_domain_have_distinct_completion(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = analysis_dsl_case_factory("j1").session
    entity = session.catalog.entities.get("sales.customer")
    metric = session.catalog.metrics.get("sales.revenue")
    region = session.catalog.fields.get("sales.customer.region")
    period = session.time_scope(start="2026-08-01", end="2026-09-01")
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    before = session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Grouping construction must not access source rows")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        grouping = mv.identity(entity)
        assert grouping.kind == members.axes[0].kind == "identity"
        assert isinstance(grouping, mv.Grouping)
        grouping.show()
        assert "sales.customer" in capsys.readouterr().out
        assert len(repr(grouping)) < 200 and "\n" not in repr(grouping)
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            grouping.kind = "field"  # type: ignore[misc]  # Runtime immutability probe.
        for by in ((grouping, region), (region, grouping)):
            reuse = tuple(members.axes[0] if item is grouping else item for item in by)
            left = session.observe({"value": metric}, by=(period, *by))
            right = session.observe({"value": metric}, by=(period, *reuse))
            assert source_graph(left).signature == source_graph(right).signature
    assert session.runs().items == before
    observed = session.observe({"value": metric}, by=(period, grouping))
    complete = session.observe({"value": metric}, by=mv.product(period, members))
    inferred = session.observe(
        {"value": metric}, by=(period, grouping), where=mv.in_population(members)
    )
    assert observed.compute().to_pandas()[("value", "value")].tolist() == [450, 150, 400]
    assert complete.compute().to_pandas()[("value", "value")].tolist() == [450, 150, 400, 0]
    pd.testing.assert_frame_equal(inferred.compute().to_pandas(), observed.compute().to_pandas())


def test_calendar_grouping_is_the_unique_output_time_scope(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    metric = session.catalog.metrics.get("sales.revenue")
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    assert isinstance(calendar, mv.Grouping) and calendar.kind == "calendar"
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    captured_calendar = mv.product(members, calendar).axes[1]
    direct = session.observe({"value": metric}, by=(calendar,))
    reused = session.observe({"value": metric}, by=(captured_calendar,))
    assert source_graph(direct).signature == source_graph(reused).signature
    assert direct.compute().to_pandas()[("value", "value")].tolist() == [1000, 99]


def test_shared_consumers_keep_their_grouping_boundaries(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    entity = session.catalog.entities.get("sales.customer")
    region = session.catalog.fields.get("sales.customer.region")
    metric = session.catalog.metrics.get("sales.revenue")
    identity = mv.identity(entity)
    fields = mv.coordinates(by=(region,), keys=(("East",),))
    field = fields.axes[0]
    assert field.kind == "field"
    assert (
        consume_metric(metric.per(field).mean()).node
        == consume_metric(metric.per(region).mean()).node
    )
    people = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    assert people.project(by=(identity,)).axes == people.axes
    assert people.classify(by=(identity, region)).axes[0].kind == "identity"
    frame = session.values(
        by=(identity,), columns={"value": None}, rows=(((("A",),), {"value": 1}),)
    )
    assert frame.axes[0].role == "identity"
    assert mv.coordinates(by=(identity,), keys=()).axes == people.axes
    with pytest.raises(SemanticError):
        consume_metric(metric.per(identity).mean())
    with pytest.raises(AnalysisError):
        session.observe({"value": metric}, by=(people._definition.axes[0],))  # type: ignore[arg-type]  # Internal records are not public grouping inputs.
    with pytest.raises(AnalysisError):
        mv.identity(())
    with pytest.raises(AnalysisError):
        mv.identity(ms.ref.entity("sales.customer"))  # type: ignore[arg-type]  # Unresolved lookup keys are not grouping definitions.


def test_population_predicates_and_classify_have_explicit_boundaries(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = analysis_dsl_case_factory("j1").session
    region = session.catalog.fields.get("sales.customer.region")
    entity = session.catalog.entities.get("sales.customer")
    people = mv.coordinates(by=(mv.identity(entity),), keys=((("missing",),),))
    with pytest.raises(AnalysisError) as caught:
        people.where(region == "east")  # Population selection permits membership leaves only.
    assert caught.value.expected and caught.value.received and caught.value.repair
    with pytest.raises(AnalysisError):
        bool(people)
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    interval = mv.product(people, scope).axes[1]
    with pytest.raises(AnalysisError):
        people.classify(by=(interval,))
    unchanged = people.classify(by=people.axes)
    assert session.frame(unchanged).compute().to_pandas().iloc[:, 0].tolist() == ["missing"]
    left = mv.in_population(mv.coordinates(by=(region,), keys=(("east",),)))
    right = mv.in_population(mv.coordinates(by=(region,), keys=(("west",),)))
    domain = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    assert domain.where(left & right).show() is None
    conjunction = capsys.readouterr().out
    assert domain.where(left | right).show() is None
    disjunction = capsys.readouterr().out
    assert conjunction != disjunction
    assert " & " in conjunction and " | " in disjunction
    assert all(value in conjunction and value in disjunction for value in ("east", "west"))
    assert "in_population" in conjunction and "in_population" in disjunction


def test_population_algebra_matches_business_coordinates_across_definition_versions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    region = session.catalog.fields.get("sales.customer.region")
    current = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    definition = current._definition
    assert isinstance(definition, LiteralPopulation)
    axis = definition.axes[0]
    assert axis.field is not None
    older = mv.Population(
        replace(
            definition,
            axes=(
                replace(
                    axis,
                    entity=replace(axis.entity, version="earlier-entity"),
                    field=replace(axis.field, version="earlier-field"),
                ),
            ),
            keys=(("east",),),
        )
    )
    assert population_keys(older.union(current)._definition) == (("east",), ("west",))
    assert population_keys(older.project(by=current.axes)._definition) == (("east",),)
    assert population_keys(current.where(mv.in_population(older))._definition) == (("east",),)
