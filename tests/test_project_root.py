"""Tests for the shared project-root resolution kernel."""

from pathlib import Path

from marivo.project import resolve_project_root


def test_env_var_wins_over_cwd(tmp_path: Path, monkeypatch) -> None:
    env_root = tmp_path / "env_root"
    (env_root / ".marivo").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(env_root))
    assert resolve_project_root() == env_root


def test_explicit_start_wins_over_env(tmp_path: Path, monkeypatch) -> None:
    env_root = tmp_path / "env_root"
    (env_root / ".marivo").mkdir(parents=True)
    explicit = tmp_path / "explicit"
    (explicit / ".marivo").mkdir(parents=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(env_root))
    assert resolve_project_root(start=explicit) == explicit


def test_walks_up_to_dotmarivo_ancestor(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".marivo").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(nested)
    assert resolve_project_root() == tmp_path


def test_falls_back_to_cwd_without_dotmarivo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert resolve_project_root() == tmp_path
