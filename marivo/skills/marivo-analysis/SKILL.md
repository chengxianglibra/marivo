---
name: marivo-analysis
description: Use when a user wants to run or continue a trusted Marivo investigation over governed metrics, Events, StateModels, or persisted analysis artifacts.
---

# marivo-analysis

## Purpose and ownership

Use this skill to turn a business, product, or operational question into a
bounded, evidence-backed Marivo investigation. This skill owns workflow,
handoffs, hard boundaries, evidence continuity, and closeout obligations. The
agent owns planning, analytical judgment, and synthesis.

Trust the verified installed Marivo environment:

- `marivo.help("analysis")` owns the current capability map;
- `marivo.help("analysis.<target>")` owns exact signatures, constraints, return
  types, errors, and runnable examples;
- governed semantic objects own reusable business meaning;
- `.show()` owns bounded current artifact state;
- `.contract()` owns mechanically valid next actions;
- structured errors own repair guidance.

Do not reconstruct those contracts from this skill, remembered syntax, or
private implementation details.

## Capability routing map

Use this map to choose a capability family, then read its focused live help.
It is a conceptual route, not an exhaustive API inventory.

| Question or task | Capability | What it establishes |
| --- | --- | --- |
| What is the governed metric value? | `session.observe(...)` | Materializes governed metrics or a closed runtime metric expression into a typed `MetricFrame`. |
| Which rows, slices, windows, rankings, or rollups matter? | `frame.transform.*(...)` | Reshapes a `MetricFrame` or `DeltaFrame` without leaving its typed family. |
| How did two compatible periods or artifacts differ? | `session.compare(...)` | Aligns them into a typed `DeltaFrame`. |
| What contributed to a measured change? | `session.attribute(...)` | Reconciles a `DeltaFrame` over explicit governed axes. |
| Where should the investigation look next? | `session.discover.*(...)` | Produces bounded candidates for anomalies, shifts, drivers, slices, windows, peers, or semantic hypotheses. |
| Are two observed metrics associated? | `session.correlate(...)` | Measures statistical association without making a causal claim. |
| Does an explicit paired hypothesis hold? | `session.hypothesis_test(...)` | Tests prepared compatible metric artifacts under a typed sampling contract. |
| What may happen in future buckets? | `session.forecast(...)` | Projects a time-series or panel `MetricFrame`. |
| Is a published artifact trustworthy enough to use? | `frame.quality_report()` | Reads the fixed construction-time checks already committed with the artifact. |
| What happened across an Event journey? | `session.events.match/funnel/time_to_event(...)` | Matches typed journeys, reduces funnels, or measures elapsed time. |
| What happened in a governed StateModel? | `session.lifecycle.replay/distribution/transitions/dwell/violations(...)` | Replays canonical state history and applies lifecycle reducers. |
| Which exact subjects should continue into another typed branch? | `session.select_subjects(...)` | Materializes a closed `SubjectSet` from Event loss or Lifecycle state. |

Use `marivo.help("analysis.transform")`, `marivo.help("analysis.discover")`,
`marivo.help("analysis.events")`, or `marivo.help("analysis.lifecycle")` to see
the installed members of a family. Supporting builders such as time scopes,
grains, alignments, sampling policies, Event patterns, and runtime metric
expressions are also discovered through `marivo.help("analysis")` and their
focused targets.

## Marivo analysis method

### 1. Frame the question as an evidence contract

Record a short question-shaped checklist before execution. For each required
answer, identify:

- the claim or decision it supports;
- the exact population or carried-forward subset;
- the governed metric, Event, or StateModel;
- the time scope, comparison direction, and grouping or attribution axes;
- the minimum evidence that would support the answer;
- whether the answer is observed, interpreted, or currently unsupported.

Preserve scopes named by the user. Do not replace a selected cohort, segment,
top-N set, time window, comparison direction, or exhaustive request with a
broader or easier analysis. Add optional analysis only when it can materially
change a required conclusion, recommendation, or limitation.

### 2. Establish the environment and governed inputs

Use the host-selected project interpreter throughout. For a conventional local
project, prefer `.venv/bin/python` on macOS, Linux, and WSL or
`.venv/Scripts/python.exe` on Windows. Run
`<selected-python> -m marivo doctor` once when an environment fingerprint is
needed, and keep using the reported interpreter and package. Treat a diagnostic
failure as blocking only when it affects the required project or datasource.

Create or resume one question-scoped session. Resolve the exact typed semantic
inputs together and inspect readiness only for the required closure. Do not
repeat readiness when a current semantic handoff already supplies
`analysis_ready_inputs` for the same project and scope.

If the required capability is not known, read `marivo.help("analysis")`. Before
the first use of a selected capability, read its focused help. Avoid broad
catalog browsing when an exact typed ref or full semantic identity is already
available.

### 3. Build the minimum typed evidence chain

Enter typed analysis through the public family that matches the question:

- governed metric facts start with `observe`;
- Event journeys start with `events.match`;
- StateModel history starts with `lifecycle.replay`.

Run the first bounded typed artifact early. Inspect `.show()` when current state
contributes evidence. Use `.contract()` when the next mechanically valid action
is unknown. Choose subsequent operators from the question and the artifact in
hand rather than following a fixed operator recipe.

Before treating a required calculation as custom, classify its analytical
intent against the current capability map. Stay in typed flow whenever Marivo
expresses that intent; convenience or a small remaining step does not make the
calculation custom.

Batch compatible operations into one decision round. Prefer the smallest chain
that supports a required answer, and stop expanding the analysis when another
result cannot materially change the answer or its limitations.

