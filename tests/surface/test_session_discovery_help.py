"""Public Session discovery Help discloses absence without changing state."""

from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._authoring.model import AuthoringCapability
from marivo._help import help as show_help
from marivo.datasource.adapters import SourceSession
from marivo.semantic import Entity
from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
from tests.shared_fixtures import DslCaseFactory, rendered_help, terminal_runs


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
    assert "Older Stores coexist unchanged" in page


def test_session_help_explains_generation_isolation_and_uncertain_id_ownership() -> None:
    assert "Names, IDs and saved Frames remain independent" in rendered_help("analysis.sessions")
    recent = rendered_help("analysis.session.recent")
    assert "warn once per set per process" in recent
    assert "without opening older Stores" in recent
    for target in ("analysis.session.resume", "analysis.session.inspect"):
        page = rendered_help(target)
        assert "SessionNotFoundError" in page
        assert "without verifying ID ownership" in page


def test_committed_frame_discovery_and_description_have_public_help() -> None:
    assert "analysis.session.persisted_frames" in rendered_help("analysis.sessions")
    page = rendered_help("analysis.session.persisted_frames")
    assert page == rendered_help(mv.Session.persisted_frames)
    assert "PersistedFrameSummaryPage" in page
    assert "1 through 100" in page
    assert "no result files" in page
    assert "analysis.session.persisted_frames" in rendered_help("analysis.session.persisted_frame")
    persist = rendered_help(mv.AnalysisFrame.persist)
    assert "Input description:" in persist and "immutable" in persist
    for target in (
        "analysis.analysis_frame.description",
        "analysis.runtime.persisted_frame_summary.description",
        "analysis.runtime.persisted_frame_summary.schema",
        "analysis.runtime.persisted_frame_summary_page.items",
        "analysis.runtime.persisted_frame_summary_page.next_cursor",
    ):
        assert "Kind: attribute" in rendered_help(target)


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
    assert not (old.parent.parent / "v22").exists()


def test_observe_help_distinguishes_field_axes_from_session_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    show_help("analysis.session.observe")
    text = capsys.readouterr().out
    assert "() gives one zero-axis cell" in text
    assert "TimeScope/CalendarAxis: intervals/buckets" in text
    assert "P fixes joint keys" in text
    assert "by=() supplies no time range" in text
    assert "Windows and calendar samples retain their period requirements" in text
    assert "TimeField sample reducers allow missing clocks" in text
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
    assert "canonical kind:path" in guidance
    assert "object.kind" in guidance and "typed object directly" in guidance
    show_help("semantic.semantic_catalog.get")
    assert "SemanticObject" in capsys.readouterr().out

    for kind, path in (
        ("metric", "sales.revenue"),
        ("dimension", "sales.customer.region"),
        ("entity", "sales.customer"),
    ):
        page = catalog.search(kind=kind, limit=100)
        page.show()
        assert path in capsys.readouterr().out
        assert any(entry.path == path for entry in page.items)
        catalog.get(f"{kind}:{path}").show()
        assert path in capsys.readouterr().out

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
    assert terminal_runs(case.session) == ()


def test_catalog_focused_help_preserves_one_semantic_owner() -> None:
    from marivo._help.model import MarivoHelpTargetError
    from marivo.analysis._capabilities.catalog_inputs import CATALOG_INPUTS

    for name, target in (
        ("search", "semantic_catalog.search"),
        ("get", "semantic_catalog.get"),
        ("readiness", "readiness"),
    ):
        contract = SEMANTIC_REGISTRY.by_canonical_id(target)
        assert isinstance(contract, AuthoringCapability)
        page = rendered_help("semantic." + target)
        assert f"Output family: {contract.output_family}\n" in page
        assert all(
            descriptor.callable_path != contract.callable_path for descriptor in CATALOG_INPUTS
        )
        with pytest.raises(MarivoHelpTargetError):
            rendered_help("analysis.catalog." + name)
    navigation = rendered_help("analysis.inputs.catalog")
    assert "session.catalog" in navigation
    assert "semantic.semantic_catalog.search" in navigation
    assert "semantic.semantic_catalog.get" in navigation
    assert "analysis.catalog.search" not in navigation
