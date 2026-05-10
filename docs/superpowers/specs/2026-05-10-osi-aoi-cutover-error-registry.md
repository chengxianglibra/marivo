# OSI/AOI Cutover — Error & Rescue Registry

**Plan:** `2026-05-10-osi-aoi-static-cutover-design.md`
**Created:** 2026-05-10
**Status:** Draft

---

## Error Scenarios

### E1: Generated Model Validation Fails on Spec Examples

**Symptom:** Phase A gate fails — `datamodel-code-generator` produces models that reject valid spec examples.

**Root cause:** Generator doesn't support JSON Schema draft 2020-12 features (`contentSchema`, `if/then/else`, `unevaluatedProperties`).

**Rescue:**
1. Check generator version — upgrade to latest `datamodel-code-generator`
2. If feature unsupported, add manual post-generation patch to `scripts/generate_contract_models.py`
3. If unpatchable, document as known limitation (D9) and add runtime validation layer

**Prevention:** Phase A includes explicit test: "generated models parse current `osi-marivo-spec/examples/**` and `aoi-spec/examples/**`"

---

### E2: Circular Import Between Contracts and Transports

**Symptom:** `ImportError: cannot import name 'SemanticModel' from partially initialized module` during Phase B.

**Root cause:** Runtime code imports from `transports/http/models/osi`, which re-exports from `contracts/generated/osi`, but some transport code also imports runtime utilities.

**Rescue:**
1. Audit import graph: `grep -r 'from marivo.contracts' marivo/transports/`
2. Break cycle by moving shared utilities to `marivo/core/` or `marivo/contracts/`
3. Ensure transport layer only imports from contracts, never the reverse

**Prevention:** D11 (migrate runtime imports to `contracts/generated/`) eliminates the need for transport-to-runtime imports.

---

### E3: Storage Migration Breaks Existing Semantic Models

**Symptom:** After Phase B storage migration, existing semantic models fail to load or have missing metric metadata.

**Root cause:** Dropping the 5 old metric columns (`observed_dataset`, `observation_grain`, `primary_time_field`, `additivity`, `filters`) loses data for models created before the cutover. Note: `primary_time_field` is fully eliminated — AOI `TimeScope.field` makes time field selection caller-specified.

**Rescue:**
1. If pre-cutover models exist in production: add migration that reads old columns, infers from OSI document, and validates consistency before dropping
2. If pre-launch (no production data): drop columns cleanly
3. Add readiness check that re-infers metadata and compares against old column values (if present)

**Prevention:** Plan review finding #1 — add explicit storage migration step to Phase B.

---

### E4: MCP Tool Schema Breaks Agent Compatibility

**Symptom:** Phase D MCP E2E fails — agents reject new tool schemas or send malformed requests.

**Root cause:** DTO shape change (e.g., `artifact_id` string vs typed ref object) breaks existing agent prompts or MCP client code.

**Rescue:**
1. Check FastMCP `inputSchema` output: `mcp inspect stdio python -m marivo.transports.mcp.server`
2. Compare old vs new schema — identify breaking changes
3. Add temporary compatibility shim in DTO layer if needed
4. Update agent prompts/examples to match new schema

**Prevention:** Phase D gate includes "MCP tool inputSchema tests" — snapshot schemas and diff against expected shape.

---

### E5: Derived Operation Latency Regression

**Symptom:** After Phase E, `attribute` requests time out or take 10x longer than before.

**Root cause:** Decomposing `attribute` into 2x observe + compare + Nx decompose multiplies I/O operations. If N (dimension count) is large, sequential execution is slow.

**Rescue:**
1. Add parallelism: run observe calls concurrently, run decompose calls concurrently
2. Add timeout/limit: cap N decompose calls to top K dimensions
3. Add caching: reuse observe results if slices are identical
4. If unfixable, document as known limitation and recommend direct AOI atomic calls for performance-sensitive use cases

**Prevention:** Plan review finding #6 — acknowledge latency tradeoff in plan.

---

### E6: AOI `test` Intent Architecture Mismatch

**Symptom:** Phase C fails — existing `test` intent implementation expects artifact refs, but AOI `test` is source-type (takes metric + slices).

**Root cause:** Marivo's current `test` is 2-step (observe → test artifact), AOI `test` is 1-step (metric + slices → hypothesis test).

**Rescue:**
1. Rewrite `test` intent runtime to accept metric + slices (source-type)
2. Update MCP tool to accept metric + slices, not artifact refs
3. Update tests to use new shape
4. If old 2-step pattern is needed, implement as derived operation (observe → test)

**Prevention:** D5 decision locked this — AOI shape wins, Marivo adapts.

---

### E7: Stale Generated Models in CI

**Symptom:** PR passes CI but fails in production — generated models don't match updated schema.

**Root cause:** Developer updated `osi-marivo-spec/schema/osi-marivo.schema.json` but forgot to run `generate_contract_models.py`.

**Rescue:**
1. Regenerate models: `python scripts/generate_contract_models.py`
2. Commit regenerated files
3. Add CI check to prevent future occurrences

**Prevention:** Plan review finding #2 — add CI step: `generate_contract_models.py && git diff --exit-code marivo/contracts/generated/`

---

### E8: Readiness Check False Negatives

**Symptom:** Semantic model passes readiness check but fails at execution time with "missing field" or "invalid metric expression".

**Root cause:** Readiness check doesn't validate all preconditions for runtime inference (D6 — runtime inference). Compiler infers `observed_dataset` at execution time, but datasource is unavailable or dataset doesn't exist. Note: `primary_time_field` is no longer validated at readiness — AOI `TimeScope.field` makes the time field a caller-supplied parameter, so readiness only validates that time-typed fields exist in the dataset, not that a specific one is bound to the metric.

**Rescue:**
1. Move inference earlier: run inference at readiness check time, cache results
2. Add datasource availability check to readiness
3. Add dataset schema validation to readiness
4. If inference fails, surface as readiness issue (not execution error)

**Prevention:** D7 decision — readiness issues vs execution errors. Readiness check should validate all preconditions for inference.

---

### E9: `extract_marivo_extension()` Decode Failure

**Symptom:** Runtime crashes with `json.JSONDecodeError` when parsing MARIVO extension `data` field.

**Root cause:** Generated models have `data: str` (JSON-encoded string), but `extract_marivo_extension()` expects pre-decoded dict.

**Rescue:**
1. Update `extract_marivo_extension()` to decode `data` string before parsing
2. Add error handling for malformed JSON
3. Add validation: reject extensions with invalid JSON at write time

**Prevention:** D8 decision — keep existing helper pattern. Phase B must update helper to handle generated model shape.

---

### E10: Phase Gate Partial Pass

**Symptom:** Phase B gate: 95% of tests pass, 5% fail due to edge cases. Team wants to proceed to Phase C.

**Root cause:** No explicit gate policy — plan doesn't state whether partial pass is acceptable.

**Rescue:**
1. Fix failing tests before advancing
2. If tests are flaky/unrelated, isolate and skip (with justification)
3. If tests reveal real bugs, fix bugs before advancing

**Prevention:** Plan review finding #4 — state explicitly that gates require 100% pass.
