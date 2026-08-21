# Agent-Native Semantic Authoring Simplification Design

Status: proposed

Date: 2026-08-21

## Relationship To Existing Designs

This design replaces the canonical semantic-authoring workflow established by
[`2026-07-10-authoring-evidence-snapshot-redesign.md`](2026-07-10-authoring-evidence-snapshot-redesign.md)
and the one-object routing policy retained by
[`2026-07-13-marivo-semantic-boundary-state-router-design.md`](2026-07-13-marivo-semantic-boundary-state-router-design.md).
It supersedes their requirements for:

- one `DiscoverySnapshot` as the canonical semantic-authoring evidence token;
- semantic-shaped snapshot projections as the required exploration path;
- authoring exactly one semantic object before every reload and validation;
- `catalog.verify(...)` as a distinct post-load checkpoint;
- snapshot-backed preview evidence as a semantic-readiness concern;
- result-local `semantic.verified`, `semantic.previewed`, and `semantic.ready`
  lifecycle states.

The state-model removal is repository-wide for the shared authoring contract.
Datasource and ontology operations remain public, but their capabilities are
described directly through live help, effects, inputs, outputs, and typed
repairs rather than through `datasource.*`, `source.*`, `scope.*`,
`evidence.*`, or `ontology.loaded` lifecycle states. This is a deliberate
de-stating of all three tracks, not a semantic-only shrink of
`AuthoringStateId`.

It retains the terminal data boundary from
[`2026-07-14-terminal-raw-sql-boundary-design.md`](2026-07-14-terminal-raw-sql-boundary-design.md):
arbitrary SQL results cannot be promoted into canonical Marivo analysis
artifacts. This design changes how the authoring skill routes to `md.raw_sql(...)`,
not the one-way typed-analysis boundary.

Historical design records remain unchanged. Implementation must update the
active runtime specs, live help, packaged skills, tests, and latest site
documentation in one clean cutover.

## Summary

Marivo currently treats semantic authoring as an evidence-snapshot state
machine:

```text
inspect -> scope -> snapshot -> semantic projections -> settle
-> author one object -> load -> verify -> preview -> readiness -> analysis
```

The workflow protects data access, but it also turns optional exploration and
runtime smoke checks into a universal certification ceremony. That slows agents,
prevents them from designing a coherent semantic slice together, and gives a
bounded discovery sample more authority than it can support.

The target workflow is agent-native:

```text
browse current definitions and authoritative schema
-> explore with inspection, optional bounded sampling, and governed raw SQL
-> author one coherent semantic slice
-> load once and repair structural failures
-> run scoped semantic readiness
-> run only the runtime probes justified by current risk
-> continue analysis with the exact requested refs or runtime expressions
```

Marivo owns safe access, stable semantic contracts, loading, compilation,
execution, and structured failures. The packaged skill owns exploration
strategy, evidence selection, checkpoint size, and business-judgment routing.
An independent source-health mechanism owns datasource and data drift. A
`DiscoverySnapshot` is optional authoring evidence, never proof that a semantic
definition remains current.

## Problem

### The canonical route optimizes for compliance rather than learning

The current packaged skill requires an agent to author one object, reload the
catalog, navigate to the exact entry, verify it, preview it, and run readiness
before advancing to a dependent object. This is stricter than the runtime:
after a successful load, a catalog entry exposes verify, preview, and readiness
as independent continuations.

The rule has three costs:

- every dimension, time dimension, measure, and metric adds another file-edit,
  load, lookup, render, and validation cycle;
- an agent cannot jointly settle names, dependencies, decomposition, time axes,
  and business context for one coherent entity slice;
- repeated local passes create the appearance of stronger verification even
  when they repeat facts already established by project loading.

### Per-object verification does not add a distinct proof

`ms.load()` already assembles the project and validates declarations,
dependencies, typed references, expression bindings, cycles, and structural
contracts. For a successfully loaded project, `catalog.verify(ref)` primarily
confirms that the exact ref exists at the requested kind and returns a static
pass. It does not query the datasource, compile an operation-specific analysis,
or establish runtime executability.

The separate verify stage therefore adds API surface and workflow latency
without representing a durable or independently meaningful state.

### Discovery evidence cannot own semantic validity

A `DiscoverySnapshot` records observations for one datasource definition,
source, schema fingerprint, explicit scope, selected column set, and acquisition
time. It may contain bounded profiles and retained values. It cannot prove:

- that the physical schema is still current after acquisition;
- that permissions, routing, or backend capabilities still work;
- that a key remains unique outside the observed scope;
- that freshness, null, enum, or relationship expectations still hold;
- that the business meaning represented by a semantic object remains approved;
- that a later analysis operation with different time scope, grain,
  dimensions, filters, or fold policy will execute successfully.

Snapshot age and identity can describe evidence. They cannot manage ongoing
semantic invalidation. Keeping snapshot and preview absence in readiness, even
as advisory issues, preserves the wrong ownership and adds noise to the final
semantic handoff.

### Semantic-shaped projections constrain exploration without deciding meaning

`snapshot.entity(...)`, `dimensions(...)`, `time_dimensions(...)`,
`measures(...)`, and `relationships(...)` expose fixed profiles and fixed lists
of unresolved judgments. They intentionally do not choose primary keys,
business definitions, units, aggregation, additivity, time axes, or
cardinality.

That restraint is correct, but it also means most of the projection taxonomy is
workflow guidance encoded as runtime types. A capable agent can select more
relevant evidence through authoritative schema inspection and explicit SQL,
while a skill can teach stable cautions such as “sampled uniqueness is not
business-key authority.” Freezing these questions into Marivo narrows the
exploration space and creates a large contract surface without closing a
runtime correctness gap.

