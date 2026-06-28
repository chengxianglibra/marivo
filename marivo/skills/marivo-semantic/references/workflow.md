# marivo-semantic workflow notes

The canonical authoring flow lives in `SKILL.md`. This reference only adds
supporting notes for evidence sources, Ref handling, and handoff checks. It
intentionally does not repeat constructor parameters, discovery result schemas,
parse recipes, or the flow-control state machine.

## Discovery Before Questions

Before asking the user, inspect the evidence Marivo can already provide:

- `md.discover_entity(...)` for table schema columns, partition columns, and
  entity evidence;
- `md.discover_dimensions(...)` for dimension-shaped column evidence;
- `md.discover_time_dimensions(...)` for temporal column evidence;
- `md.discover_measures(...)` for numeric measure-shaped evidence;
- `md.discover_relationship(...)` for join evidence;
- `md.discover_dimension_values(...)` for current value evidence;
- existing semantic catalog objects via `ms.load()`;
- project docs, source SQL/provenance, and prior decisions when present.

Read discovery results through `.show()` / `.render()`. Do not depend on
concrete discovery result classes or internal evidence fields.

Ask the user only for semantic intent, business policy, or ambiguity that
remains after that evidence pass.

When the canonical flow reaches the grill step, each question must:

- name the semantic object being authored;
- state the evidence already checked;
- ask one decision;
- put the strongest evidence-backed option first when options are justified;
- avoid options not grounded in inspected evidence.

If evidence supports only one path, ask the user to confirm it. If evidence is
too thin for options, ask an open question or run another bounded discovery
query. Use `md.raw_sql(...)` only for diagnostics that discovery cannot expose;
metadata statements such as `SHOW`, `DESCRIBE`, `DESC`, and `EXPLAIN` are
supported diagnostics, not semantic expression bodies.

Do not ask users for schema, column names, data types, sample values,
join-key viability, or existing object state when Marivo can discover them.

## Ref Handling

Use Ref objects for every semantic-object parameter. Pass refs returned by
previous declarations, import refs from sibling semantic modules, or use
`ms.ref("<kind>.<semantic_id>")` when a forward/cross-file reference cannot be
imported cleanly. Do not pass bare strings such as `"sales.orders"` to
`entity=`, `entities=`, `measure=`, relationship endpoints, `ms.join_on(...)`,
or derived metric components.

## Closeout

Before handing refs to `marivo-analysis`, run `ms.readiness(...)` for the refs
that will be analyzed. Do not hand off blocked refs.
