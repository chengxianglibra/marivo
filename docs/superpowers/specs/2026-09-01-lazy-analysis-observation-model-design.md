# Lazy Analysis Observation Model Design

Date: 2026-09-01

Revised: 2026-09-04

Status: accepted

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

- public Dataset state, state-specific actions, or generic row-contract
  mechanics;
- private logical node schemas, join order, relational lowering, or engine
  placement;
- datasource capability negotiation or local-stage limits;
- durable sampling realizations, storage receipts, Runs, Artifacts, Evidence,
  Findings, quality publication, or recovery;
- correlate, compare, discovery, forecasting, or inferential test methods;
- the exact filterable generated-field inventory of Candidate, Event,
  Lifecycle, and compact analytical Dataset families;
- Event or Lifecycle subject matching;
- the family-specific `SubjectSet` row contract.

Those modules consume the exact logical authority defined here. They may not
reinterpret Population membership or turn aggregation coordinates into a new
Population kind.

## Upstream Invariants

The observation model accepts these Dataset Core decisions as fixed:

1. `PopulationDataset` and `MetricDataset` are sealed nominal Dataset families;
2. construction is lazy, immutable, Session-owned, and performs no datasource
   work;
3. every output row contract and public schema is complete before execution;
4. every analytical family has paired logical and materialized public state
   types with the same family id and row contract;
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

- exact Entity refs and ordered primary keys;
- Metric root Entity and recursive component bindings;
- Dimension and time-Dimension Entity bindings;
- Relationship cardinality and fanout policy;
- Metric aggregation, additivity, temporal fold, status-time axis, unit, and
  component graph;
- current-catalog normalization and readiness.

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
Metric root resolves to the same exact governed analysis Entity. It never
chooses an Entity because it is physically nearby, appears first, has a short
join path, or is cheaper to query.

An explicit Population may deliberately select another Entity only when every
Metric has one unique governed, fanout-safe semantic path to that Entity. The
explicit Entity resolves the analytical question; it does not resolve an
ambiguous Relationship path or authorize unsafe aggregation.

### Keep membership scope separate from output coordinates

Population scope determines which Entity instances and source facts are
eligible. `with_dimensions(...)` and `with_time_axis(...)` determine how
eligible Metric contributions are positioned in output rows. `aggregate()`
removes the Entity coordinate but preserves the same Population and
target-Population authority.

A time scope therefore does not imply a time-series Dataset, and a time
coordinate does not widen or replace the Population.

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
at the requested coordinates. It is not a blind reduction over already
projected Entity values. Ratio, weighted mean, count-distinct, percentile,
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
  authority_rules[]
```

Filtering preserves the owning family and qualified shape while producing a
new current-row definition. Its downstream continuations are derived by
matching that resulting Dataset contract against source and operator consumer
admission contracts; `FilterAdmissionV1` does not copy a continuation list.

Each authority rule is a closed tuple:

```text
FilterAuthorityRuleV1
  input_state
  field_resolution
  admitted_field_roles[]
  authority_mode
```

The initial `input_state` values are `logical` and `materialized`.
`field_resolution` is either `retained_row` or `reachable_semantic`; the latter
is available only to the Population specialization defined below. The
authority-mode vocabulary is supplied by the Materialization Runtime contract.
Filtering uses these two existing modes:

```text
semantic_current   require exact current semantic authority beyond or within the logical input
materialized_only  evaluate only fields retained by the immutable Artifact leaf
```

The complete shared matrix is:

| Input state | Field resolution | Authority mode | Admission |
| --- | --- | --- | --- |
| logical | retained current-row field | `semantic_current` | registered families and shapes |
| logical | reachable semantic field absent from rows | `semantic_current` | Population only |
| materialized | retained current-row field | `materialized_only` | registered families and shapes |
| materialized | reachable semantic field absent from rows | `semantic_current` | Population only |

Family modules may remove a row from this matrix for a shape or field role. They
may not add another authority mode or reinterpret one. `dataset.contract()`
projects the one exact rule selected for the current Dataset state and operand;
it never reports one mode merely from the field role.

The initial `effect` vocabulary is:

```text
membership
row_subset
```

`PopulationDataset` uses `membership`. Ordinary Metric, Delta,
Association, Candidate, Event, Lifecycle, and other analytical result families
use `row_subset`. `SubjectSet` does not admit `where(...)` in the first cutover.
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
schema or row contract. `get(name)` resolves an exact current public field name,
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
| `MetricDataset[metric/entity@v1]` | select current Entity observation rows | exact target Entity, identity signature, original Population lineage | directly admitted through `population=` by exact identity projection |
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
Logical execution may observe a newer source snapshot on a later action, but it
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
observation rows but retains the original Population as target/denominator
lineage plus the exact selection predicate. On an already reduced,
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

Physical pushdown does not change the public operator order, filter effect, or
lineage.

### Materialized filtering authority

A predicate over fields present in a logical Dataset row contract is
`semantic_current`: it evaluates as part of that exact logical input definition
under the already admitted current semantic and datasource authority.

A predicate over fields present in a materialized Dataset row contract is
`materialized_only`: the new logical Dataset scans and filters the immutable
Artifact without requiring original semantic sources. An exact typed ref may
match its retained field identity after catalog drift; a current catalog entry
is not required. `dataset.fields.get(...)` remains the canonical selector
for retained generated fields.

A Population membership predicate that introduces one admitted current
membership-stable Dimension is `semantic_current`: it starts from the immutable
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

The Entity must have one non-empty, ordered, unique compiled primary-key
signature. Marivo never asks the caller to repeat physical key column names and
never substitutes a Dimension for missing Entity identity.

`time_scope` is an optional literal half-open interval `[start, end)`. Its end
is excluded exactly as authored. The scope restricts eligible membership and
facts; it does not add a time coordinate.

`time_dimension` is legal only with `time_scope`. It selects the exact governed
reference axis used to apply that scope. If omitted, construction uses this
closed order:

1. the Entity's exact declared default time Dimension;
2. the only compatible reachable time Dimension, if exactly one exists;
3. otherwise a structured ambiguity or missing-axis failure.

Marivo does not select the first Metric's time axis or infer temporal meaning
from a physical type or column name.

### Population family contract

`PopulationDataset` has one initial family-qualified shape:

```text
population/entity-membership@v1
```

Its family payload contains:

```text
PopulationContractV1
  entity_ref
  identity_signature[]
  reference_scope
  reference_time_dimension_ref
  predicates[]
  sampling
  target_population_definition_fingerprint
