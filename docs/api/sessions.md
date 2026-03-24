# Sessions & Steps

Sessions are the primary unit of analysis in Factum. Every investigation belongs to a session that holds a goal, constraints, budget, and policy. All steps, evidence, and plans are scoped to a session.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Create a new session |
| `GET` | `/sessions` | List sessions |
| `GET` | `/sessions/{session_id}` | Get a session |
| `POST` | `/sessions/{session_id}/steps/{step_type}` | Execute a step |
| `GET` | `/sessions/{session_id}/evidence` | Get the evidence graph |
| `GET` | `/sessions/{session_id}/reflection-context` | Get structured evidence-gap summary for agents |

---

## Create Session

```
POST /sessions
```

Creates a new analysis session with a goal, constraints, budget, and policy.

### Request Body

```json
{
  "goal": "Investigate watch time drop among mobile users in Q1 2024",
  "constraints": {
    "platform": "mobile",
    "region": "US"
  },
  "budget": {
    "max_scan_bytes": 500000000000,
    "max_latency_sec": 120
  },
  "policy": {
    "aggregate_only": true,
    "min_group_size": 100
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `goal` | string | yes | Human-readable description of the analysis objective |
| `constraints` | object | no | Key-value pairs injected as WHERE filters into steps (default: `{}`) |
| `budget` | object | no | Execution limits (default: `{"max_scan_bytes": 500000000000, "max_latency_sec": 120}`) |
| `policy` | object | no | Data governance policy (default: `{"aggregate_only": true, "min_group_size": 100}`) |

**Budget fields:**

| Field | Type | Description |
|-------|------|-------------|
| `max_scan_bytes` | integer | Maximum bytes scanned across all steps |
| `max_latency_sec` | integer | Maximum wall-clock seconds for any single step |

**Policy fields:**

| Field | Type | Description |
|-------|------|-------------|
| `aggregate_only` | boolean | Disallow row-level queries; enforce aggregation |
| `min_group_size` | integer | Minimum rows per group in aggregate results |

### Response

```json
{
  "session_id": "sess_a1b2c3d4e5f6",
  "goal": "Investigate watch time drop among mobile users in Q1 2024",
  "constraints": {"platform": "mobile", "region": "US"},
  "budget": {"max_scan_bytes": 500000000000, "max_latency_sec": 120},
  "policy": {"aggregate_only": true, "min_group_size": 100},
  "status": "active",
  "created_at": "2024-01-15T10:00:00+00:00"
}
```

---

## List Sessions

```
GET /sessions
```

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status: `active`, `completed`, `abandoned` |

### Response

Array of session objects (same shape as Create Session response).

---

## Get Session

```
GET /sessions/{session_id}
```

### Response

Session object (same shape as Create Session response).

---

## Execute Step

```
POST /sessions/{session_id}/steps/{step_type}
```

Executes a typed analysis step within the session. Step parameters are provided in the request body. The response contains the step result, extracted observations, and provenance metadata.

**Valid `step_type` values:** `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, `correlate_metrics`, `synthesize_findings`

Session constraints are automatically merged into the WHERE clause for `compare_metric`, `sample_rows`, and `aggregate_query` steps.

---

### compare_metric

Compare a published semantic metric between a baseline window and a current window. Requires that the metric is published in the semantic layer and has a corresponding mapping to a source object.

```
POST /sessions/{session_id}/steps/compare_metric
```

**Request body:**

