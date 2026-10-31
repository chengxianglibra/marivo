"""Shared fixtures for the Python-native Marivo test suite."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import duckdb
import ibis
import pytest

from tests.packaging.installer_helpers import InstallerEnv, InstallerToolchain
from tests.packaging.wheel_support import InstalledWheel, prepare_wheel
from tests.shared_fixtures import (
    DSL_NAMES,
    DslCase,
    DslCaseFactory,
    DslNames,
    DslScenario,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    authoring_evidence_template,
    sales_orders_template,
    seed_analysis_dsl_database,
)
from tests.support.source_trace import SourceTrace

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject


class _SemanticProjectFactory(Protocol):
    def __call__(
        self,
        files: dict[str, str],
        load: bool = True,
        models: list[str] | None = None,
        workspace_dir: Path | None = None,
    ) -> SemanticProject: ...


# Cap DuckDB to a single thread per connection. DuckDB defaults to
# hardware_concurrency() threads; with one pytest-xdist worker per CPU that
# oversubscribes cores (N workers x N threads) and inflates test wall time
# roughly 10x via thread thrash. One thread per connection keeps the suite
# CPU-bound and parallelizable across workers. Applied at import so every
# ibis.duckdb.connect call site (marivo backends + tests) is covered before
# any test runs.
_original_duckdb_connect = ibis.duckdb.connect


def _duckdb_connect_single_thread(*args: object, **kwargs: object) -> object:
    kwargs["threads"] = 1
    return _original_duckdb_connect(*args, **kwargs)


ibis.duckdb.connect = _duckdb_connect_single_thread

# Direct fixture connections otherwise start a native thread pool before a
# later SET threads command can shrink it. Honor explicit concurrency tests.
_original_native_duckdb_connect = duckdb.connect


def _native_duckdb_connect_single_thread(
    database: str | Path = ":memory:",
    read_only: bool = False,
    config: dict[str, str | bool | int | float | list[str]] | None = None,
) -> duckdb.DuckDBPyConnection:
    settings: dict[str, str | bool | int | float | list[str]] = {"threads": 1, **(config or {})}
    return _original_native_duckdb_connect(database, read_only=read_only, config=settings)


duckdb.connect = _native_duckdb_connect_single_thread


@pytest.fixture
def analysis_dsl_case_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DslCaseFactory:
    """Load one real DSL declaration project per isolated source and Session."""
    import marivo.analysis.session as session_attach

    next_index = 0

    def build(
        scenario: DslScenario,
        *,
        names: DslNames = DSL_NAMES,
        revenue_unit: str = "CNY",
    ) -> DslCase:
        nonlocal next_index
        next_index += 1
        root = tmp_path / f"dsl_{next_index}"
        root.mkdir()
        database_path = root / "warehouse.duckdb"
        rows = analysis_dsl_rows(scenario)
        seed_analysis_dsl_database(
            database_path,
            names,
            rows,
            float_amount=scenario in ("j4", "j4_ties", "nonfinite"),
        )
        (root / "marivo.toml").write_text('[project]\nname = "analysis-dsl-fixture"\n')
        for relative_path, source in analysis_dsl_project_files(
            names, database_path, revenue_unit=revenue_unit
        ).items():
            destination = (
                root / "models" / relative_path
                if relative_path.startswith("datasources/")
                else root / "models" / "semantic" / relative_path
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source)
        monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
        session = session_attach.get_or_create(f"dsl-{scenario}", timezone="UTC")
        return DslCase(scenario, names, root, database_path, None, session)

    return build


@pytest.fixture(autouse=True)
def _isolate_prior_generation_notices(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.analysis.materialization import prior_generations

    monkeypatch.setattr(prior_generations, "_notified", set())


@pytest.fixture(autouse=True)
def _disable_telemetry_outside_telemetry_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unrelated tests from writing local telemetry for every public call."""
    monkeypatch.setenv("MARIVO_TELEMETRY", "off")


@pytest.fixture(scope="session")
def installer_toolchain(
    pytestconfig: pytest.Config,
) -> InstallerToolchain:
    """Reuse a versioned immutable installer toolchain across test runs."""
    cache_root = Path(pytestconfig.cache.mkdir("installer-toolchains"))
    return InstallerToolchain.load_or_build(cache_root / "v4")


@pytest.fixture
def installer_env(tmp_path: Path, installer_toolchain: InstallerToolchain) -> InstallerEnv:
    """Shared fixture for the Marivo installer black-box tests.

    Reuses a versioned read-only toolchain while isolating each test's
    command log, HOME, project target, and environment mapping.
    """
    log = tmp_path / "commands.log"
    log.touch()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{installer_toolchain.base_bin}:{os.defpath}",
            "FAKE_LOG": str(log),
            "FAKE_UNAME": "Linux",
            "HOME": str(tmp_path / "home"),
            "FAKE_MARIVO_SHIM": str(installer_toolchain.marivo),
        }
    )
    return installer_toolchain, env


@pytest.fixture(scope="session")
def _sales_orders_template_path() -> Path:
    """Session-scoped: ensure the DuckDB template is built once per worker."""
    return sales_orders_template()


