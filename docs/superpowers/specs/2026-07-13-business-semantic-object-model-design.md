# Marivo Business Semantic Object Model Design

Status: written-review revisions integrated; pending written-spec re-review

Date: 2026-07-13

## Summary

Redesign Marivo's semantic layer from first principles as a strongly typed
business semantic graph grounded in executable datasets and consumed through
task-specific analytical intents.

The design adds four business primitives—`Concept`, `Property`, `Event`, and
`StateModel`—and separates three different meanings that are commonly
collapsed under “relationship”:

- `BusinessRelation` describes queryable instance-level relationships between
  concepts;
- `SemanticEdge` describes catalog-level relationships used for bounded
  navigation and planning;
- `DataJoin` describes physical recordset composition.

Business definitions do not know about tables or columns. Physical data does
not acquire business meaning by naming convention. First-class, typed
bindings connect the two planes, and explicit `DataJoin` objects describe only
physical recordset composition. A deterministic binding resolver lowers a
task requirement to one legal canonical intermediate representation, then to
Ibis and backend SQL.

The public analysis surface remains metric-centered. General-purpose coding
agents do not receive a generic ontology query language or have to manipulate
bindings. They use governed metrics for ordinary analysis and task-specific
event and lifecycle intents for the new capabilities. Deeper business and
grounding objects are progressively disclosed only when required.

This is an atomic from-scratch target. It provides no compatibility aliases,
migration adapters, dual model, or preservation of current object shapes.

## Scope

This design covers the pure data-analysis semantic kernel:

- business type definitions;
- catalog-level semantic navigation;
- dataset descriptions and business-to-data grounding;
- governed analytical projections and metrics;
- subject-aware metric analysis;
- event sequence, funnel, and time-to-event analysis;
- lifecycle distribution, transition, dwell, and violation analysis;
- deterministic semantic planning, SQL lowering, validation, readiness, and
  artifact lineage;
- a progressive-disclosure surface suitable for general-purpose coding
  agents.

This design intentionally excludes:

- runtime business instances or a knowledge-base assertion store;
- `Rule`, `Policy`, `Action`, permission, obligation, or operational workflow
  objects;
- action execution or write-back to business systems;
- compatibility or migration from the current Marivo model;
- a universal graph query language;
- arbitrary raw SQL as semantic source code;
- implicit join inference from column names;
- heuristic fallback between semantically ambiguous bindings;
- causality claims inferred from catalog edges;
- a cost optimizer that is allowed to change semantic meaning.

Rules, policies, and actions are a future layer that may depend on this
analysis kernel. They must not be simulated through analytical filters,
semantic edges, or state transitions in this design.

## Problem

Metric-oriented semantic layers can answer well-formed aggregation questions,
but they usually leave important business structure implicit:

- a metric cannot reliably state which business subject it quantifies;
- an event stored as a row is indistinguishable from an entity snapshot unless
  authors and agents remember conventions;
- multi-party events cannot express the role each participant plays;
- a lifecycle is reconstructed ad hoc from values and timestamps;
- one “relationship” construct is asked to mean business association, catalog
  relevance, and physical joinability at the same time;
- equivalent physical sources cannot be selected deterministically without a
  first-class grounding contract;
- agents must infer exploration scope from names, prose, or schema topology;
- SQL generation can be syntactically correct while answering the wrong
  business question.

Adding a general ontology does not solve these problems by itself. Common
ontology models are strong at naming classes and predicates, but often leave
the following analytical contracts underspecified:

- whether a declaration defines a type or asserts a runtime instance;
- which relationships are navigational hints versus executable facts;
- how business identities and participant roles map onto recordsets;
- how grain, fanout, time alignment, freshness, and source coverage constrain
  a query;
- how an analysis requirement selects exactly one legal binding;
- how a semantic path becomes relational algebra and SQL;
- how the resulting artifact preserves reproducible lineage.

Marivo therefore adopts ontology-like type semantics without adopting a
universal ontology runtime.

## Design Goals

### Semantic precision

Every first-class object has one meaning. Catalog navigation, instance-level
business truth, physical joins, and analytical projections are separate
contracts.

### Executability

Every supported analytical intent either lowers through typed canonical
relations to Ibis and SQL or fails with a structured capability error. Prose
never silently supplies executable meaning.

### Determinism

The same catalog, binding state, task requirement, and execution scope produce
the same semantic plan. Ambiguous semantics fail before query execution.

### Agent usability

An ordinary coding agent can discover and use governed analysis without
learning the full object system. The default route exposes metrics,
dimensions, artifacts, and task-relevant business context; grounding internals
remain diagnostic.

### Authoring integrity

Business authors can define stable concepts independently of a warehouse.
Data authors can describe physical recordsets independently of business
meaning. Binding authors must make the relationship explicit and verifiable.

### Reproducibility

Definitions, bindings, plans, compiled SQL, execution scope, and quality
evidence are fingerprinted and retained in artifact lineage.

## Alternatives Considered

### Universal ontology kernel

Represent everything as `NodeType`, `Predicate`, `Assertion`, and `DataMap`,
then expose graph traversal and graph query as the core language.

This model is maximally extensible, but it pushes essential distinctions into
conventions. Agents and validators would have to rediscover which predicates
are structural, executable, temporal, causal, or merely contextual. SQL
lowering would depend on metadata outside the generic graph, and catalog
instances could be confused with business instances.

### Relation-algebra-first semantic views

Represent concepts, events, states, and relations as typed relational views
with annotated column roles, then make relational composition the semantic
language.

This model lowers naturally to SQL, but it makes physical shape the primary
abstraction. Business identity, participant roles, lifecycle rules, and
catalog exploration become view annotations rather than durable semantic
objects. It also encourages ordinary agents to reason directly about joins.

### Strongly typed business semantic graph — selected

Use distinct business object types, a separate grounding graph, governed
analytical projections, and task-specific analytical intents. This design
retains the useful part of ontology—stable types and semantic
relationships—while making execution and agent-facing contracts explicit.

## Architectural Model

The target architecture has four one-way planes:

```text
Catalog Kernel
    Domain, typed references, identity, fingerprints
        |
        v
Business Semantic Graph
    Concept, Property, Event, StateModel,
    BusinessRelation, SemanticEdge
        |
        v
Data Grounding Graph <----- Dataset and DataJoin
    ConceptBinding, EventBinding,
    RelationBinding, StateBinding
        |
        v
Analysis Compiler and Runtime
    Intent -> Requirement -> Semantic Plan -> Canonical IR
           -> Ibis -> SQL -> Typed Artifact
```

Dependencies point downward only:

- business definitions depend on the catalog kernel but not on data;
- datasets describe physical records but not business meaning;
- bindings depend on both business objects and datasets;
- analytical projections depend on business properties;
- metrics depend on governed projections and business subjects;
- analysis depends on verified business and grounding state.

The following boundaries are absolute:

- a `SemanticEdge` may influence exploration but cannot create a join, filter,
  or SQL expression;
- a `BusinessRelation` becomes queryable only through a verified
  `RelationBinding` and the necessary executable `DataJoin` path;
- a binding is not part of the ordinary analysis-agent path;
- a table or column name never creates a business object automatically;
- a generated SQL expression is output, not a semantic definition.

## Catalog Kernel

### SemanticProject

`SemanticProject` is the assembly and fingerprint boundary for one deployable
semantic catalog. It owns domain registration, definition versions, dependency
resolution, environment-independent catalog validation, and the root project
fingerprint.

It is not a business object, tenant, datasource, or runtime analysis session.
The same verified project definition may be bound to different authorized
execution environments without changing its business-object identities; the
environment-specific grounding and readiness fingerprints remain distinct.

### Type declarations, not instances

The catalog stores definitions of business and analytical types. It does not
store runtime orders, customers, refund requests, or state instances.

For example, `concept.commerce.order` defines what an order is. It does not
identify order `O-123`. Runtime identities exist only in canonical query
relations and artifacts.

### Domains

`Domain` is a catalog namespace and ownership boundary, not a business entity
and not a data source. Every business object belongs to exactly one domain.

### Stable typed references

References are kind-qualified and globally unambiguous.

Top-level objects use:

```text
<kind>.<domain>.<name>
```

Examples:

```text
concept.commerce.order
event.commerce.refund_requested
state_model.commerce.order_lifecycle
business_relation.commerce.order_merchant
semantic_edge.commerce.refund_affects_order_health
metric.commerce.refund_rate
```

Owned properties use:

```text
property.<owner-kind>.<domain>.<owner-name>.<name>
```

Examples:

```text
property.concept.commerce.order.order_no
property.event.commerce.refund_requested.occurred_at
```

Nested identities, participant roles, states, transitions, mappings,
normalized mapping fingerprints, grounding scopes, relation paths, metric
axes, predicates, parity cases, and parity oracles are immutable value objects.
They do not receive standalone catalog identities.

### Common business-object contract

Every business object requires:

- stable typed identity;
- domain ownership;
- a concise business definition;
- a versionable structural fingerprint.

Synonyms and examples are optional search aids. They never change execution.
Executable roles, cardinalities, states, and temporal semantics must be
structured fields, not facts hidden in prose.

No business object may reference a table, column, datasource, SQL fragment, or
backend dialect.

### Canonical business time and interval algebra

The kernel has one temporal algebra. Every business timestamp is a typed
instant whose source timezone must be resolvable. The compiler normalizes
instants to UTC for comparison while retaining the declared timezone,
calendar, and original value in lineage.

Every validity or coverage interval is half-open: `[start, end)`. It contains
an instant `t` exactly when `start <= t < end`. A missing lower or upper bound
means negative or positive infinity only where that object's contract permits
an open bound. A validity row requires `valid_from`; a null `valid_to` means
positive infinity. Zero-length and negative intervals are invalid. Two
intervals overlap when their intersection has positive duration; adjacent
intervals whose boundaries are equal are non-overlapping and gapless.

These rules apply without per-object endpoint overrides to binding coverage,
`effective_dated` relations, versioned concept properties, state intervals,
analysis windows, and temporal `DataJoin` predicates. Dataset metadata may
describe a physical source with different endpoint conventions, but its
dataset expression must normalize them to this algebra before a binding can
use it. The target model therefore replaces, rather than preserves, any
pre-cutover closed-closed validity option.

Snapshot selection chooses the latest version whose `as_of` is less than or
equal to the requested instant. An `event_anchored` relation is an
event-sourced relation version: an anchor establishes one target for a source
from the anchor's `occurred_at` until that source's next anchor. Two different
anchor identities for the same source at the same instant are ambiguous even
when they name the same target; an exact duplicate anchor identity is a
duplicate-quality failure. The planner never orders anchors by identity or
declaration order.

## Business Semantic Graph

### Concept

`Concept` defines a persistent, identifiable kind of business subject, such as
an order, merchant, customer, product, or subscription.

A concept is not:

- a database table;
- a record instance;
- the current state of an entity;
- a metric population;
- an arbitrary grouping label.

Each concept declares at least one identity scheme. Exactly one identity
scheme is canonical; alternate schemes are allowed. An identity scheme refers
only to properties owned by the concept.