```

The payload is immutable and complete before execution. `sampling` is a closed
exact-or-approximate logical request, not an execution receipt.

Every Population operator returns a new logical `PopulationDataset`, including
when its input is materialized. In that case the immutable membership Artifact
is a private scan-leaf input to the new definition; the operator does not mutate
the Artifact-backed value or preserve materialized state on its output.

`target_population_definition_fingerprint` is absent on an exact unsampled
root. On a sampled Population it identifies the exact unsampled input
definition. Repeated downstream observation retains that target lineage. The
field never contains realized identities or a membership digest.

### Primary-key row contract

One Population row is one exact Entity primary-key tuple.

The public coordinate column is:

```text
entity_identity
```

Its logical type is a fixed-arity tuple whose components follow the compiled
primary-key signature in declaration order. A one-column primary key still
uses a one-element tuple. Scalar-versus-tuple dual layouts are forbidden.

The row contract requires:

- `entity_identity` is non-null;
- every component is non-null and conforms to its governed logical type;
- rows are unique by the complete tuple;
- cardinality is zero-or-more and unknown before execution;
- default analytical ordering is unordered;
- canonical presentation ordering uses the tuple's registered component order.

Composite components are not exploded into independently joinable public
columns. This prevents partial-key membership and keeps `PopulationDataset`,
`SubjectSet`, multi-Metric observation, and downstream Entity alignment bound
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

Sampling preserves the `PopulationDataset` family and row contract but changes
the definition fingerprint and adds target-Population lineage.

## Default Population Inference

### Metric analysis-Entity resolution

Every admitted Metric observation input resolves one exact analysis Entity:

- a base Metric uses its compiled `root_entity`;
- a derived Metric recursively requires a coherent governed root Entity across
  all components;
- a cumulative Metric retains the compatible root Entity of its base;
- a Session-owned runtime Metric expression resolves the governed Entity
  binding of its leaves and composition;
- an unresolved, empty, or ambiguous Entity set is not an observation input.

For a multi-Metric call, the default Population is inferred only when all
resolved analysis Entities are exactly equal. A Relationship-connected set of
different Entities is not "close enough" for default inference.

Inference performs no datasource work and produces a real private logical
`PopulationDataset` input node. Although the node is implicit in user syntax,
its complete contract contributes to the Metric Dataset definition
fingerprint, lineage, contract, errors, and later materialization authority.

Dimensions and time Dimensions participate as compatibility constraints, not
late Population inference candidates. Because the canonical source call has no
Dimension output arguments, Metric bindings determine the initial default
Entity. A later `with_dimensions(...)` or `with_time_axis(...)` call validates
its semantic Entity and Relationship binding against that already fixed
Population. It never re-runs inference or replaces the Population with the
Dimension's Entity.

### Default scope

When `population=` is omitted, `time_scope` and `time_dimension` on
`session.observe(...)` are applied to the inferred Population using the same
rules as explicit `session.population(...)`.

When both are omitted, the default means all semantically eligible Entity
instances under current versioning and source authority. This may be logically
unbounded. Construction remains legal, while action policy may later require a
narrower scope or explicit sampling.

Filters embedded in an authored Metric definition do not become Population
predicates. They affect only that Metric value, leaving the shared membership
spine intact.

### Inference failures

Default inference fails before Dataset construction when:

- a Metric has no exact analysis Entity;
- derived or runtime components disagree on Entity authority;
- ordered Metric roots resolve different Entities;
- a scoped request has no compatible reference time axis;
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
    | LogicalSubjectSet
    | MaterializedSubjectSet
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
- no `slice_by=`; filter Population membership before observation or use the
  shared `MetricDataset.where(...)` operation after observation;
- no `cohort=`; pass one exact `PopulationInput` through `population=`;
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

The argument matrix is exact:

| `population` | `time_scope` / `time_dimension` | Result |
| --- | --- | --- |
| omitted | omitted | infer the all-eligible default Population |
| omitted | supplied consistently | infer the default Entity and construct its scoped Population |
| `PopulationDataset` | both omitted | consume that exact Population definition or Artifact authority |
| `SubjectSet` | both omitted | consume that exact selected-subject membership authority |
| `MetricDataset[metric/entity@v1]` | both omitted | consume the exact current Entity row set by identity projection |
| `CandidateDataset[candidate/entity-outlier@v1]` | both omitted | consume the exact current Candidate row set by identity projection |
| any explicit `PopulationInput` | either supplied | reject conflicting membership owners |

There is no precedence rule, intersection fallback, or implicit rescoping. The
repair for the last row is to construct the desired scope before the call and
pass only `population=`.

Every explicit value selects one immutable `PopulationInputContractV1` during
source construction:

```text
PopulationInputContractV1
  source_family
  source_shape
  mode = direct_membership | identity_projection
  entity_ref
  identity_field_id
  identity_field_role = entity_identity
  identity_signature[]
  input_authority_token
  scope_and_sampling_authority
