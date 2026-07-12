"""Black-box tests for the Marivo Bash installer."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-marivo.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_uname(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "uname",
        "#!/usr/bin/env bash\nset -eu\nprintf '%s\\n' \"${FAKE_UNAME:-Linux}\"\n",
    )


def _fake_unsupported_python(bin_dir: Path, name: str) -> None:
    _write_executable(bin_dir / name, "#!/usr/bin/env bash\nexit 1\n")


def _fake_python(
    bin_dir: Path,
    name: str = "python3",
    *,
    default_version: str = "3.12",
) -> Path:
    path = bin_dir / name
    _write_executable(
        path,
        f'#!/usr/bin/env bash\ndefault_version="{default_version}"\n'
        + r"""set -eu
printf 'python:%s:%s\n' "$0" "$*" >> "${FAKE_LOG:?}"
if [[ "$0" == */managed/* ]]; then
    version="${FAKE_MANAGED_PYTHON_VERSION:-3.12}"
else
    version="${FAKE_PYTHON_VERSION:-$default_version}"
fi
major="${version%%.*}"
minor="${version#*.}"
minor="${minor%%.*}"
if [ "${1:-}" = "-c" ]; then
    code="${2:-}"
    if [[ "$code" == *"sys.version_info"* ]]; then
        if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 12 ]; }; then
            exit 0
        fi
        exit 1
    fi
    if [[ "$code" == *"sys.prefix"* ]]; then
        expected="${3:?}"
        actual="$(cd "$(dirname "$0")/.." && pwd -P)"
        [ "$actual" = "$expected" ]
        exit
    fi
    if [[ "$code" == *"import marivo"* ]]; then
        [ -x "$(dirname "$0")/marivo" ]
        printf 'marivo 0.3.0\n'
        exit
    fi
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
    [ "${FAKE_VENV_FAIL:-0}" != "1" ] || exit 1
    target="${3:?}"
    mkdir -p "$target/bin"
    cp "$0" "$target/bin/python"
    chmod +x "$target/bin/python"
    exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "ensurepip" ]; then
    [ "${FAKE_ENSUREPIP_FAIL:-0}" != "1" ] || exit 1
    touch "$(cd "$(dirname "$0")/.." && pwd -P)/.pip-ready"
    exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ]; then
    prefix="$(cd "$(dirname "$0")/.." && pwd -P)"
    if [ "${FAKE_PIP_MISSING:-0}" = "1" ] && [ ! -f "$prefix/.pip-ready" ]; then
        exit 1
    fi
    if [ "${3:-}" = "--version" ]; then
        printf 'pip 26.0 from %s/lib/python/site-packages/pip (python %s)\n' "$prefix" "$version"
        exit 0
    fi
    if [ "${3:-}" = "install" ]; then
        if [[ "$*" == *"marivo[all]"* ]]; then
            [ "${FAKE_PIP_FAIL:-0}" != "1" ] || exit 1
            marivo="$(dirname "$0")/marivo"
            cat > "$marivo" <<'MARIVO'
#!/usr/bin/env bash
set -eu
printf 'marivo:%s:%s\n' "$PWD" "$*" >> "${FAKE_LOG:?}"
if [ "${1:-}" = "--version" ]; then
    printf 'marivo 0.3.0\n'
elif [ "${1:-}" = "init" ]; then
    [ "${FAKE_INIT_FAIL:-0}" != "1" ] || exit 1
    if [ "${FAKE_SKIP_INIT_ARTIFACTS:-0}" != "1" ]; then
        printf '[project]\nname = "fake"\n' > marivo.toml
        mkdir -p models .marivo
    fi
fi
MARIVO
            chmod +x "$marivo"
        fi
        exit 0
    fi
fi
exit 2
""",
    )
    return path


def _fake_uv(bin_dir: Path) -> Path:
    uv = bin_dir / "uv"
    _write_executable(
        uv,
        r"""#!/usr/bin/env bash
set -eu
printf 'uv:%s\n' "$*" >> "${FAKE_LOG:?}"
if [ "${1:-}" = "--version" ]; then
    printf 'uv 0.11.28\n'
elif [ "${1:-}" = "python" ] && [ "${2:-}" = "install" ]; then
    exit 0
elif [ "${1:-}" = "python" ] && [ "${2:-}" = "find" ]; then
    printf '%s\n' "${FAKE_MANAGED_PYTHON:?}"
elif [ "${1:-}" = "venv" ]; then
    target="${@: -1}"
    FAKE_VENV_FAIL=0 "${FAKE_MANAGED_PYTHON:?}" -m venv "$target"
    touch "$target/.pip-ready"
else
    exit 2
fi
""",
    )
    return uv


def _fake_uv_download(bin_dir: Path, root: Path) -> Path:
    installer = root / "install-uv.sh"
    _write_executable(
        installer,
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'mkdir -p "$HOME/.local/bin"\n'
        'cp "$FAKE_UV_SOURCE" "$HOME/.local/bin/uv"\n'
        'chmod +x "$HOME/.local/bin/uv"\n',
    )
    _write_executable(
        bin_dir / "curl",
        r"""#!/usr/bin/env bash
set -eu
printf 'curl:%s\n' "$*" >> "${FAKE_LOG:?}"
output=""
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-o" ]; then
        output="${2:?}"
        shift 2
    else
        shift
    fi
done
cp "${FAKE_UV_INSTALLER:?}" "$output"
""",
    )
    return installer


@pytest.fixture
def installer_env(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    log.touch()
    _fake_uname(bin_dir)
    for name in ("python3.14", "python3.13", "python3.12"):
        _fake_unsupported_python(bin_dir, name)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{os.defpath}",
            "FAKE_LOG": str(log),
            "FAKE_UNAME": "Linux",
            "HOME": str(tmp_path / "home"),
        }
    )
    return bin_dir, env


def _run_installer(
    target: Path,
    env: dict[str, str],
    *args: str,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        cwd=target,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_rejects_unknown_argument_before_mutation(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    _, env = installer_env

    completed = _run_installer(tmp_path, env, "--force")

    assert completed.returncode != 0
    assert "unknown argument: --force" in completed.stderr
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("platform", ["MINGW64_NT-10.0", "MSYS_NT-10.0", "CYGWIN_NT-10.0"])
def test_rejects_native_windows_bash_platforms(
    tmp_path: Path,
    installer_env: tuple[Path, dict[str, str]],
    platform: str,
) -> None:
    _, env = installer_env
    env["FAKE_UNAME"] = platform

    completed = _run_installer(tmp_path, env)

    assert completed.returncode != 0
    assert "Use Windows Subsystem for Linux (WSL)" in completed.stderr


def test_refuses_noninteractive_replacement_without_yes(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    _, env = installer_env
    (tmp_path / ".venv").mkdir()

    completed = _run_installer(tmp_path, env)

    assert completed.returncode != 0
    assert "rerun with --yes" in completed.stderr
    assert (tmp_path / ".venv").exists()


def test_yes_replaces_broken_venv(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    _fake_python(bin_dir)
    (tmp_path / ".venv").mkdir()
    marker = tmp_path / ".venv" / "broken"
    marker.touch()

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0
    assert not marker.exists()


def test_reuses_valid_python_venv(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    source = _fake_python(bin_dir)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_bytes(source.read_bytes())
    (venv_bin / "python").chmod(0o755)

    completed = _run_installer(tmp_path, env)

    assert completed.returncode == 0
    assert "Reusing valid virtual environment" in completed.stdout


def test_selects_versioned_local_python_newer_than_minimum(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    _fake_python(bin_dir, default_version="3.11")
    _fake_python(bin_dir, "python3.13", default_version="3.13")

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode == 0, completed.stderr
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert f"python:{bin_dir / 'python3.13'}:-m venv" in log


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


def test_installs_marivo_all_with_venv_python_and_initializes_target(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    _fake_python(bin_dir)

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
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    _fake_python(bin_dir)
    env["FAKE_PIP_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert ":init" not in log
    assert not (tmp_path / "marivo.toml").exists()


def test_init_failure_preserves_installed_venv(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    _fake_python(bin_dir)
    env["FAKE_INIT_FAIL"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    assert (tmp_path / ".venv" / "bin" / "python").is_file()
    assert 'stage "Initialize Marivo project" failed' in completed.stderr


def test_fails_when_init_reports_success_without_required_artifacts(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    _fake_python(bin_dir)
    env["FAKE_SKIP_INIT_ARTIFACTS"] = "1"

    completed = _run_installer(tmp_path, env, "--yes")

    assert completed.returncode != 0
    assert "missing required init artifact" in completed.stderr


def test_rerun_reuses_environment_and_initialized_project(
    tmp_path: Path, installer_env: tuple[Path, dict[str, str]]
) -> None:
    bin_dir, env = installer_env
    _fake_python(bin_dir)

    first = _run_installer(tmp_path, env, "--yes")
    second = _run_installer(tmp_path, env, "--yes")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Reusing valid virtual environment" in second.stdout
    assert (tmp_path / "marivo.toml").is_file()
    log = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert log.count(f"marivo:{tmp_path}:init") == 2
