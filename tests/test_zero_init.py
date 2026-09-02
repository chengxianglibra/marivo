"""End-to-end coverage for running Marivo without ``marivo init``."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.errors import SessionStateError
from marivo.datasource.catalog import DatasourceCatalog
from marivo.datasource.errors import DatasourceLoadError
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.check import run_check
from marivo.semantic.errors import SemanticLoadFailed


def test_empty_project_loads_without_creating_authored_or_state_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    datasource_catalog = md.load()
    semantic_catalog = ms.load()

    assert isinstance(datasource_catalog, DatasourceCatalog)
    assert datasource_catalog.list().items == ()
    assert isinstance(semantic_catalog, SemanticCatalog)
    assert semantic_catalog.workspace_dir == tmp_path
    assert list(tmp_path.iterdir()) == []


def test_datasource_authoring_creates_only_required_models_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    md.register(md.duckdb(name="warehouse", path=":memory:"))

    assert (tmp_path / "models" / "datasources" / "warehouse.py").is_file()
    assert not (tmp_path / "marivo.toml").exists()
    assert not (tmp_path / ".marivo").exists()


def test_analysis_session_creates_state_without_manifest_or_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    session = mv.session.get_or_create(name="zero_init", use_datasources=False)

    assert session.project_root == tmp_path
    assert (tmp_path / ".marivo" / "analysis" / "session_store.db").is_file()
    assert not (tmp_path / "marivo.toml").exists()
    assert not (tmp_path / "models").exists()


def test_default_telemetry_creates_only_telemetry_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARIVO_TELEMETRY", raising=False)

    ms.load()

    telemetry_dir = tmp_path / ".marivo" / "telemetry"
    assert telemetry_dir.is_dir()
    assert list(telemetry_dir.glob("events-*.jsonl"))
    assert not (tmp_path / "marivo.toml").exists()
    assert not (tmp_path / "models").exists()


def test_semantic_check_accepts_empty_project_and_rejects_invalid_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    empty = run_check(readiness=True, format="json")

    assert empty["status"] == "ready"
    assert empty["errors"] == []

    (tmp_path / "marivo.toml").write_text("invalid [[ toml", encoding="utf-8")

    invalid = run_check(format="json")

    assert invalid["status"] == "errored"
    assert invalid["errors"]


def test_project_surfaces_fail_before_writing_when_manifest_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text(
        '[project]\nname = ""\n',
        encoding="utf-8",
    )

    with pytest.raises(DatasourceLoadError, match="name must be a non-empty string") as datasource:
        md.load()
    assert datasource.value.location == str(tmp_path / "marivo.toml")
    assert datasource.value.repair is not None
    assert datasource.value.repair.help_target.canonical_id == "load"
    with pytest.raises(SemanticLoadFailed, match="name must be a non-empty string"):
        ms.load()
    with pytest.raises(SessionStateError, match="name must be a non-empty string") as analysis:
        mv.session.get_or_create(name="invalid", use_datasources=False)
    assert analysis.value.location == str(tmp_path / "marivo.toml")
    assert analysis.value.repair is not None
    assert analysis.value.repair.help_target.canonical_id == "runtime.sessions"

    assert not (tmp_path / "models").exists()
    assert not (tmp_path / ".marivo").exists()