### 4. Validate the evidence before interpreting it

For every material result, check the facts that can change its interpretation:

- semantic identity and exact population;
- time coverage, completeness, censoring, and comparison alignment;
- grain, units, additivity, and reconciliation where applicable;
- missingness, uncertainty, and quality blockers;
- whether the result is an observation, association, projection, or hypothesis
  test rather than causal evidence.

Supported Frame families run fixed quality checks automatically before
publication. A blocking `ArtifactQualityError` means no usable Artifact was
published. For a material published result, inspect `frame.quality_summary` and
read `frame.quality_report()` when check-level detail matters; this is a
read-only sidecar lookup and does not create a job. Preserve warnings and
partial coverage; do not turn absence into zero, association into causation, a
point forecast into certainty, or a segment result into a population claim.

### 5. Synthesize, hand off, or stop

Update the question checklist from current artifacts. Close the investigation
when every required answer is supported or explicitly blocked. Separate:

- observed or computed facts from current artifacts;
- interpretations supported by those facts;
- recommendations or hypotheses that require judgment;
- unsupported questions and the smallest missing evidence or semantic object.

Hand only reusable semantic gaps to `marivo-semantic`. Resume the affected
analysis branch from its returned `analysis_ready_inputs`; do not restart
unaffected branches or require a redundant user approval.

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

A `runtime_metric` is valid only as a question-scoped expression over governed
inputs. It does not acquire a catalog identity, owner, readiness status, or
durable business definition. When it materially affects a conclusion, preserve
its dependencies, slices, policies, derived unit/additivity limits, and relevant
zero or missing-value behavior in the evidence. Hand it to `marivo-semantic` if
it must become reusable organizational truth.

### Typed execution and terminal exits

Do not query business rows through Ibis, DuckDB, pandas readers, backend
connections, private datasource handles, or ad hoc SQL to bypass Marivo.

Use `frame.to_pandas()` only after a bounded typed artifact establishes the
semantic inputs, scope, and evidence chain, and only when the capability check
above establishes that the intended analysis method is outside Marivo's
governed typed surface. Keep that calculation in the same rerunnable step script
as the conversion. Do not export and reload artifact rows, and never feed a
terminal result back into typed analysis.

### Evidence continuity and recovery

Use one question-scoped session. For a fresh session, write the first decision
round to a real temporary `00_bootstrap.py` file. Let it create the session and
produce the first bounded artifact. After `session.id` is known, move the same
file without changing its bytes to
`<project_root>/.marivo/analysis/sessions/<session.id>/scripts/00_bootstrap.py`.
Do not create the session with an inline interpreter command.

Write later rounds as ordered `01_*.py`, `02_*.py`, and so on in the session's
`scripts/` directory. Once executed, each script is an immutable source record
for one decision round. Apply a repair in a new step script rather than editing
or re-executing an earlier one.

Carry dependencies by exact artifact ref and restore them with
`session.get_frame(ref)`. Do not share Python variables across rounds, import a
prior script, select an implicit latest result, or repeat a successful
observation merely to obtain a new artifact id. Label new operations with a
concise `analysis_purpose` and show only artifacts produced in the current
round.

After restoring an old Artifact with `session.get_frame(ref)`, read
`marivo.help("analysis.session.revalidate")` and run
`session.revalidate(frame)` before treating it as current evidence. Continue
only when the result is admissible. Re-run a stale branch from its producing
operator; stop and disclose an indeterminate branch until its authority or
evidence can be restored. Revalidation proves persisted identity, current
semantic authority, and evidence consistency; it does not prove datasource
freshness.

Artifact-consuming capabilities enforce their registered authority requirement
at execution. Follow `ArtifactStaleError` or `ArtifactAuthorityUnknownError`
repairs instead of recreating catalog-current checks or bypassing the typed
operator. A materialized continuation may remain valid after catalog drift;
use focused help to inspect that capability's requirement. Never treat
`artifact.contract()` as current revalidation: it describes only mechanical
commit-time compatibility.

When resuming work, start with `marivo.help("analysis.recovery")`. Read bounded
session inspection, frame summaries, recent jobs, evidence digests, and findings
before loading exact supporting artifacts. Historical conclusions and chat or
script summaries are navigation aids, not current evidence.

Before combining two or more Findings, including Findings from the same
artifact or analysis round, read
`marivo.help("analysis.session.evidence.compatibility")` and run the public
compatibility check over the exact Finding ids. Continue mechanical synthesis
only when it is compatible. Split or repair an incompatible selection; stop and
disclose an indeterminate selection instead of inferring through its unknown
authority or rule boundary.

### Structured repair and stopping

Follow the structured repair or focused help for a failed public capability.
Do not invent a neighboring API, private workaround, or silent fallback. Stop
and disclose the affected branch when the current public contract cannot
produce the required evidence.

## Closeout

Answer the user's questions first in their business vocabulary. For every
material conclusion:

- state the supported direction and the magnitude or uncertainty needed to
  interpret it;
- preserve the exact source, scope, definition, and comparison that govern it;
- distinguish evidence, interpretation, recommendation, and hypothesis;
- disclose material blockers, warnings, omissions, quality limits, and terminal
  exits;
- keep the supporting semantic refs, session/job, artifact, and scope
  recoverable without exposing runtime bookkeeping unless the user requests an
  audit;
- name missing reusable semantic objects and route their authoring to
  `marivo-semantic`.

Do not prescribe a fixed report template or continue exploring after the
required answers and limitations are complete. Delivery or publication belongs
to an independent capability when the user requests it.