### `md.raw_sql(...)` is safer and more useful than its current routing implies

The current raw-SQL boundary already provides the important runtime controls:
one statement, read-only enforcement, positive timeout, bounded returned rows,
active truncation disclosure, backend-specific errors, and no typed-analysis
promotion.

Calling it only a terminal diagnostic escape makes agents exhaust narrower
discovery APIs before using the tool that can directly answer source-specific
questions. SQL results must remain terminal data, but the agent must be allowed
to read them as evidence, revise a hypothesis, and author an ordinary Python
semantic definition from the resulting understanding.

### Readiness mixes independent concerns

The current readiness surface combines several different questions:

- is the semantic dependency closure structurally legal;
- is business context rich enough for an agent;
- does old discovery or preview evidence exist;
- does a data-backed semantic artifact have a current certified materialization;
- is a specific future analysis operation executable.

These questions have different authorities and invalidation rules. One report
should not make a discovery snapshot look like semantic certification or make
missing descriptive enrichment look like a broken executable definition.

## Goals

- Let an agent explore enough of the physical and business space before
  committing to a semantic shape.
- Replace mandatory per-object checkpoints with coherent semantic-slice
  checkpoints.
- Keep Python and Git as the only source of truth for reusable semantic
  definitions.
- Keep datasource access read-only, bounded in returned rows, timeout-governed,
  and explicit about potentially unbounded backend work.
- Keep typed refs, source bindings, dependency validation, expression
  validation, and structured repairs in Marivo.
- Make semantic readiness independent of ordinary discovery snapshots and
  preview-check persistence.
- Separate source/data drift from authoring and semantic-definition readiness.
- Use runtime preview or analysis execution only where it answers a concrete
  risk that static validation cannot settle.
- Preserve business authority without adding approval ceremony: unresolved
  business-caliber choices stop before first typed analysis use, while meaning
  already established by the current user, an approved definition, or
  attributable non-conflicting project documentation needs no second approval.
- Remove superseded public paths and state machinery rather than retaining
  aliases, dual-read behavior, or compatibility gates.

## Non-Goals

- Automatically infer or author a semantic layer from schema or SQL results.
- Turn arbitrary SQL, pandas, or Ibis results into canonical Marivo analysis
  artifacts.
- Guarantee that a returned-row limit bounds scanned bytes or backend work.
- Build a general-purpose profiler, data-quality platform, scheduler, daemon,
  or schema registry as part of the authoring simplification.
- Treat observed uniqueness, physical types, value frequencies, or source
  comments as final business authority.
- Make every semantic object executable under every possible analysis shape.
- Add a new authoring wizard, plan object, approval receipt, workflow lease, or
  persisted agent-decision ledger.
- Preserve retired authoring lifecycle APIs for compatibility.

## Design Principles

1. **Exploration is not certification.** Evidence helps an agent decide what to
   author; it does not certify that the authored meaning remains valid.
2. **Checkpoint coherent work.** Validate a dependency-coherent slice, not every
   declaration and not an unbounded domain rewrite.
3. **One owner per fact.** Marivo owns mechanical truth, the skill owns method,
   the agent owns technical judgment, and an accountable human or approved
   project definition owns business meaning.
4. **Static means static.** Semantic readiness does not consult ordinary
   snapshots, preview history, or their timestamps.
5. **Execution proof is shaped.** A runtime probe proves only the exact scope and
   operation it executed.
6. **Governed SQL is a normal exploration tool.** Safety controls stay in the
   library; query strategy stays in the skill and agent.
7. **Terminal data can inform authoring without becoming typed analysis.** The
   boundary prohibits mechanical promotion, not agent learning.
8. **Current contracts replace compatibility.** Remove old lifecycle states,
   evidence gates, and aliases in one coordinated cutover.

## Target Responsibility Model

### Marivo runtime

Marivo owns:

- datasource declarations, typed source descriptors, registration, connection
  tests, and credential-reference rules;
- authoritative metadata and schema inspection to the extent supported by the
  backend;
- read-only execution, enforceable timeout, returned-row bounds, truncation,
  and effect disclosure;
- typed physical-column bindings and reusable semantic constructors;
- exact refs, dependency graphs, loader validation, expression validation, and
  catalog navigation;
- semantic-static readiness for an exact requested closure;
- explicit runtime preview and ordinary analysis execution;
- structured errors and repairs based on current runtime state.

### `marivo-semantic` skill

The packaged skill owns:

- deciding whether the current task needs only schema inspection, bounded
  sampling, raw SQL, semantic authoring, or runtime validation;
- selecting the smallest useful evidence and avoiding repetitive queries;
- using SQL to test source-specific hypotheses when fixed inspection output is
  insufficient;
- choosing a coherent semantic checkpoint;
- deciding which objects are high-risk enough to preview before handoff;
- distinguishing provisional technical conclusions from business-authorized
  meaning;
- asking the earliest unresolved question only when its answer changes the
  authored contract;
- returning to analysis as soon as the requested semantic roots are usable.

The skill does not duplicate signatures, result fields, backend catalogs, or
error taxonomies from live help.

### Agent

The agent owns:

- interpreting schema, SQL, existing definitions, project documentation, and
  user context together;
- drafting explicit Python definitions;
- choosing names, dependency layout, metric decomposition, and evidence queries
  within the available authority;
- disclosing provisional assumptions and residual runtime risk.

