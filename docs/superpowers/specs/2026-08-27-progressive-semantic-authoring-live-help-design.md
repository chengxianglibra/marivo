# Progressive Semantic Authoring Live Help Design

Date: 2026-08-27

Status: proposed

## Relationship To Existing Designs

This design extends the implemented agent-native authoring contract in
[`2026-08-21-agent-native-semantic-authoring-simplification-design.md`](2026-08-21-agent-native-semantic-authoring-simplification-design.md).
It does not restore a public authoring state machine, a one-object-at-a-time
checkpoint, a separate verify stage, or snapshot-backed readiness.

It refines the public semantic help topology described by
[`2026-07-13-marivo-semantic-live-interface-surface-design.md`](2026-07-13-marivo-semantic-live-interface-surface-design.md)
and preserves the implemented internal ownership in
[`2026-07-16-marivo-live-infrastructure-layering-design.md`](2026-07-16-marivo-live-infrastructure-layering-design.md):

- `marivo.introspection.live` owns neutral target resolution, reflection, and
  render limits;
- the semantic surface owns semantic object meaning, construction topology,
  checks, and rendering;
- the datasource and ontology surfaces retain their own native descriptors;
- the packaged `marivo-semantic` skill owns workflow and judgment boundaries,
  not API facts or parameter tables.

This design supersedes only the active help-routing and progressive-disclosure
claims in
[`2026-06-26-authoring-guidance-layering-design.md`](2026-06-26-authoring-guidance-layering-design.md)
and
[`2026-07-08-semantic-authoring-public-guidance-design.md`](2026-07-08-semantic-authoring-public-guidance-design.md).
Their historical `ms.help(...)`, `prepare_*`, per-object verification, and
one-object workflow language is not part of the target contract.

Core Domain, Entity, field, Metric, and Relationship meanings remain owned by
[`docs/specs/semantic/semantic-object-model.md`](../../specs/semantic/semantic-object-model.md).
Event and StateModel meaning remains owned by
[`2026-07-13-event-semantic-and-analysis-design.md`](2026-07-13-event-semantic-and-analysis-design.md),
and the target PeriodCalendar, TemporalSet, and WorkSchedule meanings are owned
by the proposed
[`docs/specs/temporal-semantics.md`](../../specs/temporal-semantics.md). The
current authoring process remains owned by
[`docs/specs/semantic/authoring-workflow.md`](../../specs/semantic/authoring-workflow.md).

## Summary

Make `marivo.help(...)` disclose semantic authoring in six bounded layers:

1. global authoring routing;
2. a semantic authoring decision hub;
3. semantic object-kind, supporting-builder, and check navigation;
4. exact callable contracts;
5. public type contracts;
6. current object and structured-error briefings.

The target route is:

```text
marivo.help("authoring")
  -> marivo.help("semantic.authoring")
    -> marivo.help("semantic.objects")
      -> marivo.help("semantic.objects.<kind>")
        -> marivo.help("semantic.<constructor>")
    -> marivo.help("semantic.builders")
      -> marivo.help("semantic.builders.<family>")
        -> marivo.help("semantic.<builder>")
      -> marivo.help("semantic.ref")
        -> marivo.help("semantic.ref.<kind>")
    -> marivo.help("semantic.checks")
      -> marivo.help("semantic.source_check")
        -> marivo.help("semantic.source_check.<method>")
      -> marivo.help("semantic.<check>")
```

An agent should not need to know constructor names before entering help, scan a
flat list of all authoring primitives, inspect private source, or infer object
relationships from examples. Exact leaf help remains the only static callable
contract; navigation pages select the right leaf without copying its signature
or parameter table. An object-kind page also tells the agent which material
semantic choices must be settled and gives bounded, fail-closed guidance for
settling them. It never chooses project-specific business meaning from
physical evidence.

## Current-State Assessment

Against the installed 2026-08-27 surface, the answer to the motivating
authoring questions is **not yet complete**:

| Requirement | Current state | Conclusion |
|---|---|---|
| Exact help for top-level public semantic callables | The semantic capability registry covers the current top-level constructors, builders, load, probes, readiness, and diagnostics | Broadly complete at the leaf level |
| Exact help for every public construction method | `semantic.ref` and `semantic.source_check` document their shared factory conventions, but neither `ms.ref.<kind>` nor `ms.source_check.<method>` methods are independently resolvable callable leaves | Incomplete |
| Discover every semantic object kind and its meaning | The root groups constructors by broad family; there is no registry-owned object-kind index or object relationship graph | Incomplete |
| Discover what must be decided for each object and how to decide it safely | Callable signatures expose some required inputs, while the packaged skill gives only the general workflow; there is no object-owned checklist, determination guidance, or authority boundary | Incomplete |
| Discover all legal construction modes for one object kind | The relevant callable leaves exist, but the agent must already know names such as `aggregate`, `ratio`, or `dimension_column` and assemble the alternatives itself | Incomplete |
| Discover supporting builders by parameter problem | Builders are mixed into the large authoring family; exact leaves exist but need-directed grouping is absent | Incomplete |
| Choose the correct inspection or check | Operations exist, but there is no single proof/non-proof routing page | Incomplete |
| Route from `authoring` to every needed leaf | `semantic.authoring` is a renderer-owned partial route list and omits several object families and helpers | Incomplete |
| Keep help within the agent's context budget | Hard root/focused line and codepoint ceilings exist, but there are only two coarse budget classes and no route/example quotas | Partially complete |
| Keep implementation and help output aligned | Callable signatures are reflected, but semantic relationships and route membership can still drift through registry or renderer-owned duplicate facts | Partially complete |

Therefore “the target string resolves if already known” must not be used as the
definition of help completeness. This design defines completeness as both:

1. **leaf completeness** — every public callable or intentional type/navigation
   contract has one exact canonical target; and
2. **route completeness** — every required leaf is reachable from bounded roots
   through registry-owned, intent-selective edges.

## Problem

### Target coverage is not the same as usable discovery

The current semantic registry covers the public semantic callables and each
canonical target resolves. The semantic root nevertheless renders one large
`Author by object family` group containing object constructors, parameter
builders, typed-handle builders, and low-level expression helpers together.

This answers “what callable targets exist?” but not the earlier questions an
agent actually has:

- what semantic objects does Marivo model;
- what each object means and who owns it;
- which material choices define each object and how they can be settled safely;
- which objects it depends on;
- which construction modes are legal;
- which mode is the default and which is an expression escape hatch;
- which helper builds a nested parameter value;
- which operation answers a particular inspection or validation question.

### The focused authoring page is a renderer-owned partial inventory

The current `semantic.authoring` renderer contains a manually selected route
list for domains, entities, direct fields, basic aggregate metrics, load,
readiness, preview, and source health. It omits calculated fields, derived
metrics, relationships, events, state models, governed temporal objects, and
many supporting builders.

Because that list is maintained in rendering code instead of the semantic
registry, it can drift from public exports and registry membership. Agents can
eventually recover by returning to the full semantic root, but that is broad
catalog scanning rather than need-directed progressive disclosure.

### Object meaning and callable shape are different contracts

`semantic.dimension` is the exact help leaf for `@ms.dimension(...)`. It cannot
also be a neutral “what is a Dimension and how can I build one?” page without
mixing the object family with one of its construction modes.

The same distinction is more visible for Metric:

- `ms.aggregate(...)` and `ms.count(...)` are the helper-first defaults;
- `ms.ratio(...)`, `ms.weighted_mean(...)`, `ms.linear(...)`, and
  `ms.cumulative(...)` construct different derived meanings;
- `@ms.metric(...)` is an expression-body escape hatch.

A flat callable index assumes the agent already knows which of those names
matches its intent.

