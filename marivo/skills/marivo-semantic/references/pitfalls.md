# marivo-semantic pitfalls

These are workflow-level failure modes for semantic authoring. API field
contracts belong to `ms.help(...)`, datasource evidence belongs to
`md.discover_*`, and runtime failures should be handled from the structured
error raised by Marivo.

## Skipping help

Do not author from memory. Read `ms.help("<constructor-or-object>")` before
choosing constructor values.

## Skipping discovery

Do not ask users for facts Marivo can inspect. Run the matching `md.discover_*`
call before each datasource-backed semantic object.

## Inventing grill options

Do not turn the grill gate into plausible guesses. Options must come from
inspected metadata, profiles, sample distributions, existing semantic objects,
source SQL, project docs, or prior decisions. If evidence is thin, ask an open
question or run another bounded discovery query.

## Asking about discoverable facts

Do not ask users for schema, column names, data types, sample values,
join-key viability, or existing refs when Marivo can discover them.

## Writing multiple objects before verification

Author exactly one object, then run `ms.verify_object(ref)`. Fix failures
before moving to the next object.

## Passing naked semantic-id strings

Do not pass strings such as `"sales.orders"` or `"sales.revenue"` to
semantic-object authoring parameters. Use the Ref returned by the earlier
authoring call, import the Ref from the module that declares it, or use
`ms.ref("<kind>.<semantic_id>")` only for explicit forward/cross-file
references.

## Advancing past failed verification

If `ms.verify_object(ref)` fails, stop. Show the verification result, repair
the object, and verify again.

## Using raw SQL as an authoring body

Use `md.raw_sql(...)` for diagnostics and `ms.from_sql(...)` for provenance.
Do not make SQL text an executable semantic expression body.

## Analysis handoff before readiness

Run `ms.readiness(...)` before handing refs to `marivo-analysis`. Blocked refs
are not ready for analysis.
