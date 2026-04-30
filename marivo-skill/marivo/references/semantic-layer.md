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
- entity contracts ground entity fields to synced `source_objects`
- predicates define governed, reusable filter semantics consumed by metrics, request scopes, and governance policies
- mappings govern source-to-engine routing and catalog projection; they are separate from entity physical grounding
- domains group objects for discovery and search only; they do not grant permissions or prove compatibility

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
- `relationship.*`
- `compiler_profile.*`
- `domain.*`

Namespace-only rules:

- `key.*` is a contract-value namespace, not a standalone CRUD family
- `grain.*` is a contract-value namespace, not a standalone CRUD family

Use them only as typed contract values, not as objects to create or activate directly.

## Scope-First Modeling

Do not create semantic objects in isolation. Start from the final object graph that must become usable.

- for a `domain`, decide the discovery grouping and aliases only
- for an `entity`, decide identity keys, thin `fields[]`, source object grounding, optional primary time, and stable descriptors
- for a `time` or `dimension`, decide which `entity.<entity>.field.<field>` it describes
- for a `metric`, decide the metric family first, then component `input_field_ref` values, then optional `primary_time_ref`
- for a `predicate`, decide the entity field `target_ref` and filter expression first, then allowed usage categories, then time policy
- for a `process`, decide the population subject first, then anchor time, analysis window, and process context requirements
- for a `relationship` or `compiler_profile`, decide the cross-entity blocker it resolves before creating it

Avoid speculative semantics. If a time semantic, descriptor, or compatibility profile is not needed by the current object graph, do not create it by default.

## Dependency Order

Treat semantic creation order as part of the contract:

1. inspect synced source metadata first
2. discover or create the `domain.*` used for catalog search
3. identify source columns for entity identity, time, stable descriptors, predicates, and metric inputs
4. create `entity.*` with thin `fields[]` and entity-owned physical grounding
5. create `time.*`, `dimension.*`, and `predicate.*` objects that reference fully qualified entity fields
6. create reusable business measures or process semantics: `metric.*` and/or `process.*`
7. create `relationship.*` and `compiler_profile.*` only when cross-entity composition needs them
8. validate and activate in dependency order
9. resolve or search the resulting refs to confirm runtime visibility

Operational rule:

- create discovery context first
- create entity grounding before dependent semantics
- create field-referencing objects after the entity fields exist
- create compatibility artifacts only to resolve real blockers

For multi-object authoring, prefer `POST /semantic/batch` in `dry_run` mode before individual
writes. Batch v1 executes items in submitted order, supports `time`, `dimension`, `entity`,
and `metric`, and returns per-item diagnostics. It is a validation-oriented authoring
surface, not a transactional SQL executor or DAG planner.

## Predicate Usage Categories

Each `predicate.*` must declare at least one `allowed_usage`:

- `metric_qualifier`: filters rows for a specific metric contract or measurement component
- `carrier_row_filter`: filters rows at the entity grounding level
- `request_scope`: constrains results at the request/intent level
- `governance_policy`: enforces filter policies through governance

v1 time policy restriction:

- v1 predicates only support `time_policy="non_time_only"`; time-based filtering must use the intent's `time_scope` directly

## Discovery And Resolution

Use catalog search when you need fuzzy discovery across active semantic objects or synced assets.

Search guidance:

- list domains with `GET /semantic/domains?status=active&q=...` when the business area is unclear
- search domain objects with `/semantic/domain-objects?domain_ref=...&object_type=...&readiness_status=ready`
- use `related_domain_refs` to find adjacent objects; do not treat related domains as authorization or compatibility
- `q` should contain the concrete business term or typed-ref fragment you expect
- narrow `object_type` when you already know the object family
- omit readiness when the default ready-only filter is sufficient
- use `readiness=all` only when blocker inspection is the task

Use explicit typed ref resolution instead of search when the exact ref is already known, such as `metric.watch_time`.

## Activation And Updates

Lifecycle guidance:

1. create semantic objects in `draft`
2. create predicates in `draft` after their target refs (entity, dimension, key) are clear
3. create relationships or compatibility profiles in `draft` after referenced semantics are clear
4. validate when you want a guardrail pass without changing lifecycle
5. activate to move the object into the governed public catalog
6. deprecate when the object should stay readable but stop being the default choice for new work

Updates are draft-only. Once activated, the current public contract is frozen through the public API.

