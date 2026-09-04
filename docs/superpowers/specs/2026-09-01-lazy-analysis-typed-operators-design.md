# Lazy Analysis Typed Operators Design

Date: 2026-09-01

Revised: 2026-09-04

Status: accepted

Owner decision status: confirmed

## Outcome

Define the first-cutover typed analysis operators that consume one or more
public Datasets and always return another public Dataset.

This document is the sole authority for:

- the registered non-filter Dataset operator matrix;
- canonical operator ids and public Dataset method paths;
- exact input families, qualified shapes, coordinates, arity, and value types;
- the registered `MetricDataset.rollup(...)` coordinate-coarsening operator;
- output Dataset families, qualified shapes, row meanings, and ordered schemas;
- state- and operand-dependent authority-mode assignment;
- generated fields and filter admission for Candidate and compact analytical
  Dataset families, plus consumer admission contracts from which their typed
  continuations are derived;
- null, constant-input, approximation, and disclosure behavior owned by an
  operator;
- the rule that a statistical test may appear only as an exact Delta consumer
  affordance whose inferential prerequisites are already authoritative;
- family-specific quality, Evidence, Finding extraction, and retained
  sufficient-statistic contracts consumed by materialization;
- structured construction- and action-time repairs for the owned matrix.

The first matrix covers:

```text
correlate
rank
limit
compare
attribute
forecast
discover.*
```

The goal is not to preserve the current eager Frame and Result surface. The
goal is one clean lazy Dataset contract in which an agent can determine before
execution whether a call is structurally admissible, what one output row will
mean, which facts remain action-time requirements, and which exact Dataset
continuations remain legal.

## Ownership Boundary

### This module owns

- one versioned registry entry for every exact operator variant in the first
  matrix;
- the natural Dataset receiver and one canonical public spelling per operator;
- operator-specific parameters and closed policy values;
- operator-specific input compatibility beyond the shared Dataset and
  Population rules;
- complete output row contracts known before datasource work;
- operator-specific authority branch selection;
- operator-specific data-dependent statuses and publication gates;
- operator-specific quality, Evidence, Finding extraction, and retained-state
  meaning supplied to the runtime;
- Candidate and compact analytical family filter-field registration;
- conditional Delta test-affordance admission, with no generic hypothesis
  operator or first-cutover test method;
- bounded consumer admission declarations and exact constraint-owned typed
  repairs.

### This module does not own

- common Dataset construction, state, actions, fingerprints, or scan leaves;
- Population inference, observation, aggregation coordinates, predicate syntax,
  common filter effects, or filter ordering;
- Ibis expression construction, physical placement, federation, transfer, or
  local-execution policy;
- Run, Artifact, Evidence, recovery, or commit ordering;
- SubjectSet identity authority or its output row contract;
- Event and Lifecycle source construction, reducers, or family-specific
  operator registrations or population-input admission;
- public removals, compatibility behavior, Help rollout, or implementation
  sequencing.

The owning sources are:

- [Dataset Core](2026-09-01-lazy-analysis-dataset-core-design.md);
- [Observation Model](2026-09-01-lazy-analysis-observation-model-design.md);
- [Ibis Compiler and Execution Boundaries](2026-09-01-lazy-analysis-planner-and-pushdown-design.md);
- [Materialization Runtime](2026-09-01-lazy-analysis-materialization-runtime-design.md);
- [Subject, Event, and Lifecycle](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md);
- the planned public-cutover plan.

## Upstream Invariants

This module consumes the following frozen contracts without redefining them:

1. `(LogicalDataset[A] | MaterializedDataset[A]) x TypedInputs ->
   LogicalDataset[B]` is the only analysis-operator shape. Every registered
   downstream operator is present on both Dataset states and always extends a
   lazy DAG; no operator returns a materialized result, detached result,
   selection, future, task, DataFrame, Ibis expression, SQL value, or planner
   handle.
2. Every Dataset has one nominal family, one family-qualified shape, one exact
   row contract, one owning Session, and one logical or materialized input
   authority token.
3. Operator construction performs deterministic local admission and constructs
   the complete output row contract before datasource work.
4. Row-dependent requirements remain typed action-time requirements. A preview
   never upgrades them into reusable authority.
5. Materialized inputs are immutable scan leaves. An operator may consume their
   retained rows or join them to separately admitted current semantic inputs,
   but may not reach through them to rewrite the origin definition.
6. Metric Dataset coordinates, Population authority, Metric bindings,
   aggregation semantics, approximation intent, and target-Population lineage
   come from the Observation Model.
7. `where(...)` uses only the shared `AnalysisPredicate` vocabulary and the
   shared `row_subset` effect for Candidate and compact analytical rows. This
   document registers fields and shapes; it does not create another predicate
   grammar.
8. Every occurrence selects exactly one of `semantic_current`,
   `materialized_only`, or `semantic_or_materialized` before execution. A mode
   is a requirement, never runtime fallback.
9. The compiler implements only a registered semantic node and exact admitted
   lowering. It does not decide product admission, statistical method,
   approximation, or output meaning.
10. Hidden approximation, implicit sampling, silent local fallback, and
    compatibility recovery are forbidden.

Throughout this document, a family name such as `MetricDataset` names the
analytical family rather than one runtime state when used in prose or matrices.
Public signatures name the exact `Logical*Dataset` or `Materialized*Dataset`
class, and every operator return names the Logical class. `state-specific
actions` means `execute()` on the Logical class and `show()` plus `to_pandas()`
on the paired Materialized class.

## Design Principles

### One natural receiver, one public path

An operator lives on the Dataset whose current rows supply its observational
unit or analytical state:

```text
metrics.correlate(...)
dataset.rank(...)
current.compare(baseline, ...)
delta.attribute(...)
history.forecast(...)
dataset.discover.<objective>(...)
```

The registry contains no equivalent `session.<operator>(dataset, ...)` path and
no `frame.transform.<operator>` namespace. Source operators remain on Session;
downstream analysis remains on Dataset.

### Registration is variant-based, nominal, shape-aware, and field-aware

Python inheritance alone does not admit an operator. Every registry entry names:

```text
OperatorVariantRegistrationV1
  operator_id
  variant_id
  contract_version
  capability_id
  invocation_contract: InvocationContractV1
  output_contract: OutputContractV1
  semantic_node_kind
  action_requirement_contract_id
  materialization_contract_id

InvocationContractV1
  receiver_patterns: tuple[DatasetInputPatternV1, ...]
  operands: tuple[OperandContractV1, ...]
  parameter_contract: ParameterContractV1
  authority_rule

OperandContractV1
  role
  accepted_inputs: tuple[DatasetInputPatternV1, ...]

DatasetInputPatternV1
  family
  shapes[]
  coordinate_requirements
  metric_arity
  value_type_requirements
```

The registration key is `(operator_id, variant_id, contract_version)`. A variant
correlates one complete receiver/operand/parameter admission contract with one
authority rule and one output contract. It never represents overloads as
parallel family, shape, or role arrays whose cross-product could admit an
invalid combination.

The notation `operator/variant@vN` below is prose shorthand for that three-part
registration key; for example, `compare/metric@v1` identifies
`("compare", "metric", 1)`.

`capability_id` links to the existing analysis capability registry, which alone
owns the public entrypoint, focused Help target, reflected signature, summary,
and examples. The operator registry does not repeat those values. Metric and
Event overloads may therefore share an `operator_id` while using different
`variant_id` and `capability_id` values.

`ParameterContractV1` binds reflected parameter names to their typed normalized
semantic payload and cross-parameter constraints. It does not copy signature
text, Help prose, examples, or renderer defaults; drift tests validate those
public facts against the linked capability and callable.

The registered variant is the semantic construction authority. Public Help,
`dataset.contract()`, structured errors, the compiler lowerer manifest, and
tests consume its owned contracts or independently validate their seam. A
renderer-local or compiler-local shadow admission/output matrix is invalid.

### One operator meaning covers every admitted input-authority topology

Every operator variant defines one calculation and one output contract across
two execution scenarios: a fully logical upstream closure and an upstream
closure cut by one or more explicit Materialized Dataset inputs. A multi-input
variant therefore admits or rejects an ordered vector of logical and
materialized input states; partial materialization is not a separate overload.

The variant must declare enough information to decide this before execution:

- which input roles admit logical, materialized, or mixed authority;
- which retained fields or sufficient statistics make the materialized branch
  semantically complete;
- whether a logical branch may add current semantic dependencies;
- whether mixed inputs preserve Population, coordinate, snapshot, and
  approximation compatibility;
- the exact structured repair when a materialized input lacks retained state
  and origin replay is forbidden.

The operator registry selects authority and validates semantics; it does not
choose placement or duplicate implementation ids. The compiler binds logical
inputs to their semantic subgraphs and materialized inputs to immutable scan
leaves, then applies the one exact lowerer for the variant. Backend inability to
scan, import, or combine those leaves is an execution-boundary failure, not
permission to switch to the logical origin or another operator algorithm.

### Local execution is registered equivalence, not a second product branch

An operator may admit one compiler-owned DuckDB relational implementation or
one exact Python kernel when its semantics cannot remain in the source engine.
That placement does not create another public overload, authority rule, output
contract, approximation, or Dataset fingerprint. The operator owner supplies
the same reference semantics, exact input/output contracts, minimum-data rules,
null and non-finite behavior, and any stricter local bounds used by every
implementation.

The compiler registry owns implementation ids, dependency fingerprints, Arrow
or Parquet exchange modes, and DuckDB/Python placement. pandas, NumPy, SciPy, or
statsmodels may be dependencies of one registered Python kernel but never
operator inputs or outputs. Polars is not a first-cutover implementation kind.
Any future additional engine must prove differential equivalence to the same
operator reference contract; it cannot widen product admission.

### Output contracts are complete before execution

An operator may defer a value, count, coefficient, score, interval, or
data-dependent status until execution. It may not defer:

- output family or qualified shape;
- ordered public field identities and logical types;
- row key and one-row meaning;
- which fields may be null and under which typed status;
- coordinate retention or reduction;
- ordering and cardinality class;
- filterable generated fields;
- retained-statistic and state facts needed by downstream admission.

If those facts depend on a backend probe or realized data, the public operator
contract is under-specified and construction fails.

### Status rows disclose unusable calculations without inventing values

Where an operator has several requested calculations and some may be undefined
for data-dependent reasons, the row contract uses one closed status vocabulary.
An unusable row carries its exact counts and a null calculated value. It does
not carry NaN or an invented neutral value.

The owning operator must separately decide whether:

- unusable rows are valid disclosed output; or
- any unusable requested calculation makes the complete action fail atomically.

That decision is part of the operator matrix, not compiler policy.

### Approximation is authored or inherited, never inferred

Every output binds one exact approximation state derived from:

- an explicit upstream Population sampling policy;
- an explicit operator method that is approximate by contract; or
- exact execution.

Resource pressure cannot switch an exact definition to an approximate
implementation. Method-specific error bounds, confidence parameters, sample
counts, backend approximation identity, and unknown-error states are disclosed
in typed fields or committed metadata according to the owning row contract.

### Candidate means lead, not conclusion

Every `CandidateDataset[objective]` contains ranked analytical leads. Filtering,
ranking, or limiting Candidate rows does not establish causality, significance,
or acceptance, and it never changes the Candidate family. For the exact
entity-outlier shape, a later explicit source call with `population=candidates`
consumes those current rows as membership without a bridge or conversion. Other
Candidate shapes are not population inputs.

## Registry Layers

The typed-operator registry coordinates four contract layers without copying
facts between them:

```text
AnalysisCapability              <- capability_id
OperatorVariantRegistrationV1   <- invocation, output, semantic node,
                                   action requirement, materialization link
LowererManifest                 <- semantic_node_kind
DatasetMaterializationContractV1 <- materialization_contract_id
```

