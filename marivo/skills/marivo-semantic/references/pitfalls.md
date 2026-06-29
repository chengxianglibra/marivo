# marivo-semantic pitfalls

These are workflow-level failure modes. API contracts belong to
`ms.help(...)`, datasource evidence belongs to `md.discover_*`, and runtime
failures should be handled from the structured error raised by Marivo.

## Anti-patterns

| Anti-pattern | Correct action |
| --- | --- |
| Authoring from memory | Read `ms.help("<constructor-or-object>")` before choosing constructor values. |
| Asking for discoverable facts | Use `md.inspect_table(...)` / `md.inspect_partitions(...)` for schema and partition facts, then the matching `md.discover_*` call for semantic evidence. |
| Inventing grill options | Ground every option in inspected metadata, profiles, sample distributions, existing semantic objects, source SQL, project docs, or prior decisions. |
| Writing multiple objects before verification | Author one object, run `ms.verify_object(ref)`, and fix failures before moving on. |
| Passing naked semantic-id strings | Use returned/imported Ref objects, or `ms.ref("<kind>.<semantic_id>")` for explicit forward or cross-file references. |
| Using raw SQL as an authoring body | Use `md.raw_sql(...)` for diagnostics and `ms.from_sql(...)` for provenance; semantic expressions stay in Python. |
| Handing off blocked refs | Run `ms.readiness(...)` and keep blocked refs out of `marivo-analysis`. |