```

`PopulationDataset` and `SubjectSet` use `direct_membership`.
`metric/entity@v1` and `candidate/entity-outlier@v1` use
`identity_projection`. The selected contract and exact input Dataset definition
fingerprint participate in the consuming source definition fingerprint. No mode
creates or returns another public Dataset.

### Explicit `PopulationDataset` admission

An explicit Population is admitted only when:

- it belongs to the same Session;
- it has the exact current `population/entity-membership@v1` contract;
- its Entity identity signature is complete;
- every Metric is computable at that exact Entity under one unique governed
  semantic path;
- all scope, time-axis, versioning, Relationship, and fanout requirements are
  mutually compatible;
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

### Explicit `SubjectSet` admission

`SubjectSet` is a sibling Dataset family, not a Python subclass of
`PopulationDataset`. It is admitted through `PopulationInputContractV1` with
`mode = direct_membership`.

Admission requires:

- same Session ownership;
- one exact subject Entity ref;
- an ordered identity signature exactly equal to that Entity's primary key;
- a ready logical identity-producing definition or an immutable materialized
  identity Artifact;
- scope and sampling authority compatible with every Metric;
- one unique safe Metric path to the subject Entity.

The observation preserves SubjectSet lineage and target-Population authority.
It does not copy raw identities into metadata. A logical SubjectSet may lower
as a same-plan semi-join; a materialized SubjectSet is consumed as an immutable
identity scan leaf. Those lowering details belong to later modules.

### Explicit identity-bearing analysis Dataset admission

Only `metric/entity@v1` and `candidate/entity-outlier@v1` are admitted with
`mode = identity_projection`. Their row contracts must already bind:

- one exact target Entity ref;
- one non-null fixed-arity field registered with the stable field id and
  `entity_identity` role required by the exact shape;
- an identity signature exactly equal to that Entity's primary key;
- uniqueness by the complete identity tuple;
- complete current-row authority under the exact logical definition or
  materialized Artifact.

The input Dataset remains a Metric or Candidate Dataset. Passing it through
`population=` is the explicit user decision to consume its current row set as
membership; `where(...)`, `rank(...)`, and `limit(...)` create ordinary new
definitions in that same family and do not independently create a Population or
`SubjectSet` authority.

A logical input contributes one same-plan identity projection and semi-join. A
materialized input contributes one immutable scan-leaf projection of its
retained identity field. The compiler may not collect identities, scan unrelated
value fields, silently deduplicate rows, or reach through an Artifact to replay
its origin. Null, duplicate, missing, stale, or signature-incompatible identity
authority fails atomically. An empty but complete current row set is a valid
empty membership input.

Other Metric shapes have removed the Entity axis. Other Candidate shapes retain
coordinates or analytical items rather than one exact Entity identity. They are
rejected without source replay or inferred membership. Physical column names,
display names, and merely similar identity-shaped fields never establish this
role.

### Local construction sequence

Calling `session.observe(...)` performs only:

1. normalize the ordered Metric inputs;
2. normalize or infer one membership authority;
3. validate Session, Entity, identity, scope, Relationship, and semantic
   compatibility;
4. construct one ordered Metric-binding set;
5. derive the complete Entity-grained row contract and schema;
6. capture row-dependent action-time requirements;
7. derive the definition fingerprint and bounded lineage;
8. return one immutable logical `MetricDataset`.

It does not connect to a datasource, enumerate members, estimate cardinality,
create a Run, publish an Artifact, or choose a physical plan.

## Entity-Grained Multi-Metric Observation

### Family contract

The Entity observation shape is:

```text
metric/entity@v1
```

Its family payload is conceptually:

```text
MetricObservationContractV1
  population_binding
  target_entity_ref
  identity_signature[]
  metric_bindings[]
  dimension_coordinates[]
  time_coordinate
  entity_axis = present
  aggregation_contracts[]
