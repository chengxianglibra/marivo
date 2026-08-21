# Semantic Authoring Workflow

Status: draft design. This document defines the current agent-native authoring
workflow across `marivo.datasource` and `marivo.semantic`.

## Outcome

An agent loads current project state, establishes the physical facts needed for
the task, authors the smallest dependency-coherent semantic slice, and uses one
`ms.load()` as the project-level static validation event. Exact authored roots
are then resolved with `catalog.require(...)`, checked through scoped readiness,
and handed to analysis when their business meaning has current authority.

The workflow has no public authoring lifecycle graph, no one-object-at-a-time
checkpoint rule, and no separate static verification result.

## Ownership

- `marivo.help(...)` owns current constructors, callable operations, effects,
  input facts, constraints, examples, and repair routes.
- Datasource inspection, optional bounded sampling, and governed raw SQL expose
  physical evidence. They do not decide reusable business meaning.
- Project Python is the semantic source of truth; `ms.load()` validates the
  whole current project and assembles the catalog.
- The agent owns evidence interpretation, explicit Python drafting, checkpoint
  choice, and residual-risk disclosure.
- Current authority owns choices that change reusable business meaning.

## Current flow

```text
load current datasource and semantic catalogs
-> inspect authoritative physical facts
-> choose inspection, optional bounded sampling, and/or governed raw SQL
-> author one dependency-coherent semantic slice
-> one ms.load()
-> catalog.require(...) for every authored root
-> scoped readiness and targeted runtime probes when needed
-> first typed analysis use
```

### 1. Enter from current state

Read current datasource and semantic catalogs before mutation:

```python
import marivo.datasource as md
import marivo.semantic as ms

datasources = md.load()
catalog = ms.load()
```

Environment fingerprinting and focused help remain available. Metadata
inspection must not be blocked merely because an accountable owner has not yet
been identified. Owner or business-definition questions become mandatory only
when the answer changes a reusable declaration or its promotion caliber.

### 2. Establish authoritative physical facts

Use datasource registration, connection testing, and `md.inspect(...)` for
column names, physical types, source identity, partition facts, and backend
capabilities. Inspection is the preferred schema path because these facts should
not require a user-data scan. Unknown or unsupported metadata remains explicit;
Marivo does not replace it with a discovery guess.

### 3. Explore according to the question

Choose among three evidence paths:

- inspection only when schema and existing project context are sufficient;
- optional explicitly scoped sampling when retained rows or generic profiles
  directly answer the current question;
- `md.raw_sql(...)` for source-specific metadata, distributions, joins,
  conditional logic, comparison with existing SQL, or bounded scratch work.

These paths are composable within the caller's explicit data-access budget.
There is no mandatory inspect-snapshot-projection ladder. Every user-data read
has positive row and timeout guards; a returned-row limit is not a scan bound.

Raw SQL is a normal governed exploration option. It remains read-only, bounded,
effect-disclosed, and terminal. A `RawSqlResult` cannot enter
`session.observe(...)`, become a `MetricFrame`, or be persisted as canonical
analysis. Its observed facts and disclosed assumptions may inform semantic
Python.

A `DiscoverySnapshot` retains generic bounded rows, profiles, source evidence,
coverage, and cache identity. It does not produce semantic-shaped projections or
business judgment requirements.

### 4. Author a coherent semantic slice

A semantic slice is the smallest dependency-coherent set that can be reviewed
and loaded meaningfully. Typical slices include:

- one entity with its direct dimensions, time dimensions, measures, and base
  metrics;
- one relationship plus the exact participating entity fields;
- one cross-entity or derived metric plus newly required reusable components;
- one event or state model with its exact semantic dependencies.

A slice is not restricted to one object, but it must not expand into an
unrelated domain-wide rewrite. A smaller checkpoint remains appropriate when an
individual declaration is unusually uncertain.

### 5. Load once and repair structural failures

`ms.load()` is the authoritative static validation event for authored source. It
evaluates the current project and fails closed on invalid organization,
duplicate identity, unresolved refs, type mismatches, illegal expression
bindings, invalid decomposition, and cycles.

After a successful load, confirm exact identity with ordinary catalog navigation:

```python
catalog = ms.load(project_root)

for ref in authored_roots:
    catalog.require(ref)
```

Repair all structural failures for the slice and reload. Do not insert a
separate per-object validation checkpoint between load and catalog navigation.

### 6. Scoped readiness and targeted runtime checks

Run `catalog.readiness(refs=[...])` over the exact requested roots and their
governed dependency closures. During this milestone, the existing contracts for
snapshot-bound preview input, persisted preview evidence, readiness issue
ownership, and ready-input projection fields remain unchanged. Follow current
live help and typed repairs; do not introduce a second preview or readiness path.

Use targeted preview or observation only for a concrete runtime risk or current
readiness repair. A successful project load proves static coherence, not current
external source health or every possible downstream execution.

## Business meaning and first-use authority

An agent may explore freely and draft a coherent slice before every
business-caliber question is settled. Drafting does not grant typed-analysis
authority.

Before the first typed analysis use of a new or changed definition, every
unresolved choice that changes reusable business meaning must be settled by at
least one current, non-conflicting authority:

1. the user's explicit request or answer in the current task;
2. an approved existing project definition;
3. attributable project documentation or source provenance that is sufficiently
   explicit.

This includes denominator, inclusion and exclusion policy, failure handling,
unit, aggregation, additivity, business time axis, and metric caliber. If the
current request or approved project already establishes the meaning, the agent
proceeds without asking for redundant confirmation. Marivo does not persist an
approval token, acknowledgement, decision record, or handoff receipt.

When no current authority settles the earliest material choice, the agent names
the object and choice, summarizes the evidence and its limit, asks one question,
and stops before typed analysis handoff. Physical observations alone do not
authorize primary-key meaning, exhaustive enums, unit, additivity, timezone, or
business semantics.

## Closeout

An authoring closeout records the coherent slice changed, physical evidence and
scope used, authoritative sources for business meaning, validation outcome,
exact analysis-ready roots, and remaining warnings or runtime risks. If the
parent task includes analysis, the current refs or `analysis_ready_inputs` are
handed to `marivo-analysis` and the original question continues.
