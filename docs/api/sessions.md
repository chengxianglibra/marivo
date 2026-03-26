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
| `GET` | `/sessions/{session_id}/debug` | Get request-time evidence-engine introspection for the current graph |
| `GET` | `/sessions/{session_id}/reflection-context` | Get structured evidence-gap summary for agents |

---

## Create Session

```
POST /sessions
```

Creates a new analysis session with a goal, constraints, optional `raw_filter`, budget, and policy.

### Request Body

```json
{
  "goal": "Investigate watch time drop among mobile users in Q1 2024",
  "constraints": {
    "platform": "mobile",
    "region": "US"
  },
  "raw_filter": "cluster IN ('k8sbi-bi1', 'k8sbi-bi2')",
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
| `constraints` | object | no | Scalar key-value filters injected as `col = value` predicates into supported steps (default: `{}`) |
| `raw_filter` | string | no | Raw SQL predicate appended to session constraints. Use this for `IN`, `BETWEEN`, `IS NOT NULL`, and compound filters. |
| `budget` | object | no | Execution limits (default: `{"max_scan_bytes": 500000000000, "max_latency_sec": 120}`) |
| `policy` | object | no | Data governance policy (default: `{"aggregate_only": true, "min_group_size": 100}`) |

**Scoping guidance:**

| Need | Mechanism |
|------|-----------|
| Stable scalar scope | `constraints` |
| Complex row predicate | `raw_filter` |
| Time window | Step params |

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
  "raw_filter": "cluster IN ('k8sbi-bi1', 'k8sbi-bi2')",
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

**Valid `step_type` values:** `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, `correlate_metrics`, `attribute_change`, `synthesize_findings`

Session `constraints` / `raw_filter` are automatically merged into supported query steps, including `compare_metric`, `sample_rows`, `aggregate_query`, and `attribute_change`.

---

### compare_metric

Evaluate a published semantic metric over typed time windows. Requires that the metric is published in the semantic layer and has a corresponding mapping to a source object.

`compare_metric` supports two execution semantics under the same typed contract:

- `compare`: compare a current window against a baseline window and emit comparison-shaped metric observations
- `single_window`: emit current-window metric observations without fabricating baseline or delta fields; this aligns with the current-window observation semantics of `aggregate_query(single_window)`

`compare_metric` is a typed time-window primitive. Time windows are expressed only through `time_scope`; non-time row/entity scoping is expressed only through `scope`. Legacy fields such as `metric_name`, `table_name`, `period_start`, `period_end`, `baseline_start`, `baseline_end`, `comparison_type`, `date_column`, `where`, and `filter` are no longer supported.

| Scoping need | Mechanism |
|---|---|
| Scalar entity scope (e.g. `cluster = 'k8sbi-bi1'`) | Step `scope.constraints` |
| Complex non-time row predicate (e.g. `state = 'SUCCEED'`) | Step `scope.predicate` |
| Time window | Step `time_scope` |

```
POST /sessions/{session_id}/steps/compare_metric
```

**Request body:**

```json
{
  "table": "events.user_video_watch",
  "metric": "avg_watch_time_minutes",
  "dimensions": ["device_type"],
  "time_scope": {
    "mode": "compare",
    "grain": "day",
    "current": {
      "start": "2024-01-24",
      "end": "2024-01-31"
    },
    "baseline": {
      "start": "2024-01-17",
      "end": "2024-01-24"
    }
  },
  "scope": {
    "constraints": {
      "region": "us"
    },
    "predicate": "watch_duration_sec > 30"
  },
  "order": "delta_pct DESC",
  "limit": 20
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table` | string | yes | Physical table that backs the metric |
| `metric` | string | yes | Name of a published semantic metric |
| `dimensions` | array[string] | no | Dimensions to group by |
| `time_scope` | object | yes | Typed time contract with `mode`, `grain`, `current`, and optional `baseline` |
| `scope` | object | no | Non-time scope with `constraints` and optional non-time `predicate` |
| `time_axis` | object | no | Advanced override for analysis-time and partition-pruning columns |
| `order` | string | no | Output ordering expression; valid fields are mode-specific |
| `limit` | integer | no | Maximum rows to return (default: `10`) |

`time_scope` rules:

- request validation accepts `single_window` or `compare`
- `grain` must be `day` or `hour`
- `baseline` is required only when `mode = compare`
- all windows are interpreted as half-open intervals `[start, end)`
- hour-grain boundaries must be naive datetimes without timezone offsets; phase 1 assumes session-consistent naive timestamps only

Mode-specific request notes:

- `compare` mode uses comparison semantics; `baseline` is required and `order` may target comparison fields such as `delta_pct DESC`
- `single_window` mode uses current-window observation semantics; `baseline` must be omitted, callers must not assume comparison-only response fields, and `order` may target `current_value` or `current_sessions`

