"""Fresh-process checks for core APIs, selected drivers and installed resources."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.metadata
import importlib.util
import inspect
import json
import os
import sqlite3
import sys
from enum import Enum
from pathlib import Path

DRIVERS = {
    "duckdb": "duckdb",
    "postgres": "psycopg",
    "mysql": "MySQLdb",
    "trino": "trino",
    "clickhouse": "clickhouse_connect",
}


def public_snapshot() -> dict[str, object]:
    """Compare native authoring discovery and signatures with the source owner."""
    import marivo.datasource as md
    import marivo.ontology as mo
    import marivo.semantic as ms
    from marivo._help.render import render_help_text
    from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY
    from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
    from tests.surface.exports import DATASOURCE_PUBLIC, SEMANTIC_PUBLIC

    result: dict[str, object] = {}

    def help_text(target: str | None) -> str:
        import marivo

        return (
            render_help_text(target)[0]
            .replace("Python: " + sys.executable, "Python: <interpreter>")
            .replace("Package: " + marivo.__file__, "Package: <product module>")
        )

    for owner, module, expected, targets in (
        ("datasource", md, DATASOURCE_PUBLIC, DATASOURCE_REGISTRY.canonical_ids()),
        ("semantic", ms, SEMANTIC_PUBLIC, SEMANTIC_REGISTRY.canonical_ids()),
    ):
        assert set(module.__all__) == set(expected)
        signatures: dict[str, str] = {}
        enumerations: dict[str, dict[str, str]] = {}
        for name in sorted(expected):
            value: object = getattr(module, name)
            if inspect.isclass(value) and issubclass(value, Enum):
                enumerations[name] = {
                    key: str(item.value) for key, item in value.__members__.items()
                }
            elif callable(value):
                signatures[name] = str(inspect.signature(value))
        result[owner] = {
            "signatures": signatures,
            "enumerations": enumerations,
            "help": {target: help_text(owner + "." + target) for target in targets},
        }
    assert callable(mo.load)
    result["roots"] = {
        target or "root": help_text(target)
        for target in (None, "authoring", "analysis", "datasource", "semantic", "ontology")
    }
    return result


def dependencies(extra: str) -> dict[str, object]:
    """Validate core imports without importing any unselected native driver."""
    import pyarrow

    import marivo.analysis as mv
    import marivo.datasource as md
    import marivo.semantic as ms
    from marivo.datasource.engines import SUPPORTED_BACKEND_TYPES, require_profile_for_backend_type

    assert importlib.metadata.version("sqlglot") == "30.8.0"
    assert callable(md.register) and callable(ms.entity) and callable(mv.session.resume)
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
        if backend != extra
    }
    assert all(absent.values()), absent
    selected = None
    local_rows: tuple[dict[str, object], ...] | None = None
    if extra != "base":
        selected = importlib.import_module("ibis.backends." + extra).__name__
        if extra == "duckdb":
            import ibis

            connection = ibis.duckdb.connect(":memory:")
            try:
                table = connection.create_table("cleanup_probe", ibis.memtable({"value": [1, 2]}))
                assert table.execute()["value"].tolist() == [1, 2]
                connection.drop_table("cleanup_probe")
                assert "cleanup_probe" not in connection.list_tables()
            finally:
                connection.disconnect()
        if extra in ("duckdb", "sqlite"):
            project = Path.cwd() / ("native-" + extra)
            project.mkdir()
            os.environ["MARIVO_PROJECT_ROOT"] = str(project)
            database = project / ("source." + extra)
            statements = (
                "CREATE TABLE measurements (value INTEGER)",
                "INSERT INTO measurements VALUES (1), (2)",
            )
            if extra == "duckdb":
                import duckdb

                with duckdb.connect(str(database)) as connection:
                    for statement in statements:
                        connection.execute(statement)
            else:
                with sqlite3.connect(database) as sqlite_connection:
                    for statement in statements:
                        sqlite_connection.execute(statement)
                sqlite_connection.close()
            spec = (
                md.duckdb(name="local", path=str(database))
                if extra == "duckdb"
                else md.sqlite(name="local", path=str(database))
            )
            reference = md.register(spec)
            assert md.test(reference.name).ok
            result = md.raw_sql(
                ms.ref.datasource(reference.name),
                "SELECT value FROM measurements ORDER BY value",
                reason="verify isolated local read path",
            )
            assert result.rows == ({"value": 1}, {"value": 2}) and not result.is_truncated
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
        self.module = "ibis.backends." + backend

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname == self.module or fullname.startswith(self.module + "."):
            raise ModuleNotFoundError("selected backend unavailable", name=self.module)


def missing_driver(backend: str) -> dict[str, object]:
    """A missing selected backend produces the public typed installation repair."""
    import marivo.datasource as md

    project = Path.cwd() / ("missing-" + backend)
    project.mkdir()
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    os.environ["MARIVO_BOUNDARY_USER"] = "boundary"
    specs: dict[
        str,
        md.DuckDBSpec
        | md.SQLiteSpec
        | md.PostgresSpec
        | md.MySQLSpec
        | md.TrinoSpec
        | md.ClickHouseSpec,
    ] = {
        "duckdb": md.duckdb(name="missing", path=":memory:"),
        "sqlite": md.sqlite(name="missing", path=":memory:"),
        "postgres": md.postgres(name="missing", host="127.0.0.1", database="unused"),
        "mysql": md.mysql(name="missing", host="127.0.0.1", database="unused"),
        "trino": md.trino(
            name="missing",
            host="127.0.0.1",
            catalog="unused",
            schema="unused",
            user_env="MARIVO_BOUNDARY_USER",
        ),
        "clickhouse": md.clickhouse(name="missing", host="127.0.0.1", database="unused"),
    }
    reference = md.register(specs[backend])
    sys.meta_path.insert(0, MissingBackend(backend))
    result = md.test(reference.name)
    assert not result.ok and result.failure is not None and result.repair is not None
    assert result.failure.exception_type == "DatasourceConnectionError", result.failure
    assert f"marivo[{backend}]" in result.repair.action
    assert "installed optional dependencies" in result.failure.message
    assert "Received:" in result.failure.message
    return {"backend": backend, "failure": result.failure.message, "repair": result.repair.action}


def saved_read(project: Path) -> dict[str, object]:
    """Read a real producer Artifact without installing any database driver."""
    import marivo.analysis as mv
    from tests.support.json import checked, read

    assert not (project / "warehouse.duckdb").exists() and not (project / "models").exists()
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    state = read(project / "journey.json")
    session_id, artifact_ref = state["session"], state["artifact"]
    assert isinstance(session_id, str) and isinstance(artifact_ref, str)
    session = mv.session.resume(session_id, by="id")
    artifact = session.artifact(artifact_ref)
    assert isinstance(artifact, mv.MaterializedNumericRelation)
    artifact.show(n=0)
    assert not any(
        name == root or name.startswith(root + ".")
        for root in DRIVERS.values()
        for name in sys.modules
    )
    return {
        "session": session.id,
        "rows": checked(json.loads(artifact.to_pandas().to_json(orient="table", index=False))),
    }


if __name__ == "__main__":
    from tests.packaging.wheel_probe import assert_installed_origin

    assert_installed_origin()
    mode, argument, destination = sys.argv[1:]
    report = (
        public_snapshot()
        if mode == "surface"
        else dependencies(argument)
        if mode == "dependencies"
        else saved_read(Path(argument))
        if mode == "saved"
        else missing_driver(argument)
    )
    report["origin"] = assert_installed_origin()
    Path(destination).write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
