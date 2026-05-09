# Legacy Package Drain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drain all 11 legacy top-level packages under `marivo/`, leaving only the target architecture packages: `contracts/`, `core/`, `ports/`, `runtime/`, `adapters/`, `transports/`, `profiles/`, `local/`.

**Architecture:** Three parallel tracks (Track 1: easy cleanups, Track 2: core refactoring, Track 3: physical renames). Each package is drained in one commit via big-bang moves — no temporary shims. Import linter stays enforced throughout. Each batch produces a validated commit where `lint-imports` + `pytest` pass and zero `from marivo.<old_package>` references remain.

**Tech Stack:** Python 3.12, import-linter, pytest, ruff, git

**Spec:** [`docs/superpowers/specs/2026-05-09-legacy-package-drain-design.md`](../specs/2026-05-09-legacy-package-drain-design.md)

---

## Execution Tracks & Dependencies

```
Track 1:  B1 ── B2                          (parallel, no deps)
Track 2:  B3 ── B5 ── B4 ── B6              (sequential chain; B1 must complete before B4)
Track 3:  B7 ── B8 ── B9 ── B10 ── B11      (sequential)
```

Cross-track: **B1 must complete before B4** (analysis_core/compiler.py imports from semantic_runtime, which B1 deletes).

**IMPORTANT: Execute tasks strictly in dependency order.** B3 before B5, B5 before B4, B4 before B6. B1 and B2 can run in parallel. Track 3 is independent until B7.

---

## File Structure Overview

### New directories to create
- `marivo/runtime/semantic/` — compilation, validation, resolution orchestration
- `marivo/runtime/intents/` — intent runners (moved from top-level)
- `marivo/runtime/evidence/` — evidence pipeline runtime
- `marivo/runtime/execution/` — orchestrator, federation
- `marivo/runtime/step_runners/` — step runners (from analysis_core)
- `marivo/runtime/workflows/` — workflow runtime (from analysis_core)
- `marivo/transports/http/` — HTTP API (moved from api/)
- `marivo/transports/cli/` — CLI commands (moved from cli/)
- `marivo/local/` — local-mode utilities

### Directories to delete (after drain)
- `marivo/semantic_runtime/`
- `marivo/registry/`
- `marivo/semantic_service_v2/`
- `marivo/intents/`
- `marivo/analysis_core/`
- `marivo/execution/`
- `marivo/storage/`
- `marivo/evidence_engine/`
- `marivo/api/`
- `marivo/cli/`

---

## Validation Pattern (every task ends with this)

Every task's final steps follow this pattern. Referenced as **"Run Validation Gate"** in each task:

```bash
# 1. No dangling imports from the drained package
grep -rn 'from marivo\.<OLD_PACKAGE>' --include='*.py' marivo/ tests/
# Expected: zero results

# 2. Old package directory fully removed
ls marivo/<OLD_PACKAGE>/ 2>&1
# Expected: "No such file or directory"

# 3. Import linter passes
.venv/bin/lint-imports
# Expected: exit 0

# 4. Tests pass
make test
# Expected: all pass, no new failures
```

---

## Task 1 (B1): Drain `semantic_runtime/` — 7 files

**Files:**
- Delete: `marivo/semantic_runtime/dimensions.py`
- Delete: `marivo/semantic_runtime/status_utils.py`
- Delete: `marivo/semantic_runtime/resolution.py` (delete stubs; check if `ResolvedSemanticObject` is used elsewhere — if yes, move to `marivo/core/semantic/`)
- Delete: `marivo/semantic_runtime/__init__.py`
- Move: `marivo/semantic_runtime/errors.py` → `marivo/runtime/errors.py`
- Move: `marivo/semantic_runtime/repository.py` → `marivo/runtime/evidence/semantic_repository.py`
- Move: `marivo/semantic_runtime/semantic_metadata.py` → `marivo/core/semantic/metadata.py`
- Modify: `marivo/runtime/__init__.py` — update exports
- Modify: `marivo/runtime/semantic_ops.py` — update imports
- Modify: `marivo/analysis_core/compiler.py` — update imports
- Modify: `marivo/execution/feedback.py` — update imports
- Modify: `marivo/evidence_engine/ref_boundary.py` — update imports
- Modify: `.importlinter` — remove `runtime.* -> semantic_runtime.*` wildcards
- Modify: All test files importing from `marivo.semantic_runtime`

- [ ] **Step 1: Audit all importers of `semantic_runtime`**

```bash
grep -rn 'from marivo\.semantic_runtime\|import marivo\.semantic_runtime' --include='*.py' marivo/ tests/
```

Record every file and the specific symbol imported. This determines which callers need updating.

- [ ] **Step 2: Check if `ResolvedSemanticObject` from `resolution.py` is used**

```bash
grep -rn 'ResolvedSemanticObject' --include='*.py' marivo/ tests/
```

If used outside `semantic_runtime/`, extract `ResolvedSemanticObject` to `marivo/core/semantic/resolution.py`. If only used internally or not used, delete entirely.

- [ ] **Step 3: Create target directories**

```bash
mkdir -p marivo/runtime/evidence
touch marivo/runtime/evidence/__init__.py
```

Ensure `marivo/core/semantic/` already exists (it does — has `__init__.py`).

- [ ] **Step 4: Move `errors.py` to `marivo/runtime/errors.py`**

```bash
git mv marivo/semantic_runtime/errors.py marivo/runtime/errors.py
```

Update all importers: `from marivo.semantic_runtime.errors import X` → `from marivo.runtime.errors import X`.

- [ ] **Step 5: Move `repository.py` to `marivo/runtime/evidence/semantic_repository.py`**

```bash
git mv marivo/semantic_runtime/repository.py marivo/runtime/evidence/semantic_repository.py
```

Update all importers: `from marivo.semantic_runtime.repository import X` → `from marivo.runtime.evidence.semantic_repository import X`.

- [ ] **Step 6: Move `semantic_metadata.py` to `marivo/core/semantic/metadata.py`**

```bash
git mv marivo/semantic_runtime/semantic_metadata.py marivo/core/semantic/metadata.py
```

Update all importers: `from marivo.semantic_runtime.semantic_metadata import X` → `from marivo.core.semantic.metadata import X`.

Also update internal imports inside the moved file (if it imports from `semantic_runtime.*`, fix to new locations).

- [ ] **Step 7: Delete stub files**

