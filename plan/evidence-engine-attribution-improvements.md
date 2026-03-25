# Evidence Engine: Attribution Chain Improvements

> **Origin**: Identified during a live OneService Trino cluster investigation (2026-03-24).
> The investigation revealed that while Factum's readiness and incremental synthesis signals
> worked correctly for `compare_metric` steps, the evidence chain broke down completely
> for root-cause attribution workflows driven by multi-step `aggregate_query`.
>
> **Relationship to existing plans**: This document covers new gaps not addressed by
> `task-list.md` (Phase 1–3, now complete) or `factum-issues-from-oneservice-analysis.md`
> (which covers bugs BUG-1 through UX-1). All items here are net-new capability work.

---

## Background

The Phase 1–3 evidence engine work (M-01 through M-11) built a solid foundation:
incremental synthesis, causal checkers, temporal annotations, reflection context, and
plan patching are all implemented and tested. However, a live investigation exposed a
structural gap that the existing roadmap did not address: **attribution**.

### What the investigation revealed

A WoW analysis of the OneService Trino cluster (2026-03-23 vs 2026-03-16) proceeded as
follows:

1. A `compare_metric` step correctly detected that `query_count` for `state=FAILED`
   increased 30% WoW. Two tentative claims were created at L1 (cross-slice consistency
   confirmed). The evidence engine worked as designed here.

2. To understand *why*, a sequence of `aggregate_query` steps was needed:
   hourly queue-time trends → resource-group breakdown → user breakdown within the
   anomalous RG → WoW comparison of that user's load. Each step produced artifact
   rows, but **zero tentative claims were formed** from any of them.

3. `synthesize_findings` returned empty (0 confirmed claims, 0 recommendations, 0
   evidence gaps) even though the session contained clear causal structure.

4. The root cause — `sys_titan` submitting 41,698 queries at 02:00 (up from 2,889
   baseline, +1344%) while consuming 1.1M CPU-seconds — was identified entirely through
   manually guided `aggregate_query` steps, not through the evidence chain.

### Why the evidence chain did not fire

Three distinct mechanisms caused the failure:

**Mechanism 1 — `AnomalyExtractor` is disconnected from `aggregate_query`.**
`AnomalyExtractor` (`app/evidence_engine/extractors/anomaly.py`) already implements
Z-score anomaly detection, but it requires an explicit `anomaly_rows` artifact type and
caller-supplied `value_col`/`dim_col` context. `aggregate_query` produces
`aggregate_rows` artifacts, which are only processed by `AggregateRowExtractor`.
The extractor produces one `metric_change` observation per row; it never compares rows
against each other to detect which slices are outliers. The signal that
"02:00 query count is 14× the session average for other hours" exists in the data but
is never extracted.

**Mechanism 2 — Multi-dimensional `group_by` creates hyper-granular scopes that prevent temporal upgrade.**
`AggregateRowExtractor` builds each observation's `scope.slice` from all `group_by`
columns. With `group_by: ["log_date", "log_hour", "resource_group"]`, every row has a
unique slice (`{log_date: "20260323", log_hour: "02", resource_group: "others"}`).
`IncrementalSynthesizer` keys claims on `(metric, slice)`, so each observation creates
its own isolated tentative claim.

Note: `CrossSliceConsistencyChecker` is NOT blocked by this — it groups observations by
metric session-wide (`causal_checkers.py:162-166`), not by claim scope. L0→L1 upgrades
can still fire as long as multiple observations share the same metric with consistent
delta sign. The real blocker is `TemporalPrecedenceChecker` (`causal_checkers.py:237-241`),
which requires ≥2 observations in a single claim's `supporting_observations` to establish
temporal ordering. With scope explosion, each claim has exactly 1 supporting observation,
so L1→L2 upgrade never fires even when the data clearly shows a time-ordered sequence.

**Mechanism 3 — No step type exists for dimension attribution.**
When an anomalous observation is identified (e.g., "02:00 queue_time spiked"), the
natural next question is: "which dimension value is responsible?" There is no step type
that takes an anomaly observation and automatically decomposes it into contributing
dimensions. Agents must manually write SQL GROUP BY chains and interpret the results
themselves.

### What is not broken

- `compare_metric` + incremental synthesis works correctly for well-scoped single-metric
  comparisons. The 30% FAILED query increase was correctly captured at L1.
- `observed_window` auto-detection from `group_by` temporal columns (G-2) is implemented
  and functional in `AggregateRowExtractor._detect_temporal_column()`. The issue is
  downstream: temporal observations are produced, but isolated scopes prevent the
  `TemporalPrecedenceChecker` from upgrading any claims.
- `readiness` and `live_claims` fields surface correctly on each step response.
- `reflection-context` returns a valid gap summary when claims exist.

---

## Problem Statement

The evidence chain has three capability gaps that prevent attribution workflows from
producing structured evidence:

| # | Gap | Symptom | Affected files |
|---|-----|---------|----------------|
| G-A | `AggregateRowExtractor` has no cross-row anomaly awareness | Outlier rows (e.g., one hour with 14× normal load) produce the same observation type as normal rows; no `anomaly_detection` observation is generated | `app/evidence_engine/extractors/aggregate.py` |
| G-B | Multi-dimensional `group_by` creates scope explosion that blocks claim formation | `synthesize_findings` sees N isolated single-observation claims, none with enough supporting evidence to confirm | `app/evidence_engine/incremental_synthesizer.py`, `app/evidence_engine/causal_checkers.py` |
| G-C | No step type for dimension attribution | Root-cause tracing from "metric anomaly → responsible entity" requires manually guided multi-step SQL; no structured evidence edge is created | `app/analysis_core/primitives.py`, `app/analysis_core/step_runners/` |

A fourth improvement — cross-step claim chaining — is technically related but is
treated as a separate, longer-term item because it requires changes to the
`IncrementalSynthesizer`'s graph reasoning model and has wider blast radius.

---

## Out of Scope

**Domain-specific step types (qoe_analysis, ad_analysis, rec_analysis, etc.) are
explicitly excluded from this roadmap.**

These were removed in a prior cleanup for good reasons that still hold:

1. They encode "what to look at" rather than "why something happened". A
   `qoe_analysis` step that groups by device type is just a `aggregate_query` with a
   preset template. It adds no causal reasoning capability.
2. They couple the evidence engine to specific business domains, breaking the
   general-purpose design.
3. The correct solution — detecting anomalies and tracing attribution generically — is
   what this roadmap builds. Once `attribute_change` exists, a "QoE investigation"
   is just a session goal, not a step type.

The same argument applies to `anomaly_scan` as a standalone step type. The
`AnomalyExtractor` already exists; the fix is to wire it into `aggregate_query`'s
post-processing path, not to add a separate user-facing step.

---

## Improvement Tasks

### P0 — Cross-row anomaly detection in `AggregateRowExtractor`

**Motivation**

`AggregateRowExtractor` currently processes each row in isolation. The signal that a
particular slice is anomalous relative to the rest of the result set is present in the
data but is never extracted. `AnomalyExtractor` already implements Z-score and IQR
detection, but operates on a separate `anomaly_rows` artifact type and requires explicit
invocation. The fix is to run a cross-row anomaly pass inside `AggregateRowExtractor`
after the per-row loop, adding `anomaly_detection` observations for outlier rows without
changing the existing `metric_change` observations.

**Design**

- After the per-row loop in `AggregateRowExtractor.extract()`, collect all numeric
  values for the detected value column.
- **Stratification for multi-dimensional GROUP BY**: When multiple `group_by` columns
  exist, Z-score must NOT be computed across all rows as a single population — rows from
  different non-temporal dimensions are structurally heterogeneous (e.g., mixing
  resource groups with different baseline volumes). Instead:
  - If `group_by` has only 1 column (or 1 non-temporal column + temporal columns):
    compute Z-score across all rows (single population).
  - If `group_by` has >1 non-temporal column: group rows by all non-temporal columns
    except the last one (the "innermost" dimension), then compute Z-score within each
    stratum. E.g., for `group_by: ["log_date", "resource_group", "user"]`, stratify by
    `(log_date, resource_group)` and detect outlier users within each date+RG group.
  - Alternatively, the caller can set an explicit `anomaly_value_column` context key to
    override the auto-detected value column, and `anomaly_group_by` to specify which
    columns define the strata.
- If `len(rows_in_stratum) >= 5`, compute Z-scores for the value column. Rows with
  `|z| > z_threshold` (default 2.5, configurable via context key `anomaly_z_threshold`)
  emit an additional `anomaly_detection` observation alongside their existing
  `metric_change` observation.
- For IQR-based detection (more robust with small N), also flag rows outside
  `[Q1 - 1.5*IQR, Q3 + 1.5*IQR]` when `len(rows_in_stratum) < 20`.
- The `anomaly_detection` observation payload includes: `value`, `mean`, `std`,
  `z_score`, `outlier_factor` (value / mean), `method` (`"z_score"` or `"iqr"`),
  `stratum` (dict of column→value pairs identifying the stratum, empty for
  single-population mode).
- Reuse `make_anomaly_observation()` from `app/evidence_engine/factories.py` for
  consistent observation construction.
- This is additive — existing `metric_change` observations are unchanged.

**Files to modify**

- `app/evidence_engine/extractors/aggregate.py` — add `_detect_anomalies()` method,
  call it after the per-row loop, extend `observation_types` class var to include
  `"anomaly_detection"`
- `app/evidence_engine/extractors/anomaly.py` — extract shared Z-score/IQR logic into
  a module-level helper function `_compute_outliers(values, z_threshold, use_iqr)`
  importable by `aggregate.py`
- `tests/test_aggregate_extractor.py` — add test cases: outlier row triggers
  `anomaly_detection` observation; non-outlier rows do not; N < 5 produces no anomaly
  observations; `anomaly_z_threshold` context override works

