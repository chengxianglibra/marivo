# Backlog: Causal Inference Gaps

> Derived from a March 2026 BI cluster investigation (`sess_53fb3312be5c`).
> The session successfully identified three cluster-level risks, but the causal
> reasoning required to move claims from L0 to L1/L2 had to be performed
> manually outside Factum and could not be written back into the evidence
> graph. This document tracks the structural gaps that prevented automatic
> inference-level promotion.

---

## G-1  Cross-step correlation produces no observable

**Problem**

`DoseResponseChecker` can run a Spearman test, but only on observations that
already exist inside the evidence engine. When the signal spans *two separate
`aggregate_query` steps* — e.g. "daily sycpb_bi 500 GB+ query count" from
step A and "daily other-user failure rate" from step B — there is no mechanism
to register the relationship between them as a structured observation. The
correlation (ρ = 0.593, p < 0.05) was computed in a Python snippet outside
Factum and was never written back, so it will never trigger a claim upgrade.

**Root cause**

`AggregateRowExtractor` extracts facts from single-step rows only. There is no
extractor type for "relationship between two observation series produced by
different steps."

**Tasks**

- [x] **G-1a — `correlate_metrics` step type**
  Add a new primitive step `correlate_metrics` to `STEP_TAXONOMY` and
  `PRIMITIVE_STEP_TYPES`. Accepts two artifact IDs (or step IDs) plus a column
  selector for each, runs Spearman and optionally Pearson, and emits a
  `correlation` observation carrying `rho`, `p_value`, `n`, `method`, and
  `observed_window` derived from the union of the two series' time ranges.
  Files: `app/analysis_core/primitives.py`, new
  `app/analysis_core/step_runners/correlation.py`.

- [x] **G-1b — `CorrelationObservationExtractor`**
  Register a new extractor in `ExtractorRegistry` that handles
  `step_type == "correlate_metrics"`. Emits one observation per pair with
  `type = "correlation_result"` and populates `observed_window` from the
  series date range.
  Files: `app/evidence_engine/extractors/correlation.py`,
  `app/evidence_engine/registry.py`.

- [x] **G-1c — Wire `DoseResponseChecker` to `correlation_result` observations**
  `DoseResponseChecker` currently re-computes Spearman from raw claim data.
  Extend it to also recognise pre-computed `correlation_result` observations
  (ρ ≥ 0.7 threshold unchanged) so that a `correlate_metrics` step can
  directly trigger the L1 → L1+ bonus path without re-running the SQL.
  File: `app/evidence_engine/causal_checkers.py`.

- [x] **G-1d — Add `correlate_metrics` to the User UI step palette**
  Expose the new step in `app/static/user.html` Analysis panel alongside the
  existing step types.

---

## G-2  `observed_window` is never populated from time-sliced aggregates

**Status: Implemented (2026-03-24)**

**Problem**

`TemporalPrecedenceChecker` requires observations to carry a non-null
`observed_window` (ISO date range). Every `aggregate_query` step that groups
by a date column (e.g. `log_date`, `hour_slot`, `create_time`) has the
information needed to infer the window, but `AggregateRowExtractor` always
leaves `observed_window = null`. As a result, 22 days of daily trend data are
treated as unordered, independent slices and `TemporalPrecedenceChecker` never
fires.

**Root cause**

`AggregateRowExtractor` did not inspect the slice keys for recognisable time
columns.

**Implementation (G-2)**

Completed in three slices on 2026-03-24:

- [x] **G-2a — Time-column heuristic in `AggregateRowExtractor`**
  Added configurable temporal column-name constants (`TEMPORAL_COLUMN_NAMES_DAY`,
  `TEMPORAL_COLUMN_NAMES_HOUR`) and parsing helpers (`_parse_temporal_value`,
  `_build_observed_window`) to `AggregateRowExtractor`. Supports ISO date,
  YYYYMMDD, ISO datetime, and `YYYY-MM-DD HH[:MM[:SS]]` formats. Infers
  per-row `observed_window` from recognized slice keys with day/hour granularity.
  File: `app/evidence_engine/extractors/aggregate.py`.

- [x] **G-2b — typed `time_scope` fallback plus temporal-column refinement**
  `aggregate_query` now inherits `observed_window` from the typed request
  `time_scope`, then refines that window per row when `group_by` contains a
  recognized temporal column. Earlier design discussion referenced an
  `observed_window_column` override; TSU replaced that with the unified
  `time_scope` / `time_axis` contract.
  Files: `app/service.py`, `docs/api/sessions.md`.

