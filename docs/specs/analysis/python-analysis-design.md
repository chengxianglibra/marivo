# Python Analysis Design

Status: design. This document is the overview of `marivo.analysis`, the analysis
layer of the Marivo Python library. It describes the design philosophy — what the
layer is for, the line it draws between computation and judgment, and how its
pieces fit — and points to the focused specs that define each area in detail. It
is a design document; not every stated capability is fully implemented.

`marivo.analysis` is consumed primarily by general coding agents (Claude Code,
Codex) through a write-run-read loop. The alias throughout is `mv`
(`import marivo.analysis as mv`). It builds on the semantic layer
([`../semantic/overview.md`](../semantic/overview.md)):
analysis consumes stable semantic refs and materialized metrics and never guesses
business meaning from column or table names.

## Design goals

The analysis API is not a menu of BI features and does not expose SQL, tables, or
ad-hoc workflows as its primary contract. It is a small set of composable
operators over canonical artifacts, built for real analysis of complex internet
business data. The target API:

- Lets an agent express common metric analysis with a few stable core operators.
- Fixes exactly one canonical artifact family per public core operator; parameters
  change the algorithm, grain, scope, ranking, or policy — never the output family.
- Composes downstream through artifact refs, selector refs, typed policies, and
  typed inputs, never free-text interpretation.
- Collects exploratory analysis into typed `CandidateSet[...]` rather than a
  separate core operator per anomaly/driver/window/outlier objective.
- Defaults to a step-wise analysis session: an agent reads an intermediate result,
  then continues, while lineage stays continuous.

The decisive test: if a capability would return different artifact families under
different parameters, it is not one public core operator — it is split, promoted to
a typed composite, or demoted to a projection/terminal exit. Closed typed shapes
within a family (e.g. `MetricFrame[time_series]`, `CandidateSet[driver_axis]`) are
allowed for ergonomics.

## Computation versus judgment

The layer's central boundary: Marivo makes each computation reliable, reproducible,
auditable, and recoverable; the agent does the analytical planning and judgment.

| Marivo exposes deterministically | Only the agent decides |
| --- | --- |
| Type-legal operators/capabilities an artifact can feed | Which operator to run next |
| Required inputs and pass/fail preconditions | Which candidate is "meaningful" |
| Fixed-algorithm scores, candidates, contributions | The objective, threshold, axis, cohort |
| Mechanically pre-fillable params (current ref, resolved window) | The judgment-bearing params |
| Fact summaries, quality status, blocking issues, lineage | Conclusions, headlines, narrative, stop criteria |

Marivo therefore does not: plan an analysis DAG from a natural-language question;
auto-pick the "best next step"; rank/recommend/headline legal next steps; decide
whether analysis should continue; dress a candidate/correlation/low-quality
attribution as a business conclusion; or write an agent's working conclusion into
an artifact's factual truth.

A direct consequence lives in the result surface: **analysis operators do not write
stdout; every result is silent and returns a typed object.** A `repr` or `show()`
carries only deterministic descriptors (ref, kind, materialization state, row
count, fixed-rule totals) — never a headline that implies a business conclusion.
The full result contract is specified in
[`operators-and-frames.md`](operators-and-frames.md).

## The write-run-read loop

An agent uses Marivo in a loop that may span many turns, compacted context, and
separate script files:

```text
write analysis script -> run -> read result -> revise -> run again
```

Frames and results are therefore not just "a return value plus metadata" — they are
persistent, recomputable, cold-start-recoverable, progressively-readable nodes of an
analysis DAG. Four constraints follow, and they shape the runtime
([`session-state-and-runtime.md`](session-state-and-runtime.md)):

| Constraint | Loop reality | Requirement |
| --- | --- | --- |
| Recompute-safe | each turn may re-run an accumulating script | operators are pure; artifacts carry fingerprint/cache metadata; re-running never drifts |
| Cold-start rebuild | turn N+1 may lose in-memory objects | `get_frame(ref)` and persisted metadata restore kind, schema, lineage, quality, blocking |
| Read economics | every frame read costs context tokens | layered reads (`repr -> show() -> contract() -> to_pandas()`) avoid forcing a full read |
| Resumable failure | step *k* fails after *k-1* materialized | operators fail loud; the session/job layer keeps completed upstream refs and structured errors |

## Layered operator model

The API is five layers. The agent-facing surface is the small core; the rest is
either family-preserving reshaping or controlled escape.

1. **Source-to-artifact** — read a semantic metric into the start of a chain:
   `observe -> MetricFrame`. `session.observe(...)` is the sole canonical
   `MetricFrame` producer.
2. **Family-preserving transform** — reshape/scope/rank an artifact without changing
   its family: `session.transform.<op>` over a `MetricFrame` or `DeltaFrame`. The
   output family follows the input; cross-family derivation must use a named
   operator.