`InvocationContractV1` owns local receiver and operand patterns, normalized
parameter semantics, ownership and Population alignment, and authority-branch
selection. `DatasetInputPatternV1` keeps family, qualified shapes, coordinates,
Metric arity, and value types together. `OperandContractV1` keeps each role with
its own admitted patterns; no positional parallel arrays exist.

`OutputContractV1` owns family, shape rule, row meaning, ordered schema, row
key, cardinality, ordering, statuses, approximation disclosure, generated-field
roles, row-contract builder, and filter registration.

`action_requirement_contract_id` names the typed semantic requirements that
construction instantiates for the exact normalized inputs and parameters.
Construction- and action-time constraint rules own their own structured repair;
there is no entry-level repair catalog.

`semantic_node_kind` is the only compiler-facing semantic link. Module 3's
immutable lowerer manifest independently maps that kind to its portable,
backend-specific, or admitted bounded-local implementations. Equivalent
implementation additions do not change the operator contract version or
Dataset definition fingerprint.

`DatasetMaterializationContractV1` names the exact quality, Evidence, Finding,
validation-output, and retained-sufficient-statistic contracts that Module 4
must invoke before publication. It does not own transaction mechanics.

The former flat-field proposal is resolved as follows:

| Former field group | Canonical owner |
| --- | --- |
| `operator_id`, `contract_version` | the variant key, together with required `variant_id` |
| `public_entrypoint`, `help_target` | linked `AnalysisCapability`, reached through `capability_id` |
| receiver family/shapes and coordinate, arity, value-type requirements | one or more correlated `receiver_patterns` |
| operand roles/families/shapes and their requirements | ordered `OperandContractV1` values, each bundling its role and accepted patterns |
| `parameter_contract`, `authority_rule` | `InvocationContractV1` |
| output family/shape, row builder, generated fields, filter registration | `OutputContractV1` |
| `action_time_requirements[]` | one typed rule set reached through `action_requirement_contract_id` |
| `implementation_ids[]` | compiler-owned `LowererManifest`, keyed by `semantic_node_kind` |
| `materialization_contract_id` | retained as the link to the Module 4 materialization contract |
| `continuations[]` | removed; derived by matching current Dataset facts to consumer admission |
| `repair_contracts[]` | removed; each failing constraint rule owns its exact repair |

Continuations are not stored on the producing variant. `dataset.contract()`
derives them by matching the current Dataset contract and state against every
source or operator consumer admission contract, including retained-statistic
and materialization blockers. Adding a consumer therefore does not modify prior
producer registrations.

Changing a semantic field that can change admitted inputs, normalized
parameters, output rows, authority, approximation, quality meaning, Finding
extraction, or action requirements requires a new variant contract version and
a new Dataset definition fingerprint. Changing only an equivalent compiler
implementation or Help prose does not.

## Initial Operator Inventory

The following table summarizes the confirmed public path and primary output
family. The complete matrix and method sections below are normative.

| Operator id | Canonical path | Primary input | Output family |
| --- | --- | --- | --- |
| `correlate` | `metrics.correlate(...)` | multi-Metric `MetricDataset` | `AssociationDataset` |
| `rank` | `dataset.rank(...)` | registered row Dataset | Logical Dataset of the same analytical family |
| `limit` | `dataset.limit(...)` | deterministically ordered row Dataset | Logical Dataset of the same analytical family |
| `rollup` | `metric_dataset.rollup(...)` | Entity-reduced `MetricDataset` with retained fold authority | `LogicalMetricDataset` |
| `compare` | `current.compare(baseline, ...)` | compatible `MetricDataset` pair | `DeltaDataset` |
| `attribute` | `delta.attribute(...)` | `DeltaDataset` | `AttributionDataset` |
| `forecast` | `history.forecast(...)` | time-bearing `MetricDataset` | `ForecastDataset` |
| `discover.<objective>` | `dataset.discover.<objective>(...)` | objective-specific Dataset | `CandidateDataset[objective]` |

## Owner-Confirmed Product Decisions

The owner confirmed decisions 1-10 on 2026-09-01 and the rollup decision 11 on
2026-09-04:

1. the discovery surface contains exactly `point_anomalies`,
   `interesting_windows`, `entity_outliers`, `period_shifts`, and
   `driver_axes`;
2. `interesting_slices` and `semantic_hypotheses` are removed, and
   `entity_outliers` replaces `cross_sectional_outliers` without an alias;
3. `compare`, `attribute`, and `forecast` require an arity-one Metric binding;
   the repair for a multi-Metric input is `dataset.metric(metric_ref)`;
4. `rank()` is one common family-preserving row operator over a registered
   numeric field, uses selector-only field refs, emits one canonical `rank`
   field, and leaves row bounding to the separately registered `limit(...)`;
5. correlation supports Pearson, Spearman, and Kendall; forecasting supports
   naive, drift, and seasonal-naive;
6. correlation may publish unusable lag-candidate rows, but every Metric pair
   must have at least one valid candidate or the action fails atomically;
7. a materialized Delta may attribute only retained axes and retained exact
    sufficient-statistic authority; it cannot replay or join current semantics
    to add a missing axis;
8. `candidate/entity-outlier@v1` retains its exact governed Entity identity
   binding and may be passed directly through `population=`. Module 2 owns that
   source-input admission; other Candidate shapes expose no population-input
   capability and cannot replay their source to recover identities;
9. generic descriptive profiling is outside the first-cutover operator matrix:
    reusable percentiles and business counts are explicitly authored Metrics,
    while null/non-null completeness diagnostics belong to quality/Evidence.
    The cutover adds no generic summary/inspection replacement or compatibility
    alias;
10. generic `hypothesis_test`, `HypothesisDataset`, and paired-t behavior are
    outside the first cutover. A future theory-valid test is an exact named
    Delta affordance, never a method selector over arbitrary Metric rows.
11. `rollup(drop_dimensions=..., grain=...)` is retained as one Dataset-owned
    current-row fold, not a Metric-graph recomputation; it accepts only
    Entity-reduced Metric shapes and exact Observation-owned fold authority.

These decisions replace the corresponding eager Frame behavior. They are not
compatibility defaults.

## Output Family Vocabulary

The first cutover adds these nominal Dataset families to the Dataset Core
registry:

```text
AssociationDataset
DeltaDataset
AttributionDataset
ForecastDataset
CandidateDataset[objective]
```

They share the common Dataset protocol but not one optional-field metadata
model. Each family has closed qualified shapes and one owning row-contract
builder. The public family name never implies that rows have executed.

All generated field ids are versioned identities. Public column names are
stable projections of those ids and are not accepted as substitute semantic
identity. Operator parameters select fields through `DatasetFieldRef` or exact
semantic refs as specified below.

### Common generated roles

The initial generated-field roles are:

```text
metric_identity
pair_identity
method_identity
status
sample_count
comparison_value
effect_value
score
reason_code
rank
forecast_value
interval_bound
```

These roles extend Dataset Core's role vocabulary. They do not create a generic
expression type. A role is filterable only where the family registration below
admits its exact field id.

### Generated field identity and type rules

Every generated field named by an operator row contract has the exact field id
`generated.<operator_id>.<public_name>@v1`, with dots in a discovery operator id
retained. The sole shorter shared id is the explicitly registered
`generated.rank@v1`. Retained coordinates and semantic bindings keep their
owning input field ids and logical types; paired current/baseline time fields
keep their source temporal logical type. Public names never substitute for these
ids in selectors or registry comparisons.

The following schema rules are normative together with each family section's
ordered field list and status/null rules:

| Generated field class | Logical type and nullability |
| --- | --- |
| `*_count`, `*_ordinal`, `lag_offset`, `window_size`, `contribution_rank`, `rank` | `int64`; non-negative counts and positive ranks; nullable only where the owning row/status section says the calculation is unavailable |
| coefficient, relative delta, share, score, effect, forecast, interval, deviation, and baseline generated values | `float64`; finite when defined; nullable exactly under the owning closed status |
| promoted `current_value`, `baseline_value`, `delta`, and additive/component contribution values | the row-contract builder's registered lossless common numeric type; nullable exactly under the owning closed status |
| `selected_for_pair` | non-null `bool` |
| `status`, presence, direction, method, and scale fields | the owning non-null closed string enum; never an open string |
| `active_axis_mask`, `other_mask` | fixed-length `tuple[bool, ...]` with length equal to the authored axis count |
| `metric_key`, `axis_ref` | the owning typed semantic-ref identity |
| `item_id` | non-null canonical `sha256:<lowercase-hex>` digest string |
| `reason_codes` | non-null bounded tuple of the objective's closed reason enum |

All count arithmetic uses exact `int64` with an action-time overflow check.
Fields not listed by the exact family row contract do not appear as optional
columns.

## Complete Operator Matrix

| Operator id | Variant id | Receiver shapes | Arity | Output shape | Authority rule |
| --- | --- | --- | --- | --- | --- |
| `correlate` | `metric` | `metric/entity@v1`, `metric/dimension@v1`, `metric/time@v1`, `metric/dimension-time@v1` | 2-16 Metrics | coordinate-derived Association shape | `semantic_or_materialized` |
| `rank` | `registered_rows` | registered non-singleton row shapes | one numeric field | same family and shape | `semantic_or_materialized` |
| `limit` | `ordered_rows` | registered deterministically ordered non-singleton row shapes | one bounded count | same family and shape | `semantic_or_materialized` |
| `rollup` | `metric_coordinates` | `metric/dimension@v1`, `metric/time@v1`, `metric/dimension-time@v1` | 1-16 Metrics | coordinate-derived Entity-reduced Metric shape | `semantic_or_materialized` |
| `compare` | `metric` | compatible arity-one Metric pair | one Metric per input | coordinate-derived Delta shape | branch per logical/materialized operand |
| `compare` | `event_funnel` | compatible `event/funnel@v1` pair | one funnel per input | `delta/funnel@v1` | Module 6-owned registration and authority rule |
| `attribute` | `metric_delta` | all arity-one Delta shapes | one Delta Metric | `attribution/joint@v1` or `attribution/hierarchy@v1` | retained-row or logical semantic-expansion branch |
| `attribute` | `event_funnel_delta` | ungrouped logical `delta/funnel@v1` | one typed funnel-loss-rate target | `attribution/funnel-loss-rate@v1` | Module 6-owned registration and authority rule |
| `forecast` | `metric_time` | `metric/time@v1`, `metric/dimension-time@v1` | one Metric | `forecast/time@v1` or `forecast/dimension-time@v1` | `semantic_or_materialized` |
| `discover.point_anomalies` | `metric_time` | arity-one time-bearing Metric | one Metric | `candidate/point-anomaly@v1` | `semantic_or_materialized` |
| `discover.interesting_windows` | `metric_time` | arity-one time-bearing Metric | one Metric | `candidate/interesting-window@v1` | `semantic_or_materialized` |
| `discover.entity_outliers` | `metric_entity` | `metric/entity@v1` | one Metric | `candidate/entity-outlier@v1` | `semantic_or_materialized` |
| `discover.period_shifts` | `delta_time` | time-bearing Delta | one Metric | `candidate/period-shift@v1` | `semantic_or_materialized` |
| `discover.driver_axes` | `delta` | any Delta shape | one Metric | `candidate/driver-axis@v1` | retained-row or logical semantic-expansion branch |

`semantic_or_materialized` means the registry contains two proven-equivalent
branches and selects one from the exact input state during construction. For a
two-input operator, the selected branch vector is one of logical/logical,
logical/materialized, materialized/logical, or materialized/materialized. A
failure in one selected branch never retries another vector.

The strings in the Operator id column are the exact `operator_id` values. Every
row is one independently versioned registration key and initially uses
`contract_version = 1`. The five expanded discovery ids, not the wildcard
spelling, are individually registered.

## `rollup`

### Public contract

