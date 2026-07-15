# Marivo Executable Semantic and Ontology Extension Design

Status: written-review revisions integrated; pending written-spec re-review

Date: 2026-07-13

## Summary

Marivo keeps one stable executable semantic model, one independent typed
analysis language, and one optional ontology extension with a narrow bridge
back into analysis.

The existing `marivo.semantic` model remains the sole authority for executable
business meaning. `Entity`, `Dimension`, `TimeDimension`, `Measure`, `Metric`,
and `Relationship` retain their current identities, authoring contracts,
readiness behavior, and metric execution semantics. This design adds `Event`
and `StateModel` as semantic objects that reuse those contracts; it does not
replace the Metric lane or introduce a parallel grounding model.

`marivo.analysis` remains an independent typed DSL. It consumes ready semantic
handles and produces persistent typed artifacts. Event and lifecycle analysis
therefore use explicit operators and the new `EventFrame` and `LifecycleFrame`
families without acquiring Metric-only affordances.

`marivo.ontology` is an optional knowledge extension. It references typed
semantic identities, adds reviewable contextual relationships, and builds a
read-only index over dependencies already declared by the semantic catalog. It
cannot define executable identity, joins, filters, populations, readiness, or
SQL. Its only v1 bridge into typed analysis is
`discover.semantic_hypotheses(source)`, which returns unscored candidates that
an agent must explicitly inspect and observe.

`Rule`, `Policy`, and `Action` belong to a future decision extension that may
consume ontology context and assessed evidence. They are not semantic objects,
ontology edges, evidence facts, or analysis operators in this design.

## Architectural Decision

The architecture has four dependency layers:

```text
Executable Semantic Core (`marivo.semantic`)
    Entity / Dimension / TimeDimension / Measure / Metric / Relationship
    + Event / StateModel
        |
        | ready typed semantic handles
        v
Typed Analysis Core (`marivo.analysis`)
    metric operators + events.* + lifecycle.*
    -> typed artifacts -> Evidence Engine

Optional Ontology Extension (`marivo.ontology`)
    typed semantic refs + explicit SemanticEdge values
    -> CandidateSet[semantic_hypothesis]
        |
        | explicit candidate selection and observe
        v
Typed Analysis Core

Future Decision Extension
    Rule / Policy / Action
    consumes ontology context + assessed evidence
```

The arrows express allowed consumption, not shared ownership:

- semantic definitions never depend on ontology;
- Metric, Event, and StateModel execution never requires ontology;
- analysis does not redefine semantic objects;
- ontology references semantic identities but cannot alter their contracts;
- evidence records observations and assessments but does not execute ontology;
- the future decision layer may read both ontology and evidence, but neither
  lower layer contains decision logic.

## Scope

This design adds:

- executable Event definitions over existing Entities;
- executable StateModel definitions over existing Entities and Events;
- event sequence, funnel, and time-to-event analysis;
- lifecycle distribution, transition, dwell, and violation analysis;
- `EventFrame` and `LifecycleFrame` artifact families;
- an optional `marivo.ontology as mo` authoring and browsing surface;
- bounded ontology-guided semantic-hypothesis discovery;
- event and lifecycle observation digests in the existing Evidence Engine;
- lineage from ontology candidate discovery into later statistical analysis.

This design does not add:

- a second executable semantic model;
- data-independent replacement identities for current Entities;
- a generic knowledge-graph node or runtime instance store;
- a graph query language or arbitrary traversal API;
- automatic event-sequence selection;
- automatic candidate execution, ranking, or causal inference;
- automatic inheritance from taxonomy;
- new Metric constructors or changes to current Metric semantics;
- Rule, Policy, Action, permission, obligation, or write-back behavior;
- migration adapters or aliases for an alternative semantic object system.

The current coupling between an Entity and its physical source is accepted in
this increment. A future Semantic Core v2 may separately address portable
business identity or multiple executable representations. Ontology cannot be
used to simulate that future work.

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

No ontology annotation may override a semantic object's definition,
dependencies, source, expression, grain, additivity, unit, fanout policy,
time contract, or readiness result. If ontology text conflicts with the
semantic catalog, the executable semantic contract wins and ontology
validation reports the stale or contradictory annotation.

### Stable semantic identity

Existing semantic ids remain stable. The two new kinds use the same typed
identity scheme:

