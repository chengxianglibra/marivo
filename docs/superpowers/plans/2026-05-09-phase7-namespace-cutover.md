# Phase 7 — Namespace Cutover & Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the Python package from `app/` to `marivo/`, remove dead code, update all references, add CI guards, and document architectural invariants.

**Architecture:** Mechanical rename using `git mv` + `sed`. Flat layout (`marivo/marivo/`). Single atomic commit. No backward-compatibility shim (pre-launch hard break).

**Tech Stack:** Python 3.12, setuptools, ruff, mypy, import-linter, pytest, pre-commit

**Spec:** [`docs/superpowers/specs/2026-05-09-phase7-namespace-cutover-design.md`](../specs/2026-05-09-phase7-namespace-cutover-design.md)

---

## File Structure

### Files to delete (dead code)
- `app/database.py` — unused `DuckDBStore` class (0 importers)
- `app/models.py` — unused re-export stub (0 importers)

### Files to move (entire directory)
- `app/` → `marivo/` (246 Python files)

### Files to modify (configuration)
- `pyproject.toml` — entry points, package find, ruff, mypy, coverage (6 lines)
- `.importlinter` — root package + all contract module paths (~52 lines)
- `.pre-commit-config.yaml` — mypy file filter (1 line)
- `Makefile` — mypy target (1 line)
- `.github/workflows/ci.yml` — mypy target, smoke test, coverage (3 lines)
- `CONTRIBUTING.md` — mypy and coverage commands (4 lines)

### Files to create
- `docs/architecture-invariants.md` — 5 core invariants with enforcement details

---

### Task 1: Remove Dead Code

**Files:**
- Delete: `app/database.py`
- Delete: `app/models.py`

- [ ] **Step 1: Verify `app/database.py` has zero importers**

Run:
```bash
grep -rn 'from app\.database\|import app\.database\|from app import database' --include='*.py' app/ tests/
```
Expected: zero output (no matches)

- [ ] **Step 2: Verify `app/models.py` has zero importers**

Run:
```bash
grep -rn 'from app\.models\|import app\.models\|from app import models' --include='*.py' app/ tests/
```
Expected: zero output (no matches)

- [ ] **Step 3: Delete both files**

```bash
git rm app/database.py app/models.py
```

- [ ] **Step 4: Run tests to confirm nothing breaks**

Run: `make test`
Expected: all tests pass

---

### Task 2: Rename Directory

**Files:**
- Move: `app/` → `marivo/`

- [ ] **Step 1: Move the package directory**

```bash
git mv app/ marivo/
```

This preserves git history. `git log --follow marivo/<file>` traces back to the original `app/<file>`.

- [ ] **Step 2: Verify the directory structure**

Run:
```bash
test -d marivo/ && echo "marivo/ exists" && test ! -d app/ && echo "app/ gone"
```
Expected:
```
marivo/ exists
app/ gone
```

---

### Task 3: Rewrite Python Imports

**Files:**
- Modify: all `.py` files under `marivo/`, `tests/`, `scripts/`

- [ ] **Step 1: Run sed to rewrite import statements**

```bash
find marivo/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from app\./from marivo./g' \
  -e 's/import app\./import marivo./g' \
  -e 's/from app import/from marivo import/g' \
  -e 's/^import app$/import marivo/g' \
  {} +
```

- [ ] **Step 2: Run sed to rewrite string literals referencing app modules**

This catches `patch("app.runtime...")`, `importlib.import_module("app.api")`, `sys.modules.pop("app.api")`, and similar string references:

```bash
find marivo/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/"app\./"marivo./g' \
  -e "s/'app\./'marivo./g" \
  {} +
```

- [ ] **Step 3: Verify no `from app.` or `import app.` references remain in Python files**

Run:
```bash
grep -rn 'from app\.\|import app\.\|from app import\|^import app$' --include='*.py' marivo/ tests/ scripts/
```
Expected: zero output

- [ ] **Step 4: Verify no `"app.` or `'app.` string literals remain in Python files**

Run:
```bash
grep -rn '"app\.\|'\''app\.' --include='*.py' marivo/ tests/ scripts/
```
Expected: zero output (or only false positives like `"application"` — review any matches manually)

---

### Task 4: Update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update entry points (lines 33-34)**

Change:
```toml
marivo = "app.cli:main"
marivo-stdio = "app.transports.mcp.stdio:main"
```
To:
```toml
marivo = "marivo.cli:main"
marivo-stdio = "marivo.transports.mcp.stdio:main"
```

- [ ] **Step 2: Update setuptools package find (line 46)**

Change:
```toml
include = ["app*"]
```
To:
```toml
include = ["marivo*"]
```

- [ ] **Step 3: Update ruff per-file-ignores (line 92)**

Change:
```toml
"app/api/**/*.py" = ["B008"]  # allow function calls in FastAPI defaults
```
To:
```toml
"marivo/api/**/*.py" = ["B008"]  # allow function calls in FastAPI defaults
```

