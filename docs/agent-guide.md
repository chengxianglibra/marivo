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

- Physical: synced source objects
- Semantic: entities, metrics, mappings
- Evidence (canonical): sessions, steps, artifacts, findings, propositions, assessments, evidence_gaps, inference_records, action_proposals
- Evidence (legacy, removed in phase 6): observations, claims, evidence_edges, recommendations

**Canonical pipeline:** `artifact → finding → proposition → assessment → action proposal`. DDL: `app/storage/schema.py`.

**Finding identity:** `finding_id = make_finding_id(artifact_id, finding_type, canonical_item_key)`; stable key beats index (`make_canonical_item_key`). Use `make_item_identity(collection, key, index)` to atomically co-generate both `canonical_item_key` + `ArtifactItemRef`. `UNIQUE(artifact_id, finding_type, canonical_item_key)` enforces idempotent replay. Excluded from identity: `extractor_version`, `artifact_schema_version`, `projection_ref`, `rank`, summary text. Types + helpers: `app/evidence_engine/canonical_finding.py`.

**Finding subtypes (7):** `ObservationFinding`, `DeltaFinding`, `DecompositionItemFinding`, `AnomalyCandidateFinding`, `CorrelationResultFinding`, `TestResultFinding`, `ForecastPointFinding`. Extractor output contract: `FindingExtractionResult` TypedDict (`findings`, `extractor_name`, `extractor_version`, `artifact_schema_version`, `finding_count`).

**Family empty semantics:** `app/evidence_engine/family_contract.py` — `FAMILY_ALLOWS_EMPTY`: `observe`/`detect` = True; all others = False (non-empty required). Unknown families fail-safe to non-empty-required. Extractor dispatch key: `(artifact_type, artifact_schema_version)`; NULL version → `"v1"`.

**Canonical ref taxonomy:** `app/evidence_engine/canonical_refs.py` — `PropositionRef`, `PropositionSeedRef` (creation-time only, never updated), `ArtifactLineageRef`, `AssessmentRef` (includes `snapshot_seq`), `EvidenceGapRef`, `GapMembershipEntry` (snapshot-owned `blocking`/`severity`), `InferenceRecordRef`, `ProposalContextRef` (union). Two-surface design: `propositions.seed_finding_refs_json` is authoritative at creation (never modified); `proposition_seed_finding_refs` junction table is the live reverse-lookup index (`add_seed_finding_refs` writes junction only — does NOT update JSON).

**Evidence repositories:** `app/storage/evidence_repositories.py` — all canonical reads/writes go through these; no ad-hoc SQL. `FindingRepository` (idempotent `INSERT OR IGNORE`), `PropositionRepository` (`get_by_identity_key` for dedup; `add_seed_finding_refs` for junction only), `AssessmentRepository` (`get_latest`, `next_snapshot_seq`), `ActionProposalRepository` (`priority_rank ASC`), `EvidenceGapRepository` (`list_by_proposition` + `list_by_session`), `InferenceRecordRepository`. `propositions.identity_key` + UNIQUE partial index enforce dedup.

**Finding extractor registry:** `app/evidence_engine/finding_extractor_registry.py` — `FindingExtractor` ABC + `FindingExtractorRegistry` keyed on `(artifact_type, artifact_schema_version)`. `find(type, None)` normalises `None → "v1"`; `get(type, ver)` strict. `validate_for_commit(family, result)` chains count invariant + D4 family empty contract. `default_finding_registry` singleton; 4d-* extractors bootstrap it.

**Proposition seed registry:** `app/evidence_engine/proposition_seed_registry.py` — maps `finding_type → proposition_type` via versioned templates. 6 v1 templates: `delta→change`, `decomposition_item→decomposition`, `anomaly_candidate→anomaly`, `correlation_result→correlation`, `test_result→test_hypothesis`, `forecast_point→forecast`. `observation` has no template by design. Breaking upgrades must bump `derivation_version`.

**Proposition identity + registration:** `app/evidence_engine/proposition_normalizer.py` — `normalize_proposition_identity(...) → str` (64-char SHA-256 hex); `make_proposition_id(key) → "prop_" + key[:24]`. Identity always includes `session_id`, `origin_kind`, `proposition_type`, `derivation_version` + per-type judgment-semantic fields. Excluded: `template_id/version`, `schema_version`, `created_at`, `seed_finding_refs` order, display/metadata fields. Floats canonicalised; dict keys sorted. `app/evidence_engine/proposition_registration.py` — `register_system_seeded_proposition`: on miss creates + populates junction, returns `created=True`; on hit returns existing id with `created=False` (no writes). Raises `ValueError` if `origin["kind"] != "system_seeded"`.