```text
event.<domain>.<name>
state_model.<domain>.<name>
```

Nested participant roles, states, transitions, replay seeds, and state
projections are immutable values owned by their parent object. Participant
roles expose typed handles for analysis calls but do not become top-level
catalog objects.

## Event Semantic

### Event contract

`Event` is an executable semantic view over one existing `Entity`. It declares
the meaning of immutable occurrences represented by rows from that Entity.
It does not own a new source or join system.

Every Event declares:

- one `entity: EntityRef` supplying its rows;
- an ordered, non-empty `identity: tuple[DimensionRef, ...]`;
- exactly one `occurred_at: TimeDimensionRef`;
- one or more named participant roles;
- an optional restricted typed row predicate for a shared event Entity;
- `ai_context` containing the business definition.

Identity dimensions and `occurred_at` must belong to the source Entity. Every
identity component must be scalar, deterministic, non-null, equality
comparable, and covered by the Entity key contract. `occurred_at` must have a
resolvable timezone and cannot be a technical ingestion timestamp when a
business occurrence time is available.

The optional event predicate reuses the semantic validator's restricted typed
expression model. It may reference declared Dimensions on the source Entity
and closed scalar constants. It cannot execute raw SQL, call external state,
perform aggregation, introduce a join, or redefine a Metric. This permits one
physical event log to expose several business Event definitions while keeping
each event type explicit and inspectable.

### ParticipantRole

Each participant role declares:

- a stable role name unique within the Event;
- `entity: EntityRef` identifying the participant kind;
- an explicit directed `via: tuple[RelationshipRef, ...]` beginning at the
  Event's source Entity and ending at the participant Entity;
- closed cardinality `one | optional_one`.

An empty path is legal only when the participant Entity is the Event source
Entity. Every non-empty path must be contiguous and executable under existing
Relationship readiness. The planner verifies that the path is to-one from one
event row; it never infers a participant from matching field names or from an
ontology edge.

A role used as an analytical subject must have cardinality `one`. An
`optional_one` role may appear as context but cannot silently define the
population. If multiple eligible roles identify the requested subject Entity,
the caller must pass the typed role handle; the planner never guesses.

### Event authoring shape

The target public shape is additive to `marivo.semantic`:

```python
import marivo.semantic as ms

refund_requested = ms.event(
    name="refund_requested",
    entity=order_events,
    identity=(event_id,),
    occurred_at=event_ts,
    where=event_type == "refund_requested",
    participants=(
        ms.participant(
            name="order",
            entity=orders,
            via=(event_to_order,),
            cardinality="one",
        ),
    ),
    ai_context=ms.ai_context(
        business_definition="A request to refund an order."
    ),
)
```

`ms.event(...)` returns `EventRef`. `ms.participant(...)` returns an immutable
authoring value and is not exported as an independently loadable object.

## StateModel Semantic

### StateModel contract

`StateModel` defines one finite lifecycle for a subject `Entity`. An Entity may
have several independent state models.

Every StateModel declares:

- `subject: EntityRef`;
- a closed, non-empty set of state values;
- exactly one initial state;
- zero or more terminal states;
- a closed set of deterministic transitions;
- one replay-seed contract when event replay is enabled;
- an optional typed state projection over existing semantic objects;
- `ai_context` containing the business definition.

Each transition declares an optional source state, one target state, one
`EventRef`, and one typed participant-role handle from that Event. The role
must identify the StateModel subject Entity with cardinality `one`.

For one source state, Event, and participant role, at most one target state is
legal. A wildcard-source transition cannot overlap a source-specific
transition for the same Event and role. Transition order is derived from
business occurrence time and event identity, never declaration order.

A StateModel contains no arbitrary predicate, transition guard, permission,
Rule, Policy, or Action. When a physical event code implies different business
transitions, authors define distinct Events with explicit row predicates and
reference those Events.

### Replay seed and ordering

The closed replay-seed variants are:

- `initial_event(event, role)`: the declared inception Event establishes the
  initial state at its `occurred_at`;
- `state_projection`: the latest trusted state projection at or before the
  replay range supplies the subject state and seed instant.

Replay reconstructs state before clipping to the requested analysis window.
Missing history raises `insufficient_state_history`; it never assumes that all
subjects were in the initial state at the window boundary.

