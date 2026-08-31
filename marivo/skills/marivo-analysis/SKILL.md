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

Trust the verified installed Marivo environment and its live guidance:

- `marivo.help("analysis")` owns the environment fingerprint, responsibility
  boundary, and progressive entry map;
- `analysis.entry`, `analysis.methods`, `analysis.inputs`, `analysis.artifacts`,
  `analysis.evidence`, and `analysis.runtime` are the six discovery hubs;
- focused `marivo.help("analysis.<target>")` owns exact signatures,
  constraints, return types, and examples;
- governed semantic objects own reusable business meaning;
- `.show()` owns bounded current artifact state;
- `.contract()` owns mechanically valid next actions;
- `marivo.help("analysis.boundary.to_pandas")` owns the exact terminal exit;
- structured errors own repair guidance.

Do not reconstruct those contracts from this skill, remembered syntax, or
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

Use `marivo.help("analysis.artifacts")`,
`marivo.help("analysis.evidence")`, the Artifact's current state, and its
structured contract for the installed inspection mechanics. Preserve warnings
and partial coverage. Do not turn absence into zero, association into causation,
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

Before a terminal conversion, read
`marivo.help("analysis.boundary.to_pandas")`. Exit typed flow only when the
required method is outside the installed governed surface and a bounded typed
Artifact already establishes the inputs, scope, and Evidence chain. Keep the
terminal calculation rerunnable alongside the exit, do not export and reload
Artifact rows, and never feed a terminal result back into typed analysis.

### Evidence continuity and recovery

Use one question-scoped session and carry exact Artifact identities across
decision rounds. Do not depend on process memory, an implicit latest result,
imported prior scripts, chat summaries, or repeated successful observations as
substitutes for persisted identity.

When resuming work, start with `marivo.help("analysis.runtime")` and use only its
public runtime reads to recover the relevant branch. Consult
`marivo.help("analysis.evidence")` before treating recovered Evidence as current.
Use the installed revalidation and repair guidance when authority-sensitive
reuse requires it; mechanical compatibility alone is not current semantic
authority.

Runtime history, graph projections, and persisted Evidence reads aid recovery
and audit. They do not by themselves establish current semantic authority,
datasource freshness, causal interpretation, or business validity. Stop and
disclose an affected branch when its authority or Evidence cannot be restored,
and disclose freshness as unchecked unless a separate current source check
establishes it.

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
  audit;
- name missing reusable semantic objects and route their authoring to
  `marivo-semantic`.

Do not prescribe a fixed report template or continue exploring after the
required answers and limitations are complete. Delivery or publication belongs
to an independent capability when the user requests it.
