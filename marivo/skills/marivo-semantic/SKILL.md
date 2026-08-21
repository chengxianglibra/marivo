---
name: marivo-semantic
description: Use for Marivo datasource setup, governed source exploration, coherent semantic authoring or repair, scoped preview, and readiness, including semantics needed to answer a business question.
---

# marivo-semantic

## Trigger and exit

Use this skill for datasource declaration and validation, physical-source
inspection, optional bounded sampling, governed raw SQL, new or changed semantic
objects, scoped preview, readiness repair, or a missing-object handoff from
`marivo-analysis`.

Leave authoring as soon as the requested refs are usable. If the parent task
includes analysis, pass the current refs or `analysis_ready_inputs` to
`marivo-analysis` and continue the original question. Return only when analysis
exposes another reusable semantic gap.

For a datasource-only request, declare, register, test the connection, and stop.
Do not enter discovery or semantic authoring unless the request needs it.

## Ownership

This skill owns routing, data-access safety, coherent checkpoint choice,
business-authority stops, and handoff. It does not duplicate signatures, result
fields, backend catalogs, or error taxonomies.

- Live `marivo.help(...)` owns current constructors, operations, effects,
  constraints, examples, and repair calls.
- `.show()` and structured errors own current result detail and repair.
- The agent owns evidence interpretation and explicit Python drafting.
- Current authority owns reusable business meaning.

The source of truth is project Python evaluated by `ms.load()`. Credentials are
references, never authored plaintext.

## Environment entry

Use the project interpreter supplied by the host. For a conventional checkout,
prefer `.venv/bin/python` on macOS, Linux, and WSL, or
`.venv/Scripts/python.exe` on Windows. Keep using the same interpreter.

Before connecting, reading user data, or changing project source, prefer one
`<selected-python> -m marivo doctor` call. Enter runtime guidance through
`<selected-python> -m marivo help`, then `marivo.help("authoring")` and the
focused datasource or semantic target shown by current state.

```python
import marivo.datasource as md
import marivo.semantic as ms

datasources = md.load()
catalog = ms.load()
```

## Canonical route

```text
current catalogs
-> inspect authoritative physical facts
-> choose inspection, optional bounded sampling, and/or governed raw SQL
-> author one dependency-coherent semantic slice
-> one ms.load()
-> catalog.require(...) for every authored root
-> scoped readiness and only the targeted runtime probes the current risk needs
-> first typed analysis use
```

There is no mandatory snapshot ladder, one-object checkpoint loop, separate
verify stage, or public authoring lifecycle state.

### Explore according to the question

Use `md.inspect(...)` for source identity, columns, physical types, partitions,
and backend capabilities. Metadata inspection does not require an accountable
owner or a prior business approval.

Choose the smallest additional evidence path that answers the current question:

- inspection only when schema and current project context are sufficient;
- optional explicitly scoped sampling when retained rows or generic profiles
  matter;
- `md.raw_sql(...)` for source-specific metadata, distributions, joins,
  conditional logic, comparison with existing SQL, or bounded scratch work.

All user-data reads require explicit positive row and timeout guards and remain
within caller-stated budgets. A returned-row limit is not a scan bound.
`md.raw_sql(...)` is a normal governed exploration option: read-only, bounded,
effect-disclosed, and terminal. Its result cannot be passed to typed analysis,
promoted to a `MetricFrame`, or persisted as canonical analysis. Its observed
facts may inform explicit semantic Python.

Use only physical names and normalized types that inspection established. Treat
unknown, conflicted, dynamic, or unparseable fields as unresolved physical
facts; do not promote them through a guess.

### Author a coherent slice

A semantic slice is the smallest dependency-coherent set that can be loaded and
reviewed meaningfully. It may contain an entity with its direct dimensions, time
dimension, measures, and base metrics; a relationship with the exact
participating fields; or a derived object with newly required reusable
dependencies. It is not restricted to one object and should not expand into an
unrelated domain-wide rewrite.

Author the whole slice in explicit Python, then use one `ms.load()`. That load
is the authoritative project-level static validation event. Repair all reported
structural errors together, reload, and use `catalog.require(ref)` to confirm
every authored root. Do not add a separate verification checkpoint.

Run readiness over the exact requested roots. Keep the current runtime contract
for scoped preview, persisted preview evidence, readiness issues, and ready-input
fields; follow live help and typed repairs rather than inventing another path.
Use targeted preview or observation only when a concrete runtime risk or current
readiness repair calls for it.

## Business meaning and first-use authority

The agent may inspect freely and draft a coherent slice before every
business-caliber choice is settled. Drafting does not grant typed-analysis
authority.

Before the first typed analysis use of a new or changed definition, settle each
unresolved choice that changes reusable business meaning through at least one
current, non-conflicting authority:

1. the user's explicit request or answer in the current task;
2. an approved existing project definition;
3. attributable project documentation or source provenance that is sufficiently
   explicit.

This applies to choices such as denominator, inclusion and exclusion policy,
failure handling, unit, aggregation, additivity, business time axis, and metric
caliber. If the current request or approved project already establishes the
meaning, proceed without asking for redundant confirmation. Do not create an
approval token, receipt, or second authorization stage.

When no current authority settles the earliest material choice, name the object
and choice, summarize the relevant evidence and its limit, ask one question, and
stop before typed analysis handoff. Physical observations alone do not establish
primary-key authority, exhaustive enums, unit, additivity, timezone, or business
meaning.

## Hard boundaries

- Do not bypass Marivo safety with direct Ibis, DuckDB, pandas, backend clients,
  or ad hoc SQL.
- Caller-stated read-count, row, and timeout budgets override suggested retries.
- If the same structured root cause occurs twice, stop that branch after at most
  one focused-help recovery and report the exact blocker and observed effects.
- Reuse a matching snapshot's generic rows, profiles, source evidence, and cache
  identity when useful. Reacquire only for missing facts, changed source/schema/
  scope identity, or an explicit current repair; snapshot age alone is not
  invalidation.
- Semantic Events own governed occurrence meaning; ordered pattern matching
  belongs to analysis. StateModels own normative states and legal transitions,
  not replay or completeness assumptions.

## Closeout

For authoring-only work, state the coherent slice changed, evidence and scope
used, authoritative sources for business meaning, validation outcome, exact
analysis-ready roots, and remaining warnings or runtime risks. When the parent
task includes analysis, hand off and continue instead of closing early.

If blocked, state the exact object and unresolved choice or structured blocker,
whether data was queried or source mutated, whether evidence remains reusable,
and the single action required to continue.
