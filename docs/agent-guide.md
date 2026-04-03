# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.
- Target-state external step submission contract lives in `docs/api/intent-steps.md`; `docs/analysis/intents/` remains the design source, not the wire spec.
- analysis refactor design docs is located at 'docs/analysis'
- Canonical read surfaces expose externally visible state only; do not mix runtime queue/claim/retry status into `session` / `state` / `context`.
- Evidence Engine runtime lifecycle, runtime status surface, and migration/invalidation policies live under `docs/analysis/evidence-engine/`.

## Python / Typing

- All new or modified Python code must satisfy `mypy` for the touched modules.
- Add explicit type annotations for public functions, dataclass/model fields, and non-trivial locals when needed for `mypy` clarity.
- Do not introduce new implicit `Any`, broad `cast(...)`, or `# type: ignore` unless strictly necessary.
- If `# type: ignore` is unavoidable, keep it narrow and add a short reason.
- When changing schemas, API models, or service contracts, update type annotations end-to-end in the same change.
- Before finishing a Python change, run the repository `mypy` check for the touched paths, or explain why it could not be run.

## Code Style (Ruff)

`ruff --fix` and `ruff-format` run as pre-commit hooks. All generated code must pass them
without requiring a fix cycle. Enabled rule families: `E/W` (pycodestyle), `F` (pyflakes),
`I` (isort), `N` (pep8-naming), `UP` (pyupgrade), `B` (bugbear), `C4` (comprehensions),
`SIM` (simplify), `TCH` (type-checking imports), `RUF` (ruff-specific).

**Non-obvious gotchas to avoid:**

- **RUF046** — `round()` with no `ndigits` already returns `int`; never wrap it:
  - Wrong: `int(round(x))` / `int(round(float(x)))`
  - Right: `round(x)` / `round(float(x))`
- **N806** — Local variables inside functions must be lowercase (including pseudo-constants):
  - Wrong: `_MAXIT = 200` / `_EPS = 3e-7` inside a `def`
  - Right: `_maxit = 200` / `_eps = 3e-7` (module-level constants may stay UPPER)
- **N802** — Function names must be lowercase (`def myFunc` → `def my_func`); exempt in tests.
- **UP** — Use modern Python 3.10+ syntax: `X | Y` unions instead of `Optional[X]`, `list[x]`
  instead of `List[x]`, etc.
- **B** — Avoid mutable default arguments, use `assert` only in tests, no bare `except`.
- **SIM** — Prefer ternary / `any()` / `all()` over equivalent `if` chains where natural.
- **I** — Imports must be isort-sorted: stdlib → third-party → first-party (`app`).