- [ ] **Step 4: Update ruff isort known-first-party (line 95)**

Change:
```toml
known-first-party = ["app"]
```
To:
```toml
known-first-party = ["marivo"]
```

- [ ] **Step 5: Update coverage source (line 117)**

Change:
```toml
source = ["app"]
```
To:
```toml
source = ["marivo"]
```

---

### Task 5: Update .importlinter

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1: Rewrite all `app` references to `marivo` using sed**

```bash
sed -i '' 's/app\./marivo./g; s/^    app$/    marivo/' .importlinter
```

- [ ] **Step 2: Update root_package (line 2)**

Change:
```ini
root_package = app
```
To:
```ini
root_package = marivo
```

- [ ] **Step 3: Update the ignore_imports line in surfaces-must-use-runtime**

Verify line 86 reads:
```ini
    marivo.api.sessions -> marivo.runtime
```
(The sed in Step 1 should have caught `app.api.sessions -> app.runtime`.)

- [ ] **Step 4: Add the no-legacy-app-namespace guard contract**

Append to the end of `.importlinter`:

```ini

[importlinter:contract:no-legacy-app-namespace]
name = No remaining app namespace imports
type = forbidden
source_modules =
    marivo
forbidden_modules =
    app
```

- [ ] **Step 5: Verify the file is correct**

Run:
```bash
grep 'app\.' .importlinter
```
Expected: zero matches (all `app.` references replaced with `marivo.`)

Run:
```bash
grep 'root_package' .importlinter
```
Expected: `root_package = marivo`

---

### Task 6: Update Makefile, pre-commit, and CI

**Files:**
- Modify: `Makefile` (line 15)
- Modify: `.pre-commit-config.yaml` (line 17)
- Modify: `.github/workflows/ci.yml` (lines 33, 56, 60)

- [ ] **Step 1: Update Makefile**

Change line 15:
```makefile
	@$(VENV_MYPY) app
```
To:
```makefile
	@$(VENV_MYPY) marivo
```

- [ ] **Step 2: Update .pre-commit-config.yaml**

Change line 17:
```yaml
        files: ^app/
```
To:
```yaml
        files: ^marivo/
```

- [ ] **Step 3: Update .github/workflows/ci.yml line 33**

Change:
```yaml
        run: mypy app
```
To:
```yaml
        run: mypy marivo
```

- [ ] **Step 4: Update .github/workflows/ci.yml line 56**

Change:
```yaml
        run: python -c "from app.transports.mcp.stdio import main; print('marivo-stdio entry point OK')"
```
To:
```yaml
        run: python -c "from marivo.transports.mcp.stdio import main; print('marivo-stdio entry point OK')"
```

- [ ] **Step 5: Update .github/workflows/ci.yml line 60**

Change:
```yaml
          pytest --cov=app --cov-report=xml --cov-report=html --cov-report=term-missing
```
To:
```yaml
          pytest --cov=marivo --cov-report=xml --cov-report=html --cov-report=term-missing
```

---

### Task 7: Update Documentation

**Files:**
- Modify: `CONTRIBUTING.md` (lines 72, 98, 101, 105)

- [ ] **Step 1: Update mypy command in CONTRIBUTING.md (line 72)**

Change:
```bash
mypy app
```
To:
```bash
mypy marivo
```

- [ ] **Step 2: Update coverage commands in CONTRIBUTING.md (lines 98, 101, 105)**

Change all three occurrences:
```bash
pytest --cov=app --cov-report=term-missing
pytest --cov=app --cov-report=html
pytest --cov=app --cov-report=xml
```
To:
```bash
pytest --cov=marivo --cov-report=term-missing
pytest --cov=marivo --cov-report=html
pytest --cov=marivo --cov-report=xml
```

---

### Task 8: Write Architecture Invariants Document

**Files:**
- Create: `docs/architecture-invariants.md`

- [ ] **Step 1: Create the invariants document**

Create `docs/architecture-invariants.md` with this content:

