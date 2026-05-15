# Error Reference

Marivo uses standard HTTP status codes. Most errors return a `detail` field
from FastAPI or the runtime. Request-validation errors may also include guided
remediation metadata.

## HTTP Status Codes

| Code | Meaning | Common causes |
|------|---------|---------------|
| `200` | OK | Successful reads and most successful POST/PUT/DELETE calls |
| `204` | No Content | Successful semantic model delete |
| `400` | Bad Request | Invalid query parameter or datasource request |
| `401` | Unauthorized | A user identity is required but `X-Marivo-User` is absent |
| `403` | Forbidden | Caller cannot perform the requested operation |
| `404` | Not Found | Session, datasource, semantic model, or artifact was not found |
| `409` | Conflict | Session lifecycle conflict, duplicate calendar version, semantic readiness blocker, or compatibility blocker |
| `422` | Unprocessable Entity | Request body fails schema or intent validation |
| `500` | Internal Server Error | Unexpected server-side failure |
| `502` | Bad Gateway | Intent execution error from the runtime |

## Basic Error Shape

```json
{
  "detail": "Session sess_abc123 not found"
}
```

FastAPI request-validation failures use a `detail` array. The HTTP layer may add
guided fields:

```json
{
  "detail": [
    {
      "loc": ["body", "goal"],
      "msg": "Field required",
      "type": "missing"
    }
  ],
  "error": {
    "code": "request_validation_error",
    "message": "Request validation failed. Use the guided example and contract links."
  },
  "guidance": {
    "contract_url": "/openapi/paths/L3Nlc3Npb25z?operation=post&expand=request,schemas&depth=5",
    "schema_url": "/openapi/schemas/SessionCreateRequest?depth=5",
    "examples": []
  }
}
```

Use [`openapi.md`](openapi.md) to fetch the route or schema fragment referenced
by `guidance`.

## Semantic Validation Results

`POST /semantic-models/validate` and failed imports return a structured
validation result instead of a plain error envelope:

```json
{
  "valid": false,
  "schema_version": "0.1.1",
  "errors": [
    {
      "code": "relation_not_found",
      "message": "Dataset orders source analytics.orders was not found.",
      "json_pointer": "/semantic_model/0/datasets/0/source",
      "severity": "error",
      "hint": "Browse schemas and tables, then update dataset.source.",
      "context": {
        "dataset": "orders",
        "datasource_id": "ds_a1b2c3d4e5f6",
        "source": "analytics.orders"
      }
    }
  ],
  "warnings": [],
  "summary": {
    "models": 1,
    "datasets": 1,
    "fields": 3,
    "metrics": 1
  }
}
```

Common semantic blockers:

| Code | Recovery |
|------|----------|
| `datasource_not_found` | Create/select a datasource and put its id in the dataset MARIVO extension |
| `relation_not_found` | Browse schemas/tables and update `dataset.source` to the live FQN |
| `field_expression_invalid` | Update `field.expression.dialects[]` for the target datasource dialect |

## Intent Errors

Intent routes map runtime failures to these common statuses:

| Status | Scenario |
|--------|----------|
| `404` | session or referenced artifact not found |
| `409` | semantic runtime readiness or compatibility blocker |
| `422` | schema validation, unknown intent type, or intent-level validation failure |
| `501` | runtime method is not implemented |
| `502` | unexpected execution error |

When compilation hits object readiness or compatibility gates, `detail` may be a
structured object with fields such as `message`, `code`, `category`,
`subject_ref`, `readiness_status`, `blocking_requirements`, or `issues`.

## Datasource And Routing Errors

Datasource CRUD and live browse errors are usually plain `detail` strings:

```json
{"detail": "schema_name query parameter is required"}
```

`POST /routing/resolve` returns `200` for both successful and failed routing
resolution. Routing failures are represented in the response body:

```json
{
  "resolved": false,
  "failure_code": "relation_not_found",
  "table_names": ["analytics.orders"],
  "engine": null,
  "qualified_names": {},
  "selection_reason": "Dataset source was not found in datasource metadata.",
  "routing_detail": {
    "resolution_status": "unresolved",
    "unresolved_tables": ["analytics.orders"]
  },
  "capability_profile": null
}
```

## Trino-Specific Errors

When a Trino-backed datasource is used, query errors from the coordinator are
surfaced through runtime error messages. Common causes include missing tables,
invalid catalog/schema values, or authentication/user configuration problems in
the datasource connection.
