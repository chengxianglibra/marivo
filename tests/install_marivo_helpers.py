"""Shared black-box test infrastructure for the Marivo Bash installer."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

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
    ln -s "$0" "$target/bin/python"
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
            ln -sf "${FAKE_MARIVO_SHIM:?}" "$marivo"
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
        'ln -sf "$FAKE_UV_SOURCE" "$HOME/.local/bin/uv"\n',
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
ln -sf "${FAKE_UV_INSTALLER:?}" "$output"
""",
    )
    return installer


def _fake_marivo(bin_dir: Path) -> Path:
    marivo = bin_dir / "marivo"
    _write_executable(
        marivo,
        r"""#!/usr/bin/env bash
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
""",
    )
    return marivo


@dataclass(frozen=True)
class InstallerToolchain:
    """Versioned immutable executable shims reused across pytest runs."""

    base_bin: Path
    python312: Path
    python311: Path
    python313: Path
    managed_python: Path
    host_python312: Path
    uv: Path
    curl: Path
    uv_installer: Path
    marivo: Path

    @classmethod
    def from_root(cls, root: Path) -> InstallerToolchain:
        """Resolve a previously published toolchain without modifying it."""
        return cls(
            base_bin=root / "base-bin",
            python312=root / "python312-bin" / "python3",
            python311=root / "python311-bin" / "python3",
            python313=root / "python313-bin" / "python3.13",
            managed_python=root / "managed" / "bin" / "python",
            host_python312=root / "host-bin" / "python3.12",
            uv=root / "uv-bin" / "uv",
            curl=root / "download-bin" / "curl",
            uv_installer=root / "uv-download" / "install-uv.sh",
            marivo=root / "marivo-bin" / "marivo",
        )

    @classmethod
    def load_or_build(cls, root: Path) -> InstallerToolchain:
        """Reuse a complete versioned cache or publish one atomically."""
        if cls._is_complete(root):
            return cls.from_root(root)

        build_root = root.with_name(f".{root.name}-{uuid4().hex}")
        cls.build(build_root)
        try:
            os.replace(build_root, root)
        except OSError:
            if not cls._is_complete(root):
                raise
            shutil.rmtree(build_root)
        return cls.from_root(root)

    @classmethod
    def _is_complete(cls, root: Path) -> bool:
        if not (root / ".complete").is_file():
            return False
        toolchain = cls.from_root(root)
        executables = (
            toolchain.python312,
            toolchain.python311,
            toolchain.python313,
            toolchain.managed_python,
            toolchain.host_python312,
            toolchain.uv,
            toolchain.curl,
            toolchain.uv_installer,
            toolchain.marivo,
            toolchain.base_bin / "uname",
        )
        return all(path.is_file() and os.access(path, os.X_OK) for path in executables)

    @classmethod
    def build(cls, root: Path) -> InstallerToolchain:
        """Build every installer shim once, then make the files read-only."""
        base_bin = root / "base-bin"
        python312_bin = root / "python312-bin"
        python311_bin = root / "python311-bin"
        python313_bin = root / "python313-bin"
        managed_bin = root / "managed" / "bin"
        host_bin = root / "host-bin"
        uv_bin = root / "uv-bin"
        download_bin = root / "download-bin"
        download_root = root / "uv-download"
        marivo_bin = root / "marivo-bin"
        for directory in (
            base_bin,
            python312_bin,
            python311_bin,
            python313_bin,
            managed_bin,
            host_bin,
            uv_bin,
            download_bin,
            download_root,
            marivo_bin,
        ):
            directory.mkdir(parents=True)

        _fake_uname(base_bin)
        for name in ("python3.14", "python3.13", "python3.12"):
            _fake_unsupported_python(base_bin, name)
        python312 = _fake_python(python312_bin)
        python311 = _fake_python(python311_bin, default_version="3.11")
        python313 = _fake_python(python313_bin, "python3.13", default_version="3.13")
        managed_python = _fake_python(managed_bin, "python")
        host_python312 = _fake_python(host_bin, "python3.12")
        uv = _fake_uv(uv_bin)
        uv_installer = _fake_uv_download(download_bin, download_root)
        curl = download_bin / "curl"
        marivo = _fake_marivo(marivo_bin)
        (root / ".complete").touch()

        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(path.stat().st_mode & ~0o222)

        return cls(
            base_bin=base_bin,
            python312=python312,
            python311=python311,
            python313=python313,
            managed_python=managed_python,
            host_python312=host_python312,
            uv=uv,
            curl=curl,
            uv_installer=uv_installer,
            marivo=marivo,
        )

    def activate(self, env: dict[str, str], *tools: Path) -> None:
        """Prepend selected immutable tool profiles to one test environment."""
        directories = tuple(dict.fromkeys(tool.parent for tool in tools))
        env["PATH"] = ":".join((*map(str, directories), env["PATH"]))


type InstallerEnv = tuple[InstallerToolchain, dict[str, str]]


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
