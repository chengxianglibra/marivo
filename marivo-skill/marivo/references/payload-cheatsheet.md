# Marivo Payload Cheatsheet

Use this file when you already know **which semantic object or common intent payload to write** and only need the minimum useful request shape.

Skip this file if you still need to decide whether the task is investigation versus modeling, or if you still need lifecycle and dependency-order guidance. Read `SKILL.md` and `semantic-layer.md` first in that case.

This file owns minimum useful payloads for common semantic-layer writes and high-frequency investigation intents. It does not explain when to create an object or how to troubleshoot lifecycle/readiness.

Guardrails:

- keep examples intentionally minimal; this file is not a substitute for the canonical HTTP schema
- prefer the shortest valid payload that matches the object family you already chose
- when the server returns `422` guidance, use that guidance instead of expanding this file into a field-by-field contract manual
- keep modeling decisions such as dependency order, descriptor strategy, or import topology in `semantic-layer.md`
- keep investigation sequencing decisions in `steps.md` and `planning.md`
- entity fields are the only physical grounding owner; downstream objects should reference `entity.<entity>.field.<field>`

## `detect` Intent

Minimal point-anomaly request:

```json
{
  "metric": "metric.latency",
  "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-28"},
  "granularity": "day",
  "patterns": ["point_anomaly"]
}
```

Minimal period-shift request:

```json
{
  "metric": "metric.latency",
  "time_scope": {"kind": "range", "start": "2026-04-20", "end": "2026-04-27"},
  "granularity": "day",
  "patterns": ["period_shift"],
  "sensitivity": "balanced"
}
```

Rules:

- `time_scope` must be the observe-aligned range shape
- `granularity` is top-level
- do not send legacy `time_scope.mode`, `time_scope.current`, or `time_scope.grain`
- omitted `patterns` means point anomaly scanning unless `profile="level_shift"` is set

## `diagnose` Intent

Minimal explicit current-vs-baseline diagnosis:

```json
{
  "mode": "explicit_compare",
  "metric": "metric.latency",
  "current": {
    "time_scope": {"kind": "range", "start": "2026-04-20", "end": "2026-04-27"}
  },
  "baseline": {
    "time_scope": {"kind": "range", "start": "2026-04-13", "end": "2026-04-20"}
  },
  "candidate_dimensions": ["dimension.service", "dimension.error_code"],
  "decomposition_limit": 5
}
```

Minimal auto-detect diagnosis for level shift:

```json
{
  "mode": "auto_detect",
  "metric": "metric.latency",
  "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-28"},
  "granularity": "day",
  "patterns": ["period_shift"],
  "candidate_dimensions": ["dimension.service"],
  "decomposition_limit": 5
}
```

Rules:

- `mode` defaults to `auto_detect`
- use `explicit_compare` when current and baseline windows are already known
- in `explicit_compare`, do not send top-level `time_scope` or `granularity`
- in `auto_detect`, do not send `current` or `baseline`
- if auto-detect returns `no_detect_candidates`, treat it as `needs_attention` and switch to `explicit_compare` when possible

## `domain.*`

Domains are discovery objects only. They do not grant permissions or prove compiler compatibility.

Required fields:

- `domain_ref`
- `display_name`

Minimal example:

```json
{
  "domain_ref": "domain.growth",
  "display_name": "Growth",
  "description": "Acquisition and activation analytics",
  "aliases": ["growth", "activation"]
}
```

## `time.*`

Required header fields:

- `time_ref`
- `display_name`
- `semantic_roles`
- `time_contract_version`
- `source_field_ref` when the time semantic is backed by a source field

Minimal example:

```json
{
  "header": {
    "time_ref": "time.event_date",
    "display_name": "Event Date",
    "semantic_roles": ["measurement"],
    "time_contract_version": "time.v1",
    "source_field_ref": "entity.event.field.event_date"
  }
}
```

## `enum.*`

Required fields:

- `header.enum_set_ref`
- `header.value_type`
- `display_name`
- `versions[].enum_version`
- `versions[].values[]`