- [x] **G-2c — Regression test proving L1→L2 upgrade path**
  Added `G2TemporalWindowInferenceTests` to `tests/test_mvp.py` with four tests:
  - `test_aggregate_query_infers_observed_window_from_event_date`
  - `test_aggregate_query_explicit_observed_window_column`
  - `test_aggregate_query_yyyymmdd_format`
  - `test_l1_to_l2_upgrade_via_temporal_precedence`
  File: `tests/test_mvp.py`.

**Accepted design decisions:**

- Represent inferred windows as half-open buckets: `[day, next_day)` for day granularity.
- Skip `observed_window` for unparseable values rather than fabricating one.
- per-row temporal refinement only occurs when the grouped result itself exposes a recognized temporal slice key.
- `_annotate_temporal` in `service.py` preserves extractor-inferred windows (does not overwrite).

**Remaining follow-up:**

- [x] **G-2d — Persist `temporally_precedes` edges from temporal precedence reasoning**
  `TemporalPrecedenceChecker` currently acts as an inference-level upgrader, but
  the evidence graph contract and causal schemas also describe a `temporally_precedes`
  edge type. Add the missing edge creation/persistence path so temporal ordering is
  written back into the graph instead of existing only as a claim-level upgrade token.
  Files: `app/evidence_engine/causal_checkers.py`, the causal upgrade/evidence
  persistence path, and relevant integration tests.

---

## G-3  `unresolved_confounders` is a static lookup, not data-driven

**Status: Implemented (2026-03-24)**

**Problem**

`reflection-context` returns the same three boilerplate strings for every L0
claim regardless of what the claim is actually about:

```
"correlation only; directionality not established"
"concurrent changes not controlled"
"selection bias not assessed"
```

These are read from `_CAUSAL_CONFOUNDERS["L0"]` in
`app/evidence_engine/schemas.py`. They give the analyst no actionable
guidance: they do not say "check whether sycpb_bi queries temporally precede
other-user failures" or "control for overall query volume before concluding
resource spillover." The evidence_gaps section of the reflection context is
therefore decorative rather than diagnostic.

**Root cause**

`_build_causal_basis` and `_confounders_for` resolve confounders from a
level-keyed table rather than from the claim's own scope, metric, or
supporting observations.

**Tasks**

- [x] **G-3a — Scope-aware confounder generation**
  Replaced the static lookup in `_build_causal_basis` with a rule engine in
  new shared module `app/evidence_engine/causal_basis.py`. Rules inspect the
  claim's scope (metric name, slice keys), supporting observations, and a
  small `SessionSummary` derived from all session observations. Three rules:
  - `missing_observed_window`: supporting observations exist but none have
    `observed_window` set → prompt to add typed `time_scope` and, when needed,
    a temporal `group_by`.
  - `missing_temporal_ordering`: metric is time/failure-based AND supporting
    observations lack both temporal order and observed windows.
  - `normalise_workload_volume`: slice key uses a resource dimension (cluster,
    user, resource_group, etc.) AND comparable slices exist in the session.
    Falls back to level-keyed pairs when no specific rules fire.
    Files: `app/evidence_engine/causal_basis.py` (new),
    `app/evidence_engine/schemas.py`, `app/evidence_engine/pipeline.py`.

