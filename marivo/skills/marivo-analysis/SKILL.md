---
name: marivo-analysis
description: Use when a user wants to answer a business question or continue a Marivo investigation over governed metrics, Events, StateModels, or persisted analysis artifacts. Guides analytical judgment, evidence continuity, semantic handoffs, and stopping.
---

# marivo-analysis

## Purpose and ownership

Turn the user's question into a bounded, evidence-backed investigation. This
skill guides investigation decisions, boundaries, handoffs, and completion. The
agent chooses methods, plans the work, interprets results, and decides when the
answer is sufficient.

Governed semantic objects own reusable business meaning. The Analysis DSL owns
typed computation and Evidence. `marivo.help("analysis")` provides progressive
discovery; focused Help owns signatures, constraints, and examples. `.show()`
exposes committed result state, `.contract()` owns mechanically valid
continuations, and structured errors own repair. A valid continuation need not
be useful to the question; successful execution alone does not answer it.

Use the host-selected verified environment. Consult live guidance when the next
decision needs it, without reconstructing API contracts from memory or private
implementation details.

## Analytical judgment

Before choosing an operation, establish the distinctions that affect the answer:

- Identify the unit of analysis: subjects, event occurrences, or state history.
  One subject can contribute several occurrences; do not silently change the
  denominator or generalize a selected segment to the whole population.
- Keep population selection, Metric observation, and follow-up windows distinct.
  Preserve their alignment through Event and Lifecycle selections; incomplete
  follow-up or unknown coverage cannot establish absence.
- Distinguish statistics over current result rows from aggregation of an
  original Metric. They may describe different quantities; preserve grain,
  units, weighting, and additivity when choosing the intended calculation.
- Establish the comparison baseline and comparability before interpreting a
  difference. Algebraic attribution and association do not establish cause;
  forecasts describe model outputs under assumptions, not observed outcomes.
- Distinguish recovering a past result, continuing over retained data, and
  observing current sources. Choose according to the question's freshness need.

Use these distinctions where they matter. Exact methods and admitted inputs
come from live Help and the current object's contract, not a fixed operator recipe.

## Question-driven decision loop

Enter at the unresolved decision. A simple question may need one round; existing
work may already supply the inputs or results. Reuse established facts and
completed checks instead of restarting the workflow.

### Identify the remaining answer and evidence gap

Identify what the user still needs to know and what evidence would support it.
Preserve the requested population, governed meaning, time window, comparison
direction, selected cohort or top-N set, accuracy, and completeness. Keep this
framing proportional to the task; it does not require a separate document.

Distinguish missing reusable meaning, a missing computation, incomplete
coverage, and an interpretation that the available evidence cannot support.
Reuse exact semantic refs, a current analysis-ready handoff, and relevant
committed results. Check readiness only for missing required inputs.

### Choose the next useful step

Choose work that can materially change a required answer, recommendation, or
limitation. Prefer the smallest sufficient typed analysis chain and batch known,
compatible work into one decision round.

For an unknown capability, start with `marivo.help("analysis")` and follow the
selected intent and its prerequisites. For an existing object, use its contract
and exact Help target when the next action is uncertain. Stop browsing once the
available guidance supports the next useful operation. Connectivity and semantic
readiness alone do not establish method execution support.

Identify known downstream input needs before executing. Do not assume that a
materialized selection can accept a new live-source observation; follow the
current contract to construct the required dependencies. Execute when results
are needed for interpretation, delivery, an intentional recovery boundary, or a
required execution boundary. If the next choice depends on those results, read
them before expanding the graph. Logical construction is planned work, not
evidence that business rows were read or a result exists.

### Read the result and update the answer

Inspect the materialized result with `.show()`. Follow
`marivo.help("analysis.evidence")` when additional evidence detail is needed.
Assess the facts that could change the conclusion: exact identity and scope,
comparison alignment, completeness and censoring, missingness, reconciliation,
uncertainty, and material warnings. Do not repeat mechanical checks already
owned by the DSL or treat its guarantees as proof of business validity.

