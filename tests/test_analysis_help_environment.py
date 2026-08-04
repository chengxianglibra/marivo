"""Environment-bound tests for the installed ``marivo`` entry points.

The ordinary CLI contract is covered by ``tests/test_cli.py``. These tests
only cover behavior that requires isolated virtual environments: fingerprints
must distinguish editable and copy installs, and the console script must agree
with ``python -m marivo``.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _deps_dir() -> str:
    """A directory of symlinks to the test env's site-packages *minus marivo*.

    The fresh fingerprint venvs install only marivo (``--no-deps``); their
    runtime dependencies live in the test environment's site-packages and are
    exposed to subprocesses via ``PYTHONPATH``. We symlink every entry here
    *except* marivo's dist-info and editable-install files, so that
    ``importlib.metadata.version("marivo")`` and marivo imports resolve to the
    venv's own install (editable or copy), not the test environment's. ``.pth``
    files in ``PYTHONPATH`` directories are not processed by ``site``, so the
    editable marivo finder never activates from here either.
    """
    import site
    import tempfile

    src = site.getsitepackages()[0]
    deps = tempfile.mkdtemp(prefix="marivo_deps_")
    for entry in os.listdir(src):
        if "marivo" in entry.lower():
            continue
        os.symlink(os.path.join(src, entry), os.path.join(deps, entry))
    return deps


def _venv_subprocess_env() -> dict[str, str]:
    """Environment for a fresh-venv subprocess: inherit env plus deps on ``PYTHONPATH``."""
    env = os.environ.copy()
    env["PYTHONPATH"] = _deps_dir()
    return env


def _run_cli(
    args: list[str], *, python: str | None = None, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run marivo CLI via ``python -m marivo`` in the given interpreter."""
    executable = python or sys.executable
    # A fresh venv only has marivo installed; expose its deps via PYTHONPATH.
    env = _venv_subprocess_env() if python is not None else None
    return subprocess.run(
        [executable, "-m", "marivo", *args],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=cwd,
        env=env,
    )


