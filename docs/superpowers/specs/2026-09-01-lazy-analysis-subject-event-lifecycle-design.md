# Lazy Analysis Subject, Event, and Lifecycle Design

Date: 2026-09-01

Revised: 2026-09-05

Status: accepted

Owner decision status: confirmed

## Outcome

Define the first-cutover identity-preserving bridge between Metric, Event, and
Lifecycle analysis in the lazy Dataset algebra.

This document is the sole authority for:

- domain selection into Module 2's sole Population membership family;
- logical subject-selection authority and materialized identity-row authority;
- exact subject identity, scope, sampling, completeness, privacy, and recovery
  contracts;
- `session.events.match(...)` as the sole Event source;
- Event journey, funnel, and time-to-event Dataset families;
- `session.lifecycle.replay(...)` as the sole Lifecycle source;
- Lifecycle history, distribution, transition, dwell, and violation Dataset
  families;
- Event/Lifecycle source consumption of the shared population-input contract,
  plus typed subject selection from Event journey and Lifecycle history
  Datasets;
- Event- and Lifecycle-family generated fields and `where(...)` admission;
- legal cross-domain loops through explicit `population=PopulationInput`;
- family-specific authority selection, censoring, Evidence inputs, and
  identity-safe cold recovery.

The goal is not to preserve eager `EventFrame`, `LifecycleFrame`, detached
selection, or Session reducer APIs. The goal is one lazy, typed path in which
an agent can determine before execution:

1. which Entity supplies subject identity;
2. which Dataset rows are selected and which subject membership they imply;
3. which Event or Lifecycle time authority answers the question;
4. when coverage is sufficient to publish a reusable cohort;
5. which identities may be stored or explicitly read without leaking into
   metadata, logs, cards, Evidence, or errors;
6. which exact Dataset continuation is legal after every source, reducer, and
   selection boundary.

## Ownership Boundary

### This module owns

- nominal registrations plus row and row-set contracts for `EventDataset` and
  `LifecycleDataset`;
- domain selection producers of Module 2's Population family, including their
  selection-time and completeness authority;
- Event/Lifecycle source application of the shared `PopulationInput`
  compatibility and input-mode contract;
- identity-preserving selection and completeness requirements;
- Event Pattern admission at the Event source boundary;
- Event matching policy, anchor window, follow-up, and coverage semantics;
- StateModel replay admission, replay window, seed, trigger ordering,
  censoring, and violation semantics;
- family-specific reducers and their complete output schemas;
- exact filter registrations for owned shapes;
- Event/Lifecycle specializations of shared operator ids when the input meaning
  is owned here;
- privacy-safe family metadata and family-specific runtime requirements.

### This module does not own

- the common Dataset class, common state values, actions, field selectors, or
  scan-leaf protocol;
- generic Population inference for Metrics, Population predicate semantics, or
  Metric coordinates and aggregation;
- the shared `AnalysisPredicate` grammar, literal compatibility, filter effects,
  filter ordering, or shared filter field-resolution rules;
- private semantic nodes, semi-join lowering, Ibis construction, execution
  fixed-domain binding or Runtime resource policy;
- Run, Artifact, storage-receipt, Evidence-envelope, writer-lock, commit, cleanup, or
  recovery state machines;
- ordinary Metric operator admission or Candidate scoring;
- public removals, Help rollout, documentation cutover, or implementation
  sequencing.

The owning upstream sources are:

- [Dataset Core](2026-09-01-lazy-analysis-dataset-core-design.md);
- [Observation Model](2026-09-01-lazy-analysis-observation-model-design.md);
- [Direct Compiler and Fixed Execution Boundaries](2026-09-01-lazy-analysis-planner-and-pushdown-design.md);
- [Materialization Runtime](2026-09-01-lazy-analysis-materialization-runtime-design.md);
- [Typed Operators](2026-09-01-lazy-analysis-typed-operators-design.md).

The downstream public-cutover plan will own removal of the eager paths and
alignment of Help, skills, tests, and current English/Chinese documentation.

## Upstream Invariants

This module consumes the following contracts without redefining them:

1. Every public analysis value is one nominal `Dataset` family with one exact
   family-qualified shape, row contract, row-set contract, Session owner,
   definition fingerprint, lineage summary, and logical or materialized input
   authority token.
2. A Dataset operator performs deterministic local construction only and always
   returns a Dataset. It never returns a DataFrame, detached selection, plan,
   task, future, Ibis expression, SQL value, or execution handle.
3. Each analytical family has paired Logical and Materialized Dataset classes.
   Every downstream operator accepts either state and returns a new Logical
   Dataset; a materialized input enters that lazy DAG only as an immutable scan
   leaf and never exposes or replays an origin plan.
4. Every selection producer returns Module 2's Population family with the same
   complete tuple-valued `entity_identity` coordinate. No separate membership
   family or Python refinement is introduced.
5. `Dataset.where(...)` is the sole row-filter spelling. The Observation Model
   owns `membership` and `row_subset`, predicate binding, field resolution,
   authored position, and the prohibition on unproved reordering.
6. Raw Entity identities may appear only in Dataset rows and explicit terminal
   reads. They are forbidden in bounded metadata, errors, lineage, Run
   arguments, Evidence subjects, Findings, cards, and `repr`.
7. Each operator binds exact Logical or Materialized inputs and any explicitly
   requested semantic enrichment. Concrete input checks belong to that operator;
   runtime never substitutes another input or replays an Artifact origin.
8. The compiler may lower an admitted logical Population input as direct
   membership or an identity projection followed by a semi-join, and a
   materialized input as an immutable identity scan leaf. This document defines
   meaning and requirements, not the physical join.
9. Materialization is the only public reusable execution boundary. Any admitted
   materialized Population input may provide cold-recoverable identity rows;
   inspection and collection publish no Artifact or reusable authority.
10. No old eager persistence generation, alias, migration, dual read, or
    eager/lazy compatibility path survives the cutover.

## Decision Summary

The first-cutover choices include the 2026-09-05 observation-model amendment:

1. `PopulationDataset` is the sole membership family, owned by Module 2.
   Domain-owned `select_subjects(...)` producers return this family; no separate
   SubjectSet class, shape, or alias survives.
2. A logical PopulationDataset owns one exact identity-producing Dataset definition. A
   materialized PopulationDataset owns the exact immutable selected identity rows.
   Materialized Entity Metric and entity-outlier Candidate Datasets may also be
   reused as Population inputs through their retained identity coordinate; they
   do not become selection Populations.
3. Every published PopulationDataset has complete membership truth. Unknown or
   coverage-censored selection fails the action atomically instead of
   publishing a non-consumable cohort. Empty-but-complete membership is valid.
4. Entity Metric and entity-outlier Candidate inputs are passed directly through
   `population=`. Event journey and Lifecycle history inputs use
   `.select_subjects(selection)` because those operations add typed
   Event/Lifecycle selection semantics and censoring requirements.
5. Selected Populations use Module 2's `where(...)` membership specialization,
   including governed reachable atemporal Dimensions. Identity literals remain
   excluded. Filtering never bypasses the source selection's completeness gate.
6. Event journey and Lifecycle history are structural row sets and reject
   `where(...)`. Compact or independently meaningful owned shapes register an
   exact row-subset filter matrix below.
7. `events.match` requires an explicit matching policy. It never chooses a
   business interpretation from cost, cardinality, or omitted input.
8. Population membership scope and Event/Lifecycle analysis windows are
   independent. `events.match` retains `cohort_window`; `lifecycle.replay`
   retains `window`; `population=` only constrains eligible subjects.
9. Pattern steps and model states are selected only through exact
   `PatternStep` and `ModelStateHandle` values, never bare strings or positions.
10. `from_inception()` distinguishes subjects with no modeled trigger, subjects
    with modeled transitions but provably missing inception history, and
    subjects whose prior coverage is insufficient. It never assumes an initial
    state at the replay-window boundary.
11. Event and Lifecycle source/reducer execution remains datasource-first. A
    high-cardinality identity or journey intermediate may not cross to local
    memory unless an explicit bounded terminal or durable boundary admits it.
12. Identity values may be stored only as governed Dataset rows. Even a digest
    derived solely from realized identity rows is private integrity state, not
    public metadata or Evidence.
13. `every_start` shares eligible non-final occurrences across journey attempts.
    Only the final PatternStep is controlled by `completion_assignment`; the
    exact assignment algorithm below is part of the operator contract version.
14. `from_inception()` requires source-origin coverage for every modeled trigger
    Event before absence of inception can be established. A bounded coverage
    interval can establish later follow-up but never historical non-occurrence.

## Subject Identity Contract

### One governed Entity identity

Every PopulationDataset, Event Dataset, and Lifecycle Dataset binds one exact subject
contract:

```text
SubjectIdentityContractV1
  subject_entity_ref
  identity_signature[]
  identity_logical_types[]
  population_authority
  target_population_lineage
```

`subject_entity_ref` is one exact current semantic Entity at logical source
construction. `identity_signature` is the Entity's non-empty ordered primary-key
signature. Every component is non-null and retains its governed logical type.

Public subject identity uses one fixed-arity tuple field:

```text
entity_identity
```

A one-column primary key still produces a one-element tuple. Components are not
exploded into independently joinable public columns. An Event or Lifecycle
source may retain occurrence identity in other tuple-valued row fields, but no
occurrence identity becomes subject membership authority.

The contract rejects:

- an Entity without a complete primary key;
- different component order, logical type, or nullability;
- partial composite keys;
- identity inferred from a Dimension label;
- identity inferred from observed uniqueness;
- cross-Session identity equality based only on matching refs or names;
- a nearest, first, or cheapest Relationship path.

### Identity compatibility is exact

An explicit `PopulationDataset` is admitted to an Event or
Lifecycle source only when it has:

- a Logical definition in the consuming Session or an explicitly selected
  Materialized Artifact in the same Store with its original owner preserved;
- the exact source subject Entity;
- the exact ordered primary-key signature;
- compatible membership scope and sampling authority;
- complete logical membership requirements or immutable materialized rows.

Unlike Metric observation, Event and Lifecycle sources do not reinterpret a
Population of another Entity through a Relationship. Pattern participant and
StateModel trigger subject identity is normative. The repair is to select or
construct subjects at that exact Entity, not to add an implicit cross-Entity
projection.

Explicit cross-Session Materialized membership uses the same structural Entity,
primary-key, scope, and sampling checks as a local input. There is no Artifact
age, source-version equivalence, or reuse-approval check. Source-origin coverage,
follow-up windows, and completeness declarations below remain concrete Event/
Lifecycle algorithm inputs; they do not form a generic datasource freshness
certificate or guarantee suitability for a different question.

### Population and source-time authority remain separate

Subject membership and source-time meaning answer different questions:

```text
Population reference scope
  = which Entity members are eligible under Population semantics

Event cohort window
  = which first-step occurrences may anchor journeys

Event completion-through
  = how far later-step follow-up must be classified

Lifecycle replay window
  = which reconstructed state intervals are emitted

Lifecycle inception lookback
  = which earlier modeled triggers may establish state
```

An explicit Population may be unscoped or may carry a membership scope. An
Event or Lifecycle source always binds its own source window in addition. No
window silently overwrites, intersects, widens, or relabels another authority.
Compatibility validation may reject a contradictory combination, but it does
not choose one as precedent.

## Domain Selection Produces Population

### One common membership contract

Every `select_subjects(...)` returns `LogicalPopulationDataset` with Module 2's
`population/entity-membership@v1` contract: one complete non-null tuple identity
per row, exact Entity/signature, unique identity key, unordered `keyed(unknown)`
rows, and `DatasetFamilyRowSemantics.complete_from_schema`. Module 2 owns this
schema, filter/sample behavior, and its paired materialized type. This module
registers domain selection producers rather than another family or shape.

Selection has stronger production requirements than simple identity projection:
Event/Lifecycle truth and completeness must be evaluated under the owned source
contract. The resulting Population definition retains:

```text
  source_family
  source_shape
  selection_operator_id
  selection_contract_version
  selection_definition_fingerprint
  source_population_authority
  target_population_lineage
  inherited_sampling_authority
  source_temporal_authority
  membership_requirement = complete
```

These are producer-specific definition/lineage facts, not additional public rows
or a parallel membership protocol. Raw identities and membership-derived public
digests remain prohibited.

Population inputs use direct membership. Registered Entity-present Metric and
Entity-outlier Candidate inputs use Module 2's identity projection when Entity
uniqueness is proven, including functionally dependent coordinates. Sources own
their independent Metric/Event/Lifecycle windows. Selection time is never copied
as a consuming source's observation window.

The Population has no `observe`, `events`, or `lifecycle` namespace. It is reused
only through the canonical Session source's explicit `population=` argument.

### Logical authority

A logical PopulationDataset owns one exact identity-producing definition from an
admitted source Dataset:

