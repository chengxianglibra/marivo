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

**Valid `step_type` values:** `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, `synthesize_findings`

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
  }
}
```

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

```
POST /sessions/{session_id}/steps/aggregate_query
```

**Request body:**

```json
{
  "table_name": "events.user_video_watch",
  "group_by": ["device_type", "region"],
  "measures": [
    {"expr": "AVG(watch_duration_sec)", "alias": "avg_watch_sec"},
    {"expr": "COUNT(*)", "alias": "session_count"}
  ],
  "filter": "event_date >= '2024-01-01'",
  "extract_observations": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table_name` | string | yes | Table to query |
| `group_by` | array[string] | yes | Columns to group by |
| `measures` | array[object] | yes | Aggregation expressions with aliases |
| `filter` | string | no | SQL filter expression (ANDed with session constraints) |
| `extract_observations` | boolean | no | Extract observations from result rows (default: `true`). Set to `false` to skip. |

**`measures` object:**

| Field | Type | Description |
|-------|------|-------------|
| `expr` | string | SQL aggregate expression (e.g., `AVG(col)`, `COUNT(*)`) |
| `alias` | string | Output column alias |

---

### synthesize_findings

Composite step that synthesizes all observations in the session into claims and recommendations. This step reads from the evidence graph accumulated by prior steps — it does not require additional parameters.

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
      "quality": {"completeness": 1.0}
    }
  ],
  "claims": [
    {
      "claim_id": "claim_...",
      "claim_type": "root_cause_candidate",
      "text": "Metric decline is concentrated in iOS / mobile traffic...",
      "scope": {"slice": {"platform": "iOS", "device_type": "mobile"}},
      "confidence": 0.87,
      "status": "supported",
      "supporting_observations": ["obs_..."],
      "contradicting_observations": [],
      "confidence_breakdown": {
        "effect_strength": 0.71,
        "consistency": 0.95,
        "sample_score": 0.80,
        "data_quality_score": 0.95,
        "contradiction_penalty": 0.0
      },
      "inference_level": "L0",
      "inference_justification": []
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

| Type | Description |
|------|-------------|
| `supports` | Observation/claim supports a claim |
| `contradicts` | Observation/claim contradicts a claim |
| `justifies` | Observation justifies a recommendation |

**Claim `inference_level` values:**

| Level | Meaning |
|-------|---------|
| `L0` | Correlation / association only. No causal claim is made. *(all Phase 1 claims)* |
| `L1` | Temporal precedence established (cause precedes effect in time). *(Phase 2)* |
| `L2` | Mechanism identified — a plausible causal pathway is described. *(Phase 2)* |
| `L3` | Counterfactual / experimental evidence (A/B test, natural experiment). *(Phase 2)* |

`inference_justification` is a list of provenance tokens that establish the stated level. It is always `[]` for `L0`.
