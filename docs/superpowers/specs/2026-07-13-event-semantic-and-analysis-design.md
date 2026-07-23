# Marivo Event and Lifecycle Semantic and Analysis Design

Status: architecture revised; review feedback incorporated; pending implementation

Date: 2026-07-13

> This document is one of two specifications split from the original
> `Marivo Executable Semantic and Ontology Extension Design`. It owns the
> executable `Event`, `StateModel`, and `StateProjection` semantic objects and
> their typed Event
> and Lifecycle analysis. The optional knowledge layer is specified separately
> in the companion
> [`ontology extension design`](2026-07-13-ontology-extension-design.md).

## Summary

Marivo keeps one stable executable semantic model and one independent typed
analysis language. This document adds executable Event and lifecycle semantics
and their analysis; it does not add the optional ontology extension, which is
specified separately.

The existing `marivo.semantic` model remains the sole authority for executable
business meaning. `Entity`, `Dimension`, `TimeDimension`, `Measure`, `Metric`,
and `Relationship` retain their current identities, authoring contracts,
readiness behavior, and metric execution semantics. This design adds `Event`,
`StateModel`, and `StateProjection` as semantic objects that reuse those
contracts; it does not replace the Metric lane or introduce a parallel
grounding model.

`marivo.analysis` remains an independent typed DSL. It consumes ready semantic
handles and produces persistent typed artifacts. Event and lifecycle analysis
centers on canonical journey and lifecycle-history materializations. Funnel,
grouped funnel, time-to-event, lifecycle distribution with optional subject
axes, transitions, dwell, and violations are closed reducers over those
materializations rather than independent Event or state-source queries.

An exact `SubjectSet` is the only v1 bridge from Event/Lifecycle analysis into
another typed subject-compatible branch. Funnel comparison and attribution are
registered only for their exact shapes and additive count components. Neither
bridge turns Event/Lifecycle artifacts into MetricFrames or causal evidence.
Public Event consumers identify a pattern step through its typed,
pattern-member `PatternStep`, never through a positional integer or an
`EventRef` alone.
Public Lifecycle consumers identify a StateModel state through a typed
`ModelStateHandle`, never through a bare state-name string.

The optional `marivo.ontology` knowledge extension and its narrow
`discover.semantic_hypotheses` bridge are specified in the companion ontology
design and are never a prerequisite for the semantics and analysis defined
here.

`Rule`, `Policy`, and `Action` belong to a future decision extension outside
this design. They are not semantic objects or analysis operators here.

## Architectural Decision

This design occupies the executable semantic core and typed analysis core
layers:

```text
Executable Semantic Core (`marivo.semantic`)
    Entity / Dimension / TimeDimension / Measure / Metric / Relationship
    + Event / StateModel / StateProjection
        |
        | ready typed semantic handles
        v
Canonical Analysis Materialization (`marivo.analysis`)
    events.match      -> EventFrame[journey]
    lifecycle.replay  -> LifecycleFrame[history]
    lifecycle.observe -> LifecycleFrame[snapshot | history]
        |
        v
Closed Derived Analysis
    Event/Lifecycle reducers + SubjectSet + exact compare/attribute
    -> typed artifacts -> Evidence Engine
```

The optional ontology extension and the future decision extension consume these
layers from above and are specified separately. Within this design the arrows
express allowed consumption, not shared ownership:

- semantic definitions never depend on ontology;
- Metric, Event, StateModel, and StateProjection execution never requires
  ontology;
- analysis does not redefine semantic objects;
- evidence records observations of committed artifacts but does not execute
  ontology.

Durable business meaning, execution policy, and computed facts remain separate:

- Event describes a reusable occurrence, not a funnel or matching strategy;
- StateModel describes a normative lifecycle, not replay execution;
- StateProjection describes an observed-state representation and closed decoding,
  not conformance or a second temporal contract;
- analysis policies own pattern matching, cohort selection, replay seeding,
  overlap, follow-up, declared completeness assumptions, and censoring;
- datasource execution evidence remains the only observed completeness
  authority; an analysis declaration is retained as an explicit weaker basis
  and never rewritten as an observed watermark;
- artifacts own computed facts, never business judgment.

### Public interface minimization rules

The extension applies one public-boundary rule consistently:

- a typed field ref supplies its owning Entity; callers never repeat that
  Entity;
- a directed Relationship path supplies its endpoint; callers never repeat the
  endpoint Entity, and omit the path when source and target are identical;
- a nested typed handle supplies its parent identity; a consumer never asks for
  both the handle and its Event, StateProjection, StateModel, or subject again;
- an execution choice becomes public only when v1 has at least two
  business-distinct legal values; fixed operator behavior belongs to the
  operator version;
- a new family dispatch reuses existing core parameter names and row-layout
  contracts unless the family requires genuinely new meaning;
- internal ordinals, fingerprints, and repeated plan facts remain artifact
  metadata; public rows use stable business coordinates and explicit nouns.

### Industry semantics adopted

This design borrows stable semantic boundaries, not vendor surface syntax:

