# Marivo Semantic Layer Reference

Use this file when the task is about **reusable semantic contracts** rather than one-off
investigation work.

Skip this file if you only need the top-level routing choice from `SKILL.md`, or if the task is
limited to session-scoped investigation execution.

This file owns semantic modeling order, object-family heuristics, and dataset-native grounding
rules. Runtime availability troubleshooting is expanded in `semantic-readiness.md`. Global
transport and session rules stay in `http-contracts.md`. For exact schemas and field examples, use
the matching tool and `payload-cheatsheet.md`.

## Core Rules

Marivo's semantic layer is OSI-aligned, typed, and HTTP-first.

- dataset and field are the only persisted physical grounding contract
- `dataset.custom_extensions[].data.datasource_id` selects the datasource
- `dataset.source` names the datasource-local relation FQN
- `field.expression` names a physical column or computed SQL expression
- metrics, dimensions, predicates, and relationships reference datasets and fields
- catalog metadata is live; use datasource browse/preview endpoints before authoring
- runtime and catalog defaults should target semantic objects that are both active and ready
- semantic refs/names and session evidence refs are different things
- predicates define governed, reusable filter semantics consumed by metrics and request scopes
- mappings govern source-to-engine routing and catalog projection when that surface is in play; they are separate from semantic dataset grounding
- domains group objects for discovery and search only; they do not grant permissions or prove compatibility

Do not use storage status as a shortcut for usability.

## Public Object Families

Use the OSI semantic model as the primary authoring surface:

- semantic model
- dataset
- field
- metric
- relationship

Where older typed object families still appear in read surfaces, treat them as compatibility or
adjacent semantic concepts unless the current HTTP contract explicitly asks for them:

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

Do not create a separate physical binding object to ground a metric, dimension, time object,
predicate, or process object. Put physical grounding in OSI datasets and fields.

## Scope-First Modeling

Do not create semantic objects in isolation. Start from the final graph that must become usable.

- for a semantic model, decide visibility/owner and the reusable business area
- for a dataset, decide datasource id, relation FQN, primary key, unique keys, and fields
- for a field, decide the physical column/computed expression, type hint, and whether it is a time dimension
- for a metric, decide observed dataset, expression, primary time field, grain, additivity, and optional filters
- for a relationship, decide dataset pair, field alignment, and cardinality
- for a predicate or governed filter, decide the dataset/field it constrains and whether it belongs in metric extension data

Avoid speculative semantics. If a metric, relationship, or governed predicate is not needed by the
current object graph, do not create it by default.

## Dependency Order

Treat semantic creation order as part of the contract:

1. register or select the datasource
2. inspect live metadata with `/datasources/{id}/browse/schemas`, `/browse/tables`, `/browse/columns`, and `/catalog/preview`
3. identify dataset relations and source columns for identity, time, descriptors, predicates, and metric inputs
4. create or import the OSI semantic model with datasets and fields
5. create relationships and metrics that reference the model's datasets and fields
6. validate/read semantic model readiness
7. repair datasource, relation, or field-expression blockers
8. run one representative typed intent or preview-backed workflow before treating the semantic graph as usable

Operational rule:

- live browse first
- dataset/field grounding second
- dependent metrics/relationships after fields exist
- readiness check before using the model for reusable analysis

For multi-object authoring, prefer importing an OSI document when you already have the full graph.

## Dataset And Field Grounding Rules

Datasets and fields are the physical grounding layer.

Key rules:

- choose `dataset.source` from live browse output
- store the selected datasource id in the dataset MARIVO extension
- define each physical column once as a field expression where possible
- use simple column names for direct fields
- use computed SQL expressions only when the semantic field genuinely needs a derived expression
- keep datasource ids and relation names out of metric and relationship definitions
- downstream objects should reference dataset and field names, not physical locators
- if readiness reports a missing datasource, relation, or field expression, repair the dataset/field first
- if a metric crosses dataset boundaries, model the needed relationship instead of embedding join SQL in the metric

Example dataset fragment:

```json
{
  "name": "orders",
  "source": "analytics.orders",
  "primary_key": ["order_id"],
  "custom_extensions": [
    {
      "vendor_name": "MARIVO",
      "data": "{\"datasource_id\":\"ds_a1b2c3d4e5f6\"}"
    }
  ],
  "fields": [
    {
      "name": "order_id",
      "expression": {
        "dialects": [
          {"dialect": "ANSI_SQL", "expression": "order_id"}
        ]
      }
    }
  ]
}
```

## Discovery And Resolution

Use datasource browse when you need physical metadata:

- schemas: `GET /datasources/{datasource_id}/browse/schemas`
- tables: `GET /datasources/{datasource_id}/browse/tables?schema_name=...`
- columns: `GET /datasources/{datasource_id}/browse/columns?schema_name=...&table_name=...`
- preview: `GET /datasources/{datasource_id}/catalog/preview?schema=...&table=...`

Use semantic model reads when you need persisted semantic contracts:

- list models: `GET /semantic-models`
- read model: `GET /semantic-models/{model}`
- readiness: `GET /semantic-models/{model}/readiness`

Do not present live browse as evidence for an analysis conclusion. It is a grounding and
inspection surface.

## Metric Modeling

Metrics use field-backed expressions and MARIVO metric extension data.

Heuristics:

- use count expressions for volume and distinct subject counts
- use sum expressions for additive amounts, bytes, spend, duration, or units
- use average expressions for mean latency, cost, or consumption
- use ratio/rate expressions for success rate, failure rate, conversion, or hit rate
- set `observed_dataset` to the dataset the metric measures
- set `primary_time_field` when time-scoped analysis is expected
- set additivity explicitly for reusable metrics

Keep metric expressions about measurement, not physical routing. If a metric cannot find its input
field, repair the dataset/field or relationship graph.

## Relationship Modeling

Relationships connect datasets by field names.

Create a relationship when:

- a metric or analysis needs fields from more than one dataset
- two datasets share keys but the join eligibility must be reusable
- cardinality matters for correctness

Do not put raw join SQL, optimizer hints, CTEs, or arbitrary boolean expressions in relationship
contracts.

## Predicate Usage Categories

Each reusable predicate should be clear about where it applies:

- metric qualifier: filters rows for a specific metric contract or measurement component
- request scope: constrains results at the request/intent level

Time-based filtering should normally stay in the intent's structured `time_scope` instead of being
hidden inside a non-obvious predicate.

## Readiness Recovery

Common semantic readiness blockers:

- `datasource_not_found`: create/select a datasource and update the dataset MARIVO extension
- `relation_not_found`: browse live schemas/tables and update `dataset.source`
- `field_expression_invalid`: update `field.expression.dialects[]`
- `datasource_not_ready`: repair datasource connection/configuration

Repair the earliest physical grounding blocker first. Later metric, relationship, or intent errors
are often downstream of a dataset/field problem.

## Modeling Heuristics

- stay in direct session investigation for one-off exploration
- create or revise semantic contracts when the same business concept will be reused
- keep semantic names stable and business-facing
- let datasets and fields absorb physical churn
- let predicates absorb filter churn
- keep object readiness separate from request-level incompatibility

Completion rule:

- a semantic graph is not complete when objects are merely written; it is complete when readiness is
  clean and one representative usage path succeeds.

## Read Next

- Read `semantic-readiness.md` when an active semantic object is still unavailable at runtime.
- Read `payload-cheatsheet.md` when you know what object to create and only need the minimum useful request body.
- Read `infrastructure.md` when the issue is datasource browse, routing, or grounding operations rather than semantic design.
