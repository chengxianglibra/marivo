# Marivo Semantic Layer Reference

Use this file when the task is about **reusable semantic contracts** rather than one-off investigation work.

Skip this file if you only need the top-level routing choice from `SKILL.md`, or if the task is limited to session-scoped investigation execution.

This file owns semantic object families, dependency order, activation order, and modeling heuristics. Runtime availability troubleshooting is expanded in `semantic-readiness.md`. Global transport and session rules stay in `http-contracts.md`. For exact schemas and field examples, use the matching tool and `payload-cheatsheet.md`.

## Core Rules

Marivo's semantic layer is typed and HTTP-first.

- public semantic object families expose public lifecycle separately from storage status
- activation is the public catalog boundary
- `publish` is a compatibility alias for `activate`, not a separate lifecycle phase
- runtime and catalog defaults should target objects that are both `lifecycle_status=active` and `readiness_status=ready`
- semantic refs and session evidence refs are different things
- bindings ground semantic objects to synced `source_objects`
- predicates define governed, reusable filter semantics consumed by metrics, request scopes, and governance policies
- mappings govern source-to-engine routing and catalog projection; they are separate from semantic bindings

Do not use storage `status` as a shortcut for usability.

## Public Object Families

Use these public semantic families:

- `entity.*`
- `metric.*`
- `process.*`
- `dimension.*`
- `predicate.*`
- `time.*`
- `enum.*`
- `binding.*`
- `compiler_profile.*`

Namespace-only rules:

- `key.*` is a contract-value namespace, not a standalone CRUD family
- `grain.*` is a contract-value namespace, not a standalone CRUD family
- `metric_input.*` is a binding payload namespace, not a standalone CRUD family

Use them only as typed contract values, not as objects to create or activate directly.

## Scope-First Modeling

Do not create semantic objects in isolation. Start from the final object that must become bindable.

- for an `entity`, decide identity keys first, then optional `primary_time_ref`, then stable descriptors
- for a `metric`, decide the metric family first, then required `metric_input` slots, then optional `primary_time_ref`
- for a `predicate`, decide the target ref and filter expression first, then allowed usage categories, then time policy
- for a `process`, decide the population subject first, then anchor time, analysis window, and process context requirements

Avoid speculative semantics. If a time semantic, descriptor, or compatibility profile is not needed by the current object graph, do not create it by default.

## Dependency Order

Treat semantic creation order as part of the contract:

1. inspect synced source metadata first
2. identify source columns for identity, time, stable descriptors, and metric inputs
3. create foundational shared contracts: `time.*`, then `enum.*` when needed
4. create reusable axes: `dimension.*`
5. create stable business identity: `entity.*`
6. create reusable business measures or process semantics: `metric.*` and/or `process.*`
7. create governed filter semantics: `predicate.*`
8. create physical grounding: `binding.*`
9. create compatibility artifacts only when needed: `compiler_profile.*`
10. validate and activate in dependency order
11. resolve or search the resulting refs to confirm runtime visibility

Operational rule:

- create meaning first
- create filter semantics second
- create grounding third
- create compatibility artifacts last

For multi-object authoring, prefer `POST /semantic/batch` in `dry_run` mode before individual
writes. Batch v1 executes items in submitted order, supports `time`, `dimension`, `entity`,
`metric`, and `binding`, and returns per-item diagnostics. It is a validation-oriented authoring
surface, not a transactional SQL executor or DAG planner.

## Predicate Usage Categories

Each `predicate.*` must declare at least one `allowed_usage`:

- `metric_qualifier`: filters rows for a specific metric binding
- `carrier_row_filter`: filters rows at the carrier level across bindings
- `request_scope`: constrains results at the request/intent level
- `governance_policy`: enforces filter policies through governance

v1 time policy restriction:

- v1 predicates only support `time_policy="non_time_only"`; time-based filtering must use the intent's `time_scope` directly

## Discovery And Resolution

Use catalog search when you need fuzzy discovery across active semantic objects or synced assets.

Search guidance:

- `q` should contain the concrete business term or typed-ref fragment you expect
- narrow `type` when you already know the object family
- omit readiness when the default ready-only filter is sufficient
- use `readiness=all` only when blocker inspection is the task

Use explicit typed ref resolution instead of search when the exact ref is already known, such as `metric.watch_time`.

## Activation And Updates

Lifecycle guidance:

1. create semantic objects in `draft`
2. create predicates in `draft` after their target refs (entity, dimension, key) are clear
3. create bindings or compatibility profiles in `draft` after referenced semantics are clear
4. validate when you want a guardrail pass without changing lifecycle
5. activate to move the object into the governed public catalog
6. deprecate when the object should stay readable but stop being the default choice for new work

Updates are draft-only. Once activated, the current public contract is frozen through the public API.

Compiler pipeline gates apply during validation and activation:

- **predicate contract gate**: validates that the predicate expression references valid semantic targets and uses allowed operators
- **usage-level gate**: validates that `allowed_usage` values are consistent with the predicate's target refs
- **scope validation gate**: validates that scope-widening semantics are correct and no conflicting predicates exist

## Binding And Grounding Rules

Bindings are the physical grounding layer.

Key rules:

- use the synced source object's `authority_locator` (catalog/schema/table) as the primary identity for routing
- do not shorten the carrier locator when the synced object contains a wider catalog or engine prefix
- resolve source metadata before creating bindings
- activate referenced semantic objects before activating dependent bindings or compatibility profiles
- `time_bindings` must reference declared carrier `time_surfaces` with `time_surface.*` refs; do not point `timestamp_surface_ref`, `date_surface_ref`, or `hour_surface_ref` at `field.*`
- binding target kinds are scope-specific: `entity` supports `identity_key`, `primary_time`, and `stable_descriptor`; `metric` supports `population_subject`, `primary_time`, and `metric_input`; `process_object` supports `population_subject`, `primary_time`, `analysis_window_anchor`, and `process_context`
- metric input bindings use family slot names such as `count_target`, `measure`, `numerator`, and `denominator` as `target.target_key`; the `semantic_ref` must use `metric_input.*`
- for average and rate metrics, declare both local metric input slots: `numerator` and `denominator`

Binding coverage preview:

- create/detail readiness capabilities expose `required_targets`, `covered_targets`, `missing_required_targets`, `imported_covered_targets`, and `covers_required_targets`
- inspect `missing_required_targets` before activation; create may be permissive for incomplete graphs, but readiness will show the missing contract coverage

Imported target coverage:

- a metric binding can use a published same-source entity or process binding import to cover `identity_key`, `primary_time`, `stable_descriptor`, or matching `population_subject` requirements
- imports participate only when `imports.required_ref_prefixes` explicitly matches the imported target ref
- `metric_input` never propagates from imports and must be declared locally on the metric binding
- if multiple imports can satisfy the same required target, treat the ambiguity blocker as a modeling issue and choose one explicit import path

Grouped metric-dimension bridge rule:

- metric bindings do not expose a separate `dimension` target kind
- grouped `observe(..., dimensions=["dimension.*"])` support requires the metric binding to import a published entity binding that exposes the requested `dimension.*` as `stable_descriptor`

## Metric Additivity

Metrics use structured `additivity_constraints` instead of a flat `additivity` field.

The structure encodes:

- whether the metric is additive, semi-additive, or non-additive
- per-dimension additivity blockers where applicable
- fine-grained dimension gate validation for semi-additive metrics

Use `additivity_constraints` when creating or updating metrics. The flat `additivity` field is deprecated.

## Reusable Modeling Playbook For Event And Log Tables

Use this playbook for execution histories, request logs, audit trails, task runs, query logs, job runs, and similar row-level fact tables:

1. identify the stable analysis subject first
2. separate source columns into identity, time, descriptors, and metric inputs
3. create one shared `entity.*` for the stable subject before creating a metric family
4. create `predicate.*` for common filter patterns (regions, platforms, tiers) before binding
5. expose reusable descriptors through one published entity binding
6. import those descriptors into metric bindings instead of rebinding the same grouping axes repeatedly
7. verify one representative typed intent before you consider the object graph complete

Subject-first heuristics:

- choose the entity as the thing an analyst would naturally count, segment, and diagnose: a request, session, task run, query, order, ticket, shipment, or alert
- if there is a durable row identifier, use it as the first candidate identity key
- if multiple physical identifiers exist, prefer the one that stays stable across retries, ingestion refreshes, and late-arrival correction

Column classification heuristics:

- identity columns define `key.*` refs and population-subject bindings
- business or execution anchor timestamps define `time.*` refs and runtime time-axis bindings
- reusable grouping axes become `dimension.*` and should usually appear as entity `stable_descriptors`
- value-bearing numeric or boolean columns become metric inputs, not standalone semantic objects

Metric family heuristics:

- use `count_metric` for volume, subject counts, and distinct-entity tracking
- use `sum_metric` for additive resource or value totals such as bytes, spend, duration, or units
- use `average_metric` when the analysis question is about mean cost, mean latency, or mean consumption
- use `distribution_metric` for long-tail questions such as p95 or p99 latency, memory, or size
- use `rate_metric` for success rate, failure rate, conversion rate, or hit rate

Time modeling heuristics:

- prefer the business or execution start time as the primary analysis axis when it exists
- treat ingestion time, sync time, and partition-only dates as operational support unless the task explicitly needs them as the main axis
- for string-backed timestamps, declare `timestamp_format` explicitly instead of relying on `native`
- for date-plus-hour layouts, prefer `date_hour_columns` when the combined fields represent the runtime time axis

Binding heuristics:

- create one entity binding that exposes the stable descriptors analysts will repeatedly group by
- create metric bindings that focus on metric inputs, population subject, and primary time
- for grouped `observe` or `detect`, import `dimension.*` coverage from the entity binding rather than duplicating those mappings in each metric binding

Predicate heuristics:

- create predicates for filter patterns that will be reused across metrics or sessions
- use `metric_qualifier` when the predicate is specific to one metric's row population
- use `request_scope` when the predicate should be available as a general-purpose session filter
- use `governance_policy` when the predicate enforces a mandatory access or visibility constraint

Completion rule:

- a semantic graph is not complete when objects are merely active; it is complete when one representative typed intent succeeds against the ready objects

## Modeling Heuristics

- stay in direct session investigation for one-off exploration
- create or revise semantic contracts when the same business concept will be reused
- keep semantic refs stable and business-facing
- let bindings absorb physical churn
- let predicates absorb filter churn
- keep object-level readiness separate from request-level incompatibility

## Read Next

- Read `semantic-readiness.md` when an active semantic object is still unavailable at runtime.
- Read `payload-cheatsheet.md` when you know what object to create and only need the minimum useful request body.
- Read `infrastructure.md` when the issue is source sync, routing, or grounding operations rather than semantic design.