Every identity component must be scalar, required, non-null in every complete
binding, deterministic, and equality-comparable after type normalization.
Collection properties, floating-point values without a canonical equality
encoding, and nondeterministic expressions cannot participate in identity.
The declared component order is part of the identity scheme: a composite
identity is the normalized ordered tuple, not an unordered property set.
Canonical and alternate schemes obey the same invariants.

Concept specialization may be expressed with a taxonomic `SemanticEdge`, but
it does not automatically inherit properties, identities, bindings, or
readiness. Any desired reuse must be declared explicitly.

### Property

`Property` defines one named business attribute owned by exactly one
`Concept` or `Event`.

It declares:

- its owner;
- value type;
- scalar or collection cardinality;
- required or nullable semantics;
- value constraints where appropriate;
- unit, timezone, or calendar semantics where appropriate;
- a business definition.

A property has one owner even if several objects use the same vocabulary.
Shared semantics belong in reusable value types, units, and value constraints,
not in shared property identity. For example, order currency and refund
currency may both use an ISO currency value type while remaining different
properties.

A property does not map to a column and is not automatically a dimension,
measure, or time dimension. Those are governed analytical projections.

### Event

`Event` defines an immutable point occurrence in business time, such as
`PaymentCaptured`, `RefundRequested`, or `RefundCompleted`.

Every event declares:

- one event identity scheme;
- exactly one owned property as `occurred_at`;
- one or more named participant roles;
- zero or more additional owned properties;
- a business definition.

Each participant role references a `Concept` and declares cardinality. Roles
are semantic names such as `order`, `merchant`, `payer`, `payee`, `buyer`, or
`seller`; they are not join aliases.

Event identity components obey the same scalar, required, deterministic,
equality-comparable rules as concept identity. `occurred_at` must be a scalar,
required temporal property with a resolvable timezone. Participant
cardinality is closed in v1 to `one` and `optional_one`; a many-valued
participant must be reified as an event or association with one row per
participant identity. A role used as an analytical subject must be `one` and
non-null in the selected binding. `optional_one` is available only as context
and cannot partition event analysis.

An event has no mandatory “primary subject.” A metric or event analysis chooses
which participant role supplies its subject. If exactly one eligible `one`
role targets the requested subject concept, the planner may select it.
Otherwise the author or caller must specify an eligible role; the planner
never guesses.

An event represents a point, not an interval. Intervals are derived from event
pairs or state transitions.

Distinct business event types remain distinct even when they share a physical
table. A binding-level row discriminator selects their records. Raw event
codes do not become a general business-rule object.

### StateModel

`StateModel` defines a finite, verifiable lifecycle for one `Concept`.

It declares:

- the owned concept;
- a closed set of state values;
- exactly one default initial state;
- zero or more terminal states;
- a closed set of allowed transitions;
- the event and participant role that trigger each transition;
- one closed replay-seed contract when event replay is supported.

Each transition's participant role must identify an instance of the owned
concept. A transition may optionally constrain the source state, and it must
declare the resulting state.

Transitions must be deterministic. For one source state, triggering event,
and participant role, at most one target state is legal. A source-wildcard
transition cannot overlap a source-specific transition for the same event and
role. Ambiguous transition definitions fail graph assembly.

States and transitions are nested value objects, not top-level catalog
objects. A concept may have multiple independent state models, such as payment
status and fulfillment status. Each model is named and analyzed separately.

A state model supports two evidence paths:

- replay event bindings to derive state intervals;
- read an explicit `StateBinding` snapshot or validity history.

When both are present, Marivo may reconcile them. A material mismatch is
quality evidence or a blocker; neither source silently overwrites the other.

Event replay never assumes that every subject was in the initial state at the
requested window start. It requires one `ReplaySeed` per subject before
applying later transitions. The closed seed variants are:

- `initial_event(event, role)`: the first occurrence of the declared
  inception event establishes the model's default initial state at that
  event's `occurred_at`;
- `external_seed`: grounding must supply the latest trusted snapshot or
  validity row at or before the replay range as the subject's state and seed
  instant. The state model declares the requirement but does not reference a
  binding.

The seed is part of the replay requirement and canonical IR, not a heuristic.
If neither an inception event within complete lifecycle history nor a trusted
state seed exists at or before the required replay range, lifecycle replay
fails with `insufficient_state_history`. The planner reconstructs state before
clipping to the requested analysis window; it does not discard pre-window
events and bootstrap again at the window boundary.

Transition-event coverage must be continuous from the seed instant through
the requested range. A gap in binding or dataset coverage evidence after the
seed also raises
`insufficient_state_history`; replay does not treat absence of evidence as
evidence that state remained unchanged.

For `initial_event`, two distinct earliest seed events at one timestamp, or a
seed event concurrent with the first transition, raise
`ambiguous_replay_order`. A later recurrence of the initialization event is a
lifecycle violation and leaves state unchanged. Event identity never breaks a
seed-order tie.

Evidence capabilities are deliberately asymmetric:

| Evidence path | Supported lifecycle intents |
| --- | --- |
| Complete seeded event replay | distribution, transitions, dwell, violations |
| Validity-history `StateBinding` | distribution, transitions, dwell |
| Point or snapshot `StateBinding` | distribution only at covered `as_of` instants |

A state history alone cannot prove which triggering event caused a boundary
or whether an illegal event occurred, so it cannot answer violations. A
snapshot series is not treated as continuous state history unless the binding
declares normalized validity intervals. Reconciliation compares only the
overlapping evidence range and preserves each path's capability limits.

State replay orders transition events by the business timestamp mapped to
`occurred_at`. Two distinct transition-triggering event identities for the
same subject and the same `occurred_at` are an ambiguous concurrency class.
Replay raises `ambiguous_replay_order` and blocks lifecycle readiness; it does
not sort by event identity, catalog declaration order, event type name, or an
implicit transition priority.

An author who needs deterministic replay must ground a more precise business
timestamp or model upstream events whose timestamps preserve the real order.
This kernel does not introduce a state-model priority list because that would
invent business chronology absent from the event evidence. Exact duplicate
rows with the same event identity are a separate event-binding quality error,
not a replay tie-break case.

For a valid seed and unambiguous order, a modeled transition event that has no
legal transition from the subject's current state is emitted as a lifecycle
violation. The current state remains unchanged and replay continues with the
next event. This rule also applies to an outgoing event from a terminal state.
Events not referenced by the state model are not replay inputs and are not
violations.

A state model does not contain arbitrary conditions, permissions, policies, or
actions.

When a transition appears to need a guard, the canonical pattern is to define
distinct business `Event` types and use `EventBinding` row discriminators to
map the physical event codes or predicates into those types. The state model
then references the resulting event types. Guard predicates do not enter the
transition model.

### BusinessRelation

`BusinessRelation` defines a queryable, instance-level binary relationship
between two concepts, such as “order belongs to merchant” or “subscription is
owned by customer.”

It declares:

- source and target concepts;
- forward and inverse business labels;
- source-to-target and target-to-source cardinality;
- a closed temporal mode: `immutable`, `effective_dated`, or
  `event_anchored`;
- a business definition.

It does not declare join keys. A `RelationBinding` supplies physical grounding.

Business relations are binary and have no properties. If an association has
its own identity or properties, or has more than two participants, authors
reify it as a `Concept` and connect it with business relations or events.

### SemanticEdge

`SemanticEdge` defines a directed catalog-level relationship between any two
catalog objects. It helps search and planning discover relevant objects; it
does not assert that two runtime business instances are related.

Every edge belongs to one closed planner family:

| Family | Typical predicates | Planner meaning | Forbidden inference |
| --- | --- | --- | --- |
| Structural | `owns`, `participates_as`, `governed_by`, `measures` | Direct dependency or affordance | Joinability or readiness |
| Temporal | `precedes`, `follows` | Candidate event sequence | Actual row order before query |
| Causal | `causes`, `influences` | Hypothesis or driver candidate | Proven causality |
| Taxonomic | `specializes`, `part_of` | Directory or category expansion | Property or binding inheritance |
| Contextual | `related_to`, `contrasts_with` | Weak discovery hint | Join, filter, or analytical validity |

Structural edges that follow mechanically from declarations are derived by
the catalog. Explicit edges have stable catalog identities and declare a
rationale plus provenance. Causal-family edges require a reviewable external
source or an explicitly labeled business hypothesis; the edge itself is never
causal evidence. A business label may extend the vocabulary only if it maps to
one closed family. Unknown families fail validation.

Semantic edges carry no SQL, predicate, join key, or runtime truth value.

## Data Grounding Graph

### Dataset

`Dataset` is a named executable recordset owned by `marivo.datasource`. It
describes physical structure without assigning business meaning.

It declares:

- datasource and physical source;
- typed fields and field references;
- row key;
- structural grain;
- one closed versioning model: `static`, `append_only`, `snapshot`, or
  `validity`;
- partitions, coverage, and engine capabilities where available;
- a structural fingerprint.

Grain is a physical uniqueness claim, not a concept identity. Coverage is
evidence about which records and time range are available, not a business
population definition.

### DataJoin

`DataJoin` describes an executable relationship between two datasets. It is a
physical composition contract, not a business relationship.

It declares:

- left and right dataset references;
- typed field pairs or restricted expressions;
- declared cardinality;
- one temporal match mode: `equality`, `as_of`, or `validity`;
- temporal direction and tolerance where relevant. Every interval predicate
  uses the kernel's canonical half-open boundaries.

A data join cannot replace a `BusinessRelation`. It cannot be inferred from a
`SemanticEdge`, matching column names, or matching property names.

Combining binding outputs from different datasets requires an explicit
`DataJoin` path or an explicitly composed dataset. Multiple bindings over the
same dataset may share its record relation without an additional join, but
their row discriminators and mapped identities remain independently verified.
Marivo never silently merges sources.

`DataJoin` lives in `marivo.datasource`, so it cannot reference a concept
identity scheme. The semantic planner therefore verifies each use of a data
join contextually:

1. The adjacent bindings must map the same concept identity scheme.
2. Every component of that identity, including composite identities, must map
   to one normalized expression fingerprint on each side.
3. The data join's equality field pairs must match those two ordered mapping
   sets exactly. Temporal predicates must separately match the declared
   `as_of` or `validity` mode.
4. No identity pair may be missing, duplicated, or replaced by an unrelated
   physical key.

A physical surrogate key is legal only when it is declared as an alternate
concept identity and mapped by both bindings, or when the author publishes a
precomposed dataset whose row identity has already been verified. An
unmodeled surrogate join cannot establish business identity.

The same `DataJoin` may be structurally valid for one pair of bindings and
invalid for another. Plan verification records the identity-alignment proof;
a mismatch raises `misaligned_data_join` before data execution.

### Binding

A binding is a versioned, executable grounding claim that says how one
business object is represented by one dataset.

Bindings form a many-to-many relationship:

- one business object may have multiple bindings for different regions,
  histories, latency tiers, or roles;
- one dataset may bind multiple concepts, event types, relations, or state
  models.

All bindings declare one role:

