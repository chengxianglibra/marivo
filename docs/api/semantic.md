# Semantic Layer

The semantic layer exposes typed semantic objects over HTTP. Entity and metric routes now use the target-state typed contract only; legacy payloads and `?surface=typed` are no longer supported on those endpoints.

Semantic storage lifecycle is still shared across objects during the current migration phase:

- `draft`
- `published`
- `deprecated`

Public semantic lifecycle/readiness is exposed separately:

- `lifecycle_status`: `draft`, `active`, `deprecated`
- `readiness_status`: `not_ready`, `ready`, `stale`

Runtime/catalog surfaces default to `active + ready` semantic objects. `published` remains the
storage status that maps to `lifecycle_status=active`, but callers must not assume `published`
implies runtime availability.

Semantic object responses expose both the legacy storage status and a derived lifecycle/readiness
contract:

**For detail endpoints (GET by ID):**

- `status`: compatibility field backed by storage (`draft`, `published`, `deprecated`)
- `lifecycle_status`: derived public lifecycle (`draft`, `active`, `deprecated`)
  - **Phase A**: `validated` is a reserved value in the type definition but never produced; it will
    become a persisted state in Phase B when a validation step is introduced between draft and active.
- `readiness_status`: derived readiness (`not_ready`, `ready`, `stale`)
  - `published` no longer implies `ready` for entity, metric, or process objects.
  - `stale` is produced when the current metadata can prove an object was aligned and then drifted
    out of readiness, such as a published compatibility profile whose pinned `subject_revision`
    no longer matches the active subject revision, or a published binding/grounded object whose
    carrier or imported binding has drifted away.
- `blocking_requirements`: structured blockers for why an object is not currently ready
  - For `stale` objects, these same blockers are also the explicit stale reasons; there is no
    separate top-level `stale_reason` field.
  - Entity, metric, process, dimension, time, enum set, binding, and compatibility profile routes
    now return object-specific blockers.
- `capabilities`: object-family-specific capability payload
  - Metric, process, dimension, time, enum set, binding, and compatibility profile routes now
    return computed capability flags.
- `dependency_refs`: direct refs or locators the object depends on
  - These surface the immediate semantic/runtime dependencies used for modeling and debugging
    without requiring callers to reverse-engineer every contract payload.
- `dependent_refs`: refs of objects that depend on this object (stubbed as empty list)

**For list endpoints (GET without ID):**

- `blocker_count`: count of blocking requirements for quick filtering
- `capabilities_summary`: summary of key capability flags (boolean only)
- Headers are included but heavy payloads (interface_contract, payload) are omitted
- Readiness is computed from the same full semantic contract used by detail reads; the list surface
  is lightweight in shape only

Storage `status=published` maps to `lifecycle_status=active`.
Readiness is evaluated separately from lifecycle. List routes
accept `lifecycle_status` as the canonical lifecycle filter and `readiness_status` as the
usability filter; `status` remains a compatibility filter and callers should prefer
`lifecycle_status`. The `status` filter only accepts storage values: `draft`, `published`, and
`deprecated`.

**Migration notes:**

- **Phase A:** storage continues using `draft`, `published`, and `deprecated`; `validated` remains
  reserved in public type definitions and validate routes, but it is not persisted.
- **Phase B:** whether `validated` becomes a persisted lifecycle state is deferred to a later
  migration and is intentionally out of scope for the current HTTP contract.
- If an older metadata sqlite still contains semantic `status='active'`, run
  `scripts/migrate-semantic-status-active-to-published.sh` before starting the service.
- Callers should migrate availability checks to `lifecycle_status` plus `readiness_status` instead
  of inferring usability from `status`.

Lifecycle actions are now shared across public semantic object families:

- `POST .../validate`: check-only guardrail pass; does not persist `validated`
- `POST .../activate`: promote a draft object into storage `published` / public lifecycle `active`
- `POST .../deprecate`: move an active object into storage/public `deprecated`
- `POST .../publish`: compatibility alias for `activate`

`activate` does not imply `ready`. Callers must still inspect `readiness_status`,
`blocking_requirements`, and `capabilities`.