**Proposition seeding run:** `app/evidence_engine/proposition_seeding_run.py` — `run_system_seeded_propositions(session_id, trigger_finding_ids, proposition_repo, finding_repo, ctx)` orchestrates the full seeding pipeline for a batch of committed findings. Returns `SeedingRunResult` (`created_proposition_ids`, `existing_proposition_ids`, `affected_proposition_ids` sorted, `schema_version="finding_proposition_seeding_run.v1"`). Algorithm: load → sort by `(finding_type, artifact_id, finding_id)` → template routing via `default_seed_registry` → per-template creation condition check → `register_system_seeded_proposition()` → collect results. `affected_proposition_ids = sorted(set(created ∪ existing))` — both new and hit propositions included for assessment recompute. `MaterializationContext` Protocol + `SimpleMaterializationContext` provide artifact/finding dereference. T1–T6 materializers implement per-template resolution and creation-condition logic (flat delta → no proposition; no comparison_window → no proposition; etc). Replay-safe: same inputs always produce same `affected_proposition_ids`.

**Assessment evaluation context:** `app/evidence_engine/assessment_evaluation_context.py` — `build_assessment_evaluation_context(session_id, proposition_id, proposition, candidate_assessment_id, trigger_finding_ids, assessment_repo, gap_repo, finding_repo, inference_record_repo) → AssessmentEvaluationContext`. Assembles the deterministic canonical input bundle required before any assessment recompute. The returned `AssessmentEvaluationContext` (TypedDict, `schema_version="assessment_evaluation_context.v1"`) is the rule engine's sole finding input boundary. 8-phase algorithm: (1) proposition anchor load — extract `assessment_type`, `origin_kind`, `seed_finding_refs`; (2) prior assessment + open gap load — derive `prior_assessment_ids` (snapshot_seq ASC), `current_latest_assessment_id`, `open_gap_ids`; (3) seed hydration — resolve `seed_finding_refs_json` to committed same-session findings → `resolved_seed_finding_ids`; (4) trigger normalisation — dedup + sort `trigger_finding_ids` by `finding_id ASC`; (5) carry-forward closure replay — include `supporting_finding_ids`, `opposing_finding_ids`, inference record `input_finding_ids`, and open-gap `related_finding_ids` from the latest assessment snapshot; (6) proposition-compatible expansion — filter triggers + carry-forward by v1 finding-type/assessment-type compatibility table and subject non-conflict (metric/entity/grain; slice deferred); (7) authored proposition discovery fallback — scan session findings for `agent_authored` propositions with no seeds, no prior evidence closure, and no triggers; (8) candidate set finalisation — `candidate_finding_ids = sorted(dedup(seeds ∪ triggers ∪ carry-forward ∪ fallback))`. Finding-type → assessment-type compatibility: `delta→change_assessment`, `decomposition_item→decomposition_assessment`, `anomaly_candidate→anomaly_assessment`, `correlation_result→correlation_assessment`, `test_result→test_hypothesis_assessment`, `forecast_point→forecast_assessment`; `observation` compatible with all. Only reads canonical objects; raises `ValueError` on session/proposition mismatch.

**Assessment recompute runtime:** `app/evidence_engine/assessment_recompute.py` — `recompute_proposition_assessment(ctx, assessment_repo, gap_repo, inference_record_repo, finding_repo) → AssessmentRecomputeResult`. 9-step pipeline: gate families (precondition/quality/comparability) → support/oppose evidence → status resolution → gap management → confidence shaping → canonical diff → commit. Returns `AssessmentRecomputeResult` (`assessment_id`, `created`, `snapshot_seq`, `status`, `candidate_assessment_id`, `schema_version="assessment_recompute_result.v1"`). `make_assessment_id(session_id, proposition_id, snapshot_seq) → str` — public, deterministic SHA-256 (`"assess_" + hex[:24]`); caller must pre-allocate via `candidate_id = make_assessment_id(session_id, proposition_id, assessment_repo.next_snapshot_seq(proposition_id))` and pass it to `build_assessment_evaluation_context` before calling recompute; at commit time the function re-derives the expected ID and raises `RuntimeError` if they diverge (concurrent write guard). Canonical diff excludes `applied_inference_record_ids` (bound to candidate ID) to prevent false diffs. No-op path returns early with `created=False` before the ID check. Gap management: precondition miss → open new gap (ID derived from candidate_assessment_id); precondition hit → resolve existing open gap; persistent miss → keep open gap.

