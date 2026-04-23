# Engines

Engines represent analytics execution backends. In the current runtime, supported engine types are
`duckdb` and `trino` only. Source-to-engine routing is governed by explicit mappings; see
[`mappings.md`](mappings.md) for the authority-to-execution projection contract.

This page documents the engine inventory surface only. Legacy `binding` terminology may still
appear in some internal/admin read surfaces, but the public operator-facing source-to-engine write
contract is `/mappings`, not `/bindings`.

When `marivo.yaml` includes a Trino engine, Marivo validates the optional `trino` Python package at
startup and fails fast if it is missing. Install Trino support with `pip install -e .[trino]`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/engines` | Register an engine |
| `GET` | `/engines` | List engines |
| `GET` | `/engines/{engine_id}` | Get an engine |

---

## Register Engine

```
POST /engines
```

Registers an analytics engine. The engine type determines which adapter implementation is used.

### Request Body

```json
{
  "engine_type": "duckdb",
  "display_name": "Local DuckDB Engine",
  "connection": {
    "path": "/data/analytics.duckdb"
  },
  "default_namespace": {
    "catalog": null,
    "schema": null
  },
  "deployment_capabilities": {},
  "policy": {
    "allowed_step_types": [],
    "required_policy_support": []
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `engine_type` | string | yes | `"duckdb"` or `"trino"` |
| `display_name` | string | yes | Human-readable name |
| `connection` | object | no | Engine-specific connection parameters (default: `{}`) |
| `default_namespace` | object \| null | no | Engine-local default catalog/schema fallback |
| `deployment_capabilities` | object | no | Deployment-scoped capability overrides. Omit fields you are not overriding so built-in engine defaults remain intact. |
| `policy` | object | no | Operator control-plane restrictions |

**DuckDB connection parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `db_path` | string | Path to the `.duckdb` file |

**Trino connection parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `host` | string | Trino coordinator hostname |
| `port` | integer | Port (default: `8080`) |
| `user` | string | Trino user |
| `catalog` | string | Default catalog |
| `schema` | string | Default schema |
| `http_scheme` | string | `"http"` or `"https"` (default: `"http"`) |
| `session_properties` | object | Optional Trino session properties |

### Response

```json
{
  "engine_id": "eng_a1b2c3d4e5f6",
  "engine_type": "duckdb",
  "display_name": "Local DuckDB Engine",
  "connection": {"path": "/data/analytics.duckdb"},
  "default_namespace": {
    "catalog": null,
    "schema": null
  },
  "intrinsic_capabilities": {
    "materialization_support": "temporary_table",
    "performance_class": "embedded",
    "federation_support": "none"
  },
  "deployment_capabilities": {},
  "policy": {
    "allowed_step_types": [],
    "required_policy_support": []
  },
  "status": "active",
  "readiness_status": "ready",
  "failure_code": null,
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

`readiness_status` is derived from engine validation. This check is configuration-only in the
current runtime: it validates engine type, connection shape, `default_namespace`, and the value
stability of `deployment_capabilities` / `policy`. It does not run online connectivity probes or
issue `SELECT 1`. `failure_code` exposes the current blocker, such as `engine_invalid_connection`
or `engine_invalid_namespace`.

---

## List Engines

```
GET /engines
```

Returns all registered engines.

### Response

Array of engine objects.

---

## Get Engine

```
GET /engines/{engine_id}
```

---

## Query Routing Resolution

```
POST /routing/resolve
```

Resolves a set of table names to a single engine capable of querying all of them. This endpoint is useful for debugging routing decisions or for agents that want to understand which engine will be used before submitting a step.

The router selects the highest-priority engine that has active bindings to sources containing all the specified tables.
Routing accepts either a synced table's full `source_objects.fqn` or its short `native_name`.
When both could match, full FQN wins; if a short name matches multiple synced tables, the request
fails and the caller must retry with a full FQN.

### Request Body

```json
{
  "table_names": ["events.user_video_watch", "dimensions.video_metadata"],
  "routing_intent": {
    "step_type": "aggregate_query",
    "metric_names": [],
    "requested_dimensions": ["device_type"],
    "compatible_dimensions": ["device_type", "region"],
    "legal_grains": ["daily"],
    "policy_hints": ["aggregate_only"]
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `table_names` | array[string] | yes | Table names to resolve. Use synced full FQNs when available; short native names remain supported only when unambiguous. |
| `routing_intent` | object | no | Hints to guide engine selection |

**Routing intent fields:**

| Field | Type | Description |
|-------|------|-------------|
| `step_type` | string | The step type being planned |
| `metric_names` | array[string] | Metric names being queried |
| `requested_dimensions` | array[string] | Dimensions requested in the step |
| `compatible_dimensions` | array[string] | Dimensions supported by the metric |
| `legal_grains` | array[string] | Acceptable time grains |
| `policy_hints` | array[string] | Governance policy hints |

### Response

```json
{
  "resolved": true,
  "table_names": ["events.user_video_watch", "dimensions.video_metadata"],
  "engine": {
    "engine_id": "eng_...",
    "engine_type": "trino",
    "display_name": "Trino Cluster"
  },
  "qualified_names": [
    "iceberg.events.user_video_watch",
    "iceberg.dimensions.video_metadata"
  ],
  "selection_reason": "Highest priority binding covering all requested tables",
  "routing_detail": "Resolved via binding bind_... with namespace {catalog: iceberg}",
  "capability_profile": {
    "dialect": "trino",
    "supports_federation": true
  }
}
```

When routing fails (no engine covers all tables):

```json
{
  "resolved": false,
  "table_names": ["events.user_video_watch", "other_source.some_table"],
  "engine": null,
  "qualified_names": [],
  "selection_reason": "No single engine has bindings covering all requested tables",
  "routing_detail": "events.user_video_watch → src_abc (eng_xyz); other_source.some_table → unresolved",
  "capability_profile": null
}
```