Within one subject, replay orders Events by normalized `occurred_at` and then
by the declared ordered event identity. If two different event types for the
same subject share an instant and their order changes the resulting state,
replay fails with `ambiguous_event_order`. Event identity and declaration order
are not business chronology fallbacks.

A modeled transition Event that is illegal from the current state emits a
lifecycle violation, leaves the current state unchanged, and allows replay to
continue. Events not referenced by the StateModel are not replay inputs and
are not violations.

### Optional state projection

A state projection may reference only:

- an existing versioned `EntityRef`;
- one state `DimensionRef` on that Entity;
- the existing versioning time contract or a `TimeDimensionRef` compatible
  with it;
- one explicit Relationship path from the projected Entity to the subject.

The projection maps source values to the StateModel's closed state values. It
does not create a separate source-selection or identity model. Snapshot
projections support distribution as of a requested instant. Validity history
may additionally support transition and dwell calculations when its intervals
are complete and non-overlapping.

When replay and a state projection are both available, the artifact records
which path supplied the result. A requested reconciliation may produce quality
evidence; neither path silently overwrites the other.

### StateModel authoring shape

```python
order_lifecycle = ms.state_model(
    name="order_lifecycle",
    subject=orders,
    states=("created", "paid", "fulfilled", "cancelled"),
    initial="created",
    terminal=("fulfilled", "cancelled"),
    transitions=(
        ms.transition(
            from_state="created",
            event=payment_captured,
            role=payment_captured.participants.order,
            to_state="paid",
        ),
        ms.transition(
            from_state="paid",
            event=order_fulfilled,
            role=order_fulfilled.participants.order,
            to_state="fulfilled",
        ),
    ),
    replay_seed=ms.initial_event(
        event=order_created,
        role=order_created.participants.order,
    ),
    ai_context=ms.ai_context(
        business_definition="Commercial order lifecycle."
    ),
)
```

`ms.state_model(...)` returns `StateModelRef`. States, transitions, and seeds
remain owned immutable values.

## Semantic Validation and Readiness

Event and StateModel verification extends the existing semantic pipeline; it
does not create another route.

Declaration validation checks local types, closed enums, source ownership,
identity requirements, temporal types, unique role names, and transition
determinism. Catalog assembly resolves every typed ref and rejects cycles in
participant Relationship paths. Preview checks the Event predicate, identity
uniqueness evidence, occurrence-time normalization, participant coverage, and
state-projection mappings under the existing scoped query contract.

Readiness is task-specific:

- Event readiness requires a ready source Entity, Event identity,
  `occurred_at`, requested roles, and every Relationship on their paths;
- replay readiness requires ready transition Events, subject roles, a legal
  seed, continuous required coverage, and deterministic ordering;
- state-projection readiness requires a ready versioned Entity, state mapping,
  subject path, and coverage for the requested operation;
- Metric readiness is unchanged and never depends on Event, StateModel, or
  ontology unless a Metric explicitly adopts a future semantic dependency.

No ontology edge can satisfy a failed semantic readiness check.

## Typed Event Analysis

Event analysis belongs to `marivo.analysis` and consumes ready Event handles.
The v1 public operators are:

```text
events.sequence      -> EventFrame[sequence]
events.funnel        -> EventFrame[funnel]
events.time_to_event -> EventFrame[time_to_event]
```

Every call declares the subject `EntityRef`, typed participant role for each
Event, half-open analysis window, and any operation-specific matching or
follow-up bound. The caller explicitly supplies the Event order. A temporal
ontology edge may be shown as navigation context, but it never fills this
sequence.

### Sequence

`events.sequence` takes an ordered non-empty tuple of Event handles. Within
each subject, it applies the declared closed matching policy and emits stable
journey steps. Matching uses normalized business occurrence time and event
identity; ambiguous order fails closed.

The sequence shape is a stable long table with one row per journey step:

```text
journey_id
journey_status          complete | partial | coverage_censored
subject_identity
step_index
event_ref
event_identity
occurred_at
elapsed_from_start
elapsed_from_previous
```

### Funnel

`events.funnel` uses the same explicit Event order and subject roles, then
aggregates matched journeys. It emits one row per stage:

```text
stage_index
event_ref
subject_count
conversion_from_first
conversion_from_previous
coverage_censored_count
```

