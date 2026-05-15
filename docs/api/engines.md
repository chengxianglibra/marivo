# Routing Resolution

The current HTTP implementation does not expose `/engines` CRUD endpoints.
Execution routing is datasource-backed: semantic datasets point at a
`datasource_id`, and `POST /routing/resolve` can be used to inspect how table
names resolve before an intent executes.

Historical engine-inventory and source-to-engine mapping designs are not part
of the active HTTP router.

## Endpoint

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/routing/resolve` | Resolve requested table names to one execution datasource |

## Resolve Route

```http
POST /routing/resolve
```

Request body:

```json
{
  "table_names": ["analytics.orders"],
  "routing_intent": {
    "step_type": "observe",
    "metric_names": ["order_revenue"],
    "requested_dimensions": ["country"],
    "compatible_dimensions": ["country"],
    "legal_grains": ["day"],
    "policy_hints": []
  }
}
```

`routing_intent` is optional. When present, it provides planner/debug hints; it
does not create an engine, datasource, mapping, or semantic object.

Success response:

```json
{
  "resolved": true,
  "failure_code": null,
  "table_names": ["analytics.orders"],
  "engine": {
    "datasource_id": "ds_a1b2c3d4e5f6",
    "datasource_type": "duckdb",
    "display_name": "Analytics DuckDB"
  },
  "qualified_names": {
    "analytics.orders": "analytics.orders"
  },
  "selection_reason": "resolved from datasource context",
  "routing_detail": {
    "resolution_status": "resolved",
    "selected_mapping_ids": [],
    "execution_locators": {},
    "sources": {},
    "candidates": [],
    "readiness_blockers": [],
    "unresolved_tables": []
  },
  "capability_profile": null
}
```

Routing failures use the same `200` response contract with `resolved: false`.
Inspect `failure_code`, `selection_reason`, and `routing_detail` for the
operator-facing blocker.
