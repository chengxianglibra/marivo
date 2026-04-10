# factum-mcp

External MCP adapter for Factum.

This subproject keeps the MCP runtime separate from Factum's core HTTP service.
Factum remains HTTP-only. The MCP server is a client-side adapter over the
canonical HTTP API.

## Current Scope

Current scope provides:

- a standalone Python package
- `stdio` and Streamable HTTP MCP server entrypoints
- environment-driven configuration loading
- a shared HTTP client with uniform result envelopes
- discovery and catalog tools that proxy canonical Factum HTTP endpoints
- session lifecycle and canonical state/context investigation tools
- typed intent tools that map directly to Factum's `/sessions/{id}/intents/*` routes

It does not yet provide:

- semantic-layer production tool implementations

## Environment

The server reads these environment variables:

- `FACTUM_BASE_URL` (required)
- `FACTUM_API_TOKEN` (optional)
- `FACTUM_MCP_TRANSPORT` (optional, default `stdio`)
- `FACTUM_TIMEOUT_MS` (optional, default `10000`)
- `FACTUM_OPENAPI_CACHE_TTL_SEC` (optional, default `300`)
- `FACTUM_DEFAULT_SOURCE_ID` (optional)
- `FACTUM_MCP_HOST` (optional, default `127.0.0.1`)
- `FACTUM_MCP_PORT` (optional, default `8000`)
- `FACTUM_MCP_STREAMABLE_HTTP_PATH` (optional, default `/mcp`)
- `FACTUM_MCP_STATELESS_HTTP` (optional, default `true`)
- `FACTUM_MCP_JSON_RESPONSE` (optional, default `true`)

Missing or invalid required configuration fails at startup with a clear error.
No implicit fallback base URL is used.

## Install

```bash
cd factum-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

```bash
FACTUM_BASE_URL=http://127.0.0.1:8000 factum-mcp
```

The entrypoint starts a local `stdio` MCP server. If the Python MCP SDK is not
installed, startup fails with an explicit dependency error.

Run the Streamable HTTP transport:

```bash
FACTUM_BASE_URL=http://127.0.0.1:8000 factum-mcp-http
```

Or select it via the shared entrypoint:

```bash
FACTUM_BASE_URL=http://127.0.0.1:8000 \
FACTUM_MCP_TRANSPORT=streamable-http \
factum-mcp
```

With the current defaults and the official Python MCP SDK, clients should
connect to `http://127.0.0.1:8000/mcp`.

## Tool Envelope

Every HTTP-backed tool returns the same envelope:

```json
{
  "ok": true,
  "status_code": 200,
  "data": {
    "status": "ok"
  },
  "error": null,
  "meta": {
    "factum_path": "/health",
    "method": "GET",
    "request_url": "http://127.0.0.1:8000/health",
    "attempt_count": 1,
    "content_type": "application/json"
  }
}
```

For failures, `error.category` is normalized to one of:

- `validation`
- `not_found`
- `conflict`
- `transport`
- `server_error`

Typed semantic `422` responses preserve Factum's canonical `detail` and
`guidance` fields. The MCP adapter only adds a short `remediation_hint`; it
does not rewrite the original error body.

## T4 Tools

Current discovery / health / catalog coverage:

- `health_check()` -> `GET /health`
- `list_openapi_paths()` -> `GET /openapi/index`
- `get_openapi_schema(schema_name, depth=1)` -> `GET /openapi/schemas/{schema_name}`
- `get_openapi_fragment(path, operation=None, expand=None, depth=1)` -> `GET /openapi/fragment`
- `get_openapi_path_fragment(encoded_path, expand=None, depth=1)` -> `GET /openapi/paths/{encoded_path}`
- `search_catalog(q, type=None)` -> `GET /catalog/search`
- `resolve_typed_ref(ref)` -> `GET /semantic/resolve/{ref}`

These tools are adapters over the canonical HTTP contract. They do not publish a
second schema or reinterpret Factum object families.

## T5 Tools

Current session / state / context coverage:

- `create_session(goal, constraints=None, raw_filter=None, budget=None, policy=None)` -> `POST /sessions`
- `get_session(session_id)` -> `GET /sessions/{session_id}`
- `get_session_state(session_id, metric=None, entity=None, proposition_type=None, origin_kind=None, assessment_presence=None, assessment_status=None, has_blocking_gaps=None, limit=None, page_token=None)` -> `GET /sessions/{session_id}/state`
- `query_session_state(session_id, metric=None, entity=None, slice=None, proposition_types=None, origin_kinds=None, assessment_presence=None, assessment_statuses=None, has_blocking_gaps=None, limit=None, page_token=None)` -> `POST /sessions/{session_id}/state/query`
- `get_proposition_context(session_id, proposition_id)` -> `GET /sessions/{session_id}/propositions/{proposition_id}/context`

Boundary notes:

- `get_session_state()` mirrors the `GET /state` query contract and intentionally does not support `slice`
- use `query_session_state()` when `slice` or a structured state query body is required
- `get_proposition_context()` reads canonical proposition closure, not runtime status
- tool `data` remains the raw Factum canonical body; the MCP adapter only wraps it in the shared envelope

## T6 Tools

Current typed intent coverage:

- `observe(session_id, metric, time_scope, result_mode="standard", scope=None, granularity=None, dimensions=None)` -> `POST /sessions/{session_id}/intents/observe`
- `compare(session_id, left_ref, right_ref, mode="auto")` -> `POST /sessions/{session_id}/intents/compare`
- `decompose(session_id, compare_ref, dimension, method="delta_share")` -> `POST /sessions/{session_id}/intents/decompose`
- `correlate(session_id, left_ref, right_ref, method="spearman", min_pairs=5)` -> `POST /sessions/{session_id}/intents/correlate`
- `detect(session_id, metric, time_scope, scope=None, split_by=None, profile="auto", sensitivity="balanced", limit=None, max_series=None)` -> `POST /sessions/{session_id}/intents/detect`
- `test_intent(session_id, left_ref, right_ref, hypothesis, method="auto")` -> `POST /sessions/{session_id}/intents/test`
- `forecast(session_id, source_ref, horizon, profile="auto", interval_level=None)` -> `POST /sessions/{session_id}/intents/forecast`
- `attribute(session_id, metric, left, right, dimensions, decomposition_method="delta_share", decomposition_limit=5)` -> `POST /sessions/{session_id}/intents/attribute`
- `diagnose(session_id, metric, time_scope, candidate_dimensions, scope=None, detect_split_by=None, profile="auto", sensitivity="balanced", candidate_limit=None, followup_limit=3, decomposition_limit=None)` -> `POST /sessions/{session_id}/intents/diagnose`
- `validate(session_id, metric, left, right, sample_kind=None, hypothesis=None, method=None)` -> `POST /sessions/{session_id}/intents/validate`

Boundary notes:

- these are path-discriminated intent tools; do not add an extra `intent` or `step_type` field to the request body
- MCP parameter names intentionally reuse the canonical HTTP request field names
- tool `data` remains the raw Factum success body; the adapter does not derive a new evidence summary
- for `422` responses, use `error.guidance.contract_url`, `error.guidance.schema_url`, and `error.guidance.examples` to repair the payload

## Minimal Examples

Examples below use the MCP tool names and the smallest useful argument set.

Health check:

```json
{}
```

List OpenAPI paths:

```json
{}
```

Read one OpenAPI schema:

```json
{
  "schema_name": "SessionCreateRequest",
  "depth": 1
}
```

Read one OpenAPI operation fragment:

```json
{
  "path": "/sessions",
  "operation": "post",
  "expand": ["request", "response", "schemas"],
  "depth": 1
}
```

Read one OpenAPI path fragment:

```json
{
  "encoded_path": "L3Nlc3Npb25z",
  "expand": ["schemas"],
  "depth": 1
}
```

Search the catalog:

```json
{
  "q": "watch",
  "type": "metric"
}
```

Resolve a typed ref:

```json
{
  "ref": "metric.watch_time"
}
```

Create a minimal session:

```json
{
  "goal": "Investigate the week-over-week watch time decline"
}
```

Read one session root:

```json
{
  "session_id": "sess_123"
}
```