Update what is supported, what remains an interpretation, and what is still
unknown. Do not turn missing values into zero, partial coverage into complete
coverage, or a point forecast into certainty. Continue only for a remaining
material gap; otherwise complete the answer.

## Boundaries and handoffs

### Semantic authority and persistence

Use governed definitions and relationships for reusable business meaning.
Analysis may choose question-specific windows, cohorts, policies, and runtime
expressions over governed inputs. It must not invent a physical-column meaning,
join, substitute metric, or reusable definition to make an analysis proceed.

When analysis reveals a missing reusable semantic definition, tell the user
what is missing, why it matters, and that it should be persisted through
`marivo-semantic` for future reuse. Include any candidate meaning and unresolved
business choices in the handoff. A question-scoped expression is not a persisted
semantic definition; do not silently promote it to organizational truth.

Hand reusable authoring or repair to `marivo-semantic`, reusing existing scope
and authorization. Ask only for material unresolved business meaning or missing
execution scope. A blocking gap pauses only the affected branch; a future reuse
recommendation need not block a valid question-scoped calculation. After the
handoff, resume with the returned analysis-ready inputs without restarting
unaffected work. Keep unpersisted definitions visible in the final answer.

### Typed computation and terminal exits

Before calling a calculation custom, classify its intent against live capability
Help. Keep supported analysis in typed flow. A failed precondition is not an
unsupported method; follow structured repair and disclose a blocked branch when
the public contract cannot produce its required evidence. Do not bypass it with
direct Ibis, DuckDB, pandas readers, backend handles, ad hoc SQL, or uploads.

Before exporting, read `marivo.help("analysis.actions.to_pandas")`. Presentation
may arrange, plot, label, or format existing results. Changing population,
metric meaning, aggregation, or comparison is analytical work. For a method
outside the installed surface, first complete supported upstream work, then
preserve exact inputs, assumptions, and rerunnable external calculations.
External outputs do not inherit typed Evidence guarantees. Exported rows and
derivatives cannot re-enter typed analysis; the original Artifact remains usable.

Use `md.raw_sql(...)` only for a concrete source-specific question public
inspection cannot answer, or provisional terminal analysis when typed inputs
cannot be established. Read `marivo.help("datasource.raw_sql")`, preserve access
budgets, and disclose scope, truncation, and semantic gaps. Raw SQL cannot replace
available governed definitions or settle missing business meaning. Keep its
provisional results separate from canonical Evidence.

### Evidence continuity

Use one question-scoped Session and exact committed Artifact identities across
rounds. Process memory, an implicit latest result, scripts, and chat summaries
are not substitutes for persisted identity. Logical objects stay in their
originating Session; follow public recovery when crossing Session boundaries.

Use `marivo.help("analysis.runtime")` for recovery mechanics. Never replay source
queries or reconstruct displayed rows to conceal missing retained state. Local
committed results are trusted; recovery does not establish current semantic
authority, source freshness, causality, or suitability for the question. If a
current observation is needed, make it explicit. If recovery fails, disclose the
affected branch and continue independent work.

### Scope and resources

Choose input scope and external runner resources deliberately. Preserve
caller-stated access budgets and the requested method, accuracy, and completeness
through retries and continuations. Failure does not authorize sampling,
truncation, relaxed precision, or an easier population. Disclose incomplete work
and resolve any necessary change to the question before proceeding with it.

## Completion and communication

Finish when every required answer is supported or explicitly blocked with its
smallest missing evidence or definition. Answer in the user's business vocabulary:
state the supported direction and magnitude or uncertainty; distinguish computed
facts, interpretation, recommendations, and hypotheses; disclose material
warnings, omissions, terminal exits, and unresolved semantic persistence needs.

Keep supporting definitions, scope, Session, Run, and Artifact identities
recoverable without exposing runtime bookkeeping unless an audit is requested.
Use a format suited to the question. Do not continue exploring after the answers
and limitations are complete. Delivery or publication uses an independent
capability when requested.
