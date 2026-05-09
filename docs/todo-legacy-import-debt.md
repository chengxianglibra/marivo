# Legacy Import Debt — I/O-Bound Cross-Boundary Imports

These 18 imports remain I/O-bound with no `core/` equivalent. They require deeper
refactoring (port extraction, service wiring inversion, or package migration) and
are tracked here for future phases.

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
