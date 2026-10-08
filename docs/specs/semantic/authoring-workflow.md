# Semantic Authoring Workflow

Status: current authoring workflow, 2026-10-08. This document defines agent-native
authoring across `marivo.datasource` and `marivo.semantic`.

## Outcome

An agent loads current project state, establishes the physical facts needed for
the task, authors the smallest dependency-coherent semantic slice, and uses one
`ms.load()` as the project-level static validation event. Exact authored roots
are then resolved with `catalog.require(...)`. Scoped readiness is required when
the delivery needs analysis-ready roots; analysis handoff additionally requires
current authority for their business meaning. Explanation and datasource tasks
have their own earlier exits.

The workflow has no public authoring lifecycle graph, no one-object-at-a-time
checkpoint rule, and no separate static verification result.

## Ownership

- `marivo.help(...)` owns static constructors, callable operations, effects,
  input facts, constraints, examples, and proof boundaries. Entry briefings add
  identity and usage navigation; error briefings preserve concrete failure facts.
- Entry and details cards own current definitions. Check reports own actual
  scope, outcomes, affected refs, and structured repair.
- Datasource inspection, optional bounded sampling, and governed raw SQL expose
  physical evidence. They do not decide reusable business meaning.
- Project Python is the semantic source of truth; `ms.load()` validates the
  whole current project and assembles the catalog.
- The agent owns evidence interpretation, explicit Python drafting, checkpoint
  choice, and residual-risk disclosure.
- Current authority owns choices that change reusable business meaning.

## Task exits and decision dependencies

Choose the exit before acquiring evidence:

- Definition explanation loads and reads the current catalog, with details or
  focused Help as needed; it does not require readiness or preview.
- Datasource setup or repair ends at the requested connection validation.
- Reusable authoring or repair supplies the exact roots needed by the parent
  task, following the dependency flow below.

The packaged skill groups decisions into task boundaries, reuse and business
authority, evidence and validation choices, and delivery. Help navigation selects
objects, builders, or checks; it does not repeat that workflow.

```text
load current datasource and semantic catalogs
-> inspect authoritative physical facts
-> choose inspection, optional bounded sampling, and/or governed raw SQL
-> author one dependency-coherent semantic slice
-> one ms.load()
-> catalog.require(...) for every authored root
-> scoped readiness when analysis-ready delivery is required
-> targeted runtime/source-health probes for concrete remaining risks
-> selected authoring exit or first typed analysis use
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
capabilities. Inspection helps authors choose projections and understand the
current source; it does not copy types into semantic declarations. At execution,
table metadata supplies types for the required dependency closure, while CSV and
JSON infer them during the actual read. Unknown or unsupported required types
fail explicitly, and unused columns do not add type restrictions.

### 3. Explore according to the question

Authoring has two governed evidence paths:

- inspection only when schema and existing project context are sufficient;
- optional explicitly scoped sampling when retained rows or generic profiles
  directly answer the current question.

These paths are composable within the caller's explicit data-access budget.
There is no mandatory inspect-snapshot-projection ladder. Every user-data read
has positive row and timeout guards; a returned-row limit is not a scan bound.

`md.raw_sql`/`RawSqlResult` remains available for a source-specific question
outside Marivo's governed Analysis capability. It submits SQL text without
parsing or classifying it, with a reason and enforced timeout. Query predicates,
aggregation and an explicit SQL LIMIT control returned size; the complete query
result is loaded into client memory.
Read-only behavior depends on connection and backend permissions. Its result is
terminal. To bring an answer into typed Analysis, author an upstream governed
view or a qualified Ibis expression and bind it through normal Semantic facts;
neither provenance text nor terminal raw-query rows are Analysis inputs.

A `DiscoverySnapshot` retains generic bounded rows, profiles, source evidence,
coverage, and cache identity. Its `.contract()` is a query-free read contract;
when `persist_values=True`, `retained_values` is a tuple of dictionaries keyed
by the selected column names. It does not produce semantic-shaped projections
or business judgment requirements.

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

When delivering analysis-ready roots, run `catalog.readiness(refs=[...])` over
the exact requested roots and their governed dependency closures. Readiness is
snapshot-independent: it evaluates
the current semantic project, the requested closure, and dedicated certified
temporal artifacts, and exposes only `analysis_ready_inputs` as its handoff.
Known aggregate/backend incompatibilities block affected roots without a
connection or query. Follow the returned repair to explicitly choose an
acceptable approximate definition or a compatible datasource, then reload and
rerun scoped readiness. A passing report still does not certify physical types
or operation-specific execution.

Use targeted `catalog.preview(..., scope=...)` only for a concrete runtime risk
or dedicated artifact repair. Ordinary preview reads the current datasource and
does not persist a check or change readiness. A successful project load proves
static coherence, not current external source health or every possible
downstream execution.

When current source or data drift matters, run
`catalog.source_health(refs, checks=..., scope=...)` separately. Omitting
`checks` runs connectivity and schema/capability inspection without querying
user data and therefore requires no scope. Null, enum, uniqueness, freshness,
relationship, and cardinality expectations exist only when explicitly built
with `ms.source_check`; those data-reading checks require an exact bounded
scope and disclose it in every result. Source health is ephemeral and never
changes readiness or semantic source. A passing relationship-match check covers
only the selected source rows. It cannot decide whether a consuming operation
requires matches for its own selected members or how that operation treats an
allowed absence.

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
scope used, authoritative sources for business meaning, checks performed and
their outcomes, exact roots, and remaining warnings or runtime risks. Label
roots analysis-ready only when readiness established that result. If the
parent task includes analysis, the current refs or `analysis_ready_inputs` are
handed to `marivo-analysis` and the original question continues.
