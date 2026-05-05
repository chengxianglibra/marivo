# Error Reference

Marivo uses standard HTTP status codes. Error responses include a `detail` field with a human-readable message, and in some cases additional structured context.

## HTTP Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| `200` | OK | Successful GET or POST |
| `201` | Created | Resource created successfully (some POST endpoints) |
| `400` | Bad Request | Invalid request body, missing required fields, or invalid parameter values |
| `404` | Not Found | Resource ID does not exist |
| `409` | Conflict | Duplicate unique constraint (e.g., duplicate policy name), or invalid state transition |
| `422` | Unprocessable Entity | FastAPI/Pydantic validation error — request body fails schema validation |
| `500` | Internal Server Error | Engine error, unexpected exception |
| `503` | Service Unavailable | Metadata store or analytics engine unreachable |

## Error Response Format

```json
{
  "detail": "Session sess_abc123 not found"
}
```

For Pydantic validation errors (422):

```json
{
  "detail": [
    {
      "loc": ["body", "metric_name"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ],
  "error": {
    "code": "request_validation_error",
    "message": "Request validation failed. Use the guided example and contract links."
  },
  "guidance": {
    "docs_url": "docs/api/semantic.md",
    "contract_url": "/openapi/paths/L3NlbWFudGljL2VudGl0aWVz?operation=post&expand=request,schemas&depth=6",
    "schema_url": "/openapi/schemas/TypedEntityCreateRequest?depth=6",
    "examples": [
      {
        "summary": "Minimal typed entity create payload",
        "complexity": "minimal",
        "payload": {
          "header": {
            "entity_ref": "entity.user",
            "display_name": "User",
            "entity_contract_version": "entity.v4"
          },
          "interface_contract": {
            "identity": {
              "key_refs": ["key.user_id"],
              "uniqueness_scope": "global",
              "id_stability": "stable"
            }
          }
        }
      }
    ],
    "next_action": "Start with guidance.examples, then read guidance.schema_url for the exact request model."
  }
}
```

The legacy `detail` array is preserved for compatibility. Clients that want guided remediation should
prefer `error` and `guidance` when present.

For typed semantic create/update failures, use this remediation order:

1. `guidance.examples`
2. `guidance.schema_url`
3. `guidance.contract_url`
4. `detail[*].loc`

`guidance.examples` is the fastest way to recover because the service returns a shortest-valid
payload shape for the specific route. `guidance.schema_url` is the next stop when you need the
exact request model. `guidance.contract_url` is a path-level OpenAPI fragment for route-scoped
context and is usually a later step than the example and schema links.

`guidance.contract_url` uses `GET /openapi/paths/{encoded_path}` where `encoded_path` is the raw
route path encoded with unpadded base64url. Example:

- raw path: `/semantic/entities`
- encoded path: `L3NlbWFudGljL2VudGl0aWVz`

Common typed semantic `422` patterns:

| Symptom | Correct structure |
| --- | --- |
| entity create is missing `header` or `interface_contract` | include both, and include `interface_contract.identity.key_refs` |
| metric create is missing `payload` or mismatches metric family | include `payload` and keep `header.metric_family` equal to `payload.metric_family` |
| metric create is missing `header.additivity_constraints` | include `header.additivity_constraints` with `dimension_policy` (`"all"`, `"subset"`, or `"none"`) and `time_axis_policy` (`"additive"` or `"non_additive"`) |
| metric create uses an unsupported `metric_family` or `value_semantics` | use one of the service-supported pairs such as `count_metric -> count`, `sum_metric -> sum`, `average_metric -> mean`, or `rate_metric -> ratio` |
| metric create uses the wrong payload shape for the family | `count_metric` uses `count_target`, `sum_metric` uses `measure`, and `average_metric` or `rate_metric` use `numerator` plus `denominator` |
| dimension create is missing `value_domain` | place it at `interface_contract.value_domain` |
| time create sends `interface_contract` | remove it; `/semantic/time` is header-only |
| dataset create/import is missing MARIVO datasource id | add `dataset.custom_extensions[].data.datasource_id` |
| dataset create/import has an empty `source` | set `dataset.source` to the datasource-local relation FQN from live browse |
| readiness reports `datasource_not_found` | create/select a datasource and put its id in the MARIVO dataset extension |
| readiness reports `relation_not_found` | browse schemas/tables and update `dataset.source` to the live FQN |
| readiness reports `field_expression_invalid` | update `field.expression.dialects[]` for the target datasource dialect |

Semantic service validation errors use the same guided envelope shape as request validation where possible. The `detail` object includes `message`, `code`, `category`, optional `field_path`, and nested `guidance` with docs/schema/contract links plus remediation examples.

## Step Submission Semantic Context