```

Every ordered Metric binding contains:

```text
metric_key
metric_identity
value_field_id
value_column
semantic_type
unit
aggregation_contract
nullable
```

`metric_key` is a stable semantic identity, not a display label. Catalog
Metrics derive it from the exact Metric ref. Runtime expressions derive it from
their governed expression fingerprint. Duplicate semantic identities are
rejected even if labels differ.

Value columns preserve request order. Public names use the governed Metric name
or runtime label and the Dataset Core's deterministic collision normalization.
Display-name changes do not alter `metric_key` or value identity.

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
original Population as target/denominator lineage, and records the selection
predicate separately. Filtering after `aggregate()` selects completed
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
`high_resource` remains a Metric Dataset. No intermediate SubjectSet, detached
selection, or locally collected identity list is created.

A materialized Metric Dataset may be filtered only by fields retained in its
committed row contract. It produces a new logical Dataset over the immutable
scan leaf and cannot regain an absent Dimension, Metric component, or source
field from lineage.

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
- compatibility with the Population reference scope;
- declared physical granularity and timezone/calendar authority;
- requested grain is not finer than the semantic source permits;
- semi-additive status-axis and cumulative-axis constraints;
- one common time coordinate for all Metric roots.

`with_time_axis(...)` partitions eligible contributions into governed buckets.
It does not create or widen the Population scope. If the Population has a
finite `time_scope`, bucket membership is clipped to that exact half-open
interval. A scope end is never advanced to create an inclusive last day.

If no finite scope exists, the Dataset may remain logically unbounded. Action
admission owns execution bounds; `with_time_axis(...)` does not insert an
implicit window.

### Coordinate spine

Dimension and time coordinates are derived from one governed coordinate spine
rooted at the Population Entity, independently of Metric non-nullness.

The spine contains distinct coordinate tuples reachable for eligible members
under the selected scope and semantic Relationships. It is not:

- the intersection or union of non-null Metric rows;
- a full Dimension-by-time Cartesian product;
- a calendar densification request;
- a cheapest physical join path;
- a post-hoc alignment of independently executed Metric results.

Metric branches are evaluated at the exact spine coordinate and joined
one-to-one. Missing Metric contributions stay null. A Population member with no
reachable selected coordinate has no row in the coordinate-shaped Dataset, but
the retained Population authority still owns its denominator and later coverage
accounting.

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
| `metric/entity@v1` | `metric/scalar@v1` | explicit singleton contract |
| `metric/entity-dimension@v1` | `metric/dimension@v1` | ordered Dimension tuple |
| `metric/entity-time@v1` | `metric/time@v1` | exact time bucket |
| `metric/entity-dimension-time@v1` | `metric/dimension-time@v1` | Dimension tuple + time bucket |

The scalar shape has exactly one logical row for the Population definition,
including an empty Population. It uses the Dataset Core singleton exception
and adds no synthetic public `_scalar` column.

All non-scalar reduced rows are unique by the exact ordered coordinate key.
Default ordering remains unordered unless a later registered operator declares
an order.

`aggregate()` retains:

- Population definition and target-Population lineage;
- Entity identity authority as the reduced analytical unit;
- reference scope and sampling intent;
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
) -> LogicalMetricDataset:
    ...
```

`rollup(...)` is admitted only on Entity-reduced Metric shapes and requires at
least one non-empty `drop_dimensions` tuple or `grain`. It may remove retained
Dimension coordinates, replace one retained time grain with an exact coarser
grain, or do both in one coordinate transition. It cannot remove the time
coordinate, introduce a new Dimension or time axis, refine a grain, accept a
generic axis selector, or change Population membership.

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
fold node after its upstream logical relation and may receive an equivalent
compiler pushdown. A Materialized input creates the same fold over its exact
immutable Artifact scan leaf. The Logical branch may not reinterpret rollup as
a fresh Metric-graph recomputation, and the Materialized branch may not reach
through origin lineage.

The admitted Entity-reduced transitions are:

| Input shape | Request | Output shape |
| --- | --- | --- |
| `metric/dimension@v1` | drop one or more retained Dimensions | `metric/dimension@v1` or `metric/scalar@v1` |
| `metric/time@v1` | coarsen the retained grain | `metric/time@v1` |
| `metric/dimension-time@v1` | drop retained Dimensions | `metric/dimension-time@v1` or `metric/time@v1` |
| `metric/dimension-time@v1` | coarsen grain, optionally dropping Dimensions | `metric/dimension-time@v1` or `metric/time@v1` |

A request containing both arguments canonicalizes to time-grain folding within
the complete original Dimension tuple, followed by Dimension folding at the
target grain. It is semantically equal to the corresponding two-call chain and
uses the same normalized semantic nodes; authored occurrence paths remain
available only for diagnostics. A Metric whose folds cannot compose in that
order is rejected. Cumulative Dimension folding additionally requires equal
evaluation ends and compatible coverage across every combined row.

Every named Dimension must be an exact distinct retained coordinate. The
target grain must be strictly coarser than the current grain under one exact
calendar/containment contract. All Metrics in an arity-N Dataset must admit the
same requested coordinate transition; no Metric column may be omitted or
silently approximated.

Each Metric binding resolves one exact registered fold for every removed
coordinate:

| Retained Metric authority | Admitted rollup fold |
| --- | --- |
| additive `sum` / `count` | `sum` |
| `min` / `max` | matching extremum |
| semi-additive | only its declared fold on the exact removed axis |
| additive linear composition | only when the composed retained value is proven additive |
| mean, weighted mean, ratio | only a registered merge over retained named sufficient-state bindings |
| `count_distinct` | only a registered exact mergeable distinct state; projected counts never sum |
| median, percentile, distribution statistic | only a registered exact mergeable distribution state |
| cumulative time series | declared period-end `last` fold with exact ordered time and evaluation-end authority |
| opaque or unresolved value | rejected |

A cumulative `last` fold selects the final retained source bucket within each
target period, preserves that row's exact evaluation end, and marks a target
period partial unless coverage proves the source buckets reach its complete end
boundary. It never presents a mid-period last observation as a complete
calendar period. Dimension reduction of cumulative values requires a separate
exact fold for that Dimension axis; the time `last` rule does not authorize a
Dimension sum.

When dropping the final Dimension produces `metric/scalar@v1`, the output keeps
the common explicit singleton contract even when the current input has zero
rows. Each Metric's fold registration must define its empty-input identity or
null result. A missing empty-fold rule rejects the scalar transition rather
than returning zero rows or inventing a default value.