The artifact metadata retains cohort size, completed-final-stage count,
partial count, unused-event count, matching policy, and coverage boundaries.
Funnel conversion is an event-analysis result, not a MetricFrame and not a
new governed Metric definition.

### Time to event

`events.time_to_event` takes explicit start and end Events and emits one row
per start attempt:

```text
subject_identity
start_event_identity
start_time
end_event_identity
end_time
duration
completion_status       completed | not_completed | coverage_censored
```

The closed matching contract determines whether an end Event may satisfy one
or several starts. The requested `completion_through` bound distinguishes a
true non-completion from missing follow-up coverage.

## Typed Lifecycle Analysis

Lifecycle analysis consumes one ready StateModel handle. The v1 operators are:

```text
lifecycle.distribution -> LifecycleFrame[distribution]
lifecycle.transitions  -> LifecycleFrame[transitions]
lifecycle.dwell        -> LifecycleFrame[dwell]
lifecycle.violations   -> LifecycleFrame[violations]
```

### Closed lifecycle shapes

`distribution` emits one row per as-of bucket and state:

```text
as_of_or_bucket
state
subject_count
share
```

`transitions` emits one row per from/to state pair:

```text
from_state
to_state
transition_count
share
```

`dwell` emits one row per state:

```text
state
interval_count
completed_count
censored_count
mean_duration
median_duration
p90_duration
```

`violations` emits one row per modeled replay violation:

```text
subject_identity
trigger_event_ref
trigger_event_identity
occurred_at
state_at_event
violation_kind          illegal_transition | transition_from_terminal
```

A violation is an observation of the declared StateModel applied to observed
Events. It is not automatically a policy breach, data-quality failure,
business Rule, or causal fact.

## EventFrame and LifecycleFrame Algebra

`EventFrame` and `LifecycleFrame` are distinct public artifact families with
closed shape-dispatched metadata. They do not use an optional-field mega-class
and do not inherit MetricFrame semantics.

Every artifact carries:

- subject `EntityRef` and identity signature;
- `AnalysisScope` and exact half-open time coverage;
- Event or StateModel refs and semantic fingerprints;
- executable Entity and Relationship path refs;
- matching, replay, seed, or state-projection contract;
- quality and coverage status;
- artifact family, closed shape, row-contract version, and lineage;
- evidence status and bounded evidence summary.

The only v1 continuations are:

- `show()` and `contract()`;
- bounded evidence reads;
- session summary, job, and `get_frame` recovery;
- `assess_quality`;
- terminal `to_pandas()`.

Metric-only compare, attribute, correlate, hypothesis-test, forecast, discover,
and generic transform operations reject both families unless a future design
defines one exact shape-specific semantic relationship. Implementing the
shared artifact protocol alone never grants an operator affordance.

## Optional Ontology Extension

### Module ownership

Ontology is authored and loaded through a separate public module:

```python
import marivo.ontology as mo

mo.help()
ontology = mo.load(semantic=catalog)
```

`mo.help(...)` owns ontology authoring and browsing contracts. `mo.load(...)`
validates all endpoints against the supplied semantic catalog and returns an
immutable `OntologyCatalog`. The authored ontology root is optional. The
returned catalog records `configured=False` when no ontology root is present;
ordinary semantic and analysis loading still succeeds.

Ontology code ships with Marivo; optionality concerns authored ontology
content, not a plugin or alternate package installation.

### No second executable identity

Ontology v1 contains no standalone term, generic node, runtime instance, or
independent business-object identity. Every endpoint is one of this closed
typed-ref union:

```text
EntityRef
DimensionRef
TimeDimensionRef
MeasureRef
MetricRef
RelationshipRef
EventRef
StateModelRef
```

Bare semantic-id strings are not accepted by the authoring API. Physical
datasource refs and analysis artifact refs are not ontology endpoints.

### Read-only semantic index

The `OntologyCatalog` builds deterministic read-only indexes from executable
semantic declarations, including:

- Entity ownership of Dimensions, TimeDimensions, and Measures;
- Metric dependencies and root Entity;
- Measure-to-Metric observation relationships (`measures` resolution);
- Event source Entity and participant roles;
- StateModel subject Entity and transition Events;
- executable Relationship adjacency.

