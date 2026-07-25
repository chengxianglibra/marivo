# Phase 3 Replay-based Lifecycle Core Implementation Plan

Status: Ready for implementation

## Summary

This plan implements Phase 3 of the Event and Lifecycle design on top of the
completed Phase 1 Event Journey and Phase 2 Event reducers/typed-composition
vertical slices.

The Phase 3 public boundary is:

- `LifecycleState`, `StateModel`, deterministic inception/transition authoring,
  and exact `ModelStateHandle` resolution;
- `session.lifecycle.replay(...)` from the required
  `mv.from_inception()` seed into one canonical
  `LifecycleFrame[history]`;
- replay-only `lifecycle.distribution`, `lifecycle.transitions`,
  `lifecycle.dwell`, and `lifecycle.violations` reducers;
- Lifecycle-backed `session.select_subjects(...,
  selection=mv.in_state(...))`;
- ready `SubjectSet` cohort admission on `session.lifecycle.replay`;
- Lifecycle quality, evidence, persistence/cold recovery, live help,
  structured repair, catalog browsing, and agent-facing contracts.

Phase 3 does **not** introduce `StateProjection`, `NormalizedState`,
`ProjectionStateHandle`, `StateAlignment`, Entity `ChangesVersioning`,
`lifecycle.observe`, `mv.as_of`, `mv.from_projection`, projection-backed
cohorts, lifecycle reconciliation, Event funnel comparison/attribution, or
retention. Those symbols remain absent from exports, help, contracts, recovery
affordances, and capability registration.

## Source of Truth and Starting Point

The normative product contract is
[`2026-07-13-event-semantic-and-analysis-design.md`](../specs/2026-07-13-event-semantic-and-analysis-design.md),
especially:

- StateModel Semantic;
- Typed Lifecycle Analysis: replay materialization and closed lifecycle shapes;
- SubjectSet Bridge;
- Runtime Completeness and Censoring;
- Artifact Algebra and `QualityReport[lifecycle]`;
- Evidence Engine Integration;
- Capability Registration, Help, Recovery, Lineage, and Structured Errors;
- acceptance criteria 1, 3, 8, 10–13, 15–18, and 20–27;
- Delivery Boundary phase 3.

The coordinated agent-facing contract in
[`2026-07-24-marivo-agent-interface-surface-vnext-design.md`](../specs/2026-07-24-marivo-agent-interface-surface-vnext-design.md)
remains authoritative for catalog entry/ref normalization, bounded cards,
canonical help routing, artifact contracts, and executable structured repair.

Implementation starts from commit `069c802f`, where Phase 2 provides:

- Event semantic refs, participant roles, compilation, catalog browsing,
  readiness, and occurrence materialization;
- canonical `EventFrame[journey]`, reducers, completeness resolution, quality,
  evidence, and recovery;
- `SubjectSet`, `DroppedBefore`, and ready cohort admission for Metric observe
  and Event match;
- final `analysis-artifact/v6`, `marivo.analysis_job/v2`, and evidence-store v4
  envelopes;
- the shared capability registry, artifact contract, semantic-input
  normalizer, structured repair, and transactional evidence commit paths.

Phase 3 retains those envelope versions and adds only final tagged StateModel,
LifecycleFrame, Lifecycle evidence, and `InState` variants. It adds no migration,
dual-read decoder, compatibility alias, or generic state-machine abstraction.

### Contract clarifications incorporated before implementation

The source design now records the following implementation-critical answers:

1. Replay windows reuse the existing `mv.TimeScope(start=..., end=...)`; there
   is no parallel `mv.window(...)` constructor.
2. `ModelStateHandle` stores only the exact StateModel ref and local state name.
   A standalone constructor has no active catalog and therefore cannot safely
   stamp a process-global parent fingerprint. The consuming catalog-bound
   operation resolves and persists the current model fingerprint.
3. `from_inception()` uses the first qualifying inception occurrence.
   Pre-inception modeled occurrences have no `model_state_at_event` and are not
   violations. Repeated inception after seeding is a fixed-contract violation.
4. Replay transition output is dense over the distinct modeled
   `(from_state, to_state)` pairs declared by the StateModel, including
   zero-count pairs, in declared state order.

The implementation plan additionally fixes these Phase 3-only operational
interpretations:

- `window` is half-open `[start, end)`. Replay scans modeled Event history
  before `start`, establishes state, and only then clips emitted intervals to
  the requested window.
- The first emitted interval boundary is
  `max(actual_state_entry_time, window.start)`; its retained
  `entered_by_event_*` fields still identify the actual pre-window trigger.
- Every emitted interval has a concrete `valid_to` at either its next legal
  transition or `window.end`. A final interval is `right_censored` only when
  all required replay Events have sufficient coverage through `window.end`;
  otherwise it is `coverage_censored`.
- Without a cohort, the replay population is the union of subject identities
  observed through the StateModel's resolved trigger roles. With a cohort, it
  is exactly the SubjectSet membership.
- A population subject with no inception raises `insufficient_state_history`
  only when every required Event input has sufficient coverage. Under unknown
  coverage it contributes to metadata as a coverage-censored subject and
  produces no fabricated initial-state row.
- `mv.in_state(state, as_of=...)` accepts an instant in the closed range
  `[window.start, window.end]`; the inclusive end allows selecting the state
  established at the replay boundary. History distribution instants remain
  strictly inside `[window.start, window.end)`.

## Public Contracts

### StateModel semantic authoring

```python
created = ms.lifecycle_state(name="created", initial=True)
paid = ms.lifecycle_state(name="paid")
fulfilled = ms.lifecycle_state(name="fulfilled", terminal=True)
cancelled = ms.lifecycle_state(name="cancelled", terminal=True)

order_lifecycle = ms.state_model(
    name="order_lifecycle",
    subject=orders,
    states=(created, paid, fulfilled, cancelled),
    transitions=(
        ms.inception(on=order_created),
        ms.transition(
            from_state=created,
            on=payment_captured,
            to_state=paid,
        ),
        ms.transition(
            from_state=paid,
            on=order_fulfilled,
            to_state=fulfilled,
        ),
        ms.transition(
            from_state=created,
            on=order_cancelled,
            to_state=cancelled,
        ),
        ms.transition(
            from_state=paid,
            on=order_cancelled,
            to_state=cancelled,
        ),
    ),
    ai_context=ms.ai_context(
        business_definition="Commercial order lifecycle."
    ),
)

paid_state = ms.model_state(model=order_lifecycle, name="paid")
```

The final public constructors are:

