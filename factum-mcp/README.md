# factum-mcp

External MCP adapter for Factum.

This subproject keeps the MCP runtime separate from Factum's core HTTP service.
Factum remains HTTP-only. The MCP server is a client-side adapter over the
canonical HTTP API.

## Supported Scope

Validated P0 scope provides:

- a standalone Python package
- `stdio` and Streamable HTTP MCP server entrypoints
- environment-driven configuration loading
- a shared HTTP client with uniform result envelopes
- discovery and catalog tools that proxy canonical Factum HTTP endpoints
- TTL-based caching for OpenAPI discovery tools
- session lifecycle and canonical state/context investigation tools
- typed intent tools that map directly to Factum's `/sessions/{id}/intents/*` routes
- semantic-layer lifecycle tools for all public object families
- read-only MCP resources that mirror canonical Factum HTTP surfaces

Semantic lifecycle tools now expose `validate_*`, `activate_*`, and `deprecate_*` for each public
semantic object family. Legacy `publish_*` tools remain available as compatibility aliases for
`activate_*`.
Activation only changes public lifecycle into `active`; MCP callers must still inspect
`readiness_status`, `blocking_requirements`, and `capabilities` before assuming a semantic object is
usable for default runtime resolution.

Implemented but non-P0 surfaces remain available for source admin and routing
workflows:

- `get_openapi_path_fragment`
- `list_sources`
- `register_source`
- `sync_source`
- `get_source_objects`
- `get_source_object`
- `resolve_routing`
- `factum://catalog/summary`
- `factum://sources/{source_id}/objects`
- `factum://sources/{source_id}/objects/{object_id}`
- `factum://server/config`

