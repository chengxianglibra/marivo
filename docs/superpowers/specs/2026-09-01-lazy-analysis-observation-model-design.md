# Lazy Analysis Observation Model Design

Date: 2026-09-01

Revised: 2026-09-07

Status: accepted; amended 2026-09-07 for Entity identity, aggregation algebra,
Ibis pushdown, and pandas execution. These are target design contracts; the
current eager implementation changes only through the Public Cutover Plan.

## Outcome

Define the public and semantic contract that turns governed Entity membership
and governed Metrics into lazy `PopulationDataset` and `MetricDataset` values.

A reviewer can determine, without consulting planner or runtime design:

- which Entity is the analytical subject;
- which exact primary-key signature identifies one Population member;
- how a default Population is inferred and when inference fails;
- how every admitted explicit `population=` Dataset supplies membership;
- what one Entity-grained multi-Metric observation row means;
- how Dimension and time coordinates are introduced and ordered;
- what `aggregate()` removes and what Population authority it preserves;
- how parameterized non-secret source values are captured into a logical
  definition without becoming ambient execution state;
- when `rollup(...)` may coarsen already-defined Dataset coordinates without
  recomputing through an origin graph;
- whether additive, semi-additive, non-additive, component, ratio, weighted,
  or cumulative Metric semantics admit the requested coordinate transition;
- which transitions remain valid after a materialization barrier;
- which structured repair is valid for an Entity, scope, coordinate, or
  aggregation conflict.

This document is the Module 2 authority named by
[`2026-09-01-lazy-analysis-design-decomposition-plan.md`](2026-09-01-lazy-analysis-design-decomposition-plan.md).
It consumes the accepted Dataset value contract in
[`2026-09-01-lazy-analysis-dataset-core-design.md`](2026-09-01-lazy-analysis-dataset-core-design.md)
and refines the observation invariants in
[`2026-09-01-lazy-analysis-dataset-dsl-design.md`](2026-09-01-lazy-analysis-dataset-dsl-design.md).

## Ownership Boundary

This document owns:

- Entity Population meaning and primary-key authority;
- the `PopulationDataset` family contract;
- default Population inference from Metric Entity bindings;
- explicit `session.population(...)` construction;
- Population reference scope, predicates, sampling requests, and target
  Population lineage;
- the shared `AnalysisPredicate` vocabulary and family-preserving
  `Dataset.where(...)` contract;
- the shared filter-effect vocabulary and state-by-field-resolution authority
  assignment;
- Population-membership and Metric-row filtering specializations;
- the canonical lazy `session.observe(...)` signature;
- the public `Session.source_bindings(...)` authoring scope and its immutable
  source-definition capture semantics;
- the closed `PopulationInput` union and exact direct-membership or
  identity-projection admission selected by `population=`;
- Entity-grained multi-Metric observation row semantics;
- the `MetricDataset` observation and coordinate family contracts;
- `with_dimensions(...)`, `with_time_axis(...)`, `aggregate()`, `rollup(...)`,
  and `metric(...)` coordinate and arity transitions;
- scalar, Dimension, time, and Dimension-by-time shapes;
- Metric, Dimension, Entity, scope, and coordinate compatibility;
- logical recomputation versus materialized reaggregation admission;
- family-owned Population and Metric quality, validation, Evidence-extraction,
  zero-Finding, and retained-state registrations consumed by materialization;
- structured repair for every locally detectable observation-model conflict.

It does not own:

- public Dataset state, state-specific actions, or generic row-contract and
  row-set-contract mechanics;
- private logical node schemas, join order, relational lowering, or engine
  placement;
- datasource capability negotiation or local-stage limits;
- durable sampling realizations, storage receipts, Runs, Artifacts, Evidence,
  Findings, quality publication, or recovery;
- correlate, compare, discovery, forecasting, or inferential test methods;
- the exact filterable generated-field inventory of Candidate, Event,
  Lifecycle, and compact analytical Dataset families;
- Event or Lifecycle subject matching;
- Event/Lifecycle subject-selection predicates and completeness proofs.

Those modules consume the exact logical authority defined here. They may not
reinterpret Population membership or turn aggregation coordinates into a new
Population kind.

## Upstream Invariants

The observation model accepts these Dataset Core decisions as fixed:

1. `PopulationDataset` is the sole Entity-membership Dataset family;
   `MetricDataset` is the governed observation family;
2. construction is lazy, immutable, Session-owned, and performs no datasource
   work;
3. every output row contract and row-set contract is complete before execution,
   and the public schema is the row contract's canonical field inventory;
4. every analytical family has paired logical and materialized public state
   types with the same family id, row contract, and row-set contract;
5. a materialized Dataset is an immutable scan leaf and cannot be transparently
   rewritten through to its original semantic sources;
6. `definition_fingerprint` identifies a normalized definition, not realized
   rows or membership;
7. arbitrary pandas, SQL, Ibis, expressions, and physical columns cannot enter
   typed analysis;
8. every operator accepts the state variants admitted by its family, both
   logical and materialized inputs remain composable, and every operator
   returns a new Logical Dataset;
9. no eager aliases, dual paths, migrations, or logical recovery path survive
   the cutover.

The semantic model remains authoritative for:

- exact Entity refs, ordered identity primary keys, and separate versioning;
- Metric root Entity and recursive component bindings;
- Dimension and time-Dimension Entity bindings;
- Relationship keys and version rules, and Metric-specific contribution paths;
- Metric aggregation, additivity, temporal fold, status-time axis, unit, and
  component graph;
- current-catalog normalization and readiness.

The [Semantic Object Model](../../specs/semantic/semantic-object-model.md)
owns these intrinsic definitions. This module combines them with the selected
Population, coordinates, contributions, and retained input state to derive
operator admission. Neither layer invents a business allocation or an author
`rollup_safe` flag. Storage schemas for retained state remain family-owned
materialization contracts, not semantic authoring parameters.

## Decision Summary

### Population means governed Entity membership

A Population is the governed set of eligible instances of exactly one Entity.
It is not a scalar, segment, time series, panel, result shape, or statistical
sample.

Its logical identity binds:

- one exact Entity ref;
- that Entity's non-empty ordered primary-key signature;
- one reference scope and its selected time axis, when present;
- ordered normalized membership predicates;
- exact or explicitly approximate sampling intent;
- unsampled target-Population lineage;
- semantic, datasource, and Session authority.

The Population definition exists before execution. It does not claim that its
members have been enumerated or that two executions will see the same source
snapshot.

### Infer one exact default Entity or fail closed

`session.observe(...)` infers a default Population only when every normalized
Metric root resolves to the same exact governed analysis Entity and that Entity
admits unscoped membership under the rules below. It never
chooses an Entity because it is physically nearby, appears first, has a short
join path, or is cheaper to query.

An explicit Population may deliberately select another Entity only when every
Metric has one unique governed, fanout-safe semantic path to that Entity. The
explicit Entity resolves the analytical question; it does not resolve an
ambiguous Relationship path or authorize unsafe aggregation.

### Separate membership, observation scope, and output coordinates

Population answers which Entity identities are eligible and why. Its reference
scope applies only to member selection. It never supplies or clips a downstream
Metric observation window.

`session.observe(..., time_scope=..., time_dimension=...)` owns the Metric
observation scope and reference axis, with or without an explicit `population=`.
Coordinates position contributions within that observation scope. `aggregate()`
removes the Entity coordinate while preserving membership provenance and the
exact selected-contribution authority.

A January registration Population may therefore be observed over February
payments. The windows may be disjoint; that alone is not a compatibility error.
An omitted observation scope means all facts admitted by the Metric semantics,
not an inherited cohort window. Operators never invent a window from lineage.

### One membership family across analytical domains

`PopulationDataset` is the only public membership family. Explicit Entity roots
and Event/Lifecycle `select_subjects(...)` are different producers of the same
`population/entity-membership@v1` row contract. Source-owned selection predicates,
coverage proofs, and selection time stay in the producing definition and lineage.
There is no `SubjectSet` family, class, shape, or compatibility alias.

Module 2 owns Population rows, filtering, sampling, and source-input admission.
Module 6 owns the meaning and completeness requirements of Event/Lifecycle
selection producers. Every published selection Population has complete member
truth; subsequent membership filtering cannot repair an uncertain source selection.

### Observe one shared Population spine

`session.observe(...)` produces one logical Entity-grained relation:

```text
one Entity identity tuple + one ordered value per Metric
```

All Metric branches bind to the same Population spine. Metric-specific filters
or missing facts produce nullable Metric values; they do not silently remove a
Population member. Multi-Metric observation is the canonical form, not a
convenience implemented as independently observed and later aligned frames.

### Coordinates are declarations; aggregation is semantic recomputation

`with_dimensions(...)` and `with_time_axis(...)` add governed coordinates while
retaining the Entity coordinate. `aggregate()` removes the Entity coordinate
exactly once. Their names deliberately describe coordinate enrichment rather
than grouping or reduction. `group_by(...)` and `by_time(...)` are not public
names or compatibility aliases in the clean cutover.

For a logical Dataset, aggregation is recomputed from the governed Metric graph
at the requested coordinates over the exact contributions selected by its
upstream row operators. It is not a blind reduction over already projected
Entity values or an unrestricted replay of every original Population member. Ratio, weighted mean, count-distinct, percentile,
cumulative, and other non-additive semantics retain their owning component or
distribution contracts.

For a materialized scan leaf, recomputation through the original Metric graph
is forbidden. Aggregation is admitted only when the retained rows and the
Metric's exact materialized-fold contract are sufficient. Otherwise the repair
is to add coordinates and aggregate before materialization.

### Sampling is explicit and belongs to Population construction

Exact membership is the default. `mv.engine_sample(...)` is the only initial
approximate Entity sampling policy. It authorizes an engine-chosen Entity-safe
sample target; it does not promise an exact row count, strict uniformity, or
cross-backend reproducibility.

Sampling is applied after all Population predicates and before Metric joins or
aggregation. Marivo never inserts sampling to make an action fit a limit.

### Use one filter protocol with family-specific effects

All admitted row-bearing Dataset families use one public filtering spelling:

```python
filtered = dataset.where(predicate)
```

They share one typed predicate algebra, field-selection contract, lazy
construction lifecycle, definition-identity rule, and planner equivalence
boundary. They do not share one vague semantic effect.

Filtering a `PopulationDataset` changes Entity membership because one
Population row is one member. Filtering any other Dataset initially narrows its
current rows and preserves its existing Population or subject authority. An
eligible identity-bearing result becomes membership input only when the caller
passes that exact Dataset through a later source's `population=` argument; the
filter itself never changes nominal family or authority.

## Unified Analysis Filtering Contract

### Public `where(...)` operation

Every Dataset family that registers row filtering exposes:

```python
def where(
    self: LogicalDataset | MaterializedDataset,
    *predicates: AnalysisPredicate,
) -> LogicalDataset:
    ...
```

At least one predicate is required. Multiple top-level predicates are combined
with logical AND in authored order. The operation performs deterministic local
normalization and admission only, then returns the Logical Dataset paired with
the input's analytical family.

`where(...)` never mutates its input, executes a query, collects rows, creates a
Run, publishes an Artifact, returns a boolean mask, or changes Dataset family.
When the input is materialized, the new logical Dataset consumes its immutable
Artifact as a scan leaf.

Family registration declares:

```text
FilterAdmissionV1
  effect
  admitted_shapes[]
  admitted_field_roles[]
  admitted_predicate_kinds[]
  field_rules[]
```

Filtering preserves the owning family and qualified shape while producing a
new current-row definition. Its downstream continuations are derived by
matching that resulting Dataset contract against source and operator consumer
admission contracts; `FilterAdmissionV1` does not copy a continuation list.

Each field rule is a closed tuple within the filter admission contract:

```text
FilterFieldRuleV1
  input_state
  field_resolution
  admitted_field_roles[]
```

The initial `input_state` values are `logical` and `materialized`.
`field_resolution` is either `retained_row` or `reachable_semantic`; the latter
is available only to the Population specialization defined below. These facts
already determine the required input nodes; no additional authority mode exists.

| Input state | Field resolution | Calculation | Admission |
| --- | --- | --- | --- |
| logical | retained current-row field | filter the admitted logical input | registered families and shapes |
| logical | reachable semantic field absent from rows | add the exact admitted Dimension path | Population only |
| materialized | retained current-row field | scan and filter immutable rows | registered families and shapes |
| materialized | reachable semantic field absent from rows | join the exact admitted Dimension path to retained identities | Population only |

Family modules may narrow this matrix by shape or field role. They cannot add
implicit enrichment or source replay. `dataset.contract()` projects the exact
field rule and concrete requirements for the current Dataset and operand;
field role alone never determines a legal calculation.

The initial `effect` vocabulary is:

```text
membership
row_subset
```

`PopulationDataset` uses `membership`. Ordinary Metric, Delta,
Association, Candidate, Event, Lifecycle, and other analytical result families
use `row_subset`. Event/Lifecycle-selected Populations use the same membership filter contract.
A family that has no sound filter semantics does not register `where(...)`.

### Closed predicate vocabulary

`AnalysisPredicate` is a sealed immutable public value produced only by focused
helpers:

```text
comparison: eq, not_eq, lt, lte, gt, gte
membership: is_in
null:       is_null, is_not_null
boolean:    all_of, any_of, not_
```

Conceptually:

```python
def eq(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate: ...
def not_eq(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate: ...
def lt(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate: ...
def lte(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate: ...
def gt(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate: ...
def gte(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate: ...
def is_in(
    field: PredicateField,
    values: list[PredicateLiteral] | tuple[PredicateLiteral, ...],
) -> AnalysisPredicate: ...
def is_null(field: PredicateField) -> AnalysisPredicate: ...
def is_not_null(field: PredicateField) -> AnalysisPredicate: ...
def all_of(*predicates: AnalysisPredicate) -> AnalysisPredicate: ...
def any_of(*predicates: AnalysisPredicate) -> AnalysisPredicate: ...
def not_(predicate: AnalysisPredicate) -> AnalysisPredicate: ...
```

The helpers produce immutable unbound authoring predicates. They validate the
closed operand kind, literal Python class, container shape, boolean arity, and
null syntax, but they do not consult a Dataset or catalog and do not choose a
field-dependent numeric or temporal coercion. This is required because an exact
semantic `Ref` carries identity and kind, not the consuming Dataset's retained
logical type binding.

`dataset.where(...)` resolves every operand against its exact input and creates
one private immutable bound tree:

```text
BoundAnalysisPredicateV1
  resolved_field_binding
  predicate_logical_type_class
  canonical_operator
  canonical_typed_literals[]
  canonical_children[]
  authored_occurrence_paths[]
```

The public `AnalysisPredicate` is not mutated or made Session-owned and may be
reused as an authoring value. Dataset definition identity and private in-process
planning bind the exact bound tree, never the unresolved public operand alone.
Materialization persists its definition fingerprint and bounded redacted
predicate projection, not an executable predicate or raw literal payload.
`BoundAnalysisPredicateV1` has no public constructor, export, Help target, or
standalone recovery path.

The first-cutover `PredicateLiteral` union is:

