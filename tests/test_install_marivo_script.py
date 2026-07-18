"""Black-box tests for the Marivo Bash installer lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install_marivo_helpers import (
    InstallerEnv,
    _run_installer,
)

pytestmark = pytest.mark.release


def test_rejects_unknown_argument_before_mutation(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    _, env = installer_env

    completed = _run_installer(tmp_path, env, "--force")

    assert completed.returncode != 0
    assert "unknown argument: --force" in completed.stderr
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("platform", ["MINGW64_NT-10.0", "MSYS_NT-10.0", "CYGWIN_NT-10.0"])
def test_rejects_native_windows_bash_platforms(
    tmp_path: Path,
    installer_env: InstallerEnv,
    platform: str,
) -> None:
    _, env = installer_env
    env["FAKE_UNAME"] = platform

    completed = _run_installer(tmp_path, env)

    assert completed.returncode != 0
    assert "Use Windows Subsystem for Linux (WSL)" in completed.stderr


def test_refuses_noninteractive_replacement_without_yes(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    _, env = installer_env
    (tmp_path / ".venv").mkdir()

    completed = _run_installer(tmp_path, env)

    assert completed.returncode != 0
    assert "rerun with --yes" in completed.stderr
    assert (tmp_path / ".venv").exists()


def test_yes_replaces_broken_venv(tmp_path: Path, installer_env: InstallerEnv) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python312)
    (tmp_path / ".venv").mkdir()
    marker = tmp_path / ".venv" / "broken"
    marker.touch()

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0
    assert not marker.exists()


def test_reuses_valid_python_venv(tmp_path: Path, installer_env: InstallerEnv) -> None:
    toolchain, env = installer_env
    source = toolchain.python312
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(source)

    completed = _run_installer(tmp_path, env)

    assert completed.returncode == 0
    assert "Reusing valid virtual environment" in completed.stdout


def test_selects_versioned_local_python_newer_than_minimum(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python311, toolchain.python313)

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert f"python:{toolchain.python313}:-m venv" in log


def test_installs_marivo_all_with_venv_python_and_initializes_target(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python312)

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    expected_python = tmp_path / ".venv" / "bin" / "python"
    assert f"python:{expected_python}:-m pip install --upgrade pip" in log
    assert f"python:{expected_python}:-m pip install --upgrade marivo[all]" in log
    assert f"marivo:{tmp_path}:init" in log
    assert (tmp_path / "marivo.toml").is_file()
    assert (tmp_path / "models").is_dir()
    assert (tmp_path / ".marivo").is_dir()
    assert "warning: optional init artifact is missing" in completed.stderr


def test_stops_before_init_when_marivo_installation_fails(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python312)
    env["FAKE_PIP_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert ":init" not in log
    assert not (tmp_path / "marivo.toml").exists()


def test_init_failure_preserves_installed_venv(tmp_path: Path, installer_env: InstallerEnv) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python312)
    env["FAKE_INIT_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    assert (tmp_path / ".venv" / "bin" / "python").is_file()
    assert 'stage "Initialize Marivo project" failed' in completed.stderr


def test_fails_when_init_reports_success_without_required_artifacts(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python312)
    env["FAKE_SKIP_INIT_ARTIFACTS"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    assert "missing required init artifact" in completed.stderr


def test_rerun_reuses_environment_and_initialized_project(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python312)

    first = _run_installer(tmp_path, env, "--yes")
    second = _run_installer(tmp_path, env, "--yes")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Reusing valid virtual environment" in second.stdout
    assert (tmp_path / "marivo.toml").is_file()
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert log.count(f"marivo:{tmp_path}:init") == 2