### Object construction requires decisions, evidence, and encoding

Knowing that a semantic object kind exists is not sufficient for safe
authoring. Before choosing a constructor for any object kind, an agent must
know:

1. which material semantic choices define the object;
2. which reliable facts and current authorities can settle each choice; and
3. which exact Marivo contract encodes the settled meaning.

For example, an Entity needs an explicit row grain, key, and history model. A
partition column alone does not establish that each partition is a periodic
full snapshot. A Measure owns the row-level numeric fact's unit and additivity;
a numeric dtype, familiar field name, or historical `SUM(...)` does not
establish additivity. Tier-1 Metrics consume that governed Measure meaning,
while the `@ms.metric(...)` expression escape hatch declares its own
additivity. These are examples of the general requirement, not privileged
decision families.

These are object-kind questions, not callable-shape facts and not a reason to
add a decision engine. The object-kind page owns the stable checklist,
determination criteria, and evidence limitations. Datasource operations own
physical observations, current business authority owns reusable meaning, the
agent interprets the evidence, and the exact callable leaf owns Marivo syntax.

### Kind-level prerequisite reuse can misstate constructor dependencies

Source placement is shared by constructors that produce the same semantic
kind, but construction prerequisites are not. For example:

- `count` requires an Entity and does not require a Measure;
- `aggregate` requires a Measure;
- `ratio` and `linear` require Metrics;
- `cumulative` requires a Metric and may require a TimeDimension or an anchor
  builder.

Attaching one `entity + measure` prerequisite set to every Metric-producing
constructor creates inaccurate help even when reflection renders the correct
signature. Placement, identity, constructor dependencies, and recommended
construction mode must therefore be separate registry facts.

### Inspection and checking operations prove different things

Datasource inspection, semantic load, readiness, preview, source health,
parity, and richness answer independent questions. Listing them as generic
runtime or diagnostic capabilities does not teach their evidence boundary and
encourages invalid substitutions such as:

```text
load success == runtime executability
preview success == readiness certification
source health == semantic meaning approval
richness warning == execution blocker
```

Help must route checks by the question the agent needs answered and state what
each result can and cannot prove.

## Goals

- Keep `marivo.help(...)` as the only public help coordinator.
- Let a cold agent discover every semantic object kind without knowing Python
  symbol names.
- Explain each object kind's meaning, identity, ownership, dependencies,
  construction modes, catalog location, and principal consumers.
- Explain the material semantic choices for each object kind, how reliable
  evidence and current authority can settle them, and which common signals do
  not establish them.
- Preserve one exact canonical help leaf for every public callable.
- Separate semantic object constructors from supporting value and handle
  builders.
- Route inspection, preview, checking, and diagnostics by the question they
  answer and the evidence they establish.
- Make every navigation family and relationship registry-owned.
- Keep roots, navigation pages, callable leaves, type leaves, and instance
  briefings independently bounded.
- Spend context only on the current decision: each help call renders one layer,
  never recursively expands descendants, and advertises only bounded next
  routes.
- Derive callable shape from live implementation and every non-reflectable help
  fact from one owning registry; reject drift before rendering it.
- Fail closed on semantic ambiguity: help may describe stable decision criteria
  and evidence limits, but never manufactures a project-specific conclusion.
- Preserve typed-ref authoring: object-to-object arguments use declaration
  results or `ms.ref.<kind>(path)`, never bare semantic-id strings.
- Preserve helper-first defaults and clearly label decorator or expression
  paths as escape hatches.
- Make every advertised route mechanically resolvable and test root-to-leaf
  reachability.

## Non-Goals

- No new `md.help`, `ms.help`, `mv.help`, `mo.help`, `describe`, or public
  registry API.
- No public JSON help format or `format=` argument.
- No automatic semantic authoring, recommendation engine, confidence score, or
  constructor selection API.
- No separate decision-help target family or per-decision navigation hierarchy;
  bounded decision guidance stays on the owning object-kind page.
- No workflow wizard, authoring plan object, lifecycle graph, persisted
  checkpoint, approval receipt, or per-object verify stage.
- No inference of business meaning from physical evidence.
- No duplication of exact signatures or parameter tables in navigation pages,
  site guides, or packaged skills.
- No “include everything” or recursive help mode that expands the authoring
  graph into one response.
- No flattening of datasource, semantic, analysis, and ontology descriptors
  into one weaker shared capability model.
- No compatibility aliases for proposed navigation targets. Each concept has
  one canonical target.
- No change to semantic object meaning, constructor signatures, loader
  behavior, readiness, preview, source health, parity, richness, or analysis
  execution in this design.

## Disclosure Model

### Context-budget policy

Progressive disclosure is a context-isolation contract, not only an information
architecture. One invocation answers one bounded routing question, object-kind
question, exact contract, or current-state question and returns only the routes
needed for the next step. Parent pages do not inline child contracts, and child
pages do not repeat the parent inventory.

The initial hard ceilings are:

| Render class | Used for | Max lines | Max codepoints | Max outgoing routes | Max examples/snippets |
|---|---|---:|---:|---:|---:|
| Root | Global and native surface roots | 32 | 3,000 | 10 | 0 |
| Decision hub | `authoring` and `semantic.authoring` | 40 | 4,000 | 8 | 0 |
| Navigation | Object, builder, check, and ref-factory indexes; object-kind pages | 64 | 6,000 | 16 | 0 |
| Exact contract | Callable and public-type leaves | 72 | 7,000 | 8 | 1 |
| Current briefing | Concrete object, result, and error help | 64 | 6,000 | 6 | 1 |

Budget ownership follows page ownership. The global help coordinator owns the
`root` budget for `marivo.help()` and the `decision_hub` budget for
`marivo.help("authoring")`. The semantic registry owns the five semantic page
classes and their budgets for `semantic.*` targets and semantic runtime
briefings. Both owners pass their selected hard limits to the same neutral
enforcement primitive; neither imports the other's vocabulary or membership.

Lines and codepoints are deterministic model-neutral proxies for token use;
Marivo does not depend on one model tokenizer. Route and example limits prevent
a short but semantically dense page from consuming context through large inline
inventories.

Budgets apply independently to each rendered page type. This design does not
add cumulative journey accounting, tokenizer-specific accounting, or a second
budget mechanism. Progressive disclosure is enforced by these single-page
ceilings, non-recursive rendering, bounded outgoing routes, and graph
reachability.

These ceilings are calibrated against the installed design-time surface, not
chosen from the previous maximums: the current semantic root is 71 lines and
6,483 codepoints and must be split, while the longest current semantic callable
leaf is 44 lines and fewer than 2,000 codepoints. Budget increases require new
observed evidence and a design review; adding content is not sufficient reason.

The global `marivo.help()` page advertises only the authoring and analysis
secondary roots. There is no flat cross-surface target inventory; semantic
targets are discovered through bounded authoring routes, while exact secondary
leaves remain resolvable when already known or obtained from live state.

Content follows these rules:

- roots contain orientation and exact next targets, never member signatures;
- decision hubs route by agent intent and contain no exhaustive object or
  helper list;
- navigation pages contain bounded meaning plus member routes, never callable
  parameter tables or examples;
- an exact leaf is self-contained for one invocation and includes at most one
  minimal example;
- current-object and error help adds only facts needed for the next repair or
  operation;
- related targets are selected from registry-owned edges, deduplicated, and
  capped; there is no generic exhaustive “see also” dump;
- help never auto-fetches or recursively renders a referenced target;
- the installed version appears once in compact form; full interpreter and
  package-path fingerprints remain root-only or explicit mismatch diagnostics.