- `answer`: eligible to supply authoritative analysis rows;
- `enrichment`: may add attributes to an already valid answer plan;
- `validation`: may check or reconcile an answer plan.

Enrichment and validation bindings never silently substitute for a missing
answer binding.

Every binding also declares a structured `BindingScope`. `SemanticProject`
owns a closed `GroundingScopeSchema` whose axes have typed values, allowed
values, and an optional explicit default. Typical project-defined axes are
`region`, `legal_entity`, `service_level`, and `environment`; these names are
examples, not hardcoded planner fields. Temporal coverage is an additional
interval constraint rather than a free-form axis.

A binding scope may assign one exact value or a finite declared value set to
an axis; omitting an axis means all values on that axis. Arbitrary predicates,
priorities, and executable conditions are not allowed, so scope intersection
and overlap remain statically decidable.

An empty schema and empty binding scope are valid when a target has one answer
binding for all authorized execution environments. Additional answer bindings
require declared axes that make their effective scopes disjoint,
non-overlapping declared time coverage, or both.

An `AnalysisScope` resolves explicit request values plus project defaults. If
a required axis has neither, planning raises `missing_binding_scope`. The
resolved scope and the default source are visible in readiness and artifact
lineage.

Answer uniqueness uses one target-specific `IdentitySignature`:

- `ConceptBinding`: the selected concept identity scheme;
- `EventBinding`: the event identity plus the ordered participant identity
  schemes;
- `RelationBinding`: the ordered endpoint identity schemes;
- `StateBinding`: the subject identity scheme.

For the same target, identity signature, effective grounding-scope cell, and
point in time, at most one `answer` binding may apply. Required properties and
capabilities are deliberately not part of this uniqueness key. Stage 3 binding
verification raises `ambiguous_binding_scope` for overlapping answer sources
and requires the author to do one of the following:

- make the binding scopes disjoint;
- make their declared time coverage non-overlapping;
- change one binding to `enrichment` or `validation`;
- remove the duplicate answer source.

Project defaults resolve omitted request axes; they never make overlapping
answer declarations legal.

There is no binding priority or declaration-order preference. A user may
choose a business-readable scope value for one analysis, but a recurring
choice becomes durable only by editing the project scope or binding
declaration and rerunning readiness. Ordinary analysis never accepts a raw
binding reference as a source-selection override.

The planner selects the identity signature before it selects a binding. The
canonical signature is the default; an alternate signature must be required
explicitly by the semantic object or task. After that choice, the unique
answer binding is selected without considering whether it maps a wider or
narrower capability set. If it lacks a required property, the planner may add
the unique minimal complete enrichment set; it never switches to a second
answer binding because that binding happens to contain the property.

Several non-overlapping answer bindings may cover adjacent time intervals.
One analysis window may compose them into a single ordered union plan only
when their identity signatures are equal, every semantic slot required by the
current requirement has the same `NormalizedMappingFingerprint` in every
segment, and their declared coverage has neither overlap nor gap. Extra
mappings outside the requirement may differ or be absent. Selection remains
unique at every point in time.

A normalized mapping fingerprint contains the target semantic-slot identity,
normalized output type and requiredness, unit/timezone/calendar normalization,
and restricted-expression operator tree. Dataset-local field leaves are
replaced by their ordered mapping-input positions, so physically different
field names may still prove the same mapping semantics. Dataset identity,
physical field identity, binding identity, and extra mappings are excluded.
If two required slots have different normalized fingerprints, the bindings
cannot form one temporal union; the planner never treats “compatible” as a
weaker, implementation-defined comparison.

If the requested half-open analysis interval is not fully covered by one
answer binding or a legal temporal union, resolution raises `missing_binding`
with phase `binding_resolution`. `evidence.uncovered_intervals` contains the
ordered canonical `[start, end)` gaps, and `candidates` identifies adjacent
bindings and their declared or verified coverage. A coverage gap is not
reported as ambiguity or temporal alignment failure.

Multiple `enrichment` bindings are allowed because their usefulness is
requirement-specific. Readiness must find exactly one minimal complete set of
enrichment bindings for the current missing-property set or raise
`ambiguous_enrichment`; two different complete sets are ambiguous even when
each would return the same projected columns. “Minimal” is set-inclusion
minimal: removing any binding would leave at least one requirement unsatisfied.
This ambiguity is intentionally checked after the answer source is fixed
rather than by the global answer-overlap rule. All matching `validation`
bindings may contribute evidence and never compete to supply answer rows.

Mappings use a restricted, typed Ibis expression subset. Raw executable SQL is
not accepted. The compiler must be able to inspect type, lineage, nullability,
determinism, and backend capability before execution.

#### ConceptBinding

Maps one concept identity scheme and zero or more owned properties to a
dataset. A complete answer binding must map all identity properties required
by the selected scheme.

A concept binding also declares one closed `PropertyGrounding` for its mapped
property rows. It is semantic interpretation of the dataset's existing
versioning metadata, not a second declaration of physical version fields:

| Property grounding | Compatible dataset versioning | Selection semantics |
| --- | --- | --- |
| `timeless` | `static` | One value per identity applies at every instant |
| `current` | any model with deterministic current-row selection | Row valid at the source freshness point; no historical attribution |
| `as_of_snapshot` | `snapshot` | Latest snapshot whose `as_of <= observation_at` |
| `validity` | `validity` | Unique row whose `[valid_from, valid_to)` contains `observation_at` |

All properties mapped by one concept binding share its row-level grounding.
If properties have different histories, the author uses the unique answer
binding plus requirement-specific enrichment bindings, or non-overlapping
answer coverage. An `append_only` dataset can supply `current`
concept properties only when its declared order and row key select exactly one
latest row per concept identity; it cannot masquerade as historical property
evidence.

`current` grounding is eligible only for a non-historical query whose recorded
execution `as_of` equals the source freshness point. It cannot answer event-
time, past `as_of`, cumulative, or historical relation-path requirements. For
historical use, the planner evaluates the relation version and the target
concept-property version at the same canonical `observation_at`. Zero rows are
missing evidence; more than one matching version raises
`ambiguous_temporal_match`. A planner never substitutes the current value for
missing historical evidence.

#### EventBinding

Maps event identity, `occurred_at`, participant identities, and event
properties to a dataset. It may include a row discriminator expressed through
the restricted mapping language.

A complete `answer` event binding must map the event identity,
`occurred_at`, and every participant role declared by the `Event`, using the
ordered identity schemes in its `IdentitySignature`. Participant completeness
is not requirement-specific: an analysis projects unneeded roles only after
answer binding resolution. A missing role or identity component fails Stage 3
with `unmapped_property`, whose evidence names the participant role and
missing identity components. Event-property mappings may remain partial and
be supplied by the unique minimal enrichment set.

#### RelationBinding

Maps the source and target identities of one `BusinessRelation`, plus validity
or event anchors required by its temporal mode. Effective-dated validity must
already be normalized to `[valid_from, valid_to)`. Event anchors lower to
successive half-open versions under the kernel rule above.

#### StateBinding

Maps the subject identity, external state values, and `as_of` or validity
fields for one `StateModel`. External values are explicitly mapped to the
model's closed state set. Its closed evidence shape is `point_snapshot`,
`snapshot_series`, or `validity_history`; only `validity_history` may claim
continuous intervals and those intervals use `[valid_from, valid_to)`.

`PropertyMap`, `IdentityMap`, `ParticipantMap`, and `StateMap` are nested value
objects, not catalog kinds.

### Binding resolution

The planner resolves a typed `AnalysisRequirement`:

```text
AnalysisRequirement(
    target,
    identity_signature,
    required_properties,
    temporal_mode,
    time_contract,
    intent_contract,
    population_scope,
    grounding_scope,
    required_capabilities,
)
```

`time_contract` is the normalized `MetricTime`, event cohort/follow-up pair,
or lifecycle interval. `intent_contract` is a closed typed payload for metric
composition, event matching, or lifecycle evidence; it is absent for a simple
base-metric requirement. Neither field is an untyped option bag.

Resolution follows a fixed sequence:

1. Resolve the typed grounding scope from explicit values and project
   defaults.
2. Resolve one target-specific identity signature, using the canonical
   signature unless the requirement explicitly names an alternate.
3. Select the unique answer binding at every point in the requested grounding
   scope and time interval without capability-based tie-breaking.
4. Check required mappings and static capabilities on that answer source, then
   resolve one unique minimal complete enrichment set for any missing
   properties.
5. Apply runtime coverage, freshness, readiness, and access gates for the
   requested population, time, and grounding scope.
6. Construct one complete semantic plan, including any compatible temporal
   union, every required `DataJoin`, and each identity-alignment proof.
7. Lower that plan to canonical intermediate relations.

If no plan is complete, resolution fails with evidence. If multiple plans are
complete, resolution raises `AmbiguousBinding` and exposes their meaningful
differences. It does not choose by declaration order, source name, row count,
freshness preference, or cost.

Physical cost optimization may reorder or optimize a plan only after semantic
resolution has selected one plan. It cannot break a semantic tie.

A target that passed scoped readiness should not produce runtime binding
ambiguity for the same scope. `AmbiguousBinding` remains a fail-closed safety
net for changed coverage, access state, or stale readiness. Its repair points
back to the durable project scope and binding declarations; it does not ask an
ordinary agent to remember a raw binding choice.

### Executable business-relation paths

Cross-concept analytical use is expressed by an immutable `RelationPath`, an
ordered, directed list of `BusinessRelation` references. The path is attached
to a metric's governed axis or cross-concept predicate as authoring metadata;
it is not discovered during query execution.

Path selection follows these rules:

1. A projection owned by the metric subject needs no path.
2. A projection owned by another concept requires an explicit path from the
   metric subject to that owner, even if the catalog currently contains only
   one candidate.
3. Every step declares traversal direction, and the next endpoint must equal
   the following step's start. Paths must be acyclic.
4. Two relations between the same concept pair are distinguished by their
   typed relation references. The planner never chooses between
   `belongs_to` and `fulfilled_by` from labels or topology.
5. There is no semantic hop limit for an explicitly authored acyclic path.
   Deployment query budgets may reject an expensive plan, but they do not
   replace it with a shorter path. Automatic discovery remains one hop by
   default and may only suggest candidate paths for authoring.
6. Each step must resolve to one ready `RelationBinding`, with every
   cross-dataset boundary backed by an identity-aligned `DataJoin`.
7. `effective_dated` and `event_anchored` steps align at the canonical
   `observation_at` defined below. A temporal relation without a compatible
   observation time fails with `temporal_alignment`.
8. When the target projection is versioned, the target `ConceptBinding`
   resolves its property row at that same `observation_at`. Relation and
   property history cannot be independently rebased to different instants.

Under the default `fanout_policy="block"`, every path step must be to-one in
the traversal direction. Optional to-one relations yield a nullable dimension
value; required to-one relations must satisfy runtime identity completeness.

A path containing a to-many step is legal only under the explicit
`aggregate_then_join` contract defined below. Cross-concept scalar population
predicates follow the same path rules; a to-many predicate is not treated as
an implicit `exists` and must instead be modeled through an event, metric
observation, or other explicit analytical construct.