Line length is 100 (formatter handles wrapping; no need to manually break lines).
`app/api/**/*.py` ignores `B008` (FastAPI `Depends` calls in defaults are fine).

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && uvicorn app.main:app --reload
```

Tests: `.venv/bin/pytest`. Requires Python 3.12+, `DUCKDB_MVP_DB`. SQLite metadata, DuckDB/Trino engines.

## Architecture

Client → FastAPI → `app/api/` → service → semantic/routing/execution → SQLite + engines.
Metadata reads use synced `source_objects`, not live catalogs.

## Model

**Layers:** Physical (synced source objects) → Semantic (entities/metrics/mappings) → Evidence canonical (sessions, steps, artifacts, findings, propositions, assessments, evidence_gaps, inference_records, action_proposals) → Evidence legacy/phase-6 removal (observations, claims, evidence_edges, recommendations).

**Canonical pipeline:** `artifact → finding → proposition → assessment → action_proposal`. DDL: `app/storage/schema.py`.

**Finding identity:** `finding_id = make_finding_id(artifact_id, finding_type, canonical_item_key)`. Call `make_item_identity(collection, key, index)` to atomically co-generate `canonical_item_key` + `ArtifactItemRef` (stable key beats index). `UNIQUE(artifact_id, finding_type, canonical_item_key)` enforces idempotent replay. Excluded: `extractor_version`, `artifact_schema_version`, `projection_ref`, `rank`, text. 7 subtypes: `ObservationFinding`, `DeltaFinding`, `DecompositionItemFinding`, `AnomalyCandidateFinding`, `CorrelationResultFinding`, `TestResultFinding`, `ForecastPointFinding`. Extractor output: `FindingExtractionResult{findings, extractor_name, extractor_version, artifact_schema_version, finding_count}`. → `canonical_finding.py`

**Family empty contract:** `observe`/`detect` allow empty; all others require non-empty; unknown → fail-safe non-empty. `validate_for_commit(family, result)` chains count invariant + empty contract. Dispatch key: `(artifact_type, artifact_schema_version)`; NULL version → `"v1"`. `find(type, None)` normalises; `get(type, ver)` strict. → `family_contract.py`, `finding_extractor_registry.py`

**Canonical refs:** `PropositionSeedRef` is creation-time only (never modified). `GapMembershipEntry` is snapshot-owned. Two-surface design: `propositions.seed_finding_refs_json` is authoritative at creation; `proposition_seed_finding_refs` junction is the live reverse-lookup — `add_seed_finding_refs` writes junction only, does NOT update JSON. → `canonical_refs.py`, `evidence_repositories.py`

**Evidence repositories:** All canonical reads/writes through `app/storage/evidence_repositories.py`; no ad-hoc SQL. `FindingRepository` (idempotent `INSERT OR IGNORE`), `PropositionRepository` (`get_by_identity_key`; `add_seed_finding_refs` junction only; `set_externally_visible_assessment` conditional UPDATE prevents downgrade atomically), `AssessmentRepository` (`get_latest`, `next_snapshot_seq`), `ActionProposalRepository` (`priority_rank ASC`), `EvidenceGapRepository`, `InferenceRecordRepository`.

**Proposition seeds:** 6 v1 templates — `delta→change`, `decomposition_item→decomposition`, `anomaly_candidate→anomaly`, `correlation_result→correlation`, `test_result→test_hypothesis`, `forecast_point→forecast`; `observation` has no template. Breaking upgrades bump `derivation_version`. → `proposition_seed_registry.py`

**Proposition identity:** `normalize_proposition_identity(…) → 64-char SHA-256`; `make_proposition_id(key) → "prop_" + key[:24]`. Identity includes `session_id`, `origin_kind`, `proposition_type`, `derivation_version` + per-type judgment fields. Excluded: `template_id/version`, `schema_version`, `created_at`, `seed_finding_refs` order, display fields. Floats canonicalised; dict keys sorted. `register_system_seeded_proposition`: CREATE on miss (writes JSON + junction), HIT on existing (no writes). → `proposition_normalizer.py`, `proposition_registration.py`

**Seeding run:** `run_system_seeded_propositions(session_id, trigger_finding_ids, …) → SeedingRunResult{created_proposition_ids, existing_proposition_ids, affected_proposition_ids}`. Sort by `(finding_type, artifact_id, finding_id)` → template routing → creation-condition check (skip: flat delta / no comparison_window / zero contribution / no observed_window / unstructured join_basis / invalid horizon_index) → register. `affected = sorted(created ∪ existing)`. Replay-safe. → `proposition_seeding_run.py`

**Assessment evaluation context:** `build_assessment_evaluation_context(…) → AssessmentEvaluationContext`. 8-phase deterministic assembly: (1) anchor load, (2) prior assessments (snapshot_seq ASC) + open gaps, (3) seed hydration (same-session committed only), (4) trigger dedup+sort, (5) carry-forward closure from latest assessment, (6) compatibility filter (`delta→change_assessment`, `decomposition_item→decomposition_assessment`, etc.; `observation` compatible with all; subject non-conflict on metric/entity/grain), (7) `agent_authored` discovery fallback (no seeds/prior evidence/triggers only), (8) `candidate_finding_ids = sorted(dedup(seeds ∪ triggers ∪ carry-forward ∪ fallback))`. Read-only; raises `ValueError` on session/proposition mismatch. → `assessment_evaluation_context.py`

**Assessment recompute:** `recompute_proposition_assessment(ctx, …) → AssessmentRecomputeResult{assessment_id, created, snapshot_seq, status}`. Pipeline: precondition/quality/comparability gates → support/oppose evidence → status (`supported/contradicted/mixed/insufficient`) → gap management (open/keep/resolve) → confidence shaping (v1: `evidence_sufficiency` max `"weak"`) → canonical diff (excludes `applied_inference_record_ids`) → commit. `make_assessment_id(session_id, proposition_id, snapshot_seq) → "assess_" + SHA-256[:24]` — pre-allocate `candidate_id` before calling `build_assessment_evaluation_context`; ID mismatch at commit raises `RuntimeError`. → `assessment_recompute.py`

**Proposal refresh + publish:** `run_action_proposal_refresh(…)` v1 rules: blocking gap `missing_rule_precondition` → investigate; blocking gap quality/comparability → validate; `mixed`+no blocking → validate; `insufficient`+no gaps → investigate; `supported/contradicted`+no blocking → empty. `open_gaps` anchored to assessment snapshot (NOT a live proposition-wide query). `assemble_publish_ready_bundle` always uses highest `snapshot_seq`. `execute_publish_switch`: atomic conditional UPDATE (idempotent, anti-downgrade); returns `None` from `assemble_externally_visible_bundle` when pointer is NULL. → `proposal_refresh_run.py`, `publish_switch.py`

**Canonical downstream:** `run_canonical_downstream(session_id, trigger_finding_ids, …)` wires seeding→recompute→proposal→publish for all affected propositions; per-proposition errors non-fatal. → `canonical_pipeline_runtime.py`

**Replay / recovery:** `recover_proposition_pipeline` — 3-path: (noop) externally visible; (partial) assessment committed → proposal+publish; (full) nothing → full pipeline. Replay-safe. → `replay_recovery.py`

**Version policy:** 6 axes. Bump classes: `artifact_schema_version` → forward_compatible; `extractor_version`/`rule_version`/`policy_version` → replay_required; `template_version`/`derivation_version` → identity_breaking. `MIGRATION_STATUS_LABELS` must **never** enter canonical objects. → `version_policy.py`

**Read surfaces:**
- `GET /sessions/{id}` → `AnalysisSession` (`schema_version="analysis_session.v1"`); sub-objects `goal`, `governance`, `lifecycle`, `state_summary`; no legacy flat fields.
- `GET /sessions/{id}/runtime-status` → `SessionRuntimeStatus` — operator only; must not derive from canonical `state`/`context`.
- `GET /sessions/{id}/state` → `SessionStateView` — externally visible bundles only; unpublished propositions have `latest_assessment=null`. `POST /state/query` adds `slice` + multi-axis filtering. `slice` on GET → 400; `assessment_presence="unassessed"` + `assessment_statuses` → empty 200; `has_blocking_gaps=false` excludes unassessed. Contract: `docs/api/session-state.md`. → `state_view.py`
- `GET /sessions/{id}/artifacts/{aid}/runtime-status` → `ArtifactRuntimeStatus` — operator only. `artifact_stage`: D4-allows-empty family or has findings → `"findings_committed"`; else → `"staged"`. v1 emits no intermediate stages. → `session_manager.py`
- `GET /sessions/{id}/propositions/{pid}/context` → `PropositionContextView` (`schema_version="proposition_context_view.v1"`): `proposition`, `seed_entries` (`finding=null` when unresolvable), `relevant_findings` (committed assessment closure, not candidate input set), `latest_assessment`, `blocking_gaps`, `non_blocking_gaps`, `applied_inference_records`, `assessment_dependencies` (direct only, no recursion), `artifact_refs`. When `latest_assessment=null` all assessment-derived fields are `null` (not `[]`). `latest_assessment` and proposals always from the same externally visible bundle. Contract: `docs/api/context-surface.md`. → `context_view.py`
- `GET /sessions/{id}/propositions/{pid}/runtime-status` → `PropositionRuntimeStatus` — operator only. `current_stage`: `queued` → `assessment_committed` → `publish_ready` → `externally_visible`. → `session_manager.py`
- `GET /sessions/{id}/reflection-context` — legacy compact summary only; do not add new canonical fields.

**Soft invalidation:** `soft_invalidate_finding(…) → InvalidationResult` — tombstone-first: sets `invalidated_at`/`invalidation_reason`, row stays readable. Returns repair plan only (no execution): `reopen_gap` for resolved gaps + `recompute_assessment`+`bundle_rollback` for published bundles. Idempotent. Same pattern on `PropositionRepository.soft_invalidate`. → `invalidation.py`

## Steps

Defined in `app/analysis_core/primitives.py`: `metric_query`, `profile_table`, `sample_rows`, `aggregate_query`, `attribute_change`, `synthesize_findings`.

### Contracts

- `metric_query`: `table`, `metric`, `time_scope` (required) + `dimensions`, `scope`, `time_axis`, `order`, `limit`
- `aggregate_query`: `table`, `measures`, `time_scope` (required) + `group_by`, `scope`, `time_axis`, `order`, `limit`
- `time_scope` = time windows; `scope` = non-time scope
- `scope.constraints` = scalar entity/row scope; `scope.predicate` = non-time conditions only
- Session root does not carry canonical execution scope; analysis constraints belong to step-level `scope` / `time_scope`

### Rules

- Design drafts (`docs/analysis/`): use `time_scope`/`scope` split; keep artifact/projection separated
- External wire docs (`docs/api/intent-steps.md`): define the target-state per-intent submission surface for `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`, `attribute`, `diagnose`, and `validate`
- **Implemented intents** (all registered in `IntentRunnerRegistry` via `app/intents/`):
  - Atomic: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`
  - Derived: `attribute` (→ `observe×2 + compare + decompose×D`), `diagnose` (→ `detect + (observe×2 + compare + decompose×D)×K`), `validate` (→ `observe×2 + test`; `sample_kind="auto"` fails `SAMPLE_KIND_AMBIGUOUS` in v1)
  - No stubs remain — `_STUB_INTENT_TYPES` is empty
