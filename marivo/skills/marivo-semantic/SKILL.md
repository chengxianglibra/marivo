---
name: marivo-semantic
description: Use for Marivo datasource declaration, evidence, semantic authoring, verification, preview, readiness, or missing-object repair. For build requests, ask only the earliest missing accountable input before data access or capability enumeration.
---

# marivo-semantic

## Trigger

Use this skill for datasource setup and validation, physical source inspection,
bounded authoring evidence, new or changed semantic objects, semantic
verify/preview/readiness repair, or a missing-object handoff from
`marivo-analysis`.

Stop using it once the requested refs are analysis-ready. Investigation over
already-ready refs belongs to `marivo-analysis`.

For a build request, the first routing decision is the non-observable decision
preflight. After one environment fingerprint and one current-project catalog
read, ask for a missing accountable owner or target business concept and stop.
Do not enumerate root/focused help or inspect metadata before that stop.
Do not bundle independent judgments. A user-named build target satisfies the
target-concept preflight unless its ambiguity would change the requested scope.

## Ownership and authority

This skill is a boundary router, not an API catalog or semantic inference
engine.

- Live Marivo help owns current signatures, effects, examples, file placement,
  result fields, and repair calls.
- `.show()`, `.contract()`, and structured errors own object-local state and
  continuation.
- The skill owns ordering, data-access safety, evidence continuity, judgment
  stops, and handoff.
- The agent owns technical interpretation and explicit Python drafting.
- The user or accountable business owner owns business meaning.

Physical observations can establish only what they measured. They cannot
authorize an owner, primary key, business definition, unit, aggregation,
additivity, cardinality, timezone, lifecycle, or event meaning.

## Environment entry

Use one project interpreter for discovery and execution. Verify the rendered
Marivo version, Python executable, and package path before connecting, reading
data, or changing project source.

```python
import marivo
import marivo.datasource as md
import marivo.semantic as ms
```

After the decision preflight passes, enter through
`<selected-python> -m marivo help`, then use
`marivo.help("authoring")`. It owns the exact current-project catalog reads and
routes to the focused datasource or semantic target shown by live state. If
help and execution fingerprints differ, stop for environment repair.
The CLI owns only environment bootstrap. All target help goes through
`marivo.help(...)`; `md` and `ms` execute domain APIs and do not expose separate
public help aliases.

Never reconstruct a signature or repair from this skill. Prefer, in order:
the current structured error, current result `.contract()`, focused help, then
root help.
Read focused help only for the active object. When several already-known,
independent targets are needed, render them in one interpreter invocation.

## Canonical route

Resume from the earliest unsatisfied boundary for one object:

```text
environment -> project browse -> non-observable decision preflight
-> metadata inspect -> explicit scope -> one snapshot -> query-free projections
-> evidence-grounded judgment -> author one object -> load exact entry
-> static verify -> required preview -> readiness -> analysis
```

The partial order is policy even when a live method is mechanically callable:

- browse current project and catalog before mutation;
- ask required non-observable inputs before user-data access;
- inspect metadata before reading business rows;
- use explicit positive row and timeout guards for every user-data read;
- satisfy dependencies before dependents;
- validate one authored object before advancing;
- static verification precedes required preview;
- readiness precedes analysis use.

### Decision preflight

Before any user-data read, detect required inputs that data cannot answer:
accountable owner, requested business concept, supplied policy, or an explicit
choice among disputed definitions. Ask the earliest such question and stop.
Do not sample data to answer it.

After preflight, collect only evidence needed for the active object. Acquire one
bounded snapshot and reuse its query-free projections. Do not reacquire merely
to inspect entity, dimension, value, time, measure, or relationship views.

### One-object loop

Author exactly one explicit Python object, reload the catalog, acquire that
exact typed entry, and follow its registered verify, preview, and readiness
path. Repair and revalidate the same object before moving to a dependent.
Domain-sized batch authoring followed by deferred validation is forbidden.

## Judgment boundary

Read `result.show()` and
`result.contract().judgment_requirements`. These requirements are
non-mechanical: they identify what remains unresolved and who owns the answer;
they are not constructor recommendations or approval tokens.

Use this authority order:

1. live contracts for mechanical legality;
2. explicit accountable business-owner decisions for meaning;
3. approved existing project definitions;
4. attributable project documentation and source provenance;
5. inspection and snapshot output for physical observations only.

If one judgment remains, name the object and requirement, summarize the
relevant evidence and its limit, ask one question, and stop. If several remain,
ask only the earliest dependency whose answer can change later questions.

Observed uniqueness is not primary-key authority. Observed values are not an
exhaustive enum. Physical types do not establish unit, additivity, timezone, or
business time. Never turn plausible evidence into an authored promise.

## Deterministic stop rule

If the same root cause occurs twice, stop that branch. At most one
focused-help recovery is allowed before reporting the exact blocker, current
state, observed effects, and whether evidence is reusable.

Stop when minimum sufficient evidence answers the current routing decision.
Do not enumerate catalogs, capabilities, columns, or candidate objects without
a concrete unresolved need.

## Hard boundaries

### Data access and bypass

Do not read business rows directly through Ibis, DuckDB, pandas, backend
clients, or ad hoc SQL to bypass Marivo inspection and snapshot contracts.
`md.raw_sql(...)` is an explicit terminal diagnostic escape only; its result
cannot re-enter typed semantic or analysis work.

A returned-row limit is not a scan bound. Never retry an operation until its
focused help exposes the effect and required scope.
Caller-stated read-count, row, and timeout budgets override any repair that
would otherwise permit a retry.

### Snapshot continuity

One snapshot supports all query-free projections for the active object or
relationship evidence pair. Reacquire only when current structured state says
columns or retained values are missing, evidence is stale, or source/schema/
scope identity no longer matches. Snapshot age alone is not invalidation.

### Explicit source and secrets

The source of truth is project Python evaluated by the semantic loader.
Credentials remain references, not authored plaintext. Plaintext value
persistence requires the live effect contract and explicit acceptance.

### Events and StateModels

Semantic Events own governed occurrence meaning; ordered pattern matching
belongs to analysis. StateModels own normative states and legal transitions,
not replay windows, censoring, completeness assumptions, or observed policy.
A loadable model without the required inception is not silently treated as replay-ready.

## Routing

| Current condition | Route |
| --- | --- |
| Environment mismatch | Stop before connection, data read, or mutation |
| Non-observable required decision missing | Ask one accountable question before sampling |
| Current result or error exists | Read `.show()` and `.contract()` or its typed repair |
| Snapshot already matches | Reuse it and project evidence without querying |
| Business judgment remains | Ask one evidence-grounded question and stop |
| Static verify fails | Repair and reverify the same object |
| Required preview is missing during authoring | Run the registered scoped preview |
| Readiness blocks | Follow the typed repair; do not expose the ref |
| Readiness exposes analysis-ready inputs | Hand only those inputs to `marivo-analysis` |

## Closeout

Successful closeout states the object changed, evidence identity and scope,
business decisions and accountable source, validation stages passed,
analysis-ready inputs, and remaining warnings.

Blocked closeout states the exact object and state, judgment or typed blocker,
whether data was queried or source mutated, whether evidence is reusable, and
the single required user or environment action.
