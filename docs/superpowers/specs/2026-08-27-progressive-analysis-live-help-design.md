# Progressive Analysis Live Help Design

Date: 2026-08-27

Status: proposed

## Relationship To Existing Designs

This design extends the implemented analysis capability kernel in
[`2026-07-13-marivo-analysis-interface-surface-design.md`](2026-07-13-marivo-analysis-interface-surface-design.md).
It preserves that design's central boundary:

- Marivo owns installed capability facts, typed computation, mechanical
  admission, current artifact state, evidence identity, and structured repair;
- the agent owns question interpretation, method choice, judgment, synthesis,
  and stopping;
- the packaged `marivo-analysis` skill owns workflow boundaries, evidence
  continuity, handoffs, and closeout obligations, not API facts.

It also preserves the result protocol in
[`docs/specs/analysis/operators-and-frames.md`](../../specs/analysis/operators-and-frames.md),
the state and recovery contract in
[`docs/specs/analysis/session-state-and-runtime.md`](../../specs/analysis/session-state-and-runtime.md),
and the evidence contract in
[`docs/specs/analysis/evidence-access-surface.md`](../../specs/analysis/evidence-access-surface.md).

This design supersedes the following active disclosure claims in the 2026-07-13
interface design:

- the analysis root as a nearly complete capability index;
- the root-level first-observation recipe;
- the root-level static type algebra;
- the two-class `root` versus `focused` help budget;
- `root_group`, `root_visibility`, prefix inference, and renderer-side special
  cases as the discovery topology;
- `analysis.recovery` as a mixed session, Artifact, Evidence, and revalidation
  page.

The capability kernel, exact callable ids, artifact algebra, runtime family
gate, typed affordances, and structured repair model remain. This is a clean
navigation and disclosure cutover. It does not add compatibility aliases for
replaced navigation topics.

The design follows the context-isolation, registry ownership, route
completeness, and drift-validation principles in
[`2026-08-27-progressive-semantic-authoring-live-help-design.md`](2026-08-27-progressive-semantic-authoring-live-help-design.md),
but it does not copy the semantic object hierarchy. Analysis has a second,
runtime-dependent disclosure graph: the exact legal continuation depends on
the Artifact family, shape, authority, quality, and retained state.

## Summary

Redesign `marivo.help("analysis")` as two connected progressive-disclosure
planes:

1. a **static installed-contract plane** for cold discovery; and
2. a **dynamic object-near plane** for current Artifact state, legal
   continuations, Evidence, recovery, and terminal boundaries.

The static route is:

```text
marivo.help("analysis")
  -> marivo.help("analysis.entry")
  -> marivo.help("analysis.methods")
  -> marivo.help("analysis.inputs")
  -> marivo.help("analysis.artifacts")
  -> marivo.help("analysis.evidence")
  -> marivo.help("analysis.runtime")
  -> marivo.help("analysis.boundary.to_pandas")
```

The six decision hubs route to bounded family pages, exact callable contracts,
or public type contracts. The one terminal boundary is linked directly because
there is no second boundary choice. No root or hub expands signatures,
examples, all operators, all builders, all types, or all errors.

The dynamic route is:

```text
repr(artifact)
  -> artifact.show()
  -> artifact.contract().show()
  -> marivo.help("analysis.<exact-target>")
  -> selected Evidence or recovery read
  -> frame.to_pandas() only for an intentional terminal exit
```

The two planes meet through canonical `LiveHelpTarget` values. Every Artifact
affordance, boundary port, semantic handoff, Evidence read, and structured
repair points to a target that resolves through the static plane. Static help
never guesses the current legal action; dynamic contracts never copy callable
signatures or parameter manuals.

An agent should be able to answer all of these without source inspection:

- how governed semantic inputs enter typed analysis;
- which computation families exist and what Artifact family each produces;
- what one Artifact family means and which operations can mechanically consume
  it in principle;
- how to construct a required policy, scope, Event pattern, selection, or
  runtime metric expression;
- how to inspect current state and exact current continuations;
- how to recover sessions, jobs, Artifacts, Findings, and derivation traces;
- when compatibility, revalidation, or quality assessment answers the needed
  question;
- what is lost when typed continuity is crossed.

The design does not turn those facts into a recommended investigation plan.

## Current-State Assessment

The installed 2026-08-27 analysis surface has strong exact leaves but an
incomplete progressive route.

Measured in the current checkout:

- the capability registry contains 140 descriptors;
- the public type resolver contains 65 types;
- the error resolver contains 101 structured error types;
- `marivo.help("analysis")` is 75 lines, 4,976 codepoints, and advertises 35
  outgoing routes;
- the longest rendered analysis callable leaves are `events.match` at 120
  lines, `observe` at 118 lines, and `compare` at 118 lines;
- the current analysis discovery projection exposes 35 of the 140 exact or
  grouping descriptors.

| Requirement | Current state | Conclusion |
|---|---|---|
| Exact callable contracts | Public operators, constructors, reads, recovery methods, and the terminal boundary broadly resolve through exact leaves | Broadly complete |
| Cold-start orientation | The root includes imports, one metric-first recipe, and 35 mixed capability routes | Too dense and biased toward one entry family |
| Method discovery | Core operators are grouped, but the root mixes entry producers, reducers, method families, and individual builders | Partially complete |
| Supporting-input discovery | Some builders are direct root entries; others such as `dropped_before`, `from_inception`, `in_state`, source bindings, and closed option values require prior name knowledge | Incomplete |
| Artifact-family discovery | Public type help exists, but there is no bounded family index and producer/consumer rendering is not the complete static Artifact algebra | Incomplete |
| Evidence discovery | Evidence reads and compatibility are mixed into `analysis.session` and `analysis.recovery`; there is no `analysis.evidence` decision page | Incomplete |
| Runtime and recovery discovery | Session, job, frame, Evidence, and revalidation reads appear in one long recovery member list | Incomplete |
| Dynamic continuation | `show()` and `contract()` already separate current state from complete mechanical continuations | Strong base |
| Route ownership | Grouping relies on dotted-id prefix inference and `_EXPLICIT_GROUPING_MEMBER_TARGETS`; non-callable topics are encoded as constructor descriptors | Drift-prone |
| Context budgets | One 96-line root limit and one 120-line focused limit cover semantically different pages; outgoing routes and examples are unbudgeted | Incomplete |
| Implementation alignment | Signatures are reflected and accepted families drive runtime admission, but navigation membership, related links, public type algebra, and renderer special cases can drift independently | Partially complete |

Therefore analysis help completeness has four independent parts:

1. **leaf completeness** — every public callable, intentional value contract,
   public type, and structured error has one exact canonical target;
2. **route completeness** — every leaf needed for ordinary analysis is
   reachable from bounded hubs without knowing its Python name;
3. **continuation completeness** — every mechanically legal current Artifact
   action appears in its complete `contract()` and resolves to exact help; and
4. **recovery completeness** — persisted state and Evidence can be found,
   checked, and resumed through bounded public reads without guessing storage
   layout or private APIs.

One part cannot substitute for another. A resolvable string is not necessarily
discoverable; a static family edge is not current admission; a contract
affordance is not semantic or Evidence revalidation; and successful recovery
does not prove current source freshness.

## Problem

### The root combines orientation, recipe, inventory, and teaching order

The current root spends context on an inline metric-first recipe and then lists
individual builders, entry producers, core methods, family pages, recovery, and
the terminal exit. This makes a single page answer several different decisions:

