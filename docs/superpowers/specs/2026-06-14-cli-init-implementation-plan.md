# `marivo init` CLI Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `marivo init` CLI command that scaffolds a Marivo project (manifest, directories, and agent skill symlinks) after `pip install marivo`.

**Architecture:** argparse CLI with a single `init` subcommand. Skills bundle inside `marivo/skills/` so they ship with the wheel. `init_project()` creates `marivo.toml`, `marivo/`, `.marivo/`, and symlinks from `.claude/skills/` and `.codex/skills/` to the bundled skill directories. Existing `marivo-skills/` at repo root is replaced by `marivo/skills/` inside the package.

**Tech Stack:** Python 3.12+, argparse, pathlib, tomllib (read), tomli_w (write), pytest + tmp_path for testing

---

## File Structure

### New files
- `marivo/cli.py` — CLI module with `main()` and `init_project()`
- `marivo/skills/__init__.py` — minimal package marker
- `marivo/skills/marivo-semantic/` — relocated from `marivo-skills/marivo-semantic/`
- `marivo/skills/marivo-analysis/` — relocated from `marivo-skills/marivo-analysis/`
- `tests/test_cli.py` — tests for the CLI

### Modified files
- `pyproject.toml` — add `[project.scripts]`, `tomli_w` dependency, and package-data for skills
- `marivo/config.py` — add skill-related path constants
- `scripts/run_skill_examples.py` — update `SKILL_DIRS` to new paths
- `Makefile` — update `examples-check` skill directory references
- `agent-guide.md` — update documentation routing table
- `.gitignore` — track the new skill symlinks
- Various test files referencing `marivo-skills/` — update to `marivo/skills/`

---

### Task 1: Add skill path constants to `marivo/config.py`

**Files:**
- Modify: `marivo/config.py:12-21`
- Test: `tests/test_cli.py` (indirect — constants used by later tasks)

- [ ] **Step 1: Add constants**

Add after the existing `ANALYSIS_DIR` line (line 21) in `marivo/config.py`:

```python
CLAUDE_SKILLS_DIR = ".claude/skills"
CODEX_SKILLS_DIR = ".codex/skills"
SKILL_SEMANTIC = "marivo-semantic"
SKILL_ANALYSIS = "marivo-analysis"
```

- [ ] **Step 2: Verify the file parses**

Run: `.venv/bin/python -c "from marivo.config import CLAUDE_SKILLS_DIR, CODEX_SKILLS_DIR, SKILL_SEMANTIC, SKILL_ANALYSIS; print(CLAUDE_SKILLS_DIR, CODEX_SKILLS_DIR, SKILL_SEMANTIC, SKILL_ANALYSIS)"`
Expected: `.claude/skills .codex/skills marivo-semantic marivo-analysis`

- [ ] **Step 3: Commit**

```bash
git add marivo/config.py
git commit -m "feat(cli): add skill path constants to config"
```

---

### Task 2: Create `marivo/skills/` package and relocate skills

**Files:**
- Create: `marivo/skills/__init__.py`
- Create: `marivo/skills/marivo-semantic/` (relocated from `marivo-skills/marivo-semantic/`)
- Create: `marivo/skills/marivo-analysis/` (relocated from `marivo-skills/marivo-analysis/`)
- Delete: `marivo-skills/marivo-semantic/`
- Delete: `marivo-skills/marivo-analysis/`

- [ ] **Step 1: Create the skills package marker**

Create `marivo/skills/__init__.py`:

```python
"""Bundled agent skills shipped inside the marivo package."""
```

- [ ] **Step 2: Move skill directories into the package**

```bash
cp -r marivo-skills/marivo-semantic marivo/skills/marivo-semantic
cp -r marivo-skills/marivo-analysis marivo/skills/marivo-analysis
```

- [ ] **Step 3: Remove old skill directories**

```bash
rm -rf marivo-skills/marivo-semantic marivo-skills/marivo-analysis
```

If `marivo-skills/` is now empty, remove it too:

```bash
rmdir marivo-skills 2>/dev/null || true
```

- [ ] **Step 4: Verify the package can be imported**

Run: `.venv/bin/python -c "import marivo.skills; from pathlib import Path; p = Path(marivo.skills.__file__).parent; print(p); assert (p / 'marivo-semantic' / 'SKILL.md').is_file(); assert (p / 'marivo-analysis' / 'SKILL.md').is_file(); print('OK')"`
Expected: prints the `marivo/skills/` path and `OK`

- [ ] **Step 5: Commit**