Minimal example:

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
        {"value_key": "US", "raw_value": "US", "label": "United States"}
      ]
    }
  ]
}
```

## `dimension.*`

Required fields:

- `header.dimension_ref`
- `header.display_name`
- `header.dimension_contract_version`
- `interface_contract.source_field_ref`
- `interface_contract.value_domain`

Minimal example:

```json
{
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
    }
  }
}
```

## `entity.*`

Required fields:

- `header.entity_ref`
- `header.display_name`
- `header.entity_contract_version`
- `interface_contract.identity.key_refs[]`
- `interface_contract.identity.uniqueness_scope`
- `interface_contract.identity.id_stability`
- `interface_contract.fields[]`
- `interface_contract.binding`

Add these only when they are part of the contract:

- `interface_contract.primary_time_ref`
- `interface_contract.stable_descriptors`

Minimal example:

```json
{
  "catalog_metadata": {"domain_ref": "domain.growth"},
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
    "fields": [
      {"field_ref": "field.user_id", "value_type": "string", "physical_column": "user_id"},
      {"field_ref": "field.event_date", "value_type": "date", "physical_column": "event_date"},
      {"field_ref": "field.country", "value_type": "string", "physical_column": "country"}
    ],
    "binding": {
      "source_object_ref": "obj_user_events",
      "source_object_fqn": "analytics.user_events",
      "carrier_kind": "table"
    },
    "primary_time_ref": "time.event_date",
    "stable_descriptors": [
      {"dimension_ref": "dimension.country", "field_ref": "entity.user.field.country"}
    ]
  }
}
```

## `predicate.*`

Required header fields:

- `predicate_ref` (must start with `predicate.`)
- `subject_ref` (must start with `entity.` or `subject.`)
- `predicate_contract_version` (must start with `predicate.`)

Required interface contract fields:

- `expression`: a `PredicateAtom` or `PredicateConjunction`
- `allowed_usage`: at least one of `metric_qualifier`, `carrier_row_filter`, `request_scope`
- `time_policy`: v1 only supports `non_time_only`

Minimal example (simple equality filter):

```json
{
  "header": {
    "predicate_ref": "predicate.us_mobile_users",
    "display_name": "US Mobile Users",
    "subject_ref": "entity.user",
    "predicate_contract_version": "predicate.v1"
  },
  "interface_contract": {
    "expression": {
      "op": "eq",
      "target_ref": "entity.user.field.country",
      "value": "US"
    },
    "allowed_usage": ["request_scope"],
    "time_policy": "non_time_only"
  }
}
```

Conjunction example (AND-combined filters):

```json
{
  "header": {
    "predicate_ref": "predicate.us_mobile_premium",
    "display_name": "US Mobile Premium",
    "subject_ref": "entity.user",
    "predicate_contract_version": "predicate.v1"
  },
  "interface_contract": {
    "expression": {
      "op": "and",
      "items": [
        {"op": "eq", "target_ref": "entity.user.field.country", "value": "US"},
        {"op": "eq", "target_ref": "entity.user.field.platform", "value": "mobile"}
      ]
    },
    "allowed_usage": ["metric_qualifier", "request_scope"],
    "time_policy": "non_time_only"
  }
}
```

Allowed operators: `eq`, `neq`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `between`, `is_null`, `is_not_null`.

Allowed target ref prefixes: `dimension.`, `entity.`, `key.`, `enum.`, `subject.`, `population.`, `event.`.

When targeting an entity field, use the fully qualified form `entity.<entity>.field.<field>`.

Forbidden target ref prefixes: `time.`, `metric.`, `process.`, `binding.`, `predicate.`, `grain.`, `measure.`, `compiler_profile.`.

## `metric.*`

Required header fields:

- `metric_ref`
- `display_name`
- `metric_family`
- `observed_entity_ref`
- `observation_grain_ref`
- `sample_kind`
- `value_semantics`
- `aggregation_scope`
- `additivity_constraints`
- `metric_contract_version`

Common conditional fields:

- `primary_time_ref`
- `population_subject_ref`

Allowed `metric_family` and `value_semantics` pairs:

| `metric_family` | `value_semantics` | Required payload slots |
| --- | --- | --- |
| `count_metric` | `count` | `count_target` |
| `sum_metric` | `sum` | `measure` |
| `average_metric` | `mean` | `numerator`, `denominator` |
| `rate_metric` | `ratio` | `numerator`, `denominator` |
| `distribution_metric` | `distribution_statistic` | `value_component`, `distribution_spec` |
| `score_metric` | `score` | `score_source` |
| `survival_metric` | `survival_probability` | `survival_spec` |

Payload rules:

- `payload.metric_family` must exactly match `header.metric_family`
- the payload structure must match the selected family slot names
- each measurement component should declare `input_field_ref` as a fully qualified `entity.<entity>.field.<field>` ref
- metric payloads must not include physical table, carrier, or column binding fields
- start with the shortest valid payload, then use server guidance if the service returns a `422`

`additivity_constraints` structure:

```json
{
  "additivity_constraints": {
    "dimension_policy": "all",
    "time_axis_policy": "additive"
  }
}
```

For subset-additive metrics, include explicit additive dimensions:

```json
{
  "additivity_constraints": {
    "dimension_policy": "subset",
    "additive_dimensions": ["dimension.country"],
    "time_axis_policy": "non_additive"
  }
}
```

For non-additive metrics:

```json
{
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  }
}
```

The flat `additivity` field is deprecated. Use `additivity_constraints` instead.

Minimal count metric example:

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
      "semantics": "Distinct active users",
      "input_field_ref": "entity.user.field.user_id",
      "aggregation": "count_distinct"
    }
  }
}
```

