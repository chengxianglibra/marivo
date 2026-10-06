"""Real local boundary checks for the R9.5 independent audit helper."""

import sqlite3
from pathlib import Path

import ibis
import pytest

import marivo
from marivo.analysis.materialization.store import SessionStore
from tests.r95_driver_audit import DriverAudit, Origin, classify, frames, native_audit


@pytest.mark.parametrize("backend", ("duckdb", "sqlite"))
def test_native_driver_records_fixture_sql_once(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with native_audit(monkeypatch, backend) as audit:
        connection = ibis.duckdb.connect() if backend == "duckdb" else ibis.sqlite.connect()
        try:
            connection.raw_sql("SELECT 11 AS witness")
        finally:
            connection.disconnect()
        observed = [item for item in audit.submissions if item.sql == "SELECT 11 AS witness"]
        assert len(observed) == 1
        assert observed[0].category == "test_administration"
        assert observed[0].boundary != "adapters._native_cursor"
        audit.assert_classified()


def test_store_connection_authority_is_separate_from_datasource_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with native_audit(monkeypatch, "sqlite") as audit:
        store = SessionStore._graph_store(tmp_path)
        assert store.db_path.exists()
        with sqlite3.connect(tmp_path / "ordinary.sqlite") as datasource:
            datasource.execute("SELECT 13 AS outside_store")
        observed = [item for item in audit.submissions if item.sql == "SELECT 13 AS outside_store"]
        assert len(observed) == 1 and observed[0].category == "test_administration"
        owned = [item for item in audit.submissions if item.category == "store"]
        assert owned and observed[0].connection not in {item.connection for item in owned}
        audit.assert_classified()


def test_admin_credentials_are_redacted_only_when_native_records_are_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("psycopg")
    from psycopg import sql as pg_sql

    postgres = pg_sql.SQL("ALTER ROLE {} WITH PASSWORD {} NOSUPERUSER").format(
        pg_sql.Identifier("r95_fixture_reader"), pg_sql.Literal("r95-fixture's-credential")
    )
    queries: tuple[tuple[str, object], ...] = (
        ("postgres", postgres),
        ("mysql", "ALTER USER 'reader'@'%' IDENTIFIED BY 'r95-mysql-credential'"),
        (
            "clickhouse",
            "CREATE USER reader IDENTIFIED WITH sha256_password BY 'r95-clickhouse-credential'",
        ),
        ("mysql", "ALTER USER 'reader'@'%' IDENTIFIED BY %s"),
        ("clickhouse", "CREATE USER reader IDENTIFIED BY {password:String}"),
    )
    audit = DriverAudit()
    for backend, query in queries:
        audit.submit(backend, "fixture.native.execute", None, query, lambda: None)
    original = tuple(item.sql for item in audit.submissions)
    assert "r95-fixture''s-credential" in original[0]
    assert all(item.category == "test_administration" for item in audit.submissions)
    monkeypatch.setenv("MARIVO_R95_EVIDENCE_DIR", str(tmp_path))
    audit.save("redaction", {"backend": "postgres"}, {"credential_redaction": True})
    saved = (tmp_path / "redaction.json").read_text()
    assert "r95-fixture" not in saved
    assert "r95-mysql-credential" not in saved
    assert "r95-clickhouse-credential" not in saved
    assert saved.count("[REDACTED]") == 3
    assert "IDENTIFIED BY %s" in saved and "IDENTIFIED BY {password:String}" in saved
    assert tuple(item.sql for item in audit.submissions) == original


@pytest.mark.parametrize("relative_path", ("doctor.py", "telemetry/__init__.py"))
def test_product_modules_cannot_claim_fixture_sql_authority(relative_path: str) -> None:
    namespace: dict[str, object] = {"classify": classify, "frames": frames}
    code = compile(
        'origin = classify("SELECT 17", tuple(frames()), store_connection=False)',
        str(Path(marivo.__file__).resolve().parent / relative_path),
        "exec",
    )
    exec(code, namespace)
    origin = namespace["origin"]
    assert isinstance(origin, Origin) and origin.category == "unknown"
    assert classify("SELECT 17", tuple(frames()), store_connection=False).category == (
        "test_administration"
    )