**Action proposal refresh + publish-ready bundle:** `app/evidence_engine/proposal_refresh_run.py` — `run_action_proposal_refresh(session_id, proposition_id, latest_assessment_id, proposal_context, proposal_repo, assessment_repo, gap_repo, policy_version="v1") → ProposalRefreshResult`. v1 candidate generation: blocking gap (`missing_rule_precondition`) → investigate; blocking gap (quality/comparability) → validate; `status=mixed` + no blocking gaps → validate; `status=insufficient` + no gaps → investigate; `status=supported/contradicted` + no blocking gaps → empty set. `proposal_id` is SHA-256 of `{session_id, action_kind, primary_assessment_ref, target_proposition_ref, proposal_context, payload_semantic}`. No-op when sorted candidate IDs == sorted existing IDs. `assemble_publish_ready_bundle(session_id, proposition_id, assessment_repo, gap_repo, proposal_repo) → PublishReadyBundle` — always reads from latest assessment (highest `snapshot_seq`). `assemble_bundle_from_assessment` is semi-public (in `__all__`), shared with `publish_switch.py`. `open_gaps` in bundles hydrated from `assessment["gap_memberships_json"]` — anchored to the specific assessment snapshot, NOT a live proposition-wide query.

**Publish switch + atomic visibility:** `app/evidence_engine/publish_switch.py` — `execute_publish_switch(session_id, proposition_id, candidate_assessment_id, assessment_repo, proposition_repo) → PublishSwitchResult`. Idempotent: same `assessment_id` → `noop=True`. Anti-downgrade enforced atomically at DB level via conditional UPDATE (`WHERE snapshot_seq < candidate_seq`) in `PropositionRepository.set_externally_visible_assessment` — no Python-level TOCTOU check. Raises `ValueError` if the UPDATE returns 0 rows (concurrent downgrade attempt or assessment does not exist). `assemble_externally_visible_bundle(session_id, proposition_id, ...) → PublishReadyBundle | None` — reads from pointer, returns `None` if pointer is `NULL` (not yet published). Both functions share `assemble_bundle_from_assessment` from `proposal_refresh_run.py`.

**Canonical downstream orchestrator:** `app/evidence_engine/canonical_pipeline_runtime.py` — `run_canonical_downstream(session_id, trigger_finding_ids, finding_repo, proposition_repo, assessment_repo, gap_repo, inference_record_repo, proposal_repo, metadata_store) → CanonicalDownstreamResult`. Wires seeding → recompute → proposal refresh → publish switch for all affected propositions from a batch of trigger findings. Per-proposition errors are caught and recorded in `PropositionPipelineResult["error"]` (non-fatal; other propositions continue). Assessment IDs are deterministic via `make_assessment_id`.

**Replay / recovery:** `app/evidence_engine/replay_recovery.py` — `get_proposition_checkpoint(proposition_id, assessment_repo, proposition_repo) → PropositionRecoveryCheckpoint` — read-only probe returning `{assessment_committed: bool, assessment_id: str | None, externally_visible: bool, schema_version}`. `recover_proposition_pipeline(session_id, proposition_id, trigger_finding_ids, ...) → PropositionRecoveryResult` — 3-path: (noop) already externally visible → return immediately; (partial) assessment committed but not published → run proposal refresh + publish; (full) nothing committed → run full recompute → proposal refresh → publish. Replay-safe: same inputs produce same outcome.

**Version policy:** `app/evidence_engine/version_policy.py` — `VERSION_AXES: list[VersionAxisDecl]` — registry of 6 axes with `bump_class_on_change` field. `classify_version_bump(axis, from_version, to_version) → Literal["forward_compatible", "replay_required", "identity_breaking"]` — raises `ValueError` for unknown axes. `MIGRATION_STATUS_LABELS: frozenset` — `{"migration_required", "migration_in_progress", "migration_blocked"}`; these are runtime truth labels that must **never** enter canonical session/state/context objects. Bump classes: `artifact_schema_version` → forward_compatible; `extractor_version`, `rule_version`, `policy_version` → replay_required; `template_version`, `derivation_version` → identity_breaking.

**Soft invalidation:** `app/evidence_engine/invalidation.py` — `soft_invalidate_finding(session_id, finding_id, reason, finding_repo, proposition_repo, gap_repo, proposal_repo, assessment_repo) → InvalidationResult`. Tombstone-first baseline: marks the finding as invalidated (sets `invalidated_at` + `invalidation_reason`) without deleting it; the finding row remains readable. Returns `InvalidationResult` with `{invalidated_id, object_type="finding", downstream_repair_actions, schema_version}`. `downstream_repair_actions` is a list of `{action: str, target_id: str}` entries covering: `reopen_gap` for every `status=resolved` gap on affected propositions (v1 conservative strategy — does not trace individual inference record inputs); `recompute_assessment` + `bundle_rollback` for every proposition with an externally visible bundle. Does NOT execute repairs — returns a plan only. Idempotent: calling twice does not raise. `FindingRepository.soft_invalidate(finding_id, reason)` and `PropositionRepository.soft_invalidate(proposition_id, reason)` implement the same pattern; `is_invalidated(id) → bool` for both.

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