- how do I enter typed analysis;
- what method families exist;
- how do I build one parameter;
- what can I do with an Artifact;
- how do I recover old work;
- how do I cross the typed boundary.

The recipe is valid for a governed Metric but is not the entry contract for an
Event journey or a StateModel replay. Expanding it to include all three would
make the root longer and more procedural. The root should route to an entry
page instead.

### Root categories do not match the agent's decision points

`policies_builders` currently mixes Grain, Event pattern construction,
completeness, alignment, sampling, runtime metric expressions, attribution
mode, transform option types, and Session source bindings. Those values have
different owners and consumers. An agent looking for a Lifecycle seed should
not scan temporal alignment or normalization modes.

Similarly, `typed_analysis` mixes Event reducers, Lifecycle reducers,
SubjectSet selection, comparison, attribution, association, testing, forecast,
quality, and candidate discovery. The grouping is accurate at a broad type
level but too coarse for bounded intent selection.

### Static Artifact algebra and current admission are conflated

The registry can derive which Artifact families a capability accepts, but the
current type pages do not consistently expose the full producer and consumer
algebra. Conversely, a complete static algebra cannot say whether one concrete
Artifact is currently admissible because that depends on facts such as:

- exact family and closed semantic shape;
- arity and metric identity;
- matching kind and Event coverage;
- current semantic authority policy;
- cumulative anchor and temporal suitability;
- quality or Evidence blockers;
- retained axes, selections, and candidate state.

Static family pages must show possibilities. `artifact.contract()` must show
the current mechanically valid set. Neither may impersonate the other.

### Evidence is not recovery

Evidence digests, Findings, derivation traces, selection compatibility, and
Artifact revalidation currently appear under session/recovery membership. They
are session-addressed reads, but their analytical purpose is distinct:

- recovery finds persisted work;
- Evidence reads expose persisted factual projections;
- compatibility checks whether a selected Finding set can be mechanically
  synthesized;
- revalidation checks Artifact identity, semantic authority, and Evidence
  integrity;
- quality assessment evaluates supported Artifact quality predicates;
- none of these proves datasource freshness.

Putting all of them on one recovery page hides these proof boundaries.

### Prefix-derived grouping is not a durable topology

The current registry discovers groups through dotted-id prefixes plus explicit
special cases for alignment, sampling, recovery, BaseFrame reads, and other
families. This creates several risks:

- renaming an id can silently move or orphan a route;
- a new builder can resolve exactly but remain undiscoverable;
- a renderer special case can disagree with registry discovery;
- one exact target can appear under multiple inferred groups;
- a non-callable navigation topic is represented as a constructor with an
  empty output type.

Navigation membership and cross-links must be explicit typed registry facts.

### One focused budget hides semantically different costs

A 120-line callable leaf, a 49-line public type page, a 27-line recovery page,
and a 10-line navigation topic currently share one focused budget. Line and
codepoint limits alone also allow a short page to expose too many routes or
examples.

Analysis needs separate budgets for roots, decision hubs, navigation pages,
exact callables, public types, current briefings, and dynamic contracts.

### Broad related-target generation spends context without owning intent

Current related links may be derived from shared input families, output
families, dotted siblings, and renderer-specific rules. Two operators that
accept `MetricFrame` are not automatically useful neighbors for the current
decision. Related links must come from explicit registry-owned edges and stay
bounded. Dynamic current alternatives belong in `contract()`, not in a generic
static `Related` dump.

## Goals

- Keep `marivo.help(...)` as the only public static-help coordinator.
- Make the analysis root a compact orientation page with no inline operator
  inventory or runnable workflow.
- Let a cold agent discover all governed entry families without assuming a
  Metric-first analysis.
- Route computation by the deterministic fact or Artifact transformation it
  performs, not by a recommended user-question classification.
- Let every supporting policy, builder, selection, and option value be found by
  the parameter problem it solves.
- Make the complete static Artifact family algebra discoverable without
  executing an operation.
- Preserve `artifact.contract()` as the complete current mechanical
  continuation contract.
- Give Evidence, compatibility, revalidation, runtime recovery, and terminal
  boundaries independent bounded routes.
- Create a navigation page only for a genuine choice among at least two
  distinct next targets; route singleton capabilities directly to their exact
  leaf.
- Keep exact callable leaves self-contained for one minimal valid invocation.
- Derive callable shape from the installed implementation and all navigation
  membership from native analysis registry descriptors.
- Eliminate prefix inference, renderer-owned membership tuples, and fake
  constructor descriptors for navigation topics.
- Give every exact leaf one discovery owner; represent all other appearances as
  typed cross-links.
- Bound lines, codepoints, outgoing routes, examples, affordances, and repair
  branches independently.
- Preserve lexical unknown-target suggestions without turning them into method
  recommendations.
- Make registry reachability, rendered reachability, dynamic affordances, and
  runtime admission independently testable.

## Non-Goals

- No `mv.help`, `mv.describe`, public capability registry, JSON help format, or
  public `format=` argument.
- No natural-language question router, operator recommender, planner, analysis
  DAG generator, or automatic stop decision.
- No prescribed operator sequence, universal quality gate, or report template.
- No change to operator algorithms, Artifact schemas, Evidence extraction,
  persistence, lineage, quality rules, or semantic meaning.
- No automatic project load, catalog search, readiness check, query, recovery,
  revalidation, or datasource access from static help.
- No recursive “show all” help page.
- No duplicated signatures, parameter tables, Artifact admission matrices, or
  error catalogs in navigation pages, site guides, or the packaged skill.
- No inference that static family compatibility means current Artifact
  admission.
- No inference that revalidation or compatibility proves datasource freshness,
  causality, or business correctness.
- No inbound path from pandas, SQL, Ibis, or another terminal result into typed
  analysis.
- No public lifecycle or workflow state machine for an analysis investigation.
- No compatibility aliases for replaced navigation topics.
- No change to historical versioned documentation or historical design files.

## Disclosure Model

### Two connected planes

The static plane answers questions about the installed contract:

```text
surface -> decision hub -> family page -> exact callable or public type
```

The dynamic plane answers questions about one current object:

```text
repr -> show -> contract -> exact help -> Evidence/recovery or terminal exit
```

Static pages may describe family-level producers and consumers. They never
evaluate current preconditions. Dynamic contracts may evaluate current
preconditions. They never copy static signatures, examples, or long-form
constraints.

Every transition between the planes carries one canonical `LiveHelpTarget`.
There is no string reconstruction based on class names, receiver names, or
display labels.

### Context-budget policy

The initial static Help budgets are:

| Render class | Used for | Max lines | Max codepoints | Max outgoing routes | Max examples/snippets |
|---|---|---:|---:|---:|---:|
| Root | Global and `analysis` roots | 32 | 3,000 | 8 | 0 |
| Decision hub | `entry`, `methods`, `inputs`, `artifacts`, `evidence`, `runtime` | 44 | 4,500 | 10 | 0 |
| Navigation | Method, input, Evidence, runtime, and Artifact-family indexes | 64 | 6,500 | 16 | 0 |
| Exact callable | One operator, constructor, read, recovery method, or boundary | 104 | 9,000 | 10 | 1 |
| Public type | One Artifact, result, policy, selection, or Session type | 72 | 7,000 | 10 | 0 |
| Current briefing | Analysis structured-error instance | 72 | 7,000 | 6 | 1 |