```markdown
# Marivo Architecture Invariants

These invariants must hold across all phases and future development. Each entry
documents the rule, rationale, enforcement mechanism, and what a violation looks
like.

Source: [Platform Architecture Design §2](superpowers/specs/2026-05-06-marivo-platform-architecture-design.md)

---

## 1. Core Isolation

**Rule:** `core/` must not import any adapter, transport, or storage library.

**Rationale:** Core Engine contains pure domain logic. I/O dependencies would
make it untestable without infrastructure and create coupling between business
rules and deployment choices.

**Enforcement:** `importlinter:contract:core-no-io` — forbids `marivo.core`
from importing `marivo.api`, `marivo.storage`, `marivo.analysis_core`,
`marivo.evidence_engine`, `marivo.semantic_runtime`, `marivo.execution`,
`marivo.registry`, `marivo.adapters`, `marivo.cli`, and `marivo.ports`.

**Violation example:** Adding `import marivo.storage.metadata` inside a
`marivo/core/` module to read model definitions directly from SQLite.

---

## 2. Domain-Only Ports

**Rule:** Ports return domain objects and domain IDs — not ORM rows or SQL
cursors.

**Rationale:** Port interfaces define the boundary between core logic and
infrastructure. Leaking storage representations (rows, cursors, result sets)
forces core code to understand storage internals.

**Enforcement:** Code review. No automated linter rule exists for return type
shapes.

**Violation example:** A `ModelStore.get_model()` method returning a SQLAlchemy
`Row` object instead of a `SemanticModel` domain type.

---

## 3. Surface → Runtime Only

**Rule:** Surfaces (API, CLI, MCP) call Runtime only — not Core Engine
directly.

**Rationale:** Runtime orchestrates core + ports. Surfaces that bypass Runtime
duplicate orchestration logic and break the single-responsibility boundary.

**Enforcement:** `importlinter:contract:surfaces-must-use-runtime` — forbids
`marivo.api` and `marivo.cli` from importing `marivo.analysis_core`,
`marivo.evidence_engine`, or `marivo.semantic_runtime`.

**Violation example:** An API endpoint importing `marivo.analysis_core.compiler`
to run analysis directly instead of calling `runtime.execute_intent()`.

---

## 4. Profile Factory Exclusivity

**Rule:** Profile Factory is the only place that knows which adapter wires to
which port.

**Rationale:** Adapter wiring in multiple places creates hidden coupling and
makes it impossible to reason about which implementation is active. A single
factory per profile (local, server) is the composition root.

**Enforcement:** Convention and code review.
`importlinter:contract:profiles-do-not-import-runtime-factory` prevents
profiles from importing the old runtime factory.

**Violation example:** An API endpoint constructing a `DuckDBModelStore`
directly instead of receiving a `ModelStore` port from the profile factory.

---

## 5. Mode Separation

**Rule:** Local profile does not start HTTP service; enterprise profile does
not maintain independent business logic.

**Rationale:** Local mode is a library consumed by agents via MCP stdio.
Enterprise mode reuses the same Runtime exposed via HTTP. If either mode
maintains its own business logic, the codebase forks.

**Enforcement:** Architecture tests and code review.

**Violation example:** Adding a `local_analyze()` function in the local profile
that reimplements intent execution logic already in Runtime.
```

---

### Task 9: Reinstall Package and Verify

**Files:** None (verification only)

- [ ] **Step 1: Reinstall the package in development mode**

The package discovery path changed from `app*` to `marivo*`. Reinstall so
Python resolves the new package name:

```bash
.venv/bin/pip install -e ".[dev]" --quiet
```

- [ ] **Step 2: Run the full test suite**

Run: `make test`
Expected: all tests pass

- [ ] **Step 3: Run the linter (ruff + import-linter)**

Run: `make lint`
Expected: pass, including the new `no-legacy-app-namespace` contract

- [ ] **Step 4: Run the type checker**

Run: `make typecheck`
Expected: pass against `marivo/`

- [ ] **Step 5: Final grep check for any remaining `app` namespace references**

```bash
grep -rn 'from app\.\|import app\.\|from app import' --include='*.py' .
```
Expected: zero hits

```bash
grep -rn '"app\.\|'\''app\.' --include='*.py' marivo/ tests/
```
Expected: zero hits (or only false positives unrelated to module paths)

```bash
test ! -d app/ && echo "PASS: app/ directory removed"
test -f docs/architecture-invariants.md && echo "PASS: invariants doc exists"
```

- [ ] **Step 6: Fix any failures**

If any test, lint, or typecheck fails, investigate and fix. Common issues:
- Missed string literal reference → manually update the specific line
- Import ordering change → run `make format` to let ruff re-sort
- Mypy error from stale cache → `rm -rf .mypy_cache` and re-run

---

### Task 10: Atomic Commit

**Files:** All changes from Tasks 1–8

- [ ] **Step 1: Stage all changes**

```bash
git add -A
```

- [ ] **Step 2: Review what will be committed**

```bash
git status --short | head -30
git diff --cached --stat | tail -5
```

Verify:
- Deleted: `app/database.py`, `app/models.py`
- Renamed: `app/` → `marivo/` (all files)
- Modified: `pyproject.toml`, `.importlinter`, `.pre-commit-config.yaml`, `Makefile`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`
- Added: `docs/architecture-invariants.md`
- No unexpected files (`.DS_Store`, `.marivo/`, `marivo.yaml`, etc.)

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor: rename app/ to marivo/ — namespace cutover (Phase 7)

- Remove dead code: app/database.py, app/models.py (zero importers)
- git mv app/ marivo/ (flat layout)
- Rewrite all imports and string literals from app.* to marivo.*
- Update pyproject.toml, .importlinter, Makefile, CI, pre-commit, docs
- Add import-linter guard: no-legacy-app-namespace contract
- Add docs/architecture-invariants.md with 5 core invariants

Co-Authored-By: Copilot CLI:claude-opus-4.6 [Edit] [Bash]
EOF
)"
```
