# Health & Observability

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health check |
| `GET` | `/metrics` | Operational metrics |

---

## Health Check

```
GET /health
```

Returns the current health status of the Marivo service. Used by load balancers, Kubernetes liveness probes, and monitoring systems.

### Response

```json
{
  "status": "ok"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` when the service is healthy |

HTTP status codes:

| Code | Meaning |
|------|---------|
| `200` | Service is healthy |
| `503` | Service is degraded (metadata store or analytics engine unreachable) |

---

## Metrics

```
GET /metrics
```

Returns operational metrics collected since service startup. Supports JSON and Prometheus text formats.

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `"prometheus"` for Prometheus text format; omit for JSON |

### JSON Response

```json
{
  "counters": {
    "sessions_created": 42,
    "steps_executed": 318,
    "steps_failed": 7,
    "plans_executed": 15,
    "datasource_live_browse_completed": 8
  },
  "histograms": {
    "step_duration_sec": {
      "count": 318,
      "sum": 1241.3,
      "p50": 2.1,
      "p95": 12.4,
      "p99": 28.7
    },
    "query_scan_bytes": {
      "count": 318,
      "sum": 4200000000000,
      "p50": 8500000000,
      "p95": 45000000000
    }
  },
  "gauges": {
    "active_sessions": 3,
    "pending_jobs": 1
  },
  "collected_at": "2024-01-15T10:30:00+00:00"
}
```

### Prometheus Response

When `format=prometheus`:

```
# HELP marivo_sessions_created_total Total sessions created
# TYPE marivo_sessions_created_total counter
marivo_sessions_created_total 42

# HELP marivo_steps_executed_total Total steps executed
# TYPE marivo_steps_executed_total counter
marivo_steps_executed_total 318

# HELP marivo_step_duration_seconds Step execution duration in seconds
# TYPE marivo_step_duration_seconds histogram
marivo_step_duration_seconds_bucket{le="1"} 89
marivo_step_duration_seconds_bucket{le="5"} 241
marivo_step_duration_seconds_bucket{le="30"} 315
marivo_step_duration_seconds_bucket{le="+Inf"} 318
marivo_step_duration_seconds_sum 1241.3
marivo_step_duration_seconds_count 318
```

---

## Structured Logging

Marivo emits structured JSON logs when configured with the JSON formatter. Log entries include:

```json
{
  "timestamp": "2024-01-15T10:05:00+00:00",
  "level": "INFO",
  "logger": "marivo.service",
  "message": "Step completed",
  "session_id": "sess_...",
  "step_id": "step_...",
  "step_type": "metric_query",
  "duration_sec": 3.2,
  "engine": "duckdb",
  "scan_bytes": 2100000000
}
```

Log levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`

Execution logging defaults to an `INFO` event for each SQL statement sent to the analytics engine.
The execution log includes the rendered SQL text plus execution metadata such as:

- `sql`
- `param_count`
- `engine_type`
- `engine_id`
- `stage_id`
- `source_tables`
- `execution_mode`
- `execution_purpose`

### Execution Auth Logging

When a Trino engine uses `auth.mode = "username_only"`, Marivo emits execution-auth structured logs
when runtime actually touches the engine. Building a runtime engine object for routing/preflight
alone does not emit the success event.

Success event:

```json
{
  "timestamp": "2024-01-15T10:05:00+00:00",
  "level": "INFO",
  "logger": "marivo.execution_auth",
  "message": "execution_auth_resolved",
  "session_id": "sess_...",
  "engine_id": "eng_...",
  "session_user": "alice",
  "actor_ref": "agent.alice"
}
```

Failure event:

```json
{
  "timestamp": "2024-01-15T10:05:00+00:00",
  "level": "WARNING",
  "logger": "marivo.execution_auth",
  "message": "execution_auth_preflight_failed",
  "session_id": "sess_...",
  "engine_id": "eng_...",
  "session_user": null,
  "actor_ref": "agent.alice",
  "failure_code": "session_user_missing"
}
```

Notes:

- these events are emitted only for Trino `username_only` runtime resolution
- DuckDB ignores session execution identity and does not emit execution-auth audit events
- `timestamp` is the audit event time; there is no separate `executed_at` field in this v1 surface