These limits are calibrated against the installed surface. The current root
must shrink from 75 to at most 32 lines. Current complex leaves at 118–120 lines
must remove duplicated guidance and route non-invocation-critical background
to a narrower type, input, or method-family page. The callable leaf remains
self-contained for invocation-critical facts.

`CatalogEntry` and `Ref` briefings remain semantic-surface current briefings and
use the semantic design's independent budget. Analysis contributes only the
registry-owned handoff edge for a qualifying current entry; it neither renders
nor rebudgets semantic object identity.

The global `marivo.help()` page is a concept and routing boundary. It advertises
only `marivo.help("authoring")` and `marivo.help("analysis")`; it does not flatten
native discovery projections. Exact secondary targets remain resolvable and are
discovered from the bounded route that owns them.

Dynamic reads keep their independent contracts:

| Dynamic render | Contract |
|---|---|
| `repr(value)` | One bounded identity line pointing to `.show()` |
| `artifact.show()` | Current bounded card and state-dependent hints only; existing default row/byte bounds remain |
| `artifact.contract().show()` | Complete mechanical continuations, grouped deterministically; at most 120 lines, 12,000 codepoints, 24 affordances, and no examples |
| Evidence digest | Existing bounded item, boundary, and omission counts |
| Session/Evidence pages | Existing typed keyset pagination and explicit cursors |

The affordance ceiling is a closed-surface invariant, not permission to
truncate. If a valid Artifact would expose more than 24 current affordances,
the registry topology or readable grouping must be redesigned before shipping.
The structured contract remains complete. Failed preconditions remain visible
only with a complete typed repair, as in the existing Artifact contract.

Budget overflow is always a contract failure. Renderers do not silently remove
signatures, constraints, preconditions, repairs, examples, Artifact actions, or
Evidence omissions.

### Level 0: Analysis root

`marivo.help("analysis")` owns only the installed environment identity, the
analysis boundary, six decision hubs, and one exact terminal route:

```text
marivo.analysis
<installed Marivo version, resolved Python executable, and package path>

Resolve governed semantic inputs and enter typed analysis:
  marivo.help("analysis.entry")

Installed computation families:
  marivo.help("analysis.methods")

Construct inputs and policies:
  marivo.help("analysis.inputs")

Understand Artifact families and reads:
  marivo.help("analysis.artifacts")

Read and validate Evidence:
  marivo.help("analysis.evidence")

Resume persisted work:
  marivo.help("analysis.runtime")

Inspect typed-flow exits:
  marivo.help("analysis.boundary.to_pandas")
```

The root states once that Marivo computes typed facts while the agent chooses
the method and interpretation. It contains no first-observation code, operator
names, builder names, Artifact family list, recovery member list, or type
algebra. The full environment fingerprint appears only on the root and explicit
environment-mismatch diagnostics; focused pages carry only the installed
version in compact form.

### Level 1A: Governed entry hub

`analysis.entry` maps governed input kinds to canonical entry producers. This
is mechanical type routing, not a recommended analysis sequence:

| Governed input | Entry route | First Artifact family |
|---|---|---|
| Metric entry/ref or closed runtime metric expression | `analysis.observe` | `MetricFrame` |
| Event refs plus a typed `EventPattern` | `analysis.events.match` | `EventFrame[journey]` |
| StateModel entry/ref plus an explicit inception seed | `analysis.lifecycle.replay` | `LifecycleFrame[history]` |

The page also routes identity and readiness questions to:

- `analysis.catalog` for the Session's current typed semantic catalog;
- `analysis.catalog.readiness` for the exact requested closure;
- `semantic.authoring` when reusable meaning is missing or blocked.

Semantic object access is explicit and does not treat identity as current
authority:

| Supplied object | Static/current Help behavior | Analysis access contract |
|---|---|---|
| Current `CatalogEntry[K]` from `session.catalog` | `marivo.help(entry)` may add the registry-owned kind-level analysis handoff | The focused operator leaf decides whether the current entry or `entry.ref` is accepted and still owns readiness and companion-input requirements |
| Exact `Ref[K]` | `marivo.help(ref)` shows typed identity and exact current-catalog `catalog.require(ref)` only; it exposes no qualifying analysis route | An operator may accept the Ref, but its Session must resolve exact kind, current membership, and readiness before backend work |
| Unknown semantic identity | `analysis.catalog` routes bounded collection browse; exact known identity uses `catalog.require(ref)` without browsing | No bare string, guessed path, cross-catalog entry, or duck-typed `.ref` object crosses the boundary |
| Closed runtime metric expression | The exact runtime-metric type or constructor contract owns its shape | It enters only through `analysis.observe`; it is not a catalog entry and does not receive semantic-object instance help |

This preserves both supported operator input forms for qualifying
catalog-backed parameters without letting a bare Ref claim project membership
or mechanically qualified handoff. Static Help never loads a catalog to enrich
the Ref.

Event source observations are not Event computation methods. When matching or
Lifecycle replay needs bounded facts about observed occurrences, the entry hub
routes to `analysis.entry.event_observations`, which owns exactly:

- `analysis.events.watermark` for an authoritative observed completeness bound;
- `analysis.events.occurrence_bounds` for observed occurrence bounds.

These reads may consult the Session's configured datasource runtime. They
produce bounded immutable facts, not Artifacts; they do not match journeys,
prove semantic readiness, or replace an explicit completeness policy when no
authoritative watermark exists.

The page states:

```text
catalog selection != readiness
readiness != current source health
entry execution != analytical conclusion
```

It contains no runnable three-path workflow and does not call any operation.
The three entry leaves keep their discovery ownership in the computation owners
shown below; `analysis.entry` holds typed cross-links.

### Level 1B: Method-family hub

`analysis.methods` routes by the deterministic computation performed. The
wording describes a result contract, not the user's likely next step.

| Computation family | Canonical route | Members or leaf contract |
|---|---|---|
| Materialize governed metric values | `analysis.observe` | exact callable; no singleton family page |
| Align change and reconcile contributions | `analysis.methods.change` | `compare`, `attribute` |
| Produce bounded candidate sets | `analysis.discover` | all registered `discover.<objective>` leaves |
| Measure association or evaluate an explicit paired hypothesis | `analysis.methods.relationship_testing` | `correlate`, `hypothesis_test` |
| Project future buckets from observed history | `analysis.forecast` | exact callable; no singleton family page |
| Read committed construction-quality predicates | `analysis.methods.quality` | read-only Frame method; no singleton family page |
| Match and reduce governed Event journeys | `analysis.events` | `events.match`, `events.funnel`, `events.time_to_event` |
| Replay and reduce governed StateModel history | `analysis.lifecycle` | `lifecycle.replay`, `lifecycle.distribution`, `lifecycle.transitions`, `lifecycle.dwell`, `lifecycle.violations` |
| Materialize exact typed subject cohorts | `analysis.select_subjects` | exact callable; no singleton family page |
| Reshape a MetricFrame or DeltaFrame without changing its family | `analysis.transform` | all registered `transform.<operation>` leaves |

Each multi-member method-family page owns:

- a bounded statement of what is computed;
- the admitted input family or families in principle;
- the fixed output family or same-as-input rule;
- the epistemic class of the output: observed, algebraic, candidate,
  association, statistical decision, projection, or quality evaluation;
- exact member routes;
- cross-links to required input families and produced Artifact family pages.

It does not list signatures, parameter tables, examples, current
preconditions, preferred methods, or stop conditions.