**Backward compatibility:**

- Use `detail=true` query parameter on list endpoints to return full object payloads
- Example: `GET /semantic/entities?detail=true` returns full objects instead of lightweight items
- Default behavior (`detail=false`) returns lightweight items
- `/admin?tab=semantic-catalog` uses the lightweight list shape for inventory and fetches detail by
  object id when the operator selects an object. That UI surfaces `lifecycle_status`,
  `readiness_status`, blocker detail, dependencies, dependents, and capabilities directly so
  operators can see why an object is unusable without triggering a runtime failure first.

Current compatibility policy for read routes:

- list endpoints continue returning the same full object payload shape used by current admin and
  integration callers
- readiness-facing callers should consume `lifecycle_status`, `readiness_status`,
  `blocking_requirements`, `capabilities`, and `dependency_refs`
- `status` remains a storage lifecycle compatibility field only; callers must not infer
  `published=ready`

Unknown storage status values will raise `ValueError` at the service layer to catch data integrity
issues early, rather than silently falling back to a default status.

Typed semantic object contract updates are draft-only. After `activate` (or the compatibility alias
`publish`), the public contract is frozen; a second activation attempt or any later update returns a
validation error from the service layer.

The minimal end-to-end semantic closure is:

1. read synced source metadata from `/sources/{source_id}/objects`
2. create typed semantic objects and typed bindings in dependency order while they are in `draft`
3. activate the referenced objects and bindings (`publish` remains a compatibility alias)
4. resolve only ready refs through runtime/catalog surfaces
5. compile typed intent inputs into IR and compile metadata
6. persist the step semantic snapshot for evidence/runtime consumers

Compiler and evidence surfaces must keep semantic refs and canonical refs separate. Typed semantic
refs belong in runtime resolution, compiler metadata, and persisted `typed_semantic_snapshot`
records; canonical refs remain confined to session/state/context read payloads.

## Ref Taxonomy

Current public semantic object families are:

- `entity.*`
- `metric.*`
- `process.*`
- `dimension.*`
- `time.*`
- `enum.*`
- `binding.*`
- `compiler_profile.*`

The semantic layer also uses constrained ref namespaces that are **not** standalone object
families today:

- `key.*` — entity identity keys referenced from entity contracts and typed bindings
- `grain.*` — observation or emitted grain refs referenced from metric/process/binding contracts
- `measure.*` — measure identifiers used inside metric family payloads
- `metric_input.*` — typed binding inputs for metric grounding

These refs are legal contract values, but there are no public create/list/get/publish routes such as
`/semantic/keys` or `/semantic/grains`. Agents should generate them as stable refs inside typed
payloads, not as separate objects.

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
| `POST` | `/semantic/entities/{entity_id}/validate` | Validate a typed entity without persisting lifecycle changes |
| `POST` | `/semantic/entities/{entity_id}/activate` | Activate a typed entity |
| `POST` | `/semantic/entities/{entity_id}/deprecate` | Deprecate a typed entity |
| `POST` | `/semantic/entities/{entity_id}/publish` | Compatibility alias for activate |

### Metrics

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/metrics` | Create a typed metric |
| `GET` | `/semantic/metrics` | List typed metrics |
| `GET` | `/semantic/metrics/{metric_id}` | Get a typed metric |
| `PUT` | `/semantic/metrics/{metric_id}` | Update a typed metric |
| `POST` | `/semantic/metrics/{metric_id}/validate` | Validate a typed metric without persisting lifecycle changes |
| `POST` | `/semantic/metrics/{metric_id}/activate` | Activate a typed metric |
| `POST` | `/semantic/metrics/{metric_id}/deprecate` | Deprecate a typed metric |
| `POST` | `/semantic/metrics/{metric_id}/publish` | Compatibility alias for activate |

### Process Objects

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/process-objects` | Create a process object |
| `GET` | `/semantic/process-objects` | List process objects |
| `GET` | `/semantic/process-objects/{process_contract_id}` | Get a process object |
| `PUT` | `/semantic/process-objects/{process_contract_id}` | Update a process object |
| `POST` | `/semantic/process-objects/{process_contract_id}/validate` | Validate a process object without persisting lifecycle changes |
| `POST` | `/semantic/process-objects/{process_contract_id}/activate` | Activate a process object |
| `POST` | `/semantic/process-objects/{process_contract_id}/deprecate` | Deprecate a process object |
| `POST` | `/semantic/process-objects/{process_contract_id}/publish` | Compatibility alias for activate |

