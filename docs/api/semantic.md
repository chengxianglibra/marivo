# Semantic Layer

The semantic layer exposes typed semantic objects over HTTP. Entity and metric routes now use the target-state typed contract only; legacy payloads and `?surface=typed` are no longer supported on those endpoints.

Semantic lifecycle is shared across objects:

- `draft`
- `published`
- `deprecated`

Only `published` objects are available to runtime resolution and intent execution.

Related design docs:

- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/compiler-compatibility-profile.zh.md`

## Endpoints

### Entities

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/entities` | Create a typed entity |
| `GET` | `/semantic/entities` | List typed entities |
| `GET` | `/semantic/entities/{entity_id}` | Get a typed entity |
| `PUT` | `/semantic/entities/{entity_id}` | Update a typed entity |
| `POST` | `/semantic/entities/{entity_id}/publish` | Publish a typed entity |

### Metrics

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/metrics` | Create a typed metric |
| `GET` | `/semantic/metrics` | List typed metrics |
| `GET` | `/semantic/metrics/{metric_id}` | Get a typed metric |
| `PUT` | `/semantic/metrics/{metric_id}` | Update a typed metric |
| `POST` | `/semantic/metrics/{metric_id}/publish` | Publish a typed metric |

### Bindings

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/bindings` | Create a typed binding |
| `GET` | `/semantic/bindings` | List typed bindings |
| `GET` | `/semantic/bindings/{binding_id}` | Get a typed binding |
| `PUT` | `/semantic/bindings/{binding_id}` | Update a typed binding |
| `POST` | `/semantic/bindings/{binding_id}/publish` | Publish a typed binding |

### Compiler Compatibility Profiles

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/compiler/compatibility-profiles` | Create a compatibility profile |
| `GET` | `/compiler/compatibility-profiles` | List compatibility profiles |
| `GET` | `/compiler/compatibility-profiles/{profile_id}` | Get a compatibility profile |
| `PUT` | `/compiler/compatibility-profiles/{profile_id}` | Update a compatibility profile |
| `POST` | `/compiler/compatibility-profiles/{profile_id}/publish` | Publish a compatibility profile |

### Legacy Compatibility Surface

`/semantic/mappings` remains available as a legacy compatibility surface for runtime wiring that has not yet migrated to typed bindings.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/mappings` | Create a legacy mapping |
| `GET` | `/semantic/mappings` | List legacy mappings |
| `DELETE` | `/semantic/mappings/{mapping_id}` | Delete a legacy mapping |

## Entity Contract

`POST /semantic/entities`

Request:

```json
{
  "header": {
    "entity_ref": "entity.user",
    "display_name": "User",
    "description": "Registered platform user",
    "entity_contract_version": "entity.v4"
  },
  "interface_contract": {
    "identity": {
      "key_refs": ["key.user_id"],
      "uniqueness_scope": "global",
      "id_stability": "stable"
    },
    "primary_time_ref": "time.user_created_at",
    "stable_descriptors": [
      {
        "dimension_ref": "dimension.signup_channel",
        "cardinality": "one"
      }
    ]
  }
}
```

Response:

```json
{
  "entity_contract_id": "entc_a1b2c3d4e5f6",
  "header": {
    "entity_ref": "entity.user",
    "display_name": "User",
    "description": "Registered platform user",
    "entity_contract_version": "entity.v4"
  },
  "interface_contract": {
    "identity": {
      "key_refs": ["key.user_id"],
      "uniqueness_scope": "global",
      "id_stability": "stable",
      "nullable_key_policy": "reject"
    },
    "hierarchy": null,
    "primary_time_ref": "time.user_created_at",
    "stable_descriptors": [
      {
        "dimension_ref": "dimension.signup_channel",
        "cardinality": "one"
      }
    ]
  },
  "status": "draft",
  "revision": 1,
  "created_at": "2026-04-08T12:00:00+00:00",
  "updated_at": "2026-04-08T12:00:00+00:00"
}
```

List responses are always wrapped:

```json
{
  "items": [
    {
      "entity_contract_id": "entc_a1b2c3d4e5f6",
      "header": {
        "entity_ref": "entity.user",
        "display_name": "User",
        "description": "Registered platform user",
        "entity_contract_version": "entity.v4"
      },
      "interface_contract": {
        "identity": {
          "key_refs": ["key.user_id"],
          "uniqueness_scope": "global",
          "id_stability": "stable",
          "nullable_key_policy": "reject"
        },
        "hierarchy": null,
        "primary_time_ref": null,
        "stable_descriptors": null
      },
      "status": "published",
      "revision": 2,
      "created_at": "2026-04-08T12:00:00+00:00",
      "updated_at": "2026-04-08T12:05:00+00:00"
    }
  ],
  "total": 1
}
```

Query parameters:

- `status`: optional lifecycle filter

Notes:

- `PATCH /semantic/entities/{id}/properties` has been removed.
- Legacy entity payloads such as `name`, `keys`, `level`, `join_constraints`, and `properties` are rejected on the HTTP route.

## Metric Contract

`POST /semantic/metrics`

Request:

```json
{
  "header": {
    "metric_ref": "metric.dau",
    "display_name": "DAU",
    "description": "Daily active users",
    "metric_family": "count_metric",
    "observed_entity_ref": "entity.user",
    "observation_grain_ref": "grain.user",
    "sample_kind": "numeric",
    "value_semantics": "count",
    "aggregation_scope": "window",
    "primary_time_ref": "time.activity_date",
    "additivity": "additive",
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "active_users",
      "semantics": "distinct active users",
      "aggregation": "count_distinct"
    }
  }
}
```

Response:

```json
{
  "metric_contract_id": "metc_a1b2c3d4e5f6",
  "header": {
    "metric_ref": "metric.dau",
    "display_name": "DAU",
    "description": "Daily active users",
    "metric_family": "count_metric",
    "population_subject_ref": null,
    "observed_entity_ref": "entity.user",
    "observation_grain_ref": "grain.user",
    "sample_kind": "numeric",
    "value_semantics": "count",
    "aggregation_scope": "window",
    "primary_time_ref": "time.activity_date",
    "additivity": "additive",
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "active_users",
      "semantics": "distinct active users",
      "aggregation": "count_distinct",
      "measure_ref": null,
      "qualifier_refs": null
    }
  },
  "status": "draft",
  "revision": 1,
  "created_at": "2026-04-08T12:00:00+00:00",
  "updated_at": "2026-04-08T12:00:00+00:00"
}
```

List responses are also wrapped as `{"items": [...], "total": n}`.

Query parameters:

- `status`: optional lifecycle filter

Notes:

- Legacy metric payloads such as `definition_sql`, `dimensions`, `grain`, and `measure_type` are rejected on the HTTP route.
- Family-specific payload shape is determined by `header.metric_family` and `payload.metric_family`.

## Error Semantics

- `404`: object not found
- `422`: request validation failed or service rejected the request as invalid

Validation errors use FastAPI/Pydantic `detail` arrays. Service-level validation errors use string `detail` values.
