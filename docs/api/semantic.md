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
- API callers can use the lightweight list shape for inventory and fetch detail by
  object id when they need `lifecycle_status`, `readiness_status`, blocker detail,
  dependencies, dependents, and capabilities without triggering a runtime failure first.

Current compatibility policy for read routes:

- list endpoints return lightweight items by default and full object payloads only when callers pass
  `detail=true`
- readiness-facing callers should consume `lifecycle_status`, `readiness_status`,
  `blocking_requirements`, `capabilities`, and `dependency_refs`
- `status` remains a storage lifecycle compatibility field only; callers must not infer
  `published=ready`

Unknown storage status values will raise `ValueError` at the service layer to catch data integrity
issues early, rather than silently falling back to a default status.

Typed semantic object contract updates are draft-only for the currently persisted object row. After
`activate` (or the compatibility alias `publish`), that public revision is frozen; a second
activation attempt or any later row update returns a validation error from the service layer. For
metric maintenance, small same-identity corrections should create a new metric revision under the
same `metric_ref`; `deprecate` is reserved for semantic identity retirement and does not release the
ref for ordinary recreate flows.

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
- `predicate.*`
- `enum.*`
- `binding.*`
- `compiler_profile.*`
- `domain.*` — Domain Catalog discovery objects

The semantic layer also uses constrained ref namespaces that are **not** standalone object
families today:

- `key.*` — entity identity keys referenced from entity contracts and typed bindings
- `grain.*` — observation or emitted grain refs referenced from metric/process/binding contracts
- `measure.*` — measure identifiers used inside metric family payloads
- `metric_input.*` — legacy metric binding input refs; new physical grounding should use
  `entity.<entity>.field.<field>` through entity bindings

These refs are legal contract values, but there are no public create/list/get/publish routes such as
`/semantic/keys` or `/semantic/grains`. Agents should generate them as stable refs inside typed
payloads, not as separate objects.

Related design docs:

- `spec/semantic/entity-schema-contract.zh.md`
- `spec/semantic/metric-v2-schema.zh.md`
- `spec/semantic/dimension-schema-contract.zh.md`
- `spec/semantic/time-schema-contract.zh.md`
- `spec/semantic/enum-set-schema-contract.zh.md`
- `spec/semantic/process-object-schema.zh.md`
- `spec/semantic/typed-binding-contract.zh.md`
- `spec/semantic/compiler-compatibility-profile.zh.md`

## Endpoints

### Domain Catalog

Domain Catalog routes manage discovery objects only. Domain records expose these public fields:
`domain_ref`, `display_name`, `description`, `status`, and `aliases`. `status` is discovery status
only (`active` or `deprecated`); it is not the lifecycle of semantic objects inside the domain.

Semantic objects may expose `catalog_metadata` with `domain_ref`, `related_domain_refs`, and
`aliases`. This metadata is for discovery and search only. `domain_ref` is not an authorization
source; permissions remain governed by governance policy, data authorization, and execution engine
ACL. Domain metadata is also not compiler compatibility truth.
Domain object search supports `entity`, `dimension`, `time`, `predicate`, `metric`, `process`,
`relationship`, and `compatibility_profile`. It can filter by `domain_ref`, `object_type`,
`status`, `lifecycle_status`, `readiness_status`, `related_domain_refs`, and `q`.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/domains` | Create a domain discovery object |
| `GET` | `/semantic/domains` | List domain discovery objects |
| `GET` | `/semantic/domains/{domain_ref}` | Get a domain discovery object |
| `PUT` | `/semantic/domains/{domain_ref}` | Update a domain discovery object |
| `POST` | `/semantic/domains/{domain_ref}/deprecate` | Deprecate a domain discovery object |
| `GET` | `/semantic/domain-objects?domain_ref=...&object_type=...&lifecycle_status=...&readiness_status=...&related_domain_refs=...&q=...` | Search semantic objects by domain catalog metadata |

Create or update a domain before authoring objects that should be discoverable together:

```json
{
  "domain_ref": "domain.growth",
  "display_name": "Growth",
  "description": "Acquisition and activation analytics",
  "aliases": ["growth", "activation"]
}
```

List and search examples:

```text
GET /semantic/domains?status=active&q=growth
GET /semantic/domain-objects?domain_ref=domain.growth&object_type=metric&readiness_status=ready
GET /semantic/domain-objects?related_domain_refs=domain.ads&q=signup
```

`/semantic/domain-objects` returns semantic object summaries with `object_kind`, `ref`,
`display_name`, `catalog_metadata`, `lifecycle_status`, `readiness_status`, `blocker_count`, and
`detail_path`. The result is a discovery view only. A metric can belong to `domain.growth` and
declare `related_domain_refs=["domain.ads"]`, but compile compatibility still comes from entity
fields, relationships, profiles, and governance/readiness checks.

### Entities

`GET /semantic/entities/{entity_id}` accepts either the internal `entity_contract_id` or the
canonical `entity.*` ref for detail reads. Write and lifecycle routes remain internal-id based.
Entity contracts expose `entity_kind` as a lightweight discovery/readiness hint. Supported values are
`business_entity`, `event_entity`, `fact_entity`, `snapshot_entity`, and `derived_entity`; the default
is `business_entity`. `entity_kind` is not compiler or authorization truth: it must not decide SQL
lowering, permission results, or whether fields can serve as metric inputs, dimensions, time anchors,
or process steps.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/entities` | Create a typed entity |
| `GET` | `/semantic/entities` | List typed entities |
| `GET` | `/semantic/entities/{entity_id}` | Get a typed entity |
| `GET` | `/semantic/entities/{entity_id}/field-dependents?field_ref=...` | List structured consumers of one entity field |
| `PUT` | `/semantic/entities/{entity_id}` | Update a typed entity |
| `POST` | `/semantic/entities/{entity_id}/validate` | Validate a typed entity without persisting lifecycle changes |
| `POST` | `/semantic/entities/{entity_id}/activate` | Activate a typed entity |
| `POST` | `/semantic/entities/{entity_id}/deprecate` | Deprecate a typed entity |
| `POST` | `/semantic/entities/{entity_id}/publish` | Compatibility alias for activate |

