"""Population and Path Help expose the actual public values and workflow."""

from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import NavigationInput
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.semantic._expression_values import MetricExpression, RowExpression
from tests.shared_fixtures import DslCaseFactory


def test_population_count_help_describes_closed_metric() -> None:
    population = mv.coordinates(by=(), keys=((),))
    assert isinstance(population.count(), MetricExpression)


def test_in_population_help_distinguishes_closed_and_deferred() -> None:
    population = mv.coordinates(by=(), keys=((),))
    symbolic = mv.coordinates(by=(ms.ref.dimension("sales.orders.region"),), keys=(("east",),))
    assert isinstance(mv.in_population(population), RowExpression)
    assert isinstance(symbolic, Deferred)
    assert isinstance(mv.in_population(symbolic), Deferred)


def test_population_workflow_help_example_selects_and_counts_complete_identities(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    page = REGISTRY.by_canonical_id("inputs.population")
    assert isinstance(page, NavigationInput)
    assert page.example is not None
    namespace: dict[str, object] = {
        "session": case.session,
        "customer": case.catalog.get("entity:sales.customer"),
        "region": case.catalog.get("dimension:sales.customer.region"),
    }
    exec(compile(page.example.code, "<population-workflow>", "exec"), namespace)
    result = namespace[page.example.result]
    assert isinstance(result, mv.AnalysisFrame)
    assert result.to_pandas()["customers"].tolist() == [2]


def test_path_type_and_factory_have_distinct_exact_help() -> None:
    for target, value in (
        ("analysis.path", mv.Path),
        ("analysis.path.define", mv.path),
        ("analysis.relationship_step", mv.RelationshipStep),
        ("semantic.relationship.at", ms.Relationship.at),
    ):
        text = render_help_text(target)[0]
        assert text == render_help_text(value)[0]
    assert "analysis.path.define" in render_help_text("semantic.field.via")[0]