```bash
git add marivo/skills/
git add -u marivo-skills/
git commit -m "refactor(skills): relocate skills from marivo-skills/ into marivo/skills/ package"
```

---

### Task 3: Update pyproject.toml for CLI and skill packaging

**Files:**
- Modify: `pyproject.toml:27-28` (add `tomli_w` to dependencies)
- Modify: `pyproject.toml:67-68` (add package-data and scripts)

- [ ] **Step 1: Add `tomli_w` to core dependencies**

In `pyproject.toml`, add `"tomli_w>=1.0"` to the `dependencies` list (after `"scipy>=1.13.0"`):

```toml
dependencies = [
    "ibis-framework>=12.0.0",
    "numpy>=1.26.0",
    "pandas>=2.2.0",
    "pydantic>=2.9.0",
    "scipy>=1.13.0",
    "tomli_w>=1.0",
]
```

- [ ] **Step 2: Add `[project.scripts]` entry**

Add after the `[project.urls]` section (after line 32):

```toml
[project.scripts]
marivo = "marivo.cli:main"
```

- [ ] **Step 3: Add package-data for skills**

Update the `[tool.setuptools.package-data]` section to include the skills package:

```toml
[tool.setuptools.package-data]
"marivo.skills" = ["**/*"]
```

- [ ] **Step 4: Install updated package and verify**

```bash
.venv/bin/pip install -e .
marivo --help
```

Expected: prints a help message (the argparse default — this will fail until `marivo/cli.py` exists, which is created in Task 5; this step is for verification after that task).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "feat(cli): add console_scripts entry, tomli_w dep, and skills package-data"
```

---

### Task 4: Update references from `marivo-skills/` to `marivo/skills/`

**Files:**
- Modify: `scripts/run_skill_examples.py:18-21`
- Modify: `Makefile:31-32`
- Modify: `agent-guide.md:122`
- Modify: `tests/test_run_skill_examples.py:28`
- Modify: `tests/test_analysis_session_surface.py:221`
- Modify: `tests/test_analysis_evidence_walkthrough_doctest.py:17-22`
- Other files referencing `marivo-skills/` (found via grep)

- [ ] **Step 1: Update `scripts/run_skill_examples.py`**

Change `SKILL_DIRS` (line 18-21):

```python
SKILL_DIRS = (
    "marivo/skills/marivo-semantic",
    "marivo/skills/marivo-analysis",
)
```

- [ ] **Step 2: Update `Makefile`**

Change the `examples-check` recipe skill directory paths (lines 31-32):

```makefile
		marivo/skills/marivo-semantic/references/examples \
		marivo/skills/marivo-analysis/references/examples; do \
```

- [ ] **Step 3: Update `agent-guide.md`**

Change the documentation routing table (line 122):

```markdown
| Agent usage examples | `marivo/skills/marivo-semantic/` or `marivo/skills/marivo-analysis/` |
```

- [ ] **Step 4: Update `tests/test_run_skill_examples.py`**

Change `_make_skill_tree` (line 28):

```python
skill_dir = root / "marivo" / "skills" / skill_name
```

And update the docstring (line 27):

```python
"""Create a minimal marivo/skills/<skill_name>/... layout under root."""
```

- [ ] **Step 5: Update `tests/test_analysis_session_surface.py`**

Change line 221:

```python
for prefix in ("marivo/skills", "docs/specs"):
```

And update the docstring (line 218):

```python
"""Return all .py and .md paths under marivo, marivo/skills, docs/specs, and tests."""
```

- [ ] **Step 6: Update `tests/test_analysis_evidence_walkthrough_doctest.py`**

Change lines 17-22:

```python
SKILL_PATH = REPO_ROOT / "marivo/skills/marivo-analysis/SKILL.md"