```text
eligible source rows or owned source selection
  -> exact subject identity projection
  -> action-time uniqueness and membership-completeness requirements
```

It does not own realized rows, row count, content hash, Artifact ref, snapshot
receipt, Evidence, or Findings. Same-plan consumers may use its private root as
an admitted membership input. They do not collect identities or convert them to
Python values.

Calling an Event, Lifecycle, or Metric source with a logical PopulationDataset returns
a new logical Dataset. The consuming definition binds the PopulationDataset's exact
input authority and lineage. It does not persist the PopulationDataset, memoize it
across actions, or claim stable membership between independent actions.

### Materialized authority

Executing a Logical Population returns its paired Materialized Population with
the same family id, row contract, and row-set contract and one immutable
Artifact-backed state.
Its stored public rows contain only
`entity_identity` tuples. The committed family authority additionally proves:

- complete selection truth for the owning selection contract;
- exact row count;
- uniqueness and non-nullness of the complete tuple;
- exact subject Entity and identity signature;
- inherited Population and sampling authority;
- source temporal and completeness authority;
- storage integrity and Evidence publication through Module 4.

The Artifact is the only cold-recoverable PopulationDataset. Recovery reconstructs the
same family, row contract, row-set contract, identity signature, scope, sampling
lineage, and
selection contract, and therefore yields the same mechanically derived
continuations without restoring a logical origin graph or requiring current
catalog membership.

A downstream current Metric, Event, or Lifecycle source must still validate its
own current semantic inputs. A recovered PopulationDataset authorizes only immutable
membership rows; it does not authorize current Metrics, Events, Relationships,
or StateModels by association.

### Complete, empty, and uncertain membership

The confirmed first-cutover contract has one publishable membership status:

```text
complete
```

An empty result under complete source coverage is a valid PopulationDataset with zero
rows. It may be materialized, recovered, and passed as `population=`. Consumers
produce their exact empty or dense-zero result according to their own family
contract.

If any candidate subject's selected/not-selected truth depends on unknown or
coverage-censored follow-up, selection cannot publish a PopulationDataset. Inspection,
collection, or materialization fails with a structured coverage repair; no
partial known-positive cohort is returned. The source Event or Lifecycle
Dataset remains available for censored-row diagnosis.

This decision deliberately removes the eager surface's
`SubjectSet[coverage_censored]` state. A Dataset that looks like governed
membership but cannot enter another source would make `PopulationDataset` admission
stateful and error-prone.

### Filtering a selected Population

Selected Populations register the same Module 2-owned `where(...)` as explicit
Entity Populations. A ready selection can be refined by a unique single-valued,
non-versioned, atemporal Dimension without exposing identity literals:

```python
eu_dropouts = dropouts.where(mv.eq(region, "EU"))
```

A materialized selection remains an immutable identity leaf. The explicit filter
may join the exact current governed Dimension path, but cannot rematch Events,
replay Lifecycle history, or reconstruct original membership. Such enrichment
adds its own current semantic authority while retaining original selection time
and proof. An upstream unknown selection still fails before any filtered output
can publish. Sampling restrictions and predicate order follow Module 2 unchanged.

Set algebra, uploaded identities, identity-component predicates, temporal
Dimension evaluation, and implicit collection-membership rules remain outside
this filter contract.

### PopulationDataset action behavior

- `repr` and `contract()` never execute and never render identity values.
- Materialized `show()` may render a bounded identity preview only because the caller
  explicitly requested a terminal read; the preview is never copied into Run
  arguments, cards, errors, Evidence, or lineage.
- Materialized `to_pandas()` is a guarded complete terminal identity export;
  its returned pandas value cannot re-enter typed analysis.
- Logical `execute()` is the durable transition to a Materialized PopulationDataset.
- Reconstructing the same Logical PopulationDataset in the same named Session and
  calling `execute()` recovers its write-once bound Artifact without refreshing
  identities or creating a new Run.
- Both PopulationDataset states admit registered downstream operators. Those operators
  always return a Logical Dataset and therefore preserve lazy DAG construction.

## Subject Selection Matrix

### One output contract, one typed operation

The first cutover registers these exact bridges:

| Input family and shape | Public operation | Selection input | Identity authority |
| --- | --- | --- | --- |
| `EventDataset[event/journey@v1]` | `.select_subjects(selection)` | `DroppedBefore` | complete journey assignment |
| `LifecycleDataset[lifecycle/history@v1]` | `.select_subjects(selection)` | `InState` | complete replayed state at one instant |

Every operation returns `PopulationDataset[population/entity-membership@v1]`.

Event and Lifecycle selection is not equivalent to projecting visible rows: it
evaluates a closed source-owned selection against complete journey or interval
authority. Metric and Candidate inputs need no selection operator; their exact
current rows are consumed directly when passed through `population=`.

The shared public method contract is:

```python
def select_subjects(
    self,
    selection: DroppedBefore | InState,
) -> LogicalPopulationDataset:
    ...
```

Registry dispatch narrows `select_subjects` to `DroppedBefore` for Event journey
and `InState` for Lifecycle history. The runtime union above is architectural
notation; each focused Help leaf and static method overload exposes only its
own admitted selector.

There is no generic:

```text
session.select_subjects(dataset, selection=...)
dataset.select_subjects(predicate=None, state=None, step=None)
PopulationDataset.from_rows(...)
```

and no detached `Selection`, `Cohort`, or list-of-identities return value.

Funnel, distribution, transition, and dwell shapes have no identity rows and
cannot select subjects. Time-to-event and violation rows retain identity, but
one subject may own several attempts or violations. The first cutover does not
invent implicit `any`, `all`, `first`, or deduplication policy for those shapes;
they remain terminal row analyses until a separately reviewed typed selection
contract exists.

### Event `DroppedBefore`

The public selection constructor is conceptually:

```python
def dropped_before(*, step: PatternStep) -> DroppedBefore:
    ...
```

`step` must be one exact non-initial step retained exactly once by the journey's
Pattern. It cannot be a key string, ordinal, Event ref, or structurally similar
step from another Pattern.

For a `first_per_subject` journey, it selects the subject when:

1. the subject reached the immediately preceding Pattern step;
2. the selected target step was not reached;
3. all Event inputs required to classify that missing step are complete through
   the journey's follow-up bound.

It therefore selects the exact resolved-loss population used by the same
target step's funnel row. Coverage-censored loss truth makes the complete
PopulationDataset action fail. `every_start` journey inputs reject `DroppedBefore`
because several attempts can map to one subject and the first cutover defines
no any-attempt/all-attempt subject policy.

### Lifecycle `InState`

The public selection constructor is conceptually:

```python
def in_state(
    state: ModelStateHandle,
    *,
    at: datetime,
) -> InState:
    ...
```

`state` must belong to the exact StateModel retained by the source history.
`at` is a timezone-aware instant from `window.start` through `window.end`.
Inside the half-open replay window it selects the interval containing the
instant. At the exact exclusive `window.end` boundary it selects the left-limit
state established immediately before the boundary and requires coverage through
that boundary. A bare state name, cross-model handle, naive datetime, instant
outside that closed selection range, or inferred `latest` instant fails
locally.

One subject is selected when its replayed interval establishes the exact model
state at `at`. A gap, missing inception, or insufficient Event coverage cannot
be interpreted as another state. Any membership uncertainty makes PopulationDataset
publication fail atomically under the confirmed complete-only contract.

## Event Source Contract

### Canonical signature

The sole Event source is:

```python
def match(
    self,
    pattern: EventPattern,
    *,
    cohort_window: TimeScope,
    completion_through: datetime,
    matching: EventMatchingPolicy,
    population: PopulationInput | None = None,
    completeness: tuple[CompletenessDeclaration, ...] = (),
) -> LogicalEventDataset:
    ...
```

There is no Dataset-owned `events` source method, eager Frame overload,
`cohort=`, string Event id, untyped step list, arbitrary predicate, or
output-shape parameter.

`pattern`, both temporal arguments, and `matching` are analytical definition
identity. `completeness` contains explicit assumptions and also participates in
definition and later Evidence authority. The optional `population` narrows
eligible subjects but does not replace `cohort_window`.

Construction performs no datasource work. It resolves exact semantic identity,
validates local compatibility, constructs the complete journey row and row-set
contracts,
binds concrete input checks and semantic dependencies, and returns a logical
Event Dataset.

### Pattern and subject inference

The first-cutover `EventPattern` is an ordered non-empty sequence of exact
`PatternStep` values. Every step binds one Event participant role. Local
admission requires:

- every role resolves through the current Session catalog;
- every role is cardinality-one at one exact subject Entity;
- all steps resolve the same subject Entity and identity signature;
- step keys are unique stable snake-case analysis identities;
- exact Event, role, Pattern order, and semantic fingerprints are available;
- semantic readiness and occurrence-time authority are complete.

When `population` is omitted, the source constructs an implicit exact unsampled
Population root for the resolved subject Entity. It does not infer membership
from observed first-step rows. The Event anchor filter later selects journeys
within `cohort_window`.

When `population` is present, it must satisfy the exact `PopulationInput`
compatibility contract above. A materialized admitted input is consumed through
an immutable identity scan leaf. A logical input contributes an identity
projection or direct membership relation to the same lazy graph. Neither path
exposes identities to Python, changes the input Dataset family, or creates a
`PopulationDataset`.

### Event temporal authority

`cohort_window` is a timezone-aware non-empty half-open interval `[start, end)`.
It selects eligible first-step anchor occurrences. It does not filter later
steps to the same window.

`completion_through` is a timezone-aware instant at or after
`cohort_window.end`. Later steps may consume eligible occurrences at or after
their preceding step and before `completion_through`. Coverage must establish
every required Event input through that bound before a missing later step is
classified as resolved non-completion.

The following are distinct and retained:

- requested cohort anchor window;
- requested completion-through bound;
- each Event input's observed watermark authority, if any;
- each exact caller declaration used in place of missing watermark authority;
- aggregate coverage basis: `observed`, `declared`, `mixed`, or `unknown`;
- matching-policy identity and occurrence-order requirements.

Successful query execution, maximum observed occurrence, ingestion SLA, fixture
range, wall-clock time, or a later materialization time never becomes a
completeness watermark.

### Matching policy

The closed first-cutover policies are:

```text
first_per_subject
every_start(completion_assignment = exclusive | shared)
```

Their meaning is:

| Policy | Journey unit | Completion assignment | Admitted continuations |
| --- | --- | --- | --- |
| `first_per_subject` | earliest eligible first-step anchor for one subject | later steps consume earliest unused eligible occurrences | funnel, time-to-event, dropped-before selection |
| `every_start(exclusive)` | one journey per eligible first-step occurrence | one completion closes at most the earliest open eligible journey | time-to-event |
| `every_start(shared)` | one journey per eligible first-step occurrence | one completion may close several eligible journeys | time-to-event |

The matching algorithm is normative:

1. partition occurrences by subject and order first-step anchors by normalized
   occurrence order;
2. `first_per_subject` opens only the first eligible anchor, while
   `every_start` opens one attempt for every eligible anchor;
3. within each attempt, walk PatternSteps in declaration order and choose the
   earliest eligible occurrence after the previously assigned step; one
   occurrence cannot fill two steps in that attempt;
4. a missing step makes that step and every later step missing for the attempt;
   matching never skips a missing intermediate step to fill a later one;
5. different `every_start` attempts may share the same eligible non-final
   occurrence, including an occurrence used by a repeated Event ref in another
   attempt;
6. for `shared`, the earliest eligible final occurrence is independently chosen
   for every attempt and may close several attempts;
7. for `exclusive`, process final occurrences in occurrence order and assign
   each one to the earliest open eligible attempt; an assigned final occurrence
   is unavailable as the final step to every later attempt for that subject.

Final-step reservation is role-specific. When a repeated Event appears at a
non-final PatternStep, the same occurrence may still be shared in that non-final
role by another attempt; it cannot fill two roles inside one attempt.

Repeated Event refs are legal when distinct PatternSteps ask for ordered,
distinct occurrences inside one journey. There is no backend-selected greedy,
maximum-cardinality, or cost-based alternative to the algorithm above.

Occurrences order by normalized `occurred_at` and then one compatible governed
Event identity order. If changing the order of simultaneous cross-Event
occurrences changes assignment, the action fails with
`ambiguous_event_order`. Pattern order, Event name, physical row order, and
backend incidental order are not tie-breakers.

No matching policy is inferred from downstream use. Funnel admission checks
that the source selected `first_per_subject`; it does not silently rematch.

### Completeness declarations

`completeness` is an ordered duplicate-free tuple of the closed declaration
union:

```text
CompletenessDeclaration =
  BoundedCompletenessDeclarationV1
    inputs[]
    complete_from
    complete_through
    rationale
  | SourceOriginCompletenessDeclarationV1
    inputs[]
    source_origin_ref
    complete_through
    rationale
```