Construction derives the complete output coordinates, row key, schema,
coverage requirements, per-Metric fold ids, and action-time checks. Definition
identity binds the exact input authority token, ordered dropped Dimension refs,
target grain/calendar identity, fold-contract versions, and output row
contract. Unsupported folds fail locally with a repair to author and execute a
fresh observation at the target coordinates; no error suggests replaying a
Materialized origin.

Arbitrary time-axis deletion and whole-window scalarization are absent from the
first-cutover rollup contract. A caller that needs a scalar over a governed
window authors that scalar observation explicitly so its temporal aggregation
meaning remains owned by the Metric graph.

## Aggregation Admission

### One closed aggregation contract per Metric

Every Metric binding carries one normalized coordinate-aggregation contract
derived from current semantic authority:

```text
MetricCoordinateAggregationV1
  logical_recompute_mode
  materialized_fold_by_reduced_axis
  required_components[]
  required_time_axis
  status_time_contract
  cumulative_contract
  blockers[]
```

This is semantic admission metadata, not a private physical plan. It is complete
enough to decide whether a coordinate transition is meaningful and whether a
materialized row set is sufficient.

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
| opaque Tier-2 expression | admitted only when its declaration publishes an exact reaggregation contract | otherwise fail with semantic-authoring repair |

Backend support is an action-time planner requirement. The local Dataset may be
constructed when semantic admission is exact but physical capability is not
yet selected; `contract()` exposes that requirement. Missing semantic authority
fails during construction.

### Semi-additive rules

A semi-additive Metric must retain its exact `status_time_dimension` and
governed fold.

- Dimension-only grouping is admitted across non-time coordinates.
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
evaluation end. A finite Population reference-scope end is therefore required.
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
| additive `sum` / `count` | `sum` |
| `min` | `min` |
| `max` | `max` |
| semi-additive value reduced only across a non-time Entity axis | `sum` |
| additive linear composition | admitted only when the composed value is itself proven additive |
| `count_distinct` | admitted only when the distinct identity is exactly the Population Entity key and disjointness is proven by the row contract |
| mean, weighted mean, ratio | rejected without retained named sufficient-statistic value bindings |
| median, percentile, other distribution statistic | rejected |
| cumulative | rejected in the first cutover |
| opaque or unresolved composition | rejected |

Public Metric value columns alone do not imply hidden component authority.
Another selected Metric column is not silently borrowed as a numerator,
denominator, count, or weight. A future retained-statistics contract would
require an explicit amendment to this module and the Dataset row contract.

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

### Exact compatibility checks

Construction compares semantic identity, never display names. The complete
admission set is:

| Input | Required compatibility |
| --- | --- |
| Metric roots | one exact inferred Entity, or one unique safe path to the explicit Population-input Entity |
| `PopulationDataset` input | same Session, exact family contract, complete identity, compatible scope and sampling |
| `SubjectSet` input | same Session, exact subject Entity and identity signature, ready complete membership authority |
| exact Entity Metric input | same Session, exact `metric/entity@v1` shape, unique complete retained identity, compatible scope and sampling |
| entity-outlier Candidate input | same Session, exact `candidate/entity-outlier@v1` shape, unique complete retained identity, compatible scope and sampling |
| Dimension | one unique governed path from Population Entity and safe Metric contribution semantics |
| time Dimension | one unique governed path, compatible granularity/calendar/scope, common to all Metrics |
| multiple Metrics | one shared Population and coordinate spine; no independent row-set alignment |
| runtime Metric expression | same Session authority, stable identity/label, complete governed component graph |

Matching primary-key field names, physical table names, source columns,
datasource ids, or display labels never establish compatibility.

### Population versus Metric scope

The Population reference scope is the outer eligibility boundary. Metric
definitions may own narrower semantic filters, cumulative history, status-time
folds, or versioning behavior, but may not widen Population membership.

For each Metric:

- Population predicates determine which Entity identities are eligible;
- the Population reference scope constrains ordinary eligible facts;
- Metric-local filters determine contributing values only;
- cumulative all-history lookback may read pre-scope facts to compute an
  in-scope endpoint without adding pre-scope Entity members;
- time coordinates partition eligible contributions and do not rescope them;
- null Metric results do not remove members or coordinates.

A Metric whose semantic evaluation necessarily requires a conflicting Entity
membership or incompatible reference scope is rejected. Marivo does not merge
scopes by union, intersection, earliest start, or latest end.

### Structured repair matrix

The observation model requires structured `AnalysisError` subclasses with
expected, received, location, and a mechanically valid next action.

