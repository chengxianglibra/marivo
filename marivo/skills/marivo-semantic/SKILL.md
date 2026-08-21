---
name: marivo-semantic
description: Use for Marivo datasource declaration, evidence, reusable semantic authoring or repair, verification, preview, and readiness, including semantics needed to answer a business question. Ask only the earliest missing accountable input before data access or capability enumeration.
---

# marivo-semantic

## Trigger

Use this skill for datasource setup and validation, physical source inspection,
bounded authoring evidence, new or changed semantic objects, semantic
verify/preview/readiness repair, or a missing-object handoff from
`marivo-analysis`.

Once the requested refs are analysis-ready, leave authoring. If the parent task
includes analysis, hand its `analysis_ready_inputs` to `marivo-analysis` and
continue the original question. Return here when analysis exposes another
reusable semantic gap. Do not ask permission to switch skills; ask only when
business meaning itself remains unresolved.

For a semantic build request, the first routing decision is the non-observable decision
preflight. After one environment fingerprint and one current-project catalog
read, ask for a missing accountable owner or target business concept and stop.
Do not enumerate root/focused help or inspect metadata before that stop.
Do not bundle independent judgments. A user-named build target satisfies the
target-concept preflight unless its ambiguity would change the requested scope.

For a datasource-only request that only declares, registers, and validates a
connection, use the short datasource route and stop after validation. Do not
enter physical-source discovery or semantic authoring unless the request needs
them.

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

Prefer the project interpreter provided by the host. For a conventional
project-local installation, prefer `.venv/bin/python` on macOS, Linux, and WSL,
or `.venv/Scripts/python.exe` on Windows. Keep using the selected interpreter
for discovery and execution.

Before connecting, reading data, or changing project source, prefer running
`<selected-python> -m marivo doctor` once and using the reported Marivo version,
Python executable, package path, and project state as the runtime fingerprint.

```python
import marivo
import marivo.datasource as md
import marivo.semantic as ms
```

After the decision preflight passes, prefer entering through
`<selected-python> -m marivo help`, then use
`marivo.help("authoring")`. It owns the exact current-project catalog reads and
routes to the focused datasource or semantic target shown by live state. Prefer
the same selected interpreter for doctor, help, and execution. For target help,
prefer `marivo.help(...)`; `md` and `ms` execute domain APIs.

For signatures and repairs, prefer, in order: the current structured error,
current result `.contract()`, focused help, then root help.
Prefer focused help for the active object. When several already-known,
independent targets are needed, prefer rendering them in one interpreter
invocation.

If live help fails unexpectedly, use the current error or result contract when
one exists and prefer a focused or root retry. Local docs or installed package
source are valid read-only recovery aids when help remains unavailable; treat
private implementation details as unverified, do not call private APIs, and do
not bypass datasource safety boundaries.

## Canonical route

Choose the smallest route that matches the request:

```text
datasource-only:
environment -> declare -> register -> connection test -> closeout

physical-source discovery:
environment -> project browse -> non-observable decision preflight
-> metadata inspect -> explicit scope -> one snapshot -> query-free projections

semantic authoring:
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

Keep already-known independent datasource actions in one interpreter invocation
when practical; do not depend on process-local state across invocations.

### Decision preflight

Before any user-data read, detect required inputs that data cannot answer:
accountable owner, requested business concept, supplied policy, or an explicit
choice among disputed definitions. Ask the earliest such question and stop.
Do not sample data to answer it.

After preflight, collect only evidence needed for the active object. Acquire one
bounded snapshot and reuse its query-free projections. Do not reacquire merely
to inspect entity, dimension, value, time, measure, or relationship views.

When table inspection reports declared-only typed bindings, follow the warning's
focused datasource route and runtime evidence requirement. Choose an explicit
bounded scope and acquire one matching snapshot before semantic closeout; use an
explicit unpruned scope only when the live contract classifies metadata as
unavailable or partition discovery as omitted. Static load, verify, and
zero-query readiness do not substitute for the required runtime preview.

When inspection exposes `projectable_columns`, use only the exact physical names
and normalized types it verified in `md.source_column(...)`; a conflicted or
unparseable physical type is not a safe candidate. For a date/timestamp interval,
follow `datasource.time_range`; this is also the bounded route for one transformed
temporal partition. Dynamic keys not materialized as physical columns remain
outside governed authoring and require upstream materialization, a database view,
or terminal raw SQL.

If evidence reports `null_semantics`, record the business interpretation of
`NULL` in `ai_context.guardrails`. Do not infer that a high null rate is a quality
failure and do not filter nulls implicitly. In readiness, distinguish issue
severity: advisory-only reports remain ready, and grouped snapshot/preview
advisories are one evidence-root repair, not one failure per semantic ref.

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

Semantic Events own governed business occurrence meaning and identity; ordered
pattern matching belongs to analysis. A ``TemporalSet`` owns named analysis
windows such as holidays, campaigns, and incidents, while a ``WorkSchedule``
owns a finite daily final ``is_working`` status authority for working-day
alignment. Neither is an Event substitute or an implicit recurrence rule.
StateModels own normative states and legal transitions,
not replay windows, censoring, completeness assumptions, or observed policy.
A loadable model without the required inception is not silently treated as replay-ready.

## Routing

| Current condition | Route |
| --- | --- |
| Datasource declaration only | Declare, register, test the connection, then close out |
| Environment mismatch | Stop before connection, data read, or mutation |
| Non-observable required decision missing | Ask one accountable question before sampling |
| Current result or error exists | Read `.show()` and `.contract()` or its typed repair |
| Snapshot already matches | Reuse it and project evidence without querying |
| Business judgment remains | Ask one evidence-grounded question and stop |
| Static verify fails | Repair and reverify the same object |
| Required preview is missing during authoring | Run the registered scoped preview |
| Readiness blocks | Follow the typed repair; do not expose the ref |
| Readiness exposes analysis-ready inputs | Hand only those inputs to `marivo-analysis` and continue the original question |

## Closeout

For an authoring-only task, successful closeout states the object changed,
evidence identity and scope, business decisions and accountable source,
validation stages passed, analysis-ready inputs, and remaining warnings. When
the parent task includes analysis, follow the routing handoff instead of closing.

Blocked closeout states the exact object and state, judgment or typed blocker,
whether data was queried or source mutated, whether evidence is reusable, and
the single required user or environment action.