DOCS = [
    REPO_ROOT / "marivo/skills/marivo-analysis/references/final-report.md",
    REPO_ROOT / "marivo/skills/marivo-analysis/references/cheatsheet.md",
    REPO_ROOT / "marivo/skills/marivo-analysis/references/pitfalls.md",
```

- [ ] **Step 7: Find and update any remaining references**

Run: `grep -rn "marivo-skills" --include='*.py' --include='*.toml' --include='*.md' --include='*.sh' --include='Makefile' | grep -v '.git/' | grep -v '__pycache__' | grep -v 'node_modules'`

Fix any remaining references found. If the only hits are in this plan document or the spec, that's fine.

- [ ] **Step 8: Run tests to verify nothing is broken**

Run: `make test`
Expected: all existing tests pass

- [ ] **Step 9: Commit**

```bash
git add scripts/run_skill_examples.py Makefile agent-guide.md tests/test_run_skill_examples.py tests/test_analysis_session_surface.py tests/test_analysis_evidence_walkthrough_doctest.py
git commit -m "refactor(skills): update all marivo-skills/ references to marivo/skills/"
```

---

### Task 5: Write the `init_project` function

**Files:**
- Create: `marivo/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for `init_project`**

Create `tests/test_cli.py`:

```python
"""Tests for marivo.cli — the marivo init command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from marivo.cli import init_project


class TestInitCreatesAllArtifacts:
    """init_project creates marivo.toml, marivo/, .marivo/, and skill symlinks."""

    def test_creates_marivo_toml(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        assert (tmp_path / "marivo.toml").is_file()

    def test_creates_marivo_toml_with_project_name(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        import tomllib

        with open(tmp_path / "marivo.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["project"]["name"] == tmp_path.name

    def test_creates_marivo_dir(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        assert (tmp_path / "marivo").is_dir()

    def test_creates_dot_marivo_dir(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        assert (tmp_path / ".marivo").is_dir()

    def test_installs_claude_skills(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        link = tmp_path / ".claude" / "skills" / "marivo-semantic"
        assert link.is_symlink() or link.is_dir()
        assert (link / "SKILL.md").is_file()

    def test_installs_codex_skills(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        link = tmp_path / ".codex" / "skills" / "marivo-semantic"
        assert link.is_symlink() or link.is_dir()
        assert (link / "SKILL.md").is_file()

    def test_claude_analysis_skill(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        link = tmp_path / ".claude" / "skills" / "marivo-analysis"
        assert link.is_symlink() or link.is_dir()
        assert (link / "SKILL.md").is_file()

    def test_codex_analysis_skill(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        link = tmp_path / ".codex" / "skills" / "marivo-analysis"
        assert link.is_symlink() or link.is_dir()
        assert (link / "SKILL.md").is_file()


class TestInitSymlinksPointToInstalledSkills:
    """Skill symlinks resolve to the installed package's skill directories."""

    def test_semantic_symlink_resolves_to_package(self, tmp_path: Path) -> None:
        init_project(project_dir=tmp_path)
        import marivo.skills

        skills_src = Path(marivo.skills.__file__).parent
        link = tmp_path / ".claude" / "skills" / "marivo-semantic"
        assert link.resolve() == (skills_src / "marivo-semantic").resolve()


class TestInitFailsIfArtifactsExist:
    """init_project exits with code 1 when artifacts already exist (no --force)."""

    def test_fails_if_marivo_toml_exists(self, tmp_path: Path) -> None:
        (tmp_path / "marivo.toml").write_text('[project]\nname = "x"\n')
        with pytest.raises(SystemExit) as exc_info:
            init_project(project_dir=tmp_path)
        assert exc_info.value.code == 1

    def test_fails_if_marivo_dir_exists(self, tmp_path: Path) -> None:
        (tmp_path / "marivo").mkdir()
        with pytest.raises(SystemExit) as exc_info:
            init_project(project_dir=tmp_path)
        assert exc_info.value.code == 1

    def test_fails_if_dot_marivo_dir_exists(self, tmp_path: Path) -> None:
        (tmp_path / ".marivo").mkdir()
        with pytest.raises(SystemExit) as exc_info:
            init_project(project_dir=tmp_path)
        assert exc_info.value.code == 1


class TestInitForceOverwrites:
    """init_project with force=True replaces existing artifacts."""

    def test_force_overwrites_marivo_toml(self, tmp_path: Path) -> None:
        (tmp_path / "marivo.toml").write_text('[project]\nname = "old"\n')
        init_project(force=True, project_dir=tmp_path)
        import tomllib

        with open(tmp_path / "marivo.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["project"]["name"] == tmp_path.name

    def test_force_overwrites_marivo_dir(self, tmp_path: Path) -> None:
        (tmp_path / "marivo").mkdir()
        init_project(force=True, project_dir=tmp_path)
        assert (tmp_path / "marivo").is_dir()

    def test_force_removes_skill_symlinks(self, tmp_path: Path) -> None:
        # First init to create symlinks
        init_project(project_dir=tmp_path)
        # Second init with force should succeed
        init_project(force=True, project_dir=tmp_path)
        assert (tmp_path / ".claude" / "skills" / "marivo-semantic").is_symlink()


class TestInitForcePreservesNonemptyDotMarivo:
    """--force skips removing .marivo/ if it contains files."""

    def test_force_preserves_nonempty_dot_marivo(self, tmp_path: Path) -> None:
        (tmp_path / ".marivo").mkdir()
        (tmp_path / ".marivo" / "analysis").mkdir()
        (tmp_path / ".marivo" / "analysis" / "session.json").write_text("{}")
        init_project(force=True, project_dir=tmp_path)
        assert (tmp_path / ".marivo" / "analysis" / "session.json").read_text() == "{}"


class TestInitForceRejectsInvalidToml:
    """--force refuses to overwrite marivo.toml when it contains invalid TOML."""

    def test_force_rejects_invalid_toml(self, tmp_path: Path) -> None:
        (tmp_path / "marivo.toml").write_text("this is not valid [[toml")
        with pytest.raises(SystemExit) as exc_info:
            init_project(force=True, project_dir=tmp_path)
        assert exc_info.value.code == 1


class TestInitNoSubcommandPrintsHelp:
    """Running `marivo` with no subcommand prints help and exits 0."""

    def test_no_args_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        from marivo.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Marivo" in captured.out or "marivo" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marivo.cli'`

- [ ] **Step 3: Implement `marivo/cli.py`**

Create `marivo/cli.py`:

```python
"""Marivo CLI — project scaffolding and tooling."""

from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from pathlib import Path

import tomli_w

from marivo.config import (
    AUTHORED_DIR,
    CLAUDE_SKILLS_DIR,
    CODEX_SKILLS_DIR,
    PROJECT_MANIFEST,
    SKILL_ANALYSIS,
    SKILL_SEMANTIC,
    STATE_DIR,
)


def _skills_source_dir() -> Path:
    """Resolve the installed package's skills directory."""
    import marivo.skills

    return Path(marivo.skills.__file__).parent


def _artifact_paths(project_dir: Path) -> dict[str, Path]:
    """Return all artifact paths that init checks or creates."""
    return {
        "marivo.toml": project_dir / PROJECT_MANIFEST,
        "marivo/": project_dir / AUTHORED_DIR,
        ".marivo/": project_dir / STATE_DIR,
        ".claude/skills/marivo-semantic": project_dir / CLAUDE_SKILLS_DIR / SKILL_SEMANTIC,
        ".claude/skills/marivo-analysis": project_dir / CLAUDE_SKILLS_DIR / SKILL_ANALYSIS,
        ".codex/skills/marivo-semantic": project_dir / CODEX_SKILLS_DIR / SKILL_SEMANTIC,
        ".codex/skills/marivo-analysis": project_dir / CODEX_SKILLS_DIR / SKILL_ANALYSIS,
    }


def init_project(force: bool = False, project_dir: Path | None = None) -> None:
    """Initialize a Marivo project in the given directory.

    Args:
        force: If True, overwrite existing artifacts (except non-empty .marivo/
            and invalid marivo.toml).
        project_dir: Target directory. Defaults to the current working directory.

    Raises:
        SystemExit: With code 1 when artifacts exist and force is False,
            when marivo.toml contains invalid TOML (even with force),
            or when directory creation fails due to permissions.

    Example:
        >>> init_project(project_dir=Path("/tmp/my-project"))

    Constraints:
        Never removes .marivo/ if it contains any files. Never overwrites
        marivo.toml if it contains invalid TOML. Symlink creation failures
        are non-fatal (warning printed, init continues).
    """
    project_dir = project_dir or Path.cwd()
    artifacts = _artifact_paths(project_dir)
    skills_src = _skills_source_dir()

    # --- Detect existing artifacts ---
    existing = [label for label, path in artifacts.items() if path.exists() or path.is_symlink()]

    if existing and not force:
        print(f"Marivo project artifacts already exist in {project_dir}:", file=sys.stderr)
        for label in existing:
            print(f"  {label}", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        raise SystemExit(1)

    # --- Guard: refuse to overwrite invalid marivo.toml ---
    manifest_path = project_dir / PROJECT_MANIFEST
    if manifest_path.is_file():
        try:
            with open(manifest_path, "rb") as f:
                tomllib.load(f)
        except tomllib.TOMLDecodeError:
            print(
                f"Error: {PROJECT_MANIFEST} exists but contains invalid TOML. "
                "Fix or remove it manually before reinitializing.",
                file=sys.stderr,
            )
            raise SystemExit(1)

    # --- Force: remove conflicting artifacts ---
    if existing and force:
        for label in existing:
            path = artifacts[label]
            if label == ".marivo/":
                # Skip if it has content (any file or nested entry)
                if any(path.rglob("*")):
                    print(
                        f"  Warning: {label} has content — skipping removal.",
                        file=sys.stderr,
                    )
                    continue
            if label == "marivo.toml":
                # Already validated above; safe to remove
                pass
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()

    # --- Create marivo.toml ---
    project_name = project_dir.name
    manifest_data = {"project": {"name": project_name}}
    (project_dir / PROJECT_MANIFEST).write_text(tomli_w.dumps(manifest_data))
    print(f"  Created {PROJECT_MANIFEST}")

    # --- Create marivo/ ---
    (project_dir / AUTHORED_DIR).mkdir(exist_ok=True)
    print(f"  Created {AUTHORED_DIR}/")

    # --- Create .marivo/ ---
    (project_dir / STATE_DIR).mkdir(exist_ok=True)
    print(f"  Created {STATE_DIR}/")

    # --- Install skills ---
    for agent_dir_name, agent_label in [
        (CLAUDE_SKILLS_DIR, "Claude Code"),
        (CODEX_SKILLS_DIR, "Codex"),
    ]:
        agent_skill_dir = project_dir / agent_dir_name
        agent_skill_dir.mkdir(parents=True, exist_ok=True)
        for skill_name in (SKILL_SEMANTIC, SKILL_ANALYSIS):
            link_path = agent_skill_dir / skill_name
            source_path = skills_src / skill_name
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            try:
                link_path.symlink_to(source_path)
            except OSError as exc:
                print(
                    f"  Warning: could not create symlink {link_path}: {exc}",
                    file=sys.stderr,
                )
        print(f"  Installed skills for {agent_label} ({agent_dir_name}/)")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the marivo command.

    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:].

    Example:
        >>> main(["init", "--force"])
    """
    parser = argparse.ArgumentParser(
        prog="marivo",
        description="Marivo project tooling",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a Marivo project")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing project artifacts",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    if args.command == "init":
        init_project(force=args.force)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add marivo/cli.py tests/test_cli.py
git commit -m "feat(cli): add marivo init command with init_project()"
```

---

### Task 6: Update `.gitignore` to track skill symlinks

**Files:**
- Modify: `.gitignore`

The current `.gitignore` ignores `.claude/` and `.codex/` entirely with broad patterns. After `marivo init` creates symlinks, those symlinks need to be tracked. The current exceptions for `.claude/skills/` are for different skill names. We need to add exceptions for the new marivo-semantic and marivo-analysis symlinks.

- [ ] **Step 1: Update `.gitignore`**

Find the section starting with `# Claude Code` and add exceptions for the new skill symlinks. The current pattern is:

```gitignore
# Claude Code
.mcp.json
.claude/
!.claude/skills/
.claude/skills/*
!.claude/skills/marivo-test-fixtures/
!.claude/skills/marivo-test-fixtures/SKILL.md
!.claude/skills/commit-attribution/
!.claude/skills/commit-attribution/SKILL.md
.codex/
.agents/
```

Add the new skill symlink exceptions:

```gitignore
# Claude Code
.mcp.json
.claude/
!.claude/skills/
.claude/skills/*
!.claude/skills/marivo-test-fixtures/
!.claude/skills/marivo-test-fixtures/SKILL.md
!.claude/skills/commit-attribution/
!.claude/skills/commit-attribution/SKILL.md
!.claude/skills/marivo-semantic/
!.claude/skills/marivo-analysis/
.codex/
!.codex/skills/
!.codex/skills/marivo-semantic/
!.codex/skills/marivo-analysis/
.agents/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: track marivo-semantic and marivo-analysis skill symlinks in gitignore"
```

---

### Task 7: Update dev-repo symlinks for the relocated skills

**Files:**
- Modify: `.claude/skills/marivo-py-semantic` (symlink — currently points to `marivo-skill/marivo-py-semantic`)
- Modify: `.claude/skills/marivo-py-analysis` (symlink — currently points to `marivo-skill/marivo-py-analysis`)

The existing dev-repo symlinks `.claude/skills/marivo-py-semantic` and `.claude/skills/marivo-py-analysis` point to the old `marivo-skill/` directory. These are separate from the `marivo-semantic`/`marivo-analysis` symlinks that `marivo init` creates — they use different names (`marivo-py-*`). They can remain as-is if the `marivo-skill/` directory still exists in the repo.

However, `marivo init` will also create `.claude/skills/marivo-semantic` and `.claude/skills/marivo-analysis` symlinks. These are separate and won't conflict with the existing `marivo-py-*` ones.

- [ ] **Step 1: Check if `marivo-skill/` still exists**

Run: `ls -la marivo-skill/ 2>/dev/null || echo "does not exist"`

If it exists, these dev symlinks are fine as-is — they are separate from the `marivo init` symlinks.

- [ ] **Step 2: Verify `marivo init` in the repo doesn't conflict**

The `marivo init` symlinks will be named `marivo-semantic` and `marivo-analysis`, distinct from the existing `marivo-py-semantic` and `marivo-py-analysis`. No conflict. No action needed unless `marivo-skill/` is gone.

- [ ] **Step 3: If `marivo-skill/` no longer exists, update the dev symlinks**

If `marivo-skill/` was removed in a prior cleanup, redirect the dev symlinks to the new location:

```bash
rm .claude/skills/marivo-py-semantic
ln -s marivo/skills/marivo-semantic .claude/skills/marivo-py-semantic
rm .claude/skills/marivo-py-analysis
ln -s marivo/skills/marivo-analysis .claude/skills/marivo-py-analysis
```

Then commit the symlink changes.

---

### Task 8: Add the initialized header to output

**Files:**
- Modify: `marivo/cli.py`

The spec says the output should start with `Initialized Marivo project in /path/to/project`. The current implementation prints individual lines without that header.

- [ ] **Step 1: Add the header line to `init_project`**

In `marivo/cli.py`, add before the `marivo.toml` creation line:

```python
    print(f"Initialized Marivo project in {project_dir}")
```

- [ ] **Step 2: Add the test for the header**

Add to `tests/test_cli.py` in the `TestInitCreatesAllArtifacts` class:

```python
    def test_prints_initialized_header(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        init_project(project_dir=tmp_path)
        captured = capsys.readouterr()
        assert f"Initialized Marivo project in {tmp_path}" in captured.out
```

- [ ] **Step 3: Run tests to verify**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add marivo/cli.py tests/test_cli.py
git commit -m "feat(cli): add initialized header to init output"
```

---

### Task 9: Verify the CLI entry point works end-to-end

**Files:**
- No new files — verification only

- [ ] **Step 1: Reinstall the package**

```bash
.venv/bin/pip install -e .
```

- [ ] **Step 2: Test `marivo --help`**

Run: `marivo --help`
Expected: prints usage information mentioning `init`

- [ ] **Step 3: Test `marivo init` in a temp directory**

```bash
mkdir -p /tmp/marivo-init-test
cd /tmp/marivo-init-test
marivo init
```

Expected output:

```
Initialized Marivo project in /tmp/marivo-init-test
  Created marivo.toml
  Created marivo/
  Created .marivo/
  Installed skills for Claude Code (.claude/skills/)
  Installed skills for Codex (.codex/skills/)
```

Verify: `ls -la /tmp/marivo-init-test/.claude/skills/` shows `marivo-semantic` and `marivo-analysis` symlinks.

- [ ] **Step 4: Test `marivo init` again without --force**

Run: `cd /tmp/marivo-init-test && marivo init`
Expected: exits with code 1, lists conflicting artifacts

- [ ] **Step 5: Test `marivo init --force`**

Run: `cd /tmp/marivo-init-test && marivo init --force`
Expected: succeeds, re-creates all artifacts

- [ ] **Step 6: Test `.marivo/` protection with --force**

```bash
mkdir -p /tmp/marivo-init-test2/.marivo/analysis
echo '{"session":"test"}' > /tmp/marivo-init-test2/.marivo/analysis/session.json
cd /tmp/marivo-init-test2
marivo init --force
```

Expected: succeeds, prints warning about `.marivo/` having content, preserves the existing file.

- [ ] **Step 7: Clean up**

```bash
rm -rf /tmp/marivo-init-test /tmp/marivo-init-test2
```

---

### Task 10: Run full test suite and type checking

**Files:**
- No new files — verification only

- [ ] **Step 1: Run typecheck**

Run: `make typecheck`
Expected: no errors

- [ ] **Step 2: Run lint**

Run: `make lint`
Expected: no errors

- [ ] **Step 3: Run full test suite**

Run: `make test`
Expected: all tests pass

- [ ] **Step 4: Run examples check**

Run: `make examples-check`
Expected: all skill examples pass

- [ ] **Step 5: Commit any formatting fixes if needed**

If `make format` makes changes:

```bash
git add -A
git commit -m "style: format after cli init implementation"
```