Every declaration names a non-empty exact set of consumed Event refs, a
timezone-aware `complete_through` instant, and a non-empty rationale.
`BoundedCompletenessDeclarationV1.complete_from` is timezone-aware and defines
the inclusive lower coverage bound. `source_origin_ref` is a typed datasource
provider authority identifying the beginning of governed occurrence history.
The Event definition binds that provider ref through its occurrence source, but
the semantic layer does not invent or certify it. It is not a caller-authored
timestamp or free-text label.

For Event matching, every required Event must cover the closed evaluation range
from `cohort_window.start` through `completion_through`. A bounded declaration
is sufficient only when `complete_from <= cohort_window.start`. Lifecycle
`from_inception()` requires the source-origin variant for every modeled trigger
Event. Overlapping declarations for one Event, insufficient bounds, stale or
unrelated Events, and datasource-wide free-text claims fail locally.

### Public completeness construction

The two immutable declaration classes are their only public construction path:

```python
BoundedCompletenessDeclarationV1(
    *,
    inputs: tuple[ms.Ref, ...],
    complete_from: datetime,
    complete_through: datetime,
    rationale: str,
)

SourceOriginCompletenessDeclarationV1(
    *,
    inputs: tuple[ms.Ref, ...],
    source_origin_ref: ms.Ref,
    complete_through: datetime,
    rationale: str,
)
```

Every `inputs` item must be an exact `event` Ref from the current Session
catalog. `source_origin_ref` must be an exact `datasource` Ref and must equal
the provider authority bound by every named Event occurrence source. The tuple
must be non-empty and duplicate-free in authored order. Both time values must
be timezone-aware; bounded coverage requires
`complete_from <= complete_through`. `rationale` is stripped and must remain
non-empty.

Construction performs only local catalog and value validation. A wrong-kind,
foreign, stale, duplicate, empty, naive-time, reversed-bound, unrelated-origin,
or empty-rationale input raises one structured `AnalysisError` that identifies
the parameter, expected contract, received value, and exact repair. It never
queries a datasource or turns a declaration into observed authority.

`CompletenessDeclaration` remains a module-internal closed union annotation
used by implementation signatures. Public reflected signatures render the two
concrete variants directly. The alias is not exported, callable, or assigned a
Help leaf. There is no `bounded_completeness`,
`source_origin_completeness`, watermark alias, mapping shorthand, string Ref,
or unversioned constructor.

A declaration is an explicit assumption, not observed Event/source-origin evidence. It may
classify missing follow-up only for its exact inputs and bound. It does not
change semantic readiness, source rows, future actions, or datasource state.

Observed coverage uses the same interval vocabulary:

```text
EventCoverageReceiptV1
  event_ref
  coverage_start = bounded_from(instant) | source_origin(source_origin_ref)
  complete_through
  authority
  observed_at
  source_revision?
```

An observed maximum Event time, successful query, or empty result is not a
coverage start. Receipt validation must prove that the exact Event definition,
occurrence authority, and source-origin identity still match the current source.

## Event Dataset Families

### Family matrix

| Qualified shape | Constructed by | One-row meaning | Cardinality |
| --- | --- | --- | --- |
| `event/journey@v1` | `session.events.match` | one exact Pattern step position in one assigned journey | zero-or-more dense journey-step rows |
| `event/funnel@v1` | `journeys.funnel` | one Pattern step summary at one optional subject-axis tuple | dense over admitted step and axis groups |
| `event/time-to-event@v1` | `journeys.time_to_event` | one journey's duration classification between two exact steps | zero-or-more journey rows |

All shapes retain the exact Pattern, subject identity signature, matching
policy, cohort window, follow-up bound, Population authority, completeness
basis, and source definition lineage required by their meaning. Compact
reducers do not retain raw identities unless their public row contract requires
them.

### Owned row-contract and row-set-contract closure

The schema blocks in this document are ordered public schemas, not name-only
sketches. They combine with the following rules to form the complete
construction-time `DatasetRowContract` and `DatasetRowSetContract` for every
owned shape.

The Population identity field uses Module 2's canonical field id and exact
Entity/signature binding. `SubjectIdentityContractV1` references that same
binding for Event/Lifecycle consumption; it does not define a second field id. Every other Module 6-native field has the stable id
`generated.<producer_id>.<public_name>@v1`, where `producer_id` is exactly one of
`events.match`, `events.funnel`, `events.time_to_event`, `lifecycle.replay`,
`lifecycle.distribution`, `lifecycle.transitions`, `lifecycle.dwell`, or
`lifecycle.violations`. Event funnel comparison and attribution reuse Module
5's `generated.compare.*@v1` and `generated.attribute.*@v1` ids for shared
fields; `contribution_kind` uses
`generated.event_funnel.attribute.contribution_kind@v1`. Retained governed axes
keep their source field id, semantic ref, path identity, logical type, and
nullability, qualified by authored axis ordinal when the same governed field is
reachable through more than one admitted path. Public names are never accepted
as field identity.

The exact generated-field logical types are:

| Field class | Logical type |
| --- | --- |
| `journey_id` | non-null opaque digest string |
| `entity_identity`; Event identity fields | the owning non-null fixed-arity governed identity tuple when present |
| Event ref fields | exact typed Event ref |
| `step_key` | exact retained PatternStep identity projected as its stable key |
| state fields | exact retained ModelState identity projected as its stable key |
| `occurred_at`, `from_time`, `to_time`, `valid_from`, `valid_to`, `as_of` | timezone-aware instant normalized to UTC |
| elapsed and duration fields | backend-independent duration logical type |
| `*_count`, `contribution_rank` | exact `int64`, with non-negative counts and positive ranks |
| rates, shares, mean, median, and percentile fields | finite `float64` when defined |
| comparison and contribution numeric fields | Module 5's registered lossless common numeric type |
| masks | fixed-length `tuple[bool, ...]` matching authored axis count |
| status, presence, violation, method, contribution-kind, and causal-claim fields | owning non-null closed enum |

The closed semantic-role mapping is:

| Role | Exact fields |
| --- | --- |
| `subject_identity` | every `entity_identity` |
| `journey_identity` | `journey_id` |
| `pattern_step_identity` | `step_key` |
| `event_occurrence_identity` | every `*_event_identity` and `event_identity` |
| `event_semantic_identity` | `trigger_event_ref`, `entered_by_event_ref`, `exited_by_event_ref` |
| `model_state_identity` | `model_state`, `from_model_state`, `to_model_state`, `model_state_at_event` |
| `time_coordinate` | `occurred_at`, `from_time`, `to_time`, `valid_from`, `valid_to`, `as_of` |
| `duration_value` | elapsed fields, `duration`, and dwell duration statistics |
| `additive_count` | every `*_count` field |
| `rate_value` | conversion, loss-rate, and share fields |
| `status` | completion, interval, calculation, presence, and violation classifications |
| `comparison_value` | current/baseline count and rate fields plus `loss_rate_delta` |
| `attribution_partition_identity` | authored axes, `active_axis_mask`, `other_mask`, `contribution_kind` |
| `effect_value` | attribution current/baseline values, overall delta, contribution, and pool/total shares |
| `method_identity` | `method`, `causal_claim` |
| `rank` | `contribution_rank` |

Retained funnel/distribution axes keep their governed Dimension-coordinate role
rather than the attribution role. Where a field matches a more specific later
row in this table, that specific role wins; for example Delta
`current_lost_count` is `comparison_value`, not `additive_count`.

Count overflow, a value outside its closed enum, a non-finite defined value, or
a realized physical type outside the registered logical class is an action-time
row-contract contradiction and blocks publication.

Coordinate and row-key ownership is exact:

| Shape | Coordinate fields | Ordered row key |
| --- | --- | --- |
| `population/entity-membership@v1` | `entity_identity` | `entity_identity` |
| `event/journey@v1` | `journey_id`, `entity_identity`, `step_key` | `journey_id`, `step_key` |
| `event/funnel@v1` | declared axes, `step_key` | declared axes, `step_key` |
| `event/time-to-event@v1` | `journey_id`, `entity_identity` | `journey_id` |
| `lifecycle/history@v1` | `entity_identity`, `valid_from` | `entity_identity`, `valid_from` |
| `lifecycle/distribution@v1` | declared axes, `as_of`, `model_state` | declared axes, `as_of`, `model_state` |
| `lifecycle/transitions@v1` | `from_model_state`, `to_model_state` | `from_model_state`, `to_model_state` |
| `lifecycle/dwell@v1` | `model_state` | `model_state` |
| `lifecycle/violations@v1` | `entity_identity`, `trigger_event_ref`, `trigger_event_identity`, `occurred_at` | `trigger_event_ref`, `trigger_event_identity` |
| `delta/funnel@v1` | shared axes, `step_key` | shared axes, `step_key` |
| `attribution/funnel-loss-rate@v1` | authored axes, `active_axis_mask`, `other_mask`, `contribution_kind` | `active_axis_mask`, authored axes, `other_mask`, `contribution_kind` |

Every listed non-coordinate field is a value binding with the specific role
named by its public field: occurrence fact, elapsed fact, classification,
additive count, rate/share, duration statistic, comparison component, or
attribution component. Identity and semantic-ref fields retain their governed
semantic/derivation identity even where they are functionally dependent rather
than part of the minimal row key.

Nullability is closed by shape:

- journey `event_identity`, `occurred_at`, and both elapsed fields are nullable;
- funnel rates are nullable; its counts and `step_key` are not;
- time-to-event occurrence identities, times, and `duration` are nullable; its
  journey/subject coordinates and status are not;
- history exit Event ref/identity fields are nullable; all other history fields
  are not;
- distribution `share`, transition `share_of_modeled_transitions`, and dwell
  duration statistics are nullable; their coordinates and counts are not;
- violation rows contain no nullable fields;
- funnel Delta rate and delta fields are nullable under their closed status;
  its coordinates, counts, presence, and status are not;
- attribution axes are nullable for real null members or inactive hierarchy
  cells, distinguished by the masks; numeric outputs are nullable only under
  the inherited Module 5 status rules, and masks, kind, method, causal claim,
  and status are not nullable.

Governed funnel/distribution axes retain source nullability because null is an
explicit group. No other field becomes nullable from a backend outer join.

Canonical presentation ordering is part of each row-set contract: journey and
time-to-event rows use subject identity, anchor time, anchor Event identity, then
Pattern order where applicable; funnel and funnel Delta use canonical axis tuple
then Pattern order; history uses subject identity then `valid_from`;
distribution uses canonical `at` order, axis tuple, then StateModel order;
transitions and dwell use StateModel declaration order; violations use subject,
occurrence time, Event ref, then Event identity; attribution uses resolution
prefix order, canonical axis tuple, `other_mask`, then `contribution_kind`.
Storage has no incidental ordering authority; actions reconstruct this ordering
from the row-set contract.

### Journey row contract

`event/journey@v1` has this ordered schema:

```text
journey_id
completion_status
entity_identity
step_key
event_identity
occurred_at
elapsed_from_start
elapsed_from_previous
```

One journey is dense over the Pattern's steps in declared order. A missing later
step retains its row with null Event identity, occurrence time, and elapsed
values. `completion_status` is:

```text
complete | incomplete | coverage_censored
```

`incomplete` is legal only when complete follow-up authority proves the step was
not reached. Unknown coverage uses `coverage_censored`; it is never relabeled as
loss.

`journey_id` is a stable opaque journey coordinate derived from operator
version, Pattern, matching policy, subject identity, and anchor Event identity.
The row key is `(journey_id, step_key)` because the dense shape emits one row per
PatternStep. The id may appear in explicit row reads but not in metadata,
errors, Evidence, or cards. PatternStep selection uses the typed retained step
identity bound to the `step_key` field, not the digest or a caller-authored key.

The journey shape rejects `where(...)`. Removing a step row would break density,
completion interpretation, funnel reconciliation, time-to-event selection, and
subject-selection truth. Callers use owned reducers or typed selection instead
of structurally filtering the assignment table.

### `funnel`

The public reducer is:

```python
def funnel(
    self,
    *,
    axes: list[SemanticInput[DimensionKind]] | tuple[SemanticInput[DimensionKind], ...] = (),
) -> LogicalEventDataset:
    ...
```

Only `event/journey@v1` produced by `first_per_subject` is admitted. The reducer
consumes the exact journey assignment. It never rematches Events.

Every optional axis must be a same-Session non-time Dimension on the subject
Entity or reachable through one unique governed to-one path. Its value is
resolved at the subject's first-step occurrence. Ambiguous, to-many, unversioned
historical, incompatible, duplicate, or colliding axes fail before execution.
Null axis values form an explicit group.

The ordered public schema is:

```text
<one typed column per declared axis>
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

The output is dense over Pattern steps for every realized axis tuple. The first
step has `lost_count = 0`; previous-step rates are null. Later steps use:

```text
entry_count          = preceding reached_count
resolved_entry_count = entry_count - coverage_censored_count
lost_count           = resolved_entry_count - reached_count
conversion_previous  = reached_count / resolved_entry_count
loss_rate_previous   = lost_count / resolved_entry_count
conversion_first     = reached_count / resolved_cohort_count
```

Zero denominators yield null rates. Grouped additive counts, including the null
axis group, must reconcile exactly to the ungrouped funnel. Rates are recomputed
from components and never summed or averaged.

With no axes, the reducer consumes the exact source journey rows. Adding axes
explicitly joins the exact current Dimension paths even when the journey
input is materialized; the source journey remains an immutable input leaf and
is never rematched.

### `time_to_event`

The public reducer is:

```python
def time_to_event(
    self,
    *,
    from_step: PatternStep,
    to_step: PatternStep,
) -> LogicalEventDataset:
    ...
```

Both steps must occur exactly once in the retained Pattern and `from_step` must
precede `to_step`. Bare strings, positions, Event refs, and same-key foreign
steps fail locally.

The ordered schema is:

```text
journey_id
entity_identity
from_event_identity
from_time
to_event_identity
to_time
duration
completion_status
```

One row means one admitted journey attempt between the two exact steps.
`duration` is non-negative and present only for complete rows. Incomplete rows
require complete follow-up; coverage-censored rows retain unknown truth.

The reducer consumes exact journey rows supplied by the Logical input or
immutable Artifact. It never rematches Events or replaces the source matching policy.

### Event filtering matrix

Event filtering consumes the shared `AnalysisPredicate` contract and always has
`row_subset` effect. The exact first-cutover registration is:

| Shape | `where(...)` | Filterable fields | Reason |
| --- | --- | --- | --- |
| `event/journey@v1` | no | none | dropping structural step rows corrupts journey authority |
| `event/funnel@v1` | yes | retained axes, `step_key`, all count and rate fields | each row is one complete summary cell |
| `event/time-to-event@v1` | yes | `from_time`, `to_time`, `completion_status`; null checks only on `duration` | each row is one independently meaningful attempt summary |

`journey_id`, `entity_identity`, and Event identity tuples are not filter
operands. Row filtering never changes Pattern meaning, matching assignment, or
subject membership. No Event shape turns filtered rows into PopulationDataset without
an explicit registered bridge; the first cutover admits only the
`DroppedBefore` bridge on the unfiltered structural journey contract.

Materialized filters consume retained fields. Logical filters follow their
admitted upstream graph. No Event filter may reach through a
materialized input to recover an absent field or rematch the Pattern.

Generated Event fields use this exact predicate-kind registration:

| Field class | Admitted predicate kinds |
| --- | --- |
| retained governed axis | the shared logical-type matrix for that exact axis binding |
| `step_key`, `completion_status` | `eq`, `not_eq`, `is_in` |
| integer count | `eq`, `not_eq`, `lt`, `lte`, `gt`, `gte`, `is_in` |
| floating rate | numeric comparisons and `is_in`; `is_null` / `is_not_null` when nullable |
| `from_time`, `to_time` | datetime comparisons and `is_in`; null checks when nullable |
| `duration` | `is_null`, `is_not_null` only |

The shared predicate contract has no governed duration literal class in the
first cutover, so this module cannot admit duration thresholds by treating a
duration as an arbitrary number. Adding `timedelta`-like literals requires an
Observation Model amendment first.

## Lifecycle Source Contract

### Canonical signature

The sole Lifecycle source is:

```python
def replay(
    self,
    model: SemanticInput[StateModelKind],
    *,
    window: TimeScope,
    seed: FromInception,
    population: PopulationInput | None = None,
    completeness: tuple[CompletenessDeclaration, ...] = (),
) -> LogicalLifecycleDataset:
    ...
```

The first cutover admits only the exact `mv.from_inception()` seed. The typed
parameter remains required so callers and contracts state the historical
assumption explicitly. Projection observation, projection-backed seeds,
`lifecycle.observe`, a default-at-window-start state, and arbitrary seed rows
are outside this clean-cut module.

There is no Dataset-owned `lifecycle` source method, eager Frame overload,
`cohort=`, bare model id, state-name seed, output grain, or violation policy
parameter.

### StateModel subject inference

The exact current StateModel supplies:

- one subject Entity and complete identity signature;
- one non-empty closed state vocabulary;
- exactly one declared initial state;
- zero-or-more terminal states;
- exact inception Event participant roles;
- exact transition Event participant roles;
- deterministic transition rules.

Every trigger role must resolve cardinality-one at the same subject Entity. The
source rejects unresolved, cross-Entity, ambiguous, or nondeterministic model
authority before Dataset construction.

When `population` is omitted, replay constructs an implicit exact unsampled
Population root for the model subject Entity. An explicit `PopulationInput`
must match that Entity and identity signature exactly under its registered
direct-membership or identity-projection mode. It narrows eligible subjects but
does not become a seed, change its own Dataset family, or change the replay
window.

### Replay window and lookback

`window` is a timezone-aware non-empty half-open interval `[start, end)`. Replay
evaluates admitted modeled occurrences before `window.end`, reconstructs state,
and emits only intervals overlapping the requested window after clipping.

`from_inception()` never assumes all eligible subjects occupy the initial state
at `window.start`. The compiler/runtime may read earlier occurrences required to
find a deterministic inception and subsequent state, subject to exact source
coverage and action policy. That lookback is source evaluation authority, not a
second public window or an emitted coordinate.

For this seed, “sufficient prior coverage” has one exact meaning: every distinct
modeled trigger Event has a validated `source_origin(source_origin_ref)` coverage
start and `complete_through >= window.end`, and all receipts/declarations agree
on the governed source-origin identity required by the current Event definition.
A finite `bounded_from(instant)` interval is never sufficient, even when it
starts before the earliest observed trigger or before `window.start`.

The confirmed subject classification is:

| Observed history | Coverage authority | Classification |
| --- | --- | --- |
| no modeled trigger at all | every trigger is source-origin complete through `window.end` | `not_incepted`; no interval rows |
| inception exists | complete or explicitly classified later coverage | replay from first inception |
| transition trigger exists but no inception exists | every trigger is source-origin complete through `window.end` | fail `insufficient_state_history` atomically |
| inception truth cannot be established | one or more triggers lack compatible source-origin coverage | `coverage_censored`; no invented initial interval |

Coverage-censored subjects contribute to bounded diagnostics and Evidence inputs
but cannot enter an `InState` PopulationDataset. Pre-inception modeled occurrences do
not silently seed state. The fixed replay contract records or rejects them only
according to the exact row below; callers do not choose an `on_missing_history`
policy.

### Trigger ordering and violations

Replay queries each distinct trigger Event once per action and shares the
resulting occurrence stream across all uses in the StateModel. Occurrences are
ordered by normalized time and compatible governed Event identity.

If simultaneous cross-Event order changes final state or violation outcome,
the action fails with `ambiguous_event_order`. Declaration order, Event name,
physical row order, and backend order are invalid fallbacks.

After inception:

- one legal trigger closes the current interval and enters the declared next
  state;
- one illegal modeled trigger leaves state unchanged and enters the private
  violation trace;
- a trigger from a terminal state is a
  `transition_from_terminal` violation and leaves state unchanged;
- Events absent from the StateModel are not queried and are not violations.

The first-cutover behavior is fixed `record_and_continue`. Because callers have
no alternative, it is bound to the operator contract version and is not a
public policy argument.

### Lifecycle completeness

Every distinct trigger Event needs source-origin coverage through `window.end`.
Typed completeness declarations may name only exact trigger Events consumed by
the selected StateModel. They remain assumptions and may not overlap or claim
datasource-wide completeness. A valid bounded interval may prove later
follow-up for another Event action, but Lifecycle replay classifies the subject
as `coverage_censored` unless every trigger satisfies the source-origin rule.

When source-origin coverage is complete, no modeled trigger for one enumerated
subject proves `not_incepted`; a transition trigger without an inception trigger
proves `insufficient_state_history`. When source-origin coverage is absent or
incompatible, either observation remains `coverage_censored`. Querying all
available rows, observing a minimum timestamp, or receiving an empty result does
not upgrade either classification.

The Lifecycle contract separately retains:

- requested replay window;
- seed identity;
- exact StateModel and trigger fingerprints;
- each trigger Event's occurrence and identity authority;
- observed/declaration coverage per Event;
- aggregate coverage basis;
- counts of not-incepted, seeded, coverage-censored, interval, and violation
  observations after execution.

Counts are safe aggregate facts. Raw subject or Event identity values never
enter family metadata.

## Lifecycle Dataset Families

### Family matrix

| Qualified shape | Constructed by | One-row meaning | Cardinality |
| --- | --- | --- | --- |
| `lifecycle/history@v1` | `session.lifecycle.replay` | one clipped non-overlapping state interval for one subject | zero-or-more interval rows |
| `lifecycle/distribution@v1` | `history.distribution` | one state count at one requested instant and optional axis tuple | dense over instants, states, and realized groups |
| `lifecycle/transitions@v1` | `history.transitions` | one distinct declared modeled state pair | dense over distinct declared transition pairs |
| `lifecycle/dwell@v1` | `history.dwell` | one declared state duration summary | dense over declared states |
| `lifecycle/violations@v1` | `history.violations` | one persisted illegal modeled-trigger observation | zero-or-more violation rows |

Every reducer consumes the exact replay result. No reducer re-queries trigger
Events or replays the StateModel.

### History row contract

`lifecycle/history@v1` has this ordered schema:

```text
entity_identity
model_state
valid_from
valid_to
entered_by_event_ref
entered_by_event_identity
exited_by_event_ref
exited_by_event_identity
interval_status
```

Rows for one subject are ordered, non-overlapping intervals clipped to the
replay window and always satisfy `valid_from < valid_to`. A deterministic
same-time trigger sequence may change transient internal state, but zero-duration
intermediate intervals are not emitted; only the state effective after that
instant can begin a public interval. The row key is the complete subject
identity plus `valid_from`.
`interval_status` is:

```text
completed | right_censored | coverage_censored
```

A completed interval ends at a legal modeled transition. The final open
interval is right-censored at `window.end` only when coverage is complete
through that instant. Otherwise it is coverage-censored. No interval assigns a
state before deterministic inception.

History rejects `where(...)`. Removing one interval would corrupt adjacency,
state-at-time truth, transition counts, dwell censoring, and violation linkage.
Owned reducers and `InState` selection are the only structural consumers.

### `distribution`

The public reducer is:

```python
def distribution(
    self,
    *,
    at: tuple[datetime, ...],
    axes: list[SemanticInput[DimensionKind]] | tuple[SemanticInput[DimensionKind], ...] = (),
) -> LogicalLifecycleDataset:
    ...
```

`at` is non-empty, duplicate-free, timezone-aware, and canonically ordered.
Each instant must be between `window.start` and `window.end`, inclusive for this
point-selection operation. The exact `window.end` boundary uses the same
left-limit and complete-through rule as `InState`. History never invents a
bucket schedule or default latest instant.

Axes follow the same exact to-one subject enrichment rules as Event funnel, but
the temporal anchor is each requested `at` instant. Null values form explicit
groups. Grouped subject counts must reconcile to ungrouped counts for every
instant and state.

The ordered schema is:

```text
<one typed column per declared axis>
as_of
model_state
subject_count
known_subject_count
coverage_censored_subject_count
share
```

`known_subject_count` is the denominator for `share` within the exact axis tuple
and instant. `coverage_censored_subject_count` is kept separate. Subjects not
yet incepted are outside the StateModel distribution and are not assigned to
the initial state. A zero known denominator yields null share.

With no axes, distribution consumes the exact input history rows. Adding axes
explicitly joins current Dimension paths while retaining the source history as
its exact logical or immutable input.

### `transitions`

The public reducer is:

```python
def transitions(self) -> LogicalLifecycleDataset:
    ...
```

The ordered schema is:

```text
from_model_state
to_model_state
transition_count
share_of_modeled_transitions
```

It emits every distinct declared StateModel transition pair, including zero
counts, ordered by the pair's first declaration. Several trigger Events may
therefore contribute to one pair row. Illegal triggers are not transitions;
they remain in the violation trace. The share denominator is the count of all
legal modeled transitions. A zero denominator yields null.

The reducer consumes the exact logical source or retained materialized history
and trace. It adds no current semantic expansion.

### `dwell`

The public reducer is:

```python
def dwell(self) -> LogicalLifecycleDataset:
    ...
```

The ordered schema is:

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

It emits every declared state in StateModel order. Duration statistics use only
completed intervals. Right- and coverage-censored intervals remain explicit
counts and are never assigned an artificial duration. Zero completed intervals
produce null duration statistics.

The first-cutover median and p90 are exact. A backend that cannot lower the
exact registered reduction fails compilation; runtime pressure does not switch
to approximation.

### `violations`

The public reducer is:

```python
def violations(self) -> LogicalLifecycleDataset:
    ...