| Failure | Required repair owner |
| --- | --- |
| no exact default Entity | construct a specific explicit Population only when a real compatible candidate exists, otherwise repair Metric authoring |
| different inferred Entities | split observations or pass a compatible explicit Population |
| ambiguous Relationship path | repair or disambiguate semantic Relationships; explicit Population alone is insufficient |
| explicit scope plus explicit `PopulationInput` | move scope construction before the source call and remove source-level scope args |
| Population input Entity/signature mismatch | pass an input for the exact target Entity or repair the current semantic path |
| Population input lacks an admitted identity-bearing shape | pass `PopulationDataset`, `SubjectSet`, `metric/entity@v1`, or `candidate/entity-outlier@v1` |
| Population input identity is null, duplicate, missing, or stale | repair or reconstruct the exact input Dataset; never deduplicate or replay it |
| unreachable predicate/Dimension | choose a real reachable ref or repair the semantic Relationship |
| time Dimension passed to `with_dimensions` | use `with_time_axis(time_dimension, grain=...)` |
| incompatible grain | use the bounded legal grains derived from the exact time-Dimension contract |
| repeated time coordinate | reconstruct the chain with one exact `with_time_axis(...)` declaration |
| aggregate after Entity axis removed | remove the repeated `aggregate()` or reconstruct from the Entity-grained Dataset |
| unsupported Metric reaggregation | aggregate before materialization or repair the Metric's governed aggregation/components |
| missing, extra, secret-like, or invalid parameterized source binding | rebuild the source inside `Session.source_bindings(...)` with the exact current Entity parameter contract |
| rollup on an Entity-grained or unsupported shape | call `aggregate()` first or author the target observation directly |
| missing retained rollup state or incompatible fold | author and execute a fresh observation at the target Dimensions/grain; never replay a Materialized origin |
| filter after sampling | move all Population predicates before `.sample(...)` |
| repeated sampling | branch from the unsampled Population and apply one policy |
| bool, kwargs, string, lambda, SQL, or expression predicate | rebuild the condition with the focused `mv.*` predicate helpers |
| field is absent from a non-Population row contract | add and retain the field before filtering, or select one exact current field from `dataset.fields` |
| wrong field role or incompatible literal | choose an admitted current field and literal type shown by the row contract |
| temporal, versioned, or collection-valued Population field | use `time_scope` for temporal eligibility, filter an observed Dataset, or choose a single-valued atemporal Dimension |
| materialized filter requires an absent semantic field | reconstruct the logical chain or retain the field before materialization |
| row selection passed through an unsupported consumer path | pass the exact admitted identity-bearing Dataset through `population=` |
| cross-Session Dataset | reacquire or reconstruct the input in the owning Session |

Suggestions are derived from current catalog and row-contract facts. Errors do
not hardcode refs, choose an analytical Entity, or claim that an action will fit
runtime bounds.

## Population and Metric Materialization Contracts

Every Population- or Metric-producing definition resolves the common
`DatasetMaterializationContractV1` envelope owned by Module 4 before Run
admission. Module 2 owns the registrations and semantic checks below; Module 4
owns their invocation, persistence, Evidence envelope, and atomic publication.

The first-cutover registry is closed:

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
predicate realization, and sampling receipt coherence. Metric checks prove one
shared Population spine, exact row-key uniqueness, ordered Metric and coordinate
bindings, null-retention semantics, branch-to-spine reconciliation, and the
selected aggregation equations. A sample validates its realized membership
once per action and records requested versus realized authority without
publishing identity values.

Evidence contains only bounded semantic refs, contract ids, scope and sampling
classes, row and null-count summaries, coordinate/Metric counts, reconciliation
maxima, and approximation facts. Raw Entity identities, coordinate samples,
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

## Definition Identity and Lineage

### Population definition identity

A Population definition fingerprint binds:

- family and contract versions;
- exact Entity ref and compiled primary-key signature;
- reference scope and selected time-Dimension ref;
- canonical membership predicate trees in authored operator order;
- exact sampling request;
- unsampled target-Population authority token;
- semantic and Relationship dependency identities;
- logical or materialized input authority token.

It excludes realized members, row counts, sample realization, generated SQL,
backend strategy, and source snapshot identity.

### Metric Dataset definition identity

A Metric Dataset definition fingerprint additionally binds:

- ordered Metric identities and aggregation-contract versions;
- exact Population input authority token;
- exact captured source-binding value digest for every reachable parameterized
  Entity;
- ordered Dimension coordinates;
- exact time coordinate and grain;
- Entity-axis present/reduced state;
- operator versions, rollup requests, and materialized-fold decisions.

Every filtered Dataset fingerprint also binds the exact input authority token,
family filter effect, canonical predicate tree, authored operator position, and
predicate-contract version. Equivalent physical pushdown does not rewrite that
public definition identity.

Metric projection binds the selected identity. `aggregate()` binds the exact
coordinate transition and each Metric's recomputation/fold mode. `rollup(...)`
binds its ordered dropped Dimension refs, target grain/calendar identity, and
per-Metric retained-state fold contracts.

Two chains with identical public labels but different Entity, scope,
Population, sampling, Metric order, Dimension order, time grain, component
authority, or logical/materialized inputs intentionally have different
definition fingerprints.

### Bounded lineage

Public lineage may disclose:

- target Entity ref and identity-signature field names without raw values;
- Population definition fingerprint and target-Population fingerprint;
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
filterable fields, authority mode, legal continuations, approximation
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
shape, Population Entity, Metric identities/count, coordinates, state, and
continuation facts. Neither card renders raw identities, values, SQL, or the
full capability matrix.

## Cross-Module Seams

### Dataset Core supplies

- nominal `PopulationDataset` and `MetricDataset` families;
- row-contract and schema construction;
- selector-only `DatasetFields` and public `DatasetFieldRef` values;
- immutable logical/materialized Dataset state;
- Session ownership and private authority tokens;
- definition fingerprints, bounded lineage, actions, and scan-leaf behavior.

This module supplies the family payloads, shapes, value/coordinate bindings,
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

It decides relational lowering and placement. It may not change Entity,
membership, coordinate domain, Metric aggregation, or null-retention semantics.

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
predicate syntax, authority-mode vocabulary, filter assignment matrix, and
`where(...)` lifecycle and may not create kwargs, string, callable, or
family-specific predicate grammars.

It may restrict accepted shapes further. It may not reinterpret a Dimension
bucket as an Entity sample or add another Metric source path.