- [x] **G-3b — `suggested_validation` should reference available step types**
  `_build_suggested_validation` in `causal_basis.py` generates concrete step
  templates from fired gap keys (e.g. "Run `aggregate_query` with a typed
  `time_scope` and temporal `group_by`...") and falls back to
  corrected level-based text that no longer claims `correlate_metrics`
  establishes directionality.
  File: `app/evidence_engine/causal_basis.py`.

- [x] **G-3c — Deduplicate confounder strings across claims in reflection context**
  `evidence_gaps` in the reflection-context API is now a **session-level**
  deduplicated list of `{"gap_key", "text", "suggested_validation",
  "affected_claims"}` dicts (breaking change). Each `gap_key` appears at most
  once; `affected_claims` lists all claims contributing the gap.
  `tentative_claims[].unresolved_confounders` also uses the scope-aware rule
  engine, returning strings derived from the claim's supporting observations.
  Files: `app/reflection/context.py`, `tests/test_reflection.py`.

**Implementation notes:**

- `unresolved_confounders` in persisted `causal_basis_json` is now a list of
  `{"key": ..., "text": ...}` dicts rather than plain strings. Legacy rows
  with plain strings are handled by `_load_evidence_gaps` backward-compat path.
- `EvidenceGap` NamedTuple and `SessionSummary` NamedTuple are the stable
  internal contracts; both are exported from `causal_basis.py`.
- `_build_causal_basis` in `schemas.py` is preserved as a thin wrapper that
  calls `build_causal_basis` with no observation context (for callers without
  access to the metadata store).

---

## G-4  `compare_metric` silently returns 0 results when `filter` truncates the baseline

**Problem**

Passing a date-range `filter` param to `compare_metric` (e.g.
`"filter": "log_date BETWEEN '20260301' AND '20260322'"`) ANDs it into the
WHERE clause alongside the internally-constructed baseline window. The
baseline period rows (Feb 7–28 in this case) are excluded, every row's
`delta_pct` becomes NULL, and line 544 of `service.py` silently drops all
results:

```python
rows = [r for r in execute_compiled(engine, compiled_query).rows
        if r.get("delta_pct") is not None]
```

The step returns `"Metric '...' comparison returned no results."` with no
indication of *why* — indistinguishable from "the table is empty" or "the
metric SQL returned NULL." The caller (human or agent) has no signal that the
problem is a self-inflicted filter conflict.

**Root cause**

Two separate issues combine:

1. `_run_compare_metric` passes `**params` straight into the step compiler,
   so any `filter` the caller provides is AND-combined with the period WHERE
   clause without validation.
2. The 0-result summary string does not distinguish between an empty baseline,
   an empty current window, and a populated result set where all `delta_pct`
   values happened to be NULL.

**Design direction**

`compare_metric` should be treated as a **dual-window comparison primitive**
with a narrow contract:

- entity / slice scoping belongs in session `constraints` or `raw_filter`
- time scoping belongs in typed `time_scope`
- step-level `filter` / `where` is not part of the supported contract

This is expected to remain compatible with existing partition-filter policy as
long as the required partition column is the same column used as
`compare_metric`'s period axis (the common case for `log_date` / `event_date`).
If a deployment depends on a different partition column, track that as a
separate compatibility follow-up rather than keeping step-level `filter`
support.

**Tasks**

- [x] **G-4a — Enforce the no-filter contract for `compare_metric`**
  Treat any step-level `filter` / `where` param on `compare_metric` as invalid
  input. Reject it at the service entry point before SQL compilation, and add
  matching plan-validation coverage so draft/validate and runtime agree. The
  error should explicitly direct callers to use session `constraints` /
  `raw_filter` for entity scoping and `time_scope` for time
  windowing. If needed, add a defensive rejection in the compiler path so the
  old contract cannot leak through indirectly.
  Files: `app/service.py`, `app/planning.py`, optionally
  `app/analysis_core/compiler.py`.

- [x] **G-4b — Rewrite the public contract and migration path**
  Remove all `compare_metric` documentation and examples that imply step-level
  `filter` is supported. Because step execution currently accepts a free-form
  params dict, this is not a single schema deletion; the public contract must
  instead be aligned across runtime behaviour, plan validation, and docs. Add a
  positive worked example showing a cluster-scoped month-over-month comparison
  using session `raw_filter` plus typed `time_scope`.
  Files: `docs/api/sessions.md`, `docs/api/quickstart.md`, and any other
  `compare_metric` examples.

- [x] **G-4c — Replace old tests with contract and migration regressions**
  Remove or rewrite any positive `compare_metric + filter` tests so they do not
  preserve the old behaviour by accident. Add regressions for: runtime rejection
  of `filter`, plan-validation rejection of `filter`, and the recommended
  success path using session `raw_filter` plus typed `time_scope`.
  Where feasible, add a sanity-check that `compare_metric`'s generated period
  WHERE remains sufficient for environments whose partition filter policy uses
  the same date axis.
  Files: `tests/test_compiler_executor.py`, `tests/test_mvp.py`,
  `tests/test_plan_validation.py`.

- [x] **G-4d — Differentiate empty-result diagnostics (recommended follow-up)**
  Decide whether G-4 should also improve the `"comparison returned no results"`
  summary so callers can distinguish at least three cases: empty baseline
  window, empty current window, and populated rows whose `delta_pct` values are
  all NULL. This is separable from the contract fix, but it addresses the
  second half of the current root cause and would make future debugging less
  guessy for both humans and agents.
  File: `app/service.py`.

---

## G-5  Unit discoveries are never written back to entity definitions

**Problem**

During the March 2026 BI cluster investigation, analysis revealed that
`elapsed_time` values are in **seconds** (median ≈ 3.2 s). This is a
structural fact about the field that belongs in the semantic entity definition
— yet Factum has no path from "observation about a field's unit" to "pending
entity metadata update." The analyst must manually remember the finding, leave
the session, and edit the entity separately. If they forget, every future
`compare_metric` step silently produces correctly-calculated but
unit-ambiguous results, and downstream recommendations (e.g. "queries
exceeding 5-second threshold") carry no machine-readable unit assertion.

**Root cause**

Two issues combine:

1. `AggregateRowExtractor` extracts facts about *values* (magnitudes, rates,
   deltas) but never infers *properties* of the *column itself* (unit,
   data type interpretation, expected range). Inferred column metadata has
   nowhere to go in the current observation schema.
2. There is no recommendation action type that carries a structured
   `entity_patch` — recommendations today hold free-text `action` strings
   and `causal_basis` but no machine-readable diff against a semantic entity.

**Tasks**

- [x] **G-5a — Unit heuristic in `AggregateRowExtractor`**
  After extracting numeric observations for a column, run a **layered unit
  inference** pass instead of a single fixed heuristic. Resolution order:
  (1) existing field metadata from synced column properties / semantic entity
  metadata, if a unit is already present; (2) family-specific heuristics for
  common units, starting with **duration** (`time`, `duration`, `latency`,
  `delay`, `elapsed`) and **bytes** (`bytes`, `size`, `memory`, `traffic`,
  `bandwidth`); (3) distribution-based scoring from the observed values
  (`p50`, `p95`, `max`, and magnitude buckets) to rank candidate units within
  the detected family. Emit a `column_unit_hint` annotation carrying
  `{"column": "<name>", "source": "metadata|semantic|heuristic", "family":
  "<duration|bytes|other>", "candidates": [{"unit": "<unit>", "confidence":
  0.0-1.0, "signals": ["..."]}]}` rather than a single inferred unit. If
  existing metadata conflicts with the heuristic, emit the conflict as a
  low-confidence hint and do not treat it as auto-applicable. Keep keyword
  lists, aliases, and magnitude thresholds as module-level constants so the
  supported unit families can expand without changing extractor control flow.
  File: `app/evidence_engine/extractors/aggregate.py`.

- [x] **G-5b — `entity_patch_json` column on `recommendations`**
  Add an optional `entity_patch_json` TEXT column to the `recommendations`
  table (schema DDL + `RecommendationModel`). The patch schema:
  ```json
  {
    "entity_id": "<ent_…>",
    "field": "<column_name>",
    "property": "unit",
    "current_value": null,
    "suggested_value": "<unit_string>",
    "confidence": 0.95,
    "evidence_step_id": "<step_…>"
  }
  ```
  `IncrementalSynthesizer` should create a recommendation with this payload
  whenever a `column_unit_hint` annotation is present on a
  `confirmed`-or-better claim and a matching published entity exists in the
  semantic layer.
  Files: `app/storage/schema.py`, `app/models.py`,
  `app/evidence_engine/incremental_synthesizer.py`.

- [x] **G-5c — Surface `entity_update_suggestions` in reflection context**
  `build_reflection_context()` should collect all recommendations whose
  `entity_patch_json` is non-null and expose them under a new top-level key
  `entity_update_suggestions`. This makes the suggestions directly visible to
  an agent reading the reflection context without scanning the full
  recommendation list.
  File: `app/reflection/context.py`.

- [x] **G-5d — `PATCH /semantic/entities/{id}/metadata` endpoint**
  Add a lightweight endpoint that accepts `{"field": "…", "property": "…",
  "value": "…"}` and persists the update to the entity's `metadata_json`
  column (incrementally merged, not replaced). Requires entity to be in
  `published` state; bumps `revision`. The User UI "Evidence" panel should
  render a **"Apply to entity"** button next to any recommendation that
  carries an `entity_patch_json`, calling this endpoint on click.
  Files: `app/api/semantic.py`, `app/semantic.py`,
  `app/static/user.html`.

- [x] **G-5e — Unit guard in `compare_metric` output**
  When a confirmed `column_unit_hint` claim exists for the metric's
  underlying column, append a `unit` field to each row in the
  `compare_metric` result artifact and include a `unit_note` string in the
  step summary (e.g. `"elapsed_time values are in seconds"`). This surfaces
  the unit inline rather than requiring the analyst to consult the entity
  definition separately.
  File: `app/analysis_core/step_runners/generic.py`.

---

## Cross-cutting

- [x] **X-1 — `observed_window` integration test**
  Extend `tests/test_reflection.py` with a fixture that seeds observations
  carrying `observed_window` values and asserts that `readiness_signal` and
  `tentative_claims` in the reflection context reflect the temporal ordering
  (i.e. claims backed by a longer or more recent window score higher on
  `evidence_sufficiency`).

- [x] **X-2 — Document the inference-level promotion path**
  Add a section to `docs/api/sessions.md` (or a new
  `docs/service/causal-inference.md`) that explains exactly which step types
  and observation patterns are required to move a claim from L0 through L2,
  with a worked example using the BI cluster investigation as the reference
  case. This makes the promotion path explicit for future investigation
  authors rather than implicit in the checker source code.