Read filtered session state with GET-compatible query fields:

```json
{
  "session_id": "sess_123",
  "metric": "watch_time",
  "assessment_presence": "assessed",
  "has_blocking_gaps": true,
  "limit": 25
}
```

Read structured session state with `slice`:

```json
{
  "session_id": "sess_123",
  "metric": "watch_time",
  "slice": {
    "country": "US",
    "device_type": "mobile"
  },
  "proposition_types": ["metric_status"],
  "page_token": "cursor_1"
}
```

Read one proposition context:

```json
{
  "session_id": "sess_123",
  "proposition_id": "prop_456"
}
```

Minimal typed intent examples:

Observe:

```json
{
  "session_id": "sess_123",
  "metric": "metric.watch_time",
  "time_scope": {
    "kind": "range",
    "start": "2025-03-01",
    "end": "2025-03-08"
  }
}
```

Compare:

```json
{
  "session_id": "sess_123",
  "left_ref": {
    "artifact_id": "obs_left",
    "step_type": "observe"
  },
  "right_ref": {
    "artifact_id": "obs_right",
    "step_type": "observe"
  }
}
```

Decompose:

```json
{
  "session_id": "sess_123",
  "compare_ref": {
    "artifact_id": "cmp_123",
    "step_type": "compare"
  },
  "dimension": "dimension.country"
}
```

Correlate:

```json
{
  "session_id": "sess_123",
  "left_ref": {
    "artifact_id": "obs_left",
    "step_type": "observe"
  },
  "right_ref": {
    "artifact_id": "obs_right",
    "step_type": "observe"
  }
}
```

Detect:

```json
{
  "session_id": "sess_123",
  "metric": "metric.watch_time",
  "time_scope": {
    "mode": "single_window",
    "grain": "day",
    "current": {
      "start": "2025-03-01",
      "end": "2025-03-08"
    }
  }
}
```

Test:

```json
{
  "session_id": "sess_123",
  "left_ref": {
    "artifact_id": "obs_left",
    "step_type": "observe"
  },
  "right_ref": {
    "artifact_id": "obs_right",
    "step_type": "observe"
  },
  "hypothesis": {
    "family": "difference",
    "alternative": "two_sided",
    "alpha": 0.05
  }
}
```

Forecast:

```json
{
  "session_id": "sess_123",
  "source_ref": {
    "artifact_id": "obs_series",
    "step_type": "observe"
  },
  "horizon": 14
}
```

Attribute:

```json
{
  "session_id": "sess_123",
  "metric": "metric.watch_time",
  "left": {
    "time_scope": {
      "kind": "range",
      "start": "2025-03-01",
      "end": "2025-03-08"
    }
  },
  "right": {
    "time_scope": {
      "kind": "range",
      "start": "2025-02-22",
      "end": "2025-03-01"
    }
  },
  "dimensions": ["dimension.country"]
}
```

Diagnose:

```json
{
  "session_id": "sess_123",
  "metric": "metric.watch_time",
  "time_scope": {
    "mode": "single_window",
    "grain": "day",
    "current": {
      "start": "2025-03-01",
      "end": "2025-03-08"
    }
  },
  "candidate_dimensions": ["dimension.country"]
}
```

Validate:

```json
{
  "session_id": "sess_123",
  "metric": "metric.conversion_rate",
  "left": {
    "time_scope": {
      "kind": "range",
      "start": "2025-03-01",
      "end": "2025-03-08"
    }
  },
  "right": {
    "time_scope": {
      "kind": "range",
      "start": "2025-02-22",
      "end": "2025-03-01"
    }
  }
}
```

Common failure examples:

- `get_session({"session_id":"sess_missing"})` -> `404` with `error.category = "not_found"`
- `query_session_state(...)` with an invalid body field or enum -> `422` with canonical `detail` preserved under `error.detail`
- `get_proposition_context(...)` for a missing or cross-session proposition -> `404` with the original Factum error message
- `observe(...)` with an invalid or incomplete body -> `422` with canonical `guidance` preserved under `error.guidance`; start with `error.guidance.examples`, then inspect `error.guidance.schema_url` or `error.guidance.contract_url`