Entity authoring is the first modeling step after source metadata sync and domain discovery. Entity
is the only semantic object family that directly owns physical grounding to a source object/table/view
and its fields. Downstream `dimension.*`, `time.*`, `predicate.*`, `metric.*`, and `process.*`
objects must reference entity fields as `entity.<entity>.field.<field>` and must not declare their
own table, view, column, carrier, or SQL binding authority.

Entity detail responses include `field_dependency_graph`, keyed by `interface_contract.fields[*].field_ref`.
Each entry lists structured consumers of that field across dimension/time/predicate/metric/process/profile
objects as `{object_kind, ref, usage_paths, usage_count}`. The graph only scans structured ref fields
such as `target_ref`, `required_inputs[*].input_field_ref`, or `*_refs`; it does not scan free-text
descriptions or SQL text. Field consumers must use fully qualified refs such as
`entity.user.field.country`; unqualified refs such as `field.country` are accepted only as entity
local field definitions or route parameters where the entity context is already explicit.

`dimension.*`, `time.*`, and `predicate.*` no longer own physical grounding. When they need a field,
they reference entity fields through `source_field_ref` or predicate atom `target_ref`. Dimension and
time `source_field_ref` values must use the fully qualified form `entity.<entity>.field.<field>`.
Predicate `target_ref` must also use that fully qualified form when filtering entity fields.
Requests that put physical binding fields such as
`physical_column`, carrier locators, or field surfaces on dimension/time/predicate objects fail request
validation. Validate/activate also checks field usage: time objects require date/datetime-compatible
fields, dimensions must match their declared value type, and predicate comparison operators must match
the referenced field type. Type failures are surfaced as `invalid_field_type_for_semantic_object`
blockers/errors instead of SQL execution failures.

Legacy object-level physical binding payloads are rejected instead of being interpreted as a second
source of truth. Examples include `dimension.interface_contract.physical_column`,
`time.header.physical_column`, `predicate.interface_contract.carrier_locator`,
`metric.payload.definition_sql`, `metric.payload.physical_column`,
`process.payload.source_table`, and typed binding writes with `binding_scope="metric"` or
`binding_scope="process_object"`. These requests return `422` with a legacy/unsupported binding
error and guidance to move physical locators onto `entity.interface_contract.fields[]` and
`entity.interface_contract.binding`.

### Metrics

`GET /semantic/metrics/{metric_id}` accepts either the internal `metric_contract_id` or the
canonical `metric.*` ref for detail reads. Write and lifecycle routes remain internal-id based.
For `metric.*` reads, the default ref resolution is latest active revision. Historical reads must use
`metric_ref + revision` so old artifacts can be interpreted against the definition that was active
when they were produced.

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

