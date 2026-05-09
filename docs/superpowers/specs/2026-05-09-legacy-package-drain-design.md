# Legacy Package Drain Design

Drain all 11 legacy top-level packages under `marivo/`, leaving only the
target architecture packages: `contracts/`, `core/`, `ports/`, `runtime/`,
`adapters/`, `transports/`, `profiles/`, `local/`.

## Principles

1. **Move I/O modules as-is** — no internal refactoring, *unless* the module
   seriously violates the target architecture (e.g., HTTP coupling in runtime,
   direct storage imports in orchestration). Violating modules are split during
   the move. Conforming modules move unchanged.
2. **Delete re-export shims** — modules that re-export from `core/` are
   deleted; callers use `core/` directly.
3. **Big-bang moves** — no temporary shims. Each package is moved/deleted in
   one commit.
4. **Import linter stays enforced** — `.importlinter` contracts are updated
   alongside each drain. `ignore_imports` entries are removed as imports are
   resolved. New chain entries are added when file moves create new violations.
5. **Validation gate** — `lint-imports` + `pytest` must pass after each
   package drain. Zero dangling `from marivo.<old_package>` references remain.
6. **Runtime is organized, not flat** — I/O modules land in focused
   sub-packages under `runtime/`, not in a flat directory.

## Execution Tracks

Three parallel tracks with internal dependency ordering.

```
Track 1:  B1 ── B2                          (parallel, no deps)
Track 2:  B3 ── B5 ── B4 ── B6              (sequential chain, B1 must complete before B4)
Track 3:  B7 ── B8 ── B9 ── B10 ── B11      (sequential)
```

Cross-track dependencies:
- **B1 must complete before B4** — `analysis_core/compiler.py` imports from
  `semantic_runtime`, which B1 deletes.
- Cross-track merge conflicts (e.g., both B6 and B7 touch `adapters/server/`)
  are resolved via normal git merge.

---

## Track 1: Easy Cleanups

### B1. `semantic_runtime/` — 7 files

Delete stubs, move active modules to their targets.

| File | Action |
|------|--------|
| `dimensions.py` | Delete (returns `[]`) |
| `status_utils.py` | Delete (returns constants) |
| `resolution.py` | Delete stubs, extract `ResolvedSemanticObject` to `core/semantic/` if used |
| `errors.py` | Move to `runtime/errors.py` |
| `repository.py` | Move to `runtime/evidence/semantic_repository.py` |
| `semantic_metadata.py` | Move to `core/semantic/metadata.py` |
| `__init__.py` | Delete |

Callers to update: `runtime/__init__.py`, `analysis_core/compiler.py`,
`runtime/semantic_ops.py`.

Linter: Remove `runtime.* -> semantic_runtime.*` wildcards from
`surfaces-must-use-runtime`.

### B2. `registry/` — 4 files

Move to `adapters/server/`. Only `adapters/server/` imports from it.

| File | Action |
|------|--------|
| `datasource_registry.py` | Move to `adapters/server/datasource_registry.py` |
| `factories.py` | Move to `adapters/server/registry_factories.py` |
| `common.py` | Move to `adapters/server/registry_common.py` |
| `__init__.py` | Delete |

---

## Track 2: Core Refactoring

### B3. `semantic_service_v2/` — 5 files

Split into runtime orchestration + adapter persistence. This module seriously
violates the target architecture (imports HTTPException from FastAPI,
SQLiteMetadataStore directly), so it must be split during the move.

| File | Action |
|------|--------|
| `service.py` | Split: orchestration logic → `runtime/semantic/semantic_service.py`, HTTP/storage coupling → `adapters/server/semantic_service_adapter.py`. Replace HTTPException with domain errors in runtime/, keep HTTPException in adapter. |
| `storage.py` | Merge into `adapters/server/model_store.py` |
| `validation.py` | Move to `runtime/semantic/semantic_validation.py` (if I/O) or `core/semantic/validator.py` (if pure) |
| `extensions.py` | Move to `core/semantic/extensions.py` (pure logic) |
| `__init__.py` | Delete |

Key blocker: `profiles/server.py` imports `semantic_service_v2.service`.
Rewire to use `adapters/server/model_store.py` directly.

Linter: Add `no-semantic-service-v2` contract before starting. Delete it
after drain.

### B5. `intents/` — 14 files

`git mv` to `runtime/intents/` (sub-package under runtime).