- [OCEL 2.0](https://www.ocel-standard.org/) motivates occurrence identity,
  time, and qualified participant roles; v1 deliberately models only to-one
  analytical roles and defers multi-valued event-object relationships;
- SQL row-pattern recognition, represented by
  [Trino MATCH_RECOGNIZE](https://trino.io/docs/current/sql/match-recognize.html),
  motivates explicit subject partitioning, order, pattern, match selection, and
  rows-per-match semantics;
- [Apache Flink CEP](https://nightlies.apache.org/flink/flink-docs-stable/docs/libs/cep/)
  motivates keeping contiguity, overlap, and within-window policy orthogonal,
  while its streaming/late-event mechanics remain outside v1;
- [ClickHouse windowFunnel](https://clickhouse.com/docs/sql-reference/aggregate-functions/parametric-functions#windowfunnel)
  is an eligible backend lowering only when it preserves the canonical journey
  contract; its maximum-level result is not the public artifact;
- [PM4Py process mining](https://processintelligence.solutions/static/api/2.7.17/api.html)
  separates event logs, process models, discovery, and conformance; that
  motivates keeping StateModel, StateProjection, and replay execution distinct.

[Amplitude funnel behavior](https://amplitude.com/docs/analytics/charts/funnel-analysis/funnel-analysis-how-amplitude-computes)
remains a behavioral oracle for conversion windows and denominators, not the
DSL foundation. Marivo exposes the more fundamental journey facts required for
recovery, quality, composition, and attribution.

## Scope

This design adds:

- executable Event definitions over existing Entities;
- normative StateModel definitions over existing Entities and Events;
- executable StateProjection definitions over ordinary, snapshot, change-log,
  or validity Entities without requiring an SCD2 source;
- canonical journey, lifecycle-history, and lifecycle-snapshot materialization;
- funnel, time-to-event, lifecycle distribution, transition, dwell, and
  violation reducers;
- optional typed subject axes on funnel and lifecycle-distribution reducers,
  with exact temporal anchors and count reconciliation;
- stable typed `PatternStep`, `ProjectionStateHandle`, and `ModelStateHandle`
  values for public step/state selection and alignment without numeric public
  coordinates;
- explicit typed completeness declarations for sources that cannot expose an
  observed occurrence-time watermark, retained as assumptions in scope,
  quality, evidence, and lineage;
- exact funnel comparison and loss-rate attribution;
- a governed SubjectSet bridge for subject-compatible typed composition;
- additive `cohort=SubjectSet` admission on existing analysis entrypoints,
  including `observe`, without changing Metric calculation semantics;
- `EventFrame` and `LifecycleFrame` artifact families;
- event and lifecycle observation digests in the existing Evidence Engine.

This design does not add:

- a second executable semantic model;
- data-independent replacement identities for current Entities;
- automatic event-sequence selection;
- unrestricted row-pattern regular expressions, SQL, or CEP authoring;
- retention/cohort-return matrices or a repeat-pattern operator; retention is
  a distinct future analysis family rather than an overloaded funnel;
- arbitrary artifact joins or DataFrame re-entry;
- a catalog-promoted Funnel or governed named analysis-recipe registry;
- causal inference from funnel attribution;
- new Metric constructors or changes to current Metric semantics;
- the optional ontology extension, its `SemanticEdge` authoring, or its
  candidate bridge (specified separately);
- Rule, Policy, Action, permission, obligation, or write-back behavior.

The current coupling between an Entity and its physical source is accepted in
this increment. A future Semantic Core v2 may separately address portable
business identity or multiple executable representations.

## Stable Executable Semantic Core

### Existing objects remain authoritative

The active contracts in
[`semantic-object-model.md`](../../specs/semantic/semantic-object-model.md)
remain normative for:

- `Entity` as the executable logical recordset and root identity surface;
- `Dimension` and `TimeDimension` as typed grouping, filtering, and temporal
  fields;
- `Measure` as the authority for row-level numeric meaning, additivity, and
  unit;
- `Metric` as the governed executable aggregation or composition;
- `Relationship` as the explicit executable join path between Entities;
- semantic loading, verification, preview, readiness, and typed refs.

This design makes one narrow additive change to that core: Entity versioning
gains `ChangesVersioning` for business-effective change-point rows. Existing
snapshot and validity semantics remain Entity-owned and are not copied into
StateProjection.

No ontology annotation may override a semantic object's definition,
dependencies, source, expression, grain, additivity, unit, fanout policy,
time contract, or readiness result. If ontology text conflicts with the
semantic catalog, the executable semantic contract wins and ontology
validation reports the stale or contradictory annotation.

### Stable semantic identity

Existing semantic ids remain stable. As with every existing semantic kind, the
kind-relative path and canonical runtime key are distinct:

| Kind | Factory path | Canonical runtime key |
| --- | --- | --- |
| Event | `<domain>.<name>` | `event:<domain>.<name>` |
| StateModel | `<domain>.<name>` | `state_model:<domain>.<name>` |
| StateProjection | `<domain>.<name>` | `state_projection:<domain>.<name>` |

The exact factories are `ms.ref.event(path)`, `ms.ref.state_model(path)`, and
`ms.ref.state_projection(path)`. `SemanticKind`, ref serialization, catalog
lookup, details, lineage, readiness, and persistence add all three kinds without
introducing a second identity system.

Nested participant roles, LifecycleState and NormalizedState declarations, and
transitions are immutable values owned by their parent object. Participant
roles, loaded projection states, and model states expose typed handles through
`ms.participant_role(...)`, `ms.projection_state(...)`, and
`ms.model_state(...)` for analysis calls but do not become top-level catalog
objects.

## Event Semantic

### Event contract

`Event` is an executable semantic view over one existing `Entity`. It declares
the meaning of immutable occurrences represented by rows from that Entity.
It does not own a new source or join system.

Every Event declares:

- an ordered, non-empty `identity: tuple[DimensionRef, ...]`;
- exactly one `occurred_at: TimeDimensionRef`;
- one or more named participant roles;
- an optional restricted typed row predicate for a shared event Entity;
- `ai_context` containing the business definition.

The Event source Entity is mechanically resolved as `owner(occurred_at)`.
Every identity field must have that same owner. Event does not accept an
`entity` parameter: repeating it would add no business meaning and would create
an invalid state in which `entity` disagrees with the typed time-field owner.

The Entity key and Event identity serve different purposes. The Entity key
makes a source row addressable. Event identity names one business occurrence
within the filtered Event. Identity dimensions and `occurred_at` must belong to
the source Entity. Every identity component must be scalar, deterministic,
non-null, equality-comparable, and the tuple must be functionally unique.
Preview may measure uniqueness, but observed uniqueness never supplies or
overrides the business identity declaration.

`occurred_at` must have a resolvable timezone and cannot be a technical
ingestion timestamp when a business occurrence time is available. Ingestion
time, completeness watermarks, and arrival guarantees remain datasource
execution evidence.

The optional event predicate reuses the semantic validator's restricted
decorator-body model. A filtered Event is authored with `@ms.event(...)`; its
function receives one source-Entity alias and contains exactly one
`return <boolean Ibis expression>`. The expression may use only
`ms.bind(dimension_ref, entity_alias)` for declared Dimensions on that source
Entity, closed scalar constants, comparisons, and boolean composition. It
cannot use a bare `DimensionRef` as an expression, execute raw SQL, call
external state, aggregate, introduce a join, or redefine a Metric.

An Event with no predicate uses the body-free `ms.event(...)` constructor. The
decorator and body-free form produce the same `EventRef` family and persisted
Event IR; the predicate AST hash and compiled-expression sidecar are part of the
Event fingerprint. The fingerprint also covers the inferred source Entity,
identity and occurrence-time refs, and participant names, paths, inferred
endpoints, and cardinalities. This permits one physical event log to expose
several business Event definitions without making `Ref.__eq__` or another ref
operator an expression language.

### ParticipantRole

Each participant role declares:

- a stable role name unique within the Event;
- an optional directed `path: tuple[RelationshipRef, ...]` beginning at the
  inferred Event source Entity and ending at the participant Entity;
- closed cardinality `one | optional_one`.

The participant Entity is the path endpoint. Omit `path` when the Event source
Entity itself plays the role; an empty tuple is not a public value. Every
provided path must be non-empty, contiguous, directed, and executable under
existing Relationship readiness. The planner verifies that the declared
cardinality is compatible with the executable path; it never infers a
participant from matching field names or from an ontology edge. Cardinality
remains explicit business meaning because the current Relationship contract
does not declare it.

A role used as an analytical subject must have cardinality `one`. An
`optional_one` role may appear as context but cannot silently define the
population. Multi-valued participant roles are deferred until an exact
consumer owns fanout and deduplication semantics. If multiple eligible roles
identify the requested subject Entity, the caller must pass the typed role
handle; the planner never guesses.

### Event authoring shape

The target filtered authoring shape is additive to `marivo.semantic`:

```python
import marivo.semantic as ms

@ms.event(
    name="refund_requested",
    identity=(event_id,),
    occurred_at=event_ts,
    participants=(
        ms.participant(
            name="order",
            path=(event_to_order,),
            cardinality="one",
        ),
    ),
    ai_context=ms.ai_context(
        business_definition="A request to refund an order."
    ),
)
def refund_requested(event_rows):
    return ms.bind(event_type, event_rows) == "refund_requested"
```

`ms.event(...)` returns `EventRef`. `ms.participant(...)` returns an immutable
authoring value and is not exported as an independently loadable object. A
nested role is resolved later through the sole typed handle constructor
`ms.participant_role(event=<EventRef>, name=<str>)`; an `EventRef` remains a
data-only identity and does not acquire a dynamic `.participants` namespace.

## StateModel Semantic

### StateModel contract

`StateModel` defines one finite, normative lifecycle for a subject `Entity`.
An Entity may have several independent state models.

Every StateModel declares:

- `subject: EntityRef`;
- a closed, non-empty tuple of uniquely named `LifecycleState` authoring values;
- a closed set of deterministic transitions;
- `ai_context` containing the business definition.

Each `LifecycleState` declares a non-empty local name plus explicit
`initial: bool = False` and `terminal: bool = False` meaning. Exactly one state
is initial; zero or more states are terminal.
States and their initial/terminal meaning are declared once and reused by
transition authoring; callers do not repeat state names through separate
`states`, `initial`, `terminal`, `from_state`, and `to_state` string channels.

An ordinary `ms.transition(...)` declares one source LifecycleState, one target
LifecycleState, and one typed trigger through `on=...`. A separate
`ms.inception(on=...)` declares a trigger into the model's sole initial state;
it accepts no redundant target and no public sentinel such as
`from_state=None`.

`on` accepts either an `EventRef` or a `ParticipantRoleHandle`. An EventRef is
the minimal form when exactly one cardinality-one participant role ends at the
StateModel subject. If several roles qualify, validation fails with exact
`ms.participant_role(...)` repairs and the caller passes the selected handle as
`on`; that handle supplies both Event and role identity, so the Event is never
repeated in another parameter. A StateModel that supports Event replay must
have at least one deterministic inception or require an external state seed at
analysis time. For one source state and resolved trigger, at most one target
state is legal. An inception always enters the declared initial state and cannot
overlap another inception for the same trigger. Terminal states have no legal
outgoing transitions in v1.

A StateModel contains no replay window, source-selection policy, seed lookup,
illegal-event continuation policy, state projection, arbitrary predicate,
transition guard, permission, Rule, Policy, or Action. When a physical event
code implies different business transitions, authors define distinct Events
with explicit row predicates and reference those Events.

LifecycleState values are local authoring values because the parent StateModel
does not exist while its transitions are being declared. After the StateModel
is loaded, public analysis calls resolve a state only through
`ms.model_state(model=<StateModelRef>, name=<str>) -> ModelStateHandle`.
The handle carries the parent StateModel identity and state name; catalog
resolution verifies that the state still belongs to that model. A bare state
string is not accepted by an analysis selector or reducer. This prevents two
independent StateModels that both contain a state named `paid` from being
silently mixed.

### StateModel authoring shape

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
        ms.inception(
            on=order_created,
        ),
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
    ),
    ai_context=ms.ai_context(
        business_definition="Commercial order lifecycle."
    ),
)
```

`ms.state_model(...)` returns `StateModelRef`. States and transitions remain
owned immutable values. For example, analysis resolves the model-owned state
after loading:

```python
paid_state = ms.model_state(model=order_lifecycle, name="paid")
```

`ModelStateHandle` is nested identity, not a new catalog object, and the
StateModel ref remains data-only without a dynamic `.states` namespace.
The StateModel fingerprint covers the exact subject, ordered LifecycleState
names and initial/terminal flags, canonically resolved Event/participant triggers,
transitions, and semantic context. A ModelStateHandle carries that parent
fingerprint and local state name.

## StateProjection Semantic

`StateProjection` is a reusable executable representation of observed state.
It is independent of StateModel because one projection may be checked against
different normative models and one StateModel may be evaluated against several
operational projections. It is intentionally a thin observed-state adapter,
not a second state machine and not another owner of source time semantics.

Every StateProjection declares:

- one `state_field: DimensionRef` whose owning Entity supplies state rows and
  the temporal shape;
- an optional `subject_path: tuple[RelationshipRef, ...]` from the state field's
  owning Entity to the projection subject;
- a closed, non-empty `normalized_states: tuple[NormalizedState, ...]`
  defining the normalized state domain and physical source-value decoding;
- `ai_context` describing what the projection represents.

The names deliberately separate the three layers that the earlier
`state`/`states` pair conflated:

| Public name | Type | Meaning |
| --- | --- | --- |
| `state_field` | `DimensionRef` | physical source column whose values are decoded |
| `subject_path` | optional `tuple[RelationshipRef, ...]` | directed path whose endpoint is the lifecycle subject; omission means the state-field owner |
| `normalized_states` | `tuple[NormalizedState, ...]` | closed canonical business-state domain plus physical-value aliases |
| `ProjectionStateHandle` | loaded analysis identity | one normalized state owned and fingerprinted by the exact StateProjection |

StateProjection does not accept an `entity`, `subject`, an empty relationship
path, `valid_from`, `valid_to`, snapshot, or change-time parameters. The source
Entity is mechanically resolved as `owner(state_field)` and remains the single
owner of the temporal shape. The subject is that same Entity when
`subject_path` is omitted and otherwise the exact path endpoint. Repeating
either Entity would add no meaning and would create contradictory public
states. The projection fingerprints the exact inferred source and subject
identities and the source Entity versioning contract.

The admitted source shapes are:

| Inferred source Entity temporal shape | StateProjection meaning | Legal materialization |
| --- | --- | --- |
| no versioning | state captured by the concrete read | `LifecycleFrame[snapshot]` with actual `captured_at`; `scope` omitted |
| `ms.snapshot(...)` | state observed at one governed snapshot partition | `LifecycleFrame[snapshot]` at the selected partition instant |
| `ms.changes(...)` | each row makes a state effective until the subject's next change row | as-of snapshot or `LifecycleFrame[history]` |
| `ms.validity(...)` | each row owns an explicit effective interval | as-of snapshot or `LifecycleFrame[history]` |

After resolving `subject_path`, one materialized instant may contain at most
one non-null decoded state row per included subject. Zero rows or one null state
mean unknown state; multiple active rows fail projection integrity. This
subject-level invariant is stricter than row-level Entity identity and applies
to all four source shapes.

This extension adds `ms.changes(effective_at=<TimeDimensionRef>)` to the existing
Entity versioning union. `effective_at` is business-effective time, not
ingestion time. It must be a ready TimeDimension on the same Entity and
participate in that Entity's primary key; as with validity bounds, the planner
removes it from the effective versioned key during point-in-time resolution.
For one resolved projection subject, change rows must have unique normalized
`effective_at` values. Materialization derives
`[effective_at, next_effective_at)` intervals; ambiguous same-time changes fail
rather than using row order, and the final interval remains open until runtime
coverage classifies its right boundary. Authors do not manufacture `valid_to`
for a source that naturally stores change points.

A validity Entity admitted to StateProjection must use
`interval="closed_open"`. Its `valid_from` is the inclusive business-effective
start, `valid_to` is the exclusive first instant at which the row is no longer
effective, and Entity-owned `open_end` values mean “still effective.” Bounds are
not ingestion timestamps. Intervals must be ordered and non-overlapping per
resolved subject. A gap establishes unknown state for that time; StateProjection
never extends the prior interval to fill it. A `closed_closed` validity Entity
is rejected because adjacent boundary ownership would be ambiguous.

Each `NormalizedState` has one non-empty local name and a non-empty tuple of
closed scalar `source_values` compatible with `state_field`. State names and
source values must be unique within their respective domains, source-value sets
must be pairwise disjoint, `None` is not a state value, and wildcard, regex,
callable, and catch-all decoding are outside v1. Unmapped non-null source values
fail closed at preview or execution instead of silently becoming a new state.
Null or absent state rows remain explicit unknown coverage; they are never
coerced to an initial state.

The authored `normalized_states` tuple and its `source_values` are the governed
closed-domain assertion. Preview can disprove it by finding an unmapped or
multiply mapped value, but a bounded preview cannot prove that no future value
exists. Execution therefore revalidates every materialized value against the
same decoding contract.

```python
order_normalized_states = (
    ms.normalized_state(
        name="created",
        source_values=("CREATED", "NEW"),
    ),
    ms.normalized_state(
        name="paid",
        source_values=("PAID", "CAPTURED"),
    ),
    ms.normalized_state(
        name="fulfilled",
        source_values=("FULFILLED",),
    ),
    ms.normalized_state(
        name="cancelled",
        source_values=("CANCELLED",),
    ),
)

current_order_state = ms.state_projection(
    name="current_order_state",
    state_field=order_status,
    normalized_states=order_normalized_states,
    ai_context=ms.ai_context(
        business_definition="Current operational order status."
    ),
)

order_state_projection = ms.state_projection(
    name="order_state_history",
    state_field=state_code,
    subject_path=(state_row_to_order,),
    normalized_states=order_normalized_states,
    ai_context=ms.ai_context(
        business_definition="Operational order-state history."
    ),
)
```

The first projection is the minimal current-status case: `orders` is both
source and subject because `owner(order_status)` is inferred and
`subject_path` is omitted. It needs no versioning and admits only an omitted
analysis scope. The second projection infers `order_state_history` from
`owner(state_code)` and infers `orders` from the `subject_path` endpoint. That
source Entity owns either
`versioning=ms.changes(effective_at=state_changed_at)` or a compatible existing
`ms.validity(...)` declaration. The Entity changes the admitted analysis scope
and output shape; StateProjection does not acquire temporal parameters.

After catalog load, each NormalizedState resolves to a projection-owned
ProjectionStateHandle only through:

```python
paid_projection_state = ms.projection_state(
    projection=order_state_projection,
    name="paid",
)
```

`ProjectionStateHandle` carries the parent StateProjection identity and
normalized state name. A bare state string is not accepted by analysis
alignment. Physical source literals remain authoring inputs because they
describe stored values; they never become semantic identity.

The StateProjection fingerprint covers the exact inferred source Entity ref and
versioning fingerprint, inferred subject and `subject_path` refs, `state_field`
ref, ordered NormalizedState definitions, and semantic context. `source_values`
order is not semantic and is canonically normalized within each normalized
state; the declared `normalized_states` order remains the stable display order.
A ProjectionStateHandle carries the parent projection fingerprint so a
same-named state from another projection cannot compare equal.

StateProjection does not claim conformance to a StateModel merely because
values share names. Projection-to-model correspondence is a typed analysis
alignment defined when the projection is observed, not semantic decoding stored
inside StateProjection.

## Semantic Validation and Readiness

Event, StateModel, and StateProjection verification extends the existing
semantic pipeline; it does not create another route.

Declaration validation checks local types, closed enums, source ownership,
identity requirements, temporal types, unique role/LifecycleState/NormalizedState
names, exactly one initial LifecycleState, terminal-state closure, disjoint
NormalizedState source values, and transition determinism. Catalog assembly
resolves every typed ref, infers source/subject endpoints, and rejects cycles in
participant or StateProjection Relationship paths. Preview checks the Event
predicate, identity uniqueness evidence, occurrence-time normalization,
participant coverage, normalized-state decoding coverage, current/snapshot
subject uniqueness, change-time uniqueness, and validity-interval integrity
under the existing scoped query contract.

Readiness is task-specific:

- Event readiness requires a ready `owner(occurred_at)` Entity, same-owner Event
  identity, requested roles, and every Relationship on their paths;
- StateModel readiness requires ready resolved triggers, subject roles, closed
  LifecycleStates, deterministic transitions, and a legal inception/seed
  capability;
- StateProjection readiness requires a ready `owner(state_field)` Entity, an
  omitted same-Entity or exact cross-Entity `subject_path`, closed normalized
  state decoding, and one admitted Entity temporal shape; it does not duplicate
  or repair Entity versioning;
- Metric readiness is unchanged and never depends on Event, StateModel,
  StateProjection, or ontology unless a Metric explicitly adopts a future
  semantic dependency.

Request-window completeness, actual watermarks, late-arrival status, and
whether a concrete replay has enough history are runtime quality facts. They
must not be reported as query-free semantic readiness.

No ontology edge can satisfy a failed semantic readiness check.

## Typed Event Analysis

Event analysis first materializes matching once, then applies closed reducers:

```text
events.match         -> EventFrame[journey]
EventFrame[journey]  -> events.funnel        -> EventFrame[funnel]
EventFrame[journey]  -> events.time_to_event -> EventFrame[time_to_event]
```

### Closed EventPattern

`events.match` accepts one typed EventPattern. V1 supports only an ordered
sequence of
`mv.step(participant=<ParticipantRoleHandle>, key=<str>) -> PatternStep`
values. The participant handle supplies both the EventRef and subject Entity;
neither is repeated in PatternStep or `events.match`. Every step in one pattern
must resolve to the same cardinality-one participant Entity. Each `key` is a
stable non-empty lowercase snake-case identifier matching
`[a-z][a-z0-9_]*`, unique within its EventPattern. It is analysis identity
rather than a display label and therefore participates in the pattern
fingerprint.

`PatternStep` is an immutable analysis value containing the key and
participant-role handle, from which EventRef and subject are mechanically
resolved. Its canonical fingerprint covers that complete payload. The
containing EventPattern additionally persists its inferred subject and ordered
step fingerprints, so a consumer can prove exact membership without relying on
position. A structurally identical PatternStep may be deliberately reused in
another pattern; a same-key value with a different EventRef or role is a
different step and fails membership. PatternStep is not a semantic object or a
top-level catalog ref: Event defines reusable occurrence meaning, while a step
defines that Event's role in one ordered analysis pattern.

All public step-consuming APIs accept a `PatternStep` whose complete
fingerprint is present exactly once in the source EventPattern. They reject a
positional integer, bare step-key string, `EventRef`, or a same-key step with a
different definition before datasource execution. An EventRef alone is
insufficient because two explicit steps may reference the same EventRef or use
different participant roles. Persisted artifacts retain the complete
PatternStep values, and recovered `contract()` output reconstructs
copy-pasteable `mv.step(...)` values rather than numeric selectors.

V1 ordered matching has one fixed eventually-followed-by contiguity contract,
so no `contiguity` parameter or `mv.eventually()` policy is public. Direct
adjacency, alternation, negation, optional steps, repeat operators, and arbitrary
row predicates are outside v1. Explicit ordered steps may reference the same
EventRef more than once when their keys are distinct.

Repeating one EventRef in several explicit PatternSteps answers an ordered
occurrence question such as “first purchase, then another purchase.” It does not
define retention, where one anchored cohort is evaluated independently in
several return-time buckets. A future `events.retention` family must own bucket
assignment, return-event reuse, cohort denominators, and its own artifact shape;
retention is never inferred from `repeat(...)` or synthesized as an eight-step
funnel.

EventPattern and PatternStep are immutable, fingerprinted analysis values and
may be defined once in a shared project Python module and imported by several
analysis scripts. They are not catalog semantic objects and cannot be promoted
to Funnel refs. Organization-wide discovery, approval, and versioning of named
analysis recipes is a separate future analysis-governance capability; this
design neither hides that policy in Event semantics nor invents a recipe
registry.

The EventPattern supplies the subject identity. The caller supplies a half-open
cohort window, `completion_through >= end`, and one matching policy. Optional
typed completeness declarations may supplement missing runtime watermark
evidence but remain explicit assumptions. An optional SubjectSet may narrow the
population only when its subject identity matches. Ontology may be shown as
navigation context, but it never fills the EventPattern.

### Closed matching policies

V1 supports:

- `mv.first_per_subject()`: one journey anchored at the earliest first-step
  occurrence in the cohort window; later steps consume the earliest unused
  eligible occurrence;
- `mv.every_start(completion_assignment="exclusive")`: one attempt per
  first-step occurrence; a terminal occurrence closes only the earliest open
  eligible attempt;
- `mv.every_start(completion_assignment="shared")`: one attempt per first-step
  occurrence; the earliest eligible terminal occurrence may close several
  attempts.

Funnel accepts only `first_per_subject`. Repeated-attempt time-to-event consumes
an `every_start` journey. One occurrence cannot fill two steps within one
journey, including when the pattern repeats the same EventRef.

All policies sort by normalized `occurred_at` and then by a compatible declared
Event identity order. If different Event types share an instant and swapping
them changes assignment, matching fails with `ambiguous_event_order`. Event
type name, declaration order, and tuple position are not chronology fallbacks.

### Matching and follow-up choice guidance

Matching policy, follow-up horizon, and completeness basis are required
business-caliber choices rather than tuning knobs. `mv.help(...)` must describe
both their mechanical contracts and when each answers the intended question:

| Choice | Business interpretation | Required caution |
| --- | --- | --- |
| `mv.first_per_subject()` | one subject-level conversion journey anchored at the earliest first step | later starts are intentionally excluded; this is the only funnel policy |
| `mv.every_start(completion_assignment="exclusive")` | one attempt per start, with one terminal occurrence assigned to at most one attempt | use when a completion belongs to one attempt |
| `mv.every_start(completion_assignment="shared")` | one attempt per start, where overlapping attempts may share a later terminal occurrence | use only when that shared completion is business-correct |
| `completion_through` | the latest follow-up instant required to classify a missing later step | it requests coverage but never proves coverage |
| `mv.declared_complete_through(...)` | an explicit caller assertion that named inputs are complete through an instant | it is weaker than an observed watermark and must include rationale |

There is no implicit matching-policy choice and no automatic completeness
declaration. `show()`, `contract()`, quality, evidence scope, and recovery expose
the selected policy and whether classification relied on observed, declared,
mixed, or unknown completeness. Quality reports do not rematch Events under an
alternative policy or invent a “better” choice; policy sensitivity, when
needed, is a separate explicit analysis.

### Canonical journey materialization

```python
cart_user = ms.participant_role(event=cart_created, name="user")
checkout_user = ms.participant_role(event=checkout_started, name="user")
payment_user = ms.participant_role(event=payment_succeeded, name="user")

cart_step = mv.step(participant=cart_user, key="cart")
checkout_step = mv.step(
    participant=checkout_user,
    key="checkout",
)
payment_step = mv.step(
    participant=payment_user,
    key="payment",
)

checkout_pattern = mv.sequence(
    cart_step,
    checkout_step,
    payment_step,
)

journeys = session.events.match(
    pattern=checkout_pattern,
    cohort=eligible_users,  # optional ready SubjectSet; omit for all subjects
    cohort_window=mv.window(start, end),
    completion_through=followup_end,
    matching=mv.first_per_subject(),
)
```

When one or more Event inputs cannot expose a runtime watermark, the caller may
make a bounded declaration over exact inputs:

```python
declared_event_coverage = mv.declared_complete_through(
    inputs=(cart_created, checkout_started, payment_succeeded),
    through=followup_end,
    rationale="The daily warehouse backfill was reconciled through followup_end.",
)

journeys = session.events.match(
    pattern=checkout_pattern,
    cohort_window=mv.window(start, end),
    completion_through=followup_end,
    matching=mv.first_per_subject(),
    completeness=(declared_event_coverage,),
)
```

`completeness` is a tuple of typed `CompletenessDeclaration` values and defaults
to empty. A declaration always names `inputs=<non-empty tuple>`: either one or
more exact EventRefs, or exactly one StateProjectionRef. Mixed Event/projection
tuples are invalid. It also declares one `through` instant in the relevant Event
occurrence-time or inferred projection-source Entity temporal domain and a
non-empty rationale. It cannot target an Entity, datasource, pattern, or
free-text id because those shapes do not prove which analytical inputs the
caller meant. Declarations are analysis-owned assumptions; they do not mutate
datasource state, Entity versioning, semantic readiness, or future operations.

An unversioned current-state projection has no governed business-time domain and
therefore cannot be named by `declared_complete_through`. Its runtime capture
clock is lineage, not a caller-known completeness bound. If the source exposes a
business refresh or effective time, authors model that authority through
snapshot, changes, or validity versioning instead of relabeling query time.

`EventFrame[journey]` is a stable long table with one row per materialized
journey step:

```text
journey_id
completion_status       complete | incomplete | coverage_censored
subject_identity
step_key
event_identity
occurred_at
elapsed_from_start
elapsed_from_previous
```

`step_key` is the stable reader-facing coordinate. Public operator arguments
still use the complete PatternStep value so key reuse cannot weaken type
safety. Deterministic row order comes from the persisted EventPattern order;
serialization may retain a private ordinal, but it is not a public data column
or selector. The PatternStep already resolves the EventRef, and `journey_id`
already identifies one materialized attempt, so neither a repeated `event_ref`
nor an `attempt_index` is public row state. Both remain recoverable from
artifact metadata when needed for audit.

`journey_id` is a stable hash of operator version, subject identity, pattern
fingerprint, matching-policy payload, and anchor Event identity. A missing step
is `incomplete` only when observed watermark evidence or an exact declared basis
covers every required Event input through `completion_through`; otherwise it is
`coverage_censored`. The artifact retains the basis used for every input, so
declared classification cannot render as an observed non-completion.

### Subject-axis enrichment

Funnel and lifecycle-distribution reducers accept optional
`axes=[DimensionRef, ...]`. Every axis must be a ready Dimension on the subject
Entity or reachable from it through one executable to-one Relationship path.
Bare names, to-many paths, ambiguous paths, and axes without a compatible
point-in-time contract fail before enrichment.

Axis values use an operator-owned temporal anchor:

- Event funnel uses the subject's cohort-entry/first-step occurrence time;
- lifecycle distribution uses each exact persisted or requested `as_of`
  instant.

Axis order, refs, paths, temporal anchors, and null handling participate in the
artifact fingerprint. A null axis value remains an explicit group and is never
silently dropped. Enrichment may query the governed subject Dimensions and
Relationship paths required for those values, but it never queries Event
occurrence inputs, rereads StateProjection rows, rematches a journey, or replays
a lifecycle. The derived artifact retains the source materialization as its
assignment authority.

### Funnel

`events.funnel(journeys, axes=())` accepts only `EventFrame[journey]` produced
with `mv.first_per_subject()`. With no axes it emits one row per PatternStep.
With axes it emits one row per exact axis-value tuple and PatternStep:

```python
channel_funnel = session.events.funnel(
    journeys,
    axes=[acquisition_channel, plan_tier],
)
```

```text
<one column per declared axis, in declared order>
step_key
cohort_count
resolved_cohort_count
entry_count
resolved_entry_count
reached_count
lost_count
conversion_from_first
conversion_from_previous
loss_rate_from_previous
coverage_censored_count
```

Every funnel row corresponds one-to-one with its source PatternStep.
`step_key` is its stable reader-facing coordinate; typed consumers use the
complete PatternStep, which also supplies the EventRef. Deterministic
display/export order comes from the persisted EventPattern rather than a
public numeric coordinate.

`cohort_count` is the first-step population. `resolved_cohort_count` excludes
subjects whose reach status for this step is coverage-censored. For the first
step, entry and resolved entry equal reached, lost is zero, and previous-step
rates are null. For grouped output the same definitions apply within each exact
axis tuple. For later steps:

```text
entry_count          = preceding reached_count
resolved_entry_count = entry_count - coverage_censored_count
lost_count           = resolved_entry_count - reached_count
conversion_previous  = reached_count / resolved_entry_count
loss_rate_previous   = lost_count / resolved_entry_count
conversion_first     = reached_count / resolved_cohort_count
```

Zero denominators produce null. Censored subjects remain separate and never
become lost subjects. The reducer reads persisted journey assignment; it never
queries Event sources or rematches occurrences. For every PatternStep, additive
counts across all grouped axis tuples must exactly equal the corresponding
ungrouped counts, including the explicit null group; rates are recomputed from
group components and are never summed or averaged. A failed reconciliation
rejects the grouped artifact.

### Time to event

`events.time_to_event(journeys, start_step=<PatternStep>,
end_step=<PatternStep>)` projects one row per qualifying journey attempt. Both
steps must belong to the source EventPattern and `start_step` must precede
`end_step`:

```python
checkout_to_payment = session.events.time_to_event(
    journeys,
    start_step=checkout_step,
    end_step=payment_step,
)
```

```text
journey_id
subject_identity
start_event_identity
start_time
end_event_identity
end_time
duration
completion_status       complete | incomplete | coverage_censored
```

Matching and end-consumption semantics are inherited from the journey artifact
and cannot be overridden. The exact start/end PatternSteps live once in
artifact metadata rather than repeating identical keys on every attempt row.
The source `journey_id` already identifies the attempt, so a derived numeric
attempt ordinal is likewise not a public column.
`incomplete` is legal only when the source journey has complete follow-up
coverage. Both Event shapes use the same closed completion vocabulary.

## Typed Lifecycle Analysis

Lifecycle analysis materializes state once and then applies closed reducers:

```text
lifecycle.replay(StateModel)                       -> LifecycleFrame[history]
lifecycle.observe(StateAlignment)                  -> LifecycleFrame[snapshot | history]
LifecycleFrame -> lifecycle.distribution / transitions / dwell / violations
```

### Replay materialization

```python
history = session.lifecycle.replay(
    order_lifecycle,
    window=mv.window(start, end),
    seed=mv.from_inception(),
)
```

Closed seed policies are `mv.from_inception()` and
`mv.from_projection(snapshot)`, where `snapshot` is a committed, model-aligned
`LifecycleFrame[snapshot]` produced by `lifecycle.observe`. Replay reconstructs
state before clipping to the requested window and never assumes all subjects
were in the initial state at the window boundary. Missing history raises
`insufficient_state_history` when complete inputs prove that no inception/seed
exists; unknown prior coverage yields coverage-censored subjects. This is fixed
classification, not a caller policy. `from_projection` never accepts a
StateProjectionRef plus an as-of timestamp and never rereads the projection
during replay; the exact snapshot artifact is the seed authority.

Replay completeness combines runtime watermark evidence and declarations for
the exact transition EventRefs it consumes. A declaration for an Event outside
the selected StateModel is rejected rather than retained as unused scope.

V1 has one fixed violation contract: an illegal modeled Event produces a
replay-trace violation, leaves state unchanged, and allows later Events to be
evaluated. Because callers have no alternative behavior to choose, replay does
not expose an `on_violation` parameter or `mv.record_and_continue()` policy in
v1. The fixed contract belongs to the replay operator version, not the
StateModel fingerprint. Events absent from StateModel are not replay inputs or
violations.

Within one subject, replay orders Events by normalized `occurred_at` and a
compatible identity order. If ambiguous same-time order changes state, replay
fails with `ambiguous_event_order`.

`LifecycleFrame[history]` is a stable interval table:

```text
subject_identity
model_state
valid_from
valid_to
entered_by_event_ref
entered_by_event_identity
exited_by_event_ref
exited_by_event_identity
interval_status          completed | right_censored | coverage_censored
```

The committed artifact retains the replay trace required for deterministic
violation projection. Reducers never replay Events again.

### Projection observation

Projection observation infers current scope only when the source has no
versioning. Every temporally versioned source requires one explicit scope:

| Entity temporal shape | Legal scope |
| --- | --- |
| no versioning | omit `scope`; current capture is the only legal operation |
| `ms.snapshot(...)` | `mv.as_of(t)` selecting the governed partition for `t` |
| `ms.changes(...)` | `mv.as_of(t)` or `mv.window(start, end)` |
| `ms.validity(...)` | `mv.as_of(t)` or `mv.window(start, end)` |

An ordinary current-state Entity is captured at the concrete read instant. It
cannot answer a historical as-of request. A snapshot Entity establishes state
at the actual selected partition instant and does not silently carry an older
partition forward when the governed partition is absent. Change-log and
validity sources can materialize either a point-in-time snapshot or interval
history. Scope mismatch fails before source execution.

Projection decoding and model alignment are separate contracts:

- StateProjection semantic authoring decodes closed physical source values to
  `ProjectionStateHandle` identities;
- `mv.state_alignment(...)` maps those handles to `ModelStateHandle` identities
  for one exact StateModel at analysis time.

`StateAlignment` is an immutable, fingerprinted analysis value containing a
non-empty tuple of
`mv.align_state(projection_state=..., model_state=...)` pairs. Every
normalized projection state and every model state must appear exactly once in
v1, so the alignment is a total bijection. Both sides must belong to the
alignment's exact StateProjection and StateModel. Bare strings, cross-owner
handles, duplicate sources or targets, partial mappings, and many-to-one
collapsing fail before querying. Physical aliases are normalized inside
`NormalizedState`; v1 alignment does not collapse business states.

Its fingerprint covers the exact StateProjection and StateModel refs and
semantic fingerprints plus the pairs canonically ordered by
ProjectionStateHandle. Analysis-session or job audit metadata remains lineage,
not StateAlignment identity.

When the two closed domains intentionally use the same state names, callers
still request the typed operation explicitly:

```python
current_order_alignment = mv.identity_state_alignment(
    projection=current_order_state,
    model=order_lifecycle,
)

current_order_snapshot = session.lifecycle.observe(
    alignment=current_order_alignment,
)
```

The constructor verifies exact domain equality and returns an ordinary
StateAlignment; shared labels never trigger implicit alignment. For differently
named domains:

```python
order_state_alignment = mv.state_alignment(
    mv.align_state(
        projection_state=ms.projection_state(
            projection=order_state_projection,
            name="created",
        ),
        model_state=ms.model_state(model=order_lifecycle, name="created"),
    ),
    mv.align_state(
        projection_state=ms.projection_state(
            projection=order_state_projection,
            name="paid",
        ),
        model_state=ms.model_state(model=order_lifecycle, name="paid"),
    ),
    mv.align_state(
        projection_state=ms.projection_state(
            projection=order_state_projection,
            name="fulfilled",
        ),
        model_state=ms.model_state(model=order_lifecycle, name="fulfilled"),
    ),
    mv.align_state(
        projection_state=ms.projection_state(
            projection=order_state_projection,
            name="cancelled",
        ),
        model_state=ms.model_state(model=order_lifecycle, name="cancelled"),
    ),
)
```

The alignment carries its exact StateProjection and StateModel identities, so
`lifecycle.observe` accepts the alignment as its source and repeats neither
`projection=` nor `model=`:

```python
observed_order_history = session.lifecycle.observe(
    alignment=order_state_alignment,
    scope=mv.window(start, end),
)
```

Point-in-time scopes emit `LifecycleFrame[snapshot]` with
`subject_identity, model_state, as_of`. `as_of` is the actual capture instant
for an unversioned current source, the selected governed partition instant for
a snapshot Entity, and the requested effective instant for a change-log or
validity Entity. The source temporal kind and requested/actual instants remain
distinct metadata, so no value is presented as a fabricated business
timestamp. Change-log and validity window scopes emit
`LifecycleFrame[history]` using the same interval row contract as replay with
projection-specific lineage. The public row uses the unambiguous
`model_state`; the exact ProjectionStateHandle remains recoverable through the
persisted total-bijective alignment. Artifact metadata retains both handle
domains and the alignment fingerprint without duplicating them as two state
columns.

`lifecycle.observe(..., completeness=...)` optionally accepts declarations only
for the alignment's exact StateProjectionRef and selected scope end or as-of
instant. Omit the argument when there are no declarations. An unversioned
current source rejects a non-empty declaration because it has no caller-known
business-time bound. A declaration never adds historical capability to a
current or snapshot source, repairs invalid decoding/alignment, resolves
duplicate subjects or same-time change rows, fills validity gaps, repairs
overlapping intervals, or satisfies semantic readiness.

### Lifecycle execution choice guidance

`mv.help(...)` must state the business and history preconditions of each
lifecycle choice:

| Choice | Use when | Reject or disclose |
| --- | --- | --- |
| `mv.from_inception()` | replay input includes a deterministic inception event before the requested state interval | missing inception/history cannot be replaced by assuming the initial state |
| `mv.from_projection(snapshot)` | a compatible committed LifecycleFrame snapshot supplies the seed before replay begins | rejects a raw StateProjectionRef, mismatched model/scope, or a snapshot after replay begins; no projection reread |
| `mv.declared_complete_through(...)` | named replay Events or a snapshot/change/validity projection are asserted complete through a governed bound | the declaration remains an assumption, rejects unversioned current projections, and cannot establish semantic readiness |

Replay seed and completeness basis have no agent-selected hidden default.
Violation handling is the fixed v1 replay contract rather than a policy slot.
`show()`, quality, evidence, and recovery expose the exact seed, completeness
basis, and fixed replay contract without declaring which business policy should
exist in a future extension.

### Closed lifecycle shapes

Lifecycle distribution uses two closed input shapes:

- `lifecycle.distribution(snapshot, axes=())` evaluates the snapshot's exact
  persisted `as_of`;
- `lifecycle.distribution(history, at=(t1, ...), axes=())` requires a non-empty
  tuple of explicit instants inside the history scope.

History never invents a sampling grain or bucket schedule. Each requested
instant is normalized through the existing time contract; duplicates and
out-of-scope instants fail, while valid instants are canonically ordered and
fingerprinted. Supplying `at` for a snapshot or omitting it for history fails
before execution.

```python
state_by_region = session.lifecycle.distribution(
    history,
    at=(week_1_end, week_2_end, week_3_end),
    axes=[account_region],
)
```

```text
<one column per declared axis, in declared order>
as_of
model_state
subject_count
share
```

Its population is the distinct requested subject identities whose model state
is established at that as-of instant by the source artifact. `share`
divides by that known-state population. Subjects not yet seeded, lacking
history, or censored by coverage are excluded from the denominator and retained
as separate metadata counts; they are never assigned the initial state.
Grouped distribution uses the subject-axis enrichment contract above at each
as-of instant. For each instant and model state, grouped `subject_count` values
including the null-axis group reconcile exactly to the ungrouped count. `share`
is recomputed against the known-state population within each axis tuple and is
never summed across groups.

`lifecycle.transitions(history)` emits one row per model-state pair:

```text
from_model_state
to_model_state
transition_status       modeled | unmodeled_projection
transition_count
share_of_modeled_transitions
```

Replay history emits only `modeled`: an illegal Event leaves state unchanged,
so it never forms an interval transition and remains in `violations`.
Projection history classifies every adjacent aligned interval pair against the
selected StateModel. A pair absent from the normative transition set is emitted
as `unmodeled_projection`, counted, excluded from the modeled denominator, and
surfaced by `QualityReport[lifecycle]`; it is not rewritten as a replay
violation because no trigger Event is known.

`share_of_modeled_transitions` divides modeled rows by all modeled transitions
in the requested half-open window and is null for unmodeled-projection rows. A
zero modeled denominator produces null.

`lifecycle.dwell(history)` emits one row per model state:

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

Completed intervals end at the next valid transition. The final open interval
is right-censored at the requested window end only when coverage is proven
through that instant; otherwise it is coverage-censored and excluded from
duration aggregates. Duration statistics use completed intervals only, while
the two censoring counts remain explicit metadata and quality inputs.

`lifecycle.violations(history)` accepts only replay-produced history and emits
one row per persisted replay-trace violation:

```text
subject_identity
trigger_event_ref
trigger_event_identity
occurred_at
model_state_at_event
violation_kind          illegal_transition | transition_from_terminal
```

A violation is an observation of the declared StateModel applied to observed
Events. It is not automatically a policy breach, data-quality failure,
business Rule, or causal fact.

## SubjectSet Bridge

`session.select_subjects(artifact, selection=...)` is the sole v1 cross-family
selection bridge. It accepts only registered Event/Lifecycle shapes and emits
`SubjectSet` with subject EntityRef and identity signature, source artifact and
fingerprint, one closed selection, coverage/censoring, immutable lineage, and
session ownership.

Its exact row contract is one row per deterministically selected subject:

```text
subject_identity
```

Subjects whose selection depends on unknown follow-up remain excluded and are
counted in metadata. A `SubjectSet[coverage_censored]` remains inspectable but
cannot scope another typed operator; the caller must repair coverage or choose
a selection whose truth is established. The one typed selection and its
parameters live once in artifact metadata and lineage rather than being
repeated as an identical reason string on every subject row.

```python
dropouts = session.select_subjects(
    journeys,
    selection=mv.dropped_before(step=payment_step),
)

revenue = session.observe(
    metric=revenue_metric,
    cohort=dropouts,
    time_scope={"start": start, "end": end},
)
```

For a non-initial PatternStep, `mv.dropped_before(step=target_step)` selects
subjects that reached the immediately preceding step but did not reach the
target step under complete follow-up. It therefore selects the same resolved
lost population used by that target's funnel row. The selector rejects an
initial step, a step not retained by the source pattern, a positional index, or
coverage-censored truth. Naming the missing target is intentional:
`payment_step` directly expresses payment loss and remains stable if earlier
pattern steps are inserted.

Lifecycle selections use equally closed constructors with a typed model-state
handle:

```python
paid_state = ms.model_state(model=order_lifecycle, name="paid")

paid_at_end = session.select_subjects(
    history,
    selection=mv.in_state(paid_state, as_of=end),
)
```

`mv.in_state(...)` rejects a bare string and a state handle owned by a different
StateModel. SubjectSet is a typed selection plan, not a generic table. A ready
SubjectSet may be passed only as the `cohort` input to `observe`,
`events.match`, `lifecycle.replay`, or `lifecycle.observe`, and only when
subject Entity, identity signature, and owner match. Its digest never contains
raw identities.
`to_pandas()` remains terminal and cannot construct SubjectSet or re-enter
typed analysis.

## Funnel Comparison and Attribution

`session.compare` gains one exact non-Metric dispatch:

```text
EventFrame[funnel] x EventFrame[funnel] -> DeltaFrame[funnel]
```

Inputs must have identical subject identity, EventPattern fingerprints and
PatternStep keys/definitions, matching policy, follow-up bound, and
identical axes/path/temporal-anchor contracts, and compatible completeness.
The funnel dispatch has one mechanically determined alignment: persisted
PatternStep identity plus the exact axis-value tuple. `session.compare` derives
that alignment from the two compatible funnel artifacts and accepts no
`alignment` argument for this dispatch; exposing a `mv.funnel_stage()` value
would ask the caller to repeat the only legal implementation. It never aligns
by numeric position. The delta retains current/baseline
cohort, resolved cohort, entry, resolved entry, reached, lost, and censored
counts plus the loss-rate delta at each PatternStep and axis tuple. It also retains
source journey artifact refs for exact re-aggregation. Every delta row carries
the declared axis columns and `step_key`; downstream step selection uses the
retained PatternStep rather than a scalar coordinate.

Grouped comparison aligns the full outer union of exact axis tuples. When a
fully materialized side has no row for one tuple, its additive count components
are zero for that tuple and its rates are recomputed with normal zero-denominator
null semantics; a rate is never copied from the other side. Missing tuples are
not zero-filled when source coverage or grouped reconciliation is unresolved.

Observed and declared completeness bases may differ between otherwise fully
classified inputs, but each side remains explicit in scope and quality. A
coverage-censored population that affects an aligned PatternStep still rejects
comparison.

`session.attribute` gains one exact dispatch:

```text
DeltaFrame[funnel] -> AttributionFrame[funnel_loss_rate]
```

V1 attribution accepts only an ungrouped `DeltaFrame[funnel]`. Attribution
itself introduces its driver axes and re-aggregates the retained source journey
membership; admitting an already grouped delta would ambiguously mix descriptive
breakdown with hierarchical contribution. The structured repair tells the
caller to compare the corresponding ungrouped funnels, then pass the desired
driver axes to `session.attribute(...)`.

```python
delta = session.compare(
    current_funnel,
    baseline_funnel,
)

drivers = session.attribute(
    delta,
    axes=[account_region, acquisition_channel, plan_tier],
    mode="joint",
    target=mv.funnel_loss_rate(step=payment_step),
)
```

`mv.funnel_loss_rate(...)` accepts only a PatternStep retained by the compared
funnels. It rejects an integer position, bare step-key string, bare EventRef,
or same-key step with a different EventRef or participant role. The selected
target is the loss from its immediately preceding PatternStep into that step.

Attribution operates on additive `lost_count` and `resolved_entry_count`
components using ratio-mix decomposition. Driver axes reuse the subject-axis
enrichment contract and the funnel cohort-entry anchor. Event-step properties,
ambiguous time ownership, and any to-many axis path are rejected. Attribute
re-aggregates persisted journey membership over those axes through governed
subject relationships; it never rematches Events.

Funnel attribution reuses the existing `session.attribute` layout contract
rather than introducing an `axis_ref/axis_value` shape. One axis omits `mode`
and preserves that concrete Dimension column. Multiple axes require
`mode="joint"` for one additive row per full axis tuple or
`mode="hierarchy"` for prefix drill-down whose parent rows repeat descendant
totals. Joint rows preserve one concrete column per declared axis; hierarchy
rows use the existing `level`, `axis`, `driver`, and `path` columns. Every
layout also includes:

```text
contribution_kind      loss | denominator_mix
contribution
share_of_total_delta
share_of_positive_pool
share_of_negative_pool
```

Artifact metadata retains the exact target PatternStep once, together with
positive/negative contribution pools, residual, and exact reconciliation; it
does not repeat the same target key on every contribution row. Raw loss-rate
point estimates are never summed or averaged. The result describes arithmetic
contribution to an observed change, not treatment effect, counterfactual, or
causality.

Mismatched cohorts, matching policies, PatternStep definitions, censored
comparison populations, unsupported axes/fanout, missing additive components,
or failed reconciliation reject the operation.

## Runtime Completeness and Censoring

Analysis distinguishes requested read range, observed source extent, observed
completeness evidence, and declared completeness assumptions. Datasource or
snapshot execution evidence is the only authoritative observed basis. An Event,
change-log, or validity-history input is observed complete through instant `t`
only when that evidence establishes a watermark at or after `t` in its governed
business-time domain. A current or snapshot projection is observed complete at
one actual capture or partition instant only when source execution evidence
establishes the complete subject population for that exact snapshot; successful
query execution alone does not. A configured lateness SLA is advisory and
proves neither kind of completeness.

Therefore:

- `completion_through` is a requested bound, not proof of coverage;
- sufficient through-time watermark evidence classifies with basis
  `observed_watermark`, while exact current/snapshot population evidence
  classifies with basis `observed_snapshot`;
- when observed evidence is insufficient, an exact
  `CompletenessDeclaration` may classify only its named inputs with basis
  `declared_complete`;
- inputs classified by different sufficient bases produce aggregate basis
  `mixed`;
- any uncovered required input produces basis `unknown` and
  `coverage_censored`;
- completeness combines conservatively across EventPattern, StateModel, and
  StateProjection inputs;
- completeness belongs to artifact quality and lineage, not semantic
  readiness.

If a datasource cannot expose an authoritative watermark or exact snapshot
population receipt for the relevant temporal shape, execution reports observed
completeness as unknown; aggregate coverage remains unknown unless an eligible
exact declaration covers the required instant. This design never infers a
watermark from maximum observed timestamp or a configured SLA. A declaration
does not create or backfill a datasource watermark or snapshot receipt; it
permits classification only inside the exact operation scope that carries it.

For StateProjection, the required instant is interpreted only in the source
Entity's admitted temporal shape. An unversioned current projection rejects a
through-time declaration. A snapshot declaration may cover the requested and
selected partition instant but cannot create coverage before or after that
observation. A change-log or validity declaration may cover the selected
history-scope end, but cannot repair ambiguous change times, gaps, overlaps,
decoding, alignment, or any semantic integrity failure.

`CompletenessDeclaration` is an immutable, fingerprinted analysis policy value.
Its fingerprint covers exact input refs and semantic fingerprints, normalized
`through`, and rationale; session/job audit metadata is lineage rather than
value identity.
For every required input and requested instant, the planner first uses sufficient
observed evidence; otherwise it may use one exact sufficient declaration.
Non-identical declarations that both cover the same input and required instant
fail as ambiguous rather than being ranked by timestamp or rationale.
Declarations naming unused inputs, a
different Event definition, a different StateProjection, an incompatible time
domain, or a bound before the required instant fail before querying.

Artifacts retain per-input `coverage_basis`, the observed watermark or exact
snapshot receipt when available, declaration fingerprint/rationale when used,
and aggregate
`observed_watermark | observed_snapshot | declared_complete | mixed | unknown`.
An `incomplete` or `right_censored` classification derived from a declaration is
therefore an observed absence under an explicit assumption, not an
unconditional observed fact. Findings and digests preserve that AnalysisScope
assumption and never upgrade it to authoritative source evidence.

Journey/lifecycle materializers and descriptive reducers degrade unknown
coverage to `coverage_censored`; absence of a watermark alone is not an error.
`event_coverage_unknown` is raised only when a caller attempts a continuation
that requires a fully classified population, including admitting a censored
SubjectSet as a cohort or comparing/attributing a funnel whose unresolved
population affects the requested PatternStep.

## Artifact Algebra

`EventFrame`, `LifecycleFrame`, `DeltaFrame`, `AttributionFrame`, and
`SubjectSet` are distinct immutable public families with closed shapes. They do
not use an optional-field mega-class or inherit MetricFrame semantics.

Every artifact carries:

- subject `EntityRef` and identity signature;
- the discriminated `AnalysisScope` and exact cohort, window, follow-up, or
  as-of coverage;
- Event, StateModel, or StateProjection refs and semantic fingerprints;
- exact PatternStep values, ProjectionStateHandles, ModelStateHandles, and
  StateAlignment used by its source operation;
- executable Entity and Relationship path refs;
- pattern, matching, replay operator version, seed, or state-projection
  contract, including the Entity-owned temporal shape and
  inferred/requested/actual projection scope;
- exact subject axes, paths, temporal anchors, and grouped reconciliation when
  present;
- read extent, observed watermark evidence, declared completeness assumptions,
  per-input/aggregate coverage basis, quality, and censoring;
- artifact family, closed shape, row-contract version, and lineage;
- evidence status and the bounded `ArtifactDigest` snapshot.

The registered v1 continuation matrix is:

| Input | Legal analysis continuations |
| --- | --- |
| `EventFrame[journey]` with `matching=first_per_subject` | `events.funnel`, `events.time_to_event`, `select_subjects(dropped_before)`, `assess_quality` |
| `EventFrame[journey]` with `matching=every_start` | `events.time_to_event`, `assess_quality` |
| `EventFrame[funnel]` | compatible `compare`, `assess_quality` |
| `EventFrame[time_to_event]` | `assess_quality` |
| `LifecycleFrame[snapshot]` | `lifecycle.distribution`, `select_subjects(in_state)`, `assess_quality` |
| replay `LifecycleFrame[history]` | all lifecycle reducers, `select_subjects(in_state)`, `assess_quality` |
| projection `LifecycleFrame[history]` | distribution/transitions/dwell, `select_subjects(in_state)`, `assess_quality` |
| `LifecycleFrame[distribution]` | compatible lifecycle reconciliation quality with identical `as_of` instants and axes |
| ungrouped `DeltaFrame[funnel]` | `attribute`, `assess_quality` |
| grouped `DeltaFrame[funnel]` | `assess_quality` |
| `SubjectSet` | exact subject-compatible cohort inputs only |

Registry admission includes the matching policy and source-kind predicates
shown in the table; family and shape alone are insufficient. Step-consuming
continuations additionally validate exact PatternStep membership, and
state-consuming continuations validate ModelStateHandle ownership. Projection
alignment additionally validates ProjectionStateHandle ownership on its source
side. A recovered cold agent therefore receives only mechanically legal,
typed-handle continuations from `contract()`; numeric indexes and bare state
strings are never advertised.

All artifacts also support bounded `show()`, `contract()`, evidence reads,
recovery, and terminal `to_pandas()`. Correlate, hypothesis-test, forecast,
discover, generic transform, and every unlisted family edge fail before
querying. Implementing the shared artifact protocol never grants an affordance.

### Closed quality reports

`session.assess_quality(...)` keeps one `QualityReport` output family and adds
closed shapes:

| Shape | Accepted input | Required checks |
| --- | --- | --- |
| `QualityReport[event_journey]` | `EventFrame[journey]` | identity uniqueness, participant coverage, event-order determinism, per-input coverage basis, declared-assumption disclosure, censoring, row-contract integrity |
| `QualityReport[event_funnel]` | `EventFrame[funnel]` | source-journey integrity, denominator math, censored population, subject-axis temporal ownership, grouped-to-total count reconciliation, PatternStep reconciliation |
| `QualityReport[lifecycle]` | one LifecycleFrame | seed/history coverage, per-input coverage basis, declared-assumption disclosure, state closure, projection decoding/alignment, current or snapshot subject uniqueness, change-time uniqueness, validity interval integrity and gaps, ordering, unmodeled projected transitions when applicable, subject-axis temporal ownership and grouped reconciliation when applicable, censoring, row-contract integrity |
| `QualityReport[lifecycle_reconciliation]` | replay and projection `LifecycleFrame[distribution]` inputs with identical StateModel, subject, `as_of` instants, axes, and scope | per-axis/per-model-state count/share difference, missing subjects, source coverage, reconciliation tolerance |
| `QualityReport[funnel_attribution]` | `AttributionFrame[funnel_loss_rate]` | additive components, contribution pools, residual, exact reconciliation |

All shapes use the existing quality row contract
`check_id, check_kind, status, severity, message, details_json`, persist exact
source refs and target family/shape metadata, and emit the existing
`QualityCheckResult` finding/digest item. Event and lifecycle checks do not
reuse Metric-only null-ratio or metric-comparability semantics by analogy. A
wrong family, mismatched reconciliation pair, or unsupported shape fails the
capability gate before execution.

Whenever a declaration supplies required coverage, quality emits a deterministic
`declared_completeness_used` advisory with exact input refs, bound, and
declaration fingerprint. It is not a blocker when it covers the operation, but
it remains visible in `show()`, findings, digests, and recovery. A mixed
observed/declared comparison preserves both sides rather than collapsing them
to one generic “complete” label.

## Evidence Engine Integration

### Typed subject and scope extension

The existing `artifact -> typed Finding -> bounded ArtifactDigest` pipeline
remains the only evidence path. This cutover replaces the metric-only persisted
subject slot with one discriminated union while retaining the current metric
payload unchanged:

```text
AnalysisEvidenceSubject =
    MetricAnalysisSubject       # wraps the current TypedEvidenceSubject
  | EventAnalysisSubject
  | LifecycleAnalysisSubject
  | SubjectSetAnalysisSubject
```

`EventAnalysisSubject` carries the subject EntityRef and identity signature,
EventPattern, PatternStep values, EventRefs, participant-role handles, Event
shape, matching policy, and fingerprints. `LifecycleAnalysisSubject` carries
the subject identity, StateModel or StateProjection ref, exact Entity temporal
shape, resolved ProjectionStateHandles and ModelStateHandles when applicable,
StateAlignment, Lifecycle shape, seed or fixed replay contract, and
fingerprints.
`SubjectSetAnalysisSubject` carries the source artifact and closed typed-handle
selection without embedding raw identities. Exactly one discriminated variant
is present; this is not an optional-field mega-shape.

The public `AnalysisScope` name becomes a discriminated closed union. Its metric
variant retains the current metric identities, comparison, axes, predicates,
window, and assumptions. Its Event variant carries `cohort_window`,
`completion_through`, matching policy, subject axes and their cohort-entry
anchor, observed watermarks, declared completeness assumptions, per-input
coverage basis, and censoring. Its Lifecycle variant carries the requested
as-of/window scope or inferred current capture, actual capture or partition
instant, Entity temporal shape, StateAlignment, seed or fixed replay contract,
projection coverage, subject axes and their as-of anchors,
observed/declaration bases, unknown validity gaps, and censoring boundary. Its
SubjectSet variant carries the source scope, selection, and admitted consumer
scope. Ontology edges and candidate rows from the companion design remain
lineage, never evidence subjects.

The final contract persists one versioned closed subject/scope and
ObservationValue union, and its decoder accepts only that current version. This
design defines no legacy subject adapter, compatibility mode, dual-read shape,
or evidence-store migration. Metric artifacts use the final
`MetricAnalysisSubject` variant directly.

### Findings, digests, and observations

Every successfully committed EventFrame, LifecycleFrame, funnel DeltaFrame,
funnel AttributionFrame, or SubjectSet invokes exactly one shape-dispatched
finding extractor. It first creates exact typed `Finding`
objects from declared artifact fields, including `epistemic_kind="observed"`,
the discriminated subject and scope, a versioned `DerivationRule`, and source
refs. `ObservationValue` gains one registered closed value per new artifact
shape; unknown shapes fail closed. `epistemic_kind="observed"` describes the
computed row/count/duration, while a declared completeness basis remains an
explicit scope assumption and digest inference boundary. It never becomes
observed datasource evidence.

The bounded `ArtifactDigest` is then built only from those exact finding objects
and artifact metadata. Its `ObservationFact` items retain at most five
deterministically ordered aggregates and at most three inference boundaries,
with `omissions` and fallback refs to `session.evidence.findings(...)` and
`session.get_frame(...)`. Shape-specific digest extraction includes:

- journey: attempt, complete/incomplete, unused-event, and censoring counts;
- funnel: bounded axis tuples when present, cohort, ordered-PatternStep, conversion,
  grouped reconciliation, unused-event, coverage basis, and censoring summaries;
- time-to-event: attempt, completion, non-completion, duration, unused-end, and
  censoring summaries;
- distribution: bounded axis tuples when present, model-state counts, shares, grouped
  reconciliation, and coverage basis;
- transitions: bounded from/to counts and shares;
- dwell: bounded interval/completed/censored counts and duration summaries;
- violations: bounded counts by closed violation kind and current model state;
- funnel delta/attribution: additive component deltas, contributions, residual,
  and reconciliation;
- SubjectSet: selection count and coverage status only.

The digest contains aggregates and semantic refs, never raw subject or event
identities. `ObservationFact` means a typed observed digest item only; it does
not create the removed proposition/fact model, a policy fact, or a causal fact.
Exact identity-bearing detail remains available only through the artifact and
authorized exact-finding fallback.

Commit and failure isolation follow the implemented evidence contract:

- on success, artifact, findings, digest, and artifact-local issues persist in
  one evidence transaction and the identical digest snapshot enters frame meta;
- finding extraction failure persists the artifact as `partial`, with no
  fabricated finding or digest and a typed `evidence_partial` issue;
- digest construction failure after extraction persists the exact findings,
  omits the digest, and adds `evidence_digest_unavailable`;
- unavailable evidence storage returns the artifact as `unavailable` with no
  digest.

Recovery and audit paging decode the same subject, scope, finding, digest, and
derivation variants; Event/Lifecycle evidence does not introduce a second read
API or summary model.

## Capability Registration

The semantic and analysis capability registries cumulatively receive the
surface below as delivery slices land. “Extension” names the final coherent
surface, not one atomic release unit. The independent ontology discovery
extension is specified in the companion design.

### Semantic execution surface

Registers:

- semantic kinds Event, StateModel, and StateProjection with their exact typed
  refs; typed participant roles over Entity refs; Entity-owned
  ChangesVersioning; LifecycleState authoring values and typed model-state
  handles owned by StateModel; and NormalizedState authoring values and typed
  projection-state handles owned by StateProjection;
- semantic constructors and resolvers `event`, `participant`,
  `participant_role`, `lifecycle_state`, `state_model`, `model_state`,
  `inception`, `transition`, `changes`, `normalized_state`,
  `state_projection`, and `projection_state`;
- semantic verification, preview, readiness, details, lineage, persistence,
  and the exact `ms.help(...)` topics for those values.

### Analysis execution surface

Registers:

- `events.match`, `events.funnel`, and `events.time_to_event`;
- `sequence`, keyed `PatternStep` construction through `step`,
  `first_per_subject`, and `every_start` constructors;
- typed subject-axis admission on funnel and lifecycle distribution, exact
  history-distribution `at` instants, and the `declared_complete_through`
  completeness-policy constructor;
- `lifecycle.replay`, `lifecycle.observe`, and four lifecycle reducers;
- `as_of`, `state_alignment`, `align_state`, `identity_state_alignment`,
  `from_inception`, and `from_projection` values;
- `select_subjects` plus the closed `dropped_before` and `in_state`
  constructors;
- exact funnel dispatches for `compare` and `attribute`, plus the
  `funnel_loss_rate` target;
- Event/Lifecycle/SubjectSet shapes, QualityReport shapes, recovery, evidence,
  and terminal contracts;
- discriminated evidence subject/scope and registered ObservationValue shapes.

This extension has no ontology precondition and changes no existing Metric
calculation semantics. The sole Metric-surface addition is exact
`cohort=SubjectSet` admission with matching subject identity.

## Help, Browsing, and Recovery

Public guidance preserves one owner per contract:

- `ms.help(...)` describes Event, StateModel, StateProjection, Entity
  change-log versioning, LifecycleState and NormalizedState authoring,
  participant, `participant_role`, `projection_state`, `model_state`,
  inception, transition, verification, preview, and readiness;
- `mv.help(...)` describes keyed PatternStep construction, pattern matching,
  subject-axis temporal ownership, completeness declarations, projection scope,
  typed state alignment, replay, exact distribution instants, reducers, typed
  SubjectSet selections, comparison, attribution, artifact families, and
  quality shapes; matching,
  follow-up, projection scope, seed, fixed replay-violation behavior, and
  completeness topics include the business-question guidance defined above
  rather than signatures alone;
- `show()` provides bounded current content and evidence;
- `contract()` provides mechanically valid continuations;
- structured errors provide typed repair.

`mo.help(...)` and semantic-hypothesis discovery help are specified in the
companion ontology design.

Ordinary analysis agents discover ready Event, StateModel, and StateProjection
handles through the session semantic catalog. They do not manipulate source
internals.

Recovery preserves exact artifact family, closed shape, row-contract version,
semantic refs, PatternStep keys/definitions, ProjectionStateHandles,
ModelStateHandles, StateAlignment, Entity temporal shape, requested and actual
projection scope or inferred current capture, business-choice policies, subject
axes/paths/temporal anchors, exact distribution instants, fixed replay contract,
observed watermarks, completeness declarations and coverage basis, evidence
status, and lineage. A recovered artifact is never coerced to MetricFrame or a
generic table. Its `contract()` reconstructs typed
step/state/alignment/axis/policy arguments from persisted identities rather
than suggesting row indexes, bare strings, an inferred identity alignment, or
an inferred completeness declaration.

## Lineage

Event artifact lineage records:

- Event refs and semantic fingerprints;
- source Entity and Relationship path refs;
- identity, occurrence time, participant roles, scope, and coverage;
- row predicate fingerprint;
- EventPattern, ordered PatternStep keys/definitions, matching policy,
  follow-up bound, subject axes and cohort-entry anchor, observed watermarks,
  completeness declarations, per-input/aggregate basis, and censoring;
- compiled expression and backend execution evidence.

Derived Event artifacts reference the source journey rather than duplicating
matching authority. Lifecycle lineage additionally records StateModel or
StateProjection, source Entity versioning fingerprint, inferred current or
requested and actual projection scope, resolved ProjectionStateHandles and
ModelStateHandles, StateAlignment, transition set, replay seed or exact snapshot
artifact, fixed replay operator version, ordering, validity gaps, requested
distribution instants, subject axes and as-of anchors, observed watermarks,
completeness declarations, coverage basis, trace, censoring, and
reconciliation.
SubjectSet lineage records the exact source artifact and closed typed-handle
selection.

Lineage records provenance. It never turns a bounded observation into
executable truth or a causal fact.

## Structured Errors

All new public errors follow the existing SemanticError or AnalysisError
templates and include expected, received, location, and typed repair.

| Error kind | Trigger |
| --- | --- |
| `invalid_event_identity` | Event identity is empty, nullable, non-scalar, or not functionally unique |
| `invalid_event_source` | `owner(occurred_at)` is unresolved or identity fields do not share that owner |
| `invalid_event_time` | `occurred_at` is not a ready TimeDimension on its owning Entity |
| `invalid_event_predicate` | Event predicate is not one restricted boolean decorator body over bound source Dimensions |
| `invalid_participant_path` | participant path is empty, discontinuous, reversed, or unresolved |
| `invalid_participant_cardinality` | participant cardinality is outside v1 `one | optional_one` |
| `ambiguous_participant_role` | an EventRef trigger has several cardinality-one roles ending at the StateModel subject; repair supplies exact ParticipantRoleHandles |
| `invalid_event_pattern` | pattern contains an empty/duplicate step key, invalid participant handle, or inconsistent inferred subject |
| `pattern_step_mismatch` | a step consumer receives an integer, string, EventRef, or PatternStep whose complete fingerprint is not present in the source EventPattern |
| `invalid_event_matching_policy` | matching policy belongs to another operator family or has an invalid completion-assignment value |
| `invalid_completeness_declaration` | declaration has empty rationale/inputs, mixed or empty input tuple, unused or mismatched input/time domain, conflicts with another declaration, or does not cover the required instant |
| `event_coverage_unknown` | a fully classified continuation is requested from coverage-censored Event subjects |
| `invalid_subject_axis` | axis is a bare name, not ready, not on/to-one reachable from the subject, or lacks compatible temporal ownership |
| `grouped_reconciliation_failed` | grouped funnel or lifecycle-distribution counts do not reconcile exactly to the ungrouped source population |
| `invalid_state_transition` | LifecycleState, inception, resolved trigger, terminal-state, or deterministic-transition contract is invalid |
| `invalid_state_projection` | inferred source/subject Entity, `state_field`, `subject_path`, NormalizedState domain/decoding, or admitted source temporal shape is invalid |
| `unmapped_projection_state` | a non-null physical state value is absent from the projection's closed NormalizedState decoding |
| `projection_state_mismatch` | an alignment receives a bare string, unknown ProjectionStateHandle, or handle owned by another StateProjection |
| `model_state_mismatch` | an analysis consumer receives a bare string, unknown state, or ModelStateHandle owned by another StateModel |
| `invalid_state_alignment` | alignment is partial, non-bijective, duplicated, cross-owner, or incompatible with the selected projection/model |
| `invalid_projection_scope` | scope was supplied for an unversioned current source, omitted for a versioned source, incompatible with the inferred temporal shape, or selects an absent partition |
| `ambiguous_state_change_order` | one projection subject has multiple different change rows at the same normalized effective time |
| `insufficient_state_history` | replay cannot establish state before the requested range |
| `ambiguous_event_order` | same-time Events have no deterministic business order and affect replay |
| `invalid_lifecycle_seed` | replay seed is unavailable, mismatched, or incompatible with the requested window |
| `invalid_distribution_instants` | history distribution omits `at`, contains duplicate/out-of-scope instants, or snapshot distribution receives `at` |
| `subject_set_mismatch` | SubjectSet Entity, identity, owner, or scope is incompatible |
| `funnel_comparison_mismatch` | funnels differ in pattern, matching, PatternStep, subject, axis/anchor, or coverage semantics |
| `funnel_attribution_unsupported` | delta is already grouped, target PatternStep is invalid, multi-axis mode is missing, or additive components, driver axes, or reconciliation are unavailable |
| `lifecycle_reconciliation_mismatch` | replay/projection frames differ in StateModel, subject, distribution `as_of` instants, axes/anchors, or scope |
| `quality_shape_unsupported` | assess_quality receives an unregistered family/shape combination |

Ontology-specific error kinds are specified in the companion design.

## Module Ownership

### `marivo.semantic as ms`

Owns executable semantic identities, Entity/field/Measure/Metric/Relationship
contracts, the Entity `ChangesVersioning` variant, Event, StateModel, and
StateProjection definitions, semantic catalog assembly, nested
ParticipantRole, LifecycleState, and NormalizedState values,
ProjectionStateHandle and ModelStateHandle identities, verification, preview,
and readiness.

### `marivo.analysis as mv`

Owns keyed PatternStep values, projection scopes, typed StateAlignment,
patterns, matching/seed/completeness policies, fixed replay execution,
subject-axis enrichment, sessions, canonical materialization, reducers,
comparison, attribution, SubjectSet, artifacts, recovery, quality, and Evidence
Engine integration. The narrow
semantic-hypothesis candidate bridge is registered by, and specified in, the
companion ontology design.

`marivo.ontology as mo` and the future decision extension (Rule, Policy,
Action) are specified separately.

## Behavioral Fixtures

### Stable Metric lane without ontology

Load and analyze current Metrics with no ontology root. Observe, compare,
attribute, correlate, hypothesis-test, forecast, quality, recovery, and
evidence behavior remain unchanged.

### Shared event Entity

Define filtered Events over one event-log Entity with cardinality-one and
optional-one roles. Verify identity, predicate, role-path, subject-role
admission, readiness, and recovery. Verify that v1 rejects authoring a
multi-valued participant role with repair pointing to a future exact consumer.
Reject Event `entity`, participant `entity`, `via`, and empty `path` arguments.
Verify Event source from `owner(occurred_at)`, same-owner identity validation,
same-source participant path omission, and participant Entity inference from a
non-empty path endpoint.

### Journey materialization and reducers

Materialize first-per-subject and every-start journeys with repeated EventRefs,
distinct PatternStep keys, same-time ambiguity, unused occurrences,
exclusive/shared completion assignment, observed/declared/mixed completeness,
complete follow-up, and unknown watermark.
Reject empty/duplicate keys, numeric/string/EventRef selectors, and
PatternSteps whose complete fingerprint is absent from the source pattern
before querying. Reject `mv.step` Event/subject duplication, mixed participant
endpoints, and any `contiguity` argument. Reject
empty/mixed/mismatched/unused/conflicting completeness declarations. Verify stable
journey ids and step keys, inferred pattern subject identity, and that reducers
issue no Event occurrence query or rematch and preserve source matching
lineage. Verify journey, funnel, and time-to-event rows do not repeat EventRefs
or expose attempt ordinals that are derivable from typed source metadata.
Verify time-to-event retains exact start/end PatternSteps once in metadata
rather than repeating their keys on every row.

Materialize ungrouped and multi-axis funnels. Verify ready to-one subject axes,
cohort-entry temporal ownership, explicit null groups, deterministic axis order,
and exact grouped-to-total count reconciliation. Permit only governed subject
axis enrichment; reject bare/unready axes, to-many fanout, ambiguous temporal
ownership, and reconciliation failure. Verify rates are recomputed per group
rather than summed or averaged.

Verify that repeating an EventRef in explicit keyed steps remains an ordered
occurrence question and that no retention/repeat operator is advertised by
help, contract, or capability registration.

### SubjectSet composition

Select funnel dropouts with `dropped_before(PatternStep)` and lifecycle-state
subjects with `in_state(ModelStateHandle)`. Verify compatible Metric, Event, and
Lifecycle cohort use; reject wrong pattern/model, Entity, identity, owner,
initial-PatternStep dropout target, or censored selection. Verify digests omit raw
identities and terminal pandas results cannot re-enter. Verify Metric
`session.observe` adds only `cohort=SubjectSet`, retains its existing
`time_scope`/grain/dimensions contract, and rejects a new `window` alias.

### Lifecycle replay and state projection

Replay from inception and from a compatible committed projection snapshot.
Verify that `from_projection(snapshot)` preserves the exact seed artifact and
never rereads a StateProjection. Verify interval history, illegal-transition
trace, deterministic violation projection, censoring, observed/declared/mixed
completeness, and ambiguity failure. Verify the fixed v1 violation behavior and
reject any `on_violation` argument or `record_and_continue` constructor.

Author StateModels with LifecycleState values and explicit `ms.inception`.
Reject string state channels, separate `initial`/`terminal` parameters, and
`from_state=None` or an inception target. Verify an EventRef trigger infers its unique
cardinality-one subject role, an ambiguous EventRef trigger returns exact
ParticipantRoleHandle repairs, and an explicitly supplied handle becomes the
sole Event/role identity.

Observe projections over ordinary current-state, snapshot, change-log, and
closed-open validity Entities. Verify current capture time, exact snapshot
partition time, change-point-to-interval derivation, open final intervals,
validity gaps as unknown, and parity between compatible change-log and validity
history. Reject any supplied scope on an unversioned current source, a window
scope on a snapshot source, a missing snapshot partition, closed-closed
validity, duplicate current/snapshot subjects, same-time change rows,
overlapping validity intervals, and any attempt to redeclare Entity or Entity
temporal fields in StateProjection. Reject
`entity`, `subject`, `state`, `states`, `via`, and an empty `subject_path` at
authoring; verify that `owner(state_field)` is the only source-Entity resolution
path and that the projection subject is that owner or the exact non-empty path
endpoint.
Reject a through-time CompletenessDeclaration on an unversioned current
projection; verify exact snapshot population evidence remains distinct from a
through-time watermark.

Verify `normalized_states` and every NormalizedState name/source-value tuple are
non-empty and disjoint; reject null, wildcard, overlapping, or unmapped non-null
values. Resolve ProjectionStateHandles after load. Build explicit
total-bijective StateAlignment values from ProjectionStateHandles to
ModelStateHandles; reject bare strings, cross-owner handles, partial or
many-to-one alignment, and implicit same-name alignment. Verify
`identity_state_alignment` succeeds only for exactly equal closed domains.

Reject transitions/dwell on snapshot frames. Verify unmodeled adjacent
projection jumps in history are emitted with `unmodeled_projection`, excluded
from modeled shares, and surfaced in lifecycle quality. Reconcile compatible
replay/projection distributions without changing either artifact. Materialize
lifecycle snapshot distributions at their persisted `as_of` and history
distributions at exact requested `at` instants. Reject missing history `at`,
snapshot `at`, duplicate/out-of-scope instants, to-many axes, and temporally
ambiguous axes. Verify explicit null groups and exact grouped-to-total counts.

### Funnel comparison and attribution

Compare identical funnel definitions across two scopes. Verify entry, reached,
and lost components; PatternStep-plus-axis alignment for grouped funnels;
full-outer axis-tuple alignment with safe zero counts and null
zero-denominator rates; ratio-mix contributions; positive/negative pools;
residual and exact reconciliation. Verify funnel compare derives its sole
PatternStep/axis alignment and rejects an `alignment` argument. Reject
positional targets, mismatched PatternSteps/patterns, policies, coverage,
axes/anchors, and fanout. Reject attribution from an already grouped delta with
repair to compare ungrouped funnels first. Verify one-axis attribution omits
mode, multi-axis attribution requires `joint | hierarchy`, and row layouts
match the existing AttributionFrame contract. Verify the target PatternStep is
metadata rather than a repeated contribution column. Verify help/evidence
describes arithmetic contribution and never causality.

### Agent policy guidance and recovery

Verify focused help explains when first-per-subject,
exclusive/shared completion assignment, completion-through, declared
completeness, inferred-current versus explicit as-of/window projection scope,
typed identity/explicit state alignment, inception/projection seed, and the
fixed v1 replay-violation contract are business-correct without choosing for
the caller.
Verify `show()`, `contract()`, quality, evidence, and recovered artifacts retain
the Entity temporal shape, inferred or requested and actual projection scope,
exact StateAlignment, chosen policies, fixed replay contract, exact
distribution instants, axes/anchors, declarations, and coverage basis. Quality
must not infer an identity alignment, rematch an alternative policy, or
fabricate a sensitivity judgment.

## Acceptance Criteria

The design is accepted when:

1. Event, StateModel, and StateProjection reuse existing Entity, field,
   Relationship, typed-ref, validation, and readiness contracts;
   ChangesVersioning is the only additive Entity-contract variant.
2. Event supports one and optional-one participant roles; analytical subject
   roles remain exactly one. Event source is inferred from
   `owner(occurred_at)`; participant target is the source or exact `path`
   endpoint. Event/participant Entity repetition, empty paths, and multi-valued
   roles are not authorable in v1.
3. StateModel contains normative lifecycle meaning only; projection and replay
   execution choices are separate. LifecycleState values declare
   initial/terminal meaning once, `ms.inception` represents inception without a
   `None` sentinel or repeated initial target, and one typed `on` value supplies
   trigger identity.
4. StateProjection is a thin observed-state adapter. It infers the source Entity
   from `owner(state_field)` and the subject from that owner or the exact
   `subject_path` endpoint; the source Entity is the sole temporal owner.
   StateProjection has no `entity`, `subject`, empty-path, snapshot,
   `valid_from`, `valid_to`, or change-time parameters.
5. Ordinary, snapshot, change-log, and closed-open validity Entities admit only
   their declared current/as-of/window scopes. Current scope is inferred only
   for an unversioned source; versioned sources require explicit as-of/window
   scope. Change-log history derives half-open intervals without requiring an
   SCD2 source; unsupported scope, ambiguous change time, validity overlap, or
   closed-closed validity fails explicitly.
6. Physical values decode through a closed `normalized_states` tuple of
   NormalizedState values.
   Analysis uses ProjectionStateHandles and ModelStateHandles joined by one
   explicit total-bijective StateAlignment; no string mapping, implicit
   same-name alignment, partial alignment, or many-to-one collapse is legal.
7. Every EventPattern step has a unique stable key and one
   ParticipantRoleHandle that supplies Event and subject identity. Pattern
   subject is inferred from consistent step endpoints; `mv.step` does not
   repeat Event/subject, and v1 exposes no single-value contiguity parameter.
   Public consumers require the exact PatternStep; numeric indexes, bare
   strings, and EventRefs are not accepted as step selectors.
8. Public StateModel consumers require a ModelStateHandle owned by the exact
   model; bare state strings and cross-model handles fail before execution.
9. Event matching materializes one canonical journey artifact; funnel and
   time-to-event consume its assignment without querying Event inputs or
   rematching. Optional subject-axis enrichment may query only governed subject
   Dimensions and to-one paths.
10. Lifecycle replay/observation materializes history or snapshot once;
    reducers consume that artifact without replaying or rereading.
    `from_projection` consumes the exact committed snapshot artifact rather than
    a StateProjectionRef that could trigger a second read. StateAlignment is the
    sole `lifecycle.observe` source and supplies both projection and model
    identity. V1 replay has one fixed violation contract and no public
    `on_violation` policy.
11. Observed runtime watermark evidence and exact snapshot population receipts
    remain the only authoritative completeness facts for their respective
    temporal shapes. An exact typed declaration may classify only its named
    inputs as an explicit AnalysisScope assumption; artifacts never render it as
    observed source evidence.
12. SubjectSet is the only v1 cross-family subject bridge and preserves typed
    identity, ownership, lineage, coverage, and privacy boundaries. Existing
    Metric observe gains only `cohort=SubjectSet`; its `time_scope`, grain, and
    dimensions contract does not gain a second window API.
13. Funnel and lifecycle distribution accept only exact typed subject axes with
    to-one reachability and explicit temporal anchors; grouped counts reconcile
    exactly to ungrouped counts and rates are recomputed per group. Snapshot
    distribution uses its persisted `as_of`; history distribution requires
    exact explicit `at` instants and always emits `as_of`.
14. Funnel compare aligns the full outer union of exact PatternStep and axis
    tuples with safe additive zero-fill only for fully materialized groups and
    exposes no caller-supplied alignment. Multi-axis funnel attribution reuses
    the existing required `joint | hierarchy` mode and row-layout contract.
    Attribution accepts only an ungrouped funnel delta, uses additive entry/lost
    components, reconciles exactly, and makes no causal claim.
15. Pattern order remains artifact metadata; `step_index`, `stage_index`, and
    attempt ordinals are absent from public data schemas and selectors.
    EventRefs and fixed selection/target PatternSteps live once in typed
    artifact metadata rather than repeating on every derivable row. Lifecycle
    rows name `model_state` explicitly.
16. Every family/shape edge is registered explicitly; illegal paths fail before
    datasource execution.
17. Artifacts recover with exact shape, semantics, typed step/state/alignment
    handles, Entity temporal shape, inferred/requested/actual scope, chosen
    policies, fixed replay contract, distribution instants, axes/anchors,
    completeness declarations/basis, findings, digest, and continuation
    contract intact.
18. New evidence uses the existing typed Finding and bounded ArtifactDigest
    path without raw identities or fabricated facts.
19. Retention and governed named analysis recipes remain explicit future
    families; neither is synthesized from repeat steps or promoted into Event
    semantics.
20. Focused help explains business applicability of matching, follow-up,
    projection scope/alignment, seed, fixed replay-violation behavior, and
    completeness choices without exposing single-value strategy slots.
21. Ontology is absent from every execution, readiness, matching, replay, and
    evidence prerequisite.

## Delivery Boundary

This document specifies a future public contract only.

Delivery proceeds in vertical slices rather than one all-or-nothing cutover:

1. Event, participant roles, keyed PatternStep/EventPattern, `events.match`,
   Event-input completeness declarations, journey recovery, quality, and
   evidence;
2. funnel/time-to-event reducers, subject-axis funnel breakdown, SubjectSet,
   and typed cohort admission;
3. funnel compare/attribute with existing one-axis/joint/hierarchy layouts and
   reconciliation;
4. LifecycleState, StateModel, ModelStateHandle, inception, lifecycle
   replay/history with fixed violation behavior, reducers, quality, replay-Event
   completeness declarations, and evidence, initially with
   `from_inception()` only;
5. StateProjection, NormalizedState/ProjectionStateHandle, typed StateAlignment,
   inferred-current and explicit snapshot observation, projection completeness
   declarations, exact-as-of snapshot distribution/selection, and
   `from_projection(snapshot)` replay seed;
6. Entity `ChangesVersioning`, change-log and closed-open validity history
   observation, projection-backed transitions/dwell/distribution, and temporal
   integrity quality;
7. replay/projection reconciliation.

Slice 1 is intentionally a complete standalone journey-analysis capability:
`EventFrame[journey]` supports bounded show/contract, quality, evidence, and
recovery. Its contract does not advertise funnel, time-to-event, or
select-subject continuations until slice 2 registers those consumers. “Thin”
therefore means one canonical fact surface, not an incomplete published
capability.

Each slice ships its semantic inputs, public help, registry roles, artifact
contract, persistence/recovery, evidence extractor, structured errors, and
behavioral fixtures together. No slice may publish a partial capability whose
artifact cannot be recovered or whose evidence contract is missing.

Slices 1–2 form the standalone Event-analysis core. Slice 4 forms the standalone
replay-based Lifecycle core. Slice 5 is the ordinary current-status/snapshot
entry path, and slice 6 adds native change-log and validity history; neither is
merely a reconciliation prerequisite. Funnel attribution in slice 3 and
replay/projection reconciliation in slice 7 may be scheduled after their
respective cores without removing their final contracts.
`from_projection(snapshot)` is not registered or advertised until slice 5
supplies its exact typed artifact input.

Retention/cohort-return analysis and an organization-governed named
analysis-recipe registry are explicit post-v1 extensions. Retention receives a
dedicated materialization/artifact contract; a future recipe registry governs
analysis values without promoting Funnel or matching policy into
`marivo.semantic`.

This extension is independent of, and never requires, the optional ontology
discovery extension specified in the companion design; the ontology extension
may ship afterward.

This specification revision does not implement runtime code, skills, or site
documentation.
