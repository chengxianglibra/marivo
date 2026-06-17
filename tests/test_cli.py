"""Tests for marivo.cli — the marivo init command."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from marivo.cli import init_project, main

# ---------------------------------------------------------------------------
# init_project creates all artifacts
# ---------------------------------------------------------------------------


def test_creates_marivo_toml(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    assert (tmp_path / "marivo.toml").is_file()


def test_creates_marivo_toml_with_project_name(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == tmp_path.name


def test_creates_models_dir(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    assert (tmp_path / "models").is_dir()


def test_creates_dot_marivo_dir(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    assert (tmp_path / ".marivo").is_dir()


def test_installs_claude_semantic_skill(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    link = tmp_path / ".claude" / "skills" / "marivo-semantic"
    assert link.is_symlink() or link.is_dir()
    assert (link / "SKILL.md").is_file()


def test_installs_codex_semantic_skill(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    link = tmp_path / ".codex" / "skills" / "marivo-semantic"
    assert link.is_symlink() or link.is_dir()
    assert (link / "SKILL.md").is_file()


def test_installs_claude_analysis_skill(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    link = tmp_path / ".claude" / "skills" / "marivo-analysis"
    assert link.is_symlink() or link.is_dir()
    assert (link / "SKILL.md").is_file()


def test_installs_codex_analysis_skill(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    link = tmp_path / ".codex" / "skills" / "marivo-analysis"
    assert link.is_symlink() or link.is_dir()
    assert (link / "SKILL.md").is_file()


def test_prints_initialized_header(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert f"Initializing Marivo project in {tmp_path}" in captured.out


# ---------------------------------------------------------------------------
# Symlinks resolve to the installed package's skill directories
# ---------------------------------------------------------------------------


def test_semantic_symlink_resolves_to_package(tmp_path: Path) -> None:
    init_project(project_dir=tmp_path)
    import marivo.skills

    skills_src = Path(marivo.skills.__file__).parent
    link = tmp_path / ".claude" / "skills" / "marivo-semantic"
    assert link.resolve() == (skills_src / "marivo-semantic").resolve()


# ---------------------------------------------------------------------------
# init_project warns but continues when artifacts exist (no --force)
# ---------------------------------------------------------------------------


def test_warns_if_marivo_toml_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "x"\n')
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert "marivo.toml already exists" in captured.err


def test_warns_if_models_dir_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "models").mkdir()
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert "models/ already exists" in captured.err


def test_warns_if_dot_marivo_dir_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".marivo").mkdir()
    init_project(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert ".marivo/ already exists" in captured.err


def test_does_not_overwrite_existing_marivo_toml(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "x"\n')
    init_project(project_dir=tmp_path)
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == "x"


def test_warns_but_creates_missing_artifacts(tmp_path: Path) -> None:
    """When some artifacts exist, missing ones are still created."""
    (tmp_path / "marivo.toml").write_text('[project]\nname = "x"\n')
    init_project(project_dir=tmp_path)
    # marivo.toml was skipped (already exists), but models/ and .marivo/ were created
    assert (tmp_path / "models").is_dir()
    assert (tmp_path / ".marivo").is_dir()


# ---------------------------------------------------------------------------
# init_project with force=True replaces existing artifacts
# ---------------------------------------------------------------------------


def test_force_overwrites_marivo_toml(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "old"\n')
    init_project(force=True, project_dir=tmp_path)
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == tmp_path.name


def test_force_overwrites_models_dir(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    init_project(force=True, project_dir=tmp_path)
    assert (tmp_path / "models").is_dir()


def test_force_removes_skill_symlinks(tmp_path: Path) -> None:
    # First init to create symlinks
    init_project(project_dir=tmp_path)
    # Second init with force should succeed
    init_project(force=True, project_dir=tmp_path)
    assert (tmp_path / ".claude" / "skills" / "marivo-semantic").is_symlink()


# ---------------------------------------------------------------------------
# --force deletes and recreates non-empty .marivo/
# ---------------------------------------------------------------------------


def test_force_deletes_nonempty_dot_marivo(tmp_path: Path) -> None:
    (tmp_path / ".marivo").mkdir()
    (tmp_path / ".marivo" / "analysis").mkdir()
    (tmp_path / ".marivo" / "analysis" / "session.json").write_text("{}")
    init_project(force=True, project_dir=tmp_path)
    # .marivo/ was deleted and recreated, so the old file is gone
    assert not (tmp_path / ".marivo" / "analysis" / "session.json").exists()
    assert (tmp_path / ".marivo").is_dir()


# ---------------------------------------------------------------------------
# --force overwrites invalid TOML
# ---------------------------------------------------------------------------


def test_force_overwrites_invalid_toml(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text("this is not valid [[toml")
    init_project(force=True, project_dir=tmp_path)
    # marivo.toml should now be valid and contain the project name
    with open(tmp_path / "marivo.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == tmp_path.name


# ---------------------------------------------------------------------------
# Invalid TOML without --force still errors
# ---------------------------------------------------------------------------


def test_rejects_invalid_toml_without_force(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text("this is not valid [[toml")
    with pytest.raises(SystemExit) as exc_info:
        init_project(project_dir=tmp_path)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# No subcommand prints help and exits 0
# ---------------------------------------------------------------------------


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Marivo" in captured.out or "marivo" in captured.out