`discover`, `events`, `lifecycle`, and `transform` retain their canonical topic
ids. Their membership becomes explicit registry data rather than dotted-prefix
expansion. `events.watermark` and `events.occurrence_bounds` are not members of
`events`; their discovery owner is `entry.event_observations`.

### Level 1C: Input-family hub

`analysis.inputs` routes by the value the caller needs to construct or select:

| Input need | Canonical route | Members or leaf contract |
|---|---|---|
| Select exact current reusable semantics | `analysis.catalog` | catalog collections, `catalog.require`, `catalog.readiness` |
| Construct time scope, grain, governed period, or execution scope | `analysis.inputs.scope` | `grain`, `time_scope`, `AbsoluteWindow`, `calendar.period`, `Session.source_bindings` |
| Align two temporal Artifacts | `analysis.alignment` | registered alignment-policy constructors |
| Choose an explicit hypothesis sampling policy | `analysis.SamplingPolicy` | exact public-type contract; no singleton family page |
| Compose a question-scoped runtime metric | `analysis.runtime_metric` | registered runtime metric constructors |
| Build an Event pattern, matching policy, completeness declaration, or funnel target | `analysis.inputs.events` | `step`, `sequence`, `first_per_subject`, `every_start`, `declared_complete_through`, `funnel_loss_rate` |
| Select subjects or seed Lifecycle replay | `analysis.inputs.subject_selection` | `dropped_before`, `in_state`, `from_inception` |
| Fill closed method option values | `analysis.inputs.operator_options` | `AttributionMode`, `SemanticShape`, `PointAnomalyStrategy` |
| Fill closed transform option values | `analysis.inputs.transform_options` | `RankMethod`, `NormalizeKind`, `NormalizeBaseline` |

The family pages use “build when this parameter requires …” language. They do
not duplicate the exact constructor signature, allowed-value table, example,
or operator constraint.

Each supporting value has exactly one discovery owner. An operator leaf may
link to the value through parameter-specific help, but that link does not make
the operator a second discovery owner.

### Level 1D: Artifact-family hub

`analysis.artifacts` is the static Artifact family decision hub and the entry to
the read protocol. It stays within the decision-hub route budget by routing to
five bounded navigation pages:

| Artifact need | Canonical navigation target |
|---|---|
| Metric observation, change, and attribution | `analysis.artifacts.metric_change` |
| Event, Lifecycle, and subject-cohort Artifacts | `analysis.artifacts.event_lifecycle` |
| Candidate, association, test, and forecast results | `analysis.artifacts.discovery_inference` |
| Quality and bounded component/coverage projections | `analysis.artifacts.quality_projection` |
| Common read protocol | `analysis.artifacts.reading` |

The four family navigation pages collectively route to the existing canonical
public type target for each family; they do not introduce parallel
`artifacts.<family>` aliases.

| Public Artifact family | Bounded meaning | Discovery owner | Canonical type target |
|---|---|---|---|
| MetricFrame | Governed observed metric values with a closed semantic shape | `artifacts.metric_change` | `analysis.MetricFrame` |
| EventFrame | Persisted Event journey, funnel, or time-to-event facts | `artifacts.event_lifecycle` | `analysis.EventFrame` |
| LifecycleFrame | Persisted StateModel history or one closed reducer shape | `artifacts.event_lifecycle` | `analysis.LifecycleFrame` |
| SubjectSet | Exact persisted subject identities for a typed cohort handoff | `artifacts.event_lifecycle` | `analysis.SubjectSet` |
| DeltaFrame | Aligned metric or funnel change facts | `artifacts.metric_change` | `analysis.DeltaFrame` |
| AttributionFrame | Reconciled arithmetic contribution facts | `artifacts.metric_change` | `analysis.AttributionFrame` |
| ForecastFrame | Projected future metric buckets | `artifacts.discovery_inference` | `analysis.ForecastFrame` |
| QualityReport | Fixed quality evaluations over one supported Artifact | `artifacts.quality_projection` | `analysis.QualityReport` |
| CandidateSet | Bounded candidates for one closed discovery objective | `artifacts.discovery_inference` | `analysis.CandidateSet` |
| AssociationResult | Estimated association facts between observed metrics | `artifacts.discovery_inference` | `analysis.AssociationResult` |
| HypothesisTestResult | Statistical decision under one declared paired test | `artifacts.discovery_inference` | `analysis.HypothesisTestResult` |
| ComponentFrame | Bounded component projection from a supported parent | `artifacts.quality_projection` | `analysis.ComponentFrame` |
| CoverageFrame | Bounded coverage projection from a supported parent | `artifacts.quality_projection` | `analysis.CoverageFrame` |

`analysis.artifacts.reading` states the common ladder without expanding any
current object:

```text
repr -> show/render -> contract -> exact Evidence or rows -> terminal exit
```

It owns the two common exact read leaves `analysis.BaseFrame.show` and
`analysis.BaseFrame.contract`. Evidence reads and
`analysis.boundary.to_pandas` remain typed cross-links owned by their Evidence
route or the analysis root.

#### One Artifact-family type page

Each canonical Artifact type page owns:

- bounded family meaning and epistemic kind;
- closed semantic shapes or variants;
- producers derived from capability output contracts;
- consumers derived from capability accepted inputs and admission rules;
- family-specific public properties and methods;
- common inherited reads;
- typed Evidence availability;
- exact recovery and terminal-boundary routes;
- one warning that static consumers are possibilities, not current admission.

Producer and consumer edges are generated from the same capability facts used
by runtime family admission. They are not separately registered prose.

For example, `analysis.MetricFrame` may state that MetricFrame is produced by
`observe` and family-preserving transforms, and may be consumed in principle by
comparison, discovery, association, testing, forecast, quality, transforms,
and the terminal boundary. The concrete `frame.contract()` may expose a strict
subset because of shape, arity, authority, or retained-state preconditions.

Type pages do not render constructors for result types, private fields,
dataclass defaults, current rows, current quality, or a complete operator
manual.

### Level 1E: Evidence hub

`analysis.evidence` routes by the Evidence question:

| Question | Canonical route | What it establishes | What it does not establish |
|---|---|---|---|
| What bounded factual projection was committed with this Artifact? | `analysis.BaseFrame.show` | Current Artifact digest/status and typed issues | Cross-Artifact compatibility or freshness |
| Which persisted digests or Findings match bounded filters? | `analysis.evidence.browse` | Healthy-store bounded pages and exact omission counts | Absence when the store is unavailable |
| What exact Finding, digest, or derivation trace is this id? | `analysis.evidence.exact` | Exact persisted Evidence identity and derivation | Business interpretation or causality |
| Can this exact Finding selection be mechanically synthesized? | `analysis.session.evidence.compatibility` | Pairwise selection compatibility under current semantic authority and Evidence rules | Artifact revalidation, source freshness, or business usefulness |
| Is this recovered Artifact's identity, semantic authority, and Evidence integrity current? | `analysis.session.revalidate` | Bounded Artifact revalidation status | Datasource freshness or operation-shaped recomputation |
| Does this supported Artifact satisfy its fixed construction predicates? | `analysis.methods.quality` | Read committed `frame.quality_report()` | Semantic authority, Evidence compatibility, or causal validity |

The `browse` and `exact` multi-member pages route to the existing exact leaves:

- `session.evidence.digests`, `session.evidence.findings`;
- `session.evidence.digest`, `session.evidence.finding`,
  `session.evidence.trace`.