```json
{
  "metric_name": "avg_watch_time_minutes",
  "period_start": "2024-01-01",
  "period_end": "2024-01-31",
  "filter": "platform = 'mobile'",
  "order": "DESC",
  "limit": 20
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `metric_name` | string | yes | Name of a published semantic metric |
| `period_start` | string | no | Start of the current window (ISO date or engine-native format) |
| `period_end` | string | no | End of the current window |
| `filter` | string | no | Additional SQL filter expression (ANDed with session constraints) |
| `order` | string | no | `ASC` or `DESC` (default: `DESC`) |
| `limit` | integer | no | Maximum rows to return (default: `10`) |

**Response:**

```json
{
  "step_id": "step_...",
  "step_type": "compare_metric",
  "status": "completed",
  "summary": "avg_watch_time_minutes: 14.2% decrease across 8 slices",
  "result": {
    "metric": "avg_watch_time_minutes",
    "rows": [
      {
        "dimension_values": {"device_type": "iOS"},
        "baseline_value": 42.3,
        "current_value": 36.3,
        "absolute_change": -6.0,
        "relative_change_pct": -14.2,
        "direction": "down"
      }
    ]
  },
  "observations": [...],
  "provenance": {
    "query_hash": "sha256:...",
    "engine": "duckdb",
    "timestamp": "2024-01-15T10:05:00+00:00",
    "param_count": 4
  },
  "readiness": {
    "goal_coverage": 0.4,
    "evidence_sufficiency": 0.33,
    "contradiction_resolution": 1.0,
    "budget_remaining": 0.87,
    "diminishing_returns": 0.0,
    "suggested_action": "continue_exploring"
  },
  "live_claims": [
    {
      "claim_id": "claim_...",
      "claim_type": "metric_regression",
      "text": "Average watch time declined 14.2% on iOS mobile",
      "confidence": 0.72,
      "status": "tentative",
      "scope": {"device_type": "iOS"},
      "inference_level": "L0",
      "inference_justification": []
    }
  ]
}
```

**Readiness signal (all primitive steps):**

Every primitive step response includes `readiness` and `live_claims`. These are signals — Factum never auto-triggers next steps; the agent decides what to do next.

**`readiness` fields:**

| Field | Range | Description |
|-------|-------|-------------|
| `goal_coverage` | [0, 1] | Fraction of session goal covered by claims with confidence ≥ 0.5 (denominator: 5) |
| `evidence_sufficiency` | [0, 1] | Average supporting observations per claim, clipped to [0, 1] |
| `contradiction_resolution` | [0, 1] | Fraction of claims with no contradicting observations |
| `budget_remaining` | [0, 1] | (max_steps − primitive_step_count) / max_steps |
| `diminishing_returns` | [0, 1] | Fraction of last 3 steps that produced ≥ 1 new claim |
| `suggested_action` | string | Recommended next action (see below) |

**`suggested_action` cascade (checked in order):**

| Action | Condition |
|--------|-----------|
| `resolve_contradiction` | Any claim has contradicting observations |
| `synthesize` | `goal_coverage` ≥ 0.7 AND `evidence_sufficiency` ≥ 0.7 |
| `stop` | Budget nearly exhausted OR `diminishing_returns` < 0.2 with sufficient evidence |
| `continue_exploring` | Otherwise |

**`live_claims`** — list of all `tentative` and `confirmed` claims in the session, fully hydrated with scope, confidence, inference level, and supporting/contradicting observations.

---

### profile_table

Profile a table's row count and column-level completeness and cardinality signals.

```
POST /sessions/{session_id}/steps/profile_table
```

**Request body:**

```json
{
  "table_name": "events.user_video_watch"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table_name` | string | yes | Fully qualified or catalog-resolvable table name |

**Response:**

```json
{
  "step_id": "step_...",
  "step_type": "profile_table",
  "status": "completed",
  "result": {
    "table_name": "events.user_video_watch",
    "row_count": 15234891,
    "columns": [
      {
        "name": "user_id",
        "null_rate": 0.001,
        "distinct_count": 3241000,
        "sample_values": ["u_001", "u_002"]
      }
    ]
  }
}
```

---

### sample_rows

Return a bounded sample of rows from a table. Supports filter expressions and column selection. Session constraints are auto-injected.

```
POST /sessions/{session_id}/steps/sample_rows
```

**Request body:**

```json
{
  "table_name": "events.user_video_watch",
  "filter": "watch_duration_sec > 30",
  "columns": ["user_id", "video_id", "watch_duration_sec"],
  "limit": 100
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table_name` | string | yes | Table to sample |
| `filter` | string | no | SQL filter expression (ANDed with session constraints) |
| `columns` | array[string] | no | Columns to include (default: all) |
| `limit` | integer | no | Maximum rows (default: `100`) |

---

### aggregate_query

Execute an ad-hoc GROUP BY aggregation. Observations are extracted automatically from the result. Session constraints are auto-injected.

**G-2 enhancement:** When a temporal column (e.g., `log_date`, `event_date`, `dt`) is present in `group_by`, observations automatically receive `observed_window` inferred from the slice key. This enables `TemporalPrecedenceChecker` to recognize time-ordered evidence and promote claims from L1 to L2.

```
POST /sessions/{session_id}/steps/aggregate_query
```

**Request body:**

```json
{
  "table_name": "events.user_video_watch",
  "select": ["device_type", "region", "AVG(watch_duration_sec) as avg_watch_sec", "COUNT(*) as cnt"],
  "group_by": ["device_type", "region"],
  "where": "event_date >= '2024-01-01'",
  "order_by": "avg_watch_sec DESC",
  "limit": 50,
  "extract_observations": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table_name` | string | yes | Table to query |
| `select` | array[string] | yes | SELECT expressions (column names or SQL aggregate expressions with aliases) |
| `group_by` | array[string] | yes | Columns to group by |
| `where` | string | no | SQL filter expression (ANDed with session constraints) |
| `order_by` | string | no | ORDER BY clause (e.g., `avg_watch_sec DESC`) |
| `limit` | integer | no | Maximum rows (default: `100`) |
| `extract_observations` | boolean | no | Extract observations from result rows (default: `true`). Set to `false` to skip. |
| `observed_window_column` | string | no | Explicit column for `observed_window` inference (G-2). Default: auto-detect from temporal column names (`log_date`, `event_date`, `dt`, `date`, `day`, `hour`). |

**Temporal column auto-detection (G-2):**

When `observed_window_column` is not specified, the extractor checks for these column names in `group_by`:

- **Day granularity:** `date`, `day`, `dt`, `log_date`, `event_date`, `partition_date`, `report_date`, `transaction_date`, `created_date`, `updated_date`
- **Hour granularity:** `hour`, `dt_hour`, `log_hour`, `event_hour`, `report_hour`

Supported temporal value formats: ISO date (`2024-01-15`), YYYYMMDD (`20240115`), ISO datetime (`2024-01-15T10:30:00`), `YYYY-MM-DD HH[:MM[:SS]]`.

---

### correlate_metrics

Compute Spearman (and optionally Pearson) correlation between two numeric series from prior step artifacts. Emits a `correlation_result` observation that can trigger causal inference bonus tokens for L1+ claims.

**Important:** This step operates on artifacts (outputs from previous `aggregate_query` steps), not on raw tables. It requires explicit metric names that match the claims you want to correlate.

```
POST /sessions/{session_id}/steps/correlate_metrics
```

**Request body:**

```json
{
  "left_artifact_id": "art_abc123...",
  "right_artifact_id": "art_def456...",
  "left_value_column": "query_count",
  "right_value_column": "failure_rate",
  "join_on": "log_date",
  "left_metric": "daily_query_count",
  "right_metric": "daily_failure_rate",
  "method": "spearman",
  "min_pairs": 3,
  "left_scope_slice": {"platform": "ios"},
  "right_scope_slice": {"platform": "ios"}
}
```

**Required parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `left_artifact_id` or `left_step_id` | string | Artifact or step ID for the left series |
| `right_artifact_id` or `right_step_id` | string | Artifact or step ID for the right series |
| `left_value_column` | string | Numeric column in left series |
| `right_value_column` | string | Numeric column in right series |
| `join_on` | string | Shared key column to align both series (e.g., `log_date`) |
| `left_metric` | string | Metric name for left series (must match claim's `scope.metric`) |
| `right_metric` | string | Metric name for right series (must match claim's `scope.metric`) |

**Optional parameters:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `method` | string | `"spearman"` | Correlation method: `"spearman"`, `"pearson"`, or `"both"` |
| `min_pairs` | integer | `3` | Minimum matched pairs required (raises ValueError if not met) |
| `left_scope_slice` | object | `{}` | Scope slice for left series (for claim matching) |
| `right_scope_slice` | object | `{}` | Scope slice for right series (for claim matching) |

**Response:**

```json
{
  "step_id": "step_...",
  "step_type": "correlate_metrics",
  "status": "completed",
  "summary": "Correlation between 'daily_query_count' and 'daily_failure_rate' over 22 paired observations on 'log_date': ρ=0.593, p=0.0034 (spearman).",
  "artifact_id": "art_...",
  "correlation": {
    "n": 22,
    "method": "spearman",
    "join_on": "log_date",
    "left_metric": "daily_query_count",
    "right_metric": "daily_failure_rate",
    "rho": 0.593,
    "p_value": 0.0034,
    "observed_window": {
      "start": "2026-03-01",
      "end": "2026-03-22",
      "granularity": "day"
    },
    "left_series_size": 22,
    "right_series_size": 22,
    "matched_pairs": 22
  },
  "observations": [
    {
      "observation_id": "obs_...",
      "type": "correlation_result",
      "subject": {
        "metric": "daily_failure_rate",
        "slice": {},
        "related_metric": "daily_query_count"
      },
      "payload": {
        "rho": 0.593,
        "p_value": 0.0034,
        "n": 22,
        "method": "spearman",
        "left_metric": "daily_query_count",
        "right_metric": "daily_failure_rate",
        "join_on": "log_date"
      },
      "significance": {
        "significant": true,
        "strong": false
      }
    }
  ]
}
```

**Causal inference integration:**

When `|rho| >= 0.7`, the `correlation_result` observation triggers `DoseResponseChecker` to add a bonus justification token to matching L1+ claims:

- Matching criteria: claim's `scope.metric` equals `left_metric` or `right_metric`
- If `scope_slice` is provided, claim's `scope.slice` must also match
- Token: `dose_response_precomputed:ρ=0.xxx`

**observed_window derivation:**

The `observed_window` is derived from the **union** of date values in both series (not just matched rows). This represents the full time span of both artifact series.

---

### synthesize_findings

Composite step that promotes `tentative` claims (created incrementally after each primitive step) into `confirmed` or `insufficient` status, then generates recommendations. This step does **not** count toward `budget.max_steps`.

After every primitive step, `IncrementalSynthesizer` automatically creates or updates tentative claims keyed by (metric, slice). `synthesize_findings` promotes them:
- `tentative` → `confirmed` — confidence ≥ 0.5 AND no contradicting observations
- `tentative` → `insufficient` — otherwise

Recommendations are generated from confirmed claims. If all claims are insufficient, a P2 investigation recommendation is generated from the highest-confidence claim.

```
POST /sessions/{session_id}/steps/synthesize_findings
```

**Request body:** `{}` (no parameters required)

**Response:**

```json
{
  "step_id": "step_...",
  "step_type": "synthesize_findings",
  "status": "completed",
  "result": {
    "claims_created": 4,
    "recommendations_created": 2,
    "claims": [
      {
        "claim_id": "claim_...",
        "claim_type": "metric_regression",
        "text": "Average watch time declined 14.2% on iOS mobile in January 2024",
        "confidence": 0.87,
        "status": "confirmed",
        "scope": {"platform": "mobile", "device_type": "iOS"},
        "inference_level": "L0",
        "inference_justification": []
      }
    ],
    "recommendations": [
      {
        "rec_id": "rec_...",
        "action_text": "Investigate buffering events on iOS 17.x builds released Dec 2023",
        "priority": "P0",
        "expected_impact": "Recover ~6 minutes of average watch time",
        "risk": "low"
      }
    ]
  }
}
```

---

## Get Evidence Graph

```
GET /sessions/{session_id}/evidence
```

Returns the full evidence graph for a session: all steps, artifacts, observations, claims, evidence edges, and recommendations.

### Response

```json
{
  "session_id": "sess_...",
  "goal": "Investigate watch time drop...",
  "steps": [
    {
      "step_id": "step_...",
      "step_type": "compare_metric",
      "status": "completed",
      "summary": "...",
      "provenance": {...},
      "created_at": "..."
    }
  ],
  "artifacts": [...],
  "observations": [
    {
      "observation_id": "obs_...",
      "observation_type": "metric_comparison",
      "subject": {"metric": "avg_watch_time_minutes", "slice": {"device_type": "iOS"}},
      "payload": {
        "baseline_value": 42.3,
        "current_value": 36.3,
        "relative_change_pct": -14.2,
        "direction": "down"
      },
      "significance": {"score": 0.92},
      "quality": {"completeness": 1.0},
      "observed_window": {"start": "2024-01-01", "end": "2024-01-31"},
      "temporal_order": 1
    }
  ],
  "claims": [
    {
      "claim_id": "claim_...",
      "claim_type": "root_cause_candidate",
      "text": "Metric decline is concentrated in iOS / mobile traffic...",
      "scope": {"slice": {"platform": "iOS", "device_type": "mobile"}},
      "confidence": 0.87,
      "status": "confirmed",
      "supporting_observations": ["obs_..."],
      "contradicting_observations": [],
      "confidence_breakdown": {
        "effect_strength": 0.71,
        "consistency": 0.95,
        "sample_score": 0.80,
        "data_quality_score": 0.95,
        "contradiction_penalty": 0.0
      },
      "inference_level": "L1",
      "inference_justification": ["cross_slice_consistency:6/8_slices_down→L1"]
    }
  ],
  "evidence_edges": [
    {
      "edge_id": "edge_...",
      "from_node_id": "obs_...",
      "from_node_type": "observation",
      "to_node_id": "claim_...",
      "to_node_type": "claim",
      "edge_type": "supports",
      "weight": 0.87,
      "explanation": "Direct metric measurement supports the regression claim"
    }
  ],
  "recommendations": [...]
}
```

**Evidence edge types:**

Base layer:

| Type | Description |
|------|-------------|
| `supports` | Observation/claim supports a claim |
| `contradicts` | Observation/claim contradicts a claim |
| `justifies` | Observation justifies a recommendation |

Causal layer (assigned by causal checkers when inference level is upgraded):

| Type | Inference level | Description |
|------|----------------|-------------|
| `correlates_with` | L0/L1 | Statistical association between two observations |
| `temporally_precedes` | L1/L2 | Cause event observed before effect event |
| `mechanistically_explains` | L2/L3 | Plausible causal mechanism described |
| `eliminates_alternative` | L3/L4 | Alternative explanation ruled out |
| `experimentally_confirms` | L4/L5 | A/B test or natural experiment confirms the claim |

**Observation fields:**

| Field | Type | Description |
|-------|------|-------------|
| `observed_window` | object or null | `{start, end, granularity}` ISO dates/datetimes for the time window observed. Populated for `compare_metric`; inferred per-row for `aggregate_query` when a recognized temporal column (e.g. `date`, `event_date`, `log_date`, `hour`, `hour_slot`) appears in `group_by` (G-2). Null for `profile_table`, `sample_rows`, and aggregations with no temporal group-by column. |
| `temporal_order` | integer | Sequential position of this observation within the session (1-based). Used for temporal ordering in the evidence graph. |

**Claim `status` values:**

| Status | Description |
|--------|-------------|
| `tentative` | Created by incremental synthesis after a primitive step; awaiting promotion |
| `confirmed` | Promoted by `synthesize_findings` — confidence ≥ 0.5 AND no contradictions |
| `insufficient` | Promoted by `synthesize_findings` — confidence < 0.5 OR contradictions present |

**Claim `inference_level` values:**

| Level | Meaning | Set by |
|-------|---------|--------|
| `L0` | Correlation / association only | Default (all new claims) |
| `L1` | Temporal precedence established (cross-slice consistency) | `CrossSliceConsistencyChecker` |
| `L2` | Mechanism identified — temporal precedence confirmed | `TemporalPrecedenceChecker` |
| `L3`–`L5` | Counterfactual / experimental evidence | Reserved (not yet implemented) |

`inference_justification` is a list of provenance tokens encoding how the level was achieved (e.g. `"cross_slice_consistency:6/8_slices_down→L1"`). It is always `[]` for `L0`.

**Recommendation `causal_basis` field:**

```json
{
  "rec_id": "rec_...",
  "action_text": "...",
  "priority": "P0",
  "risk": "low",
  "causal_basis": {
    "inference_level": "L1",
    "strongest_evidence_summary": "6/8 dimension slices show consistent decline (cross-slice consistency)",
    "unresolved_confounders": ["seasonal effects", "concurrent feature rollout"],
    "suggested_validation": "Run A/B test isolating the iOS build version variable"
  }
}
```

`causal_basis` is `null` for recommendations without associated claims or for rows created before M-10.

---

## Get Reflection Context

```
GET /sessions/{session_id}/reflection-context?plan_id={plan_id}
```

Returns a compact, token-efficient evidence-gap summary designed for agent consumption. The response is deterministically computed from the session's evidence state — no LLM is involved.

**Design principle:** Factum produces structured facts (gaps, readiness, confounders). The agent decides what steps to run next.

Requires `reflection.enabled: true` in `factum.yaml` (default: `true`).

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `plan_id` | string | optional | If provided, plan context is included in the response |

### Response

```json
{
  "session_id": "sess_...",
  "plan_id": null,
  "readiness_signal": {
    "goal_coverage": 0.4,
    "evidence_sufficiency": 0.33,
    "contradiction_resolution": 1.0,
    "budget_remaining": 0.73,
    "diminishing_returns": 0.33,
    "suggested_action": "continue_exploring"
  },
  "readiness_score": 0.56,
  "tentative_claims": [
    {
      "claim_id": "claim_...",
      "text": "Average watch time declined 14.2% on iOS mobile",
      "scope": {"device_type": "iOS"},
      "confidence": 0.72,
      "inference_level": "L0",
      "unresolved_confounders": ["concurrent feature rollout", "seasonal effects"]
    }
  ],
  "evidence_gaps": [
    {
      "gap_key": "missing_observed_window",
      "text": "populate `observed_window` (use `observed_window_column` param in `aggregate_query`) to enable temporal precedence checking",
      "suggested_validation": "Run `aggregate_query` with `observed_window_column` set to a time column to enable temporal ordering.",
      "affected_claims": ["claim_..."]
    },
    {
      "gap_key": "missing_temporal_ordering",
      "text": "run `aggregate_query` grouped by a time column with `observed_window_column` to establish temporal ordering for `elapsed_time`; optionally run `correlate_metrics` to test cross-series association",
      "suggested_validation": "Run `aggregate_query` grouped by a time column with `observed_window_column` to establish temporal ordering for `elapsed_time`.",
      "affected_claims": ["claim_...", "claim_..."]
    }
  ],
  "available_step_types": ["compare_metric", "profile_table", "sample_rows", "aggregate_query", "correlate_metrics", "synthesize_findings"]
}
```

| Field | Description |
|-------|-------------|
| `readiness_signal` | Full 5-dimensional readiness signal (same as in step responses) |
| `readiness_score` | Scalar average of the 5 dimensions |
| `tentative_claims` | Claims with `inference_level` < L2 that still need supporting evidence; `unresolved_confounders` is a list of scope-aware strings |
| `evidence_gaps` | **Session-level deduplicated** gaps derived from persisted recommendations. Dedup key is `(gap_key, text)` — the same `gap_key` can appear more than once if the text differs (e.g. `missing_temporal_ordering` for different metrics). Each entry has `gap_key` (stable identifier), `text` (human-readable), `suggested_validation` (concrete next step), and `affected_claims` (claim IDs that contribute the gap). |
| `available_step_types` | Step types available to the agent for next steps |
