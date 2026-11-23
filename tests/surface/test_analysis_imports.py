"""Smoke tests that the analysis package and its subpackages import cleanly."""

import os
import subprocess
import sys

import marivo.analysis as mv
from marivo.datasource.metadata_types import TableMetadata
from marivo.datasource.runtime.preview import PreviewResult
from tests.support.paths import PROJECT_ROOT


def test_public_bindings_stay_stable_across_private_imports() -> None:
    script = """
import importlib
import marivo.analysis as mv
from marivo.analysis._help_specs.manifest import REGISTRY
names = ('Population', 'AnalysisFrame', 'FrameAxis', 'Column',
         'FrameMethodFacts')
bindings = tuple(getattr(mv, name) for name in names)
exports = tuple(mv.__all__)
help_targets = REGISTRY.canonical_ids()
for module in ('model.data_types', 'model.artifact_data', 'model.errors'):
    importlib.import_module('marivo.analysis.' + module)
assert bindings == tuple(getattr(mv, name) for name in names)
assert all(name in mv.__all__ for name in names)
assert tuple(mv.__all__) == exports
assert REGISTRY.canonical_ids() == help_targets
assert {'analysis_frame', 'frame_axis', 'analysis_frame.compute'} <= set(help_targets)
from marivo.analysis.session.core import Session

exports = tuple(mv.__all__)
help_targets = REGISTRY.canonical_ids()
members = Session.members
for module in ('source_bindings', 'relationship_binding', 'observation'):
    importlib.import_module('marivo.analysis.binding.' + module)
importlib.import_module('marivo.analysis.session._lazy_sources')
assert Session.members is members
assert tuple(mv.__all__) == exports
assert REGISTRY.canonical_ids() == help_targets
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import marivo; assert Path(marivo.__file__).resolve() == Path.cwd() / 'marivo/__init__.py'\n"
            + script,
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_model_import_boundary_rejects_new_heavy_dependencies() -> None:
    """Exercise the checked-in contract against real and injected import graphs."""
    script = """
from configparser import ConfigParser
from copy import deepcopy
from importlib import import_module
from pkgutil import walk_packages
import sys

from marivo.analysis import model
from marivo.analysis.errors import AnalysisRepair

for info in walk_packages(model.__path__, model.__name__ + '.'):
    import_module(info.name)
for dependency in ('pandas', 'ibis', 'pyarrow', 'duckdb', 'pydantic'):
    assert dependency not in sys.modules, dependency

import grimp
from importlinter.configuration import configure
from importlinter.contracts.forbidden import ForbiddenContract

configure()
config = ConfigParser()
assert config.read('.importlinter')
section = config['importlinter:contract:analysis-model-no-heavy-runtime']
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
    exclude_type_checking_imports=config['importlinter'].getboolean('exclude_type_checking_imports'),
    cache_dir=None,
)
baseline = contract.check(deepcopy(graph), verbose=False)
assert baseline.kept and not baseline.warnings, baseline.metadata

source = 'marivo.analysis.model.errors'
helper = 'marivo.dataset_import_probe'
for backend in ('pandas', 'ibis', 'pyarrow', 'duckdb', 'pydantic'):
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

# A dependency on the semantic runtime also reaches the backend.
candidate = deepcopy(graph)
candidate.add_import(importer=source, imported='marivo.semantic.catalog')
result = contract.check(candidate, verbose=False)
assert not result.kept, result.metadata
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import marivo; assert Path(marivo.__file__).resolve() == Path.cwd() / 'marivo/__init__.py'\n"
            + script,
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_analysis_keeps_typed_correspondence_and_member_values() -> None:

    assert isinstance(mv.ordinal(), mv.CalendarCorrespondence)
    assert isinstance(mv.offset(periods=1), mv.CalendarCorrespondence)
    assert isinstance(mv.offset(years=-1), mv.CalendarCorrespondence)
    assert callable(mv.identity)
    assert isinstance(mv.Grouping, type)
    assert callable(mv.Population.count)
    assert callable(mv.row_count)


def test_session_class_exposes_sources_and_frame_owned_operators() -> None:

    for name in (
        "members",
        "read",
        "observe",
        "frame",
        "values",
        "persisted_frame",
        "population_from",
    ):
        assert callable(getattr(mv.Session, name))
    for name in (
        "select",
        "where",
        "top",
        "reduce",
        "over",
        "correlate",
        "align",
        "compare",
        "attribute",
        "compute",
        "persist",
        "to_pandas",
        "axis",
        "coord",
    ):
        assert callable(getattr(mv.AnalysisFrame, name))


def test_analysis_keeps_subdomain_dtos_out_of_top_level() -> None:

    assert mv.FrameSourceRead is not None
    assert TableMetadata is not None
    assert PreviewResult is not None


def test_private_workers_defer_public_analysis_initialization() -> None:
    script = """
import marivo.analysis as mv
# A normal session facade access must still install wrappers before invocation.
assert callable(mv.session.get_or_create)
from marivo.analysis.session.core import Session
assert mv.Session is Session
assert callable(Session.members)
assert sorted(mv.__all__) == dir(mv)
assert mv.grain('day').unit == 'day'
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import marivo; assert Path(marivo.__file__).resolve() == Path.cwd() / 'marivo/__init__.py'\n"
            + script,
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_retired_result_entries_have_no_public_aliases() -> None:

    for name in (
        "Query",
        "CorrespondenceAxis",
        "DeclaredCorrespondence",
        "corresponding",
        "broadcast",
        "Statistic",
        "ComparisonFrame",
        "Analysis",
    ):
        assert not hasattr(mv, name) and name not in mv.__all__
    for name in ("query", "artifact", "execute"):
        assert not hasattr(mv.Session, name)
    for name in ("execute", "population", "rollup", "drill", "share"):
        assert not hasattr(mv.AnalysisFrame, name)
    assert not hasattr(mv, "Artifact")