### Dimensions

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/dimensions` | Create a dimension |
| `GET` | `/semantic/dimensions` | List dimensions |
| `GET` | `/semantic/dimensions/{dimension_contract_id}` | Get a dimension |
| `PUT` | `/semantic/dimensions/{dimension_contract_id}` | Update a dimension |
| `POST` | `/semantic/dimensions/{dimension_contract_id}/validate` | Validate a dimension without persisting lifecycle changes |
| `POST` | `/semantic/dimensions/{dimension_contract_id}/activate` | Activate a dimension |
| `POST` | `/semantic/dimensions/{dimension_contract_id}/deprecate` | Deprecate a dimension |
| `POST` | `/semantic/dimensions/{dimension_contract_id}/publish` | Compatibility alias for activate |

### Time Semantics

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/time` | Create a time semantic |
| `GET` | `/semantic/time` | List time semantics |
| `GET` | `/semantic/time/{time_contract_id}` | Get a time semantic |
| `PUT` | `/semantic/time/{time_contract_id}` | Update a time semantic |
| `POST` | `/semantic/time/{time_contract_id}/validate` | Validate a time semantic without persisting lifecycle changes |
| `POST` | `/semantic/time/{time_contract_id}/activate` | Activate a time semantic |
| `POST` | `/semantic/time/{time_contract_id}/deprecate` | Deprecate a time semantic |
| `POST` | `/semantic/time/{time_contract_id}/publish` | Compatibility alias for activate |

### Enum Sets

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/enum-sets` | Create an enum set |
| `GET` | `/semantic/enum-sets` | List enum sets |
| `GET` | `/semantic/enum-sets/{enum_set_contract_id}` | Get an enum set |
| `PUT` | `/semantic/enum-sets/{enum_set_contract_id}` | Update an enum set |
| `POST` | `/semantic/enum-sets/{enum_set_contract_id}/validate` | Validate an enum set without persisting lifecycle changes |
| `POST` | `/semantic/enum-sets/{enum_set_contract_id}/activate` | Activate an enum set |
| `POST` | `/semantic/enum-sets/{enum_set_contract_id}/deprecate` | Deprecate an enum set |
| `POST` | `/semantic/enum-sets/{enum_set_contract_id}/publish` | Compatibility alias for activate |

### Bindings

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/bindings` | Create a typed binding |
| `GET` | `/semantic/bindings` | List typed bindings |
| `GET` | `/semantic/bindings/{binding_id}` | Get a typed binding |
| `PUT` | `/semantic/bindings/{binding_id}` | Update a typed binding |
| `POST` | `/semantic/bindings/{binding_id}/validate` | Validate a typed binding without persisting lifecycle changes |
| `POST` | `/semantic/bindings/{binding_id}/activate` | Activate a typed binding |
| `POST` | `/semantic/bindings/{binding_id}/deprecate` | Deprecate a typed binding |
| `POST` | `/semantic/bindings/{binding_id}/publish` | Compatibility alias for activate |

### Compiler Compatibility Profiles

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/compiler/compatibility-profiles` | Create a compatibility profile |
| `GET` | `/compiler/compatibility-profiles` | List compatibility profiles |
| `GET` | `/compiler/compatibility-profiles/{profile_id}` | Get a compatibility profile |
| `PUT` | `/compiler/compatibility-profiles/{profile_id}` | Update a compatibility profile |
| `POST` | `/compiler/compatibility-profiles/{profile_id}/validate` | Validate a compatibility profile without persisting lifecycle changes |
| `POST` | `/compiler/compatibility-profiles/{profile_id}/activate` | Activate a compatibility profile |
| `POST` | `/compiler/compatibility-profiles/{profile_id}/deprecate` | Deprecate a compatibility profile |
| `POST` | `/compiler/compatibility-profiles/{profile_id}/publish` | Compatibility alias for activate |

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
  "lifecycle_status": "draft",
  "readiness_status": "not_ready",
  "blocking_requirements": [],
  "capabilities": {},
  "status": "draft",
  "revision": 1,
  "created_at": "2026-04-08T12:00:00+00:00",
  "updated_at": "2026-04-08T12:00:00+00:00"
}
```

