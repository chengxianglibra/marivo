"""Smoke tests that the analysis package and its subpackages import cleanly."""

import subprocess
import sys
from pathlib import Path


def test_private_dataset_core_has_no_early_public_surface() -> None:
    script = """
import importlib
import marivo.analysis as mv
import sys
assert 'scipy.stats' not in sys.modules
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
from marivo.analysis.errors import HelpTargetError
from marivo.introspection.live.resolve import resolve_live_target

names = '''Dataset LogicalDataset MaterializedDataset DatasetShapeId
DatasetFieldId DatasetFieldIdentity DatasetPhysicalTypeState DatasetField
DatasetRowBound DatasetCardinality DatasetOrderTerm DatasetOrdering DatasetByteCount
DatasetFamilyRowSemantics DatasetRowContract DatasetRowSetContract DatasetSchema
LogicalDatasetState MaterializedDatasetState DatasetContract DatasetFields
DatasetFieldRef'''.split()
baseline_exports = tuple(mv.__all__)
baseline_help = REGISTRY.help_targets
for name in names:
    assert name not in mv.__all__ and not hasattr(mv, name), name
for module in ('base', 'descriptors', 'fields', 'state', 'contract', 'registry',
               'handles', 'actions', 'errors'):
    importlib.import_module('marivo.analysis.datasets.' + module)
for name in names:
    assert name not in mv.__all__ and not hasattr(mv, name), name
assert tuple(mv.__all__) == baseline_exports
assert REGISTRY.help_targets == baseline_help
for target in ('datasets', 'datasets.dataset', 'datasets.logical',
               'datasets.materialized', 'datasets.contract', 'actions.execute'):
    try:
        resolve_live_target(target, ANALYSIS_LIVE_SURFACE)
    except HelpTargetError:
        pass
    else:
        raise AssertionError('Early Dataset Help route: ' + target)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_private_observation_imports_do_not_activate_sources_or_help() -> None:
    script = """
import importlib
import marivo.analysis as mv
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.session.core import Session

exports = tuple(mv.__all__)
help_targets = REGISTRY.help_targets
observe = Session.observe
bindings = Session.source_bindings
for module in ('population', 'metric', 'source_bindings', 'predicates',
               'coordinates', 'aggregation', 'contracts', 'errors'):
    importlib.import_module('marivo.analysis.observation.' + module)
importlib.import_module('marivo.analysis.session._lazy_sources')
for name in ('LogicalPopulationDataset', 'MaterializedPopulationDataset',
             'LogicalMetricDataset', 'MaterializedMetricDataset', 'AnalysisPredicate'):
    assert name not in mv.__all__ and not hasattr(mv, name), name
