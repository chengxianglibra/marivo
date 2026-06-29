# Stepwise Semantic Authoring Design

Status: draft design.

This document defines the active agent workflow for Marivo semantic-layer
construction. It supersedes
`docs/specs/semantic/semantic-authoring-design-superseded.md` and complements
`docs/specs/semantic/python-semantic-layer.md`, which owns the Python object
model and decorator contracts.

## Current Public Flow

The public semantic-authoring flow is:

```text
help -> discover -> settle/grill -> author -> verify
```

`marivo-semantic` is the packaged skill contract for applying this flow. The
library surface supplies facts and validation; the agent settles semantic
intent and writes ordinary Python definitions.

## Layer Ownership

- `ms.help("<constructor-or-object>")` owns static authoring contracts:
  constructors, required and optional parameters, allowed values, defaults,
  omit rules, nested parse shapes, and static constraints.
- `md.discover_*` owns bounded runtime datasource evidence. Results are read
  through `.show()` / `.render()` and treated as evidence text, not as stable
  field-access DTOs.
- The agent settles constructor values from help, discovery evidence, catalog
  state, project docs, source SQL/provenance, prior decisions, and user
  answers. If a semantic decision remains unresolved, the agent grills the
  user one decision at a time.
- `ms.verify_object(...)`, load errors, and `ms.readiness(...)` own validation,
  blockers, registry state, and final analysis handoff readiness.

## Authoring Ladder

Build datasource-backed semantic objects in dependency order:

```text
domain -> entity -> dimension -> time_dimension -> measure -> metric
       -> relationship -> cross-entity metric -> derived metric
```

Datasource registration is a prerequisite owned by `marivo.datasource`, not a
semantic ladder rung.

Each active batch is one entity plus one semantic kind, for example
`entity.sales.orders + dimension`, then
`entity.sales.orders + time_dimension`, then
`entity.sales.orders + measure`. Relationship and cross-entity batches may span
multiple entities only when that semantic kind requires the scope.

## Per-Object Cycle

Every semantic object uses the same bounded loop:

1. Read `ms.help("<constructor-or-object>")`.
2. Run the matching bounded discovery call and read its rendered output.
3. Inspect current catalog state with `ms.load()` when reuse or dependencies
   matter.
4. Settle one candidate from evidence, registry facts, project docs, source
   SQL/provenance, prior decisions, and user answers.
5. Ask the user only when semantic intent or business policy remains
   unresolved after the evidence pass.
6. Author exactly one semantic object in Python.
7. Run `ms.verify_object(ref)` and fix failures before advancing.

The skill forbids writing several semantic objects and validating later.

## Datasource Discovery

Datasource evidence comes from `marivo.datasource`:

- `md.discover_entity(...)` for table schema columns, partition columns, and
  entity evidence.
- `md.discover_dimensions(...)` for dimension-shaped column evidence.
- `md.discover_time_dimensions(...)` for temporal column evidence.
- `md.discover_measures(...)` for numeric measure-shaped evidence.
- `md.discover_relationship(...)` for join evidence.
- `md.discover_dimension_values(...)` for current value evidence.
- `md.raw_sql(...)` only for diagnostics that discovery cannot expose.

Inspect table metadata before discovery and use explicit scan scopes:

```python
md.inspect_table(warehouse, orders).show()
md.inspect_partitions(warehouse, orders).show()

scope = md.partition({"dt": "20260625"}, max_rows=1000)
scope = md.unpruned(max_rows=1000)
```

`DatasourceRef`, `TableSource`, and the source constructors such as
`md.table(...)`, `md.parquet(...)`, and `md.csv(...)` are datasource-owned.
Semantic files reference datasource refs and use discovered evidence to author
semantic objects.

## Reference Model

Object-to-object authoring parameters use Ref objects. Prefer refs returned by
earlier declarations or imported from sibling semantic modules. Use
`ms.ref("<kind>.<semantic_id>")` only for explicit forward or cross-file
references, import cycles, or generated-code boundaries.
Sibling semantic modules can import refs from each other regardless of sorted
loader order; a sibling imported by Python before the loader reaches it is not
executed again during the same load.

After an entity is registered, the agent should not re-supply datasource/table
tuples for semantic object parameters. Physical facts remain datasource-owned;
semantic refs remain semantic-owned.

## Verification And Readiness

`ms.verify_object(ref)` is the per-object gate. A failed verification means the
agent fixes the authored object and reruns verification before moving to the
next object.

`ms.readiness(...)` is the final closeout gate for the refs that will be handed
to `marivo-analysis`. Its role is final consistency, parity/richness
aggregation, and blocked-ref detection after per-object verification has
already passed.

## Historical Context

Older design notes in this repository may mention Brief objects or a separate
semantic authoring handoff stage. Those notes are historical. Current agents
should use the flow in this document and the packaged `marivo-semantic` skill.