Budget overflow is a contract failure. The renderer must not silently truncate
signatures, constraints, repair facts, or examples. It must either make the
page more compact, move optional material behind another canonical target, or
fail validation until the owning descriptor is corrected.

### Level 0: Global routing

`marivo.help()` remains the bounded global concept page. It points only to
`marivo.help("authoring")` and `marivo.help("analysis")`, without listing
semantic constructors or analysis capabilities.

`marivo.help("authoring")` answers only which surface owns the current need:

```text
authoring

  Physical source definitions and evidence:
    marivo.help("datasource.authoring")

  Executable reusable business semantics:
    marivo.help("semantic.authoring")

  Optional non-executable contextual relations:
    marivo.help("ontology.authoring")

  Current result or error:
    marivo.help(result_or_error)
```

The global topic may also route to each surface root. It does not own surface
vocabulary, constructor membership, or semantic teaching order.

### Level 1: Semantic authoring decision hub

`marivo.help("semantic.authoring")` is a non-callable navigation topic. It
routes by the agent's question:

```text
semantic.authoring

  What semantic objects exist, what do they mean, and how are they related?
    marivo.help("semantic.objects")

  How do I build a nested parameter value or typed handle?
    marivo.help("semantic.builders")

  Which inspection, preview, or check answers my current question?
    marivo.help("semantic.checks")

  How are project sources loaded and exact current entries selected?
    marivo.help("semantic.load")
    marivo.help("semantic.SemanticCatalog")

  Do I need optional contextual relations rather than executable semantics?
    marivo.help("ontology.authoring")
```

This page retains the stable source layout and coherent-slice closeout:

```text
models/datasources/<datasource>.py
models/semantic/<domain>/_domain.py
models/semantic/<domain>/<module>.py

author one dependency-coherent slice
-> one ms.load()
-> catalog.require(...) for each authored root
-> scoped readiness
-> only the runtime or source-health probes justified by current risk
```

It does not list every object or helper target.

### Level 2A: Semantic object-kind index

`marivo.help("semantic.objects")` lists semantic object kinds, not Python
callables. Each row contains a bounded meaning and one exact object-kind topic.

| Object kind | Bounded meaning | Object-kind target |
|---|---|---|
| Domain | Business namespace and accountability boundary | `semantic.objects.domain` |
| Entity | Reusable business entity or fact-set identity backed by a source | `semantic.objects.entity` |
| Dimension | Categorical field used for grouping, filtering, identity, or joins | `semantic.objects.dimension` |
| TimeDimension | Explicit business time axis with grain and parse semantics | `semantic.objects.time_dimension` |
| Measure | Row-level numeric fact owning unit and additivity | `semantic.objects.measure` |
| Metric | Analyzable business value built from governed semantic inputs | `semantic.objects.metric` |
| Relationship | Executable directed join contract between Entities | `semantic.objects.relationship` |
| Event | Reusable occurrence with identity, occurrence time, and participants | `semantic.objects.event` |
| StateModel | Closed normative lifecycle for one subject Entity | `semantic.objects.state_model` |
| PeriodCalendar | Governed finite business-period hierarchy | `semantic.objects.period_calendar` |
| TemporalSet | Governed finite set of named temporal occurrences | `semantic.objects.temporal_set` |
| WorkSchedule | Governed final daily working-status schedule | `semantic.objects.work_schedule` |

Datasource is upstream physical authority and is not a semantic object kind.
Ontology is an optional separate surface over exact Entity, Measure, and Metric
refs; it is not added to this object index.

The object-kind set is derived from the closed runtime `SemanticKind` set minus
`datasource`. Adding a semantic kind therefore requires its object page,
normative owner, decision scope, construction routes, and checks in the same
change; it cannot appear as a callable-only object with no business guidance.

The object index renders this bounded relationship graph before routing to one
kind:

```text
Domain ─┐
        ├─> Entity
DatasourceRef ─┘     ├─ Dimension / TimeDimension / Measure
                      │    └─ Metric
                      ├─ Relationship ── participant path ──> Event
                      └─ Event ── trigger ──> StateModel

TimeDimension / Dimension
  └─> PeriodCalendar / TemporalSet / WorkSchedule

EntityRef / MeasureRef / MetricRef
  └─> optional marivo.ontology contextual edges
```

The graph expresses semantic ownership and dependency, not a mandatory build
sequence. An authoring checkpoint is one dependency-coherent slice and may
contain several related objects.

The initial construction and supporting-builder inventory is:

| Object kind | Ref-producing default | Ref-producing alternatives and escape hatches | Supporting parameter or local-value builders |
|---|---|---|---|
| Domain | `semantic.domain` | none | `semantic.ai_context` |
| Entity | `semantic.entity` | none | `semantic.snapshot`, `semantic.validity`, `semantic.ai_context` |
| Dimension | `semantic.dimension_column` | `semantic.dimension` expression decorator | `semantic.ai_context`, `semantic.bind` |
| TimeDimension | `semantic.time_dimension_column` | `semantic.time_dimension` expression decorator | `semantic.datetime`, `semantic.timestamp`, `semantic.strptime`, `semantic.hour_prefix`, `semantic.ai_context`, `semantic.bind` |
| Measure | `semantic.measure_column` | `semantic.measure` expression decorator | `semantic.semi_additive`, `semantic.ai_context`, `semantic.bind` |
| Metric | `semantic.aggregate` or `semantic.count` according to intent | `semantic.ratio`, `semantic.weighted_mean`, `semantic.linear`, `semantic.cumulative`; `semantic.metric` expression decorator as escape hatch | `semantic.where`, `semantic.from_sql`, `semantic.grain_to_date`, `semantic.trailing`, `semantic.ai_context`, `semantic.bind` |
| Relationship | `semantic.relationship` | none | `semantic.join_on`, `semantic.ai_context` |
| Event | `semantic.event` | none | `semantic.all_rows`, `semantic.participant`, `semantic.ai_context` |
| StateModel | `semantic.state_model` | none | `semantic.lifecycle_state`, `semantic.inception`, `semantic.transition`, `semantic.model_state`, `semantic.participant_role`, `semantic.ai_context` |
| PeriodCalendar | `semantic.period_calendar` | none | `semantic.period_correspondence`, `semantic.ai_context` |
| TemporalSet | `semantic.temporal_set` | none | `semantic.ai_context` |
| WorkSchedule | `semantic.work_schedule` | none | `semantic.ai_context` |

A `ConstructionMode` target must itself produce the object kind's exact
`Ref[kind]`. A builder that supplies `versioning=`, `parse=`, `additivity=`,
`keys=`, participants, local lifecycle values, provenance, anchors, or context
is a `supporting_target`, never an alternative construction mode.

### Level 2B: One semantic object-kind page

Each `semantic.objects.<kind>` page answers object-level questions without
copying callable signatures.

Every page owns:

- bounded business meaning;
- the material semantic choices that change the object's interpretation;
- bounded guidance for determining each choice from reliable evidence and
  current authority;
- when material, explicit signals that may inform a decision but do not
  establish it;
- produced typed-ref kind;
- ref construction route for forward or cross-file references;
- source placement and catalog collection;
- owning object or namespace;
- required, optional, and inferred object relationships;
- legal construction modes and when each mode applies;
- default, alternative, and escape-hatch labeling;
- supporting builders;
- principal semantic consumers;
- applicable post-load checks.

Decision guidance is concise object-level contract text, not a recommendation
or a project-specific answer. It distinguishes physical facts from reusable
business meaning and uses one of three determination bases:

- `source_evidence` for facts established by the source and its production
  contract;
- `business_authority` for reusable meaning that must come from the user, an
  approved project definition, or attributable non-conflicting documentation;
- `source_and_business` when physical evidence constrains the legal choices but
  business authority determines the intended interpretation.