```python
def rollup(
    self,
    *,
    drop_dimensions: tuple[SemanticInput[DimensionKind], ...] = (),
    grain: TemporalGrain | None = None,
) -> LogicalMetricDataset:
    ...
```

The receiver must be one Entity-reduced Metric shape registered in the matrix.
At least one argument must request a real coordinate reduction. Every dropped
Dimension is an exact distinct retained coordinate, and `grain` is admitted
only when the receiver retains one time coordinate whose current grain has one
exact strictly-coarser containment path to the target.

When both arguments are present, the invocation canonicalizes to the
Observation-owned time-fold-then-Dimension-fold order and the same semantic
nodes as the equivalent two-call chain. The registry admits the combined call
only when every Metric's fold composition, evaluation-end alignment, coverage,
and empty-fold behavior satisfy that contract.

Lists, sets, generators, strings, physical columns, time-Dimension refs in
`drop_dimensions`, arbitrary axis selectors, equal/finer/incomparable grains,
and a request that changes no coordinate are rejected locally. There is no
`drop_axes`, `by`, aggregation-method, fill, approximation, or
`analysis_purpose` parameter.

The Observation Model owns the coordinate-transition and per-Metric retained
fold matrix. This operator registration consumes its exact result:

```text
MetricRollupInvocationV1
  input_shape
  ordered_dropped_dimension_refs[]
  source_time_coordinate
  target_grain_and_calendar
  metric_fold_contracts[]
  coverage_requirements[]
  output_row_contract
```

Every Metric must have one exact admitted fold for every removed coordinate.
An arity-N receiver fails atomically when any Metric lacks sufficient retained
state. It never drops the blocked Metric, substitutes `sum`, borrows another
Metric column as sufficient state, or changes exactness.

### One meaning across input states

`rollup` means fold the receiver's current rows or registered retained
sufficient state. The registry selects one of two equivalent authority
branches:

| Input state | Selected authority | Required behavior |
| --- | --- | --- |
| Logical Metric Dataset | `semantic_current` for the admitted upstream logical relation | retain a rollup node after that relation; an equivalent engine fold may be pushed down |
| Materialized Metric Dataset | `materialized_only` | scan only the immutable Artifact and its retained state |

The Logical branch does not recompute the original Metric graph at the target
coordinates. The Materialized branch does not resolve origin semantic sources.
Both branches apply the same fold ids and produce the same row meaning for
equal current input rows. A compiler that can recompute a more precise ratio or
distribution still may not do so under `rollup`; the caller must author a fresh
target-coordinate observation for that meaning.

### Output contract

The output remains a Metric Dataset with the same ordered Metric identities,
Population and target-Population authority, scope, sampling, approximation,
units, and value meaning. It removes only the requested Dimension coordinates,
replaces only the requested time grain, and carries the Observation-owned
coverage transition.

The output row key is the canonical retained Dimension tuple followed by time
when present. Dropping every Dimension from `metric/dimension@v1` produces the
explicit `metric/scalar@v1` singleton contract, including for zero input rows;
each Metric uses its registered empty-fold result. Time is never removed:
`metric/time@v1` remains time-shaped, and dropping every Dimension from
`metric/dimension-time@v1` produces `metric/time@v1`.

Definition identity binds the exact input authority token, authored occurrence,
ordered dropped Dimension refs, source and target grain/calendar identities,
per-Metric fold-contract versions, coverage contract, and output row contract.
No realized period count, generated SQL, backend placement, or Artifact row
value enters the semantic node.

The output admits the ordinary Metric continuations matched from its resulting
shape: `where`, `metric`, another compatible `rollup`, registered typed Metric
operators, and state-specific actions. `aggregate()` is absent because the
Entity coordinate was already removed. A second rollup is legal only when it
performs another strictly coarser admitted transition.

### Failure and implementation contract

Construction-time failure names the missing retained Dimension, incompatible
grain/containment edge, blocked Metric identity, required fold or sufficient
state, received input state/shape, and one valid repair. When retained state is
insufficient, the repair authors and executes a new observation at the target
coordinates; it never suggests origin replay from a Materialized Dataset.

The compiler manifest registers one relational fold lowering for every admitted
fold id. A cumulative period-end `last` fold preserves exact evaluation-end
authority and incomplete target-period coverage. A retained-state merge may use
one registered local kernel only under the common bounded Arrow/Parquet
contract. Unsupported compilation or guard overflow fails the action without
switching fold, recollecting rows, or publishing a partial Metric Dataset.

## `correlate`

### Public contract

```python
def correlate(
    self,
    *,
    method: Literal["pearson", "spearman", "kendall"] = "pearson",
    lag_range: range | None = None,
) -> LogicalAssociationDataset:
    ...
```

The receiver must contain two through sixteen distinct quantitative Metric
bindings. Metric request order establishes pair order. Every unordered pair is
identified by `(metric_key_a, metric_key_b)` with `a` preceding `b` in that
order; public labels or lexical sorting never reverse a pair.

Observation units come directly from the Metric shape:

| Metric shape | Observation unit | Lag admitted | Association shape |
| --- | --- | --- | --- |
| `metric/entity@v1` | governed Entity identity | no | `association/entity@v1` |
| `metric/dimension@v1` | exact Dimension tuple | no | `association/dimension@v1` |
| `metric/time@v1` | exact ordered time bucket | yes | `association/time-lag@v1` |
| `metric/dimension-time@v1` | time bucket within each Dimension tuple | yes | `association/dimension-time-lag@v1` |

Scalar Metrics are rejected because one row cannot establish association.
Entity identity participates in alignment but never appears in output rows.

`lag_range=None` normalizes to the single lag `(0,)`. An explicit range must be
finite, non-empty, duplicate-free after normalization, and contain only signed
integer bucket offsets. Positive lag means Metric A leads Metric B. Entity and
Dimension shapes reject an explicit lag range, including `range(0, 1)`, because
they carry no time-order authority.

The product of unordered Metric pairs, normalized lags, and Dimension series
must have a statically bounded candidate ceiling when known and an enforced
action-time ceiling otherwise. The first-cutover hard maximum is 4,096
pair/lag/series candidates per action. Exceeding it fails rather than trimming
pairs, lags, or series.

### Row contracts

Non-lag rows use:

```text
metric_key_a
metric_key_b
status
coefficient
input_observation_count
null_pair_count
complete_pair_count
```

Lag rows add exact retained Dimension coordinates when present and:

```text
lag_offset
selected_for_pair
matched_observation_count
lag_boundary_drop_count
```

The row key is the retained Dimension tuple, when any, followed by the Metric
pair and lag. Non-lag shapes have no synthetic lag column.

Pairwise null deletion occurs after exact coordinate and lag alignment.
Different pairs may therefore have different complete-pair counts. The status
vocabulary is:

```text
valid
insufficient_pairs
constant_a
constant_b
constant_both
```

Fewer than two complete pairs is `insufficient_pairs`. Constant classification
runs only after that gate. An invalid row carries a null coefficient and, for a
lag shape, `selected_for_pair=False`. A non-finite coefficient for a row
classified `valid` is an execution contradiction and fails the action.

Every Metric pair within every retained Dimension series must have at least one
valid lag candidate. Otherwise the entire action fails and publishes no
Association Dataset. Exactly one valid candidate is selected by:

1. greatest absolute coefficient;
2. smallest absolute lag;
3. smallest signed lag.

Invalid lag rows remain published beside the selected row so boundary loss,
null loss, and constant inputs remain auditable.

Pearson, Spearman, and Kendall retain their standard definitions. Spearman rank
ties use average rank. Kendall is tau-b. An engine implementation must match the
registered independent reference tolerance; unsupported exact compilation does
not fall back to pandas or another method.

### Filtering and continuations

Retained Dimension coordinates, lag, status, coefficient, selection flag, and
count fields are filterable. `metric_key_a` and `metric_key_b` are typed
semantic-ref identities and are not predicate operands in the first cutover;
the shared predicate contract deliberately has no semantic-identity literal.
Per-Metric and pair approximation disclosures are family-contract metadata and
cannot enter row predicates. The derived continuations are `where`, `rank`,
`limit`, and state-specific actions. Association rows express statistical association
only and do not enter attribution.

## `rank`

### Public contract

```python
def rank(
    self,
    by: DatasetFieldRef,
    *,
    order: Literal["ascending", "descending"] = "descending",
    ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
    partition_by: tuple[DatasetFieldRef, ...] = (),
) -> LogicalDataset:
    ...
```

`by` must resolve to one current numeric value field owned by the receiver.
Every partition field must resolve to a distinct current coordinate field and
must be part of the row key. Foreign, stale, duplicate, non-numeric, generated
status, or non-key partition selectors fail locally.

The receiver must have a non-singleton shape and a deterministic row key. The
family registration may reject structural Event or Lifecycle rows whose order
cannot change independently; Module 6 owns those registrations.

Module 5 registers exactly:

```text
Metric:       entity, dimension, time, dimension-time
Delta:        entity, dimension, time, dimension-time
Association:  entity, dimension, time-lag, dimension-time-lag
Attribution:  joint, hierarchy
Forecast:     time, dimension-time
Candidate:    all five first-cutover objectives
```

Metric scalar and Delta scalar shapes reject rank because their row contract is
an explicit singleton. Module 6 may add exact Event, Lifecycle, or Subject
registrations without widening the Module 5 list.

### Output contract

The output is a Logical Dataset that retains the input analytical family id,
qualified shape, coordinates,
Population, value bindings, and row meaning. It adds exactly one generated
field:

```text
field_id = generated.rank@v1
name = rank
role = rank
logical_type = int64
nullable = true
```

A Dataset already containing that field id rejects another `rank()` call. There
is no custom field-name parameter.

Null and non-finite ranked values do not participate in ranking. Their `rank`
is null and they sort after ranked rows inside the partition. `ordinal` assigns
unique ranks using the declared row key as the deterministic tie breaker.
`dense`, `min`, and `max` use their conventional tie semantics. Rank starts at
one. Rank is therefore nullable even when the selected input field has a
non-nullable logical schema: a non-nullable floating field may still contain a
non-finite value, and the output contract must be valid before execution.

The output ordering contract is the partition tuple in canonical coordinate
order, followed by non-null rank ascending, null rank last, followed by the
original row key. `rank()` does not drop rows and accepts no limit. A caller
uses the separate semantic `limit(...)` operator after ranking.

The generated `rank` field is filterable and may be selected by later generic
row operators. Rank consumes only current rows, so logical and materialized
branches are equivalent and never consult new semantics.

## `limit`

### Public contract

```python
def limit(self, count: int) -> LogicalDataset:
    ...
```

`count` must be an exact integer in `[1, 100_000]`; booleans are rejected.
`limit()` is admitted only when the receiver already owns a deterministic
logical ordering. Registered ordered inputs include Candidate objective order,
Forecast coordinate order, authored Association order, and the output
of `rank()`. An unordered Metric, Delta, Event, Lifecycle, or SubjectSet shape
rejects `limit()` with a repair to establish a registered order, normally by
calling `rank(...)` on an admitted numeric field. Datasource-natural order,
preview order, and materialized file order are never promoted into logical
ordering authority.

The output is a Logical Dataset that retains the input analytical family id,
qualified shape, row meaning,
coordinates, value bindings, Population, approximation, and ordering contract.
It adds no field, but it returns a new row contract whose
`DatasetCardinality.row_bound` is the smaller of the input static bound, when
present, and `static(count)`. The result retains at most the first `count` rows
under the exact input order. Limiting an already limited Dataset is legal and
normalizes to the smaller bound without changing authored occurrence
diagnostics.

Logical and materialized inputs use the state-selected
`semantic_or_materialized` branches over their exact current rows. The compiler
may push the bound only together with the complete registered ordering. It may
not apply an unordered backend limit and sort afterward. `limit()` has
`row_subset` effect and never changes Population membership by itself. An exact
`metric/entity@v1` result may be supplied later through `population=`; Module 2
then consumes only its retained Entity identity coordinate under the registered
population-input contract.

