"""Public Session discovery Help discloses absence without changing state."""

from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._authoring.model import AuthoringCapability
from marivo._help import help as show_help
from marivo.datasource.adapters import SourceSession
from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS
from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
from marivo.semantic.expressions import Entity
from tests.shared_fixtures import DslCaseFactory, rendered_help, terminal_runs
from tests.support.refs import as_ref


def test_repeatable_session_help_is_reachable_and_discloses_conflicts() -> None:
    routes = rendered_help("analysis.sessions")
    assert "analysis.session.get_or_create" in routes
    page = rendered_help("analysis.session.get_or_create")
    assert page == rendered_help(mv.session.get_or_create)
    assert "Kind: callable" in page
    assert "None stores no description on creation" in page
    assert "None resolves and freezes the system timezone on creation" in page
    assert "None selects all authoring domain directories on creation" in page
    assert "Non-None values must match" in page
    assert "never set a current Session" in page
    assert "SessionStateError" in page
    assert "error.repair.candidates" in page


@pytest.mark.parametrize(
    ("target", "absence"),
    [
        ("analysis.session.recent", "return an empty page when its Store is absent"),
    ],
)
def test_session_discovery_help_describes_absence_without_creating_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    target: str,
    absence: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    old = tmp_path / ".marivo/analysis/generations/v5/session_store.db"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"preserve-old-store")

    assert marivo.help is show_help
    show_help(target)
    text = capsys.readouterr().out
    assert absence in text
    assert "No creation" in text
    assert old.read_bytes() == b"preserve-old-store"
    assert not (old.parent.parent / "v16").exists()


def test_observe_help_distinguishes_field_axes_from_session_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    show_help("analysis.session.observe")
    text = capsys.readouterr().out
    assert "() gives one zero-axis cell" in text
    assert "TimeScope/CalendarAxis: intervals/buckets" in text
    assert "P fixes joint keys" in text
    assert "by=() supplies no time range" in text
    assert "finite TimeScope/CalendarAxis in by" in text
    assert "qualified native means may omit it" in text
    assert "Metric-specific sample coverage may still reject at compute/persist" in text
    assert "Input during:" not in text and "Input members:" not in text
    show_help("analysis.population.count")
    assert "declared identities without facts still count" in capsys.readouterr().out
    show_help("analysis.session.resume")
    resume = capsys.readouterr().out
    assert "Input session_id:" in resume and "Input by:" not in resume


def test_time_scope_help_routes_to_calendar_capable_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import marivo.analysis as mv

    show_help("analysis.time_scope")
    text = capsys.readouterr().out
    assert "session.time_scope" in text
    assert "frozen timezone" in text
    assert "mv.time_scope" not in text
    show_help("analysis.session.time_scope")
    assert "start=" in capsys.readouterr().out
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("calendar-help", timezone="UTC")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    assert len(scope.by(mv.grain("day")).grid.cells) == 31
    from marivo._temporal import _validate_time_scope_data

    with pytest.raises(ValueError, match="frozen boundary_timezone"):
        _validate_time_scope_data({"start": "2026-01-01", "end": "2026-02-01"})
    assert terminal_runs(session) == ()


def test_catalog_discovery_separates_definitions_from_execution_values(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    catalog = case.session.catalog

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Catalog discovery opened or read a source")

    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    show_help("analysis.inputs.catalog")
    guidance = capsys.readouterr().out
    assert "collection.show()" in guidance and "displayed plain path" in guidance
    assert "catalog.describe(ref).show()" in guidance
    assert "Deferred" in guidance and "show()/contract()" in guidance
    show_help("semantic.semantic_catalog.describe")
    assert "ExpressionContract" in capsys.readouterr().out

    metric_ref = ms.ref.metric("sales.revenue")
    field_ref = ms.ref.dimension("sales.customer.region")
    entity_ref = ms.ref.entity("sales.customer")
    for collection, ref in (
        (catalog.metrics, metric_ref),
        (catalog.fields, field_ref),
        (catalog.entities, entity_ref),
    ):
        reference = as_ref(ref)
        assert reference is not None
        collection.show()
        assert reference.path in capsys.readouterr().out
        assert reference in collection._refs
        catalog.describe(ref).show()
        assert reference.path in capsys.readouterr().out

    metric_reference = as_ref(metric_ref)
    field_reference = as_ref(field_ref)
    entity_reference = as_ref(entity_ref)
    assert metric_reference is not None
    assert field_reference is not None
    assert entity_reference is not None
    metric = catalog.metrics.get(metric_reference.path)
    field = catalog.fields.get(field_reference.path)
    entity = catalog.entities.get(entity_reference.path)
    assert isinstance(metric, Deferred)
    assert isinstance(field, Deferred)
    assert isinstance(entity, Entity)
    query = case.session.observe({"revenue": metric}, by=(field,))
    population = case.session.members(entity)
    assert isinstance(query, mv.AnalysisFrame) and query.output_names
    assert population.axes
    assert terminal_runs(case.session) == ()


def test_catalog_focused_help_preserves_owning_output_families() -> None:
    for member in CATALOG_MEMBER_CONTRACTS:
        page = rendered_help("analysis.catalog." + member.property_name)
        assert f"Result: CatalogCollection[{member.item_type_name}]\n" in page
    for name, target in (("readiness", "readiness"),):
        contract = SEMANTIC_REGISTRY.by_canonical_id(target)
        assert isinstance(contract, AuthoringCapability)
        page = rendered_help("analysis.catalog." + name)
        assert f"Result: {contract.output_family}\n" in page
