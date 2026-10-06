"""Smoke tests that the analysis package and its subpackages import cleanly."""

import subprocess
import sys

from tests.support.paths import PROJECT_ROOT


def test_dataset_core_public_bindings_are_stable_across_private_imports() -> None:
    script = """
import importlib
import marivo.analysis as mv
import sys
assert 'scipy.stats' not in sys.modules
from marivo.analysis._capabilities.registry import REGISTRY
names = ('LogicalAnalysisDomain', 'LogicalNumericRelation', 'MaterializedNumericRelation', 'MaterializedDatasetState', 'DatasetByteCount')
bindings = tuple(getattr(mv, name) for name in names)
exports = tuple(mv.__all__)
help_targets = REGISTRY.canonical_ids()
for module in ('descriptors', 'state', 'handles', 'errors'):
    importlib.import_module('marivo.analysis.datasets.' + module)
assert bindings == tuple(getattr(mv, name) for name in names)
assert all(name in mv.__all__ for name in names)
assert tuple(mv.__all__) == exports
assert REGISTRY.canonical_ids() == help_targets
assert {'datasets', 'datasets.materialized_state', 'actions.execute'} <= set(help_targets)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
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
members = Session.members
bindings = Session.source_bindings
for module in ('source_bindings', 'coordinates', 'contracts', 'errors'):
    importlib.import_module('marivo.analysis.observation.' + module)
importlib.import_module('marivo.analysis.session._lazy_sources')
assert Session.members is members
assert Session.source_bindings is bindings
assert tuple(mv.__all__) == exports
assert REGISTRY.canonical_ids() == help_targets
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
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

source = 'marivo.analysis.datasets.descriptors'
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

# An allowed edge does not authorize the same dependency from another owner.
candidate = deepcopy(graph)
candidate.add_import(importer=source, imported='marivo.semantic.catalog')
result = contract.check(candidate, verbose=False)
assert not result.kept, result.metadata
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_analysis_keeps_typed_dataset_operator_values():
    import marivo.analysis as mv

    assert mv.window_bucket().kind == "window_bucket"
    assert callable(mv.naive)
    assert callable(mv.seasonal_naive)
    assert callable(mv.drift)
    assert callable(mv.periods)


def test_session_class_exposes_sources_and_dataset_owned_operators():
    import marivo.analysis as mv

    assert callable(mv.LogicalNumericRelation.compare)
    assert callable(mv.Session.members)
    assert isinstance(mv.Session.events, property)
    assert isinstance(mv.Session.lifecycle, property)
    for name in ("correlate", "forecast"):
        assert not hasattr(mv.Session, name)
        assert callable(getattr(mv.LogicalNumericRelation, name))


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


def test_private_workers_defer_public_analysis_initialization() -> None:
    script = """
import sys
import marivo.analysis as mv
from marivo.analysis.materialization import storage, reads, inspection
assert 'marivo.analysis._public' not in sys.modules
assert 'marivo.analysis.session' not in sys.modules
from marivo.analysis.materialization import graph_storage
assert 'marivo.analysis._capabilities.registry' not in sys.modules
# A normal session facade access must still install wrappers before invocation.
assert callable(mv.session.get_or_create)
from marivo.analysis.session.core import Session
assert mv.Session is Session
assert callable(Session.members)
assert sorted(mv.__all__) == dir(mv)
assert mv.grain('day').unit == 'day'
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