## `compare`

### Public contract

```python
def compare(
    self,
    baseline: LogicalMetricDataset | MaterializedMetricDataset,
    *,
    alignment: WindowBucketAlignment = mv.window_bucket(),
) -> LogicalDeltaDataset:
    ...
```

This is the `compare/metric@v1` variant owned here. Module 6 contributes the
separate `compare/event_funnel@v1` variant to the shared registry:

```python
def compare(
    self,
    baseline: LogicalEventDataset | MaterializedEventDataset,
) -> LogicalDeltaDataset:
    ...
```

That variant admits only `event/funnel@v1`, has no caller-authored alignment
argument, and returns `delta/funnel@v1`. Module 6 owns its compatibility,
temporal, arithmetic, row, materialization, and Finding contracts; this document
owns the common operator id and Delta protocol. Static dispatch and the two
capability links must expose the overloads without widening either one into a
union-heavy runtime signature.

The receiver is the current side. Both inputs must:

- belong to the same Session;
- contain exactly one Metric binding with the same metric identity, value type,
  unit, aggregation contract, and approximation method;
- have the same qualified Metric shape;
- have identical Dimension coordinate identities and order;
- have the same target Entity, membership predicate, non-time scope, and
  sampling definition;
- differ in reference time scope only where `window_bucket()` admits it.

Multi-Metric inputs fail locally with the exact repair
`dataset.metric(metric_ref)`. The first cutover accepts no alternate alignment
policy and no implicit current/baseline inference.

Entity and Dimension shapes align by exact row key and require equal Population
membership authority. Time-bearing shapes pair buckets by ordinal position
inside each Dimension series. Unequal bucket counts fail; they are not silently
truncated. Scalar inputs pair their explicit singleton rows.

### Row contract

The output shape maps directly from the input:

| Metric input | Delta output |
| --- | --- |
| `metric/entity@v1` | `delta/entity@v1` |
| `metric/scalar@v1` | `delta/scalar@v1` |
| `metric/dimension@v1` | `delta/dimension@v1` |
| `metric/time@v1` | `delta/time@v1` |
| `metric/dimension-time@v1` | `delta/dimension-time@v1` |

Entity and Dimension outputs retain their coordinate key. Time-bearing outputs
retain Dimension coordinates when present and add one `comparison_ordinal`
coordinate plus exact `current_time` and `baseline_time` fields. The paired
time fields are values attached to the ordinal comparison coordinate; neither
is silently renamed into one common time bucket.

Every row then contains:

```text
coordinate_presence
current_value
baseline_value
delta
relative_delta
calculation_status
relative_delta_status
```

`coordinate_presence` is `matched`, `current_only`, or `baseline_only`.
`calculation_status` is `ok`, `null_input`, or `missing_side`.
`relative_delta_status` is `ok`, `baseline_zero`, or `delta_unavailable`.

For a matched row, `delta = current_value - baseline_value` after the
registered lossless signed numeric promotion. `current_value`, `baseline_value`,
and `delta` share that promoted logical type; construction fails when no
lossless common type exists. A null side yields `null_input` and a null delta.
For a one-sided Entity or Dimension coordinate, the absent side becomes zero
only when the Metric aggregation contract declares zero as its exact empty
value. Otherwise it stays null and the row is `missing_side`.

When the promoted values and delta are finite and `baseline_value != 0`, the
nullable `float64` field is calculated by the complete contract:

```text
relative_delta = float64(delta) / abs(float64(baseline_value))
relative_delta_status = ok
```

The absolute denominator preserves the direction of the signed delta when a
Metric admits negative baselines. A finite zero baseline yields null
`relative_delta` and `baseline_zero`; a missing, null, or unrepresentable
float64 projection yields null `relative_delta` and
`delta_unavailable`. A non-null non-finite current, baseline, or promoted delta
is an action-time input contradiction and fails the complete action. No infinity
or alternate signed-denominator convention is admitted.

All output coordinates and generated comparison row fields are filterable.
The derived continuations are `where`, `rank`, `limit`, `attribute`,
`discover.period_shifts` for time-bearing shapes, `discover.driver_axes`, and
state-specific actions. The one approximation binding for this arity-one comparison is
definition metadata, not a repeated row field.

Compare consumes the exact rows represented by each selected input authority
token. Logical and materialized inputs may be mixed. It never re-executes a
materialized side, substitutes a current semantic definition, or treats equal
definition fingerprints as equal realized rows.

## `attribute`

### Public contract

```python
def attribute(
    self,
    *,
    axes: list[SemanticInput[DimensionKind]],
    mode: Literal["joint", "hierarchy"] = "joint",
    top_k: int | None = None,
) -> LogicalAttributionDataset:
    ...
```

This is the `attribute/metric_delta@v1` variant owned here. Module 6 contributes
the `attribute/event_funnel_delta@v1` variant, which additionally requires
`target: FunnelLossRate`, uses the Event-owned additive loss/denominator
components, and returns `attribution/funnel-loss-rate@v1`. It does not admit the
Metric method-selection matrix or materialized aggregate replay. Module 6 owns
that variant's exact signature and rows; this document owns the shared
Attribution layouts, status behavior, algebraic Finding protocol, and common
operator id.

The receiver must be an arity-one Delta Dataset. `axes` is required, non-empty,
ordered, and duplicate-free. Every axis must be a governed non-time Dimension
reachable through one unique safe path from both comparison branches. Time
Dimensions fail with a repair to use the existing comparison coordinate rather
than treating time as an attribution driver.

`mode="joint"` emits one resolution over the full ordered axis tuple.
`mode="hierarchy"` emits every authored prefix resolution. Supplying
`mode="hierarchy"` for one axis is rejected as meaningless. `top_k`, when
present, must be an integer in `[1, 1000]` and is applied independently within
each parent resolution before attribution arithmetic. The remainder is
represented by a typed Other cell plus an `other_mask`; no string sentinel is
inserted into the Dimension value.

### Authority branches

Admission chooses one of two branches:

```text
retained_axes
  -> every requested axis and required sufficient statistic is present
  -> semantic_or_materialized

logical_axis_expansion
  -> the Delta is logical and one or more axes are absent
  -> semantic_current
  -> extend both logical Metric branches before compare lowering
```

A materialized Delta with a missing axis or missing sufficient-statistic
contract fails locally. There is no Artifact replay, origin-graph recovery,
current-catalog join, or automatic re-observation path. The repair reconstructs
the logical current and baseline Datasets with the requested axes before
materialization.

### Method admission

The Metric's exact aggregation contract selects one registered method:

| Metric aggregation contract | Admitted attribution |
| --- | --- |
| additive sum or count | `additive_difference@v1` |
| semi-additive | `additive_difference@v1` only on non-status-time axes |
| ratio or weighted mean with named numerator and denominator/weight | `component_mix@v1` |
| first-cutover mean with retained sum and non-null count | `component_mix@v1` |
| `count_distinct(key)` with exact reproducible membership basis | `distinct_membership@v1` |
| exact median or percentile with retained exact distribution basis | `distribution_shapley@v1` |
| explicitly approximate semantic percentile with a replayable method contract | `distribution_shapley@v1` with inherited semantic approximation disclosure |

Opaque non-additive Metrics, `min`, `max`, an unbound mean, an unsupported
distribution basis, or a semi-additive status-time axis fail before execution.
Attribution never guesses additivity from observed rows.

The selected versioned method is stored once in the Attribution family contract
and is not a compiler choice.

### Exact attribution arithmetic

For one complete comparison scope, let `C_i` and `B_i` be the current and
baseline component states for partition `i`, and let `D` be the independently
computed overall Delta row. Missing additive/component partitions use zero only
where the aggregation contract declares zero as its exact empty value.

`additive_difference@v1` calculates:

```text
contribution_i = C_i - B_i
```

For `component_mix@v1`, each side retains numerator `N_i` and basis `W_i`
(denominator, weight, or non-null count). Let `W_s = sum_i(W_i,s)`,
`p_i,s = W_i,s / W_s`, and `v_i,s = N_i,s / W_i,s`. A structurally absent
`N_i,s = 0, W_i,s = 0` contributes a zero term. `W_i,s = 0` with non-zero
`N_i,s`, or a non-zero total numerator with `W_s = 0`, is a contradiction. The
registered contribution is:

```text
side_term_i,s = 0                              when N_i,s = 0 and W_i,s = 0
side_term_i,s = p_i,s * v_i,s                  otherwise
contribution_i = side_term_i,current - side_term_i,baseline
```

This is one component-mix decomposition; no alternate value/mix split changes
the persisted contribution.

`distinct_membership@v1` deduplicates `(key, partition)` separately on each
side. For each distinct key on one side, `membership_degree` is the number of
partitions containing it and each membership receives `1 / membership_degree`.
The side value for a partition is the sum of those allocations, and its
contribution is current allocated value minus baseline allocated value. Raw keys
never cross the engine or enter metadata, Evidence, rows, errors, or logs.

`distribution_shapley@v1` treats the final mapped partitions as players. For a
coalition `S`, its value is the Metric's registered percentile over current
distribution state for players in `S` union baseline distribution state for
players outside `S`. Empty and full coalitions must reproduce the independent
baseline and current endpoints. For `m` players, the exact contribution is:

```text
contribution_i = sum over S subset of N without i:
  |S|! * (m - |S| - 1)! / m!
  * (value(S union {i}) - value(S))
```

The first cutover admits at most eight players after Top-K/Other mapping and
enumerates every coalition. Nine or more players fail with a repair to lower
`top_k` or choose a coarser axis. No sampled-permutation Shapley implementation
is equivalent. Exact percentile Metrics use exact value-frequency state. An
explicitly approximate semantic percentile replays only its authored backend
method and parameters for every coalition; the inherited semantic approximation
is disclosed, but runtime cost never introduces another approximation.

### Top-K and reconciliation

Top-K membership is selected once over the complete current-plus-baseline scope
before attribution arithmetic. Descending scores and typed-coordinate order
break ties. The method-owned score is:

| Method | Partition selection score |
| --- | --- |
| `additive_difference@v1` | `abs(C_i) + abs(B_i)` |
| `component_mix@v1` | `abs(W_i,current) + abs(W_i,baseline)` |
| `distinct_membership@v1` | distinct-key memberships in the current/baseline union before allocation |
| `distribution_shapley@v1` | current plus baseline non-null frequency |

All non-selected members become one real typed Other player. Multi-axis
selection runs in authored axis order within every already-mapped parent,
including Other; hierarchy resolutions repeat the same mapped membership rather
than selecting a new Top-K view after arithmetic.

For every comparison scope and complete resolution, let `S` be the sum of
published contributions. Publication requires:

```text
abs(D - S) <= max(1e-12, 1e-9 * max(abs(D), abs(S), 1))
```

Endpoint mismatch, incomplete players, invalid component state, resource-limit
failure, or reconciliation failure aborts the whole action. The compiler may
choose only lowerings proven equivalent to these formulas and ceilings.

### Row contracts

The shapes are:

```text
attribution/joint@v1
attribution/hierarchy@v1
```

Joint rows are keyed by the full ordered axis tuple. Hierarchy rows are keyed by
the closed `active_axis_mask` plus the full typed axis tuple. Inactive typed axis
cells are null and the mask distinguishes them from real null members. The
family contract stores the ordered authored resolution prefixes once; no public
row uses a numeric resolution position as identity.

Every row contains:

```text
active_axis_mask
other_mask
current_value
baseline_value
overall_delta
contribution
share_of_total_delta
share_of_positive_pool
share_of_negative_pool
contribution_rank
status
```