For example, an order metric may expose merchant tier only by declaring which
business relation supplies it:

```python
ms.axis(
    merchant_tier,
    via=ms.relation_path(order_belongs_to_merchant),
)
```

If both `order_belongs_to_merchant` and `order_fulfilled_by_merchant` exist,
omitting `via` is `missing_relation_path`, not a runtime path search.

#### Temporal attribution anchors

Before traversing an `effective_dated` or `event_anchored` relation, the
planner assigns every contributing row one canonical `observation_at`. The
relation is evaluated at that contribution time, not at query execution time
or at the label of the grouped output bucket.

| Observation variant | Canonical `observation_at` |
| --- | --- |
| Measure aggregation or governed expression over event input | The contributing event's `occurred_at` |
| Measure aggregation or governed expression over concept input | The selected `property_time` value on each contribution row |
| Distinct concept count without event input | The explicit analysis `as_of`; for a grained series, each bucket end |
| Event count | The counted event's `occurred_at` |
| State transition | The triggering event's `occurred_at` |
| State membership or distribution | The requested `as_of`; for a grained series, each bucket end |
| Cumulative composition | Each base contribution's original `observation_at`, before accumulation |
| Ratio, weighted-average, or linear composition | Each component retains its own verified `observation_at` semantics |

Grouping a contribution into a day or month does not replace its temporal
anchor with the bucket boundary. In particular, an all-history cumulative
metric attributes every historical contribution through the relation version
effective when that contribution occurred; it does not reclassify the whole
running total by the current relation value or the display-bucket end.

Composition is legal only when component temporal semantics can be aligned
without borrowing another component's anchor. A missing contribution time,
an incompatible component anchor, or a relation whose temporal mode cannot be
evaluated at `observation_at` raises `temporal_alignment`. Current-value
rebasing is not an implicit option in v1; it requires a separately authored
metric contract.

Every temporal match must produce at most one target version for a to-one
path. Overlapping effective-dated rows, competing event anchors, or duplicate
property versions raise `ambiguous_temporal_match`. A missing optional target
produces null plus coverage evidence; a missing required target blocks runtime
quality. These cardinality rules use the canonical half-open interval algebra,
including at exact boundaries.

## Governed Analytical Semantics

### Analytical projections

Business properties acquire analytical roles through separate governed
objects:

- `Dimension` exposes a property or restricted expression for grouping and
  filtering and declares allowed operations and values where appropriate;
- `TimeDimension` exposes a temporal property with timezone, calendar, and
  allowed grains;
- `Measure` exposes a numeric contribution with unit, aggregation behavior,
  and additivity constraints.

These projections refer to business properties, never physical fields.

All v1 analytical projections are scalar. A collection-valued property may be
rendered as contextual detail, but it cannot be an identity component,
`Dimension`, `TimeDimension`, or `Measure`, and cannot be expanded implicitly
into rows. Membership analysis requires a reified concept, relation, or event
with explicit grain and fanout semantics.

A projection over a concept property inherits the concept as its subject. A
projection over an event property declares the participant role that supplies
its analytical subject whenever the event has more than one possible role.

`Additivity` is a closed value model:

- `additive` permits aggregation across declared non-restricted axes;
- `non_additive` forbids automatic re-aggregation;
- `semi_additive(over, fold)` permits ordinary aggregation off the named
  status-time axis and requires one closed `TimeFold` on that axis.

`TimeFold` supports `mean`, `min`, `max`, `first`, `last`, and `percentile`.
The `over` value is a governed `TimeDimension`, not an arbitrary property or
field. For sampled status data, the resolved status-time axis, fold, sample
interval, and coverage travel through plan and artifact metadata. Sampling
metadata comes from the time projection and binding evidence; it is not
redeclared as an untyped metric flag.

### Metric

`Metric` defines a quantified business claim over exactly one concept subject.

It requires:

- `subject`: a concept reference;
- `population`: an inline typed analytical predicate for a base observation,
  or `component_populations()` for composition;
- `observation`: one closed observation variant;
- `time`: one closed `MetricTime` variant;
- aggregation or metric composition;
- one resolved unit and additivity contract, inherited or declared under the
  authority rules below;
- one resolved closed fanout policy, declared by a base metric or inherited by
  composition;
- optional verification provenance;
- governed dimensions and time dimensions.

The closed observation variants are:

- a measure aggregation using `sum`, `count`, `count_distinct`, `min`, `max`,
  `mean`, `median`, or `percentile`;
- distinct concept count;
- event count with an explicit subject participant role;
- state membership or state transition over a state model;
- a governed Ibis expression over canonical subject, event, relation, or state
  inputs;
- composition of other compatible metrics through `ratio`,
  `weighted_average`, `linear`, or `cumulative`.

The expression observation is the typed escape hatch for calculations that
cannot be represented by the simpler variants. Its enclosing metric declares
subject and `MetricTime`; the observation declares unit, additivity,
contribution grain, and required properties explicitly. It remains
non-rewritable unless a validator proves a specific capability: it cannot
become a cumulative base, and it may use `aggregate_then_join` only with the
validator-proven subject-contribution contract defined below.

A base `population` uses a nested immutable `FilterExpr`.
`component_populations()` is a nested marker that preserves the referenced
populations; neither value is a catalog object or may be promoted into a
general `Rule` or `Policy` in this scope.

Metric details expose at least:

- subject and observation;
- time semantics and allowed grains;
- allowed analytical axes;
- explicit relation paths and fanout policy;
- related events and state models;
- unit and additivity;
- parity fixed-case applicability and requested-scope status;
- requirement-specific readiness.

#### Metric time contract

`Metric.time` never contains a bare `TimeDimension`. It is one closed
`MetricTime` value:

- `event_time(time_dimension)` assigns each contribution the declared event
  time and requires an event-owned temporal projection;
- `property_time(time_dimension)` assigns each concept contribution the
  declared concept property time;
- `query_as_of()` evaluates a population or state at the request's explicit
  `as_of`; a grained series evaluates once at each bucket end;
- `timeless()` declares that the observation has no business-time axis;
- `component_time()` is legal only for ratio, weighted-average, or linear
  composition and preserves each compatible component's own time contract
  while declaring their shared output-bucket contract.

Compatibility is checked by observation variant:

| Observation variant | Legal metric time |
| --- | --- |
| Event-owned measure or expression; event count; state transition | `event_time` |
| Concept-owned measure or expression | `property_time`, or `timeless` when every input is timeless |
| Distinct concept count; state membership | `query_as_of` |
| Cumulative | The base metric's `event_time` or `property_time` plus its declared cumulative time dimension |
| Ratio, weighted average, linear | `component_time`, after component compatibility succeeds |

A governed expression must declare the matching variant explicitly.
`timeless` rejects an analysis time window, grain, cumulative composition, or
temporal relation path. `query_as_of` requires an explicit instant; it never
uses wall-clock execution time as a hidden default. Event and property time
require compatible requested windows and allowed grains. An incompatible
combination fails declaration or planning with `invalid_metric_time`.

#### Unit and additivity authority

Every resolved metric contract has one unit and one additivity result, but the
common metric authoring path does not redeclare semantics already owned by a
`Measure`. A measure observation inherits the measure's unit and additivity;
the measure is authoritative. A governed-expression observation has no such
source and therefore declares its unit, additivity, contribution grain, and
required properties explicitly.

Derived observations use these closed rules:

- `sum`, `min`, `max`, `mean`, `median`, and `percentile` preserve the measure
  unit; `sum` inherits the measure's additivity, including its complete
  semi-additive status-time contract, and rejects a non-additive measure;
  the other aggregations are non-additive unless a more specific closed rule
  says otherwise;
- `count` and `count_distinct` carry a semantic counted-noun unit derived from
  the counted concept or event, such as `{order}` or `{refund_requested}`;
  renderers may omit that suffix for a plain count. `count` is additive and
  `count_distinct` is non-additive;
- `ratio` applies a closed quotient rule: equal units cancel to `1`, while
  different known units produce a structural compound such as
  `CNY/{order}` or `{refund_requested}/{order}`. Ratio observations are
  non-additive;
- `weighted_average` preserves the value component's unit and is
  non-additive; its weight component must be a known dimensionless weight or
  raise `incompatible_component_unit`;
- `linear` requires one common compatible unit and is additive only when every
  component is additive; distinct known units raise
  `incommensurable_linear_units`;
- `cumulative` preserves its base unit and resolves to `non_additive`, with the
  anchor-specific reaggregation restrictions defined below.

A metric-level unit override may add compatible display metadata or a
counted-noun alias. A ratio author may give a display alias only when its
normalized factors equal the inferred quotient. It cannot relabel an
incompatible physical quantity,
silently convert values, or suppress an incommensurable linear composition.
A value-changing override accepted by the pre-cutover model is deliberately
tightened here: the new validator rejects it unless the governed expression
performs the conversion and declares the resulting unit.
A semi-additive observation may select a different allowed fold only over the
same governed status `TimeDimension`; it cannot replace the measure's
restricted axis or claim full additivity. Any conversion of values belongs in
the governed observation expression and produces a correspondingly new unit.

The v1 unit algebra intentionally supports only equality, counted-noun units,
ratio cancellation and quotient construction, weighted-average preservation,
and linear compatibility. It does not simplify arbitrary products or powers.
If a component unit is unknown, a ratio unit is unresolved and declaration
fails with `incompatible_component_unit`; an author cannot make it known with
a display label. A governed
expression must perform and declare any calculation outside this closed
algebra.

#### Metric composition compatibility

Before a `ratio`, `weighted_average`, or `linear` metric is catalog-valid, the
compiler derives an immutable `ComponentSignature` for every component:

```text
subject identity signature
population fingerprint
metric-time contract and allowed grain
ordered output axes with RelationPath fingerprints
grounding-scope schema
fanout semantics
unit and additivity
```

Compatibility is decided in this order:

1. Every component must have the same subject concept and selected identity
   signature. A composed metric never changes subject.
2. A composed metric declares `component_populations()`; component
   populations remain independent and are evaluated exactly as
   authored. A refund-rate numerator and order-count denominator are not
   forced to share one predicate.
3. Every axis exposed by the composed metric must exist on every component
   with the same `Dimension` identity and the same normalized relation-path
   fingerprint and fanout treatment. Matching property names or endpoints are
   insufficient.
4. The requested time window, grain, timezone, and calendar must be legal for
   every component. Their contribution-level `observation_at` values remain
   independent, but their output bucket keys must be identical.
5. One typed `AnalysisScope` is applied to all components. Each may resolve to
   different bindings or datasets, but every plan must cover the same scope
   cell and time interval and must lower to one executable backend relation or
   an explicitly supported federated plan.
6. Unit and additivity are resolved by the closed rules above. No component
   borrows an axis, time contract, source selection, or unit from another.

