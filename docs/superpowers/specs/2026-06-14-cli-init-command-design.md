# `marivo init` CLI Command Design

## Problem

After `pip install marivo`, a user has no guided way to set up a Marivo project.
They must manually create `marivo.toml`, `marivo/`, `.marivo/`, and install agent
skill files. This friction slows onboarding and is error-prone.

## Solution

Add a `marivo init` CLI command that scaffolds a Marivo project in the current
directory, including project manifest, directory structure, and skill symlinks
for supported coding agents.

## File Layout & Package Changes

### New files

- `marivo/cli.py` — CLI module with `main()` and `init` subcommand
- `marivo/skills/__init__.py` — package marker for bundled skills
- `marivo/skills/marivo-semantic/` — bundled skill (relocated from `marivo-skills/marivo-semantic/`)
- `marivo/skills/marivo-analysis/` — bundled skill (relocated from `marivo-skills/marivo-analysis/`)

### Modified files

- `pyproject.toml` — add `[project.scripts]` entry and package-data for skills
- `marivo/config.py` — add skill-related path constants

### pyproject.toml additions

```toml
[project.scripts]
marivo = "marivo.cli:main"

[tool.setuptools.package-data]
"marivo.skills" = ["**/*"]
```

### Skill relocation

The `marivo-skills/` directory at repo root is replaced by `marivo/skills/`
inside the package, so skills ship with the installed wheel. Existing symlinks
in the dev repo's `.claude/skills/` will need updating.

### New config constants in `marivo/config.py`

```python
CLAUDE_SKILLS_DIR = ".claude/skills"
CODEX_SKILLS_DIR = ".codex/skills"
SKILL_SEMANTIC = "marivo-semantic"
SKILL_ANALYSIS = "marivo-analysis"
```

## CLI Interface

```
marivo init [--force]
```

### argparse structure

- Top-level parser: `marivo`, description "Marivo project tooling"
- Subparser: `init` with `--force` flag
- No subcommand → print help, exit 0

### `init_project(force, project_dir)` logic

1. **Detect artifacts** — check existence of: `marivo.toml`, `marivo/`, `.marivo/`,
   `.claude/skills/marivo-semantic`, `.claude/skills/marivo-analysis`,
   `.codex/skills/marivo-semantic`, `.codex/skills/marivo-analysis`
2. **Guard** — if any artifact exists and `force` is False, print which were
   found and exit with code 1
3. **If force** — remove conflicting artifacts:
   - Delete `marivo.toml`
   - Remove skill symlinks in `.claude/skills/` and `.codex/skills/`
   - Remove empty `marivo/` and `.marivo/`
   - Skip `.marivo/` if it contains any files or non-empty subdirectories;
     print warning instead. "Has content" means `any(.marivo.rglob("*"))`
     returns at least one file.
4. **Create `marivo.toml`** with `[project] name = "<directory-basename>"`
5. **Create `marivo/`** — empty directory
6. **Create `.marivo/`** — empty directory
7. **Install skills** — for both agents (Claude Code, Codex):
   - Resolve skill source from installed package via `marivo.skills.__file__`
   - Create agent skill directory if missing (`.claude/skills/`, `.codex/skills/`)
   - Create symlinks: `<agent-skill-dir>/marivo-semantic -> <site-packages>/marivo/skills/marivo-semantic`
     and same for `marivo-analysis`
8. **Report** — print summary, exit 0

### Agent skill directories (project-level)

| Agent       | Directory          | Symlink names                        |
|-------------|--------------------|---------------------------------------|
| Claude Code | `.claude/skills/`  | `marivo-semantic`, `marivo-analysis`  |
| Codex       | `.codex/skills/`   | `marivo-semantic`, `marivo-analysis`  |

Both agents always receive skills, regardless of whether the agent tool is
installed. Unused symlinks cause no harm.

## Skill Source Resolution

```python
import marivo.skills

def _skills_source_dir() -> Path:
    return Path(marivo.skills.__file__).parent
```

Works for wheel, editable, and source installs. `marivo/skills/__init__.py` is
a minimal package marker with no public API.

## `marivo.toml` Content

```toml
[project]
name = "<directory-basename>"
```

The basename is `Path.cwd().name`. No additional sections for now.

## Error Handling

| Condition                              | Behavior                                        |
|----------------------------------------|-------------------------------------------------|
| Artifact exists, no `--force`          | Exit 1, list conflicting artifacts              |
| `.marivo/` has content, with `--force` | Skip removal, print warning                     |
| `marivo.toml` exists but invalid TOML  | Error, don't overwrite even with `--force`      |
| Symlink creation fails                 | Warning, continue (non-fatal)                   |
| Directory creation fails (permissions) | Error and abort                                 |

## Output on Success

```
Initialized Marivo project in /path/to/project
  Created marivo.toml
  Created marivo/
  Created .marivo/
  Installed skills for Claude Code (.claude/skills/)
  Installed skills for Codex (.codex/skills/)
```

## Testing

Tests in `tests/test_cli.py` using `tmp_path` and `monkeypatch.chdir`.

| Test                                            | What it verifies                                    |
|-------------------------------------------------|-----------------------------------------------------|
| `test_init_creates_all_artifacts`               | All files, dirs, and symlinks created               |
| `test_init_marivo_toml_content`                 | `marivo.toml` has correct `[project] name`          |
| `test_init_symlinks_point_to_installed_skills`  | Symlinks resolve to correct package paths           |
| `test_init_fails_if_artifacts_exist`             | Exit 1 when artifacts already present               |
| `test_init_force_overwrites`                     | `--force` replaces existing artifacts               |
| `test_init_force_preserves_nonempty_dot_marivo`  | `.marivo/` with content is skipped with warning     |
| `test_init_no_subcommand_prints_help`            | No args → help text, exit 0                         |

`init_project` accepts `project_dir: Path | None = None` for testability,
defaulting to `Path.cwd()`.

## Future Extensions (not in scope)

- `marivo version` — print installed version
- `marivo doctor` — validate project setup and skill health
- Additional agent support (opencode `.opencode.json`, others)
- `marivo init --agents=claude` — selective agent skill installation