- `diagnose` expansion contract: `detect` scans for anomaly candidates; top-`followup_limit` candidates each get `observe(current) + observe(baseline) + compare(scalar) + decompose×len(candidate_dimensions)`; baseline policy is `previous_adjacent_equal_length` (fixed, non-configurable); design doc: `docs/analysis/intents/derived/diagnose.md`
- `validate` expansion contract: two explicit `left`/`right` populations; `sample_kind` selects `numeric_sample_summary` or `rate_sample_summary` for internal `observe`s; `method` passed through to `test`; output is `validation_bundle`; design doc: `docs/analysis/intents/derived/validate.md`

## Sync

After changes: update this guide + affected API models, UI docs, entrypoint agent docs.

Docs layout:
- `docs/api/`: external HTTP API docs only; target-state step submission is in `intent-steps.md`, and canonical read surfaces are split into `session-state.md` and `context-surface.md`
- `docs/analysis/foundations/`: shared terminology, agent-first interaction principles, and canonical schema design baselines
- `docs/analysis/intents/`: intent-system design docs; atomic schemas live in `docs/analysis/intents/atomic/`, derived schemas live in `docs/analysis/intents/derived/`
- `docs/analysis/evidence-engine/`: Evidence Engine theme docs for overview, runtime pipeline, finding/proposition seeding, inference/gap engine, assessment evaluation context, support/oppose/status resolution, gap-confidence-transition materialization, proposal policy engine, graph/ref semantics, and read surfaces
- `docs/analysis/evidence-engine/`: also contains `runtime-lifecycle.md`, `runtime-status-surface.md`, and `migration-and-invalidation.md` for stage ownership, operator-facing runtime status, and version/invalidation governance
- `docs/analysis/evidence-engine/schemas/`: canonical evidence schemas (`session.md`, `finding.md`, `proposition.md`, `assessment.md`, `action-proposal.md`, `state-surface-schema.md`, `context-surface-schema.md`)
- `docs/analysis/evidence-engine/rules/`: rule contracts and supplements (`precondition-gate-contract.md`, `quality-gate-contract.md`, `comparability-gate-contract.md`, `rule-family-design-checklist.md`, `assessment-judgment-policy.md`, `rule-registry-contract.md`); align them with `docs/analysis/evidence-engine/inference-and-gap-engine.md` plus `docs/analysis/evidence-engine/schemas/assessment.md`