Each component is computed independently under the shared subject and
`AnalysisScope` to the exact output key `(time bucket, ordered axes)`. The
compiler full-outer aligns those keys. A missing component remains null and is
retained as quality evidence; it is never coerced to zero. A ratio with a zero
denominator returns null plus `zero_denominator` evidence. A weighted average
with zero or null total weight returns null with the corresponding evidence.
A linear result is
null when any required term is missing. These rules prevent an apparently
complete derived metric from hiding asymmetric populations or coverage.

The arithmetic itself is closed: `ratio` divides its aligned numerator by its
denominator; `weighted_average` divides an additive weighted-value-total
component by its aligned additive, dimensionless weight-total component; and
`linear` sums an ordered set of dimensionless literal coefficients times
aligned components. The weighted-average value role is therefore not an
already-averaged value, and a counted-noun metric is not a legal weight unless
a governed expression explicitly produces a dimensionless weight.
Non-additive inputs are rejected. Linear term order is part of the fingerprint
even though addition may be physically reordered after semantic validation.

Multi-metric observation is not composition. Metrics may share one result
frame only when their resolved output signatures—subject identity, scope, time
bucket, ordered axes, timezone, and calendar—are identical. Otherwise planning
raises `incompatible_multi_metric_frame` and the caller issues separate
observations;
Marivo does not drop axes, re-bucket values, or copy one metric's scope to make
a rectangular frame.

#### Governed metric axes and fanout

Each allowed metric dimension is a nested `MetricAxis` containing a dimension
reference and, when the dimension owner differs from the metric subject, an
explicit `RelationPath`. The same rule applies to time dimensions and
cross-concept scalar population operands.

Every base metric declares one closed fanout policy; composed metrics inherit
their compatible component policies:

- `block` is the default and accepts only relation paths that are to-one in
  the traversal direction;
- `aggregate_then_join` permits a path containing to-many steps for additive
  or semi-additive base observations.

`aggregate_then_join` has exact set semantics. The compiler first computes the
metric contribution once at subject grain, separately computes distinct
`(subject_id, axis_value)` membership through the declared relation path, and
then joins and groups those two relations. Duplicate relationship rows cannot
multiply the subject contribution. A subject may intentionally contribute
once to several different axis values, so totals across those groups are not
reaggregatable.

The policy does not authorize arbitrary joins, choose a relation path, or
apply to non-additive or composed metrics. A governed-expression observation
may use it only when the expression exposes a validator-proven subject
contribution IR; otherwise it remains non-rewritable and fails closed.
Composed metrics inherit the verified fanout semantics of their components
and cannot declare an independent override. Plan and artifact lineage retain
the policy, subject pre-aggregation grain, membership relation, and
non-reaggregatable axes.

#### Cumulative composition

`cumulative` is a first-class metric composition over a rewritable base
observation using `sum`, `count`, or exact `count_distinct`. It declares one
governed time dimension and one closed anchor:

- `all_history`: accumulate from the beginning of available history; the
  display window clips rows but does not reset values, and empty buckets carry
  the previous value;
- `grain_to_date(grain)`: reset at week, month, quarter, or year boundaries;
  empty buckets carry the previous value within the current reset period;
- `trailing(count, unit)`: use a fixed-size rolling window; partial leading
  windows remain visible with coverage evidence, and an empty trailing window
  is zero.

Exact cumulative distinct uses a first-seen rewrite rather than summing bucket
distinct counts. Cumulative-over-derived is rejected; compatible ratio,
weighted-average, and linear compositions may reference cumulative
components. Rollup uses period-end `last` semantics. Compare eligibility is
anchor-specific and remains visible in the artifact contract: all-history is
not directly comparable as a window delta, grain-to-date requires aligned
period position, and trailing requires the same anchor and window semantics.

All cumulative variants lower through canonical IR and Ibis. If the backend
cannot execute the required first-seen, window, reset, or interval plan, the
operation fails with `backend_capability`; the new model does not replace it
with an untracked local calculation.

#### SQL provenance and parity

A base metric may attach an optional `SqlParityOracle` containing SQL text,
dialect, one immutable `ParityCase`, and numeric tolerances. V1 parity cases
are fixed scalar checks with no axes. A case declares an exact typed grounding
scope and one exact temporal scope compatible with `MetricTime`: a half-open
interval for event/property time, one instant for `query_as_of`, or no temporal
scope for `timeless`. Its population fingerprint is inherited from the metric
definition. Changing the population, case, metric semantics, or SQL
invalidates the prior result.

The oracle SQL is authored for that exact fixed case and must encode its own
population, grounding, and temporal predicates. Marivo does not parse,
rewrite, or inject parameters into arbitrary SQL. It compiles the semantic
metric for the declared case, executes both scalar jobs, and records the case,
both fingerprints, values, tolerances, and execution evidence. The SQL is
verification provenance only: it never becomes the metric expression and it
does not weaken the rule that executable semantic expressions are Ibis.

An oracle case is `unverified` until checked, `verified` after a passing check,
and `drifted` after a mismatch. For an ordinary analysis whose resolved scope
or time differs from the fixed case, the metric reports
`not_checked_for_scope`; it does not reuse the case's `verified` label.
Readiness decides whether `unverified` or `not_checked_for_scope` is a warning
or blocker for the requested task. `drifted` is always visible and may not be
silently ignored. The author assertion that the SQL encodes the declared case
is visible provenance, not something the planner claims to infer from SQL
text.

Composed metrics do not carry independent SQL oracles. For a requested scope,
their parity status is derived only from component cases that exactly match
that scope and time: `drifted` dominates `unverified`, which dominates
`verified`; a missing matching case produces `not_checked_for_scope`. A base
metric without an oracle is `not_applicable`, not implicitly SQL-verified.

#### Metric capability parity at atomic cutover

The from-scratch model retains the existing metric capabilities below while
moving them onto business subjects and typed grounding.

| Existing capability | Cutover decision | Target contract |
| --- | --- | --- |
| Count and aggregate metrics | Include | Concept/event count and governed measure aggregation with the current closed aggregation set |
| Python/Ibis expression metrics | Include | Governed expression observation with explicit subject, grain, time, unit, and additivity; non-rewritable by default |
| Ratio, weighted average, and linear composition | Include | Closed composition variants with exact component subject, axis-path, output-key, time, scope, null, and unit rules |
| Cumulative metrics | Include | All-history, grain-to-date, and fixed trailing anchors; exact distinct; period-end rollup; anchor-aware compare gates |
| Sampled semi-additive metrics | Include | Explicit status `TimeDimension`, `TimeFold`, sample interval, coverage, and propagated artifact metadata |
| Unit and additivity algebra | Include | Measure-authoritative inheritance, expression declarations, counted-noun and quotient units, closed composition rules, and fail-closed compatibility |
| `fanout_policy="aggregate_then_join"` | Include | Subject-grain pre-aggregation plus distinct subject-axis membership; default remains fail-closed `block` |
| SQL provenance and parity | Include | Base-metric fixed scalar `ParityCase`, explicit scope evidence and verification state, exact-case component propagation, and lineage |
| Multi-metric observation and component lineage | Include | Identical output signatures may share a frame; each component retains its semantic plan, scope, additivity, and quality evidence |

This table is the minimum parity baseline, not an exhaustive deletion list.
The implementation plan must begin by generating an inventory of the
pre-cutover public metric, frame, intent, help, and error capabilities and map
every entry to `include`, `replace`, or an explicitly approved `drop`
decision before scheduling public cutover. An omitted capability blocks atomic
release; omission is never an implicit removal decision.

### Public analytical language

The public language has three task lanes.

#### Metric lane

Existing metric-centered intents remain the ordinary entry:

```text
observe, compare, attribute, discover, correlate,
hypothesis_test, forecast, quality assessment
```

The exact operator set is versioned by the live interface surface. All metric
operators consume governed metric handles or typed metric artifacts.

Intents may accept a typed `AnalysisScope` to override project grounding-scope
defaults using business-readable axis values. They never accept `BindingRef`,
`DatasetRef`, or `DataJoinRef` as an ordinary source-selection argument.

#### Event lane

Task-specific event intents expose:

```text
events.sequence
events.funnel
events.time_to_event
```

They accept event handles, subject concept or participant-role constraints,
time windows, and typed population predicates. They do not accept arbitrary
graph patterns.

All three intents use two temporal bounds. `time` is the half-open cohort
window: only the first step or start event may enter the cohort when its
`occurred_at` is in that window. `completion_through` is the exclusive
follow-up horizon for later steps or end events and defaults to `time.end`.
It may extend beyond the cohort window and must be greater than or equal to
its finite end; an open-ended cohort requires an explicit finite follow-up
horizon. Follow-up lanes scan `[time.start, completion_through)`; matching
rules below further constrain which of those events can advance a journey. An
optional `max_elapsed` further limits each journey, measured
inclusively from its first/start event. Event data coverage must reach the
per-journey minimum of `completion_through` and `start + max_elapsed` when the
maximum is present; otherwise it must reach `completion_through`. Incomplete
journeys without that coverage are labeled
`coverage_censored` rather than silently counted as business drop-off.

Every event lane first verifies unique event identity. Duplicate rows for one
event identity raise `duplicate_event_identity`; event id is never a hidden
deduplication instruction. Population is evaluated at subject grain before
matching. Candidate order within one event type is
`(occurred_at, event_id)`, where event id only enumerates same-type candidates
with equal timestamps.

`events.sequence` accepts an ordered tuple of at least two typed event-role
handles and owns the `next_unconsumed_sequence` policy. It visits first-step
events in cohort order and greedily chooses the earliest still-unconsumed
event for each next step whose `occurred_at` is strictly later than the
previous step and before the follow-up horizon. One event identity can occupy
at most one matched journey. Partial journeys and unused events remain
artifact evidence. Two different required step types at the same subject and
timestamp cannot prove order and raise `ambiguous_event_order`; the compiler
does not use step declaration order as chronology.

`events.funnel` owns the `earliest_subject_journey` policy. Its denominator is
the distinct subjects whose earliest qualifying first-step event lies in the
cohort window. For each subject, later steps greedily use the earliest event
strictly after the prior matched step and before the follow-up horizon. Each
subject contributes once, reaches one deepest step, and therefore produces
monotone step counts. Repeated first steps do not create additional funnel
cohorts; attempt-level behavior belongs to `events.sequence` or
`events.time_to_event`. Equal-timestamp cross-step candidates raise
`ambiguous_event_order` for the same reason as sequence.

`events.time_to_event` owns the `next_unconsumed` policy. It partitions by
subject, visits start events in cohort order, and pairs each with the earliest
still-unconsumed end at or after the start, before `completion_through`, and
within optional `max_elapsed`. An end is consumed at most once. Equal start
and end timestamps form a valid zero-duration pair, with the start treated
before the end for pairing only; this does not assert general chronology
between the event types. Eligible but unused ends and starts with no completion
remain explicit evidence, classified as `not_completed_by_horizon` or
`coverage_censored` according to follow-up coverage.

These contracts prevent repeated attempts from forming Cartesian pairs and
make cohort-boundary behavior executable. Intent lineage retains cohort and
follow-up intervals, matching policy, consumed identities, partial journeys,
unused events, and censoring counts. New matching or repeat variants require a
future closed kernel contract; implementations may not infer first-to-first,
each-to-next, event reuse, or same-timestamp chronology from data shape.