Current-state inspection, compatibility, revalidation, and quality route
directly to their exact callable leaves. Their return-type links live on those
leaves, including `ArtifactDigest`, `Finding`, `EvidenceCompatibility`, and
`ArtifactRevalidation` where applicable; a one-callable navigation page is not
introduced merely to repeat one route.

The hub states the proof separation directly:

```text
quality != compatibility != revalidation != source freshness
```

Evidence routes never infer conclusions or recommend which Findings to combine.

### Level 1F: Runtime and recovery hub

`analysis.runtime` separates persisted runtime questions:

| Runtime need | Canonical route | Members or leaf contract |
|---|---|---|
| Create, locate, inspect, resume, or delete a Session | `analysis.runtime.sessions` | `session.get_or_create`, `session.current`, `session.recent`, `session.inspect`, `session.resume`, `session.delete` |
| Find or recover exact Artifacts | `analysis.runtime.artifacts` | `session.frame_summaries`, `session.get_frame`; cross-link to revalidation |
| Inspect execution jobs | `analysis.runtime.jobs` | `session.jobs`, `session.recent_jobs`, `session.job` |
| Inspect the live Session contract | `analysis.Session` | public Session properties, namespaces, and reads |
| Read persisted Evidence | `analysis.evidence` | Evidence hub, not duplicated runtime members |

`analysis.runtime` explains identity selection:

- stable Session lookup uses `name`;
- immutable Session persistence identity uses `session.id`;
- Artifact recovery uses exact `frame.ref`;
- job inspection uses exact `job_id`;
- Evidence uses exact Artifact or Finding ids.

It does not render storage paths, select the latest result implicitly, load a
frame, mutate current Session state, or treat historical summaries as current
Evidence.

The old mixed `analysis.recovery` and `analysis.session` navigation topics are
removed in the cutover. Their exact public method targets remain unchanged and
move under explicit runtime or Evidence owners.

### Exact terminal-boundary route

The exact `analysis.boundary.to_pandas` leaf states the typed-flow boundary:

```text
typed Artifact
  -> bounded reads and typed continuations preserve Marivo continuity
  -> frame.to_pandas() returns a defensive copy and ends typed continuity
```

It owns the exact preserves and does-not-preserve facts from the boundary
descriptor. There is no governed entry from arbitrary pandas, SQL, or Ibis
output. Governed analysis starts from `analysis.entry`.

The old singular `analysis.boundary` navigation topic is removed, and no plural
singleton replacement is introduced. The exact
`analysis.boundary.to_pandas` leaf remains canonical.

### Level 2: Exact callable or value leaf

`marivo.help("analysis.<exact-target>")` remains the only static contract for
one public invocation or closed value contract.

An invokable leaf owns:

- canonical id and public receiver-qualified entrypoint;
- live reflected signature and concrete return annotation;
- accepted input families and precise output family;
- shape, authority, and policy admission facts needed before invocation;
- parameter-specific acquisition routes;
- effects: datasource access, persistence, mutation, and terminal boundaries;
- one minimal runnable example;
- stable structured error kinds and repair targets;
- bounded prerequisite and produced-Artifact links.

A closed value leaf owns its exact values or construction contract, producer,
consumers, and constraints. It does not pretend to be callable when it is a
literal family.

Reflection owns parameter names, order, kinds, annotations, defaults, and
return annotation. Registry descriptors own non-reflectable facts such as
accepted families, authority policy, Artifact shape admission, effects,
parameter acquisition, and constraint ids.

The exact leaf includes all invocation-critical facts. Optional background,
family inventories, sibling examples, and broad related-target lists stay on
their owning family pages or current site documentation.

### Level 3: Current object and structured-error disclosure

Analysis runtime objects follow closed behavior:

- `marivo.help(frame)` renders the static public type contract and points to
  `frame.show()` and `frame.contract()`; it does not read rows or evaluate
  current admission;
- `marivo.help(session)` renders the static `Session` contract; it does not
  inspect persisted state;
- `marivo.help(entry)` may render a bounded kind-level handoff because the
  entry carries current compiled catalog identity; the focused operator leaf
  remains authoritative for accepted input form and readiness;
- `marivo.help(ref)` renders typed identity and the exact
  `catalog.require(ref)` route only; it does not claim membership,
  readiness, or a qualifying analysis handoff;
- `marivo.help(error_instance)` renders the static error contract plus concrete
  expected, received, location, repair action, current candidates, executable
  retry when complete, and one exact next help target.

Instance enrichment cannot query a datasource, choose an operator, choose a
semantic replacement, expose private fields, or include memory addresses.

## Dynamic Artifact Disclosure

### `repr` is identity only

Every public Artifact and terminal result keeps a one-line bounded repr with
kind and stable identity, pointing to `.show()`. It contains no row preview,
method list, recommendation, or conclusion.

### `show()` is current state, not the capability matrix

An Artifact card owns bounded current facts:

- identity, family, shape, materialization, scope, and lineage;
- bounded data preview;
- quality and Evidence status;
- state-dependent continuation hints only when those facts materially affect
  admission;
- exact Evidence fallback and omission facts.

It does not repeat every compatible operator or builder. A hint such as current
funnel attribution admission may appear; the full continuation set remains in
`contract()`.

### `contract()` is complete current mechanical admission

`artifact.contract()` remains the one complete structured contract for current
mechanically legal next actions. It owns:

- Artifact schema and retained semantic inputs;
- typed issues;
- all visible typed affordances;
- current precondition pass/fail facts;
- complete typed repair or repair options for visible failures;
- expected output family;
- terminal boundary ports.

Readable rendering groups affordances by registry-owned method family and then
public entrypoint. Grouping changes presentation only; the structured
`affordances` tuple remains flat, deterministic, and complete.

An affordance points to one exact callable leaf. It never embeds that leaf's
signature, parameter table, example, or long constraint text. Affordances are
alternatives for agent judgment, not ranked or recommended next actions.

### Static and dynamic algebra must agree

For each Artifact family:

- the static type page's consumer set is the union of registered capabilities
  that accept that family in principle;
- a concrete `contract()` affordance set is a subset after current admission;
- every dynamic affordance belongs to the static consumer set;
- every omitted static consumer has a mechanically explainable family, shape,
  or current-state reason when evaluated for that Artifact;
- runtime execution passes through the same family and admission authorities.

No renderer owns a second compatibility matrix.

### Evidence and revalidation stay explicit

`contract()` describes committed mechanical compatibility. It does not prove
current semantic authority or Evidence integrity after recovery and does not
query source freshness. The agent follows `analysis.session.revalidate` when
current authority matters and `analysis.session.evidence.compatibility` before
combining Findings.

### Terminal exit is visibly separate

`boundary_ports` remain separate from typed `affordances`. The readable
contract labels `frame.to_pandas()` as terminal and carries its preserves and
does-not-preserve facts. A terminal result has no re-entry affordance.

## Registry Design

### Preserve the capability kernel and add native navigation descriptors

Exact runtime capabilities remain a closed kind-dispatched union:

```python
CapabilityDescriptor = (
    OperatorCapability
    | ConstructorCapability
    | ReadCapability
    | RecoveryCapability
    | BoundaryCapability
)
```

The Help registry becomes a closed union of exact capabilities and native
navigation/type contracts:

```python
AnalysisHelpDescriptor = (
    CapabilityDescriptor
    | AnalysisNavigationTopic
    | AnalysisMethodFamily
    | AnalysisArtifactFamilyContract
)
```

All variants satisfy the neutral resolver's minimal protocol:

