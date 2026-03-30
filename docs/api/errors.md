# Error Reference

Factum uses standard HTTP status codes. Error responses include a `detail` field with a human-readable message, and in some cases additional structured context.

## HTTP Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| `200` | OK | Successful GET or POST |
| `201` | Created | Resource created successfully (some POST endpoints) |
| `400` | Bad Request | Invalid request body, missing required fields, or invalid parameter values |
| `404` | Not Found | Resource ID does not exist |
| `409` | Conflict | Duplicate unique constraint (e.g., duplicate binding, duplicate policy name), or invalid state transition |
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
  ]
}
```

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
{"detail": "Cannot cancel job in 'running' status. Only pending jobs can be cancelled."}
```

### Governance Violation (400)

```json
{
  "detail": "Step blocked by governance policy",
  "violations": [
    {
      "policy_name": "no_raw_pii",
      "policy_type": "aggregate_only",
      "message": "Step type 'sample_rows' is disallowed on table events.user_video_watch"
    }
  ]
}
```

### Routing Failure (400)

```json
{
  "detail": "No single engine has bindings covering all requested tables: ['events.user_video_watch', 'other.unknown_table']"
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
{"detail": "Binding already exists for source_id=src_abc and engine_id=eng_xyz (binding_id: bind_...)"}
{"detail": "A policy named 'no_raw_pii' already exists"}
```

## Trino-Specific Errors

When using a Trino engine, query errors from the Trino coordinator are wrapped and surfaced as 500 errors with the Trino error message in `detail`. Common Trino errors:

| Trino Error | Cause | Resolution |
|-------------|-------|------------|
| `QUERY_REJECTED: Missing required partition filter` | Table requires a partition column in WHERE clause | Ensure the step uses typed `time_scope`; Factum will resolve partition pruning automatically when time metadata or heuristics can identify the partition columns |
| `Table ... does not exist` | Table not found in Trino catalog | Check the namespace configuration in the engine binding |
| `identityAccountPassword can't be empty` | Wrong Trino user | Use the correct user in engine connection config |