**TODO**

- [ ] **P0.1** Extract shared outlier detection into `_compute_outliers()` in
  `app/evidence_engine/extractors/anomaly.py`
  - Signature: `_compute_outliers(values: list[float], z_threshold: float, use_iqr: bool) -> list[int]`
    returning indices of outlier rows
  - Handles edge cases: N < 3 returns empty, zero std returns empty
- [ ] **P0.2** Add `_detect_anomalies()` to `AggregateRowExtractor`
  - File: `app/evidence_engine/extractors/aggregate.py`
  - Called after the per-row observation loop
  - Skips if `len(rows) < 5` or no value column detected
  - Produces `anomaly_detection` observations using `make_anomaly_observation()`
  - Includes `observed_window` from the row if temporal column was detected (reuse
    existing `temporal_col` detection result)
- [ ] **P0.3** Extend `AggregateRowExtractor.observation_types` to include
  `"anomaly_detection"`
  - File: `app/evidence_engine/extractors/aggregate.py`, line ~155
- [ ] **P0.4** Add tests in `tests/test_aggregate_extractor.py`
  - Outlier row (z > 2.5) → `anomaly_detection` observation emitted
  - All normal rows → no `anomaly_detection` observations
  - N = 4 rows → no anomaly pass runs
  - `anomaly_z_threshold: 1.5` in context → lower threshold respected
  - `observed_window` is propagated from temporal column to anomaly observation
- [ ] **P0.5** Verify `IncrementalSynthesizer` processes `anomaly_detection`
  observation type
  - File: `app/evidence_engine/incremental_synthesizer.py`
  - `anomaly_detection` observation payload has `{value, mean, std, z_score,
    outlier_factor, method, sample_size}` — different from `metric_change` payload
    which has `{current_value, delta_pct, ...}`
  - `_obs_to_scope()`: must extract `metric` from `subject.metric` (same for both
    types — no change needed here)
  - `_obs_delta_pct()`: `anomaly_detection` observations do not have `delta_pct`; the
    synthesizer should treat `outlier_factor` as the magnitude signal instead, or skip
    delta-based contradiction detection for this observation type
  - Add a branch in `_obs_delta_pct()` (or equivalent) for `type == "anomaly_detection"`
    that returns `outlier_factor - 1.0` as the effective delta, so contradiction
    detection (two observations with opposite sign) still works for anomalies
  - Add unit test: `anomaly_detection` observation feeds into `IncrementalSynthesizer`
    and creates a tentative claim with non-null confidence

---

### P1 — Temporal scope folding for multi-dimensional `group_by`

**Motivation**

`TemporalPrecedenceChecker` (`causal_checkers.py:224-286`) upgrades claims from L1 to L2
when a claim has ≥2 supporting observations with non-overlapping `observed_window` values.
`AggregateRowExtractor` already infers `observed_window` from temporal columns in
`group_by` (G-2). However, when `group_by` contains both temporal and non-temporal
columns (e.g., `["log_date", "resource_group"]`), each row gets a unique scope
(`{log_date: X, resource_group: Y}`), so `IncrementalSynthesizer` creates one tentative
claim per unique scope — each with exactly 1 supporting observation.

`CrossSliceConsistencyChecker` still works in this situation because it groups
observations by metric session-wide (`causal_checkers.py:162-166`), not by claim scope.
L0→L1 upgrades can fire normally. The blocker is specifically `TemporalPrecedenceChecker`,
which inspects `claim.supporting_observations` and needs ≥2 entries with `observed_window`.
With one observation per claim, temporal ordering can never be established.

The fix has two parts: (a) ensure `observed_window` is reliably populated when a
temporal column is present among multiple `group_by` columns, and (b) add a
per-step "temporal dimension stripping" option so `IncrementalSynthesizer` can group
observations by their non-temporal scope dimensions, folding time-series observations
into a single multi-observation claim.

**Risk note**: Temporal stripping merges observations that differ only in the temporal
column into one claim. This is semantically correct when the agent's intent is "track
metric M for slice S over time". However, it is NOT safe to apply blindly — if the
query semantics vary across temporal columns (e.g., different aggregation windows),
the merged claim would be incoherent. This is why `temporal_group_by_columns` is a
per-step opt-in parameter, not a session-wide default.

**Design**

Part (a): `observed_window` is already populated correctly by `_detect_temporal_column`
when a temporal column name is in `group_by`. Verify this works when the temporal column
is not the first element of `group_by` (e.g., `["resource_group", "log_date"]`).

Part (b): Add an optional **per-step parameter** `temporal_group_by_columns` (list of
strings) to `aggregate_query` step params. This is a step-level param only, not a
session config. When set, `IncrementalSynthesizer` strips these columns from the scope
key before claim lookup, grouping time-series observations for the same non-temporal
slice into a single claim. The claim accumulates `observed_window` values from all
supporting observations, enabling `TemporalPrecedenceChecker` to work.