```python
canonical_id: str
public_entrypoint: str | None
summary: str
callable_path: str | None
```

The neutral resolver stays unaware of analysis methods, Artifact families,
Evidence proof boundaries, runtime recovery, or render classes.

### Navigation topics are not constructors

```python
@dataclass(frozen=True)
class AnalysisNavigationTopic:
    canonical_id: str
    summary: str
    render_class: Literal["decision_hub", "navigation"]
    members: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = None
    callable_path: None = None
```

The initial decision hubs are `entry`, `methods`, `inputs`, `artifacts`,
`evidence`, and `runtime`. The root links directly to the exact terminal
boundary leaf.

Nested entry-observation, method, input, Artifact, Evidence, and runtime pages
are registered topics only when they present at least two distinct routes.
Family inventories own their member leaves; decision hubs may also carry typed
cross-links. All routes are explicit. The renderer never derives them from
canonical-id prefixes.
Registry construction rejects a navigation topic with fewer than two members;
a singleton is linked directly from its parent to the exact callable or type
contract.

### Method families own computation classification

```python
EpistemicKind = Literal[
    "observed",
    "algebraic",
    "candidate",
    "association",
    "statistical_decision",
    "projection",
    "quality_evaluation",
    "selection",
]


@dataclass(frozen=True)
class AnalysisMethodFamily:
    canonical_id: str
    summary: str
    epistemic_kinds: tuple[EpistemicKind, ...]
    members: tuple[LiveHelpTarget, ...]
    input_routes: tuple[LiveHelpTarget, ...]
    output_routes: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = None
    callable_path: None = None
```

This classifies deterministic computation only. It carries no question
keywords, usefulness rank, first/default operator, or stop rule. Method-family
construction also requires at least two members; a one-method computation is
owned directly by `analysis.methods`.

### Artifact family contracts derive algebra from capabilities

```python
@dataclass(frozen=True)
class AnalysisArtifactFamilyContract:
    canonical_id: str
    artifact_family: ArtifactFamily
    summary: str
    epistemic_kinds: tuple[EpistemicKind, ...]
    semantic_shapes: tuple[str, ...]
    type_name: str
    specialized_member_targets: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = None
    callable_path: None = None
```

The contract does not copy producer or consumer tuples. The registry derives:

- producers from `OperatorCapability.output_contract` and
  `SameAsInputFamily` rules;
- consumers from `accepted_inputs` and `artifact_admission`;
- inherited reads from the public object contracts;
- boundary ports from registered `BoundaryCapability` inputs.

Derived edges are materialized into immutable registry views and checked
against runtime admission. Family summaries, epistemic kinds, semantic shapes,
and specialized public members are non-reflectable native facts.

### Discovery membership and cross-links are different

Every exact capability or intentional public type has at most one discovery
owner. Examples:

- `compare` is owned by `methods.change`;
- `observe` is owned directly by `methods`;
- `events.match` is owned by `events`;
- `events.watermark` is owned by `entry.event_observations`;
- `from_inception` is owned by `inputs.subject_selection`;
- `session.evidence.compatibility` is owned by
  `evidence` directly;
- `SamplingPolicy` is owned by `inputs` directly;
- `boundary.to_pandas` is owned by the analysis root directly;
- `MetricFrame` is owned by `artifacts`.

Entry pages, Artifact consumer edges, parameter-help routes, Evidence proof
cross-links, and structured repairs are directed cross-links. They do not add
another membership.

Registry validation rejects duplicate discovery ownership, orphan ordinary
leaves, dead edges, and renderer-only membership.

### Exact parameter guidance binds to live parameters

`ParameterHelpContract` remains exact to one parameter and gains validation
that:

- the named parameter exists in the live signature;
- its required/optional state agrees with the live default;
- every acquisition target resolves;
- constructed value families agree with registered producers and consumers;
- any `derivable_from_current_artifact` claim has a behavioral witness.

Cross-parameter constraints remain constraint descriptors. They do not use an
empty parameter binding to avoid live signature checks.

### Remove root placement from exact capabilities

`root_group` and `root_visibility` are presentation facts and are removed from
`CapabilityBase`. Exact descriptors retain capability identity and runtime
contract only. Navigation descriptors own placement.

`_EXPLICIT_GROUPING_MEMBER_TARGETS`, prefix-based `grouping_topic_for`, and
renderer branches that invent member lists are removed. The registry exposes
immutable views for:

- analysis-root members;
- decision-hub members;
- family-page members;
- discovery ownership;
- cross-links;
- Artifact producer/consumer algebra;
- bounded target-index projection.

### Render-class budgets are analysis-owned

```python
AnalysisHelpRenderClass = Literal[
    "root",
    "decision_hub",
    "navigation",
    "exact_callable",
    "public_type",
    "current_briefing",
]


@dataclass(frozen=True)
class AnalysisHelpRenderBudget:
    max_lines: int
    max_codepoints: int
    max_outgoing_routes: int
    max_examples_or_snippets: int
```

The analysis registry owns one immutable budget per class. The neutral live
layer continues to provide generic enforcement primitives and suggestion
limits; it does not know analysis page semantics.

Dynamic Artifact-card, Artifact-contract, Evidence, and pagination budgets
remain owned by their native result models. They must not reuse a static Help
budget merely because both render text.

### Implementation-to-disclosure authority

| Fact | Single authority | Disclosure behavior |
|---|---|---|
| Public callable existence and identity | Installed exported Python object | Exact resolver binds the same object |
| Parameter names, order, kinds, annotations, defaults, and return annotation | Live callable signature and type hints | Reflected at render and validation time |
| Accepted input families and output Artifact family | Exact capability descriptor | Drives help, static algebra, dynamic affordances, and runtime family gate |
| Shape and authority admission | Exact capability descriptor plus runtime Artifact facts | Static page states possible admission; contract/runtime evaluate current admission |
| Method-family membership and epistemic class | Native method-family descriptor | Family renderer iterates it |
| Entry-observation, input, Evidence, and runtime membership | Native navigation descriptors | Renderer iterates explicit members |
| Direct singleton discovery, including the terminal boundary | Parent hub/root descriptor plus the exact capability or type descriptor | Parent renders the exact route without an intermediate topic |
| Artifact meaning, shapes, and specialized methods | Artifact family contract | Type page renders it |
| Artifact producers and consumers | Derived registry algebra | No hand-authored type-page matrix |
| Current Artifact state | Concrete persisted Artifact | `show()` and `contract()` only |
| Current Evidence and authority status | Evidence store, Artifact sidecar, current catalog | Explicit Evidence reads and revalidation only |
| Semantic `CatalogEntry` or `Ref` identity briefing | Semantic Help/object-briefing owner plus current catalog identity | Analysis supplies only a qualifying kind-level handoff edge for a current entry; a bare Ref receives none |
| Concrete failure and repair | Structured error instance | Current briefing only |
| Final text layout | Renderer | Presentation only; no topology or API facts |

Help is a derived installed view, not a second API specification. Site docs and
the packaged skill route to live topics rather than becoming parameter,
Artifact-algebra, or recovery inventories.

## Public Target Rules

- `analysis` is the canonical surface root.
- `analysis.entry`, `analysis.methods`, `analysis.inputs`,
  `analysis.artifacts`, `analysis.evidence`, and `analysis.runtime` are
  canonical non-callable decision hubs.
