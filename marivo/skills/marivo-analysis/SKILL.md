---
name: marivo-analysis
description: Use when a user wants to answer a business question or continue a Marivo investigation over governed entities, fields, metrics, or persisted analysis artifacts. Guides analytical judgment, evidence continuity, semantic handoffs, and stopping.
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
exposes committed result state. Population and Query `.contract()` own
mechanically valid continuations; terminal Artifacts have no computation
contract. Structured errors own repair. A valid continuation need not
be useful to the question; successful execution alone does not answer it.

Use the host-selected verified environment. Consult live guidance when the next
decision needs it, without reconstructing API contracts from memory or private
implementation details.

## Construction and reusable meaning

Physical authoring captures lookup keys before dependency closure. Finalize the
complete physical scope before consuming immutable values; resolve needed
fields from that same frozen scope. Keep capture and expression composition in
order. Missing dependencies are capture repairs, not permission to discard an
Ibis forward reference or refresh a value already returned.
Do not require original semantic-definition versions or reject an input solely
because a same-path definition changed. The agent/user accepts the consequences
of semantic changes during analysis. Keep kind, root, grain, unit, time-role and
null checks; fingerprints/cache keys must describe actual consumed definitions,
and persisted records keep their owning contracts. Resolving consumers use their
complete scope; this does not refresh immutable values or replay authoring.

Compose question-scoped row reductions and completed Metrics from constructed
semantic values. A zero-argument Metric declaration can name a local definition
without model registration; output labels do not require reusable publication.
A new row quantity that needs additional sum permission is a Measure business
assertion and must retain its source, units and fixed time constraints. Use the
semantic workflow when that business authority is unresolved.

Construction, Catalog publication and execution are separate capabilities. A
constructed expression or successful leaf load is not evidence that a Session
can execute the new expression path. Use current Help and actual checks to state
which boundary is available; do not fall back to retired Ref-based factories or
Ibis Metric callbacks.

## Analytical judgment

Before choosing an operation, establish the distinctions that affect the answer:

- Identify the unit of analysis: members, contributions, or current result rows.
  One member can contribute several facts; do not silently change the
  denominator or generalize a selected segment to the whole population.
- Keep population selection, Metric observation, and output time coordinates
  distinct. Incomplete or unknown source coverage cannot establish absence.
- Distinguish statistics over current result rows from aggregation of an
  original Metric. They may describe different quantities; preserve grain,
  units, weighting, and additivity when choosing the intended calculation.
- Choose output grain independently of member scope. Ordinary observation
  computes the overall Metric; preserve member identity explicitly for member
  screening, comparisons or association. A time grid retains its buckets while
  spatial grouping chooses who shares each value. Preserve attribution
  partitions before reducing them, and use current-row statistics only when the
  question concerns the represented rows rather than the original Metric.
- Establish the comparison baseline and comparability before interpreting a
  difference. Algebraic attribution and association do not establish cause;
  forecasts describe model outputs under assumptions, not observed outcomes.
- Distinguish reopening committed output, postprocessing exported values, and
  executing a new Query against current sources. Choose according to the
  question's freshness need; an Artifact is not an input to typed computation.

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
selected intent and its prerequisites. For a Population or Query, use its
contract and exact Help target when the next action is uncertain. Read a
terminal Artifact through its output and evidence interfaces. Stop browsing once the
available guidance supports the next useful operation. Connectivity and semantic
readiness alone do not establish method execution support.

Compose known Population and Query dependencies before executing. Execute
when values are needed for interpretation or delivery. If the next choice
depends on those values, read the terminal output, then construct a new Query
from semantic inputs and explicit chosen keys. Reusing a Query executes its
sources again; reopening an Artifact only reads saved output. Logical
construction is not evidence that business rows were read or a result exists.

### Read the result and update the answer

Inspect the terminal Artifact with `.show()`. Follow
`marivo.help("analysis.evidence")` when additional evidence detail is needed.
Assess the facts that could change the conclusion: exact identity and scope,
comparison alignment, completeness, missingness, reconciliation,
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

Before exporting, read `marivo.help("analysis.Artifact.to_pandas")`. Presentation
may arrange, plot, label, or format existing results. Changing population,
metric meaning, aggregation, or comparison is analytical work. For a method
outside the installed surface, first complete supported upstream work, then
preserve exact inputs, assumptions, and rerunnable external calculations.
External outputs do not inherit typed Evidence guarantees. An exported table
cannot restore the computation graph. Chosen complete keys or explicit literal
references may become ordinary inputs to a new Query, without inheriting the
exported result's semantic or Evidence authority. The saved Artifact remains
readable and cannot supply a computation graph.

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