Compiler pipeline gates apply during validation and activation:

- **predicate contract gate**: validates that the predicate expression references valid semantic targets and uses allowed operators
- **usage-level gate**: validates that `allowed_usage` values are consistent with the predicate's target refs
- **scope validation gate**: validates that scope-widening semantics are correct and no conflicting predicates exist

## Entity Fields And Grounding Rules

Entity contracts are the physical grounding layer. Metric, process, dimension, time, and predicate
objects reference entity fields and other semantic refs; they do not own physical grounding in new
authoring flows.

Key rules:

- use the synced source object's `authority_locator` (catalog/schema/table) as the primary identity for routing
- do not shorten the carrier locator when the synced object contains a wider catalog or engine prefix
- resolve source metadata before creating entity fields and entity grounding
- define each physical column once in `entity.interface_contract.fields[]`
- use entity-local `field.*` only inside that entity contract; downstream refs must use `entity.<entity>.field.<field>`
- activate referenced entity/time/dimension/predicate objects before activating dependent metrics, processes, relationships, or compatibility profiles
- do not put `physical_column`, carrier locators, SQL, or table/view names on dimension/time/predicate/metric/process payloads
- dimensions and time objects use `source_field_ref` pointing at a fully qualified entity field
- predicate atoms use `target_ref` pointing at a fully qualified entity field when filtering entity fields
- metric components use `input_field_ref` such as `entity.order.field.pay_amount`
- for average and rate metrics, declare both component fields: `numerator.input_field_ref` and `denominator.input_field_ref`
- process steps, split basis, session events, and state refs use governed refs such as `entity.event.field.event_name`, `predicate.*`, `time.*`, or `event.*`

Field dependency preview:

- entity details expose `field_dependency_graph` so agents can see which dimension/time/predicate/metric/process/profile objects consume each field
- inspect field dependents before renaming or removing an entity field
- if readiness reports a missing field or binding, repair the entity field definition or entity grounding first
- if a metric or process crosses entity boundaries, model the needed relationship/profile instead of adding metric/process-owned grounding
- create `relationship.*` when two entities need key/time/grain/snapshot alignment. The relationship
  references entity fields and time refs only; it must not contain SQL, optimizer hints, CTE shape,
  arbitrary join graph, or boolean-expression DSL.
- create `compiler_profile.*` when a metric/process needs explicit compile-time preconditions:
  `required_relationship_refs`, grain/time/additivity compatibility hints, field profile
  requirements, or governance preflight requirements. The profile does not replace metric/process
  contracts and does not own physical grounding.

Grouped metric-dimension bridge rule:

- grouped `observe(..., dimensions=["dimension.*"])` support relies on the metric's observed entity
  and that entity's stable descriptors

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
4. create `predicate.*` for common filter patterns (regions, platforms, tiers) after entity fields exist
5. expose reusable descriptors through the entity contract
6. reference entity fields directly from metric components
7. verify one representative typed intent before you consider the object graph complete

Subject-first heuristics:

- choose the entity as the thing an analyst would naturally count, segment, and diagnose: a request, session, task run, query, order, ticket, shipment, or alert
- if there is a durable row identifier, use it as the first candidate identity key
- if multiple physical identifiers exist, prefer the one that stays stable across retries, ingestion refreshes, and late-arrival correction

Column classification heuristics:

- identity columns define `key.*` refs and population-subject contracts
- business or execution anchor timestamps define `time.*` refs and runtime time-axis grounding through entity fields
- reusable grouping axes become `dimension.*` and should usually appear as entity `stable_descriptors`
- value-bearing numeric or boolean columns become entity fields referenced by metric components

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

Grounding heuristics:

- create one entity contract that exposes identity, primary time, and stable descriptors analysts will repeatedly use
- do not add metric/process/time/dimension/predicate-owned grounding in new authoring flows
- for grouped `observe` or `detect`, rely on the observed entity's stable descriptors

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
- let entity fields absorb physical churn
- let predicates absorb filter churn
- keep object-level readiness separate from request-level incompatibility

## Read Next

- Read `semantic-readiness.md` when an active semantic object is still unavailable at runtime.
- Read `payload-cheatsheet.md` when you know what object to create and only need the minimum useful request body.
- Read `infrastructure.md` when the issue is source sync, routing, or grounding operations rather than semantic design.