#### Lifecycle lane

Task-specific lifecycle intents expose:

```text
lifecycle.distribution
lifecycle.transitions
lifecycle.dwell
lifecycle.violations
```

They accept a `StateModel` handle, subject scope, and time scope. They use
event replay, state bindings, or both according to verified readiness.

The kernel owns the pairing, replay, temporal-attribution, and lifecycle
semantics in this document. The
[`analysis-interface surface`](2026-07-13-marivo-analysis-interface-surface-design.md)
owns registration, signatures, help, and rendering for the symmetric
`session.events.*` and `session.lifecycle.*` namespaces before this business
model cuts over. Metric intents retain their existing flat
`session.observe(...)`-style entrypoints.

All lanes return typed artifacts with `show()` and `contract()`.

Marivo does not expose `Concept.query()`, a SPARQL-like language, a generic
graph traversal DSL, or arbitrary joins as the business analysis API.

## Canonical Intermediate Representation

The compiler lowers semantic plans into four canonical relation shapes.

### SubjectRelation

```text
subject_id
projected subject properties
valid_from / valid_to when applicable
lineage columns
```

### EventRelation

```text
event_id
occurred_at
one identity column per participant role
projected event properties
lineage columns
```

### BusinessRelationIR

```text
from_id
to_id
valid_from / valid_to or event anchor when applicable
lineage columns
```

### StateRelation

```text
subject_id
state
valid_from
valid_to
source: replay | snapshot | validity_history
seed identity and evidence capability when replayed
lineage columns
```

The IR is typed, backend-independent, and internal. It is not a public object
query language.

## SQL Lowering

Every supported intent follows this pipeline:

```text
Intent
  -> AnalysisRequirement
  -> SemanticPlan
  -> unique BindingPlan
  -> canonical IR
  -> Ibis expression
  -> backend SQL
  -> execution job
  -> typed artifact
```

Subject-aware metrics lower through identity-preserving projections and
governed aggregations. Event sequence and funnel analysis lower through
participant-identity alignment, cohort and follow-up intervals, the kernel's
closed matching policies, windows, and aggregation. Time-to-event lowers
through `next_unconsumed` ordered pairs and duration expressions. Lifecycle
replay lowers through explicit seed selection, pre-window history, business
timestamp ordering, same-timestamp ambiguity detection, transition
validation, and canonical half-open validity intervals. Distribution,
transition, and dwell artifacts aggregate the resulting `StateRelation`;
violation artifacts aggregate the replay trace's triggering `EventRelation`
evidence. Each intent remains limited by the selected evidence path's declared
capability.

An explicit `RelationPath` lowers to an ordered composition of
`BusinessRelationIR` inputs. Before generating Ibis, the compiler verifies the
selected `RelationBinding`, identity-aligned `DataJoin`, traversal cardinality,
and observation-time alignment for every step. A to-one path preserves one
axis value per subject. `aggregate_then_join` instead lowers to a subject-grain
contribution relation plus a distinct subject-axis membership relation.

The compiler preserves subject grain before aggregation and verifies declared
join cardinality and fanout policy at every cross-dataset boundary. Cumulative
composition lowers to first-seen, partitioned window, reset, or rolling-window
relational operations according to its anchor. Temporal alignment is explicit
throughout the plan.

If a backend cannot express a required window, as-of join, validity join, or
ordered aggregation safely, compilation raises `BackendCapabilityError`.
Marivo does not silently approximate the operation in SQL or move it into an
untracked local computation.

Compiled SQL is retained as artifact lineage, but it is not a stable semantic
API and is not used to reconstruct business definitions.

SQL parity oracles execute as a separate validation job for their fixed
`ParityCase`. Their SQL is never parameter-injected, inserted into the
semantic plan, or used as a fallback when semantic compilation fails.

## Catalog and Agent Surface

### Exposure tiers

The catalog exposes three progressive-disclosure tiers.

| Tier | Default audience | Objects |
| --- | --- | --- |
| 1 | Ordinary analysis | `Metric`, `Dimension`, `TimeDimension`, typed artifacts |
| 2 | Task-relevant context | `Concept` summary, `Event`, `StateModel`, `BusinessRelation` |
| 3 | Authoring and diagnostics | `Property`, `Dataset`, bindings, `DataJoin`, raw `SemanticEdge` |

Tier 1 is the default. Event and lifecycle tasks disclose only the Tier 2
objects needed by the requested intent. Tier 3 appears only during semantic
authoring, readiness repair, or explicit diagnostics.

The tiers govern direct navigation, not information hiding. Tier 1
`details()` renders the definitions, owners, relation paths, and constraints
of its property-derived projections inline. An ordinary agent does not need a
separate Tier 3 property lookup to understand a metric population or
dimension.

### One ordinary read path

The ordinary agent path is:

```text
browse a typed collection
  -> get one typed handle
  -> details().show()
  -> invoke a task intent
  -> artifact.show()
  -> artifact.contract()
```

`details()` aggregates the object's current semantic contract, task-relevant
business context, readiness, and valid affordances. It must not require a
second generic navigation call to learn how to use the object.

Typed collections own their kind:

```python
metric = session.catalog.metrics.get("commerce.refund_rate")
event = session.catalog.events.get("commerce.refund_requested")
model = session.catalog.state_models.get("commerce.order_lifecycle")
concept = session.catalog.concepts.get("commerce.order")
```

The caller does not repeat `metric.` or `event.` inside a typed collection.
`get()` returns a loaded typed handle that has `details()` and may be passed
directly to an intent. There is no mandatory `.ref` conversion.

An event handle owns a typed collection of its declared participant roles:

```python
order_role = event.participants.get("order")
```

The returned `ParticipantRole` is a value handle scoped to its parent event,
not a standalone catalog object or a raw string. An event intent may infer the
role only when exactly one eligible `one` participant role targets the
requested subject concept; otherwise the caller supplies the appropriate
typed role handle.

Authoring forward references remain fully kind-qualified:

```python
ms.ref("property.concept.commerce.order.order_no")
```

### Bounded semantic exploration

The planner explores the catalog as follows:

1. Anchor on the requested metric, event, state model, or concept.
2. Select allowed semantic-edge families from the intent policy.
3. Traverse one hop by default, with an explicit maximum depth and candidate
   budget.
4. Order candidates deterministically by family priority and typed identity.
5. Require executable proof through business objects, bindings, and data joins
   before constructing a plan.
6. Apply readiness for the concrete task and scope.
7. Expose only valid task affordances.

Semantic edges expand candidate scope; they never bypass binding or readiness.
Unknown edge families fail closed. Contextual edges have the lowest priority
and can never independently make an object eligible.

## Authoring API Shape

The examples below define target contracts, not compatibility promises for the
current API.

### Business objects

```python
order = ms.concept(
    name="order",
    domain=commerce,
    identities=(
        ms.identity(
            name="canonical",
            properties=(
                ms.ref("property.concept.commerce.order.order_no"),
            ),
            canonical=True,
        ),
    ),
    definition="A commercial order placed by a customer.",
)

order_no = ms.property(
    name="order_no",
    owner=order,
    value_type=ms.string(),
    required=True,
    definition="The stable business order number.",
)

refund_requested = ms.event(
    name="refund_requested",
    domain=commerce,
    identity=ms.identity(
        name="canonical",
        properties=(
            ms.ref(
                "property.event.commerce.refund_requested.event_id"
            ),
        ),
        canonical=True,
    ),
    occurred_at=ms.ref(
        "property.event.commerce.refund_requested.occurred_at"
    ),
    participants=(
        ms.participant("order", concept=order, cardinality="one"),
        ms.participant("merchant", concept=merchant, cardinality="one"),
    ),
    definition="A request to refund an order.",
)
```

Forward references allow declarations to be split across files. Graph
assembly, not Python construction order, determines whether references are
valid.

A replayable state model uses
`replay_seed=ms.initial_event(event=order_created, role="order")` or
`replay_seed=ms.external_seed()`. The first is a business-event reference; the
second is a semantic requirement that readiness must satisfy with state
grounding. Neither form accepts a `StateBinding` reference in the business
object.

### Metric authoring

The target shape below assumes governed projections over `Order` properties
and an authored `order_belongs_to_merchant` relation. It is one complete base
metric declaration, including population, observation, time, axes, fanout,
and verification provenance:

```python
gross_order_value = ms.metric(
    name="gross_order_value",
    domain=commerce,
    subject=order,
    population=order_status.isin(("paid", "fulfilled")),
    observation=ms.measure_observation(
        measure=order_amount,
        aggregation="sum",
    ),
    time=ms.property_time(order_created_at),
    axes=(
        ms.axis(order_status),
        ms.axis(
            merchant_tier,
            via=ms.relation_path(order_belongs_to_merchant),
        ),
    ),
    fanout_policy="block",
    parity=ms.sql_parity_oracle(
        sql="""
            SELECT SUM(amount)
            FROM warehouse.orders
            WHERE status IN ('paid', 'fulfilled')
              AND created_at >= TIMESTAMP '2026-06-01 00:00:00 UTC'
              AND created_at < TIMESTAMP '2026-07-01 00:00:00 UTC'
        """,
        dialect="trino",
        case=ms.scalar_parity_case(
            grounding_scope=ms.analysis_scope(
                region="global",
                service_level="warehouse",
            ),
            time=ms.interval(
                start="2026-06-01T00:00:00Z",
                end="2026-07-01T00:00:00Z",
            ),
        ),
        relative_tolerance=1e-9,
        absolute_tolerance=0.01,
    ),
    definition="Gross value of paid or fulfilled orders.",
)
```

`order_amount` is the authority for this metric's unit and additivity, so the
metric does not repeat them. `order_status.isin(...)` produces an immutable
typed `FilterExpr`; it is not Python row filtering. The merchant axis is
available only through the declared relation path, and the SQL text is a
fixed-case parity oracle rather than the executable metric definition. Marivo
compiles the semantic side for that case but does not inject the displayed
scope or time into the SQL; the SQL author has encoded the same population and
half-open time interval explicitly.

A governed-expression observation makes the semantics that a measure would
otherwise supply explicit:

```python
net_order_value = ms.metric(
    name="net_order_value",
    domain=commerce,
    subject=order,
    population=order_status.isin(("paid", "fulfilled")),
    observation=ms.expression_observation(
        required_properties=(gross_amount, discount_amount),
        expression=ms.row_expr(
            lambda row: row[gross_amount] - row[discount_amount]
        ),
        aggregation="sum",
        contribution_grain=order,
        unit=ms.unit("CNY"),
        additivity=ms.additive(),
    ),
    time=ms.property_time(order_created_at),
    axes=(ms.axis(order_status),),
    fanout_policy="block",
    definition="Paid order value after governed discounts.",
)
```

The expression receives only compiler-provided canonical row inputs keyed by
declared property handles. It cannot access datasets, fields, sessions, or
arbitrary Python state, and every referenced property must appear in
`required_properties`.