Example: with `group_by: ["log_date", "resource_group"]` and
`temporal_group_by_columns: ["log_date"]`, the scope key for claim lookup becomes
`{metric: "queued_time", slice: {resource_group: "global.oneservice.others"}}`.
All date rows for the same resource_group fold into one claim with multiple supporting
observations, each carrying its own `observed_window`.

**Files to modify**

- `app/evidence_engine/extractors/aggregate.py` — verify ordering of `_detect_temporal_column()` is
  position-independent; add integration test
- `app/evidence_engine/incremental_synthesizer.py` — add `_strip_temporal_dims()` helper;
  use it when computing scope key if `temporal_group_by_columns` is present in observation
  context
- `app/service.py` — update `_run_aggregate_query()` to pass `temporal_group_by_columns`
  from step params into extractor / observation context. This is the real implementation
  path today; `step_runners/generic.py` is only a thin delegate.
- `app/api/models.py` — if step request models are later made strongly typed, add
  `temporal_group_by_columns: list[str] | None`; for the current generic step API,
  ensure the param is accepted and documented

**TODO**

- [ ] **P1.1** Verify `_detect_temporal_column` position-independence
  - File: `app/evidence_engine/extractors/aggregate.py`
  - Test: `group_by=["resource_group", "log_date"]` → `temporal_col == "log_date"`
  - Test: `group_by=["log_date", "log_hour"]` → picks `log_date` (day granularity takes
    priority; `log_hour` is also in `TEMPORAL_COLUMN_NAMES_HOUR` — verify day wins)
- [ ] **P1.2** Add `temporal_group_by_columns` to the `aggregate_query` step contract
  - Files: `app/service.py`, optionally `app/api/models.py` if step models become typed
  - Type: `list[str] | None = None`
  - Pass-through from step params → `_run_aggregate_query()` → extractor / observation context
- [ ] **P1.3** Add `_strip_temporal_dims()` to `IncrementalSynthesizer`
  - File: `app/evidence_engine/incremental_synthesizer.py`
  - When `temporal_group_by_columns` keys are present in observation
    `subject.slice`, exclude them when computing the scope key for claim lookup
  - The claim's `supporting_observations` accumulate all time-series rows for the same
    non-temporal slice
- [ ] **P1.4** Verify `TemporalPrecedenceChecker` fires on the resulting multi-observation claims
  - File: `app/evidence_engine/causal_checkers.py`
  - Add integration test: aggregate_query with `temporal_group_by_columns=["log_date"]`
    and 3+ date-ordered rows for the same metric/slice → claim upgrades from L1 to L2

---

### P2 — `attribute_change` step type

**Motivation**

When an anomaly has been identified (either from a `compare_metric` observation or from
the new `anomaly_detection` observations in P0), the next natural step is: "which
dimension value drove this?" This is a structured, deterministic operation — not free-form
SQL — and it should produce a `mechanistically_explains` evidence edge linking the
contributing entity to the upstream anomaly observation.

**Design decision: API step type vs internal auto-trigger**

`attribute_change` should be an **explicit API step type**, parallel to `compare_metric`
and `aggregate_query`.

Internal auto-trigger (running attribution automatically after every anomaly observation)
is rejected because:
- Each attribution requires N Trino queries (one per candidate dimension). Auto-triggering
  on every `anomaly_detection` observation is cost-unbounded.
- The agent has semantic context about which dimensions are meaningful attribution
  candidates; Factum does not.
- Explicitness is consistent with the design principle "typed steps over SQL strings".

The recommended pattern is a two-layer design:
1. `aggregate_query` with P0's cross-row anomaly detection identifies the anomaly
   (automatic, cheap — no extra queries).
2. Agent reads the `anomaly_detection` observation, selects candidate attribution
   dimensions, and calls `attribute_change` explicitly (controlled cost, agent-directed).

**Metric resolution**

`attribute_change` resolves `metric` through the same service-layer path used by
`compare_metric`: `SemanticLayerService.resolve_metric_sql(metric)` provides the
published metric's `definition_sql` (e.g., `avg(queued_time)`) to use as the aggregate
expression. If the metric does not exist or is not in `published` status, return 422.
Table resolution uses the same `QueryRouter` / service infrastructure already exercised
by `_run_aggregate_query()` in `app/service.py`, not any special-case runner logic.

**Step contract**

```
POST /sessions/{session_id}/steps/attribute_change

{
  "metric": "queued_time",                // must be a published semantic metric
  "table": "ods_trino_query_info",        // resolved through QueryRouter
  "time_scope": {
    "mode": "compare",
    "grain": "day",
    "current": {"start": "2026-03-23", "end": "2026-03-24"},
    "baseline": {"start": "2026-03-16", "end": "2026-03-17"}
  },
  "candidate_dimensions": ["resource_group", "user", "source"],
  "anomaly_observation_id": "obs_abc123", // optional: link to upstream anomaly obs
  "top_k": 5,                             // top contributors per dimension (default 5)
  "min_contribution_pct": 5.0             // ignore slices contributing < this % (default 5.0)
}
```

