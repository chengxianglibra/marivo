"""Environment-bound CLI help tests for ``marivo help analysis [target]``.

These tests verify that:

1. Product root help preserves semantic-authoring pointers and doctor
   commands while advertising ``marivo help analysis``.
2. ``marivo help analysis <target>`` CLI output equals ``mv.help_text(target)``.
3. Unknown targets exit non-zero and render the same ``HelpTargetError`` text.
4. ``python -m marivo help analysis <target>`` produces identical output to
   the console script when both use the same interpreter.
5. Environment fingerprints differ across separate venvs but agree within a
   single venv (console script vs ``python -m marivo``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
from marivo.analysis.errors import HelpTargetError
from marivo.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(
    args: list[str], *, python: str | None = None, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run marivo CLI via ``python -m marivo`` in the given interpreter."""
    executable = python or sys.executable
    return subprocess.run(
        [executable, "-m", "marivo", *args],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=cwd,
    )


def _run_console(
    args: list[str], *, bin_dir: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the ``marivo`` console script."""
    base_dir = Path(bin_dir or os.path.dirname(sys.executable))
    marivo_bin = base_dir / "Scripts" / "marivo.exe" if os.name == "nt" else base_dir / "marivo"
    return subprocess.run(
        [str(marivo_bin), *args],
        capture_output=True,
        text=True,
        timeout=300,
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
# In-process CLI route and fingerprint tests
# ---------------------------------------------------------------------------


def test_root_help_advertises_help_analysis(capsys: pytest.CaptureFixture[str]) -> None:
    """Product root help must advertise ``marivo help analysis``."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "marivo help analysis" in captured.out


def test_root_help_preserves_semantic_authoring(capsys: pytest.CaptureFixture[str]) -> None:
    """Root help must preserve semantic authoring pointers."""
    with pytest.raises(SystemExit):
        main(["--help"])
    captured = capsys.readouterr()
    assert "Semantic authoring workflow:" in captured.out
    assert "python -c \"import marivo.datasource as md; md.help('authoring')\"" in captured.out
    assert "python -c \"import marivo.semantic as ms; ms.help('authoring')\"" in captured.out


def test_root_help_preserves_doctor_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """Root help must preserve doctor command pointers."""
    with pytest.raises(SystemExit):
        main(["--help"])
    captured = capsys.readouterr()
    assert "marivo doctor" in captured.out
    assert "marivo doctor --semantic" in captured.out
    assert "marivo doctor --datasource <name> --connect" in captured.out


@pytest.mark.parametrize(
    "target",
    [None, "observe", "compare", "forecast", "help", "Session", "MetricFrame"],
)
def test_cli_output_equals_sdk_output(
    target: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``marivo help analysis <target>`` equals ``mv.help_text(target)``."""
    argv = ["help", "analysis"]
    if target is not None:
        argv.append(target)

    main(argv)
    captured = capsys.readouterr()
    expected = mv.help_text(target)
    assert captured.out.rstrip("\n") == expected.rstrip("\n")


def test_cli_root_analysis_help_has_fingerprint(capsys: pytest.CaptureFixture[str]) -> None:
    """Root analysis help via CLI must contain the environment fingerprint."""
    main(["help", "analysis"])
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) >= 3
    assert lines[0].startswith("Marivo: ")
    assert marivo.__version__ in lines[0]
    assert lines[1].startswith("Python: ")
    assert lines[2].startswith("Package: ")


def test_unknown_target_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    """Unknown targets must exit with code 2."""
    with pytest.raises(SystemExit) as exc_info:
        main(["help", "analysis", "nonexistent_thing_xyz"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "HelpTargetError" in captured.err


def test_unknown_target_renders_same_error_as_sdk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI error output must match what HelpTargetError renders."""
    # Get the SDK error text
    try:
        mv.help_text("nonexistent_thing_xyz")
        raise AssertionError("expected HelpTargetError")
    except HelpTargetError as exc:
        sdk_error_text = str(exc)

    # Get the CLI error output
    with pytest.raises(SystemExit):
        main(["help", "analysis", "nonexistent_thing_xyz"])
    cli_error_text = capsys.readouterr().err

    assert sdk_error_text in cli_error_text


def test_unknown_track_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    """``marivo help <unknown-track>`` must exit non-zero."""
    with pytest.raises(SystemExit) as exc_info:
        main(["help", "datasource"])
    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Subprocess tests via python -m marivo
# ---------------------------------------------------------------------------


def test_module_root_help_exits_zero() -> None:
    """``python -m marivo help analysis`` exits 0 with fingerprint."""
    result = _run_cli(["help", "analysis"])
    assert result.returncode == 0
    assert "Marivo:" in result.stdout
    assert "Python:" in result.stdout
    assert "Package:" in result.stdout


def test_module_focused_help_matches_sdk() -> None:
    """``python -m marivo help analysis observe`` matches ``mv.help_text('observe')``."""
    result = _run_cli(["help", "analysis", "observe"])
    assert result.returncode == 0
    expected = mv.help_text("observe")
    assert result.stdout.rstrip("\n") == expected.rstrip("\n")


def test_module_unknown_target_exits_nonzero() -> None:
    """``python -m marivo help analysis <unknown>`` exits 2 with error."""
    result = _run_cli(["help", "analysis", "nonexistent_thing_xyz"])
    assert result.returncode == 2
    assert "analysis help target is not registered" in result.stderr


def test_module_version_flag() -> None:
    """``python -m marivo --version`` prints the package version."""
    result = _run_cli(["--version"])
    assert result.returncode == 0
    assert result.stdout.strip() == f"marivo {marivo.__version__}"


# ---------------------------------------------------------------------------
# Environment fingerprint fixtures
# ---------------------------------------------------------------------------


def _create_venv(venv_dir: Path) -> Path:
    """Create a venv and return its python executable path."""
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
    """Install marivo into the given python environment."""
    repo_root = Path(__file__).resolve().parent.parent
    cmd: list[str] = [str(python), "-m", "pip", "install"]
    if editable:
        cmd.append("-e")
    cmd.append(str(repo_root))
    cmd.append("-q")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture(scope="module")
def venv_a(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a venv with editable install for fingerprint testing."""
    venv_dir = tmp_path_factory.mktemp("venv_a")
    python = _create_venv(venv_dir)
    result = _install_marivo(python, editable=True)
    if result.returncode != 0:
        pytest.skip(f"pip install failed in venv_a: {result.stderr[:500]}")
    return python


@pytest.fixture(scope="module")
def venv_b(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a second venv with copy install for fingerprint comparison."""
    venv_dir = tmp_path_factory.mktemp("venv_b")
    python = _create_venv(venv_dir)
    result = _install_marivo(python, editable=False)
    if result.returncode != 0:
        pytest.skip(f"pip install failed in venv_b: {result.stderr[:500]}")
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

    result_a = _run_cli(["help", "analysis"], python=str(venv_a), cwd=venv_a_cwd)
    result_b = _run_cli(["help", "analysis"], python=str(venv_b), cwd=venv_b_cwd)

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
    result_console = _run_console(["help", "analysis"], bin_dir=bin_dir)
    # Module execution
    result_module = _run_cli(["help", "analysis"], python=str(venv_a))

    if result_console.returncode != 0:
        pytest.skip(f"console script not available: {result_console.stderr[:300]}")

    assert result_module.returncode == 0

    fp_console = _extract_fingerprint(result_console.stdout)
    fp_module = _extract_fingerprint(result_module.stdout)

    assert fp_console is not None, "fingerprint not found in console output"
    assert fp_module is not None, "fingerprint not found in module output"

    assert fp_console == fp_module, (
        f"Console and module fingerprints differ:\n  console: {fp_console}\n  module:  {fp_module}"
    )


def test_focused_help_matches_across_console_and_module(venv_a: Path) -> None:
    """Focused help output matches between console script and module execution."""
    bin_dir = str(venv_a.parent)

    result_console = _run_console(["help", "analysis", "observe"], bin_dir=bin_dir)
    result_module = _run_cli(["help", "analysis", "observe"], python=str(venv_a))

    if result_console.returncode != 0:
        pytest.skip(f"console script not available: {result_console.stderr[:300]}")

    assert result_module.returncode == 0
    assert result_console.returncode == 0

    assert result_console.stdout.rstrip("\n") == result_module.stdout.rstrip("\n")