### Business meaning and first-use authority

An agent may explore freely and may draft a coherent semantic slice before all
business-caliber questions are settled. Drafting does not grant analysis
authority.

Before the first typed analysis use of a new or changed semantic definition,
every unresolved choice that changes reusable business meaning must be settled
by at least one current authority:

- the user's explicit request or answer in the current task;
- an approved existing project definition;
- attributable project documentation or source provenance that is sufficiently
  explicit and does not conflict with another current authority.

Examples include denominators, inclusion and exclusion policy, failure
handling, unit, aggregation, additivity, business time-axis choice, and metric
caliber. When no current authority settles the earliest such choice, the agent
asks one evidence-grounded question and stops before typed analysis handoff.

This is not a blanket second approval step for every authored metric. If the
current request or approved project state already supplies the meaning, the
agent proceeds without asking the user to reconfirm it. Marivo does not persist
an approval token, acknowledgement, decision record, or handoff receipt.

Physical and execution uncertainty follows a different boundary. It may be
disclosed, checked through source health or a targeted runtime probe, or left as
explicit residual risk when it does not change the reusable business contract.

### Source health and drift

An independent source-health mechanism owns current external validity:

- datasource reachability and permissions;
- current schema and physical-column compatibility;
- declared freshness, null, uniqueness, enum, and relationship expectations;
- mapping a failed source contract to affected semantic refs;
- recording when the check ran and what current source identity it observed.

This mechanism may be invoked explicitly, by CI, or by a scheduler. This design
does not require a resident process or choose the final public command name.
Implementation planning must first reuse the smallest suitable existing seams,
including datasource test/inspection and `marivo doctor --connect`, before
adding a new public API.

## Target Authoring Flow

### 1. Enter from current state

Load current datasource and semantic catalogs before mutation. Environment
fingerprinting and focused help remain available, but the skill must not block
metadata inspection merely because an accountable owner has not yet been
identified. Owner and business-definition questions become mandatory only
when the answer changes a reusable semantic declaration or its promotion
caliber.

```python
import marivo.datasource as md
import marivo.semantic as ms

datasources = md.load()
catalog = ms.load()
```

### 2. Establish authoritative physical facts

Use datasource registration, connection testing, and `md.inspect(...)` for
column names, physical types, source identity, partition facts, and backend
capabilities. Inspection remains the preferred schema path because these facts
should not require a user-data scan.

When backend metadata is incomplete, Marivo reports unknown or unsupported
facts explicitly. It does not replace an unknown with a discovery guess.

### 3. Explore according to the question

The agent chooses among three evidence paths:

- inspection only when schema and existing project context are sufficient;
- optional explicitly scoped sampling when retained rows or generic profiles
  directly answer the current question;
- `md.raw_sql(...)` for source-specific metadata, distributions, joins,
  conditional logic, comparison with existing SQL, or other bounded scratch
  investigation.

These paths are composable and repeatable within the caller's data-access
budget. There is no mandatory `inspect -> snapshot -> every projection` ladder.

`md.raw_sql(...)` retains its terminal result contract. The result cannot be
passed to `session.observe(...)`, promoted to a `MetricFrame`, or persisted as
canonical analysis. The agent may use the observed facts and disclosed
assumptions to write or revise semantic Python.

### 4. Author one coherent semantic slice

A semantic slice is the smallest dependency-coherent set that can be reviewed
and loaded meaningfully. Typical slices include:

- one entity with its direct dimensions, time dimensions, and measures;
- one entity's base metrics after its direct fields are stable;
- one relationship plus the exact participating entity fields;
- one cross-entity or derived metric plus any newly required reusable
  components;
- one event or state model with its exact semantic dependencies.

A slice is not required to contain only one object. It also must not expand to
an unrelated domain-wide rewrite. The agent may use a smaller checkpoint when
an individual declaration is unusually uncertain.

### 5. Load once and repair structural failures

`ms.load()` is the authoritative static validation event for authored source.
It evaluates the current project and fails closed on invalid organization,
duplicate identity, unresolved refs, type mismatches, illegal expression
bindings, invalid decomposition, and cycles.

After a successful load, exact identity is confirmed with ordinary catalog
navigation such as `catalog.require(ref)`. There is no separate
`catalog.verify(ref)` checkpoint and no `semantic.verified` state.

### 6. Run scoped semantic readiness

`catalog.readiness(refs=[...])` remains an explicit preflight over the exact
requested roots and their governed semantic dependency closures. It answers:

> Is the current semantic definition structurally legal and internally usable
> as an input to the relevant Marivo surface?

It may block on semantic facts such as:

- unknown requested refs;
- invalid metric or runtime-expression graphs;
- unsupported cross-datasource execution requirements;
- impossible fold or temporal declarations;
- state or event declarations with no legal interpretation;
- missing certified semantic artifacts whose contents are part of the semantic
  object itself.

It does not read or report:

- ordinary `DiscoverySnapshot` presence, age, cache status, or identity;
- persisted preview-check presence or age;
- inferred physical types recovered only from an old preview;
- generic source freshness or schema drift;
- whether every possible future analysis operation will execute.

`ai_context.business_definition` and `ai_context.guardrails` remain visible in
catalog details and advisory richness/governance reporting. Their absence does
not make an otherwise legal definition fail semantic-static readiness. A
publication or organizational governance policy may require them separately.

`ReadinessReport.analysis_ready_inputs` remains a convenience projection of
the exact requested roots that contain no blocker. It is not a validation token,
receipt, or new runtime authority; ordinary current refs and runtime expressions
remain the analysis inputs.