**Query logic per dimension — MUST use existing compile path**

`attribute_change` must NOT hand-write SQL or bypass the existing constraint injection,
governance, and compilation infrastructure. The current `compare_metric` and
`aggregate_query` runners in `app/service.py` use `_session_constraints_to_filter()` to
auto-inject session constraints, resolve engines via `QueryRouter`, and route queries
through `AnalyticsEngine.query_rows()`. `attribute_change` must follow the same path.

**Engineering constraint**: `attribute_change` must not introduce a third query-construction
path alongside `compare_metric` and `aggregate_query`. Re-implementing date formatting,
filter merging, engine capability branching, or provenance logic inside a new runner would
create behavior drift and duplicate maintenance. The new step should be a thin orchestration
layer over the existing service/compiler path, not a parallel mini query engine.

**Implementation strategy**: For each dimension `d` in `candidate_dimensions`, build two
`aggregate_query`-equivalent requests (current + baseline window) using the existing
`_run_aggregate_query()` infrastructure in `SemanticLayerService`:

```python
# Conceptual — actual implementation should call through the service layer
for d in candidate_dimensions:
    current_rows = service._run_aggregate_query(session_id, {
        "table": table,
        "group_by": [d],
        "measures": [
            {"expr": metric_definition_sql, "as": "metric_value"},
            {"expr": "COUNT(*)", "as": "row_count"},
        ],
        "time_scope": current_time_scope,
        "extract_observations": False,  # attribution handles its own extraction
    })
    baseline_rows = service._run_aggregate_query(session_id, {
        # same shape, with baseline_time_scope
    })
```

This ensures:
- Session `constraints` and `raw_filter` are auto-injected (via `_session_constraints_to_filter()`)
- Table resolution goes through `QueryRouter`
- Date column format is inferred per engine type (existing logic)
- Governance policies are enforced
- Provenance is tracked

After both queries return, compute per slice:
`delta_pct = (current - baseline) / baseline * 100`,
`contribution_pct = abs(current - baseline) / sum(abs(current_i - baseline_i)) * 100`.
Return top `top_k` slices sorted by `abs(delta_pct)` DESC, filtered by
`contribution_pct >= min_contribution_pct`.

**Observation model — aligning with `ContributionShiftExtractor` reality**

The current `ContributionShiftExtractor` (`extractors/contribution_shift.py`) produces
**one observation per dimension** (not per contributor). All contributors for a dimension
are folded into a `contributions` list inside the observation payload. The factory
function `make_contribution_observation()` (`factories.py:79-104`) builds:

```python
{
  "observation_id": "obs_...",
  "type": "contribution_shift",
  "subject": {"metric": "queued_time", "slice": {"segment": "resource_group", "biggest_shift": "global.oneservice.others"}},
  "payload": {
    "segment_name": "resource_group",
    "contributions": [
      {"segment_value": "global.oneservice.others", "baseline_share": 0.001, "current_share": 0.87, "delta_share": 0.869, "current_count": 41698},
      {"segment_value": "global.oneservice.oneservice", "baseline_share": 0.999, "current_share": 0.13, "delta_share": -0.869, "current_count": 8302}
    ],
    "biggest_shift_segment": "global.oneservice.others",
    "biggest_delta_share": 0.869
  },
  ...
}
```

`attribute_change` should work WITH this model, not against it. Each dimension
produces one `contribution_shift` observation via the existing extractor. The per-row
schema fed to `ContributionShiftExtractor.extract()` maps to its existing
`(dim_col, baseline_col, current_col)` context interface:

```python
# Per dimension d, rows fed to extractor:
rows = [
  {"resource_group": "global.oneservice.others", "baseline_value": 0.0002, "current_value": 1.1005},
  {"resource_group": "global.oneservice.oneservice", "baseline_value": 0.3, "current_value": 0.25},
]
context = {"dim_col": "resource_group", "baseline_col": "baseline_value", "current_col": "current_value",
           "metric": "queued_time", "share_threshold": 0.05}
```

This produces one `contribution_shift` observation per dimension, with all contributors
folded in. The top-contributor detail is in `payload.biggest_shift_segment`.

**Response**

```json
{
  "step_type": "attribute_change",
  "metric": "queued_time",
  "contributions": [
    {
      "dimension": "resource_group",
      "top_contributors": [
        {
          "value": "global.oneservice.others",
          "current_value": 1.1005,
          "baseline_value": 0.0002,
          "delta_pct": 45000.0,
          "contribution_pct": 87.3,
          "current_row_count": 41698,
          "baseline_row_count": 453
        }
      ]
    }
  ],
  "observations": [...],
  "artifact_id": "art_...",
  "readiness": {...},
  "live_claims": [...]
}
```

If no data exists in the current window, return an empty `contributions` list and set
`debug: {"current_has_data": false, "current_window": "...", "baseline_window": "..."}`.

**Evidence edges and causal inference level — CRITICAL DESIGN POINT**