The ordered resolution-prefix contract, selected method id, approximation
binding, Top-K definition and mapped membership digest, numeric tolerance, and
sufficient-statistic contract are stored once in the Attribution family contract
and committed Artifact metadata. That contract also carries
`resolution_semantics = rollup, rollup_safe = true` for additive/component
methods or `resolution_semantics = independent, rollup_safe = false` for
distinct/distribution methods; these definition facts are not repeated per row.

`active_axis_mask` is all-active for joint mode. Only a Dataset whose family
contract declares `rollup_safe = true` may be summed across child members, and
even then callers select one resolution before summing.

Positive- and negative-pool shares are non-negative within their same-sign
pools. Marivo does not label a sign as improvement or degradation. A zero total
delta yields a null `share_of_total_delta` with status
`zero_total_delta`; contribution and pool shares remain available.

Every complete resolution must reconcile its contribution sum to the exact
input overall delta within the registered numeric tolerance. A reconciliation
failure, incomplete partition set, invalid component state, or endpoint
mismatch fails the whole action and publishes no Attribution Dataset.

All axis coordinates, masks, contribution values, rank, and status row fields
are filterable. Method, resolution, rollup-safety, and approximation contracts
are definition metadata. The derived continuations are `where`, `rank`,
`limit`, and state-specific actions. Attribution does not feed back into `compare` or
discovery in the first cutover.

## Statistical tests are conditional Delta affordances

The first cutover exposes no generic `hypothesis_test` operator, no
`HypothesisDataset`, and no statistical-test method. A Metric or Population row
shape is not by itself a statistical sample, so Entity identity, Dimension
coordinates, and time buckets cannot be promoted into inferential units merely
because they can be aligned.

A future statistical test may be registered only as an exact named method on a
`DeltaDataset`. The Delta is the authority for the already aligned current,
baseline, presence, null, and difference rows; a test method may consume those
current rows but may not independently realign its source Metrics or replay
through a materialization boundary.

The method is an admitted affordance only when the Delta contract already
carries every prerequisite required by that test's theory:

- one explicit estimand, null hypothesis, alternative, and inferential unit;
- one target population plus a sampling, assignment, or repeated-measure design
  that justifies inference beyond the realized Delta rows;
- the dependence, exchangeability, distribution, and missingness assumptions
  required by the exact method;
- a mechanically valid mapping from Delta coordinates to observations without
  treating arbitrary Dimension members or autocorrelated buckets as independent;
- one predeclared multiplicity scope and correction whenever one call can emit
  more than one test decision;
- the exact effect, uncertainty, decision, quality, Evidence, Finding, and cold
  recovery contract for the method's result.

Each admitted test is its own operator variant and public Delta method. It does
not use `hypothesis=...` or `method=...` to reserve future algorithms inside one
generic dispatcher. `dataset.contract()` derives the method's availability from
the exact Delta state and consumer admission; when the prerequisites are absent,
the method is not advertised.

No first-cutover Population or Delta contract carries this inferential
authority. The design therefore reserves no empty test namespace, result family,
Help target, semantic node, quality contract, or implementation id. Adding the
first test requires a focused design that introduces its upstream authority and
complete output contract together.

## `forecast`

### Public contract

```python
def forecast(
    self,
    *,
    horizon: ForecastHorizon,
    model: ForecastModel = mv.naive(),
    interval_level: float = 0.95,
) -> LogicalForecastDataset:
    ...
```

`ForecastHorizon` is produced only by `mv.periods(count)` with an integer count
in `[1, 1000]`. `ForecastModel` is a closed value produced by:

```text
mv.naive()
mv.drift()
mv.seasonal_naive(periods=...)
```

`seasonal_naive` requires a season length greater than one. Built-in calendar
grains may supply the documented canonical season only when their coordinate
contract carries that exact calendar identity; custom semantic periods require
an explicit value. `interval_level` must be finite and in `(0, 1)`.

The receiver must be an arity-one `metric/time@v1` or
`metric/dimension-time@v1` Dataset with ordered, complete, consecutive time
coordinates and a certified continuation covering the requested horizon. Every
Dimension series must share the same history coordinate sequence and future
period contract. Null, NaN, duplicate, partial, or missing history buckets fail
the action; they are not imputed or treated as zero.

Minimum complete periods per series are:

```text
naive             2
drift             3
seasonal_naive    seasonality_period + 1
```

Naive repeats the final observation. Drift extends the first-to-last average
step. Seasonal-naive repeats the value one season behind. Prediction intervals
use the exact `normal_residual@v1` product contract below and are epistemic
estimates, not guaranteed coverage.

For one series `y[1..n]`, residuals are calculated independently per series:

```text
naive:
  point[h] = y[n]
  residual[t] = y[t] - y[t - 1]                         for t = 2..n

drift:
  slope = (y[n] - y[1]) / (n - 1)
  point[h] = y[n] + h * slope
  residual[t] = y[t] - (y[1] + (t - 1) * slope)        for t = 1..n

seasonal_naive(periods = s):
  point[h] = y[n - s + ((h - 1) mod s) + 1]
  residual[t] = y[t] - y[t - s]                        for t = s + 1..n
```

Let `sigma` be the sample standard deviation of the complete residual vector
with `ddof = 1`; when fewer than two residuals exist or every residual is equal,
`sigma = 0`. Let `z` be the inverse standard-normal CDF at
`(1 + interval_level) / 2`. The interval for horizon ordinal `h` is:

```text
margin[h] = z * sigma * sqrt(h)
interval_lower[h] = point[h] - margin[h]
interval_upper[h] = point[h] + margin[h]
```

Every intermediate and published result must be finite. The method does not
pool residuals across Dimension series. There is no automatic model choice,
fitting search, forecast-vs-actual evaluator, alternate t interval, or local
library fallback.

### Row contract

The output shapes are:

```text
forecast/time@v1
forecast/dimension-time@v1
```

One row means one projected future period for one retained Dimension series.
The row key is the retained Dimension tuple, when present, plus the exact future
time coordinate. Fields are:

```text
horizon_ordinal
forecast_value
interval_lower
interval_upper
training_row_count
```

`horizon_ordinal` starts at one. Published rows are always complete and finite;
an inability to compute a requested point or bound fails the action rather than
publishing a partial horizon. `model_id`, `interval_level`,
`interval_method = normal_residual@v1`, and approximation inherited from the
input are definition-level family-contract and Artifact metadata. Forecast-model
estimation remains epistemic meaning, not an input-sampling approximation.

Retained Dimensions, future time, horizon ordinal, forecast and interval
values, and training count are filterable. Model, interval, and approximation
facts are definition-level. The derived continuations are `where`, `rank`,
`limit`, and state-specific actions. Forecast rows do not re-enter compare, discovery,
or Metric observation in v1.

## Discovery Namespace

### Common contract

`dataset.discover` is a non-callable namespace whose five methods each own one
fixed objective and one fixed scoring contract. There is no
`discover(objective=...)`, strategy string, generic candidate schema, or
Session-level duplicate.

Every objective returns one bounded, ordered `CandidateDataset[objective]`.
Common parameters are:

```text
threshold   finite positive float for thresholded objectives
limit       integer in [1, 1000], default 50
```

`driver_axes` has no threshold. `limit` is part of the discovery definition,
not a preview bound. Candidate selection is deterministic: descending
non-negative score, then objective-specific typed coordinates, then `item_id`.
Ties are never resolved by datasource-natural order.

Every Candidate row begins with:

```text
item_id
score
reason_codes
```

`item_id` is a deterministic digest of the objective contract, exact source
authority token, typed candidate coordinates, method version, and normalized
parameters. It is a generated value and one-item selector, not a row coordinate,
row ordinal, or substitute for the shape key. A duplicate `item_id` for two
different typed coordinate encodings is a digest contradiction and fails the
action. `reason_codes` is an ordered, bounded tuple from a closed objective
vocabulary; it contains no generated narrative or raw identity.

Every shape has a complete business-coordinate row key independent of the
definition fingerprint:

| Candidate shape | Ordered row key |
| --- | --- |
| `candidate/point-anomaly@v1` | retained Dimension tuple, exact `time_coordinate` |
| `candidate/interesting-window@v1` | retained Dimension tuple, `window_start`, `window_end` |
| `candidate/entity-outlier@v1` | governed `entity_identity` |
| `candidate/period-shift@v1` | retained Dimension tuple, `window_start`, `window_end`, `baseline_start`, `baseline_end` |
| `candidate/driver-axis@v1` | governed `axis_ref` |

The key coordinates are typed exactly as their owning input or semantic ref and
must be unique before `limit` is applied. `item_id` is then calculated from the
canonical typed key encoding plus definition authority; the runtime validates
both key uniqueness and item-id uniqueness before publication.

`source_authority_kind`, the matching logical-definition fingerprint or
Artifact ref, normalized objective parameters, method version, and the inherited
approximation binding are stored once in the Candidate family contract and
committed Artifact metadata. They are never repeated as row columns.

`score` ranks candidates only within one Candidate Dataset definition. Scores
from different objectives, methods, sources, or parameterizations are not
comparable. A Candidate is a lead, not a causal claim, accepted hypothesis,
Finding, or Population member.

An eligible evaluation that finds no candidate publishes a valid zero-row
Candidate Dataset. If no input series or axis can be evaluated because of
insufficient values, constant baselines, missing coordinates, or capability
absence, the action fails instead of making zero rows mean both "none found"
and "not evaluated".

### `discover.point_anomalies`

```python
def point_anomalies(
    self,
    *,
    threshold: float = 3.0,
    limit: int = 50,
) -> LogicalCandidateDataset:
    ...
```

The receiver must be an arity-one `metric/time@v1` or
`metric/dimension-time@v1` Dataset. Each Dimension tuple is evaluated as an
independent ordered series. The fixed `point_zscore@v1` method computes the
population mean and population standard deviation over non-null points in the
current series. Candidate strength is absolute z-score; `direction` preserves
the sign. Null points are excluded and cannot become candidates.

Every evaluated series requires at least three non-null points and non-zero
finite standard deviation. Shape-specific rows add:

```text
retained Dimension coordinates
time_coordinate
observed_value
baseline_value
signed_deviation
direction
```

One row means one exact observed point whose absolute z-score meets the
threshold against its complete current series.

### `discover.interesting_windows`

```python
def interesting_windows(
    self,
    *,
    threshold: float = 2.0,
    limit: int = 50,
) -> LogicalCandidateDataset:
    ...
```

The receiver shapes and per-series baseline are the same as point anomalies.
The fixed `global_zscore_runs@v1` method forms maximal contiguous runs whose
points all meet the absolute z-score threshold. It does not search arbitrary
overlapping windows or optimize a variable window length.

Rows add:

```text
retained Dimension coordinates
window_start
window_end
point_count
peak_absolute_zscore
direction
baseline_start
baseline_end
```

One row means one maximal unusual run in one exact current series. `score` is
the peak absolute z-score. Mixed-sign runs use the sign of the point carrying
that peak; exact ties use earliest time.

### `discover.entity_outliers`

```python
def entity_outliers(
    self,
    *,
    threshold: float = 3.0,
    limit: int = 50,
) -> LogicalCandidateDataset:
    ...
```

The receiver must be an arity-one `metric/entity@v1` Dataset. The fixed
`entity_mad@v1` method compares Entity values across the exact current
Population. It uses median absolute deviation scaled by `1.4826`; when MAD is
zero but mean absolute deviation is positive, the registered fallback uses the
same median center and mean absolute-deviation scale. If neither scale is
positive, the input is constant and cannot be evaluated.

At least three non-null Entity values are required. Rows add:

```text
entity_identity
observed_value
baseline_value
signed_deviation
scale_method
direction
```

`entity_identity` is an identity-bearing row coordinate governed by Module 6's
privacy contract. This exact Candidate shape may be supplied through
`population=` but its identity is never
rendered in `repr`, lineage, Run arguments, errors, Evidence summaries, or the
Candidate card. Bounded row previews use the Module 6 redaction contract.

