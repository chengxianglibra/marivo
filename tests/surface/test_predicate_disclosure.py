"""The source predicate grammar has one native Help owner and no public type."""

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_registry import prepare
from marivo.analysis.errors import HelpTargetError
from marivo.semantic._capabilities.registry import REGISTRY


def test_source_predicate_topic_and_consumer_boundaries() -> None:
    text, owner, target = render_help_text("semantic.predicate")
    assert (owner, target) == ("semantic", "predicate")
    for fact in (
        "row-stage boolean expressions",
        "&, | and ~",
        "three-valued",
        "complete correlated tuples",
        "Both boolean sides",
        "Population.where accepts membership leaves only",
        "Source reduction where= accepts field leaves only",
        "exact Frame's boolean Column",
        "session.observe({'revenue': revenue}",
    ):
        assert fact in text
    assert len(text) < 6000
    field = REGISTRY.by_canonical_id("field")
    assert any(route.target.canonical_id == "predicate" for route in field.members)
    assert "semantic.predicate" in render_help_text("semantic.metric.where")[0]
    assert "Deferred" in render_help_text("analysis.in_population")[0]
    assert "Column" in render_help_text("analysis.column.is_null")[0]


def test_retired_predicate_type_and_pages_are_absent() -> None:
    assert "PopulationPredicate" not in mv.__all__
    assert not hasattr(mv, "PopulationPredicate")
    condition = mv.in_population(mv.coordinates(by=(), keys=((),)))
    assert type(condition) is ms.RowExpression
    registry = prepare()
    for target in (
        "population_predicate",
        "population_predicate.show",
        "population_predicate.condition",
    ):
        with pytest.raises(HelpTargetError):
            registry.resolve(target)
