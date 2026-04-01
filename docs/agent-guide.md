# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.
- Target-state external step submission contract lives in `docs/api/intent-steps.md`; `docs/analysis/intents/` remains the design source, not the wire spec.
- analysis refactor design docs is located at 'docs/analysis'

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

**Canonical pipeline:** `artifact → finding → proposition → assessment → action proposal`. Findings: deterministic atomic facts (`findings`). Propositions: judgment-layer objects seeded from findings (`propositions`). Assessments: immutable evaluation snapshots with membership + gap tracking (`assessments`, `evidence_gaps`, `inference_records`). Action proposals: planning-shortcut projections from latest assessments (`action_proposals`). DDL: `app/storage/schema.py`.

**Finding identity (Phase 4a-2):** `finding_id = make_finding_id(artifact_id, finding_type, canonical_item_key)`; `canonical_item_key = make_canonical_item_key(collection, key, index)` (stable key beats index). Types + helpers: `app/evidence_engine/canonical_finding.py`. `canonical_item_key` stored as a dedicated column to enforce `UNIQUE(artifact_id, finding_type, canonical_item_key)` for idempotent replay. Fields excluded from identity: `extractor_version`, `artifact_schema_version`, `projection_ref`, `rank`, summary text.

**Canonical ref taxonomy (Phase 4a-3):** `app/evidence_engine/canonical_refs.py`. Types: `PropositionRef`, `PropositionSeedRef`, `ArtifactLineageRef`, `AssessmentRef`, `EvidenceGapRef`, `GapMembershipEntry`, `InferenceRecordRef`, `ProposalContext`, `ProposalContextRef` (union). Invariants: (1) `PropositionSeedRef` is creation-time only — records `seeded_by` lineage, never updated for supporting/opposing evidence; (2) `GapMembershipEntry.blocking`/`.severity` are snapshot-owned — same gap can be reclassified across snapshots; (3) `AssessmentRef` includes `snapshot_seq` for immutable anchoring. Two-surface design: `propositions.seed_finding_refs_json` is authoritative for the seed set at creation time (written by `PropositionRepository.create`, never modified after); `proposition_seed_finding_refs(proposition_id, finding_id, role, UNIQUE(proposition_id, finding_id))` is the live reverse-lookup index for seeding-run tracking (Phase 4e), populated by `PropositionRepository.add_seed_finding_refs` (junction table only — does NOT update the JSON blob).

**Extractor contract + family empty semantics (Phase 4a-4):** `app/evidence_engine/family_contract.py` encodes D4 (approved): `FAMILY_ALLOWS_EMPTY` maps each of the 7 canonical families to a bool (`observe`/`detect` = True; others = False). `check_finding_count(family, count)` raises `FamilyEmptyError` for mandatory-non-empty families when count == 0; unknown families default to non-empty-required (fail-safe). `app/evidence_engine/canonical_finding.py` extended with (a) 7 concrete finding subtypes (`ObservationFinding`, `DeltaFinding`, `DecompositionItemFinding`, `AnomalyCandidateFinding`, `CorrelationResultFinding`, `TestResultFinding`, `ForecastPointFinding`) and (b) `FindingExtractionResult` TypedDict — unified extractor output contract with `findings`, `extractor_name`, `extractor_version`, `artifact_schema_version`, `finding_count`. DDL: `artifacts.artifact_schema_version TEXT` migration added (extractor dispatch key D1: `(artifact_type, artifact_schema_version)`; NULL treated as `'v1'` by convention).

**Evidence repository seam (Phase 4b-1):** `app/storage/evidence_repositories.py` — typed repository layer over all canonical evidence tables. Runtime pipeline code must read/write canonical objects through these repositories, not via ad-hoc SQL. Six classes: `FindingRepository` (idempotent `create` via `INSERT OR IGNORE` on the `UNIQUE(artifact_id, finding_type, canonical_item_key)` index), `PropositionRepository` (includes `get_by_identity_key(session_id, proposition_type, identity_key)` for Phase 4e-2 dedup; `add_seed_finding_refs` writes to the junction table only — does NOT update `seed_finding_refs_json`), `AssessmentRepository` (`get_latest(proposition_id)` returns the highest-`snapshot_seq` snapshot; `next_snapshot_seq(proposition_id)` returns the next seq), `ActionProposalRepository` (`list_by_session` ordered by `priority_rank ASC`), `EvidenceGapRepository` (both `list_by_proposition` and `list_by_session`), `InferenceRecordRepository`. DDL migrations: `propositions.identity_key TEXT NOT NULL DEFAULT ''` + UNIQUE partial index `idx_propositions_session_type_identity ON propositions(session_id, proposition_type, identity_key) WHERE identity_key != ''`. Tests: `tests/test_evidence_repositories.py` (48 tests).

**Finding extractor registry (Phase 4b-2):** `app/evidence_engine/finding_extractor_registry.py` — `FindingExtractor` ABC + `FindingExtractorRegistry` keyed on `(artifact_type, artifact_schema_version)` (D1). Key methods: `register(extractor, *, override=False)` raises `ValueError` on duplicate; `get(artifact_type, version)` strict lookup; `find(artifact_type, version_or_none)` lenient lookup with `None → "v1"` normalisation; `snapshot()` sorted auditable list; `registered_keys()` sorted key list. `default_finding_registry` module singleton starts empty — 4d-* extractor modules populate it. Actual per-family extractors registered in Phases 4d-1 through 4d-4. Tests: `tests/test_finding_extractor_registry.py` (40 tests).

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
- `docs/analysis/evidence-engine/schemas/`: canonical evidence schemas (`session.md`, `finding.md`, `proposition.md`, `assessment.md`, `action-proposal.md`, `state-surface-schema.md`, `context-surface-schema.md`)
- `docs/analysis/evidence-engine/rules/`: rule contracts and supplements (`precondition-gate-contract.md`, `quality-gate-contract.md`, `comparability-gate-contract.md`, `rule-family-design-checklist.md`, `assessment-judgment-policy.md`, `rule-registry-contract.md`); align them with `docs/analysis/evidence-engine/inference-and-gap-engine.md` plus `docs/analysis/evidence-engine/schemas/assessment.md`