| File | Action |
|------|--------|
| 10 intent runners | Move to `runtime/intents/` |
| `_helpers.py` | Move to `runtime/intents/_helpers.py` |
| `calendar_alignment_metadata.py` | Move to `runtime/intents/calendar_alignment_metadata.py` |
| `predicate_lineage_reuse.py` | Move to `runtime/intents/predicate_lineage_reuse.py` |
| `__init__.py` | Update re-exports |

Update `runtime/intent_execution.py`: `marivo.intents.*` →
`marivo.runtime.intents.*`.

Linter: Update `marivo.intents.*` → `marivo.runtime.intents.*` in
`runtime-no-direct-core-orchestration` and `surfaces-must-use-runtime`.

### B4. `analysis_core/` — 23 files

Delete re-export shims and duplicates (where `core/` already has the pure
logic). Extract remaining I/O-bound functions into `runtime/` sub-packages.
Do NOT move files that contain pure logic already present in `core/` — delete
the duplicates and extract only the I/O-bound functions that lack a `core/`
equivalent.

**Delete (re-export shims — callers already use `core/`):**

| File | Current re-export source |
|------|-------------------------|
| `ir.py` | `core.semantic.ir` |
| `step_registry.py` | `core.intent.step_registry` |
| `intent_registry.py` | `core.intent.intent_registry` |
| `primitives.py` | `core.intent.primitives` |
| `additivity_capabilities.py` | `core.semantic.additivity` |
| `calendar_*.py` | `core.semantic.calendar` |
| `capability_profiles.py` | Stub for import compat |
| `predicate_validator.py` | Stub for import compat |

**Extract I/O-bound functions (delete pure duplicates, keep only I/O):**

| File | Action | Target |
|------|--------|--------|
| `compiler.py` | Delete pure re-exports (already in `core.semantic.compiler`). Extract `compile_step()` only. | `runtime/semantic/compile_step.py` |
| `typed_resolution.py` | Delete pure data classes (already in `core.semantic.typed_resolution`). Extract `normalize_step_request()`, `resolve_compiler_inputs()` only. | `runtime/semantic/resolution_orchestrator.py` |
| `validator.py` | Delete pure re-exports (already in `core.semantic.validator`). Extract I/O-bound `validate_compiler_inputs()` only. | `runtime/semantic/analysis_validator.py` |

**Move to `runtime/` sub-packages (I/O-bound, no `core/` equivalent):**

| File | Target |
|------|--------|
| `executor.py` | `runtime/semantic/executor.py` |
| `step_runners/` | `runtime/step_runners/` |
| `workflows/` | `runtime/workflows/` |
| `calendar_data_runtime.py` | `runtime/semantic/calendar_data_runtime.py` |
| `composites.py` | `runtime/semantic/composites.py` |

Callers to update: `runtime/semantic_ops.py`, `runtime/step_executor.py`,
`execution/orchestrator.py`, `profiles/`, `intents/` (now `runtime/intents/`).

Linter: Remove all `analysis_core` entries from
`runtime-no-direct-core-orchestration` and `surfaces-must-use-runtime`.

### B6. `execution/` — 8 files

Split into runtime orchestration + adapter implementations.

| File | Target |
|------|--------|
| `orchestrator.py` | `runtime/execution/orchestrator.py` |
| `feedback.py` | `runtime/semantic/feedback.py` |
| `routing_runtime.py` | `adapters/server/routing_runtime.py` |
| `translation.py` | `adapters/server/translation.py` |
| `federation.py` | `runtime/execution/federation.py` |
| `capabilities.py` | `contracts/capabilities.py` |
| `errors.py` | Merge into `contracts/errors.py` |
| `__init__.py` | Delete |

Callers to update: `adapters/server/data_source.py`,
`runtime/semantic_ops.py`.

Linter: Add `execution-isolation` contract before starting. Delete after
drain.

---

## Track 3: Physical Renames & Expansion

### B7. `storage/` — 13 files

Move implementations to `adapters/`, protocols to `ports/`.

| File | Target |
|------|--------|
| `sqlite_metadata.py` | `adapters/local/sqlite_metadata.py` |
| `duckdb_analytics.py` | `adapters/local/duckdb_analytics.py` |
| `mysql_metadata.py` | `adapters/server/mysql_metadata.py` |
| `trino_analytics.py` | `adapters/server/trino_analytics.py` |
| `analytics.py` (protocol) | `ports/analytics.py` |
| `evidence_repositories.py` | `adapters/server/evidence_repositories.py` |
| `step_metadata_repository.py` | `adapters/server/step_metadata_repository.py` |
| `metadata.py` | `adapters/metadata.py` |
| `schema.py` | `adapters/schema.py` |
| `repositories.py` | `adapters/repositories.py` |
| `factories.py` | `adapters/storage_factories.py` |
| `__init__.py` | Delete |