```text
str | bool | int | finite float | Decimal | date | datetime
```

`bool` is not accepted where an integer is expected despite Python subclassing.
NaN, positive/negative infinity, bytes, UUID-like arbitrary objects, and
backend-native scalar wrappers are rejected rather than coerced. Supporting a
new semantic literal class requires one shared predicate-contract amendment,
not a family-local parser.

### Logical type and literal compatibility

Every predicate operand resolves one exact `PredicateLogicalTypeClassV1` while
the helper-produced value is admitted by `where(...)`. A direct semantic ref
obtains the class from its retained row binding or current semantic contract; a
`DatasetFieldRef` carries it from the row contract. Deferred physical dtype does
not mean deferred logical type class.

The first-cutover compatibility matrix is:

| Field logical type class | Admitted literal | `eq` / `not_eq` / `is_in` | ordering comparisons | Canonical normalization |
| --- | --- | --- | --- | --- |
| boolean | exact `bool` | yes | no | exact boolean |
| integer | exact `int`, excluding `bool` | yes | yes | arbitrary-precision signed integer |
| decimal | `Decimal` or exact `int` | yes | yes | integer is converted exactly to normalized decimal; float is rejected |
| floating | finite `float` or an `int` exactly representable by the field's governed floating class | yes | yes | normalized finite floating payload; lossy integer conversion is rejected |
| string | exact `str` | yes | only with one governed ordering/collation identity | exact Unicode scalar sequence plus ordering identity when used |
| civil date | exact `date`, excluding `datetime` | yes | yes | ISO civil date |
| instant or localizable datetime | timezone-aware `datetime` | yes | yes | UTC instant; localizable fields additionally bind their exact read-timezone authority |

`is_null` and `is_not_null` are admitted for any registered nullable field and
take no literal. A non-nullable field rejects them locally because their result
is constant and the caller should remove the condition. A naive `datetime`, a
string pretending to be a date or number, a `float` for a decimal field, a
`datetime` for a civil-date field, and an unordered string comparison without a
governed ordering contract all fail during Dataset construction with a typed
repair. There is no backend-selected coercion.

For a localizable datetime field, the semantic contract must already identify
the read timezone needed to land the canonical instant. If that authority is
not locally complete, the predicate is not construction-admissible; action time
does not choose an engine timezone. Backend checks may still reject an admitted
logical comparison when the exact physical operation is unsupported, but they
may not widen, parse, recollate, or otherwise change the canonical comparison.

Every member of `is_in` is normalized independently during binding through the
same row of the matrix, then the values are deduplicated and ordered by typed
identity. Mixed logical literal classes are rejected even when a backend would
coerce them.

`PredicateField` is the closed union of:

- a `DatasetFieldRef` resolved from the current Dataset row contract;
- an exact typed Metric, Dimension, or time-Dimension ref, or a concrete current
  catalog entry, when the consuming family's admission contract can resolve
  that semantic identity.

When the field already exists in the current row contract, an exact typed ref
must resolve to exactly one retained binding without requiring current catalog
membership. If the same semantic identity occurs in more than one current
binding, the ref is ambiguous and the caller must pass the exact
`DatasetFieldRef`. A concrete catalog entry must belong to the current
Dataset-owning Session catalog. When Population filtering introduces a
reachable membership-stable Dimension absent from its public rows, both the ref
and path must resolve through current semantic authority.

A recovered runtime-Metric binding has no live `RuntimeMetricExpr` object and no
catalog ref. Its exact selector is reacquired from the current Dataset through
`dataset.fields.get(field_id)`, using the stable id exposed by the retained
row-contract schema. `get(name)` resolves an exact current public field name,
including generated family/operator fields, but does not parse semantic paths or
typed keys.

For example:

```python
high_cpu = features.where(
    mv.gt(cpu_seconds, 60),
)

regional = grouped.where(
    mv.eq(region, "APAC"),
)

candidate = candidates.where(
    mv.eq(candidates.fields.get("item_id"), "candidate_123"),
)
```

Direct semantic refs keep governed Metric and Dimension filtering concise.
`dataset.fields.get(...)` is used for family-generated fields such as
Candidate `item_id`, score, reason code, Event step, lag, statistic, or status
that have no catalog identity. It resolves to the selector-only
`DatasetFieldRef` owned by Dataset Core, not a public Series or expression.

Predicate helpers accept typed scalar literals only. They reject callables,
pandas/NumPy containers, Datasets, Ibis expressions, SQL fragments, backend
objects, and arbitrary iterables. `is_in` requires a non-empty list or tuple of
values that normalize to one compatible logical literal class. Set input is
rejected because its authored order is nondeterministic.

Direct `AnalysisPredicate(...)` construction and generic model validation are
not public authoring paths. Private in-process decoding uses the exact bound-tree
schema; materialized Dataset recovery restores the immutable Artifact input and
redacted lineage rather than an executable or reusable predicate value.

`repr(predicate)` is bounded and never renders raw literal payloads. The public
unbound value may disclose operand identity, predicate kind, authored literal
class, and boolean shape. Safe Dataset lineage, Run arguments, errors, and
contracts project the resolved field identity, predicate logical type,
canonical boolean shape, and bounded redacted values only. Definition identity
still binds the exact bound canonical typed literal payload.

### Boolean and null semantics

`all_of(...)` and `any_of(...)` require at least two predicates. `not_(...)`
accepts exactly one. Helpers normalize one structural authoring tree;
`where(...)` produces the canonical bound tree while retaining authored
occurrence paths for errors.

Structural helper normalization flattens nested identical combinators and
preserves authored occurrence paths. Dataset binding removes exact duplicate
children, orders commutative children by stable bound-child digest, and
canonicalizes `is_in` values by normalized typed identity. It does not
distribute AND over OR, push `not_` through null checks, or perform algebraic
rewrites whose equivalence depends on backend coercion or three-valued logic.

Boolean combinators follow registered SQL three-valued logic. Null handling is
never inferred from Python truthiness:

- `eq(field, None)` and `not_eq(field, None)` are rejected with repairs to
  `is_null(field)` and `is_not_null(field)`;
- ordering comparisons reject null literals;
- `is_in` rejects null members in the first cutover; callers use `any_of` with
  `is_null` when null is intended;
- a predicate result of unknown does not pass the filter.

Literal normalization and logical compatibility are completed without
datasource work under the matrix above. Only exact physical capability and
realized-dtype conformance remain action-time requirements visible in
`contract()`; a backend may not supply missing semantic coercion.

### No Python comparison or kwargs shortcut

Dataset fields and semantic refs do not overload Python comparison or boolean
operators to build predicates. These forms are invalid:

```python
features.where(cpu_seconds > 60)
candidates.where(item_id="candidate_123")
features.where("cpu_seconds > 60")
features.where(lambda row: row.cpu_seconds > 60)
```

Python `==` may already mean object or semantic-ref identity and can return a
plain `bool`; `and`, `or`, and `not` also evaluate eagerly. A kwargs shortcut
would support equality only, make generated names look more authoritative than
semantic refs, and create a second predicate grammar. The focused `mv.*`
builders are the one public construction path.

### Family-specific effect matrix

The shared operation has these initial effects:

| Input family/stage | Filter effect | Authority preserved | Population-input consequence |
| --- | --- | --- | --- |
| `PopulationDataset` | narrow eligible Entity membership | unsampled target-Population lineage | directly admitted through `population=` |
| registered Entity-present `MetricDataset` with unique Entity identity | select current Entity observation rows | exact target Entity, identity signature, original Population lineage | directly admitted through `population=` by exact identity projection |
| coordinate-bearing reduced `MetricDataset` / `DeltaDataset` | select aggregate coordinate rows | contributors and Population authority of each retained row | not admitted; aggregate coordinates are not members |
| `CandidateDataset[candidate/entity-outlier@v1]` | shortlist exact Entity candidate rows | source Dataset, Entity identity, score, and reason authority | directly admitted through `population=` by exact identity projection |
| other `CandidateDataset` shapes | shortlist candidate rows | source Dataset, score, and reason authority | not admitted; their current rows own no Entity membership coordinate |
| registered row-summary `EventDataset` shapes | select complete matched Event result rows | original pattern, subject, and matching authority | Module 6-owned typed bridge, if that exact shape admits one |
| registered row-summary `LifecycleDataset` shapes | select complete replay/state result rows | original model, subject, and replay authority | Module 6-owned typed bridge, if that exact shape admits one |
| compact analytical Dataset | select result rows only | method, inputs, Population, and approximation authority | none unless its family registers one |

Filtering never upgrades a Candidate into an accepted hypothesis, a Metric row
selection into Population authority, an Event row into a rewritten pattern, or
a Lifecycle row into a modified StateModel. Direct `population=` use is a new
source definition that consumes the exact selected rows; it is not a mutation
of the filtered Dataset.

Event or Lifecycle shapes whose rows are structural steps, transitions, or
partial histories may reject `where(...)` when dropping one row would corrupt
the family invariant. Module 6 owns that exact shape matrix; it consumes this
shared syntax and effect vocabulary.

### Current-row and reachable-membership fields

For every non-Population family, a predicate may reference only fields present
in the input Dataset's current row contract. A field added by a later operator,
dropped by projection or aggregation, hidden in lineage, or present only in the
original semantic source is not filterable at that point.

`PopulationDataset` has one deliberate specialization: its membership predicate
may reference an exact governed non-time Dimension reachable from the
Population Entity even though Population rows publicly expose only
`entity_identity`. The Dimension is admitted only when the current semantic
contract proves at most one value per Entity through a unique direct,
one-to-one, or many-to-one path and the field is membership-stable. The
predicate asks whether the current logical Entity member satisfies that exact
snapshot condition; it does not project the field into Population rows.

`membership-stable` is an analysis admission fact derived from existing
semantic contracts, not a new semantic authoring flag. It requires a categorical
non-time Dimension, no snapshot or validity versioning on its owning Entity or
any Entity traversed by the path, and a functional-dependency proof that the
join cannot yield more than one value for one Population identity. Missing or
nullable values remain one SQL-null input to the common null rules.

A time Dimension, validity-interval or slowly changing Dimension, historical
state, one-to-many collection, or field whose value depends on an evaluation
instant/window is not a first-cutover Population predicate. `time_scope` plus
`time_dimension` owns temporal eligibility. A data-derived temporal or
collection condition is expressed on an observed row-bearing Dataset. It may
constrain later membership only when the resulting exact identity-bearing shape
is explicitly passed through `population=`.
Logical execution may read changed source rows on a later unbound action, but it
never chooses between start, end, any-time, or all-time membership semantics.

This exception does not widen other families into semantic-source query
builders. A materialized Metric Dataset cannot filter by an absent source
Dimension merely because lineage once mentioned it.

### Operator order is semantic

The authored position of `where(...)` participates in Dataset definition
identity. These chains are not interchangeable:

```text
population.where(...).sample(...)
population.sample(...).where(...)

features.where(...).aggregate()
features.with_dimensions(...).aggregate().where(...)

selected_features = features.where(...)
session.observe(..., population=selected_features)
```

On an Entity-axis Metric Dataset, a value predicate selects observations before
Entity-axis reduction. A following `aggregate()` aggregates only the selected
observation rows but retains the original Population only as target/selection provenance,
plus the exact selected-contribution binding. It does not retain an original
computational denominator implicitly. On an already reduced,
coordinate-bearing Dataset, the same predicate shape filters completed
aggregate rows and cannot change their contributors.

The exact singleton `metric/scalar@v1` shape does not admit `where(...)` in the
first cutover because filtering could turn its mandatory one row into zero rows.
A caller filters before scalar aggregation or uses a registered scalar decision
operator; Marivo does not silently weaken singleton cardinality.

The planner may push a predicate toward a scan or lower it as `WHERE`, `EXISTS`,
semi-join, or `HAVING` only when it proves exact equivalence under the family
effect, null, aggregation, Relationship, fanout, and authority contracts. It
must not reorder a predicate across:

- Population sampling;
- Entity-axis reduction or another non-commuting aggregation;
- materialization;
- a `population=` identity-projection boundary or another family bridge;
- Event matching;
- Lifecycle replay;
- component composition or cumulative evaluation.

Source Ibis composition and any exact pandas continuation preserve the public
operator order, filter effect and lineage. A local row-subset filter is admitted
only over the exact rows/state its registered contract permits; it never pulls
Population refinement or another source-owned semantic operation into pandas.

### Filtering input resolution

A predicate over fields present in a logical Dataset row contract
evaluates as part of that exact logical input definition
under the admitted current semantic definitions and source execution configuration.

For a predicate over fields present in a materialized Dataset row contract,
the new logical Dataset scans and filters the immutable
Artifact without requiring original semantic sources. An exact typed ref may
match its retained field identity after catalog drift; a current catalog entry
is not required. `dataset.fields.get(...)` remains the canonical selector
for retained generated fields.

A Population membership predicate that introduces one admitted current
membership-stable Dimension starts from the immutable
identity leaf, joins only the unique single-valued atemporal semantic path, and
never re-evaluates the Population's original scope, predicates, or sampling.
Current-authority drift, temporal/versioned behavior, cardinality change, or an
unavailable path fails explicitly.

No other materialized Dataset family may regain an absent semantic field
through lineage. The repair is to add and retain the required coordinate before
materialization or reconstruct the logical source chain.

### Semantic authoring remains separate

`ms.where(...)` remains a semantic-authoring helper for reusable Metric
definitions. It changes the governed Metric graph and applies whenever that
Metric is observed.

`dataset.where(...)` is an analysis operator over one current Dataset chain. It
changes that Dataset definition only. The two surfaces may share internal
literal/type utilities, but they do not share public constructors, accepted
field roles, Help routes, persistence identity, or repair ownership.

Analysis filtering never promotes an exploratory predicate into a semantic
model change.

## Public Population Contract

### `session.population(...)`

The canonical explicit Population source is:

```python
def population(
    self,
    entity: SemanticInput[EntityKind],
    *,
    time_scope: TimeScope | None = None,
    time_dimension: SemanticInput[TimeDimensionKind] | None = None,
) -> LogicalPopulationDataset:
    ...
```

`SemanticInput[K]` retains its existing current-catalog contract: an exact
same-kind ref or a concrete entry owned by the current Session catalog. Bare
strings, stale or cross-catalog entries, wrong-kind refs, arbitrary subclasses,
and duck-typed values fail during local construction.

The Entity must have one non-empty, ordered compiled identity primary-key
signature `K`. Its fields identify one Entity instance across versions. A
snapshot partition or validity boundary is not appended to `K` merely because
it distinguishes source versions. A source-only Entity without an identity key
cannot produce a Population. Marivo never asks the caller to repeat key fields
or substitutes a Dimension for missing Entity identity.

`time_scope` is an optional literal half-open interval `[start, end)`. Its end
is excluded exactly as authored. The scope restricts eligible membership only.
It does not constrain downstream Metric facts or add a time coordinate.

`time_dimension` is legal only with `time_scope`. It selects the exact governed
reference axis used to apply that scope. If omitted, construction uses this
closed order:

1. the Entity's exact declared default time Dimension;
2. the only compatible reachable time Dimension, if exactly one exists;
3. otherwise a structured ambiguity or missing-axis failure.