@pytest.fixture
def authoring_evidence_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real DuckDB project covering authoring through analysis handoff."""
    import shutil

    database_path = tmp_path / "warehouse.duckdb"
    replica_path = tmp_path / "warehouse_replica.duckdb"
    shutil.copy2(authoring_evidence_template(), database_path)
    shutil.copy2(authoring_evidence_template(), replica_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "authoring-e2e"\n')
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
    )
    (datasource_dir / "warehouse_replica.py").write_text(
        "import marivo.datasource as md\n"
        f"md.duckdb(name='warehouse_replica', path={str(replica_path)!r})\n"
    )
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic_dir / "models.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "orders = ms.entity(\n"
        "    name='orders',\n"
        "    datasource=ms.ref.datasource('warehouse'),\n"
        "    source=md.table('orders', columns={\n"
        "        'query_id': 'query_id',\n"
        "        'self': 'self',\n"
        "        'region': 'region',\n"
        "        'log_date': 'log_date',\n"
        "        'log_hour': 'log_hour',\n"
        "        'amount': 'amount',\n"
        "        'uncommon_date': 'uncommon_date',\n"
        "        'epoch_like': 'epoch_like',\n"
        "    }),\n"
        "    primary_key=['query_id'],\n"
        "    ai_context=ms.ai_context(\n"
        "        business_definition='One row per accepted order query.',\n"
        "        guardrails=['Use only accepted order queries.'],\n"
        "    ),\n"
        ")\n"
        "region = ms.dimension_column(\n"
        "    name='region', entity=orders, column='region',\n"
        "    ai_context=ms.ai_context(business_definition='Order region.'),\n"
        ")\n"
        "log_hour = ms.dimension_column(\n"
        "    name='log_hour', entity=orders, column='log_hour',\n"
        "    ai_context=ms.ai_context(business_definition='UTC log hour component.'),\n"
        ")\n"
        "log_date = ms.time_dimension_column(\n"
        "    name='log_date', entity=orders, column='log_date', \n"
        "    parse=ms.strptime('%Y%m%d'), is_default=True,\n"
        "    ai_context=ms.ai_context(business_definition='UTC order log date.'),\n"
        ")\n"
        "amount = ms.measure_column(\n"
        "    name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='USD',\n"
        "    ai_context=ms.ai_context(business_definition='Accepted order amount in USD.'),\n"
        ")\n"
    )
    # Sorted after models.py, so physical declarations appended there are
    # captured before this file finalizes and publishes the Metric.
    (semantic_dir / "revenue.py").write_text(
        "import marivo.semantic as ms\n\n"
        "from .models import amount as amount_ref\n\n"
        ""
        "amount = amount_ref\n\n\n"
        "@ms.metric(\n"
        "    ai_context=ms.ai_context(\n"
        "        business_definition='Sum of accepted order amounts.',\n"
        "        guardrails=['Do not mix currencies.'],\n"
        "    ),\n"
        ")\n"
        "def revenue():\n"
        "    return amount.sum()\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def semantic_project_factory(tmp_path: Path) -> _SemanticProjectFactory:
    """Create a SemanticProject from a mapping of project-relative files."""

    def _make(
        files: dict[str, str],
        load: bool = True,
        models: list[str] | None = None,
        workspace_dir: Path | None = None,
    ) -> SemanticProject:
        from marivo.semantic.reader import SemanticProject

        effective_dir = workspace_dir if workspace_dir is not None else tmp_path
        # Write project manifest for discovery
        (effective_dir / "marivo.toml").write_text('[project]\nname = "test"\n')
        marivo_root = effective_dir / "models"
        root = marivo_root / "semantic"
        root.mkdir(parents=True, exist_ok=True)
        for rel, src in files.items():
            full = marivo_root / rel if rel.startswith("datasources/") else root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(src)

        declared_datasources: set[str] = set()
        datasource_ref_pattern = re.compile(
            r"(?:"
            r"datasource\s*=\s*(?:ms\.)?ref\.datasource\("
            r"|(?:ms\.)?ref\.datasource\("
            r"|datasource\s*=\s*"
            r")(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)"
        )
        for src in files.values():
            for match in datasource_ref_pattern.finditer(src):
                name = match.group("name")
                if "." not in name:
                    declared_datasources.add(name)
        for datasource_name in declared_datasources:
            datasource_file = marivo_root / "datasources" / f"{datasource_name}.py"
            if datasource_file.exists():
                continue
            datasource_file.parent.mkdir(parents=True, exist_ok=True)
            datasource_file.write_text(
                "import marivo.datasource as md\n"
                f"md.duckdb(name={datasource_name!r}, "
                "path=':memory:')\n"
            )

        project = SemanticProject(workspace_dir=effective_dir)
        if load:
            project.load(domains=models)
        return project

    return _make


@pytest.fixture
def acquisition_channel_entry(funnel_session: Any) -> Any:
    from tests.shared_fixtures import catalog_value

    return catalog_value(funnel_session.catalog, "acquisition_channel", kind="dimension")


@pytest.fixture
def plan_tier_entry(funnel_session: Any) -> Any:
    from tests.shared_fixtures import catalog_value

    return catalog_value(funnel_session.catalog, "plan_tier", kind="dimension")


def bootstrap_sales_project(tmp_path: Path, *, with_time: bool = True) -> None:
    """Create a ready semantic project on disk for analysis tests."""
    # Write project manifest for discovery
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    time_dimension = (
        "@ms.time_dimension(entity=orders, is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        f"{time_dimension}"
        "@ms.time_dimension(entity=orders, )\n"
        "def status_at(orders):\n"
        "    return orders.status_at\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def platform(orders):\n"
        "    return orders.platform\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def bucket(orders):\n"
        "    return orders.bucket\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def bucket_start(orders):\n"
        "    return orders.bucket_start\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def department(orders):\n"
        "    return orders.department\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def cluster(orders):\n"
        "    return orders.cluster\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def category(orders):\n"
        "    return orders.category\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def channel(orders):\n"
        "    return orders.channel\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def country(orders):\n"
        "    return orders.country\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def a(orders):\n"
        "    return orders.a\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def b(orders):\n"
        "    return orders.b\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def nonexistent(orders):\n"
        "    return orders.nonexistent\n"
        "\n"
        "@ms.metric(entities=[orders], additivity=ms.additive_all(), "
        "name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


@pytest.fixture
def retained_coordinates_case(analysis_dsl_case_factory: DslCaseFactory) -> DslCase:
    """Preserve the independent 147 total and selected 140/3 fold oracle."""
    import duckdb

    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer WHERE customer_id = 'D'")
        connection.execute("""INSERT INTO "order" VALUES
            ('a1', 'A', 'web', 'paid', '2026-08-10T00:00:00+00:00', 100),
            ('a2', 'A', 'web', 'paid', '2026-08-11T00:00:00+00:00', 20),
            ('b1', 'B', 'app', 'paid', '2026-08-10T00:00:00+00:00', 20),
            ('c1', 'C', 'app', 'paid', '2026-08-10T00:00:00+00:00', 7)""")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\n@ms.metric()\ndef mean_amount():\n    return amount_value.mean(time=time_value)\n"
    )
    ms.load(workspace_dir=case.root)
    return case


@pytest.fixture
def source_trace(monkeypatch: pytest.MonkeyPatch) -> SourceTrace:
    """Capture Ibis source execution calls and execution-owner receipts."""
    from tests.support.source_trace import capture_source

    return capture_source(monkeypatch)


@pytest.fixture(scope="session")
def installed_wheel(tmp_path_factory: pytest.TempPathFactory) -> InstalledWheel:
    """Prepare one candidate wheel without replaying development test matrices."""
    return prepare_wheel(tmp_path_factory.mktemp("installed-wheel"))


@pytest.fixture(
    scope="session", params=("base", "duckdb", "postgres", "mysql", "trino", "clickhouse", "all")
)
def installed_dependency_wheel(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> InstalledWheel:
    """Keep each extra's dependency graph in a separate noneditable environment."""
    extra = str(request.param)
    if extra in ("base", "duckdb"):
        existing: object = request.getfixturevalue(
            "installed_base_wheel" if extra == "base" else "installed_wheel"
        )
        assert isinstance(existing, InstalledWheel)
        return existing
    return prepare_wheel(
        tmp_path_factory.mktemp("installed-" + extra), extras=() if extra == "base" else (extra,)
    )