It becomes the only ready-input projection. Remove
`ReadinessReport.analysis_ready_refs`, which is a refs-only compatibility view
of the same roots, rather than retaining two fields that can drift. Remove
`preview_required_refs` as well: it exists to drive snapshot-backed preview
repair, which no longer belongs to readiness. A caller that chooses a runtime
probe passes its selected current roots directly to `preview_many(...)`.

### 7. Run targeted runtime probes

Runtime validation is selected by risk, not object count.

High-value probes include:

- a time dimension whose cast, parse, or timezone behavior is uncertain;
- a relationship whose keys or backend join behavior need a smoke test;
- a metric with non-trivial expression materialization;
- a cross-entity execution plan;
- a backend-specific feature or permission boundary;
- an exact analysis operation whose grain, filters, fold, or time scope matters.

`catalog.preview(...)` and `preview_many(...)` remain bounded runtime smoke
tools, but they no longer require a `DiscoverySnapshot` as certification
authority. Their target contract accepts an explicit scope, or exact
entity-to-scope bindings for a multi-entity object, and executes the current
semantic definition against the current datasource state.

The implementation plan must reuse the existing `AuthoringScope` values and
must not introduce a second scope language. The exact parameter spelling is
settled during implementation against the current catalog surface. No
snapshot-backed compatibility overload is retained after cutover.

For metrics, a representative `session.observe(...)` is often a stronger probe
than an approximate preview because it validates the actual requested analysis
shape. Neither probe becomes a universal readiness prerequisite.

### 8. Continue analysis

Once the requested refs or runtime expressions have no semantic blocker, the
agent applies the first-use authority rule above. Unresolved business-caliber
meaning stops the handoff; already-authorized meaning does not require another
approval turn. Warnings, provisional physical or execution assumptions,
skipped runtime probes, and source-health state remain explicit, but the
workflow does not require a separate handoff object.

## Discovery Snapshot Target Contract

`SourceInspection.sample(...)` remains available when one explicitly scoped
bounded acquisition is useful. The returned value is evidence, not a lifecycle
state or readiness token.

The retained contract includes:

- datasource, source, scope, selected columns, and acquisition identity;
- schema fingerprint observed at acquisition time;
- exact returned-row and scope-exhaustion facts;
- generic per-column profiles already captured by the acquisition;
- explicit memory-only versus persisted-value behavior;
- cache identity and staleness disclosure;
- no hidden follow-up query from a local accessor.

The following semantic-shaped public projection family is removed:

```text
DiscoverySnapshot.entity(...)
DiscoverySnapshot.dimensions(...)
DiscoverySnapshot.time_dimensions(...)
DiscoverySnapshot.measures(...)
DiscoverySnapshot.relationships(...)
```

`DiscoverySnapshot.values(...)` is also removed as a semantic-specific
continuation. Generic retained values and profiles may remain directly readable
from the snapshot's structured fields when the caller explicitly chose to
retain them. The skill teaches how to interpret those observations and when to
use a new scoped SQL query instead.

Removing these methods also removes the frozen
`AuthoringJudgmentRequirement` taxonomy attached to their results. Business
judgment remains a skill and user boundary rather than a runtime result family.

Snapshot storage may remain as an internal query-reuse optimization for an
exact acquisition identity. No readiness or semantic validity result reads it.

## Runtime Preview And Certified Semantic Artifacts

Ordinary preview evidence is ephemeral. A successful preview reports exactly
what it executed, with current scope, backend, types, rows, warnings, and
approximation policy. It is not persisted as a readiness checkpoint and does
not create a `semantic.previewed` state.

Some semantic object families contain governed data as part of their runtime
meaning, including period calendars, temporal sets, and work schedules. Their
certified materializations are not ordinary discovery evidence. They retain:

- an object-family-specific artifact schema;
- semantic-definition and dependency identity;
- declared coverage and completeness rules;
- integrity validation and current/stale/invalid status;
- exact reconstruction for consuming analysis operations.

These artifacts may remain semantic blockers when the requested object cannot
operate without them. Their lifecycle must not be expressed as
`DiscoverySnapshot` freshness or generic preview history. Acquisition may reuse
a safe datasource read internally, but certification produces the dedicated
semantic artifact and thereafter follows its own identity.

Readiness issue names use artifact vocabulary after cutover:

```text
period_calendar_artifact_missing | stale | invalid
temporal_set_artifact_missing | stale | invalid
work_schedule_artifact_missing | stale | invalid
```

The old `*_snapshot_*` readiness issue names are removed. Internal storage
types may retain snapshot terminology only where it denotes an immutable exact
capture rather than ordinary discovery evidence; public readiness and repair
language must say certified artifact.

## Live Help And Contract Simplification

Live help remains the source of truth for callable signatures, effects,
constraints, examples, and structured repair targets. The authoring lifecycle
state graph is removed across datasource, semantic, and ontology. Registration,
inspection, sampling, catalog loading, and ontology loading remain capabilities;
they no longer claim that a shared runtime workflow state has been produced.

Retain:

- `AuthoringEffects` or its existing shared effect representation;
- concrete data-access, connection, mutation, scope, timeout, and plaintext
  persistence disclosure;
- input-family facts that help an agent call the current function;
- typed structured repairs grounded in current state;
- result `.show()` and bounded structured fields.

Remove:

- `AuthoringStateId` and `AuthoringStateRef`;
- `AuthoringTransition`, `TransitionKind`, state normalization, and transition
  rendering used only by the shared lifecycle graph;
