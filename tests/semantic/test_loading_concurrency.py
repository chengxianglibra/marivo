"""Independent authored imports and atomic Session source initialization."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from threading import Event, Lock
from types import ModuleType
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.datasource.errors import DatasourceLoadError
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar
    from marivo.semantic.validator import Registry
    from tests.conftest import _SemanticProjectFactory

T = TypeVar("T")


@pytest.fixture(autouse=True)
def isolate_loaded_modules() -> Iterator[None]:
    existing = set(sys.modules)
    yield
    for name in set(sys.modules) - existing:
        if name.startswith(("_marivo_semantic_", "_marivo_datasource_")):
            sys.modules.pop(name, None)


class _Interleaving:
    """Let B collect declarations while A's model file is only partly executed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.label: ContextVar[str] = ContextVar("load_label")
        self.a_domain = Event()
        self.b_domain = Event()
        self.a_partial = Event()
        self.b_done = Event()
        self.module_name = "_loading_probe_" + uuid4().hex
        module = ModuleType(self.module_name)
        monkeypatch.setattr(module, "domain_gate", self.domain_gate, raising=False)
        monkeypatch.setattr(module, "body_gate", self.body_gate, raising=False)
        monkeypatch.setitem(sys.modules, self.module_name, module)

    def domain_gate(self) -> None:
        if self.label.get() == "A":
            self.a_domain.set()
            assert self.b_domain.wait(10)
        else:
            self.b_domain.set()
            assert self.a_partial.wait(10)

    def body_gate(self) -> None:
        if self.label.get() == "A":
            self.a_partial.set()
            assert self.b_done.wait(10)

    def run(self, first: Callable[[], T], second: Callable[[], T]) -> tuple[T, T]:
        def invoke(label: str, operation: Callable[[], T]) -> T:
            token = self.label.set(label)
            try:
                return operation()
            finally:
                if label == "B":
                    self.b_done.set()
                self.label.reset(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(invoke, "A", first)
            assert self.a_domain.wait(10)
            b = pool.submit(invoke, "B", second)
            return a.result(timeout=20), b.result(timeout=20)


def _project(factory: _SemanticProjectFactory, hooks: _Interleaving) -> Path:
    project = factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Tester', default=True)\n"
                f"from {hooks.module_name} import domain_gate\ndomain_gate()\n"
            ),
            "sales/a_model.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders'))\n"
                "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
                "additivity=ms.additive_all())\n"
                f"from {hooks.module_name} import body_gate\nbody_gate()\n"
                "@ms.measure()\ndef adjusted():\n    return amount * 2\n"
            ),
            "sales/z_metric.py": (
                "import marivo.semantic as ms\n"
                "adjusted = ms.ref.measure('sales.orders.adjusted')\n"
                "@ms.metric()\ndef total_cost():\n    return adjusted.sum()\n"
            ),
        },
        load=False,
    )
    return project.workspace_dir


def _assert_complete(catalog: SemanticCatalog) -> None:
    for identity in (
        "entity:sales.orders",
        "measure:sales.orders.amount",
        "measure:sales.orders.adjusted",
        "metric:sales.total_cost",
    ):
        assert catalog.get(identity).id == identity


@pytest.mark.parametrize("sessions", [False, True], ids=["load", "different-sessions"])
def test_concurrent_loads_capture_complete_independent_definitions(
    semantic_project_factory: _SemanticProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
    sessions: bool,
) -> None:
    hooks = _Interleaving(monkeypatch)
    root = _project(semantic_project_factory, hooks)
    if sessions:
        monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
        first = mv.session.get_or_create("first", timezone="UTC")
        second = mv.session.get_or_create("second", timezone="UTC")
        catalogs = hooks.run(lambda: first.catalog, lambda: second.catalog)
    else:
        catalogs = hooks.run(
            lambda: ms.load(workspace_dir=root), lambda: ms.load(workspace_dir=root)
        )
    for catalog in catalogs:
        _assert_complete(catalog)
    assert catalogs[0] is not catalogs[1]
    assert catalogs[0].definition_fingerprint == catalogs[1].definition_fingerprint


