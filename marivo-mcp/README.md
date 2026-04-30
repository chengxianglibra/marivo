# marivo-mcp

External MCP adapter for Marivo.

This subproject keeps the MCP runtime separate from Marivo's core HTTP service.
Marivo remains HTTP-only. The MCP server is a client-side adapter over the
canonical HTTP API.

The MCP tool inventory is a convenience layer for agent clients, not a second
semantic contract. Tool names, parameters, schemas, resources, and examples
must map back to canonical HTTP paths and payload fields. When the HTTP
contract changes, update MCP descriptions to point at the new target-state
payload instead of introducing MCP-only object semantics.

## Supported Scope

Validated P0 scope provides:

- a standalone Python package
- `stdio` and Streamable HTTP MCP server entrypoints
- environment-driven configuration loading
- a shared HTTP client with uniform result envelopes
- discovery and catalog tools that proxy canonical Marivo HTTP endpoints
- TTL-based caching for OpenAPI discovery tools
- session lifecycle and canonical state/context investigation tools
- typed intent tools that map directly to Marivo's `/sessions/{id}/intents/*` routes
- semantic-layer lifecycle tools for all public object families
- read-only MCP resources that mirror canonical Marivo HTTP surfaces

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
- `marivo://catalog/summary`
- `marivo://sources/{source_id}/objects`
- `marivo://sources/{source_id}/objects/{object_id}`
- `marivo://server/config`

The executable support inventory lives in `marivo_mcp.inventory`. Tests use that
module as the machine-readable source of truth for registration and HTTP mapping
checks. Inventory descriptions should name the mirrored HTTP route and avoid
claiming MCP ownership of semantic lifecycle, grounding, relationship, or
compiler-profile contracts.

## Environment

The server reads these environment variables:

- `MARIVO_MODE` (optional, default `auto`; `remote` requires `MARIVO_BASE_URL`)
- `MARIVO_BASE_URL` (optional; when present in `auto`, selects remote explicit connection)
- `MARIVO_API_TOKEN` (optional)
- `MARIVO_WORKSPACE_ROOT` (optional for local auto-managed `stdio`; required for local HTTP MCP)
- `MARIVO_LOCAL_HOST` (optional, default `127.0.0.1`)
- `MARIVO_LOCAL_PORT` (optional, default `0`)
- `MARIVO_START_TIMEOUT_MS` (optional, default `15000`)
- `MARIVO_HEALTHCHECK_TIMEOUT_MS` (optional, default `2000`)
- `MARIVO_MCP_TRANSPORT` (optional, default `stdio`)
- `MARIVO_TIMEOUT_MS` (optional, default `600000`)
- `MARIVO_OPENAPI_CACHE_TTL_SEC` (optional, default `300`)
- `MARIVO_DEFAULT_SOURCE_ID` (optional)
- `MARIVO_MCP_HOST` (optional, default `127.0.0.1`)
- `MARIVO_MCP_PORT` (optional, default `8000`)
- `MARIVO_MCP_STREAMABLE_HTTP_PATH` (optional, default `/mcp`)
- `MARIVO_MCP_STATELESS_HTTP` (optional, default `true`)
- `MARIVO_MCP_JSON_RESPONSE` (optional, default `true`)

Remote explicit connection failures never fall back to a local runtime. Local
auto-managed mode requires a workspace root and resolves the endpoint from
`.marivo/runtime.json`.

## Install

