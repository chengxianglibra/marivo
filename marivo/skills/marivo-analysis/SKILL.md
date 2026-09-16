---
name: marivo-analysis
description: Use when a user wants to run or continue a trusted Marivo investigation over governed metrics, Events, StateModels, or persisted analysis artifacts.
---

# marivo-analysis

## Purpose and ownership

Use this skill to turn a business, product, or operational question into a
bounded, evidence-backed Marivo investigation. This skill owns workflow
boundaries, handoffs, evidence continuity, and closeout obligations. The agent
owns planning, method choice, analytical judgment, synthesis, and stopping.

Use the host-selected verified environment. `marivo.help("analysis")` provides
progressive discovery; focused Help owns signatures, constraints, and examples.
Materialized Dataset `.show()` owns committed result state, Dataset `.contract()`
owns mechanically valid continuations, and structured errors own repair.
Governed semantic objects own reusable business meaning. Consult live guidance
when the next decision needs it; do not reconstruct contracts from memory or
private implementation details.

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
one question-scoped Session. Keep Population membership selection separate from
Metric observation time, and preserve both when continuing from Event or
Lifecycle subject selections. Resolve the exact typed semantic inputs together
and inspect readiness only for the required closure. Reuse a current semantic
handoff for the same project and scope instead of repeating it.

If the required capability is unknown, start with `marivo.help("analysis")`.
Use `marivo.help("analysis.entry")` to start or resume the investigation.
Follow `marivo.help("analysis.inputs")` only for missing governed inputs or
scope choices, then read the selected capability's focused Help and its required
input links. Once those facts support the next useful step, write and run it;
do not enumerate unrelated Help pages. Avoid broad catalog browsing when an
exact typed ref or full semantic identity is already available.

### 3. Build the minimum typed Evidence chain

Datasource connectivity and semantic readiness do not prove that a method can
execute on that source. Read `marivo.help("analysis.actions.execute")` for execution
boundaries and follow the concrete structured repair when a shape is unsupported.
Do not upload retained results or bypass governed analysis to evade a rejection.

Build the smallest logical Dataset chain that can satisfy the Evidence
obligation. Logical construction describes work; it is not evidence that rows
were read or results exist. Inspect the Dataset contract when state or the next
action is uncertain, and follow its exact Help target for the chosen continuation.
Execute when a result is needed for interpretation or an intentional recovery
boundary, then inspect the Materialized Dataset with `.show()`. Discover methods through
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
projection, proposed hypotheses, and causal evidence.

Use `marivo.help("analysis.artifacts")`,
`marivo.help("analysis.evidence")`, the Artifact's current state, and its
structured contract for the installed inspection mechanics. Preserve warnings
and partial coverage. Preserve the selected method and accuracy requirements
through comparisons and retained continuations; do not relax them merely to
obtain a result. Do not turn absence into zero, association into causation,
a point forecast into certainty, or a segment result into a population claim.

### 5. Synthesize, hand off, or stop

Close the investigation when every required answer is supported or explicitly
blocked. Separate observed or computed facts, interpretations supported by
those facts, recommendations or hypotheses requiring judgment, and unsupported
questions with their smallest missing evidence or semantic object.

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

Before exporting rows, read `marivo.help("analysis.actions.to_pandas")` and
distinguish inspection or presentation from a new calculation. Presentation may
plot, arrange, label, or format existing results; changing population, metric
meaning, aggregation, or comparison is analytical work and stays in typed flow
when supported. A failed precondition is not an unsupported method.

For a method outside the installed surface, complete the supported upstream
work before exporting. Keep external calculations rerunnable with exact inputs,
assumptions, and limitations; their outputs do not inherit typed Evidence
guarantees. Preserve the original Artifact identity and scope for presentation.
Terminality applies to the exported branch: the original Artifact remains
usable, but exported rows and derivatives cannot re-enter typed analysis.

Use `md.raw_sql(...)` only for a concrete source-specific question public
inspection cannot answer, or provisional terminal analysis when typed inputs
cannot be established. Read `marivo.help("datasource.raw_sql")` first, preserve
caller-stated access budgets, and disclose scope, truncation, and semantic gaps.
Raw SQL cannot replace available governed definitions or settle missing business
meaning; keep provisional results separate from canonical Evidence and hand
reusable gaps to `marivo-semantic`.

### Evidence continuity and recovery

Use one question-scoped session and carry exact Artifact identities across
decision rounds. Do not depend on process memory, an implicit latest result,
imported prior scripts, chat summaries, or repeated successful observations as
substitutes for persisted identity. A logical Dataset belongs to its originating
Session; carry an exact committed Artifact identity when moving between Sessions.
Never replay origin queries to conceal missing retained state. Reusing the
same Session and exact execution definition recovers its committed snapshot.
If the question requires current source rows, follow the live execution contract
for an explicit fresh observation boundary rather than treating a cache hit as
refresh evidence.

When resuming work, start with `marivo.help("analysis.runtime")`. Use bounded
Run history to locate the exact committed Artifact, public runtime reads to
restore it, and graph or Finding reads only when adjacency or audit detail is
needed. Consult `marivo.help("analysis.evidence")` before relying on recovered
Evidence.

Recovery and integrity checks do not establish current semantic authority,
source freshness, causality, or business validity. Follow live inspection and
repair guidance; disclose freshness as unchecked without a separate current
source check. If authority or Evidence cannot be restored, stop and disclose
only the affected branch and continue independent work.

### Structured repair and stopping

Follow the structured repair or focused Help for a failed public capability.
Do not invent a neighboring API, private workaround, or silent fallback. Stop
and disclose the affected branch when the current public contract cannot
produce the required Evidence.

### Resource decisions

Choose input scope and external runner resources deliberately. Use current result
contracts for complete-input requirements and semantic repairs. An external
resource failure does not authorize sampling, truncation or fallback; explain
the incomplete outcome before changing the analytical question or scope.

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
  audit;
- name missing reusable semantic objects and route their authoring to
  `marivo-semantic`.

Do not prescribe a fixed report template or continue exploring after the
required answers and limitations are complete. Delivery or publication belongs
to an independent capability when the user requests it.
