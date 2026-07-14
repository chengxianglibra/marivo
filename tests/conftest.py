"""Shared fixtures for the Python-native Marivo test suite."""

from __future__ import annotations

import re

import ibis
import pytest

from tests.shared_fixtures import authoring_evidence_template, sales_orders_template

# Cap DuckDB to a single thread per connection. DuckDB defaults to
# hardware_concurrency() threads; with one pytest-xdist worker per CPU that
# oversubscribes cores (N workers x N threads) and inflates test wall time
# roughly 10x via thread thrash. One thread per connection keeps the suite
# CPU-bound and parallelizable across workers. Applied at import so every
# ibis.duckdb.connect call site (marivo backends + tests) is covered before
# any test runs.
_original_duckdb_connect = ibis.duckdb.connect


def _duckdb_connect_single_thread(*args: object, **kwargs: object) -> object:
    backend = _original_duckdb_connect(*args, **kwargs)
    backend.raw_sql("SET threads=1")
    return backend


ibis.duckdb.connect = _duckdb_connect_single_thread


@pytest.fixture(autouse=True)
def _reset_analysis_session_process_state():
    from marivo.analysis.session._runtime import reset_process_state

    reset_process_state()
    yield
    reset_process_state()


@pytest.fixture
def installer_env(tmp_path):
    """Shared fixture for the Marivo installer black-box tests.

    Builds a fake ``bin`` directory of shimmed tools (uname, unsupported
    pythons) and a sanitized environment. Lives in ``conftest`` so the two
    split installer test modules can use it without importing it (which would
    collide with the parameter name under ruff F811). The python/uv shims are
    added per-test by the modules themselves.
    """
    import os

    from tests.install_marivo_helpers import _fake_uname, _fake_unsupported_python

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    log.touch()
    _fake_uname(bin_dir)
    for name in ("python3.14", "python3.13", "python3.12"):
        _fake_unsupported_python(bin_dir, name)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{os.defpath}",
            "FAKE_LOG": str(log),
            "FAKE_UNAME": "Linux",
            "HOME": str(tmp_path / "home"),
        }
    )
    return bin_dir, env


@pytest.fixture(scope="session")
def _sales_orders_template_path():
    """Session-scoped: ensure the DuckDB template is built once per worker."""
    return sales_orders_template()


@pytest.fixture
def authoring_evidence_project(tmp_path, monkeypatch):
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
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic_dir / "models.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "orders = ms.entity(\n"
        "    name='orders',\n"
        "    datasource=md.ref('datasource.warehouse'),\n"
        "    source=md.table('orders'),\n"
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
        "    name='log_date', entity=orders, column='log_date', granularity='day',\n"
        "    parse=ms.strptime('%Y%m%d'), is_default=True,\n"
        "    ai_context=ms.ai_context(business_definition='UTC order log date.'),\n"
        ")\n"
        "amount = ms.measure_column(\n"
        "    name='amount', entity=orders, column='amount', additivity='additive', unit='USD',\n"
        "    ai_context=ms.ai_context(business_definition='Accepted order amount in USD.'),\n"
        ")\n"
        "revenue = ms.aggregate(\n"
        "    name='revenue', measure=amount, agg='sum', unit='USD',\n"
        "    ai_context=ms.ai_context(\n"
        "        business_definition='Sum of accepted order amounts.',\n"
        "        guardrails=['Do not mix currencies.'],\n"
        "    ),\n"
        ")\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def semantic_project_factory(tmp_path):
    """Create a SemanticProject from a mapping of project-relative files."""

    def _make(
        files: dict[str, str],
        load: bool = True,
        models: list[str] | None = None,
        workspace_dir=None,
    ):
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
        for src in files.values():
            for match in re.finditer(
                r"(?:datasource=|md\.ref\()(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)",
                src,
            ):
                name = match.group("name")
                if name.startswith("datasource."):
                    declared_datasources.add(name.removeprefix("datasource."))
                elif "." not in name:
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


def bootstrap_sales_project(tmp_path, *, with_time: bool = True) -> None:
    """Create a ready semantic project on disk for analysis tests."""
    # Write project manifest for discovery
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
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
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        f"{time_dimension}"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', "
        "name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
