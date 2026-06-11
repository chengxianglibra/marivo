"""Tests for the unified md management surface."""

from pathlib import Path

import pytest

import marivo.datasource as md


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".marivo").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    return tmp_path


def _register_duckdb(name: str, path: str | None = None) -> None:
    md.register(md.DatasourceSpec(name=name, backend_type="duckdb", path=path or ":memory:"))


def test_register_list_describe_remove(project_root: Path) -> None:
    db_path = str(project_root / "t.duckdb")
    _register_duckdb("tiny", path=db_path)
    summaries = md.list()
    assert [s.name for s in summaries] == ["tiny"]
    assert summaries[0].backend_type == "duckdb"
    assert summaries[0].semantic_id == "tiny"
    described = md.describe("tiny")
    assert described.literal_fields["path"] == db_path
    assert md.remove("tiny") is True
    assert md.list() == []


def test_connect_returns_live_backend(project_root: Path) -> None:
    db_path = str(project_root / "t.duckdb")
    _register_duckdb("tiny", path=db_path)
    backend = md.connect("tiny")
    try:
        assert backend.raw_sql("SELECT 1") is not None
    finally:
        backend.disconnect()


def test_preview_disconnects_backend(project_root: Path, monkeypatch) -> None:
    import marivo.datasource.manage as manage

    db_path = str(project_root / "t.duckdb")
    _register_duckdb("tiny", path=db_path)
    seed = md.connect("tiny")
    seed.raw_sql("CREATE TABLE t AS SELECT 1 AS a")
    seed.disconnect()

    closed: list[bool] = []
    real_connect = manage.connect

    def tracking_connect(name: str):
        backend = real_connect(name)
        real_disconnect = backend.disconnect

        def spy_disconnect() -> None:
            closed.append(True)
            real_disconnect()

        monkeypatch.setattr(backend, "disconnect", spy_disconnect, raising=False)
        return backend

    monkeypatch.setattr(manage, "connect", tracking_connect)
    result = manage.preview("tiny", table="t")
    assert result.returned_row_count == 1
    assert closed == [True]
