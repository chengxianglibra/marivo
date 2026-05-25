"""Tests for scripts/run_skill_examples.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_skill_examples.py"


def _load_runner_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_skill_examples_for_test", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_skill_tree(root: Path, skill_name: str, *, skill_md: str = "# placeholder\n") -> Path:
    """Create a minimal marivo-skill/<skill_name>/... layout under root."""
    skill_dir = root / "marivo-skill" / skill_name
    examples_dir = skill_dir / "references" / "examples"
    (examples_dir / "_fixtures").mkdir(parents=True)
    (examples_dir / "_fixtures" / "__init__.py").write_text("")
    (skill_dir / "SKILL.md").write_text(skill_md)
    return examples_dir


def _run_runner(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_runner_succeeds_on_empty_tree(tmp_path: Path) -> None:
    _make_skill_tree(tmp_path, "marivo-py-analysis")
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr


def test_runner_executes_passing_example(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-py-analysis")
    (examples / "01_smoke.py").write_text('print("hello from example")\n')
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "hello from example" not in result.stdout, (
        "runner should not echo child stdout on success"
    )


def test_runner_fails_when_example_exits_nonzero(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-py-analysis")
    (examples / "01_bad.py").write_text("raise SystemExit(2)\n")
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "01_bad.py" in result.stderr


def test_runner_fails_when_example_stdout_empty(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-py-analysis")
    (examples / "01_quiet.py").write_text("x = 1\n")
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "01_quiet.py" in result.stderr


def test_skill_md_within_cap_passes(tmp_path: Path) -> None:
    _make_skill_tree(tmp_path, "marivo-py-analysis", skill_md="# ok\n" * 100)
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr


def test_skill_md_over_cap_fails(tmp_path: Path) -> None:
    _make_skill_tree(tmp_path, "marivo-py-analysis", skill_md="# x\n" * 700)
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "SKILL.md exceeds" in result.stderr


def test_check_example_reports_timeout_with_partial_stdout(
    tmp_path: Path, monkeypatch: object
) -> None:
    runner = _load_runner_module()
    example = tmp_path / "01_timeout.py"
    example.write_text("raise AssertionError('should not execute')\n")

    def timeout(_example: Path) -> tuple[int, str, str]:
        raise subprocess.TimeoutExpired(
            cmd=[sys.executable, _example.name],
            timeout=0.05,
            output=b"started before timeout\n",
            stderr=b"partial error\n",
        )

    monkeypatch.setattr(runner, "EXAMPLE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(runner, "_execute_example", timeout)

    failure = runner._check_example(example)

    assert failure is not None
    assert failure.reason == "timeout"
    assert failure.file.name == "01_timeout.py"
    assert "partial stdout: started before timeout" in failure.detail
    assert "partial stderr: partial error" in failure.detail


_PITFALL_PASS = textwrap.dedent(
    """
    \"\"\"Pitfall: x.

    Expected output:
        FakeError: something went wrong
        正确写法:
          do this instead
    \"\"\"
    print("FakeError: something went wrong")
    print("正确写法:")
    print("  do this instead")
    """
).lstrip()

_PITFALL_FAIL = textwrap.dedent(
    """
    \"\"\"Pitfall: x.

    Expected output:
        FakeError: something went wrong
        正确写法:
          do this instead
    \"\"\"
    print("everything is fine!")
    """
).lstrip()


def test_pitfall_passes_when_keywords_present(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-py-analysis")
    (examples / "99_pitfall_x.py").write_text(_PITFALL_PASS)
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr


def test_pitfall_fails_when_keywords_missing(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-py-analysis")
    (examples / "99_pitfall_x.py").write_text(_PITFALL_FAIL)
    _make_skill_tree(tmp_path, "marivo-py-semantic")
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "missing pitfall keyword" in result.stderr.lower()
    assert "99_pitfall_x.py" in result.stderr
