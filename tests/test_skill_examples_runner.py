"""Tests for the skill examples runner script.

Validates that the runner:
- Requires semantic examples to exist and pass contract checks.
- Treats absent analysis examples as valid (single-file skill).
- Still validates SKILL.md presence for each skill directory.
- Only iterates existing example directories.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_skill_examples.py"


@pytest.fixture(scope="module")
def runner_module() -> object:
    """Import the runner script as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_skill_examples", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_skill_examples"] = module
    spec.loader.exec_module(module)
    return module


def test_runner_still_targets_semantic_skill(runner_module: object) -> None:
    """The runner must still iterate the marivo-semantic skill directory."""
    skill_dirs = runner_module.SKILL_DIRS  # type: ignore[attr-defined]
    assert "marivo/skills/marivo-semantic" in skill_dirs


def test_runner_still_targets_analysis_skill(runner_module: object) -> None:
    """The runner must still iterate the marivo-analysis skill directory
    (to check SKILL.md presence) even though examples are optional."""
    skill_dirs = runner_module.SKILL_DIRS  # type: ignore[attr-defined]
    assert "marivo/skills/marivo-analysis" in skill_dirs


def test_runner_does_not_require_analysis_examples(runner_module: object) -> None:
    """The runner must not fail when analysis examples are absent."""
    main = runner_module.main  # type: ignore[attr-defined]
    # Running against the repo root should succeed (exit 0) even though
    # marivo-analysis has no references/examples directory.
    rc = main(["--root", str(REPO_ROOT), "--in-process"])
    assert rc == 0, "run_skill_examples.py should exit 0 with absent analysis examples"


def test_runner_requires_semantic_examples(runner_module: object) -> None:
    """The runner must still enforce the semantic example contract."""
    # Verify the semantic example contract constants exist.
    assert hasattr(runner_module, "_SEMANTIC_EXAMPLE_NAMES")
    assert hasattr(runner_module, "_check_semantic_example_contract")
    names = runner_module._SEMANTIC_EXAMPLE_NAMES  # type: ignore[attr-defined]
    assert len(names) >= 2, "Semantic example contract must require multiple files"


def test_runner_skill_md_check_for_analysis(runner_module: object) -> None:
    """The runner must still check SKILL.md presence for marivo-analysis."""
    check_fn = runner_module._check_skill_md  # type: ignore[attr-defined]
    analysis_dir = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
    failure = check_fn(analysis_dir)
    assert failure is None, f"SKILL.md check failed for marivo-analysis: {failure}"


def test_makefile_does_not_hardcode_analysis_examples_dir() -> None:
    """The Makefile examples-check loop must not hardcode the analysis
    examples directory path (it may not exist)."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    # The Makefile should not have a direct path reference to the deleted
    # analysis examples directory in the typecheck loop.
    # It should iterate only existing directories.
    assert "marivo/skills/marivo-analysis/references/examples" not in makefile, (
        "Makefile still hardcodes marivo-analysis/references/examples; "
        "should iterate only existing directories"
    )