These entries are derived indexes, not authored `SemanticEdge` objects. They
have no independent edge identity and cannot be overridden. The ontology
extension may use them for search, explanation, and the final mechanical
resolution step of a bounded candidate bridge, but never as new executable
facts.

### Explicit SemanticEdge

The only authored ontology object in v1 is `SemanticEdge`:

```python
refund_pressure = mo.edge(
    name="refund_pressure_influences_order_health",
    source=refund_rate,
    target=healthy_order_rate,
    family="causal_hypothesis",
    predicate="influences",
    rationale="Refund pressure may precede deterioration in order health.",
    provenance=mo.business_hypothesis(owner="commerce-analytics"),
)
```

`mo.edge(...)` returns `SemanticEdgeRef`. Every edge has a stable identity,
typed source and target refs, one closed family/predicate combination,
rationale, and provenance.

| Family | Predicates | Allowed use | Forbidden inference |
| --- | --- | --- | --- |
| `temporal` | `precedes`, `follows` | Candidate Event navigation | Actual row order or automatic sequence |
| `causal_hypothesis` | `causes`, `influences` | Driver-hypothesis candidate | Causal fact, effect, or confidence |
| `taxonomic` | `specializes`, `part_of` | Browse and explanation | Property, identity, or readiness inheritance |
| `contextual` | `related_to`, `contrasts_with` | Association-hypothesis candidate | Joinability or statistical association |

All authored edges require provenance. A causal-hypothesis edge additionally
requires either a reviewable external source or an explicitly labeled business
hypothesis owner. The edge itself is never evidence.

SemanticEdge carries no identity mapping, row predicate, join key, expression,
Metric definition, readiness result, artifact, or runtime truth value. It
cannot make an invalid semantic plan valid.

### Loading states

Ontology state is closed and observable:

- not configured: the semantic and analysis cores remain fully usable;
- configured and valid: ontology browsing and candidate discovery are enabled;
- configured but empty for a source: discovery returns an empty CandidateSet;
- configured but invalid: `mo.load(...)` fails with typed endpoint or edge
  validation evidence rather than silently dropping definitions.

Calling ontology-guided discovery when the session has no configured ontology
raises `OntologyNotConfiguredError` with
`kind="ontology_not_configured"`. This is distinct from a successful empty
candidate result.

## Typed Ontology-to-Analysis Bridge

### Public operator

`session.discover.semantic_hypotheses(source)` is the sole v1 execution bridge
from ontology context into typed analysis.

`source` must be a persisted `MetricFrame` or `DeltaFrame` with exactly one
recoverable source Metric lineage. A Delta comparing two observations of the
same Metric is legal. Missing or multiple Metric identities raise
`missing_metric_lineage` or `ambiguous_metric_lineage`; the operator never
selects one.

The result is the existing `CandidateSet` family with closed shape
`semantic_hypothesis`.

### Resolution algorithm

For the source Metric and its root Entity, discovery performs exactly these
steps:

1. Read explicit outgoing or incoming `causal_hypothesis` and `contextual`
   SemanticEdges whose configured direction is relevant to the source.
2. Traverse at most one such explicit edge.
3. If the target is a MetricRef, use that Metric directly.
4. Otherwise consult the semantic catalog's read-only dependency and
   `measures` indexes to resolve every Metric that explicitly observes or
   depends on the target.
5. Evaluate normal semantic readiness for each resolved Metric under the
   inherited analysis scope and time contract.

There is no recursive graph traversal, path search, popularity ranking,
heuristic target choice, or implicit Metric authoring.

If one target resolves to several Metrics, every Metric becomes an independent
candidate. If it resolves to none, one blocked candidate preserves the target
and the reason `no_observable_metric`. If a Metric exists but readiness fails,
the blocked row preserves the Metric and typed readiness repair.

### Candidate row contract

Each semantic-hypothesis row carries:

```text
candidate_kind          driver_hypothesis | association_hypothesis
source_metric_ref
source_entity_ref
semantic_edge_ref
edge_family
target_semantic_ref
resolved_metric_ref     optional
status                  ready | blocked
blocked_reason           optional closed reason
analysis_target          optional SemanticMetricCandidate
affordances              item-level only
```

Candidate identity is the stable tuple of edge ref, target semantic ref, and
resolved Metric ref or blocked sentinel. Rows are deterministically ordered by
edge family, target typed identity, and resolved Metric identity. They have no
score, correlation, effect size, causal confidence, recommendation, or
artifact-level next step.

