# Sources

Sources represent metadata authority catalogs. In the current runtime, supported source types are
`duckdb` and `trino` only. After registering a source, you trigger a sync to snapshot its schema
and table metadata into Marivo's local metadata store. Post-sync, all stored metadata queries hit
SQLite; the external system is not queried at read time.

A source never declares execution-side catalog projection. Source-to-engine projection is governed
only by [`/mappings`](mappings.md); source objects keep their source-side identity through
`authority_locator`.

`marivo.yaml` does not carry source inventory. Sources are registered and managed only through the
HTTP API.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sources` | Register a source |
| `GET` | `/sources` | List sources |
| `GET` | `/sources/{source_id}` | Get a source |
| `PUT` | `/sources/{source_id}` | Update a source |
| `DELETE` | `/sources/{source_id}` | Delete a source |
| `POST` | `/sources/{source_id}/sync` | Trigger catalog sync |
| `GET` | `/sources/{source_id}/sync/{job_id}` | Get sync job status |
| `GET` | `/sources/{source_id}/sync/selections` | List sync selections |
| `POST` | `/sources/{source_id}/sync/selections` | Set sync selections |
| `DELETE` | `/sources/{source_id}/sync/selections` | Clear all sync selections |
| `DELETE` | `/sources/{source_id}/sync/selections/{selection_id}` | Remove one sync selection |
| `GET` | `/sources/{source_id}/catalog/schemas` | Browse source schemas (live) |
| `GET` | `/sources/{source_id}/catalog/tables` | Browse source tables (live) |
| `GET` | `/sources/{source_id}/objects` | List synced source objects |
| `GET` | `/sources/{source_id}/objects/{object_id}` | Get one synced source object |

---

## Register Source

```
POST /sources
```

Registers a new data source. The source type determines which catalog adapter is used.

### Request Body

```json
{
  "source_type": "duckdb",
  "display_name": "Analytics DuckDB",
  "authority": {
    "catalog_system": "duckdb",
    "connection": {
      "path": "/data/analytics.duckdb"
    },
    "synthetic_catalog": "main"
  },
  "sync": {
    "mode": "selected"
  },
  "policy": {
    "allow_live_browse": true,
    "allow_sync": true
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_type` | string | yes | Adapter type: `"duckdb"` or `"trino"` |
| `display_name` | string | yes | Human-readable name |
| `authority` | object | yes | Metadata authority contract with `catalog_system`, `connection`, and optional `synthetic_catalog` |
| `sync` | object | no | Sync policy (`selected`, `all`, `none`) |
| `policy` | object | no | Operator control-plane flags |

**DuckDB connection parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Recommended absolute path to the `.duckdb` file |
| `database` | string | Supported alias for `path` |
| `db_path` | string | Supported alias for `path` |

**Trino connection parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `host` | string | Trino coordinator host |
| `port` | integer | Port (default: `8080`) |
| `user` | string | Trino user |
| `catalog` | string | Default Trino catalog |
| `schema` | string | Default Trino schema |
| `http_scheme` | string | `"http"` or `"https"` (default: `"http"`) |

### Response

```json
{
  "source_id": "src_a1b2c3d4e5f6",
  "source_type": "duckdb",
  "display_name": "Analytics DuckDB",
  "authority": {
    "catalog_system": "duckdb",
    "connection": {"path": "/data/analytics.duckdb"},
    "synthetic_catalog": "main"
  },
  "sync": {"mode": "selected"},
  "intrinsic_capabilities": {"supports_partitions": false},
  "policy": {
    "allow_live_browse": true,
    "allow_sync": true
  },
  "status": "active",
  "readiness_status": "ready",
  "failure_code": null,
  "mappings": [],
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

The canonical response model is `SourceResponse`. `authority`, `sync`, `intrinsic_capabilities`,
`policy`, and `mappings` are structured sub-objects; `intrinsic_capabilities`,
`readiness_status`, and `failure_code` are read-only derived fields.

`mappings` is a summary of mapping objects that govern this source. It is not embedded source
configuration and does not let a source carry execution namespace defaults.

`readiness_status` is derived from source validation. A source stays `not_ready` when the
authority connection is incomplete or when a source without a native catalog layer lacks a stable
`synthetic_catalog`. `failure_code` exposes the current blocker, such as
`source_invalid_connection` or `source_missing_synthetic_catalog`.

**Sync modes:**

| Value | Description |
|-------|-------------|
| `selected` | Sync only tables listed in sync selections (default) |
| `all` | Sync the full authority catalog |
| `none` | Disable automatic sync |

---

## List Sources

```
GET /sources
```

Returns all registered sources.

### Response

Array of `SourceResponse` objects.

---

## Get Source

```
GET /sources/{source_id}
```

The detail surface includes a `mappings` array summarizing the mappings that currently govern the
source. Each entry exposes `mapping_id`, `engine_id`, `status`, `readiness_status`,
`failure_code`, and the mapping's `catalog_mappings`.

---

## Update Source

```
PUT /sources/{source_id}
```

### Request Body

All fields are optional; only provided fields are updated.

```json
{
  "display_name": "Production Analytics DuckDB",
  "authority": {
    "catalog_system": "duckdb",
    "connection": {
      "path": "/data/prod_analytics.duckdb"
    },
    "synthetic_catalog": "main"
  },
  "sync": {
    "mode": "selected"
  },
  "policy": {
    "allow_live_browse": true,
    "allow_sync": true
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `display_name` | string | New display name |
| `authority` | object | Updated authority contract |
| `sync` | object | Updated sync contract |
| `policy` | object | Updated operator policy |

### Response

Returns `SourceResponse`.

---

## Delete Source

```
DELETE /sources/{source_id}
```

Deletes the source and its synced objects. Will fail if the source has dependent mappings.

### Response

```json
{"status": "deleted", "source_id": "src_..."}
```

---

## Trigger Sync

```
POST /sources/{source_id}/sync
```

Triggers a catalog sync job. The job snapshots schemas, tables, and columns from the external source into the local metadata store. Stale objects (present in prior sync but absent from current sync) are automatically removed.

If `sync.mode` is `selected`, only tables listed in sync selections are synced. If it is `all`,
Marivo syncs the full authority catalog.

For Trino sources, table detail sync also attempts to capture table properties. Marivo reads both
connector hidden metadata tables such as `"table$properties"` and explicit `WITH (...)` properties
from `SHOW CREATE TABLE` when available, so Hive and Iceberg-backed tables can both surface table
property metadata in synced `source_objects`.

### Response

```json
{
  "job_id": "sync_a1b2c3d4e5f6",
  "source_id": "src_...",
  "status": "running"
}
```

The sync runs asynchronously. Poll `GET /sources/{source_id}/sync/{job_id}` for completion.

---

## Get Sync Job Status

```
GET /sources/{source_id}/sync/{job_id}
```

### Response

```json
{
  "job_id": "sync_...",
  "source_id": "src_...",
  "job_type": "full_sync",
  "status": "completed",
  "started_at": "2024-01-15T10:01:00+00:00",
  "finished_at": "2024-01-15T10:01:08+00:00",
  "objects_synced": 142,
  "error_message": null
}
```

**Sync job status values:** `pending`, `running`, `completed`, `failed`

---

## Sync Selections

Sync selections allow fine-grained control over which tables to include when `sync.mode` is `selected`. Each selection specifies a schema + table pair.

### List Sync Selections

```
GET /sources/{source_id}/sync/selections
```

**Response:**

```json
[
  {
    "selection_id": "sel_...",
    "source_id": "src_...",
    "schema_name": "events",
    "table_name": "user_video_watch",
    "created_at": "2024-01-15T10:00:00+00:00"
  }
]
```

### Set Sync Selections

```
POST /sources/{source_id}/sync/selections
```

Adds new selections (non-destructive; existing selections are preserved). Duplicate entries are silently ignored.

**Request body:**

```json
{
  "selections": [
    {"schema_name": "events", "table_name": "user_video_watch"},
    {"schema_name": "events", "table_name": "user_sessions"}
  ]
}
```

**Response:** Array of created selection objects.

### Clear All Sync Selections

```
DELETE /sources/{source_id}/sync/selections
```

Removes all sync selections for the source.

**Response:**

```json
{"status": "cleared", "source_id": "src_..."}
```

### Remove One Selection

```
DELETE /sources/{source_id}/sync/selections/{selection_id}
```

**Response:**

```json
{"status": "deleted", "selection_id": "sel_..."}
```

---

## Browse Schemas (Live)

```
GET /sources/{source_id}/catalog/schemas
```

Queries the external source directly (not the local snapshot) to list available schemas. Useful for exploring before configuring sync selections.
For cataloged backends such as Trino, the live schema list is scoped to the source's configured
catalog rather than aggregating every catalog visible to the connection.

### Response

```json
[
  {"schema_name": "events", "table_count": 12},
  {"schema_name": "dimensions", "table_count": 5}
]
```

---

## Browse Tables (Live)

```
GET /sources/{source_id}/catalog/tables?schema=events
```

Queries the external source directly for tables in a specific schema.
For Trino sources, live table browse should enumerate tables from the requested schema within the
source's configured catalog; do not treat the connection's default schema as the browse target.
Admin UI note: `Manage Selections` should treat the schema dropdown as the single source of truth
and ignore stale table-list responses from earlier schema requests so the checklist always matches
the currently selected schema.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `schema` | string | yes | Schema name to browse |

### Response

```json
[
  {
    "table_name": "user_video_watch",
    "schema_name": "events",
    "row_count": 15234891,
    "column_count": 18
  }
]
```

---

## Preview Table (Live)

```
GET /sources/{source_id}/catalog/preview
```

Queries the external source directly to preview sample rows from a table.
Useful for inspecting actual data values when configuring semantic bindings,
especially for determining timestamp formats and column data types.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `schema` | string | yes | Schema name |
| `table` | string | yes | Table name |
| `limit` | integer | no | Max rows to return (default 100, max 1000) |
| `columns` | string | no | Comma-separated column names to select |

### Response

```json
{
  "source_id": "src_...",
  "schema_name": "events",
  "table_name": "user_sessions",
  "columns": [
    {"name": "user_id", "type": "VARCHAR"},
    {"name": "event_time", "type": "TIMESTAMP"}
  ],
  "rows": [
    {"user_id": "user_001", "event_time": "2024-01-15T10:30:00"},
    {"user_id": "user_002", "event_time": "2024-01-15T11:45:00"}
  ],
  "row_count": 2,
  "truncated": false,
  "limit_requested": 100,
  "limit_applied": 100
}
```

### Error Responses

- **404**: Source or table not found
- **400**: Invalid column names or limit value

---

## List Source Objects

```
GET /sources/{source_id}/objects
```

Returns synced objects from the local metadata store. These are snapshots taken during the last sync.

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | Filter by object type: `schema`, `table`, `column` |
| `schema` | string | Filter by authority schema name from `authority_locator.schema` |

### Response

```json
[
  {
    "object_id": "obj_...",
    "source_id": "src_...",
    "object_type": "table",
    "parent_id": "obj_...",
    "native_name": "user_video_watch",
    "fqn": "main.events.user_video_watch",
    "authority_locator": {
      "catalog": "main",
      "schema": "events",
      "table": "user_video_watch"
    },
    "properties": {
      "row_count": 15234891,
      "partition_columns": ["event_date"],
      "time_capabilities": {
        "analysis_time": {
          "timestamp_column": "event_time",
          "fallback_date_column": "event_date"
        },
        "partition_time": {
          "date_column": "event_date"
        },
        "default_compare_grain": "day"
      }
    },
    "synced_at": "2024-01-15T10:01:08+00:00"
  }
]
```

`authority_locator` is the stable source-side locator frozen during sync. `fqn` is derived from that
locator and uses the same `catalog.schema.table` prefix for tables.

**Object types:**

| Type | Description |
|------|-------------|
| `schema` | Database schema |
| `table` | Table or view |
| `column` | Column within a table |
| `partition` | Partition (if supported by the adapter) |

For typed time resolution, table-level `properties.time_capabilities` is the source-metadata hint consumed after semantic-entity overrides and before field-name heuristics.

---

## Get Source Object

```
GET /sources/{source_id}/objects/{object_id}
```

Returns one synced source object from the local metadata store. This is the detail form of the list endpoint and returns the same payload shape as one item from `GET /sources/{source_id}/objects`.

### Response

```json
{
  "object_id": "obj_...",
  "source_id": "src_...",
  "object_type": "table",
  "parent_id": "obj_...",
  "native_name": "user_video_watch",
  "native_id": null,
  "fqn": "main.events.user_video_watch",
  "authority_locator": {
    "catalog": "main",
    "schema": "events",
    "table": "user_video_watch"
  },
  "properties": {
    "row_count": 15234891,
    "partition_columns": ["event_date"]
  },
  "sync_version": "sync_a1b2c3d4e5f6",
  "synced_at": "2024-01-15T10:01:08+00:00"
}
```

Returns `404` if the source does not exist or if the object is not present under that source.
