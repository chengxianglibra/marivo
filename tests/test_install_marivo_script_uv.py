"""Release-only installer tests for uv and managed-Python paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.install_marivo_helpers import InstallerEnv, _run_installer

pytestmark = pytest.mark.release


def test_uses_uv_managed_python_when_local_python_is_too_old(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python39, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:python install 3.10" in log
    assert "uv:python find --managed-python 3.10" in log


def test_ignores_host_versioned_python_candidates(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    env["PATH"] = ":".join(
        (
            str(toolchain.python39.parent),
            str(toolchain.uv.parent),
            str(toolchain.base_bin),
            str(toolchain.host_python310.parent),
            os.defpath,
        )
    )
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.managed_python)

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:python install 3.10" in log
    assert f"python:{toolchain.host_python310}:-c" not in log


def test_installs_uv_when_no_working_uv_is_available(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python39, toolchain.curl)
    env.update(
        {
            "FAKE_MANAGED_PYTHON": str(toolchain.managed_python),
            "FAKE_UV_INSTALLER": str(toolchain.uv_installer),
            "FAKE_UV_SOURCE": str(toolchain.uv),
        }
    )

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    assert (Path(env["HOME"]) / ".local" / "bin" / "uv").is_file()
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "curl:-LsSf https://astral.sh/uv/install.sh -o" in log


def test_uses_uv_to_create_the_virtual_environment(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python310, toolchain.uv)
    env["FAKE_MANAGED_PYTHON"] = str(toolchain.python310)
    env["FAKE_VENV_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:venv --python" in log


def test_uv_creates_a_seeded_virtual_environment(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python310, toolchain.uv)
    env.update(
        {
            "FAKE_MANAGED_PYTHON": str(toolchain.python310),
            "FAKE_PIP_MISSING": "1",
            "FAKE_ENSUREPIP_FAIL": "1",
        }
    )

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:venv --python" in log
    assert (tmp_path / ".venv" / ".pip-ready").is_file()


def test_reuses_an_existing_venv_without_requiring_pip(
    tmp_path: Path, installer_env: InstallerEnv
) -> None:
    toolchain, env = installer_env
    toolchain.activate(env, toolchain.python310, toolchain.uv)
    source = toolchain.python310
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(source)
    env.update(
        {
            "FAKE_MANAGED_PYTHON": str(source),
            "FAKE_PIP_MISSING": "1",
            "FAKE_ENSUREPIP_FAIL": "1",
        }
    )

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "Reusing valid virtual environment" in completed.stdout
    assert "uv:venv --python" not in log