Marivo does not select the first Metric's time axis or infer temporal meaning
from a physical type or column name.

### Versioned membership resolution

The first-cutover `population(...)` contract requires a finite `time_scope` for
a versioned Entity. With no scope, construction fails and points to an explicit
scoped Population; it never scans all versions and silently takes distinct
identities or chooses an ambient latest snapshot. No new selection-policy
constructor or implicit inheritance from a later `observe(...)` is introduced.

For this source, the membership state is resolved immediately before the
excluded `time_scope.end`, then the selected membership time-axis predicate is
applied over `[start, end)`. This fixed endpoint-view meaning is not an
ever-present, any-time, or all-time membership query. The selected
`time_dimension` still determines the membership predicate; it does not replace
the Entity's declared version axis. The membership endpoint and its
left-boundary interpretation are normalized definition facts, not a subtraction
of one arbitrary machine timestamp tick.

- Snapshot resolution chooses the single semantic snapshot period containing
  that left-limit endpoint. An endpoint exactly on a period boundary therefore
  selects the preceding period. The expected partition must be available under
  the Semantic Object Model's complete-snapshot contract; no prior-partition
  fallback or per-Entity last-known lookup is allowed.
- Validity resolution selects intervals containing that left-limit endpoint.
  For `[valid_from, valid_to)`, the condition is
  `valid_from < end <= valid_to`, with the declared open-end convention. Other
  admitted interval closure follows its exact semantic boundary rule.
- Source version-row uniqueness is validated at `(K, snapshot_version)` or
  `(K, valid_from)` before identity projection. Validity overlap and conflicting
  versions fail. After temporal resolution, at most one source representation
  may remain per `K`; `DISTINCT K` cannot repair duplicate or conflicting rows.

Every branch uses the same resolved membership representation and validation
facts within the action. Population rows carry only `K`; selection/version
coordinates and completeness facts remain in the producing definition and
committed provenance. A declared complete-snapshot meaning does not prove
actual availability or coverage: missing required source evidence fails the
source action. An explicit empty complete snapshot can produce zero members.

Consumers of that Population resolve Metric facts and historical enrichment
using their own observation or domain anchors. January snapshot membership can
therefore feed February observation without forcing February attributes to use
the January version. Materialized membership uses its exact committed `K` rows
and never reselects a source version. Event/Lifecycle selection producers retain
their own exact temporal and completeness rules instead of acquiring this
Entity-source endpoint convention.

### Population row semantics

`PopulationDataset` has one initial family-qualified shape:

```text
population/entity-membership@v1
```

Its family-owned row-semantics payload contains only the facts required to
interpret the identity field:

```text
DatasetFamilyRowSemantics.complete_from_schema
```

This variant has no additional payload fields: the canonical
`entity_identity` binding already owns the exact Entity ref, identity signature,
logical type, and nullability.

The accepted Slice 2a Core amendment represents those identity facts with
`DatasetFieldIdentity.entity_identity(entity_ref, identity_signature)`. The
ordered signature contains primary-key field names and component logical-type
ids, including the single component of a one-column key. Population and Metric
identity coordinates use this same descriptor; they do not hide the signature
in private graph metadata or repeat it in Population family semantics.

Reference scope, reference-time identity, normalized predicates, sampling, and
target-Population definition identity remain in the normalized Dataset
definition and bounded lineage. They do not change the meaning or type of one
`entity_identity` row and are not duplicated in row semantics. `sampling` is a
closed exact-or-approximate logical request, not an execution receipt.

Every Population operator returns a new logical `PopulationDataset`, including
when its input is materialized. In that case the immutable membership Artifact
is a private scan-leaf input to the new definition; the operator does not mutate
the Artifact-backed value or preserve materialized state on its output.

`target_population_definition_fingerprint` is absent on an exact unsampled
root. On a sampled Population it identifies the exact unsampled input
definition. Repeated downstream observation retains that target lineage. The
field never contains realized identities or a membership digest.

### Primary-key row contract

One Population row is one exact Entity identity primary-key tuple. Source
version-row keys and analytical keys such as `(K, time)` are separate from this
membership identity. The same `K` in several historical versions is one Entity,
not several members or independent statistical units.

The public coordinate column is:

```text
entity_identity
```

The shared registered field id is `identity.entity_identity@v1`. Every
Population producer and admitted Metric/Candidate identity projection uses this
role and the exact Entity/signature binding; no domain-specific identity alias
is introduced.

Its logical type is a fixed-arity tuple whose components follow the compiled
primary-key signature in declaration order. A one-column primary key still
uses a one-element tuple. Scalar-versus-tuple dual layouts are forbidden.

The row and row-set contracts require:

- `entity_identity` is non-null;
- every component is non-null and conforms to its governed logical type;
- rows are unique by the complete tuple;
- row-set cardinality is `keyed(unknown)`;
- default analytical row-set ordering is unordered;
- canonical presentation ordering uses the tuple's registered component order.

Composite components are not exploded into independently joinable public
columns. This prevents partial-key membership and keeps `PopulationDataset`, multi-Metric observation, and downstream Entity alignment bound
to one exact identity signature.

Raw identities may appear only in Dataset rows and at explicit terminal reads.
They do not appear in `repr`, `contract()`, errors, lineage summaries, Run
arguments, Evidence subjects, or graph cards.

### Population filtering specialization

Population filtering is a lazy Dataset operation:

```python
eligible = population.where(
    mv.eq(workload_type, "interactive"),
    mv.is_in(status, ["succeeded", "failed"]),
)
```

This is the shared `Dataset.where(...)` operation with
`FilterAdmissionV1.effect = membership`. Its predicates use the common
`AnalysisPredicate` vocabulary. A direct semantic field operand must be one
exact current non-time, single-valued, non-versioned, atemporal Dimension under
current semantic authority. Those terms mean the derived `membership-stable`
admission above; they do not add a semantic authoring flag. Generated Dataset
fields are not Population membership inputs in the first cutover.

Construction validates kind, current-catalog ownership, exact logical
type/literal compatibility, duplicate operands, semantic reachability, path
cardinality, and temporal stability without datasource work. Exact physical
capability and realized-dtype conformance remain action-time requirements
visible in `contract()`.

A predicate on a directly bound Entity Dimension uses ordinary row filtering. A
unique one-to-one or many-to-one path may be joined only when its current
semantic contract proves at most one atemporal value per Entity. An ambiguous,
unreachable, one-to-many, versioned, time-bearing, or otherwise non-single-valued
path fails rather than choosing a Relationship or inventing `EXISTS`, `as_of`,
or window semantics.

Predicates preserve authored occurrence paths for diagnostics but normalize one
canonical semantic tree for definition identity. Provably contradictory
predicates fail locally. Row-dependent emptiness is valid and produces an empty
Population.

Sampling is the final membership-selection transform. Calling `where(...)` on
a Population that already carries a sampling request is rejected with a repair
that moves predicates before `.sample(...)`. This prevents the accidental
change from "sample eligible members" to "filter an already sampled set."

### Population sampling

The public operation is:

```python
sampled = population.sample(
    mv.engine_sample(target_rows=100_000, seed=42),
)
```

The closed policy is conceptually:

```python
class EntitySamplingPolicy:
    target_rows: int
    seed: int | None

def engine_sample(
    *,
    target_rows: int,
    seed: int | None = None,
) -> EntitySamplingPolicy:
    ...
```

`target_rows` must be a positive exact integer. Booleans, floats, strings,
zero, and negative values are rejected without coercion. `seed` is an exact
integer request when present.

`EntitySamplingPolicy` is immutable and helper-produced. Direct model
construction, generic deserialization, and user-selected physical strategies
are not public authoring paths. The caller cannot select row, block, system,
hash, or local sampling.

The policy means:

- sample the target Entity identity set, never a finer fact table;
- apply the same selected identities to every Metric branch;
- treat `target_rows` as a planning target, not exact realized cardinality;
- request seeded behavior where an admitted engine can honor it;
- fail if the sample cannot be placed without changing the Entity unit;
- disclose approximation requirements before execution and realization facts
  only after the owning action.

Calling `.sample(...)` twice is rejected. Resampling has no first-cutover
meaning because it is ambiguous whether the second policy targets the original
Population or the first realized sample. A caller constructs a new branch from
the unsampled Population instead.

Sampling preserves the `PopulationDataset` family, row contract, and
`keyed(unknown)` row-set contract but changes the definition fingerprint and
adds target-Population lineage.

Each authored sampling call also establishes one private realization handle.
All branches that consume that sampled Population retain the same handle and
must consume its one action-scoped sample. Two calls on the unsampled Population
establish distinct realizations even when their requests, including any seed,
and standalone definition fingerprints match. Equal parameters do not authorize
merging those realizations, and separate realizations may still produce equal
members. Dataset Core normalizes their sharing relation when composing the
complete logical definition; raw handles and realized membership never enter
the fingerprint.

## Default Population Inference

### Metric computation roots and analysis Entity resolution

A Metric computation root identifies its governed fact grain and join authority.
An analysis Entity identifies one observational unit. Output Dimension/time
coordinates identify reporting grain. These are separate facts; selecting an
analysis Entity never rewrites a Metric's compiled computation root.

Normalize the complete Metric graph before resolving observation authority:

1. With an explicit Population input, take its Entity as the analysis Entity.
   Validate every base/component occurrence against that Entity through its own
   unique governed path and exact contribution/aggregation contract. Different
   computation roots are legal when every branch can be evaluated safely at the
   shared analysis Entity and requested coordinates. Do not first demand that
   a derived Metric's leaves already share one computation root.
2. Without an explicit Population input, infer only when all governed base
   occurrences resolve to the same exact computation-root Entity. Runtime and
   cumulative roots use the same recursive rule. Otherwise require an explicit
   Population with real safe candidates or split the observations.
3. A missing computation root or unresolved semantic component fails locally.
   Graph proximity, display names, first-input order, and estimated cost never
   select an analysis Entity or Relationship path.

Each branch aggregates its governed facts before joining one-to-one to the
shared spine. A finer-grained revenue branch and an order-count branch may
therefore share an explicitly selected customer Population without multiplying
orders by order lines. Existence of a Relationship alone is insufficient:
allocation, temporal compatibility, and component composition must be proven.

Inference performs no datasource work and produces a private logical
`PopulationDataset` root. Its contract contributes to definition identity and
lineage. Later coordinates validate the already selected analysis Entity; they
never replace it or restart inference.

### Default membership and observation scope

When `population=` is omitted, construct the inferred non-versioned Entity's
all-eligible, exact, unscoped Population. A versioned inferred Entity instead
requires an explicit scoped Population under Versioned membership resolution;
observe-level `time_scope` is never borrowed to satisfy that requirement.
Observe-level `time_scope` and `time_dimension` apply only to Metric evaluation.
For an Entity with a registration axis, observing February spending does not
implicitly select February registrations.

When observation scope is absent, Metric evaluation is semantically unbounded
unless its own contract provides an exact bound. Construction can remain legal;
action admission may require an explicit window. Metric-local filters affect
only their contributions and never narrow shared membership.

A versioned Metric source or enrichment that requires a temporal boundary must
resolve it from that Metric's observation/evaluation contract. If none exists,
construction rejects the missing temporal authority; the selected Population's
membership endpoint is not an observation default. Historical source rows are
never joined as a unique Entity-side relation merely because `K` is stable.

Observation `time_dimension` requires `time_scope`. An omitted reference axis
resolves from the governed Metric graph: use its unique compatible declared
default, otherwise its sole compatible reference axis, otherwise fail with exact
candidates. Do not borrow the Population membership axis or arbitrarily choose
the first Metric's axis. Distinct branch axes require an existing exact governed
temporal alignment contract; equal physical types or names are not proof.

### Logical Entity coordinates are not an execution prerequisite

The first cutover keeps one `session.observe(...)` spelling and the existing
lazy coordinate chain. It introduces no observation builder class or parallel
aggregate-source API. An Entity-present row contract is a compositional analysis
unit, not a requirement to enumerate, persist, transfer, or execute an Entity
feature table before a grouped report. The compiler may lower a complete safe
chain directly to the requested aggregate grain.

For Entity correlation/outliers, Entity is the actual sample unit. For trends,
comparison, and attribution, the caller declares reporting coordinates and
reduces Entity in the same lazy chain. Cross-root observations use explicit
membership authority; no independent result alignment is introduced.

### Inference failures

Default inference fails before Dataset construction when:

- a Metric has no exact analysis Entity;
- no explicit Population was supplied and component computation roots differ;
- ordered Metric roots resolve different Entities;
- a scoped observation has no compatible Metric reference time axis;
- more than one compatible reference time axis remains after defaults;
- semantic readiness cannot prove the required Entity or Relationship facts.

The error includes ordered Metric identities and their resolved Entity sets. A
mechanical repair may:

- construct an explicit `session.population(entity, ...)` when that Entity is
  known and each Metric has a unique admitted path to it;
- select an exact `time_dimension=` from real candidates;
- correct the Metric or Relationship declaration through semantic authoring;
- split unrelated Metrics into separate observations.

It never recommends an Entity merely because one candidate is first or
cheapest.

## Canonical Observation Contract

### Exact signature

The canonical Metric source is:

```python
def observe(
    self,
    metrics: MetricObservationInput
    | list[MetricObservationInput]
    | tuple[MetricObservationInput, ...],
    *,
    population: PopulationInput | None = None,
    time_scope: TimeScope | None = None,
    time_dimension: SemanticInput[TimeDimensionKind] | None = None,
) -> LogicalMetricDataset:
    ...
```

The initial closed `MetricObservationInput` union is:

```text
SemanticInput[MetricKind] | RuntimeMetricExpr
```

The initial closed `PopulationInput` union is:

```text
PopulationInput = (
    LogicalPopulationDataset
    | MaterializedPopulationDataset
    | LogicalMetricDataset
    | MaterializedMetricDataset
    | LogicalCandidateDataset
    | MaterializedCandidateDataset
)
```

`PopulationInput` is a closed annotation alias, not a constructible Dataset
family, conversion result, or top-level analysis export. The family names in
the Python union are narrowed by the exact shape matrix below. Passing another
shape of an otherwise listed family fails locally; the union is not permission
for runtime identity inference.

Catalog inputs use exact current-catalog normalization. A
`RuntimeMetricExpr` must be owned by the same Session semantic authority, have
one stable non-empty public label, resolve a complete governed Metric graph,
and satisfy the same Entity and aggregation contracts as a catalog Metric.

One Metric may be passed directly. A list or tuple must be non-empty, ordered,
duplicate-free, and within the registered arity bound. Sets, generators,
mappings, arbitrary iterables, strings, physical columns, callables, pandas
objects, Ibis expressions, and Datasets are rejected.

The signature deliberately removes eager-observe concerns:

- no `grain=`; use `.with_time_axis(..., grain=...)`;
- no `dimensions=`; use `.with_dimensions(...)`;
- no `slice_by=`; filter Population membership before observation, or declare
  the required coordinates and use `MetricDataset.where(...)` for current rows;