- Existing useful family ids `analysis.catalog`, `analysis.alignment`,
  `analysis.runtime_metric`, `analysis.discover`, `analysis.transform`,
  `analysis.events`, and `analysis.lifecycle` remain
  canonical navigation topics with explicit membership.
- New nested navigation ids use their owning hub, for example
  `analysis.entry.event_observations`, `analysis.methods.change`,
  `analysis.inputs.events`, `analysis.artifacts.metric_change`,
  `analysis.evidence.browse`, and `analysis.runtime.jobs`.
- Navigation descriptors require at least two distinct member routes, and
  method-family descriptors require at least two owned capabilities. A
  singleton route points directly to its exact callable or public type; no
  pass-through topic is registered.
- Exact callable ids such as `analysis.observe`, `analysis.compare`,
  `analysis.discover.point_anomalies`, `analysis.session.get_frame`, and
  `analysis.boundary.to_pandas` remain unchanged.
- Public Artifact types use their existing exact type targets such as
  `analysis.MetricFrame`; no `analysis.artifacts.metric_frame` alias is added.
- Receiver and namespace methods retain canonical ids based on public identity,
  not navigation placement.
- `analysis.recovery`, `analysis.session`, `analysis.sampling`, and
  `analysis.boundary` navigation topics are removed. Their exact public method
  or type leaves remain. `analysis.boundaries` is not introduced.
- Supporting types and errors remain exact resolvable leaves. They enter
  discovery only when an intentional family page advertises them.
- There is no global flat target inventory. Each bounded native route advertises
  the targets needed for its decision, while exact secondary leaves remain
  resolvable when already known or obtained from live state.
- Callable/type objects and canonical strings resolve to the same static
  contract where applicable.
- Unknown or ambiguous strings fail with bounded canonical lexical
  suggestions. Suggestions never depend on user question, Artifact score, or
  operation popularity.
- No compatibility aliases or surface-order fallbacks are introduced.

## Help, Runtime, Evidence, And Skill Ownership

### Static Help

Static Help owns installed API identity, navigation topology, Artifact family
algebra, and static constraints. It performs no project load, query,
construction, recovery, revalidation, or mutation.

### Semantic catalog

The Session catalog owns current reusable semantic entries and scoped
readiness. Analysis Help may route to it but does not copy semantic object
meaning, identity briefings, budgets, or authoring contracts. The semantic Help
owner renders `CatalogEntry` and `Ref` instances; the analysis registry supplies
only qualifying handoff targets for current entries.

### Artifact cards and contracts

`show()` owns bounded current state. `contract()` owns complete current
mechanical continuations. Neither recommends a continuation or repeats the
static manual.

### Evidence and runtime results

Evidence pages and revalidation results own typed, bounded factual reads and
proof boundaries. Session/runtime pages own persisted identity and recovery
mechanics. Empty healthy pages, unavailable stores, missing ids, stale
Artifacts, and indeterminate authority remain distinct typed states.

### Structured errors

Errors own concrete failure and repair facts. A repair points to the narrowest
exact input, method, Artifact, Evidence, runtime, semantic-authoring, or
environment target that can teach the next legal action.

### Packaged skill

The packaged `marivo-analysis` skill owns:

- framing the user's question as an Evidence obligation;
- preserving semantic authority and typed-flow boundaries;
- deciding which live family page or exact contract to consult;
- choosing an analysis method and parameters;
- preserving cross-script Artifact and Evidence continuity;
- requiring compatibility and revalidation at the appropriate handoff;
- distinguishing observation, interpretation, recommendation, and unsupported
  claims;
- closeout and semantic-authoring handoff.

The skill points to the six analysis hubs and the exact terminal boundary leaf.
It does not copy the method table, input inventories, Artifact family graph,
Evidence member list, signatures, parameter values, recovery member list, or
error catalog.

## Validation

### Registry invariants

- Every intended public analysis callable resolves to exactly one exact
  capability descriptor.
- Every exact callable descriptor binds one installed public callable identity.
- Every public Artifact family has exactly one Artifact family contract and one
  canonical public type target.
- Every navigation and method-family page has an explicit registered member
  tuple with at least two distinct routes; method-family members are
  discovery-owned by that family.
- Every ordinary exact capability needed for analysis has exactly one discovery
  owner.
- Cross-links never count as additional discovery membership.
- Every navigation member, cross-link, constraint link, parameter-help link,
  repair link, affordance link, and boundary-port link resolves independently.
- No navigation topic is represented as a constructor with an empty output
  type.
- No grouping membership is inferred from a dotted prefix.
- Accepted input and output families use only the closed family vocabulary.
- `SameAsInputFamily.parameter` names a live accepted-input parameter.
- Parameter-help contracts name live parameters and agree with defaults.
- Artifact admission rules refer only to accepted Artifact families and closed
  shapes.
- Every public type/member allowlist names installed public members only.
- `events.match`, `events.funnel`, and `events.time_to_event` are operator
  capabilities owned by `events`; `events.watermark` and
  `events.occurrence_bounds` are bounded read capabilities owned by
  `entry.event_observations`.

### Reachability invariants

Build the static Help graph from registry-owned membership and cross-links,
independently of rendered text.

Starting from `analysis`:

- every method family is reachable;
- every governed entry producer is reachable;
- both bounded Event observation reads are reachable without appearing in an
  Event computation family;
- every supporting input constructor or intentional closed value is reachable;
- every Artifact family is reachable;
- every Evidence and revalidation read is reachable;
- every Session, Artifact, and job recovery read is reachable;
- every terminal boundary is reachable;
- `semantic.authoring` is reachable for a missing reusable meaning;
- no ordinary exact callable leaf is more than four edges from the analysis
  root.

Parse every rendered root, hub, and navigation page. Resolve every advertised
target and assert that rendered membership equals registry membership. Cross-
links may add reachable edges but cannot hide an owned member.

### Render invariants

- Every page selects exactly one render class.
- Each render class enforces line, codepoint, route, and example/snippet limits.
- Roots and hubs contain no signatures, parameter tables, examples, or member
  recursion.
- Navigation pages contain no expanded child contracts.
- Exact callables contain one live signature, precise inputs/output, critical
  constraints/effects, and at most one minimal example.
- Public type pages expose consumption contracts, not constructors or private
  fields.
- Parent pages do not inline child text; child pages do not repeat parent
  inventories.
- Related targets come only from explicit edges and obey their route budget.
- Current briefings are deterministic, bounded, address-free, and side-effect
  free.
- A current `CatalogEntry` briefing may expose a kind-level analysis handoff; a
  bare `Ref` briefing exposes only identity and exact current-catalog
  resolution. Rendering either performs no project load or datasource access.
- Error briefings preserve every available structured diagnostic and complete
  repair within budget.
- Overflow fails explicitly and never truncates required content.
- Every navigation page advertises only its bounded canonical discovery
  projection; there is no global flat inventory.

### Static/dynamic continuity invariants

- Static Artifact producer/consumer algebra is derived from exact capability
  contracts.
- Every dynamic affordance's capability accepts the concrete Artifact family
  statically.
- Every affordance and boundary port has one exact resolvable help target and
  registry-owned public entrypoint.
- Dynamic affordances are grouped for rendering without reordering or dropping
  the structured tuple.
- The readable contract exposes all visible current affordances and all
  boundary ports within its independent budget.
- Failed current preconditions appear only with a complete typed repair or
  repair options.
- `show()` does not repeat the full contract matrix.
- `contract()` does not claim revalidation, compatibility, source freshness,
  business usefulness, causality, or recommendation.