```python
ms.lifecycle_state(
    *,
    name: str,
    initial: bool = False,
    terminal: bool = False,
) -> LifecycleState

ms.inception(
    *,
    on: Ref[EventKind] | ParticipantRoleHandle,
) -> Inception

ms.transition(
    *,
    from_state: LifecycleState,
    on: Ref[EventKind] | ParticipantRoleHandle,
    to_state: LifecycleState,
) -> StateTransition

ms.state_model(
    *,
    name: str,
    subject: Ref[EntityKind],
    states: tuple[LifecycleState, ...],
    transitions: tuple[Inception | StateTransition, ...],
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[StateModelKind]

ms.model_state(
    *,
    model: Ref[StateModelKind],
    name: str,
) -> ModelStateHandle
```

Fixed semantic constraints:

- `name` values are non-empty lowercase snake_case; StateModel local state
  names are unique and declaration order is semantic display order;
- `states` is non-empty and contains exactly one `initial=True` state;
- `LifecycleState` declares `initial` and `terminal` once; StateModel accepts no
  separate initial/terminal lists or string state channels;
- every transition state is the exact authoring value present in `states`;
- `ms.inception(...)` has no source/target state parameters and always enters
  the sole initial state;
- `on` is one exact Event ref or ParticipantRoleHandle; callers do not repeat
  Event and role;
- an Event ref resolves automatically only when exactly one
  `cardinality="one"` participant ends at the StateModel subject; ambiguity
  returns bounded exact `ms.participant_role(...)` repairs;
- for one source state and resolved `(Event, participant role)` trigger, at
  most one target is legal;
- inception triggers are unique and do not overlap an ordinary transition
  trigger from an unseeded state;
- terminal states have no outgoing transitions;
- StateModel may load without inception for future projection seeding, but in
  Phase 3 it is not analysis-ready for replay and `from_inception()` admission
  fails before Event querying;
- StateModel contains no replay window, seed, completeness, violation policy,
  projection, guard, action, or arbitrary callable;
- `ModelStateHandle` is ref plus local name only; membership and current parent
  fingerprint are resolved by the active catalog at consumption.

StateModel fingerprinting covers:

- exact subject Entity ref and primary-key signature;
- ordered state names and initial/terminal flags;
- canonically resolved Event ref and participant role for each inception and
  transition;
- source/target state names;
- semantic context.

### Replay and seed

```python
history = session.lifecycle.replay(
    session.catalog.state_models.get("commerce.order_lifecycle"),
    window=mv.TimeScope(start=start, end=end),
    seed=mv.from_inception(),
    completeness=(coverage,),
    cohort=paid_customers,
    analysis_purpose="Reconstruct governed order state history.",
)
```

The Phase 3 signatures are:

```python
mv.from_inception() -> FromInception

SessionLifecycle.replay(
    model: SemanticInput[StateModelKind],
    *,
    window: TimeScope,
    seed: FromInception,
    completeness: tuple[CompletenessDeclaration, ...] = (),
    cohort: SubjectSet | None = None,
    analysis_purpose: str | None = None,
) -> LifecycleFrame
```

Replay constraints:

- the model accepts one exact current StateModel entry or equivalent ref and
  normalizes immediately to the ref;
- `window` is required, normalized, timezone-aware, and half-open;
- `seed` is required and has no hidden default; Phase 3 accepts only the exact
  `FromInception` value;
- `on_violation`, `record_and_continue`, raw StateProjection refs, snapshot
  artifacts, and duck-typed seed values are rejected before queries;
- completeness declarations may name only exact Event refs consumed by the
  selected StateModel, may not overlap ambiguously, and must cover
  `window.end`;
- each distinct Event ref is queried at most once, even when it triggers
  several transitions; all required participant roles are projected in that
  one query;
- a ready SubjectSet is semi-joined into every Event occurrence query and must
  match the model subject Entity, primary-key signature, project, session, and
  current catalog;
- Event predicates, identity, time normalization, participant fanout/null
  validation, watermark receipts, and declaration precedence reuse the Event
  core rather than a parallel implementation;
- occurrences are evaluated per subject from unbounded modeled history before
  `window.end`; only intervals overlapping `window` are emitted;
- same-Event occurrences use normalized time then declared Event identity
  ordering; distinct triggers at one instant fail with
  `ambiguous_event_order` only when their legal/violation outcome or resulting
  state depends on the ordering;
- legal transitions change state; illegal modeled Events after inception
  create one persisted trace violation, leave state unchanged, and do not
  prevent later evaluation;
- Events absent from the StateModel are neither queried nor violations.

### LifecycleFrame history and replay trace

`LifecycleFrame` is a distinct immutable `BaseFrame` family. Phase 3 introduces
only replay-produced `semantic_shape="history"`.

The public row contract is exactly:

```text
subject_identity
model_state
valid_from
valid_to
entered_by_event_ref
entered_by_event_identity
exited_by_event_ref
exited_by_event_identity
interval_status
```

Row semantics:

- `subject_identity` is the tuple of the StateModel subject Entity primary-key
  components in declaration order;
- `model_state` is the stable local state name; exact ModelStateHandles and
  parent model fingerprint live in metadata;
- `valid_from` and `valid_to` are normalized UTC instants clipped to the
  requested window;
- entry Event fields identify the actual transition that established the state,
  even when it occurred before the clipped `valid_from`;
- exit Event fields are populated only for `completed` intervals;
- `interval_status` is exactly
  `completed | right_censored | coverage_censored`;
- one subject has ordered, non-overlapping intervals; adjacent completed
  intervals meet at a boundary, and no empty interval is emitted;
- rows sort by deterministic subject identity and `valid_from`, then declared
  state order only as a final invariant-preserving tie-break;
- state ordinals, fingerprints, transition ids, trace ids, and raw policy
  payloads are absent from public rows.

Illegal Event detail must survive cold recovery without leaking identity values
into metadata, jobs, evidence summaries, cards, or telemetry. A replay history
therefore owns one private auxiliary Parquet payload:

```text
subject_identity
trigger_event_ref
trigger_event_identity
occurred_at
model_state_at_event
violation_kind
```

where `violation_kind` is
`illegal_transition | transition_from_terminal`.

Persistence rules:

- the auxiliary file is always written for replay history, including an empty
  typed table;
- Lifecycle metadata stores only the auxiliary filename, row count, schema
  version, and content hash;
- the auxiliary hash participates in the parent artifact content identity;
- load verifies path containment, schema, row count, and hash before creating a
  recovered LifecycleFrame;
- `to_pandas()` returns only the public history rows;
- only `session.lifecycle.violations(history)` exposes a typed copy of the
  trace rows;