```

It consumes the logical replay trace or the private committed trace bound to a
materialized history Artifact. It never replays Events.

The ordered schema is:

```text
entity_identity
trigger_event_ref
trigger_event_identity
occurred_at
model_state_at_event
violation_kind
```

`violation_kind` is:

```text
illegal_transition | transition_from_terminal
```

A row is an observation of a declared StateModel against governed Event data.
It is not automatically a policy breach, quality defect, causal conclusion, or
Finding. Those interpretations require a separately owned analytical contract.

### Lifecycle filtering matrix

Lifecycle filtering consumes the shared predicate contract and has
`row_subset` effect. The exact registration is:

| Shape | `where(...)` | Filterable fields | Reason |
| --- | --- | --- | --- |
| `lifecycle/history@v1` | no | none | interval removal corrupts state continuity |
| `lifecycle/distribution@v1` | yes | axes, `as_of`, `model_state`, three count fields, `share` | independently meaningful summary cells |
| `lifecycle/transitions@v1` | yes | both state fields, `transition_count`, `share_of_modeled_transitions` | independently meaningful declared-pair cells |
| `lifecycle/dwell@v1` | yes | `model_state`, all counts; null checks only on duration fields | independently meaningful state summaries |
| `lifecycle/violations@v1` | yes | `occurred_at`, `model_state_at_event`, `violation_kind` | independently meaningful violation observations |

Raw subject and Event identity tuples, `trigger_event_ref`, definition
fingerprints, Artifact refs, completeness basis, and audit-only trace facts are
not filter operands.

Filtering does not rewrite the StateModel, replay history, transition graph, or
subject membership. No filtered Lifecycle summary becomes a PopulationDataset. The
only first-cutover Lifecycle bridge is typed `InState` selection on the
structurally complete history.

Generated Lifecycle fields use this exact predicate-kind registration:

| Field class | Admitted predicate kinds |
| --- | --- |
| retained governed axis | the shared logical-type matrix for that exact axis binding |
| `model_state`, `from_model_state`, `to_model_state`, `violation_kind` | `eq`, `not_eq`, `is_in` |
| integer count | `eq`, `not_eq`, `lt`, `lte`, `gt`, `gte`, `is_in` |
| floating share | numeric comparisons and `is_in`; null checks when nullable |
| `as_of`, `occurred_at` | datetime comparisons and `is_in` |
| duration statistic | `is_null`, `is_not_null` only |

State names and closed statuses have identity equality but no governed
collation, so ordering comparisons are not admitted. Duration thresholds wait
for the shared predicate contract to define one exact duration literal and
normalization rule.

## Complete Continuation Matrix

Every Module 6 family has exactly the following first-cutover continuations.
`state actions` means `execute()` on the Logical state and `show()` plus
`to_pandas()` on the Materialized state. Registered downstream operators are
available on both states and always return a Logical Dataset. A method absent
from this table is rejected by the
consumer's invocation contract; sharing a nominal family with a Module 5 shape
does not inherit that shape's registrations.

This matrix is the normative result of consumer-admission matching, not a
producer-owned continuation list stored on each family or operator result.

| Qualified shape | Admitted continuations |
| --- | --- |
| `population/entity-membership@v1` | Module 2 membership `where`/`sample`; `population=` on Metric/Event/Lifecycle sources; state actions |
| `event/journey@v1` from `first_per_subject` | `funnel`, `time_to_event`, `select_subjects(DroppedBefore)`, state actions |
| `event/journey@v1` from `every_start` | `time_to_event`, state actions |
| `event/funnel@v1` | `where`, compatible Event-funnel `compare`, state actions |
| `event/time-to-event@v1` | `where`, state actions |
| `lifecycle/history@v1` | `distribution`, `transitions`, `dwell`, `violations`, `select_subjects(InState)`, state actions |
| `lifecycle/distribution@v1` | `where`, state actions |
| `lifecycle/transitions@v1` | `where`, state actions |
| `lifecycle/dwell@v1` | `where`, state actions |
| `lifecycle/violations@v1` | `where`, state actions |
| `delta/funnel@v1` | `where`, Event-funnel `attribute`, state actions |
| `attribution/funnel-loss-rate@v1` | state actions |

Structural journey/history shapes do not admit `where`, `rank`,
or `limit`. No v1 `rank` or `limit` consumer invocation pattern matches those
shapes. Metric, Candidate, ordinary Delta, and ordinary Attribution shapes are
admitted only by the separate Module 5 consumer variants matching their exact
qualified shapes.

## Event Funnel Comparison Seam

Event funnel comparison reuses the public operator ids `compare` and
`attribute`, but its Event-specific registration and arithmetic inputs are owned
here rather than inferred from the Metric matrix.

The admitted chain is:

```text
EventDataset[event/funnel@v1]
  x compatible EventDataset[event/funnel@v1]
  -> compare
  -> DeltaDataset[delta/funnel@v1]
  -> attribute(target=FunnelLossRate, axes=...)
  -> AttributionDataset[attribution/funnel-loss-rate@v1]
```

The Event-specific overload is:

```python
def compare(
    self,
    baseline: LogicalEventDataset | MaterializedEventDataset,
) -> LogicalDeltaDataset:
    ...
```

Both operands must be `event/funnel@v1`. They must have the same Pattern, step
identity, matching policy, subject Entity/signature, exact axis contract, and
compatible target-Population definition. Their cohort windows may differ, but
must have the same duration and temporal-domain contract. The offset from each
cohort-window end to its `completion_through` bound must also be equal. Both
sides must have complete follow-up classification for every aligned step.

There is no caller-authored Event alignment policy. Comparison full-outer-aligns
exact PatternStep identity and axis tuples. It zero-fills only additive counts
for a provably absent tuple and recomputes rates with normal zero-denominator
null behavior.

The ordered `delta/funnel@v1` schema is:

```text
<one typed column per shared axis>
step_key
coordinate_presence
current_cohort_count
baseline_cohort_count
current_resolved_cohort_count
baseline_resolved_cohort_count
current_entry_count
baseline_entry_count
current_resolved_entry_count
baseline_resolved_entry_count
current_reached_count
baseline_reached_count
current_lost_count
baseline_lost_count
current_coverage_censored_count
baseline_coverage_censored_count
current_loss_rate_from_previous
baseline_loss_rate_from_previous
loss_rate_delta
calculation_status
```

`coordinate_presence` is `matched`, `current_only`, or `baseline_only`.
`calculation_status` is `ok`, `zero_denominator`, or `missing_side`. A complete
admitted action may publish the latter two with null rate values. Any censored
aligned input fails the comparison action before publication and is not encoded
as a partial output status. Additive count fields remain exact integers.

The Delta admits `where`, the Event-specific `attribute` overload below, and
state actions. Axes, `step_key`, presence/status fields, counts, and rate/delta
fields use the same predicate-kind rules as funnel rows. It does not acquire a
Module 5 Metric-Delta `rank` or `limit` registration merely because it shares
the Delta nominal family.

`FunnelLossRate` accepts one exact non-initial retained PatternStep:

```python
target = mv.funnel_loss_rate(step=payment_step)
```

The Event-specific overload is:

```python
def attribute(
    self,
    *,
    target: FunnelLossRate,
    axes: list[SemanticInput[DimensionKind]],
    mode: Literal["joint", "hierarchy"] = "joint",
    top_k: int | None = None,
) -> LogicalAttributionDataset:
    ...
```

Attribution is admitted only from an ungrouped logical funnel Delta whose
private logical inputs still retain both exact journey assignments. It uses
resolved-entry and lost-count components and may enrich exact current subject
axes under the same first-step anchor contract. It never rematches Events.

A materialized Delta is rejected because aggregate rows do not retain subject
assignment. A logical Delta built from already materialized funnel summaries is
rejected for the same reason. The repair reconstructs each funnel logically
from its logical journey or an explicit materialized journey scan leaf, then
compares and attributes before materializing the aggregate result. Artifact
lineage or source refs are not replay authority.

The ordered `attribution/funnel-loss-rate@v1` schema follows the shared joint or
hierarchy coordinate layout and adds:

```text
active_axis_mask
other_mask
contribution_kind
current_value
baseline_value
overall_delta
contribution
share_of_total_delta
share_of_positive_pool
share_of_negative_pool
contribution_rank
method
causal_claim
status
```

The authored resolution-prefix contract is stored once in family metadata.
`active_axis_mask`, not a numeric resolution ordinal, identifies the hierarchy
resolution in each row. This keeps the specialization identical to Module 5's
Attribution coordinate contract.

`contribution_kind` is `loss` or `denominator_mix`. `method` is the exact
registered funnel ratio-mix method, `causal_claim` is always `none`, and every
resolution must reconcile to the selected funnel loss-rate delta before
publication. Positive and negative pool shares follow the shared Attribution
contract and are never interpreted as improvement or degradation.

Module 5 continues to own common Delta/Attribution Dataset protocol, selector,
status, and action behavior. This module supplies only the Event-specific
family registration, compatibility, additive components, target identity, and
temporal meaning. This module contributes the independently versioned
`compare/event_funnel` and `attribute/event_funnel_delta` variant rows to the
shared registry; neither document duplicates capability, renderer, or compiler
inventories.

Module 5's Metric-only Python signature remains valid as one overload. Its
consumed-seam section, operator matrix, and Help inventory point to this Event
overload without restating these Event rows or arithmetic rules.

## Operator Input Matrix

Each operator binds the following inputs and concrete checks during construction:

| Occurrence | Logical input | Materialized input | Selected rule |
| --- | --- | --- | --- |
| exact Metric/Candidate `population=` input | project exact current input identities | project retained identity field | input-state-selected identity projection; no reach-through |
| journey `.select_subjects(DroppedBefore)` | execute exact Pattern assignment | consume retained dense journey rows | exact input rows; no origin replay; complete coverage required |
| history `.select_subjects(InState)` | execute exact replay intervals | consume retained complete history rows | exact input rows; no origin replay; complete at-instant truth required |
| `events.match` | current Pattern Events plus membership input | current Pattern Events plus immutable membership leaf | explicit current Event source execution |
| `journeys.funnel(axes=())` | consume journey definition | consume retained journey rows | exact input rows; no origin replay |
| `journeys.funnel(axes=...)` | consume journey plus current axes | retain journey leaf plus current axes | explicit current Dimension joins |
| `journeys.time_to_event` | consume journey definition | consume retained journey rows | exact input rows; no origin replay |
| `lifecycle.replay` | current StateModel Events plus membership input | current StateModel Events plus immutable membership leaf | explicit current Event source execution |
| `history.distribution(axes=())` | consume replay history | consume retained history rows | exact input rows; no origin replay |
| `history.distribution(axes=...)` | consume history plus current axes | retain history leaf plus current axes | explicit current Dimension joins |
| transitions/dwell/violations | consume exact logical history and trace | consume retained history and committed trace | exact input rows; no origin replay |
| owned row filter | current logical rows | retained current rows | shared logical/materialized filter matrix |

Equivalent lowering preserves the calculation over the exact rows owned by
each bound input token. It never means fallback from failed current
semantics to historical rows or fallback from an unreadable Artifact to
re-execution.

## Cross-Domain Loops

### Legal loop

The cross-domain population paths are:

```text
eligible Entity Metric or Candidate rows
  -> explicit population= on Metric, Event, or Lifecycle source
  -> owned Dataset analysis

complete Event journey or Lifecycle history
  -> explicit typed subject selection
  -> another PopulationDataset
  -> explicit population= on Metric, Event, or Lifecycle source
  -> owned Dataset analysis