If no stable general rule can be stated accurately, the page omits the rule and
states that current authority is required. It does not substitute a heuristic,
confidence score, or generated recommendation.

Decision coverage follows one rule for every object kind. Review the owning
normative object contract and classify each fact that changes business
interpretation as object decision guidance. Keep callable shape, defaults, and
purely mechanical encoding on the exact leaf; keep current observations on
datasource or catalog results. A material decision may route to a constructor,
supporting builder, check, or `ai_context` guardrail. If the current object
model cannot encode it, the page says so rather than implying that a parameter
or guarantee exists.

The initial material-decision scope is:

| Object kind | Decisions its object-kind page must cover |
|---|---|
| Domain | Business boundary, accountable owner, default-domain behavior, and reusable definition or guardrails |
| Entity | Reusable recordset meaning, authoritative source, row grain, identity/key, history and as-of model, and domain ownership |
| Dimension | Owning Entity, reusable categorical/identity/filter/join meaning, relevant code and null semantics, and direct-column versus normalized-expression construction |
| TimeDimension | Owning Entity, business-time role, granularity, physical encoding and parse/timezone meaning, default-axis choice, and sampled cadence when applicable |
| Measure | Row-level numeric meaning and grain, physical unit, dimensional and temporal additivity, semi-additive status axis/fold, and direct-column versus expression construction |
| Metric | Reusable population and value definition, base versus derived construction, aggregation/filter, denominator or failure policy, root/fanout, unit/additivity, temporal behavior, provenance, and guardrails |
| Relationship | Directed business meaning, endpoint grains, join-key equivalence, expected multiplicity and fanout implications, and the checks needed when those claims require source evidence |
| Event | Business occurrence and row predicate, occurrence identity, business occurrence time, participant roles, directed paths, and participant cardinality |
| StateModel | Subject lifecycle, closed state vocabulary, initial and terminal meaning, inception and transition triggers, deterministic transitions, and deliberately excluded replay policies |
| PeriodCalendar | Business calendar convention, civil-date authority, boundary timezone, finite coverage, level/key meaning, derived containment expectations, and named correspondence conventions |
| TemporalSet | Meaning as sparse or overlapping named occurrences, occurrence identity, half-open bounds, date-versus-timestamp encoding, boundary timezone, finite coverage, category, and intentional overlap/gap semantics |
| WorkSchedule | Final working-status authority, civil-date and boolean meaning, boundary timezone, finite exhaustive coverage, and business-owned rule precedence already resolved by the source |

This table is the minimum review scope, not help text to render verbatim. Each
object page remains bounded and includes only the decisions applicable to that
kind.

For example:

```text
semantic.objects.dimension

  Meaning:
    A reusable categorical field owned by one Entity. Dimensions can group,
    filter, identify Events, form Relationship keys, and define governed
    temporal metadata.

  Identity:
    output: Ref[dimension]
    forward/cross-file: ms.ref.dimension(path)
    catalog: catalog.dimensions

  Decide before authoring:
    Establish the reusable categorical meaning and owning Entity from current
    business authority. Use source evidence to choose a direct column or a
    normalized expression; a column name or string dtype does not establish
    the business meaning.

  Construction modes:
    Direct physical column — default
      marivo.help("semantic.dimension_column")

    Restricted row-level Ibis expression — escape hatch
      marivo.help("semantic.dimension")

  Relationships:
    owned by: Entity
    consumed by: Relationship, Event, PeriodCalendar, TemporalSet, WorkSchedule

  Supporting builders:
    marivo.help("semantic.ai_context")
    marivo.help("semantic.bind")
```

The Metric object-kind page provides intent-based dispatch:

```text
semantic.objects.metric

  Aggregate one Measure:
    marivo.help("semantic.aggregate")

  Count Entity rows:
    marivo.help("semantic.count")

  Divide two Metrics:
    marivo.help("semantic.ratio")

  Compute a weighted mean from Measures:
    marivo.help("semantic.weighted_mean")

  Add or subtract commensurable Metrics:
    marivo.help("semantic.linear")

  Accumulate one Metric over governed time:
    marivo.help("semantic.cumulative")

  Define a restricted Ibis expression body — escape hatch:
    marivo.help("semantic.metric")
```

Construction-mode descriptions distinguish business intent; they do not rank
two equivalent spellings of the same capability or preserve legacy aliases.
The object page is the only decision-guidance layer: there are no
`semantic.decisions.*` or `semantic.objects.<kind>.<decision>` targets.

### Level 2C: Supporting-builder index

`marivo.help("semantic.builders")` groups public constructors that build nested
parameter values, local authoring values, or typed handles rather than a
top-level source-authored semantic ref. To stay within the route budget, the
index routes to bounded family pages instead of listing every helper at once.

| Builder need | Canonical next target |
|---|---|
| Create an exact typed ref | `semantic.ref` |
| Attach bounded agent context | `semantic.ai_context` |
| Describe Entity history | `semantic.builders.entity_history` |
| Parse physical time values | `semantic.builders.temporal_parsing` |
| Support Field and Metric parameters and expressions | `semantic.builders.field_metric_support` |
| Build Relationship/Event values and typed participant handles | `semantic.builders.relationship_event` |
| Build StateModel local values and handles | `semantic.builders.state_model` |
| Build governed temporal parameter values | `semantic.builders.governed_temporal` |

Each `semantic.builders.<family>` page gives a one-line “use when” description
and routes to these exact leaves:

| Builder-family target | Exact callable leaves |
|---|---|
| `semantic.builders.entity_history` | `semantic.snapshot`, `semantic.validity` |
| `semantic.builders.temporal_parsing` | `semantic.datetime`, `semantic.timestamp`, `semantic.strptime`, `semantic.hour_prefix` |
| `semantic.builders.field_metric_support` | `semantic.where`, `semantic.semi_additive`, `semantic.bind`, `semantic.from_sql`, `semantic.grain_to_date`, `semantic.trailing` |
| `semantic.builders.relationship_event` | `semantic.join_on`, `semantic.participant`, `semantic.participant_role`, `semantic.all_rows` |
| `semantic.builders.state_model` | `semantic.lifecycle_state`, `semantic.inception`, `semantic.transition`, `semantic.model_state` |
| `semantic.builders.governed_temporal` | `semantic.period_correspondence`, `semantic.calendar_grain` |

Neither the builder index nor a family page inlines member signatures,
allowed-value tables, or examples.

`semantic.ref` is a bounded typed-ref factory page rather than one callable
leaf. It lists the closed semantic kinds and routes each factory method to an
exact nested leaf:

```text
semantic.ref

  Domain:         marivo.help("semantic.ref.domain")
  Datasource:     marivo.help("semantic.ref.datasource")
  Entity:         marivo.help("semantic.ref.entity")
  Dimension:      marivo.help("semantic.ref.dimension")
  TimeDimension:  marivo.help("semantic.ref.time_dimension")
  Measure:        marivo.help("semantic.ref.measure")
  Metric:         marivo.help("semantic.ref.metric")
  Relationship:   marivo.help("semantic.ref.relationship")
  Event:          marivo.help("semantic.ref.event")
  StateModel:     marivo.help("semantic.ref.state_model")
  PeriodCalendar: marivo.help("semantic.ref.period_calendar")
  TemporalSet:    marivo.help("semantic.ref.temporal_set")
  WorkSchedule:   marivo.help("semantic.ref.work_schedule")
```

Each `semantic.ref.<kind>` leaf reflects the corresponding
`ms.ref.<kind>(path)` method, including its exact path shape and returned
`Ref[<kind>]`. `semantic.ref.datasource` is the one upstream exception in this
factory page because the public `ms.ref` namespace also creates Datasource
refs. It does not make Datasource a semantic object kind: physical datasource
construction and inspection remain owned by `datasource.authoring`.