A composed metric keeps component populations and time anchors explicit. If
`paid_order_count` counts `Order`, the quotient below resolves to the
structural unit `CNY/{order}` directly from its operands; the author does not
supply an arbitrary result-unit override:

```python
average_paid_order_value = ms.metric(
    name="average_paid_order_value",
    domain=commerce,
    subject=order,
    population=ms.component_populations(),
    observation=ms.ratio(
        numerator=gross_order_value,
        denominator=paid_order_count,
    ),
    time=ms.component_time(),
    axes=(
        ms.axis(
            merchant_tier,
            via=ms.relation_path(order_belongs_to_merchant),
        ),
    ),
    definition="Gross paid order value per paid order.",
)
```

Both components must expose the displayed axis with the same relation-path
fingerprint. The composed metric has no independent fanout override or parity
oracle.

### Dataset and binding

```python
order_history = md.dataset(
    name="order_history",
    datasource=warehouse,
    source=md.table("order_history"),
    row_key=("order_id", "valid_from"),
    versioning=md.validity(
        valid_from="valid_from",
        valid_to="valid_to",
    ),
)

order_rows = ms.concept_binding(
    name="orders_from_history",
    target=order,
    dataset=order_history,
    role="answer",
    scope=ms.binding_scope(
        region="global",
        service_level="warehouse",
    ),
    identity=ms.identity_map(
        "canonical",
        mappings=(ms.map(order_no, order_history["order_id"]),),
    ),
    properties=(
        ms.map(order_status_property, order_history["status"]),
    ),
    property_grounding=ms.validity_properties(),
)

order_events = md.dataset(
    name="order_events",
    datasource=warehouse,
    source=md.table("order_events"),
    row_key=("event_id",),
    versioning=md.append_only(order_by="event_ts"),
)

refund_requested_rows = ms.event_binding(
    name="refund_requested_from_order_events",
    target=refund_requested,
    dataset=order_events,
    role="answer",
    scope=ms.binding_scope(
        region="global",
        service_level="warehouse",
    ),
    where=order_events["event_type"] == "refund_requested",
    properties=(
        ms.map(event_id, order_events["event_id"]),
        ms.map(occurred_at, order_events["event_ts"]),
    ),
    participants=(
        ms.participant_map(
            "order",
            identity="canonical",
            mappings=(ms.map(order_no, order_events["order_id"]),),
        ),
        ms.participant_map(
            "merchant",
            identity="canonical",
            mappings=(
                ms.map(merchant_id, order_events["merchant_id"]),
            ),
        ),
    ),
)
```

`validity_properties()` consumes the dataset's declared versioning fields; it
does not redeclare their names. The binding and dataset validators jointly
prove one normalized half-open property row per order identity and instant.

`region` and `service_level` in this example must be typed axes declared by
the project's `GroundingScopeSchema`. A second realtime binding is legal only
under a disjoint `service_level` value or a different non-answer role. The
project may make one service level the explicit default for requests that omit
it.

The binding is a semantic authoring object. An ordinary analysis agent should
not need to inspect or pass it when the target event is ready.

### Analysis

```python
metric = session.catalog.metrics.get("commerce.refund_rate")
metric.details().show()

frame = session.observe(metric, time=window, grain="day")
frame.show()
frame.contract()
```

Event and lifecycle tasks use the same handle-first pattern:

```python
requested = session.catalog.events.get("commerce.refund_requested")
completed = session.catalog.events.get("commerce.refund_completed")
order = session.catalog.concepts.get("commerce.order")
requested_order = requested.participants.get("order")
completed_order = completed.participants.get("order")

latency = session.events.time_to_event(
    requested,
    completed,
    subject=order,
    start_role=requested_order,
    end_role=completed_order,
    time=window,
    completion_through=followup_end,
)

lifecycle = session.catalog.state_models.get(
    "commerce.order_lifecycle"
)
dwell = session.lifecycle.dwell(lifecycle, time=window)
```

## Validation and Readiness

Validation is staged so authors can distinguish a definition problem from a
data or runtime problem.

### Stage 1: declaration validation

Validate local identifiers, required fields, value types, closed variants,
canonical half-open intervals, scalar identity and analytical projection
invariants, participant cardinality, `MetricTime`, unit algebra, and local
invariants. No query is allowed.

### Stage 2: graph assembly

Resolve typed references and validate ownership, identity schemes, participant
roles, transitions, relation endpoints, metric relation paths, and
semantic-edge families. Validate the state model's single initial state and
replay-seed contract plus every composed metric's declared component
signatures. Relation paths must be endpoint-contiguous, directed, and acyclic.
No query is allowed.

### Stage 3: binding verification

Validate field types, mapping completeness, expression restrictions, binding
roles, binding-scope schema membership, answer-scope overlap, join
cardinality, concept-property grounding compatibility, canonical interval
normalization, temporal compatibility, and static backend capability. For every
`(target, IdentitySignature, effective grounding-scope cell, point in declared
time coverage)`, readiness must identify at most one eligible answer binding,
independent of mapped capability, before runtime data checks. Adjacent answer
bindings that may form a temporal union are also checked for equal normalized
mapping fingerprints on every requirement-required semantic slot. Every
answer `EventBinding` must map all declared participant roles even when the
current requirement uses only one. Multiple enrichment bindings are allowed
here by design; requirement-specific readiness must later resolve exactly one
minimal complete enrichment set or raise `ambiguous_enrichment`. No data query
is required.

### Stage 4: plan verification

Resolve a scoped analysis requirement, require one complete semantic plan,
verify every relation-path step, prove that each `DataJoin` field-pair set
matches the adjacent binding identity maps, verify relation and concept-
property alignment at one `observation_at`, metric component output
compatibility, temporal alignment, grain, fanout safety, event matching
policy, and canonical IR shape. A surrogate-key join with no shared mapped
identity fails here. This may read metadata but not business rows.

### Stage 5: scoped preview

Execute an explicitly bounded preview with required time, row, and timeout
guards. Preview validates mappings and runtime relational behavior; it does
not establish production readiness for every scope.

### Stage 6: runtime quality

Evaluate coverage, freshness, identity completeness, duplicate risk, event
identity uniqueness, event ordering, sequence/funnel cross-step ambiguity,
follow-up censoring, temporal-version cardinality, state seed and history
coverage, same-timestamp lifecycle replay ambiguity, illegal-transition
evidence, replay consistency, snapshot reconciliation, component key
alignment, and artifact shape for the requested scope. When a metric declares
a parity oracle, this stage evaluates or reads only the exact fixed
`ParityCase` status according to readiness policy; other analysis scopes are
`not_checked_for_scope`.

Readiness is requirement-specific. An object may be catalog-valid and ready
for one metric while not ready for a sequence, relation, or historical scope.

Changing a referenced definition invalidates dependent readiness and semantic
plan caches. Historical artifacts retain the fingerprints used when they were
created.

## Structured Errors

All semantic planning and grounding errors use one envelope:

```text
code
phase
target
requirement
expected
received
candidates
evidence
repair
```

Core error codes are:

- `invalid_definition`;
- `invalid_identity_shape`;
- `invalid_temporal_interval`;
- `invalid_metric_time`;
- `unresolved_ref`;
- `missing_binding`;
- `ambiguous_binding`;
- `missing_binding_scope`;
- `ambiguous_binding_scope`;
- `ambiguous_enrichment`;
- `unmapped_property`;
- `missing_relation_path`;
- `invalid_relation_path`;
- `missing_data_join`;
- `misaligned_data_join`;
- `unsafe_data_join`;
- `unsafe_fanout`;
- `temporal_alignment`;
- `ambiguous_temporal_match`;
- `duplicate_event_identity`;
- `ambiguous_event_order`;
- `insufficient_state_history`;
- `incompatible_component_subject`;
- `incompatible_component_axis`;
- `incompatible_component_time`;
- `incompatible_component_scope`;
- `incompatible_component_unit`;
- `incompatible_multi_metric_frame`;
- `incommensurable_linear_units`;
- `backend_capability`;
- `parity_drift`;
- `ambiguous_replay_order`;
- `state_replay_mismatch`.

`zero_denominator`, zero or null total weight, partial event journeys,
follow-up censoring, and illegal state transitions are result-level quality
evidence rather than planner errors when the surrounding plan is valid. Their
counts and affected output keys remain visible in the artifact contract.

`missing_binding` includes an ordered `evidence.uncovered_intervals` field when
an otherwise valid answer-binding sequence cannot cover the requested
half-open interval. `unmapped_property` names a missing participant role and
its identity components when an answer `EventBinding` is incomplete.

Candidate lists come from the current catalog and include the differences that
prevent deterministic selection. `repair` supplies one immediate executable
next step when the issue is mechanical. If resolution requires a business
choice—such as selecting an authoritative regional source—the error returns
that choice to the user rather than guessing. The durable repair edits a
relation path, binding scope, binding role, project scope default, or identity
mapping as appropriate; a transient raw binding selection is not offered as a
recurring fix.

No error path may silently fall back to another binding, weaken a temporal
condition, ignore a participant role, or replace an answer binding with an
enrichment or validation binding.

## Artifact Lineage

Every typed artifact retains a lineage manifest containing:

- project fingerprint;
- referenced business and analytical object identities and fingerprints;
- traversed semantic edges and the planner reason for using them;
- explicit business-relation paths, direction, cardinality, and temporal
  alignment, including the observation-at rule used by every temporal step;
- normalized timezone and canonical half-open interval evidence;
- resolved grounding-scope values and whether each came from the request or a
  project default;
- binding, dataset, and data-join identities, identity-alignment proofs,
  concept-property grounding and selected versions, and scope coverage;
- normalized intent requirement;
- canonical IR shape and compiler version;
- backend, dialect, compiled SQL, execution job, and time scope;
- metric additivity, status-time fold, fanout policy, contribution grain, and
  cumulative anchor where applicable;
- metric-time and component signatures, output-key alignment, component
  populations, and null or zero-denominator evidence where applicable;
- parity-oracle and fixed-case fingerprints, author scope assertion, last
  exact-case result, and propagated parity status;
- quality evidence and blockers;
- event cohort and follow-up intervals, sequence/funnel/pairing policy,
  consumed identities, partial journeys, unmatched counts, and censoring when
  applicable;
- lifecycle seed source, history coverage, evidence-path capabilities,
  event-replay ordering, violation evidence, ambiguous concurrency classes,
  and state-snapshot reconciliation when applicable.

Lineage distinguishes semantic selection from physical optimization. A user
must be able to see which business plan was chosen even if the backend changes
join order or materialization strategy.

## Module Ownership

### `marivo.datasource as md`

Owns:

- datasource connections;
- datasets and typed fields;
- physical versioning and coverage;
- data joins;
- physical evidence and backend capabilities.

### `marivo.semantic as ms`

Owns:

- domains and the business semantic graph;
- bindings;
- dimensions, time dimensions, and measures;
- metrics;
- catalog assembly;
- validation, preview, and readiness.

### `marivo.analysis as mv`

Owns:

- metric, event, and lifecycle intents;
- semantic planning and compilation entrypoints;
- analysis sessions and execution;
- typed artifacts and their contracts.

