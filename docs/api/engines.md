# Engines & Bindings

Engines represent analytics execution backends (DuckDB, Trino). Source-engine bindings link a source to an engine with a priority for routing. The QueryRouter uses bindings to resolve which engine should execute a query against a given set of tables.

When `factum.yaml` includes a Trino engine, Factum validates the optional `trino` Python package at
startup and fails fast if it is missing. Install Trino support with `pip install -e .[trino]`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/engines` | Register an engine |
| `GET` | `/engines` | List engines |
| `GET` | `/engines/{engine_id}` | Get an engine |
| `POST` | `/bindings` | Create a source-engine binding |
| `GET` | `/bindings` | List bindings |
| `GET` | `/bindings/{binding_id}` | Get a binding |
| `DELETE` | `/bindings/{binding_id}` | Delete a binding |
| `GET` | `/sources/{source_id}/engines` | List engines bound to a source |

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
    "db_path": "/data/analytics.duckdb"
  },
  "capabilities": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `engine_type` | string | yes | `"duckdb"` or `"trino"` |
| `display_name` | string | yes | Human-readable name |
| `connection` | object | no | Engine-specific connection parameters (default: `{}`) |
| `capabilities` | object \| null | no | Explicit capability overrides (default: auto-detected) |

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
  "connection": {"db_path": "/data/analytics.duckdb"},
  "capabilities": {
    "supports_federation": false,
    "supports_partitions": false,
    "dialect": "duckdb"
  },
  "status": "active",
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

**Capability fields:**

| Field | Type | Description |
|-------|------|-------------|
| `supports_federation` | boolean | Can join across multiple sources |
| `supports_partitions` | boolean | Can enumerate table partitions |
| `dialect` | string | SQL dialect: `"duckdb"` or `"trino"` |

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

## Create Binding

```
POST /bindings
```

Creates a source-engine binding. A binding tells the QueryRouter that a given engine can execute queries against tables in the specified source.

Each `(source_id, engine_id)` pair must be unique. If a binding already exists, use the returned `binding_id` or delete and recreate it.

### Request Body

```json
{
  "source_id": "src_a1b2c3d4e5f6",
  "engine_id": "eng_a1b2c3d4e5f6",
  "priority": 10,
  "namespace": {
    "catalog": "iceberg",
    "schema": "events"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_id` | string | yes | Source to bind |
| `engine_id` | string | yes | Engine to bind |
| `priority` | integer | no | Routing priority (higher = preferred, default: `0`) |
| `namespace` | object | no | Table qualification namespace (e.g., catalog/schema for Trino) |

**Namespace fields (Trino):**

| Field | Type | Description |
|-------|------|-------------|
| `catalog` | string | Trino catalog to prefix table references |
| `schema` | string | Default schema for unqualified table names |

### Response

```json
{
  "binding_id": "bind_a1b2c3d4e5f6",
  "source_id": "src_...",
  "engine_id": "eng_...",
  "priority": 10,
  "namespace": {"catalog": "iceberg", "schema": "events"},
  "status": "active",
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

---

## List Bindings

```
GET /bindings
```

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_id` | string | Filter by source |
| `engine_id` | string | Filter by engine |

### Response

Array of binding objects.

---

## Get Binding

```
GET /bindings/{binding_id}
```

---

## Delete Binding

```
DELETE /bindings/{binding_id}
```

### Response

```json
{"status": "deleted", "binding_id": "bind_..."}
```

---

## List Engines for Source

```
GET /sources/{source_id}/engines
```

Returns all engines that have an active binding to the specified source, ordered by priority (descending).

### Response

Array of engine objects with their binding priority:

```json
[
  {
    "engine_id": "eng_...",
    "engine_type": "trino",
    "display_name": "Trino Cluster",
    "priority": 10,
    "binding_id": "bind_..."
  }
]
```

---

## Query Routing Resolution

```
POST /routing/resolve
```

Resolves a set of table names to a single engine capable of querying all of them. This endpoint is useful for debugging routing decisions or for agents that want to understand which engine will be used before submitting a step.

The router selects the highest-priority engine that has active bindings to sources containing all the specified tables.

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
| `table_names` | array[string] | yes | Table names to resolve (fully qualified or catalog-resolvable) |
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
