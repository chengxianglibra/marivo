"""Shared black-box test infrastructure for the Marivo Bash installer.

The installer test suite is split across two modules so that pytest-xdist's
``loadscope`` distribution can run the two halves on separate workers. Both
modules import their helpers and the ``installer_env`` fixture from here.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

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
