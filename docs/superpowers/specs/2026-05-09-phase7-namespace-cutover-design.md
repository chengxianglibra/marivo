---
status: draft
created: 2026-05-09
---

# Phase 7 — Namespace Cutover & Convergence Design

**Date:** 2026-05-09
**Status:** Draft
**Parent spec:** [`2026-05-06-marivo-platform-architecture-design.md`](./2026-05-06-marivo-platform-architecture-design.md)
**Position:** Phase 7 (execution order 9 — the final phase)
**Predecessor:** Phase 9 (Production Server Adapters)

---

## 1. Scope

Phase 7 is the final mechanical pass in the Marivo platform migration. It
renames the Python package from `app/` to `marivo/`, removes confirmed dead
code, updates all configuration and CI references, adds an import-linter guard
against the old namespace, and documents the architectural invariants.

### 1.1 In scope

1. Dead code removal — modules under `app/` with zero importers.
2. Mechanical rename — `git mv app/ marivo/`, flat layout (`marivo/marivo/`).
3. Import rewriting — all `from app.` / `import app.` references across `.py`,
   config, and documentation files.
4. String literal fixups — uvicorn ASGI paths, module name strings in tests.
5. Configuration cutover — `pyproject.toml`, `.importlinter`, `Makefile`, ruff,
   mypy, coverage.
6. Import boundary enforcement — new import-linter contract forbidding
   `from app` imports.
7. Architecture invariants document — `docs/architecture-invariants.md`.
8. Verification — all existing tests pass, lint/typecheck green, zero `app`
   namespace references remain.

### 1.2 Out of scope

- New E2E golden-test suite (existing tests serve as the regression baseline).
- Backward-compatibility shim for the `app` namespace (hard break, pre-launch).
- Src layout migration (`src/marivo/`).
- Any functional or behavioral changes.

### 1.3 Acceptance criteria

| # | Criterion |
|---|-----------|
| 1 | No `app/` directory exists in the repository |
| 2 | `grep -r 'from app\.\|import app\.' --include='*.py'` returns zero hits |
| 3 | `make test` passes (all existing tests green) |
| 4 | `make lint` passes (ruff + import-linter, including new no-legacy-app contract) |
| 5 | `make typecheck` passes against `marivo/` |
| 6 | `docs/architecture-invariants.md` exists and documents all 5 core invariants |
| 7 | Single atomic commit for the entire rename |

---

## 2. Design Decisions

### 2.1 Flat layout

The renamed package uses flat layout: the Python package `marivo/` sits
directly under the repository root (`~/source/oss/marivo/marivo/`). This is
standard for Python projects (Django, FastAPI, Pydantic all use this pattern).

The existing `marivo-skill/` directory is not a Python package and does not
conflict.

### 2.2 Hard break, no compatibility shim

Marivo is pre-launch. The platform spec (§2, principle 10) says: "Breaking
changes between phases are first-class." No `app` package, re-export shim, or
deprecation warning is retained after the rename.

### 2.3 Approach: git mv + sed

The rename uses `git mv` (preserves history with `git log --follow`) followed
by repo-wide `sed` for import rewriting. AST-based tools (rope, bowler) were
considered but add complexity without meaningful benefit for this mechanical
task.

---

## 3. Dead Code Removal

Before renaming, identify modules in `app/` with zero importers and remove
them. This prevents carrying unused code into the new namespace.

**Method:** For each top-level `app/*.py` file (excluding `__init__.py` and
known entry points), run:
```
grep -r 'from app.<module>\|import app.<module>' --include='*.py' app/ tests/
```

If zero results, the module is a removal candidate. Entry points declared in
`pyproject.toml` (`app.cli:main`, `app.transports.mcp.stdio:main`) are
excluded from removal regardless.

**Candidates to investigate:**

| Module | Remove if unused |
|--------|-----------------|
| `app/database.py` | Yes |
| `app/datasources.py` | Yes |
| `app/models.py` | Yes |
| `app/routing.py` | Yes |
| `app/identity.py` | Yes |
| `app/metric_inputs.py` | Yes |
| `app/observability.py` | Yes |
| `app/redaction.py` | Yes |
| `app/source_object_locator.py` | Yes |
| `app/time_axis_metadata.py` | Yes |
| `app/time_contracts.py` | Yes |
| `app/time_scope.py` | Yes |
| `app/runtime_contracts.py` | Yes |
| `app/dialect.py` | Yes |

Each candidate is verified by grep before removal. If any module has at least
one importer, it is kept. The implementation plan will record the actual
grep results for each candidate as evidence.

---

## 4. Mechanical Rename

### 4.1 Directory move

```bash
git mv app/ marivo/
```

### 4.2 Import rewriting

Repo-wide sed on all `.py` files under `marivo/`, `tests/`, and `scripts/`:

```bash
find marivo/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from app\./from marivo./g' \
  -e 's/import app\./import marivo./g' \
  -e 's/from app import/from marivo import/g' \
  -e 's/import app$/import marivo/g' \
  {} +
```

### 4.3 String literal fixups

Some files reference `app` as a string (not as an import). These require
targeted fixes:

| File | Old value | New value |
|------|-----------|-----------|
| `marivo/cli/cmd_serve.py` | `"app.main:app"` | `"marivo.main:app"` |
| `marivo/cli/cmd_serve_local.py` | `"app.main:app"` | `"marivo.main:app"` |
| `marivo/cli/__init__.py` | docstring referencing `app.cli:main` | `marivo.cli:main` |
| `tests/core/test_intent_registries.py` | `"app.core.intent..."`, `"app.service"`, etc. | `"marivo.core.intent..."`, `"marivo.service"`, etc. |

**Important:** The FastAPI application variable `app = FastAPI()` in
`marivo/main.py` is a local variable name, not a namespace reference. It is
**not** renamed. The uvicorn path `"marivo.main:app"` refers to the `app`
variable inside the `marivo.main` module.

### 4.4 Documentation references

Update any `.md`, `.yaml`, or `.rst` files that reference `app/` as a path or
`app.` as a Python module.

---

## 5. Configuration Cutover

### 5.1 pyproject.toml

| Section | Old | New |
|---------|-----|-----|
| `[project.scripts] marivo` | `app.cli:main` | `marivo.cli:main` |
| `[project.scripts] marivo-stdio` | `app.transports.mcp.stdio:main` | `marivo.transports.mcp.stdio:main` |
| `[tool.setuptools.packages.find] include` | `["app*"]` | `["marivo*"]` |
| `[tool.ruff.lint.isort] known-first-party` | `["app"]` | `["marivo"]` |
| `[tool.ruff.lint.per-file-ignores]` | `"app/api/**/*.py"` | `"marivo/api/**/*.py"` |
| `[tool.coverage.run] source` | `["app"]` | `["marivo"]` |

### 5.2 .importlinter (setup.cfg)

- `root_package = app` → `root_package = marivo`
- All `app.*` module references in every contract → `marivo.*`
- Add new contract:

```ini
[importlinter:contract:no-legacy-app-namespace]
name = No remaining app namespace imports
type = forbidden
source_modules =
    marivo
forbidden_modules =
    app
```

### 5.3 Makefile

- `$(VENV_MYPY) app` → `$(VENV_MYPY) marivo`

### 5.4 Other files

- `agent-guide.md`, `CONTRIBUTING.md`, `README.md` — update any `app/` path
  references to `marivo/`.
- `.github/` CI workflow files — update any hardcoded `app/` paths.

---

## 6. Architecture Invariants Document

Create `docs/architecture-invariants.md` with the 5 core invariants from the
platform architecture spec (§2):

1. **Core isolation:** `core/` must not import any adapter, transport, or
   storage library. Enforced by `importlinter:contract:core-no-io`.

2. **Domain-only ports:** Ports return domain objects and domain IDs — not ORM
   rows or SQL cursors. Enforced by code review; no automated linter rule.

3. **Surface → Runtime only:** Surfaces (API, CLI, MCP) call Runtime only —
   not Core Engine directly. Enforced by
   `importlinter:contract:surfaces-must-use-runtime`.

4. **Profile Factory exclusivity:** Profile Factory is the only place that
   knows which adapter wires to which port. Enforced by convention and review.

5. **Mode separation:** Local profile does not start HTTP service; enterprise
   profile does not maintain independent business logic. Enforced by
   architecture tests and review.

Each invariant entry includes: the rule, the rationale, how it is enforced
(linter contract name or review), and what a violation looks like.

---

## 7. Verification

After the single atomic commit:

| Step | Command | Expected |
|------|---------|----------|
| 1 | `make test` | All existing tests pass |
| 2 | `make lint` | Ruff + import-linter pass (including `no-legacy-app-namespace`) |
| 3 | `make typecheck` | Mypy passes against `marivo/` |
| 4 | `grep -r 'from app\.\|import app\.' --include='*.py' .` | Zero hits |
| 5 | `test ! -d app/` | `app/` directory does not exist |
| 6 | `test -f docs/architecture-invariants.md` | Invariants doc exists |

---

## 8. Risk & Mitigations

| Risk | Mitigation |
|------|------------|
| Sed over-matches `app` in unrelated strings | Manual review of string literals; targeted fixups in §4.3 |
| `git blame` lost for renamed files | `git mv` preserves history; `git log --follow` traces |
| Import-linter contracts miss a pattern | New `no-legacy-app-namespace` contract catches any `app` import |
| Dead code removal breaks something | Each candidate verified with grep; tests run after removal |
| `marivo-skill/` directory confusion | Not a Python package; different name; no conflict |