The L3 upgrade chain requires careful alignment with the existing inference machinery.
Two independent upgrade paths exist in the codebase; the plan must target the right one:

1. **`_derive_inference_level_from_edges()` in `pipeline.py:37-66`** — Used during
   `synthesize_findings`. Scans all evidence edges where `to_node_type == "claim"` and
   maps the highest causal edge type to an inference level. This only works when edges
   point TO claims, not to observations.

2. **`IncrementalSynthesizer._run_causal_checkers()` in `incremental_synthesizer.py:277-346`**
   — Runs after every primitive step. Uses the `CausalCheckerRegistry` which produces
   `CausalEdge` objects with `to_node_type="claim"`. These are persisted via
   `reconcile_causal_edges()`.

**The original plan's edge design was broken**: it proposed edges from `contribution_shift
observation → anomaly observation` (obs→obs). Neither upgrade path inspects obs→obs edges.
The edges would be persisted but invisible to the inference machinery.

**Corrected design — new `MechanisticExplanationChecker`**:

Add a new causal checker to the registry that detects when a `contribution_shift`
observation mechanistically explains an existing claim:

```
MechanisticExplanationChecker (new, runs after CrossSliceConsistencyChecker):
  For each L1+ claim:
    1. Find contribution_shift observations in the session whose metric matches the claim
    2. Check if the claim's scope slice matches a top contributor in the observation payload
    3. If match found: emit CausalEdge(
         from_node_id=contribution_shift_obs_id,
         from_node_type="observation",
         to_node_id=claim_id,
         to_node_type="claim",    # ← THIS is what makes L3 upgrade work
         edge_type="mechanistically_explains",
         ...)
       and LevelUpgrade(claim_id, new_level="L3", ...)
