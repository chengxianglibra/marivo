# Causal Inference Guide

This document explains how Factum promotes claims from **L0 (correlation only)** through
**L2 (temporal precedence established)** automatically, which step types and observation
patterns are required at each stage, and how to design a step sequence that reliably
reaches L2. It uses the March 2026 BI cluster investigation as the reference case.

---

## Inference levels

Claims carry an `inference_level` field that tracks the strongest causal evidence
assembled so far. The level never decreases — it can only be promoted upward.

| Level | Meaning | Automated? |
|-------|---------|------------|
| `L0` | Correlation / association only — default for all new claims | n/a (start state) |
| `L1` | Effect shows a deterministic consistency signal across slices, scopes, or correlated metrics | Yes — `CrossSliceConsistencyChecker`, `CrossScopeCorrelationChecker`, `CrossMetricCorrelationChecker` |
| `L2` | Temporal precedence established — cause observed before effect in non-overlapping windows | Yes — `TemporalPrecedenceChecker` |
| `L3` | Causal mechanism identified | Reserved (not yet implemented) |
| `L4` | Confounders ruled out | Reserved (not yet implemented) |
| `L5` | Experimental confirmation (A/B or natural experiment) | Reserved (not yet implemented) |

Levels appear in every claim object in the evidence graph and in `reflection-context`:

```json
{
  "claim_id": "claim_abc123",
  "text": "failure_rate increased for cluster k8sbi-bi1",
  "inference_level": "L2",
  "inference_justification": [
    "cross_slice_consistency:5/5_days_positive→L1",
    "temporal_precedence:lag=15d→L2"
  ],
  "confidence": 0.81,
  "status": "confirmed"
}
```

`inference_justification` tokens record which checker fired and what evidence it found.
They accumulate across multiple steps — a claim at L2 retains the L1 token as well.

---

## Prerequisite: populating `observed_window`

The `observed_window` field on each observation is required for the L1 → L2 promotion.
`TemporalPrecedenceChecker` is skipped entirely for claims whose supporting observations
all have `observed_window: null`.

Three ways to ensure `observed_window` is set:

### 1. `aggregate_query` with a recognised temporal column (automatic)

`AggregateRowExtractor` inspects the `group_by` columns after a step runs. If any column
name matches a known temporal pattern, it infers a per-row `observed_window` from the
parsed date value.

Recognised day-level column names: `date`, `ds`, `log_date`, `event_date`, `stat_date`,
`report_date`, `partition_date`, `dt`, `day`.

Recognised hour-level column names: `hour`, `hour_slot`, `hour_ts`.

```json
{
  "step_type": "aggregate_query",
  "params": {
    "sql": "SELECT log_date, cluster, COUNT(*) AS heavy_count FROM ods_trino_query_info WHERE log_date BETWEEN '20260201' AND '20260214' GROUP BY log_date, cluster",
    "group_by": ["log_date", "cluster"]
  }
}
```

Each result row will carry `observed_window: {"start": "2026-02-01", "end": "2026-02-02", "granularity": "day"}` (half-open bucket: `[day, next_day)`).

### 2. `aggregate_query` request window fallback

Every typed `aggregate_query` observation inherits the request's `time_scope` as its
default `observed_window`. When `group_by` contains a recognized temporal column, the
extractor refines that to per-row buckets instead of the coarser request window.

### 3. `correlate_metrics` (derived from series date range)

`correlate_metrics` joins two numeric series on a shared key column (`join_on`) and
derives `observed_window` from the union of all matched date values in the joined
result. No extra configuration needed.

---

## L0 → L1: Cross-slice consistency

### What triggers it

`CrossSliceConsistencyChecker` runs automatically after every primitive step as part of
incremental synthesis. It examines **all observations in the session** that share the
claim's metric name and extracts their `delta_pct` value from the observation payload.

**Promotion fires when:**
- At least 2 observations with non-null `delta_pct` exist for the metric
- More than 80% of those observations share the same sign (all positive or all negative)