The canonical `semantic.ref` string and the concrete public `ms.ref` namespace
object resolve to this same static factory contract. Its closed method
membership is registry-owned rather than reconstructed from `dir(...)` or
renderer-local names.

An exact target has one discovery owner. Object-kind pages may cross-link a
builder, but that cross-link does not create a second builder-group membership.

### Level 2D: Inspection and check index

`marivo.help("semantic.checks")` routes by the fact the agent needs to
establish:

| Question | Exact route | What it proves | What it does not prove |
|---|---|---|---|
| What physical source, schema, columns, and types exist? | `datasource.authoring`, `datasource.inspect` | Current authoritative physical metadata to the backend's supported extent | Reusable business meaning |
| Do I need bounded sampled rows or source-specific SQL evidence? | `datasource.authoring` | Explicitly scoped or governed physical observations | Semantic validity or typed-analysis authority |
| Do project sources execute, resolve refs, and compile as one project? | `semantic.load` | Static project assembly and structural validation | Current external health or operation-shaped executability |
| Is this exact requested dependency closure statically ready for analysis? | `semantic.readiness` | Governed semantic closure and `analysis_ready_inputs` | Successful execution of every future analysis shape |
| What does this entry produce under one explicit authoring scope? | `semantic.preview`, `semantic.preview_many` | Bounded current runtime observation for the exact requested scope | Persistent certification or readiness mutation |
| How do I declare an exact null, enum, uniqueness, freshness, relationship, or cardinality expectation? | `semantic.source_check` | The expectation is explicit, typed, and closed | That the current source satisfies it |
| Does the current source still satisfy explicit schema or data expectations? | `semantic.source_health` | Ephemeral current source evidence for declared checks | Business approval or readiness mutation |
| Does a Metric agree with its governed SQL provenance? | `semantic.parity_check` | Exact parity result for the declared comparison | General correctness outside that comparison |
| Is the semantic project rich enough for current demand? | `semantic.richness` | Demand-ranked advisory gaps | A readiness blocker or execution failure |

`semantic.source_check` is a bounded factory page, parallel to `semantic.ref`.
It routes each public constructor to one exact callable leaf:

```text
semantic.source_check

  Not null:                marivo.help("semantic.source_check.not_null")
  Allowed values:          marivo.help("semantic.source_check.allowed_values")
  Unique fields:           marivo.help("semantic.source_check.unique")
  Freshness:               marivo.help("semantic.source_check.freshness")
  Relationship matches:    marivo.help("semantic.source_check.relationship_matches")
  Relationship cardinality: marivo.help("semantic.source_check.relationship_cardinality")
```

Each `semantic.source_check.<method>` leaf reflects the corresponding
`ms.source_check.<method>(...)` signature, constraints, and exact `SourceCheck`
variant. Constructing a check establishes only an explicit expectation;
`catalog.source_health(..., checks=[...], scope=...)` owns the current evidence
that evaluates it.

The canonical `semantic.source_check` string and concrete public
`ms.source_check` namespace object resolve to the same static factory contract.
Its six method routes are registry-owned and render no signatures until one
exact method leaf is selected.

The page states the non-equivalence directly:

```text
load success
  != readiness
  != preview success
  != source health
  != operation-shaped analysis execution
```

There is no `semantic.verify` target and no per-object verification checkpoint.

### Level 3: Exact callable leaf

`marivo.help("semantic.<callable-target>")`, including qualified receiver and
factory-method targets, remains the only static contract for one public
callable.

A callable leaf owns:

- canonical id and public receiver-qualified entrypoint;
- live reflected signature;
- required and optional inputs, concrete types, defaults, and omit rules;
- cross-parameter constraints;
- output family or return type;
- data access, connection, mutation, and other effects;
- source placement for source-authored declarations;
- one correct minimal example;
- exact prerequisite and supporting targets;
- post-save `ms.load()` and catalog handoff when applicable;
- stable error kinds and structured repair routes.

Reflection owns callable shape. Registry metadata owns semantics that reflection
cannot express, such as cross-parameter constraints, effects, construction
intent, placement, and help relationships.

For example, `semantic.relationship` must explicitly route the `keys` value:

```text
keys: list[JoinKey]
Build with: marivo.help("semantic.join_on")
```

It is insufficient for `ms.join_on(...)` to appear only inside the example.

### Level 4: Public type leaf

Public type help remains focused and excluded from root discovery unless the
type is an intentional navigation topic.

A type leaf owns:

- producers;
- public fields;
- public consumption methods;
- consumers;
- stable construction or serialization guidance when applicable.

Examples include `SemanticCatalog`, `CatalogEntry`, `CatalogCollection`, `Ref`,
the public `ref` and `source_check` factory namespaces, `JoinKey`, result types,
and error classes. A factory-namespace contract owns its closed method routes
without inlining their signatures. Type-object, namespace-object, and
canonical-string targets render the same static contract where applicable.

### Level 5: Current object and error briefing

`marivo.help(entry)` adds only bounded deterministic current facts to the
static entry contract:

- exact kind and path;
- current catalog identity;
- `.show()` and `.details().show()` inspection;
- `catalog.readiness(refs=[entry])`;
- applicable preview or source-health routes;
- qualifying analysis handoff when mechanically valid.

`marivo.help(ref)` shows identity and the exact `catalog.require(ref)` route. It
does not load a project or guess membership.

`marivo.help(error_instance)` preserves:

- concrete message;
- expected and received values;
- location;
- repair kind and action;
- candidates from current state;
- copyable snippet when an executable retry is possible;
- one exact next help target.

Instance enrichment cannot change static API truth, recommend business values,
or expose private fields and memory addresses.

## Registry Design

### Keep native descriptor richness

The semantic registry becomes a closed union of native semantic descriptor
types rather than expanding `AuthoringCapability` into an optional-field mega
record:

```python
SemanticHelpDescriptor = (
    AuthoringCapability
    | SemanticNavigationTopic
    | SemanticObjectContract
)
```

All variants satisfy the neutral resolver's minimal descriptor protocol:

```python
canonical_id: str
public_entrypoint: str | None
summary: str
```

The resolver remains generic and passes the native descriptor to the semantic
renderer. It does not learn semantic kinds, construction modes, check meaning,
or builder families.

### Navigation topics

```python
@dataclass(frozen=True)
class SemanticNavigationTopic:
    canonical_id: str
    summary: str
    members: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = None
```

The initial navigation topics are:

- `authoring`;
- `objects`;
- `builders`;
- `checks`.

Nested object-kind contracts and `builders.<family>` topics are separate
descriptors rather than renderer special cases.

### Object-kind contracts

```python
@dataclass(frozen=True)
class ConstructionMode:
    intent: str
    role: Literal["default", "alternative", "escape_hatch"]
    target: LiveHelpTarget


@dataclass(frozen=True)
class SemanticObjectRelationship:
    relation: Literal[
        "owned_by",
        "requires",
        "may_reference",
        "inferred_from",
        "consumed_by",
    ]
    target: LiveHelpTarget
    explanation: str


@dataclass(frozen=True)
class SemanticObjectDecision:
    question: str
    determine_from: str
    basis: Literal[
        "source_evidence",
        "business_authority",
        "source_and_business",
    ]
    next_targets: tuple[LiveHelpTarget, ...]
    does_not_establish: str | None = None


@dataclass(frozen=True)
class SemanticObjectContract:
    canonical_id: str
    summary: str
    semantic_kind: SemanticKind
    ref_target: LiveHelpTarget
    catalog_collection: str
    placement_kind: AuthoringPlacementKind
    decisions: tuple[SemanticObjectDecision, ...]
    construction_modes: tuple[ConstructionMode, ...]
    relationships: tuple[SemanticObjectRelationship, ...]
    supporting_targets: tuple[LiveHelpTarget, ...]
    check_targets: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = None
```