- no `cohort=`; pass one exact `PopulationInput` through `population=`; its
  selection scope is independent of observe-level `time_scope`/`time_dimension`;
- no `expect_shape=`; the row contract is already available locally;
- no `analysis_purpose=`; Session question and action records own execution
  purpose rather than changing a logical Dataset definition.

There is no Dataset-owned `observe(...)` alias. Session remains the sole Metric
source owner.

### Parameterized source bindings are captured at construction

The retained public authoring path for parameterized non-secret JSON source
values is:

```python
with session.source_bindings(
    {
        ms.ref.entity("monitoring.samples"): {
            "start": 1,
            "end": 100,
        },
    }
):
    logical = session.observe(metrics=[sample_value])

materialized = logical.execute()
```

`Session.source_bindings(...)` remains a context manager, but its authority is
construction-scoped rather than execution-scoped. Every Session-owned source
constructed inside the scope captures the exact bindings required by its
reachable parameterized Entities into its immutable private source definition.
The resulting Logical Dataset may execute after the context manager exits.
`execute()` never consults a current `ContextVar`, mutable Session binding map,
process environment, or later nested binding scope.

The first-cutover value contract is closed:

```text
SourceBindingScalar = str | bool | int | finite float
SourceBindingValue = SourceBindingScalar |
                     non-empty flat list-or-tuple[SourceBindingScalar]

BoundSourceParametersV1
  entity_ref
  ordered_parameter_names[]
  private_canonical_typed_values[]
  exact_value_digest
  bounded_redacted_projection
```

Mapping keys normalize by exact current-catalog Entity ref and declared
parameter name. Sequence order is preserved because repeated parameter order
may affect a source request. Nested sequences, empty sequences, non-finite
floats, unsupported scalar classes, missing declared parameters, extra
parameters, non-Entity refs, non-JSON sources, and Entities without declared
source parameters fail before a Logical Dataset is returned.

`exact_value_digest` covers the binding schema version, exact Entity ref,
ordered parameter names, scalar type tags, and canonical typed values. It is
not a digest of untyped display strings.

A scope may contain bindings for several Entity refs so several source calls
can share one authoring block. Each source definition captures only the
bindings reachable from that source; unrelated entries do not change its
definition identity. An inner scope replaces the complete active binding map
for that Session until exit, then restores the outer map. There is no implicit
merge whose result depends on nesting order.

Observation stores normalized source arguments and captures in the Core-owned
closed immutable node-payload interface. Its safe identity projection contains
the exact semantic dependency and capture digests; Core computes the one Dataset
definition fingerprint. Downstream construction retains already captured inputs
and captures only newly reached sources. Traversal stops at Materialized scan
leaves: retained membership identity does not require recapturing the original
source bindings.

Exact canonical typed values participate in the source definition fingerprint
and the runtime execution key through `exact_value_digest`. The raw in-memory
values remain available to the private compiler adapter for a later
`execute()`, but Runs, cards, contracts, lineage, errors, Evidence, telemetry,
and persisted logical metadata may contain only the bounded redacted
projection. Internal definition/execution-key records may contain the opaque
digest but never the values. Credentials remain datasource-owned `*_env`
references; a secret-like parameter name or datasource-declared credential
slot is rejected rather than treated as an analysis source binding.

Reconstructing the same source definition in another process requires
supplying the same bindings again. In the same named Session, the equal
definition and execution key may recover its already committed Artifact
without sending the source request. A changed binding value is an explicit new
definition input and cannot hit the old execution binding.

There is no `execute(bindings=...)`, Dataset mutation method, bound-Session
facade, or fallback to whatever source values happen to be active at action
time. Other Session-owned source modules consume this same capture contract;
they do not define a second Event- or Lifecycle-specific binding mechanism.

### Explicit Population and scope matrix

| `population` | Observation scope | Result |
| --- | --- | --- |
| omitted | omitted | infer all-eligible non-versioned membership; evaluate Metrics only if their own temporal contract admits no observation scope |
| omitted | supplied consistently | infer all-eligible non-versioned membership; evaluate Metrics in the explicit observation window |
| admitted logical or materialized Population input | omitted | consume its members; evaluate semantically unbounded Metrics without inheriting selection time |
| admitted logical or materialized Population input | supplied consistently | consume its members; evaluate Metrics in the independent observation window |
| any | `time_dimension` without `time_scope` | reject incomplete observation scope |

Disjoint membership and observation windows are legal. Compatibility checks
validate governed identity, paths, time semantics, and sampling; they do not
intersect or overwrite the two scopes. A finite scope or endpoint required by a
Metric must be provided at observation construction, even when membership has a
finite selection window.

Every explicit value resolves one immutable private input contract:

```text
PopulationInputContractV1
  source_family
  source_shape
  mode = direct_membership | identity_projection
  entity_ref
  identity_field_id
  identity_field_role = entity_identity
  identity_signature[]
  identity_uniqueness_contract
  input_authority_token
  membership_selection_authority
  sampling_authority
```

`PopulationDataset` uses `direct_membership`, including Event/Lifecycle selection
outputs. Registered Entity-present Metric shapes and Entity-outlier Candidates
use `identity_projection`. Mode, exact input identity, and uniqueness proof bind
the consuming definition. This consumption creates no intermediate public
Dataset and does not mutate the input family.

### Explicit `PopulationDataset` admission

An explicit Population is admitted only when:

- it is a Logical input in the consuming Session or an explicitly selected
  Materialized Artifact from the same Store;
- it has the exact current `population/entity-membership@v1` contract;
- its Entity identity signature is complete;
- every Metric is computable at that exact Entity under one unique governed
  semantic path;
- membership selection and Metric observation retain independent scopes;
- observation time-axis, versioning, Relationship, and fanout requirements are
  proven for every branch at the selected analysis Entity;
- any sampling policy applies to the Entity spine before Metric evaluation.

The explicit Entity may differ from a Metric's compiled root Entity. This is
legal only when the semantic model proves a unique safe mapping and a valid
Metric aggregation at the Population Entity. The Population does not authorize
a Cartesian join, arbitrary bridge, nearest Entity, or final group-by used to
hide fanout.

If a materialized Population is supplied, its immutable identities form a scan
leaf. Downstream Metric evaluation may join current governed facts to those
identities, but it cannot reach through the leaf and re-evaluate its original
membership predicates.

### Explicit identity-bearing analysis Dataset admission

The closed registration includes:

| Input shape | Additional identity-projection requirement |
| --- | --- |
| `metric/entity@v1` | exact unique Entity key |
| `metric/entity-dimension@v1` | every retained Dimension functionally depends on Entity; exact unique Entity key |
| `metric/entity-time@v1` | retained time coordinate functionally depends on Entity; exact unique Entity key |
| `metric/entity-dimension-time@v1` | every retained coordinate functionally depends on Entity; exact unique Entity key |
| `candidate/entity-outlier@v1` | exact unique Entity key under its source contract |

All rows must bind one exact analysis Entity, complete non-null tuple identity,
and a registered uniqueness proof for Entity alone. A compound Entity/coordinate
row key is insufficient. Uniqueness must follow from the semantic/row-set
contract before execution; observed accidental uniqueness never establishes
admission. Execution validates that proof against realized rows.

A customer feature Dataset may add a to-one region coordinate, filter `region`,
and still supply membership. Projection, filtering, ranking, and limiting retain
the input's proven Entity uniqueness. They never infer uniqueness from a limit
of one or a predicate that happens to leave one date per customer.

A true customer-by-day Dataset is rejected. Module 2 does not invent `any`,
`all`, latest-row, or deduplication semantics. The repair is to author selection
at one row per Entity with a governed Metric or use a registered domain-owned
subject selection. Entity-reduced shapes remain ineligible.

The original Metric or Candidate family is preserved. Passing it through
`population=` explicitly consumes its selected identity rows. Logical input
contributes a same-plan identity projection and semi-join. Materialized input
contributes an immutable retained-identity projection, never origin replay or
local identity collection. Empty complete input is valid; null, duplicate, or
signature-incompatible identity fails atomically.

Its original observation window remains selection provenance. The consuming
observe call independently binds its own Metric window and reference axis.

### Local construction sequence

Calling `session.observe(...)` performs only:

1. normalize the ordered Metric inputs;
2. normalize or infer one membership authority;
3. validate Session, Entity, identity, independent membership/observation
   scopes, Relationship, and component compatibility;
4. construct one ordered Metric-binding set;
5. derive the complete Entity-grained row contract and row-set contract; the
   public schema is the row contract's canonical field inventory;
6. capture row-dependent action-time requirements;
7. derive the definition fingerprint and bounded lineage;
8. return one immutable logical `MetricDataset`.

It does not connect to a datasource, enumerate members, estimate cardinality,
create a Run, publish an Artifact, or choose a physical plan.

## Entity-Grained Multi-Metric Observation

### Family row semantics

The Entity observation shape is:

```text
metric/entity@v1
```

Its family-owned row-semantics payload uses two closed variants so retained
Entity context is present only after the Entity coordinate has been reduced:

```text
DatasetFamilyRowSemantics: metric/entity-present@v1
  metric_bindings[]
  coordinate_semantics[]

DatasetFamilyRowSemantics: metric/entity-reduced@v1
  reduced_entity_ref
  reduced_identity_signature[]
  metric_bindings[]
  coordinate_semantics[]
```

Every ordered Metric binding contains:

```text
  value_field_id
  unit
  aggregation_contract
```

Every coordinate-semantics entry likewise references its canonical
`DatasetFieldId` and adds only family-specific grain, path, or fold meaning not
already present in the common `DatasetField`. Public name, role, identity,
logical and physical type state, and nullability remain owned solely by
`DatasetRowContract.schema`. Population binding, explicit input identity, and
normalized observation parameters remain in the Dataset definition and lineage.
For an Entity-present shape, the canonical Entity coordinate binding owns the
target Entity ref and identity signature. The reduced variant retains them as
independent row context only because that public field is no longer present.

Each value field's `DatasetField.identity` is the stable semantic identity, not
a display label. Catalog Metrics use the exact Metric ref; runtime expressions
use their governed expression fingerprint. Duplicate semantic identities are
rejected even if labels differ.

Value columns preserve request order. Public names use the governed Metric name
or runtime label and the Dataset Core's deterministic collision normalization.
Display-name changes do not alter the field id or value identity.

### Entity row meaning

The initial row layout is:

```text
entity_identity | <ordered Metric value columns...>
```

One row means one member of the shared Population and one governed observation
value for every requested Metric at that Entity coordinate.

The row key is `entity_identity`. The Entity identity is never null. A Metric
value may be null because its governed facts are absent, its filter excludes
all contributions, or its own null semantics produce null. Such nulls do not
remove the Entity or another Metric's value.

Every Metric branch is evaluated independently against the same membership
spine and joined back one-to-one. The logical contract therefore forbids:

- intersecting independently non-null Metric row sets;
- using the first Metric as the Population source;
- duplicating a member because one branch traverses a one-to-many edge;
- treating a final `GROUP BY entity_identity` as proof of fanout safety;
- silently dropping identities with all-null Metric values.

Execution validates identity non-nullness and uniqueness. A contradiction is a
blocking action failure, not a deduplication request.

### Metric row filtering specialization

`MetricDataset.where(...)` uses the shared predicate protocol with
`FilterAdmissionV1.effect = row_subset`:

```python
high_resource = features.where(
    mv.gte(cpu_seconds, 60),
    mv.is_not_null(peak_memory_bytes),
)
```

Every predicate field must exist in the current Metric Dataset row contract:

- a selected Metric ref resolves its exact value binding;
- a selected Dimension ref resolves its exact coordinate binding;
- the selected time-Dimension ref resolves the current bucket field;
- `entity_identity` is not admitted to the general scalar predicate builders in
  the first cutover. Raw identity tuple filtering requires a future typed
  identity-set contract rather than leaking identity literals into ordinary
  predicate metadata.

Metric value predicates are evaluated after the governed Metric value exists at
the current Entity/Dimension/time coordinate. Coordinate predicates are
evaluated against the current coordinate row. Neither becomes a reusable
Metric-local semantic filter.

Filtering while the Entity axis is present selects current observation rows. A
following `aggregate()` aggregates only those selected rows, preserves the
original Population as target/selection provenance, and records the exact
selected-contribution binding separately. No original computational denominator
is inherited from Population lineage. Filtering after `aggregate()` selects completed
Dimension, time, or Dimension-by-time result rows and cannot change their
contributors. The exact singleton scalar shape rejects `where(...)` under the
common family admission rule.

The filtered output remains a `MetricDataset`; it does not become a
`PopulationDataset` even when its row key still includes `entity_identity`.
It may be consumed directly as membership only at a later source boundary:

```python
followup = session.observe(metrics=[lifetime_value], population=high_resource)
```

The new source binds `high_resource` and its exact identity-projection contract;
`high_resource` remains a Metric Dataset. No intermediate selection Population, detached
selection, or locally collected identity list is created.

A materialized Metric Dataset may be filtered only by fields retained in its
committed row contract. It produces a new logical Dataset over the immutable
scan leaf and cannot regain an absent Dimension, Metric component, or source
field from lineage.

### Selected contributions and computational denominators

`where`, `rank` followed by `limit`, and any admitted row-subset operation bind
the exact upstream observation definition and complete
Entity/Dimension/time coordinates at which the predicate was evaluated. Private
component state is selected with those coordinates. The selection is not reduced
to Entity identity unless the upstream grain was Entity-only.

Logical `aggregate()` first establishes the selected upstream coordinates, then
recomputes each Metric from only the governed contributions represented by those
coordinates. It does not rerun the predicate at the coarser output grain, admit
other dates/categories of a selected Entity, or evaluate selection and components
against different source realizations. Coordinate introduction after selection
must preserve this contribution boundary; an unprovable refinement fails locally.
Registered distinct/distribution/cumulative contracts must prove the same bound
or reject the transition; the availability of an origin graph is not enough.

For components, the common selection applies to both numerator and denominator
contributions; Metric-local semantic filters remain local to each component.
The original Population remains selection/coverage provenance only. Computing a
share against the original total requires an explicit governed denominator
Metric, not a hidden effect of `where(...)`.

Example: A has 8 conversions / 10 opportunities, B has 1 / 10. Selecting Entity
rows with conversion rate above 50% and aggregating yields 8 / 10 = 80%, not
8 / 20 = 40%. Selecting one customer-day never admits that customer's other days.
The selected contribution keys, graph binding, and fold contract participate in
definition identity and retained-state validation. No raw keys enter public
metadata.

### Metric projection

Metric projection is lazy:

```python
one = features.metric(revenue)
```

Its signature accepts one exact Metric identity already present in the Dataset.
It returns another `MetricDataset` with:

- the same Population and target-Population authority;
- the same Entity and coordinate contract;
- the same logical or materialized input authority token;
- exactly one selected Metric binding and value column.

Projection performs no query. A missing or ambiguous identity fails locally
and lists bounded exact present identities. A projection of a materialized
Dataset returns a new logical `MetricDataset` whose private input is the same
immutable Artifact scan leaf. It does not publish a new Artifact merely because
fewer public columns are selected.

## Aggregation Coordinate Algebra

