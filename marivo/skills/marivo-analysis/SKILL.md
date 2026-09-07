---
name: marivo-analysis
description: Use when a user wants to run or continue a trusted Marivo investigation over governed metrics, Events, StateModels, or persisted analysis artifacts.
---

# marivo-analysis

## Purpose and ownership

Turn the user's question into a bounded, evidence-backed investigation. The
agent owns planning, method choice, judgment, synthesis, and stopping; governed
semantic objects own reusable business meaning.

Use the host-selected verified environment. `marivo.help("analysis")` provides
progressive discovery; focused Help owns exact signatures, constraints, and
examples. `.show()` owns current artifact state, `.contract()` owns mechanically
valid continuations, and structured errors own repair. Consult them when the
next decision needs that information; do not reconstruct contracts from memory
or private implementation details.

## Bounded analysis loop

### 1. Frame the question as an Evidence obligation

Before execution, identify the answers the investigation owes the user. For
each required answer, record the claim or decision it supports, the exact
population and governed inputs, the time and comparison scope, the minimum
supporting evidence, and whether the answer is observed, interpreted, or
currently unsupported.

Preserve scopes named by the user. Do not replace a selected cohort, segment,
top-N set, time window, comparison direction, or exhaustive request with a
broader or easier analysis. Add optional analysis only when it can materially
change a required conclusion, recommendation, or limitation.

### 2. Establish the governed starting point

Use the host-selected verified Marivo environment throughout. Create or resume
one question-scoped session. Resolve the exact typed semantic inputs together
and inspect readiness only for the required closure. Reuse a current semantic
handoff for the same project and scope instead of repeating it.

If the required capability is unknown, start with `marivo.help("analysis")`.
Route through `marivo.help("analysis.inputs")` for input construction or
`marivo.help("analysis.entry")` for the first typed Artifact, then consult the
focused Help for the selected capability. Avoid broad catalog browsing when an
exact typed ref or full semantic identity is already available.

### 3. Build the minimum typed Evidence chain

Produce a bounded typed Artifact early. Inspect `.show()` when current state
contributes evidence and use `.contract()` when the mechanically valid next
actions are unknown. Route method discovery through
`marivo.help("analysis.methods")`, but choose subsequent methods from the
question and the Artifact in hand rather than from a fixed recipe.

Before treating a calculation as custom, classify its analytical intent against
the installed capability map. Stay in typed flow whenever Marivo expresses that
intent. Batch compatible work into one decision round, prefer the smallest chain
that supports a required answer, and stop expanding when another result cannot
materially change the answer or its limitations.

### 4. Validate before interpreting

For every material result, check the semantic identity and exact population;
time coverage, completeness, censoring, and comparison alignment; grain, units,
additivity, and reconciliation where applicable; missingness, uncertainty, and
quality blockers; and the boundary between observation, association,
projection, hypothesis testing, and causal evidence.

Use `marivo.help("analysis.artifacts")` and `marivo.help("analysis.evidence")`
when inspection or Evidence mechanics are unknown. Preserve warnings
and partial coverage. Do not turn absence into zero, association into causation,
a point forecast into certainty, or a segment result into a population claim.

### 5. Synthesize, hand off, or stop

Close when every required answer is supported or explicitly blocked, using the
closeout obligations below.

Hand only reusable semantic gaps to `marivo-semantic`. Resume the affected
analysis branch from the returned analysis-ready inputs; do not restart
unaffected branches or require redundant user approval.

## Hard boundaries

### Semantic authority

Take metrics, dimensions, Events, StateModels, participant roles,
relationships, units, definitions, and admissible joins from the semantic
catalog. Analysis may choose question-specific windows, alignments, cohorts,
policies, seeds, and completeness declarations, but it must not author or edit
reusable semantic definitions while this skill is active.

A missing or disputed reusable business object stops only the affected branch.
Record the smallest gap and hand it to `marivo-semantic`. Never substitute a
physical column, guessed join, different metric, or presentation label for
governed meaning.

A runtime metric remains a question-scoped expression over governed inputs; it
does not become reusable organizational truth. Preserve the assumptions and
limits that materially affect a conclusion, and hand the definition to
`marivo-semantic` if it must become reusable.

### Typed execution and terminal exits

Do not query business rows through Ibis, DuckDB, pandas readers, backend
connections, private datasource handles, or ad hoc SQL to bypass Marivo.

Before a terminal conversion, read
`marivo.help("analysis.boundary.to_pandas")`. Exit typed flow only when the
required method is outside the installed governed surface and a bounded typed
Artifact already establishes the inputs, scope, and Evidence chain. Keep the
terminal calculation rerunnable alongside the exit, do not export and reload
Artifact rows, and never feed a terminal result back into typed analysis.

### Evidence continuity and recovery

Carry exact Artifact identities across decision rounds in that session. Do not
depend on process memory, an implicit latest result,
imported prior scripts, chat summaries, or repeated successful observations as
substitutes for persisted identity.

When resuming work, start with `marivo.help("analysis.runtime")` and use only its
public runtime reads to recover the relevant branch. Consult
`marivo.help("analysis.evidence")` before treating recovered Evidence as current.
Use the installed revalidation and repair guidance when authority-sensitive
reuse requires it; mechanical compatibility alone is not current semantic
authority.

For cold starts, use bounded Run history to locate the exact committed Artifact;
use focused graph reads for factual adjacency and Artifact-owned Finding reads
for audit detail. These public recovery reads do not establish current semantic
authority, datasource freshness, causality, or business validity. Disclose
freshness as unchecked unless a separate current source check establishes it.
If authority or Evidence cannot be restored, block only the affected branch and
continue independent work.

### Structured repair and stopping

Follow the structured repair or focused Help for a failed public capability.
Do not invent a neighboring API, private workaround, or silent fallback. Stop
and disclose the affected branch when the current public contract cannot
produce the required Evidence.

## Closeout

Answer the user's questions first in their business vocabulary. For every
material conclusion:

- state the supported direction and the magnitude or uncertainty needed to
  interpret it;
- preserve the exact source, scope, definition, and comparison that govern it;
- distinguish Evidence, interpretation, recommendation, and hypothesis;
- disclose material blockers, warnings, omissions, quality limits, and terminal
  exits;
- keep the supporting semantic refs, Session, Run, Artifact, and scope
  recoverable without exposing runtime bookkeeping unless the user requests an
  audit.

Do not prescribe a fixed report template or continue exploring after the
required answers and limitations are complete. Delivery or publication belongs
to an independent capability when the user requests it.
