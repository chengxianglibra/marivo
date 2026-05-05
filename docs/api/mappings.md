# Mappings

Mappings are the operator-facing authority-to-execution projection contract between a `source`
and an `engine`. In the current runtime, supported source types and engine types are `duckdb` and
`trino` only. A mapping does not change source-side identity; it explicitly declares how authority
catalogs project into execution catalogs for routing and compile.

`marivo.yaml` does not carry mapping inventory. Mappings are created, updated, and deleted only
through the HTTP API.

This page documents the current minimal HTTP write/read surface for mappings. It aligns with the
v1 constraints in [`spec/service/data-plane/source-engine-mapping-contract.md`](../specs/service/data-plane/source-engine-mapping-contract.md):

- `catalog_mappings` is the only catalog projection contract and must be non-empty for a ready
  mapping
- Marivo does not guess execution catalogs from source defaults or engine defaults
- `default_schema` is only a fallback when the authority locator omits schema
- readiness is derived from source readiness, engine readiness, type compatibility, and catalog
  coverage validation
- active mappings fail closed: when a source already has synced table catalogs, the mapping must
  cover exactly those authority catalogs

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/mappings` | Create a mapping |
| `GET` | `/mappings` | List mappings |
| `GET` | `/mappings/{mapping_id}` | Get a mapping |
| `PUT` | `/mappings/{mapping_id}` | Update a mapping |
| `DELETE` | `/mappings/{mapping_id}` | Delete a mapping |

---

## Create Mapping

```
POST /mappings
```

Creates a source-to-engine mapping that explicitly governs authority catalog projection.

### Request Body

```json
{
  "source_id": "src_a1b2c3d4e5f6",
  "engine_id": "eng_a1b2c3d4e5f6",
  "priority": 10,
  "catalog_mappings": [
    {
      "authority_catalog": "main",
      "execution_catalog": "duckdb_runtime",
      "default_schema": null
    }
  ],
  "status": "active"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_id` | string | yes | Source governed by this mapping |
| `engine_id` | string | yes | Execution engine targeted by this mapping |
| `priority` | integer | no | Routing priority. Defaults to `0` |
| `catalog_mappings` | array | no | Explicit authority-to-execution catalog projection entries. Defaults to `[]`, but an empty list is `not_ready` |
| `status` | string | no | `active`, `inactive`, or `deprecated` |

Each `catalog_mappings[]` entry has the following shape:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `authority_catalog` | string | yes | Source authority catalog name |
| `execution_catalog` | string | yes | Execution-side catalog name used for routing/compile |
| `default_schema` | string \| null | no | Fallback schema only when the authority locator omits schema |

`authority_catalog` values must be unique within one mapping. Blank catalog names, blank execution
catalog names, and blank `default_schema` values are rejected.

### Response

```json
{
  "mapping_id": "map_a1b2c3d4e5f6",
  "source_id": "src_a1b2c3d4e5f6",
  "engine_id": "eng_a1b2c3d4e5f6",
  "priority": 10,
  "catalog_mappings": [
    {
      "authority_catalog": "main",
      "execution_catalog": "duckdb_runtime",
      "default_schema": null
    }
  ],
  "status": "active",
  "readiness_status": "ready",
  "failure_code": null,
  "created_at": "2026-04-23T09:00:00+00:00",
  "updated_at": "2026-04-23T09:00:00+00:00"
}
```

`readiness_status` is derived from validation. Common `failure_code` values include:

- `mapping_inactive`
- `mapping_invalid_type_combo`
- `mapping_incomplete`
- `mapping_invalid_namespace`
- `mapping_inactive_dependency`
- source or engine propagated blockers such as `source_invalid_connection`

`mapping_incomplete` is used when catalog coverage is missing or does not match the source's
currently synced authority catalogs. Source and engine readiness blockers propagate into mapping
readiness so callers can fix the dependency before retrying routing.

---

## List Mappings

```
GET /mappings
```

Returns all mappings. Optional query parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_id` | string | Filter mappings for one source |
| `engine_id` | string | Filter mappings for one engine |
| `status` | string | Filter by `active`, `inactive`, or `deprecated` |

### Response

Array of mapping objects using the same schema as `GET /mappings/{mapping_id}`.

---

## Get Mapping

```
GET /mappings/{mapping_id}
```

Returns one mapping object, including derived `readiness_status` and `failure_code`.

---

## Update Mapping

```
PUT /mappings/{mapping_id}
```

Updates one mapping. All request body fields are optional; only provided fields are changed.

### Request Body

```json
{
  "priority": 20,
  "catalog_mappings": [
    {
      "authority_catalog": "main",
      "execution_catalog": "duckdb_runtime_v2",
      "default_schema": null
    }
  ],
  "status": "inactive"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `priority` | integer | New routing priority |
| `catalog_mappings` | array | Replacement catalog projection entries |
| `status` | string | New lifecycle status |

### Response

Returns the updated mapping object using the same schema as `GET /mappings/{mapping_id}`.

---

## Delete Mapping

```
DELETE /mappings/{mapping_id}
```

Deletes the mapping.

### Response

```json
{
  "status": "deleted",
  "mapping_id": "map_a1b2c3d4e5f6"
}
```

---

## Notes

- `/mappings` is the minimal explicit surface for source-to-engine projection.
- This contract does not introduce schema-level rewrite or object-level remap.
- `catalog_mappings` should be treated as authoritative. Missing or incomplete coverage fails closed
  during readiness/routing evaluation.
- Engine `default_namespace` and source authority defaults are not used to infer missing
  projection entries.
