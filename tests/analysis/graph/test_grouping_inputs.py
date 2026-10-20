"""Public grouping capture preserves source bindings and complete-domain semantics."""

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
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
    members = session.members(ms.ref.entity("sales.customer"))
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
            grouping.kind = "field"
        for by in ((grouping, region), (region, grouping)):
            reuse = tuple(members.axes[0] if item is grouping else item for item in by)
            left = session.observe({"value": metric}, by=by, during=period)
            right = session.observe({"value": metric}, by=reuse, during=period)
            assert source_graph(left).signature == source_graph(right).signature
    assert session.runs().items == before
    observed = session.observe({"value": metric}, by=(grouping,), during=period)
    complete = session.observe({"value": metric}, by=members, during=period)
    inferred = session.observe({"value": metric}, by=(grouping,), members=members, during=period)
    assert observed.compute().to_pandas()[("value", "value")].tolist() == [450, 150, 400]
    assert complete.compute().to_pandas()[("value", "value")].tolist() == [450, 150, 400, 0]
    pd.testing.assert_frame_equal(inferred.compute().to_pandas(), complete.compute().to_pandas())


def test_calendar_grouping_keeps_independent_observation_scope(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    metric = session.catalog.metrics.get("sales.revenue")
    period = session.time_scope(start="2026-08-01", end="2026-09-01")
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    assert isinstance(calendar, mv.Grouping) and calendar.kind == "calendar"
    members = session.members(ms.ref.entity("sales.customer"))
    captured_calendar = mv.product(members, calendar).axes[1]
    direct = session.observe({"value": metric}, during=period, by=(calendar,))
    reused = session.observe({"value": metric}, during=period, by=(captured_calendar,))
    assert source_graph(direct).signature == source_graph(reused).signature
    assert direct.compute().to_pandas()[("value", "value")].tolist() == [1000, 0]


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
    people = session.members(ms.ref.entity("sales.customer"))
    assert people.project(by=(identity,)).axes == people.axes
    assert people.classify(by=(identity, region)).axes[0].kind == "identity"
    frame = session.values(
        by=(identity,), columns={"value": None}, rows=(((("A",),), {"value": 1}),)
    )
    assert frame.axes[0].role == "identity"
    with pytest.raises(AnalysisError, match="field or membership"):
        mv.coordinates(by=(identity,), keys=())
    with pytest.raises(SemanticError):
        consume_metric(metric.per(identity).mean())
    with pytest.raises(AnalysisError):
        session.observe({"value": metric}, by=(people._definition.axes[0],))  # type: ignore[arg-type]  # Internal records are not public grouping inputs.
    with pytest.raises(AnalysisError):
        mv.identity(())
    with pytest.raises(AnalysisError):
        mv.identity(ms.ref.entity("sales.customer"))  # type: ignore[arg-type]  # Unresolved lookup keys are not grouping definitions.