```

Every population-input boundary preserves:

- exact subject Entity and identity signature;
- Session ownership;
- source Population and target-Population lineage;
- sampling authority and approximation disclosure;
- source temporal and completeness authority;
- source definition and family, including a selection definition for
  `PopulationDataset` inputs;
- logical or materialized input authority.

The consumer adds its own source-time and semantic authority. It does not
rewrite or discard the membership lineage.

### Illegal loop

The following do not create population-input authority:

- `where(...)` alone on a Dataset shape that does not already carry an admitted
  exact Entity identity contract;
- a terminal pandas DataFrame containing identities;
- a list, set, file, SQL query, or Ibis table of identifiers;
- aggregate funnel, distribution, transition, or dwell rows;
- source lineage that once contained subject identity;
- a Candidate item id without the entity-outlier identity contract;
- a materialized Dataset whose retained row contract does not contain the exact
  complete unique target-Entity identity field;
- recovering and replaying an origin plan from Artifact metadata.

### Same-plan and cross-process behavior

A logical admitted `PopulationInput` may be consumed in the same action graph
without local identity collection. It is not reusable authority across
independent actions or processes because source rows may change and no Artifact
exists.

A materialized admitted `PopulationInput` may be recovered and reused by ref
across actions or processes. The consumer reads the exact retained immutable
identity field and never re-runs the origin definition. `PopulationDataset` remains the
only family produced by Event/Lifecycle semantic subject selection; it is not
the only materialized Dataset that can provide population input.

## Identity Privacy and Persistence

### Raw identity locations

Raw subject and Event identity values may exist only in:

- engine expressions and datasource execution internal to an admitted action;
- authorized Dataset row storage;
- bounded explicit `show()` output;
- guarded complete terminal `to_pandas()` output;
- transient in-process batches already allowed by an exact guarded boundary.

They must not exist in:

- `repr`, `contract()`, ordinary cards, Help, or capability listings;
- definition or lineage summaries rendered to the user;
- Run arguments, audit projections, query diagnostics, or cleanup journals;
- structured-error received values, candidate repairs, examples, or logs;
- Artifact metadata fields, filenames, object keys, partitions, or engine
  relation names;
- Evidence subjects, scope projections, observations, Findings, or digests;
- telemetry tags, metrics, traces, exception messages, or test snapshots.

### Fingerprints and integrity hashes

Definition fingerprints may cover the exact selection definition and semantic
authority because they do not contain realized membership. They must use the
shared bounded redaction rules for predicate literals.

An integrity hash over stored identity rows may exist in Module 4's private
receipt and atomic Store publication authority. It is not public family metadata, a
membership id, an Evidence digest, or a card field. This matters especially for
small cohorts whose raw row hash could be dictionary-tested.

No public digest may allow equality testing of two realized selection Populations beyond
their explicit Artifact refs. Equal logical definitions and equal row counts do
not prove equal realized membership.

### Storage constraints

A PopulationDataset, Event journey, Event time-to-event, Lifecycle history, or
Lifecycle violation Artifact may contain identity rows only when the selected
Module 4 configured storage target:

- provides the ordinary Session-authorized access boundary;
- uses opaque runtime-owned locators;
- does not partition, name, or index storage with raw identity values;
- can validate exact schema, row count, non-nullness, and tuple uniqueness where
  required;
- keeps temporary resources private, proves execution terminal/fenced, and
  journals harmless deletion leftovers without blocking atomic publication;
- supports exact cleanup without logging row payloads.

Failure to meet those requirements is storage-selection failure, not permission
to collect identities locally or publish a reduced privacy contract.

### Family-safe metadata and Evidence

Committed family metadata may contain:

- exact subject Entity ref and identity-signature refs/types;
- Pattern, StateModel, Event, step, state, and Dimension semantic refs;
- definition and semantic fingerprints;
- source windows, coverage basis, matching policy, seed, and selection kind;
- aggregate row, subject, censoring, transition, violation, and quality counts;
- Artifact, producing Run, storage kind, schema, and bounded lineage refs.

It may not contain any raw subject or Event identity, identity-derived sample,
min/max, top values, per-identity reason, or public realized-membership digest.

Evidence may assert that identity uniqueness, completeness, matching,
reconciliation, censoring, interval, and selection checks passed. It records
counts and contract identities, not the rows that passed.

### Registered materialization contracts

Every Module 6 producing-operator registration names the common
`DatasetMaterializationContractV1` envelope owned by Module 4, including
producer and contract versions, family and shape, quality contract, Evidence
extractor, Finding extractor, validation-output contracts, retained-private-state
contracts, and Finding policy. Module 6 owns the exact registrations and their
semantic meaning. These identities are part of Dataset definition identity and
are supplied to Module 4 before Run admission. Missing registration, extractor
failure, schema mismatch, or blocking contradiction fails publication; the
runtime never substitutes a generic family extractor.

The exact first-cutover registrations are:

| Producing operator | Quality contract | Validation output | Evidence extractor | Finding extractor / policy | Retained private contract |
| --- | --- | --- | --- | --- | --- |
| Event/Lifecycle `select_subjects` | `subject_selection_quality@v1` | `subject_selection_validation@v1` | `subject_selection_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `session.events.match` | `event_journey_quality@v1` | `event_journey_validation@v1` | `event_journey_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `journeys.funnel` | `event_funnel_quality@v1` | `event_funnel_validation@v1` | `event_funnel_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `journeys.time_to_event` | `event_time_to_event_quality@v1` | `event_time_to_event_validation@v1` | `event_time_to_event_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `session.lifecycle.replay` | `lifecycle_history_quality@v1` | `lifecycle_history_validation@v1` | `lifecycle_history_evidence@v1` | `none@v1` / `zero_findings@v1` | `lifecycle_violation_trace@v1` |
| `history.distribution` | `lifecycle_distribution_quality@v1` | `lifecycle_distribution_validation@v1` | `lifecycle_distribution_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `history.transitions` | `lifecycle_transitions_quality@v1` | `lifecycle_transitions_validation@v1` | `lifecycle_transitions_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `history.dwell` | `lifecycle_dwell_quality@v1` | `lifecycle_dwell_validation@v1` | `lifecycle_dwell_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `history.violations` | `lifecycle_violations_quality@v1` | `lifecycle_violations_validation@v1` | `lifecycle_violations_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| Event funnel `compare` | `funnel_delta_quality@v1` | `funnel_delta_validation@v1` | `funnel_delta_evidence@v1` | `funnel_delta_finding@v1` / `bounded_algebraic_findings@v1` | none |
| Event funnel `attribute` | `funnel_attribution_quality@v1` | `funnel_attribution_validation@v1` | `funnel_attribution_evidence@v1` | Module 5 `contribution_finding@v1` / `bounded_algebraic_findings@v1` | method-owned mapped membership and additive component state |

All registrations first run `dataset_structure_quality@v1`, validating ordered
schema, field ids, logical types, nullability, row-key uniqueness, canonical
ordering inputs, realized row count, family, and qualified shape. Family checks
then run over the same staged rows and bounded validation outputs:

| Quality contract | Blocking checks | Canonical bounded Evidence projection |
| --- | --- | --- |
| `subject_selection_quality@v1` | non-null exact identity tuples; row-key uniqueness; complete membership authority; no censored member | source family/shape, subject Entity/signature refs, selection kind, row count, coverage basis |
| `event_journey_quality@v1` | dense exact Pattern steps per journey; matching algorithm/version; within-journey occurrence uniqueness; time monotonicity; status/null coherence; coverage classification | Pattern/matching ids, journey/subject/step counts, completion-status counts, coverage basis; no journey or identity values |
| `event_funnel_quality@v1` | dense steps/groups; count equations; zero-denominator nulls; grouped-to-ungrouped reconciliation | axes/step refs, group count, count ranges, status totals, reconciliation maxima |
| `event_time_to_event_quality@v1` | one row per source journey; selected-step identity; non-negative complete duration; status/null and coverage coherence | selected step refs, row/status counts, duration range for defined aggregate values, coverage basis |
| `lifecycle_history_quality@v1` | source-origin classification; interval key uniqueness; positive clipped intervals; no overlap; legal adjacency; status/end coherence; violation-trace linkage | StateModel/seed ids, subject/interval/status/violation counts, coverage basis; no identity or interval samples |
| `lifecycle_distribution_quality@v1` | dense instants/states/groups; known/censored count coherence; grouped reconciliation; share equation | requested instants, state/axis refs, count ranges, censored totals, reconciliation maxima |
| `lifecycle_transitions_quality@v1` | dense distinct declared pairs; non-negative counts; legal total and share reconciliation | StateModel id, pair count, transition total, zero-count rows, reconciliation maximum |
| `lifecycle_dwell_quality@v1` | dense states; interval-count equation; completed-only exact statistics; censor/null coherence | StateModel id, state/count ranges, censor totals, defined-statistic counts |
| `lifecycle_violations_quality@v1` | exact trace projection; key uniqueness; closed violation kind; trigger/state coherence | StateModel id, violation-kind counts, distinct trigger refs; no subject or occurrence identities |
| `funnel_delta_quality@v1` | compatible inputs; coordinate presence; additive zero-fill; count/rate equations; complete follow-up; delta status coherence | Pattern/axis refs, presence/status/count totals, reconciliation maxima, coverage basis |
| `funnel_attribution_quality@v1` | complete mapped membership; additive component equations; endpoint reproduction; per-resolution reconciliation; rank/share/status coherence | target/method/axis refs, resolution and Top-K/Other counts, status totals, reconciliation maxima |

Validation outputs contain only the bounded counts, extrema, booleans, and
reconciliation residuals needed by these checks. They are committed only through
the Module 4 Evidence envelope and are not a second public result.

`none@v1` always emits the canonical empty Finding digest and count zero,
including for Lifecycle violations. `funnel_delta_finding@v1` selects only
`calculation_status = ok` rows in descending `abs(loss_rate_delta)` then row-key
order. `contribution_finding@v1` selects only reconciled rows in descending
`abs(contribution)`, then resolution and row-key order. Both use Module 5's
`finding_cap = 1000`, report eligible/emitted/truncated counts, and have the
epistemic label `algebraic`; attribution also fixes `causal_claim = none`.
Identity-bearing row keys are never projected into a Finding. An extractor that
cannot construct every selected Finding fails publication rather than skipping
the row.

The exact Module 6-owned `FunnelDeltaFindingValueV1` contains
`kind="funnel_delta"`, `step_key`, `coordinate_presence`, current/baseline
cohort counts, resolved-cohort counts, entry counts, resolved-entry counts,
reached counts, lost counts, coverage-censored counts,
`current_loss_rate_from_previous`, `baseline_loss_rate_from_previous`,
`loss_rate_delta`, and `calculation_status="ok"`. Every count is a
non-negative exact integer. Both coverage-censored counts are exact zero; all
three rate values are finite. Only complete-follow-up status-`ok` rows are
eligible. Axis values live in the outer ordered Finding coordinates, while
Pattern and subject-Entity authority live in the outer Funnel Finding subject.

For funnel Attribution, Module 6 extends the Module 5-owned
`ContributionFindingValueV1.contribution_kind` with exactly `loss` and
`denominator_mix`. It uses the same masks, typed shares, rank, reconciliation,
and `causal_claim="none"` invariants; no second contribution payload exists.

`lifecycle_violation_trace@v1` is the only first-cutover private retained state
introduced here. It binds the history Artifact, exact StateModel and replay
versions, trigger refs, trace schema, trace row count, and integrity receipt. It
contains the identity-bearing violation rows needed by `history.violations()`
under the same authorization boundary as history storage. Cold reads validate
it and fail if absent or corrupt; they never replay Events to rebuild it.

The trace is an actual `retained_parts[]` instance under the history Artifact,
using role `lifecycle_violation_trace` with the registered version and its own
receipt. Its schema/count/content and locator are committed with the primary
history receipt; both reservations transfer in one Store transaction. It is not
temporary exchange garbage or a separately published Artifact. Ordinary history
row reads do not open the trace; `violations()` and full integrity inspection do.
The same physical ownership and authorization rules apply across local, engine,
and object backing, with no Event replay when the trace is missing or corrupt.

## Definition Identity and Lineage

### PopulationDataset definition identity

The logical PopulationDataset fingerprint binds:

- source Dataset definition fingerprint and input authority token;
- source family and shape;
- exact subject Entity and identity signature;
- Population, target-Population, scope, and sampling lineage;
- selection operator and complete typed selection payload;
- source temporal and completeness requirements;
- PopulationDataset row-contract and operator versions.

It does not bind realized identities, row count, physical plan, engine choice,
storage candidate, or execution diagnostics.

### Event definition identity

An Event Dataset source fingerprint additionally binds:

- exact Pattern and resolved Event/role semantic fingerprints;
- matching policy;
- cohort window and completion-through bound;
- optional Population authority;
- completeness declarations and required coverage identities;
- exact output row-contract and row-set-contract fingerprints.

Reducer fingerprints bind the exact source input authority, reducer parameters,
PatternStep handles, axes and anchor semantics, and output row and row-set
contracts.

### Lifecycle definition identity

A Lifecycle replay fingerprint binds:

- exact StateModel, state, trigger, Event, and participant-role fingerprints;
- replay window and seed;
- optional Population authority;
- completeness declarations and prior-history requirements;
- fixed violation behavior version;
- exact output row-contract and row-set-contract fingerprints.

Reducer fingerprints bind requested instants, axes and temporal anchors, exact
input Artifact or logical-definition identity, reduction version, and output
row and row-set contracts.

### Bounded lineage

Lineage discloses semantic refs, family/shape transitions, window identities,
matching/seed/selection kinds, coverage class, field requirements, and input
Artifact refs within the common budget. Predicate values remain redacted and
identity rows never appear.

## Structured Errors and Repairs

Construction-time errors occur before datasource work or Run admission. They
name expected, received, and one mechanically valid next step derived from real
contract state.

| Failure | Phase | Repair source |
| --- | --- | --- |
| Pattern steps resolve different subject Entities | construction | exact resolved step/Entity bindings |
| explicit Population Entity mismatch | construction | required source subject Entity/signature |
| foreign Logical membership or cross-Store Artifact | construction | materialize in the owner Session, then explicitly select its same-Store Artifact |
| bare or foreign step/state selector | construction | exact retained PatternSteps or ModelStateHandles |
| missing Event cohort or Lifecycle replay window | construction | canonical source signature |
| invalid follow-up bound | construction | exact cohort-window end |
| omitted/invalid matching policy | construction | closed policy constructors |
| unrelated or overlapping completeness declaration | construction | exact required Event refs |
| bounded completeness declaration supplied to `from_inception()` | construction | source-origin declaration for the exact trigger Events, or omit it and use observed source-origin authority |
| stale or incompatible source-origin ref | construction/action | current governed source-origin identity for every trigger Event |
| reducer on wrong family/shape | construction | mechanically derived continuations from matching consumer contracts |
| structural `where(...)` request | construction | owned reducer or subject-selection continuation |
| unavailable field filter | construction | exact current filterable fields |
| ambiguous subject-axis path | construction | real governed path candidates |
| insufficient inception history under proved coverage | action | widen governed history or repair Event/StateModel data |
| unknown Event follow-up for subject selection | action | establish watermark or explicit bounded declaration |
| ambiguous same-time Event order | action | repair governed occurrence identity/order authority |
| matching implementation disagrees with the registered assignment algorithm | quality/publication | repair the lowering; never publish an alternative assignment |
| duplicate selected identities | action | repair violated source row contract |
| funnel/group reconciliation failure | action | inspect source contract; no partial output |
| identity-safe storage unavailable | compilation/storage selection | choose an admitted durable policy/backend |
| recovered identity Artifact unreadable/corrupt | runtime read | repair storage authority; never replay origin |

Errors never render identity examples, first offending identity, Event payloads,
SQL, Ibis, storage credentials, or unbounded candidate lists. A row-dependent
failure may disclose aggregate counts and safe semantic identities only.

Coverage failure is not repaired by silently dropping uncertain subjects.
Identity mismatch is not repaired by a join on similarly named columns.
Unsupported compilation is not repaired by a post-failure pandas, Polars, or
DuckDB fallback. The 2026-09-05 amendment fixes matching, replay and
identity-bearing reducers to their registered engine implementation. Local
Artifact relations may use their fixed DuckDB reader when that exact method is
supported; remote identities are not collected to make an unsupported method
work. Complex methods are admitted per tested engine, not universally.

## Contract and Help Disclosure

### Observed occurrence bounds are not a public action

The first cutover removes:

```text
session.events.occurrence_bounds(...)
EventOccurrenceBounds
analysis.events.occurrence_bounds
```

The current operation performs datasource aggregation immediately and returns
observed earliest/latest matching Event timestamps. Those timestamps describe
only rows seen by that query. They do not establish source completeness,
source-origin authority, the eligible subject Population, or the correct
business analysis window.

Retaining it would create a second datasource-read lifecycle outside
`LogicalDataset.execute()` and invite callers to derive analytical scope from
data presence. Module 6 therefore provides no non-Dataset inspection action,
Dataset wrapper, compatibility alias, or automatic replacement in the first
cutover.

Event matching and Lifecycle replay continue to require their exact explicit
windows. Follow-up and inception truth use the bounded/source-origin
completeness contracts defined by this module. Help and structured errors name
the missing window or completeness authority; they never query an observed
range or silently fill a boundary.

A future observed-range diagnostic would require a separate accepted design
for a bounded, auditable read action and could not restore this method by
compatibility. It is not reserved by the first-cutover registry.

### `dataset.contract()`

Owned Dataset contracts disclose, within the common budget:

- family, qualified shape, one-row meaning, ordered schema, and row key;
- subject Entity and identity-signature shape without identity values;
- Population, target-Population, scope, sampling, and source-time authority;
- Pattern/matching/follow-up or StateModel/seed/window identity;
- logical/materialized state and concrete input checks;
- coverage and censoring requirements;
- exact filterable fields and effect;
- exact typed reducers, selectors, and state-specific actions;
- construction-time rejection and action-time preconditions;
- whether materialization is required for cross-process cohort reuse.

It does not execute, estimate rows, prove completeness, show a plan, or rank a
subject.

### Focused Help

The public-cutover plan must provide one independently resolvable Help leaf for
every public source, reducer, selection constructor, matching policy, Dataset
family, and generated-field contract introduced here. The canonical inventory
is:

```text
analysis.PopulationDataset
analysis.EventDataset
analysis.LifecycleDataset
analysis.events.match
analysis.event_matching
analysis.event_matching.first_per_subject
analysis.event_matching.every_start
analysis.event_dataset.funnel
analysis.event_dataset.time_to_event
analysis.event_dataset.select_subjects
analysis.event_dataset.compare
analysis.funnel_delta_dataset.attribute
analysis.lifecycle.replay
analysis.lifecycle_dataset.distribution
analysis.lifecycle_dataset.transitions
analysis.lifecycle_dataset.dwell
analysis.lifecycle_dataset.violations
analysis.lifecycle_dataset.select_subjects
analysis.dropped_before
analysis.in_state
analysis.funnel_loss_rate
analysis.from_inception
analysis.BoundedCompletenessDeclarationV1
analysis.SourceOriginCompletenessDeclarationV1
```

The inventory intentionally contains no `analysis.events.occurrence_bounds`
target or `EventOccurrenceBounds` type.

`analysis.event_dataset.compare` and
`analysis.funnel_delta_dataset.attribute` are the capability links for the two
Module 6-owned variants in the shared operator registry; neither generic Metric
leaf may claim their inputs or parameters. Population-input admission is
documented by the source Help leaf and the shared Observation Model contract;
it has no Dataset conversion Help target.

Source and constructor Help must explain the business choice as well as the
signature:

- membership scope versus Event/Lifecycle source windows;
- matching-policy interpretations;
- follow-up and declaration authority;
- from-inception history requirements;
- right censoring versus coverage censoring;
- row filtering versus subject membership;
- logical same-plan versus materialized cold reuse;
- identity privacy and terminal export boundaries.

Help and `contract()` consume the same registry contracts or are independently
drift-tested. Neither renderer maintains a shadow family/operator inventory.

## Rejected Alternatives

### Keep a separate SubjectSet family or subclass

Rejected by the 2026-09-05 amendment because the published row contract and
member-consumption meaning are identical to Population. Domain selection owns
its predicates, completeness, and source time; those production requirements do
not need another membership family or a different filter surface.

### Allow a coverage-censored PopulationDataset as `population=`

Rejected because it would present uncertain membership as a
governed cohort and make every consumer rediscover readiness state. Event and
Lifecycle source Datasets already retain censored diagnostics.

### Add `PopulationDataset.where(...)` over identity values

Rejected because identity tuples are not ordinary analysis literals, composite
components are deliberately not public, and manual identifier membership needs
its own privacy and authorization contract.

### Use one optional-field subject-selection method for every family

Rejected because exact row projection and Event/Lifecycle semantic selection
have different inputs and censoring authority. One mega-signature would be
nominally accepted but semantically sparse.

### Put reducers back on Session

Rejected because matching/replay are source operations while funnel,
time-to-event, distribution, transitions, dwell, and violations consume an
existing Dataset's exact rows. Session duplicates create two public owners.

### Infer `first_per_subject` when matching is omitted

Rejected because matching policy changes the observational unit
and business answer. Downstream funnel admission does not justify changing the
source definition.

### Reuse Population `time_scope` as Event or Lifecycle window

Rejected because Entity eligibility, Event anchor time, Event
follow-up, Lifecycle emitted intervals, and inception lookback are independent
authority axes. Reuse fails common questions with distinct membership and
behavior windows.

### Accept bare step keys or state names

Rejected because names do not bind the owning Pattern, Event participant role,
or StateModel. Exact typed handles remain copyable and recoverable without
string guessing.

### Filter journey or history rows structurally

Rejected because dropping one row changes assignment, continuity, completion,
transition, dwell, and selection semantics. Owned reducers provide typed
questions over the complete structure.

### Reconstruct missing identities from lineage

Rejected because a materialized Dataset is an immutable scan leaf. Lineage is
bounded audit context, not executable identity authority.

### Build PopulationDataset from pandas, files, SQL, or Python collections

Rejected because it bypasses governed Entity identity, Session ownership,
privacy, scope, sampling, Evidence, and cold-recovery authority.

### Default every Lifecycle subject to the initial state

Rejected because a StateModel initial state begins at deterministic inception,
not at the requested replay-window boundary. Missing history is not evidence of
initial state.

### Treat replay violations as Findings automatically

Rejected because an illegal modeled trigger is an observation under one
StateModel. A business policy or data-quality conclusion needs its own accepted
contract.

### Fall back to local identity processing

Rejected because identity privacy and high-cardinality bounds are semantic
requirements. Missing backend capability produces a structured failure or an
explicit durable-boundary repair.

## Vertical Acceptance Journeys

Journey fixtures must choose an explicit compatible execution/storage setup.
A retained Population or identity selection later joined to current sources uses
an engine target and reader in that same datasource domain. Local Artifact-only
continuations use DuckDB. These are fixture configurations, not automatic
placement or target switching. Include a conflicting-domain negative fixture;
`.execute()` must not be advertised as a repair unless its configured writer
and reader can actually establish the required common domain within bounds.

### Metric population input into Event analysis

```python
high_resource = features.where(
    mv.all_of(
        mv.gte(scanned_bytes, scanned_bytes_threshold),
        mv.gte(cpu_seconds, cpu_seconds_threshold),
    )
)