- Terminal exits never appear as typed affordances and never produce inbound
  typed routes.

### Implementation drift invariants

- Enumerate public exports, facade methods, namespace methods, Artifact public
  members, and error classes independently of registry rows.
- Compare every registered callable path and public entrypoint to the same live
  object identity.
- Compare every rendered signature and return annotation with the installed
  callable.
- Compare accepted family/runtime gate behavior with generated static algebra
  and dynamic affordance generation.
- Change one fixture export, signature, default, return family, accepted family,
  admission shape, navigation membership, Artifact public member, Evidence
  route, and repair target adversarially; require the intended invariant to
  fail.
- Exercise every non-reflectable constraint, effect, authority policy, and
  structured repair family with behavioral witnesses.
- Renderer snapshots are secondary regression aids and cannot prove alignment
  when the registry and expected text drift together.

### Example validation

- Every exact callable example parses and binds against the live signature.
- Executable examples run in controlled fixtures with the selected repository
  interpreter.
- Examples use exact current entries or typed refs, never bare semantic id
  strings where a typed input is required.
- The semantic-object help matrix proves that a current `CatalogEntry` can
  expose a handoff, a bare `Ref` cannot, and focused operator leaves still state
  both accepted input forms where supported.
- Event and Lifecycle examples construct required typed inputs through their
  canonical input leaves.
- Terminal examples do not feed pandas results back into typed analysis.
- Navigation pages contain no examples.
- At least one adversarial route per hub proves unknown-target suggestions,
  cross-surface qualification, removed-topic absence, and no alias fallback.

### Evidence and recovery validation

- Healthy empty pages remain distinct from Evidence store failure.
- Exact digest, Finding, trace, Artifact, and job reads preserve identity.
- Compatibility, revalidation, quality, and source freshness remain distinct
  in help prose, result types, errors, and behavior tests.
- Recovered Artifacts render the same bounded `show()` and `contract()` facts as
  warm Artifacts before any explicit revalidation.
- Revalidation remains explicit and does not mutate or query datasource rows.
- Evidence compatibility evaluates the exact submitted Finding ids and never
  chooses a selection.

### Documentation and skill validation

- Current English and Chinese site documentation route through the same six
  canonical hubs and exact terminal boundary leaf.
- Site guides explain concepts and workflows without duplicating exact
  signatures or navigation inventories.
- The packaged skill remains one file and references the live hubs for current
  contract discovery.
- The analysis overview links this design as the current disclosure contract.
- Historical site versions and historical design records remain unchanged.

## Delivery

### Slice 1: Native navigation topology and budgets

- Add native navigation and method-family descriptor variants.
- Add the six analysis static render classes and immutable analysis-owned
  budgets.
- Register the six root decision hubs and the root's direct terminal-boundary
  edge.
- Reject singleton navigation and method-family descriptors.
- Move discovery ownership out of exact capability descriptors.
- Add eager registry validation before changing rendered output.

### Slice 2: Method, input, and Artifact family disclosure

- Register only multi-member method-family and input-family pages; route
  singleton capabilities and types directly from their parent hub.
- Replace prefix and special-case grouping with explicit membership.
- Register every Artifact family contract.
- Derive complete static Artifact producer/consumer algebra from exact
  capability contracts.
- Add route ownership, parameter binding, output-family, and type/member drift
  checks.

### Slice 3: Evidence, runtime, and boundary disclosure

- Add multi-member Evidence browse and exact-read pages; route current
  inspection, compatibility, revalidation, and quality directly to their exact
  leaves.
- Split Session, Artifact, job, and Evidence navigation; keep the terminal
  boundary as an exact root route.
- Replace `analysis.recovery`, `analysis.session`, and `analysis.boundary`
  navigation topics with the new canonical owners, and remove the singleton
  `analysis.sampling` topic in favor of `analysis.SamplingPolicy`.
- Preserve exact public method and boundary leaves.
- Add bounded target-index projection tests.

### Slice 4: Dynamic contract convergence

- Group readable affordances by registry-owned method family.
- Assert static family algebra, dynamic contract, and runtime admission
  continuity.
- Verify all affordance, repair, semantic-handoff, Evidence, recovery, and
  boundary links.
- Enforce independent Artifact-contract budgets without truncation.

### Slice 5: Public guidance cutover

- Compact the analysis root and remove the inline first-observation recipe.
- Update exact leaves to the new callable budget and one-example rule.
- Route structured errors to the narrowest new hub or exact leaf.
- Update the packaged skill plus current English and Chinese site guidance.
- Link this design from the analysis overview.
- Run focused help/registry/evidence/recovery tests, full repository gates,
  example validation, and site verification/build.

Each slice first lands its independent invariants and then changes the public
rendering that depends on them. The final public navigation cutover is atomic;
there is no supported mixed topology.

## Acceptance Criteria

- `marivo.help("analysis")` fits the root budget and advertises only the six
  decision hubs plus the exact terminal boundary leaf.
- The root contains no runnable workflow, operator inventory, builder
  inventory, Artifact family inventory, or recovery member list.
- A cold agent can find the correct governed entry for a Metric, Event pattern,
  or StateModel without assuming a Metric-first analysis.
- A current `CatalogEntry` can disclose its kind-level analysis handoff, while
  a bare `Ref` discloses only identity and exact Session-catalog resolution;
  both remain usable only according to the focused operator contract and
  runtime membership/readiness checks.
- A cold agent can discover every public analysis computation family by the
  fact it computes without receiving a recommendation or ordered plan.
- Every supporting builder, policy, selection, and intentional closed option is
  reachable by the parameter problem it solves.
- Every Artifact family has one bounded type page with complete derived static
  producers and consumers.
- A concrete Artifact contract exposes the complete mechanically visible
  continuation subset and every affordance resolves to one exact callable
  leaf.
- Static family compatibility is never presented as current admission.
- Evidence, compatibility, revalidation, quality, recovery, and source
  freshness are independently routed and never treated as equivalents.
- Session, Artifact, job, digest, Finding, trace, and revalidation reads are
  discoverable without storage-layout knowledge.
- Event computation discovery contains only matching and reducers; bounded
  watermark and occurrence-bound reads are owned by
  `analysis.entry.event_observations`.
- Exact callable leaves remain self-contained, reflected from the installed
  runtime, within the exact-callable budget, and contain at most one minimal
  example.
- No help call recursively expands another target or silently truncates
  required facts.
- Every ordinary exact callable is reachable from the root in at most four
  edges; every advertised edge resolves independently.
- No navigation or method-family page exists with fewer than two distinct
  member routes; singleton capabilities and types are direct parent routes.
- Every exact leaf has one discovery owner; cross-links do not create duplicate
  membership.
- The renderer contains no prefix-based or special-case family membership.
- Public export, signature, default, input/output family, admission shape,
  Artifact member, navigation edge, Evidence route, or repair-target drift
  cannot remain green.
- No global flat target inventory is restored; bounded native routes own
  discovery while exact secondary leaves remain resolvable.
- `analysis.recovery`, `analysis.session`, `analysis.sampling`,
  `analysis.boundary`, and the unintroduced `analysis.boundaries` are absent as
  compatibility aliases after cutover; exact public leaves remain canonical.
- The packaged skill owns boundaries and workflow judgment only and does not
  duplicate live inventories.
- No new planner, recommender, public registry, help coordinator, structured
  Help DTO, inbound terminal boundary, or compatibility path is introduced.
