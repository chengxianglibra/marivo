---
status: archived
canonical-path: docs/api/semantic.md
created: 2026-05-02
---

# Dataset-Native Grounding Design

Date: 2026-05-02

## Summary

Marivo v2 semantic layer uses OSI Dataset and Field as the only physical grounding contract. A dataset maps to one datasource relation through `custom_extensions[].data.datasource_id` plus `dataset.source`. A field maps to a physical column or computed expression through `field.expression`.

This is a breaking cleanup. The old persisted catalog cache, datasource sync flow, and typed binding family are removed from the v2 target architecture. There is no compatibility path for `/semantic/bindings`, datasource sync endpoints, or persisted `source_objects`.

## Goals

- Make v2 SemanticModel the single source of truth for physical grounding.
- Remove persisted catalog cache and sync lifecycle.
- Remove typed bindings as an API, storage family, and modeling requirement.
- Keep datasource APIs focused on registration, live browse, preview, and execution.
- Keep write-time validation lightweight and move datasource-dependent checks to readiness or explicit validation.

## Non-Goals

- No compatibility shim for removed sync, object-cache, or binding endpoints.
- No migration of old typed binding rows into v2 semantic models.
- No cross-datasource join support in this cleanup.
- No new persisted catalog metadata cache under a different name.

## Architecture

The only physical grounding path is:

```text
SemanticModel -> Dataset -> Field
```

`Dataset` carries table or view grounding:

```json
{
  "name": "orders",
  "source": "dwd.orders",
  "custom_extensions": [
    {
      "vendor_name": "MARIVO",
      "data": "{\"datasource_id\":\"ds_prod\"}"
    }
  ]
}
```

`dataset.source` is the relation FQN inside the referenced datasource. The canonical authoring form is:

- DuckDB: `schema.table`; explicit schema is recommended.
- Trino: `catalog.schema.table`; `schema.table` is allowed only when the datasource connection fixes the catalog.

`Field` carries column or computed-expression grounding:

```json
{
  "name": "pay_amount",
  "expression": {
    "dialects": [
      {
        "dialect": "ANSI_SQL",
        "expression": "pay_amount"
      }
    ]
  },
  "custom_extensions": [
    {
      "vendor_name": "MARIVO",
      "data": "{\"data_type\":\"number\"}"
    }
  ]
}
```

Runtime resolution uses `dataset.datasource_id + dataset.source` to locate a relation, then compiles `field.expression` into SQL. `binding`, `carrier_binding`, `field_binding`, and `time_binding` are no longer external or persisted contracts.

## API Cleanup

Remove these datasource endpoints:

- `POST /datasources/{datasource_id}/sync`
- `GET /datasources/{datasource_id}/sync/{job_id}`
- `GET /datasources/{datasource_id}/sync/selections`
- `POST /datasources/{datasource_id}/sync/selections`
- `DELETE /datasources/{datasource_id}/sync/selections`
- `DELETE /datasources/{datasource_id}/sync/selections/{selection_id}`
- `GET /datasources/{datasource_id}/objects`
- `GET /datasources/{datasource_id}/objects/{object_id}`
- `PATCH /datasources/{datasource_id}/objects/{object_id}/properties`

Remove all `/semantic/bindings*` routes and their OpenAPI error guidance. Remove matching MCP tools, MCP resources, inventory entries, frontend operations, and tests.

Keep datasource live APIs:

- `GET /datasources/{id}/browse/schemas`
- `GET /datasources/{id}/browse/tables?schema_name=...`
- `GET /datasources/{id}/browse/columns?schema_name=...&table_name=...`
- `GET /datasources/{id}/catalog/preview?schema=...&table=...`

Datasource APIs become registration plus live discovery, preview, and execution. They no longer expose persisted synchronized catalog objects.

## Storage Cleanup

Remove persisted catalog cache and binding tables:

- `source_objects`
- `sync_jobs`
- `sync_selections`
- `typed_bindings`
- `binding_imports`
- `carrier_bindings`
- `carrier_field_surfaces`
- `carrier_time_surfaces`
- `field_bindings`
- `time_bindings`
- `join_relations`
- `consumption_policies`

Keep `datasources`, but remove datasource sync state:

- remove `datasources.sync_mode`
- remove `policy.allow_sync`

Datasource policy should keep only live browse, preview, and execution authorization concerns, such as `allow_live_browse` and `allow_identity_reuse`.

## Runtime Data Flow

1. Intent or metric resolution selects v2 `metric`, `dataset`, and `field` objects.
2. Metric extension `observed_dataset` names the dataset used for the metric.
3. Dataset extension provides `datasource_id`.
4. `dataset.source` provides the datasource-local relation FQN.
5. Runtime builds an engine with `DatasourceService.build_analytics_engine(datasource_id, session_id=...)`.
6. SQL compilation emits `FROM <dataset.source>` and uses field expressions for measures, dimensions, filters, and grouping.
7. Multi-dataset queries use v2 `Relationship` objects. They do not use a binding join graph.

If a metric or query spans multiple datasources, return `cross_datasource_join_unsupported` unless a future design explicitly adds federated execution.

## Validation And Readiness

Write-time validation is structural and local:

- `datasource_id` must exist.
- `dataset.source` must be a non-empty FQN string.
- `field.expression` must be valid OSI expression JSON.
- Required MARIVO extensions must parse and satisfy their Pydantic contracts.

Readiness or explicit validation performs live checks:

- datasource connection is ready
- relation exists
- referenced columns or expressions can compile
- optional preview/sample query succeeds

Expected error codes:

- `datasource_not_found`
- `datasource_not_ready`
- `relation_not_found`
- `field_expression_invalid`
- `cross_datasource_join_unsupported`

This split keeps authoring resilient to temporary datasource outages while still exposing execution readiness.

## Internal Compiler Transition

External and persisted binding concepts disappear immediately. During implementation, internal services may temporarily derive an execution context from dataset and field rows if that reduces compiler migration risk.

That adapter must stay private and must not introduce new storage, API fields, or docs that recreate binding as a hidden contract. The final target is direct compiler consumption of v2 dataset and field grounding.

## Test Scope

Update datasource tests:

- remove sync, sync selection, object list, object detail, and object property patch tests
- keep registration, update, delete, readiness, browse, preview tests
- add live columns browse tests

Update semantic v2 tests:

- validate dataset `datasource_id` and `source`
- validate field expression storage and readback
- cover readiness live validation failures for missing datasource, missing relation, and invalid field expression

Remove or rewrite binding tests, MCP binding inventory tests, frontend binding UI tests, and any tests that require `source_objects`.

## Documentation Scope

Update:

- `docs/api/semantic.md`
- `marivo-skill/marivo/references/semantic-layer.md`
- `marivo-skill/marivo/references/http-contracts.md`
- datasource and MCP inventory docs/tests
- frontend operations copy and tests

Docs must state that v2 grounding is dataset-native and that synced source metadata and typed bindings are no longer part of the contract.

## Success Criteria

- No public API route exposes datasource sync, datasource objects, or semantic bindings.
- No schema table stores synced catalog objects or typed bindings.
- v2 semantic model import and CRUD persist datasource-native dataset and field grounding.
- Runtime/readiness resolves physical data from `dataset.datasource_id`, `dataset.source`, and `field.expression`.
- OpenAPI, MCP inventory, frontend operations, docs, and tests match the breaking contract.
