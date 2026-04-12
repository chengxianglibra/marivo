# Sources

Sources represent external data catalogs (DuckDB databases, Trino clusters, etc.). After registering a source, you trigger a sync to snapshot its schema and table metadata into Factum's local metadata store. Post-sync, all catalog queries hit SQLite — the external system is not queried at read time.

When `factum.yaml` includes a Trino source, Factum validates the optional `trino` Python package at
startup and fails fast if it is missing. Install Trino support with `pip install -e .[trino]`.

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
  "connection": {
    "db_path": "/data/analytics.duckdb"
  },
  "capabilities": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_type` | string | yes | Adapter type: `"duckdb"` or `"trino"` |
| `display_name` | string | yes | Human-readable name |
| `connection` | object | no | Adapter-specific connection parameters (default: `{}`) |
| `capabilities` | object \| null | no | Explicit capability overrides (default: auto-detected) |

**DuckDB connection parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `db_path` | string | Absolute path to the `.duckdb` file |

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
  "connection": {"db_path": "/data/analytics.duckdb"},
  "capabilities": {"supports_partitions": false},
  "sync_mode": "all",
  "status": "active",
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

**Sync modes:**

| Value | Description |
|-------|-------------|
| `all` | Sync all schemas and tables |
| `by_select` | Sync only tables listed in sync selections |
| `none` | Disable automatic sync |

---

## List Sources

```
GET /sources
```

Returns all registered sources.

### Response

Array of source objects.

---

## Get Source

```
GET /sources/{source_id}
```

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
  "connection": {
    "db_path": "/data/prod_analytics.duckdb"
  },
  "sync_mode": "by_select"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `display_name` | string | New display name |
| `connection` | object | Updated connection parameters |
| `sync_mode` | string | `"all"`, `"by_select"`, or `"none"` |

---

## Delete Source

```
DELETE /sources/{source_id}
```

Deletes the source and its synced objects. Will fail if the source has active engine bindings.

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

If `sync_mode` is `by_select`, only tables listed in sync selections are synced.

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

Sync selections allow fine-grained control over which tables to include when `sync_mode` is `by_select`. Each selection specifies a schema + table pair.

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

## List Source Objects

```
GET /sources/{source_id}/objects
```

Returns synced objects from the local metadata store. These are snapshots taken during the last sync.

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | Filter by object type: `schema`, `table`, `column` |
| `schema` | string | Filter by schema name |

### Response

```json
[
  {
    "object_id": "obj_...",
    "source_id": "src_...",
    "object_type": "table",
    "parent_id": "obj_...",
    "native_name": "user_video_watch",
    "fqn": "events.user_video_watch",
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
  "fqn": "events.user_video_watch",
  "properties": {
    "row_count": 15234891,
    "partition_columns": ["event_date"]
  },
  "sync_version": "sync_a1b2c3d4e5f6",
  "synced_at": "2024-01-15T10:01:08+00:00"
}
```

Returns `404` if the source does not exist or if the object is not present under that source.