`entity_outliers` is the clean replacement for the old category-bucket
`cross_sectional_outliers`. A Dimension bucket is not an Entity and cannot be
promoted into this shape.

### `discover.period_shifts`

```python
def period_shifts(
    self,
    *,
    threshold: float = 2.0,
    limit: int = 50,
) -> LogicalCandidateDataset:
    ...
```

The receiver must be an arity-one `delta/time@v1` or
`delta/dimension-time@v1` Dataset with available numeric deltas. Each Dimension
tuple is evaluated separately. The fixed `delta_window_zscore@v1` method uses a
trailing window size of `max(7, floor(series_length / 10))`, computes trailing
window means, z-scores those means against their complete-series distribution,
and forms maximal contiguous runs meeting the threshold.

At least four source rows and at least two finite trailing-window means are
required, and the trailing-window mean distribution must have non-zero finite
standard deviation. Rows add:

```text
retained Dimension coordinates
window_start
window_end
baseline_start
baseline_end
window_size
peak_absolute_zscore
direction
```

One row means one bounded period whose local Delta level is unusual relative to
the current Delta series. It is not a second comparison and does not establish
a persistent regime change.

### `discover.driver_axes`

```python
def driver_axes(
    self,
    *,
    search_space: list[SemanticInput[DimensionKind]],
    limit: int = 50,
) -> LogicalCandidateDataset:
    ...
```

`search_space` is required, ordered, non-empty, duplicate-free, and contains
only governed non-time Dimensions uniquely reachable from both comparison
branches. The receiver is any arity-one Delta shape whose Metric aggregation
contract admits additive concentration scoring. Non-additive or component
Metrics use `attribute`, not this screening heuristic.

The fixed `axis_concentration@v1` method groups Delta by each candidate axis,
orders absolute group contributions, finds the smallest `k` whose cumulative
absolute contribution reaches at least 50%, and scores:

```text
1 / (k + axis_cardinality / 1000)
```

Rows add:

```text
axis_ref
axis_cardinality
concentration_member_count
concentration_share
```

One row means one governed axis whose Delta is concentrated in relatively few
members. It is a search lead, not causal attribution.

Authority follows the same clean barrier as `attribute`:

- retained axes use `semantic_or_materialized`;
- missing axes on a logical Delta use one `semantic_current` expansion of both
  upstream branches before compare lowering;
- a missing axis on a materialized Delta is rejected with no replay.

### Candidate filtering and continuations

Common filterable fields are `item_id`, `score`, and every shape-specific
generated scalar field such as `direction`.
Retained Dimension/time coordinates are filterable under the shared predicate
matrix. `reason_codes` is a bounded structured value rather than a scalar
operand. Definition fingerprints, Artifact refs, method parameters, and
approximation disclosures exist only in family-contract and Artifact metadata
and cannot enter row predicates.

Every Candidate shape admits `where`, `rank`, `limit`, and state-specific
actions. Only
`candidate/entity-outlier@v1` carries the exact Entity identity binding required
for direct `population=` admission. No candidate selection read or detached
`*Selection` value exists. Filtering by the exact `item_id` is the canonical
one-item Dataset path:

```python
candidate = candidates.where(
    mv.eq(candidates.fields.get("item_id"), candidate_id),
)
```

## Authority Assignment Matrix

The exact first-cutover assignment is:

| Operator occurrence | Logical input | Materialized input | Selected requirement |
| --- | --- | --- | --- |
| `correlate`, `rank`, `limit`, `forecast` | consume logical rows | consume retained rows | state-selected `semantic_or_materialized` branch |
| `compare` | evaluate each logical operand | scan each materialized operand | fixed ordered branch vector |
| `attribute`, axes retained | consume admitted row/sufficient-statistic contract | consume retained row/sufficient-statistic contract | state-selected `semantic_or_materialized` branch |
| `attribute`, axis missing | expand logical branches under current semantics | rejected | `semantic_current` only for logical Delta |
| row-only discovery | consume logical rows | consume retained rows | state-selected `semantic_or_materialized` branch |
| `driver_axes`, axes retained | consume admitted rows | consume retained rows | state-selected `semantic_or_materialized` branch |
| `driver_axes`, axis missing | expand logical branches under current semantics | rejected | `semantic_current` only for logical Delta |

For `semantic_or_materialized`, equivalence means the same operator over the
exact rows represented by the selected input authority token. It does not mean
a logical definition and one historical Artifact are interchangeable cache
keys or expected to contain equal rows.

Materialized selected branches validate retained fields, row-contract version,
sufficient statistics, Artifact integrity, and storage readability. They do
not require the current catalog to retain the original semantic definitions.
Semantic-current expansion binds the new dependency digest and compatibility
contract into definition identity.

## Approximation and Sample Disclosure

Every output family carries one closed structured approximation binding:

```text
ApproximationBindingV1
  population_sampling_receipt_requirement
  metric_entries[metric_key -> semantic method and parameters]
  operator_method_exactness

ApproximationKindV1 =
  exact
  | sampled_population
  | semantic_approximation
  | sampled_population_and_semantic_approximation
```

It binds the inherited Population sampling receipt requirement, Metric
approximation method and parameters, and operator method exactness. Bounded
Metric- and pair-level projections are derived from this map for cards and
Evidence. They remain in the family contract and committed Artifact metadata;
no output repeats an `approximation_kind` column per row. A pair projection is
the closed union of its two Metric entries and Population sampling state. A
projection is disclosure, not authority.

Operator-specific sample and null counts remain row fields where they can vary
by pair or series. Population requested/realized sampling facts remain
committed metadata supplied by the runtime. An operator must not claim exact
population inference merely because its arithmetic implementation is exact over
a sampled input.

Correlation methods, rank, compare arithmetic, and discovery scores are exact
over their admitted input rows. Forecast intervals and Candidate scores remain
estimates by product meaning even when arithmetic is deterministic. Attribution
inherits any
approximation in its Metric basis and must reconcile against the exact input
Delta produced under that basis.

## Quality, Evidence, and Finding Extraction

### Registered materialization contract

Every Module 5 operator registration names one immutable
`DatasetMaterializationContractV1` owned by Module 4:

```text
DatasetMaterializationContractV1
  producer_id
  producer_contract_version
  family_id
  qualified_shape_id
  quality_contract_id
  quality_contract_version
  evidence_extractor_id
  evidence_extractor_version
  finding_extractor_id
  finding_extractor_version
  validation_output_contract_ids[]
  retained_private_state_contract_ids[]
  finding_policy_id
```

These identities participate in Dataset definition identity and are supplied to
Module 4 before Run admission. The runtime invokes the registered contracts
after primary and validation stages finish but before the Artifact commit
decision. An absent contract, extractor exception, schema mismatch, non-finite
required Evidence value, or blocking quality contradiction fails publication;
the runtime never substitutes a generic extractor or publishes rows without the
matching Evidence envelope.

All families first run `dataset_structure_quality@v1`, which validates the exact
registered ordered schema, logical types, nullability, row-key uniqueness,
ordering, realized row count, action bound, and family/shape identity. Family
checks then run against the same staged rows and registered bounded validation
outputs.

### Family quality and Evidence matrix

| Producing operator | Quality contract | Required family checks | Canonical Evidence projection |
| --- | --- | --- | --- |
| `correlate` | `association_quality@v1` | unique pair/lag/series key; coefficient in `[-1, 1]`; nulls match status; at least one valid row per Metric pair | method, pair count, lag range, valid/status counts, sample-count range, approximation bindings by pair |
| `rank` | `rank_quality@v1` | source row keys and row count preserved exactly; null/non-finite inputs have null rank; tie policy and partition-local rank sequence match the registered method | source/output row counts, partition count, tie method, ranked/null-rank counts; no row values |
| `limit` | `limit_quality@v1` | output row keys are the exact ordered prefix; output count is `min(input_count, count)`; family, shape, and values are unchanged | input/output row counts, requested bound, ordering-contract id; no row values |
| `compare` | `delta_quality@v1` | coordinate-presence and status coherence; lossless promoted values; exact delta equation; relative-delta formula/status; paired time-coordinate completeness | shape, presence/status counts, matched/unpaired counts, relative-status counts, numeric-promotion id, approximation binding |
| `attribute` | `attribution_quality@v1` | mapped membership completeness; method-specific sufficient-statistic checks; endpoint reproduction; exact contribution formula; per-scope/resolution reconciliation and rank/share coherence | method id, axes/resolutions, partition counts, Top-K/Other counts, reconciliation maxima, status counts, approximation binding |
| `forecast` | `forecast_quality@v1` | exact horizon rows per series; consecutive certified future coordinates; finite point/bounds; lower <= point <= upper; training count/minimum; no cross-series residual pooling | model/method ids, interval level, horizon, series count, training-count range, residual-zero series count, approximation binding |
| `discover.*` | objective-specific `<objective>_quality@v1` | exact shape key and item-id uniqueness; non-negative finite score; objective threshold/scorer equation; deterministic ordering and limit; evaluable-zero versus not-evaluated distinction | objective/method ids, normalized parameters, evaluated unit count, candidate count, reason/status counts, score range, approximation binding |

Evidence projections contain bounded semantic refs, contract ids, aggregate
counts, numeric ranges, and reconciliation summaries only. They never contain
raw Entity identities, raw distinct keys, source rows, typed coordinate samples,
SQL, private nodes, or per-row Candidate reasons. For an identity-bearing
`metric/entity@v1`, `delta/entity@v1`, or `candidate/entity-outlier@v1`, Evidence
records only aggregate counts and the Module 6 identity-contract ref.

Typed issues are emitted in `(severity, check_id, canonical_scope_key)` order.
Warnings disclose usable null/constant/insufficient subrows already authorized
by the family status contract. Schema, key, arithmetic, identity, endpoint,
ordering, or reconciliation contradictions are blocking and publish no Artifact.

### Finding extraction matrix

Findings are bounded evidence facts, not a second result table. Every extractor
has a hard `finding_cap = 1000`. It records `eligible_finding_count`,
`emitted_finding_count`, and `finding_truncated` in Evidence; when eligible rows
exceed the cap it emits the first 1,000 under the exact ordering below. A
Finding identity is the digest of `(artifact_ref, finding_type,
canonical_item_key, extractor_contract_version)`. The canonical item key is the
canonical typed row-key encoding, except that no identity-bearing row key may be
projected into a Finding.

| Producing operator | Finding extractor | Eligible rows and order | Epistemic boundary |
| --- | --- | --- | --- |
| `correlate` | `association_finding@v1` | valid rows by descending `abs(coefficient)`, then row key | `estimated`; association is explicitly non-causal |
| `rank` | `none@v1` | zero new Findings regardless of preserved family | rank changes presentation order, not the underlying claim |
| `limit` | `none@v1` | zero new Findings regardless of preserved family | limiting selects rows but creates no new analytical claim |
| `rollup` | `none@v1` | zero new Findings; upstream Findings remain linked through lineage | rollup changes coordinate resolution under an existing Metric claim |
| `compare` | `delta_finding@v1` | non-Entity rows with `calculation_status = ok`, by descending `abs(delta)`, then row key | `algebraic`; relative delta may remain unavailable with its reason |
| `attribute` | `contribution_finding@v1` | reconciled rows by descending `abs(contribution)`, then resolution and row key | `algebraic`; `causal_claim = none` |
| `forecast` | `forecast_point_finding@v1` | Dimension tuple, then ascending horizon ordinal | `predicted`; interval is an estimate, not guaranteed coverage |
| `discover.*` | `none@v1` | zero Findings | Candidate rows are leads and do not become conclusions by publication |

The exact Module 5-owned `FindingValueV1` variants are:

| Variant | Exact immutable fields | Invariants |
| --- | --- | --- |
| `AssociationFindingValueV1` | `kind="association"`, `method: pearson\|spearman\|kendall`, `coefficient`, `input_observation_count`, `null_pair_count`, `complete_pair_count`, `lag`, `causal_claim="none"` | `coefficient` is finite; complete pairs are at least two; null plus complete pairs do not exceed input count. `lag` is a discriminated `none` variant for non-lag shapes or a `lag` variant with signed `lag_offset`, `selected_for_pair`, non-negative `matched_observation_count`, and `lag_boundary_drop_count`. |
| `DeltaFindingValueV1` | `kind="delta"`, `coordinate_presence`, `current_value`, `baseline_value`, `delta`, `relative_delta`, `calculation_status="ok"` | Numeric values are finite and use the registered lossless common type. `relative_delta` is a discriminated finite `defined(value)` or `undefined(reason="baseline_zero")` value. Only calculation-status `ok` rows are eligible. |
| `ContributionFindingValueV1` | `kind="contribution"`, registered `method`, `active_axis_mask`, `other_mask`, `contribution_kind`, `current_value`, `baseline_value`, `overall_delta`, `contribution`, three typed share values, positive `contribution_rank`, `status`, `causal_claim="none"` | Module 5 admits `contribution_kind="metric"`; Module 6 adds `loss` and `denominator_mix`. Each share is discriminated as finite `defined(value)` or `undefined` with `zero_total_delta`, `empty_positive_pool`, or `empty_negative_pool`. Masks have equal length and match authored axes; the row passed exact resolution reconciliation. |
| `ForecastPointFindingValueV1` | `kind="forecast_point"`, `model`, `interval_method="normal_residual@v1"`, `interval_level`, `horizon_ordinal`, `forecast_value`, `interval_lower`, `interval_upper`, `training_row_count` | Model is `naive@v1`, `drift@v1`, or `seasonal_naive@v1`; all values are finite, `0 < interval_level < 1`, horizon is positive, training count meets the model minimum, and lower <= forecast <= upper. |

Association Metric identities live in the outer Association subject. Metric
identity for Delta, Attribution, and Forecast lives in the outer Metric
subject. Retained Dimension/time coordinates live in the outer ordered
`Finding.coordinates`; no variant duplicates them or admits an identity-bearing
coordinate. The public read design owns the outer Finding/subject/coordinate
composition while this module owns these semantic payloads.

An eligible zero-row set produces the canonical empty Finding digest and
`finding_count = 0`. An extractor that cannot construct every selected Finding
from the staged row contract fails publication; it cannot silently skip a
malformed row. Upstream Findings remain reachable through lineage but are not
copied, re-identified, or counted as Findings produced by the new Artifact.

### Retained sufficient statistics

Materialized reads validate the exact retained-statistic contract named by the
operator occurrence. Correlation, rank, compare, forecast, row-only discovery,
and additive/extremum/cumulative-last rollup need only their public staged rows
and registered validation outputs. Non-additive rollup additionally requires
the Observation-owned exact mergeable sufficient state. Attribution requires
the method-owned state
already frozen above: numerator/basis pairs for component mix, engine-private
membership basis for distinct allocation, or exact/replayable distribution
state for distribution Shapley. These states are storage-authorized private
Artifact dependencies; they are not public columns, Finding payloads, or a
permission to replay an origin graph.

## Structured Errors and Repairs

Every construction or action failure subclasses `AnalysisError`, supplies
structured `expected`, `received`, `location`, and `repair`, and points to one
canonical Help target. The initial repair matrix is:

| Failure | Phase | Required repair |
| --- | --- | --- |
| wrong Dataset family or shape | construction | construct/project the named admitted Dataset shape |
| wrong Metric arity | construction | call `dataset.metric(metric_ref)` or observe the required Metric set |
| foreign Session Dataset or field ref | construction | reconstruct all inputs in the receiving Session |
| incompatible Population/coordinates | construction | rebuild current and baseline from one compatible Population and coordinate definition |
| non-numeric selected field | construction | select one listed numeric field |
| stale or absent `DatasetFieldRef` | construction | read the retained `field_id` from the current schema/row contract and call `dataset.fields.get(field_id)` |
| invalid method, threshold, horizon, limit, or tie policy | construction | use the bounded closed values rendered by focused Help |
| invalid rollup coordinate or target grain | construction | choose retained Dimensions and one strictly coarser compatible grain shown by the Dataset contract |
| Metric lacks an exact retained rollup fold | construction | author and execute a fresh observation at the target coordinates |
| missing attribution/driver axis on logical Delta | construction | admitted semantic-current expansion occurs automatically |
| missing attribution/driver axis on materialized Delta | construction | rebuild the logical comparison with the axis before materialization |
| distribution attribution has more than eight mapped players | action | lower `top_k` or choose a coarser attribution axis |
| correlation pair has no valid candidate | action | narrow Metrics/lags, repair constants/nulls, or provide more observations |
| forecast history incomplete or horizon uncertified | action | re-observe complete consecutive periods with certified future coverage |
| discovery has no evaluable series/axis | action | provide the method's minimum non-null, non-constant input and required coordinate |
| attribution reconciliation contradiction | action | repair source semantics/data or choose an admitted aggregation contract; never accept partial rows |
| quality/Evidence/Finding contract absent or invalid | publication | repair the owning registration or staged rows; never publish without the exact registered contract |
| compiler/backend capability absent | compilation | materialize at an explicit capable boundary or choose another registered exact method |

Errors may include bounded present field ids, shapes, Metric refs, and safe
coordinate identities. They do not render raw Entity identities, source rows,
SQL, private nodes, secrets, or an unbounded capability list.

## Contract and Help Disclosure

Static Help owns one independently resolvable target per public method and
closed policy constructor:

```text
analysis.metric_dataset.correlate
analysis.datasets.rank
analysis.datasets.limit
analysis.metric_dataset.rollup
analysis.metric_dataset.compare
analysis.delta_dataset.attribute
analysis.event_dataset.compare
analysis.funnel_delta_dataset.attribute
analysis.metric_dataset.forecast
analysis.ForecastHorizon
analysis.ForecastModel
analysis.periods
analysis.forecast_models
analysis.forecast_models.naive
analysis.forecast_models.drift
analysis.forecast_models.seasonal_naive
analysis.discovery
analysis.discovery.point_anomalies
analysis.discovery.interesting_windows
analysis.discovery.entity_outliers
analysis.discovery.period_shifts
analysis.discovery.driver_axes
```

`ForecastHorizon`, the sealed helper-produced `ForecastModel` base, `periods`,
`naive`, `drift`, and `seasonal_naive` are exact public exports.
`analysis.ForecastModel` owns the common value contract; the three model
constructors own separate focused leaves beneath `analysis.forecast_models`.
Private concrete model variants and nominal Dataset family aliases are not
exported.

Each leaf owns the reflected signature, accepted families/shapes, parameter
construction, output family, method boundary, failure rules, and one minimal
runnable example. `analysis.discovery` is a registry-owned navigation group,
not a second callable.

`dataset.contract()` owns the exact currently admitted continuations after
considering family, qualified shape, Metric arity, value types, coordinates,
state, retained fields, sufficient statistics, approximation, and
materialization blockers. It renders at most the bounded legal methods for that
exact Dataset; it does not dump the complete global matrix.

Cards render family, shape, state, method/objective, bounded input identity,
approximation, row/status counts when committed, and mechanically legal next
actions. They do not render statistical conclusions, business interpretation,
raw Entity identities, or a recommended decision.

Independent seam tests verify that every `capability_id` resolves to one live
Help target and reflected method, every output contract resolves its family and
filter fields, and every `semantic_node_kind` resolves through the compiler's
exhaustive lowerer manifest. A derived continuation index is rebuilt from
source and operator consumer admission contracts and tested for exact
reachability; it is not a second producer-owned inventory. No renderer owns or
copies any of these facts.

## Rejected Alternatives

### Retain eager Session methods as aliases

Rejected because `session.compare`, `session.correlate`, `session.forecast`, and
`session.discover.*` would create a second input-authority and Help path. The
public cutover removes them in the same release.

### Preserve detached Result and Selection values

Rejected because Association, Candidate selection, and other terminal result
objects break Dataset composition, materialization, authority, and cold
recovery. Every analytical output remains a Dataset; one Candidate is a one-row
Candidate Dataset.

### Let multi-Metric compare choose a Metric later

Rejected because it makes Delta row layout and every downstream attribution or
future test consumer ambiguous. The exact repair is an explicit
`.metric(metric_ref)` projection before compare.

### Put top-N inside `rank`

Rejected because ranking and row removal are distinct semantics. `rank()` adds
one stable fact; `limit()` defines the later row bound.

### Treat invalid correlation rows as zero

Rejected because insufficient or constant inputs do not imply no association.
They retain null coefficients and exact typed statuses, and a pair with no
valid candidate blocks publication.

### Keep a generic hypothesis test over Metric pairs

Rejected because Metric row shape and pair count do not establish an
inferential unit, target population, sampling or assignment design, dependence
model, or multiplicity contract. Repeating comparison alignment inside a test
would also create a second authority for the Delta being tested. Future exact
test methods consume an already aligned Delta and are admitted only when its
contract carries the complete method-specific inferential authority.

### Replay a materialized Delta to add an axis

Rejected because a materialized Dataset is an immutable semantic barrier.
Lineage and origin fingerprints are audit authority, not an executable graph.

### Use coordinate Candidates as Population inputs

Rejected because a time window, Dimension member, or driver axis does not carry
an exact governed Entity identity coordinate. Source replay would create a
hidden query and invent membership that the current Candidate rows do not own.

### Keep removed discovery objectives for compatibility

Rejected. `interesting_slices` overlaps explicit coordinate construction and
row ranking, `semantic_hypotheses` belongs to semantic/ontology discovery rather
than typed statistical output, and category `cross_sectional_outliers` is not
the governed Entity analysis required by `entity_outliers`.

## Vertical Acceptance Journeys

### Reuse one materialized daily regional checkpoint

```python
daily_region = (
    session.observe(metrics=[revenue], time_scope=window)
    .with_dimensions(region)
    .with_time_axis(order_time, grain=mv.grain("day"))
    .aggregate()
    .execute()
)

monthly = daily_region.rollup(grain=mv.grain("month")).execute()
monthly_total = daily_region.rollup(
    drop_dimensions=(region,),
    grain=mv.grain("month"),
).execute()
```

Acceptance proves both downstream actions scan only the immutable daily
Artifact, apply the same registered current-row fold meaning as a Logical
input, preserve exact partial-period coverage, and never execute the original
Metric sources. A non-additive fixture without retained sufficient state fails
during construction with a repair to author the target observation directly.

### Shared materialized Entity checkpoint

```python
features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
    population=queries,
).execute()

association = features.correlate(method="spearman")
outliers = features.metric(cpu_seconds).discover.entity_outliers(limit=25)

association.execute().show()
outliers.execute().show()
```

Acceptance proves both operators consume the same immutable Entity rows,
correlation never transfers the Entity feature table into an unbounded local
stage, and Candidate cards do not disclose raw identities.

### Logical time comparison with missing-axis attribution

```python
current = (
    session.observe(metrics=[revenue], time_scope=current_window)
    .with_time_axis(order_time, grain=mv.grain("day"))
    .aggregate()
)
baseline = (
    session.observe(metrics=[revenue], time_scope=baseline_window)
    .with_time_axis(order_time, grain=mv.grain("day"))
    .aggregate()
)

delta = current.compare(baseline)
drivers = delta.attribute(axes=(region, channel), mode="joint")
drivers.execute().show()
```

Acceptance proves arity-one admission, ordinal time alignment, one
semantic-current expansion of both still-logical branches, exact axis order,
and reconciliation before publication.

### Materialization barrier repair

```python
checkpoint = current.compare(baseline).execute()
checkpoint.attribute(axes=(region,))
```