```bash
rm marivo/semantic_runtime/dimensions.py
rm marivo/semantic_runtime/status_utils.py
rm marivo/semantic_runtime/resolution.py  # or move ResolvedSemanticObject first if needed (Step 2)
```

Update any callers that import from these files. For `dimensions.py` (returns `[]`) and `status_utils.py` (returns constants), inline the constant values at call sites.

- [ ] **Step 8: Delete `marivo/semantic_runtime/__init__.py` and remove the directory**

```bash
rm marivo/semantic_runtime/__init__.py
rmdir marivo/semantic_runtime/  # or rm -r if __pycache__ remains
```

- [ ] **Step 9: Update `.importlinter`**

In `.importlinter`, the `runtime-no-direct-core-orchestration` contract has:
```
marivo.analysis_core.compiler -> marivo.evidence_engine.ref_boundary
```
This entry references `analysis_core` which still exists at this point — leave it. But remove any `runtime.* -> semantic_runtime.*` wildcard entries from `surfaces-must-use-runtime`:
```
marivo.runtime.* -> marivo.semantic_runtime.*
marivo.execution.* -> marivo.semantic_runtime.*
```

- [ ] **Step 10: Run Validation Gate**

```bash
grep -rn 'from marivo\.semantic_runtime' --include='*.py' marivo/ tests/
# Expected: zero results

ls marivo/semantic_runtime/ 2>&1
# Expected: No such file or directory

.venv/bin/lint-imports
# Expected: exit 0

make test
# Expected: all pass
```

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain semantic_runtime/ — move to runtime/, core/semantic/ (#B1)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 2 (B2): Drain `registry/` — 4 files

**Files:**
- Move: `marivo/registry/datasource_registry.py` → `marivo/adapters/server/datasource_registry.py`
- Move: `marivo/registry/factories.py` → `marivo/adapters/server/registry_factories.py`
- Move: `marivo/registry/common.py` → `marivo/adapters/server/registry_common.py`
- Delete: `marivo/registry/__init__.py`
- Modify: `marivo/adapters/server/data_source.py` — update imports
- Modify: `marivo/datasources.py` — update imports
- Modify: All test files importing from `marivo.registry`
- Modify: `.importlinter` — remove `marivo.registry` from `contracts-isolation` and `ports-isolation` forbidden lists

- [ ] **Step 1: Audit all importers of `registry`**

```bash
grep -rn 'from marivo\.registry\|import marivo\.registry' --include='*.py' marivo/ tests/
```

Record every importer. Only `adapters/server/` and `datasources.py` should import from it.

- [ ] **Step 2: Move files to `adapters/server/`**

```bash
git mv marivo/registry/datasource_registry.py marivo/adapters/server/datasource_registry.py
git mv marivo/registry/factories.py marivo/adapters/server/registry_factories.py
git mv marivo/registry/common.py marivo/adapters/server/registry_common.py
```

- [ ] **Step 3: Update internal imports in moved files**

In each moved file, update any `from marivo.registry.X import Y` to import from `marivo.adapters.server.X`. For example, if `datasource_registry.py` imports from `marivo.registry.common`, change to `from marivo.adapters.server.registry_common import ...`.

- [ ] **Step 4: Update all callers**

Update every file found in Step 1: `from marivo.registry.X` → `from marivo.adapters.server.X` (using the new filenames: `datasource_registry`, `registry_factories`, `registry_common`).

- [ ] **Step 5: Delete `marivo/registry/` directory**

```bash
rm marivo/registry/__init__.py
rm -rf marivo/registry/
```

- [ ] **Step 6: Update `.importlinter`**

Remove `marivo.registry` from the `forbidden_modules` lists in `contracts-isolation` and `ports-isolation` contracts. It's being moved into `adapters`, which is already covered.

- [ ] **Step 7: Run Validation Gate**

```bash
grep -rn 'from marivo\.registry' --include='*.py' marivo/ tests/
# Expected: zero results

ls marivo/registry/ 2>&1
# Expected: No such file or directory

.venv/bin/lint-imports
make test
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain registry/ — move to adapters/server/ (#B2)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 3 (B3): Drain `semantic_service_v2/` — 5 files

This package violates target architecture (imports HTTPException from FastAPI, SQLiteMetadataStore directly). Must be split during the move.

**Files:**
- Split: `marivo/semantic_service_v2/service.py` →
  - Orchestration logic → `marivo/runtime/semantic/semantic_service.py`
  - HTTP/storage coupling → `marivo/adapters/server/semantic_service_adapter.py`
- Move: `marivo/semantic_service_v2/storage.py` → merge into `marivo/adapters/server/model_store.py`
- Move: `marivo/semantic_service_v2/validation.py` → `marivo/runtime/semantic/semantic_validation.py` (if I/O) or `marivo/core/semantic/validator.py` (if pure)
- Move: `marivo/semantic_service_v2/extensions.py` → `marivo/core/semantic/extensions.py`
- Delete: `marivo/semantic_service_v2/__init__.py`
- Modify: `marivo/profiles/server.py` — rewire to use `adapters/server/model_store.py`
- Modify: `marivo/api/deps.py` — update imports
- Modify: `marivo/api/semantic_v2.py` — update imports
- Modify: `.importlinter` — remove `marivo.semantic_service_v2` from all forbidden lists

- [ ] **Step 1: Audit all importers**

```bash
grep -rn 'from marivo\.semantic_service_v2\|import marivo\.semantic_service_v2' --include='*.py' marivo/ tests/
```

- [ ] **Step 2: Read `service.py` and classify each function**

Read `marivo/semantic_service_v2/service.py` (1374 lines). Classify each method/function:
- **Orchestration** (business logic, no HTTP/storage coupling) → `runtime/semantic/semantic_service.py`
- **HTTP-coupled** (references `HTTPException`, FastAPI deps) → `adapters/server/semantic_service_adapter.py`

In the runtime version, replace `HTTPException` raises with domain error raises (e.g., `from marivo.contracts.errors import NotFoundError`). The adapter catches domain errors and converts to `HTTPException`.

- [ ] **Step 3: Create target directories**

```bash
mkdir -p marivo/runtime/semantic
touch marivo/runtime/semantic/__init__.py
```

- [ ] **Step 4: Split `service.py`**

Create `marivo/runtime/semantic/semantic_service.py` with orchestration logic. Create `marivo/adapters/server/semantic_service_adapter.py` with HTTP/storage coupling. The adapter imports from the runtime service and wraps calls with HTTP error handling.

- [ ] **Step 5: Read `validation.py` and classify**

```bash
grep -n 'import\|from.*import' marivo/semantic_service_v2/validation.py
```

If it imports I/O modules (storage, HTTP), move to `marivo/runtime/semantic/semantic_validation.py`. If pure logic only, move to `marivo/core/semantic/validator.py` (check for name conflicts with existing `marivo/core/semantic/validator.py`).

- [ ] **Step 6: Move `validation.py` to determined target**

```bash
git mv marivo/semantic_service_v2/validation.py marivo/runtime/semantic/semantic_validation.py
# OR if pure:
# git mv marivo/semantic_service_v2/validation.py marivo/core/semantic/ssv2_validator.py
```

- [ ] **Step 7: Move `extensions.py` to `core/semantic/extensions.py`**

```bash
git mv marivo/semantic_service_v2/extensions.py marivo/core/semantic/extensions.py
```

Update callers.

- [ ] **Step 8: Merge `storage.py` into `adapters/server/model_store.py`**

Read `marivo/semantic_service_v2/storage.py` and `marivo/adapters/server/model_store.py`. Merge the storage logic from `storage.py` into `model_store.py`. Delete `storage.py`.

- [ ] **Step 9: Update all callers**

- `marivo/profiles/server.py` — rewire from `semantic_service_v2.service` to `adapters/server/model_store.py` or `adapters/server/semantic_service_adapter.py`
- `marivo/api/deps.py` — update DI to use new locations
- `marivo/api/semantic_v2.py` — update service imports
- All test files

- [ ] **Step 10: Delete `marivo/semantic_service_v2/` directory**

```bash
rm -rf marivo/semantic_service_v2/
```

- [ ] **Step 11: Update `.importlinter`**

Remove `marivo.semantic_service_v2` from forbidden lists in `contracts-isolation`, `ports-isolation`, `core-no-io`.

- [ ] **Step 12: Run Validation Gate**

```bash
grep -rn 'from marivo\.semantic_service_v2' --include='*.py' marivo/ tests/
.venv/bin/lint-imports
make test
```

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain semantic_service_v2/ — split to runtime/semantic/, adapters/server/ (#B3)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 4 (B5): Drain `intents/` — 14 files

**Depends on:** Task 3 (B3)

Straight `git mv` to `runtime/intents/` sub-package.

**Files:**
- Move: All 10 intent runners → `marivo/runtime/intents/`
  - `attribute.py`, `compare.py`, `correlate.py`, `decompose.py`, `detect.py`, `diagnose.py`, `forecast.py`, `observe.py`, `test.py`, `validate.py`
- Move: `marivo/intents/_helpers.py` → `marivo/runtime/intents/_helpers.py`
- Move: `marivo/intents/calendar_alignment_metadata.py` → `marivo/runtime/intents/calendar_alignment_metadata.py`
- Move: `marivo/intents/predicate_lineage_reuse.py` → `marivo/runtime/intents/predicate_lineage_reuse.py`
- Move: `marivo/intents/__init__.py` → `marivo/runtime/intents/__init__.py` (update re-exports)
- Modify: `marivo/runtime/intent_execution.py` — `marivo.intents.*` → `marivo.runtime.intents.*`
- Modify: `.importlinter` — update `marivo.intents.*` → `marivo.runtime.intents.*` entries
- Modify: All test files importing from `marivo.intents`

- [ ] **Step 1: Audit all importers**

```bash
grep -rn 'from marivo\.intents\b\|import marivo\.intents\b' --include='*.py' marivo/ tests/
```

- [ ] **Step 2: Create target directory**

```bash
mkdir -p marivo/runtime/intents
```

- [ ] **Step 3: Move all files**

```bash
git mv marivo/intents/__init__.py marivo/runtime/intents/__init__.py
git mv marivo/intents/_helpers.py marivo/runtime/intents/_helpers.py
git mv marivo/intents/attribute.py marivo/runtime/intents/attribute.py
git mv marivo/intents/calendar_alignment_metadata.py marivo/runtime/intents/calendar_alignment_metadata.py
git mv marivo/intents/compare.py marivo/runtime/intents/compare.py
git mv marivo/intents/correlate.py marivo/runtime/intents/correlate.py
git mv marivo/intents/decompose.py marivo/runtime/intents/decompose.py
git mv marivo/intents/detect.py marivo/runtime/intents/detect.py
git mv marivo/intents/diagnose.py marivo/runtime/intents/diagnose.py
git mv marivo/intents/forecast.py marivo/runtime/intents/forecast.py
git mv marivo/intents/observe.py marivo/runtime/intents/observe.py
git mv marivo/intents/predicate_lineage_reuse.py marivo/runtime/intents/predicate_lineage_reuse.py
git mv marivo/intents/test.py marivo/runtime/intents/test.py
git mv marivo/intents/validate.py marivo/runtime/intents/validate.py
```

- [ ] **Step 4: Update internal imports in moved files**

Each intent file may import `from marivo.intents._helpers` or `from marivo.intents.X`. Update all to `from marivo.runtime.intents.X`.

- [ ] **Step 5: Update `runtime/intent_execution.py`**

All `from marivo.intents.X import Y` → `from marivo.runtime.intents.X import Y`.

- [ ] **Step 6: Update all external callers and test files**

Every file found in Step 1 needs `marivo.intents` → `marivo.runtime.intents`.

- [ ] **Step 7: Delete `marivo/intents/` directory**

```bash
rm -rf marivo/intents/
```

- [ ] **Step 8: Update `.importlinter`**

In `runtime-no-direct-core-orchestration`:
- Remove: `marivo.intents.* -> marivo.analysis_core.*`
- Remove: `marivo.intents.* -> marivo.evidence_engine.*`

These are no longer needed because after moving intents to `runtime/intents/`, the existing `marivo.runtime.*` wildcards already cover these imports.

In `surfaces-must-use-runtime`:
- Remove: `marivo.intents.* -> marivo.analysis_core.*`
- Remove: `marivo.intents.* -> marivo.evidence_engine.*`

- [ ] **Step 9: Run Validation Gate**

```bash
grep -rn 'from marivo\.intents\b' --include='*.py' marivo/ tests/
# Must be zero (note: marivo.runtime.intents is fine)

ls marivo/intents/ 2>&1
# Expected: No such file or directory

.venv/bin/lint-imports
make test
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain intents/ — move to runtime/intents/ (#B5)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 5 (B4): Drain `analysis_core/` — 23 files

**Depends on:** Task 1 (B1), Task 4 (B5)

Delete re-export shims. Extract I/O-bound functions. Move remaining I/O modules to runtime/.

**Files:**

Delete (re-export shims):
- `marivo/analysis_core/ir.py` — re-export of `core.semantic.ir`
- `marivo/analysis_core/step_registry.py` — re-export of `core.intent.step_registry`
- `marivo/analysis_core/intent_registry.py` — re-export of `core.intent.intent_registry`
- `marivo/analysis_core/primitives.py` — re-export of `core.intent.primitives`
- `marivo/analysis_core/additivity_capabilities.py` — re-export of `core.semantic.additivity`
- `marivo/analysis_core/calendar_alignment_baseline.py` — re-export of `core.semantic.calendar`
- `marivo/analysis_core/calendar_alignment_pairing.py` — re-export of `core.semantic.calendar`
- `marivo/analysis_core/calendar_policy.py` — re-export of `core.semantic.calendar`
- `marivo/analysis_core/capability_profiles.py` — stub for import compat
- `marivo/analysis_core/predicate_validator.py` — stub for import compat

Extract I/O-bound functions (delete pure duplicates):
- `marivo/analysis_core/compiler.py` → extract `compile_step()` to `marivo/runtime/semantic/compile_step.py`
- `marivo/analysis_core/typed_resolution.py` → extract `normalize_step_request()`, `resolve_compiler_inputs()` to `marivo/runtime/semantic/resolution_orchestrator.py`
- `marivo/analysis_core/validator.py` → extract `validate_compiler_inputs()` to `marivo/runtime/semantic/analysis_validator.py`

Move to runtime/ (I/O-bound, no core/ equivalent):
- `marivo/analysis_core/executor.py` → `marivo/runtime/semantic/executor.py`
- `marivo/analysis_core/step_runners/` → `marivo/runtime/step_runners/`
- `marivo/analysis_core/workflows/` → `marivo/runtime/workflows/`
- `marivo/analysis_core/calendar_data_runtime.py` → `marivo/runtime/semantic/calendar_data_runtime.py`
- `marivo/analysis_core/composites.py` → `marivo/runtime/semantic/composites.py`
- `marivo/analysis_core/__init__.py` — delete

Modify callers:
- `marivo/runtime/semantic_ops.py`
- `marivo/runtime/step_executor.py`
- `marivo/runtime/intents/` (all intent files, now at new location from B5)
- `marivo/execution/orchestrator.py`
- `marivo/profiles/`
- `.importlinter`
- All test files

- [ ] **Step 1: Audit all importers**

```bash
grep -rn 'from marivo\.analysis_core\|import marivo\.analysis_core' --include='*.py' marivo/ tests/
```

Categorize each import: is the imported symbol a re-export from `core.*`, or unique I/O logic?

- [ ] **Step 2: Verify re-export shims**

For each file marked as "re-export shim", confirm it only re-exports from `core.*`:

```bash
head -30 marivo/analysis_core/ir.py
head -30 marivo/analysis_core/step_registry.py
head -30 marivo/analysis_core/intent_registry.py
head -30 marivo/analysis_core/primitives.py
head -30 marivo/analysis_core/additivity_capabilities.py
head -30 marivo/analysis_core/capability_profiles.py
head -30 marivo/analysis_core/predicate_validator.py
```

Verify callers already have access to `core.*` equivalents.

- [ ] **Step 3: Update callers of re-export shims to use `core.*` directly**

For every caller of a re-export shim, change:
- `from marivo.analysis_core.ir import X` → `from marivo.core.semantic.ir import X`
- `from marivo.analysis_core.step_registry import X` → `from marivo.core.intent.step_registry import X`
- `from marivo.analysis_core.intent_registry import X` → `from marivo.core.intent.intent_registry import X`
- `from marivo.analysis_core.primitives import X` → `from marivo.core.intent.primitives import X`
- `from marivo.analysis_core.additivity_capabilities import X` → `from marivo.core.semantic.additivity import X`
- `from marivo.analysis_core.calendar_* import X` → `from marivo.core.semantic.calendar import X`

Delete the shim files.

- [ ] **Step 4: Extract `compile_step()` from `compiler.py`**

Read `marivo/analysis_core/compiler.py` (1959 lines). Identify `compile_step()` and any I/O-bound helpers it uses. Create `marivo/runtime/semantic/compile_step.py` with only those functions.

Callers of pure re-exports (already in `core.semantic.compiler`) should be redirected to `core.semantic.compiler`. Only `compile_step()` goes to `runtime/semantic/compile_step.py`.

- [ ] **Step 5: Extract I/O functions from `typed_resolution.py`**

Read `marivo/analysis_core/typed_resolution.py` (865 lines). Extract `normalize_step_request()` and `resolve_compiler_inputs()` to `marivo/runtime/semantic/resolution_orchestrator.py`. Delete pure data classes that already exist in `core.semantic.typed_resolution`.

- [ ] **Step 6: Extract I/O function from `validator.py`**

Read `marivo/analysis_core/validator.py` (1144 lines). Extract `validate_compiler_inputs()` to `marivo/runtime/semantic/analysis_validator.py`. Delete pure re-exports already in `core.semantic.validator`.

- [ ] **Step 7: Move I/O-bound modules**

```bash
git mv marivo/analysis_core/executor.py marivo/runtime/semantic/executor.py
git mv marivo/analysis_core/calendar_data_runtime.py marivo/runtime/semantic/calendar_data_runtime.py
git mv marivo/analysis_core/composites.py marivo/runtime/semantic/composites.py
git mv marivo/analysis_core/step_runners/ marivo/runtime/step_runners/
git mv marivo/analysis_core/workflows/ marivo/runtime/workflows/
```

- [ ] **Step 8: Update all callers to new locations**

Update every file found in Step 1:
- `marivo/runtime/semantic_ops.py` — compiler, executor imports
- `marivo/runtime/step_executor.py` — step_runners import
- `marivo/runtime/intents/*.py` — all analysis_core imports
- `marivo/execution/orchestrator.py` — workflow imports
- Test files

- [ ] **Step 9: Delete remaining `analysis_core/` files and directory**

```bash
rm -rf marivo/analysis_core/
```

- [ ] **Step 10: Update `.importlinter`**

Remove all `analysis_core` entries from `runtime-no-direct-core-orchestration`:
```
marivo.runtime.semantic_ops -> marivo.analysis_core.compiler       # REMOVE
marivo.runtime.semantic_ops -> marivo.analysis_core.executor       # REMOVE
marivo.runtime.step_executor -> marivo.analysis_core.step_runners  # REMOVE
```

Remove from `surfaces-must-use-runtime`:
```
marivo.runtime.* -> marivo.analysis_core.*   # REMOVE
```

Remove `marivo.analysis_core` from `contracts-isolation`, `ports-isolation`, `core-no-io` forbidden lists.

**Add new chain entry** (moved file creates new violation):
```
marivo.runtime.semantic.compile_step -> marivo.evidence_engine.ref_boundary
```
Add this to `runtime-no-direct-core-orchestration` `ignore_imports`. Also remove the old entry:
```
marivo.analysis_core.compiler -> marivo.evidence_engine.ref_boundary  # REMOVE
```

- [ ] **Step 11: Run Validation Gate**

```bash
grep -rn 'from marivo\.analysis_core' --include='*.py' marivo/ tests/
.venv/bin/lint-imports
make test
```

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain analysis_core/ — delete shims, extract I/O to runtime/ (#B4)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 6 (B6): Drain `execution/` — 8 files

**Depends on:** Task 5 (B4)

Split into runtime orchestration + adapter implementations.

**Files:**
- Move: `marivo/execution/orchestrator.py` → `marivo/runtime/execution/orchestrator.py`
- Move: `marivo/execution/feedback.py` → `marivo/runtime/semantic/feedback.py`
- Move: `marivo/execution/routing_runtime.py` → `marivo/adapters/server/routing_runtime.py`
- Move: `marivo/execution/translation.py` → `marivo/adapters/server/translation.py`
- Move: `marivo/execution/federation.py` → `marivo/runtime/execution/federation.py`
- Move: `marivo/execution/capabilities.py` → `marivo/contracts/capabilities.py`
- Merge: `marivo/execution/errors.py` → into `marivo/contracts/errors.py`
- Delete: `marivo/execution/__init__.py`
- Modify: `marivo/adapters/server/data_source.py` — update imports
- Modify: `marivo/runtime/semantic_ops.py` — update imports
- Modify: `.importlinter` — remove `marivo.execution` from all forbidden lists
- Modify: All test files

- [ ] **Step 1: Audit all importers**

```bash
grep -rn 'from marivo\.execution\b\|import marivo\.execution\b' --include='*.py' marivo/ tests/
```

- [ ] **Step 2: Create target directories**

```bash
mkdir -p marivo/runtime/execution
touch marivo/runtime/execution/__init__.py
```

- [ ] **Step 3: Move files**

```bash
git mv marivo/execution/orchestrator.py marivo/runtime/execution/orchestrator.py
git mv marivo/execution/feedback.py marivo/runtime/semantic/feedback.py
git mv marivo/execution/routing_runtime.py marivo/adapters/server/routing_runtime.py
git mv marivo/execution/translation.py marivo/adapters/server/translation.py
git mv marivo/execution/federation.py marivo/runtime/execution/federation.py
git mv marivo/execution/capabilities.py marivo/contracts/capabilities.py
```

- [ ] **Step 4: Merge `errors.py` into `contracts/errors.py`**

Read `marivo/execution/errors.py` (31 lines) and `marivo/contracts/errors.py` (65 lines). Append the error classes from `execution/errors.py` into `contracts/errors.py`. Delete `execution/errors.py`.

Update all callers: `from marivo.execution.errors import X` → `from marivo.contracts.errors import X`.

- [ ] **Step 5: Update all callers**

Update imports in all files found in Step 1 to point to new locations.

- [ ] **Step 6: Update internal imports in moved files**

Each moved file may import from `marivo.execution.*` — update those to the new locations.

- [ ] **Step 7: Delete `marivo/execution/` directory**

```bash
rm -rf marivo/execution/
```

- [ ] **Step 8: Update `.importlinter`**

Remove `marivo.execution` from `contracts-isolation` and `ports-isolation` forbidden lists. Remove any `marivo.execution.*` entries from `surfaces-must-use-runtime` ignore list:
```
marivo.execution.* -> marivo.semantic_runtime.*  # REMOVE (already gone after B1)
```

- [ ] **Step 9: Run Validation Gate**

```bash
grep -rn 'from marivo\.execution\b' --include='*.py' marivo/ tests/
.venv/bin/lint-imports
make test
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain execution/ — split to runtime/execution/, adapters/server/, contracts/ (#B6)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 7 (B7): Drain `storage/` — 12 files

**Files:**
- Move: `marivo/storage/sqlite_metadata.py` → `marivo/adapters/local/sqlite_metadata.py`
- Move: `marivo/storage/duckdb_analytics.py` → `marivo/adapters/local/duckdb_analytics.py`
- Move: `marivo/storage/mysql_metadata.py` → `marivo/adapters/server/mysql_metadata.py`
- Move: `marivo/storage/trino_analytics.py` → `marivo/adapters/server/trino_analytics.py`
- Move: `marivo/storage/analytics.py` (protocol) → `marivo/ports/analytics.py`
- Move: `marivo/storage/evidence_repositories.py` → `marivo/adapters/server/evidence_repositories.py`
- Move: `marivo/storage/step_metadata_repository.py` → `marivo/adapters/server/step_metadata_repository.py`
- Move: `marivo/storage/metadata.py` → `marivo/adapters/metadata.py`
- Move: `marivo/storage/schema.py` → `marivo/adapters/schema.py`
- Move: `marivo/storage/repositories.py` → `marivo/adapters/repositories.py`
- Move: `marivo/storage/dialect.py` → `marivo/adapters/dialect.py`
- Delete: `marivo/storage/__init__.py`
- Modify: `marivo/profiles/server.py` — update imports
- Modify: `marivo/adapters/server/*.py` — update imports
- Modify: `.importlinter` — remove `marivo.storage` from all forbidden lists
- Modify: All test files (37+ locations)

Note: `storage/factories.py` does not exist in the current codebase — skip that entry from the spec.

- [ ] **Step 1: Audit all importers (critical — 37+ locations)**

```bash
grep -rn 'from marivo\.storage\b\|import marivo\.storage\b' --include='*.py' marivo/ tests/ | head -80
```

This is the most heavily imported package. Create a mapping of old → new for every import pattern.

- [ ] **Step 2: Move implementation files**

```bash
git mv marivo/storage/sqlite_metadata.py marivo/adapters/local/sqlite_metadata.py
git mv marivo/storage/duckdb_analytics.py marivo/adapters/local/duckdb_analytics.py
git mv marivo/storage/mysql_metadata.py marivo/adapters/server/mysql_metadata.py
git mv marivo/storage/trino_analytics.py marivo/adapters/server/trino_analytics.py
git mv marivo/storage/evidence_repositories.py marivo/adapters/server/evidence_repositories.py
git mv marivo/storage/step_metadata_repository.py marivo/adapters/server/step_metadata_repository.py
```

- [ ] **Step 3: Move protocol to ports**

```bash
git mv marivo/storage/analytics.py marivo/ports/analytics.py
```

- [ ] **Step 4: Move shared infrastructure to adapters/**

```bash
git mv marivo/storage/metadata.py marivo/adapters/metadata.py
git mv marivo/storage/schema.py marivo/adapters/schema.py
git mv marivo/storage/repositories.py marivo/adapters/repositories.py
git mv marivo/storage/dialect.py marivo/adapters/dialect.py
```

- [ ] **Step 5: Update ALL callers (37+ locations)**

Systematically update every import. Key patterns:
- `from marivo.storage.metadata import MetadataStore` → `from marivo.adapters.metadata import MetadataStore`
- `from marivo.storage.analytics import AnalyticsEngine` → `from marivo.ports.analytics import AnalyticsEngine`
- `from marivo.storage.sqlite_metadata import X` → `from marivo.adapters.local.sqlite_metadata import X`
- `from marivo.storage.schema import X` → `from marivo.adapters.schema import X`
- etc.

Use find-and-replace across all `.py` files. Update test files too.

- [ ] **Step 6: Update internal imports in moved files**

Each moved file may reference other `marivo.storage.*` modules. Update to new locations.

- [ ] **Step 7: Delete `marivo/storage/` directory**

```bash
rm -rf marivo/storage/
```

- [ ] **Step 8: Update `.importlinter`**

Remove `marivo.storage` from forbidden lists in `contracts-isolation`, `ports-isolation`, `core-no-io`. The moved files are now in `adapters/` and `ports/`, which are covered by existing contracts.

- [ ] **Step 9: Run Validation Gate**

```bash
grep -rn 'from marivo\.storage\b' --include='*.py' marivo/ tests/
.venv/bin/lint-imports
make test
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain storage/ — move to adapters/, ports/ (#B7)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 8 (B8): Drain `evidence_engine/` — 27 files

**Depends on:** Task 7 (B7)

Largest and most complex drain. Delete shims/deprecated extractors, split remaining into runtime/core/adapters.

**Files:**

Delete (shims/deprecated):
- `marivo/evidence_engine/family_contract.py` — re-export shim from `core.evidence.family_contract`
- 7 deprecated extractors: `compare_extractor.py`, `observe_extractor.py`, `detect_extractor.py`, `decompose_extractor.py`, `test_extractor.py`, `forecast_extractor.py`, `correlate_extractor.py` — delegate to `core.evidence.finding_extraction`

Move to `runtime/evidence/` (I/O-bound):
- `canonical_pipeline_runtime.py` → `runtime/evidence/canonical_pipeline.py`
- `state_view.py` → `runtime/evidence/state_view.py`
- `context_view.py` → `runtime/evidence/context_view.py`
- `ref_boundary.py` → `runtime/evidence/ref_boundary.py`
- `publish_switch.py` → `runtime/evidence/publish_switch.py`
- `invalidation.py` → `runtime/evidence/invalidation.py`
- `replay_recovery.py` → `runtime/evidence/replay_recovery.py`
- `proposal_refresh_run.py` → `runtime/evidence/proposal_refresh.py`
- `proposition_registration.py` → `runtime/evidence/proposition_registration.py`
- `proposition_seed_registry.py` → `runtime/evidence/proposition_seed_registry.py`
- `assessment_evaluation_context.py` → `runtime/evidence/assessment_context.py`
- `assessment_recompute.py` → `runtime/evidence/assessment_recompute.py`
- `proposition_seeding_run.py` → `runtime/evidence/proposition_seeding.py`
- `finding_extractor_registry.py` → `runtime/evidence/finding_extractor_registry.py`

Move to `core/evidence/` (pure logic):
- `version_policy.py` → `core/evidence/version_policy.py`
- `canonical_refs.py` → `core/evidence/canonical_refs.py`
- `proposition_normalizer.py` → `core/evidence/proposition_normalizer.py`

Split (violates spec):
- `canonical_finding.py` (703 lines) → pure logic → `core/evidence/canonical_finding.py`, I/O methods → `runtime/evidence/canonical_finding_ops.py`

Delete: `marivo/evidence_engine/__init__.py`

- [ ] **Step 1: Audit all importers (60+ test imports)**

```bash
grep -rn 'from marivo\.evidence_engine\|import marivo\.evidence_engine' --include='*.py' marivo/ tests/ | wc -l
grep -rn 'from marivo\.evidence_engine\|import marivo\.evidence_engine' --include='*.py' marivo/ tests/
```

- [ ] **Step 2: Verify deprecated extractors delegate to `core.evidence.finding_extraction`**

```bash
head -20 marivo/evidence_engine/compare_extractor.py
head -20 marivo/evidence_engine/observe_extractor.py
head -20 marivo/evidence_engine/detect_extractor.py
```

Confirm they are shims. If any extractor has unique logic not in `core.evidence.finding_extraction`, it needs extraction instead of deletion.

- [ ] **Step 3: Verify `family_contract.py` is a re-export shim**

```bash
cat marivo/evidence_engine/family_contract.py
```

Confirm it only re-exports from `core.evidence.family_contract`.

- [ ] **Step 4: Update callers of shims/deprecated extractors**

For `family_contract.py` callers: `from marivo.evidence_engine.family_contract import X` → `from marivo.core.evidence.family_contract import X`.

For deprecated extractor callers: update to import from `core.evidence.finding_extraction`.

- [ ] **Step 5: Delete shim and deprecated files**

```bash
rm marivo/evidence_engine/family_contract.py
rm marivo/evidence_engine/compare_extractor.py
rm marivo/evidence_engine/observe_extractor.py
rm marivo/evidence_engine/detect_extractor.py
rm marivo/evidence_engine/decompose_extractor.py
rm marivo/evidence_engine/test_extractor.py
rm marivo/evidence_engine/forecast_extractor.py
rm marivo/evidence_engine/correlate_extractor.py
```

- [ ] **Step 6: Split `canonical_finding.py`**

Read `marivo/evidence_engine/canonical_finding.py` (703 lines). Separate:
- Pure logic (dataclasses, value objects, computation) → `marivo/core/evidence/canonical_finding.py`
- I/O-bound methods (repository access, pipeline integration) → `marivo/runtime/evidence/canonical_finding_ops.py`

Update callers accordingly.

- [ ] **Step 7: Move pure logic files to `core/evidence/`**

```bash
git mv marivo/evidence_engine/version_policy.py marivo/core/evidence/version_policy.py
git mv marivo/evidence_engine/canonical_refs.py marivo/core/evidence/canonical_refs.py
git mv marivo/evidence_engine/proposition_normalizer.py marivo/core/evidence/proposition_normalizer.py
```

- [ ] **Step 8: Move I/O-bound files to `runtime/evidence/`**

```bash
git mv marivo/evidence_engine/canonical_pipeline_runtime.py marivo/runtime/evidence/canonical_pipeline.py
git mv marivo/evidence_engine/state_view.py marivo/runtime/evidence/state_view.py
git mv marivo/evidence_engine/context_view.py marivo/runtime/evidence/context_view.py
git mv marivo/evidence_engine/ref_boundary.py marivo/runtime/evidence/ref_boundary.py
git mv marivo/evidence_engine/publish_switch.py marivo/runtime/evidence/publish_switch.py
git mv marivo/evidence_engine/invalidation.py marivo/runtime/evidence/invalidation.py
git mv marivo/evidence_engine/replay_recovery.py marivo/runtime/evidence/replay_recovery.py
git mv marivo/evidence_engine/proposal_refresh_run.py marivo/runtime/evidence/proposal_refresh.py
git mv marivo/evidence_engine/proposition_registration.py marivo/runtime/evidence/proposition_registration.py
git mv marivo/evidence_engine/proposition_seed_registry.py marivo/runtime/evidence/proposition_seed_registry.py
git mv marivo/evidence_engine/assessment_evaluation_context.py marivo/runtime/evidence/assessment_context.py
git mv marivo/evidence_engine/assessment_recompute.py marivo/runtime/evidence/assessment_recompute.py
git mv marivo/evidence_engine/proposition_seeding_run.py marivo/runtime/evidence/proposition_seeding.py
git mv marivo/evidence_engine/finding_extractor_registry.py marivo/runtime/evidence/finding_extractor_registry.py
```

- [ ] **Step 9: Update ALL callers (60+ locations)**

Systematically update every import. Key mappings:
- `marivo.evidence_engine.ref_boundary` → `marivo.runtime.evidence.ref_boundary`
- `marivo.evidence_engine.context_view` → `marivo.runtime.evidence.context_view`
- `marivo.evidence_engine.state_view` → `marivo.runtime.evidence.state_view`
- `marivo.evidence_engine.canonical_finding` → split between `marivo.core.evidence.canonical_finding` and `marivo.runtime.evidence.canonical_finding_ops`
- etc.

Update both source files and test files.

- [ ] **Step 10: Delete `marivo/evidence_engine/` directory**

```bash
rm -rf marivo/evidence_engine/
```

- [ ] **Step 11: Update `.importlinter`**

In `runtime-no-direct-core-orchestration`, remove all `evidence_engine` entries:
```
marivo.runtime.semantic_ops -> marivo.evidence_engine.ref_boundary        # REMOVE
marivo.runtime.session -> marivo.evidence_engine.context_view             # REMOVE
marivo.runtime.session -> marivo.evidence_engine.state_view               # REMOVE
marivo.runtime.semantic.compile_step -> marivo.evidence_engine.ref_boundary  # REMOVE (added in B4)
```

In `surfaces-must-use-runtime`, remove:
```
marivo.runtime.* -> marivo.evidence_engine.*                              # REMOVE
marivo.analysis_core.* -> marivo.evidence_engine.*                        # REMOVE (already gone)
marivo.adapters.server.artifact_store -> marivo.evidence_engine.canonical_finding  # REMOVE
```

Remove `marivo.evidence_engine` from `contracts-isolation`, `ports-isolation`, `core-no-io` forbidden lists.

- [ ] **Step 12: Run Validation Gate**

```bash
grep -rn 'from marivo\.evidence_engine' --include='*.py' marivo/ tests/
.venv/bin/lint-imports
make test
```

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain evidence_engine/ — delete shims, split to runtime/evidence/, core/evidence/ (#B8)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 9 (B9): Rename `api/` → `transports/http/` — 25 files

**Depends on:** Task 8 (B8)

Big-bang rename. Every `from marivo.api.*` becomes `from marivo.transports.http.*`.

**Files:**
- Move: `marivo/api/app_factory.py` → `marivo/transports/http/app_factory.py`
- Move: `marivo/api/deps.py` → `marivo/transports/http/deps.py`
- Move: `marivo/api/sessions.py` → `marivo/transports/http/sessions.py`
- Move: `marivo/api/middleware.py` → `marivo/transports/http/middleware.py`
- Move: `marivo/api/models/` → `marivo/transports/http/models/`
- Move: `marivo/api/endpoints/` → `marivo/transports/http/endpoints/` (if exists)
- Move: All other `marivo/api/*.py` → `marivo/transports/http/`
- Modify: `marivo/transports/mcp/http.py` — mounts API app
- Modify: `marivo/profiles/server.py` — imports `api.app_factory`
- Modify: `marivo/main.py` — application entry point
- Modify: `.importlinter` — update contract module references
- Modify: All test files

- [ ] **Step 1: Audit all importers**

```bash
grep -rn 'from marivo\.api\b\|import marivo\.api\b' --include='*.py' marivo/ tests/
```

- [ ] **Step 2: Create target directory**

```bash
mkdir -p marivo/transports/http
```

- [ ] **Step 3: Move all files and subdirectories**

```bash
# Move all Python files
for f in marivo/api/*.py; do
    [ "$f" = "marivo/api/__init__.py" ] && continue
    git mv "$f" "marivo/transports/http/$(basename $f)"
done

# Move subdirectories
git mv marivo/api/models marivo/transports/http/models

# Move __init__.py last
git mv marivo/api/__init__.py marivo/transports/http/__init__.py
```

- [ ] **Step 4: Update all imports globally**

Find-and-replace across all `.py` files:
- `from marivo.api.` → `from marivo.transports.http.`
- `import marivo.api.` → `import marivo.transports.http.`
- `marivo.api` (in string references, e.g., linter config)

Also update internal imports within the moved files themselves.

- [ ] **Step 5: Delete `marivo/api/` directory**

```bash
rm -rf marivo/api/
```

- [ ] **Step 6: Update `.importlinter`**

In `transports-mcp-no-api-internals`:
```ini
forbidden_modules =
    marivo.api.endpoints
```
→
```ini
forbidden_modules =
    marivo.transports.http.endpoints
```

In `surfaces-must-use-runtime`, update source_modules:
```ini
source_modules =
    marivo.api
```
→
```ini
source_modules =
    marivo.transports.http
```

Remove `marivo.api` from `contracts-isolation`, `ports-isolation`, `core-no-io` forbidden lists (replace with `marivo.transports.http` if needed).

- [ ] **Step 7: Run Validation Gate**

```bash
grep -rn 'from marivo\.api\b' --include='*.py' marivo/ tests/
.venv/bin/lint-imports
make test
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain api/ — rename to transports/http/ (#B9)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 10 (B10): Rename `cli/` → `transports/cli/` — 12 files

**Depends on:** Task 9 (B9)

Big-bang rename.

**Files:**
- Move: All `marivo/cli/*.py` → `marivo/transports/cli/`
- Modify: Entry points in `pyproject.toml` (if cli entry point references `marivo.cli`)
- Modify: `marivo/main.py` — if it imports from `marivo.cli`
- Modify: `.importlinter` — update source_modules
- Modify: All test files

- [ ] **Step 1: Audit all importers**

```bash
grep -rn 'from marivo\.cli\b\|import marivo\.cli\b' --include='*.py' marivo/ tests/
```

Also check `pyproject.toml` for entry points:
```bash
grep -n 'marivo\.cli' pyproject.toml
```

- [ ] **Step 2: Create target directory**

```bash
mkdir -p marivo/transports/cli
```

- [ ] **Step 3: Move all files**

```bash
for f in marivo/cli/*.py; do
    git mv "$f" "marivo/transports/cli/$(basename $f)"
done
```

- [ ] **Step 4: Update all imports and entry points**

- All `.py` files: `from marivo.cli.` → `from marivo.transports.cli.`
- `pyproject.toml` entry points: `marivo.cli:` → `marivo.transports.cli:`
- Internal imports within moved files

- [ ] **Step 5: Delete `marivo/cli/` directory**

```bash
rm -rf marivo/cli/
```

- [ ] **Step 6: Update `.importlinter`**

In `surfaces-must-use-runtime`, update source_modules:
```
marivo.cli
```
→
```
marivo.transports.cli
```

Remove `marivo.cli` from `contracts-isolation`, `ports-isolation`, `core-no-io` forbidden lists (replace with `marivo.transports.cli` if needed).

- [ ] **Step 7: Run Validation Gate**

```bash
grep -rn 'from marivo\.cli\b' --include='*.py' marivo/ tests/
.venv/bin/lint-imports
make test
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: drain cli/ — rename to transports/cli/ (#B10)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Task 11 (B11): Create `local/` — new package

**Depends on:** Task 10 (B10)

Assemble local-mode utilities from scattered locations. Only extract utilities genuinely shared between profiles, CLI, and adapters.

**Files:**
- Create: `marivo/local/__init__.py`
- Extract: State layout helpers from `marivo/profiles/local.py` → `marivo/local/state_layout.py`
- Extract: Init logic from `marivo/transports/cli/cmd_init.py` (was `cli/init.py`) → `marivo/local/init.py`
- Extract: WAL helpers from `marivo/adapters/local/` (if any exist) → `marivo/local/wal.py`

- [ ] **Step 1: Identify shared utilities in `profiles/local.py`**

```bash
cat marivo/profiles/local.py
```

Identify state layout helpers that are used by multiple consumers (profiles, CLI, adapters). Only extract if genuinely shared — don't over-abstract.

- [ ] **Step 2: Identify init logic in `transports/cli/cmd_init.py`**

```bash
cat marivo/transports/cli/cmd_init.py
```

Identify initialization logic that could be shared between CLI init and other consumers.

- [ ] **Step 3: Check for WAL helpers in `adapters/local/`**

```bash
grep -rn 'wal\|WAL' --include='*.py' marivo/adapters/local/
```

If WAL helpers exist and are shared, extract to `local/wal.py`.

- [ ] **Step 4: Create `marivo/local/` package**

```bash
mkdir -p marivo/local
touch marivo/local/__init__.py
```

- [ ] **Step 5: Extract state layout helpers**

Move the identified layout helpers from `marivo/profiles/local.py` to `marivo/local/state_layout.py`. Keep `profiles/local.py` as a thin wrapper that imports from `local/state_layout.py`.

- [ ] **Step 6: Extract init logic**

Move shared init logic from `marivo/transports/cli/cmd_init.py` to `marivo/local/init.py`. Keep CLI command as a thin wrapper.

- [ ] **Step 7: Extract WAL helpers (if applicable)**

If found in Step 3, extract to `marivo/local/wal.py`.

- [ ] **Step 8: Update all callers**

Update any imports to use the new `marivo.local.*` paths.

- [ ] **Step 9: Run Validation Gate**

```bash
.venv/bin/lint-imports
make test
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: create local/ package — extract shared local-mode utilities (#B11)

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```

---

## Final Cleanup Task: Verify Clean Architecture

After all 11 tasks complete:

- [ ] **Step 1: Verify no legacy packages remain**

```bash
for pkg in semantic_runtime registry semantic_service_v2 intents analysis_core execution storage evidence_engine api cli; do
    echo "--- $pkg ---"
    ls marivo/$pkg/ 2>&1
done
# Expected: all "No such file or directory"
```

- [ ] **Step 2: Verify only target packages exist**

```bash
ls -d marivo/*/
# Expected: contracts/ core/ ports/ runtime/ adapters/ transports/ profiles/ local/
```

- [ ] **Step 3: Full validation**

```bash
.venv/bin/lint-imports
make test
make typecheck
make lint
```

- [ ] **Step 4: Final commit (if any cleanup needed)**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore: final cleanup after legacy package drain

Co-Authored-By: copilot:claude-opus-4.6 [copilot-cli]
EOF
)"
```
