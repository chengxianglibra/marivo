# Workflow pitfalls

API details belong to `md.help(...)` and `ms.help(...)`; state-specific repair
steps belong to result output and structured errors.

| Anti-pattern | Correct action |
| --- | --- |
| Reading before metadata inspection | Run `md.inspect(...)` and read extent, partitions, schema, and capabilities first. |
| Treating `LIMIT` as a scan-cost bound | Use explicit partition scope where available; acknowledge an unpruned read explicitly. |
| Repeating source discovery calls | Acquire one scoped snapshot and reuse local projections. |
| Asking for observable facts | Read the matching snapshot projection before asking. |
| Inventing semantics from a sample | Leave uncommon formats and semantic judgments to the agent and user. |
| Writing several objects before checking | Write one object, reload it, and run `catalog.verify_object(obj)`. |
| Previewing without evidence scope | Run `catalog.preview(obj, using=snapshot)` or the exact entity-keyed mapping. |
| Treating parity as a gate | Parity is a potentially unbounded optional diagnostic, never readiness-required. |
| Handing off a blocked object | Run zero-query readiness and repair the same object before analysis. |
