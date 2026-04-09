# Semantic Layer

The semantic layer exposes typed semantic objects over HTTP. Entity and metric routes now use the target-state typed contract only; legacy payloads and `?surface=typed` are no longer supported on those endpoints.

Semantic lifecycle is shared across objects:

- `draft`
- `published`
- `deprecated`

Only `published` objects are available to runtime resolution and intent execution.

Typed semantic object contract updates are draft-only. After `publish`, the public contract is frozen; a second publish attempt or any later update returns a validation error from the service layer.

Related design docs:

- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/enum-set-schema-contract.zh.md`
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

### Process Objects

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/process-objects` | Create a process object |
| `GET` | `/semantic/process-objects` | List process objects |
| `GET` | `/semantic/process-objects/{process_contract_id}` | Get a process object |
| `PUT` | `/semantic/process-objects/{process_contract_id}` | Update a process object |
| `POST` | `/semantic/process-objects/{process_contract_id}/publish` | Publish a process object |

### Dimensions

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/dimensions` | Create a dimension |
| `GET` | `/semantic/dimensions` | List dimensions |
| `GET` | `/semantic/dimensions/{dimension_contract_id}` | Get a dimension |
| `PUT` | `/semantic/dimensions/{dimension_contract_id}` | Update a dimension |
| `POST` | `/semantic/dimensions/{dimension_contract_id}/publish` | Publish a dimension |

### Time Semantics

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/time` | Create a time semantic |
| `GET` | `/semantic/time` | List time semantics |
| `GET` | `/semantic/time/{time_contract_id}` | Get a time semantic |
| `PUT` | `/semantic/time/{time_contract_id}` | Update a time semantic |
| `POST` | `/semantic/time/{time_contract_id}/publish` | Publish a time semantic |