@pytest.fixture(scope="session")
def installed_base_wheel(tmp_path_factory: pytest.TempPathFactory) -> InstalledWheel:
    """Reuse the core-only environment for isolation and real saved Artifact reads."""
    return prepare_wheel(tmp_path_factory.mktemp("installed-base"), extras=())


@pytest.fixture(scope="session")
def installed_multisource_wheel(installed_wheel: InstalledWheel) -> InstalledWheel:
    """Add native drivers only for explicitly opted-in installed-source checks."""
    wheel = installed_wheel.wheel
    installed_wheel.run(
        "install-native-drivers",
        [
            str(installed_wheel.interpreter),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--constraint",
            str(installed_wheel.work / "constraints.txt"),
            f"{wheel}[all]",
            "psycopg[binary]",
        ],
    )
    installed_wheel.run(
        "native-dependency-check", [str(installed_wheel.interpreter), "-m", "pip", "check"]
    )
    return installed_wheel


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Drop release and runtime tests unless the caller passed ``-m``.

    The default suite is ``not release and not runtime``. That filter used to
    live in ``pytest.ini`` ``addopts``. pytest-testmon refuses affected-test
    selection when ``-m`` is set at all, so ``make check-fast`` would rerun
    the whole suite. ``make runtime-test`` and ``make release-test`` pass
    their own ``-m`` and keep that selection unchanged.
    """
    if config.getoption("markexpr"):
        return
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        external = item.get_closest_marker("release") or item.get_closest_marker("runtime")
        if external is not None:
            dropped.append(item)
        else:
            kept.append(item)
    if not dropped:
        return
    config.hook.pytest_deselected(items=dropped)
    items[:] = kept
