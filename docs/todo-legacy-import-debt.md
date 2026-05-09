# Legacy Import Debt — I/O-Bound Cross-Boundary Imports & Package Drain Plan

Target architecture (§4 of `2026-05-06-marivo-platform-architecture-design.md`) defines
these packages as the permanent structure: `contracts/`, `core/`, `ports/`, `runtime/`,
`adapters/`, `transports/`, `profiles/`, `local/`. All other top-level packages under
`marivo/` are legacy and must eventually be drained and deleted.

---

## Part A: I/O-Bound Cross-Boundary Imports (18 items)

These 18 imports remain I/O-bound with no `core/` equivalent. They require deeper
refactoring (port extraction, service wiring inversion, or package migration).

## 1. `compile_step` — I/O-coupled compiler orchestrator

- **File:** `marivo/runtime/semantic_ops.py:17`
- **Import:** `from marivo.analysis_core.compiler import compile_step`
- **Reason:** Orchestrates normalization, resolution, validation, IR bundle construction,
  and SQL generation with interleaved `SemanticRuntimeRepository` calls. Not pure.
- **Resolution path:** Extract pure compilation steps into `core.semantic.compiler`,
  make `compile_step` call `core.compile()` + `ports.execute()` + `core.validate()`.
  The orchestrator shell stays in `runtime/` and delegates to core+ports.
- **Contract entry:** `runtime-no-direct-core-orchestration` → `marivo.runtime.semantic_ops -> marivo.analysis_core.compiler`

## 2. `execute_compiled` — I/O-bound query execution

- **File:** `marivo/runtime/semantic_ops.py` (5 deferred imports at lines 1775, 1924, 2104, 2210, 2300)
- **Import:** `from marivo.analysis_core.executor import execute_compiled`
- **Reason:** Calls `AnalyticsEngine`, `FederationRuntime`, translation pipeline — all I/O.
- **Resolution path:** Replace with `ports.data_source.execute(query)`. Phase 4 design
  (section 4.4) explicitly specifies this: `execute_compiled(engine, query)` → `ports.data_source.execute(query)`.
  The `analysis_core.executor` module becomes unnecessary once all callers use the port.
- **Contract entry:** `runtime-no-direct-core-orchestration` → `marivo.runtime.semantic_ops -> marivo.analysis_core.executor`

## 3. `assert_no_canonical_refs_in_semantic_payload` — runtime ref-boundary policy

- **File:** `marivo/runtime/semantic_ops.py:1547`
- **Import:** `from marivo.evidence_engine.ref_boundary import assert_no_canonical_refs_in_semantic_payload`
- **Reason:** Accesses `marivo.semantic_runtime.semantic_metadata` — runtime policy, not pure computation.
- **Resolution path:** Either extract the pure ref-boundary assertion logic to `core.evidence`
  (leaving the semantic_runtime access in `runtime/`), or invert the dependency so the
  assertion takes metadata as a parameter instead of accessing the repository.
- **Contract entry:** `runtime-no-direct-core-orchestration` → `marivo.runtime.semantic_ops -> marivo.evidence_engine.ref_boundary`

## 4. `build_service_step_registry` — service wiring requiring MarivoRuntime

- **File:** `marivo/runtime/step_executor.py:11`
- **Import:** `from marivo.analysis_core.step_runners import build_service_step_registry`
- **Reason:** Creates a `StepRunnerRegistry` and populates it with I/O-coupled runners
  (`generic.register`, `attribution.register`) that require a `MarivoRuntime`.
- **Resolution path:** Move the registry wiring into `runtime/` itself (it already depends
  on `MarivoRuntime`). The step runners can be registered directly in the runtime factory
  (`profiles/local.py`, `profiles/server.py`) instead of going through `analysis_core.step_runners`.
- **Contract entry:** `runtime-no-direct-core-orchestration` → `marivo.runtime.step_executor -> marivo.analysis_core.step_runners`

## 5. `materialize_session_state_view` — deeply I/O-bound (6 repos)

