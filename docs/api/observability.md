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

Returns the current health status of the Factum service. Used by load balancers, Kubernetes liveness probes, and monitoring systems.

### Response

```json
{
  "status": "ok",
  "db_path": "/data/analytics.duckdb"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` when the service is healthy |
| `db_path` | string | Path to the configured analytics database |

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
    "sync_jobs_completed": 8
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
# HELP factum_sessions_created_total Total sessions created
# TYPE factum_sessions_created_total counter
factum_sessions_created_total 42

# HELP factum_steps_executed_total Total steps executed
# TYPE factum_steps_executed_total counter
factum_steps_executed_total 318

# HELP factum_step_duration_seconds Step execution duration in seconds
# TYPE factum_step_duration_seconds histogram
factum_step_duration_seconds_bucket{le="1"} 89
factum_step_duration_seconds_bucket{le="5"} 241
factum_step_duration_seconds_bucket{le="30"} 315
factum_step_duration_seconds_bucket{le="+Inf"} 318
factum_step_duration_seconds_sum 1241.3
factum_step_duration_seconds_count 318
```

---

## Structured Logging

Factum emits structured JSON logs when configured with the JSON formatter. Log entries include:

```json
{
  "timestamp": "2024-01-15T10:05:00+00:00",
  "level": "INFO",
  "logger": "factum.service",
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