assert not hasattr(Session, 'population')
assert Session.observe is observe
assert Session.source_bindings is bindings
assert tuple(mv.__all__) == exports
assert REGISTRY.help_targets == help_targets
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dataset_backend_import_boundary_rejects_new_indirect_paths() -> None:
    """Exercise the checked-in contract against real and injected import graphs."""
    script = """
from configparser import ConfigParser
from copy import deepcopy

import grimp
from importlinter.configuration import configure
from importlinter.contracts.forbidden import ForbiddenContract

configure()
config = ConfigParser()
assert config.read('.importlinter')
section = config['importlinter:contract:dataset-core-has-no-backend-imports']
options = {
    key: [line.strip() for line in value.splitlines() if line.strip()]
    if '\\n' in value else value
    for key, value in section.items()
}
contract = ForbiddenContract(
    name=section['name'],
    session_options={'root_packages': ['marivo'], 'include_external_packages': True},
    contract_options=options,
)
graph = grimp.build_graph(
    'marivo', include_external_packages=True,
    exclude_type_checking_imports=True, cache_dir=None,
)
baseline = contract.check(deepcopy(graph), verbose=False)
assert baseline.kept and not baseline.warnings, baseline.metadata

source = 'marivo.analysis.datasets.base'
helper = 'marivo.dataset_import_probe'
for backend in ('pandas', 'ibis', 'pyarrow', 'duckdb'):
    for indirect in (False, True):
        candidate = deepcopy(graph)
        if backend not in candidate.modules:
            candidate.add_module(backend, is_squashed=True)
        if indirect:
            candidate.add_module(helper)
            candidate.add_import(importer=source, imported=helper)
            candidate.add_import(importer=helper, imported=backend)
        else:
            candidate.add_import(importer=source, imported=backend)
        result = contract.check(candidate, verbose=False)
        assert not result.kept, (backend, indirect, result.metadata)

# An approved legacy target is exempt only on its named existing Core edge.
candidate = deepcopy(graph)
candidate.add_import(importer=source, imported='marivo.semantic.catalog')
result = contract.check(candidate, verbose=False)
assert not result.kept, result.metadata
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_package_imports():
    import marivo.analysis

    assert marivo.analysis is not None


def test_namespace_alias_works():
    import marivo.analysis as mv

    assert mv.__name__ == "marivo.analysis"


def test_analysis_keeps_frame_and_policy_exports():
    import marivo.analysis as mv
    from marivo.analysis.frames.forecast import ForecastFrameMeta
    from marivo.analysis.frames.hypothesis import HypothesisTestResultMeta

    assert mv.window_bucket().kind == "window_bucket"
    assert callable(mv.day_of_week)
    assert callable(mv.period_progress)
    assert callable(mv.period_correspondence)
    assert callable(mv.occurrence_progress)
    assert mv.SamplingPolicy().pairing == "window_bucket"
    assert HypothesisTestResultMeta.model_fields["kind"].default == "hypothesis_test_result"
    assert ForecastFrameMeta.model_fields["kind"].default == "forecast_frame"


def test_session_does_not_expose_report_methods() -> None:
    import marivo.analysis as mv

    assert not hasattr(mv.Session, "save_report")
    assert not hasattr(mv.Session, "validate_report")
    assert not hasattr(mv.Session, "publish_report")


def test_analysis_publish_submodule_removed() -> None:
    import marivo.analysis as mv

    assert not hasattr(mv, "publish")


def test_session_class_exposes_execution_surface():
    import marivo.analysis as mv

    assert callable(mv.Session.observe)
    assert callable(mv.Session.compare)
    assert callable(mv.Session.attribute)
    assert callable(mv.Session.correlate)
    assert callable(mv.Session.forecast)
    assert not hasattr(mv.Session, "assess_quality")
    assert callable(mv.Session.hypothesis_test)
    assert isinstance(mv.Session.discover, property)
    assert not hasattr(mv.Session, "transform")
    assert not hasattr(mv.Session, "from_pandas")
    assert not hasattr(mv.Session, "explore_ibis")
    assert not hasattr(mv.Session, "promote_metric_frame")
    assert not hasattr(mv.Session, "promote_delta_frame")
    assert not hasattr(mv.Session, "promote_attribution_frame")
    assert not hasattr(mv.MetricFrame, "from_dataframe")


def test_analysis_exports_no_promotion_types():
    import marivo.analysis as mv

    assert mv.ArtifactRef("frame_1").ref == "frame_1"
    assert not hasattr(mv, "PromotionPolicy")
    assert not hasattr(mv.errors, "PromotionFailedError")


def test_analysis_exports_public_surface_by_layer() -> None:
    import marivo.analysis as mv

    # Public surface exports listed in __all__.
    default_exports = {
        "session",
        "Session",
        "MetricFrame",
        "DeltaFrame",
        "AttributionFrame",
        "CandidateSet",
        "AssociationResult",
        "HypothesisTestResult",
        "ForecastFrame",
        "window_bucket",
        "day_of_week",
        "period_progress",
        "period_correspondence",
        "AlignmentPolicy",
        "ArtifactRef",
        "TimeScope",
        "AbsoluteWindow",
        "runtime_metric",
        "SessionGraph",
        "ArtifactSummary",
        "RunPage",
        "IncompleteRun",
        "SucceededRun",
        "FailedRun",
        "FindingPage",
        "Finding",
    }
    for name in default_exports:
        assert name in mv.__all__, name
        assert hasattr(mv, name), name

    # Types importable via explicit attribute access but not listed in the
    # top-level __all__ help index.
    advanced_internal = {
        "BaseFrame",
        "BaseFrameMeta",
        "SessionSummary",
        "Lineage",
        "LineageStep",
        "SamplingPolicy",
        "errors",
        "evidence",
        "frames",
    }
    for name in advanced_internal:
        assert name not in mv.__all__, name
        assert hasattr(mv, name), name
    assert not hasattr(mv, "JobSummary")


def test_analysis_keeps_subdomain_dtos_out_of_top_level() -> None:
    import marivo.analysis as mv
    import marivo.datasource as md
    from marivo.datasource.metadata import TableMetadata
    from marivo.preview import PreviewResult

    assert mv.evidence.Subject is not None
    assert TableMetadata is not None
    assert PreviewResult is not None
    assert not hasattr(md, "TableMetadata")
    assert not hasattr(md, "PreviewResult")
    assert not hasattr(mv.errors, "PromotionFailedError")
    assert mv.errors.DiscoverInsufficientDataError is not None


def test_analysis_keeps_report_types_out_of_public_surface() -> None:
    import marivo.analysis as mv

    for name in [
        "ReportRegistration",
        "MarivoReportArtifact",
        "ReportManifest",
        "ReportSpec",
        "PublishReportResult",
    ]:
        assert name not in mv.__all__
        assert not hasattr(mv, name)


def test_analysis_exports_no_derive_symbols() -> None:
    import marivo.analysis as mv

    for name in (
        "ibis_query",
        "metric_columns",
        "time_column",
        "dimension_column",
        "DeriveContext",
        "IbisQuerySpec",
        "MetricColumnBinding",
        "MetricColumns",
    ):
        assert not hasattr(mv, name), f"mv.{name} should be removed"


def test_analysis_derive_module_is_deleted() -> None:
    import importlib

    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("marivo.analysis.derive")


def test_analysis_escape_hatch_module_is_deleted() -> None:
    import importlib

    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("marivo.analysis.escape_hatch")


def test_ensure_session_can_execute_blocks_read_only_sessions(
    tmp_path,
    monkeypatch,
) -> None:
    """The canonical guard still carries its behavior: a read-only session
    (no backend factory) must be rejected with ``NoBackendFactoryError``."""
    import marivo.analysis.session as session_attach
    from marivo.analysis.errors import NoBackendFactoryError
    from marivo.analysis.session.core import ensure_session_can_execute

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    read_only = session_attach.get_or_create(name="demo", use_datasources=False)
    try:
        with __import__("pytest").raises(NoBackendFactoryError) as exc_info:
            ensure_session_can_execute(read_only)
        snippet = exc_info.value.repair.snippet or ""
        assert f"session_id = {read_only.id!r}" in snippet
        assert "mv.session.get_or_create" not in snippet
        namespace = {}
        exec(
            snippet.replace(
                '"<project-datasources-or-explicit-factory>"',
                '"explicit-factory"',
                1,
            ),
            namespace,
        )
        repaired = namespace["session"]
        assert repaired.id == read_only.id
        assert not repaired.is_read_only
    finally:
        read_only.close()
        session_attach._reset_process_state()


def test_ensure_session_writable_alias_is_removed() -> None:
    with __import__("pytest").raises(ImportError):
        from marivo.analysis.session.core import ensure_session_writable  # noqa: F401


def test_intent_modules_use_canonical_session_guard_name() -> None:
    """Intent modules must import the canonical name, never the old alias."""
    import pathlib

    import marivo.analysis

    intents_dir = pathlib.Path(marivo.analysis.__file__).parent / "intents"
    stale = []
    for path in sorted(intents_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "ensure_session_writable" in line:
                stale.append(f"{path.name}: {line.strip()}")
    assert not stale, "intent modules still reference ensure_session_writable:\n" + "\n".join(stale)


def test_compile_backend_factory_shim_is_removed() -> None:
    import marivo.analysis.session._runtime as runtime

    assert not hasattr(runtime, "_compile_backend_factory")


def test_migration_failed_error_is_removed() -> None:
    import marivo.analysis.errors as errors

    assert not hasattr(errors, "MigrationFailedError")


def test_private_workers_defer_public_analysis_initialization() -> None:
    script = """
import sys
import marivo.analysis as mv
from marivo.analysis.materialization import storage, reads, local_worker
assert 'marivo.analysis._public' not in sys.modules
assert 'marivo.analysis.frames' not in sys.modules
assert 'marivo.analysis._capabilities.registry' not in sys.modules
# A normal session facade access must still install wrappers before invocation.
assert hasattr(mv.session.get_or_create, '__wrapped__')
from marivo.analysis.session.core import Session
assert mv.Session is Session
assert hasattr(Session.observe, '__wrapped__')
assert sorted(mv.__all__) == dir(mv)
assert mv.grain('day').unit == 'day'
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
