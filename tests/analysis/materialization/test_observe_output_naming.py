"""Observation names come from consumed declarations without reading source data."""

from dataclasses import replace

import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.dsl import source_capture
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._deferred_semantics import MetricFacts
from marivo.semantic.errors import SemanticError
from tests.shared_fixtures import DSL_NAMES, DslCaseFactory, terminal_runs


def test_observe_named_metric_forms_preserve_names_order_and_formula_values(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = analysis_dsl_case_factory("j3").session
    line_revenue = session.catalog.metrics.get("sales.line_revenue")
    count = session.catalog.metrics.get("sales.order_count")
    formula = session.catalog.metrics.get("sales.aov_from_lines")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Naming must not read source data")

    with monkeypatch.context() as capture:
        capture.setattr(SourceSession, "execute", forbidden)
        single = session.observe(formula)
        multiple = session.observe((line_revenue, count, formula))
        explicit = session.observe(
            {"line_revenue": line_revenue, "order_count": count, "aov_from_lines": formula}
        )
        reverse = session.observe((formula, count, line_revenue))
        assert single.output_names == ("aov_from_lines",)
        assert multiple.output_names == ("line_revenue", "order_count", "aov_from_lines")
        assert reverse.output_names == ("aov_from_lines", "order_count", "line_revenue")
        assert multiple.presence_names == explicit.presence_names == ()
        assert terminal_runs(session) == ()

    result = multiple.compute().to_pandas()
    pd.testing.assert_frame_equal(result, explicit.compute().to_pandas())
    assert result["line_revenue"].tolist() == [160]
    assert result["order_count"].tolist() == [4]
    assert result["aov_from_lines"].tolist() == [40.0]
    assert single.compute().to_pandas()["aov_from_lines"].tolist() == [40.0]


def test_observe_duplicate_names_reject_with_positions_and_explicit_repair(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    finance = case.root / "models/semantic/finance"
    finance.mkdir()
    (finance / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='finance', owner='Fixture')\n"
    )
    (finance / "models.py").write_text((case.root / "models/semantic/sales/models.py").read_text())
    session = case.session
    sales = session.catalog.metrics.get("sales.revenue")
    other = session.catalog.metrics.get("finance.revenue")
    assert (
        session.observe(sales).output_names == session.observe(other).output_names == ("revenue",)
    )
    for metrics in ((sales, sales), (sales, other)):
        with pytest.raises(AnalysisError) as caught:
            session.observe(metrics)
        error = caught.value
        assert error.expected == "distinct output names in one observation"
        assert error.received and "inputs 1" in error.received and "and 2" in error.received
        assert "sales.revenue" in error.received
        assert error.repair and "Mapping" in error.repair.action
        assert error.repair.help_target.canonical_id == "session.observe"
    repaired = session.observe({"sales_revenue": sales, "finance_revenue": other})
    assert repaired.output_names == ("sales_revenue", "finance_revenue")
    repeated = session.observe({"first": sales, "second": sales}).compute().to_pandas()
    assert repeated["first"].tolist() == [1176]
    assert repeated["second"].tolist() == [1176]


def test_observe_unnamed_recipes_require_mapping_even_with_one_metric_dependency(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")
    channel = session.catalog.fields.get("sales.order.channel")
    amount = session.catalog.fields.get("sales.order.amount")
    members = session.members(session.catalog.entities.get("sales.order"))
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    recipes = (
        (revenue * 2, ()),
        (revenue.where(channel == "web"), ()),
        (revenue.window(mv.all_history()), (scope,)),
        (revenue.per(channel).sum(), ()),
        (ms.rescale(revenue, divisor=100, unit="CNY"), ()),
        (amount.sum(), ()),
        (members.count(), ()),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Naming rejection must precede business reads")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    for recipe, by in recipes:
        with pytest.raises(AnalysisError) as caught:
            session.observe(recipe)
        error = caught.value
        assert error.expected == "a directly referenced declared Metric for automatic output naming"
        assert error.received and "input 1" in error.received
        assert error.repair and "user-chosen output name" in error.repair.action
        assert session.observe({"chosen": recipe}, by=by).output_names == ("chosen",)
    assert terminal_runs(session) == ()


def test_observe_invalid_shapes_and_elements_reject_before_consumption(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Invalid shapes must not consume semantics or read data")

    monkeypatch.setattr(source_capture, "consume_metric_in_state", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    for invalid in (
        (),
        {},
        [revenue],
        {"revenue"},
        iter((revenue,)),
        "revenue",
        None,
        (revenue, 1),
        (("revenue", revenue),),
        ({"revenue": revenue},),
        {"bad": 1},
        {None: revenue},
    ):
        with pytest.raises(AnalysisError) as caught:
            session.observe(invalid)
        assert caught.value.expected and caught.value.received and caught.value.repair
        assert caught.value.repair.help_target.canonical_id == "session.observe"
    assert terminal_runs(session) == ()


@pytest.mark.parametrize("name", ("left_present", "right_present"))
def test_observe_reserved_declaration_name_requires_explicit_mapping(
    analysis_dsl_case_factory: DslCaseFactory, name: str
) -> None:
    names = replace(DSL_NAMES, revenue=name)
    session = analysis_dsl_case_factory("j1", names=names).session
    metric = session.catalog.metrics.get(f"sales.{name}")
    with pytest.raises(AnalysisError) as caught:
        session.observe(metric)
    assert caught.value.received and f"sales.{name}" in caught.value.received
    assert caught.value.expected == "a value name outside the reserved presence names"
    assert caught.value.repair and "valid output name" in caught.value.repair.action
    assert session.observe({"revenue": metric}).output_names == ("revenue",)


def test_observe_naming_consumes_once_in_the_sessions_frozen_scope(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    frozen = session.catalog._state
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("return amount_value.sum(", "return 2 * amount_value.sum(")
    )
    changed = ms.load(workspace_dir=case.root).metrics.get("sales.revenue")
    consume = source_capture.consume_metric_in_state
    consumed: list[CompiledSemanticState] = []

    def tracked(value: Deferred, state: CompiledSemanticState) -> MetricFacts:
        consumed.append(state)
        return consume(value, state)

    monkeypatch.setattr(source_capture, "consume_metric_in_state", tracked)
    frame = session.observe(changed)
    assert consumed == [frozen]
    assert frame.output_names == ("revenue",)
    assert frame.compute().to_pandas()["revenue"].tolist() == [1176]
    assert consumed == [frozen]


def test_observe_shorthand_preserves_semantic_and_session_rejections(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    for invalid in (
        ms.ref.metric("sales.revenue"),
        session.catalog.fields.get("sales.order.amount"),
    ):
        with pytest.raises(SemanticError):
            session.observe(invalid)
    other = mv.session.get_or_create("other-observation", timezone="UTC")
    members = other.members(other.catalog.entities.get("sales.customer"))
    with pytest.raises(AnalysisError, match="another Store or Session") as caught:
        session.observe(
            session.catalog.metrics.get("sales.revenue"), where=mv.in_population(members)
        )
    assert caught.value.expected and caught.value.received and caught.value.repair
    assert terminal_runs(session) == ()
    foreign = analysis_dsl_case_factory("j1", names=replace(DSL_NAMES, domain="finance"))
    with pytest.raises(SemanticError, match="frozen catalog"):
        session.observe(foreign.session.catalog.metrics.get("finance.revenue"))