`decisions` is rendered only on the owning object-kind page. It records the
material question, the stable facts or authority that can settle it, and the
common ambiguous signal that must not be promoted into a conclusion when one
is material. It does not invent a non-proof warning merely to fill the field,
and it does not store a selected answer, confidence score, project observation,
callable signature, or code example. Construction, supporting, and check
targets remain the only routes from the page to exact Marivo encoding. This is
private registry metadata, not a new public API or help target.

`next_targets` links the settled decision to existing exact constructors,
supporting builders, checks, or context builders. These are cross-links, not a
new discovery group. They must be a subset of the owning object contract's
construction, supporting, and check targets, and the renderer deduplicates them
before enforcing the existing navigation-page route budget.

`relationships` contains typed object edges plus stable explanations for facts
that cannot be represented as repeated inputs, for example:

- a field is owned by its `entity` ref;
- an Event's source Entity is inferred from the owner of `occurred_at`;
- a Relationship target is determined by its directed endpoints;
- a StateModel trigger may resolve through an exact ParticipantRoleHandle.

These facts are rendered as object relationships, not exposed as redundant
constructor parameters.

### Builder and check routing facts

Builder categories and check questions also remain typed semantic-owned data:

```python
@dataclass(frozen=True)
class SemanticBuilderGroup:
    id: str
    label: str
    summary: str
    members: tuple[LiveHelpTarget, ...]


@dataclass(frozen=True)
class SemanticCheckRoute:
    question: str
    targets: tuple[LiveHelpTarget, ...]
    proves: str
    does_not_prove: str
```

`SemanticNavigationTopic` points at registry-owned builder groups or check
routes through the native registry. The renderer does not reconstruct these
categories from canonical-id prefixes or callable return types.

### Placement and dependencies are separate

`AuthoringSourceContract` retains only placement and post-save identity facts:

```python
@dataclass(frozen=True)
class AuthoringSourceContract:
    placement_kind: AuthoringPlacementKind
    path_template: str
    catalog_collection: str
    canonical_identity_template: str
```

It no longer owns one kind-wide `prerequisite_targets` tuple. Constructor-level
input and supporting routes live on the exact capability descriptor;
object-level relationships live on `SemanticObjectContract`.

This prevents `count`, `aggregate`, `ratio`, `linear`, `cumulative`, and
`@metric` from inheriting one inaccurate Metric prerequisite set merely because
they produce the same ref kind.

### Registry-owned group membership and teaching order

The registry owns:

- root group ids, labels, order, and members;
- navigation-topic members;
- object-kind order;
- builder-family labels, order, and members;
- check-question labels, order, targets, proof, and non-proof summaries;
- object decision questions, determination guidance, determination bases,
  non-proof boundaries, and existing next-target cross-links;
- object construction modes and relationship edges.

The renderer contains no canonical-id membership tuples. It iterates immutable
registry views and applies only presentation and budget logic.

Every exact target has one discovery owner. Cross-links are directed edges, not
additional group memberships.

### Render-class budget ownership

The semantic capability model replaces its current two-way `root` versus
`focused` selection with five semantic page classes:

```python
SemanticHelpRenderClass = Literal[
    "root",
    "decision_hub",
    "navigation",
    "exact_contract",
    "current_briefing",
]


@dataclass(frozen=True)
class SemanticHelpRenderBudget:
    max_lines: int
    max_codepoints: int
    max_outgoing_routes: int
    max_examples_or_snippets: int
```

The semantic registry owns one immutable budget per semantic class and assigns
a class to each static descriptor. The semantic renderer selects
`current_briefing` only from the resolved runtime context. Separately, the
global help coordinator owns the matching `root` and `decision_hub` budget
records for its two pages. Each renderer counts structural routes and examples,
then passes the rendered text and selected limits to the existing neutral
`enforce_budget(...)` primitive. No renderer places numeric limits on
individual target ids. The neutral layer remains unaware of page-class names,
semantic object kinds, navigation membership, and authoring workflow.

### Implementation-to-help consistency

Help is a derived runtime view, not a second API specification. Authority is
split explicitly:

| Fact | Single authority | Help behavior |
|---|---|---|
| Public existence and callable identity | Exported installed Python object | Resolver must bind the exact public object |
| Parameter names, order, kinds, annotations, and defaults | Live callable signature and resolved type hints | Reflected at render time; never copied into renderer or navigation metadata |
| Return annotation and decorator product | Live callable plus explicit construction-mode contract | Reflected and checked against the registry output family |
| Intent, effects, cross-parameter constraints, placement, repairs | Exact semantic capability descriptor | Rendered from the descriptor |
| Object meaning, construction modes, relationships, catalog collection | `SemanticObjectContract` | Rendered from the object-kind descriptor |
| Material object decisions, determination criteria, and evidence limitations | `SemanticObjectContract.decisions` grounded in the owning normative object contract | Rendered only on the owning object-kind page; never converted into a project-specific answer |
| Root membership, teaching order, builder groups, and check routes | Semantic registry | Renderer only iterates registry views |
| Semantic render classes, assignments, and budget values | Semantic registry | Semantic renderer selects and enforces the descriptor or briefing class |
| Global root and authoring-hub budget values | Global help coordinator | Global renderer selects and enforces its owning page class |
| Hard line, codepoint, route, and example enforcement | Neutral live-help primitive | Receives explicit selected limits without learning page semantics |
| Current catalog or error facts | The concrete runtime value | Added only to the corresponding static contract |
| Final text layout | Renderer | Owns presentation only; it creates no API or topology facts |

Consistency does not mean generating business meaning from a Python signature
or requiring docstrings and help prose to be byte-identical. Reflectable
mechanical facts must be identical; non-reflectable semantic facts have one
registry owner and a normative semantic contract or executable behavioral
witness, as applicable. Site documentation and packaged skills route to those
live contracts rather than becoming additional parameter or topology
authorities.

Decision guidance is fail-closed. Registry content may state stable object
semantics, reliable determination criteria, and explicit non-proof boundaries.
It must not turn column names, physical types, samples, observed aggregations,
or other heuristics into reusable business meaning. When the object model
cannot supply an accurate general rule, the registry states that current
business authority is required instead of filling the gap with ambiguous
advice.

Consistency is enforced at three boundaries:

1. **Registry construction** performs cheap closed-world checks: unique ids,
   unique callable identities, resolvable internal edges, one discovery owner,
   output/ref-kind compatibility, and valid render-class assignment. Invalid
   registry state fails eagerly rather than producing partial help.
2. **Contract tests** independently enumerate public exports and live
   signatures, then compare them with registry descriptors. They detect a new
   export without help, a removed or renamed parameter with stale semantic
   metadata, an output-family mismatch, duplicate membership, dead routes, and
   stale catalog collection/ref-kind mappings.
3. **Rendered-surface tests** parse the output instead of trusting registry
   counts. Every advertised target is resolved again through public
   `marivo.help(...)`; reflected signatures are compared with
   `inspect.signature(...)`; examples are bound or executed in controlled
   fixtures; and all five render classes are checked against their own budgets.

Semantic input metadata that refers to a concrete parameter must carry that
parameter's exact name. Registry validation confirms the parameter still
exists and that required/optional claims agree with the live default. Broader
facts such as “requires a Metric dependency” may remain relationship metadata
only when no single parameter owns that meaning.

