# Health & Observability

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health check |
| `GET` | `/metrics` | Operational metrics snapshot |

## Health Check

```http
GET /health
```

Returns:

```json
{
  "status": "ok"
}
```

The handler verifies that application services are attached to the FastAPI app.

## Metrics

```http
GET /metrics
```

When metrics collection is disabled, the endpoint returns:

```json
{
  "error": "Metrics collection is disabled"
}
```

When metrics collection is enabled, the default response is the runtime metrics
snapshot returned by the configured metrics collector.

Prometheus text output is available with:

```http
GET /metrics?format=prometheus
```

The Prometheus response uses `text/plain`.

## Structured Logging

HTTP requests pass through `TimingMiddleware`; identity-aware routes receive the
trusted caller value from `X-Marivo-User` through `UserIdentityMiddleware`.
Execution and datasource logs are implementation events, not stable API
response contracts. Use `/metrics` and the runtime-status endpoints for
operator-facing HTTP reads.
