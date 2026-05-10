# OSI/AOI Cutover — Failure Modes Registry

**Plan:** `2026-05-10-osi-aoi-static-cutover-design.md`
**Created:** 2026-05-10
**Status:** Draft

---

## Failure Modes

### FM1: Spec Drift

**Description:** JSON Schema specs (`osi-marivo-spec`, `aoi-spec`) evolve but generated models aren't regenerated.

**Likelihood:** High (during active development)
**Impact:** Medium — generated models accept/reject wrong payloads
**Detection:** CI freshness check (regenerate + `git diff --exit-code`)
**Mitigation:** Add CI step in Phase A

---

### FM2: Generator Upgrade Breaking Changes

**Description:** `datamodel-code-generator` releases new version that produces different Pydantic code (different field names, different validators, different model structure).

**Likelihood:** Medium
**Impact:** High — all generated imports break
**Detection:** CI tests against spec examples
**Mitigation:** Pin generator version in `pyproject.toml`; test before upgrading

---

### FM3: Dual-Model Confusion

**Description:** During cutover (Phases B-D), developers can't tell whether a given `SemanticModel` import is the old hand-written version or the new generated version.

**Likelihood:** High (during migration)
**Impact:** Low — wrong import would be caught by type checking or tests
**Detection:** `make typecheck`, runtime errors
**Mitigation:** D11 — explicit import path migration; delete old models in Phase F

---

### FM4: Extension Payload Shape Mismatch

**Description:** Generated OSI models have `data: str` for extension payloads (JSON-encoded string per spec), but runtime code passes decoded dicts.

**Likelihood:** Medium
**Impact:** High — silent data corruption or validation failures
**Detection:** Phase B tests with real OSI documents
**Mitigation:** D8 — keep `extract_marivo_extension()` helper; update for generated model shapes

---

### FM5: AOI Artifact Schema Evolution

**Description:** AOI spec adds new result types or modifies artifact schema. Generated models gain new types that runtime doesn't handle.

**Likelihood:** Low (spec is v0.1, changes are gated)
**Impact:** Medium — new artifact types rejected at runtime
**Detection:** CI freshness check + spec example tests
**Mitigation:** Architecture handles this cleanly — new result types in discriminated union, new runtime modules

---

### FM6: Derived Operation Semantic Drift

**Description:** `attribute`, `diagnose`, `validate` are reimplemented on top of AOI atomics. Subtle behavioral differences from old implementations go unnoticed.

**Likelihood:** High
**Impact:** Medium — users see different results for same inputs
**Detection:** Phase E compatibility tests against representative old requests
**Mitigation:** Plan Section 8.2 — add golden-file tests comparing old vs new output

---

### FM7: DuckDB-Only Testing Blindspot

**Description:** All E2E tests run on DuckDB. Trino-specific behavior (SQL dialect differences, type handling, NULL semantics) is untested.

**Likelihood:** High (by design — DuckDB-first)
**Impact:** Low (Trino is behind datasource boundary, not in scope)
**Detection:** Future Trino integration tests
**Mitigation:** Plan non-goal acknowledges this. When Trino path is implemented, add Trino-specific tests.

---

### FM8: MCP Agent Schema Regression

**Description:** MCP tool input schemas change shape (field names, required/optional, types). Agents that were trained on old schemas fail on new ones.

**Likelihood:** High (intentional breaking change)
**Impact:** Medium — agent prompts need updating
**Detection:** MCP tool inputSchema snapshot tests (Phase D)
**Mitigation:** Pre-launch project — no backward compatibility needed. Update agent examples/docs.

---

### FM9: Readiness Inference Non-Determinism

**Description:** Runtime inference of metric metadata (D6) produces different results depending on datasource state, leading to non-reproducible analysis results.

**Likelihood:** Low
**Impact:** High — analysis results change between runs
**Detection:** Readiness check tests with deterministic fixtures
**Mitigation:** Inference must be deterministic given the same OSI document and datasource schema. Add assertion in readiness check.

---

### FM10: Storage Roundtrip Data Loss

**Description:** `model_to_storage → storage_to_model` loses information. Fields present in the generated OSI model are dropped during storage conversion and not reconstructed on read.

**Likelihood:** Medium (new fields in spec, storage adapter not updated)
**Impact:** High — silent data loss
**Detection:** Phase B — add roundtrip test: `assert model == storage_to_model(model_to_storage(model))`
**Mitigation:** Plan review finding #3 — add OSI storage roundtrip tests