Some step submission endpoints may include extra structured context beyond `detail` when the intent contract exposes a stable semantic failure class.

Example:

```json
{
  "detail": "Compare inputs are not comparable",
  "code": "NOT_COMPARABLE",
  "issues": [
    {
      "code": "metric_mismatch",
      "severity": "error",
      "message": "Left and right observations resolve to different metrics"
    }
  ]
}
```

Calendar alignment compare-like failures should keep the same operator-facing message in both
`detail` and the typed issue payload. Example:

```json
{
  "detail": "compare: NOT_COMPARABLE - left and right observations freeze different calendar versions, so the alignment metadata cannot be replayed safely. Re-run both observations with the same frozen calendar version.",
  "code": "NOT_COMPARABLE",
  "issues": [
    {
      "code": "calendar_version_mismatch",
      "severity": "error",
      "gate_family": "comparability_gate",
      "blocking": true,
      "message": "left and right observations freeze different calendar versions, so the alignment metadata cannot be replayed safely. Re-run both observations with the same frozen calendar version.",
      "details": {
        "field_name": "resolved_calendar_version",
        "left_value": "calendar_data_cn_2026q2_v1",
        "right_value": "calendar_data_cn_2026q2_v2"
      }
    }
  ]
}
```

Optional fields that may appear on step-submission errors:

- `code` — stable semantic failure class such as `INVALID_ARGUMENT`, `INVALID_FILTER`, `STEP_NOT_FOUND`, `NOT_COMPARABLE`, or `INSUFFICIENT_HISTORY`
- `issues` — typed validation issues when the step contract defines them
- `ref` — the typed ref associated with the failing lookup or validation

## Common Error Scenarios

### Resource Not Found (404)

```json
{"detail": "Session sess_xyz not found"}
{"detail": "Plan plan_xyz not found for session sess_abc"}
{"detail": "Metric 'nonexistent_metric' not found or not published"}
```

### Invalid State Transition (409)

```json
{"detail": "Cannot approve plan in 'draft' status. Validate the plan first."}
{"detail": "Cannot execute plan in 'draft' status. Plan must be approved."}
```

### Routing Failure (400)

```json
{
  "detail": "Dataset orders source analytics.orders was not found in datasource ds_..."
}
```

### Engine Error (500)

```json
{
  "detail": "Query execution failed: [HY000] QUERY_REJECTED: Missing required partition filter on column 'log_date'"
}
```

### Budget Exceeded (400)

```json
{
  "detail": "Step exceeds session budget",
  "field": "max_scan_bytes",
  "estimated": 600000000000,
  "limit": 500000000000
}
```

### Duplicate Resource (409)

```json
{"detail": "A policy named 'no_raw_pii' already exists"}
```

Typed semantic create routes may return a structured ref conflict when a governed semantic ref is
already owned:

```json
{
  "detail": {
    "message": "Metric ref 'metric.avg_blocked_time' is already owned by an existing semantic metric",
    "code": "semantic_ref_conflict",
    "category": "conflict",
    "field_path": "header.metric_ref",
    "error": {
      "code": "semantic_ref_conflict",
      "message": "Metric ref 'metric.avg_blocked_time' is already owned by an existing semantic metric",
      "category": "conflict",
      "field_path": "header.metric_ref"
    },
    "guidance": {
      "remediation": {
        "existing_object_kind": "metric",
        "existing_object_id": "metc_abc123",
        "existing_ref": "metric.avg_blocked_time",
        "existing_status": "deprecated",
        "existing_lifecycle_status": "deprecated",
        "existing_revision": 3,
        "ref_ownership": "deprecated objects retain semantic ref ownership"
      },
      "examples": []
    }
  }
}
```

Recovery order:

1. inspect the existing object returned in `guidance.remediation`
2. use the revision path for spelling, description, or unit-label corrections when available
3. clone with a new ref only when the new metric is a different business semantic identity

Do not treat `deprecated` as ref release. Deprecated semantic objects remain readable for audit and
continue to own their ref.

## Trino-Specific Errors

When using a Trino engine, query errors from the Trino coordinator are wrapped and surfaced as 500 errors with the Trino error message in `detail`. Common Trino errors:

| Trino Error | Cause | Resolution |
|-------------|-------|------------|
| `QUERY_REJECTED: Missing required partition filter` | Table requires a partition column in WHERE clause | Ensure the step uses typed `time_scope`; Marivo will resolve partition pruning automatically when time metadata or heuristics can identify the partition columns |
| `Table ... does not exist` | Table not found in Trino catalog | Browse the datasource and update `dataset.source` to a live relation FQN |
| `identityAccountPassword can't be empty` | Wrong Trino user | Use the correct user in engine connection config |
