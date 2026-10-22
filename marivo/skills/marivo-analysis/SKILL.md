---
name: marivo-analysis
description: Use when a user wants to answer a business question or continue a Marivo investigation over governed entities, fields, metrics, or persisted analysis artifacts. Guides analytical judgment, evidence continuity, semantic handoffs, and stopping.
---

# marivo-analysis

## Purpose and ownership

Answer the business question with bounded, governed Evidence. The agent owns
method choice, interpretation and completion; semantic objects own reusable
meaning, and the Analysis DSL owns typed computation.

Start unknown capabilities at `marivo help analysis`. Focused Help owns
signatures, constraints and examples; type Help owns operation discovery.
Population/AnalysisFrame `.show()` owns captured
facts; Artifact `.show()` owns committed audit. Artifact is not a computation operand. Structured errors own the current repair.
Use the host-selected verified environment, not private source or remembered
signatures. Construction, execution support and business suitability are
separate facts.

## Governed inputs and question-scoped meaning

Compose question-scoped quantities over governed inputs when their business
meaning and required aggregation authority are established. Naming a local
quantity does not require reusable publication. Use `marivo help semantic.expressions`
for the shared expression roles; absence of a published Metric with the same
name is not itself an authoring gap. A new business assertion, such
as authorizing a row quantity to be summed, belongs to the semantic workflow
when that authority is unresolved.

## Analytical judgment

Before choosing an operation, establish the distinctions that affect the answer:

- Identify the counted domain: complete distinct members or coordinate tuples,
  source contributions under the selected conditions and time, or current
  Frame rows. Selected members can have no facts, and one member can contribute
  several facts; preserve the denominator and qualify claims that generalize a
  selected segment to the whole population.
  Interpret rates and growth with their numerators, denominators and absolute
  changes; distinguish percent change from percentage points, and qualify
  comparisons with small or near-zero baselines.
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
- For date comparisons, consider business days, weekends, holidays, shifted
  workdays, seasonality and operating hours. Choose a baseline suited to the
  question and disclose material calendar differences that remain.
- Distinguish unfinished periods, delayed data and outcomes that have not had
  time to mature. Compare compatible effective cutoffs or observation durations;
  otherwise qualify the comparison or state why it is not yet supported.
- Distinguish changes within groups from changes in their relative shares.
  When composition could change the conclusion, examine it before attributing
  an overall movement to improved or worsened performance within groups.
- Before explaining a discontinuity as business change, consider known changes
  in definitions, instrumentation, coverage, backfills or systems. Disclose
  unresolved measurement changes that could explain the movement.
- Treat patterns selected after inspecting many groups, windows or relationships
  as exploratory. Rechecking the same selected data is not independent
  confirmation; qualify claims for selection and repeated comparisons.
- Distinguish reopening committed output, postprocessing exported values, and
  constructing explicit new source inputs against current data. Choose according to the
  question's freshness need; an Artifact is not an input to typed computation.

Apply only checks that could change the current conclusion. Do not silently
exclude holidays or outliers, change weights, or alter the question to make a
comparison work. Exact methods and admitted inputs come from live Help and
captured object facts, not a fixed operator recipe.

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
committed results. Readiness checks physical dependencies, not published
expressions or method execution support; repeat it only for a missing input.

### Choose the next useful step

Choose work that can materially change a required answer, recommendation, or
limitation. Prefer the smallest sufficient typed analysis chain and batch known,
compatible work into one decision round.

Choose disclosure for the current gap:

- Unknown method: start at `marivo help analysis` and follow the current
  information gap. Source questions use governed inputs or explicit member
  domains; Frame work calculates over the represented data. Construction
  captures dependencies, so a source question need not read a source and
  evaluating a pending Frame can still execute its captured source dependencies.
- Unknown identity: browse a bounded catalog collection; select closed inputs by
  exact path through the matching collection. Native lookup selectors belong only
  to explicitly supported metadata/readiness operations, not ordinary analysis.
- Missing syntax or precondition: read the exact callable Help.
- Missing captured facts: read Population/AnalysisFrame `.show()`; use `frame.output_names`
  for complete names. Type Help lists operations when the next method is unknown.
- Missing saved output: reopen the exact persisted Frame and read its audit; use recovery
  guidance only when its identity or Session is missing.

Stop browsing once the next useful operation is supported. Related routes and
complete method lists are options, not prerequisites. Do not read sibling pages
or reconstruct signatures before trying a documented call. Logical construction
can proceed without values; evaluate when the next decision needs values.
Connectivity and readiness do not qualify the selected method.