### Explicit re-entry into analysis

A ready row exposes `analysis_target`, an immutable
`SemanticMetricCandidate`. It has no public constructor and carries:

- resolved MetricRef;
- candidate-set and item identities;
- SemanticEdgeRef;
- inherited scope and time contract;
- readiness fingerprint.

The agent must explicitly select the row and call `session.observe(target)`.
`observe` unwraps the governed Metric and records the candidate origin in the
new MetricFrame lineage. It does not alter ordinary MetricRef behavior.

A blocked row exposes no `analysis_target` or `observe` affordance. Selecting
that attribute returns the row's typed readiness or semantic repair instead of
a MetricRef.

### Other edge families

Temporal edges appear only in Event object details as candidate navigation.
The caller still supplies every Event and its order to `events.sequence` or
`events.funnel`.

Taxonomic edges appear only in ontology browsing and explanation. They do not
create analysis populations, expand filters, inherit semantic fields, or
change readiness.

No SemanticEdge can generate a join, filter, MetricFrame, EventFrame,
LifecycleFrame, fact, or proposition.

## Evidence Engine Integration

### Closed evidence subjects

The Evidence Engine keeps one closed subject union:

```text
MetricEvidenceSubject
EventEvidenceSubject
LifecycleEvidenceSubject
```

Event and lifecycle subjects use EntityRef, Event or StateModel refs, identity
signature, shape, scope, and time contract. Ontology edges and candidate rows
are lineage, not evidence subjects.

### Event and lifecycle observations

Every successfully committed EventFrame or LifecycleFrame invokes exactly one
bounded shape-dispatched observation extractor:

- sequence: journey, completion, partial, unused-event, and censoring counts;
- funnel: cohort, ordered-stage, conversion, unused-event, and censoring
  summaries;
- time-to-event: attempt, completion, non-completion, duration, unused-end, and
  censoring summaries;
- distribution: bounded state counts and shares;
- transitions: bounded from/to counts and shares;
- dwell: bounded interval/completed/censored counts and duration summaries;
- violations: bounded counts by closed violation kind and current state.

The digest contains aggregates and semantic refs, never raw subject or event
identities. It is a business observation and seeds no proposition or fact.

Extractor or evidence-write failure does not roll back the artifact. The
artifact remains usable with `evidence_status="partial"` and a typed
`evidence_partial` issue; no digest is fabricated.

### Candidate non-seeding

`CandidateSet[semantic_hypothesis]` emits no finding, observation,
proposition, fact, open item, or system-level recommended follow-up. Edge,
target, readiness, reason, and item affordances stay in the candidate payload
and lineage.

If an observed candidate later participates in `correlate` or
`hypothesis_test`, the existing association or tested-hypothesis evidence path
retains the candidate-set id, item id, and SemanticEdgeRef. Statistical
association or hypothesis-test evidence does not upgrade the originating edge
to causal evidence.

## Analysis Capability Registration

The analysis capability registry receives two independent extensions.

### Semantic execution extension

Registers:

- semantic inputs `EventSemantic`, `StateModelSemantic`, and typed participant
  roles over `EntitySemantic`;
- `events.sequence`, `events.funnel`, `events.time_to_event`;
- `lifecycle.distribution`, `lifecycle.transitions`, `lifecycle.dwell`,
  `lifecycle.violations`;
- `EventFrame` and `LifecycleFrame`, their closed shapes, quality consumer,
  recovery contracts, evidence access, and terminal boundary.

This extension has no ontology precondition.

### Ontology discovery extension

Registers:

- `discover.semantic_hypotheses`;
- `CandidateSet[semantic_hypothesis]`;
- the consumed-not-constructed `SemanticMetricCandidate` value;
- the ontology-configured precondition and candidate item affordances.

The operator remains statically discoverable in focused analysis help even
when no ontology is configured. Its runtime precondition distinguishes that
state from an empty result.

Neither extension changes existing Metric operators or family edges.

## Help, Browsing, and Recovery

Public guidance preserves one owner per contract:

- `ms.help(...)` describes Event, StateModel, participant, transition, seed,
  state projection, verification, preview, and readiness;
- `mo.help(...)` describes ontology loading, SemanticEdge authoring, browsing,
  provenance, and non-executable boundaries;