The shared private `AuthoringInputRequirement` therefore gains an exact
`parameter_names: tuple[str, ...]` field. An empty tuple is legal only for a
genuinely cross-parameter or object-level requirement; it cannot be used to
avoid binding ordinary input help to the implementation.

Output checking distinguishes two closed invocation shapes:

- `direct`: the reflected return annotation maps directly to `output_family`;
- `decorator`: the immediate return is `Callable[...]`, and that callable's
  reflected or explicitly registered product maps to `output_family`.

The exact capability descriptor gains
`invocation_shape: Literal["direct", "decorator"] = "direct"`;
`output_family` continues to name the final public product rather than the
intermediate decorator closure.

This prevents decorator constructors such as expression-body authoring from
being incorrectly compared as though the first call returned the authored ref
directly.

Renderer snapshots are secondary regression aids, not the consistency proof.
Tests must compare independent authorities so that implementation and expected
text cannot drift together while remaining green.

## Target Semantic Root

The semantic root should become a compact family index rather than rendering
every authoring primitive:

```text
marivo.semantic
<environment fingerprint>

Start:
  authoring     Build or change reusable semantic definitions.
  load          Load the current read-only semantic catalog.

Discover authoring contracts:
  objects       Semantic object kinds, relationships, and construction modes.
  builders      Nested values, local authoring values, and typed handles.
  checks        Inspection, readiness, preview, health, parity, and richness.

Current catalog:
  SemanticCatalog
  CatalogEntry

Call marivo.help("semantic.<target>") for one exact contract.
```

Exact callable leaves remain canonical. They are discoverable through one
navigation family instead of all being repeated at the root or in a flat global
inventory.

## Public Target Rules

- `semantic.objects.<kind>` is a canonical non-callable navigation target.
- `semantic.builders.<family>` is a canonical non-callable navigation target.
- `semantic.<canonical-id>` remains the canonical exact callable target.
- Receiver and factory methods use qualified canonical ids such as
  `semantic.SemanticCatalog.items`, `semantic.ref.entity`, and
  `semantic.source_check.not_null`; the public entrypoints remain
  `ms.SemanticCatalog.items`, `ms.ref.entity`, and
  `ms.source_check.not_null`.
- A callable object and its canonical string resolve to the same capability
  descriptor.
- The public `ms.ref` and `ms.source_check` namespace objects resolve to the
  same factory pages as `semantic.ref` and `semantic.source_check`.
- There is no `semantic.lifecycle` alias. Lifecycle authoring is discovered as
  `semantic.objects.state_model`; analysis lifecycle operations remain owned by
  `analysis.lifecycle`.
- There is no `semantic.ontology` alias. `semantic.authoring` routes to the
  independent canonical `ontology.authoring` target.
- Supporting types and errors remain focused leaves and do not enter root
  discovery merely because they are public.
- There is no global flat target inventory. Bounded authoring routes own
  discovery, while exact secondary leaves remain resolvable when already known
  or obtained from live state.
- Unknown or ambiguous targets fail with bounded canonical suggestions; they
  never resolve by surface order or alias fallback.

## Help And Runtime Ownership

### Static help

Static help owns installed API facts, semantic topology, object decision
checklists, stable determination criteria, and evidence limitations. It
performs no project load, connection, query, source mutation, object
construction, evidence interpretation, or project-specific semantic choice.

### Datasource evidence

Datasource inspection, optional bounded sampling, and governed raw SQL own
current physical observations. They may establish source mechanics such as
schema, partition behavior, types, or scoped value properties to the supported
extent. They do not own reusable business meaning or select a semantic
construction mode.

### Business authority and agent judgment

The user, an approved current project definition, or attributable
non-conflicting documentation or provenance owns reusable business meaning.
The agent combines that authority with necessary physical evidence, applies the
object-kind guidance, drafts the definition, and asks only the earliest
material unresolved question. Help never performs that interpretation on the
agent's behalf.

### Catalog objects

`SemanticCatalog`, typed collections, and entries own current compiled project
state. Their `.show()` methods expose bounded current facts. Their type and
instance help exposes only mechanically valid inspection and handoff routes.

### Results and contracts

Result `.show()` exposes bounded current state. `.contract()` is present only
where the result owns a complete set of mechanically valid continuations. This
design does not add a shared semantic-authoring lifecycle contract.

### Structured errors

Errors own concrete failure and repair facts derived from current state. A
repair may route to an object-kind page when the object choice is wrong, or an
exact callable leaf when the call shape is wrong.

### Packaged skill

The packaged `marivo-semantic` skill owns:

- deciding which evidence path is useful;
- selecting a coherent semantic slice;
- interpreting evidence;
- asking for unresolved business meaning;
- choosing when a targeted runtime probe is justified;
- closeout and analysis handoff.

It points to `semantic.authoring`, `semantic.objects`, `semantic.builders`, and
`semantic.checks`. It does not copy object lists, construction-mode tables,
signatures, builder inventories, check matrices, or error catalogs.

## Validation

### Registry invariants

- Every public semantic routine resolves to exactly one callable descriptor.
- Every public `ms.ref.<kind>` factory resolves through one exact
  `semantic.ref.<kind>` callable leaf.
- Every public `ms.source_check.<method>` factory resolves through one exact
  `semantic.source_check.<method>` callable leaf.
- The concrete `ms.ref` and `ms.source_check` namespace objects resolve to the
  same registry-owned factory contracts as their canonical strings.
- Every public semantic type resolves to exactly one type contract.
- Every source-authored ref constructor belongs to exactly one semantic object
  kind and one construction mode.
- Every construction-mode target produces the owning object's exact ref kind;
  nested parameter and local-value builders appear only as supporting targets.
- Every semantic object kind has exactly one `semantic.objects.<kind>` target.
- Object-kind membership equals the closed runtime `SemanticKind` set minus
  `datasource`; no current or newly exported semantic kind can bypass decision
  coverage.
- Every object-kind contract names its material decisions, and every decision
  has a non-empty question, determination rule, and closed determination basis.
  Guidance that relies on physical evidence also names the material signal that
  evidence does not establish; other decisions do not invent filler warnings.
- Every object kind in the object index covers the complete minimum decision
  scope assigned to it in this design and agrees with its owning normative
  object contract. Illustrative examples elsewhere in this document do not
  narrow this invariant.
- Every decision next target resolves canonically and belongs to the owning
  object's construction, supporting, or check targets. An empty next-target set
  is legal only when the guidance states accurately that the current object
  model cannot encode the decision.
- Decision guidance creates no additional target family and stores no selected
  answer, confidence, current project observation, signature, or example.
- Every builder and check target has exactly one discovery owner.
- Navigation groups contain only registered canonical targets.
- Every registered navigation member resolves independently through the public
  coordinator.
- No target is an alias, duplicate group member, or renderer-only shadow.
- Constructor dependencies agree with live parameter annotations and explicit
  inference rules.
- Every parameter-specific semantic input fact names a real live parameter and
  agrees with whether that parameter is required or optional.
- Reflected return annotations map to the registered output family through one
  closed type-family mapping; decorator factories validate the authored
  product separately from their immediate `Callable[...]` return.
- Catalog collection and ref kind agree with the catalog member contract.

### Reachability invariants

Build the help graph from registry-owned edges, independently of rendered text.
Starting from `authoring`:

- every semantic object kind is reachable;
- every public semantic constructor is reachable;
- every supporting builder is reachable;
- every public `ms.source_check.<method>` factory is reachable through
  `semantic.source_check`;
- every semantic check is reachable;
- `ontology.authoring` and `datasource.authoring` are reachable;
- no required leaf is more than four edges from the global authoring topic.

Parse every rendered navigation page, resolve every advertised target, and
assert that rendered reachability matches registry reachability.

### Render invariants