Compose known Population and AnalysisFrame dependencies before evaluation. Use
`compute()` when the next decision needs complete fixed memory data; use
`persist()` when the result must remain recoverable across processes. Continue
from the returned Frame to retain those exact values and selected keys. Reusing
a source expression may read changed sources; it does not reuse a previous answer.
For a follow-up requiring new facts, provide explicit source inputs and map
ordinary complete axes with `session.population_from(..., on=...)` when needed.
A restored Frame does not recover old semantic objects or the producer's selection program.
Logical construction does not prove business rows were read or a result exists.

### Read the result and update the answer

Inspect the returned Frame with `.show()`; read its `.artifact` audit when committed. Follow
`marivo help analysis.results.evidence` when additional evidence detail is needed.
Assess the facts that could change the conclusion: exact identity and scope,
comparison alignment, completeness, missingness, reconciliation,
uncertainty, and material warnings. Consider whether few observations, extreme
values or short-term fluctuations dominate the result before generalizing it.
Distinguish observed differences, statistical evidence and business importance.
Do not repeat mechanical checks already owned by the DSL or treat its guarantees
as proof of business validity.

Update what is supported, what remains an interpretation, and what is still
unknown. Do not turn missing values into zero, partial coverage into complete
coverage, or a point forecast into certainty. Continue only for a remaining
material gap; otherwise complete the answer.

## Boundaries and handoffs

### Semantic authority and persistence

Use governed definitions, business calendars and relationships for reusable
business meaning.
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

Before exporting, read `marivo help analysis.analysis_frame.to_pandas` and
identify whether requested typed work remains. Supported transformations of
complete Frame data can continue after compute, persist or exact recovery.
For original Metric contributions or new attributes, check whether the current
Frame retains an admitted source declaration in this process. Its attribute
operation may read fresh source facts for the selected fixed parent keys;
distinguish the observed parent values from the newly evaluated totals.
An independently reopened Frame has no executable declaration. Continue its
fixed-data operations or explicitly rebuild the source question with governed
definitions and the saved joint keys. Current-row statistics and saved method
facts cannot reconstruct that declaration. Arranging, plotting, labeling and
formatting saved values are presentation.
Numerical arithmetic is not automatically presentation.
For a method outside the installed surface, first complete supported upstream
work, then preserve exact inputs, assumptions and rerunnable external calculations.
External outputs do not inherit typed Evidence guarantees. Exported tables and
attrs cannot restore semantic objects, source history or typed authority. The
saved Frame remains available for supported data operations; its Artifact is an
audit view, not a computation operand.

Use `md.raw_sql(...)` only for a concrete source-specific question public
inspection cannot answer, or provisional terminal analysis when typed inputs
cannot be established. Read `marivo help datasource.raw_sql`, preserve access
budgets, and disclose scope, truncation, and semantic gaps. Raw SQL cannot replace
available governed definitions or settle missing business meaning. Keep its
provisional results separate from canonical Evidence.

### Evidence continuity

Use one question-scoped Session and retain its exact ID across rounds. Continue
from complete memory Frames while their exact values remain available in the
same process; compute-only Frames have no Artifact. When cross-process recovery
is needed, persist and retain `frame.artifact.id` from the returned stored Frame.
An implicit latest result, scripts, and chat summaries do not replace that
persisted identity. Frame handles stay in their originating Session; reopen with
`session.persisted_frame(id)` in that Session when restoring committed data.

Use `marivo help analysis.sessions` for recovery mechanics. Never replay source
queries or reconstruct displayed rows to conceal missing retained state. Local
committed results are trusted; recovery does not establish current semantic
authority, source freshness, causality, or suitability for the question. If a
current observation is needed, make it explicit. If recovery fails, disclose the
affected branch and continue independent work. If a returned Artifact identity
was lost, explicitly resubmit the analysis rather than claiming recovery. This
may create duplicate saved outputs; a new source evaluation may return changed
data. Reuse complete memory data when exact prior values remain available.

### Scope and resources

Choose input scope and external runner resources deliberately. Preserve
caller-stated access budgets and the requested method, accuracy, and completeness
through retries and continuations. Failure does not authorize sampling,
truncation, relaxed precision, or an easier population. Disclose incomplete work
and resolve any necessary change to the question before proceeding with it.

## Completion and communication

Finish when every requested analytical transformation and answer is supported
or explicitly blocked with its smallest missing evidence or definition. A saved
result alone does not establish that comparisons, denominators or other
required transformations were completed in typed flow. Answer in the user's business vocabulary:
state the supported direction and magnitude or uncertainty; distinguish computed
facts, interpretation, recommendations, and hypotheses; disclose material
warnings, omissions, terminal exits, and unresolved semantic persistence needs.

Keep supporting definitions, scope, Session and Artifact identities
recoverable without exposing runtime bookkeeping unless an audit is requested.
Stop exploring once answers and limitations are complete. Delivery or publication
uses an independent capability when requested.