- `mv.help(...)` describes Event/Lifecycle operators, artifact families,
  semantic-hypothesis discovery, family validation, and recovery;
- `show()` provides bounded current content and evidence;
- `contract()` provides mechanically valid continuations;
- structured errors provide typed repair.

Ordinary analysis agents discover ready Event and StateModel handles through
the session semantic catalog. They do not manipulate source internals or raw
SemanticEdge objects. Ontology authors and auditors may inspect the
OntologyCatalog through `mo`, but analysis agents use the typed candidate
bridge.

Recovery preserves exact EventFrame or LifecycleFrame family, closed shape,
row-contract version, semantic refs, evidence status, and lineage. A recovered
artifact is never coerced to MetricFrame or a generic table.

## Lineage

Event artifact lineage records:

- Event refs and semantic fingerprints;
- source Entity and Relationship path refs;
- identity, occurrence time, participant roles, scope, and coverage;
- row predicate fingerprint;
- sequence order, matching policy, follow-up bound, and censoring;
- compiled expression and backend execution evidence.

Lifecycle lineage additionally records StateModel, transition set, replay seed
or state projection, ordering checks, coverage, and reconciliation evidence.

Ontology candidate lineage records OntologyCatalog fingerprint,
SemanticEdgeRef, source and target refs, resolved Metric or blocked reason,
candidate identity, readiness fingerprint, and deterministic ordering key.

Lineage records provenance. It never turns ontology context into executable
truth or causal evidence.

## Structured Errors

All new public errors follow the existing SemanticError or AnalysisError
templates and include expected, received, location, and typed repair.

| Error kind | Trigger |
| --- | --- |
| `invalid_event_identity` | Event identity is empty, nullable, non-scalar, or inconsistent with the source Entity key |
| `invalid_event_time` | `occurred_at` is not a ready TimeDimension on the source Entity |
| `invalid_participant_path` | participant path is discontinuous, not to-one, or ends at the wrong Entity |
| `ambiguous_participant_role` | subject Entity matches multiple roles and the caller supplied none |
| `invalid_state_transition` | transition references invalid states, Event, role, or overlaps another transition |
| `insufficient_state_history` | replay cannot establish state before the requested range |
| `ambiguous_event_order` | same-time Events have no deterministic business order and affect replay |
| `ontology_not_configured` | ontology-guided discovery called without configured ontology content |
| `invalid_ontology_ref` | SemanticEdge endpoint does not resolve to an allowed semantic kind |
| `invalid_semantic_edge` | family, predicate, provenance, or direction violates the closed edge contract |
| `missing_metric_lineage` | discovery source has no recoverable source Metric |
| `ambiguous_metric_lineage` | discovery source resolves to several source Metrics |
| `candidate_not_observable` | selected row has no ready analysis target |

An empty configured ontology result is not an error. A blocked candidate is a
valid CandidateSet row, not a failed operator commit.

## Module Ownership

### `marivo.semantic as ms`

Owns executable semantic identities, Entity/field/Measure/Metric/Relationship
contracts, Event and StateModel definitions, semantic catalog assembly,
verification, preview, and readiness.

### `marivo.ontology as mo`

Owns optional SemanticEdge authoring, ontology provenance, typed-ref
validation, the read-only semantic dependency index, ontology browsing, and
OntologyCatalog fingerprinting. It owns no executable expression or analysis
artifact.

### `marivo.analysis as mv`

Owns typed operators, sessions, compilation and execution entrypoints, typed
artifacts, recovery, artifact contracts, Evidence Engine integration, and the
narrow semantic-hypothesis candidate bridge.

### Future decision extension

May define Rule, Policy, and Action by consuming ontology context and assessed
evidence through explicit read boundaries. It cannot retroactively change the
meaning of a semantic definition or an evidence record. No public decision API
is defined here.

## Behavioral Fixtures

### Stable Metric lane without ontology

Load and analyze current Metrics with no ontology root. Observe, compare,
attribute, correlate, hypothesis-test, forecast, quality, recovery, and
evidence behavior remain unchanged.

### Shared event Entity

Define request and completion Events over one event-log Entity using distinct
typed predicates. Run sequence, funnel, and time-to-event with explicit order,
subject Entity, typed roles, matching policy, and coverage bounds. Verify exact
EventFrame shapes, censoring, recovery, and observation digests.