- reducer execution reads the committed source artifact/auxiliary payload and
  never queries Event sources or replays transitions.

Keep `analysis-artifact/v6`; add a final Lifecycle-specific auxiliary manifest
variant rather than a legacy decoder or a second public artifact.

### Lifecycle reducers

```python
weekly_state = session.lifecycle.distribution(
    history,
    at=(week_1_end, week_2_end, week_3_end),
    axes=[account_region],
)

transition_counts = session.lifecycle.transitions(history)
dwell = session.lifecycle.dwell(history)
violations = session.lifecycle.violations(history)
```

The Phase 3 signatures are:

```python
SessionLifecycle.distribution(
    history: LifecycleFrame,
    *,
    at: Sequence[str],
    axes: Sequence[SemanticInput[DimensionKind]] = (),
    analysis_purpose: str | None = None,
) -> LifecycleFrame

SessionLifecycle.transitions(
    history: LifecycleFrame,
    *,
    analysis_purpose: str | None = None,
) -> LifecycleFrame

SessionLifecycle.dwell(
    history: LifecycleFrame,
    *,
    analysis_purpose: str | None = None,
) -> LifecycleFrame

SessionLifecycle.violations(
    history: LifecycleFrame,
    *,
    analysis_purpose: str | None = None,
) -> LifecycleFrame
```

All reducers require an exact same-session, same-project, current-catalog
replay `LifecycleFrame[history]`. They retain a source artifact ref and content
fingerprint and perform no Event occurrence query or StateModel replay.

#### Distribution

`at` is non-empty, unique after normalization, canonically ordered, and each
instant satisfies `window.start <= at < window.end`. A SubjectSet cohort is
already fixed by the source history and cannot be changed by the reducer.

The public row contract is:

```text
<one column per declared axis, in declared order>
as_of
model_state
subject_count
share
```

Rules:

- output is dense over every requested `as_of` and declared model state,
  including zero-count states;
- the known-state denominator is the distinct source population whose state is
  established at that instant;
- not-yet-seeded and coverage-censored subjects are excluded from that
  denominator and retained as per-instant metadata counts;
- zero denominators yield null `share`;
- axes accept exact current Dimension entries/refs only, use the unique
  directed to-one subject path, and resolve at each exact `as_of`;
- ordinary Entity axes follow the existing one-row-per-primary-key contract;
  snapshot/validity axes use existing exact temporal lowering; ambiguous,
  to-many, unreachable, wrong-time, or duplicate axes fail before enrichment;
- null axis values form an explicit group;
- grouped `subject_count` totals, including null groups, reconcile exactly to
  the ungrouped count for every `(as_of, model_state)`;
- grouped shares are recomputed against the known-state population inside each
  axis tuple and are never summed.

#### Transitions

The public row contract is:

```text
from_model_state
to_model_state
transition_status
transition_count
share_of_modeled_transitions
```

Phase 3 emits one row for every distinct modeled state pair in StateModel
declaration order, including zero counts. `transition_status` is always
`modeled`; `unmodeled_projection` remains unavailable until Phase 5. Counts
include only completed adjacent interval transitions whose boundary falls
inside the source replay window. Shares divide by all modeled transitions and
are null for a zero denominator.

#### Dwell

The public row contract is:

```text
model_state
interval_count
completed_count
right_censored_count
coverage_censored_count
mean_duration
median_duration
p90_duration
```

One row is emitted per declared model state, including zero-count states.
Duration aggregates use only completed, clipped source intervals; censored
intervals contribute only to their explicit counts. A state with no completed
interval has null duration statistics.

#### Violations

The public row contract is exactly the persisted replay-trace contract:

```text
subject_identity
trigger_event_ref
trigger_event_identity
occurred_at
model_state_at_event
violation_kind
```

Rows are copied from the validated committed auxiliary payload and sorted by
subject identity, occurrence time, Event ref, and Event identity. An empty
trace returns a valid empty `LifecycleFrame[violations]`. Violation rows are
observations of the normative model applied to Events; they are not labeled
policy breaches, data-quality failures, or causal facts.

### Lifecycle subject selection and cohort composition

```python
paid_state = ms.model_state(model=order_lifecycle, name="paid")

paid_at_end = session.select_subjects(
    history,
    selection=mv.in_state(paid_state, as_of=end),
)

replayed_paid_orders = session.lifecycle.replay(
    order_model,
    window=mv.TimeScope(start=next_start, end=next_end),
    seed=mv.from_inception(),
    cohort=paid_at_end,
)
```

The constructor is:

```python
mv.in_state(
    state: ModelStateHandle,
    *,
    as_of: str,
) -> InState
```

`SubjectSelection` becomes the closed discriminated union
`DroppedBefore | InState`. `Session.select_subjects` accepts
`EventFrame | LifecycleFrame` and dispatches only registered source/selection
pairs.

`InState` rules:

- `state` is an exact ModelStateHandle, never a bare state string or catalog
  entry;
- its StateModel must match the source history's exact ref and current
  fingerprint;
- `as_of` is normalized and lies in
  `[source.window.start, source.window.end]`;
- a subject is selected only when its state is established as the requested
  state at the instant;
- unseeded or coverage-censored truth is excluded and increments
  `excluded_coverage_censored_count`;
- a resulting censored SubjectSet remains inspectable/recoverable but cannot
  scope any typed consumer;
- selected raw identities exist only in SubjectSet artifact rows.

Ready Lifecycle-backed SubjectSets may scope the existing Metric observe,
Event match, and new Lifecycle replay consumers under the same subject Entity,
identity signature, current catalog, project, session, and ownership checks.
No new generic SubjectSet predicate, union/intersection, raw identity
constructor, or DataFrame re-entry is introduced.

## Internal Data Model

### Semantic IR and canonical trigger resolution

Add final immutable IR variants:

```text
LifecycleStateIR
  name
  initial
  terminal

StateTriggerIR
  event_ref
  participant_role

StateInceptionIR
  trigger

StateTransitionIR
  from_state
  trigger
  to_state

StateModelIR
  semantic_id
  domain
  name
  subject
  ordered states
  ordered canonical inceptions/transitions
  ai_context
  location
```

Authoring values may temporarily hold Event refs, ParticipantRoleHandles, and
LifecycleState objects. Catalog compilation resolves every Event-only trigger
to one exact participant role and stores only the canonical
`(EventRef, role_name)` trigger in final StateModelIR. Fingerprints,
readiness, replay, catalog details, and persistence consume this same canonical
form; they do not independently infer roles.

`StateModelEntry` and `StateModelDetails` expose:

