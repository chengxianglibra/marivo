"""Black-box tests for the Marivo Bash installer — uv / managed-python paths.

Split from ``test_install_marivo_script.py`` so pytest-xdist's ``loadscope``
distribution runs the two halves on separate workers. Shared infrastructure
lives in ``tests/install_marivo_helpers.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.install_marivo_helpers import (
    _fake_python,
    _fake_uv,
    _fake_uv_download,
    _run_installer,
)


def test_uses_uv_managed_python_when_local_python_is_too_old(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    managed_python = _fake_python(managed_dir, "python")
    _fake_python(bin_dir, default_version="3.11")
    _fake_uv(bin_dir)
    env["FAKE_MANAGED_PYTHON"] = str(managed_python)

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:python install 3.12" in log
    assert "uv:python find --managed-python 3.12" in log


def test_ignores_host_versioned_python_candidates(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    host_bin = tmp_path / "host-bin"
    managed_dir = tmp_path / "managed"
    host_bin.mkdir()
    managed_dir.mkdir()
    managed_python = _fake_python(managed_dir, "python")
    _fake_python(bin_dir, default_version="3.11")
    host_python = _fake_python(host_bin, "python3.12")
    _fake_uv(bin_dir)
    env["FAKE_MANAGED_PYTHON"] = str(managed_python)
    env["PATH"] = f"{bin_dir}:{host_bin}:{os.defpath}"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:python install 3.12" in log
    assert f"python:{host_python}:-c" not in log


def test_installs_uv_when_no_working_uv_is_available(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    managed_dir = tmp_path / "managed"
    uv_source_dir = tmp_path / "uv-source"
    managed_dir.mkdir()
    uv_source_dir.mkdir()
    managed_python = _fake_python(managed_dir, "python")
    uv_source = _fake_uv(uv_source_dir)
    _fake_python(bin_dir, default_version="3.11")
    installer = _fake_uv_download(bin_dir, tmp_path)
    env.update(
        {
            "FAKE_MANAGED_PYTHON": str(managed_python),
            "FAKE_UV_INSTALLER": str(installer),
            "FAKE_UV_SOURCE": str(uv_source),
        }
    )

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    assert (Path(env["HOME"]) / ".local" / "bin" / "uv").is_file()
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "curl:-LsSf https://astral.sh/uv/install.sh -o" in log


def test_falls_back_to_uv_when_standard_venv_creation_fails(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    local_python = _fake_python(bin_dir)
    _fake_uv(bin_dir)
    env["FAKE_MANAGED_PYTHON"] = str(local_python)
    env["FAKE_VENV_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:venv --python" in log


def test_rebuilds_with_uv_when_ensurepip_is_unavailable(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    local_python = _fake_python(bin_dir)
    _fake_uv(bin_dir)
    env.update(
        {
            "FAKE_MANAGED_PYTHON": str(local_python),
            "FAKE_PIP_MISSING": "1",
            "FAKE_ENSUREPIP_FAIL": "1",
        }
    )

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "uv:venv --python" in log
    assert (tmp_path / ".venv" / ".pip-ready").is_file()


def test_replaces_existing_venv_when_pip_cannot_be_repaired(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    source = _fake_python(bin_dir)
    _fake_uv(bin_dir)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_bytes(source.read_bytes())
    (venv_bin / "python").chmod(0o755)
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
    assert "uv:venv --python" in log
    assert (tmp_path / ".venv" / ".pip-ready").is_file()