selected_features = high_resource.execute()

failure_journeys = session.events.match(
    pattern=query_failure_pattern,
    cohort_window=cohort_window,
    completion_through=followup_end,
    matching=mv.first_per_subject(),
    population=selected_features,
)

failure_funnel = failure_journeys.funnel().execute()
failure_funnel.show()
```

Acceptance proves no identity collection, one exact materialized Metric
identity-projection scan leaf, unchanged Metric family, current Event semantic
authority, complete follow-up classification, and no Event rematch during
funnel reduction.

### Event dropout selection into Metrics

```python
dropouts = failure_journeys.select_subjects(
    mv.dropped_before(step=payment_step),
).execute()

dropout_metrics = session.observe(
    metrics=[support_ticket_count],
    population=dropouts,
    time_scope=followup_window,
    time_dimension=ticket_created_at,
)
```

Acceptance proves that selected membership exactly equals resolved funnel loss,
coverage-censored subjects block publication, and the Metric source consumes
the immutable identity leaf without rematching Events.

### Lifecycle selection back into Event and Metric analysis

```python
history = session.lifecycle.replay(
    model=subscription_state,
    window=window,
    seed=mv.from_inception(),
    population=accounts,
)

churned = history.select_subjects(
    mv.in_state(churned_state, at=window.end),
).execute()

churn_metrics = session.observe(
    metrics=[lifetime_value],
    population=churned,
)

retention_journeys = session.events.match(
    pattern=retention_pattern,
    cohort_window=retention_window,
    completion_through=retention_followup_end,
    matching=mv.first_per_subject(),
    population=churned,
)
```

Acceptance proves exact state-at-instant truth, no assumed initial state,
identity-safe cold recovery, and independent Lifecycle and Event time windows.

### Materialized structural reducers

```python
stable_history = history.execute()

