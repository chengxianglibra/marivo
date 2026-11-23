"""Exercise optional drivers and captured reads after bootstrap has installed import guards."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.metadata
import importlib.util
import os
import sys
from pathlib import Path

import ibis
import pyarrow
from typing_extensions import override

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.engines import SUPPORTED_BACKEND_TYPES, require_profile_for_backend_type
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.support.json import obj, read

DRIVERS = {
    "duckdb": "duckdb",
    "postgres": "psycopg",
    "mysql": "pymysql",
    "trino": "trino",
    "clickhouse": "clickhouse_connect",
}


def dependencies(extra: str) -> dict[str, object]:
    """Validate core imports without importing any unselected native driver."""
    assert importlib.metadata.version("sqlglot") == "30.8.0"
    assert callable(md.register)
    assert callable(ms.entity)
    assert callable(mv.session.resume)
    for backend in SUPPORTED_BACKEND_TYPES:
        assert require_profile_for_backend_type(backend).name == backend
    assert not any(
        name == root or name.startswith(root + ".")
        for root in DRIVERS.values()
        for name in sys.modules
    ), "core import or provider discovery loaded an optional driver"
    absent = {
        backend: importlib.util.find_spec(root) is None
        for backend, root in DRIVERS.items()
        if backend != extra and extra != "all"
    }
    assert all(absent.values()), absent
    installed = {
        distribution.metadata["Name"].lower() for distribution in importlib.metadata.distributions()
    }
    assert "mysqlclient" not in installed
    selected = None
    local_rows: tuple[dict[str, object], ...] | None = None
    if extra != "base":
        # The all-extra case owns MySQL dependency removal; other backends retain
        # their independent system-library prerequisites and execution checks.
        selected_backends = ("mysql",) if extra == "all" else (extra,)
        selected = ",".join(
            importlib.import_module(
                "marivo.datasource.engines._mysql_backend"
                if backend == "mysql"
                else "ibis.backends." + backend
            ).__name__
            for backend in selected_backends
        )
        if extra == "duckdb":
            connection = ibis.duckdb.connect(":memory:")
            try:
                table = connection.create_table("cleanup_probe", ibis.memtable({"value": [1, 2]}))
                assert table.execute()["value"].tolist() == [1, 2]
                connection.drop_table("cleanup_probe")
                assert "cleanup_probe" not in connection.list_tables()
            finally:
                connection.disconnect()
        if extra == "duckdb":
            project = Path.cwd() / ("native-" + extra)
            project.mkdir()
            os.environ["MARIVO_PROJECT_ROOT"] = str(project)
            database = project / ("source." + extra)
            statements = (
                "CREATE TABLE measurements (value INTEGER)",
                "INSERT INTO measurements VALUES (1), (2)",
            )
            import duckdb  # noqa: PLC0415 - This optional driver is absent from the other wheel environments.

            # Seed the independent native fixture before opening it through Marivo.
            with duckdb.connect(str(database)) as connection:  # noqa: TID251
                for statement in statements:
                    connection.execute(statement)
            spec = md.duckdb(name="local", path=str(database))
            reference = md.register(spec)
            assert md.load().test(reference.name).ok
            result = md.raw_sql(
                ms.ref.datasource(reference.name),
                "SELECT value FROM measurements ORDER BY value",
                reason="verify isolated local read path",
            )
            assert result.rows == ({"value": 1}, {"value": 2})
            assert result.returned_row_count == 2
            local_rows = result.rows
    return {
        "python": sys.version,
        "extra": extra,
        "arrow": pyarrow.__version__,
        "selected_driver": selected,
        "local_rows": local_rows,
        "unselected_drivers_absent": absent,
        "dependencies": {
            item.metadata["Name"]: item.version for item in importlib.metadata.distributions()
        },
    }


class MissingBackend(importlib.abc.MetaPathFinder):
    """Remove one selected Ibis backend from this process before connection."""

    def __init__(self, backend: str) -> None:
        self.module = DRIVERS[backend]

    @override
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname == self.module or fullname.startswith(self.module + "."):
            raise ModuleNotFoundError("selected backend unavailable", name=self.module)


def missing_driver(backend: str) -> dict[str, object]:
    """A missing selected backend produces the public typed installation repair."""
    project = Path.cwd() / ("missing-" + backend)
    project.mkdir()
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    os.environ["MARIVO_BOUNDARY_USER"] = "boundary"
    specs: dict[
        str,
        md.DuckDBSpec | md.PostgresSpec | md.MySQLSpec | md.TrinoSpec | md.ClickHouseSpec,
    ] = {
        "duckdb": md.duckdb(name="missing", path=":memory:"),
        "postgres": md.postgres(name="missing", host="127.0.0.1", database="unused"),
        "mysql": md.mysql(name="missing", host="127.0.0.1", database="unused"),
        "trino": md.trino(
            name="missing",
            host="127.0.0.1",
            catalog="unused",
            schema="unused",
            user=md.env("MARIVO_BOUNDARY_USER"),
        ),
        "clickhouse": md.clickhouse(name="missing", host="127.0.0.1", database="unused"),
    }
    reference = md.register(specs[backend])
    sys.meta_path.insert(0, MissingBackend(backend))
    result = md.load().test(reference.name)
    assert not result.ok and result.failure is not None and result.repair is not None
    assert result.failure.exception_type == "DatasourceConnectionError", result.failure
    return {"backend": backend, "failure": result.failure.message, "repair": result.repair.action}


def saved_read(project: Path) -> dict[str, object]:
    """Read a real producer Artifact without installing any database driver."""
    assert not (project / "warehouse.duckdb").exists() and not (project / "models").exists()
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    state = read(project / "journey.json")
    session_id, artifact_ref = state["session"], obj(state["artifacts"])["value"]
    assert isinstance(session_id, str) and isinstance(artifact_ref, str)
    session = mv.session.resume(session_id)

    def run_count() -> int:
        local = session._runtime.local
        with local.store.read_transaction() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM analysis_action_runs WHERE session_ref=?",
                (local.session_ref,),
            ).fetchone()
        assert row is not None
        return int(row[0])

    artifact = session.persisted_frame(artifact_ref)
    assert isinstance(artifact, mv.AnalysisFrame)
    before = run_count()
    receipt = snapshot(artifact)
    assert receipt == obj(state["snapshots"])["value"]
    assert run_count() == before
    assert session._runtime.store.resources(session.id) == ()
    artifact.show()
    artifact.show()
    assert not any(
        name == root or name.startswith(root + ".")
        for root in DRIVERS.values()
        for name in sys.modules
    )
    return {
        "session": session.id,
        "rows": receipt["rows"],
        "new_runs": 0,
    }