Callers to update: `profiles/server.py`, `adapters/server/*.py`.

Linter: Add `storage-isolation` contract before starting. Delete after
drain.

### B8. `evidence_engine/` — 27 files

Delete shims + deprecated extractors, split remaining into runtime/core/adapters.

**Delete (shims/deprecated):**

| File | Reason |
|------|--------|
| `family_contract.py` | Re-export shim from `core.evidence.family_contract` |
| 7 deprecated extractors | Delegate to `core.evidence.finding_extraction` |

**Move to `runtime/evidence/` (I/O-bound):**

| File | Target |
|------|--------|
| `canonical_pipeline_runtime.py` | `runtime/evidence/canonical_pipeline.py` |
| `state_view.py` | `runtime/evidence/state_view.py` |
| `context_view.py` | `runtime/evidence/context_view.py` |
| `ref_boundary.py` | `runtime/evidence/ref_boundary.py` |
| `publish_switch.py` | `runtime/evidence/publish_switch.py` |
| `invalidation.py` | `runtime/evidence/invalidation.py` |
| `replay_recovery.py` | `runtime/evidence/replay_recovery.py` |
| `proposal_refresh_run.py` | `runtime/evidence/proposal_refresh.py` |
| `proposition_registration.py` | `runtime/evidence/proposition_registration.py` |
| `proposition_seed_registry.py` | `runtime/evidence/proposition_seed_registry.py` |
| `assessment_evaluation_context.py` | `runtime/evidence/assessment_context.py` |
| `assessment_recompute.py` | `runtime/evidence/assessment_recompute.py` |
| `proposition_seeding_run.py` | `runtime/evidence/proposition_seeding.py` |
| `finding_extractor_registry.py` | `runtime/evidence/finding_extractor_registry.py` |

**Move to `core/evidence/` (pure logic):**

| File | Target |
|------|--------|
| `version_policy.py` | `core/evidence/version_policy.py` |
| `canonical_refs.py` | `core/evidence/canonical_refs.py` |
| `proposition_normalizer.py` | `core/evidence/proposition_normalizer.py` |

**Split (violates spec — must split during move):**

| File | Target |
|------|--------|
| `canonical_finding.py` (703 lines) | Pure logic (dataclasses, value objects, computation) → `core/evidence/canonical_finding.py`. I/O-bound methods (repository access, pipeline integration) → `runtime/evidence/canonical_finding_ops.py`. |

Linter: Remove all `evidence_engine` entries from
`runtime-no-direct-core-orchestration` and `surfaces-must-use-runtime`.

### B9. `api/` → `transports/http/` — 25 files

Big-bang rename. Every `from marivo.api.*` import becomes
`from marivo.transports.http.*`.

| Current | Target |
|---------|--------|
| `marivo/api/app_factory.py` | `marivo/transports/http/app_factory.py` |
| `marivo/api/deps.py` | `marivo/transports/http/deps.py` |
| `marivo/api/sessions.py` | `marivo/transports/http/sessions.py` |
| `marivo/api/middleware.py` | `marivo/transports/http/middleware.py` |
| `marivo/api/models/` | `marivo/transports/http/models/` |
| `marivo/api/endpoints/` | `marivo/transports/http/endpoints/` |
| All other files | `marivo/transports/http/` |

Major importers: `transports/mcp/http.py` (mounts API app),
`profiles/server.py` (imports `api.app_factory`).

Linter: Update `transports-mcp-no-api-internals` forbidden module from
`marivo.api.endpoints` to `marivo.transports.http.endpoints`. Update
`surfaces-must-use-runtime` source_modules from `marivo.api` to
`marivo.transports.http`.

### B10. `cli/` → `transports/cli/` — 13 files

Big-bang rename. Every `from marivo.cli.*` import becomes
`from marivo.transports.cli.*`.

All command files move to `transports/cli/`.

Linter: Update `surfaces-must-use-runtime` source_modules from `marivo.cli`
to `marivo.transports.cli`.

### B11. Create `local/` — new package

Assemble local-mode utilities from scattered locations.