The executable support inventory lives in `factum_mcp.inventory`. Tests use that
module as the machine-readable source of truth for registration and contract
consistency checks.

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
pip install -e ..
pip install -e .
```

`factum-mcp` reuses Factum's canonical Pydantic request models for typed intent
tool schemas. Keep the repository root package importable in the same
environment as the MCP adapter.

## Client Setup

After installing the package, you can register Factum with an MCP client in one
of two ways.

Local `stdio` MCP:

```json
{
  "mcpServers": {
    "factum": {
      "command": "/absolute/path/to/factum/factum-mcp/.venv/bin/factum-mcp",
      "env": {
        "FACTUM_BASE_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

This starts `factum-mcp` as a local subprocess and uses the default `stdio`
transport.

Streamable HTTP MCP (`streamable-http`, sometimes called `http-stream`):

1. Start the MCP HTTP server:

```bash
cd factum-mcp
FACTUM_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/factum-mcp-http
```

2. Point the MCP client at the server URL:

```json
{
  "mcpServers": {
    "factum": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

If you need a different bind host, port, or path, set
`FACTUM_MCP_HOST`, `FACTUM_MCP_PORT`, and `FACTUM_MCP_STREAMABLE_HTTP_PATH`
before starting the HTTP transport.

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

## Validation

Run the offline MCP regression checks from the repository root:

```bash
.venv/bin/pytest \
  tests/test_factum_mcp_config.py \
  tests/test_factum_mcp_transport.py \
  tests/test_factum_mcp_resources.py \
  tests/test_factum_mcp_inventory.py \
  tests/test_factum_mcp_smoke.py
```

Optional live smoke against a running Factum HTTP service:

```bash
cd factum-mcp
FACTUM_BASE_URL=http://127.0.0.1:8000 .venv/bin/factum-mcp-smoke
```

The live smoke checks:

- configuration loading
- `GET /health`
- `GET /openapi/index`
- `POST /sessions`
- `GET /sessions/{session_id}/state`
- one intentional `422` validation envelope via `POST /semantic/entities`

The release checklist is documented in
[`docs/release-checklist.md`](docs/release-checklist.md).

## Resources

The adapter also exposes read-only MCP resources for high-frequency canonical
reads:

- `factum://catalog/summary`
- `factum://sessions/{session_id}/state`
- `factum://sessions/{session_id}/propositions/{proposition_id}/context`
- `factum://semantic/{family}`
- `factum://sources/{source_id}/objects`
- `factum://sources/{source_id}/objects/{object_id}`
- `factum://server/config`

Resource rules:

- resources return the raw canonical JSON body for the mirrored HTTP surface
- resources do not wrap responses in the tool envelope
- `factum://catalog/summary` is a fixed aggregate snapshot over canonical read
  surfaces; it does not become a search API
- `factum://sources/{source_id}/objects` reads synced metadata only, not live
  external catalog browse endpoints
- `factum://sources/{source_id}/objects/{object_id}` reads one synced source
  object detail only, not live external catalog browse endpoints
- `factum://semantic/{family}` only supports public semantic families
- **MCP resources do not support query parameters** (e.g., `{?status}`, `{?type}`).
  The HTTP endpoints remain the authoritative surface for filtering; MCP resources
  simply mirror the canonical read surface. Use HTTP tools for parameterized queries.

## Known Limitations

- Factum remains HTTP-only; this adapter is a separate client-side process.
- MCP resources mirror canonical HTTP reads and do not become a second source
  of evidence.
- The adapter does not invent planner-style tools, generic step submission, or
  SQL execution surfaces.
- Some valid HTTP contracts are intentionally not wrapped yet, including
  `GET /sessions` and `GET /sources/{source_id}`.

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

Semantic publish failures may return structured `detail` payloads instead of the
usual validation list. The MCP adapter extracts `error.message` and
`error.code` from that structure while preserving the original `error.detail`
object. Use that combination to distinguish draft-state errors, missing
dependencies, compatibility failures, and missing objects.

## T4 Tools

Current discovery / health / catalog coverage:

- `health_check()` -> `GET /health`
- `list_openapi_paths()` -> `GET /openapi/index`
- `get_openapi_schema(schema_name, depth=1)` -> `GET /openapi/schemas/{schema_name}`
- `get_openapi_fragment(path, operation=None, expand=None, depth=1)` -> `GET /openapi/fragment`
- `get_openapi_path_fragment(path, expand=None, depth=1)` -> `GET /openapi/paths/{encoded_path}` (tool auto-encodes raw path as base64url)
- `search_catalog(q, type=None, readiness=None)` -> `GET /catalog/search`; `type` includes
  `calendar_policy` for builtin calendar-alignment refs such as `calendar_policy.holiday_yoy`
- `resolve_typed_ref(ref)` -> `GET /semantic/resolve/{ref}`

These tools are adapters over the canonical HTTP contract. They do not publish a
second schema or reinterpret Factum object families.

OpenAPI discovery responses are cached inside the MCP adapter using
`FACTUM_OPENAPI_CACHE_TTL_SEC`:

- cache scope is limited to `list_openapi_paths`, `get_openapi_schema`,
  `get_openapi_fragment`, and `get_openapi_path_fragment`
- only successful responses are cached
- `FACTUM_OPENAPI_CACHE_TTL_SEC=0` disables the cache
- once the TTL expires, the adapter re-reads Factum's OpenAPI surface and
  surfaces the latest `revision`

## T5 Tools

Current session / state / context coverage:

- `create_session(goal, budget=None, policy=None)` -> `POST /sessions`
- `get_session(session_id)` -> `GET /sessions/{session_id}`
- `get_session_state(session_id, metric=None, entity=None, proposition_type=None, origin_kind=None, assessment_presence=None, assessment_status=None, has_blocking_gaps=None, limit=None, page_token=None)` -> `GET /sessions/{session_id}/state`
- `query_session_state(session_id, metric=None, entity=None, slice=None, proposition_types=None, origin_kinds=None, assessment_presence=None, assessment_statuses=None, has_blocking_gaps=None, limit=None, page_token=None)` -> `POST /sessions/{session_id}/state/query`
- `get_proposition_context(session_id, proposition_id)` -> `GET /sessions/{session_id}/propositions/{proposition_id}/context`

Boundary notes:

- `create_session()` only accepts canonical session-root fields; execution filters belong in typed
  intent requests such as `scope.constraints` or `scope.predicate`
- `get_session_state()` mirrors the `GET /state` query contract and intentionally does not support `slice`
- use `query_session_state()` when `slice` or a structured state query body is required
- `get_session_state()` is proposition-centered canonical state, not a session step or artifact inventory
- a successful `observe` may still leave `get_session_state()` empty when no externally visible proposition has been seeded yet; keep following the returned artifact or typed refs instead of treating empty state as execution failure
- `get_proposition_context()` reads canonical proposition closure, not runtime status
- tool `data` remains the raw Factum canonical body; the MCP adapter only wraps it in the shared envelope

## T6 Tools

Current typed intent coverage:

- `observe(session_id, metric, time_scope, result_mode="standard", calendar_policy_ref=None, scope=None, granularity=None, dimensions=None)` -> `POST /sessions/{session_id}/intents/observe`
- `compare(session_id, left_ref, right_ref, mode="auto")` -> `POST /sessions/{session_id}/intents/compare`
- `decompose(session_id, compare_ref, dimension, method="delta_share")` -> `POST /sessions/{session_id}/intents/decompose`
- `correlate(session_id, left_ref, right_ref, method="spearman", min_pairs=5)` -> `POST /sessions/{session_id}/intents/correlate`
- `detect(session_id, metric, time_scope, scope=None, split_by=None, profile="auto", sensitivity="balanced", limit=None, max_series=None)` -> `POST /sessions/{session_id}/intents/detect`
- `test_intent(session_id, left_ref, right_ref, hypothesis, method="auto")` -> `POST /sessions/{session_id}/intents/test`
- `forecast(session_id, source_ref, horizon, profile="auto", interval_level=None)` -> `POST /sessions/{session_id}/intents/forecast`
- `attribute(session_id, metric, left, right, dimensions, decomposition_method="delta_share", decomposition_limit=5)` -> `POST /sessions/{session_id}/intents/attribute`
- `diagnose(session_id, metric, time_scope, candidate_dimensions, scope=None, detect_split_by=None, profile="auto", sensitivity="balanced", candidate_limit=None, followup_limit=3, decomposition_limit=5)` -> `POST /sessions/{session_id}/intents/diagnose`
- `validate(session_id, metric, left, right, sample_kind=None, hypothesis=None, method=None)` -> `POST /sessions/{session_id}/intents/validate`

Boundary notes:

- these are path-discriminated intent tools; do not add an extra `intent` or `step_type` field to the request body
- MCP parameter names intentionally reuse the canonical HTTP request field names
- every typed intent tool exposes a top-level `session_id` used to fill the canonical HTTP path, while the remaining MCP parameters map directly to the canonical HTTP request body fields
- nested MCP fields such as `time_scope`, `left_ref`, `right_ref`, `source_ref`, `left`, `right`, and `hypothesis` are structured objects in the MCP contract, not JSON-encoded strings
- `observe.time_scope` must be a canonical object, not a shorthand string; for a range use `{"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`
- `observe` keeps canonical guardrails from `ObserveRequest`: `granularity` and `dimensions` are mutually exclusive, and both are only valid when `result_mode="standard"`
- typed intent `metric` parameters must use canonical semantic refs such as `metric.watch_time`; bare names like `watch_time` are rejected
- the adapter still validates those structured objects with Factum's canonical request models before forwarding HTTP requests, so discriminators such as `time_scope.kind`, nested required fields, and enums such as `grain` keep canonical behavior
- tool `data` remains the raw Factum success body; the adapter does not derive a new evidence summary
- for `422` responses, use `error.guidance.contract_url`, `error.guidance.schema_url`, and `error.guidance.examples` to repair the payload

## T7 Tools

Current semantic-layer coverage:

- `create_entity(header, interface_contract)` -> `POST /semantic/entities`
- `list_entities(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/entities`
- `get_entity(object_id=None, entity_id=None)` -> `GET /semantic/entities/{entity_id}`
- `update_entity(entity_id, display_name=None, description=None, interface_contract=None)` -> `PUT /semantic/entities/{entity_id}`
- `validate_entity(entity_id)` -> `POST /semantic/entities/{entity_id}/validate`
- `activate_entity(entity_id)` -> `POST /semantic/entities/{entity_id}/activate`
- `deprecate_entity(entity_id)` -> `POST /semantic/entities/{entity_id}/deprecate`
- `publish_entity(entity_id)` -> `POST /semantic/entities/{entity_id}/publish`
- `create_metric(header, payload)` -> `POST /semantic/metrics`
- `list_metrics(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/metrics`
- `get_metric(object_id=None, metric_id=None)` -> `GET /semantic/metrics/{metric_id}`
- `update_metric(metric_id, display_name=None, description=None, payload=None)` -> `PUT /semantic/metrics/{metric_id}`
- `validate_metric(metric_id)` -> `POST /semantic/metrics/{metric_id}/validate`
- `activate_metric(metric_id)` -> `POST /semantic/metrics/{metric_id}/activate`
- `deprecate_metric(metric_id)` -> `POST /semantic/metrics/{metric_id}/deprecate`
- `publish_metric(metric_id)` -> `POST /semantic/metrics/{metric_id}/publish`
- `create_process_object(header, interface_contract, payload)` -> `POST /semantic/process-objects`
- `list_process_objects(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/process-objects`
- `get_process_object(process_contract_id)` -> `GET /semantic/process-objects/{process_contract_id}`
- `update_process_object(process_contract_id, display_name=None, description=None, interface_contract=None, payload=None)` -> `PUT /semantic/process-objects/{process_contract_id}`
- `validate_process_object(process_contract_id)` -> `POST /semantic/process-objects/{process_contract_id}/validate`
- `activate_process_object(process_contract_id)` -> `POST /semantic/process-objects/{process_contract_id}/activate`
- `deprecate_process_object(process_contract_id)` -> `POST /semantic/process-objects/{process_contract_id}/deprecate`
- `publish_process_object(process_contract_id)` -> `POST /semantic/process-objects/{process_contract_id}/publish`
- `create_dimension(header, interface_contract)` -> `POST /semantic/dimensions`
- `list_dimensions(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/dimensions`
- `get_dimension(dimension_contract_id)` -> `GET /semantic/dimensions/{dimension_contract_id}`
- `update_dimension(dimension_contract_id, display_name=None, description=None, interface_contract=None)` -> `PUT /semantic/dimensions/{dimension_contract_id}`
- `validate_dimension(dimension_contract_id)` -> `POST /semantic/dimensions/{dimension_contract_id}/validate`
- `activate_dimension(dimension_contract_id)` -> `POST /semantic/dimensions/{dimension_contract_id}/activate`
- `deprecate_dimension(dimension_contract_id)` -> `POST /semantic/dimensions/{dimension_contract_id}/deprecate`
- `publish_dimension(dimension_contract_id)` -> `POST /semantic/dimensions/{dimension_contract_id}/publish`
- `create_time_semantic(header)` -> `POST /semantic/time`
- `list_time_semantics(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/time`
- `get_time_semantic(time_contract_id)` -> `GET /semantic/time/{time_contract_id}`
- `update_time_semantic(time_contract_id, display_name=None, description=None, semantic_roles=None)` -> `PUT /semantic/time/{time_contract_id}`
- `validate_time_semantic(time_contract_id)` -> `POST /semantic/time/{time_contract_id}/validate`
- `activate_time_semantic(time_contract_id)` -> `POST /semantic/time/{time_contract_id}/activate`
- `deprecate_time_semantic(time_contract_id)` -> `POST /semantic/time/{time_contract_id}/deprecate`
- `publish_time_semantic(time_contract_id)` -> `POST /semantic/time/{time_contract_id}/publish`
- `create_enum_set(header, display_name, versions, description=None)` -> `POST /semantic/enum-sets`
- `list_enum_sets(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/enum-sets`
- `get_enum_set(enum_set_contract_id)` -> `GET /semantic/enum-sets/{enum_set_contract_id}`
- `update_enum_set(enum_set_contract_id, display_name=None, description=None, versions=None)` -> `PUT /semantic/enum-sets/{enum_set_contract_id}`
- `validate_enum_set(enum_set_contract_id)` -> `POST /semantic/enum-sets/{enum_set_contract_id}/validate`
- `activate_enum_set(enum_set_contract_id)` -> `POST /semantic/enum-sets/{enum_set_contract_id}/activate`
- `deprecate_enum_set(enum_set_contract_id)` -> `POST /semantic/enum-sets/{enum_set_contract_id}/deprecate`
- `publish_enum_set(enum_set_contract_id)` -> `POST /semantic/enum-sets/{enum_set_contract_id}/publish`
- `create_binding(header, interface_contract)` -> `POST /semantic/bindings`
- `list_bindings(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/bindings`
- `get_binding(object_id=None, binding_id=None)` -> `GET /semantic/bindings/{binding_id}`
- `update_binding(binding_id, display_name=None, description=None, interface_contract=None)` -> `PUT /semantic/bindings/{binding_id}`
- `validate_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/validate`
- `activate_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/activate`
- `deprecate_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/deprecate`
- `publish_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/publish`
- `create_compatibility_profile(profile_ref, profile_kind, subject_kind, subject_ref, schema_version="v1", requirement=None, capability=None)` -> `POST /compiler/compatibility-profiles`
- `list_compatibility_profiles(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /compiler/compatibility-profiles`
- `get_compatibility_profile(profile_id)` -> `GET /compiler/compatibility-profiles/{profile_id}`
- `update_compatibility_profile(profile_id, requirement=None, capability=None)` -> `PUT /compiler/compatibility-profiles/{profile_id}`
- `validate_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/validate`
- `activate_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/activate`
- `deprecate_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/deprecate`
- `publish_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/publish`

Boundary notes:

- these tools map directly to the existing HTTP families; they do not create MCP-only semantic abstractions
- list tools also accept the canonical `detail` query parameter so MCP callers can choose lightweight
  list items or backward-compatible full payloads
- `publish_*` marks the runtime visibility boundary; draft objects should not be treated as resolvable runtime inputs
- `key.*`, `grain.*`, `measure.*`, and `metric_input.*` remain payload values only, not CRUD families
- `key.*` is used in entity identity declarations and binding targets such as `identity_key` or
  `population_subject`; do not invent `create_key`-style flows
- `grain.*` is a contract value such as `metric.header.observation_grain_ref`; do not treat it as a
  semantic object family
- `metric_input.*` only appears inside metric bindings; it is not a creatable semantic object
- binding target kinds are stricter than semantic-role labels:
  `entity -> {identity_key, primary_time, stable_descriptor}`,
  `metric -> {population_subject, primary_time, metric_input}`,
  `process_object -> {population_subject, primary_time, analysis_window_anchor, process_context}`
- `create_binding()` and `update_binding()` accept the canonical binding payload, including
  `interface_contract.time_bindings[]`; there is no separate `create_time_binding` MCP tool because
  HTTP does not expose time bindings as an independent object family
- `time.semantic_roles` are time-object capability labels, not a 1:1 binding target map; only
  `primary_time` and `analysis_window_anchor` are direct binding target kinds today
- on publish failures, inspect `error.code`, `error.message`, and the preserved `error.detail` object before falling back to raw OpenAPI discovery

Minimal binding payload with `time_bindings[]`:

```json
{
  "header": {
    "binding_ref": "binding.metric.user_events",
    "display_name": "User Events Metric Binding",
    "binding_scope": "metric",
    "bound_object_ref": "metric.daily_active_users",
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "carrier_bindings": [
      {
        "binding_key": "primary",
        "source_object_ref": "obj_user_events",
        "carrier_kind": "table",
        "carrier_locator": "trino.analytics.user_events",
        "binding_role": "primary",
        "field_surfaces": [
          {"surface_ref": "field.user_id", "physical_name": "user_id"},
          {"surface_ref": "field.event_date", "physical_name": "event_date"}
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "population_subject", "target_key": "key.user_id"},
        "semantic_ref": "key.user_id",
        "surface_ref": "field.user_id"
      }
    ],
    "time_bindings": [
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
        "semantic_ref": "time.event_date",
        "resolution_kind": "date_column",
        "date_surface_ref": "field.event_date",
        "timezone_strategy": "session_consistent_naive"
      }
    ]
  }
}
```

## T8 Tools

Current source metadata and routing coverage:

- `list_sources()` -> `GET /sources`
- `register_source(source_type, display_name, connection=None, capabilities=None)` -> `POST /sources`
- `sync_source(source_id)` -> `POST /sources/{source_id}/sync`
- `get_source_objects(source_id, type=None, schema=None)` -> `GET /sources/{source_id}/objects`
- `get_source_object(source_id, object_id)` -> `GET /sources/{source_id}/objects/{object_id}`
- `resolve_routing(table_names, routing_intent=None)` -> `POST /routing/resolve`

Boundary notes:

- `get_source_objects()` reads synced source metadata from Factum's local store; it does not browse the live external catalog
- `get_source_object()` reads one synced source object from Factum's local store; it does not browse the live external catalog
- live catalog browse remains under `/sources/{source_id}/catalog/schemas` and `/sources/{source_id}/catalog/tables`, and is intentionally not wrapped by T8
- `sync_source()` preserves the current HTTP response body as-is; the MCP adapter does not invent a separate async status model
- `resolve_routing()` is a planning and debugging aid over the public routing contract; it does not expose a second routing schema
- tool `data` remains the raw Factum canonical body for both source and routing tools

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

Minimal semantic examples:

Create an entity:

```json
{
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
```

Create a metric:

```json
{
  "header": {
    "metric_ref": "metric.watch_time",
    "display_name": "Watch Time",
    "metric_family": "count_metric",
    "observed_entity_ref": "entity.user",
    "observation_grain_ref": "grain.user",
    "sample_kind": "numeric",
    "value_semantics": "count",
    "additivity": "additive",
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "watch_time",
      "semantics": "total watch time",
      "aggregation": "sum"
    }
  }
}
```

Create a binding:

```json
{
  "header": {
    "binding_ref": "binding.user_events_primary",
    "display_name": "User Events Binding",
    "binding_scope": "entity",
    "bound_object_ref": "entity.user",
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "carrier_bindings": [],
    "field_bindings": []
  }
}
```

Common failure examples:

- `get_session({"session_id":"sess_missing"})` -> `404` with `error.category = "not_found"`
- `query_session_state(...)` with an invalid body field or enum -> `422` with canonical `detail` preserved under `error.detail`
- `get_proposition_context(...)` for a missing or cross-session proposition -> `404` with the original Factum error message
- `observe(request=...)` with an invalid or incomplete body -> `422` with canonical `guidance` preserved under `error.guidance`; start with `error.guidance.examples`, then inspect `error.guidance.schema_url` or `error.guidance.contract_url`
- `publish_entity(...)` after the object is already published -> `422` with structured `error.detail`, `error.code = "publish_state_error"`, and a message explaining the draft-state violation
- `publish_binding(...)` before required semantic imports are published -> `422` with structured `error.detail` and a publish-specific validation code such as `reference_validation_error`
