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

## `time.*`

Required header fields:

- `time_ref`
- `display_name`
- `semantic_roles`
- `time_contract_version`

Minimal example:

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

Add these only when they are part of the contract:

- `interface_contract.primary_time_ref`
- `interface_contract.stable_descriptors`

Minimal example:

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
    "primary_time_ref": "time.event_date"
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
- `allowed_usage`: at least one of `metric_qualifier`, `carrier_row_filter`, `request_scope`, `governance_policy`
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
      "target_ref": "dimension.country",
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
        {"op": "eq", "target_ref": "dimension.country", "value": "US"},
        {"op": "eq", "target_ref": "dimension.platform", "value": "mobile"}
      ]
    },
    "allowed_usage": ["metric_qualifier", "request_scope"],
    "time_policy": "non_time_only"
  }
}
```

Allowed operators: `eq`, `neq`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `between`, `is_null`, `is_not_null`.

Allowed target ref prefixes: `dimension.`, `entity.`, `key.`, `enum.`, `subject.`, `population.`, `event.`, `field.`.

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
- start with the shortest valid payload, then use server guidance if the service returns a `422`

`additivity_constraints` structure:

```json
{
  "additivity_constraints": {
    "kind": "additive"
  }
}
```

For semi-additive metrics, include per-dimension blockers:

```json
{
  "additivity_constraints": {
    "kind": "semi_additive",
    "dimension_blockers": [
      {"dimension_ref": "dimension.date", "reason": "non_additive_across_time"}
    ]
  }
}
```

For non-additive metrics:

```json
{
  "additivity_constraints": {
    "kind": "non_additive",
    "dimension_blockers": [
      {"dimension_ref": "dimension.date", "reason": "non_additive_across_time"}
    ]
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
    "additivity_constraints": {"kind": "additive"},
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

## `binding.*`

Required header fields:

- `binding_ref`
- `display_name`
- `binding_scope`
- `bound_object_ref`
- `binding_contract_version`

Required interface fields:

- at least one `carrier_bindings[]`
- at least one `field_bindings[]`
- add `time_bindings[]` whenever the binding must ground `time.*` semantics for runtime time-axis use

Target rules:

- `entity` bindings allow `identity_key`, `primary_time`, and `stable_descriptor`
- `metric` bindings allow `population_subject`, `primary_time`, and `metric_input`
- `process_object` bindings allow `population_subject`, `primary_time`, `analysis_window_anchor`, and `process_context`
- `metric_input` uses the metric family slot name as `target.target_key`
- `metric_input` uses `semantic_ref=metric_input.<slot_or_name>`, not `metric.*` or `measure.*`
- `time_bindings` must reference declared `time_surface.*` entries, not `field.*`

`carrier_locator` rules:

- use the synced source object's `authority_locator` (catalog/schema/table)
- do not shorten it to `schema.table` if the synced object includes a wider engine or catalog prefix

Minimal example:

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
        "carrier_locator": "trino.analytics.user_events",
        "binding_role": "primary",
        "field_surfaces": [
          {"surface_ref": "field.user_id", "physical_name": "user_id"},
          {"surface_ref": "field.event_date", "physical_name": "event_date"}
        ],
        "time_surfaces": [
          {"surface_ref": "time_surface.event_date", "physical_name": "event_date"}
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "population_subject", "target_key": "key.user_id"},
        "semantic_ref": "key.user_id",
        "surface_ref": "field.user_id"
      },
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "metric_input", "target_key": "count_target"},
        "semantic_ref": "metric_input.count_target",
        "surface_ref": "field.user_id"
      }
    ],
    "time_bindings": [
      {
        "carrier_binding_key": "primary",
        "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
        "semantic_ref": "time.event_date",
        "resolution_kind": "date_column",
        "date_surface_ref": "time_surface.event_date"
      }
    ]
  }
}
```

Average or rate metric input example:

```json
{
  "field_bindings": [
    {
      "carrier_binding_key": "primary",
      "target": {"target_kind": "metric_input", "target_key": "numerator"},
      "semantic_ref": "metric_input.numerator",
      "surface_ref": "field.elapsed_seconds"
    },
    {
      "carrier_binding_key": "primary",
      "target": {"target_kind": "metric_input", "target_key": "denominator"},
      "semantic_ref": "metric_input.denominator",
      "surface_ref": "field.session_count"
    }
  ]
}
```

## `POST /semantic/batch`

Use batch authoring when creating several semantic objects and bindings together.

Batch v1 supports:

- `mode`: `dry_run` or `apply`
- `lifecycle`: `create_only`, `create_and_validate`, or `create_validate_activate`
- `continue_on_error`
- item `kind`: `time`, `dimension`, `entity`, `metric`, or `binding`
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
          "time_contract_version": "time.v1"
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
- defaults v1 can reference whole carrier or time binding defaults by key; local field conflicts are errors, not silent overrides

## Modeling Pattern Boundary

If you need to decide whether a metric binding should import descriptors from an entity binding, or how to structure a reusable entity-plus-metric graph, stop here and read `semantic-layer.md`.

This file should only help once you already know which object you are writing and only need the minimum valid request shape.

## Read Next

- Read `semantic-layer.md` when you still need help choosing the right object graph.
- Read `steps.md` when the next task is validating the resulting objects with typed intents.