Minimal cross-entity rate example:

```json
{
  "header": {
    "metric_ref": "metric.conversion_rate",
    "display_name": "Conversion Rate",
    "metric_family": "rate_metric",
    "observed_entity_ref": "entity.conversion_event",
    "observation_grain_ref": "grain.user",
    "sample_kind": "rate",
    "value_semantics": "ratio",
    "aggregation_scope": "window",
    "primary_time_ref": "time.conversion_at",
    "additivity_constraints": {
      "dimension_policy": "none",
      "time_axis_policy": "non_additive"
    },
    "metric_contract_version": "metric.v1"
  },
  "payload": {
    "metric_family": "rate_metric",
    "numerator": {
      "name": "converted_users",
      "semantics": "Converted users",
      "input_field_ref": "entity.conversion_event.field.converted_users",
      "aggregation": "sum"
    },
    "denominator": {
      "name": "exposed_users",
      "semantics": "Exposed users",
      "input_field_ref": "entity.exposure_event.field.exposed_users",
      "aggregation": "sum"
    }
  }
}
```

## `process.*`

Process objects reference semantic refs and entity fields. They do not carry physical table, view,
SQL, carrier, or column binding fields.

Required header fields:

- `process_ref`
- `display_name`
- `process_type`
- `process_contract_version`

Minimal cohort-style example:

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

## Relationship / Profile Blocker Repair

Use these only when readiness or compiler diagnostics show a real cross-entity blocker.

Minimal relationship example:

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

Minimal compatibility profile example:

```json
{
  "profile_ref": "compiler_profile.signup_conversion_requirement",
  "profile_kind": "requirement",
  "subject_kind": "metric",
  "subject_ref": "metric.conversion_rate",
  "requirement": {
    "required_relationship_refs": ["relationship.exposure_to_signup"],
    "entity_refs": ["entity.exposure", "entity.signup"]
  },
  "catalog_metadata": {"domain_ref": "domain.growth"}
}
```

Rules:

- relationships reference entity fields; they do not contain SQL or join-plan text
- profiles describe compile-time preconditions; they do not own physical grounding
- revalidate profiles after subject revision drift before changing metric/process contracts

## `POST /semantic/batch`

Use batch authoring when creating several semantic objects together.

Batch v1 supports:

- `mode`: `dry_run` or `apply`
- `lifecycle`: `create_only`, `create_and_validate`, or `create_validate_activate`
- `continue_on_error`
- item `kind`: `time`, `dimension`, `entity`, or `metric`
- item `action`: `create`, `validate`, `activate`; `publish` is accepted as an alias for `activate`

Minimal dry-run example:

```json
{
  "mode": "dry_run",
  "lifecycle": "create_only",
  "continue_on_error": true,
  "items": [
    {
      "op_key": "time.event_date",
      "kind": "time",
      "action": "create",
      "payload": {
        "header": {
          "time_ref": "time.event_date",
          "display_name": "Event Date",
          "semantic_roles": ["measurement"],
          "time_contract_version": "time.v1",
          "source_field_ref": "entity.event.field.event_date"
        }
      }
    }
  ]
}
```

Rules:

- `dry_run` validates request and service contracts without writing metadata
- `apply` writes in submitted order and is not all-or-nothing
- batch does not plan a dependency DAG; submit items in dependency order
- batch does not create metric/time/dimension/predicate/process-owned physical grounding

## Modeling Pattern Boundary

If you need to decide how to structure a reusable entity-plus-metric graph, stop here and read `semantic-layer.md`.

This file should only help once you already know which object you are writing and only need the minimum valid request shape.

## Read Next

- Read `semantic-layer.md` when you still need help choosing the right object graph.
- Read `steps.md` when the next task is validating the resulting objects with typed intents.
