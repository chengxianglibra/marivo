# Plans

Plans allow you to define a sequence of analysis steps as a structured workflow, validate them, estimate costs, and execute them in dependency order. Plans follow the lifecycle: `draft` → `validated` → `approved` → `executing` → `completed` / `failed`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{session_id}/plans` | Draft a new plan |
| `GET` | `/sessions/{session_id}/plans` | List plans for a session |
| `GET` | `/sessions/{session_id}/plans/{plan_id}` | Get a plan |
| `PATCH` | `/sessions/{session_id}/plans/{plan_id}` | Replace all steps of a draft plan |
| `POST` | `/sessions/{session_id}/plans/{plan_id}/patch` | Incrementally patch a plan (add/modify/skip steps) |
| `POST` | `/sessions/{session_id}/plans/{plan_id}/validate` | Validate a plan |
| `POST` | `/sessions/{session_id}/plans/{plan_id}/approve` | Approve a validated plan |
| `POST` | `/sessions/{session_id}/plans/{plan_id}/execute` | Execute an approved plan |
| `GET` | `/sessions/{session_id}/plans/{plan_id}/explain` | Explain a plan |
| `POST` | `/sessions/{session_id}/plans/{plan_id}/estimate-costs` | Estimate execution costs |
| `GET` | `/sessions/{session_id}/plans/{plan_id}/budget-check` | Check against session budget |

---

## Plan Step Object

Each step in a plan is a JSON object describing a typed step to execute:

```json
{
  "step_id": "s1",
  "step_type": "compare_metric",
  "params": {
    "metric_name": "avg_watch_time_minutes",
    "period_start": "2024-01-01",
    "period_end": "2024-01-31"
  },
  "depends_on": [],
  "description": "Measure watch time change in January"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step_id` | string | yes | Local identifier within the plan (unique, used for dependency references) |
| `step_type` | string | yes | One of: `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, `correlate_metrics`, `synthesize_findings` |
| `params` | object | no | Step parameters (same as direct step execution; see [Sessions & Steps](sessions.md)) |
| `depends_on` | array[string] | no | `step_id` values that must complete before this step runs |
| `description` | string | no | Human-readable description of the step's purpose |

---

## Draft Plan

```
POST /sessions/{session_id}/plans
```

Creates a plan in `draft` status. Plans are not validated or executed until explicitly requested.

### Request Body

```json
{
  "steps": [
    {
      "step_id": "s1",
      "step_type": "profile_table",
      "params": {"table_name": "events.user_video_watch"},
      "depends_on": [],
      "description": "Baseline table profile"
    },
    {
      "step_id": "s2",
      "step_type": "compare_metric",
      "params": {
        "metric_name": "avg_watch_time_minutes",
        "period_start": "2024-01-01",
        "period_end": "2024-01-31"
      },
      "depends_on": ["s1"],
      "description": "Watch time comparison"
    },
    {
      "step_id": "s3",
      "step_type": "synthesize_findings",
      "params": {},
      "depends_on": ["s2"],
      "description": "Synthesize all evidence"
    }
  ]
}
```

### Response

```json
{
  "plan_id": "plan_abc123...",
  "session_id": "sess_...",
  "status": "draft",
  "steps": [...],
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

---

## List Plans

```
GET /sessions/{session_id}/plans
```

Returns all plans for a session.

### Response

Array of plan objects.

---

## Get Plan

```
GET /sessions/{session_id}/plans/{plan_id}
```

Returns a single plan with full step detail.

---

## Update Plan

```
PATCH /sessions/{session_id}/plans/{plan_id}
```

Updates the steps of a plan in `draft` status. Cannot update validated or approved plans.

### Request Body

```json
{
  "steps": [...]
}
```

---

## Validate Plan

```
POST /sessions/{session_id}/plans/{plan_id}/validate
```

Validates a draft plan. Validation checks:

1. **Step type validity** — all `step_type` values must be recognized
2. **Dependency acyclicity** — `depends_on` references must not form cycles
3. **Required params** — required parameters must be present for each step type
4. **Semantic resolution** — `compare_metric` metrics must be published; requested `dimensions` must be supported by the metric
5. **Contract constraints** — forbidden param combinations are rejected (e.g. `compare_metric` with `filter` or `where`)
6. **Governance** — steps are checked against active policies

**Validation issue codes** (non-exhaustive):

| Code | Category | Description |
|------|----------|-------------|
| `missing_required_param` | params | A required param is absent for the step type |
| `compare_metric_filter_not_allowed` | params | `compare_metric` received a step-level `filter` or `where` param — use session `raw_filter`/`constraints` instead |
| `semantic_metric_not_found` | semantic | Metric is not published or does not exist |
| `semantic_dimension_not_supported` | semantic | Requested dimension is not in the metric's dimension list |
| `correlate_metrics_missing_left` | semantic | `correlate_metrics` is missing `left_artifact_id` or `left_step_id` |
| `correlate_metrics_missing_right` | semantic | `correlate_metrics` is missing `right_artifact_id` or `right_step_id` |
| `aggregate_only_forbids_sample_rows` | governance | Active policy forbids `sample_rows` |
| `budget_rows_exceeded` | budget | Estimated row scan exceeds session budget |
| `routing_table_unresolved` | routing | Table cannot be resolved to a configured engine (warning, non-blocking) |

If all checks pass, the plan transitions to `validated` and is auto-approved (unless blocked by governance or budget issues). If governance or budget blocks exist, the plan remains `validated` and requires explicit approval.

### Response

```json
{
  "plan_id": "plan_...",
  "status": "validated",
  "validation": {
    "passed": true,
    "checks": [
      {"check": "step_types", "passed": true},
      {"check": "dependency_graph", "passed": true},
      {"check": "required_params", "passed": true},
      {"check": "governance", "passed": true, "warnings": []}
    ]
  },
  "auto_approved": true
}
```

When `auto_approved` is `true`, the plan has already transitioned to `approved` status.

---

## Approve Plan

```
POST /sessions/{session_id}/plans/{plan_id}/approve
```

Manually approves a `validated` plan. Required when auto-approval is blocked by governance warnings or budget concerns.

### Response

Plan object with `status: "approved"`.

---

## Execute Plan

```
POST /sessions/{session_id}/plans/{plan_id}/execute
```

Executes an `approved` plan. Steps are executed in topological order (respecting `depends_on`). Independent steps may run concurrently.

### Request Body

```json
{
  "continue_on_failure": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `continue_on_failure` | boolean | If `true`, execution continues past failing steps. If `false` (default), the plan fails on the first step failure. |

### Response

```json
{
  "plan_id": "plan_...",
  "status": "completed",
  "steps": [
    {
      "step_id": "s1",
      "status": "completed",
      "step_record_id": "step_...",
      "started_at": "2024-01-15T10:01:00+00:00",
      "completed_at": "2024-01-15T10:01:04+00:00"
    },
    {
      "step_id": "s2",
      "status": "completed",
      "step_record_id": "step_...",
      "started_at": "2024-01-15T10:01:04+00:00",
      "completed_at": "2024-01-15T10:01:09+00:00"
    }
  ],
  "completed_at": "2024-01-15T10:01:12+00:00"
}
```

**Step execution status values:** `pending`, `running`, `completed`, `failed`, `skipped`

`skipped` occurs when `continue_on_failure` is `true` and a dependency failed.

---

## Explain Plan

```
GET /sessions/{session_id}/plans/{plan_id}/explain
```

Returns a human-readable explanation of what the plan will do, the execution order, and step dependencies.

### Response

```json
{
  "plan_id": "plan_...",
  "execution_order": ["s1", "s2", "s3"],
  "explanation": "This plan will first profile the table to establish baseline counts, then compare the watch time metric over January, and finally synthesize all observations into claims and recommendations.",
  "dependency_graph": {
    "s1": [],
    "s2": ["s1"],
    "s3": ["s2"]
  }
}
```

---

## Estimate Costs

```
POST /sessions/{session_id}/plans/{plan_id}/estimate-costs
```

Estimates the execution cost for each step in the plan based on table row counts as a scan proxy.

### Response

```json
{
  "plan_id": "plan_...",
  "total_estimated_scan_bytes": 4200000000,
  "within_budget": true,
  "steps": [
    {
      "step_id": "s1",
      "step_type": "profile_table",
      "estimated_scan_bytes": 2000000000,
      "estimated_latency_sec": 3.2
    },
    {
      "step_id": "s2",
      "step_type": "compare_metric",
      "estimated_scan_bytes": 2200000000,
      "estimated_latency_sec": 4.1
    }
  ]
}
```

---

## Budget Check

```
GET /sessions/{session_id}/plans/{plan_id}/budget-check
```

Checks whether the plan's estimated costs fit within the session budget.

### Response

```json
{
  "plan_id": "plan_...",
  "session_budget": {
    "max_scan_bytes": 500000000000,
    "max_latency_sec": 120
  },
  "estimated_scan_bytes": 4200000000,
  "estimated_latency_sec": 7.3,
  "within_budget": true,
  "violations": []
}
```

When budget is exceeded, `violations` lists the specific breaches:

```json
{
  "within_budget": false,
  "violations": [
    {
      "step_id": "s2",
      "field": "max_scan_bytes",
      "estimated": 600000000000,
      "limit": 500000000000
    }
  ]
}
```

---

## Patch Plan

```
POST /sessions/{session_id}/plans/{plan_id}/patch
```

Incrementally patches a plan. Intended for agent workflows where the agent wants to add, modify, or skip steps based on new evidence (e.g., after reading a `reflection-context` response).

The patch workflow:
1. Plan is reset to `draft`
2. Patch operations are applied
3. Plan is re-validated (same checks as `POST .../validate`)
4. Auto-approval applies if all checks pass; otherwise explicit approval is required

Illegal patches (unknown step type, cyclic dependency, invalid params) return `400` without modifying the plan.

### Request Body

```json
{
  "add_steps": [
    {
      "step_type": "aggregate_query",
      "params": {
        "table_name": "events.user_video_watch",
        "select": ["platform", "COUNT(*) as cnt"],
        "group_by": ["platform"],
        "where": "event_date = '2024-01-15'"
      },
      "depends_on": ["s2"],
      "description": "Breakdown by platform after metric comparison"
    }
  ],
  "modify_steps": [
    {
      "index": 1,
      "params": {"limit": 20}
    }
  ],
  "skip_steps": [2]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `add_steps` | array[object] | no | New steps to append. Same structure as plan step objects. |
| `modify_steps` | array[object] | no | Param updates for existing steps, identified by zero-based `index`. Params are merged (not replaced). |
| `skip_steps` | array[integer] | no | Zero-based indices of steps to mark as skipped. Skipped steps are not executed. |

All three fields are optional and may be combined in a single request.

### Response

```json
{
  "plan_id": "plan_...",
  "status": "approved",
  "steps": [...],
  "validation": {
    "passed": true,
    "checks": [...]
  },
  "auto_approved": true,
  "updated_at": "2024-01-15T10:10:00+00:00"
}
```