Metric revision endpoints are the same-identity maintenance path for activated metrics:

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/metrics/{metric_id_or_ref}/revisions` | Create a draft revision for the existing `metric_ref` |
| `GET` | `/semantic/metrics/{metric_ref}/revisions` | List revision history for a stable metric identity |
| `GET` | `/semantic/metrics/{metric_ref}/revisions/{revision}` | Read one explicit revision for audit or replay |
| `POST` | `/semantic/metrics/{metric_id_or_ref}/revisions/{revision}/validate` | Validate a draft revision without changing default resolution |
| `POST` | `/semantic/metrics/{metric_id_or_ref}/revisions/{revision}/activate` | Atomically promote the revision to latest active |

`POST /semantic/metrics` creates a new stable metric identity. It must not be used for spelling,
description, unit label, or other same-identity corrections by appending suffixes such as `_v2`.
Those changes should use metric revision creation. `deprecated` metrics retain `metric_ref`
ownership; creating a new metric with the same ref returns `409 semantic_ref_conflict`.

The v1 revision create payload is full replacement with optimistic concurrency:

```json
{
  "base_revision": 1,
  "change_summary": "Fix unit label from seconds to milliseconds.",
  "expected_change_scope": "unit_display_metadata",
  "replacement": {
    "header": {
      "metric_ref": "metric.avg_blocked_time",
      "metric_contract_version": "metric.v1"
    },
    "payload": {}
  }
}
```

`base_revision`, `change_summary`, and `replacement` are required. The request does not accept
authoritative `compatibility`; `classified_compatibility` is server output. Optional
`expected_compatibility` and `expected_change_scope` are guardrails only; they let callers state
expectations without becoming the compatibility classifier. `accept_classified_compatibility`
allows a caller to retry after inspecting a server classification mismatch.

Revision responses include `classified_compatibility`, `diff_summary`, `affected_dependents`,
`required_actions`, and `can_activate_now`. Compatible display-only revisions return canonical
diff entries but no required actions. Breaking semantic-contract revisions return blocking
`required_actions`. Compiler profile dependents may return `reuse_after_revalidate` actions.
Metric typed-binding revision derivation is a legacy physical-grounding path and is no longer an
authoring entrypoint; metric revisions should be resolved through the entity fields and entity
bindings referenced by the metric contract.

### Process Objects

`GET /semantic/process-objects/{process_contract_id}` accepts either the internal
`process_contract_id` or the canonical `process.*` ref for detail reads.

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

`GET /semantic/dimensions/{dimension_contract_id}` accepts either the internal
`dimension_contract_id` or the canonical `dimension.*` ref for detail reads.
Dimension `interface_contract.source_field_ref` optionally points to the entity field that provides
the dimension value. The dimension still owns value-domain, hierarchy, and grouping semantics; it does
not own a physical column, table, carrier, or binding target.

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

`GET /semantic/time/{time_contract_id}` accepts either the internal `time_contract_id` or the
canonical `time.*` ref for detail reads.
Time `header.source_field_ref` optionally points to the entity field that provides the time value.
The time object owns time roles and calendar/alignment semantics; it does not own a physical column,
carrier, or binding target.

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

### Predicates

`GET /semantic/predicates/{predicate_contract_id}` accepts either the internal
`predicate_contract_id` or the canonical `predicate.*` ref for detail reads.
Predicate objects own governed filter semantics only. Predicate atoms that filter an entity field
must use `target_ref = "entity.<entity>.field.<field>"`; they do not own physical columns, row
filters, carrier locators, or SQL snippets.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/predicates` | Create a predicate |
| `GET` | `/semantic/predicates` | List predicates |
| `GET` | `/semantic/predicates/{predicate_contract_id}` | Get a predicate |
| `PUT` | `/semantic/predicates/{predicate_contract_id}` | Update a predicate |
| `POST` | `/semantic/predicates/{predicate_contract_id}/validate` | Validate a predicate without persisting lifecycle changes |
| `POST` | `/semantic/predicates/{predicate_contract_id}/activate` | Activate a predicate |
| `POST` | `/semantic/predicates/{predicate_contract_id}/deprecate` | Deprecate a predicate |
| `POST` | `/semantic/predicates/{predicate_contract_id}/publish` | Compatibility alias for activate |

Representative create payload:

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth"},
  "header": {
    "predicate_ref": "predicate.active_user",
    "display_name": "Active User",
    "subject_ref": "entity.user",
    "predicate_contract_version": "predicate.v1"
  },
  "interface_contract": {
    "expression": {
      "op": "eq",
      "target_ref": "entity.user.field.is_active",
      "value": true
    },
    "allowed_usage": ["metric_qualifier", "request_scope"],
    "time_policy": "non_time_only"
  }
}
```

### Enum Sets

`GET /semantic/enum-sets/{enum_set_contract_id}` accepts either the internal
`enum_set_contract_id` or the canonical `enum.*` ref for detail reads.

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

`GET /semantic/bindings/{binding_id}` accepts either the internal `binding_id` or the canonical
`binding.*` ref for detail reads.

Typed binding authoring is entity-only. `POST /semantic/bindings`, `PUT /semantic/bindings/{id}`,
`validate`, `activate`, and `publish` reject legacy `binding_scope=metric` or
`binding_scope=process_object` records. Metric and process objects should reference
`entity.field` through their own contracts instead of submitting physical carrier bindings.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/bindings` | Create an entity typed binding |
| `GET` | `/semantic/bindings` | List typed bindings |
| `GET` | `/semantic/bindings/{binding_id}` | Get a typed binding |
| `PUT` | `/semantic/bindings/{binding_id}` | Update an entity typed binding |
| `POST` | `/semantic/bindings/{binding_id}/validate` | Validate an entity typed binding without persisting lifecycle changes |
| `POST` | `/semantic/bindings/{binding_id}/activate` | Activate an entity typed binding |
| `POST` | `/semantic/bindings/{binding_id}/deprecate` | Deprecate a typed binding |
| `POST` | `/semantic/bindings/{binding_id}/publish` | Compatibility alias for activate |
| `POST` | `/semantic/bindings/{binding_id_or_ref}/revisions/derive` | Disabled legacy metric binding revision path |

### Entity Relationships