- exact ref and current definition fingerprint;
- subject Entity;
- states in declaration order with initial/terminal flags;
- every inception/transition with source/target state and exact Event/role;
- bounded cards showing at most six state/transition members plus omitted
  count and a concrete `.details().show()` full-read action.

The card does not recommend replay or choose a seed.

### Lifecycle analysis values and metadata

Add frozen, fingerprinted values:

```text
FromInception
ModelStateHandle
InState
```

Add a final discriminated Lifecycle metadata union:

```text
LifecycleHistoryFrameMeta
LifecycleDistributionFrameMeta
LifecycleTransitionsFrameMeta
LifecycleDwellFrameMeta
LifecycleViolationsFrameMeta
```

The history metadata is the sole replay authority and includes:

- StateModel ref and current fingerprint;
- subject Entity ref and ordered identity signature;
- ordered state definitions and exact ModelStateHandle payloads;
- canonical trigger table;
- normalized half-open replay window;
- exact `FromInception` seed and `lifecycle_replay/v1` operator version;
- fixed violation behavior id;
- optional SubjectCohortBinding;
- exact replay Event fingerprints and identity components;
- per-Event coverage, declarations, receipts, and aggregate basis;
- population, seeded, coverage-censored, interval, and violation counts;
- pre-inception ignored-occurrence counts by canonical trigger;
- auxiliary violation-trace manifest;
- lineage, quality summary, issues, and evidence digest.

Reducer metadata references the source history artifact and content hash rather
than duplicating replay authority. Shape-specific metadata retains only
distribution instants/axes/reconciliation or reducer counts required for exact
recovery and quality.

Use row-contract versions:

```text
lifecycle-history-rows/v1
lifecycle-distribution-rows/v1
lifecycle-transitions-rows/v1
lifecycle-dwell-rows/v1
lifecycle-violations-rows/v1
lifecycle-replay-trace/v1
```

## Implementation Workstreams

### 1. Semantic identity, authoring, and compilation

Primary files:

- `marivo/refs.py`
- `marivo/semantic/state_model.py` (new)
- `marivo/semantic/ir.py`
- `marivo/semantic/_authoring_declarations.py`
- `marivo/semantic/authoring.py`
- `marivo/semantic/__init__.py`
- `marivo/semantic/_authoring_context.py`
- `marivo/semantic/loader.py`
- `marivo/semantic/validator.py`
- `marivo/semantic/_compiled_state.py`
- `marivo/semantic/reader.py`
- `marivo/semantic/metric_graph_lowering.py`
- `marivo/semantic/metric_graph_canonical.py`
- `marivo/semantic/constraints.py`
- `marivo/semantic/errors.py`

Changes:

1. Add `SemanticKind.STATE_MODEL`, `StateModelKind`,
   `Ref[StateModelKind]`, `ms.ref.state_model(...)`, payload decoding,
   path validation, exact-kind overloads, and public-ref tests.
2. Implement immutable authoring values and exact constructors in the new
   `state_model.py`; enforce state membership by authoring-value identity before
   lowering to names.
3. Register `StateModelIR` through the same loader/registry/compiled-state
   pipeline as Event; no second registry or runtime cache.
4. Canonically resolve Event-only triggers during catalog compilation.
   Ambiguous qualifying roles produce `ambiguous_participant_role` with
   bounded exact current role candidates and no guessed choice.
5. Validate state closure, one initial state, terminal closure, unique
   inception triggers, deterministic transition targets, exact subject
   endpoints, and at least one usable primary-key component.
6. Extend dependency digests and StateModel fingerprints transitively through
   Event definitions, participant paths, subject Entity identity, and semantic
   context.
7. Keep handle construction project-neutral: no module-global model
   fingerprint cache and no dynamic `.states` namespace on refs.

### 2. Catalog, verification, preview, readiness, and semantic help

Primary files:

- `marivo/semantic/catalog.py`
- `marivo/semantic/dtos.py`
- `marivo/semantic/readiness.py`
- `marivo/semantic/materializer.py`
- `marivo/semantic/resolver.py`
- `marivo/semantic/_capabilities/registry.py`
- `marivo/semantic/_capabilities/render.py`
- `marivo/semantic/help.py`
- public-surface and catalog tests

Changes:

1. Add `catalog.state_models`, `StateModelEntry`, and `StateModelDetails` with
   scoped local/full-path/exact-ref lookup and bounded member rendering.
2. Extend list/details/help/verify/readiness routing and public kind snapshots.
3. StateModel verification is query-free and checks the canonical finite
   machine. Preview delegates only to its exact Event dependencies using the
   existing bounded Event preview contract; it does not invent state history
   from a sample.
4. Readiness requires ready subject Entity, primary key, Event definitions,
   resolved cardinality-one roles and paths, deterministic transitions, and a
   legal Phase 3 inception seed. A seedless model is loadable but not
   `analysis_ready`.
5. Add focused semantic help for `lifecycle_state`, `inception`,
   `transition`, `state_model`, and `model_state`. Examples use refs for
   standalone typed-value constructors and do not accept catalog entries in
   authoring.
6. Ensure cards/details expose exact refs and full trigger paths but do not
   recommend analysis operators.

### 3. Shared Event occurrence materialization

Primary files:

- `marivo/analysis/intents/events.py`
- `marivo/analysis/intents/_event_occurrences.py` (new)
- `marivo/analysis/intents/_subject_cohort.py`
- `marivo/analysis/event.py`
- focused Event regression tests

Changes:

1. Extract from Event Journey only the reusable occurrence operations:
   catalog Event resolution, one-query-per-Event materialization, Event
   predicate application, normalized identity/time projection, multi-role
   endpoint materialization, participant cardinality checks, cohort semi-join,
   watermark/declaration resolution, and deterministic same-Event identity
   ordering.
2. Keep EventPattern matching and journey-specific ambiguity semantics in
   `events.py`; Lifecycle consumes occurrence batches through a private typed
   plan and does not synthesize PatternSteps.
3. Query a distinct EventRef once even when StateModel transitions use several
   roles from that Event. Produce role-specific subject occurrence streams
   after the shared query.
4. Preserve every Phase 1/2 Event query count, matching result, structured
   error, persistence fingerprint, and evidence receipt through extraction.

### 4. Lifecycle values, frames, and auxiliary persistence

Primary files:

- `marivo/analysis/lifecycle.py` (new)
- `marivo/analysis/frames/lifecycle.py` (new)
- `marivo/analysis/frames/base.py`
- `marivo/analysis/frames/__init__.py`
- `marivo/analysis/__init__.py`
- `marivo/analysis/session/_layout.py`
- `marivo/analysis/session/_load.py`
- `marivo/analysis/session/_runtime.py`
- `marivo/analysis/_semantic_persistence.py`

