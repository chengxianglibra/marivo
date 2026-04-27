# Engines

Engines represent analytics execution backends. In the current runtime, supported engine types are
`duckdb` and `trino` only. An engine owns runtime execution authority: connection details,
capabilities, and execution policy. Source-to-engine routing is governed by explicit mappings; see
[`mappings.md`](mappings.md) for the authority-to-execution projection contract.

This page documents the engine inventory surface only. `default_namespace` is an engine-local
fallback and never a source-to-engine projection contract. The public operator-facing
source-to-engine write/read contract is `/mappings`; no legacy source-engine binding surface is
part of the current external HTTP API.

`marivo.yaml` does not carry engine inventory. Engines are registered and managed only through the
HTTP API.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/engines` | Register an engine |
| `GET` | `/engines` | List engines |
| `GET` | `/engines/{engine_id}` | Get an engine |
| `PUT` | `/engines/{engine_id}` | Update an engine |
| `DELETE` | `/engines/{engine_id}` | Delete an engine |

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
  "auth": {
    "mode": "none"
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
| `auth` | object | no | Minimal execution auth contract for runtime username injection. Defaults to `{ "mode": "none" }`. |
| `default_namespace` | object \| null | no | Engine-local default catalog/schema fallback |
| `deployment_capabilities` | object | no | Deployment-scoped capability overrides. Omit fields you are not overriding so built-in engine defaults remain intact. |
| `policy` | object | no | Operator control-plane restrictions |

**Auth fields:**

| Field | Type | Description |
|-------|------|-------------|
| `mode` | string | `"none"` means the engine ignores session execution user data; `"username_only"` means runtime must resolve a Trino `user` value from `auth` plus the session `execution_identity` |
| `username_source` | string | Required when `mode = "username_only"`; `"session_user"` prefers `execution_identity.session_user`, while `"fixed"` always uses `fallback_username` |
| `fallback_username` | string | Optional fallback username for `username_source = "session_user"`; required fixed username for `username_source = "fixed"` |

Auth support matrix:

| Engine type | Supported auth modes | Notes |
|-------------|----------------------|-------|
| `duckdb` | `none` | DuckDB does not consume `execution_identity.session_user`; session user data is ignored rather than injected |
| `trino` | `none`, `username_only` | `username_only` resolves the Trino connection `user` from the session user or configured fallback |

Validation rules:

- `duckdb` only supports `auth = { "mode": "none" }`
- `trino` supports `mode = "none"` or `mode = "username_only"`
- when `mode = "none"`, `username_source` and `fallback_username` must be omitted
- when `mode = "username_only"`, `username_source` is required
- when `username_source = "fixed"`, `fallback_username` is required
- `fallback_username`, if present, is trimmed before persistence; blank-after-trim values are rejected

**DuckDB connection parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Recommended path to the `.duckdb` file |
| `database` | string | Supported alias for `path` |
| `db_path` | string | Supported alias for `path` |

**Trino connection parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `host` | string | Trino coordinator hostname |
| `port` | integer | Port (default: `8080`) |
| `user` | string | Low-level Trino connection parameter. For the target username-injection contract, configure `auth.mode = "username_only"` and pass the per-analysis user through `POST /sessions` `execution_identity.session_user` instead of treating static `connection.user` as the external contract. |
| `catalog` | string | Default catalog |
| `schema` | string | Default schema |
| `http_scheme` | string | `"http"` or `"https"` (default: `"http"`) |
| `session_properties` | object | Optional Trino session properties |

When `auth.mode = "username_only"` and `username_source = "session_user"`, runtime resolves the
final Trino `user` in this order:

1. `execution_identity.session_user` from the analysis session.
2. `auth.fallback_username`, when configured.
3. Fail with `session_user_missing` if neither value is available.

Typed intent request bodies do not accept a `session_user` override; create a new session when the
analysis needs a different execution user.

### Response

```json
{
  "engine_id": "eng_a1b2c3d4e5f6",
  "engine_type": "duckdb",
  "display_name": "Local DuckDB Engine",
  "connection": {"path": "/data/analytics.duckdb"},
  "auth": {
    "mode": "none"
  },
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
  "mappings": [],
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

The canonical response model is `EngineResponse`. `auth`, `default_namespace`,
`intrinsic_capabilities`, `deployment_capabilities`, `policy`, and `mappings` are structured
sub-objects; `intrinsic_capabilities`, `readiness_status`, and `failure_code` are read-only
derived fields.

When `auth.mode = "none"`, the response only returns `{ "mode": "none" }`. Username resolution
fields are surfaced only when username injection is configured for the engine.

`mappings` is a summary of mapping objects that target this engine. It is not embedded engine
configuration and does not let an engine carry source authority identity or catalog projection.

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

Array of `EngineResponse` objects.

---

## Get Engine

```
GET /engines/{engine_id}
```

### Response

Returns `EngineResponse`.

The detail surface includes a `mappings` array summarizing the mappings that currently target the
engine. Each entry exposes `mapping_id`, `source_id`, `status`, `readiness_status`,
`failure_code`, and the mapping's `catalog_mappings`.

---

## Update Engine

```
PUT /engines/{engine_id}
```

Updates mutable engine inventory fields. `engine_type` is immutable after creation; register a new
engine when the execution adapter type changes.

### Request Body

All fields are optional; only provided fields are updated.

```json
{
  "display_name": "Updated Trino Engine",
  "connection": {
    "host": "trino.example.internal",
    "port": 8080,
    "catalog": "hive",
    "schema": "analytics"
  },
  "auth": {
    "mode": "username_only",
    "username_source": "session_user",
    "fallback_username": "marivo"
  },
  "default_namespace": {
    "catalog": "hive",
    "schema": "analytics"
  },
  "deployment_capabilities": {
    "supported_step_types": ["observe", "compare"]
  },
  "policy": {
    "allowed_step_types": ["observe", "compare"],
    "required_policy_support": []
  }
}
```

The same auth, connection, default namespace, deployment capabilities, and policy validation rules
used by `POST /engines` apply to update requests. Updating the connection may also refresh the
derived default namespace when no explicit `default_namespace` is provided.

### Response

Returns the updated `EngineResponse`.

---

## Delete Engine

```
DELETE /engines/{engine_id}
```

Deletes an engine only when no source-to-engine mappings depend on it. If mappings still target the
engine, the API returns `409 Conflict` with dependency identifiers.

### Response

```json
{
  "status": "deleted",
  "engine_id": "eng_a1b2c3d4e5f6"
}
```

### Dependency Conflict

```json
{
  "detail": {
    "message": "Cannot delete engine: 1 mapping(s) depend on it",
    "dependencies": ["map_a1b2c3d4e5f6"]
  }
}
```

---

## Query Routing Resolution

```
POST /routing/resolve
```

Resolves a set of table names to a single engine capable of querying all of them. This endpoint is useful for debugging routing decisions or for agents that want to understand which engine will be used before submitting a step.

The router selects the highest-priority engine that has active mappings to sources containing all the specified tables.
Routing resolves synced tables by explicit `source_object.authority_locator`. Callers may pass:

- full authority locator FQN: `catalog.schema.table`
- authority schema + table: `schema.table`
- short table name: `table`

If a partial form matches multiple synced tables, the request fails and the caller must retry with
the full authority locator FQN.

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
| `table_names` | array[string] | yes | Table names to resolve. Use synced full authority-locator FQNs when available; short native names remain supported only when unambiguous. |
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
  "qualified_names": {
    "events.user_video_watch": "iceberg.events.user_video_watch",
    "dimensions.video_metadata": "iceberg.dimensions.video_metadata"
  },
  "selection_reason": "Highest priority mapping covering all requested tables",
  "routing_detail": {
    "resolution_status": "resolved",
    "selected_mapping_ids": ["map_..."],
    "readiness_blockers": [],
    "execution_locators": {
      "events.user_video_watch": {
        "catalog": "iceberg",
        "schema": "events",
        "table": "user_video_watch",
        "mapping_id": "map_...",
        "authority_catalog": "lakehouse",
        "execution_catalog": "iceberg",
        "default_schema_applied": false,
        "readiness_blockers": [],
        "authority_locator": {
          "catalog": "lakehouse",
          "schema": "events",
          "table": "user_video_watch"
        }
      },
      "dimensions.video_metadata": {
        "catalog": "iceberg",
        "schema": "dimensions",
        "table": "video_metadata",
        "mapping_id": "map_...",
        "authority_catalog": "lakehouse",
        "execution_catalog": "iceberg",
        "default_schema_applied": false,
        "readiness_blockers": [],
        "authority_locator": {
          "catalog": "lakehouse",
          "schema": "dimensions",
          "table": "video_metadata"
        }
      }
    }
  },
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
  "qualified_names": {},
  "selection_reason": "No single engine has mappings covering all requested tables",
  "routing_detail": {
    "resolution_status": "no_common_engine",
    "unresolved_tables": ["events.user_video_watch", "other_source.some_table"],
    "sources": {
      "src_events": {
        "candidate_engine_ids": ["eng_trino"],
        "ready_mapping_ids": ["map_events"],
        "failed_mappings": [],
        "readiness_blockers": []
      },
      "src_other": {
        "candidate_engine_ids": ["eng_duckdb"],
        "ready_mapping_ids": ["map_other"],
        "failed_mappings": [],
        "readiness_blockers": []
      }
    },
    "candidates": [
      {
        "engine_id": "eng_trino",
        "eligible": false,
        "covered_sources": ["src_events"],
        "missing_sources": ["src_other"],
        "mapping_ids": ["map_events"]
      },
      {
        "engine_id": "eng_duckdb",
        "eligible": false,
        "covered_sources": ["src_other"],
        "missing_sources": ["src_events"],
        "mapping_ids": ["map_other"]
      }
    ],
    "selected_mapping_ids": [],
    "execution_locators": {},
    "readiness_blockers": []
  },
  "capability_profile": null
}
```

Routing failures now stay on the same `200` response contract with `resolved=false`. Inspect
`routing_detail.resolution_status`, `routing_detail.readiness_blockers`, `routing_detail.sources`,
and `routing_detail.candidates` to understand whether the blocker came from missing mappings,
dependency readiness, or lack of a common eligible engine.