Relationships are semantic compatibility artifacts for cross-entity composition. They declare
left/right entities, key alignment, optional time alignment, cardinality, grain compatibility, and
snapshot effective-window alignment. They do not accept physical join SQL, optimizer hints, CTE
shapes, arbitrary join graphs, or generic rule-engine payloads.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/semantic/relationships` | Create an entity relationship |
| `GET` | `/semantic/relationships` | List relationships |
| `GET` | `/semantic/relationships/{relationship_id}` | Get a relationship |
| `PUT` | `/semantic/relationships/{relationship_id}` | Update a draft relationship |
| `POST` | `/semantic/relationships/{relationship_id}/validate` | Validate a relationship |
| `POST` | `/semantic/relationships/{relationship_id}/activate` | Activate a relationship |
| `POST` | `/semantic/relationships/{relationship_id}/deprecate` | Deprecate a relationship |
| `POST` | `/semantic/relationships/{relationship_id}/publish` | Compatibility alias for activate |

Representative create payload:

```json
{
  "relationship_ref": "relationship.exposure_to_signup",
  "display_name": "Exposure To Signup",
  "left_entity_ref": "entity.exposure",
  "right_entity_ref": "entity.signup",
  "key_alignment": {
    "left_field_ref": "entity.exposure.field.user_id",
    "right_field_ref": "entity.signup.field.user_id",
    "alignment_kind": "equality"
  },
  "time_alignment": {
    "left_time_ref": "time.exposure_at",
    "right_time_ref": "time.signup_at",
    "alignment_kind": "bounded_after",
    "window": "P7D"
  },
  "cardinality": "many_to_one",
  "grain_compatibility": {
    "left_grain_ref": "grain.exposure",
    "right_grain_ref": "grain.user",
    "compatibility": "many_to_one_rollup"
  },
  "snapshot_effective_window_alignment": {
    "event_time_ref": "time.exposure_at",
    "effective_from_ref": "entity.signup.field.effective_from",
    "effective_to_ref": "entity.signup.field.effective_to"
  },
  "catalog_metadata": {
    "domain_ref": "domain.growth",
    "related_domain_refs": ["domain.ads"]
  }
}
```

Discovery calls for fixing cross-entity blockers:

```text
GET /semantic/relationships?left_entity_ref=entity.exposure&right_entity_ref=entity.signup&status=published
GET /semantic/domain-objects?object_type=relationship&domain_ref=domain.growth&q=signup
```

`missing_entity_relationship` means the compiler could not find an active relationship that covers
the entity pair and requested key/grain/time shape. The caller should create or activate a
relationship with matching `left_entity_ref`, `right_entity_ref`, `key_alignment` field refs, and
the required grain/time/cardinality metadata.

### Compiler Compatibility Profiles

`GET /compiler/compatibility-profiles/{profile_id}` accepts either the internal `profile_id` or the
canonical `compiler_profile.*` ref for detail reads.

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
| `POST` | `/compiler/compatibility-profiles/{profile_id_or_ref}/revalidate` | Revalidate and repin a profile after subject revision drift |

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
  "entity_kind": "business_entity",
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
    ],
    "fields": [
      {
        "field_ref": "field.user_id",
        "value_type": "string",
        "nullable": false,
        "physical_column": "user_id"
      },
      {
        "field_ref": "field.signup_day",
        "value_type": "date",
        "physical_expression_locator": {
          "expression_kind": "date_trunc",
          "input_columns": ["signup_ts"],
          "output_name": "signup_day",
          "parameters": {"unit": "day"}
        }
      }
    ]
  }
}
```