Changes:

1. Implement `FromInception`, seed fingerprinting, and public
   `from_inception()`.
2. Implement all final Lifecycle meta variants and strict public row
   validators.
3. Add `LifecycleFrame` bounded repr/show/contract/to_pandas behavior using the
   shared BaseFrame protocol.
4. Add a private frame-owned auxiliary-table persistence hook. BaseFrame
   returns no auxiliary tables; replay Lifecycle history returns one typed
   violation trace.
5. Write auxiliary payloads atomically before meta, hash them independently,
   bind their manifest into the parent content hash, and remove them with the
   existing artifact rollback directory.
6. Cold load rejects absent, escaped, malformed, wrong-count, or hash-mismatched
   trace payloads with the existing structured corruption/rebuild route.
7. Extend current artifact/job tagged unions only; keep v6/v2 versions and
   reject unknown lifecycle shapes.

### 5. Replay planning and materialization

Primary files:

- `marivo/analysis/intents/lifecycle.py` (new)
- `marivo/analysis/intents/_lifecycle_replay.py` (new)
- `marivo/analysis/session/core.py`
- `marivo/analysis/session/_connections.py`
- `marivo/analysis/errors.py`
- `marivo/analysis/constraints.py`

Changes:

1. Add `Session.lifecycle -> SessionLifecycle` and a `replay` method with the
   exact Phase 3 signature.
2. Normalize StateModel entry/ref immediately through the active catalog;
   reject stale/cross-catalog/wrong-kind/bare-string inputs before backend
   access.
3. Validate window, exact seed, completeness, cohort, model readiness, and
   subject identity before creating an occurrence plan.
4. Build canonical trigger streams from the compiled StateModel and shared
   Event materializer, scanning all modeled history before `window.end`.
5. Establish the replay population, first inception, state progression,
   fixed violations, trace rows, clipped intervals, and censoring in pure
   deterministic functions separate from persistence.
6. Evaluate same-time trigger groups under all state-distinct legal orderings
   needed to determine whether outcomes differ. Fail only when ordering changes
   final state or violation classification; otherwise use a stable internal
   order without presenting it as business time truth.
7. Fail `insufficient_state_history` for proven missing inception; retain
   unknown-history subjects only as censored metadata counts.
8. Commit frame, trace, findings, digest, artifact registration, and job
   transactionally. On any later failure, remove all newly written state while
   preserving pre-existing artifacts.

### 6. Replay-only reducers and subject axes

Primary files:

- `marivo/analysis/intents/lifecycle_reducers.py` (new)
- `marivo/analysis/intents/_lifecycle_distribution.py` (new)
- `marivo/analysis/intents/_lifecycle_transitions.py` (new)
- `marivo/analysis/intents/_lifecycle_dwell.py` (new)
- `marivo/analysis/intents/_lifecycle_violations.py` (new)
- `marivo/analysis/intents/_event_subject_axes.py`
- `marivo/analysis/session/core.py`

Changes:

1. Add four SessionLifecycle reducer methods and validate source ownership,
   replay source kind, shape, current catalog fingerprint, and persisted source
   hash before work.
2. Distribution computes ungrouped state membership first, then optionally
   reuses a generalized subject-axis planner with `anchor="as_of"` rather than
   Event's `anchor="cohort_entry"`.
3. Generalize only private axis planning primitives. Keep Event and Lifecycle
   bindings as closed shape-specific metadata variants so an invalid anchor
   cannot be authored.
4. Require exact grouped-to-ungrouped reconciliation for each
   `(as_of, model_state)` before commit.
5. Transitions, dwell, and violations read only persisted history/trace data.
   Tests use backend query counters to prove zero datasource execution.
6. Emit dense, deterministically ordered state/state-pair rows and fixed null
   semantics exactly as specified above.

### 7. InState selection and Lifecycle cohort admission

Primary files:

- `marivo/analysis/subject.py`
- `marivo/analysis/intents/subjects.py`
- `marivo/analysis/intents/_subject_cohort.py`
- `marivo/analysis/frames/subject.py`
- `marivo/analysis/session/core.py`

Changes:

1. Add frozen `InState` and extend the discriminated SubjectSelection union.
2. Split `select_subjects` source validation into Event and Lifecycle
   dispatches; do not weaken either path to attribute-based duck typing.
3. Resolve state-at-instant from source history with exact boundary semantics,
   produce ready/censored SubjectSet metadata, and retain no raw identities
   outside rows.
4. Extend cohort admission with `consumer="lifecycle.replay"` and the same
   project/session/catalog/Entity/signature/source-fingerprint rules.
5. Apply membership as an Event occurrence semi-join in replay. Do not read a
   separate identity datasource and do not filter only after replay.
6. Extend ready SubjectSet contracts with Lifecycle replay only; censored sets
   advertise no typed cohort consumer.

### 8. Quality, evidence, and recovery

Primary files:

- `marivo/analysis/intents/assess_quality.py`
- `marivo/analysis/intents/_quality_checks.py`
- `marivo/analysis/frames/quality.py`
- `marivo/analysis/evidence/types.py`
- `marivo/analysis/evidence/extraction/lifecycle.py` (new)
- `marivo/analysis/evidence/pipeline.py`
- `marivo/analysis/evidence/digest.py`
- `marivo/analysis/evidence/summary.py`
- `marivo/analysis/evidence/__init__.py`
- `marivo/analysis/session/_load.py`

Changes:

1. Add `QualityReport[lifecycle]` dispatch for every Phase 3 Lifecycle shape.
2. History checks cover model/state closure, interval ordering/non-overlap,
   entry/exit trigger consistency, seed/inception, coverage, declaration
   disclosure, censoring, trace manifest integrity, violation state, and row
   contract.
3. Distribution checks exact instants, known population, axis temporal
   ownership, null groups, shares, and grouped reconciliation from current
   rows rather than trusting only persisted receipts.
4. Transition/dwell/violation checks recompute source-derived counts and
   partitions and verify source artifact fingerprints.
5. Add LifecycleAnalysisSubject, LifecycleScope, and closed observation values
   for history/distribution/transitions/dwell/violations.
6. Digest extraction keeps at most five deterministic observations and three
   inference boundaries, includes declared/unknown coverage and fixed replay
   semantics, and excludes raw subject/Event identities.
7. Preserve existing complete/partial/unavailable evidence isolation and exact
   transaction rollback.
8. Verify warm/cold frames, trace, quality, findings, digest, issues, lineage,
   and contracts are identical.

