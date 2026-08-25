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
business meaning from column or table names. At qualifying catalog-bound runtime
inputs it may receive the exact current catalog entry or its exact ref; both
normalize immediately to the same ref before planning or persistence.

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

1. **Source-to-artifact** — materialize governed semantics into the start of a
   typed chain: `observe -> MetricFrame`, `events.match -> EventFrame[journey]`,
   and `lifecycle.replay -> LifecycleFrame[history]`.
   `session.observe(...)` remains the sole canonical `MetricFrame` producer.
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

- **Live surfaces — capabilities and runtime guidance.**
  `python -m marivo help` verifies the selected environment and hands off to
  Python. `marivo.help()` is a short global index; the exact surface names
  `marivo.help("datasource")`, `marivo.help("semantic")`,
  `marivo.help("analysis")`, and optional `marivo.help("ontology")` open their
  native bounded indexes. The analysis root includes one guarded first-observation
  path: acquire the session, select and inspect a current metric entry with
  `marivo.help(entry)`, inspect scoped readiness, then call `session.observe(...)`
  and read the resulting frame. It does not render the registry's complete type
  algebra. Registered
  namespace topics such as `analysis.events` and `analysis.lifecycle` list
  their real capability members. Focused
  `marivo.help("analysis.<target>")` (for example `analysis.observe`,
  `analysis.compare`, or `analysis.recover`) owns signatures, artifact
  families, constraints, return types, errors, and runnable examples. Frames
  and results own dynamic guidance:
  `show()` describes an artifact's current state and only state-dependent
  continuation hints; `contract()` describes the complete mechanically valid
  next actions from where it is now. Readable operation labels use registry-owned
  public entry points such as `session.compare(...)`; stable capability ids stay
  in the structured contract. Structured errors own repair guidance with typed
  `AnalysisRepair` instructions. Judgment stays with the agent.
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
catalog = session.catalog
dau = catalog.metrics.get("analytics.dau")

current = session.observe(
    metrics=dau,
    time_scope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
)
baseline = session.observe(
    metrics=dau,
    time_scope={"start": "2026-06-11", "end": "2026-06-18"},
    grain="day",
)
delta = session.compare(current, baseline, alignment=mv.window_bucket())
delta.show()  # bounded card; nothing printed unless asked
delta.contract().show()  # which operators this delta can feed
```

Which operator to reach for follows the artifact in hand: observe a metric first;
`compare` two observed frames for a change; `attribute` a delta over explicit axes;
`discover.<objective>` when the axis/window/slice worth examining is unknown;
`hypothesis_test` to check an explicit hypothesis; `forecast` to project observed
history; `assess_quality` to gate any of them. Concrete intent paths, composition
patterns, and report shape are the agent's responsibility; the `marivo-analysis`
skill owns boundaries and handoffs only. The mechanical next actions from any
given artifact come from its `contract()`.

When a Session has a ready optional ontology binding, a compatible arity-one
MetricFrame or same-Metric DeltaFrame contract also exposes
`discover.semantic_hypotheses`. That continuation returns unscored candidates
derived from one explicit ontology edge plus semantic-catalog resolution. The
agent must inspect the persisted context, select a stable `item_id`, and explicitly
observe the resulting `OntologyMetricCandidate`; Marivo never auto-executes or
promotes it to a causal fact.

### Catalog-backed semantic inputs

For a top-level runtime parameter that already consumes a catalog-backed
`Ref[K]` and has one authoritative current catalog, the annotation-level
contract is:

```python
SemanticInput[K] = Ref[K] | CatalogEntry[K]
```

`SemanticInput` is not a public constructor, export, or help topic. The runtime
accepts only exact refs and registered concrete entry classes owned by the
current compiled catalog. It validates ownership, exact kind, and current
membership, then extracts the canonical ref immediately. Bare strings,
wrong-kind refs/entries, cross-catalog or stale entries, arbitrary subclasses,
and duck-typed `.ref` objects fail before backend work. A mechanically unique
stale same-path reacquisition may produce a retry at the semantic catalog
boundary. An analysis boundary exposes it as inspection unless it can render
the complete public analysis call; partial reacquisition snippets must not be
labelled as retries. Other failures require inspection or semantic authoring
without selecting a replacement.

The frozen analysis consumer matrix is:

| Public boundary | Catalog-backed parameters |
| --- | --- |
| `Session.observe` | catalog metric root(s), `dimensions`, `slice_by` keys, `time_dimension` |
| `Session.attribute` | `axes` |
| `SessionEvents.funnel` | subject `axes` |
| `SessionLifecycle.replay` | `model` |
| `SessionLifecycle.distribution` | subject `axes` |
| `SessionDiscoverNamespace.driver_axes` | `search_space` |
| `SessionDiscoverNamespace.interesting_slices` | `search_space` |
| `SessionDiscoverNamespace.cross_sectional_outliers` | `peer_scope` |
| `frame.transform.slice` | `slice_by` keys |
| `frame.transform.rollup` | `drop_axes` |

The semantic catalog applies the same entry/ref boundary to `verify`, `preview`,
`preview_many`, and catalog-leaf readiness inputs; its strict
`catalog.require(ref)` lookup remains ref-only. After normalization, planners,
executors, job parameters, artifact metadata, evidence, replay, and recovery
contain refs or existing runtime-expression payloads only. An entry call and
its ref twin have equivalent value identity, lineage, persistence, evidence,
and replay behavior.

This widening does not apply to semantic authoring or datasource APIs, runtime
metric constructor leaves, nested Event values such as `PatternStep`,
participant roles or completeness declarations, persisted selector/replay
DTOs, or bare semantic strings.

### Typed Event composition

`SessionEvents.match(...)` materializes the canonical dense
`EventFrame[journey]`. Phase 2 adds only closed reducers and the governed
SubjectSet bridge:

```text
EventFrame[journey, first_per_subject] -> events.funnel -> EventFrame[funnel]
EventFrame[journey] -> events.time_to_event -> EventFrame[time_to_event]
EventFrame[journey, first_per_subject] -> select_subjects -> SubjectSet
SubjectSet[ready] -> observe(cohort=...) | events.match(cohort=...)
```

Reducers consume the persisted journey assignment rather than rematching Event
inputs. Funnel without axes and time-to-event perform no datasource query.
Funnel axes are exact current Dimension entries/refs and may only enrich the
journey subject through one unique directed to-one path at cohort entry.
Grouped additive counts must reconcile exactly to the ungrouped funnel.

Phase 4 adds one exact comparison and attribution continuation:

```text
EventFrame[funnel] x EventFrame[funnel] -> compare -> DeltaFrame[funnel]
DeltaFrame[funnel, ungrouped] -> attribute(target=funnel_loss_rate) ->
    AttributionFrame[funnel_loss_rate]