### Step recipe

Run `compare_metric` or `aggregate_query` to produce observations for the same metric
across multiple slice values or multiple time periods. Observations are attributed to the
same claim when they share the same `scope.metric`.

Example: run `compare_metric` for `failure_rate` on clusters `k8sbi-bi1` and `k8sbi-bi2`
separately. Each produces observations carrying `delta_pct`. If both show the same
direction, the 2/2 = 100% > 80% threshold triggers L1 promotion.

### Output

```
inference_level: "L1"
inference_justification: ["cross_slice_consistency:2/2_slices_positive→L1"]
confidence_boost: +0.02
```

No causal edge is written for L0 → L1. This is a statistical association signal, not
yet causal evidence.

## Alternate L0 → L1 path: Cross-metric consistency

### What triggers it

`CrossMetricCorrelationChecker` runs during causal promotion when the pipeline already
has claim relations from the relation-discovery layer. It does not mine claim pairs on
its own and it does not write new causal edges. Instead, it consumes existing
claim-to-claim `correlates_with` relations and upgrades the participating claims.

**Promotion fires when:**
- Relation discovery has already produced `correlates_with` relations
- Only `exact_match` and `subset_or_overlap` relation categories are considered
- A connected claim group contains at least 3 distinct metrics
- Every participating claim has a resolvable `delta_pct` sign
- All participating claims move in the same direction

### Step recipe

1. Run primitive steps that produce confirmed claims for multiple metrics in the same
   scope or a near-equivalent scope, for example:
   - `query_count` for `user=sys_titan`
   - `cpu_time` for `cluster=k8soneservice-oneservice, user=sys_titan`
   - `queued_time` for `cluster=k8soneservice-oneservice, user=sys_titan`
2. Call `synthesize_findings` so the final synthesis path can:
   - discover claim relations
   - reuse those relations in `CrossMetricCorrelationChecker`
   - upgrade the whole qualifying claim group to `L1`

### Output

```json
{
  "inference_level": "L1",
  "inference_justification": [
    "cross_metric_consistency:3_metrics:user=sys_titan→L1"
  ]
}
```

The evidence graph still shows the original claim-to-claim `correlates_with` edges from
relation discovery. `CrossMetricCorrelationChecker` only changes claim levels; it does
not add a second edge layer of its own.

---

## L1 → L2: Temporal precedence

### What triggers it

`TemporalPrecedenceChecker` runs after every primitive step on claims that are already
at L1. It examines only the **supporting observations** of the specific claim (not all
session observations).

**Promotion fires when:**
- At least 2 supporting observations have non-null `observed_window`
- The earliest window's `end` date is strictly before the latest window's `start` date
  (windows must not overlap: `first.end < last.start`)

### Step recipe

Produce two sets of observations for the same metric in two non-overlapping time
periods, so that both sets are attributed to the same claim.

**Pattern A — two `aggregate_query` steps in different periods:**

1. Run `aggregate_query` for the baseline period (e.g. Feb 1–14) with a typed
   `time_scope`. Observations carry `observed_window` for Feb.
2. Run `aggregate_query` for the current period (e.g. Mar 1–14) with the same
   shape. Observations carry `observed_window` for March.

Both sets are attributed to the same claim (same metric + compatible slice). The checker
sees: earliest window ends 2026-02-14, latest window starts 2026-03-01 — strict
non-overlap → L2 fires. A `temporally_precedes` edge is written to the graph.

**Pattern B — two `compare_metric` steps with different `time_scope.current` windows:**

`compare_metric` populates `observed_window` from `time_scope.current`. Running the
step twice for non-overlapping current windows has the same effect as Pattern A. The
baseline window remains part of the comparison payload/debug context; it is not emitted
as a second observation window.

### Output

```
inference_level: "L2"
inference_justification: ["temporal_precedence:lag=15d→L2"]
confidence_boost: +0.03
```

