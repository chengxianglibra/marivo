"""End-to-end profile integration with mv.session.create / attach."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.analysis_py as mv
from marivo.analysis_py.errors import (
    NoBackendFactoryError,
    ProfileMissingError,
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


def test_session_uses_profile_when_no_explicit_backend(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    mv.profiles.set("warehouse", backend_type="duckdb", path=":memory:")
    session = mv.session.create(name="s")
    # Force backend creation via the cache; it should resolve through the profile.
    backend = session.backend_cache.get_or_create("warehouse")
    assert backend is not None
    assert backend.list_tables() == []


def test_explicit_backend_factory_overrides_profile(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    mv.profiles.set("warehouse", backend_type="duckdb", path=":memory:")

    sentinel = ibis.duckdb.connect(":memory:")
    _seed(sentinel)

    session = mv.session.create(
        name="s",
        backend_factory=lambda name: sentinel,
    )
    backend = session.backend_cache.get_or_create("warehouse")
    assert backend is sentinel
    assert "orders" in backend.list_tables()


def test_missing_profile_raises_profile_missing(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    session = mv.session.create(name="s")
    with pytest.raises(ProfileMissingError) as exc_info:
        session.backend_cache.get_or_create("warehouse")
    rendered = str(exc_info.value)
    assert "warehouse" in rendered
    assert "mv.profiles.set" in rendered


def test_use_profiles_false_disables_auto_factory(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    mv.profiles.set("warehouse", backend_type="duckdb", path=":memory:")
    session = mv.session.create(name="s", use_profiles=False)
    with pytest.raises(NoBackendFactoryError):
        session.backend_cache.get_or_create("warehouse")


def test_audit_project_reports_missing(tmp_path: Path, fake_home: Path) -> None:
    bootstrap_sales_project(tmp_path)
    session = mv.session.create(name="s")
    result = mv.profiles.audit_project(session.semantic_project)
    assert "warehouse" in result.missing
    assert result.present == []

    mv.profiles.set("warehouse", backend_type="duckdb", path=":memory:")
    result_after = mv.profiles.audit_project(session.semantic_project)
    assert result_after.missing == []
    assert "warehouse" in result_after.present