- The global root and authoring hub select their global-owned class; every
  semantic page in this design selects exactly one semantic-owned class.
- Each render class enforces independent line, codepoint, outgoing-route, and
  example/snippet limits.
- Root and navigation pages contain no expanded signatures or parameter tables.
- Object-kind pages render their bounded decision guidance inside the existing
  navigation page; no per-decision page or recursive expansion is introduced.
- Parent pages contain no recursively rendered child section; exact leaves do
  not repeat parent inventories.
- Callable leaves contain a live reflected signature, exact output, effects,
  constraints, and a minimal example.
- Type and callable equivalent forms render the same static contract.
- Instance enrichment is deterministic, bounded, and address-free.
- Error instances preserve all available structured diagnostics and repair.
- Overflow fails explicitly; no required section is silently truncated.
- The opt-in global target inventory is checked against its separate line and
  codepoint ceiling and contains canonical ids only.

### Implementation drift invariants

- Enumerate the closed runtime `SemanticKind` set independently and require one
  object-kind contract with decision coverage for every kind except
  `datasource`.
- Enumerate the intended public semantic callable surface independently from
  registry rows and require one exact descriptor per callable.
- Resolve every registered callable path and public entrypoint to the same
  callable identity.
- Compare every rendered signature with the installed callable signature.
- Prove that object construction modes, builder members, check routes, and root
  groups reference registered canonical targets only.
- Compare every object kind's rendered decision inventory with its owning
  normative object contract. For each kind with a plausible weak signal, add an
  adversarial assertion that the signal is presented only as evidence or a
  non-proof boundary, never as sufficient reusable business authority.
- Change one fixture signature, export, output annotation, group membership,
  and catalog/ref-kind mapping adversarially and assert that the corresponding
  drift check fails for the intended reason.
- Exercise every non-reflectable constraint, effect boundary, placement rule,
  and structured repair family with a behavior test that observes the claimed
  success or failure; registry prose and renderer snapshots alone are not
  evidence of implementation behavior.
- Do not approve literal snapshot updates unless these independent invariants
  also pass.

### Example validation

- Every callable example binds against the live signature.
- Every `ms.source_check.<method>` example constructs its exact closed
  `SourceCheck` variant; evaluating that value remains a separate
  `catalog.source_health(...)` behavior test.
- Source-authored declaration examples execute only inside a controlled loader
  fixture.
- Examples use declaration-returned refs or `ms.ref.<kind>(path)`, never bare
  semantic-id strings for object-to-object arguments.
- Direct-column and aggregate/count examples remain the default teaching path.
- Expression decorators are labeled and tested as escape hatches.
- At least one adversarial route per navigation family proves unknown-target
  suggestions, cross-surface qualification, and absence of aliases.

### Documentation and skill validation

- Current English and Chinese site documentation route through the same
  canonical topics.
- Site guides explain concepts and workflow but do not duplicate leaf parameter
  tables.
- The packaged semantic skill contains routing and policy only.
- The packaged semantic skill owns evidence interpretation and unresolved
  business questions; object-kind help owns the stable checklist and criteria
  without copying workflow prose into every page.
- Historical versioned documentation and historical design records remain
  unchanged unless a separate task explicitly widens scope.

## Delivery

### Slice 1: Native navigation models

- Add `SemanticNavigationTopic`, `ConstructionMode`,
  `SemanticObjectDecision`, and `SemanticObjectContract` to the semantic-owned
  capability model.
- Adapt the semantic registry to a closed native descriptor union.
- Add five semantic render classes and one semantic-owned budget record per
  class; assign every semantic descriptor to the appropriate class.
- Add global-coordinator-owned `root` and `decision_hub` budget records for
  `marivo.help()` and `marivo.help("authoring")`; reuse neutral enforcement.
- Keep the neutral resolver generic and unchanged in semantic meaning.
- Add registry validation before changing rendered output.

### Slice 2: Object graph and constructor relationships

- Register every object-kind page and construction mode.
- Register each object's material decisions, determination rules, determination
  basis, non-proof boundary, and exact next-target cross-links on that same
  object-kind contract. Cover every object kind in the minimum decision-scope
  table before rendering any kind as complete.
- Require every construction mode to return the owning object's exact ref kind;
  keep nested parameter and local-value builders in supporting-target edges.
- Register exact callable descriptors for every `ms.ref.<kind>` and
  `ms.source_check.<method>` factory method.
- Split constructor dependencies from source placement.
- Add exact supporting-builder edges.
- Add export, callable-identity, signature/dependency, output-family, and
  catalog-kind drift invariants.

### Slice 3: Progressive rendering and reachability

- Replace renderer-local authoring route tuples with registry-owned navigation.
- Render the global `authoring` topic as a decision hub under the shared budget
  while keeping its surface routes global-coordinator-owned.
- Compact the semantic root to `authoring`, `objects`, `builders`, `checks`,
  load, and current-catalog routes.
- Render object-kind, builder, and check pages.
- Render decision guidance as a bounded section of each object-kind page; do
  not add a decision-help route or render class.
- Add registry and rendered root-to-leaf reachability tests.
- Add per-render-class line, codepoint, route, example, and non-recursion tests.

### Slice 4: Dynamic repair and documentation alignment

- Route structured repairs to the narrowest object-kind or callable target.
- Verify object, ref-factory, source-check-factory, type, result, and error help
  matrices.
- Update the packaged semantic skill and current English/Chinese site routes.
- Run focused help/registry tests, the full repository gates, examples, and
  site verification/build.

## Acceptance Criteria

- A cold agent starting only from `marivo.help()` can identify every semantic
  object kind and explain its bounded meaning without reading source or site
  documentation.
- After selecting an object kind, the same object page tells the agent which
  material semantic choices must be settled, which evidence or authority can
  settle them, and which common signals do not establish them.
- This decision coverage applies uniformly to every object kind in
  `semantic.objects` and agrees with each kind's current normative object
  contract.
- Every material decision routes to its existing constructor, supporting
  builder, check, or context contract when Marivo can encode it; unsupported
  meaning is stated explicitly rather than represented by an invented API.
- No object page promotes a name, type, sample, observed pattern, historical
  query, or other weak signal into business meaning. Object-specific non-proof
  warnings are included only where accurate and material.
- Given an intent such as “count Entity rows”, “join two Entities”, “model an
  Event”, or “define a normative lifecycle”, the agent reaches the correct
  exact constructor leaf without already knowing its function name.
- For objects with multiple construction modes, help distinguishes the default,
  alternatives, and escape hatch without duplicating signatures.
- Nested parameter and local-value builders are never presented as alternative
  ways to construct the owning semantic ref.
- Supporting builders are discoverable by the parameter problem they solve.
- Every `ms.source_check.<method>` factory has an exact callable leaf reachable
  through `semantic.source_check`, and help does not present construction of a
  check value as evidence that the source satisfies it.
- Inspection and check help states what each operation proves and what it does
  not prove.
- Every exact callable leaf remains self-contained and mechanically aligned
  with the installed runtime.
- No help call recursively expands another target or exceeds its render-class
  context budget; overflow never silently removes required information.
- Context budgeting remains one hard budget per rendered help type; no
  cumulative journey budget, tokenizer-specific counter, decision-help render
  class, or second enforcement mechanism is introduced.
- A public export, signature, default, return type, object/ref kind, catalog
  collection, or registry edge change cannot leave stale help while the
  contract tests remain green.
- Every advertised target is canonical, independently resolvable, bounded, and
  reachable from global authoring in at most four edges.
- Registry and rendered reachability are tested independently.
- The renderer contains no shadow membership list for roots, object kinds,
  builders, or checks.
- No new public help coordinator, workflow state, verification stage, alias, or
  compatibility path is introduced.