```

This follows the established pattern: all causal checkers produce edges pointing to
claims, and the existing `reconcile_causal_edges()` function persists them.

Additionally, if `anomaly_observation_id` is provided, store a `justifies` edge
(obs→obs, basic layer) for provenance tracing:
- Source: the `contribution_shift` observation
- Target: the `anomaly_observation_id`
- Edge type: `justifies` (basic edge, NOT causal — does not affect inference level)

This obs→obs edge is purely for the evidence graph visualization and audit trail. The
L3 upgrade is driven entirely by the checker's obs→claim `mechanistically_explains` edge.

**Files to create / modify**

- `app/analysis_core/step_runners/attribution.py` — new file; `AttributeChangeRunner`.
  Must delegate query execution through `SemanticLayerService` infrastructure (NOT
  hand-write SQL). See "Query logic" section above.
- `app/analysis_core/primitives.py` — add `"attribute_change"` to `STEP_TAXONOMY` and
  `PRIMITIVE_STEP_TYPES`
- `app/analysis_core/step_registry.py` — register `AttributeChangeRunner`
- `app/service.py` — add `_run_attribute_change()` method to `SemanticLayerService`,
  following the pattern of `_run_compare_metric()` and `_run_aggregate_query()`. This
  is where metric resolution, session constraint injection, and engine routing happen.
  The generic `run_step()` dispatch at `service.py` already handles routing to the
  correct `_run_*` method based on step type.
- `app/api/models.py` — add `AttributeChangeStep` request model
- `app/api/sessions.py` — NO change needed; the generic `POST /sessions/{id}/steps/{step_type}`
  route (`sessions.py:54`) dispatches via `service.run_step()` which routes by step type.
- `app/evidence_engine/causal_checkers.py` — add `MechanisticExplanationChecker` (new
  checker); register it in `build_default_registry()` after `CrossSliceConsistencyChecker`
- `app/evidence_engine/extractors/contribution_shift.py` — verify existing
  `(dim_col, baseline_col, current_col)` context interface works with attribution rows;
  may need minor adjustments
- `app/planning.py` — add `"attribute_change"` to plan validation's allowed step types
- `README.md` — update public-facing step taxonomy and examples if `attribute_change`
  becomes a supported API step
- `CLAUDE.md`, `AGENTS.md` — update step taxonomy and evidence engine sections
- `~/.claude/skills/factum/SKILL.md` — add `attribute_change` step documentation
- `app/static/user.html` — add `attribute_change` to step type dropdown / request builder
  if UI is step-aware, and ensure any step-specific form hints reflect the new params
- `tests/test_attribution.py` — new test file

**TODO**

- [ ] **P2.1** Add `"attribute_change"` to `STEP_TAXONOMY` and `PRIMITIVE_STEP_TYPES`
  - File: `app/analysis_core/primitives.py`
  - Category: `"primitive"` (counts toward budget)
- [ ] **P2.2** Add `AttributeChangeStep` request model
  - File: `app/api/models.py`
  - Fields: `metric`, `table`, `time_scope`,
    `candidate_dimensions: list[str]`,
    `anomaly_observation_id: str | None = None`,
    `top_k: int = 5`, `min_contribution_pct: float = 5.0`
  - Validate: `candidate_dimensions` must be non-empty; `time_scope.mode` must be
    `compare`
- [ ] **P2.3** Implement `_run_attribute_change()` in `SemanticLayerService`
  - File: `app/service.py`
  - MUST follow the existing `_run_compare_metric()` / `_run_aggregate_query()` pattern:
    metric resolution via `resolve_metric_sql()`, engine routing via `QueryRouter`,
    session constraint injection via `_session_constraints_to_filter()`, provenance via
    `_make_provenance()`
  - For each dimension `d`, delegate current + baseline queries through the existing
    `AnalyticsEngine.query_rows()` path (NOT raw SQL construction)
  - Compute `delta_pct`, `contribution_pct`, filter and sort per spec
  - Feed rows to `ContributionShiftExtractor` using its existing `(dim_col, baseline_col,
    current_col)` context interface — produces one observation per dimension
  - Call `IncrementalSynthesizer.process(session_id)` after extraction (same as other
    primitive steps)
  - If `anomaly_observation_id` set: store a `justifies` edge (basic layer, obs→obs) for
    audit trail — NOT `mechanistically_explains` (see P2.5 for L3 path)
- [ ] **P2.4** Implement thin `AttributeChangeRunner` shell
  - File: `app/analysis_core/step_runners/attribution.py` (new)
  - Delegates to `SemanticLayerService._run_attribute_change()` (same pattern as
    `GenericStepRunner` delegates to `_run_compare_metric()`)
  - Register in `app/analysis_core/step_registry.py`
- [ ] **P2.5** Implement `MechanisticExplanationChecker` (CRITICAL for L3 upgrade)
  - File: `app/evidence_engine/causal_checkers.py`
  - New causal checker, registered in `build_default_registry()` after
    `CrossSliceConsistencyChecker`
  - Logic: for each L1+ claim, find `contribution_shift` observations in the session
    whose metric matches the claim's scope. Check if a top contributor
    (`payload.biggest_shift_segment`) matches the claim's scope slice. If so, emit
    `CausalEdge(from_node_id=obs_id, from_node_type="observation",
    to_node_id=claim_id, to_node_type="claim",  # ← required for L3
    edge_type="mechanistically_explains", ...)`
    and `LevelUpgrade(claim_id, new_level="L3", ...)`
  - This is what makes the L3 upgrade actually work — `_derive_inference_level_from_edges()`
    in `pipeline.py:48` requires `to_node_type == "claim"` to trigger inference level
    mapping
- [ ] **P2.6** Verify `ContributionShiftExtractor` works with attribution rows
  - File: `app/evidence_engine/extractors/contribution_shift.py`
  - Verify existing `(dim_col, baseline_col, current_col)` interface accepts the rows
    produced by `_run_attribute_change()`
  - May need: adjust `share_threshold` default, ensure `metric` context key is set
  - Do NOT redesign the extractor to per-contributor model — one observation per dimension
    is the correct granularity
- [ ] **P2.7** Confirm inference level mapping in schemas
  - File: `app/evidence_engine/schemas.py`
  - Verify `CAUSAL_EDGE_TO_INFERENCE_LEVEL["mechanistically_explains"] == "L3"` (already
    confirmed at line 63, but add a test assertion)
- [ ] **P2.8** Add `"attribute_change"` to plan validation
  - File: `app/planning.py`
  - Add to the set of valid step types for plan step validation
- [ ] **P2.9** Write tests in `tests/test_attribution.py`
  - Valid params → contribution rows and `contribution_shift` observations produced
    (one per dimension, NOT per contributor)
  - `MechanisticExplanationChecker` fires → `mechanistically_explains` edge in evidence
    graph with `to_node_type == "claim"` and `from_node_type == "observation"`
  - `anomaly_observation_id` provided → `justifies` edge (obs→obs) for audit trail
  - `anomaly_observation_id` references non-existent obs → 422 with message
    `"anomaly_observation_id not found: obs_xxx"`
  - `candidate_dimensions: []` → 422 with message `"candidate_dimensions must not be empty"`
  - No data in current window → 200 with empty `contributions` and
    `debug.current_has_data == false`
  - **End-to-end L3 test**: `aggregate_query` (anomaly_detection obs) →
    `attribute_change` (contribution_shift obs + MechanisticExplanationChecker fires) →
    `synthesize_findings` → confirmed claim with `inference_level == "L3"` + recommendation
  - `IncrementalSynthesizer` triggered → `live_claims` non-empty after step
  - Metric not found or not published → 422
  - Session constraints are injected (run with `raw_filter` and verify query includes it)
- [ ] **P2.10** Update documentation
  - `README.md` — public API step taxonomy and example usage
  - `CLAUDE.md` / `AGENTS.md` — step taxonomy, evidence engine extractors/checkers
  - `~/.claude/skills/factum/SKILL.md` — add `attribute_change` step docs + example curl
  - `app/static/user.html` — add to step type UI if applicable

**Engineering scope note**

This plan changes more than extractor logic. Even though the HTTP route remains generic,
adding a new step type affects multiple system surfaces:
- step taxonomy and runtime dispatch
- plan validation / planner context surfaced to agents
- user-facing step selection UI and docs
- post-implementation sync of `README.md`, `CLAUDE.md`, `AGENTS.md`, and Factum skill docs

Treating P2 as "just add a runner" would under-scope the work and create documentation/UI
drift even if the backend implementation is correct.

---

### P3 — Cross-step claim inheritance (backlog)

**Motivation**

The three-layer causal chain found in the OneService investigation:

```
sys_titan query volume +524%  (aggregate_query step A)
  → others RG queue congestion  (aggregate_query step B)
    → oneservice RG timeout failures  (aggregate_query step C)
