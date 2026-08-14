"""Black-box tests for the Marivo Bash installer lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install_marivo_helpers import (
    INSTALLER,
    InstallerEnv,
    _run_installer,
)

pytestmark = pytest.mark.release


def test_installers_default_to_duckdb_trino_and_clickhouse_without_mysql() -> None:
    content = INSTALLER.read_text(encoding="utf-8")

    assert 'readonly DEFAULT_MARIVO_EXTRAS="duckdb,trino,clickhouse"' in content
    assert '"marivo[$DEFAULT_MARIVO_EXTRAS]"' in content


def test_rejects_unknown_argument_before_mutation(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    _, env = installer_env

    completed = _run_installer(tmp_path, env, "--force")

    assert completed.returncode != 0
    assert "unknown argument: --force" in completed.stderr
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("platform", ["MINGW64_NT-10.0", "MSYS_NT-10.0", "CYGWIN_NT-10.0"])
def test_supports_native_windows_bash_platforms(
    tmp_path: Path,
    installer_env: InstallerEnv,
    platform: str,
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.uv)
    env.update(
        {
            "FAKE_UNAME": platform,
            "FAKE_WINDOWS": "1",
            "FAKE_MANAGED_PYTHON": str(toolchain.managed_python),
        }
    )

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / ".venv" / "Scripts" / "python.exe").is_file()
    assert (tmp_path / ".venv" / "Scripts" / "marivo.exe").is_file()


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
    toolchain.activate(env, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)
    (tmp_path / ".venv").mkdir()
    marker = tmp_path / ".venv" / "broken"
    marker.touch()

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0
    assert not marker.exists()


def test_reuses_valid_python_venv(tmp_path: Path, installer_env: InstallerEnv) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)
    source = toolchain.python310
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(source)

    completed = _run_installer(tmp_path, env)

    assert completed.returncode == 0
    assert "Reusing valid virtual environment" in completed.stdout


def test_prepares_the_environment_with_uv_even_when_local_python_is_available(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python39, toolchain.python313, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:python install 3.10" in log
    assert "uv:venv --python" in log


def test_installs_default_marivo_backends_and_initializes_target(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    expected_python = tmp_path / ".venv" / "bin" / "python"
    assert (
        f"uv:pip install --python {expected_python} --upgrade marivo[duckdb,trino,clickhouse]"
        in log
    )
    assert f"marivo:{tmp_path}:init" in log
    assert (tmp_path / "marivo.toml").is_file()
    assert (tmp_path / "models").is_dir()
    assert (tmp_path / ".marivo").is_dir()
    assert "warning: optional init artifact is missing" in completed.stderr


def test_stops_before_init_when_marivo_installation_fails(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)
    env["FAKE_PIP_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert ":init" not in log
    assert not (tmp_path / "marivo.toml").exists()


def test_init_failure_preserves_installed_venv(tmp_path: Path, installer_env: InstallerEnv) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)
    env["FAKE_INIT_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    assert (tmp_path / ".venv" / "bin" / "python").is_file()
    assert 'stage "Initialize Marivo project" failed' in completed.stderr


def test_fails_when_init_reports_success_without_required_artifacts(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)
    env["FAKE_SKIP_INIT_ARTIFACTS"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    assert "missing required init artifact" in completed.stderr


def test_rerun_reuses_environment_and_initialized_project(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)

    first = _run_installer(tmp_path, env, "--yes")
    second = _run_installer(tmp_path, env, "--yes")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Reusing valid virtual environment" in second.stdout
    assert (tmp_path / "marivo.toml").is_file()
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert log.count(f"marivo:{tmp_path}:init") == 2