```

Funnel comparison accepts no caller alignment. It full-outer-aligns the
persisted PatternStep identity plus the declared axis tuple, zero-fills only
additive counts for one-sided tuples, and keeps absent-side or zero-denominator
rates null. Pattern, matching, follow-up, subject, catalog fingerprint, and
axis contracts must match exactly; coverage-censored aligned populations fail
with `event_coverage_unknown`, while structural drift fails with
`funnel_comparison_mismatch`.

`DeltaFrame[funnel]` rows contain declared axes followed by `step_key`, paired
current/baseline cohort, resolved cohort, entry, resolved entry, reached, lost,
and coverage-censored counts, then paired loss rates and their delta.

`mv.funnel_loss_rate(step=...)` retains one exact non-initial PatternStep.
Attribution is allowed only from an ungrouped funnel delta and re-aggregates
the two persisted journey assignments over governed cohort-entry subject axes;
it never rematches Events. For each group `g`, with lost counts `l`, resolved
entry counts `e`, and side totals `E`:

```text
loss(g)            = (l_current(g) - l_baseline(g)) / E_current
denominator_mix(g) = l_baseline(g) * (1/E_current - 1/E_baseline)
```

The two component families sum exactly to the target loss-rate delta within
`1e-9`. Rows expose `contribution_kind`, signed `contribution`, and shares of
the total, positive pool, and negative pool. Single-axis and joint layouts keep
concrete Dimension columns; hierarchy uses `attribution_level`,
`attribution_axis`, `attribution_driver`, and `attribution_path`. Hierarchy
pool shares are normalized independently within each visible
level because prefix aggregation can cancel signs; metadata retains the
deepest joint-partition pools used by the exact reconciliation. This is
arithmetic attribution with `causal_claim="none"`.
Unsupported targets, grouped inputs, invalid modes, or zero denominators fail
with `funnel_attribution_unsupported`.

`DeltaFrame[funnel]` is a closed artifact family. Its meta exposes only funnel
fields and never projects Metric Delta facets; generic consumers dispatch on
the `DeltaFrameMeta | FunnelDeltaFrameMeta` closed union. Metric-only
continuations (`components()`, `transform.*`) fail closed with
`semantic_kind_mismatch` rather than reading absent metric fields as optional
`None` facets.

Generic metric attribution uses the same public `session.attribute(...)`
entrypoint. `DeltaFrame.contract().attribute_admission` is the sole mechanical
method/mode admission state. New generic artifacts persist typed axes, mode,
`causal_claim="none"`, and discriminated method evidence; semantics are never
inferred from free-form params. Graph-owned non-additive bases replay an
independent unsegmented endpoint. Their multi-axis layout is either `joint` or
`hierarchy`. Typed resolution evidence distinguishes rollup-safe additive
prefixes from independent non-additive prefixes, where every ordered
semantic-ref prefix is a separately recomputed and reconciled game. Native
`top_k` selection happens before attribution and represents the remainder as a
masked Other player rather than a result residual.

`mv.dropped_before(step=...)` is the only Phase 2 SubjectSelection. It accepts
one exact non-initial PatternStep from a first-per-subject journey, selects only
resolved loss, and excludes coverage-censored truth. A SubjectSet persists only
ordered identity tuples as artifact rows; identity values never enter metadata,
jobs, evidence, cards, or errors. A ready same-session SubjectSet may scope
`observe` and `events.match`; a coverage-censored SubjectSet remains readable
but fails admission with `event_coverage_unknown`.

### Replay-based Lifecycle composition

A `StateModel` is the normative semantic contract; replay window, seed,
completeness, cohort, and violation observations belong to analysis. Phase 3
adds one replay materializer and four reducers:

```text
StateModel + explicit inception seed -> lifecycle.replay -> LifecycleFrame[history]
LifecycleFrame[history] -> lifecycle.distribution -> LifecycleFrame[distribution]
LifecycleFrame[history] -> lifecycle.transitions -> LifecycleFrame[transitions]
LifecycleFrame[history] -> lifecycle.dwell -> LifecycleFrame[dwell]
LifecycleFrame[history] -> lifecycle.violations -> LifecycleFrame[violations]
LifecycleFrame[history] + in_state -> select_subjects -> SubjectSet
```

`session.lifecycle.replay(...)` accepts one exact current StateModel entry/ref,
a timezone-aware half-open `TimeScope`, and the required exact
`mv.from_inception()` seed. Optional completeness declarations may cover only
Events consumed by that StateModel; an optional ready SubjectSet must have the
same subject Entity and identity signature. Each distinct trigger Event is
queried at most once. Event predicate, participant, ordering, watermark, and
declaration semantics reuse the Event core.

Before choosing a replay window, callers may inspect
`session.events.occurrence_bounds(event_or_model)`, where `event_or_model` is
one exact Event or StateModel entry/ref. A StateModel supplies its own distinct
inception and transition Events; Marivo evaluates those exact Event predicates
and returns one bounded `EventOccurrenceBounds` with UTC-normalized
earliest/latest occurrences. A StateModel with no Event triggers returns an
empty `event_refs` tuple with both bounds absent. The operation never collapses
data at Datasource scope. Its result is observed data range only: it does not
establish a completeness watermark, replace the operation's coverage
resolution, or turn fixture generation metadata into runtime evidence.

Replay evaluates modeled occurrences before the requested window end, then
emits only clipped intervals that overlap the window. Legal triggers change
state. A modeled trigger that is illegal in the current state leaves the state
unchanged and enters the persisted violation trace. Events outside the
StateModel are neither queried nor violations. Same-time cross-Event order is
rejected only when it changes the resulting state or violation outcome.

`LifecycleFrame[history]` contains ordered, non-overlapping intervals with
`completed | right_censored | coverage_censored` status. Reducers consume the
committed history and private violation trace without rematching Events or
replaying transitions:

- `distribution` is dense over requested instants and declared states; governed
  subject axes resolve at each instant and grouped counts must reconcile.
- `transitions` emits every distinct modeled state pair, including zero counts.
- `dwell` emits every declared state and computes duration statistics only from
  completed clipped intervals, while retaining explicit censor counts.
- `violations` exposes a typed copy of the committed replay trace; it does not
  relabel observations as policy breaches, quality failures, or causal facts.

`mv.in_state(...)` accepts an exact `ModelStateHandle`, not a bare state string.
`session.select_subjects(...)` selects subjects whose state is established at
the requested instant. Unknown or coverage-censored truth is excluded and
retained as censoring metadata. Only a ready resulting SubjectSet can scope
later `observe`, `events.match`, or `lifecycle.replay` calls.

### Typed metric composition

`Session.observe(...)` is the only public initial `MetricFrame` materializer.
Its catalog roots are exact current `MetricEntry` values or exact
`Ref[metric]` values; it also accepts closed values built with
`mv.runtime_metric.aggregate`, `.weighted_mean`, `.slice`, `.ratio`, and `.linear`.
Generic refs, stale/cross-catalog entries, bare ids, frame arithmetic, and
generic formula nodes do not cross this boundary. A non-empty list or tuple
forms one ordered mixed forest with one outer scope.

The same ordered catalog/runtime roots may be passed to
`catalog.readiness(refs=[...])` before observation. Readiness lowers the forest,
checks its governed leaves without querying, and returns passing roots through
`analysis_ready_inputs`.

Catalog and runtime roots lower to the same canonical expression graph. Runtime
expressions may recursively contain other runtime expressions or catalog metric
refs. Branch-local slices are pushed to reachable leaves for value identity;
the outer `slice_by=` remains a distinct global scope. Every root is limited to
depth 10 and the submitted pre-CSE forest to 256 occurrences. One forest must
resolve within one semantic model and datasource compatibility domain. Missing
aligned keys are retained with null values rather than filled with zero.
After physical aggregation, composition evaluators normalize numeric child
values to `float64` before ratio or linear arithmetic while preserving typed
key columns. A non-numeric metric value fails with a structured evaluation
error rather than leaking a backend or pandas type error.

Runtime expressions are session-scoped analysis values, not catalog authority.
Every `mv.runtime_metric.*` constructor requires a non-empty label, including
constructors used as nested nodes. The label becomes the stable public
value-column handle when that expression is materialized as an observed root,
but remains presentation metadata rather than catalog authority or value
identity. Persisted graph, dependency, source, key, quality, component, replay,
and comparable-semantics state—not catalog/runtime origin—controls downstream
admission. `compare` may therefore
compare a catalog frame and a runtime frame when their lowered value semantics
match, while retaining ordered current/baseline identities in the delta.
Runtime weighted means accept two governed same-entity measures, require an
additive weight, and lower to the same paired numerator/weight leaf as catalog
`ms.weighted_mean`; they do not require a precomputed weighted-sum measure.
Runtime linear expressions accept ordered metric refs or runtime expressions,
require at least two total terms, and expose only fixed `+1` add and `-1`
subtract coefficients. Known term units must be commensurable; literals,
arbitrary coefficients, unit overrides, callbacks, and formula strings remain
outside the runtime algebra. A linear result is additive only when every term
is additive; semi-additive and mixed inputs conservatively produce a
non-additive result because the runtime descriptor does not prove a shared
status-time fold contract.
Every observed root and mixed forest persists a recursive component graph;
`frame.components()` loads it for inspection. Every linear node in that graph,
including a linear expression nested below another operator, retains ordered
child ids and exact signed coefficients. `component_ref` remains the narrower
signal that the root also supports numerical decomposition.

## Non-goals

The analysis layer does not: dress arbitrary Ibis/SQL as a core operator; pass
generic pandas/sklearn wrappers off as canonical artifact producers; do causal
inference or what-if simulation; provide typed regression or a generic
statistical planner; auto-generate business conclusions; emit free text as its
primary output; map one BI chart template to one core operator; or admit
`RawSqlResult`/pandas values back into typed analysis.

## Document map

This overview is the entry point. The focused specs:

- [`operators-and-frames.md`](operators-and-frames.md) — the operator algebra:
  frame/result families, typed shapes and policies, the agent-facing core surface,
  per-operator detail, the result/read contract, the shape-aware DAG, and the
  terminal boundaries.
- [`session-state-and-runtime.md`](session-state-and-runtime.md) — the `Session`
  object, the project-local `.marivo/analysis/` layout, content-addressed identity,
  cold-start rehydration, cross-session ownership, and failure recovery.
- [`evidence-access-surface.md`](evidence-access-surface.md) — typed findings,
  bounded artifact digests, inference boundaries, session audit pages, the v4
  `judgment.db` ledger, and the agent-owned judgment boundary.
- [`evidence-compatibility-and-revalidation-design.md`](../../superpowers/specs/evidence-compatibility-and-revalidation-design.md)
  — implemented Slice 1 selection-wide Finding compatibility, Slice 2 Artifact
  identity/semantic/evidence revalidation, Slice 3 registry-owned operator
  admission, and Slice 4 adversarial persistence/recovery guarantees, including
  retry-time index withdrawal, validation-before-recovery publication, and
  stable Artifact pagination identity. All reuse the same private authority
  context and comparator; no public authority context or persistence schema was
  added.
- [`timezone-and-calendar-design.md`](timezone-and-calendar-design.md) — the two
  timezone axes (read tz and report tz), time-column classification, window/bucket
  computation, and calendar alignment.
- [`../temporal-semantics.md`](../temporal-semantics.md) — the proposed
  cross-layer period-calendar authority, temporal sets, work schedules,
  calendar-bound grains, named-period scopes, and explicit comparison alignment
  policies.
