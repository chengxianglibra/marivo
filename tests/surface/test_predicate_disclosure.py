"""The source predicate grammar has one native Help owner and no public type."""

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.semantic._capabilities.model import SemanticNavigationRoute, SemanticNavigationTopic
from marivo.semantic._capabilities.registry import REGISTRY


def test_source_predicate_topic_and_consumer_boundaries() -> None:
    text, owner, target = render_help_text("semantic.predicate")
    assert (owner, target) == ("semantic", "predicate")
    assert len(text) < 6000
    field = REGISTRY.by_canonical_id("field")
    assert isinstance(field, SemanticNavigationTopic)
    assert any(
        route.target.canonical_id == "predicate"
        for route in field.members
        if isinstance(route, SemanticNavigationRoute)
    )
    assert "semantic.predicate" in render_help_text("semantic.metric.where")[0]


def test_retired_predicate_type_and_pages_are_absent() -> None:
    condition = mv.in_population(mv.coordinates(by=(), keys=((),)))
    assert type(condition) is ms.RowExpression