```

…cannot currently be expressed as a Factum evidence chain because `IncrementalSynthesizer`
only forms claims within a single observation scope. Steps A, B, and C produce isolated
observations with different scopes; no cross-scope linking occurs automatically.

**Why this is P3 (backlog)**

- P0 + P2 together already produce the `mechanistically_explains` edge for step B→C
  (once `attribute_change` traces which entity drove the congestion). The remaining
  gap (step A→B) is important but not blocking.
- Cross-step claim chaining requires changes to `IncrementalSynthesizer`'s claim-matching
  logic that have wide blast radius across the test suite.
- A clean design requires first observing how P0+P2 evidence chains behave in practice,
  then defining the linking heuristics based on real data.

**Sketch of the approach (not a committed design)**

Add a `causal_candidate` observation type (distinct from `anomaly_detection`) that
carries a `candidate_cause_observation_id` pointer. `IncrementalSynthesizer` would
detect when a new observation's scope is a temporal predecessor of an existing claim's
scope and propose a `correlates_with` edge, which the causal checkers can then upgrade.

**Placeholder TODO**

- [ ] **P3.1** Define `causal_candidate` observation schema in
  `app/evidence_engine/schemas.py`
- [ ] **P3.2** Design cross-scope matching heuristic in `IncrementalSynthesizer`
  (spike in metric M at time T → spike in metric M' at time T+1 for related scope)
- [ ] **P3.3** Write design doc and get review before implementing

---

## Dependency Graph

```
P0 (cross-row anomaly detection)
  └─→ P1 (temporal propagation fix) [independent but composes well]
  └─→ P2 (attribute_change step)
        └─→ P3 (cross-step claim inheritance) [long-term]
```

P0 and P1 are independent of each other and can be developed in parallel. P2 depends
on P0 being complete (it relies on `anomaly_observation_id` being produced by the
enhanced extractor). P3 depends on observing P0+P2 in practice.

---

## Acceptance Criteria

### P0 Done

- [ ] `aggregate_query` with ≥5 rows where one row is a clear Z-score outlier produces
  at least one `anomaly_detection` observation in addition to `metric_change` observations
- [ ] Normal result sets (no outliers) produce zero `anomaly_detection` observations
- [ ] `anomaly_detection` observations include `z_score`, `outlier_factor`, `method` in payload
- [ ] `observed_window` is populated on `anomaly_detection` observations when a temporal
  column is in `group_by`
- [ ] All existing `test_aggregate_extractor.py` tests pass (no regression)

### P1 Done

- [ ] `aggregate_query` with `temporal_group_by_columns=["log_date"]` and 3+ date rows
  for the same non-temporal slice produces a single claim with multiple supporting
  observations (not N isolated claims)
- [ ] `TemporalPrecedenceChecker` upgrades that claim from L1 to L2 when the observations
  are in temporal order
- [ ] `_detect_temporal_column` returns the correct column regardless of its position
  in `group_by`

### P2 Done

- [ ] `POST /sessions/{id}/steps/attribute_change` returns `contributions` array with
  one entry per `candidate_dimensions` element; each entry contains `top_contributors`
  sorted by `abs(delta_pct)` DESC
- [ ] When attribution succeeds, a `mechanistically_explains` edge exists in
  `GET /sessions/{id}/evidence` response `edges` array with
  `from_node_type == "observation"` and `to_node_type == "claim"`; this is the edge
  that drives the L3 inference upgrade
- [ ] When `anomaly_observation_id` is provided, a separate `justifies` edge exists for
  audit/provenance tracing from the `contribution_shift` observation to the referenced
  anomaly observation (`from_node_type == "observation"`, `to_node_type == "observation"`)
- [ ] `live_claims` after an `attribute_change` step is non-empty
- [ ] After a session with: (1) `aggregate_query` producing an `anomaly_detection` obs,
  (2) `attribute_change` with `anomaly_observation_id` set, (3) `synthesize_findings`:
  — at least one confirmed claim exists with `inference_level == "L3"`
  — at least one recommendation exists in the evidence graph
  — this validates the full `anomaly_detection → contribution_shift →
    mechanistically_explains → L3 claim → recommendation` pipeline end-to-end
- [ ] No data in current window → 200 response with empty `contributions` and
  `debug.current_has_data == false`; no 5xx error