distribution = stable_history.distribution(at=checkpoints)
transitions = stable_history.transitions()
dwell = stable_history.dwell()
violations = stable_history.violations()
```

Acceptance proves all reducers consume one immutable history and committed
violation trace, do not query trigger Events, and recover the exact owned
family/shape contracts in a fresh process.

### Empty complete cohort

1. Construct an admitted selection whose complete result is empty.
2. Materialize and cold-recover the zero-row PopulationDataset.
3. Pass it to Event and Metric sources.
4. Assert exact empty journey/Metric outputs and dense zero funnel rows where
   the family contract requires them.
5. Assert no identity sample, fake sentinel member, or unknown-coverage warning
   is invented.

### Coverage failure

1. Match a Pattern whose last Event has no watermark or declaration through the
   required follow-up bound.
2. Inspect coverage-censored journey rows.
3. Attempt `DroppedBefore` selection.
4. Assert the action fails atomically with a bounded coverage repair.
5. Assert no PopulationDataset Artifact, partial identity storage, Evidence, or Finding
   is published.

### Identity privacy adversary

1. Use one single-member and one composite-key PopulationDataset.
2. Execute success, failure, cancellation, cleanup, materialization, recovery,
   `show`, and `to_pandas` paths.
3. Assert identities appear only in the two explicit terminal row reads and
   authorized Dataset storage.
4. Scan Store projections, Runs, logs, exceptions, cards, Evidence, Findings,
   object names, engine locators, and telemetry for raw values and public
   membership hashes.

## Implementation Evidence Required

Implementation cannot claim this design from signatures, unit mocks, a healthy
port, or a successful backend query alone. The public-cutover plan must require:

1. registry snapshots for every owned family, shape, source, reducer, selection,
   field role, filter registration, authority rule, and consumer admission
   contract, plus derived-continuation reachability tests;
2. method-signature and Help-resolution tests for every canonical path and every
   removed Session duplicate;
3. row-contract and row-set-contract builder tests proving field order, ids,
   roles, logical types, nullability, row keys, cardinality, ordering, and
   definition fingerprints before
   execution;
4. exact Session, Entity, composite-key, Population, sampling, scope, Pattern,
   StateModel, step, state, and axis admission matrices;
5. matching differential tests for all policies, repeated Events, simultaneous
   occurrences, missing steps, shared non-final occurrences, exclusive final
   assignment, and coverage bases;
6. funnel density, zero-denominator, censoring, grouped reconciliation, and
   no-rematch tests;
7. time-to-event complete, incomplete, censored, repeated-attempt, and typed-step
   tests;
8. replay tests for source-origin versus bounded coverage, inception lookback,
   no-trigger subjects, missing inception, terminal state, illegal transitions,
   simultaneous triggers, clipping, and censoring;
9. distribution, transition, dwell, and violation numerical and structural
   differential tests;
10. filter drift tests proving structural shapes reject `where(...)`, every
    generated field is registered once, and raw identities/audit fields cannot
    enter predicates;
11. subject-selection tests for logical and materialized inputs, empty complete
    membership, duplicates, coverage failure, and no local collection;
12. same-plan compiler tests proving logical PopulationDataset semi-join consumption
    and one evaluation of shared membership;
13. materialized scan-leaf tests proving cold membership recovery without an
    origin graph and no re-execution across independent actions;
14. high-cardinality Event/Lifecycle plans proving no unbounded local transfer;
15. Run-admission, failure-injection, and publication tests proving no partial
    identity Artifact or Evidence on failure;
16. materialization-contract tests resolving every quality, validation,
    Evidence, Finding, Finding-policy, and retained-state id before Run admission,
    including canonical zero-Finding envelopes and extractor failure;
17. local, engine, and object storage tests for opaque locators, identity-safe
    metadata, exact row count, schema, and cleanup;
18. adversarial redaction tests across errors, logs, Runs, cards, Evidence,
    Findings, telemetry, object names, and recovery diagnostics;
19. real-agent execution of every vertical journey with terminal Runtime
    Run/Artifact/Evidence proof in a fresh process.

The terminal proof is a same-family recoverable Dataset with exact committed
authority, or one terminal failed Run with no partial publication. A transcript,
compiled query, local preview, AM dispatch, or healthy process is not acceptance.

## Cross-Module Seams

### Dataset Core supplies

- nominal Dataset registration, common state, actions, field selectors,
  fingerprints, lineage, `repr`, `show`, `contract`, and scan leaves;
- `EventDataset` and `LifecycleDataset` family slots;
- registry-based admission rather than Python inheritance.

This module supplies family row meanings, shapes, fields, and identity/privacy
requirements without changing the common Dataset protocol.

### Observation Model supplies

- the sole Population family, row contract, filtering, and sampling;
- Population and target-Population authority;
- exact Entity identity signature and shared `population_input` seam;
- Population scope and sampling authority;
- the shared predicate, filter-effect, field-resolution, ordering, and
  authority-assignment contract.

This module registers owned filterable fields and exact subject selection. It
does not add predicate syntax, reinterpret Population scope, or add
`PopulationDataset.observe(...)`.

### Typed Operators supplies

- exact identity-bearing Metric and entity-outlier Candidate row contracts used
  by `PopulationInput` admission;
- common operator ids and Dataset-to-Dataset registration protocol;
- common Delta/Attribution family behavior used by Event funnel specialization.

This module supplies the PopulationDataset output contract and Event-specific
selection/comparison registrations. Neither module creates a second identity
authority.

### Direct Compiler and Fixed Execution Boundaries consumes

- source and reducer semantic node ids plus exact row-contract and
  row-set-contract fingerprints;
- same-plan logical population-input requirements;
- materialized population-input identity scan-leaf requirements;
- matching, replay, ordering, completeness, reconciliation, and exact reduction
  requirements;
- identity-safe boundary and diagnostic constraints;
- one fixed engine recipe for each matching/replay/identity-bearing method,
  with precise support on tested adapters and no kernel collection fallback;
- same-domain validation for source, Population and explicit current-semantic
  inputs, independently of their Logical/Materialized authority.

The compiler binds that recipe in the inherited domain. It cannot import a
materialized membership set into another engine, discover federation routes,
select a new matching policy, seed, censoring interpretation or subject set.
Materialized selection followed by current-source observation is executable
only when its fixed reader and the source share an admitted domain. Otherwise
it fails with a real storage/configuration repair or a typed unsupported reason.
Module 5's Event funnel compare and attribution variants remain fixed Ibis
recipes over the exact Event-owned additive components.

### Materialization Runtime consumes

- privacy-safe storage and metadata requirements;
- concrete input checks for every occurrence;
- PopulationDataset complete-membership publication gate;
- family-specific schema, uniqueness, coverage, reconciliation, violation-trace,
  quality, Evidence, and Finding inputs;
- exact cold-recovery privacy invariants;
- cleanup of every authorized identity-bearing reader/writer batch, Runtime
  staging file and DuckDB workspace; harmless surviving files remain
  journaled without blocking valid publication after termination/fencing.

The runtime commits those requirements through its one Artifact publication
decision. It does not reinterpret selection, expose identity rows in audit, or
recover a logical origin plan.

### Public Cutover consumes

- exact eager Session/Frame/selection paths to remove;
- new Dataset methods, family registrations, Help leaves, error contracts,
  skills, docs, tests, and runtime journeys;
- replacement of flat complete-through declarations and watermark receipts by
  the closed bounded/source-origin coverage contracts above;
- deletion of `session.events.occurrence_bounds(...)`,
  `EventOccurrenceBounds`, its Help target, tests, and examples with no
  first-cutover replacement;
- Store-generation replacement without migration or dual read;
- implementation against the synchronized north-star examples and Module 5
  overload seams; Public Cutover may not re-infer or change those contracts.

## Review and Acceptance Gates

The owner decision and design-review gates are satisfied. Implementation and
cutover acceptance must prove:

1. every subject-selection producer returns the sole Module 2 Population
   family with exact common row and row-set contracts;
2. logical and materialized membership authority is unambiguous;
3. complete, empty, and uncertain selection behavior is exact;
4. Event/Lifecycle membership and time windows have distinct owners;
5. every source and reducer has one exact signature and complete
   construction-time row and row-set contracts, including field ids, roles,
   types, nullability, row key, cardinality, and ordering;
6. every owned shape has a closed filter and continuation matrix;
7. every bridge names exact identity, coverage, and semantic dependency requirements;
8. raw identities have one exhaustive allowed-location contract;
9. cold recovery reconstructs authority without logical replay;
10. cross-domain loops require explicit `population=PopulationInput` and no
    local collection;
11. no public Session reducer, detached selection, Frame/Result, bare string
    selector, or compatibility alias remains implicit;
12. every failure has one phase owner and bounded typed repair;
13. every producing operator resolves one versioned quality, validation,
    Evidence, Finding, Finding-policy, and retained-state contract before Run
    admission;
14. `every_start` assignment and `from_inception()` source-origin coverage have
    one backend-independent conformance contract;
15. implementation evidence includes terminal Runtime proof, not transport or
    harness evidence alone;
16. every method uses its fixed admitted engine recipe; materialized readers
    preserve identity location and same-domain requirements without automatic
    import, local identity collection, hidden Artifacts or failure fallback;
17. no observed occurrence-range read performs datasource work outside
    `execute()`, and Event/Lifecycle windows remain explicit authored inputs.

## Frozen Module Decisions

This module freezes:

1. Module 2 owns the sole `PopulationDataset` family; domain selections
   produce it and sources admit it in direct-membership mode;
2. one PopulationDataset row is one exact complete tuple-valued Entity identity;
3. logical PopulationDataset authority is same-plan only;
4. a materialized admitted `PopulationInput` is the cold-recoverable population
   boundary; materialization never converts its Dataset family;
5. every published PopulationDataset has complete membership truth and empty complete
   membership is legal;
6. PopulationDataset uses Module 2's membership `where(...)` in v1;
7. exact Entity Metric and entity-outlier Candidate rows enter sources directly
   through explicit `population=`, while Event/Lifecycle use typed
   `.select_subjects(selection)` when domain selection semantics are required;
8. the v1 domain selection producers accept first-per-subject journey and
   replay history; explicit Population roots remain owned by Module 2;
9. `events.match` is the sole Event source and requires an explicit matching
   policy, cohort window, and completion-through bound;
10. Population membership scope and Event/Lifecycle source windows remain
    separate;
11. Event journey rows are dense over journey by Pattern step;
12. journey and history reject structural filtering;
13. funnel, time-to-event, Lifecycle summaries, and violations use the exact
    filter matrices above;
14. PatternStep and ModelStateHandle are the only step/state selector inputs;
15. Event matching supports first-per-subject and explicit exclusive/shared
    every-start policies only;
16. funnel accepts first-per-subject journeys only and grouped counts must
    reconcile exactly;
17. `lifecycle.replay` is the sole Lifecycle source and requires explicit
    `from_inception()`;
18. no-trigger, missing-inception, and insufficient-coverage subjects remain
    distinct;
19. replay violation behavior is fixed record-and-continue and not a public
    policy parameter;
20. distribution instants are explicit, transitions/dwell are dense over the
    StateModel, and duration statistics exclude censored intervals;
21. source and reducer authority is selected once with no fallback or rematch;
22. identity values appear only in governed rows and explicit terminal reads;
23. realized identity hashes remain private integrity authority;
24. all cross-domain reuse passes through explicit
    `population=PopulationInput`;
25. eager Frame/Result, Session reducer, detached selection, migration, alias,
    dual-read, and local-identity fallback paths do not survive cutover;
26. every owned shape has one complete construction-time field, coordinate,
    key, nullability, and ordering contract;
27. `every_start` shares non-final occurrences across attempts and applies
    exclusive/shared assignment only to the final PatternStep;
28. only compatible source-origin coverage can prove inception absence;
29. every producing operator uses the exact registered quality, Evidence,
    Finding, validation-output, Finding-policy, and retained-state contracts
    above;
30. `session.events.occurrence_bounds(...)`, `EventOccurrenceBounds`, and their
    Help/persistence surface are removed without replacement; observed row
    bounds never become window or completeness authority.
31. completeness declarations are constructed directly through the two
    immutable versioned public classes; `CompletenessDeclaration` is
    unexported annotation shorthand and no helper or watermark alias exists.

Changing one of these decisions requires an explicit amendment to this module
before the public-cutover plan or implementation depends on a replacement.

## Owner Confirmation

The 2026-09-05 amendment replaces the former separate membership family with
Population and enables its common Dimension filters. Domain matching, replay,
selection truth, and completeness choices below remain unchanged.

On 2026-09-02 the owner accepted all six surfaced choices:

1. explicit Event matching policy;
2. direct Entity identity projection at the population-input boundary and
   separate Event/Lifecycle semantic selection methods;
3. complete-only PopulationDataset publication with legal empty membership;
4. distinct no-trigger, missing-inception, and unknown-coverage Lifecycle
   outcomes;
5. independent Population membership and Event/Lifecycle source-time windows;
6. exact typed PatternStep and ModelStateHandle selectors without string
   fallbacks.

On 2026-09-04 the owner removed the observed occurrence-range read from the
first cutover. Event and Lifecycle windows remain explicit, and only the closed
completeness contracts above may establish follow-up or inception truth.

On 2026-09-04 the owner also accepted direct construction of
`BoundedCompletenessDeclarationV1` and
`SourceOriginCompletenessDeclarationV1` with the exact validation contract
above. No public helper or compatibility constructor is retained.

The owner decision gate is therefore complete. Later review may find an
internal inconsistency or missing seam, but implementation must not silently
replace one of these product choices.

## Final Boundary

Module 6 decides how Event/Lifecycle domain semantics produce an exact governed
`PopulationDataset`; what every Event and Lifecycle Dataset row means; and when
coverage, privacy, temporal, and materialized authority are sufficient for
those operations. Module 2 owns admission of that set and other exact
identity-bearing Datasets through the shared population-input boundary.

It does not expose identities as metadata, make a source Dataset reusable
without materialization, recover an origin plan, choose a physical semi-join,
or let an uncertain selection masquerade as governed Population membership.