- `required_states` and `produced_state` lifecycle claims;
- datasource, source, scope, evidence, semantic, and ontology state builders;
- `semantic.loaded`, `semantic.verified`, `semantic.previewed`, and
  `semantic.ready`, together with every datasource/source/evidence and
  `ontology.loaded` value, as public state transitions;
- generic authoring transition contracts whose only purpose is to restate live
  help;
- `AuthoringJudgmentRequirement` and its rendering;
- persisted verify/preview/readiness checkpoint language.

Remove state-only literals and fields that become dead with this cutover,
including `TransitionKind."verify"`, `RepairKind."reverify"`,
`RepairKind."repreview"`, `EffectFlag."requires_existing_snapshot_binding"`,
and preview coverage `snapshot_ids` / `cache_status`. Retain a repair kind only
when a current public operation still has that distinct recovery action; do not
preserve a literal for compatibility.

An object may expose `.contract()` only when it has object-local facts or a
continuation that cannot be reconstructed from the callable's live help. The
implementation must not replace the deleted state model with another generic
workflow graph.

## `catalog.verify(...)` Removal

Remove `catalog.verify(...)` and `VerifyResult` from the public semantic
surface. Do not add `verify_many(...)`.

Their target replacements are existing responsibilities:

- `ms.load()` for project-level static validation;
- `catalog.require(ref)` for exact current membership and kind;
- `catalog.readiness(refs=[...])` for scoped dependency-closure preflight;
- `catalog.preview(...)`, `preview_many(...)`, or an actual analysis operation
  for selected runtime proof.

Errors and help must point directly to the owner that can repair the failed
fact. No deprecated alias or compatibility result is retained.

## Readiness Target Contract

Readiness remains query-free and side-effect-free. It is deterministic for the
same loaded semantic definitions and dedicated certified semantic artifacts.
Ordinary datasource evidence does not affect the result.

Issue ownership is:

| Concern | Target owner | Readiness effect |
| --- | --- | --- |
| unresolved ref, graph, type, cycle, illegal decomposition | semantic load/readiness | blocker |
| unsupported cross-datasource plan known from definitions | semantic readiness | blocker |
| impossible fold, event, state, or temporal contract | semantic readiness | blocker |
| missing required certified calendar/set/schedule artifact | object-specific certification | blocker for affected roots |
| missing business definition or guardrails | richness/governance | non-blocking |
| missing/stale discovery snapshot | authoring evidence | absent from readiness |
| missing/stale preview check | runtime probe history | absent from readiness |
| schema, permission, freshness, null, enum, uniqueness, cardinality drift | source health | absent from readiness |
| operation-specific grain, filter, time-scope, fold, and backend execution | consuming analysis call | absent from semantic-static readiness |
| provenance SQL parity | explicit parity diagnostic | warning outside readiness unless a separate caller policy requires it |

Certified-artifact blockers use `*_artifact_missing`, `*_artifact_stale`, and
`*_artifact_invalid` issue kinds. Ordinary discovery snapshots retain no
readiness issue kind.

Readiness does not silently invoke source health, preview, parity, or analysis.
Those checks remain explicit and independently reportable.

## Source Health Boundary

Source health is separate because it has a different clock and authority from
semantic source code.

A minimal source-health result must identify:

- checked datasource and source identities;
- current semantic refs affected by those sources;
- declared checks that actually ran;
- observed schema and capability identity;
- current, failed, unavailable, or unknown status per check;
- check time and concrete repair direction;
- whether user data was queried and under what scope.

It must not infer undeclared business expectations. A uniqueness, freshness,
null, enum, or relationship check exists only when the project explicitly
declares the expectation or an accountable external contract supplies it.

The first implementation should be the smallest explicit command or library
call that composes existing datasource inspection and tests. Scheduling and
history can be added only after a real consumer requires them. Semantic
authoring and readiness do not wait for that future infrastructure.

## Packaged Skill Target

The target `marivo-semantic` skill is shorter and more permissive:

1. read the current project and exact task;
2. inspect authoritative schema before asking for physical facts;
3. choose inspection, optional scoped sampling, or governed SQL based on the
   unresolved question;
4. preserve read-only, timeout, returned-row, truncation, and caller-budget
   boundaries;
5. author a coherent semantic slice;
6. load once and repair structural errors;
7. run scoped readiness for the requested roots;
8. choose targeted preview or `observe(...)` only when it answers a concrete
   runtime risk;
9. before first typed analysis use, ask a user or business owner only when
   unresolved meaning changes the reusable contract and no current authority
   already settles it;
10. return to analysis promptly.

The skill no longer requires:

- accountable-owner confirmation before metadata inspection;
- one snapshot or every semantic-shaped projection;
- one-object authoring;
- verify-before-preview;
- preview-before-readiness;
- snapshot/preview repair before authoring closeout;
- raw SQL only after fixed discovery has failed;
- an `analysis_ready_inputs` handoff ritual beyond using the current result.

## Delivery Milestones

The design is delivered as three ordered milestones. Each milestone owns one
complete behavioral boundary, can be reviewed and verified without relying on
unfinished work from the next milestone, and leaves the repository on one
current contract. The sequence is intentional: first simplify how an agent
authors, then separate runtime evidence from semantic-static readiness, and
finally add the independent owner for source drift and close the full journey.