| Source | Target |
|--------|--------|
| State layout helpers from `profiles/local.py` | `local/state_layout.py` |
| Init logic from `cli/init.py` | `local/init.py` |
| WAL helpers (if any exist in `adapters/local/`) | `local/wal.py` |
| `local/__init__.py` | Create |

Scope: only extract utilities that are genuinely shared between profiles,
CLI, and adapters. Don't over-abstract.

---

## Import Linter Changes Summary

### Contracts to add (before drain starts, remove after package deleted)

- `no-semantic-service-v2` — before B3
- `execution-isolation` — before B6
- `storage-isolation` — before B7

### `ignore_imports` entries to remove

**After B4 (analysis_core drain):**
```
runtime-no-direct-core-orchestration:
  marivo.runtime.semantic_ops -> marivo.analysis_core.compiler
  marivo.runtime.semantic_ops -> marivo.analysis_core.executor
  marivo.runtime.step_executor -> marivo.analysis_core.step_runners
  marivo.intents.* -> marivo.analysis_core.*
surfaces-must-use-runtime:
  marivo.runtime.* -> marivo.analysis_core.*
  marivo.intents.* -> marivo.analysis_core.*
```

**After B5 (intents move):**
```
# Remove marivo.intents.* entries from both contracts — after moving
# intents/ to runtime/intents/, the marivo.runtime.* wildcards already
# cover runtime/intents/ → analysis_core and runtime/intents/ → evidence_engine.
runtime-no-direct-core-orchestration:
  marivo.intents.* -> marivo.analysis_core.*        # REMOVE
  marivo.intents.* -> marivo.evidence_engine.*       # REMOVE
surfaces-must-use-runtime:
  marivo.intents.* -> marivo.analysis_core.*         # REMOVE
  marivo.intents.* -> marivo.evidence_engine.*       # REMOVE
```

**After B8 (evidence_engine drain):**
```
runtime-no-direct-core-orchestration:
  marivo.runtime.semantic_ops -> marivo.evidence_engine.ref_boundary
  marivo.runtime.session -> marivo.evidence_engine.context_view
  marivo.runtime.session -> marivo.evidence_engine.state_view
  marivo.intents.* -> marivo.evidence_engine.*
surfaces-must-use-runtime:
  marivo.runtime.* -> marivo.evidence_engine.*
  marivo.intents.* -> marivo.evidence_engine.*
```

### Contracts to update

**After B9 (api rename):**
- `transports-mcp-no-api-internals`: `marivo.api.endpoints` →
  `marivo.transports.http.endpoints`
- `surfaces-must-use-runtime` source_modules: `marivo.api` →
  `marivo.transports.http`

**After B10 (cli rename):**
- `surfaces-must-use-runtime` source_modules: `marivo.cli` →
  `marivo.transports.cli`

### `analysis_core.compiler` → `evidence_engine.ref_boundary` chain entry

```
marivo.analysis_core.compiler -> marivo.evidence_engine.ref_boundary
```

When B4 extracts `compile_step()` to `runtime/semantic/compile_step.py`, the old
entry is removed and a NEW entry must be added:

```
marivo.runtime.semantic.compile_step -> marivo.evidence_engine.ref_boundary
```

This new entry is removed when B8 drains `evidence_engine/`. The spec must
track this chain explicitly — import-linter does not infer file moves.

### Chain entries after B4 (analysis_core drain)

After B4, these new `ignore_imports` entries are needed because moved files
now create `runtime → evidence_engine` violations:

```
runtime-no-direct-core-orchestration:
  marivo.runtime.semantic.compile_step -> marivo.evidence_engine.ref_boundary
```

---

## Validation

After each package drain:

1. `lint-imports` passes (updated `.importlinter` in same commit)
2. `pytest` passes (no new failures)
3. `grep -r "from marivo\.<old_package>" marivo/` returns zero results
4. Old package directory fully removed
5. Test files importing `from marivo.<old_package>` are also updated (not
   just source files)

No new tests needed — behavior is unchanged, only file locations change.

## Rollback Strategy

Each package drain is a single commit. Rollback is `git revert <sha>`.
For large moves (B9/B10), the revert diff is large but mechanical —
no manual intervention needed.

If a drain breaks tests or the import linter, fix before committing.
Do not commit with known failures.

## Commit Strategy

One commit per package drain. Format:
`refactor: drain <package>/ — move to <target> (#Bn)`

Linter changes are included in the same commit as the code move.