**Response (`compare` mode example):**

```json
{
  "step_type": "compare_metric",
  "metric_name": "avg_watch_time_minutes",
  "summary": "Metric 'avg_watch_time_minutes' comparison: top decline is -14.2% for device_type=iOS (current_value=36.3, baseline_value=42.3).",
  "artifact_id": "art_...",
  "observations": [
    {
      "observation_id": "obs_...",
      "type": "metric_change",
      "subject": {
        "metric": "avg_watch_time_minutes",
        "slice": {"device_type": "iOS"}
      },
      "payload": {
        "current_value": 36.3,
        "baseline_value": 42.3,
        "delta_pct": -14.2,
        "current_sessions": 180,
        "baseline_sessions": 175
      },
      "observed_window": {
        "start": "2024-01-24",
        "end": "2024-01-31",
        "granularity": "day"
      }
    }
  ]
}
```

**Response (`single_window` mode example):**

```json
{
  "step_type": "compare_metric",
  "metric_name": "avg_watch_time_minutes",
  "summary": "Metric 'avg_watch_time_minutes' current window observation: highest value is 41.2 for device_type=iOS (current_sessions=180).",
  "artifact_id": "art_...",
  "observations": [
    {
      "observation_id": "obs_...",
      "type": "metric_change",
      "subject": {
        "metric": "avg_watch_time_minutes",
        "slice": {"device_type": "iOS"}
      },
      "payload": {
        "current_value": 41.2,
        "current_sessions": 180
      },
      "observed_window": {
        "start": "2024-01-24",
        "end": "2024-01-31",
        "granularity": "day"
      }
    }
  ]
}
```

The response example still shows the current response field name `metric_name`. The
typed contract change applies to the request payload: callers must send `metric`, not
legacy request fields.

`compare_metric` observations inherit `time_scope.current` as `observed_window`. The
baseline window remains in the comparison/debug context and is not emitted as a second
observation window.

Mode-specific payload fields:

- Present in both modes: `current_value`, `current_sessions`
- Present only in `compare` mode: `baseline_value`, `baseline_sessions`, `delta_pct`
- `single_window` is a current-window observation contract; callers must not rely on null-filled comparison fields

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

`aggregate_query` now uses the same typed `time_scope` / `scope` / `time_axis` contract as `compare_metric`. Legacy fields `table_name`, `select`, `where`, `order_by`, `compare_period`, and `date_column` are no longer part of the public request contract.

**G-2 enhancement:** When a temporal column (e.g., `log_date`, `event_date`, `dt`) is present in `group_by`, observations automatically receive `observed_window` inferred from the slice key. This enables `TemporalPrecedenceChecker` to recognize time-ordered evidence and promote claims from L1 to L2.

```
POST /sessions/{session_id}/steps/aggregate_query
```

**Request body:**

```json
{
  "table": "events.user_video_watch",
  "group_by": ["device_type", "region"],
  "measures": [
    {"expr": "AVG(watch_duration_sec)", "as": "avg_watch_sec"},
    {"expr": "COUNT(*)", "as": "cnt"}
  ],
  "time_scope": {
    "mode": "compare",
    "grain": "day",
    "current": {
      "start": "2024-01-24",
      "end": "2024-01-31"
    },
    "baseline": {
      "start": "2024-01-17",
      "end": "2024-01-24"
    }
  },
  "scope": {
    "constraints": {
      "region": "us"
    },
    "predicate": "watch_duration_sec > 30"
  },
  "order": "cnt_delta_pct DESC",
  "limit": 50
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table` | string | yes | Table to query |
| `measures` | array[object] | yes | Aggregate expressions, each with required `expr` and explicit `as` alias |
| `group_by` | array[string] | no | Columns to group by |
| `time_scope` | object | yes | Typed time contract with `mode`, `grain`, `current`, and optional `baseline` |
| `scope` | object | no | Non-time scope with `constraints` and optional non-time `predicate` |
| `time_axis` | object | no | Advanced override for analysis-time and partition-pruning columns. Resolution prefers entity `properties.time_capabilities`, then source-object `properties.time_capabilities`, then heuristics |
| `order` | string | no | Output ordering expression (e.g. `cnt_delta_pct DESC`) |
| `limit` | integer | no | Maximum rows (default: `100`) |

Phase-1 time-axis note: Factum currently assumes session-consistent naive timestamps. If your table needs explicit timezone semantics, keep that conversion outside the typed step contract for now.

**Observation window behavior (G-2):**

`aggregate_query` observations always inherit the request-level `time_scope` window.
When `group_by` includes a recognized temporal column, the extractor refines that to
per-row `observed_window` buckets instead of the coarser request window.

