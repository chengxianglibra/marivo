"""Smoke tests that the analysis package and its subpackages import cleanly."""

import subprocess
import sys
from pathlib import Path


def test_dataset_core_public_bindings_are_stable_across_private_imports() -> None:
    script = """
import importlib
import marivo.analysis as mv
import sys
assert 'scipy.stats' not in sys.modules
from marivo.analysis._capabilities.registry import REGISTRY
names = ('Dataset', 'LogicalDataset', 'MaterializedDataset', 'DatasetFieldRef', 'DatasetContract')
bindings = tuple(getattr(mv, name) for name in names)
exports = tuple(mv.__all__)
help_targets = REGISTRY.canonical_ids()
for module in ('base', 'descriptors', 'fields', 'state', 'contract', 'registry', 'handles', 'actions', 'errors'):
    importlib.import_module('marivo.analysis.datasets.' + module)
assert bindings == tuple(getattr(mv, name) for name in names)
assert all(name in mv.__all__ for name in names)
assert tuple(mv.__all__) == exports
assert REGISTRY.canonical_ids() == help_targets
assert {'datasets', 'datasets.dataset', 'datasets.logical', 'datasets.materialized', 'datasets.contract', 'actions.execute'} <= set(help_targets)
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


def test_observation_imports_do_not_replace_public_bindings() -> None:
    script = """
import importlib
import marivo.analysis as mv
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.session.core import Session

exports = tuple(mv.__all__)
help_targets = REGISTRY.canonical_ids()
observe = Session.observe
bindings = Session.source_bindings
for module in ('population', 'metric', 'source_bindings', 'predicates',
               'coordinates', 'aggregation', 'contracts', 'errors'):
    importlib.import_module('marivo.analysis.observation.' + module)
importlib.import_module('marivo.analysis.session._lazy_sources')
for name in ('LogicalPopulationDataset', 'MaterializedPopulationDataset',
             'LogicalMetricDataset', 'MaterializedMetricDataset', 'AnalysisPredicate'):
    assert name in mv.__all__ and hasattr(mv, name), name
assert callable(Session.population)
assert Session.observe is observe
assert Session.source_bindings is bindings
assert tuple(mv.__all__) == exports
assert REGISTRY.canonical_ids() == help_targets
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


def test_analysis_keeps_typed_dataset_operator_values():
    import marivo.analysis as mv

    assert mv.window_bucket().kind == "window_bucket"
    assert callable(mv.naive)
    assert callable(mv.seasonal_naive)
    assert callable(mv.drift)
    assert callable(mv.periods)
    assert not hasattr(mv, "SamplingPolicy")


def test_session_does_not_expose_report_methods() -> None:
    import marivo.analysis as mv

    assert not hasattr(mv.Session, "save_report")
    assert not hasattr(mv.Session, "validate_report")
    assert not hasattr(mv.Session, "publish_report")


def test_analysis_publish_submodule_removed() -> None:
    import marivo.analysis as mv

    assert not hasattr(mv, "publish")


def test_session_class_exposes_sources_and_dataset_owned_operators():
    import marivo.analysis as mv

    assert callable(mv.Session.observe)
    assert callable(mv.Session.population)
    assert isinstance(mv.Session.events, property)
    assert isinstance(mv.Session.lifecycle, property)
    for name in (
        "compare",
        "attribute",
        "correlate",
        "forecast",
        "assess_quality",
        "hypothesis_test",
        "discover",
        "transform",
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
    ):
        assert not hasattr(mv.Session, name)
    for name in ("compare", "correlate", "forecast"):
        assert callable(getattr(mv.LogicalMetricDataset, name))


def test_analysis_exports_no_promotion_types():
    import marivo.analysis as mv

    assert mv.ArtifactRef("frame_1").ref == "frame_1"
    assert not hasattr(mv, "PromotionPolicy")
    assert not hasattr(mv.errors, "PromotionFailedError")


def test_analysis_exports_public_surface_by_layer() -> None:
    import marivo.analysis as mv

    for name in (
        "Dataset",
        "LogicalMetricDataset",
        "MaterializedMetricDataset",
        "ArtifactRef",
        "TimeScope",
        "SessionGraph",
        "ArtifactSummary",
        "RunPage",
        "Finding",
    ):
        assert name in mv.__all__
        assert hasattr(mv, name)
    for name in ("BaseFrame", "BaseFrameMeta", "Lineage", "SamplingPolicy", "JobSummary"):
        assert name not in mv.__all__
        assert not hasattr(mv, name)


def test_analysis_keeps_subdomain_dtos_out_of_top_level() -> None:
    import marivo.analysis as mv
    import marivo.datasource as md
    from marivo.datasource.metadata import TableMetadata
    from marivo.preview import PreviewResult

    assert mv.Finding is not None
    assert TableMetadata is not None
    assert PreviewResult is not None
    assert not hasattr(md, "TableMetadata")
    assert not hasattr(md, "PreviewResult")
    assert not hasattr(mv.errors, "PromotionFailedError")


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


def test_session_backend_guard_is_removed_with_eager_execution() -> None:
    import marivo.analysis.session.core as core

    assert not hasattr(core, "ensure_session_can_execute")
    assert not hasattr(core.Session, "is_read_only")


def test_ensure_session_writable_alias_is_removed() -> None:
    with __import__("pytest").raises(ImportError):
        from marivo.analysis.session.core import ensure_session_writable  # noqa: F401


def test_compile_backend_factory_shim_is_removed() -> None:
    import importlib

    import pytest

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("marivo.analysis.session._runtime")


def test_migration_failed_error_is_removed() -> None:
    import marivo.analysis.errors as errors

    assert not hasattr(errors, "MigrationFailedError")


def test_private_workers_defer_public_analysis_initialization() -> None:
    script = """
import sys
import marivo.analysis as mv
from marivo.analysis.materialization import storage, reads, inspection
assert 'marivo.analysis._public' not in sys.modules
assert 'marivo.analysis.frames' not in sys.modules
assert 'marivo.analysis.evidence' not in sys.modules
assert 'marivo.analysis.session' not in sys.modules
from marivo.analysis.materialization import local_execution
assert 'marivo.analysis._capabilities.registry' not in sys.modules
# A normal session facade access must still install wrappers before invocation.
assert callable(mv.session.get_or_create)
from marivo.analysis.session.core import Session
assert mv.Session is Session
assert callable(Session.observe)
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