### Subject, Event, and Lifecycle consumes

- Entity identity compatibility;
- Population reference scope and target lineage;
- `SubjectSet` admission through the `population_input` role's direct-membership
  mode;
- the shared filter protocol and family-preserving `row_subset` effect;
- raw-identity disclosure boundaries.

That module owns SubjectSet production, whether SubjectSet filtering is
admitted, and exact Event/Lifecycle filterable generated fields. It consumes the
shared effect vocabulary, authority-mode vocabulary, and filter assignment
matrix and may not redefine predicate syntax, add `SubjectSet.observe(...)`, or
add another Population root spelling.

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

### `PopulationDataset.observe(...)` and `SubjectSet.observe(...)`

Rejected because aliases create multiple source owners and duplicate Help,
signature, and repair contracts.

### Keep `grain`, `dimensions`, `slice_by`, and `cohort` on observe

Rejected because they merge membership, coordinate declaration, filtering, and
SubjectSet admission into one optional-field source call. Dataset operators make
each transition explicit and typed.

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
Population and SubjectSet identity would acquire parallel layouts.

### Infer sampling to satisfy action limits

Rejected because exact and approximate membership are different analytical
definitions. Only an explicit policy authorizes approximation.

## Vertical Acceptance Journeys

### Default same-Entity inference

```python
features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
    time_scope=window,
)
```

Acceptance must prove one exact inferred Entity, one implicit logical
Population node, no datasource work, one Entity identity coordinate, ordered
Metric values, and a complete pre-execution row contract.

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
)
```

Acceptance must prove predicate-before-sample ordering, one target-Population
lineage edge, one Entity-safe sample shared by every Metric, and rejection of
conflicting scope arguments on `observe`.

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

The final Dataset must retain Population authority while removing the public
Entity coordinate.

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
equivalent physical pushdown must preserve the public definition fingerprint
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
features = session.observe(metrics=[conversion_rate]).execute()
features.aggregate()
```

Acceptance must prove a local structured failure when the materialized rows do
not retain sufficient statistics, with a repair to aggregate before
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
SubjectSet; and no Dataset-owned `observe(...)` alias.

### Ambiguous Entity failure

```python
session.observe(metrics=[order_revenue, account_health])
```

Acceptance must prove a no-I/O structured failure when roots resolve different
Entities. The error must name exact inputs and only real repair candidates; it
must not select an Entity or Relationship path heuristically.

## Acceptance Criteria

This design is complete when all of the following are reviewable and later
testable:

1. a Population always owns one exact Entity and ordered primary-key signature;
2. one Population row is one non-null unique `entity_identity` tuple;
3. Population scope and aggregation coordinates are distinct contracts;
4. default inference selects only one exact common Metric Entity or fails;
5. explicit Population can change Entity only through one unique safe semantic
   path for every Metric;
6. `session.population(...)` and `session.observe(...)` have exact minimal
   signatures;
7. `PopulationInput` is the sole explicit `population=` union and admits only
   `PopulationDataset`, `SubjectSet`, `metric/entity@v1`, and
   `candidate/entity-outlier@v1` under their exact direct or projection modes;
8. explicit Population scope cannot be silently combined with observe scope;
9. Population predicates are closed, typed, and applied before sampling;
10. exact membership is default and sampling is never inferred;
11. one approximate Entity policy samples identities before Metric facts;
12. multi-Metric observation uses one shared Population spine;
13. Metric-local nulls and filters do not remove Population members;
14. every Entity-grained row owns exact identity plus ordered Metric values;
15. Dimension and time coordinates have stable semantic identity and canonical
    order;
16. `with_dimensions(...)` and `with_time_axis(...)` retain the Entity axis;
17. `aggregate()` removes the Entity axis exactly once;
18. all eight Metric shapes have complete pre-execution row contracts;
19. scalar output has one explicit singleton row contract without a synthetic
    public key column;
20. coordinate spines are governed and independent of Metric non-nullness;
21. aggregate semantics are derived per Metric and never default to sum;
22. ratio, weighted, non-additive, and cumulative Metrics recompute from owning
    authority while logical;
23. materialized reaggregation uses only exact retained sufficient statistics
    or fails locally;
24. a materialized scan leaf is never transparently re-evaluated through its
    source graph;
25. Entity, Relationship, Dimension, time, scope, and aggregation conflicts
    produce structured repairs;
26. raw Entity identities remain out of metadata, errors, Evidence, and cards;
27. definition identity binds Population, Metric order, coordinates,
    aggregation decisions, and logical/materialized input authority;
28. no source aliases, shape flags, eager overloads, or compatibility paths are
    introduced;
29. every filterable Dataset family uses one `where(*AnalysisPredicate)`
    spelling and lazy family-preserving lifecycle;
30. predicate construction uses one sealed comparison, membership, null, and
    boolean vocabulary;
31. semantic refs and exact `DatasetFieldRef` selectors are the only predicate
    field inputs;
32. kwargs, Python comparisons, strings, callables, SQL, Ibis, and arbitrary
    expression inputs are absent;
33. Population filtering changes membership while non-Population filtering
    defaults to row-subset semantics;
34. Metric row filtering never changes membership authority; only a later
    explicit `population=` source boundary may consume its exact Entity rows;
35. non-Population predicates reference only the current row contract, while
    Population owns the one reachable-membership-field specialization;
36. filter position participates in definition identity and cannot move across
    sampling, aggregation, materialization, population-input projection, Event
    matching, or Lifecycle replay without an equivalence proof;
