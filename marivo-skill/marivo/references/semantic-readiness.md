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

Binding readiness capabilities:

- `required_targets`: target coverage required by the bound semantic contract
- `covered_targets`: targets covered locally by the binding
- `imported_covered_targets`: targets covered through eligible published same-source imports
- `missing_required_targets`: required coverage that is still absent
- `covers_required_targets`: true only when required coverage is complete

Use these fields before activation. Missing metric family slots such as `metric_input.numerator`
or `metric_input.denominator` are binding coverage problems, not source sync problems. Imported
coverage can satisfy time or subject requirements, but `metric_input` must always be local.

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

## Typical Troubleshooting Order

- missing grounding or binding coverage: inspect blockers on the metric, entity, process, or binding detail
- incomplete binding coverage: inspect `capabilities.missing_required_targets` and whether eligible imports are published and resolve to the same source object
- predicate gate failure: inspect predicate detail for contract/usage/scope gate blockers
- mapping readiness failure: inspect mapping detail for failure_code and source/engine status
- profile mismatch or subject revision drift: inspect readiness and blockers on the compatibility profile and its subject
- picker or catalog visibility issue: confirm whether the caller is using the default ready-only view
- runtime failure on a semantic ref: check whether the object is active but not ready before assuming the compiler or engine is broken

## Read Next

- Read `semantic-layer.md` when the blocker requires a semantic design or dependency-order change.
- Read `infrastructure.md` when the blocker is really a sync, routing, mapping, or grounding problem.