def test_failed_concurrent_load_does_not_discard_another_load(
    semantic_project_factory: _SemanticProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = _Interleaving(monkeypatch)
    root = _project(semantic_project_factory, hooks)
    domain = root / "models/semantic/sales/_domain.py"
    domain.write_text(
        domain.read_text() + f"from {hooks.module_name} import fail_second\nfail_second()\n"
    )

    def fail_second() -> None:
        if hooks.label.get() == "B":
            raise ValueError("injected model failure")

    monkeypatch.setattr(sys.modules[hooks.module_name], "fail_second", fail_second, raising=False)

    def attempt() -> SemanticCatalog | SemanticLoadFailed:
        try:
            return ms.load(workspace_dir=root)
        except SemanticLoadFailed as error:
            return error

    first, second = hooks.run(attempt, attempt)
    assert isinstance(first, SemanticCatalog)
    _assert_complete(first)
    assert isinstance(second, SemanticLoadFailed)
    assert second.errors[0].exception_type == "ValueError"
    assert isinstance(second.errors[0].__cause__, ValueError)


@pytest.mark.parametrize("second_surface", ["datasource", "semantic"])
def test_concurrent_datasource_imports_are_complete_and_execute_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second_surface: str
) -> None:
    hooks = _Interleaving(monkeypatch)
    datasources = tmp_path / "models/datasources"
    datasources.mkdir(parents=True)
    (datasources / "a.py").write_text(
        f"from {hooks.module_name} import domain_gate, body_gate\n"
        "domain_gate()\nbody_gate()\nfrom .z_connection import warehouse\n"
    )
    (datasources / "z_connection.py").write_text(
        "import marivo.datasource as md\nwarehouse = md.duckdb(name='warehouse')\n"
    )
    domain = tmp_path / "models/semantic/demo"
    domain.mkdir(parents=True)
    (domain / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='demo', owner='Tester')\n"
    )

    def datasource_names() -> tuple[str, ...]:
        return tuple(item.name for item in md.load(workspace_dir=tmp_path).list())

    def semantic_names() -> tuple[str, ...]:
        return tuple(
            item.name for item in ms.load(workspace_dir=tmp_path).search(kind="datasource").items
        )

    results = hooks.run(
        datasource_names,
        datasource_names if second_surface == "datasource" else semantic_names,
    )
    assert results == (("default", "warehouse"), ("default", "warehouse"))


def test_session_first_initialization_publishes_one_complete_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("shared", timezone="UTC")
    second_attempt = Event()
    initialization_lock = session._sources_lock
    attempts = 0
    initialization_count = 0
    count_lock = Lock()
    original = DatasetRuntime.sources

    class ObservedLock:
        """Expose contention while retaining the Session's real mutex."""

        def __enter__(self) -> None:
            nonlocal attempts
            with count_lock:
                attempts += 1
                if attempts == 2:
                    second_attempt.set()
            assert initialization_lock.acquire(timeout=10)

        def __exit__(self, *args: object) -> None:
            initialization_lock.release()

    monkeypatch.setattr(session, "_sources_lock", ObservedLock())

    def sources(
        runtime: DatasetRuntime,
        *,
        semantic_registry: Registry,
        sidecar: CompiledExpressionSidecar,
        catalog: SemanticCatalog | None = None,
    ) -> ObservationOwner:
        nonlocal initialization_count
        with count_lock:
            initialization_count += 1
        assert second_attempt.wait(10)
        return original(
            runtime, semantic_registry=semantic_registry, sidecar=sidecar, catalog=catalog
        )

    monkeypatch.setattr(DatasetRuntime, "sources", sources)

    def acquire() -> SemanticCatalog:
        return session.catalog

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(acquire) for _ in range(2)]
        catalogs = [future.result(timeout=20) for future in futures]
    assert initialization_count == 1
    assert catalogs[0] is catalogs[1]
    owner = session._sources()
    assert owner.catalog_identity is catalogs[0]
    assert owner.semantic_registry is catalogs[0]._state.registry
    assert owner.sidecar is catalogs[0]._state.sidecar


@pytest.mark.parametrize("failure_stage", ["load", "source-owner"])
def test_session_initialization_failure_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("retry", timezone="UTC")
    if failure_stage == "load":
        domain = tmp_path / "models/semantic/sales"
        domain.mkdir(parents=True)
        source = domain / "_domain.py"
        source.write_text("raise ValueError('injected failure')\n")
        with pytest.raises(SemanticLoadFailed) as caught:
            _ = session.catalog
        assert caught.value.errors[0].exception_type == "ValueError"
        source.write_text("import marivo.semantic as ms\nms.domain(name='sales', owner='Tester')\n")
    else:
        original = DatasetRuntime.sources

        def fail(
            runtime: DatasetRuntime,
            *,
            semantic_registry: Registry,
            sidecar: CompiledExpressionSidecar,
            catalog: SemanticCatalog | None = None,
        ) -> ObservationOwner:
            raise ValueError("injected source owner failure")

        monkeypatch.setattr(DatasetRuntime, "sources", fail)
        with pytest.raises(ValueError):
            _ = session.catalog
        monkeypatch.setattr(DatasetRuntime, "sources", original)
    assert session._catalog_value is None
    assert session._sources_value is None
    catalog = session.catalog
    assert session._sources().catalog_identity is catalog