def _run_console(
    args: list[str], *, bin_dir: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the ``marivo`` console script."""
    base_dir = Path(bin_dir or os.path.dirname(sys.executable))
    marivo_bin = base_dir / "Scripts" / "marivo.exe" if os.name == "nt" else base_dir / "marivo"
    env = _venv_subprocess_env() if bin_dir is not None else None
    return subprocess.run(
        [str(marivo_bin), *args],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def _extract_fingerprint(output: str) -> tuple[str, str, str] | None:
    """Extract the three-line fingerprint from help output."""
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if (
            line.startswith("Marivo: ")
            and i + 2 < len(lines)
            and lines[i + 1].startswith("Python: ")
            and lines[i + 2].startswith("Package: ")
        ):
            return (lines[i], lines[i + 1], lines[i + 2])
    return None


# ---------------------------------------------------------------------------
# Environment fingerprint fixtures
# ---------------------------------------------------------------------------


def _create_venv(venv_dir: Path) -> Path:
    """Create a bare venv and return its python executable path.

    The venv only needs marivo itself installed (see ``_install_marivo``); its
    runtime dependencies are exposed to subprocesses via ``PYTHONPATH`` (see
    ``_venv_subprocess_env``) instead of being reinstalled, which is what makes
    these fingerprint venvs fast and independent of the pip wheel cache.
    """
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"venv creation failed: {result.stderr}")
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _install_marivo(python: Path, *, editable: bool = True) -> subprocess.CompletedProcess[str]:
    """Install marivo into the given python environment.

    ``--no-deps`` is safe because the venv's runtime dependencies are exposed
    via ``PYTHONPATH`` (see ``_venv_subprocess_env``). Skipping dependency
    resolution/install is what makes these fingerprint venvs fast.
    """
    repo_root = Path(__file__).resolve().parent.parent
    cmd: list[str] = [str(python), "-m", "pip", "install", "--no-deps"]
    if editable:
        cmd.append("-e")
    cmd.append(str(repo_root))
    cmd.append("-q")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture(scope="module")
def venv_a(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a venv with editable install for fingerprint testing."""
    venv_dir = tmp_path_factory.mktemp("venv_a")
    python = _create_venv(venv_dir)
    result = _install_marivo(python, editable=True)
    if result.returncode != 0:
        pytest.fail(f"pip install failed in venv_a: {result.stderr[:500]}")
    return python


@pytest.fixture(scope="module")
def venv_b(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a second venv with copy install for fingerprint comparison."""
    venv_dir = tmp_path_factory.mktemp("venv_b")
    python = _create_venv(venv_dir)
    result = _install_marivo(python, editable=False)
    if result.returncode != 0:
        pytest.fail(f"pip install failed in venv_b: {result.stderr[:500]}")
    return python


def test_fingerprints_differ_across_venvs(venv_a: Path, venv_b: Path) -> None:
    """Two separate venvs must produce different fingerprint tuples.

    ``venv_a`` uses an editable install (Package: points at the source tree)
    while ``venv_b`` uses a copy install (Package: points at site-packages).
    The ``Package:`` fingerprint line therefore genuinely differs, which makes
    the full fingerprint tuples distinct even on platforms where resolved
    Python paths converge (e.g. macOS framework venvs).

    Subprocesses run from each venv's home directory so that the cwd does not
    shadow the installed package (the repo root contains a ``marivo/`` source
    tree that Python would pick up via the empty-string ``sys.path`` entry).
    """
    venv_a_cwd = str(venv_a.parent.parent)
    venv_b_cwd = str(venv_b.parent.parent)

    result_a = _run_cli(["help"], python=str(venv_a), cwd=venv_a_cwd)
    result_b = _run_cli(["help"], python=str(venv_b), cwd=venv_b_cwd)

    assert result_a.returncode == 0
    assert result_b.returncode == 0

    fp_a = _extract_fingerprint(result_a.stdout)
    fp_b = _extract_fingerprint(result_b.stdout)

    assert fp_a is not None, "fingerprint not found in venv_a output"
    assert fp_b is not None, "fingerprint not found in venv_b output"

    # The Marivo version line must match (same source tree, same version).
    assert fp_a[0] == fp_b[0], f"Version lines differ: {fp_a[0]} vs {fp_b[0]}"

    # The package path line must differ: venv_a is editable (source tree),
    # venv_b is a copy install (site-packages copy).
    assert fp_a[2] != fp_b[2], (
        f"Package lines should differ (editable vs copy): {fp_a[2]} vs {fp_b[2]}"
    )

    # The full fingerprint tuples must differ because the Package lines differ.
    assert fp_a != fp_b, f"Fingerprints should differ:\n  venv_a: {fp_a}\n  venv_b: {fp_b}"


def test_console_and_module_agree_within_one_venv(venv_a: Path) -> None:
    """Console script and module execution produce the same fingerprint."""
    # venv_a is the python binary path (e.g. venv_a0/bin/python);
    # the marivo console script is in the same directory.
    bin_dir = str(venv_a.parent)

    # Console script
    result_console = _run_console(["help"], bin_dir=bin_dir)
    # Module execution
    result_module = _run_cli(["help"], python=str(venv_a))

    assert result_console.returncode == 0, result_console.stderr
    assert result_module.returncode == 0, result_module.stderr

    fp_console = _extract_fingerprint(result_console.stdout)
    fp_module = _extract_fingerprint(result_module.stdout)

    assert fp_console is not None, "fingerprint not found in console output"
    assert fp_module is not None, "fingerprint not found in module output"

    assert fp_console == fp_module, (
        f"Console and module fingerprints differ:\n  console: {fp_console}\n  module:  {fp_module}"
    )


def test_bootstrap_only_rejection_matches_across_console_and_module(venv_a: Path) -> None:
    bin_dir = str(venv_a.parent)

    result_console = _run_console(["help", "analysis", "observe"], bin_dir=bin_dir)
    result_module = _run_cli(["help", "analysis", "observe"], python=str(venv_a))

    assert result_module.returncode == 2, result_module.stdout
    assert result_console.returncode == 2, result_console.stdout

    assert result_console.stderr.rstrip("\n") == result_module.stderr.rstrip("\n")