Entity fields are grounding surfaces. A field must define exactly one physical locator:
`physical_column` or a controlled `physical_expression_locator`. The expression locator is not a
raw SQL DSL and must not include `sql`, `raw_sql`, `sql_expression`, `expression`, `template`, or
`lowering_template` parameters. Field locators only map source object columns to execution-side
columns or aliases; metric/process roles stay in their own contracts.

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
  "entity_kind": "business_entity",
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
  "field_dependency_graph": {
    "field.user_id": [],
    "field.country": [
      {
        "object_kind": "predicate",
        "ref": "predicate.us_users",
        "usage_paths": ["interface_contract.expression.target_ref"],
        "usage_count": 1
      }
    ]
  },
  "created_at": "2026-04-08T12:00:00+00:00",
  "updated_at": "2026-04-08T12:00:00+00:00"
}
```

Validation notes:

- `field_surfaces` are binding-local declarations; they do not require pre-registration.
- `field_bindings[*].surface_ref` must exist on the referenced `carrier_binding_key`.
- Entity bindings must cover all declared identity keys, plus `primary_time_ref` / stable
  descriptors when the bound entity declares them.
- Public authoring only accepts entity binding targets: `identity_key`, `primary_time`, and
  `stable_descriptor`.
- `time_bindings` now reference `time_surface.*` entries from the same carrier's
  `time_surfaces`; they no longer reference `field_surfaces`.
- Binding create/detail responses expose coverage preview in `capabilities.required_targets`,
  `covered_targets`, `missing_required_targets`, `imported_covered_targets`, and
  `covers_required_targets`.
- Metric/process bindings are legacy read/history records only. Public authoring of physical
  carrier bindings now accepts only `binding_scope = "entity"`.
- Metrics consume physical data through the entity fields and entity bindings referenced by their
  semantic contract; they do not create their own physical carrier binding.
- Grouped semantic requests such as `observe(..., dimensions=["dimension.cluster"])` only work when
  each requested `dimension.*` is already consumable through the metric's observed entity and any
  required relationship/profile.
- `time_bindings[*].resolution_kind = "timestamp_column"` may additionally declare
  `timestamp_format`. Use `native` for physical timestamp columns, `iso8601_t_naive` for
  string-backed timestamps such as `YYYY-MM-DDTHH:MM:SS`, and strftime-style format strings such
  as `%Y%m%d %H:%M:%S` for custom naive encodings.
- If a grouped request returns `COMPILER_DIMENSION_IMPORT_MISSING`, the usual fix is to declare
  the dimension on the observed entity as a stable descriptor and ensure the entity binding maps it
  via `target_kind = "stable_descriptor"`.
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
- predicates: `PredicateCreateRequest`, `PredicateUpdateRequest`
- enum sets: `EnumSetCreateRequest`, `EnumSetUpdateRequest`
- bindings: `TypedBindingCreateRequest`, `TypedBindingUpdateRequest`
- compatibility profiles: `CompatibilityProfileCreateRequest`, `CompatibilityProfileUpdateRequest`

Time semantics intentionally do not publish a standalone `TimeInterfaceContract` schema today
because the current HTTP contract is header-only.

## Complete Modeling Walkthrough

When building an entity-centric semantic layer from synced source metadata, use this order:

1. create or discover the catalog domain
2. create `entity.*` contracts with thin `fields[]` and entity-owned physical grounding
3. create `time.*`, `dimension.*`, and `predicate.*` objects that reference entity fields
4. create `metric.*` and `process.*` objects that reference entity fields and semantic refs
5. create `relationship.*` / `compiler_profile.*` objects for cross-entity composition when needed
6. validate and activate objects in dependency order
7. run typed intents such as `observe`; compile metadata records resolved entity fields and relationships

Create a domain:

```json
{
  "domain_ref": "domain.growth",
  "display_name": "Growth",
  "description": "Acquisition and activation analytics",
  "aliases": ["growth", "activation"]
}
```

Create an entity with fields and physical grounding. This is the only place where table/view and
column locators are authored in the entity-centric path:

```json
{
  "catalog_metadata": {
    "domain_ref": "domain.growth",
    "related_domain_refs": ["domain.core"],
    "aliases": ["User"]
  },
  "header": {
    "entity_ref": "entity.user",
    "display_name": "User",
    "entity_contract_version": "entity.v4"
  },
  "interface_contract": {
    "identity": {
      "key_refs": ["key.user_id"],
      "uniqueness_scope": "global",
      "id_stability": "stable"
    },
    "fields": [
      {"field_ref": "field.user_id", "value_type": "string", "physical_column": "user_id"},
      {"field_ref": "field.signup_at", "value_type": "datetime", "physical_column": "signup_at"},
      {"field_ref": "field.country", "value_type": "string", "physical_column": "country"},
      {"field_ref": "field.is_active", "value_type": "boolean", "physical_column": "is_active"}
    ],
    "binding": {
      "source_object_ref": "obj_user_events",
      "source_object_fqn": "analytics.user_events",
      "carrier_kind": "table"
    }
  }
}
```

Create time, dimension, and predicate objects by referencing entity fields:

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth"},
  "header": {
    "time_ref": "time.signup_at",
    "display_name": "Signup Time",
    "semantic_roles": ["business_anchor", "measurement"],
    "time_contract_version": "time.v1",
    "source_field_ref": "entity.user.field.signup_at"
  }
}
```

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth"},
  "header": {
    "dimension_ref": "dimension.country",
    "display_name": "Country",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "source_field_ref": "entity.user.field.country",
    "value_domain": {
      "structure_kind": "flat",
      "semantic_role": "category",
      "value_type": "string",
      "domain_kind": "open"
    },
    "grouping": {"supports_grouping": true}
  }
}
```

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth"},
  "header": {
    "predicate_ref": "predicate.active_user",
    "subject_ref": "entity.user",
    "predicate_contract_version": "predicate.v1"
  },
  "interface_contract": {
    "expression": {
      "op": "eq",
      "target_ref": "entity.user.field.is_active",
      "value": true
    },
    "allowed_usage": ["metric_qualifier", "request_scope"],
    "time_policy": "non_time_only"
  }
}
```

Create a single-entity metric:

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth"},
  "header": {
    "metric_ref": "metric.active_users",
    "display_name": "Active Users",
    "metric_family": "count_metric",
    "observed_entity_ref": "entity.user",
    "observation_grain_ref": "grain.user",
    "sample_kind": "numeric",
    "value_semantics": "count",
    "primary_time_ref": "time.signup_at",
    "additivity_constraints": {
      "dimension_policy": "none",
      "time_axis_policy": "non_additive"
    },
    "default_predicate_refs": ["predicate.active_user"],
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "active_users",
      "semantics": "Distinct active users",
      "input_field_ref": "entity.user.field.user_id",
      "aggregation": "count_distinct"
    }
  }
}
```

Create a cross-entity ratio by referencing fields from both entities. Physical grounding still stays
on each entity:

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth", "related_domain_refs": ["domain.ads"]},
  "header": {
    "metric_ref": "metric.signup_conversion_rate",
    "display_name": "Signup Conversion Rate",
    "metric_family": "rate_metric",
    "observed_entity_ref": "entity.signup",
    "observation_grain_ref": "grain.user",
    "sample_kind": "rate",
    "value_semantics": "ratio",
    "primary_time_ref": "time.signup_at",
    "additivity_constraints": {
      "dimension_policy": "none",
      "time_axis_policy": "non_additive"
    },
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "rate_metric",
    "numerator": {
      "name": "signed_up_users",
      "semantics": "Users who signed up",
      "input_field_ref": "entity.signup.field.user_id",
      "aggregation": "count_distinct"
    },
    "denominator": {
      "name": "exposed_users",
      "semantics": "Users exposed to campaign",
      "input_field_ref": "entity.exposure.field.user_id",
      "aggregation": "count_distinct"
    }
  }
}
```