A `temporally_precedes` edge is written to the evidence graph:

```json
{
  "edge_type": "temporally_precedes",
  "from_node_id": "<earliest_obs_id>",
  "from_node_type": "observation",
  "to_node_id": "<claim_id>",
  "to_node_type": "claim",
  "weight": 0.8,
  "explanation": "Baseline observation (ended 2026-02-14) precedes current observation (started 2026-03-01) by 15 days"
}
```

---

## Bonus paths (level unchanged)

Two additional checkers add justification tokens and a small confidence boost to claims
that have already reached L1 or L2. They do not change the inference level.

### DoseResponseChecker (L1+)

Looks for a monotonic relationship between a numeric predictor and the outcome magnitude.

**Path A — via `correlate_metrics` (preferred):**

Run `correlate_metrics` between a numeric predictor series and the outcome metric. If
|ρ| ≥ 0.7 (Spearman), the checker adds a `dose_response_precomputed:ρ=…` token to the
outcome claim. Requires at least 3 matched pairs.

```json
{
  "step_type": "correlate_metrics",
  "params": {
    "left_step_id": "<step_id_of_heavy_query_count>",
    "left_value_column": "heavy_count",
    "right_step_id": "<step_id_of_failure_rate>",
    "right_value_column": "failure_rate",
    "join_on": "log_date",
    "left_metric": "heavy_query_count",
    "right_metric": "failure_rate"
  }
}
```

The `left_metric` / `right_metric` labels must match the `scope.metric` of the claim
to which the bonus should be attributed.

**Path B — numeric dimension in claim slice (automatic fallback):**

If the claim's slice dict contains a numeric dimension (e.g. a rank or count field),
the checker recomputes Spearman across existing observations automatically. No extra
step required.

**Threshold:** |ρ| ≥ 0.7, minimum 3 pairs.

### ReversalChecker (L2+)

Looks for ≥2 consecutive periods at the end of the observation sequence that reverse
the initial majority direction. Useful when an intervention caused the metric to
recover after the anomaly.

Runs automatically on claims at L2+. Requires ≥3 supporting observations sorted by
`temporal_order`. No extra step needed; if the reversal pattern is present in existing
observations, the token is added automatically.

---

## Step-type reference

| Step type | Produces observations? | `observed_window` populated? | Contributes to L0→L1 | Contributes to L1→L2 |
|-----------|------------------------|------------------------------|----------------------|----------------------|
| `compare_metric` | Yes (`comparison_row`) | Yes (from typed `time_scope`) | Yes | Yes — run for two non-overlapping periods |
| `aggregate_query` | Yes (`aggregate_row`) | Yes (request `time_scope`, optionally refined by temporal `group_by`) | Yes | Yes — run for two non-overlapping periods |
| `correlate_metrics` | Yes (`correlation_result`) | Yes (union of series date range) | Indirect | Via DoseResponse bonus at L1+ |
| `profile_table` | Yes (`profile_row`) | No | Limited | No |
| `sample_rows` | No | No | No | No |
| `synthesize_findings` | No (composite) | — | — | — |

---

## Worked example: BI cluster investigation

**Session goal**: Determine whether heavy queries from user `sycpb_bi` on BI clusters
`k8sbi-bi1` and `k8sbi-bi2` spill over and elevate failure rates for other users.

**Table**: `iceberg.iceberg_inf.ods_trino_query_info` (partitioned by `log_date` YYYYMMDD).

### Step A — baseline heavy-query count (Feb)

```json
{
  "step_type": "aggregate_query",
  "params": {
    "sql": "SELECT log_date, cluster, COUNT(*) AS heavy_count FROM iceberg.iceberg_inf.ods_trino_query_info WHERE log_date BETWEEN '20260201' AND '20260214' AND user = 'sycpb_bi' AND scan_data_size >= 536870912000 GROUP BY log_date, cluster",
    "group_by": ["log_date", "cluster"]
  }
}
```