Validation notes:

- `field_surfaces` are binding-local declarations; they do not require pre-registration.
- `field_bindings[*].surface_ref` must exist on the referenced `carrier_binding_key`.
- Entity bindings must cover all declared identity keys, plus `primary_time_ref` / stable
  descriptors when the bound entity declares them.
- Process bindings must satisfy process-specific targets such as `population_subject`,
  experiment `process_context`, and required join relations for multi-carrier contracts.
- Metric bindings must map family slot names through `target.target_kind = "metric_input"`.
  Valid `target.target_key` values are `count_target`, `measure`, `numerator`, `denominator`,
  `value_component`, and `score_source` depending on the metric family.
- Metric bindings do not add a `dimension` target kind. Imported `dimension.*` consumption stays
  on the existing binding payload and is resolved through compiler/runtime bridge logic.
- A metric may consume imported entity stable descriptors by declaring `imports` against the
  matching published entity binding. In the first bridge stage, only imported
  `stable_descriptor -> dimension.*` public contract targets are eligible.
- Grouped semantic requests such as `observe(..., dimensions=["dimension.cluster"])` only work when
  each requested `dimension.*` is already consumable by the metric, either from the metric's own
  exported dimension set or from an imported entity binding bridge.
- `time_bindings[*].resolution_kind = "timestamp_column"` may additionally declare
  `timestamp_format`. Use `native` for physical timestamp columns, `iso8601_t_naive` for
  string-backed timestamps such as `YYYY-MM-DDTHH:MM:SS`, and strftime-style format strings such
  as `%Y%m%d %H:%M:%S` for custom naive encodings.
- The imported bridge path is strict: the imported binding must be `entity` scope, must match the
  metric's entity anchor, and only contributes `stable_descriptor -> dimension.*` public targets.
- If a grouped request returns `COMPILER_DIMENSION_IMPORT_MISSING`, the usual fix is:
  1. declare the dimension on the entity as a stable descriptor
  2. publish an entity binding that maps it via `target_kind = "stable_descriptor"`
  3. import that binding from the metric binding through `interface_contract.imports[]`
- `POST /semantic/bindings/{binding_id}/publish` additionally requires:
  - the bound semantic object and imported bindings are already `published`
  - referenced `time.*` / `dimension.*` dependencies are already `published`
  - each carrier resolves to a synced `source_object` via `source_object_ref` or `carrier_locator`

OpenAPI notes:

- `POST /semantic/entities` and `PUT /semantic/entities/{entity_id}` publish explicit typed
  request schemas in `components/schemas`.
- `GET /openapi/schemas/TypedEntityCreateRequest` returns the canonical create-body fragment.
- Validation failures now keep the legacy `detail` array and add guided `error` / `guidance`
  fields with contract links and layered payload examples.

Across the semantic layer, create and update routes publish explicit request-body schemas instead of
opaque `object` payloads. The main component names are:

- entities: `TypedEntityCreateRequest`, `TypedEntityUpdateRequest`
- metrics: `TypedMetricCreateRequest`, `TypedMetricUpdateRequest`
- process objects: `ProcessObjectCreateRequest`, `ProcessObjectUpdateRequest`
- dimensions: `DimensionCreateRequest`, `DimensionUpdateRequest`
- time semantics: `TimeCreateRequest`, `TimeUpdateRequest`, `TimeSemanticHeader`
- enum sets: `EnumSetCreateRequest`, `EnumSetUpdateRequest`
- bindings: `TypedBindingCreateRequest`, `TypedBindingUpdateRequest`
- compatibility profiles: `CompatibilityProfileCreateRequest`, `CompatibilityProfileUpdateRequest`