### Coordinate vocabulary

Metric observation uses three coordinate kinds:

```text
Entity identity     membership-preserving observational coordinate
Dimension           governed category coordinate
time                governed bucket coordinate
```

Population scope is retained authority, not a row coordinate. Metric identities
are value bindings, not coordinates.

Public coordinate order is always:

```text
entity_identity, ordered Dimensions, time, ordered Metric values
```

After Entity-axis reduction it is:

```text
ordered Dimensions, time, ordered Metric values
```

Dimension order is first-introduction order. The single time coordinate is
always last among public coordinates, regardless of whether
`with_time_axis(...)` was authored before or after `with_dimensions(...)`.
This canonical order participates in row-contract identity and schema.

Every Dimension/time coordinate field id binds its exact semantic ref,
coordinate role, derivation, logical type, nullability, and selected grain when
temporal. Display names never establish coordinate compatibility.

### `with_dimensions(...)`

The public operation is:

```python
def with_dimensions(
    self,
    *dimensions: SemanticInput[DimensionKind],
) -> LogicalMetricDataset:
    ...
```

At least one Dimension is required. Inputs are ordered, current-catalog,
same-Session semantic identities. Duplicate existing or newly supplied
Dimensions are rejected. A time Dimension is rejected with a repair to
`with_time_axis(...)`.

`with_dimensions(...)` is legal only while the Entity axis is present. It adds
Dimension coordinates; it does not reduce rows, create a public grouping
builder, or change Population membership.

Each Dimension must be reachable from the Population Entity through one unique
governed path compatible with every Metric branch. A Dimension functionally
dependent on Entity produces at most one coordinate per Entity. A Dimension on
a governed one-to-many path may produce multiple Entity-Dimension coordinates
only when every Metric has a valid contribution/allocation contract at that
coordinate. Otherwise construction fails before planning.

Relationship reachability alone does not prove that coordinate buckets are a
partition of Metric contributions. The compiled Metric/path contract must
distinguish disjoint assignment, explicitly weighted conserving allocation, and
overlapping membership. A reused bridge weight may be a governed Measure; its
application belongs to the Metric contribution graph, not to a universal
Relationship allocation policy. Membership stability and fold safety are
derived facts, never caller assertions or conclusions from a preview.

Null is a governed Dimension bucket when the Dimension contract permits null.
It is not silently dropped.

### `with_time_axis(...)`

The public operation is:

```python
def with_time_axis(
    self,
    time_dimension: SemanticInput[TimeDimensionKind],
    *,
    grain: TemporalGrain,
) -> LogicalMetricDataset:
    ...
```

The time Dimension and grain are required. Only one time coordinate is admitted
in the first cutover. A second call is rejected rather than replacing or
nested-bucketing the existing axis.

Construction validates:

- exact current-catalog time-Dimension identity;
- unique reachability from the Population Entity and every Metric branch;
- compatibility with the observation reference scope;
- declared physical granularity and timezone/calendar authority;
- requested grain is not finer than the semantic source permits;
- semi-additive status-axis and cumulative-axis constraints;
- one common time coordinate for all Metric roots.

`with_time_axis(...)` partitions eligible contributions into governed buckets.
It does not change membership or the observation scope. If the observation has a
finite `time_scope`, bucket membership is clipped to that exact half-open
interval. A scope end is never advanced to create an inclusive last day.

If no finite scope exists, the Dataset may remain logically unbounded. Action
admission owns execution bounds; `with_time_axis(...)` does not insert an
implicit window.

### Coordinate spine

Dimension and time coordinates are derived from one governed coordinate spine
rooted at the Population Entity, independently of Metric non-nullness.

The spine contains distinct coordinate tuples reachable for eligible members
under the observation scope and semantic Relationships. It is not:

- the intersection or union of non-null Metric rows;
- a full Dimension-by-time Cartesian product;
- a calendar densification request;
- a cheapest physical join path;
- a post-hoc alignment of independently executed Metric results.

Metric branches are evaluated at the exact spine coordinate and joined
one-to-one. Missing Metric contributions stay null. A Population member with no
reachable selected coordinate has no row in the coordinate-shaped Dataset, but
the retained Population authority still explains eligibility and coverage. It
does not implicitly contribute to a Metric denominator without a governed
component contribution at that coordinate.

The planner module decides how to lower the admitted spine. It may not change
its logical membership or coordinate domain.

### `aggregate()`

The public operation is deliberately zero-argument:

```python
def aggregate(self) -> LogicalMetricDataset:
    ...
```

`aggregate()` is legal only while the Entity axis is present. It removes that
axis exactly once and preserves every declared Dimension and time coordinate.
Calling it on an already reduced Dataset fails with a typed shape repair.

The exact transitions are:

| Input shape | Output shape | Output row key |
| --- | --- | --- |
| `metric/entity@v1` | `metric/scalar@v1` | empty row key; singleton row-set contract |
| `metric/entity-dimension@v1` | `metric/dimension@v1` | ordered Dimension tuple |
| `metric/entity-time@v1` | `metric/time@v1` | exact time bucket |
| `metric/entity-dimension-time@v1` | `metric/dimension-time@v1` | Dimension tuple + time bucket |

The scalar shape has exactly one logical row for the Population definition,
including an empty Population or empty selected contribution set. It uses the
Dataset Core singleton row-set variant
and adds no synthetic public `_scalar` column.

All non-scalar reduced rows are unique by the exact ordered coordinate key.
Default row-set ordering remains unordered unless a later registered operator
declares an order.

`aggregate()` retains:

- Population definition and target-Population lineage;
- Entity identity authority as the reduced analytical unit;
- membership-selection provenance, independent observation scope, and sampling intent;
- exact Metric identities and value order;
- Dimension/time coordinate identity;
- approximation requirements and action-time checks.

It does not retain `entity_identity` as a hidden public grouping column and does
not create a scalar/time/segment/panel Population variant.

### Coordinate declaration before a materialization barrier

Logical Metric Datasets may add a coordinate whenever the semantic model can
derive it safely from the Population and Metric roots.

A materialized scan leaf cannot recover discarded source facts. Coordinate
introduction on materialized input is therefore admitted only when the new
coordinate is functionally derivable from coordinates already present in the
Artifact under current semantic authority, without duplicating or repartitioning
Metric contributions.

Examples:

- attaching a Dimension uniquely functionally dependent on
  `entity_identity` may be admitted;
- attaching a one-to-many Dimension to materialized Entity values is rejected;
- adding a time axis to a materialized Entity-only Dataset is rejected unless
  the existing Entity coordinate itself carries the exact governed temporal
  dependency;
- changing grain after the fine-grained time coordinate has been materialized
  belongs to a separately registered rollup transform, not a second
  `with_time_axis(...)` call.

The repair names the missing coordinate and instructs the caller to add it
before `.execute()`.

### `rollup(...)` coarsens current Dataset rows

The separately registered coordinate-coarsening operation is:

```python
def rollup(
    self,
    *,
    drop_dimensions: tuple[SemanticInput[DimensionKind], ...] = (),
    grain: TemporalGrain | None = None,
    drop_time: bool = False,
) -> LogicalMetricDataset:
    ...
```

`rollup(...)` is admitted only on Entity-reduced Metric shapes and requires at
least one non-empty `drop_dimensions` tuple, `grain`, or `drop_time=True`. It may
remove Dimensions, coarsen time, or remove time under an exact registered temporal
fold. `drop_time` is an exact bool; it requires a retained time coordinate and
cannot be combined with `grain`. It cannot introduce coordinates, refine grain,
accept a generic axis selector, or change Population membership.

Its meaning is deliberately different from `aggregate()`:

```text
aggregate()
  remove Entity identity; a Logical input recomputes governed Metric
  contributions, while a Materialized input requires its exact Entity-axis fold

rollup(...)
  start after Entity identity is already absent and fold current rows or
  retained sufficient state across explicitly removed/coarsened coordinates
```

The distinction is invariant across input states. A Logical input retains a
fold node after its upstream logical relation and receives equivalent source
Ibis composition while eligible. A Materialized input creates the same fold over
its exact immutable Artifact scan leaf. When the exact retained fold admits
pandas and source lowering is ineligible or its input is already local, the
complete current rows and required retained state feed that pandas continuation.
The Logical branch may not reinterpret rollup as a fresh Metric-graph
recomputation, and the Materialized branch may not reach through origin lineage.

The admitted Entity-reduced transitions are:

| Input shape | Request | Output shape |
| --- | --- | --- |
| `metric/dimension@v1` | drop one or more retained Dimensions | `metric/dimension@v1` or `metric/scalar@v1` |
| `metric/time@v1` | coarsen the retained grain | `metric/time@v1` |
| `metric/time@v1` | `drop_time=True` | `metric/scalar@v1` |
| `metric/dimension-time@v1` | `drop_time=True`, optionally dropping Dimensions | `metric/dimension@v1` or `metric/scalar@v1` |
| `metric/dimension-time@v1` | drop retained Dimensions | `metric/dimension-time@v1` or `metric/time@v1` |
| `metric/dimension-time@v1` | coarsen grain, optionally dropping Dimensions | `metric/dimension-time@v1` or `metric/time@v1` |

A request changing both coordinate kinds canonicalizes to time folding within
the complete original Dimension tuple, followed by Dimension folding at the
retained output time grain, or with no time coordinate after time removal.
It is semantically equal to the corresponding two-call chain and uses the same
normalized semantic nodes; authored occurrence paths remain available only for
diagnostics. A Metric whose folds cannot compose in that
order is rejected. Cumulative Dimension folding additionally requires equal
evaluation ends and compatible coverage across every combined row.

Every named Dimension must be an exact distinct retained coordinate. When
`grain` is supplied, its target must be strictly coarser than the current grain
under one exact calendar/containment contract. All Metrics in an arity-N Dataset must admit the
same requested coordinate transition; no Metric column may be omitted or
silently approximated.

Each Metric binding resolves one exact registered fold for every removed
coordinate:

| Retained Metric authority | Admitted rollup fold |
| --- | --- |
| additive `sum` / `count` | `sum` only across a proven disjoint or conserving allocated contribution partition |
| `min` / `max` | matching extremum |
| semi-additive | only an exact axis fold whose order and retained state preserve the original spatial/time calculation |
| additive linear composition | only when the composed retained value is proven additive |
| mean, weighted mean, ratio | only a registered merge over retained named sufficient-state bindings |
| `count_distinct` | only a registered exact mergeable distinct state; projected counts never sum |
| median, percentile, distribution statistic | only a registered exact mergeable distribution state |
| cumulative time series | declared period-end `last` fold with exact ordered time and evaluation-end authority |
| opaque or unresolved value | rejected |

Every row in this table is conditional on the complete Metric/path/selection
contract. In particular, an additive base value is not a license to sum
overlapping Dimension buckets. One 100-unit order assigned to two tags can
legally appear as 100 in each tag view; dropping that tag coordinate cannot
claim the order total is 200. The fold must have an exact conserving allocation
or retained state that computes the unique contribution union; otherwise it is
rejected. No implementation invents equal weights or consults origin lineage.

A cumulative `last` fold selects the final retained source bucket within each
target period, preserves that row's exact evaluation end, and marks a target
period partial unless coverage proves the source buckets reach its complete end
boundary. It never presents a mid-period last observation as a complete
calendar period. Dimension reduction of cumulative values requires a separate
exact fold for that Dimension axis; the time `last` rule does not authorize a
Dimension sum.

When removing the final coordinate produces `metric/scalar@v1`, the output keeps
the common explicit singleton row-set contract even when the current input has
zero rows. Each Metric's fold registration must define its empty-input identity
or null result. A missing empty-fold rule rejects the scalar transition rather
than returning zero rows or inventing a default value.

Construction derives the complete output row contract, row-set contract,
coverage requirements, per-Metric fold ids, and action-time checks. Definition
identity binds the exact input authority token, ordered dropped Dimension refs,
`drop_time`, target grain/calendar identity, fold-contract versions, and output row
contract. Unsupported folds fail locally with a repair to author and execute a
fresh observation at the target coordinates; no error suggests replaying a
Materialized origin.

Time-axis removal folds only the current retained buckets and their compatible
component state. Additive flow sums, extrema, and ratio/mean sufficient-state
merges are admitted when their exact temporal fold is registered. Semi-additive
and cumulative Metrics require their declared endpoint/fold and coverage proof;
no generic sum or implicit latest timestamp is supplied.

For a complete daily revenue series, `rollup(drop_time=True)` computes its
window total without a new observation. If a prior `where(...)` selected only
some days, the result is a total of those selected periods. It retains that
selection and may not claim complete-window coverage. Disjoint, partial, empty,
and null buckets follow the exact fold/coverage contract; missing authority
rejects the transition instead of replaying the origin.

## Aggregation Admission

### One closed aggregation contract per Metric

Every Metric binding carries one normalized coordinate-aggregation contract
derived from current semantic authority:

```text
MetricCoordinateAggregationV1
  logical_recompute_mode
  materialized_fold_by_reduced_axis
  required_components[]
  contribution_partition_by_reduced_axis
  ordered_aggregation_and_temporal_fold
  required_alignment_and_coverage
  empty_and_null_contract
  required_time_axis
  status_time_contract
  cumulative_contract
  blockers[]
```

This is derived semantic admission metadata, not a public authoring record or
private physical plan. The semantic graph supplies intrinsic aggregation,
component, unit, versioning, and temporal facts; this binding combines them with
the actual coordinates, path allocation, selection and input authority. It is
complete enough to decide whether a coordinate transition is meaningful and
whether the current rows and required state are sufficient. Standard aggregate,
weighted-mean, ratio, linear and cumulative constructors provide those facts
without new author-supplied capability flags.

An admitted fold must prove that finalizing the merged input state gives the
same governed result as evaluating the represented contribution set at the
target coordinates. The proof includes overlap/allocation, null/empty behavior,
ordering, calendar alignment and coverage. This computational sufficiency does
not establish inferential sufficiency, independent sampling or a study design.

If any Metric in an arity-N Dataset rejects a transition, the whole Dataset
construction fails. Marivo never returns a partial subset of Metric columns and
never silently changes a Metric's aggregation.

### Logical aggregation matrix

For a logical Dataset, the semantic graph remains available. `aggregate()`
uses this matrix:

| Metric contract | Admission at requested coordinates | Required behavior |
| --- | --- | --- |
| additive `sum` / `count` | admitted when Entity/coordinate paths are safe | recompute contributions and sum at the output key |
| `min` / `max` | admitted when the governed base is available | recompute the extremum at the output key |
| semi-additive | admitted only with its exact status-time and fold contract | aggregate across non-time axes and apply the governed time fold inside each requested bucket/scope |
| `mean` | admitted only with named base measure or exact sufficient-statistic authority | compute sum and non-null count, then divide |
| weighted mean | admitted only with value and additive weight components | recompute weighted numerator and weight, then divide |
| ratio | admitted only with compatible numerator and denominator graphs | recompute both components at the output key, then divide under the governed zero-denominator policy |
| `count_distinct` | admitted only with exact governed identity authority | recompute distinct identity at the output key |
| median / percentile | admitted only with a lowerable base distribution and backend capability requirement | recompute from base values; never combine projected quantiles |
| additive linear composition | admitted when every term has compatible coordinates and units | recompute signed terms at the output key, then compose |
| other derived composition | admitted only when every component graph supplies an exact coordinate contract | recompute components before composition |
| cumulative | admitted only under the cumulative-axis rules below | recompute base contributions, then apply the governed anchor/window |
| opaque Tier-2 expression | admitted only when its existing declared graph can resolve to a registered exact contribution/reaggregation contract | otherwise fail with a repair to express the calculation using governed constructors; no author boolean or generic callback supplies proof |