No module duplicates another module's declaration model.

## Recommended Authoring Layout

```text
models/
├── datasources/
│   └── warehouse.py
├── datasets/
│   ├── commerce.py
│   └── joins.py
└── semantic/
    └── commerce/
        ├── _domain.py
        ├── concepts.py
        ├── events.py
        ├── state_models.py
        ├── relations.py
        ├── bindings.py
        └── analysis.py
```

Filesystem paths organize authoring code only. Catalog identity comes from
explicit declarations, not import path or filename.

The authoring partial order is:

1. datasource, dataset, and data joins;
2. domain, concepts, and properties;
3. events, state models, business relations, and explicit semantic edges;
4. one closed binding at a time, followed by verify, preview, and scoped
   readiness;
5. governed analytical projections;
6. metrics;
7. analysis only from ready anchors.

Forward references allow files within a stage to be authored independently,
but graph assembly and readiness enforce the dependency order.

## Testing Strategy

### Contract and graph tests

Test typed references, declaration invariants, ownership, identity schemes,
participant roles, guarded-event modeling, transitions, edge families,
projection subjects, explicit relation paths, cumulative anchors, additivity,
metric-time variants, component signatures, unit quotients, fixed parity
cases, and fingerprint invalidation. Reject collection or nullable identity
components, collection analytical projections, optional analytical subject
roles, invalid half-open intervals, and illegal observation/time pairs.

### Grounding and planner tests

Test mapping types, binding roles, scope defaults and overlap, coverage gates,
explicit data-join requirements, composite-identity alignment, surrogate-key
rejection, relation cardinality and temporal safety, fanout rewrites, unique
resolution, property-grounding/versioning compatibility, state replay seeds,
and complete ambiguity evidence. Include a wide and a narrow
answer binding whose scopes overlap and whose capability sets both satisfy a
smaller requirement; Stage 3 must reject them without capability-based
selection. Also cover adjacent compatible answer intervals, temporal gaps,
one unique minimal complete enrichment set, and competing minimal sets.
Cover exact interval boundaries, adjacent validity rows, overlapping relation
or property versions, equal-time competing event anchors, current-property
rejection for historical attribution, and relation/property selection at one
shared `observation_at`.

For event bindings, prove that an answer missing any declared participant role
fails with `unmapped_property` even when the concrete task needs only a mapped
role. For temporal unions, prove that physically different field names pass
when every requirement-required `NormalizedMappingFingerprint` is equal,
different required transformation semantics fail, extra mappings may differ,
and a requested interval crossing a coverage gap raises `missing_binding`
with the exact ordered `[start, end)` gaps.

### End-to-end behavioral fixtures

Use DuckDB fixtures as result truth for four primary scenarios.

#### Subject-aware refund rate

Define `Merchant`, `Order`, and `RefundRequested`; bind the event's `merchant`
role; define a refund-rate metric over the merchant subject; observe it by
governed merchant dimensions. Verify that the ordinary analysis path never
requires binding operations and that fanout does not change the result.

#### Cross-concept metric axis

Define an order-value metric whose subject is `Order` and a merchant-tier
dimension owned by `Merchant`. Define both `order_belongs_to_merchant` and
`order_fulfilled_by_merchant`, bind each relation, and prove that the metric's
explicit `RelationPath` selects the intended one. Verify to-one lowering,
effective-date alignment at each observation variant's canonical
`observation_at`, missing-path failure, identity-misaligned data-join failure,
historical target-property selection at that same instant, missing-path
failure, identity-misaligned data-join failure, and the ordinary agent's
path-free invocation after readiness. A cumulative fixture proves that
historical contributions retain their original relation and property versions
instead of being rebased to the display bucket or current value. Boundary
rows prove `[valid_from, valid_to)` selection, adjacency, and overlap failure.

Add a to-many order-item axis and verify both default `unsafe_fanout` and
`aggregate_then_join`: each order contribution occurs once per distinct axis
value, duplicate relationship rows do not multiply it, and cross-group totals
are marked non-reaggregatable.

#### Refund sequence and latency

Define `PaymentCaptured`, `RefundRequested`, and `RefundCompleted`; analyze
sequence, funnel, and time-to-event by order. Fixtures include duplicates,
missing steps, out-of-order records, equal timestamps, multiple refund
attempts, cohort and follow-up boundaries, insufficient follow-up coverage,
and completion outside the cohort window. Verify
`next_unconsumed_sequence`, `earliest_subject_journey`, and
`next_unconsumed`; monotone funnel counts; one-to-one consumption;
zero-duration equality only for time-to-event; `ambiguous_event_order` for
equal-time sequence/funnel steps; duplicate-identity failure; partial journey,
unused event, and censoring evidence; Ibis and SQL lowering; and typed artifact
lineage.

#### Order lifecycle

Define an `OrderLifecycle`, replay state from events, compute distribution,
transitions, dwell, and illegal transitions, then reconcile against a
`StateBinding` snapshot. Verify that material mismatches become quality
evidence or blockers according to the requested contract. Two distinct
transition-triggering events for the same subject and timestamp must block
readiness with `ambiguous_replay_order`; exact duplicate event identities must
surface as binding quality failure rather than a replay tie-break.

Cover both seed variants, pre-window replay, a missing seed, a gap before the
requested range, an illegal transition that preserves current state and lets
replay continue, and an outgoing event from a terminal state. Prove the
capability matrix: complete replay answers all four lifecycle intents,
validity history cannot answer violations, and point/snapshot evidence cannot
answer transitions or dwell. Reconciliation is limited to overlapping
coverage.

Behavioral assertions compare result relations and artifact contracts. Tests
must not pin complete backend SQL strings. Targeted assertions may verify
required window, join, temporal, or capability structure.

### Metric capability parity tests

Before cutover, generate a machine-readable manifest of the existing public
metric constructors, aggregation and composition variants, additivity modes,
fanout policies, provenance/parity states, metric-frame metadata, metric-lane
intents, help targets, and structured errors. The target implementation maps
every manifest entry to a replacement contract and pins that mapping in a
regression test.

Behavioral fixtures cover at least:

- count, every closed measure aggregation, and governed expression metrics;
- ratio, weighted-average, linear, and multi-metric observation;
- cumulative all-history, grain-to-date, and trailing anchors, including exact
  distinct, carry-forward/zero semantics, rollup, and compare gates;
- sampled semi-additive status-time folds and sample coverage;
- unit and additivity inheritance, counted-noun and compound-ratio units,
  expression declarations, compatible display aliases, and incommensurable
  linear-unit failure;
- component subject, path-axis, time, scope, output-key, missing-value,
  zero-denominator, zero-weight, and multi-metric-frame compatibility;
- `block` and `aggregate_then_join` fanout behavior;
- fixed-case SQL parity pass, unverified, drift, tolerance,
  `not_checked_for_scope`, author scope evidence, invalidation, and exact-case
  derived propagation.

### Cross-backend capability tests

Maintain a capability matrix for equality joins, as-of joins, validity joins,
window ordering, half-open interval arithmetic, event matching, and ordered
aggregation. Unsupported operations must fail before execution with
`backend_capability`.

### Cold-agent usability gate

A general-purpose coding agent starting from the live catalog must complete
the four primary scenarios through Tier 1 and task-relevant Tier 2 surfaces.
The ordinary path must contain zero direct binding, dataset, data-join, or raw
semantic-edge operations.

For the event scenario, the agent must discover the subject through
`session.catalog.concepts`, obtain ambiguous participant roles through each
event handle's `participants` collection, and invoke
`session.events.time_to_event` without reusing authoring variables or passing
raw role strings.

The gate measures:

- successful object discovery;
- number of disclosure hops before one valid intent invocation;
- semantic-plan correctness;
- whether the agent guesses a role or binding;
- whether it can recover from structured readiness errors;
- whether it preserves artifact lineage in its answer.

## Acceptance Criteria

The design is complete when all of the following are true:

1. Catalog objects are definitions only; runtime business instances never
   enter the catalog model.
2. Concepts own identities, properties have exactly one owner, events use
   named participant roles, and state models have verifiable event-triggered
   transitions. Every answer `EventBinding` maps all declared participant
   roles under its ordered identity signature.
3. `BusinessRelation`, `SemanticEdge`, and `DataJoin` are structurally and
   operationally distinct.
4. Datasets have no implicit business meaning; only typed bindings ground
   business objects.
5. Every cross-concept metric projection declares an explicit, directed,
   acyclic `RelationPath`; to-one, temporal, competing-relation, and to-many
   fanout behavior is deterministic. Relation and target concept-property
   versions resolve at one canonical `observation_at` under the shared
   half-open interval algebra.
6. Every cross-dataset semantic join proves that `DataJoin` field expressions
   match the adjacent binding identity mappings; unmodeled surrogate keys fail
   before execution.
7. Binding scopes are typed and persistent; for every target identity
   signature, effective scope cell, and time point, readiness rejects
   overlapping answer authority independent of capability. Requirement-level
   enrichment and temporal union with equal requirement mapping fingerprints
   either resolve uniquely or fail with a structured error. Uncovered
   intervals raise `missing_binding` with exact half-open gap evidence.
8. Governed projections and metrics refer to business semantics rather than
   columns, and the authoring API has one complete Metric example covering
   population, observation, closed metric time, cross-concept axes, fanout,
   and fixed-case parity. Identities, subject roles, and analytical
   projections satisfy the scalar invariants.
9. Subject-aware metric, event sequence, and lifecycle analysis lower through
   canonical IR to Ibis and SQL with explicit capability checks, fixed
   sequence/funnel/time-to-event policies, cohort and follow-up bounds,
   observation-variant temporal anchors, explicit lifecycle seeds and evidence
   capabilities, and fail-closed lifecycle replay ties.
10. The metric cutover preserves cumulative, sampled semi-additive,
    `aggregate_then_join`, SQL parity, composition, expression, and
    multi-metric capabilities through an explicit machine-checked manifest.
    Composition has one output-key compatibility algorithm, null/zero rules,
    and counted-noun/compound-unit algebra; SQL parity applies only to its
    declared fixed case.
11. The ordinary agent path is metric-centered and progressively discloses only
   the business context required by the task; event tasks can obtain Concept
   and typed participant-role handles without authoring context.
12. Semantic edges bound exploration but cannot create executable truth.
13. Artifact lineage reconstructs the semantic selection, grounding,
    temporal versions, component alignment, event matching, lifecycle seed,
    compilation, execution scope, and quality evidence.
14. DuckDB behavioral fixtures pass for the four primary metric,
    relation-path, event, and lifecycle scenarios; unsupported backend
    capabilities fail closed.
15. No compatibility, migration, implicit fallback, or generic object-query
    surface is introduced.

## Implementation Boundary

This document defines one architectural cutover target. An implementation plan
may sequence work internally by catalog kernel, grounding, compiler IR, and
task lanes, but the public release acceptance is atomic: Marivo must not expose
the new business graph without deterministic grounding and the four behavioral
scenarios across the three analytical lanes defined here.

Implementation planning begins only after this written specification is
reviewed and approved.
