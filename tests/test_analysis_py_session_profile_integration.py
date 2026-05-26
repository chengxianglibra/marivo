"""End-to-end datasource integration with mv.session.create / attach."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.analysis_py as mv
from marivo.analysis_py.errors import (
    DatasourceFieldInvalidError,
    DatasourceMissingError,
    NoBackendFactoryError,
)
from marivo.analysis_py.session import attach as session_attach
from tests.conftest import bootstrap_sales_project


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "marivo-home"
    home.mkdir()
    monkeypatch.setenv("MARIVO_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _chdir_and_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()


def _seed(con: ibis.BaseBackend) -> None:
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, amount DOUBLE, created_at DATE)")
    con.raw_sql("INSERT INTO orders VALUES (1, 10.0, DATE '2026-01-01')")


def test_session_uses_datasource_when_no_explicit_backend(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    session = mv.session.create(name="s")
    # Force backend creation via the cache; it should resolve through the project datasource.
    backend = session.backend_cache.get_or_create("warehouse")
    assert backend is not None
    assert backend.list_tables() == []


def test_observe_uses_global_datasource_name(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    db_path = tmp_path / "warehouse.duckdb"
    seeded = ibis.duckdb.connect(str(db_path))
    _seed(seeded)
    seeded.disconnect()
    mv.datasources.set("warehouse", backend_type="duckdb", path=str(db_path))

    session = mv.session.create(name="s")
    frame = mv.observe(mv.MetricRef("sales.revenue"), session=session)

    assert frame.to_pandas().iloc[0, 0] == 10.0
    assert session.known_datasources == {"warehouse"}


def test_model_qualified_datasource_name_is_rejected(tmp_path: Path, fake_home: Path) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        mv.datasources.set("sales.warehouse", backend_type="duckdb", path=":memory:")

    assert exc_info.value.details["field"] == "<name>"


def test_explicit_backend_factory_overrides_datasource(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    mv.datasources.set("warehouse", backend_type="duckdb", path=":memory:")

    sentinel = ibis.duckdb.connect(":memory:")
    _seed(sentinel)

    session = mv.session.create(
        name="s",
        backend_factory=lambda name: sentinel,
    )
    backend = session.backend_cache.get_or_create("warehouse")
    assert backend is sentinel
    assert "orders" in backend.list_tables()


def test_missing_datasource_raises_datasource_missing(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    (tmp_path / ".marivo" / "datasource" / "warehouse.py").unlink()
    session = mv.session.create(name="s")
    with pytest.raises(DatasourceMissingError) as exc_info:
        session.backend_cache.get_or_create("warehouse")
    rendered = str(exc_info.value)
    assert "warehouse" in rendered
    assert "mv.datasources.set" in rendered


def test_use_datasources_false_disables_auto_factory(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    mv.datasources.set("warehouse", backend_type="duckdb", path=":memory:")
    session = mv.session.create(name="s", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        session.backend_cache.get_or_create("warehouse")


def test_audit_project_reports_missing(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    session = mv.session.create(name="s")
    result = mv.datasources.audit_project(session.semantic_project)
    assert result.missing == []
    assert "warehouse" in result.present