Backend support is an action-time planner requirement resolved before data
work. The local Dataset may be constructed when semantic admission is exact but
physical capability is not yet selected; `contract()` exposes that requirement.
Logical Metric-graph evaluation and its governed reaggregation remain
source-required. A missing source lowering does not authorize collecting raw
contributions into pandas. Materialized aggregation may use pandas only when
its exact retained-input fold admits all required state under local budgets.
Missing semantic authority fails during construction.

### Semi-additive rules

A semi-additive Metric must retain its exact `status_time_dimension` and
governed fold.

- Dimension-only grouping requires an exact contribution partition and the
  original spatial-before-temporal calculation order.
- A selected time coordinate must be the status axis or one exact governed
  temporal path compatible with it.
- The requested grain may not be finer than physical sample/granularity
  authority.
- Sampled series use their declared fold within each output bucket.
- Snapshot selection folds require the exact versioning and business identity
  contract.
- A missing fold, ambiguous status axis, or incompatible grain fails before
  execution.

Marivo never sums snapshot dates, treats a technical ingestion time as a
business status axis, or carries a snapshot across buckets without an owning
semantic contract.

A value already folded in time is not automatically additive over Entity or
Dimension. For devices with samples `[10, 0]` and `[0, 10]`, a Metric defined as
the window maximum of the per-instant device sum is 10. Summing their separately
materialized maxima gives 20 and is invalid. `max`, `min`, and percentile folds
generally do not commute with spatial sum. Mean needs the same sample domain,
weights and missingness rules; first/last need the same governed evaluation
instant. The normalized binding must prove commutation for the exact case or
provide an explicitly registered merge of aligned retained samples followed by
the temporal fold. Without either, materialized reduction is rejected.

The first cutover does not promise generic retained sample or distribution
parts for semi-additive Metrics. A backend's ability to evaluate the logical
Metric is not evidence that its projected scalar values suffice after a read.

### Component-aware rules

Ratio, weighted-mean, and component-derived Metrics are recomputed at the
requested output key in this order:

1. align every component to the same Population and coordinate spine;
2. evaluate each component's own aggregation and temporal semantics;
3. retain null/coverage facts independently;
4. compose the parent under its governed unit and zero-division contract.

The parent value is never summed, averaged, or weighted after Entity projection
as a shortcut. Component paths with different target Entities, scopes, time
axes, or unsafe fanout fail with the responsible component occurrence path.

### Cumulative rules

A cumulative Metric retains its exact base Metric, `over` time Dimension, and
anchor.

With a time coordinate:

- `with_time_axis(...)` must select the exact `over` axis;
- the requested grain must satisfy the anchor's reset or trailing-span rules;
- base values are computed per output coordinate before accumulation;
- all-history, grain-to-date, and trailing anchors remain distinct definitions.

Without a time coordinate, the result means the cumulative value at one exact
evaluation end. A finite observation-scope end is therefore required.
The scope clips eligible displayed facts but does not silently reset an
all-history anchor.

Mixed cumulative/non-cumulative component graphs or incompatible component
anchors fail under the existing semantic contract. `aggregate()` cannot invent
an endpoint, reset grain, or accumulation axis.

### Materialized reaggregation matrix

A materialized Metric Dataset has no executable access to its original Metric
roots. Reducing its Entity axis is legal only when its retained values form
exact sufficient statistics for the requested fold.

The initial matrix is:

| Materialized Metric value | Entity-axis fold |
| --- | --- |
| additive `sum` / `count` | `sum` only under exact disjointness or conserving allocation |
| `min` | `min` |
| `max` | `max` |
| semi-additive value reduced across Entity | only a registered exact retained-state fold or a proven commuting fold with aligned evaluation/coverage; otherwise rejected |
| additive linear composition | admitted only when the composed value is itself proven additive |
| `count_distinct` | admitted only when the distinct identity is exactly the Population Entity key and disjointness is proven by the row contract |
| mean, weighted mean, ratio | merge their required named sufficient state under exact component folds |
| median, percentile, other distribution statistic | rejected |
| cumulative | rejected in the first cutover |
| opaque or unresolved composition | rejected |

### Required retained state in the first cutover

Public values alone do not establish sufficient statistics. The first-cutover
aggregation registry requires these private, row-keyed Artifact parts whenever
the admitted Metric graph uses the corresponding contract:

| Metric | Required retained state | Merge and finalize |
| --- | --- | --- |
| mean | sum and non-null count | sum both; divide under the Metric null/empty contract |
| weighted mean | weighted numerator and additive weight sum | sum both; apply governed zero-weight policy |
| ratio | named state for each component graph | merge each component by its own exact fold; divide under the governed denominator policy |

A ratio does not make its components additive. A distinct, semi-additive,
percentile, or cumulative component must independently provide an admitted exact
state/fold for the requested axis; otherwise the parent transition is blocked.
Exact distinct/distribution states are retained only for explicitly registered
contracts. Generic quantile Entity-axis folding remains outside the first cutover.

Filtering selects primary rows and the exact associated component-state records
together. Projection retains the dependency closure of the selected Metric.
Coordinate operations transform parts under the same allocation contract as
values. Parts must reconcile with primary values and commit atomically; missing
required parts block publication, and corrupt parts fail downstream dependency
validation. No original datasource is queried to reconstruct missing parts.

These bindings are private storage-authorized state, not extra public columns.
Another selected Metric column is never silently borrowed as a numerator,
denominator, count, or weight. `.contract()` derives legal folds from the exact
registered and retained state, identically after cold recovery.

Empty and null reductions consume the Semantic Object Model's fixed operator
rules. An absent contribution is not automatically a zero; a ratio does not
gain zero-fill or additive components merely because its visible value is
numeric. Count state, numerator/denominator state and coverage must stay
distinguishable through every selected-row and materialization boundary.

When rejected, the structured repair is:

```python
result = (
    logical_features
    .with_dimensions(...)
    .with_time_axis(..., grain=...)
    .aggregate()
    .execute()
)
```

It never re-queries through the materialized leaf, silently approximates the
fold, or averages already aggregated ratios.

## Population and Coordinate Compatibility

These are concrete observation-input constraints, not a verdict about freshness
or suitability. The Agent may explicitly select a committed Population, selection Population,
Metric, or Candidate from another Session in the same Store. Its immutable identity
and producing Session remain intact; the consuming Session owns only the new Run
and output. No source-version comparison or maximum-age policy is applied.
A foreign Logical definition or unrelated field selector is still rejected.

### Exact compatibility checks

Construction compares semantic identity, never display names. The complete
admission set is:

| Input | Required compatibility |
| --- | --- |
| Metric roots | one exact inferred Entity, or one unique safe path to the explicit Population-input Entity |
| `PopulationDataset` input | same-Session Logical or explicit same-Store Materialized input; exact family, complete identity, compatible scope and sampling |
| exact Entity Metric input | same-Session Logical or explicit same-Store Materialized input; registered Entity-present shape with proven Entity-only uniqueness, independent observation scope, and compatible sampling |
| entity-outlier Candidate input | same-Session Logical or explicit same-Store Materialized input; exact `candidate/entity-outlier@v1` shape, unique complete identity, compatible scope and sampling |
| Dimension | one unique governed path from Population Entity and safe Metric contribution semantics |
| time Dimension | one unique governed path, compatible granularity/calendar/scope, common to all Metrics |
| multiple Metrics | one shared Population and coordinate spine; no independent row-set alignment |
| runtime Metric expression | same Session authority, stable identity/label, complete governed component graph |

Matching primary-key field names, physical table names, source columns,
datasource ids, or display labels never establish compatibility.

### Population versus Metric scope

The Population is the outer identity boundary, not a fact-time boundary.

- Membership scope, predicates, and any source-owned subject selection determine
  eligible identities and retain their own selection-time provenance.
- Observation scope and reference axis constrain ordinary Metric facts.
- Metric-local filters affect only their governed component contributions.
- Cumulative history or status lookback may read outside the observation window
  when its exact semantic contract requires it, without adding Population members.
- Time coordinates partition observation contributions; they never select a new
  cohort or change observation scope.
- Row selection restricts exact upstream contributions. Original target lineage
  does not become an implicit computational denominator.
- Null values do not remove members or valid coordinates.

An incompatible Entity mapping, missing temporal alignment, or unsupported
component fold fails locally. A different membership window and observation
window is not itself a conflict, and no consumer inherits selection time as an
observation default.

### Structured repair matrix

| Conflict | Concrete repair |
| --- | --- |
| ambiguous implicit analysis Entity | provide an explicit Population from real safe Entity candidates, or split observations |
| unsafe component mapping to explicit Entity | inspect the named component and governed path/allocation contract |
| missing observation scope/axis | supply the required observe-level window and exact Metric reference axis |
| Population input lacks proven Entity uniqueness | select at Entity grain or use a registered domain selection; never deduplicate implicitly |
| row selection lacks a contribution mapping for recomputation | retain exact component state or author the required coordinates before selection |
| absent retained fold/state | author the observation before materialization at the desired grain; do not replay an Artifact origin |
| unsupported time removal | use an exact registered temporal fold or author the requested window observation explicitly |

Repairs contain expected/received contracts and only real candidates, and never
render identity literals or choose business meaning heuristically.

## Population and Metric Materialization Contracts

Every Population- or Metric-producing definition resolves the common
`DatasetMaterializationContractV1` envelope owned by Module 4 before Run
admission. Module 2 owns the registrations and semantic checks below; Module 4
owns their invocation, persistence, Evidence envelope, and atomic publication.

The first-cutover registry below lists Module 2-owned producers. Module 6
additionally registers Event/Lifecycle selection producers of this same
Population family, using its own complete-selection checks and this module's
identity/row contract. Producer ownership never creates a second membership
family or duplicates the common filter/sample registrations.

