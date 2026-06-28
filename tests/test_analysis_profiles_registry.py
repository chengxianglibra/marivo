"""Public API tests for marivo.datasource manage (registry)."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.analysis.errors import (
    DatasourceFieldInvalidError,
    DatasourceMissingError,
    DatasourcePreviewError,
)
from marivo.datasource import secrets as datasource_secrets
from marivo.datasource.authoring import (
    DatasourceSpec,
    _ClickHouseSpec,
    _DuckDBSpec,
    _MySQLSpec,
    _PostgresSpec,
    _TrinoSpec,
)
from marivo.preview import PreviewResult


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spec(name: str, *, backend_type: str, **fields: object) -> DatasourceSpec:
    if backend_type == "duckdb":
        return _DuckDBSpec(name=name, **fields)
    if backend_type == "trino":
        return _TrinoSpec(name=name, **fields)
    if backend_type == "mysql":
        return _MySQLSpec(name=name, **fields)
    if backend_type == "postgres":
        return _PostgresSpec(name=name, **fields)
    if backend_type == "clickhouse":
        return _ClickHouseSpec(name=name, **fields)
    raise AssertionError(f"unexpected backend_type: {backend_type}")


def test_set_returns_summary(project_root: Path) -> None:
    summary = md.register(_spec("wh", backend_type="duckdb", path=":memory:"))
    assert summary.name == "wh"
    assert summary.backend_type == "duckdb"
    assert (project_root / "models" / "datasources" / "wh.py").is_file()


def test_register_rejects_legacy_name_and_kwargs(project_root: Path) -> None:
    with pytest.raises(TypeError):
        md.register("wh", backend_type="duckdb", path=":memory:")  # type: ignore[call-arg]


def test_set_rejects_model_qualified_name(project_root: Path) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        md.register(_spec("sales.warehouse", backend_type="duckdb", path=":memory:"))
    assert exc_info.value.details["field"] == "<name>"
    assert "global datasource name" in str(exc_info.value)


def test_list_returns_sorted_summaries(project_root: Path) -> None:
    md.register(_spec("b", backend_type="duckdb", path=":memory:"))
    md.register(_spec("a", backend_type="duckdb", path=":memory:"))
    names = [p.name for p in md.list()]
    assert names == ["a", "b"]


def test_describe_redacts_secrets(project_root: Path) -> None:
    md.register(
        _spec(
            "wh",
            backend_type="trino",
            host="trino.example",
            port=8080,
            catalog="hive",
            auth_env="TRINO_AUTH",
        )
    )
    desc = md.describe("wh")
    assert desc.literal_fields == {"host": "trino.example", "port": 8080, "catalog": "hive"}
    assert desc.env_refs == {"auth": "TRINO_AUTH"}


def test_datasource_test_uses_scalar_probe_instead_of_list_tables(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("wh", backend_type="trino", host="trino.example", catalog="hive"))

    class _FakeBackend:
        disconnected = False

        def raw_sql(self, sql: str):
            assert sql == "SELECT 1"
            return object()

        def list_tables(self):
            raise AssertionError("list_tables requires a default schema for Trino")

        def disconnect(self) -> None:
            self.disconnected = True

    backend = _FakeBackend()
    import marivo.datasource.manage as registry_mod

    monkeypatch.setattr(registry_mod, "connect", lambda _name: backend)

    result = md.test("wh")

    assert result.ok is True
    assert result.error is None
    assert backend.disconnected is True


def test_connect_context_manager_yields_backend_and_disconnects(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("wh", backend_type="duckdb", path=":memory:"))

    class _FakeBackend:
        disconnect_calls = 0

        def raw_sql(self, sql: str) -> str:
            assert sql == "SELECT 1"
            return "ok"

        def list_tables(self) -> list[str]:
            return ["orders"]

        def disconnect(self) -> None:
            self.disconnect_calls += 1

    backend = _FakeBackend()
    import marivo.datasource.manage as registry_mod
    from marivo.datasource.backends import BuiltDatasourceBackend

    monkeypatch.setattr(
        registry_mod._backends,
        "build_backend_with_secrets",
        lambda _datasource: BuiltDatasourceBackend(backend=backend, env_sourced_secrets=()),
    )

    connection = md.connect("wh")
    assert connection.backend is backend
    assert connection.list_tables() == ["orders"]

    with connection as con:
        assert con is backend
        assert con.raw_sql("SELECT 1") == "ok"
        assert backend.disconnect_calls == 0

    assert backend.disconnect_calls == 1


def test_connect_context_manager_disconnects_after_error(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("wh", backend_type="duckdb", path=":memory:"))

    class _FakeBackend:
        disconnect_calls = 0

        def disconnect(self) -> None:
            self.disconnect_calls += 1

    backend = _FakeBackend()
    import marivo.datasource.manage as registry_mod
    from marivo.datasource.backends import BuiltDatasourceBackend

    monkeypatch.setattr(
        registry_mod._backends,
        "build_backend_with_secrets",
        lambda _datasource: BuiltDatasourceBackend(backend=backend, env_sourced_secrets=()),
    )

    with pytest.raises(RuntimeError, match="boom"), md.connect("wh"):
        raise RuntimeError("boom")

    assert backend.disconnect_calls == 1


def test_connect_manual_disconnect_is_idempotent(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("wh", backend_type="duckdb", path=":memory:"))

    class _FakeBackend:
        disconnect_calls = 0

        def disconnect(self) -> None:
            self.disconnect_calls += 1

    backend = _FakeBackend()
    import marivo.datasource.manage as registry_mod
    from marivo.datasource.backends import BuiltDatasourceBackend

    monkeypatch.setattr(
        registry_mod._backends,
        "build_backend_with_secrets",
        lambda _datasource: BuiltDatasourceBackend(backend=backend, env_sourced_secrets=()),
    )

    connection = md.connect("wh")
    connection.disconnect()
    connection.disconnect()
    with connection:
        pass

    assert backend.disconnect_calls == 1


def test_datasource_test_success_persists_env_sourced_secret(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md.register(
        _spec(
            "wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
            auth_env="TRINO_AUTH",
        )
    )
    monkeypatch.setenv("TRINO_AUTH", "validated-secret")
    persisted: list[tuple[str, str]] = []

    class _FakeBackend:
        def raw_sql(self, sql: str) -> object:
            assert sql == "SELECT 1"
            return object()

        def disconnect(self) -> None:
            return None

    monkeypatch.setattr(
        datasource_secrets,
        "persist_env_sourced",
        lambda resolved: persisted.extend((item.name, item.value) for item in resolved),
    )

    class _FakeTrino:
        @staticmethod
        def connect(**kwargs: object) -> object:
            assert kwargs["auth"] == "validated-secret"
            return _FakeBackend()

    class _FakeIbis:
        trino = _FakeTrino()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())

    result = md.test("wh")

    assert result.ok is True
    assert persisted == [("TRINO_AUTH", "validated-secret")]


def test_datasource_test_failure_does_not_persist_env_sourced_secret(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md.register(
        _spec(
            "wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
            auth_env="TRINO_AUTH",
        )
    )
    monkeypatch.setenv("TRINO_AUTH", "bad-secret")
    persisted: list[tuple[str, str]] = []

    class _FakeBackend:
        def raw_sql(self, sql: str) -> object:
            raise RuntimeError("authentication failed")

        def disconnect(self) -> None:
            return None

    monkeypatch.setattr(
        datasource_secrets,
        "persist_env_sourced",
        lambda resolved: persisted.extend((item.name, item.value) for item in resolved),
    )

    class _FakeTrino:
        @staticmethod
        def connect(**kwargs: object) -> object:
            assert kwargs["auth"] == "bad-secret"
            return _FakeBackend()

    class _FakeIbis:
        trino = _FakeTrino()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())

    result = md.test("wh")

    assert result.ok is False
    assert "authentication failed" in str(result.error)
    assert persisted == []


def test_describe_missing_raises_with_hint(project_root: Path) -> None:
    with pytest.raises(DatasourceMissingError) as exc_info:
        md.describe("nope")
    rendered = str(exc_info.value)
    assert "md.register" in rendered
    assert "'nope'" in rendered


def test_remove_returns_bool(project_root: Path) -> None:
    md.register(_spec("wh", backend_type="duckdb", path=":memory:"))
    assert md.remove("wh") is True
    assert md.remove("wh") is False


def _create_preview_duckdb(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.con.execute(
        "CREATE TABLE orders ("
        "order_id INT, amount DOUBLE, region TEXT, customer_email TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES "
        "(1, 100.0, 'US', 'alice@example.com', '2026-01-01'), "
        "(2, 200.0, 'EU', 'bob@example.com', '2026-01-02'), "
        "(3, 300.0, 'US', 'cara@example.com', '2026-01-03')"
    )
    con.disconnect()


def test_preview_table_returns_bounded_result(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    preview = md.preview(
        "wh",
        table="orders",
        columns=["order_id", "amount"],
        limit=2,
    )

    assert isinstance(preview, PreviewResult)
    assert preview.kind == "datasource_table"
    assert preview.ref == "wh.orders"
    assert preview.columns == ("order_id", "amount")
    assert preview.types == {"order_id": "int32", "amount": "float64"}
    assert preview.rows == ({"order_id": 1, "amount": 100.0}, {"order_id": 2, "amount": 200.0})
    assert preview.requested_limit == 2
    assert preview.returned_row_count == 2
    assert preview.is_truncated is True


def test_preview_table_supports_structured_filter_and_order(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    preview = md.preview(
        "wh",
        table="orders",
        columns=["order_id", "region", "amount"],
        where=[{"column": "region", "op": "=", "value": "US"}],
        order_by=[{"column": "amount", "direction": "desc"}],
        limit=10,
    )

    assert preview.sample_policy.method == "ordered_limit"
    assert preview.sample_policy.filters == ({"column": "region", "op": "=", "value": "US"},)
    assert preview.sample_policy.order_by == ("amount desc",)
    assert preview.rows == (
        {"order_id": 3, "region": "US", "amount": 300.0},
        {"order_id": 1, "region": "US", "amount": 100.0},
    )
    assert preview.is_truncated is False


def test_preview_table_returns_values_without_redaction(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    preview = md.preview(
        "wh",
        table="orders",
        columns=["order_id", "customer_email"],
        limit=1,
    )

    assert preview.rows == ({"order_id": 1, "customer_email": "alice@example.com"},)
    assert [warning.kind for warning in preview.warnings] == []


def test_preview_table_rejects_raw_sql_filter(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    with pytest.raises(DatasourcePreviewError) as exc_info:
        md.preview("wh", table="orders", where=["region = 'US'"])  # type: ignore[list-item]

    assert exc_info.value.details["field"] == "where"
    assert "structured preview filter" in str(exc_info.value)


def test_preview_exports_from_datasources_namespace() -> None:
    assert md.PreviewResult is PreviewResult