- **File:** `marivo/runtime/session.py:364`
- **Import:** `from marivo.evidence_engine.state_view import materialize_session_state_view`
- **Reason:** Takes 6 repository objects as parameters (proposition, assessment, finding,
  gap, inference, proposal repos). Deeply I/O-bound.
- **Resolution path:** Move the orchestration logic into `runtime/` as a use-case function
  that calls `ports.evidence_store.*` methods. The pure view-shaping logic can be extracted
  to `core.evidence.state_view` if any exists.
- **Contract entry:** `runtime-no-direct-core-orchestration` → `marivo.runtime.session -> marivo.evidence_engine.state_view`

## 6. `materialize_proposition_context_view` — deeply I/O-bound

- **File:** `marivo/runtime/session.py:399`
- **Import:** `from marivo.evidence_engine.context_view import materialize_proposition_context_view`
- **Reason:** Reads from multiple repository objects. I/O-bound.
- **Resolution path:** Same as #5 — move to `runtime/` as a port-backed use-case function.
- **Contract entry:** `runtime-no-direct-core-orchestration` → `marivo.runtime.session -> marivo.evidence_engine.context_view`

## 7. Intent runners (10 imports) — service-layer orchestrators

- **File:** `marivo/runtime/intent_execution.py:13-22`
- **Imports:**
  - `from marivo.intents.attribute import run_attribute_intent`
  - `from marivo.intents.compare import run_compare_intent`
  - `from marivo.intents.correlate import run_correlate_intent`
  - `from marivo.intents.decompose import run_decompose_intent`
  - `from marivo.intents.detect import run_detect_intent`
  - `from marivo.intents.diagnose import run_diagnose_intent`
  - `from marivo.intents.forecast import run_forecast_intent`
  - `from marivo.intents.observe import run_observe_intent`
  - `from marivo.intents.test import run_test_intent`
  - `from marivo.intents.validate import run_validate_intent`
- **Reason:** Intent runners are I/O-bound orchestrators that call the runtime, commit
  artifacts, and invoke the analysis engine. They are in the correct architectural layer
  (`intents/` is between `runtime/` and `core/`), but the import-linter flags the
  transitive chain `runtime → intents → analysis_core/evidence_engine`.
- **Resolution path:** Two options:
  - (a) Register intent runners via `core.intent.IntentRunnerRegistry` instead of
    importing them directly. `intent_execution.py` would look up runners from the registry
    rather than importing each module.
  - (b) Keep direct imports but document them as intentional — the `intents/` package
    is the correct layer for I/O-bound intent orchestration, not a legacy package.
- **Contract entry:** `runtime-no-direct-core-orchestration` → `marivo.intents.* -> marivo.analysis_core.*` / `marivo.evidence_engine.*`

## 8. `CompositeWorkflowRuntime` — workflow orchestration

- **File:** `marivo/execution/orchestrator.py:5`
- **Import:** `from marivo.analysis_core.workflows.workflow_runtime import CompositeWorkflowRuntime`
- **Reason:** Expands workflow specs into executable step IR lists. Depends on
  `CompositeWorkflowSpec`, `WORKFLOW_SPECS` catalog, and `step_ir_from_mapping`.
