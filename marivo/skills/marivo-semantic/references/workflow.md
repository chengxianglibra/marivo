# marivo-semantic workflow

This reference defines the workflow for agents building reusable Marivo
semantic objects. It intentionally does not repeat constructor parameters,
discovery result schemas, or parse recipes. Use `ms.help(...)`,
`md.discover_*`, and `ms.verify_object(...)` for those contracts.

## Object Ladder

Build in dependency order:

```text
1 domain
2 entity
3 dimension
4 time_dimension
5 measure
6 metric
7 relationship
8 cross-entity metric
9 derived metric
```

Datasource registration happens before this ladder and is owned by
`marivo.datasource`.

## Per-Object Cycle

Every datasource-backed semantic object follows this exact loop:

```text
ms.help(...) static contract
  -> md.discover_* datasource evidence
  -> settle from evidence, registry, project docs, and prior decisions
  -> grill the user for unresolved semantic decisions
  -> author exactly one semantic object
  -> ms.verify_object(...)
```

Do not write several semantic objects and verify later. The unit of work is
one semantic object.

## Discovery Before Questions

Before asking the user, inspect the evidence Marivo can already provide:

- `md.discover_entity(...)` for table and entity evidence;
- `md.discover_dimensions(...)` for dimension-shaped column evidence;
- `md.discover_time_dimensions(...)` for temporal column evidence;
- `md.discover_measures(...)` for numeric measure-shaped evidence;
- `md.discover_relationship(...)` for join evidence;
- `md.discover_dimension_values(...)` for current value evidence;
- existing semantic catalog objects via `ms.load()`;
- project docs, source SQL/provenance, and prior decisions when present.

Ask the user only for semantic intent, business policy, or ambiguity that
remains after that evidence pass.

## Grill Gate

Use a grill question only when a semantic decision is unresolved.

Each question must:

- name the semantic object being authored;
- state the evidence already checked;
- ask one decision;
- put the strongest evidence-backed option first when options are justified;
- avoid options not grounded in inspected evidence.

If evidence supports only one path, ask the user to confirm it. If evidence is
too thin for options, ask an open question or run another bounded discovery
query.

Do not ask users for schema, column names, data types, sample values,
join-key viability, or existing object state when Marivo can discover them.

## Author, Verify

After the unresolved decisions are settled, author exactly one object in the
relevant `models/semantic/<domain>/_domain.py` file.

Immediately run:

```python
verify = ms.verify_object("<semantic.ref>")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored object before continuing.")
```

Do not advance to the next object while verification fails.

## Closeout

Before handing refs to `marivo-analysis`, run `ms.readiness(...)` for the refs
that will be analyzed. Do not hand off blocked refs.