| Milestone | Owned boundary | Independently observable outcome |
| --- | --- | --- |
| M1. Coherent-slice authoring | authoring workflow, validation, discovery guidance | an agent can author a coherent semantic slice and validate it once without verify or lifecycle state machinery |
| M2. Snapshot-independent readiness | preview, readiness, ready-input projection, certified-artifact naming | readiness depends only on the current semantic project while preview runs from explicit scope |
| M3. Source health and end-to-end closeout | source drift, declared data checks, final cross-surface convergence | source validity has an explicit owner and the complete agent journey works without hidden snapshot authority |

Milestones may land only in this order. "Independent" means each milestone has
its own complete acceptance proof, not that later milestones can be
implemented against the pre-M1 contract. Within a milestone, removal and
replacement land atomically: no legacy alias, dual read, compatibility
adapter, or temporary public workflow is introduced.

### Milestone 1: Coherent-Slice Authoring Without Workflow State

**Outcome.** An agent can inspect current sources, use the governed exploration
tool appropriate to the question, author all mutually dependent objects in one
coherent semantic slice, and use one `ms.load()` as the project-level static
validation event.

**Implementation scope.**

- change the packaged `marivo-semantic` guidance and live help from
  one-object-at-a-time authoring to coherent-slice authoring;
- make `ms.load()` plus catalog lookup and scoped readiness the only current
  validation path, and remove `catalog.verify(...)`, `VerifyResult`, their
  exports, descriptors, repairs, tests, and documentation;
- remove the shared public authoring lifecycle graph across datasource,
  semantic, and ontology, including state IDs, state refs, transitions,
  transition kinds, normalization, and state rendering;
- retain callable operations, effect disclosure, input facts, structured
  errors, and typed repairs without presenting them as a workflow state
  machine;
- remove semantic-shaped discovery projections and
  `AuthoringJudgmentRequirement`; retain discovery snapshots only as optional
  bounded rows, profiles, source evidence, and cache identity;
- teach `md.raw_sql(...)` as a normal governed exploration option while
  retaining its read-only, bounded, disclosed, and terminal contract;
- encode the business first-use boundary: unresolved business-caliber meaning
  stops before typed analysis use, while user input, an approved project, or
  authoritative documentation already establishing that meaning does not
  trigger redundant approval.

**Deliberately unchanged in this milestone.** Readiness issue ownership,
snapshot-bound preview input, persisted preview evidence, and ready-input
projection fields remain on their current pre-M2 contract. Any internal caller
that previously depended on `verify` is adapted to the loaded catalog, but M1
does not introduce a second preview or readiness path. This keeps the
milestone narrow without claiming that the final snapshot-independent contract
already exists.

**Independent acceptance proof.**

1. A focused agent journey authors an entity, dimensions, a time dimension,
   measures, and mutually dependent base metrics before its next load.
2. One `ms.load()` reports all structural errors for that slice; after repair,
   `catalog.require(...)` resolves every authored ref without a verify call.
3. Public imports, registry descriptors, live help, repairs, and introspection
   contain no verify result, semantic discovery projection, judgment
   requirement, or lifecycle-state symbol.
4. Datasource and ontology help still expose their retained operations and
   effects after shared state removal.
5. Raw SQL safety tests remain green, and skill/help acceptance shows it as an
   ordinary exploration choice whose output cannot enter typed analysis.
6. A first-use test stops on genuinely unresolved business meaning, while an
   already-authorized definition proceeds without a redundant confirmation.

**Milestone gate.** Run focused authoring, help, export, datasource, ontology,
and raw-SQL tests, followed by `make check`, both site gates, and
`git diff --check`. Active skill, help, `agent-guide.md`, and latest site pages
touched by M1 must describe the M1 contract before it lands.

### Milestone 2: Snapshot-Independent Preview And Readiness

**Outcome.** Semantic-static readiness is a pure judgment over the current
loaded semantic project and requested dependency closure. Runtime preview is
an explicit, non-authoritative probe that does not require or create a
discovery checkpoint.

**Implementation scope.**

- replace snapshot-bound preview input with one explicit closed scope shape,
  including exact entity-to-scope mapping when more than one source scope is
  involved;
- stop persisting ordinary preview checks and remove readiness lookup of
  preview history;
- remove `snapshot_missing` and `runtime_preview_missing` from readiness,
  together with their repairs, serialization, rendering, help, and tests;
- remove `analysis_ready_refs` and `preview_required_refs`, leaving
  `analysis_ready_inputs` as the sole ready-input projection;
- move missing business definitions and guardrails to richness/governance
  rather than semantic-static readiness;
- retain dedicated certification for period calendars, temporal sets, work
  schedules, and similar data-backed semantic objects, but rename their issue
  families to `*_artifact_missing`, `*_artifact_stale`, and
  `*_artifact_invalid`;
- remove dead snapshot/preview lifecycle fields and actions, including
  `repreview`, `requires_existing_snapshot_binding`, preview `snapshot_ids`,
  and preview `cache_status`;
- keep operation-specific execution, grain, filter, time-scope, fold, and
  backend failures owned by preview or the consuming analysis call.

**Deliberately unchanged in this milestone.** M2 does not add a source-health
scheduler, infer data-quality expectations, or make connectivity and drift
part of readiness. Existing explicit datasource inspection and test paths
remain available until M3 composes the smallest dedicated source-health
entrypoint.

**Independent acceptance proof.**

1. Identical semantic definitions produce identical readiness status, issues,
   and ready inputs with no discovery snapshot, a fresh snapshot, and a stale
   snapshot.
2. The presence or absence of prior preview execution does not change
   readiness.
3. Single-entity and multi-entity previews execute against the current
   datasource using explicit scope, including exact mapping where scopes
   differ.