Recognized temporal columns in `group_by`:

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
    ],
    "evidence_gaps": [
      {
        "gap_key": "watch_time|platform=android",
        "text": "Claim 'watch_time on android' remains tentative — insufficient corroborating observations.",
        "suggested_validation": "Run compare_metric for watch_time scoped to android; add aggregate_query to cross-check session counts.",
        "affected_claims": ["claim_..."]
      }
    ]
  }
}
```

`evidence_gaps` is a session-level summary of claims that could not be confirmed. Each entry:

| Field | Description |
|-------|-------------|
| `gap_key` | Deduplication key: `metric\|dimension_slice` |
| `text` | Human-readable description of the gap |
| `suggested_validation` | Recommended next step to resolve the gap |
| `affected_claims` | Claim IDs that remain unconfirmed due to this gap |

Agents should treat `evidence_gaps` as the primary signal for deciding whether to run additional steps before finalizing the investigation. The same field is available in `GET /sessions/{id}/reflection-context` for stateless polling.

---

## Get Evidence Graph

```
GET /sessions/{session_id}/evidence
```

Returns the persisted evidence graph for a session: steps, observations, claims, edges, and recommendations.

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `claims_only` | string | Optional. Currently supports only `confirmed`. Filters claims first, then trims claim-linked edges and recommendations to avoid dangling references. Observations remain as full context. |
| `edge_types` | string[] | Optional repeated query param. Keeps only the selected edge types. Invalid values return `422`. |
| `include_debug` | boolean | Optional. When `true`, attaches the same request-time introspection payload returned by `/sessions/{session_id}/debug`. |

### Response

```json
{
  "session_id": "sess_...",
  "steps": [
    {
      "step_id": "step_...",
      "step_type": "compare_metric",
      "status": "completed",
      "summary": "...",
      "provenance": {...}
    }
  ],
  "observations": [
    {
      "observation_id": "obs_...",
      "type": "metric_change",
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
  "edges": [
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
  "recommendations": [
    {
      "rec_id": "rec_...",
      "type": "action",
      "claim_id": "claim_queued_time",
      "supporting_claims": ["claim_query_count", "claim_queued_time"],
      "template_id": "multi_claim_correlated_action_v1",
      "action_text": "...",
      "priority": "P1",
      "expected_impact": "...",
      "risk": "...",
      "validation_metric": {
        "primary_metric": "query_count",
        "correlated_metrics": ["queued_time"]
      },
      "causal_basis": {
        "inference_level": "L2",
        "strongest_evidence_summary": "...",
        "unresolved_confounders": [{"key": "seasonality", "text": "check whether seasonal effects explain the change"}],
        "resolved_confounders": [{"key": "normalise_workload_volume", "resolved_by": "claim_query_count", "summary": "query_count increased for the same slice"}],
        "suggested_validation": "Run a follow-up aggregate_query grouped by hour.",
        "causal_chain": "query_count changed -> queued_time changed",
        "causal_path_claim_ids": ["claim_query_count", "claim_queued_time"]
      },
      "action": "..."
    }
  ],
  "debug": {
    "session_id": "sess_...",
    "relation_discovery": {"relations_emitted": 1},
    "checker_logs": [...]
  }
}
```

**Evidence edge types:**

Base layer:

| Type | Description |
|------|-------------|
| `supports` | Observation/claim supports a claim |
| `contradicts` | Observation/claim contradicts a claim |
| `justifies` | Observation/claim justifies a recommendation |

Causal layer (assigned by causal checkers when inference level is upgraded):

| Type | Inference level | Description |
|------|----------------|-------------|
| `correlates_with` | L0/L1 | Claim-to-claim scope or metric relation discovered during synthesis |
| `temporally_precedes` | L1/L2 | Claim-to-claim directional edge backed by real observation windows or hourly lead-lag patterns |
| `mechanistically_explains` | L2/L3 | Claim-to-claim explanation grounded in contribution evidence |
| `eliminates_alternative` | L3/L4 | Claim-to-claim alternative elimination |
| `experimentally_confirms` | L4/L5 | Claim-to-claim experimental confirmation |

Claim-to-claim relation edges may also carry `match_basis`, `score_components`, and `supporting_observation_ids`.

**Observation fields:**

| Field | Type | Description |
|-------|------|-------------|
| `observed_window` | object or null | `{start, end, granularity}` ISO dates/datetimes for the time window observed. Populated for `compare_metric`; inferred per-row for `aggregate_query` when a recognized temporal column (e.g. `date`, `event_date`, `log_date`, `hour`, `hour_slot`) appears in `group_by` (G-2). Null for `profile_table`, `sample_rows`, and aggregations with no temporal group-by column. |
| `temporal_order` | integer | Sequential position of this observation within the session (1-based). Used for temporal ordering in the evidence graph. |

Derived observations may also appear after `synthesize_findings`: `cross_metric_correlation` and `temporal_pattern`.

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
| `L1` | Cross-slice consistency or cross-scope / cross-metric correlation established | `CrossSliceConsistencyChecker`, `CrossScopeCorrelationChecker`, `CrossMetricCorrelationChecker` |
| `L2` | Temporal precedence established | `TemporalPrecedenceChecker` |
| `L3` | Mechanistic explanation identified | `MechanisticExplanationChecker` |
| `L4` | Alternative explanations / confounders substantially eliminated | Future / reserved |
| `L5` | Experimental confirmation | Future / reserved |

`inference_justification` is a list of provenance tokens encoding how the level was achieved (e.g. `"cross_slice_consistency:6/8_slices_down→L1"`). It is always `[]` for `L0`.

> **Inference-level promotion**: for a detailed explanation of how claims move from L0 through L2 — including exact checker conditions, required step patterns, and a worked example — see [Causal Inference Guide](../service/causal-inference.md).

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
    "unresolved_confounders": [
      {"key": "seasonal_effects", "text": "check whether seasonality explains the shift"}
    ],
    "resolved_confounders": [
      {"key": "normalise_workload_volume", "resolved_by": "claim_query_count", "summary": "query_count increased for the same slice"}
    ],
    "suggested_validation": "Run A/B test isolating the iOS build version variable",
    "causal_chain": "query_count +30.0% -> queued_time +58.5%",
    "causal_path_claim_ids": ["claim_query_count", "claim_queued_time"]
  }
}
```

`causal_basis` is `null` for recommendations without associated claims or for rows created before M-10.

`causal_chain` is conservative by design:
- it is selected only from the recommendation-local claim subgraph (`supporting_claims` or the primary `claim_id`)
- it may use `correlates_with` relations as connectors, but it is emitted only when the selected path contains at least one directional claim-to-claim causal edge such as `temporally_precedes` or `mechanistically_explains`
- it is a deterministic compression of existing claim graph structure, not a new causal inference pass

`template_id` is a stable recommendation-template identifier for debugging and UI use. `supporting_claims` is populated for aggregated recommendations and omitted for single-claim actions.

---

## Get Session Debug

```
GET /sessions/{session_id}/debug
```

Returns request-time introspection for the current persisted evidence graph. This endpoint does not read a historical snapshot; its explanation payload may change as causal-checker and relation-discovery implementations evolve.

### Response

```json
{
  "session_id": "sess_...",
  "relation_discovery": {
    "relations_emitted": 2
  },
  "checker_logs": [
    {
      "checker": "temporal_precedence",
      "upgrades": 1
    }
  ]
}
```

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
      "unresolved_confounders": [
        {"key": "concurrent_rollout", "text": "check whether a concurrent rollout explains the change"},
        {"key": "seasonal_effects", "text": "check whether seasonality explains the shift"}
      ]
    }
  ],
  "evidence_gaps": [
    {
      "gap_key": "missing_observed_window",
      "text": "populate `observed_window` by running `aggregate_query` with a typed `time_scope` to enable temporal precedence checking",
      "suggested_validation": "Run `aggregate_query` with a typed `time_scope` to enable temporal ordering.",
      "affected_claims": ["claim_..."]
    },
    {
      "gap_key": "missing_temporal_ordering",
      "text": "run `aggregate_query` with a typed `time_scope`, grouped by a time column, to establish temporal ordering for `elapsed_time`; optionally run `correlate_metrics` to test cross-series association",
      "suggested_validation": "Run `aggregate_query` with a typed `time_scope`, grouped by a time column, to establish temporal ordering for `elapsed_time`.",
      "affected_claims": ["claim_...", "claim_..."]
    }
  ],
  "available_step_types": ["compare_metric", "profile_table", "sample_rows", "aggregate_query", "correlate_metrics", "attribute_change", "synthesize_findings"]
}
```

| Field | Description |
|-------|-------------|
| `readiness_signal` | Full 5-dimensional readiness signal (same as in step responses) |
| `readiness_score` | Scalar average of the 5 dimensions |
| `tentative_claims` | Claims with `inference_level` < L2 that still need supporting evidence; `unresolved_confounders` is a list of scope-aware gap objects |
| `evidence_gaps` | **Session-level deduplicated** gaps derived from persisted recommendations. Dedup key is `(gap_key, text)` — the same `gap_key` can appear more than once if the text differs (e.g. `missing_temporal_ordering` for different metrics). Each entry has `gap_key` (stable identifier), `text` (human-readable), `suggested_validation` (concrete next step), and `affected_claims` (claim IDs that contribute the gap). |
| `available_step_types` | Step types available to the agent for next steps |