@pytest.mark.parametrize("surface", ["datasource", "semantic"])
@pytest.mark.parametrize("fails", [False, True])
def test_relative_imports_preserve_sys_path_during_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str, fails: bool
) -> None:
    saved_path = sys.path.copy()
    module_name = "_loading_probe_" + uuid4().hex
    module = ModuleType(module_name)

    def check_path() -> None:
        assert sys.path == saved_path

    monkeypatch.setattr(module, "check_path", check_path, raising=False)
    monkeypatch.setitem(sys.modules, module_name, module)
    directory = (
        tmp_path / "models" / ("datasources" if surface == "datasource" else "semantic/demo")
    )
    directory.mkdir(parents=True)
    if surface == "semantic":
        (directory / "_domain.py").write_text(
            "import marivo.semantic as ms\nms.domain(name='demo', owner='Tester')\n"
        )
    (directory / "_settings.py").write_text("NAME = 'warehouse'\n")
    (directory / "a.py").write_text(
        f"from {module_name} import check_path\ncheck_path()\n"
        "from ._settings import NAME\n"
        + (
            "raise ValueError(NAME)\n"
            if fails
            else "import marivo.datasource as md\nmd.duckdb(name=NAME)\n"
            if surface == "datasource"
            else "import marivo.semantic as ms\nimport marivo.datasource as md\n"
            "ms.entity(name=NAME, datasource=ms.ref.datasource('default'), source=md.table('orders'))\n"
        )
    )
    if fails:
        if surface == "datasource":
            with pytest.raises(DatasourceLoadError) as caught_datasource:
                md.load(workspace_dir=tmp_path).list()
            assert caught_datasource.value.exception_type == "ValueError"
        else:
            with pytest.raises(SemanticLoadFailed) as caught_semantic:
                ms.load(workspace_dir=tmp_path)
            assert caught_semantic.value.errors[0].exception_type == "ValueError"
    elif surface == "datasource":
        assert [item.name for item in md.load(workspace_dir=tmp_path).list()] == [
            "default",
            "warehouse",
        ]
    else:
        assert (
            ms.load(workspace_dir=tmp_path).get("entity:demo.warehouse").id
            == "entity:demo.warehouse"
        )
    assert sys.path == saved_path


def test_reload_refreshes_relative_helpers_and_preserves_captured_analysis(
    semantic_project_factory: _SemanticProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "source.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE orders AS SELECT * FROM (VALUES (3), (7)) AS t(amount)")
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tester')\n",
            "sales/_factor.py": "FACTOR = 2\n",
            "sales/fields.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "from ._factor import FACTOR\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
                "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
                "@ms.measure()\ndef adjusted():\n    return amount * FACTOR\n"
            ),
            "sales/metrics.py": (
                "import marivo.semantic as ms\nfrom .fields import adjusted\n"
                "@ms.metric()\ndef cost():\n    return adjusted.sum()\n"
            ),
        },
        load=False,
    )
    root = project.workspace_dir
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
    session = mv.session.get_or_create("original", timezone="UTC")
    original = session.catalog
    metric = original.get("metric:sales.cost")
    assert metric.kind == "metric"
    pending = session.observe({"cost": metric})
    unchanged = ms.load(workspace_dir=root)
    assert unchanged.definition_fingerprint == original.definition_fingerprint
    retained = {
        name: module
        for name, module in sys.modules.copy().items()
        if name.startswith(("_marivo_semantic_", "_marivo_datasource_"))
    }
    # Different source size invalidates Python bytecode without clock-dependent waits.
    (root / "models/semantic/sales/_factor.py").write_text("FACTOR = 100\n")
    current = ms.load(workspace_dir=root)
    assert current.definition_fingerprint != original.definition_fingerprint
    assert all(sys.modules[name] is module for name, module in retained.items())
    assert session.catalog is original
    assert pending.compute().to_pandas()["cost"].tolist() == [20]
    newer = mv.session.get_or_create("newer", timezone="UTC")
    new_metric = newer.catalog.get("metric:sales.cost")
    assert new_metric.kind == "metric"
    assert newer.observe({"cost": new_metric}).compute().to_pandas()["cost"].tolist() == [1000]
