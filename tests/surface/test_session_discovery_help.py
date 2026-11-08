"""Public Session discovery ids and catalog values stay reachable."""

from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._authoring.model import AuthoringCapability
from marivo._help import help as show_help
from marivo.semantic import Entity
from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
from tests.shared_fixtures import DslCaseFactory, rendered_help


def test_repeatable_session_help_is_reachable_and_discloses_conflicts() -> None:
    routes = rendered_help("analysis.sessions")
    assert "analysis.session.get_or_create" in routes


def test_committed_frame_discovery_and_description_have_public_help() -> None:
    assert "analysis.session.persisted_frames" in rendered_help("analysis.sessions")
    assert "analysis.session.persisted_frames" in rendered_help("analysis.session.persisted_frame")


def test_session_discovery_help_describes_absence_without_creating_store() -> None:
    assert marivo.help is show_help


def test_time_scope_help_routes_to_calendar_capable_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("calendar-help", timezone="UTC")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    assert len(scope.by(mv.grain("day")).grid.cells) == 31
    from marivo._temporal import _validate_time_scope_data

    with pytest.raises(ValueError):
        _validate_time_scope_data({"start": "2026-01-01", "end": "2026-02-01"})


def test_catalog_discovery_separates_definitions_from_execution_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    catalog = case.session.catalog

    for kind, path in (
        ("metric", "sales.revenue"),
        ("dimension", "sales.customer.region"),
        ("entity", "sales.customer"),
    ):
        page = catalog.search(kind=kind, limit=100)
        assert any(entry.path == path for entry in page.items)

    metric = catalog.get("metric:sales.revenue")
    field = catalog.get("dimension:sales.customer.region")
    entity = catalog.get("entity:sales.customer")
    assert isinstance(metric, ms.Metric)
    assert isinstance(field, ms.Dimension)
    assert isinstance(entity, Entity)
    query = case.session.observe({"revenue": metric}, by=(field,))
    population = case.session.members(entity)
    assert isinstance(query, mv.AnalysisFrame) and query.output_names
    assert population.axes


def test_catalog_focused_help_preserves_one_semantic_owner() -> None:
    for target in (
        "semantic_catalog.search",
        "semantic_catalog.get",
        "readiness",
    ):
        contract = SEMANTIC_REGISTRY.by_canonical_id(target)
        assert isinstance(contract, AuthoringCapability)
    navigation = rendered_help("analysis.inputs.catalog")
    assert "session.catalog" in navigation
    assert "semantic.semantic_catalog.search" in navigation
    assert "semantic.semantic_catalog.get" in navigation