### 9. Capability registry, help, docs, and skill boundary

Primary files:

- `marivo/analysis/_capabilities/model.py`
- `marivo/analysis/_capabilities/registry.py`
- `marivo/analysis/_capabilities/surface.py`
- `marivo/analysis/_capabilities/validation.py`
- `marivo/analysis/_capabilities/render.py`
- `marivo/analysis/help.py`
- `marivo/analysis/session/core.py`
- `marivo/semantic/_capabilities/registry.py`
- `docs/api/analysis.rst`
- `docs/specs/analysis/python-analysis-design.md`
- `site/src/content/docs/en/latest/concepts/semantic-layer.mdx`
- `site/src/content/docs/zh-cn/latest/concepts/semantic-layer.mdx`
- `site/src/content/docs/en/latest/concepts/analysis-workflow.mdx`
- `site/src/content/docs/zh-cn/latest/concepts/analysis-workflow.mdx`
- `marivo/skills/marivo-semantic/SKILL.md`
- `marivo/skills/marivo-analysis/SKILL.md`

Changes:

1. Add `LifecycleFrame` to the closed artifact-family vocabulary and
   public/frame result allowlists.
2. Register StateModel semantic values and Lifecycle analysis values/operators
   with exact producer/consumer edges and artifact-admission predicates.
3. Generate the Phase 3 continuation matrix:
   - replay history: distribution, transitions, dwell, violations,
     select_subjects(in_state), assess_quality;
   - distribution/transitions/dwell/violations: assess_quality only;
   - ready SubjectSet: existing Metric/Event cohort bindings plus replay;
   - censored SubjectSet: no typed consumer.
4. Resolve `lifecycle.replay`, `session.lifecycle.replay`,
   `Session.lifecycle.replay`, and bound methods to one descriptor; apply the
   same canonicalization to all reducers.
5. Focused replay help includes complete executable inception and
   SubjectSet-cohort forms; distribution help includes exact `at` and axes;
   violation help states the fixed non-policy meaning.
6. Help and contracts do not display `lifecycle.observe`, `from_projection`,
   snapshot frames, reconciliation, or any Phase 4/5 continuation.
7. Update API docs and both latest language tracks atomically.
8. Packaged skills retain workflow/boundary guidance only and defer signatures,
   shape matrices, examples, and repair taxonomy to live help.

## Structured Error Contract

Reuse shared semantic-input, window, cross-session/project, artifact-corruption,
and evidence errors where they already own the failure. Phase 3 adds or extends
only these domain-specific paths:

| Error kind | Phase 3 trigger | Repair |
| --- | --- | --- |
| `invalid_state_transition` | invalid state declaration, inception, terminal closure, trigger endpoint, or nondeterministic target | `semantic_authoring`; current states/roles are bounded candidates when useful |
| `ambiguous_participant_role` | Event-only trigger has several qualifying subject roles | `user_choice`; candidates are exact current `ms.participant_role(...)` expressions |
| `model_state_mismatch` | bare string, unknown state, cross-model handle, or stale parent definition | `user_choice` for a valid current state; `inspect` for stale/missing model |
| `invalid_lifecycle_seed` | missing/wrong/duck-typed seed or seedless model used with `from_inception` | `retry` only when the exact current `mv.from_inception()` call is safe; otherwise `semantic_authoring` |
| `insufficient_state_history` | complete model inputs prove a replay-population subject has no inception | `semantic_authoring` or `inspect`; never fabricate an initial-state retry |
| `ambiguous_event_order` | same-time trigger ordering changes replay state or violation outcome | `inspect`; Event definitions or source ordering evidence must change |
| `invalid_distribution_instants` | empty, duplicate, malformed, or out-of-window history instants | `user_choice` with the exact source window in expected/received fields |
| `invalid_subject_axis` | invalid/unready/fanout/temporally ambiguous distribution axis | existing semantic-authoring or inspect repair with current Dimension candidates |
| `grouped_reconciliation_failed` | distribution grouped counts differ from current ungrouped rows | `inspect`; rebuild source/enrichment, never accept a retry that trusts a stored receipt |
| `subject_set_mismatch` | InState/source or cohort consumer ownership, state, Entity, identity, scope, or coverage mismatch | `inspect` or `user_choice` according to whether current catalog candidates exist |
| `quality_shape_unsupported` | unsupported Lifecycle family/shape pair | existing inspect repair and canonical `assess_quality` help |

Every instance includes non-empty `expected`, `received`, `location`,
`repair.action`, canonical `help_target`, and bounded current candidates where
the legal set is closed. A `retry` contains a fully executable snippet using
only current public names; all business choices remain `user_choice`.

## Ordered Delivery Steps

Each step starts with failing behavioral tests, implements one coherent
boundary, runs the listed focused gate, and leaves the worktree in a recoverable
state. Do not defer public-surface, persistence, or evidence work until after
all runtime code is written.

### Step 1: Freeze Phase 3 absence and final public snapshots

1. Add failing public-surface tests for `SemanticKind.STATE_MODEL`,
   `ms.ref.state_model`, semantic constructors/types, Lifecycle analysis
   values, `LifecycleFrame`, and `Session.lifecycle`.
2. Add negative assertions that all Phase 4/5 symbols remain absent.
3. Update expected `__all__`, kind, capability-family, and agent API drift
   snapshots only for the exact Phase 3 surface.
4. Run:

```text
make test TESTS='tests/test_public_surface.py tests/test_semantic_refs.py tests/test_agent_api_drift.py'
```

### Step 2: Implement StateModel authoring and canonical IR

1. Add `tests/test_semantic_state_model.py` covering happy-path values,
   immutable handles, exact refs, fingerprint stability, and every local
   contract failure.
2. Implement refs, authoring values, StateModelIR, loader registration, and
   canonical trigger resolution.
3. Cover EventRef unique-role inference, explicit role selection, ambiguous
   repairs, wrong endpoint/cardinality, exact state membership, terminal
   closure, and determinism.
4. Verify two projects with the same model path but different definitions do
   not share process-global handle/fingerprint state.
5. Run:

```text
make test TESTS='tests/test_semantic_state_model.py tests/test_semantic_event.py tests/test_semantic_refs.py'
make typecheck
make lint
```

### Step 3: Complete catalog, readiness, preview, and semantic help

1. Extend semantic catalog navigation fixtures with ordinary, large-card,
   seedless, ambiguous-role, and dependency-unready StateModels.
2. Test bounded card omission/full-read recovery, exact entry/ref behavior,
   wrong-kind/stale/cross-catalog rejection, and scoped lookup.