- **Resolution path:** Move workflow expansion logic to `core.semantic` or `core.intent`
  (pure IR transformation), and keep only the execution loop in `execution/orchestrator.py`.
  Alternatively, absorb `execution/orchestrator.py` into `runtime/` and eliminate the
  `execution/` package entirely (it's not in the target architecture).
- **Contract entry:** Not covered by current contracts (`execution/` is not a source module
  in any contract). Should be added to `surfaces-must-use-runtime` if `execution/` persists.

---

## Cross-Reference: `.importlinter` Entries

These imports are currently allowed via `ignore_imports` in two contracts:

**`runtime-no-direct-core-orchestration`:**
```ini
ignore_imports =
    marivo.runtime.semantic_ops -> marivo.analysis_core.compiler      (#1)
    marivo.runtime.semantic_ops -> marivo.analysis_core.executor       (#2)
    marivo.runtime.semantic_ops -> marivo.evidence_engine.ref_boundary (#3)
    marivo.runtime.step_executor -> marivo.analysis_core.step_runners  (#4)
    marivo.runtime.session -> marivo.evidence_engine.context_view      (#6)
    marivo.runtime.session -> marivo.evidence_engine.state_view        (#5)
    marivo.intents.* -> marivo.analysis_core.*                         (#7 transitive)
    marivo.intents.* -> marivo.evidence_engine.*                       (#7 transitive)
    marivo.analysis_core.compiler -> marivo.evidence_engine.ref_boundary
```

**`surfaces-must-use-runtime`:**
```ini
ignore_imports =
    marivo.runtime.* -> marivo.analysis_core.*       (#1, #2, #3)
    marivo.runtime.* -> marivo.evidence_engine.*      (#3, #5, #6)
    marivo.intents.* -> marivo.analysis_core.*         (#7 transitive)
    marivo.intents.* -> marivo.evidence_engine.*       (#7 transitive)
```

Each `ignore_imports` entry should be removed when the corresponding import is resolved.

---

## Part B: Legacy Package Drain Plan

Suggested ordering by dependency depth — drain packages that are depended on by
fewest others first. Each entry includes file count, current status, target
destination, key blockers, and coupling to other legacy packages.

### B1. `semantic_runtime/` — 7 files | Difficulty: LOW

Mostly stubs already. Only `repository.py` (247 lines) and `errors.py` have real logic.
All files carry "Legacy semantic_runtime stubs" headers.

| File | Status | Target |
|------|--------|--------|
| `errors.py` | Active — `SemanticRuntimeNotReadyError` etc. used by `runtime/__init__.py` | Move to `runtime/errors.py` or `contracts/errors.py` |
| `repository.py` | Active — `SemanticRuntimeRepository` used by `analysis_core/compiler.py` | Move to `runtime/` or absorb into port-backed runtime |
| `resolution.py` | Stub — `ResolvedSemanticObject` dataclass + `NotImplementedError` stubs | Extract types to `core/semantic/`, delete stubs |
| `dimensions.py` | Stub — returns `[]` | Delete |
| `status_utils.py` | Stub — returns `"draft"` / `"not_ready"` | Delete |
| `semantic_metadata.py` | Active — `SUPPORTED_RUNTIME_REF_KINDS`, `runtime_ref_kind()` | Move to `core/semantic/` |
| `__init__.py` | Re-exports | Delete after files moved |

**Blockers:** `analysis_core/compiler.py` imports `SemanticRuntimeRepository`. `runtime/semantic_ops.py` imports `semantic_runtime.{dimensions,errors,resolution}`. Must redirect these first.

**Linter contract entries to remove:** `runtime.* -> semantic_runtime.*` wildcards in `surfaces-must-use-runtime`.

### B2. `registry/` — 4 files | Difficulty: LOW

Datasource registry and factory logic. Only referenced by `adapters/server/`.

| File | Status | Target |
|------|--------|--------|
| `datasource_registry.py` | Active — `DatasourceRegistry` class | `adapters/server/datasource_registry.py` |
| `factories.py` | Active — builds analytics engines and catalog adapters | `adapters/server/factories.py` |
| `__init__.py` | Re-exports | Delete after files moved |

**Blockers:** `adapters/server/data_source.py` imports from `registry.datasource_registry`. Simple file move + import update.

**Linter contract entries:** No contract explicitly targets `registry/`. Add `registry-isolation` if needed during migration.

### B3. `semantic_service_v2/` — 5 files | Difficulty: MEDIUM

Old service layer explicitly slated for demolition in Phase 6.1 design.

| File | Status | Target |
|------|--------|--------|
| `service.py` | Active — CRUD for OSI-aligned semantic models | `runtime/semantic_ops.py` (orchestration) + `adapters/server/model_store.py` (persistence) |
| `storage.py` | Active — OSI to storage row mapping | `adapters/server/model_store.py` |
| `validation.py` | Active — write-time validation | `core/semantic/validator.py` or `runtime/` |
| `extensions.py` | Active — custom_extensions parsing | `core/semantic/` (pure) or `runtime/` |
| `__init__.py` | Re-exports | Delete after files moved |

**Blockers:** `profiles/server.py` imports `semantic_service_v2.service`. Must rewire server profile to use `adapters/server/model_store.py` directly.

**Linter contract entries:** Add `no-semantic-service-v2` contract before starting drain.

### B4. `analysis_core/` — 23 files | Difficulty: MEDIUM–HIGH

Heavily deprecated. `ir.py`, `step_registry.py`, `compiler.py` already re-export from `core/`.
Remaining I/O-bound modules: `executor.py`, `compiler.py` (compile_step), `calendar_data_runtime.py`,
`step_runners/`, `workflows/`, `capability_profiles.py`, `predicate_validator.py`.

| File | Status | Target |
|------|--------|--------|
| `ir.py` | Re-export shim from `core.semantic.ir` | Delete when no external importers |
| `step_registry.py` | Re-export shim from `core.intent.step_registry` | Delete when no external importers |
| `intent_registry.py` | Re-export shim from `core.intent.intent_registry` | Delete when no external importers |
| `primitives.py` | Re-export shim from `core.intent.primitives` | Delete when no external importers |
| `compiler.py` | Re-exports types + `compile_step` (I/O) | Move `compile_step` to `runtime/`, delete rest |
| `executor.py` | Active — `execute_compiled` (I/O) | Replace with `ports.data_source.execute()`, delete |
| `calendar_data_runtime.py` | Active — calendar data reading (I/O) | Move to `runtime/` or port-backed adapter |
| `step_runners/` | Active — I/O-bound step runners | Register in runtime factory, delete |
| `workflows/` | Active — `CompositeWorkflowRuntime` | Move expansion to `core/`, execution to `runtime/` |
| `capability_profiles.py` | Stub for import compat | Delete |
| `predicate_validator.py` | Stub for import compat | Delete |
| `validator.py` | Re-exports + `validate_compiler_inputs` (I/O) | Move orchestrator to `runtime/`, delete |
| `typed_resolution.py` | Re-exports + `normalize_step_request`/`resolve_compiler_inputs` (I/O) | Move I/O functions to `runtime/`, delete |
| `calendar_*.py` | Re-export shims from `core.semantic.calendar` | Delete when no external importers |
| `additivity_capabilities.py` | Re-export shim from `core.semantic.additivity` | Delete when no external importers |
| `composites.py` | Active — workflow templates | Move to `core/intent/` or `runtime/` |
| `__init__.py` | Re-exports | Delete after all submodules gone |

**Blockers:** Part A items #1–4, #8. See above for resolution paths.

**Linter contract entries to remove:** All `analysis_core` entries in `runtime-no-direct-core-orchestration` and `surfaces-must-use-runtime`.

### B5. `intents/` — 14 files | Difficulty: MEDIUM

10 intent runners + helpers. Not deprecated (correct architectural layer for I/O orchestration),
but `intents/` as a standalone package is not in the target structure.

| File | Status | Target |
|------|--------|--------|
| `observe.py` … `validate.py` (10 runners) | Active | Keep as `runtime/intents/` subpackage, or register via `core.intent.IntentRunnerRegistry` |
| `_helpers.py` | Active — `commit_step_result()` | `runtime/` |
| `calendar_alignment_metadata.py` | Active — I/O-coupled helper | `runtime/` |
| `predicate_lineage_reuse.py` | Active — I/O-coupled helper | `runtime/` |
| `__init__.py` | Re-exports | Update after move |

**Blockers:** Part A item #7. The `runtime/intent_execution.py` imports all 10 runners directly. Either:
- (a) Move `intents/` to `runtime/intents/` (simplest — just a `git mv` + import update)
- (b) Register runners in `core.intent.IntentRunnerRegistry` and look up by name

**Linter contract entries to remove:** `marivo.intents.* -> marivo.analysis_core.*` / `marivo.evidence_engine.*` (these resolve when `intents/` stops importing from legacy packages).

### B6. `execution/` — 8 files | Difficulty: MEDIUM

Query routing, execution orchestration, federation. Not in target architecture.

| File | Status | Target |
|------|--------|--------|
| `orchestrator.py` | Active — `WorkflowOrchestrator` | `runtime/` (orchestration) |
| `routing_runtime.py` | Active — `RoutingRuntime` | `adapters/server/` (routing adapter) |
| `feedback.py` | Active — routing/compile feedback builders | `runtime/` |
| `federation.py` | Active — `FederationPlanner`, `FederationRuntime` | `runtime/` or `adapters/` |
| `translation.py` | Active — `DefaultQueryTranslator` | `adapters/` (dialect translation is adapter concern) |
| `capabilities.py` | Active — engine capability profiles | `contracts/` (value objects) or `adapters/` |
| `errors.py` | Active — `ExecutionError` | `contracts/errors.py` |
| `__init__.py` | Re-exports | Delete after files moved |

**Blockers:** Part A item #8 (`CompositeWorkflowRuntime`). `adapters/server/data_source.py` imports `execution.routing_runtime`. `runtime/semantic_ops.py` imports `execution.feedback`.

**Linter contract entries:** Add `execution-isolation` contract before starting drain.

### B7. `storage/` — 12 files | Difficulty: MEDIUM

Storage implementations (SQLite, DuckDB, MySQL). Not in target — implementations belong in `adapters/`.

| File | Status | Target |
|------|--------|--------|
| `sqlite_metadata.py` | Active — `SQLiteMetadataStore` | `adapters/local/` or `adapters/server/` |
| `duckdb_analytics.py` | Active — `DuckDBAnalyticsEngine` | `adapters/local/` |
| `mysql_metadata.py` | Active — `MySQLMetadataStore` | `adapters/server/` |
| `trino_analytics.py` | Active — `TrinoAnalyticsEngine` | `adapters/server/` |
| `analytics.py` | Active — `AnalyticsEngine` protocol | `ports/` (protocol) + `adapters/` (implementations) |
| `metadata.py` | Active — metadata access layer | `adapters/` |
| `evidence_repositories.py` | Active — evidence repo implementations | `adapters/server/` |
| `step_metadata_repository.py` | Active — step metadata repo | `adapters/server/` |
| `schema.py` | Active — schema management | `adapters/` |
| `repositories.py` | Active — repo implementations | `adapters/` |
| `factories.py` | Active — storage factory | `adapters/` |
| `__init__.py` | Re-exports | Delete after files moved |

**Blockers:** `profiles/server.py` imports heavily from `storage/`. `adapters/server/*.py` imports from `storage/`. Must update all importers after move.

**Linter contract entries:** Add `storage-isolation` contract before starting drain.

### B8. `evidence_engine/` — 27 files | Difficulty: HIGH

Largest legacy package. Partially extracted to `core/evidence/`. Canonical pipeline runtime,
state views, context views, and many I/O-bound modules remain.

| File | Status | Target |
|------|--------|--------|
| `family_contract.py` | Re-export shim from `core.evidence.family_contract` | Delete when no external importers |
| `observe_extractor.py` … `test_extractor.py` (7) | Deprecated — delegate to `core.evidence.finding_extraction` | Delete when `finding_extractor_registry.py` updated |
| `canonical_finding.py` | Deprecated (703 lines) | Absorb into `core/evidence/` and `adapters/` |
| `assessment_recompute.py` | Deprecated — I/O pipeline | Move 9-step pipeline to `runtime/`, pure logic already in `core/` |
| `proposition_seeding_run.py` | Deprecated — I/O pipeline | Move to `runtime/` |
| `canonical_pipeline_runtime.py` | Active — pipeline orchestration | `runtime/` |
| `finding_extractor_registry.py` | Active — registry wiring | `runtime/` or `adapters/` |
| `ref_boundary.py` | Active — runtime ref-boundary policy | `runtime/` (or extract pure logic to `core/`) |
| `state_view.py` | Active — session state materialization | `runtime/` (Part A item #5) |
| `context_view.py` | Active — proposition context materialization | `runtime/` (Part A item #6) |
| `publish_switch.py` | Active — evidence publishing | `runtime/` |
| `invalidation.py` | Active — cache invalidation | `runtime/` |
| `replay_recovery.py` | Active — replay recovery | `runtime/` |
| `version_policy.py` | Active — evidence versioning | `core/evidence/` (pure) or `runtime/` |
| `canonical_refs.py` | Active — canonical reference utilities | `core/evidence/` |
| `proposal_refresh_run.py` | Active — proposal refresh pipeline | `runtime/` |
| `proposition_registration.py` | Active — proposition registration | `runtime/` |
| `proposition_seed_registry.py` | Active — seed registry | `runtime/` or `adapters/` |
| `proposition_normalizer.py` | Active — normalization | `core/evidence/` (pure) or `runtime/` |
| `assessment_evaluation_context.py` | Active — evaluation context | `runtime/` |
| `__init__.py` | Re-exports | Delete after all submodules gone |

**Blockers:** Part A items #3, #5, #6. Deep coupling with `runtime/`, `adapters/`, and `analysis_core/`. Should be drained last among the "logic" packages.

**Linter contract entries to remove:** All `evidence_engine` entries in `runtime-no-direct-core-orchestration` and `surfaces-must-use-runtime`.

### B9. `api/` — 25 files | Difficulty: HIGH (large move)

FastAPI routes, middleware, models, OpenAPI. In the target architecture, HTTP routes
belong in `transports/http/`. Not a "drain" — a physical rename + import rewrite.

| File | Status | Target |
|------|--------|--------|
| `app_factory.py` | Active | `transports/http/app_factory.py` |
| `deps.py` | Active — dependency injection | `transports/http/deps.py` |
| `sessions.py` | Active — session routes | `transports/http/sessions.py` |
| `models/` | Active — Pydantic request/response schemas | `transports/http/models/` (or split pure types to `contracts/`) |
| `middleware.py` | Active — `UserIdentityMiddleware` | `transports/http/middleware.py` |
| `endpoints/` | Active — route handlers | `transports/http/endpoints/` |
| Other files | Active | `transports/http/` |

**Blockers:** Massive import rewrite. Every file that imports `marivo.api.*` must be updated.
`transports/mcp/http.py` mounts the API app. `profiles/server.py` imports `api.app_factory`.

**Linter contract entries:** `transports-mcp-no-api-internals` must be updated to new paths.

### B10. `cli/` — 12 files | Difficulty: MEDIUM (large move)

CLI commands. In the target architecture, CLI belongs in `transports/cli/`. Physical rename.

| File | Status | Target |
|------|--------|--------|
| All command files | Active | `transports/cli/` |

**Blockers:** Import rewrite for all `marivo.cli.*` importers.

**Linter contract entries:** Update `surfaces-must-use-runtime` source_modules from `marivo.cli` to `marivo.transports.cli`.

### B11. Missing target package: `local/`

The architecture design (§4) specifies a `local/` package: "Local-mode utilities: state
layout, init, WAL helpers." This package does not exist yet. Related logic is currently
scattered across `profiles/local.py`, `adapters/local/`, and `cli/init.py`.

---

## Suggested Execution Order

```
Phase 1 (LOW difficulty):
  B1. semantic_runtime/   → mostly stubs, easiest
  B2. registry/           → only adapters/server/ depends on it

Phase 2 (MEDIUM difficulty):
  B3. semantic_service_v2/ → Phase 6.1 already designed the demolition
  B5. intents/            → git mv to runtime/intents/ + import update

Phase 3 (MEDIUM–HIGH difficulty):
  B4. analysis_core/      → drain remaining I/O orchestrators into runtime/ports
  B6. execution/          → absorb into runtime/ and adapters/
  B7. storage/            → move implementations into adapters/

Phase 4 (HIGH difficulty):
  B8. evidence_engine/    → largest package, deepest coupling

Phase 5 (Physical rename):
  B9.  api/  → transports/http/
  B10. cli/  → transports/cli/
  B11. Create local/ from scattered utilities
```
