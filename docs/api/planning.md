# Plans

Plans allow you to define a sequence of analysis steps as a structured workflow, validate them, estimate costs, and execute them in dependency order. Plans follow the lifecycle: `draft` -> `validated` -> `approved` -> `executing` -> `completed` / `failed`.

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
  "step_type": "compare_metric",
  "params": {
    "table": "events.user_video_watch",
    "metric": "avg_watch_time_minutes",
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
    }
  },
  "dependencies": [],
  "description": "Measure week-over-week watch time change"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step_type` | string | yes | One of: `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, `correlate_metrics`, `synthesize_findings` |
| `params` | object | no | Step parameters (same as direct step execution; see [Sessions & Steps](sessions.md)) |
| `dependencies` | array[integer] | no | Zero-based step indices that must complete before this step runs |
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
      "step_type": "profile_table",
      "params": {"table_name": "events.user_video_watch"},
      "dependencies": [],
      "description": "Baseline table profile"
    },
    {
      "step_type": "compare_metric",
      "params": {
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
          }
        }
      },
      "dependencies": [0],
      "description": "Watch time week-over-week comparison"
    },
    {
      "step_type": "aggregate_query",
      "params": {
        "table": "events.user_video_watch",
        "group_by": ["device_type", "region"],
        "measures": [
          {"expr": "AVG(watch_duration_sec)", "as": "avg_watch_sec"},
          {"expr": "COUNT(*)", "as": "cnt"}
        ],
        "time_scope": {
          "mode": "single_window",
          "grain": "day",
          "current": {
            "start": "2024-01-24",
            "end": "2024-01-31"
          }
        },
        "scope": {
          "predicate": "watch_duration_sec > 30"
        }
      },
      "dependencies": [1],
      "description": "Break down the current window by device and region"
    },
    {
      "step_type": "synthesize_findings",
      "params": {},
      "dependencies": [1, 2],
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

1. **Step type validity** - all `step_type` values must be recognized
2. **Dependency acyclicity** - `dependencies` references must not form cycles
3. **Required params** - required parameters must be present for each step type
4. **Semantic resolution** - `compare_metric` metrics must be published; requested `dimensions` must be supported by the metric
5. **Contract constraints** - typed step params must satisfy the final `time_scope` contract, and `scope.predicate` cannot contain time predicates
6. **Governance** - steps are checked against active policies

For the typed time-scope steps:

- `compare_metric` requires `table`, `metric`, and `time_scope`
- `aggregate_query` requires `table`, `measures`, and `time_scope`
- `scope.predicate` must not contain time conditions; move all time filtering into `time_scope`
- typed step payloads are validated through the final `compare_metric` / `aggregate_query` normalizers; legacy fields such as `metric_name`, `table_name`, `period_start`, `period_end`, `baseline_start`, `baseline_end`, `comparison_type`, `compare_period`, `date_column`, `select`, `where`, `order_by`, and `filter` are therefore invalid contract inputs

**Validation issue codes** (non-exhaustive):

| Code | Category | Description |
|------|----------|-------------|
| `missing_required_param` | params | A required param is absent for the step type |
| `invalid_step_contract` | params | A typed step payload failed contract normalization, including legacy-field usage |
| `time_predicate_not_allowed_in_scope` | params | `scope.predicate` contains time-axis conditions; move them into `time_scope` |
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

Executes an `approved` plan. Steps are executed in topological order (respecting `dependencies`).

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
  "step_results": [
    {
      "index": 0,
      "step_type": "compare_metric",
      "status": "completed",
      "summary": "Metric comparison completed.",
      "cost_estimate": {
        "subject": "step:0",
        "estimated_rows": 120000,
        "confidence": "medium"
      },
      "actual_cost_feedback": {
        "duration_ms": 850.0
      }
    },
    {
      "index": 1,
      "step_type": "aggregate_query",
      "status": "failed",
      "error": "..."
    }
  ]
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
  "status": "draft",
  "explanation": "Plan plan_... (draft): 4 steps\n  0. profile_table\n  1. compare_metric (depends on: [0])\n  2. aggregate_query (depends on: [1])\n  3. synthesize_findings (depends on: [1, 2])",
  "total_estimated_cost": 4200000
}
```

---

## Estimate Costs

```
POST /sessions/{session_id}/plans/{plan_id}/estimate-costs
```

Estimates the execution cost for each step in the plan based on the shared cost model. The stored plan steps are updated in-place with `estimated_cost` and `estimated_cost_detail`.

### Response

```json
{
  "plan_id": "plan_...",
  "total_estimated_cost": 4200000000,
  "cost_estimates": [
    {
      "subject": "step:0",
      "estimated_rows": 2000000000,
      "confidence": "medium"
    },
    {
      "subject": "step:1",
      "estimated_rows": 2200000000,
      "confidence": "medium"
    }
  ],
  "steps": [
    {
      "index": 0,
      "step_type": "profile_table",
      "estimated_cost": 2000000000,
      "estimated_cost_detail": {
        "subject": "step:0",
        "estimated_rows": 2000000000,
        "confidence": "medium"
      }
    },
    {
      "index": 1,
      "step_type": "compare_metric",
      "estimated_cost": 2200000000,
      "estimated_cost_detail": {
        "subject": "step:1",
        "estimated_rows": 2200000000,
        "confidence": "medium"
      }
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
  "max_rows": 500000000000,
  "estimated_rows": 4200000000,
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
      "subject": "step:1",
      "message": "Estimated rows exceed session budget"
    }
  ]
}
```

---

## Patch Plan

```
POST /sessions/{session_id}/plans/{plan_id}/patch
```

Incrementally patches a plan. Intended for agent workflows where the agent wants to add, modify, or skip steps based on new evidence (for example, after reading a `reflection-context` response).

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
        "table": "events.user_video_watch",
        "group_by": ["platform"],
        "measures": [
          {"expr": "COUNT(*)", "as": "cnt"}
        ],
        "time_scope": {
          "mode": "single_window",
          "grain": "day",
          "current": {
            "start": "2024-01-15",
            "end": "2024-01-16"
          }
        }
      },
      "dependencies": [1],
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