3. Test query-free verification versus Event-backed bounded preview and
   task-specific readiness.
4. Execute every StateModel focused-help example against a real semantic
   project.
5. Run:

```text
make test TESTS='tests/test_semantic_catalog_navigation.py tests/test_semantic_state_model.py tests/test_semantic_help_contract.py tests/test_semantic_live_help.py tests/test_semantic_readiness.py'
```

### Step 4: Extract shared Event occurrence materialization

1. Add query-counter regression tests proving Event match still queries each
   Event once and preserves all Phase 1/2 rows/coverage.
2. Extract private occurrence planning/materialization without changing public
   Event artifacts or errors.
3. Add a multi-role one-query probe for the future replay consumer.
4. Run:

```text
make test TESTS='tests/test_event_journey_matching.py tests/test_event_journey_persistence_regressions.py tests/test_event_reducers.py'
```

### Step 5: Introduce LifecycleFrame and trace persistence

1. Add `tests/test_lifecycle_frame_persistence.py` with valid/invalid row
   contracts, empty/non-empty trace, hash tampering, path escape, missing file,
   cold recovery, bounded display, isolated pandas output, and rollback.
2. Implement Lifecycle metadata, public frame, auxiliary persistence hook,
   loader, artifact registration, and exact current-schema decoder.
3. Ensure generic non-Lifecycle artifact byte/hash/recovery behavior is
   unchanged.
4. Run:

```text
make test TESTS='tests/test_lifecycle_frame_persistence.py tests/test_analysis_load_frame_v1_2.py tests/test_analysis_session_layout.py tests/test_agent_result_protocol.py'
```

### Step 6: Implement replay from inception

1. Add `tests/test_lifecycle_replay.py` using one shared DuckDB semantic
   fixture and isolated `tmp_path` sessions.
2. Cover pre-window seeding/clipping, multiple legal transitions, terminal
   state, repeated inception, illegal transitions, pre-inception occurrences,
   complete/declared/mixed/unknown coverage, and stable output identity.
3. Cover same-Event identity ordering and cross-trigger same-time
   outcome-sensitive ambiguity.
4. Cover no-inception complete failure versus unknown-coverage censoring.
5. Cover multi-role Event query reuse and SubjectSet query-time semi-join.
6. Assert every static invalid input fails with backend query counter zero.
7. Run:

```text
make test TESTS='tests/test_lifecycle_replay.py tests/test_event_journey_matching.py'
```

### Step 7: Implement reducers

1. Add `tests/test_lifecycle_reducers.py`.
2. Distribution cases: one/many instants, zero population, all states,
   current/snapshot/validity subject axes, null groups, fanout/path/time
   rejection, and adversarial grouped reconciliation.
3. Transition cases: distinct modeled pairs, duplicate-trigger same pair,
   zero-count rows, denominator zero, and declaration order.
4. Dwell cases: completed/right/coverage-censored partitions, clipped first
   interval, terminal final interval, and null aggregates.
5. Violation cases: empty/non-empty trace, stable order, identity restoration,
   corrupt trace, and no backend/replay call.
6. Assert all pure reducers execute with backend query counter zero;
   distribution with axes may query only governed subject Dimensions.
7. Run:

```text
make test TESTS='tests/test_lifecycle_reducers.py tests/test_event_subject_axes.py'
```

### Step 8: Implement InState and replay cohort composition

1. Add `tests/test_lifecycle_subject_composition.py`.
2. Cover exact state match at start/interior/end, boundary transition
   ownership, wrong-model/bare-string state, out-of-scope as-of, censored truth,
   empty selection, and deterministic identity order.
3. Cover Lifecycle-backed SubjectSet admission to Metric observe, Event match,
   and Lifecycle replay; reject wrong session/project/catalog/Entity/signature
   and censored sets before queries.
4. Verify replay applies membership during occurrence materialization.
5. Run:

```text
make test TESTS='tests/test_lifecycle_subject_composition.py tests/test_phase2_event_frame_subject_values.py'
```

### Step 9: Add quality and evidence end to end

1. Add `tests/test_lifecycle_quality_evidence.py`.
2. Test each shape's quality checks with both valid frames and row/meta/trace
   tampering.
3. Test exact Lifecycle subject/scope/value persistence and bounded digest
   extraction.
4. Search persisted evidence, job JSON, metadata, cards, errors, and telemetry
   for raw identity values; only artifact and trace rows may contain them.
5. Test evidence extraction failure, digest failure, store unavailable, and
   registration failure rollback with pre-existing artifacts preserved.
6. Run:

```text
make test TESTS='tests/test_lifecycle_quality_evidence.py tests/test_analysis_event_evidence.py tests/test_event_quality_capabilities.py'
```

### Step 10: Register agent-facing surface and executable help

1. Add capability descriptors, family predicates, Session/namespace cards,
   artifact contract affordances, canonical callable mappings, and focused
   examples.
2. Extend capability/help/introspection tests for all equivalent spellings,
   bound methods, real executable examples, focused size budgets, and deferred
   symbol absence.
3. Ensure every failed contract precondition has a non-empty truthful repair;
   a retry always contains an executable snippet.
4. Run:

```text
make test TESTS='tests/test_analysis_capability_registry.py tests/test_analysis_help.py tests/test_introspection_contract.py tests/test_agent_api_drift.py'
```

### Step 11: Synchronize docs and packaged skills

1. Update API docs, analysis design, English/Chinese latest semantic and
   analysis workflows, and public examples.
2. Update the Event/Lifecycle design status only after every Phase 3 gate
   passes:

```text
Status: architecture revised; phases 1-3 and the coordinated
agent-interface vNext cutover implemented; phases 4-5 pending
```

3. Keep packaged skills thin and verify they do not copy signatures,
   capability inventories, examples, or repair catalogs.
4. Run:

```text
make test TESTS='tests/test_marivo_analysis_skill_contract.py tests/test_agent_api_drift.py'
make docs-api
cd site && npm run verify:content && npm run build
```

### Step 12: Full closeout

Run all final gates from the repository root:

```text
make typecheck
make lint
make test
make docs-api
cd site && npm run verify:content && npm run build
git diff --check
```

The current Makefile has no `examples-check` target. Do not report or invoke
one; live-help examples are exercised by behavior tests.

Before closeout:

- inspect staged, unstaged, and untracked files separately;
- preserve unrelated changes, including the pre-existing
  `2026-07-24-marivo-agent-interface-surface-vnext-design.md` Phase 4
  evaluation edit;
- verify Phase 4/5 symbols remain absent through imports, live help, contract,
  and cold recovery probes;
- leave changes uncommitted unless the user explicitly asks to commit.