Produces ~28 observations (14 days × 2 clusters), each carrying `observed_window` for
that day in February.

### Step B — current heavy-query count (March)

Same query with `log_date BETWEEN '20260301' AND '20260314'`. Produces observations for
March.

After incremental synthesis: if ≥80% of daily observations show increased heavy-query
count in the same direction, `CrossSliceConsistencyChecker` promotes the `heavy_count`
claim to **L1**.

### Step C — March failure rate for other users

```json
{
  "step_type": "aggregate_query",
  "params": {
    "sql": "SELECT log_date, cluster, CAST(SUM(CASE WHEN query_state = 'FAILED' THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) AS failure_rate FROM iceberg.iceberg_inf.ods_trino_query_info WHERE log_date BETWEEN '20260301' AND '20260314' AND cluster IN ('k8sbi-bi1', 'k8sbi-bi2') AND user != 'sycpb_bi' GROUP BY log_date, cluster",
    "group_by": ["log_date", "cluster"]
  }
}
```

Produces `failure_rate` observations for March with `observed_window` for each day.

### Step D — `correlate_metrics` (DoseResponse bonus)

```json
{
  "step_type": "correlate_metrics",
  "params": {
    "left_step_id": "<step_A_id>",
    "left_value_column": "heavy_count",
    "right_step_id": "<step_C_id>",
    "right_value_column": "failure_rate",
    "join_on": "log_date",
    "left_metric": "heavy_query_count",
    "right_metric": "failure_rate"
  }
}
```

If |ρ| ≥ 0.7, `DoseResponseChecker` adds `dose_response_precomputed:ρ=…` to the
`failure_rate` claim.

### Step E — baseline failure rate (Feb)

Same query as step C but for `log_date BETWEEN '20260201' AND '20260214'`. Produces
`failure_rate` observations for February with `observed_window` for each Feb day.

Now the `failure_rate` claim has supporting observations from two non-overlapping
windows:
- Feb observations: `observed_window.end ≤ 2026-02-14`
- March observations: `observed_window.start ≥ 2026-03-01`

`TemporalPrecedenceChecker` fires: `first.end (2026-02-14) < last.start (2026-03-01)`.
The claim is promoted to **L2** and a `temporally_precedes` edge is written.

### Step F — synthesize_findings

```json
{"step_type": "synthesize_findings"}
```

Promotes tentative claims to `confirmed` or `insufficient` and generates structured
recommendations backed by the accumulated causal evidence.

### Final claim state

```json
{
  "claim_id": "claim_…",
  "text": "failure_rate for non-sycpb_bi users on k8sbi-bi1/k8sbi-bi2 increased in March relative to February",
  "inference_level": "L2",
  "inference_justification": [
    "cross_slice_consistency:14/14_days_positive→L1",
    "dose_response_precomputed:ρ=0.71",
    "temporal_precedence:lag=15d→L2"
  ],
  "confidence": 0.83,
  "status": "confirmed"
}
```

---

## Why promotion fails: common causes

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| Claim stays at L0 | Fewer than 2 observations with `delta_pct` | Run more slices, or use `aggregate_query` to produce more than one row |
| Claim stays at L0 despite many observations | < 80% of observations share the same `delta_pct` sign | Investigate contradictions first; filter to a cleaner slice dimension |
| Claim stays at L1 | `observed_window` is null on all supporting observations | Run `aggregate_query` with a typed `time_scope`; add temporal `group_by` if you need per-bucket windows |
| Claim stays at L1 | Baseline and current observations cover overlapping date ranges | Ensure the two `aggregate_query` or `compare_metric` steps use strictly non-overlapping periods |
| DoseResponse bonus missing | `correlate_metrics` metric labels do not match the claim's `scope.metric` | Set `left_metric` / `right_metric` to match the claim metric name exactly |
| No `temporally_precedes` edge in graph | Claim was promoted before the causal edge persistence path ran | Run one more primitive step to trigger re-synthesis, or call `synthesize_findings` |