3. **Core cross-family analysis** — the operators that change analysis semantics,
   each with a fixed output family: `compare -> DeltaFrame`,
   `attribute -> AttributionFrame`, `discover.<objective> -> CandidateSet`,
   `correlate -> AssociationResult`, `hypothesis_test -> HypothesisTestResult`,
   `forecast -> ForecastFrame`, `assess_quality -> QualityReport`.
4. **Composite** — stable multi-step entry points admitted only when they carry a
   cross-step constraint an agent would miss; each fixes one output family. No
   composite is on the current default surface (`attribute` is a core operator).
5. **Projection / terminal exit** — bounded reads (`show()`, `render()`,
   `contract()`) and terminal exits out of the canonical chain
   (`frame.to_pandas()`, `md.raw_sql(...)`). There is no inbound path from
   ad-hoc Ibis/pandas/SQL back into typed analysis.

Layers 1–4 and the artifact algebra are specified in
[`operators-and-frames.md`](operators-and-frames.md).

## Guidance layering

Three layers own analysis guidance, each with one job — an agent consults the right
one instead of a single monolithic manual:

- **Live surfaces — capabilities and runtime guidance.** The CLI route
  `python -m marivo help analysis [target]` and `mv.help(...)` own the static
  contract: signatures, artifact families, constraints, return types, errors, and
  runnable examples. `mv.help()` is a short index grouped by capability family;
  `mv.help("<target>")` (e.g. `observe`, `compare`, `recover`) expands one
  capability with a minimal example. Frames and results own dynamic guidance:
  `show()` describes an artifact's current state; `contract()` describes the
  mechanically valid next actions from where it is now. Structured errors own
  repair guidance with typed `AnalysisRepair` instructions. Judgment stays with
  the agent.
- **The `marivo-analysis` skill — hard boundaries, handoffs, evidence continuity,
  and closeout obligations.** It is a one-file boundary kernel. It does not
  duplicate the help contract, frame/result guidance, or error repair guidance.
  It does not prescribe an ordered operator sequence or a report template.
- **The agent — planning and judgment.** Given the contract, the boundaries, and
  the dynamic guidance, the agent owns which operator to reach for, which judgment
  slots to fill, whether to stop, and how to synthesize conclusions.

## Usage model

The default authoring model is a step-wise session. An agent creates or resumes a
session, observes metrics, and composes typed operators, reading intermediate
results to decide the next step:

```python
import marivo.analysis as mv

session = mv.session.get_or_create("q4-revenue", question="Why did Q4 drop?")

current = session.observe(
    metric=session.catalog.get("metric.analytics.dau"),
    time_scope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
)
baseline = session.observe(
    metric=session.catalog.get("metric.analytics.dau"),
    time_scope={"start": "2026-06-11", "end": "2026-06-18"},
    grain="day",
)
delta = session.compare(current, baseline, alignment=mv.window_bucket())
delta.show()                       # bounded card; nothing printed unless asked
delta.contract()                   # which operators this delta can feed
```

Which operator to reach for follows the artifact in hand: observe a metric first;
`compare` two observed frames for a change; `attribute` a delta over explicit axes;
`discover.<objective>` when the axis/window/slice worth examining is unknown;
`hypothesis_test` to check an explicit hypothesis; `forecast` to project observed
history; `assess_quality` to gate any of them. Concrete intent paths, composition
patterns, and report shape are the agent's responsibility; the `marivo-analysis`
skill owns boundaries and handoffs only. The mechanical next actions from any
given artifact come from its `contract()`.

## Non-goals

The analysis layer does not: dress arbitrary Ibis/SQL as a core operator; pass
generic pandas/sklearn wrappers off as canonical artifact producers; do causal
inference or what-if simulation; auto-generate business conclusions; emit free text
as its primary output; or map one BI chart template to one core operator.

## Document map

This overview is the entry point. The focused specs:

- [`operators-and-frames.md`](operators-and-frames.md) — the operator algebra:
  frame/result families, typed shapes and policies, the agent-facing core surface,
  per-operator detail, the result/read contract, the shape-aware DAG, and the
  terminal boundaries.
- [`session-state-and-runtime.md`](session-state-and-runtime.md) — the `Session`
  object, the project-local `.marivo/analysis/` layout, content-addressed identity,
  cold-start rehydration, cross-session ownership, and failure recovery.
- [`evidence-access-surface.md`](evidence-access-surface.md) — the result-bound,
  session-bound, and object-bound evidence surfaces, follow-up generation rules, the
  `judgment.db` ledger, identity/stability, and the exception taxonomy.
- [`timezone-and-calendar-design.md`](timezone-and-calendar-design.md) — the two
  timezone axes (read tz and report tz), time-column classification, window/bucket
  computation, and calendar alignment.
