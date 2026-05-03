# Marivo Semantic Readiness Reference

Use this file when the task is to decide **whether a semantic object is usable now**, why it is blocked, or how to troubleshoot availability without waiting for a runtime failure.

Skip this file if the main task is creating semantic objects from scratch or executing session investigation steps. Those belong in `semantic-layer.md` and `steps.md`.

This file owns lifecycle-versus-readiness troubleshooting. It does not own semantic design or intent sequencing.

## Core Model

Marivo separates two questions:

- `lifecycle_status`: is the object in the governed public catalog
- `readiness_status`: is the object usable for default runtime or catalog consumption right now

Treat them as independent axes.

Current public lifecycle values:

- `draft`
- `active`
- `deprecated`

`validated` is reserved for future public lifecycle expansion. Do not assume current read surfaces emit it.

Current readiness values:

- `not_ready`
- `ready`
- `stale`

## Default Routing Rule

Default runtime, catalog, and UI discovery should prefer semantic objects that are:

- `lifecycle_status=active`
- `readiness_status=ready`

Do not treat storage `status=published` as proof of usability. Do not treat `active` as proof of usability.

## What To Inspect

When a user explicitly wants to inspect an unavailable object:

1. read the semantic object detail surface
2. inspect `blocking_requirements`
3. inspect `capabilities`
4. inspect `dependency_refs`
5. only then decide whether the issue is lifecycle, readiness, or request-level incompatibility

Main readiness-facing fields:

- `lifecycle_status`
- `readiness_status`
- `blocking_requirements`
- `capabilities`
- `dependency_refs`
- `dependent_refs`
- `field_dependency_graph` on entity detail when a blocker names an entity field

Legacy coverage readiness capabilities:

- `required_targets`: target coverage required by the bound semantic contract
- `covered_targets`: targets covered locally by older semantic records
- `imported_covered_targets`: targets covered through eligible published same-source imports
- `missing_required_targets`: required coverage that is still absent
- `covers_required_targets`: true only when required coverage is complete

Use these fields when they appear on older objects. For entity-centric metrics, missing component
`input_field_ref` values such as `numerator.input_field_ref` or `denominator.input_field_ref`
are metric contract problems, not catalog-cache problems. In the current dataset-native path, the
referenced fields must resolve through OSI datasets and fields before runtime validation can use the metric.

Entity-field readiness checks:

- `missing_entity_field`: inspect the entity `fields[]`; add or correct the field there
- `missing_entity_binding`: older blocker name; inspect dataset/field grounding before adding any compatibility object
- `ambiguous_field_ref`: replace local or shorthand refs with `entity.<entity>.field.<field>`
- invalid field type blockers: fix the entity field `value_type` or the dependent time/dimension/predicate/metric contract

## Predicate Readiness

Predicates have additional readiness considerations:

- `allowed_usage` gating: each declared usage must be valid for the predicate's target refs
- `time_policy` restriction: v1 only supports `non_time_only`; predicates targeting `time.*` refs are rejected at validation
- compiler pipeline gates may block readiness:
  - predicate contract gate failures (invalid target refs or operators)
  - usage-level gate failures (inconsistent `allowed_usage` with target refs)
  - scope validation gate failures (conflicting predicates or scope-widening issues)
- predicate lineage and conflict gates may flag readiness issues when predicates conflict at the same scope level

Predicate readiness inspection order:

1. check `lifecycle_status` and `readiness_status` on the predicate detail
2. inspect `blocking_requirements` for gate failures
3. check whether the target `subject_ref` is active and ready
4. check for predicate conflicts reported by the scope validation gate

## Mapping Readiness

Mappings have their own readiness derivation:

- `readiness_status`: `not_ready` or `ready` (derived from source, engine, and catalog mapping state)
- `failure_code`: stable blocker code when not ready

Common mapping failure codes:

- `mapping_inactive`: the mapping or a dependency is in inactive status
- `mapping_invalid_type_combo`: source and engine type combination is not supported
- `mapping_incomplete`: required catalog mappings are missing
- `mapping_invalid_namespace`: catalog namespace is invalid
- `mapping_inactive_dependency`: source or engine is inactive

Mapping readiness inspection order:

1. check `readiness_status` and `failure_code` on the mapping detail
2. inspect whether the source and engine are active
3. inspect `catalog_mappings` for completeness
4. check whether `default_schema` is set when authority locators omit schema

## Readiness Semantics

Use these mental models:

- `not_ready`: the object has not yet met runtime prerequisites
- `ready`: the object currently satisfies its object-level runtime prerequisites
- `stale`: the object was previously aligned, but dependency drift or revision mismatch proves it is no longer reliable

`stale` is not a generic synonym for `not_ready`.

## Boundary Rules

Keep these boundaries explicit:

- object-level readiness is about whether one semantic object is generally available for default consumption
- request-level incompatibility is about whether one specific request can use an otherwise ready object
- request-specific dimension, process, or intent mismatches should not be written back into persistent object readiness
- predicate usage-level gate failures are request-level incompatibilities when the predicate is valid but used in the wrong context

A ready metric can still be incompatible with one specific request.

A ready predicate can still be incompatible with one specific intent's scope requirements.

Entity-centric compiler/readiness blockers use stable lower-case codes when the failure comes from
the new entity-field grounding path:

- `missing_entity_binding`
- `missing_entity_field`
- `ambiguous_field_ref`
- `missing_time_object`
- `invalid_metric_input_type`
- `invalid_time_field_type`
- `invalid_predicate_operand_type`
- `missing_entity_relationship`
- `missing_compatibility_profile`
- `incompatible_grain`
- `incompatible_time_semantics`
- `governance_policy_blocked`
- `permission_denied`

## Typical Troubleshooting Order

- missing grounding or coverage: inspect dataset/field blockers first; do not add metric/time/process-owned grounding
- incomplete entity grounding coverage: inspect `capabilities.missing_required_targets`, the
  dataset-owned `fields`, and any missing relationship/profile
  alignment before changing downstream metric/time/process contracts
- predicate gate failure: inspect predicate detail for contract/usage/scope gate blockers
- mapping readiness failure: inspect mapping detail for failure_code and source/engine status
- relationship failure: inspect missing/inactive endpoint entities, key field existence, and key
  value_type compatibility before changing metric/process contracts
- profile mismatch or subject revision drift: inspect readiness and blockers on the compatibility profile and its subject
- missing cross-entity composition support: search `list_relationships(left_entity_ref=..., right_entity_ref=...)`
  and then `list_compatibility_profiles(left_entity_ref=..., right_entity_ref=..., detail=true)`
- type blocker: check the entity field `value_type`, the dimension `value_domain.value_type`, metric input aggregation, or predicate operator/value compatibility
- grain blocker: check `observation_grain_ref`, relationship grain alignment, and profile requirements
- time blocker: check `primary_time_ref`, time object field type, and relationship valid-time alignment
- governance blocker: inspect predicate/governance policy requirements before changing semantic object grounding
- picker or catalog visibility issue: confirm whether the caller is using the default ready-only view
- runtime failure on a semantic ref: check whether the object is active but not ready before assuming the compiler or engine is broken

## Read Next

- Read `semantic-layer.md` when the blocker requires a semantic design or dependency-order change.
- Read `infrastructure.md` when the blocker is really a datasource browse, routing, mapping, or grounding problem.