```bash
cd marivo-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

`marivo-mcp` is installed as an independent adapter package. Remote explicit
mode only requires the adapter environment and a reachable Marivo HTTP service;
it must not rely on `PYTHONPATH` pointing at the Marivo repository root.

## Client Setup

After installing the package in the MCP server host environment, register
Marivo with an MCP client through one of three supported paths:

- local auto-managed `stdio`
- remote explicit `stdio`
- remote explicit Streamable HTTP

`marivo-mcp` is the MCP server process. It connects to Marivo through the
canonical HTTP API; the agent does not connect to Marivo directly.

Generate a minimal config snippet:

```bash
marivo-mcp init --print-config
marivo-mcp init --mode local --print-config
marivo-mcp init --mode remote --base-url http://127.0.0.1:8000 --api-token "$MARIVO_API_TOKEN" --print-config
marivo-mcp init --transport streamable-http --mode remote --base-url http://127.0.0.1:8000 --print-config
```

Generate a Codex TOML snippet or write the repo-local Codex config:

```bash
marivo-mcp init --client codex --print-config
marivo-mcp init --client codex --write
```

`--write --client codex` updates `.codex/config.toml` in the current working
directory by replacing only `[mcp_servers.marivo]`. Other Codex settings and
other MCP server registrations are preserved. Use `--config-path` to write a
specific Codex config file.

Local auto-managed `stdio` MCP:

```json
{
  "mcpServers": {
    "marivo": {
      "command": "/absolute/path/to/marivo/marivo-mcp/.venv/bin/marivo-mcp",
      "env": {
        "MARIVO_MODE": "local",
        "MARIVO_WORKSPACE_ROOT": "/absolute/path/to/workspace"
      }
    }
  }
}
```

This starts `marivo-mcp` as a local subprocess using `stdio`. The adapter
reuses a healthy workspace runtime when `.marivo/runtime.json` is valid, or
starts one through `marivo serve-local`.

Remote explicit `stdio` MCP:

```json
{
  "mcpServers": {
    "marivo": {
      "command": "/absolute/path/to/marivo/marivo-mcp/.venv/bin/marivo-mcp",
      "env": {
        "MARIVO_MODE": "remote",
        "MARIVO_BASE_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

Codex `stdio` MCP:

```toml
[mcp_servers.marivo]
command = "marivo-mcp"
env = { MARIVO_MODE = "local", MARIVO_WORKSPACE_ROOT = "/absolute/path/to/workspace" }
```

Remote explicit Streamable HTTP MCP (`streamable-http`, sometimes called
`http-stream`) is the default HTTP transport release path. The HTTP MCP server
is a separate process; the client points at that process by URL.

1. Start the MCP HTTP server:

```bash
cd marivo-mcp
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/marivo-mcp-http
```

2. Point the MCP client at the server URL:

```json
{
  "mcpServers": {
    "marivo": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

If you need a different bind host, port, or path, set
`MARIVO_MCP_HOST`, `MARIVO_MCP_PORT`, and `MARIVO_MCP_STREAMABLE_HTTP_PATH`
before starting the HTTP transport.

Local auto-managed HTTP MCP is guarded. Because Streamable HTTP does not carry
client workspace metadata, local HTTP mode requires an explicit workspace root:

```bash
MARIVO_MODE=local \
MARIVO_WORKSPACE_ROOT=/absolute/path/to/workspace \
.venv/bin/marivo-mcp-http
```

Startup fails instead of silently using an arbitrary cwd when the workspace root
is missing, points at a system directory, is not writable, or `marivo
serve-local` is not available.

## Run

```bash
MARIVO_MODE=local MARIVO_WORKSPACE_ROOT=/absolute/path/to/workspace marivo-mcp
MARIVO_MODE=remote MARIVO_BASE_URL=http://127.0.0.1:8000 marivo-mcp
```

The entrypoint starts a `stdio` MCP server. If the Python MCP SDK is not
installed, startup fails with an explicit dependency error. Remote explicit
connection failures fail closed and never fall back to a local runtime.

Run the Streamable HTTP transport:

```bash
MARIVO_MODE=remote MARIVO_BASE_URL=http://127.0.0.1:8000 marivo-mcp-http
```

Or select it via the shared entrypoint:

```bash
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
MARIVO_MCP_TRANSPORT=streamable-http \
marivo-mcp
```

With the current defaults and the official Python MCP SDK, clients should
connect to `http://127.0.0.1:8000/mcp`.

## Validation

Run the offline MCP regression checks from the repository root:

```bash
.venv/bin/pytest \
  tests/test_marivo_mcp_config.py \
  tests/test_marivo_mcp_target_resolution.py \
  tests/test_marivo_mcp_transport.py \
  tests/test_marivo_mcp_resources.py \
  tests/test_marivo_mcp_inventory.py \
  tests/test_marivo_mcp_smoke.py
```

Optional live smoke through target resolution. The smoke command first resolves
the Marivo target, then runs the same minimal HTTP workflow against the
resolved endpoint.

Remote explicit `stdio` MCP:

```bash
cd marivo-mcp
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/marivo-mcp-smoke
```

Remote explicit Streamable HTTP MCP:

```bash
cd marivo-mcp
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/marivo-mcp-http

MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/marivo-mcp-smoke
```

Local auto-managed `stdio` MCP:

```bash
cd marivo-mcp
MARIVO_MODE=local \
MARIVO_WORKSPACE_ROOT=/absolute/path/to/workspace \
.venv/bin/marivo-mcp-smoke
```

The live smoke checks:

- target resolution metadata in the summary
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

- `marivo://catalog/summary`
- `marivo://sessions/{session_id}/state`
- `marivo://sessions/{session_id}/propositions/{proposition_id}/context`
- `marivo://semantic/{family}`
- `marivo://sources/{source_id}/objects`
- `marivo://sources/{source_id}/objects/{object_id}`
- `marivo://server/config`

Resource rules:

- resources return the raw canonical JSON body for the mirrored HTTP surface
- resources do not wrap responses in the tool envelope
- `marivo://catalog/summary` is a fixed aggregate snapshot over canonical read
  surfaces; it does not become a search API
- `marivo://sources/{source_id}/objects` reads synced metadata only, not live
  external catalog browse endpoints
- `marivo://sources/{source_id}/objects/{object_id}` reads one synced source
  object detail only, not live external catalog browse endpoints
- `marivo://semantic/{family}` only supports public semantic families
- **MCP resources do not support query parameters** (e.g., `{?status}`, `{?type}`).
  The HTTP endpoints remain the authoritative surface for filtering; MCP resources
  simply mirror the canonical read surface. Use HTTP tools for parameterized queries.

## Known Limitations

- Marivo remains HTTP-only; this adapter is a separate client-side process.
- MCP resources mirror canonical HTTP reads and do not become a second source
  of evidence.
- MCP tool schemas are adapter-facing projections of HTTP request payloads; they
  must not define alternate semantic object models, physical binding semantics,
  or relationship/profile repair flows.
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
    "marivo_path": "/health",
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

Typed semantic `422` responses preserve Marivo's canonical `detail` and
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
second schema or reinterpret Marivo object families.

OpenAPI discovery responses are cached inside the MCP adapter using
`MARIVO_OPENAPI_CACHE_TTL_SEC`:

- cache scope is limited to `list_openapi_paths`, `get_openapi_schema`,
  `get_openapi_fragment`, and `get_openapi_path_fragment`
- only successful responses are cached
- `MARIVO_OPENAPI_CACHE_TTL_SEC=0` disables the cache
- once the TTL expires, the adapter re-reads Marivo's OpenAPI surface and
  surfaces the latest `revision`

## T5 Tools

Current session / state / context coverage:

- `create_session(goal, budget=None, policy=None)` -> `POST /sessions`
- `get_session(session_id)` -> `GET /sessions/{session_id}`
- `terminate_session(session_id, terminal_reason="user_closed")` -> `POST /sessions/{session_id}/terminate`
- `get_session_state(session_id, metric=None, entity=None, proposition_type=None, origin_kind=None, assessment_presence=None, assessment_status=None, has_blocking_gaps=None, limit=None, page_token=None)` -> `GET /sessions/{session_id}/state`
- `query_session_state(session_id, metric=None, entity=None, slice=None, proposition_types=None, origin_kinds=None, assessment_presence=None, assessment_statuses=None, has_blocking_gaps=None, limit=None, page_token=None)` -> `POST /sessions/{session_id}/state/query`
- `get_proposition_context(session_id, proposition_id)` -> `GET /sessions/{session_id}/propositions/{proposition_id}/context`

Boundary notes:

- `create_session()` only accepts canonical session-root fields; execution filters belong in typed
  intent requests such as `scope.constraints` or `scope.predicate`
- `terminate_session()` is the explicit lifecycle close-out step for agent-driven investigations once no further writes are needed
- `get_session_state()` mirrors the `GET /state` query contract and intentionally does not support `slice`
- use `query_session_state()` when `slice` or a structured state query body is required
- `get_session_state()` is proposition-centered canonical state, not a session step or artifact inventory
- a successful `observe` may still leave `get_session_state()` empty when no externally visible proposition has been seeded yet; keep following the returned artifact or typed refs instead of treating empty state as execution failure
- `get_proposition_context()` reads canonical proposition closure, not runtime status
- tool `data` remains the raw Marivo canonical body; the MCP adapter only wraps it in the shared envelope

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
  where `left` / `right` may include side-level `calendar_policy_ref` in the same shape accepted by scalar `observe`
- `diagnose(session_id, metric, time_scope, candidate_dimensions, scope=None, detect_split_by=None, profile="auto", sensitivity="balanced", candidate_limit=None, followup_limit=3, decomposition_limit=5)` -> `POST /sessions/{session_id}/intents/diagnose`
- `validate(session_id, metric, left, right, sample_kind=None, hypothesis=None, method=None)` -> `POST /sessions/{session_id}/intents/validate`

Boundary notes:

- these are path-discriminated intent tools; do not add an extra `intent` or `step_type` field to the request body
- MCP parameter names intentionally reuse the canonical HTTP request field names
- every typed intent tool exposes a top-level `session_id` used to fill the canonical HTTP path, while the remaining MCP parameters map directly to the canonical HTTP request body fields
- nested MCP fields such as `time_scope`, `left_ref`, `right_ref`, `source_ref`, `left`, `right`, and `hypothesis` are structured objects in the MCP contract, not JSON-encoded strings
- `observe.time_scope` must be a canonical object, not a shorthand string; for a range use `{"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`
- `compare.left_ref` and `compare.right_ref` expose the required observe ref shape inline:
  `{"step_id":"step_obs_current","step_type":"observe"}`; `session_id` is optional and defaults to the path session.
- `detect.time_scope` exposes the required single-window shape inline:
  `{"mode":"single_window","grain":"day","current":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}}`.
- `decompose.compare_ref` exposes the required compare ref shape inline:
  `{"step_id":"step_compare_1","step_type":"compare"}`.
- `observe` keeps canonical guardrails from `ObserveRequest`: `granularity` and `dimensions` are mutually exclusive, and both are only valid when `result_mode="standard"`
- typed intent `metric` parameters must use canonical semantic refs such as `metric.watch_time`; bare names like `watch_time` are rejected
- inspect the MCP tool schema first for required nested fields; use `get_openapi_fragment(...)` only when you need the full route-scoped HTTP contract
- tool `data` remains the raw Marivo success body; the adapter does not derive a new evidence summary
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
- `semantic_batch(request)` -> `POST /semantic/batch`
- `list_grains()` -> `GET /semantic/grains`
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
- Binding tools mirror the legacy/diagnostic compatibility `/semantic/bindings` route. Target-state
  authoring uses entity payload fields and `entity.interface_contract.binding`.
- `create_binding(header, interface_contract)` -> `POST /semantic/bindings`
- `list_bindings(status=None, lifecycle_status=None, readiness_status=None, detail=None)` -> `GET /semantic/bindings`
- `get_binding(object_id=None, binding_id=None)` -> `GET /semantic/bindings/{binding_id}`
- `update_binding(binding_id, display_name=None, description=None, interface_contract=None)` -> `PUT /semantic/bindings/{binding_id}`
- `validate_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/validate`
- `activate_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/activate`
- `deprecate_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/deprecate`
- `publish_binding(binding_id)` -> `POST /semantic/bindings/{binding_id}/publish`
- `create_relationship(relationship_ref, left_entity_ref, right_entity_ref, key_alignment, cardinality, display_name=None, description=None, time_alignment=None, grain_compatibility=None, snapshot_effective_window_alignment=None)` -> `POST /semantic/relationships`
- `list_relationships(status=None, lifecycle_status=None, readiness_status=None, detail=None, left_entity_ref=None, right_entity_ref=None)` -> `GET /semantic/relationships`
- `get_relationship(relationship_id)` -> `GET /semantic/relationships/{relationship_id}`
- `update_relationship(relationship_id, display_name=None, description=None, key_alignment=None, time_alignment=None, cardinality=None, grain_compatibility=None, snapshot_effective_window_alignment=None)` -> `PUT /semantic/relationships/{relationship_id}`
- `validate_relationship(relationship_id)` -> `POST /semantic/relationships/{relationship_id}/validate`
- `activate_relationship(relationship_id)` -> `POST /semantic/relationships/{relationship_id}/activate`
- `deprecate_relationship(relationship_id)` -> `POST /semantic/relationships/{relationship_id}/deprecate`
- `publish_relationship(relationship_id)` -> `POST /semantic/relationships/{relationship_id}/publish`
- `create_compatibility_profile(profile_ref, profile_kind, subject_kind, subject_ref, schema_version="v1", requirement=None, capability=None)` -> `POST /compiler/compatibility-profiles`
- `list_compatibility_profiles(status=None, lifecycle_status=None, readiness_status=None, detail=None, subject_kind=None, subject_ref=None, left_entity_ref=None, right_entity_ref=None)` -> `GET /compiler/compatibility-profiles`
- `get_compatibility_profile(profile_id)` -> `GET /compiler/compatibility-profiles/{profile_id}`
- `update_compatibility_profile(profile_id, requirement=None, capability=None)` -> `PUT /compiler/compatibility-profiles/{profile_id}`
- `validate_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/validate`
- `activate_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/activate`
- `deprecate_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/deprecate`
- `publish_compatibility_profile(profile_id)` -> `POST /compiler/compatibility-profiles/{profile_id}/publish`

Boundary notes:

- these tools map directly to the existing HTTP families; they do not create MCP-only semantic abstractions
- HTTP remains the contract authority. For semantic authoring, treat MCP tool parameters as a
  thin projection of the target-state HTTP payload: path ids fill URL templates, and all other
  fields become the HTTP request body or query string without reinterpretation.
- entity is the first-class grounding object. Entity payloads declare field refs and identity/time
  surfaces; metric, process, dimension, time, and predicate payloads reference those entity fields
  and semantic refs instead of carrying physical locators directly.
- domain discovery and repair work should map to the canonical read/write endpoints: use
  `search_catalog` and semantic list/get tools for discovery, relationship tools for
  entity-to-entity alignment, and compiler profile tools for compatibility/profile repair.
  Do not model those as MCP-owned discovery, join-planning, or repair contracts.
- list tools also accept the canonical `detail` query parameter so MCP callers can choose lightweight
  list items or backward-compatible full payloads
- `publish_*` marks the runtime visibility boundary; draft objects should not be treated as resolvable runtime inputs
- `key.*`, `grain.*`, `measure.*`, and `metric_input.*` remain payload values only, not CRUD families
- entity fields are the only physical grounding owner. `dimension.*` uses
  `interface_contract.source_field_ref`, `time.*` uses `header.source_field_ref`, and predicate atoms
  use `target_ref` to reference `entity.<entity>.field.<field>` when they need data fields.
- do not pass `physical_column`, carrier locators, or binding target fields to dimension/time/predicate
  create/update tools; those physical locators belong on entity fields or entity bindings.
- `key.*` is used in entity identity declarations and binding targets such as `identity_key` or
  `population_subject`; do not invent `create_key`-style flows
- `grain.*` is a contract value such as `metric.header.observation_grain_ref`; do not treat it as a
  semantic object family
- `metric_input.*` only appears in legacy metric binding records; new metric inputs should reference
  entity fields from the metric contract and resolve through entity grounding
- public binding authoring is entity-only. Metric/process physical bindings are legacy read/history
  records and should not be created by agents.
- compiler and step metadata may expose `resolved_entity_field_refs` and
  `resolved_entity_field_sources`; use these snapshots to audit which entity fields, entity
  revisions, and physical locators grounded the analysis result.
- compiler and step metadata may also expose `resolved_relationship_refs` and
  `resolved_relationship_sources` for cross-entity analysis audits.
- relationships/profiles must stay semantic: no raw SQL, optimizer hints, arbitrary join graph, CTE
  shape, or generic rule-engine fields
- binding target kinds are stricter than semantic-role labels; for public authoring use
  `entity -> {identity_key, primary_time, stable_descriptor}`
- `/semantic/bindings` remains a legacy/diagnostic compatibility surface. Target-state authoring
  grounds physical data on `entity.interface_contract.fields[]` and
  `entity.interface_contract.binding`, not on standalone carrier/surface binding payloads.
- `create_binding()` and `update_binding()` are retained for existing typed-binding records; do not
  use them to author metric/process physical bindings or new carrier/surface mappings.
- `time.semantic_roles` are time-object capability labels, not a 1:1 binding target map; only
  `primary_time` and `analysis_window_anchor` are direct binding target kinds today
- on publish failures, inspect `error.code`, `error.message`, and the preserved `error.detail` object before falling back to raw OpenAPI discovery

Minimal entity payload with physical grounding:

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
    },
    "primary_time_ref": "time.event_date",
    "fields": [
      {"field_ref": "field.user_id", "value_type": "string", "physical_column": "user_id"},
      {"field_ref": "field.event_date", "value_type": "date", "physical_column": "event_date"}
    ],
    "binding": {
      "source_object_ref": "obj_user_events",
      "source_object_fqn": "analytics.user_events",
      "carrier_kind": "table"
    }
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

- `get_source_objects()` reads synced source metadata from Marivo's local store; it does not browse the live external catalog
- `get_source_object()` reads one synced source object from Marivo's local store; it does not browse the live external catalog
- live catalog browse remains under `/sources/{source_id}/catalog/schemas` and `/sources/{source_id}/catalog/tables`, and is intentionally not wrapped by T8
- `sync_source()` preserves the current HTTP response body as-is; the MCP adapter does not invent a separate async status model
- `resolve_routing()` is a planning and debugging aid over the public routing contract; it does not expose a second routing schema
- tool `data` remains the raw Marivo canonical body for both source and routing tools
- create tools for metrics, enum sets, and bindings expose MCP-side Pydantic schemas for common
  authoring mistakes: enum headers require `enum_set_ref`, binding time surface declarations require
  `surface_ref`, time bindings use `*_surface_ref`, and metric input mappings use
  `semantic_ref=metric_input.<slot>`.
- `preview_source_table(..., filters={...})` forwards safe equality filters to the HTTP preview API;
  raw SQL predicates are not accepted.

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
    },
    "fields": [
      {
        "field_ref": "entity.user.field.user_id",
        "semantic_role": "identity",
        "value_type": "string"
      },
      {
        "field_ref": "entity.user.field.event_date",
        "semantic_role": "event_time",
        "value_type": "date"
      }
    ]
  }
}
```

Reference an entity field from a dimension:

```json
{
  "header": {
    "dimension_ref": "dimension.country",
    "display_name": "Country",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "source_field_ref": "entity.user.field.country",
    "value_domain": {"kind": "string"},
    "grouping": {"kind": "categorical"}
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
    "additivity_constraints": {"dimension_policy": "all", "time_axis_policy": "additive"},
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "watch_time",
      "input_field_ref": "entity.user.field.watch_time",
      "semantics": "total watch time",
      "aggregation": "count"
    }
  }
}
```

Create an entity with physical grounding:

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
    },
    "fields": [
      {"field_ref": "field.user_id", "value_type": "string", "physical_column": "user_id"},
      {"field_ref": "field.watch_time", "value_type": "number", "physical_column": "watch_time"}
    ],
    "binding": {
      "source_object_ref": "obj_user_events",
      "source_object_fqn": "analytics.user_events",
      "carrier_kind": "table"
    }
  }
}
```

Common failure examples:

- `get_session({"session_id":"sess_missing"})` -> `404` with `error.category = "not_found"`
- `query_session_state(...)` with an invalid body field or enum -> `422` with canonical `detail` preserved under `error.detail`
- `get_proposition_context(...)` for a missing or cross-session proposition -> `404` with the original Marivo error message
- `observe(request=...)` with an invalid or incomplete body -> `422` with canonical `guidance` preserved under `error.guidance`; start with `error.guidance.examples`, then inspect `error.guidance.schema_url` or `error.guidance.contract_url`
- `publish_entity(...)` after the object is already published -> `422` with structured `error.detail`, `error.code = "publish_state_error"`, and a message explaining the draft-state violation
- `publish_binding(...)` before required semantic imports are published -> `422` with structured `error.detail` and a publish-specific validation code such as `reference_validation_error`