| Producing definition | Quality contract | Validation output | Evidence extractor | Finding extractor / policy | Retained private state |
| --- | --- | --- | --- | --- | --- |
| explicit or inferred Population root | `population_root_quality@v1` | `population_root_validation@v1` | `population_root_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `PopulationDataset.where` | `population_filter_quality@v1` | `population_filter_validation@v1` | `population_filter_evidence@v1` | `none@v1` / `zero_findings@v1` | none |
| `PopulationDataset.sample` | `population_sample_quality@v1` | `population_sample_validation@v1` | `population_sample_evidence@v1` | `none@v1` / `zero_findings@v1` | `population_sampling_state@v1` |
| `session.observe` | `metric_observation_quality@v1` | `metric_observation_validation@v1` | `metric_observation_evidence@v1` | `none@v1` / `zero_findings@v1` | aggregation-contract-selected component state |
| `MetricDataset.where` | `metric_filter_quality@v1` | `metric_filter_validation@v1` | `metric_filter_evidence@v1` | `none@v1` / `zero_findings@v1` | retained compatible input component state |
| `MetricDataset.metric` | `metric_projection_quality@v1` | `metric_projection_validation@v1` | `metric_projection_evidence@v1` | `none@v1` / `zero_findings@v1` | projected compatible input component state |
| `MetricDataset.with_dimensions` / `MetricDataset.with_time_axis` | `metric_coordinate_quality@v1` | `metric_coordinate_validation@v1` | `metric_coordinate_evidence@v1` | `none@v1` / `zero_findings@v1` | coordinate-qualified component state |
| `MetricDataset.aggregate` | `metric_aggregation_quality@v1` | `metric_aggregation_validation@v1` | `metric_aggregation_evidence@v1` | `none@v1` / `zero_findings@v1` | exact retained state required by the admitted aggregation contract |

All registrations first invoke `dataset_structure_quality@v1`. Population
checks then prove exact non-null tuple identity, row-key uniqueness, scope and
predicate realization, and sampling receipt coherence. A versioned root also
requires `population_version_resolution@v1`: source version-key uniqueness,
validity non-overlap where applicable, the exact selected snapshot/interval
boundary, required source coverage, and at-most-one representation per `K`
before projecting identity. These are action-time requirements derived during
construction, not datasource checks performed by a constructor. Checks cover
the selected source versions and necessary interval boundary neighborhood;
they do not inspect unrelated history to certify source freshness. Filtering
and sampling validate their inherited resolved membership and cannot replace
source-integrity checks with output deduplication.

`population_root_validation@v1` records the normalized versioning contract,
membership boundary and interpretation, selected version coordinate, uniqueness
and interval-check outcomes, and exact source coverage binding when applicable.
Those are factual, bounded validation/provenance fields, not raw identity rows
or a new retained-state family. A non-versioned root carries its corresponding
identity-only validation variant. Missing required source evidence fails the
action; repeated execution-binding recovery uses the already committed facts.

Metric checks prove one
shared Population spine, exact row-key uniqueness, ordered Metric and coordinate
bindings, null-retention semantics, branch-to-spine reconciliation, and the
selected aggregation equations. They additionally bind every consumed source's
own temporal resolution, contribution partition/allocation and ordered-fold
requirements; a membership version never substitutes for a Metric version.
A sample validates its realized membership
once per action and records requested versus realized authority without
publishing identity values.

Evidence contains only bounded semantic refs, contract ids, scope and sampling
classes, row and null-count summaries, coordinate/Metric counts, reconciliation
maxima, version-selection/boundary and source-coverage validation summaries,
and approximation facts. Raw Entity identities, coordinate samples,
predicate literals classified as sensitive, source rows, SQL, and private graph
nodes are forbidden. Population and Metric materialization creates no Findings:
these Datasets are governed observations, not analytical conclusions. The
canonical zero-Finding envelope is still mandatory.

`population_sampling_state@v1` binds the exact realized sampling receipt needed
for Artifact audit and same-leaf reuse. Metric retained state is present only
when the exact aggregation contract names it—for example numerator and
denominator, sum and non-null count, or an exact distribution basis. It remains
private, storage-authorized Artifact state; it is not a public column and cannot
authorize origin replay. Missing, incompatible, or unreconciled retained state
blocks publication rather than silently weakening later materialized
reaggregation.

The private Slice 3a DuckDB registration records each realization's ordinal,
sampled and unsampled target definition fingerprints, exact target and seed,
realized Entity count, membership digest, and implementation id
`duckdb.entity_reservoir@v1` in `marivo.population_sampling_execution/v1`.
The actual sampling-state part is a one-row Parquet relation whose non-null
`sampling_execution_digest` string binds the canonical receipt. It contains no
raw identity rows. The part shares the Artifact's reservation, storage budget,
validation and atomic publication. Metadata recovery validates the receipt
without reading Parquet; an explicit private part audit validates its stored
digest. Ordinary primary reads do not depend on an unused sampling part.
Neither this receipt nor its part grants materialized fold or origin-replay
authority. The physical registration accepts reservoir targets up to one billion
and seeds from zero through `2**31 - 1`, rejecting unsupported exact requests
before source statements rather than truncating or coercing them.

Retained sums preserve absence of non-null support. A nullable sum, or a
registered sum-plus-support-count representation, must finalize an unsupported
empty/all-null sum as null. A local accumulator initialized to zero cannot
erase that fact. Mean and weighted-mean state follows the same exact component
support/null rules before finalization.

Each required private component state is an actual Artifact-owned retained part,
not only a registration id. Module 4's `retained_parts[]` maps its family-registered
role and schema version to a concrete receipt. Primary rows and parts validate
and commit together; original data is never replayed to recreate a missing part.
A cold rollup reads the exact required roles, while `show()`/`to_pandas()` expose
only primary rows. Parts have no independent public Artifact or graph identity.

## Definition Identity and Lineage

### Population definition identity

A Population definition fingerprint binds:

- family and contract versions;
- exact Entity ref and compiled primary-key signature;
- Entity versioning definition and the exact membership endpoint-resolution
  rule, when applicable; no ambient latest-snapshot request;
- reference scope and selected time-Dimension ref;
- canonical membership predicate trees in authored operator order;
- exact sampling request;
- unsampled target-Population authority token;
- semantic and Relationship dependency identities;
- logical or materialized input authority token.

Population and Metric definitions also use Dataset Core's canonical sharing
relation for sampling and selected-contribution realizations. Observation owns
which uses must share and propagates their private requirement handles; Core
owns normalization and identity. An immutable Artifact remains a scan leaf,
with no traversal of the producer's Population or contribution graph.

It excludes realized members, row counts, sample realization, generated SQL,
backend strategy, and factual input/source lineage.

### Metric Dataset definition identity

A Metric Dataset definition fingerprint additionally binds:

- ordered Metric identities and aggregation-contract versions;
- exact Population input authority token;
- independent observation scope and resolved Metric reference-time authority;
- exact selected-contribution binding and component-state requirements;
- exact captured source-binding value digest for every reachable parameterized
  Entity;
- ordered Dimension coordinates;
- exact time coordinate and grain;
- Entity-axis present/reduced state;
- operator versions, rollup requests, and materialized-fold decisions.
- contribution partition/allocation, ordered spatial/temporal operations,
  alignment, coverage and null/empty contract versions used for those folds.

Every filtered Dataset fingerprint also binds the exact input authority token,
family filter effect, canonical predicate tree, authored operator position, and
predicate-contract version. Equivalent same-domain expression construction does not rewrite that
public definition identity.

Metric projection binds the selected identity. `aggregate()` binds the exact
coordinate transition and each Metric's recomputation/fold mode. `rollup(...)`
binds its ordered dropped Dimension refs, `drop_time`, target grain/calendar identity, and
per-Metric retained-state fold contracts.

Two chains with identical public labels but different Entity, scope,
Population, sampling, Metric order, Dimension order, time grain, component
authority, or logical/materialized inputs intentionally have different
definition fingerprints.

### Bounded lineage

Public lineage may disclose:

- target Entity ref and identity-signature field names without raw values;
- Population definition fingerprint and target-Population fingerprint;
- membership-selection scope and independent observation scope;
- exact Metric, Dimension, and time-Dimension refs;
- public operator ids and coordinate transitions;
- filter effect, predicate-field identities, and a bounded redacted predicate
  summary;
- materialized input Artifact refs;
- approximation requested versus exact.

It does not disclose raw identities, predicate secrets, generated SQL, private
Relationship plans, physical sampling strategy, or unbounded component graphs.

## Contract and Help Disclosure

The canonical Help routes are:

```text
marivo.help("analysis.population")
marivo.help("analysis.population.sample")
marivo.help("analysis.EntitySamplingPolicy")
marivo.help("analysis.engine_sample")
marivo.help("analysis.filters")
marivo.help("analysis.eq")
marivo.help("analysis.not_eq")
marivo.help("analysis.lt")
marivo.help("analysis.lte")
marivo.help("analysis.gt")
marivo.help("analysis.gte")
marivo.help("analysis.is_in")
marivo.help("analysis.is_null")
marivo.help("analysis.is_not_null")
marivo.help("analysis.all_of")
marivo.help("analysis.any_of")
marivo.help("analysis.not_")
marivo.help("analysis.AnalysisPredicate")
marivo.help("analysis.datasets.where")
marivo.help("analysis.observe")
marivo.help("analysis.metric_dataset")
marivo.help("analysis.metric_dataset.with_dimensions")
marivo.help("analysis.metric_dataset.with_time_axis")
marivo.help("analysis.metric_dataset.aggregate")
marivo.help("analysis.metric_dataset.rollup")
marivo.help("analysis.metric_dataset.metric")
```

`analysis.filters` is a registry-owned navigation group whose members are the
twelve exact predicate-builder leaves above. It contains no renderer-local
shadow list. Every builder leaf owns its reflected public `mv.*` callable,
signature, admitted operand/literal contract, return type, failure rules, and
one minimal example. `analysis.AnalysisPredicate` is the independently
resolvable public-type leaf and is excluded from the navigation-member list.

Static Help owns `where(...)` syntax, common lifecycle, family effect
navigation, input families, coordinate rules, and examples.
Family Help owns filterable field roles and shape admission without redefining
predicate syntax. `dataset.contract()` owns the current exact Population,
coordinates, Metric arity, logical/materialized state, current filter effect,
filterable fields, required input fields and semantic paths, legal continuations, approximation
requirements, and materialization-barrier blockers. Structured errors own the
repair for a failed exact call.

`eq`, `not_eq`, `lt`, `lte`, `gt`, `gte`, `is_in`, `is_null`, `is_not_null`,
`all_of`, `any_of`, `not_`, `AnalysisPredicate`, `EntitySamplingPolicy`, and
`engine_sample` join the public export snapshot. The predicate type's concrete
variants and the sampling policy remain sealed and helper-produced. The root
`analysis` Help page routes to `analysis.filters` but does not inline the
builder signatures or public-type contracts.

A `PopulationDataset` card renders only bounded Entity, scope, predicate-count,
sampling, state, and identity facts. A `MetricDataset` card renders bounded
shape, Population Entity, membership-selection and observation scopes,
Metric identities/count, selected-contribution summary, coordinates, state, and
continuation facts. Neither card renders raw identities, values, SQL, or the
full capability matrix.

## Cross-Module Seams

### Dataset Core supplies

- nominal `PopulationDataset` and `MetricDataset` families;
- row-contract, row-set-contract, and canonical schema construction;
- selector-only `DatasetFields` and public `DatasetFieldRef` values;
- immutable logical/materialized Dataset state;
- Session ownership and private authority tokens;
- definition fingerprints, bounded lineage, actions, and scan-leaf behavior.

This module supplies the family row-semantics payloads, shapes,
value/coordinate bindings,
and exact transitions Dataset Core intentionally leaves family-specific.

### Planner and Pushdown consumes

- one exact membership spine;
- normalized predicate semantics;
- exact authored filter order, family effects, boolean/null rules, and
  non-commuting barriers;
- Entity identity and Relationship/fanout requirements;
- one coordinate spine independent of Metric non-nullness;
- per-Metric logical recomputation contracts;
- materialized-fold admission and coordinate-barrier failures;
- action-time semantic and capability requirements.

It composes eligible source work through Ibis and admits only declared exact
pandas continuations for retained-input folds and result-row operations.
Source-owned Population and Metric-graph evaluation, semantic enrichment and
identity projection stay in their admitted source domain. It may not change
Entity, membership, coordinate domain, Metric aggregation or null-retention
semantics, or choose a local continuation after source failure.

### Materialization Runtime consumes

- exact logical Population and Metric family contracts;
- canonical safe predicate projections without unbounded or secret-like
  literals;
- sampling requested versus exact intent;
- target-Population lineage;
- row identity, schema, and uniqueness requirements;
- logical/materialized authority-token distinction.

It supplies realized membership/sample facts, schema/row validation, Run and
Artifact state, execution of this module's registered quality/Evidence
contracts, storage, publication, recovery, and immutable scan leaves. It does
not invent Population or Metric checks, extractors, or retained-state meaning.

### Typed Operators consumes

- exact Metric Dataset shapes and coordinate identities;
- exact identity-bearing Metric and entity-outlier Candidate row contracts used
  by population-input admission;
- Population and target-Population authority;
- Metric arity, value semantics, aggregation contracts, and approximation;
- logical/materialized continuation blockers.

It declares the exact filterable generated fields, roles, and shapes for
Candidate and compact analytical Dataset families, plus the consumer invocation
contracts from which typed continuations are derived. It consumes the shared
predicate syntax, field-resolution rules, filter assignment matrix, and
`where(...)` lifecycle and may not create kwargs, string, callable, or
family-specific predicate grammars.

It may restrict accepted shapes further. It may not reinterpret a Dimension
bucket as an Entity sample or add another Metric source path.

### Subject, Event, and Lifecycle consumes

- Module 2's sole `PopulationDataset` family and Entity identity contract;
- membership scope and target lineage, separate from every source window;
- direct-membership and proven-unique identity-projection input admission;
- Population `membership` filtering and Event/Lifecycle `row_subset` filtering;
- raw-identity disclosure boundaries.

Module 6 owns Event/Lifecycle `select_subjects(...)` producers, their exact
selection predicates, source coverage checks, and producer quality registrations.
They return Module 2's Population family. Module 6 does not register another
membership family, redefine Population filtering, or add a Population-owned
Metric/Event/Lifecycle source namespace.

## Rejected Alternatives

### Separate filter syntax for each Dataset family

Rejected because Population, Metric, Candidate, Event, Lifecycle, and compact
result filtering would drift in boolean, null, field, Help, persistence, and
repair behavior. One `where(...)` protocol plus registered family effects keeps
the syntax shared without erasing semantic differences.

### Keyword equality shortcuts

Rejected because `where(item_id="...")` creates a second equality-only grammar,
makes generated names look equivalent to semantic identity, and cannot express
null, range, membership, or nested boolean conditions consistently.

### Python comparison and boolean operator overloading

Rejected because semantic refs may already use equality for identity and Python
boolean operators evaluate eagerly. Predicate construction uses explicit
`mv.eq`, `mv.any_of`, and related helpers rather than returning masks or
expression objects.

### Treat every filtered Dataset as a new Population

Rejected because filtering Metric, Candidate, Event, Lifecycle, or aggregate
rows selects current results and preserves the input family. Only a later
source's explicit `population=` argument may consume an exact admitted
identity-bearing row set as membership; most filtered shapes remain ineligible.

### Let the planner reorder filters freely

Rejected because predicates before and after sampling, aggregation,
materialization, Event matching, Lifecycle replay, or subject selection answer
different questions. Pushdown requires an exact equivalence proof.

### Merge semantic-authoring and analysis filtering

Rejected because `ms.where(...)` changes a reusable Metric definition while
`dataset.where(...)` changes one analysis chain. Sharing a public constructor
would conflate semantic promotion with exploratory Dataset composition.

### Mandatory explicit Population before observe

Rejected because governed Metrics already own exact Entity bindings and the
common same-Entity case has one deterministic answer. Mandatory boilerplate
would not add authority.

### Infer a common Entity through graph proximity

Rejected because path length, table proximity, and query cost are not business
authority. Cross-Entity questions require one explicit Population Entity.

### Scalar/time/segment/panel Population variants

Rejected because those concepts describe Metric output coordinates, not Entity
membership.

### `PopulationDataset.observe(...)`

Rejected because aliases create multiple source owners and duplicate Help,
signature, and repair contracts.

### Add a parallel aggregate-observation surface

Not selected for this amendment. Direct output-coordinate parameters could be a
coherent alternative, but keeping both them and the dedicated coordinate chain
would create duplicate authoring paths. This cutover keeps the lazy chain and
makes its non-materializing nature explicit. Membership and observation scopes
are independent regardless of the chosen output-coordinate syntax.

### Treat identity as a Dimension

Rejected because composite primary-key authority, uniqueness, privacy, and
membership alignment are not ordinary category-grouping semantics.

### Sample fact rows and aggregate later

Rejected because it changes Entity Metric values and overweights Entities with
more fine-grained facts. Sampling always selects Entity identities first.

### Apply Population predicates after sampling

Rejected because it changes the sample target and makes `target_rows` refer to
the wrong eligible set. Predicate-then-sample is the one canonical order.

### Align independently observed Metric rows

Rejected because independent execution can use different members, source
snapshots, scopes, and null filters. One multi-Metric observation owns one
shared Population spine.

### Sum every Entity-level value in `aggregate()`

Rejected because means, ratios, distinct counts, quantiles, weighted values,
and cumulative values generally require base or component authority.

### Reach through a materialized Dataset to recompute semantics

Rejected because materialization is an immutable execution barrier. A blocked
reaggregation must be authored before materialization.

### Explode composite identities into ordinary public key columns

Rejected because consumers could accidentally join a partial key and because
Population and selection Population identity would acquire parallel layouts.

### Infer sampling to satisfy action limits

Rejected because exact and approximate membership are different analytical
definitions. Only an explicit policy authorizes approximation.

## Vertical Acceptance Journeys

Journey fixtures must choose an explicit compatible execution/storage setup.
A retained Population or identity selection later joined to current sources uses
an engine target and reader in that same datasource domain. Exact result-only
continuations over local/object Artifacts use PyArrow reads and bounded pandas
functions; their budgets include every required retained part. Include source
pushdown, planned pandas-suffix and oversized-local-input fixtures, plus a
conflicting-domain negative fixture for source-required semantic work.
`execute()` must not be advertised as a repair unless its configured writer,
reader and the consumer's exact contract actually establish that path. No fixture
creates an internal DuckDB executor or changes a failed plan's implementation.

### Default same-Entity inference

```python
features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
    time_scope=window,
)
```

Acceptance must prove one exact inferred Entity, one implicit logical
Population node, no datasource work, one Entity identity coordinate, ordered
Metric values, and complete pre-execution row and row-set contracts.

### Explicit filtered sampled Population

```python
queries = (
    session.population(query_execution, time_scope=window)
    .where(mv.eq(workload_type, "interactive"))
    .sample(mv.engine_sample(target_rows=100_000, seed=42))
)

features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
    population=queries,
    time_scope=window,
)
```

Acceptance must prove predicate-before-sample ordering, one target-Population
lineage edge, one Entity-safe sample shared by every Metric, and independent
observe-level scope without resampling or reselection.

### Coordinate transitions

```python
daily_by_region = (
    session.observe(metrics=[revenue, order_count], time_scope=window)
    .with_dimensions(region)
    .with_time_axis(order_time, grain=mv.grain("day"))
    .aggregate()
)
```

Acceptance must prove the exact transition:

```text
metric/entity@v1
  -> metric/entity-dimension@v1
  -> metric/entity-dimension-time@v1
  -> metric/dimension-time@v1