Time semantics intentionally do not publish a standalone `TimeInterfaceContract` schema today
because the current HTTP contract is header-only.

## Complete Modeling Walkthrough

When you want Factum to build a reusable semantic layer from synced source metadata, use this order:

1. read the synced table and column metadata from `/sources/{source_id}/objects`
2. create `time.*` semantics for the business or measurement anchors you need
3. create `enum.*` value sets when a dimension has a governed domain
4. create `dimension.*` contracts
5. create `entity.*` contracts
6. create `metric.*` contracts
7. create `binding.*` contracts that ground the semantic objects to synced `source_objects`
8. publish in dependency order
9. verify with `/semantic/resolve/{typed_ref}` or `/catalog/search`

Recommended naming rules for generated refs:

- derive `entity.*` refs from the business subject, not directly from the table name
- derive `key.*` refs from stable identity fields such as `key.user_id`
- derive `grain.*` refs from the observation unit such as `grain.user`, `grain.session`, or `grain.day`
- derive `binding.*` refs from the grounded carrier such as `binding.user_events_primary`

Example semantic closure over a synced `analytics.user_events` table:

Create a time semantic:

```json
{
  "header": {
    "time_ref": "time.event_date",
    "display_name": "Event Date",
    "semantic_roles": ["measurement"],
    "time_contract_version": "time.v1"
  }
}
```

Create an enum set for a governed dimension:

```json
{
  "header": {
    "enum_set_ref": "enum.country_code",
    "value_type": "string"
  },
  "display_name": "Country Code",
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

Create a dimension:

```json
{
  "header": {
    "dimension_ref": "dimension.country",
    "display_name": "Country",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "value_domain": {
      "structure_kind": "flat",
      "semantic_role": "category",
      "value_type": "string",
      "domain_kind": "enumerated",
      "enum_set_ref": "enum.country_code",
      "enum_version": "v1"
    },
    "grouping": {
      "supports_grouping": true
    }
  }
}
```

Create an entity:

```json
{
  "header": {
    "entity_ref": "entity.user",
    "display_name": "User",
    "entity_contract_version": "entity.v1"
  },
  "interface_contract": {
    "identity": {
      "key_refs": ["key.user_id"],
      "uniqueness_scope": "global",
      "id_stability": "stable"
    },
    "primary_time_ref": "time.event_date",
    "stable_descriptors": [
      {
        "dimension_ref": "dimension.country",
        "cardinality": "one"
      }
    ]
  }
}
```

Create a metric:

```json
{
  "header": {
    "metric_ref": "metric.daily_active_users",
    "display_name": "Daily Active Users",
    "metric_family": "count_metric",
    "observed_entity_ref": "entity.user",
    "observation_grain_ref": "grain.day",
    "sample_kind": "numeric",
    "value_semantics": "count",
    "aggregation_scope": "window",
    "primary_time_ref": "time.event_date",
    "additivity": "additive",
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "active_users",
      "semantics": "Distinct active users",
      "aggregation": "count_distinct"
    }
  }
}
```

Create a typed binding against the synced source object:

```json
{
  "header": {
    "binding_ref": "binding.user_events_primary",
    "display_name": "User Events Primary Binding",
    "binding_scope": "metric",
    "bound_object_ref": "metric.daily_active_users",
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "carrier_bindings": [
      {
        "binding_key": "primary",
        "source_object_ref": "obj_user_events",
        "carrier_kind": "table",
        "carrier_locator": "analytics.user_events",
        "binding_role": "primary",
        "field_surfaces": [
          {"surface_ref": "field.user_id", "physical_name": "user_id"},
          {"surface_ref": "field.event_date", "physical_name": "event_date"},
          {"surface_ref": "field.country", "physical_name": "country"}
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "identity_key", "target_key": "key.user_id"},
        "semantic_ref": "key.user_id",
        "surface_ref": "field.user_id"
      },
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
        "semantic_ref": "time.event_date",
        "surface_ref": "field.event_date"
      },
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "metric_input", "target_key": "count_target"},
        "semantic_ref": "metric_input.active_users",
        "surface_ref": "field.user_id"
      }
    ]
  }
}
```

Publish in dependency order:

1. `time.event_date`
2. `enum.country_code`
3. `dimension.country`
4. `entity.user`
5. `metric.daily_active_users`
6. `binding.user_events_primary`

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
  "lifecycle_status": "draft",
  "readiness_status": "not_ready",
  "blocking_requirements": [],
  "capabilities": {},
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
- Runtime aggregate SQL for typed metrics is compiled from the metric family and metric-input bindings:
  `count_metric -> COUNT(field)` or `COUNT(DISTINCT field)`, `sum_metric -> SUM(field)`,
  `average_metric -> SUM(numerator_field) / NULLIF(COUNT(denominator_field), 0)`, and
  `rate_metric -> SUM(numerator_field) / NULLIF(SUM(denominator_field), 0)`.
- When a typed metric has no legacy `dimensions` payload, runtime dimension discovery falls back to
  the metric's `observed_entity_ref` and reads `stable_descriptor -> dimension.*` mappings from the
  published entity bindings for that entity.
- Sample-summary execution uses a separate per-row value-expression contract. Typed metrics that
  only define aggregate semantics are rejected for `numeric_sample_summary` or `rate_sample_summary`
  instead of being coerced into nested aggregates.

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
carrier / surface / relation wiring.

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
  "subject_revision": null,
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
- `POST /compiler/compatibility-profiles/{profile_id}/publish` freezes the current published
  subject revision into `subject_revision`; if the subject is republished later, compiler treats
  the old profile as stale and rejects it until the profile is republished.

## Runtime Catalog Discovery

Runtime catalog discovery defaults to ready semantic objects and exposes explicit readiness filters
for modeling/admin callers.

`GET /catalog/search?q=...&type=...&readiness=...`

- Supported semantic `type` filters: `entity`, `metric`, `process`, `dimension`, `time`, `binding`
- `asset` remains available as a source-object discovery filter and is not a semantic object kind
- `readiness` supports `ready` (default), `not_ready`, `stale`, and `all`
- MCP `search_catalog(q, type=None, readiness=None)` forwards the same query parameters; omit
  `readiness` to preserve the HTTP default of `ready`
- Semantic results use a unified summary envelope:
  - `object_kind`
  - `object_id`
  - `ref`
  - `name`
  - `display_name`
  - `description`
  - `status`
  - `lifecycle_status`
  - `readiness_status`
  - `blocker_count`
  - `blocking_requirements_preview`
  - `capabilities_summary`
  - `revision`
  - `created_at`
  - `updated_at`
  - `detail_path`
  - `resolve_path`
- `readiness=ready` is the intended end-user default for picker/search UI. Use `readiness=all`
  only when the caller explicitly wants to inspect unavailable objects and surface why-not-ready.
- Asset results additionally expose:
  - `source_id`
  - `object_type`
  - `synced_at`
  - `source_object_path`

`GET /catalog/objects/{object_kind}/{object_id}`

- Canonical follow-up detail read for catalog search results
- Semantic object kinds return the same typed detail envelope shape used by runtime resolution
- Catalog detail remains available for explicit inspection even when an object is `active + not_ready`
- `asset` returns:
  - `object_kind`
  - `object_id`
  - `ref`
  - `source_object`
- `source_object` matches the synced source-object detail from
  `GET /sources/{source_id}/objects/{object_id}`

`GET /semantic/resolve/{name}`

- Runtime resolution is typed-ref first: `entity.*`, `metric.*`, `process.*`, `dimension.*`,
  `time.*`, `binding.*`
- Bare-name aliases remain supported only for `entity` and `metric`
- Default resolution returns only ready semantic objects
- The response is a typed detail envelope:
  - `object_kind`
  - `object_id`
  - `ref`
  - `semantic_object`
  - `status`
  - `revision`
  - `created_at`
  - `updated_at`
- Resolve responses no longer expose legacy `mappings`, `physical_assets`, or legacy object payloads
  derived from legacy mapping tables
- When a typed ref is active but not ready, resolve returns `409` with structured readiness detail:
  - `message`
  - `code`
  - `category`
  - `subject_ref`
  - `object_kind`
  - `lifecycle_status`
  - `readiness_status`
  - `blocking_requirements`
  - `capabilities`
  - `dependency_refs`
- Request-level compatibility is not evaluated on `/semantic/resolve/{typed_ref}`. Compatibility
  failures only surface on compile/intent execution routes that include request context.

`GET /sessions/{session_id}/planner-context`

- Planner context reads only ready typed metric/entity contracts
- `metrics[*]` and `entities[*]` are returned as typed semantic objects
- Planner context no longer exposes `legacy` compatibility blocks derived from legacy tables

## Compiler And Evidence Handoff

When a typed step compiles successfully, the compiler emits:

- a typed IR bundle keyed by semantic refs and binding refs
- compile metadata such as `resolved_metric_ref`, `resolved_binding_refs`, and `ir_plan_id`
- a persisted step metadata snapshot with `metadata_kind = typed_semantic_snapshot`

That snapshot is the handoff point to evidence/runtime consumers. It must not embed canonical refs;
consumers recover semantic meaning from typed step metadata and compiler snapshots behind the scenes.

## Error Semantics

- `400`: invalid catalog type filter or invalid typed semantic ref
- `404`: object not found
- `409`: typed semantic ref exists and is active but not ready for runtime use, or a compile/intent
  request is incompatible with otherwise ready semantic objects
- `422`: request validation failed or service rejected the request as invalid

Validation errors use FastAPI/Pydantic `detail` arrays. Service-level validation errors use string `detail` values.

Request-body validation errors may additionally include:

- `error.code = request_validation_error`
- `error.message`
- `guidance.docs_url`
- `guidance.contract_url`
- `guidance.schema_url` when the endpoint has a dedicated request schema
- `guidance.examples` with layered valid payloads for typed semantic create/update routes
- `guidance.next_action` with the recommended repair order for agents

Recommended remediation order for typed semantic `422` responses:

1. start with `guidance.examples` to find the shortest valid payload shape
2. read `guidance.schema_url` for the exact request model
3. read `guidance.contract_url` when you need the route-scoped OpenAPI fragment
4. use `detail[*].loc` to map the failure to a concrete field path

`guidance.contract_url` points to `GET /openapi/paths/{encoded_path}` where `encoded_path` is the
raw route path encoded with unpadded base64url. For example, `/semantic/entities` becomes
`L3NlbWFudGljL2VudGl0aWVz`.

Common typed semantic request failures:

| Symptom | Correct structure |
| --- | --- |
| Entity create says `header` or `interface_contract` is missing | `POST /semantic/entities` requires both `header` and `interface_contract.identity` |
| Metric create says `payload` is missing or the family mismatches | include both `header.metric_family` and `payload.metric_family`, and keep them identical |
| Metric create says `header.additivity` is missing | include `header.additivity` and use one of `additive`, `semi_additive`, or `non_additive` |
| Metric create says `metric_family` or `value_semantics` is invalid | use a supported pair such as `count_metric -> count`, `sum_metric -> sum`, `average_metric -> mean`, or `rate_metric -> ratio` |
| Metric create says the payload shape is invalid for the family | use the family slot names required by the payload: `count_target` for `count_metric`, `measure` for `sum_metric`, and `numerator` plus `denominator` for `average_metric` and `rate_metric` |
| Dimension create says `value_domain` is missing | nest it under `interface_contract.value_domain` |
| Time create says extra fields are not allowed or `header` is missing | `POST /semantic/time` is header-only today |
| Binding create says required grounding is missing | provide `interface_contract.carrier_bindings` plus `interface_contract.field_bindings` with explicit semantic targets |
| Binding create says `carrier_locator` does not match the resolved source object | use the synced source object's full FQN in `carrier_locator`, not a shortened catalog name |
| Metric binding says `metric_input target_key` is invalid | `target.target_key` must be the metric family slot name such as `count_target`, `measure`, `numerator`, or `denominator`, not `metric_input.*` |