When `region` is absent, construction fails before a Run with a repair to
rebuild the logical current/baseline definitions with the axis. No source query,
Artifact replay, or current-catalog join occurs.

### Statistical test affordances remain absent

```python
delta = current.compare(baseline)
delta.contract().show()
```

Acceptance proves that no Delta shape advertises a statistical-test method,
that arbitrary Entity, Dimension, or time coordinates do not unlock one, and
that no generic test Help target, result family, or private invocation node is
registered. This is a deliberate theory-authority boundary, not an unavailable
backend capability.

### Certified forecast

```python
projection = history.forecast(
    horizon=mv.periods(14),
    model=mv.seasonal_naive(periods=7),
)
projection.execute().show()
```

Acceptance proves complete consecutive history, shared panel coverage, exact
future coordinate construction, fixed model semantics, and all-or-nothing
horizon publication.

### Entity-outlier Candidate as a Population input

```python
outliers = (
    features.metric(cpu_seconds)
    .discover.entity_outliers(limit=100)
)
selected = outliers.where(
    mv.gte(outliers.fields.get("score"), 4.0),
)

followup = session.observe(metrics=[lifetime_value], population=selected)
followup.execute().show()
```

Acceptance proves the score selector is bound to the exact Candidate Dataset,
Module 2 admits only its exact Entity identity projection as membership input,
and no raw identity is collected locally or promoted into a new public Dataset.

## Implementation Evidence Required

Implementation cannot claim this design is complete from unit signatures or a
healthy local runtime alone. It requires:

1. registry snapshot tests for every exact `(operator_id, variant_id,
   contract_version)` key, capability link, invocation pattern, output contract,
   semantic-node kind, action-requirement contract, and materialization link;
2. method-signature and Help-resolution tests for every canonical path and
   removed Session duplicate;
3. row-contract builder tests proving deterministic field ids, order, row keys,
   nullability, cardinality, and definition fingerprints before execution;
4. local admission matrices covering every accepted and adjacent rejected
   family, shape, arity, logical type, coordinate, and Session combination;
5. generated input-authority topology tests covering all-logical,
   all-materialized, and every role-distinct mixed class for each operator,
   including binary ordered vectors individually, and proving each materialized
   operand is scanned without origin replay;
6. materialization-barrier tests proving attribute and driver-axis expansion is
   logical-only;
7. negative affordance tests proving no first-cutover Delta contract, Help
   target, output family, semantic node, or lowerer registers a statistical
   test merely from row shape or realized pair counts;
8. independent numerical differential tests for correlation, rank ties,
   ordered-limit prefixes, Delta arithmetic, each attribution method, forecast
   models, and discovery scorers;
9. null, constant, empty, insufficient, non-finite, tie, one-sided, and
   approximation cases for every relevant operator;
10. compiler conformance proving every registered `semantic_node_kind` resolves
   through the compiler-owned lowerer manifest to one exact portable,
   backend-specific, or admitted bounded-local implementation and no silent
   fallback;
11. local implementation differential tests proving Ibis-to-DuckDB and every
   Python kernel match the owner-defined rows, statuses, null/time/decimal,
   ordering, approximation, and failure semantics over equivalent Arrow and
   Run-staged Parquet inputs;
12. runtime tests proving incomplete Run admission precedes execution and
   failed publication exposes no partial Dataset, Artifact, Evidence, or
   Finding;
13. materialization-contract tests proving every quality/Evidence/Finding
   contract id is registered, status/value checks fail closed, extractor
   ordering and the 1,000-Finding cap are deterministic, identity-bearing rows
   leak no identity, and every `none@v1` path commits the canonical empty set;
14. cold recovery tests proving every materialized output reconstructs the same
   nominal family, shape, row contract, and authority, and yields the same
   mechanically derived continuations;
15. filter drift tests proving every generated filterable field is registered
   once and audit-only fields cannot enter predicates;
16. real-agent execution of the vertical journeys against a supported backend,
   including terminal Runtime Run/Artifact/Evidence proof rather than only a
   transcript or local harness result.

## Cross-Module Seams

### Dataset Core supplies

- nominal Dataset families and qualified shapes;
- exact row contracts, selector-only fields, state, ownership, fingerprints,
  lineage, actions, and materialized scan leaves;
- the generic local operator-construction protocol.

This module supplies family registrations and output builders for its owned
operators. It does not change the common Dataset protocol.

### Observation Model supplies

- Population and target-Population authority;
- Metric bindings, arity, semantic value types, aggregation contracts, and
  approximation intent;
- Entity, Dimension, and time coordinates and compatibility;
- shared predicate syntax, filter effect, field-resolution lifecycle, and
  filter authority assignment;
- direct `population=` admission for `metric/entity@v1` and
  `candidate/entity-outlier@v1` under one exact identity-projection contract.

This module may restrict an accepted Metric shape or value type. It may not
reinterpret a Dimension bucket as an Entity sample or create another Metric
source path.

### Compiler and Execution Boundaries consumes

- exact operator-variant contract versions and private semantic-node kinds;
- deterministic input and output contracts;
- null, tie, ordering, status, approximation, and instantiated minimum-data
  requirements.

The compiler-owned lowerer manifest maps each semantic-node kind to the
portable, backend-specific, or bounded-local implementations already proven
equivalent. The operator registry does not enumerate implementation ids.
Unsupported expression compilation, absent boundary capability, and runtime
failure remain distinct compiler/runtime failures.

For a local implementation, this module additionally supplies the exact
reference semantics, Arrow-compatible input/output contracts, dependency-free
numerical oracle, and any operator-specific stricter bounds. Module 3 owns
DuckDB/Python registration and Arrow/Parquet placement; Module 4 owns exchange
execution and cleanup. No owner promotes the private exchange to a Dataset.

### Materialization Runtime consumes

- the authority requirement selected for every operator occurrence;
- output row-contract and realized-schema validation requirements;
- approximation and sample-count disclosures;
- the exact `DatasetMaterializationContractV1`, including family quality,
  Evidence, Finding, validation-output, and retained-statistic contract ids.

The runtime validates and records those contracts. It does not choose another
operator method or authority branch after failure.

### Subject, Event, and Lifecycle supplies

- Event/Lifecycle family registrations that reuse generic operator ids where
  appropriate;
- the exact `event/funnel@v1 -> compare -> delta/funnel@v1 -> attribute ->
  attribution/funnel-loss-rate@v1` registry rows, signatures, and
  materialization contracts;
- identity privacy, temporal authority, censoring, and selection semantics.

Module 5 cannot restate or widen Module 6-owned output rows, privacy rules, or
cross-domain admission.

## Review and Acceptance Gates

Implementation and cutover acceptance must prove all of the following:

1. every first-cutover overload has one independently versioned operator
   variant row;
2. every row uses structured receiver and operand patterns that keep roles,
   families, qualified shapes, coordinates, arity, and value types correlated;
3. Metric arity, value types, coordinates, Population alignment, and Session
   ownership are closed for every admitted path;
4. every output family has a complete pre-execution row contract and one-row
   meaning;
5. null, constant, insufficient-data, tie, approximation, and publication
   behavior are deterministic;
6. every logical/materialized input topology selects one authority branch, and
   all-logical, all-materialized, and role-distinct mixed cases preserve one
   operator meaning while every materialized operand remains a scan leaf;
7. Candidate and compact analytical filterable fields are exact, and bounded
   continuations are derived from source and operator consumer admission
   contracts;
8. every rejection rule owns an expected/received/repair contract that points
   to a mechanically valid next step;
9. the compiler lowerer manifest covers every registered semantic-node kind,
   no implementation id is copied into the operator registry, and no
   unregistered operator node exists;
10. one logical-input and one materialized-input vertical journey prove the
    public, compiler, runtime, and disclosure seams;
11. no detached Result or Selection object and no eager Session-level duplicate
    survives the public cutover plan;
12. Module 2 consumes the exact Entity identity binding of the two admitted
    population-input shapes without creating a second Dataset or identity
    authority;
13. every operator occurrence resolves one registered quality, Evidence, and
    Finding extraction contract before Run admission;
14. every local placement reuses the same operator reference semantics through
    one compiler-owned DuckDB or Python registration, with Arrow/Parquet parity
    and no pandas or Polars execution fallback;
15. rollup differential tests prove identical current-row fold meaning for
    Logical and Materialized inputs, including additive, extremum,
    sufficient-state, cumulative period-end, partial coverage, and rejected
    non-additive cases;
16. rollup never removes time, admits Entity-grained input, recomputes an origin
    Metric graph, or changes one blocked Metric while retaining others.

## Frozen Module Decisions

This accepted design freezes all of the following:

1. every registered analysis operator returns a Dataset;
2. downstream analysis lives only on its natural Dataset receiver;
3. the first matrix contains the eight operator groups listed by the
   decomposition plan and exactly five discovery objectives;
4. correlation consumes 2-16 Metrics from one shared Dataset and requires one
   valid candidate per Metric pair and retained Dimension series;
5. rank is family-preserving, selector-based, adds only canonical `rank`, and
   does not bound rows;
6. compare, attribute, and forecast are arity-one;
7. compare aligns only explicit compatible current and baseline Datasets under
   `window_bucket()`;
8. attribution method admission comes from exact Metric aggregation authority
   and every resolution reconciles before publication;
9. missing attribution or driver axes may expand only a logical Delta;
10. no generic hypothesis operator, test result family, or statistical-test
    affordance exists in the first cutover; a future exact test must consume a
    Delta carrying its complete inferential authority;
11. naive, drift, and seasonal-naive are the sole v1 forecast models;
12. discovery methods and thresholds are objective-specific and Candidate
    scores never imply causal or statistical acceptance;
13. only `metric/entity@v1` and `candidate/entity-outlier@v1` may enter another
    source directly through Module 2's `population=` identity projection;
14. unusable calculations use typed null/status rows only where the owning
    publication contract explicitly permits them;
15. approximation is explicit or inherited and never selected by runtime cost;
16. logical/materialized authority is chosen during construction and never
    used as failure fallback;
17. every materialized output invokes one registered family quality, Evidence,
    and Finding extraction contract before publication;
18. all generated field roles and filter admission are output-contract-owned,
    while continuations are derived from source and operator consumer admission
    contracts and independently reachability-tested;
19. generic descriptive profiling stays outside Module 5; authored Metrics own
    reusable distribution and business-count calculations, while
    quality/Evidence owns null/non-null completeness diagnostics;
20. eager Session duplicates, Frame/Result outputs, detached selections,
    aliases, and legacy recovery do not survive cutover;
21. local placement reuses one operator reference contract; Module 3 may bind
    only DuckDB relational or exact Python-kernel implementations over
    Arrow/Run-staged Parquet, with pandas internal and Polars absent from the
    first-cutover execution union;
22. `MetricDataset.rollup(...)` is the sole coordinate-coarsening transform,
    uses `drop_dimensions` plus optional exact coarser `grain`, and returns a
    Logical Metric Dataset under one current-row fold meaning;
23. rollup is limited to Entity-reduced Metric shapes and registered retained
    folds; arbitrary axis deletion, time removal, silent sum, and origin
    recomputation do not survive cutover.

## Final Boundary

Module 5 decides which ordinary typed analysis may be constructed from an exact
Dataset contract and what typed Dataset it produces. It does not execute that
analysis, publish its authority, or define governed subject identity.

The implementation boundary is therefore:

```text
exact public Dataset inputs
  -> Module 5 admission, output row contract, authority selection, and repair
  -> private semantic operator node
  -> Module 3 compilation and execution boundary
  -> Module 4 Run, Artifact, Evidence, and recovery authority
```

No implementation slice may fill a missing operator decision by consulting the
old eager Frame behavior. This design is accepted together with the Module 1-4
seams and Module 6's Event/Lifecycle operator overload seams; implementation
remains gated by the separate Public Cutover Plan.