Create a process object without physical binding:

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth"},
  "header": {
    "process_ref": "process.signup_cohort",
    "display_name": "Signup Cohort",
    "process_type": "cohort_definition",
    "process_contract_version": "process.v2"
  },
  "interface_contract": {
    "contract_mode": "context_provider",
    "context_kind": "cohort_membership",
    "population_subject_ref": "subject.user",
    "membership_cardinality": "exclusive_one",
    "anchor_time_ref": "time.signup_at"
  },
  "payload": {
    "process_type": "cohort_definition",
    "cohort_key": "signup_cohort",
    "entry_population": {"base_population_ref": "population.signed_up_users"},
    "cohort_anchor_ref": "time.signup_at"
  }
}
```

Create relationship/profile objects for the cross-entity metric:

```json
{
  "relationship_ref": "relationship.exposure_to_signup",
  "display_name": "Exposure To Signup",
  "left_entity_ref": "entity.exposure",
  "right_entity_ref": "entity.signup",
  "key_alignment": {
    "left_field_ref": "entity.exposure.field.user_id",
    "right_field_ref": "entity.signup.field.user_id"
  },
  "cardinality": "many_to_many",
  "catalog_metadata": {"domain_ref": "domain.growth", "related_domain_refs": ["domain.ads"]}
}
```

```json
{
  "profile_ref": "compiler_profile.signup_conversion_requirement",
  "profile_kind": "requirement",
  "subject_kind": "metric",
  "subject_ref": "metric.signup_conversion_rate",
  "requirement": {
    "required_relationship_refs": ["relationship.exposure_to_signup"],
    "entity_refs": ["entity.exposure", "entity.signup"]
  },
  "catalog_metadata": {"domain_ref": "domain.growth"}
}
```

Validate and activate in dependency order:

```text
POST /semantic/entities/{entity_id}/validate
POST /semantic/entities/{entity_id}/activate
POST /semantic/time/{time_contract_id}/activate
POST /semantic/dimensions/{dimension_contract_id}/activate
POST /semantic/predicates/{predicate_contract_id}/activate
POST /semantic/relationships/{relationship_id}/activate
POST /semantic/metrics/{metric_id}/activate
POST /compiler/compatibility-profiles/{profile_id}/activate
```

Compile happens through typed intent routes rather than raw SQL:

```text
POST /sessions/{session_id}/intents/observe
{
  "metric": "metric.active_users",
  "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
  "dimensions": ["dimension.country"]
}
```

The resulting step metadata freezes `resolved_entity_field_refs`,
`resolved_entity_field_sources`, `resolved_relationship_refs`, and the typed semantic snapshot so
later changes to entity fields or bindings do not alter artifact interpretation.

## Batch semantic authoring

`POST /semantic/batch` accepts ordered semantic authoring operations for `time`, `dimension`,
`entity`, `metric`, and `binding`.

```json
{
  "mode": "dry_run",
  "lifecycle": "create_only",
  "continue_on_error": true,
  "items": [
    {"op_key": "time.event_date", "kind": "time", "action": "create", "payload": {}},
    {"op_key": "binding.dau", "kind": "binding", "action": "create", "payload": {}}
  ]
}
```

Batch validates request and service contracts item-by-item. `dry_run` does not write metadata.
`apply` creates objects and can optionally validate/activate the objects it just created. `publish`
is accepted as an alias for `activate`. For `create_validate_activate`, create operations are
internally ordered by dependency class and the response includes `readiness_summary` so callers can
inspect final metric readiness after related bindings activate. Batch is not transactional.

For entity-centric authoring, prefer putting physical grounding directly on entity
`interface_contract.fields[]` and `interface_contract.binding`. Batch `binding` items and
`defaults.carrier_bindings` / `defaults.time_bindings` remain available for the legacy entity typed
binding API, but they are not used to ground metric or process objects.

Publish in dependency order:

1. `time.event_date`
2. `enum.country_code`
3. `dimension.country`
4. `entity.user`
5. `metric.daily_active_users`

```
GET /semantic/grains
```

Returns grain refs already observed in metric headers, process objects, and carrier bindings. Grain
refs are explicit semantic identity inputs such as `observation_grain_ref`; they are not auto-created
governance objects.

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
    "additivity_constraints": {
      "dimension_policy": "none",
      "time_axis_policy": "non_additive"
    },
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "active_users",
      "semantics": "distinct active users",
      "input_field_ref": "entity.user.field.user_id",
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
    "additivity_constraints": {
      "dimension_policy": "none",
      "time_axis_policy": "non_additive"
    },
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "count_metric",
    "count_target": {
      "name": "active_users",
      "semantics": "distinct active users",
      "input_field_ref": "entity.user.field.user_id",
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
- Measurement components declare `input_field_ref` as a fully qualified `entity.<entity>.field.<field>` ref.
  Metric objects do not carry physical table, view, or column bindings.
- Cross-entity metrics can reference fields from multiple entities. Readiness returns
  `missing_compatibility_profile` until the required relationship/profile surface exists.
- Runtime lowering resolves component `input_field_ref` through the referenced entity's grounding.
- When a typed metric has no legacy `dimensions` payload, runtime dimension discovery falls back to
  the metric's `observed_entity_ref` and reads `stable_descriptor -> dimension.*` mappings from the
  published entity bindings for that entity.
- Sample-summary execution uses a separate per-row value-expression contract. Typed metrics that
  only define aggregate semantics are rejected for `numeric_sample_summary` or `rate_sample_summary`
  instead of being coerced into nested aggregates.

## Process / Dimension / Time / Enum Set Contracts

Process, dimension, time, predicate, and enum set object families follow the same lifecycle and
envelope conventions as entities and metrics:

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
- `POST /semantic/predicates`
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

Process payload fields that identify steps, split basis, session events, or state predicates must
use governed refs: `entity.<entity>.field.<field>`, `time.*`, `predicate.*`, `dimension.*`,
`event.*`, or `population.*`. Process objects do not carry physical table/view/column bindings.

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

Target-state physical grounding is authored on entity interface fields and
`entity.interface_contract.binding`. `/semantic/bindings` is retained only as a legacy
compatibility and diagnostic route for existing binding records; new carrier, surface, and relation
wiring should be expressed through the entity contract.

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

`POST /semantic/bindings/{binding_id_or_ref}/revisions/derive` is disabled in the entity-centric
model because it was a legacy metric physical-binding completion path. Metric input coverage should
be modeled by metric contracts referencing `entity.field` and resolved through entity binding.

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
    "entity_refs": ["entity.account"],
    "required_relationship_refs": ["relationship.account_to_snapshot"],
    "grain_compatibility": {
      "required_grain_refs": ["grain.account_day"],
      "compatibility": "same_grain"
    },
    "time_compatibility": {
      "alignment_basis": "event_time"
    },
    "field_profile_requirements": [
      {
        "field_ref": "entity.account.field.account_id",
        "required_value_type": "string"
      }
    ],
    "governance_preflight": {
      "required_checks": ["sensitivity_tags"]
    }
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
    "population_subject_refs": null,
    "required_relationship_refs": ["relationship.account_to_snapshot"]
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
- `subject_kind` / `subject_ref`: optional subject filters
- `left_entity_ref` / `right_entity_ref`: optional entity-pair discovery filters for profiles whose
  requirement references those entities directly or through `required_relationship_refs`

Notes:

- `subject_kind/profile_kind` combinations are constrained by the typed profile contract.
- Requirement profiles may include `required_relationship_refs`, key/grain/time/additivity
  compatibility hints, field profile requirements, and governance preflight requirements. These are
  compiler preconditions only; they do not replace metric/process contracts and do not bind physical
  tables or columns.
- `POST /compiler/compatibility-profiles` creates a draft profile artifact; automatic generation,
  if introduced later, belongs to later migration phases rather than this HTTP contract.
- `POST /compiler/compatibility-profiles/{profile_id}/publish` freezes the current published
  subject revision into `subject_revision`; if the subject is republished later, compiler treats
  the old profile as stale and rejects it until the profile is republished.
- `POST /compiler/compatibility-profiles/{profile_id_or_ref}/revalidate` updates validation
  evidence after subject revision drift by pinning `subject_revision` to the requested revision, or
  to the current active subject revision when omitted. The requested revision must be the active
  published subject revision.
- `missing_compatibility_profile` means the metric/process references multiple entities or requires
  compiler preconditions that are not covered by an active profile for the subject. Discover existing
  profiles with `GET /compiler/compatibility-profiles?subject_kind=metric&subject_ref=...` or create
  a requirement profile that lists the subject, entity refs, required relationship refs, field profile
  requirements, and governance preflight requirements needed by the compiler.
- `stale` profiles usually mean the `subject_revision` pinned by the profile no longer matches the
  active subject revision. Use `POST /compiler/compatibility-profiles/{profile_id_or_ref}/revalidate`
  after reviewing the changed subject and dependent relationship/profile requirements.

## Runtime Catalog Discovery

Runtime catalog discovery defaults to ready semantic objects and exposes explicit readiness filters
for modeling and integration callers.

`GET /catalog/search?q=...&type=...&readiness=...`

- Supported semantic `type` filters: `entity`, `metric`, `process`, `dimension`, `time`,
  `binding`, `calendar_policy`
- `asset` remains available as a source-object discovery filter and is not a semantic object kind
- `calendar_policy.*` results are compiler-owned builtin catalog entries. They are discoverable and
  resolvable but do not expose public CRUD or semantic-object lifecycle operations.
- `readiness` supports `ready` (default), `not_ready`, `stale`, and `all`
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

- a typed IR bundle keyed by semantic refs and, for entity-centric objects, resolved entity fields
- compile metadata such as `resolved_metric_ref`, `resolved_entity_field_refs`,
  `resolved_entity_field_sources`, `resolved_relationship_refs`,
  `resolved_relationship_sources`, `resolved_binding_refs`, and `ir_plan_id`
- a persisted step metadata snapshot with `metadata_kind = typed_semantic_snapshot`

That snapshot is the handoff point to evidence/runtime consumers. It must not embed canonical refs;
consumers recover semantic meaning from typed step metadata and compiler snapshots behind the scenes.

Entity-centric compiler snapshots freeze each consumed entity field with its entity revision and
physical locator (`source_object_ref` or `source_object_fqn`, plus `physical_column` or
`physical_expression_locator`). Metric/process objects do not provide physical grounding in this
path; lowering uses entity field grounding and keeps typed analysis steps as the external contract.
When cross-entity composition uses relationship/profile checks, the snapshot also freezes resolved
relationship refs, revisions, key alignment, time alignment, cardinality, grain compatibility, and
snapshot effective window alignment.

Stable semantic blocker codes used by the entity-centric compiler/readiness path include:

- `missing_entity_binding`: the referenced entity has no active binding/locator that can ground its
  fields to a synced source object.
- `missing_entity_field`: a contract references `entity.<entity>.field.<field>`, but that field is
  absent from the active entity contract or cannot be resolved in the active entity revision.
- `ambiguous_field_ref`: a field ref is unqualified or matches multiple entity fields; use the fully
  qualified `entity.<entity>.field.<field>` form.
- `missing_time_object`: a metric/process/entity references `time.*`, but the time object is absent
  or not active/ready.
- `invalid_metric_input_type`: a metric aggregation consumes a field whose declared `value_type` is
  incompatible with the metric family or aggregation.
- `invalid_time_field_type`: a `time.*` object or time role points at a field that is not
  date/datetime-compatible.
- `invalid_predicate_operand_type`: a predicate operator/value is incompatible with the referenced
  entity field type.
- `missing_entity_relationship`: cross-entity composition needs a relationship covering the entity
  pair, key alignment, cardinality, grain, and time/snapshot alignment.
- `missing_compatibility_profile`: compile requirements for the subject are not captured by an
  active compiler profile.
- `incompatible_grain`: the requested metric/process/dimension composition cannot be proven at the
  required grain from active relationship/profile metadata.
- `governance_preflight_blocked`: sensitivity tags, field governance metadata, or declared profile
  preflight checks block compile until the required governance evidence is present.

## Error Semantics

- `400`: invalid catalog type filter or invalid typed semantic ref
- `404`: object not found
- `409`: typed semantic ref exists and is active but not ready for runtime use, a compile/intent
  request is incompatible with otherwise ready semantic objects, or a semantic create request
  conflicts with an existing governed ref
- `422`: request validation failed or service rejected the request as invalid

Validation errors use FastAPI/Pydantic `detail` arrays. Service-level semantic errors may return
structured `detail` objects with `message`, `code`, `category`, `field_path`, `error`, and
`guidance`.

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

### Semantic Ref Conflict

`POST /semantic/metrics` returns `409 semantic_ref_conflict` when `header.metric_ref` is already
owned by an existing metric. The conflict applies to `draft`, `published`, and `deprecated`
objects. `deprecated` means the semantic identity should stop being used for new work; it does not
release the ref for replacement through create.

The response guidance includes the existing metric id, ref, lifecycle status, revision, and
recommended next actions. For spelling, description, or unit-label corrections, do not create
`metric.*_v2` as a routine workaround. Inspect the existing object and use the metric revision path
when it is available. Only clone with a new ref when the new object represents a different business
semantic identity.

Common typed semantic request failures:

| Symptom | Correct structure |
| --- | --- |
| Entity create says `header` or `interface_contract` is missing | `POST /semantic/entities` requires both `header` and `interface_contract.identity` |
| Metric create says `payload` is missing or the family mismatches | include both `header.metric_family` and `payload.metric_family`, and keep them identical |
| Metric create says `header.additivity_constraints` is missing | include `header.additivity_constraints` with `dimension_policy` (`"all"`, `"subset"`, or `"none"`) and `time_axis_policy` (`"additive"` or `"non_additive"`) |
| Metric create says `metric_family` or `value_semantics` is invalid | use a supported pair such as `count_metric -> count`, `sum_metric -> sum`, `average_metric -> mean`, or `rate_metric -> ratio` |
| Metric create says the payload shape is invalid for the family | use the family slot names required by the payload: `count_target` for `count_metric`, `measure` for `sum_metric`, and `numerator` plus `denominator` for `average_metric` and `rate_metric` |
| Dimension create says `value_domain` is missing | nest it under `interface_contract.value_domain` |
| Time create says extra fields are not allowed or `header` is missing | `POST /semantic/time` is header-only today |
| Binding create says required grounding is missing | provide `interface_contract.carrier_bindings` plus `interface_contract.field_bindings` with explicit semantic targets |
| Binding create says `carrier_locator` does not match the resolved source object | use the synced source object's full FQN in `carrier_locator`, not a shortened catalog name |
| Binding create says `typed_binding_scope_not_authorable` | create an entity binding; metric/process carrier bindings are legacy read/history records only |