4. Preview creates no authoring checkpoint; its coverage reports only current
   scope and execution facts, without snapshot ID or cache state.
5. Serialization, rendering, help, skills, and public exports expose only
   `analysis_ready_inputs`; removed compatibility projections and readiness
   issue kinds are absent.
6. Dedicated certified-artifact failures still block affected roots under the
   new `*_artifact_*` names, while ordinary discovery evidence never does.
7. Business-context absence is advisory and operation-specific failures remain
   visible at their runtime owner.

**Milestone gate.** Run focused readiness, preview, serialization, rendering,
artifact-certification, help, export, and skill tests, followed by
`make check`, both site gates, and `git diff --check`. All active surfaces
changed by M2 must switch directly to the explicit-scope contract; the removed
snapshot-bound form is not retained as an overload.

### Milestone 3: Explicit Source Health And End-To-End Closeout

**Outcome.** Source and data drift have an explicit owner with their own clock,
scope, and evidence, and the complete agent journey reaches typed analysis
without treating discovery or preview history as semantic authority.

**Implementation scope.**

- audit the current datasource inspection, connection test, `md.test`,
  `doctor --connect`, and semantic-check seams, then implement the smallest one
  explicit source-health command or library call rather than a parallel
  framework;
- return datasource and source identities, affected semantic refs, declared
  checks run, observed schema/capability identity, per-check status, check
  time, repair direction, and whether user data was queried under which scope;
- support only explicitly declared freshness, null, enum, uniqueness,
  cardinality, relationship, or similar expectations; do not infer business
  constraints from samples;
- keep source health explicit and separately reportable: authoring and
  readiness neither invoke it implicitly nor absorb its state;
- add the real-backend end-to-end journey covering authoritative inspection,
  governed raw SQL, coherent-slice authoring, one load, readiness without a
  snapshot, explicit-scope preview, representative `observe(...)`, and a
  separate source-health check;
- complete cross-surface synchronization across canonical specs,
  `agent-guide.md`, packaged skills, live help, public exports, examples,
  introspection snapshots, and latest English and Chinese site documentation.

**Non-goals.** M3 does not add polling, scheduling, history retention,
dashboards, implicit readiness gating, or automatic semantic mutation. The
exact source-health entrypoint is resolved during implementation from the
smallest existing code seam, but its result and ownership contract are fixed
by this design.

**Independent acceptance proof.**

1. A declared source schema change identifies the affected current semantic
   refs and returns a concrete repair without modifying the project.
2. Permission failure, source unavailability, failed checks, and unknown state
   are distinguishable.
3. Undeclared freshness, uniqueness, null, enum, cardinality, and relationship
   expectations are absent or unknown rather than guessed.
4. Any data-reading health check discloses that fact and its exact scope.
5. Readiness output is unchanged before and after source-health execution.
6. The end-to-end journey completes with no verify result, lifecycle state,
   semantic discovery projection, persisted preview checkpoint, or hidden
   snapshot authority.
7. Active English and Chinese documentation, skills, help, exports, and
   examples contain no stale contract from any earlier milestone.

**Milestone gate.** Run focused source-health and end-to-end real-backend
tests, the complete public-surface drift suite, `make check`, both site gates,
and `git diff --check`. M3 is complete only when the aggregate acceptance
criteria below pass; source-health scheduling or history is not required.

## Migration And Clean Cutover

Implementation is a breaking replacement delivered through the three ordered
milestones above, not a compatibility migration. The lists below are the
aggregate completion inventory, not a fourth milestone. Each item is removed
or adapted atomically in the milestone that owns it.

### Remove

- semantic-shaped discovery evidence result families and their projection
  methods;
- `AuthoringJudgmentRequirement` and judgment rendering;
- public authoring lifecycle states and state-transition contracts;
- datasource and ontology uses of the shared authoring lifecycle graph while
  retaining their callable/effect/help surfaces;
- `catalog.verify(...)`, `VerifyResult`, help descriptors, exports, and tests;
- persisted ordinary semantic preview checks and their readiness lookup;
- `snapshot_missing` and `runtime_preview_missing` readiness issues;
- `analysis_ready_refs` and `preview_required_refs` from `ReadinessReport`, its
  serialization, rendering, registry descriptor, repairs, skills, and tests;
- `period_calendar_snapshot_*`, `temporal_set_snapshot_*`, and
  `work_schedule_snapshot_*` readiness issue names in favor of the certified
  `*_artifact_*` names;
- snapshot-bound preview input requirements;
- dead transition, repair, effect, and preview-coverage fields, including
  `verify`, `reverify`, `repreview`, `requires_existing_snapshot_binding`,
  `snapshot_ids`, and preview `cache_status`;
- skill and active-documentation rules that forbid coherent batch authoring or
  ordinary governed SQL exploration.

### Retain or adapt

- datasource registration, connection testing, inspection, typed source
  descriptors, and explicit scope values;
- optional bounded sampling and exact evidence identity;
- `md.raw_sql(...)` runtime safety and terminal non-promotion contract;
- semantic Python constructors, typed refs, loader validation, catalog details,
  and scoped readiness;
- runtime preview, adapted to explicit scope rather than snapshot authority;
- dedicated certified semantic artifacts for data-backed semantic object
  families;
- `ms.richness(...)` or the current advisory owner for business context;
- structured errors, repairs, bounded cards, and live help.

### Synchronize

- canonical semantic and datasource specs;
- `agent-guide.md`, including its authoring chain, snapshot-projection
  ownership, business-authority rule, and state-bearing `.contract()` rule;