### Enum Sets

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/enum-sets` | Create an enum set |
| `GET` | `/semantic/enum-sets` | List enum sets |
| `GET` | `/semantic/enum-sets/{enum_set_contract_id}` | Get an enum set |
| `PUT` | `/semantic/enum-sets/{enum_set_contract_id}` | Update an enum set |
| `POST` | `/semantic/enum-sets/{enum_set_contract_id}/publish` | Publish an enum set |

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
- `PUT /semantic/entities/{entity_id}` is only valid while the object is in `draft`.
- `POST /semantic/entities/{entity_id}/publish` is only valid from `draft`; publish increments `revision`.

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

`revision` increments on every persisted contract change, including `PUT` updates and `publish`.

List responses are also wrapped as `{"items": [...], "total": n}`.

Query parameters:

- `status`: optional lifecycle filter

Notes:

- Legacy metric payloads such as `definition_sql`, `dimensions`, `grain`, and `measure_type` are rejected on the HTTP route.
- Family-specific payload shape is determined by `header.metric_family` and `payload.metric_family`.

## Process / Dimension / Time / Enum Set Contracts

All four object families follow the same lifecycle and envelope conventions as entities and metrics:

- create, get, update, and publish return the object detail payload directly
- list returns `{"items": [...], "total": n}`
- `status` is the shared lifecycle filter/query parameter
- `PUT` is only valid while the object is in `draft`
- `POST .../publish` is only valid from `draft`; publish increments `revision`
- create/update may reference draft semantic objects, but publish requires every referenced object to already be `published`
- invalid request shape returns `422`
- unknown object id returns `404`

Representative paths:

- `POST /semantic/process-objects`
- `POST /semantic/dimensions`
- `POST /semantic/time`
- `POST /semantic/enum-sets`

Representative create payload fragments:

```json
{
  "header": {
    "process_ref": "process.new_user_cohort",
    "process_type": "cohort_definition",
    "process_contract_version": "process.v2"
  },
  "interface_contract": {
    "contract_mode": "context_provider",
    "context_kind": "cohort_membership",
    "population_subject_ref": "subject.user",
    "membership_cardinality": "exclusive_one",
    "anchor_time_ref": "time.signup_time",
    "exported_dimension_refs": ["dimension.signup_week"]
  },
  "payload": {
    "process_type": "cohort_definition",
    "cohort_key": "new_users",
    "entry_population": {"base_population_ref": "population.users"},
    "cohort_anchor_ref": "time.signup_time"
  }
}
```

```json
{
  "header": {
    "dimension_ref": "dimension.signup_week",
    "display_name": "Signup Week",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "value_domain": {
      "structure_kind": "time_derived",
      "value_type": "string",
      "domain_kind": "open"
    },
    "time_derived_requirement": {
      "required_time_anchor_ref": "time.signup_time"
    }
  }
}
```

```json
{
  "header": {
    "time_ref": "time.signup_time",
    "display_name": "Signup Time",
    "semantic_roles": ["business_anchor", "measurement"],
    "time_contract_version": "time.v1"
  }
}
```

```json
{
  "header": {
    "enum_set_ref": "enum.country_code",
    "value_type": "string"
  },
  "display_name": "Country Code",
  "description": "ISO country codes",
  "versions": [
    {
      "enum_version": "v1",
      "values": [
        {"value_key": "CN", "raw_value": "CN", "label": "China"},
        {"value_key": "US", "raw_value": "US", "label": "United States"}
      ]
    }
  ]
}
```

## Binding Contract

`POST /semantic/bindings`

Bindings are the target-state physical grounding contract. This is the primary HTTP surface for
carrier / surface / relation wiring; `mapping_json` is not the main semantic API contract anymore.

Request:

```json
{
  "header": {
    "binding_ref": "binding.account_primary",
    "display_name": "Account Binding",
    "description": "Primary warehouse grounding for account identity",
    "binding_scope": "entity",
    "bound_object_ref": "entity.account",
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "carrier_bindings": [
      {
        "binding_key": "primary",
        "carrier_kind": "table",
        "carrier_locator": "warehouse.accounts",
        "binding_role": "primary",
        "field_surfaces": [
          {
            "surface_ref": "field.account_id",
            "physical_name": "account_id"
          }
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "primary",
        "target": {
          "target_kind": "identity_key",
          "target_key": "key.account_id"
        },
        "semantic_ref": "key.account_id",
        "surface_ref": "field.account_id"
      }
    ]
  }
}
```

Response:

```json
{
  "binding_id": "bind_a1b2c3d4e5f6",
  "header": {
    "binding_ref": "binding.account_primary",
    "display_name": "Account Binding",
    "description": "Primary warehouse grounding for account identity",
    "binding_scope": "entity",
    "bound_object_ref": "entity.account",
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "imports": null,
    "carrier_bindings": [
      {
        "binding_key": "primary",
        "source_object_ref": null,
        "carrier_kind": "table",
        "carrier_locator": "warehouse.accounts",
        "binding_role": "primary",
        "semantic_role_ref": null,
        "grain_ref": null,
        "primary_entity_ref": null,
        "row_filter_refs": null,
        "freshness_policy_ref": null,
        "field_surfaces": [
          {
            "surface_ref": "field.account_id",
            "physical_name": "account_id",
            "field_type": null
          }
        ],
        "time_surfaces": null
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "primary",
        "target": {
          "target_kind": "identity_key",
          "target_key": "key.account_id",
          "context_ref": null
        },
        "semantic_ref": "key.account_id",
        "surface_ref": "field.account_id",
        "field_type_ref": null,
        "nullability_policy": null,
        "repeated_value_policy": null
      }
    ],
    "join_relations": null,
    "consumption_policies": null
  },
  "status": "draft",
  "revision": 1,
  "created_at": "2026-04-08T12:00:00+00:00",
  "updated_at": "2026-04-08T12:00:00+00:00"
}
```

`revision` increments on every persisted contract change, including `PUT` updates and `publish`.

List responses use the shared object envelope:

```json
{
  "items": [
    {
      "binding_id": "bind_a1b2c3d4e5f6",
      "header": {
        "binding_ref": "binding.account_primary",
        "display_name": "Account Binding",
        "description": "Primary warehouse grounding for account identity",
        "binding_scope": "entity",
        "bound_object_ref": "entity.account",
        "binding_contract_version": "binding.v1"
      },
      "interface_contract": {
        "imports": null,
        "carrier_bindings": [],
        "field_bindings": [],
        "join_relations": null,
        "consumption_policies": null
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

## Compiler Compatibility Profile Contract

`POST /compiler/compatibility-profiles`

Compatibility profiles are independent compiler-facing artifacts. In the current migration stage,
they are created and managed explicitly over HTTP; this endpoint is a registration/create surface,
not an automatic generation trigger.

Request:

```json
{
  "profile_ref": "compiler_profile.account_count_requirement",
  "profile_kind": "requirement",
  "schema_version": "v1",
  "subject_kind": "metric",
  "subject_ref": "metric.account_count",
  "requirement": {
    "entity_refs": ["entity.account"]
  }
}
```

Response:

```json
{
  "profile_id": "cprof_a1b2c3d4e5f6",
  "profile_ref": "compiler_profile.account_count_requirement",
  "profile_kind": "requirement",
  "schema_version": "v1",
  "subject_kind": "metric",
  "subject_ref": "metric.account_count",
  "requirement": {
    "contract_modes": null,
    "context_kinds": null,
    "entity_refs": ["entity.account"],
    "population_subject_refs": null
  },
  "capability": null,
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

- `subject_kind/profile_kind` combinations are constrained by the typed profile contract.
- `POST /compiler/compatibility-profiles` creates a draft profile artifact; automatic generation,
  if introduced later, belongs to later migration phases rather than this HTTP contract.

## Legacy Mapping Surface

`/semantic/mappings` may still exist in the codebase as a temporary compatibility surface for
unmigrated runtime wiring, but it is not part of the target-state semantic layer contract.
New integrations should use typed bindings instead of authoring new legacy mappings.

## Error Semantics

- `404`: object not found
- `422`: request validation failed or service rejected the request as invalid

Validation errors use FastAPI/Pydantic `detail` arrays. Service-level validation errors use string `detail` values.