37. materialized filtering reads retained fields only, except for the explicit
    semantic-current Population membership specialization;
38. exact singleton scalar Metric Datasets reject `where(...)` rather than
    weakening cardinality;
39. semantic-authoring `ms.where(...)` and analysis `dataset.where(...)` remain
    separate authority surfaces;
40. Help, `contract()`, errors, fingerprints, and lineage expose the current
    filter effect, admitted fields, authority mode, and bounded redacted
    predicate facts;
41. every field logical type has one closed operator/literal compatibility and
    canonical normalization rule before datasource work;
42. Population reachable-field filtering admits only unique single-valued,
    non-versioned, atemporal non-time Dimensions and never invents an `as_of`,
    window, or collection-membership rule;
43. filter authority is selected by Dataset state and field resolution, not by
    field role alone;
44. direct semantic refs resolve exactly one current row binding or require an
    exact `DatasetFieldRef` repair;
45. every public predicate builder and the `AnalysisPredicate` type have one
    independently resolvable canonical Help leaf and pinned export;
46. helper-produced predicates remain unbound, while `where(...)` resolves one
    private canonical bound tree against the exact input Dataset without
    mutating or Session-binding the public value;
47. parameterized non-secret source values are captured into immutable source
    definitions, participate in exact identity, and are never looked up from
    ambient Session state by `execute()`;
48. `rollup(...)` has one fold-current-rows meaning across Logical and
    Materialized inputs, admits only exact registered retained-state folds, and
    never recomputes through origin lineage.

## Frozen Module Decisions

On 2026-09-04 the owner retained construction-scoped source bindings and added
the strict current-row rollup contract. These decisions resolve the two Module
2 questions raised by the Public Cutover Plan.

This accepted design freezes these choices:

1. a Population is one Entity membership set, never an output-shape variant;
2. Population rows expose one tuple-valued `entity_identity` coordinate;
3. default inference requires exact equality of all Metric analysis Entities;
4. explicit Population may select another Entity only through unique governed
   paths;
5. `session.observe(...)` owns the sole Metric source spelling;
6. observe accepts only Metrics, optional `PopulationInput` authority, and
   default Population scope inputs;
7. Dimensions and time grain use their dedicated Dataset operators, every
   analysis row filter uses `dataset.where(...)`, and only explicit
   `population=` consumes current rows as membership;
8. one sealed `AnalysisPredicate` vocabulary owns comparisons, membership,
   null checks, and explicit `all_of` / `any_of` / `not_` composition;
9. Population predicates must precede one optional `engine_sample(...)`
   request;
10. `with_dimensions(...)` appends ordered Dimensions,
    `with_time_axis(...)` adds one final time coordinate, and `aggregate()`
    removes Entity exactly once;
11. logical aggregation recomputes governed Metric semantics at the output
    coordinates;
12. materialized aggregation is admitted only through an exact retained-value
    fold;
13. ratio, weighted mean, distribution, cumulative, and opaque values do not
    acquire a silent materialized fold;
14. Population and coordinate spines are defined independently of Metric
    non-nullness;
15. explicit Population plus observe-level scope is an error, not an
    intersection rule;
16. filtering a Population changes membership, while filtering any other
    initially admitted family changes only its current rows;
17. `DatasetFieldRef` is selector-only and generated fields use
    `dataset.fields.get(...)` rather than kwargs;
18. filter operator position is semantic and planner pushdown requires exact
    equivalence;
19. only an explicit `population=` source boundary may consume an admitted
    identity-bearing Dataset's current rows as membership; no intermediate
    public conversion Dataset is created;
20. semantic-authoring and analysis filtering remain separate public
    contracts;
21. logical type compatibility and literal coercion use one backend-independent
    construction-time matrix;
22. Population hidden-field membership is limited to single-valued atemporal
    Dimensions in the first cutover;
23. `materialized_only` and `semantic_current` are selected for filtering by one
    state-and-resolution matrix owned by this module, while their global runtime
    meaning remains owned by Materialization Runtime;
24. all twelve predicate builders and `AnalysisPredicate` are pinned public
    exports with one registry-owned Help route each;
25. public predicates are reusable unbound authoring values and only
    `where(...)` creates the private Dataset-bound canonical predicate tree;
26. every Population and Metric producer resolves one Module 2-owned quality,
    validation, Evidence, zero-Finding, and retained-state registration through
    Module 4's common materialization envelope before Run admission;
27. `Session.source_bindings(...)` remains the sole parameterized JSON source
    input path, captures exact non-secret values at logical source construction,
    and contributes an exact digest to definition and execution identity;
28. `rollup(drop_dimensions=..., grain=...)` is the sole post-definition
    coordinate-coarsening path, is limited to Entity-reduced Metric shapes, and
    folds only current rows or registered retained sufficient state;
29. arbitrary axis deletion, time-axis removal, origin replay, and silent
    non-additive rollup are absent from the first cutover.

Changing one of these decisions requires an explicit amendment to this module
before downstream planner, runtime, or operator designs rely on a replacement.

## Final Boundary

Population answers who is eligible. Coordinates answer where a Metric value is
positioned. Aggregation answers how governed Metric contributions are recomputed
after the Entity axis is removed.

These are three different authorities. Keeping them separate lets the public
DSL infer the common case, express an explicit cohort when needed, and remain
correct for non-additive Metrics without exposing a plan or hiding execution in
`session.observe(...)`.