## Test Matrix

### Semantic tests

- LifecycleState/state/transition/inception immutability and validation;
- StateModel exact ref/kind/path, deterministic fingerprint, dependency
  fingerprint drift, and cross-project isolation;
- unique Event role inference and explicit ParticipantRoleHandle resolution;
- ambiguous role candidates are current and bounded;
- no initial/multiple initial, duplicate state, non-member state, terminal
  outgoing transition, duplicate inception, nondeterministic transition,
  wrong Event kind/endpoint/cardinality;
- catalog collection/get/details/card/help/verify/preview/readiness and cold
  semantic load;
- seedless StateModel loads but is not Phase 3 replay-ready.

### Replay tests

- half-open window and pre-window state reconstruction;
- first inception, repeated inception, pre-inception ignored occurrences;
- legal, illegal, terminal, and later-valid transitions;
- one Event query per distinct ref across multiple transitions/roles;
- stable Event identity order and outcome-sensitive same-time ambiguity;
- complete, declared, mixed, unknown, insufficient, stale/mismatched receipt,
  unused/conflicting declaration;
- cohort/no-cohort/empty cohort populations;
- exact interval rows, clipping, statuses, counts, trace, stable artifact id;
- no fabricated initial state or hidden default seed/policy.

### Reducer tests

- distribution instant normalization, dense state rows, denominators, null
  share, censored counts, axis enrichment, null groups, and reconciliation;
- transitions dense modeled pairs, counts, shares, and no projection status;
- dwell count partition, completed-only durations, censoring, and null
  aggregates;
- violations exact trace projection and stable order;
- source ownership/hash/catalog failures and zero Event queries.

### SubjectSet and cohort tests

- exact ModelStateHandle ownership and `[start, end]` in-state boundary;
- ready/censored selection and no raw identities outside rows;
- ready Lifecycle-backed cohort admitted to Metric/Event/Lifecycle consumers;
- mismatched/censored/stale/cross-session/project/catalog inputs fail before
  queries;
- query-time semi-join, not post-replay-only filtering.

### Quality, evidence, and persistence tests

- every Lifecycle shape has `QualityReport[lifecycle]`;
- row-derived reconciliation detects tampering despite valid persisted
  receipts;
- declaration advisory remains explicit and non-authoritative;
- one shape-dispatched finding extraction per committed artifact;
- at most five digest facts and three inference boundaries;
- no raw identities in digest/finding summary/jobs/meta/cards/errors/telemetry;
- trace/content/meta corruption fails closed;
- warm/cold output, trace, contract, quality, findings, digest, issues, lineage,
  and cohort behavior are identical;
- transactional complete/partial/unavailable evidence behavior and rollback.

### Public-surface and regression tests

- final Phase 3 `__all__`, SemanticKind, artifact family, capability rows,
  canonical help spellings, CLI help, and bounded result snapshots;
- exact StateModel entry/ref normalization parity;
- no StateProjection/observe/from_projection/as_of/reconciliation, Phase 4
  funnel compare/attribute, or retention discovery;
- existing Metric lane remains unchanged without cohort;
- all Phase 1/2 Event journeys, reducers, SubjectSet, quality, evidence,
  persistence, and help tests remain green;
- all five import-linter architecture contracts remain kept.

## Acceptance and Closeout

Phase 3 is complete only when:

1. StateModel is a closed normative semantic object with one source of state,
   trigger, and transition truth and no replay policy or projection fields.
2. Event-only triggers resolve to one exact cardinality-one participant role;
   ambiguity requires explicit typed role selection.
3. `ModelStateHandle` is project-neutral ref/name identity and every consumer
   resolves current membership/fingerprint from its active catalog.
4. Replay uses one required `FromInception` seed, reconstructs before clipping,
   queries each Event once, and never assumes the initial state at window start.
5. Fixed illegal-Event behavior records a trace, leaves state unchanged, and
   exposes no public policy parameter.
6. Complete missing inception fails while unknown prior history is represented
   as censoring without fabricated rows.
7. Lifecycle history and private trace recover exactly and are bound by one
   artifact content identity.
8. Distribution, transitions, dwell, and violations consume the committed
   history/trace without Event rereads or replay.
9. Distribution axes are exact, to-one, temporally anchored, null-preserving,
   and exactly reconciled.
10. InState uses one exact ModelStateHandle and an explicit instant; censored
    truth never enters a ready SubjectSet.
11. Ready SubjectSet cohort admission preserves Entity/signature/session/
    project/catalog/coverage ownership and filters occurrences during query
    lowering.
12. Registry, live help, Session cards, artifact contracts, structured errors,
    persistence, and recovery agree on the exact Phase 3 continuation matrix.
13. Every Lifecycle shape has complete quality and evidence coverage with no
    raw identity leakage outside authorized artifact/trace rows.
14. StateModel entry/ref calls produce identical normalized jobs, artifacts,
    lineage, evidence, and cold recovery.
15. All invalid static inputs fail before datasource execution with complete
    expected/received/location/repair/help/candidate fields.
16. Existing Metric and Event behavior is unchanged when Lifecycle is unused.
17. Phase 4/5 and post-v1 capabilities remain undiscoverable.
18. Full repository, API-doc, site-content, site-build, and whitespace gates
    pass before the design status changes to Phase 3 implemented.

## Explicit Deferrals

The following remain absent from public exports, help, capability registration,
contracts, persistence variants, and packaged-skill inventories:

- Event funnel compare, funnel loss-rate targets, and attribution (Phase 4);
- StateProjection, NormalizedState, ProjectionStateHandle, StateAlignment,
  Entity ChangesVersioning, projection completeness, projection observation,
  current/snapshot/history projection shapes, and reconciliation (Phase 5);
- `mv.from_projection(...)`, `mv.as_of(...)`, and Lifecycle snapshot seed
  admission (Phase 5);
- projection-backed `SubjectSet` and `session.lifecycle.observe` cohort
  admission (Phase 5);
- unmodeled-projection transition rows and lifecycle reconciliation quality
  (Phase 5);
- retention/cohort-return matrices, repeat lifecycle instances, arbitrary
  guards, caller-selected violation policies, and arbitrary state predicates;
- generic artifact joins, raw identity constructors, SubjectSet set algebra,
  and pandas typed re-entry;
- ontology as an execution/readiness prerequisite;
- compatibility aliases, persisted-schema migrations, legacy dual-read, and
  placeholder future capability rows.

Phase 3 metadata may retain the exact replay authority later reconciliation
needs, but it must not publish StateProjection concepts or imply that Event
replay and observed-state projection are already comparable.
