# Semantic Authoring Workflow

Use `ms.ai_context(...)` and `ms.period_correspondence(...)` for authored
values. Context annotations use the existing `ms.typing.AiContextValue` path.
Their concrete result types remain discoverable through native type Help;
`AiContextValue` and `PeriodCorrespondence` are not root constructor exports.
Temporal kind markers remain root exports for specific `Ref[K]` annotations.

Native expression authoring (2026-10-17):
[Expression redesign v0.37](expression-model-redesign.md#26-原生-ibis-与自动延迟绑定)
now uses controlled native Ibis Deferred for every numerical or conditional Field,
Measure and Metric input. Physical capture/finalize still freezes semantic facts without
source reads. Named declarations attach immutable metadata to the same Deferred recipe;
model loading collects only exported root declarations. Derived aliases do not publish a
new declaration. Declaration bodies are inspected and captured without invoking author functions.

Use `catalog.describe(ref).show()` for definition facts and `AnalysisFrame.show()` for execution
facts. Native Deferred values have no Marivo `.show()` or `.contract()` methods. Real physical
dtypes are checked during automatic execution preparation; no manual schema or conversion API
is required. Implementation and execution acceptance for the additional capabilities below
are tracked separately by the [implementation plan](expression-model-implementation-plan.md).

The current native surface includes source reductions, ordered internal-sample reductions,
explicit membership buckets, local empty-input policies, and reusable window Metrics.
Each expression keeps its contribution roots, permissions, units, time role and dependencies.
The [expression model](expression-model-redesign.md) owns these rules; execution preparation
checks physical types and backend support. A declaration or successful load is not execution evidence.

Status: current authoring workflow, 2026-10-17. This document defines agent-native
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
has positive row limits and requested native query timeouts; a returned-row limit is not a scan bound.

`md.raw_sql`/`RawSqlResult` remains available for a source-specific question
outside Marivo's governed Analysis capability. It submits SQL text without
parsing or classifying it, with a reason and an optional native query timeout. Query predicates,
aggregation and an explicit SQL LIMIT control returned size; the complete query
result is loaded into client memory.
Read-only behavior depends on connection and backend permissions. Its result is
terminal. To bring an answer into typed Analysis, author an upstream governed
view or a qualified Ibis expression and bind it through normal Semantic facts;
neither provenance text nor terminal raw-query rows are Analysis inputs.

A `DiscoverySnapshot` retains generic bounded rows, profiles, source evidence,
coverage, and cache identity. Its `.show()` displays captured facts without a source query;
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
the exact requested physical inputs and their governed dependency closures.
Inspect published Measure/Metric definitions through `catalog.describe()` and select
the physical fields and Entities they consume for readiness. Object Help states
this input scope; a rejected expression supplies physical dependency candidates
from the compiled closure and a direct retry when the complete list fits the
repair bound. It never silently replaces the requested input. Readiness is
snapshot-independent: it evaluates
the current semantic project, the requested closure, and dedicated certified
temporal artifacts, and exposes only `analysis_ready_inputs` as its handoff.
Readiness opens no connection or query. Follow its physical-definition or temporal
artifact repairs, then rerun the scoped check. Source Frame preparation owns aggregate/backend
compatibility, physical types and operation-specific execution.

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

## Pure expression authoring and current handoff boundary

Capture physical declarations as typed Refs. Import every physical dependency
once, then call `finalized = ms.finalize()` to validate and close capture. Resolve
handles with `finalized.resolve(ref)` for immutable Entity/Field values
before row/Metric composition. Keep physical declarations before finalization;
failed closure checks leave capture open for repair. Bare Refs are lookup inputs.
Constructed inputs are not required to match their original definition versions.
Reference-resolving consumers use their complete scope; explicit Refs remain
lookup keys, including forward references. If semantic definitions change during
analysis, the agent/user accepts the consequences. Keep immutable captured facts,
kind/root/grain/unit/time-role/null checks, correct computation/cache identities
and existing provenance records; do not add live refresh or module replay.
Measure/Metric decorators are pure zero-argument local declarations; they add
names/context and explicit Measure business
assertions without requiring loader registration or a Session. Keep source
Ibis authoring separate and use ms.bind only in the remaining restricted Ibis
field bodies. Follow structured errors for retired factories and parameters;
do not delete old empty/fold/fanout choices and claim business equivalence.

Physical leaf loading and metadata checks remain available. New Catalog Metric
collection/publication and discovery belong to R1.4; new expression execution
belongs to R2. Report these capability boundaries instead of treating a local
expression or a leaf-only load as a published, executable business Metric.

## Closeout

An authoring closeout records the coherent slice changed, physical evidence and
scope used, authoritative sources for business meaning, checks performed and
their outcomes, exact roots, and remaining warnings or runtime risks. Label
roots analysis-ready only when readiness established that result. If the
parent task includes analysis, the current refs or `analysis_ready_inputs` are
handed to `marivo-analysis` and the original question continues.

Scalar predicate syntax is owned by `semantic.field`: comparisons use Python
operators, boolean composition uses `&`, `|`, and `~`, and literal membership
uses `semantic.field.isin`. These recipes retain contribution roots and versions.
`semantic.expressions` distinguishes Entity/Relationship metadata from Catalog
descriptions of published Deferred expressions; Deferred values have no `.show()`.