```

For this journey both Metrics have the same order computation root and a
governed common time axis. The final Dataset retains Population authority while
removing the public Entity coordinate, with no intermediate Entity execution.

### Filter phase and membership boundary

```python
features = session.observe(
    metrics=[revenue, cpu_seconds],
    time_scope=window,
)

selected_entities = features.where(
    mv.gte(cpu_seconds, 60),
)

selected_aggregate = selected_entities.aggregate()

regional_aggregate = features.with_dimensions(region).aggregate()
selected_regions = regional_aggregate.where(
    mv.gte(revenue, 1_000_000),
)

followup = session.observe(
    metrics=[lifetime_value],
    population=selected_entities,
)
```

Acceptance must prove that the first predicate selects Entity observations
before reduction, the second selects completed region rows after reduction,
and only the explicit `population=selected_entities` source boundary consumes
the first row set as membership. `selected_regions` must be rejected as a
Population input. Both `where(...)` calls must use the same predicate
normalization and error protocol while retaining different authored positions
and filter effects.

Acceptance must also prove that kwargs, Python comparisons, raw strings, and a
field absent from the current row contract fail before datasource work; an
same-domain expression construction must preserve the public definition fingerprint
and lineage.

### Component-aware logical aggregation

```python
conversion = (
    session.observe(metrics=[conversion_rate], time_scope=window)
    .with_dimensions(channel)
    .aggregate()
)
```

Acceptance must prove numerator and denominator recomputation at channel
coordinates, not a sum or average of per-Entity ratios.

### Materialization barrier rejection

```python
features = session.observe(metrics=[p90_latency], time_scope=window).execute()
features.aggregate()
```

Acceptance must prove a local structured failure for the unregistered
Entity-axis quantile fold, with a repair to aggregate before
materialization. No datasource connection, Run, or reach-through execution may
occur for the rejected construction.

### Materialized additive fold

```python
features = session.observe(metrics=[revenue]).execute()
overall = features.aggregate()
```

Acceptance must prove that the downstream definition consumes only the
immutable scan leaf and uses the exact registered additive fold. It must not
re-execute the original Metric sources.

### Identity-bearing Dataset as Population input

```python
selected = entity_metrics.where(...)
followup = session.observe(metrics=[lifetime_value], population=selected)
```

Acceptance must prove exact Session, Entity, and identity-signature matching;
same-plan identity projection and semi-join consumption for logical selection;
immutable retained-identity projection after materialization; no intermediate
selection Population; and no Dataset-owned `observe(...)` alias.

### Ambiguous Entity failure

```python
session.observe(metrics=[order_revenue, account_health])
```

Acceptance must prove a no-I/O structured failure when roots resolve different
Entities. The error must name exact inputs and only real repair candidates; it
must not select an Entity or Relationship path heuristically.

### Independent cohort and observation periods

```python
january_users = session.population(
    users, time_scope=january, time_dimension=registered_at,
)
february_spend = session.observe(
    revenue, population=january_users,
    time_scope=february, time_dimension=paid_at,
)
```

Fixtures include a January registrant with February purchases, a February
registrant, and a January registrant with no February facts. Acceptance keeps
only January identities, measures only February contributions, and retains the
all-null member. Repeat with a recovered Population and an Event-selected
Population. Omitting observation scope must not silently inherit January;
missing required cumulative endpoints fail before execution.

### Explicit analysis Entity across different computation roots

```python
customers = session.population(customer)
features = session.observe(
    metrics=[line_revenue, order_count, average_order_value],
    population=customers,
    time_scope=window,
)
```

Use one customer, two orders with line counts two and three, and total line
revenue 100. With a governed shared order-time alignment, expect order count 2
and average order value 50. Compare each branch against an independent reference
aggregation. The same roots without explicit Population must fail inference;
ambiguous paths, missing allocation, or unsafe fanout also fail locally.

### Coordinate-bearing membership refinement

```python
selected = features.with_dimensions(region).where(mv.eq(region, "EU"))
next_period = session.observe(
    revenue, population=selected, time_scope=next_window,
)
```

Prove admission for a governed to-one region and rejection for a one-to-many
category or true customer-by-day relation. Run logical and recovered-materialized
variants. A coincidentally unique observed sample must not establish a new proof.
Also select Event dropouts, filter their resulting Population by region, and
observe a separately authored follow-up window without rematching a materialized
journey or bypassing its completeness gate.

### Selection keeps exact contributions and denominators

Use A = 8/10 and B = 1/10. Filtering Entity conversion rate above 50% followed by
logical aggregation must return 80%. Repeat after materialization using retained
components, including a zero-denominator Entity and an empty selection with the
Metric's exact null/empty rule.

For customer-by-day rows, select only one day's threshold-passing observation.
A following reduction must not restore other dates or reevaluate the threshold
at the reduced grain. Test a later coordinate refinement with a valid mapping
and a locally rejected ambiguous mapping. Public metadata must expose no raw
selected keys. Original Population provenance is unchanged in every case.

### Read, recover, and fold sufficient state

```python
by_region = (
    session.observe(conversion_rate, time_scope=window)
    .with_dimensions(region)
    .aggregate()
    .execute()
)
overall = by_region.rollup(drop_dimensions=(region,)).execute()
```

Use unequal region denominators so averaging projected rates yields a different
answer. After fresh-process recovery, the exact component merge must equal the
independent whole-population reference. Repeat for mean and weighted mean,
projection and filtering. Missing required parts block publication; corruption
of a committed required part fails consumption without any origin query.

### Time removal preserves selected-period coverage

```python
daily = (
    session.observe(revenue, time_scope=window)
    .with_time_axis(paid_at, grain=mv.grain("day"))
    .aggregate()
    .execute()
)
window_total = daily.rollup(drop_time=True).execute()
```

Verify complete additive totals, ratio component merges, empty singleton folds,
and partial-period coverage. Filter daily rows before time removal and verify
only selected periods contribute, with no complete-window claim. Reject
`grain=...` together with `drop_time=True`, missing time coordinates, and a
Metric without the exact required temporal fold. Cold execution reads only
primary rows and registered parts.

### Stable identity and versioned membership

Define a snapshot Entity with `primary_key=["user_id"]`, two complete daily
versions and changing membership. A finite membership scope selects the one
snapshot immediately before its excluded end and emits each selected `user_id`
once. Test both an end exactly at midnight and an end inside a day using the
declared timezone/calendar. The source remains keyed by identity plus version;
it never adds the version field to the Population identity.

Reject unscoped inference, a missing expected partition, duplicate identities
inside a snapshot, conflicting validity intervals and any attempted
deduplication repair. Missing identities in a complete selected snapshot are not
carried from prior snapshots. Reuse the selected Population for a disjoint
Metric observation window, and repeat from a cold Artifact without version
reselection. A validity boundary fixture proves the distinct exact-instant and
excluded-endpoint interpretations. A date that belongs to a non-versioned
business identity remains part of `K`.

### Fold order and contribution partition counterexamples

For two devices with samples `[10, 0]` and `[0, 10]`, compute the spatial sum
then the window maximum: expect 10. Projected per-device peaks `[10, 10]` must
not admit a materialized Entity sum of 20. Repeat with asynchronous last samples,
unequal mean coverage and percentile folds. An admitted exact-state method must
match the same represented source calculation; otherwise reject before data
work, including after cold recovery.

For one 100-unit order in two overlapping tags, retain the valid 100-per-tag
view but reject a target-total sum of 200. Exercise a separately authored
conserving allocation and prove its target total is 100. Apply row selection
before each fold and verify that neither dropped coordinates nor original
Population lineage restores excluded contributions. These checks apply equally
to source Ibis and any admitted pandas implementation.

## Acceptance Criteria

The amendment is complete when these contracts are reviewable and later tested:

1. Population has one exact Entity, complete non-null tuple identity, proven
   uniqueness, complete selection truth, and one common family across producers.
2. Membership selection scope and Metric observation scope are independent;
   disjoint windows work, omitted observation scope never inherits selection time.
3. Default Entity inference is exact and local. Explicit membership resolves the
   analysis Entity before validating different component computation roots.
4. One shared membership/coordinate spine prevents fanout and Metric-local nulls
   from removing other observations; all-null members remain eligible.
5. Registered Entity-present inputs retain membership admission after safe to-one
   coordinate enrichment. True Entity-by-time/many-valued inputs fail locally.
6. One sealed unbound predicate vocabulary, exact bound fields, literal/null
   rules, and family-specific effects apply consistently before any datasource work.
7. Selection binds full upstream coordinates and component contributions. Ratios
   use selected component denominators; original Population is provenance only.
8. Selection cannot move across aggregation, sampling, materialization, coordinate
   repartition, or a population-input boundary without exact equivalence.
9. Event/Lifecycle selection Populations admit the same governed Dimension filter
   as explicit Populations; filtering cannot bypass uncertain selection truth.
10. Coordinate chains retain one canonical observe source and need no intermediate
    Entity execution. All eight Metric shapes remain completely typed before execution.
11. Ratio, mean, and weighted-mean parts are required by the exact supported graph,
    filtered/projected consistently, reconciled, committed atomically, and recoverable.
12. Materialized consumers use retained rows/parts only. Missing states fail without
    origin queries. Logical recomputation preserves exact selected contributions.
13. `rollup` has one current-row fold meaning for both states; `drop_time=True`
    requires an exact temporal fold and preserves partial/selected-period coverage.
14. Empty scalar outputs retain singleton row-set semantics and exact empty-fold rules.
15. Sampling remains explicit, Entity-safe, shared per action, and ordered after
    membership predicates; downstream source windows do not change sampling units.
    Explicitly shared and separately authored equal sampling branches have
    different combined definition fingerprints, while reconstructing the same
    sharing relation preserves identity without hashing handles or sample rows.
16. Identity, source binding, scopes, selection, coordinates, component state, and
    fold decisions participate in definition identity and bounded disclosure.
17. Raw identities and sensitive predicate/source literals remain outside metadata,
    errors, Evidence, cards, and public fingerprints over realized membership.
18. All affected Help, registration, runtime, cold-recovery, drift, and acceptance
    inventories use the amended contracts with no legacy aliases or dual paths.
19. Entity identity excludes pure version coordinates; version rows are validated
    before temporal resolution and Population identities after it. Versioned
    membership has the fixed explicit endpoint-view contract, independently of
    Metric and domain time anchors.
20. Additivity summaries cannot bypass contribution partition, ordered temporal
    folds or retained-state proof. The device-peak and overlapping-tag
    counterexamples fail admission wherever exact state is unavailable.

## Frozen Module Decisions

The accepted 2026-09-07 identity-and-algebra amendment additionally replaces the
source-row interpretation of Entity `primary_key`, unscoped membership inference
for versioned Entities, blanket semi-additive Entity sums and additive folding
across unproven overlapping coordinates. The Semantic Object Model owns the
intrinsic definitions; this module owns their operation-specific admission and
membership endpoint binding. This amendment changes design intent, not the
currently exported eager implementation.

The 2026-09-05 amendment supersedes earlier choices that coupled membership and
observation time, required equal computation roots before explicit-Population
admission, restricted identity projection to Entity-only Metric shape, retained
a separate SubjectSet family, deferred basic sufficient state, or prohibited all
time-axis removal.

The frozen choices are:

1. `PopulationDataset` is the sole governed Entity-membership family.
2. Population selection and Metric observation own independent temporal scopes.
3. `session.observe(...)` remains the sole lazy Metric source; dedicated coordinate
   operators remain the only output-grain declaration path in this cutover.
4. Explicit Population determines analysis Entity; per-component governed roots
   remain distinct and must each prove a safe contribution path to that Entity.
5. Default inference requires one exact common root; there is no heuristic anchor.
6. Population input uses direct membership or registered unique Entity projection,
   never inferred distinct, Python identity collection, or a conversion artifact.
7. One `where(...)` syntax has membership or current-row effects. Row selection
   binds complete observation coordinates and component contributions, not a
   hidden new denominator or an implicit member projection.
8. Population reachable-field filtering remains single-valued, atemporal, and
   governed; temporal/collection selection requires an owned observation contract.
9. Sampling remains one optional explicit Entity sample after membership predicates.
10. Logical aggregation recomputes only selected governed contributions; materialized
    aggregation and every rollup use exact retained state without origin replay.
11. Basic ratio/mean/weighted-mean state is part of v1; heavier distinct/distribution
    folds require explicit registrations and never arise from projected values alone.
12. `rollup(drop_dimensions=..., grain=..., drop_time=...)` changes only retained
    coordinates under exact per-axis folds and preserved coverage.
13. Predicate values remain reusable and unbound; Dataset binding and every
    parameterized source value are immutable construction-time definition facts.
14. Every producer resolves exact quality, validation, Evidence, and retained-part
    contracts before execution; Population and Metric production creates zero Findings.

Future changes require an amendment to this owning module before downstream
compiler, runtime, or operator designs depend on them. This amendment authorizes
design synchronization only, not runtime implementation.

## Final Boundary

Population answers who is eligible and why. Observation scope answers which
Metric facts are measured. Coordinates answer where values are positioned.
Row selection determines the exact contributions retained; aggregation applies
the governed Metric equation to those contributions at the requested grain.

Membership provenance, selected contributions, and computational denominators
remain separate authorities. Logical fusion avoids unnecessary Entity execution;
retained sufficient state supports continued analysis after an explicit read.

## 2026-09-07 Ibis Pushdown and Pandas Suffix Amendment

The accepted Module 3 replacement preserves every Population, coordinate,
filter-phase, contribution, sampling and retained-state rule in this document.
At `execute()`, contiguous eligible source operations compose through Ibis;
known source ineligibility may start only an exact registered pandas suffix.
Logical/Materialized semantic admission alone does not authorize federation,
local identity collection or replay of an Artifact's origin. Compile or runtime
failure never changes the selected boundary.

Population membership, logical Metric-graph evaluation, current Dimension
enrichment and identity projection remain source-required. Their sources,
Materialized readers and explicit current semantic dependencies must share the
admitted datasource domain. Known conflicts fail during construction; reader or
connection facts requiring live resolution are checked after Run admission and
before data work. A Materialized Population combined with current Dimensions
therefore needs a compatible engine reader/source binding. A local Artifact
and remote source are not made compatible by calling `execute()` again. A
suggested materialization repair must be reachable and writable/readable in the
required domain under the configured storage policy.

Result-row filtering and exact Entity-axis/coordinate retained folds may use
pandas only where their registered input contract admits the complete current
rows and retained sufficient state. The planner prefers source Ibis while that
exact lowering is eligible. Engine Artifacts remain immutable source scans;
local/object Artifacts use PyArrow into bounded pandas inputs. Local size guards
cover every required retained part and intermediate growth; there is no promise
of computation over arbitrarily large local Artifacts. No internal DuckDB
executor or per-operator Arrow serialization exists, and all dependent work
after a local boundary stays in pandas.

Explicitly shared Population and contribution handles retain their semantic
sharing. Their owner-bound realization requirements feed Dataset Core's sole
canonical definition identity, including sharing across otherwise equal logical
operands. Distinct authored sampling realizations remain distinct; matching
standalone fingerprints do not coalesce them. Required volatile realizations
still need an exact source fence; general compiler CSE and a global one-query
guarantee are not prerequisites.