### Lifecycle replay and state projection

Replay a StateModel from a valid seed, produce all four LifecycleFrame shapes,
and verify deterministic ordering and violation behavior. Separately compute a
distribution from a versioned state projection and reconcile it with replay as
quality evidence without silently choosing a winner.

### Ontology not configured versus empty

With no ontology root, semantic-hypothesis discovery raises
`ontology_not_configured` while all other analysis remains legal. With a valid
ontology catalog containing no eligible edge for the source Metric, discovery
returns a committed empty CandidateSet.

### Causal-hypothesis candidate

A causal-hypothesis edge from the source Metric or root Entity produces a
`driver_hypothesis` candidate. The candidate contains no causal confidence and
creates no fact. Only an explicit observe followed by correlation or hypothesis
test produces existing statistical evidence with retained edge lineage.

### Non-Metric target resolution

One edge target resolves through the semantic dependency index to several
Metrics; all appear as separate deterministically ordered candidates. Another
target resolves to none and remains visible as one blocked row with no observe
affordance.

### Temporal navigation

A temporal edge between Events appears in Event details but neither executes
nor pre-fills sequence/funnel order. The caller must still pass the full typed
Event sequence.

### Cold-agent path

A cold general-purpose coding agent:

1. discovers ready Metrics, Events, and StateModels through the semantic
   catalog;
2. completes Metric, Event, and Lifecycle paths with no ontology configured;
3. discovers ontology hypotheses through the typed CandidateSet surface;
4. inspects ready and blocked candidates and explicitly observes one ready
   target;
5. recovers EventFrame and LifecycleFrame artifacts and reads their contracts;
6. never manipulates source internals, raw SemanticEdge objects, or a graph
   query language;
7. never describes a candidate, association, test, or violation as causal
   proof or an automatically binding decision.

## Acceptance Criteria

The design is accepted when:

1. Existing Entity, field, Measure, Metric, Relationship, readiness, and Metric
   analysis contracts remain the only executable core and are explicitly
   unchanged.
2. Event and StateModel reuse existing typed semantic refs and readiness; they
   introduce no parallel source, identity, or join system.
3. Event and lifecycle analysis is fully usable without ontology.
4. Every Event/Lifecycle operator has one canonical id, one output family, and
   one closed shape.
5. EventFrame and LifecycleFrame recover with family and shape intact, accept
   quality assessment, and fail every Metric-only family gate.
6. `marivo.ontology` owns optional knowledge context and accepts only the
   closed typed semantic-ref union.
7. Ontology v1 has no standalone node, runtime instance, generic traversal, or
   executable semantic authority.
8. Derived structural indexes are read-only and cannot be authored or
   overridden as SemanticEdge objects.
9. An authored SemanticEdge cannot create identity, join, filter, population,
   readiness, SQL, artifact, proposition, fact, or causal evidence.
10. `discover.semantic_hypotheses` performs at most one explicit edge traversal
    plus one mechanical semantic-index resolution and returns every resolved
    Metric or one blocked target.
11. Candidates are unscored, deterministic, item-affordance-only, and never
    automatically executed or recommended by the system.
12. Missing ontology and configured-empty ontology have distinct typed
    behavior.
13. Event/Lifecycle commits create bounded observations but no propositions or
    facts; candidate commits create no findings at all.
14. Later association/test evidence preserves candidate and SemanticEdgeRef
    lineage without becoming causal evidence.
15. Rule, Policy, and Action remain outside semantic, ontology, analysis, and
    evidence type algebras in this release.
16. A cold agent completes all legal paths without raw ontology traversal or
    source-level authoring objects.

## Implementation Boundary

This document specifies a future public contract only. The semantic execution
extension and ontology discovery extension are independent release units:

- Event/StateModel, their analysis operators, artifact families, recovery, and
  evidence extractors must ship atomically as one semantic execution extension;
- SemanticEdge authoring, ontology loading, candidate discovery, candidate
  selection, lineage, and non-seeding evidence behavior must ship atomically as
  one ontology discovery extension.

The ontology extension may ship after the semantic execution extension. It is
never a prerequisite for current Metric analysis or Event/Lifecycle analysis.
This specification revision does not implement runtime code, skills, or site
documentation.
