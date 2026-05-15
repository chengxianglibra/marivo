# Datasources

Datasources register external catalog connections for Marivo. The current runtime supports
`duckdb` and `trino`.

Datasource metadata is live. Marivo does not snapshot schemas, tables, columns, or table
properties into a persisted catalog cache. Use the browse and preview endpoints to inspect the
external datasource directly, then persist physical grounding in the semantic model through:

- `dataset.custom_extensions[].data.datasource_id`: selects the datasource
- `dataset.source`: datasource-local relation FQN, usually `schema.table` or `catalog.schema.table`
- `field.expression`: physical column name or computed SQL expression for the field

Datasources do not declare execution-side catalog projection. In the dataset-native runtime,
execution is resolved from datasource-backed dataset context. Datasources are registered and
managed through the HTTP API.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/datasources` | Register a datasource |
| `GET` | `/datasources` | List datasources |
| `GET` | `/datasources/{datasource_id}` | Get a datasource |
| `PUT` | `/datasources/{datasource_id}` | Update a datasource |
| `DELETE` | `/datasources/{datasource_id}` | Delete a datasource |
| `GET` | `/datasources/{datasource_id}/browse/schemas` | Browse schemas live |
| `GET` | `/datasources/{datasource_id}/browse/tables?schema_name=...` | Browse tables live |
| `GET` | `/datasources/{datasource_id}/browse/columns?schema_name=...&table_name=...` | Browse columns live |
| `GET` | `/datasources/{datasource_id}/catalog/preview?schema=...&table=...` | Preview rows live |

## Component Schemas

| Schema name | Used by |
|-------------|---------|
| `DatasourceRegisterRequest` | `POST /datasources` request |
| `DatasourceUpdateRequest` | `PUT /datasources/{id}` request |
| `DatasourceResponse` | datasource CRUD responses |
| `DatasourceDeleteResponse` | `DELETE /datasources/{id}` response |
| `DuckDbDatasourceConnection` | `connection` variant for `duckdb` |
| `TrinoDatasourceConnection` | `connection` variant for `trino` |
| `BrowseSchemaItem` | schema browse list |
| `BrowseTableItem` | table browse list |
| `DatasourceColumnResponse` | column browse list |
| `TablePreviewResponse` | catalog preview |

Retrieve a schema fragment: `GET /openapi/schemas/DatasourceResponse`

## Register Datasource

```
POST /datasources
```

Registers a datasource. The datasource type determines which live catalog adapter is used.

### DuckDB Request

```json
{
  "datasource_type": "duckdb",
  "display_name": "Analytics DuckDB",
  "connection": {
    "path": "/data/analytics.duckdb"
  }
}
```

### Trino Request

```json
{
  "datasource_type": "trino",
  "display_name": "Warehouse Trino",
  "connection": {
    "host": "trino.example.com",
    "port": 8080,
    "user": "analyst",
    "catalog": "iceberg",
    "http_scheme": "https",
    "session_properties": {}
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `datasource_type` | string | yes | Adapter type: `"duckdb"` or `"trino"` |
| `display_name` | string | yes | Human-readable name |
| `connection` | object | yes | Datasource connection payload; the service injects `datasource_type` for response validation |

DuckDB accepts `path`, `database`, or `db_path`; prefer an absolute `path`.

### Response

```json
{
  "datasource_id": "ds_a1b2c3d4e5f6",
  "datasource_type": "duckdb",
  "display_name": "Analytics DuckDB",
  "connection": {
    "datasource_type": "duckdb",
    "path": "/data/analytics.duckdb"
  },
  "owner_user": "alice",
  "status": "active",
  "readiness_status": "ready",
  "failure_code": null,
  "created_at": "2026-05-03T10:00:00+00:00",
  "updated_at": "2026-05-03T10:00:00+00:00"
}
```

`readiness_status` is derived from datasource validation. A datasource can be `not_ready` when
its connection is incomplete or the live adapter cannot be constructed.

## List Datasources

```
GET /datasources
```

Returns all registered datasources as `DatasourceResponse` objects.

## Get Datasource

```
GET /datasources/{datasource_id}
```

Returns one datasource. This response does not include catalog tables or columns; browse endpoints
query the datasource live when that information is needed.

## Update Datasource

```
PUT /datasources/{datasource_id}
```

All fields are optional. When `connection` is provided, send the full connection object for the
target datasource type.

```json
{
  "display_name": "Production Analytics DuckDB",
  "connection": {
    "datasource_type": "duckdb",
    "path": "/data/prod_analytics.duckdb"
  }
}
```

## Delete Datasource

```
DELETE /datasources/{datasource_id}
```

Deletes the datasource registration.

```json
{
  "datasource_id": "ds_a1b2c3d4e5f6",
  "deleted": true
}
```

## Browse Schemas

```
GET /datasources/{datasource_id}/browse/schemas
```

Queries the external datasource directly.

```json
[
  {
    "schema_name": "analytics",
    "table_count": 12
  }
]
```

## Browse Tables

```
GET /datasources/{datasource_id}/browse/tables?schema_name=analytics
```

Returns tables in one live schema.

```json
[
  {
    "schema_name": "analytics",
    "table_name": "orders",
    "row_count": null,
    "column_count": 8
  }
]
```

## Browse Columns

```
GET /datasources/{datasource_id}/browse/columns?schema_name=analytics&table_name=orders
```

Returns live columns for one relation. Use this endpoint to choose `field.expression` values in
the semantic model.

```json
[
  {
    "name": "order_id",
    "schema_name": "analytics",
    "table_name": "orders",
    "data_type": "VARCHAR",
    "properties": {}
  },
  {
    "name": "order_date",
    "schema_name": "analytics",
    "table_name": "orders",
    "data_type": "DATE",
    "properties": {}
  }
]
```

## Preview Table

```
GET /datasources/{datasource_id}/catalog/preview?schema=analytics&table=orders&limit=20
```

Runs a bounded live preview query. Use it to inspect example values before publishing semantic
datasets and fields.

Optional query parameters:

| Parameter | Description |
|-----------|-------------|
| `limit` | requested row limit; adapters clamp to a maximum |
| `columns` | comma-separated column list |
| `filters` | JSON object, or array of `{column,value}` equality filters |

```json
{
  "datasource_id": "ds_a1b2c3d4e5f6",
  "schema_name": "analytics",
  "table_name": "orders",
  "columns": [
    {"name": "order_id", "type": "VARCHAR"},
    {"name": "amount", "type": "DOUBLE"}
  ],
  "rows": [
    {"order_id": "o_001", "amount": 42.5}
  ],
  "row_count": 1,
  "truncated": false,
  "limit_requested": 20,
  "limit_applied": 20,
  "filters_applied": null
}
```

## Dataset-Native Grounding Flow

1. Register a datasource.
2. Browse schemas, tables, and columns live.
3. Create or import an OSI semantic model.
4. Put the datasource id in the dataset MARIVO extension.
5. Put the datasource-local relation FQN in `dataset.source`.
6. Put column names or computed expressions in each `field.expression`.
7. Define metrics, dimensions, predicates, and relationships against datasets and fields.
8. Validate/import the OSI document; repair datasource, relation, or field-expression blockers
   surfaced by the semantic validation result.

Example dataset fragment:

```json
{
  "name": "orders",
  "source": "analytics.orders",
  "primary_key": ["order_id"],
  "custom_extensions": [
    {
      "vendor_name": "MARIVO",
      "data": {
        "datasource_id": "ds_a1b2c3d4e5f6"
      }
    }
  ],
  "fields": [
    {
      "name": "order_id",
      "expression": {
        "dialects": [
          {"dialect": "ANSI_SQL", "expression": "order_id"}
        ]
      }
    },
    {
      "name": "amount",
      "expression": {
        "dialects": [
          {"dialect": "ANSI_SQL", "expression": "amount"}
        ]
      }
    }
  ]
}
```

## Error Semantics

Common datasource and dataset-native readiness failures:

| Code | Meaning | Recovery |
|------|---------|----------|
| `datasource_not_found` | A dataset references a missing datasource id | Create/select a datasource and put its id in the dataset MARIVO extension |
| `relation_not_found` | `dataset.source` does not resolve through live browse | Browse schemas/tables and update `dataset.source` to the live FQN |
| `field_expression_invalid` | A field expression cannot be compiled or resolved | Update `field.expression.dialects[]` for the target datasource dialect |
| `datasource_not_ready` | The datasource adapter cannot currently browse or execute | Repair the datasource connection or adapter configuration |