- `marivo-semantic` and any affected `marivo-analysis` routing;
- top-level, datasource, semantic, readiness, preview, and raw-SQL live help;
- current public exports and introspection snapshots;
- latest English and Chinese site documentation;
- examples and end-to-end agent journeys;
- package contents and public-surface drift tests.

Historical versioned site documentation and historical design records are not
rewritten.

## Testing Strategy

### Workflow acceptance

Add an isolated real-backend authoring journey that:

1. loads an existing project;
2. inspects authoritative schema;
3. uses `md.raw_sql(...)` for one source-specific hypothesis;
4. authors an entity, dimensions, a time dimension, measures, and base metrics
   before the next load;
5. proves one `ms.load()` validates the coherent slice;
6. runs scoped readiness with no discovery snapshot present;
7. previews only selected high-risk roots using explicit scope;
8. runs a representative `observe(...)` and continues analysis.

The journey must prove that no hidden per-object state, verify result, persisted
preview check, or snapshot-readiness lookup is required.

### Readiness tests

Prove that:

- identical semantic definitions produce identical readiness with no snapshot,
  a fresh snapshot, and a stale snapshot;
- missing preview history never changes readiness status or issues;
- semantic graph and temporal legality blockers still propagate through exact
  requested dependency closures;
- missing business context is advisory outside readiness;
- dedicated certified semantic artifacts retain their object-specific
  currentness rules;
- certified readiness issues use artifact rather than discovery-snapshot
  vocabulary;
- operation-specific execution failures remain owned by preview or analysis.

### Raw SQL tests

Retain proof of:

- local validation before connection;
- single-statement and read-only behavior;
- enforceable timeout and fail-closed timeout setup;
- exact returned-row bounding and truncation disclosure;
- defensive terminal conversion;
- rejection from typed analysis inputs.

Add skill/help acceptance proving that raw SQL is taught as a normal governed
exploration choice and that its output may inform authored Python without
claiming semantic equivalence.

### Public-surface tests

Prove that:

- removed verify, projection, judgment, and lifecycle-state symbols are absent;
- datasource and ontology live help still expose their retained capabilities
  without the shared state graph;
- `analysis_ready_inputs` is the single ready-input projection and
  `analysis_ready_refs` / `preview_required_refs` are absent;
- preview coverage contains current scope and execution facts without snapshot
  IDs or cache state;
- no compatibility alias or dual path remains;
- live help exposes current signatures and effects without a workflow graph;
- structured errors route to load, require, readiness, preview, source health,
  or the consuming analysis operation according to ownership;
- packaged skills and latest docs contain no stale one-object or
  snapshot-readiness instructions.

### Repository gates

The implementation plan must include:

```text
make check
cd site && npm run verify:content
cd site && npm run build
git diff --check
```

Skill and example behavior remains exercised by the repository's pytest suite;
this design does not invent a separate `examples-check` target.

## Acceptance Criteria

1. The canonical skill and live help permit one coherent semantic slice before
   the next load.
2. `ms.load()` is the sole project-level static validation event; no public
   `catalog.verify(...)` or `VerifyResult` remains.
3. Semantic readiness is unchanged by ordinary snapshot and preview evidence
   state.
4. `snapshot_missing` and `runtime_preview_missing` are absent from readiness.
5. Business-definition and guardrail completeness are advisory outside
   semantic-static readiness.
6. Runtime preview accepts explicit scope without requiring a discovery
   snapshot or persisted preview checkpoint.
7. Discovery snapshots remain optional bounded evidence and no longer expose
   semantic-shaped projections or judgment requirements.
8. `md.raw_sql(...)` is taught as a normal governed exploration path while its
   result remains terminal and cannot enter typed analysis.
9. Source and data drift have an independent explicit owner and do not reuse
   discovery snapshot freshness as a validity signal.
10. Period calendars, temporal sets, work schedules, and similar data-backed
    semantic objects retain dedicated certified-artifact integrity rather than
    generic discovery-evidence gating, and their readiness issues use
    `*_artifact_*` names.
11. Authoring lifecycle states and generic transition contracts are removed
    across datasource, semantic, and ontology; their retained capabilities,
    effect disclosure, and typed repair remain.
12. `analysis_ready_inputs` is the only ready-input projection;
    `analysis_ready_refs` and `preview_required_refs` are removed.
13. A new or changed definition with unresolved business-caliber meaning stops
    before first typed analysis use, while already-authorized meaning requires
    no redundant approval turn or token.
14. Active specs, `agent-guide.md`, live help, skills, tests, exports, examples,
    and latest site documentation agree on the new workflow.
15. No legacy aliases, compatibility adapters, migrations, or dual-read paths
    remain.
16. `make check`, both site gates, and `git diff --check` pass.

## Open Implementation Questions

These questions are intentionally deferred to implementation planning because
they depend on the smallest current code seam, not on a new product concept:

- whether explicit preview scope should use `scope=...`, `scopes=...`, or one
  existing closed input shape without adding an optional-field mega signature;
- whether generic retained snapshot rows require a new public accessor or the
  existing structured fields are sufficient after semantic projections are
  removed;
- whether the first source-health entry should extend `marivo doctor --connect`,
  use the current semantic check CLI, or add one focused catalog method;
- which shared non-authoring result contracts still need the existing
  `AuthoringEffects` name versus a more accurate general effect type.

None of these questions changes the ownership decisions in this design. The
implementation plan must resolve them from current code and one-path-per-
capability constraints before editing the public surface.
