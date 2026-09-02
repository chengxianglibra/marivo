"""Tests for shared project-root resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.project import resolve_project_root


def test_omitted_root_uses_environment_before_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_root = tmp_path / "manifest"
    environment_root = tmp_path / "environment"
    child = manifest_root / "nested"
    child.mkdir(parents=True)
    environment_root.mkdir()
    (manifest_root / "marivo.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(child)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(environment_root))

    assert resolve_project_root() == environment_root.resolve()
    assert md.load().workspace_dir == environment_root.resolve()
    assert ms.load().workspace_dir == environment_root.resolve()


def test_omitted_root_uses_nearest_manifest_then_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = tmp_path / "nested" / "deep"
    child.mkdir(parents=True)
    (tmp_path / "marivo.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(child)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)

    assert md.load().workspace_dir == tmp_path.resolve()
    assert ms.load().workspace_dir == tmp_path.resolve()

    (tmp_path / "marivo.toml").unlink()

    assert md.load().workspace_dir == child.resolve()
    assert ms.load().workspace_dir == child.resolve()


def test_explicit_workspace_is_exact_and_does_not_search_ancestors(tmp_path: Path) -> None:
    child = tmp_path / "nested"
    child.mkdir()
    (tmp_path / "marivo.toml").write_text("", encoding="utf-8")

    assert md.load(workspace_dir=child).workspace_dir == child.resolve()
    assert ms.load(workspace_dir=child).workspace_dir == child.resolve()
