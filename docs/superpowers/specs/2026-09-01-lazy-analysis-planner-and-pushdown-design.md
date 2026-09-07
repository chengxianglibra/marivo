# Lazy Analysis Source Pushdown and Pandas Execution Design

Date: 2026-09-01

Revised: 2026-09-07

Status: accepted; source-pushdown and pandas-suffix amendment

## Outcome

Compile an admitted lazy Dataset into a small, deterministic execution recipe.
At `execute()`, push each eligible contiguous part of the authored calculation
into its Ibis source engine. Execute the remaining dependent suffix in pandas.
Marivo owns analytical meaning and exact inputs; Ibis expresses source queries;
pandas owns bounded local relation and numerical calculation. Runtime owns
resources, storage, publication, and recovery.

The 2026-09-07 amendment replaces the earlier fixed engine-or-kernel placement
and internal DuckDB executor. DuckDB remains an ordinary configured datasource,
with the same source rules as other backends. There is no analysis-owned DuckDB
connection, local SQL execution domain, or DuckDB workspace for intermediate
results or Artifact scans. Earlier placement decisions are superseded, not
alternative modes.

The first cutover supports these concrete journeys:

```text
logical source -> eligible Ibis prefix -> validation -> Artifact
logical source -> eligible Ibis prefix -> guarded Arrow -> pandas suffix
                                                       -> validation -> Artifact
local/object Artifact -> guarded PyArrow read -> pandas suffix -> new Artifact
```

A chain stays lazy until its sole producing action, `execute()`. Materialized
`show()` and `to_pandas()` read committed backing; they never enter this compiler.
There is no public plan, SQL, Ibis, future, task, placement, or receipt surface.

This module consumes [Dataset Core](2026-09-01-lazy-analysis-dataset-core-design.md),
[Observation Model](2026-09-01-lazy-analysis-observation-model-design.md),
[Typed Operators](2026-09-01-lazy-analysis-typed-operators-design.md), and
[Subject/Event/Lifecycle](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md).
[Materialization Runtime](2026-09-01-lazy-analysis-materialization-runtime-design.md)
owns the producing action and its committed outcome.

## Ownership Boundary

| Concern | Owner |
| --- | --- |
| Dataset meaning, ordered operators, row/row-set contracts, input authority | Dataset and semantic operator owners |
| Pure source relation construction from that meaning | Marivo Ibis builders |
| Dialect SQL generation and expression rewrites | Ibis and its backend compiler |
| Access paths, join algorithms, CTE handling, database query optimization | Bound source engine |
| Local algorithm, required preparation, input/output and retained-state semantics | Owning method contract |
| Source eligibility, deterministic pushdown boundary and implementation dispatch | This module |
| Credentials, connection lifecycle, Arrow readers, resource budgets and cancellation | Datasource adapters and Runtime |
| One configured storage target, writers, receipts, publication and recovery | Runtime |

This module does not infer Population, select a statistical method, weaken
exactness, invent semantic enrichment, select storage, or rank execution plans.
A backend limitation cannot change the requested analytical calculation.

The 2026-09-07 identity-and-algebra amendment also consumes the
[Semantic Object Model](../../specs/semantic/semantic-object-model.md): Entity
`primary_key` is the identity `K`, while versioning supplies source version-row
coordinates. Operator owners supply exact temporal anchors, contribution
partitions, ordered folds, comparison scopes and required reducer state. These
are accepted target contracts, not claims about the current eager compiler.

## Source Pushdown and Local Suffix Rules

### Determine support before data work

One registry owns each exact semantic node or invocation/method and its admitted
source and pandas implementations. A method can be source-only, pandas-only, or
admit both under one analytical contract. Source eligibility is a deterministic
check of the registered, tested adapter/version, input types and shape, method
parameters, required semantic guarantees, and exact source bindings. It uses no
row samples, runtime statistics, trial query, or failed compilation as a probe.
Unknown source support is ineligible; it is not permission to try a query.

Traverse the authored graph in dependency order. An operator stays in a source
prefix when its inputs remain expressions in one admitted source domain and
its exact registered source implementation is eligible. Compose every such
contiguous operator into Ibis; do not introduce an earlier local boundary for
convenience. At the first ineligible operator, use its registered exact pandas
implementation if its input roles and semantics allow local execution. Otherwise
fail before data work with the unsupported operator/input contract.

After a node enters pandas, every dependent successor also uses its admitted
pandas implementation. A successor with only a source implementation fails;
local rows are never uploaded or rebound to source SQL. Independent branches
may still run their own Ibis prefixes and feed a later admitted pandas step.
Thus the local suffix is dependency-based, not a requirement to collect every
source branch before any local step runs.

This is a support-based traversal, not a cost optimizer, general graph rewrite,
SQL fragment search, or per-Ibis-expression-operation support inventory. The
cutting granularity is the existing semantic node or the exact method's declared
preparation boundary. The compiler does not cut arbitrary SQL or numerical
function bodies. Several public operators may compose into one source query;
one method may declare a source preparation followed by a pandas calculation.

Once the boundary and implementations are bound, compile every selected source
expression before data execution. Expression-construction errors, unexpected
Ibis compilation rejection, source execution errors and local errors fail the
action. They never move the boundary, retry a different implementation or source,
insert sampling, or publish partial results. Known ineligibility at planning is
an ordinary local placement decision; an execution failure is never fallback.

### Source domains and multi-input calculation

A logical source binds to its declared datasource domain. Every relation and
current semantic dependency in one emitted Ibis expression must resolve through
one exact Marivo-owned datasource/adapter binding. Two independent connections
with the same arguments or same-named tables remain distinct bindings unless
the adapter explicitly provides one shared binding. Ibis backend equality and
connection-argument equality are not proof of one execution domain.

A configured datasource such as Trino may natively address several catalogs.
That remains one source domain. Marivo does not discover a federation engine,
rebind sources through another connection, or import one input into another
source engine.

When a multi-input analysis operator cannot compose its inputs in one source
domain, a registered pandas implementation may consume those exact roles after
each source prefix has produced its contracted rows. Semantic alignment,
identity/privacy rules and complete per-input/combined budgets must all hold.
This admits, for example, bounded aligned observation results for compare; it
is not a generic cross-datasource join facility. Source-required Population,
Metric evaluation, identity matching and private-state calculations retain their
own same-domain requirements. No local implementation means an explicit error,
not an invented join or a replay of origin data.

### Identity, temporal resolution, and contribution safety

Versioned source scans do not claim uniqueness on `K` across history. For a
declared non-empty identity `K`, the semantic version-row contract validates `(K, snapshot_version)` or
`(K, valid_from)` and non-overlapping validity intervals. The consuming source
or enrichment operation supplies its exact temporal anchor and boundary rule.
Only after that resolution may the planner use identity uniqueness to admit a
to-one join. It must not subtract version columns from `primary_key`, globally
deduplicate `K`, select each Entity's last-known row, or infer a shared temporal
anchor from another operand's lineage.

Module 2 owns versioned Population endpoint-view resolution; Module 6 owns
Event occurrence and Lifecycle evaluation anchors. A source adapter lacking
the selected snapshot, required coverage or exact temporal-join support fails.
The same resolved representation and checks must feed all branches that share
that source realization. Sampling applies only after membership/version
selection and uniqueness validation, on Entity identities rather than versions.

An unkeyed computation-only source supplies no identity or unique-side proof;
the planner never treats an empty `K` as a singleton or imposes identity-plus-
version uniqueness on it. Such a source remains ineligible for Population,
Event participant and StateModel subject admission. Its own source/Metric
contract must still prove every temporal and contribution operation it uses.

A source-safe join is necessary but not sufficient for aggregation. Builders
consume the exact Metric/path partition or allocation contract and preserve
spatial/temporal order. They cannot sum overlapping tag memberships, sum
already-folded device peaks, average ratios, or exchange first/last/percentile
folds with spatial aggregation unless the owner supplies the exact state and
equivalence proof. Backend SQL rewrites remain subject to the same semantics.
Materialized input folds consume complete registered state; no origin source
can fill missing samples, component counts or allocation data.

Attribution and discovery preserve every owner-declared comparison/screening
scope in their output keys. Entity-scoped operations remain source-required;
the existence of a pandas method for a non-identity shape does not authorize
local Entity transfer. Lifecycle history preparation produces public intervals
and the Module 6 legal-transition, subject-coverage and violation parts from
one canonical replay realization. A reducer reads only those registered rows
and parts, including subjects with no public interval and legal same-time
transitions; it never rematches Events or replays a StateModel.

### Materialized inputs remain immutable scan leaves

An Artifact fixes its exact backing and reader:

- an engine Artifact supplies an immutable scan in its owning source binding;
  eligible downstream operators may be pushed into that engine;
- local and object Parquet Artifacts use authorized PyArrow readers, never an
  implicit DuckDB engine, and enter the pandas suffix;
- every reader consumes the exact primary rows and required retained parts,
  never the producer's original logical graph or current semantic source.

Projection and supported storage predicates may reduce physical reads only
when proven equivalent to the exact requested rows and retained dependency
closure. A reader does not become an aggregation or join executor. Local
calculation validates the complete required input under its normal pandas
budgets, including retained parts. A large Artifact may remain readable for a
bounded preview while being too large for a requested local calculation.

### Pandas is the only internal local executor

The source/local boundary carries validated Arrow data. Local functions use
private DataFrames, preferably Arrow-backed where the method supports those
types; NumPy, SciPy or other numerical libraries remain implementation details
inside the same local calculation. Arrow backing does not promise zero-copy
operations or bounded peak process memory.

Adjacent local steps pass DataFrames directly with the owning schema, key,
ordering and output checks. They do not serialize to Arrow/Parquet at every
operator, register SQL relations, or mutate a shared upstream input. Runtime
may group dependent local calls in one worker while retaining checks between
steps. Worker IPC and cancellation are Runtime concerns, not a second execution
engine or a public dataframe surface.

Only the root primary output and its required retained parts are published.
Private DataFrames and necessary Run staging never acquire an Artifact ref,
Evidence authority, a binding or cross-action recovery/reuse identity.

### Initial method support

| Method family | Source-first execution and local admission |
| --- | --- |
| Population, membership, Metric evaluation and logical coordinate aggregation | Source-owned Ibis calculation; no generic collection of raw semantic sources |
| Filter, projection, rank, limit, compare and admitted retained-state folds | Eligible source Ibis implementation first; exact bounded pandas implementation for the local suffix |
| Pearson/Spearman and admitted result-based discovery | Eligible source implementation first; pandas only for the exact input shapes admitted by Module 5 |
| Current naive, drift and seasonal-naive forecast methods | Source preparation where eligible, then pandas over complete governed series |
| Kendall tau-b | Declared engine alignment/null preparation where required, then pandas over complete admitted numeric pairs |
| Attribution variants | Push eligible preparation and arithmetic into the source; Module 5 admits exact local arithmetic, including Shapley weights over source-produced coalition values |
| Event matching, Lifecycle replay and identity-bearing operations | Source-required semantics from Module 6; only explicitly admitted retained-input reducers may use pandas |

These assignments retain the method owners' exactness, coverage, identity,
privacy and retained-state rules. Source support and local admission are tested
for concrete input shapes; a local implementation for an aggregate result does
not authorize collecting an Entity population or private distribution state.

## Compilation and Runtime Handoff

Dataset construction normalizes exact semantic refs, typed literals, policy
values, ordered inputs, method contracts, row contracts, and authority tokens.
It creates immutable nodes and derives the Dataset Core definition fingerprint.
It does not connect, compile against a backend, create a Run, or estimate rows.

On an execution-binding miss, Runtime admits an incomplete Run before live
resolution, compilation, data work, or resource creation. The compiler receives:

```text
ActionCompilationRequestV1
  dataset_root_handle
  captured_source_parameter_handles[]
  input_binding_context
  exact_output_row_contract
  exact_output_row_set_contract
  runtime_execution_budgets
  configured_materialization_target
```

The action performs these determined steps:

1. Validate graph closure, exact contracts and implementation registrations.
2. Resolve exact source/Artifact bindings and tested source eligibility facts.
3. Traverse dependencies, composing all eligible same-domain Ibis operators and
   binding the remaining pandas suffix. Bind required source preparation,
   single-evaluation fences, validation and retained-state dependencies.
4. Bind Arrow readers at source/Artifact-to-pandas boundaries and direct
   DataFrame handles within the local suffix. Reject missing local contracts or
   source-only successors of local nodes before any data statement.
5. Have Runtime's selected writer validate the final output and every required
   part against the configured target. It returns one write binding or fails;
   storage does not change calculation placement.
6. Compile every selected source expression and emit the immutable recipe.
   Required source fence references use declared schemas and names; Runtime
   binds those exact resources without replanning or trial queries.
7. Runtime executes, validates and publishes under its existing action protocol.

A normal contiguous source relation becomes one query. Required sampling fences,
validation queries and writes may add statements. Semantic barriers preserve
operator order and realization; they do not by themselves require a pandas
boundary or materializing every node. One SQL statement is not a global
acceptance requirement, and shared Ibis objects do not prove one scan.

One `attribute(...).execute()` may issue multiple observation/preparation queries,
then run local compare and contribution functions over their bounded results.
If compare and contribution are themselves eligible in one source prefix, they
remain there. In the local case, observation rows cross Arrow once; compare
passes its private DataFrame directly to contribution calculation. Neither
intermediate is a durable Dataset and no internal DuckDB step is inserted.

Captured source parameters retain the accepted source-definition contract:
the exact typed values are private, process-local and definition-bound; their
digest participates in definition identity. Source adapters validate captured
values before data work. Compiler code never reads ambient Session bindings,
`ContextVar` state or raw values from persisted projections. Errors and
diagnostics contain parameter identities and opaque digests only.

## Private Semantic Dataset Graph

### One typed graph, no second identity protocol

The immutable in-process graph carries roots, typed nodes and action-time
requirements. Each node has its ordered input handles, exact authority tokens,
row/row-set contracts and owner-defined semantic payload. Its construction
location provides a stable public operator path for errors. The common fields
are `kind`, `version`, `ordered_input_ids`, `authority_tokens`, `row_contract_id`
and `row_set_contract_id`, plus `action_time_requirement_ids[]` for the owning
module's typed requirements. Those requirements carry any owner-created,
graph-local realization handle required by the semantic contract. Input and
realization handles are not content-addressed identities. Unknown variants,
missing handles, cycles and contract mismatches fail graph validation.

`SemanticDatasetGraphV1` names this private graph, not a serialized recovery
format. Node handles identify authored/shared nodes inside the graph; Dataset
Core alone owns canonical analytical definition identity. Module 3 adds no
semantic-plan fingerprint, content-addressed interning protocol, canonicalization
proof stream, or separate authored-to-canonical occurrence table.

The semantic owner declares which calculations have semantically significant
realization and creates or propagates requirement handles under its exact
sharing rule. Distinct graph nodes may share one realization handle when that
rule requires it; node identity alone does not decide sharing. Dataset Core
alone encodes the resulting equivalence relation in definition identity. Its
deterministic traversal visits each normalized operand and semantic-dependency
occurrence, including repeated references, in the declared role order and
assigns first-use ordinal labels: two references to one required realization
encode `[0, 0]`, while two distinct required realization handles encode `[0, 1]`.
Only those labels and their semantic positions participate. Raw handles,
construction addresses and diagnostic occurrence paths are not hashed.
Materialized scans are leaves; no producer realization is reconstructed through
their lineage. The compiler consumes this owner-provided relation without
assigning a second identity or persisting an occurrence table.

Explicitly shared upstream handles remain shared. Distinct owner-created
required realization handles must not be merged because their other normalized
inputs are equal; separate sampling calls therefore remain separate. Equal
deterministic subgraphs need not be physically deduplicated. Builders may
memoize expression construction for the same handle within an action, but that
is neither database single evaluation nor durable reuse. Required realization
sharing is enforced by the owning action-time requirement and its exact source
fence or equivalent guarantee, not by Ibis object identity.

### Common semantic node vocabulary

The first-cutover common union is:

```text
SemanticNodeV1 =
    SemanticSourceNodeV1
  | MaterializedScanNodeV1
  | PopulationSpineNodeV1
  | MembershipFilterNodeV1
  | EntitySampleNodeV1
  | CoordinateSpineNodeV1
  | MetricEvaluationNodeV1
  | RowFilterNodeV1
  | ProjectionNodeV1
  | EntityReductionNodeV1
  | SubjectSemiJoinNodeV1
  | RegisteredOperatorNodeV1
  | RankNodeV1
  | LimitNodeV1
```

Every variant has exactly these additional fields:

```text
SemanticSourceNodeV1
  kind = semantic_source
  version = 1
  datasource_ref
  entity_ref
  source_binding_fingerprint
  semantic_dependency_fingerprint
  projected_field_bindings[]

MaterializedScanNodeV1
  kind = materialized_scan
  version = 1
  artifact_ref
  content_authority
  row_contract_fingerprint
  row_set_contract_fingerprint
  realized_schema_fingerprint
  scan_admission_handle_id

PopulationSpineNodeV1
  kind = population_spine
  version = 1
  entity_ref
  identity_signature[]
  membership_scope: UnscopedReferenceV1 | ScopedReferenceV1
  target_population: RootPopulationV1 | DerivedTargetPopulationV1
  membership_uniqueness_requirement

MembershipFilterNodeV1
  kind = membership_filter
  version = 1
  bound_predicate
  field_resolution = retained_row | reachable_semantic
  relationship_requirement_ids[]

EntitySampleNodeV1
  kind = entity_sample
  version = 1
  target_rows
  seed_request: UnseededSampleV1 | SeededSampleV1
  target_population_definition_fingerprint
  evaluation_multiplicity = exactly_once_per_action

CoordinateSpineNodeV1
  kind = coordinate_spine
  version = 1
  entity_ref
  identity_signature[]
  ordered_dimension_bindings[]
  time_coordinate: NoTimeCoordinateV1 | TimeCoordinateV1
  observation_scope_binding
  selected_contribution_binding
  relationship_requirement_ids[]
  uniqueness_requirement

MetricEvaluationNodeV1
  kind = metric_evaluation
  version = 1
  metric_key
  metric_graph_fingerprint
  analysis_entity_ref
  observation_scope: UnscopedReferenceV1 | ScopedReferenceV1
  component_temporal_alignment_contract
  selected_contribution_binding
  value_field_binding
  coordinate_aggregation_contract_id
  relationship_requirement_ids[]
  component_occurrence_ids[]
  nullable_value_requirement

RowFilterNodeV1
  kind = row_filter
  version = 1
  bound_predicate
  upstream_observation_binding
  complete_selection_coordinate_ids[]
  retained_state_selection_contract
  effect = membership | row_subset
  field_resolution = retained_row
  barrier_predecessor_ids[]

ProjectionNodeV1
  kind = projection
  version = 1
  ordered_output_field_bindings[]
  retained_coordinate_ids[]
  retained_value_ids[]

EntityReductionNodeV1
  kind = entity_reduction
  version = 1
  ordered_output_coordinate_ids[]
  metric_reduction_bindings[]
  selected_contribution_binding
  retained_component_state_requirements[]
  empty_population_contract
  output_uniqueness_requirement

SubjectSemiJoinNodeV1
  kind = subject_semijoin
  version = 1
  subject_entity_ref
  identity_signature[]
  subject_authority_token
  privacy_contract_id

RegisteredOperatorNodeV1
  kind = registered_operator
  version = 1
  operator_id
  operator_contract_version
  invocation: RegisteredOperatorInvocationV1
  ordered_semantic_dependency_ids[]
  method_contract_id
  exactness_requirement
  output_binding_contract_id

RankNodeV1
  kind = rank
  version = 1
  operator_contract_id
  partition_field_ids[]
  ordered_sort_bindings[]
  tie_contract
  null_contract
  output_field_binding

LimitNodeV1
  kind = limit
  version = 1
  operator_contract_id
  count
  ordering_contract_id
  ordered_sort_bindings[]
  cardinality_upper_bound
```

All refs, field bindings, authority tokens, predicates, scopes, sort bindings,
and requirements in these variants are existing exact closed values owned by
their supplying module. Graph-local input and instantiated requirement ids
resolve within the graph; field ids resolve against the bound row contracts;
registered contract ids resolve through their owning registry. Graph validation
requires every reference to resolve to its exact expected type before expression
construction. This is an in-process typed graph, not a serialized recovery
protocol or an arbitrary string namespace.

Sampling is not encoded as a generic row filter. Entity reduction is not
encoded as a generic aggregate. A materialized scan is not a semantic source
with nullable origin fields. These distinctions preserve barriers and authority
through normalization.

Window calculations and lag alignment remain inside the owning Metric builder
or exact method's declared preparation. For example, cumulative evaluation
retains its governed base Metric, axis and anchor; correlation preparation
retains exact Metric-pair, coordinate, lag and null semantics. Their selected
value operands, functions and output bindings come from those owning contracts.
They are not independent common graph nodes or additional cutting points.
This does not change their analytical meaning, semantic barriers, or admitted
source/pandas preparation boundary.

`RegisteredOperatorInvocationV1` is the closed discriminated union below. It is
not a generic mapping, arbitrary serialized policy, or digest standing in for
parameters:

```text
RegisteredOperatorInvocationV1 =
    CorrelateInvocationV1
      method
      lag_offsets[]
  | MetricCompareInvocationV1
      alignment_contract
  | MetricRollupInvocationV1
  | EventFunnelCompareInvocationV1
  | MetricAttributeInvocationV1
      axes[]
      mode
      top_k
  | FunnelAttributeInvocationV1
      target_step
      axes[]
      mode
      top_k
  | ForecastInvocationV1
      horizon_count
      model_contract
      interval_level
  | PointAnomalyInvocationV1
      threshold
      limit
  | InterestingWindowInvocationV1
      threshold
      limit
  | EntityOutlierInvocationV1
      threshold
      limit
  | PeriodShiftInvocationV1
      threshold
      limit
  | DriverAxisInvocationV1
      search_space[]
      limit
  | SelectEntitiesInvocationV1
  | SelectSubjectsDroppedBeforeInvocationV1
      target_step
  | SelectSubjectsInStateInvocationV1
      model_state
      at
  | EventsMatchInvocationV1
      pattern
      cohort_window
      completion_through
      matching_policy
      completeness_declarations[]
  | EventFunnelInvocationV1
      axes[]
  | EventTimeToEventInvocationV1
      from_step
      to_step
  | LifecycleReplayInvocationV1
      state_model
      window
      seed
      completeness_declarations[]
  | LifecycleDistributionInvocationV1
      at[]
      axes[]
  | LifecycleTransitionsInvocationV1
  | LifecycleDwellInvocationV1
  | LifecycleViolationsInvocationV1
```

Every field above is the exact normalized closed value owned by Module 5 or 6,
not its display label. Optional public parameters are normalized to an explicit
closed value before node construction, so omission and the documented default
have one identity. The ordered input node ids carry Dataset operands and
Population membership; `ordered_semantic_dependency_ids` carries
current semantic dependencies that are not Dataset operands. Thus different
horizons, thresholds, windows, matching policies, selectors, axes, or
completeness declarations necessarily produce different analytical definition fingerprints and
implementation inputs.

`MetricRollupInvocationV1` is the exact type owned by
[Typed Operators' rollup contract](2026-09-01-lazy-analysis-typed-operators-design.md#rollup),
including its normalized coordinate transition, per-Metric retained folds,
coverage requirements and output contract. It is not a second Module 3 payload
schema. `rollup/metric_coordinates@v1` constructs registered-operator nodes
after the receiver's current rows or retained state; it does not use
`EntityReductionNodeV1` or reevaluate the original Metric graph. A combined
time-and-Dimension reduction normalizes to the same time-fold-then-Dimension-fold
nodes as the equivalent two-call chain. The Observation owner supplies each
exact fold; Module 5 owns its admission and normalized invocation; this module
registers the matching Ibis and bounded pandas implementations. Each fold
retains its position after the upstream input across source pushdown and the
pandas suffix.

The union is complete for the accepted Module 5 and 6 matrices. Adding an
operator or a parameter that can change rows requires an explicit versioned
Module 3 amendment and exact source/pandas implementation admission for that
method. A source implementation requires an Ibis builder and tested eligibility;
a local implementation requires a registered pandas function with the same
analytical contract. Runtime plugin registration cannot widen the union. No
invocation may contain arbitrary callables, SQL, Ibis expressions, pandas
objects, generic payload mappings, or optional-field mega-nodes.

### Normalization and identity

Dataset Core owns the definition fingerprint over normalized analytical inputs.
Method parameters, ordered input authority, semantic dependency digests, exact
source-binding digests, contract versions and Core's canonical sharing relation
participate. Backend connections, SQL text, implementation objects, execution
budgets, storage choice and timing
do not. Changing a numerical method's semantics requires its owning contract
version to change; implementation-version diagnostics cannot hide such a change.

Normalize defaults and typed values once under the owning contract. Preserve
operator order, input roles and barrier position. There is no action-time graph
rewrite phase, general CSE pass, SQL-equivalence identity, or substitution of an
Artifact for a logical branch.

## Semantic Correctness Requirements

These requirements survive the execution simplification:

- One Population identity/coordinate spine is independent of Metric missingness.
  Each Metric branch is unique at its spine key before a nullable left join.
- Relationship paths and fanout authority come from semantic admission. A final
  `DISTINCT` or `GROUP BY` cannot repair an unsafe join.
- Membership selection time, observation time and coordinate time keep their
  separate owners. Authored filters retain exact coordinates and contributions.
- Logical aggregation uses governed Metric semantics. Materialized aggregation
  and rollup use only retained rows and admitted sufficient state, preserving
  partial-period coverage and non-additive rejection.
- Sampling, Entity reduction, immutable scans, subject selection, Event matching,
  Lifecycle replay, component composition, cumulative evaluation, approximation
  and identity projection remain semantic barriers.
- A shared volatile Population or selected-contribution realization is evaluated
  once when the semantic contract requires it. The adapter must provide an
  exact action-scoped fence or equivalent guarantee; otherwise execution fails
  before data work. Distinct required realization handles stay separate even
  when their other normalized inputs match. Ordinary CTE/Ibis object reuse
  is insufficient proof.
- Schema construction is checked before execution; realized types, uniqueness,
  counts and row-dependent checks are validated at their actual execution
  boundaries. Validation may use explicit extra queries over the same required
  realization. It is not an instruction to materialize and rescan every node.
- Ordered output retains its full governed order and unique tie-breaker.
  Materialized reads of unordered output use the owning canonical row-key rule.
  Natural backend order is never presentation authority.
- No fallback changes exactness, samples rows, invents null values, hides a
  failed check, or publishes partial rows.

## Implementation Registry

One internal registry maps the exact semantic node and invocation/method to its
source-only, pandas-only, or source-and-pandas contract. It supplies admitted
callables, versions, input/output validators and source eligibility facts or
local input requirements. The common `registered_operator` node is dispatched
by its closed invocation and method, not one universal implementation.

The source-support facts live with the owning implementation registration and
cover tested adapter/version and exact type/shape/parameter scope. They do not
form a second inventory of ordinary Ibis operations. A missing whole method
registration, duplicate dispatch or unowned implementation is a registry error;
a registered method's deliberately absent source implementation is ordinary
ineligibility. There are no route arrays, candidate rankings, per-action plugin
discovery or dependency-fingerprint manifests.

A source builder constructs expressions only; it cannot execute, connect,
inspect runtime statistics, switch backend or rewrite generated SQL. A local
function cannot query a datasource or replay an input's origin graph. Admitted
source and pandas implementations share one analytical contract and independent
semantic conformance fixtures, including null/key/order/type and retained-state
behavior. Physical eligibility and placement do not enter Dataset definition
identity or override a same-Session execution-binding hit.

Source eligibility is checked before compiling selected expressions. Successful
compilation remains necessary but does not prove server acceptance, correct
rows, streaming behavior or Arrow type agreement. A mismatch between declared
support and actual compilation is an error in the selected path, not a signal
to choose pandas. Adapters claim only scope established by integration tests.

The adapter boundary contract covers exact source/Artifact binding, required
single-evaluation resources, bounded readers/writers and cancellation. It does
not import local data into source engines or provide fallback implementations.

## Execution Recipe

`PhysicalStageGraphV1` is a private in-process execution recipe:

```text
PhysicalStageGraphV1
  steps[]
  primary_output
  validation_outputs[]
  retained_outputs[]

ExecutionStepV1 =
    EngineQueryStepV1
      exact_domain_binding
      expression_handle
      ordered_input_handles[]
      output_contract
      required_resources[]
  | PandasStepV1
      implementation_id_and_version
      input_handles[]
      exact_input_contracts[]
      output_contract
      runtime_budget
  | ValidationStepV1
      owning_check_id
      input_handles[]
      required_resources[]
```

Step handles, expressions, functions and source temporary aliases are
process-private. The recipe has no physical-plan fingerprint or recovery
serialization. Stable topological ordering provides reproducible diagnostics;
Runtime records normal Run/Artifact provenance rather than compiler state.
Validation uses owner-defined source checks or bounded checks over local data.

The final writer is Runtime-owned. `BoundMaterializationOutputV1` binds the
configured target's writer to exact primary/retained producer handles and their
contracts. It is neither a sink selection result nor a storage receipt. A pure
source result may stream straight to its writer without a pandas step.

Every resource-producing step declares process-lifetime, connection-lifetime,
or recoverable-cancel behavior. Work that can outlive its owner requires a
reserved recoverable locator and a proven cancellation/fencing path. Runtime
owns reservations and terminal recovery; recipes are never persisted or resumed.

## Arrow and Local Resource Boundaries

### Exact exchange schema

`PhysicalExchangeV1` names source/Artifact-to-pandas input boundaries and local
outputs passed to storage, with producer/consumer, ordered Arrow-compatible
schema, identity/privacy policy and Runtime-owned guards. Local-to-local edges
carry validated DataFrames directly. There is no selectable transport-mode union
or requirement to serialize each intermediate. Same-domain source relations
compose as Ibis; Artifact reads use the exact receipt-authorized reader.

Adapters validate actual batches for field order, type, nullability, decimal
precision/scale, timezone, dictionary normalization and nested-value rules. Any
conversion must be lossless under the row contract; unsupported or overflowing
values fail. Fetch size and individual variable-width allocations must be
bounded before an unbounded allocation could occur.

The adapter evidence must retain integer `SUM` widening coverage: the reviewed
Ibis 12.0.0 / DuckDB 1.5.3 datasource combination produced a declared `int64`
Arrow reader with actual `decimal128(38, 0)` batches. DuckDB is a configured
source in this fixture, not an internal executor. This is no permission to cast
lossy values or widen a public row contract after work.

### Complete local input and bounded calculation

Before invoking a local step, Runtime validates all its complete required
inputs, whether Arrow reader outputs or prior DataFrames, under per-input and
combined row/decoded-byte guards. Required retained parts count with primary
rows; small displayed output does not imply small computational input. Input
overflow prevents that step from starting, and failure discards any earlier
private results. Source and local output schemas, keys and ordering remain
checked at their actual boundaries.

Pandas reads Arrow-backed columns where supported. Numerical functions may
convert to their required arrays within the bound method; no general zero-copy
or Arrow-only execution guarantee is made. Guards also cover join cardinality,
series/history length, Metric pairs, coalition counts, output size and other
method-specific dimensions. Runtime separately enforces intermediate allocation,
peak worker memory and deadlines; decoded bytes alone are not a process-RSS cap.
Shared input DataFrames must not be mutated by downstream functions.

Local functions may reread their admitted inputs. Neither Parquet staging nor
spill widens local admission or creates an out-of-core pandas execution mode.
Hard-deadline work uses a terminable worker with cleanup and terminal proof;
a clock check after a blocking numerical call is insufficient.

### Artifact reads and source budgets

Authorized PyArrow readers supply local/object Artifact rows and exact retained
parts under the same local-input rules. They may project or prune only under
the proven row contract; they do not hide an unbounded local join or aggregate.
An Artifact whose required computational payload exceeds the pandas budget
fails local consumption even if it was successfully written as a larger stream.
No hidden DuckDB reader or larger-than-memory local executor is available.

Ordinary source engines, including a configured DuckDB datasource, retain their
adapter-owned query, memory/disk where applicable, cancellation and bounded
result-reader budgets. Source-required fences remain source resources. Their
existence does not introduce an analysis-owned DuckDB domain.

### Storage streams are independent

A selected writer may stream a large source result under batch-memory and
stored-byte limits without assembling a complete Table or DataFrame. The same
rule applies to required retained parts; all published payloads must fit the
selected target's aggregate storage policy. Writer streaming does not grant
pandas admission to that data or change a local result into a source relation.
Late streaming failure discards private partial state and publishes nothing.

## Failure and Disclosure

The bounded compiler failure categories are:

| Phase | Meaning |
| --- | --- |
| graph_validation | Invalid graph or incompatible analytical contract |
| implementation_registration | Missing or inconsistent method registration |
| execution_boundary | No legal source/pandas placement or required reader/fence |
| source_binding | Invalid declared source or captured parameter binding |
| ibis_expression_construction | Selected source builder cannot express admitted meaning |
| ibis_backend_compile | Selected Ibis source expression is rejected |
| storage_selection | Configured target has no valid exact write path |
| stage_execution | Selected source query or pandas function fails during data work |
| transfer_guard | A source/local input, local result or writer exceeds its guard |
| output_validation | Realized rows/types/keys violate the output contract |

A known unsupported source method with an admitted pandas implementation is a
planning decision, not a failed Run phase. Once selected, source construction,
compilation and execution failures are terminal for that action. No failure
selects another implementation, changes the method, moves the boundary, changes
source/sink, truncates data or publishes partial authority.

Errors identify the exact public operator/input role, expected contract,
received safe facts and a reachable repair. Source-domain reasons use safe
Marivo labels, never connection strings, raw identities, SQL or storage paths.
A local budget failure may suggest a valid narrower scope or upstream reduction;
it cannot suggest an unimplemented relocation, sampling or replay path.

`contract()` renders semantic/local-input requirements derivable without live
work. It does not promise a particular source placement or successful backend
compilation. Static Help owns method semantics, failures own concrete repair,
and Artifact cards own outcomes. Optional bounded diagnostics may summarize
selected source prefixes, the pandas boundary and its eligibility reason, query
counts, transfers and timings; they are not persisted plans or publication gates.

## Cross-Module Seams

- Dataset Core supplies exact roots, identity, row contracts, ownership and
  actions. No new public execution state or placement control is added.
- Observation supplies spines, predicate phases, contribution bindings, sampling,
  identity/version resolution, fanout and contribution-partition proofs, ordered
  folds and retained state; placement cannot weaken them.
- Modules 5/6 supply exact methods, local admission and source-required work,
  reference semantics, identity/privacy rules and family validation.
- Runtime supplies admitted bindings, budgets and one configured storage target.
  It validates Arrow/DataFrame inputs, invokes the determined steps, journals
  resources and commits receipts, quality, Evidence and Findings atomically.
- Public Cutover validates source-first placement, pandas suffixes and exact
  input/backend combinations with terminal Runtime evidence.

## Rejected Alternatives

- Public SQL/Ibis/pandas input, a generic UDF or arbitrary callable would bypass
  governed semantics.
- Local collection after every observation would discard eligible source
  pushdown and move source-required identity/state work out of its owner.
- An internal DuckDB executor would add a second local calculation environment,
  exchange and resource lifecycle without a required bounded-result use case.
- Requiring every numerical algorithm to become SQL would expand backend parity
  work without a current need.
- Cost-ranked plans, exhaustive SQL fragment search, automatic engine imports,
  source federation and failure-triggered placement are outside this traversal.
- Source re-entry after a pandas step or Arrow/Parquet serialization after every
  local operator would add unnecessary execution and data-conversion boundaries.
- General CSE, separately fingerprinted compiler graphs and occurrence interning
  would add identity machinery beyond Dataset Core's authority needs.
- One-SQL acceptance and declaration-only Arrow checks would ignore required
  fences, validation work and actual transport correctness.
- Removing cancellation, immutable leaf identity or atomic publication would
  weaken correctness rather than simplify calculation.

## Delivery and Acceptance

Implement in this order, preserving each public family's contract:

1. Lazy source observe/aggregate/compare/rank, deterministic eligibility checks,
   shared-spine semantics, configured writer and immutable engine Artifact scans.
2. One unsupported-source-but-admitted-pandas suffix, bounded PyArrow Artifact
   reuse and forecast, with exact input/output validation and cancellation.
3. More method/shape/backend coverage with independent source/pandas semantic
   parity fixtures. Source-required identity methods remain individually tested.

Required evidence includes:

1. Construction performs no datasource call or Run creation. Only a binding-miss
   `execute()` plans/compiles; materialized reads never compile origin graphs.
2. A fully eligible same-domain filter/aggregate/rank chain, including any
   governed window calculation inside its owning builder, remains in source
   Ibis, with separate fence/validation/write statements only as required.
3. A known ineligible middle operator starts the pandas suffix; a later operator
   that supports source SQL still stays local. No compilation exception was used
   to discover the boundary, and no local result is uploaded to the source.
4. A declared supported source builder that fails construction, compilation or
   execution produces one failed action with zero pandas replacement calls.
5. Two independent source branches push down their eligible prefixes and feed
   an explicitly admitted pandas compare under combined guards. An unadmitted
   identity/membership cross-domain combination fails before data work.
6. Independent equal-argument DuckDB datasource connections are not fused into
   one source expression; same-named tables cannot bypass binding identity.
7. Population null retention, fanout rejection, authored predicate order, shared
   sampling, temporal coverage, retained folds and source/pandas parity pass
   adversarial tests. Required single evaluation is proven beyond Ibis reuse.
8. PyArrow reads exact local/object Artifact backing and retained parts. Engine
   Artifacts support eligible pushdown over immutable rows. No reader replays
   origin data and no internal DuckDB connection is opened.
9. A forecast followed by local filtering passes a checked DataFrame directly;
   no intermediate SQL, per-step Arrow/Parquet serialization or Artifact occurs.
10. Actual Arrow batches cover integer widening, decimal, timezone, nullability,
    variable-width guards and overflow; declaration-only checks cannot pass.
11. Source/Artifact input beyond the local budget prevents its pandas consumer;
    oversized retained state fails even when primary rows are small. Local join
    expansion, output, peak-memory and deadline failures publish nothing.
12. A large source output can stream to storage above the local collection cap;
    a later pandas action requiring that payload fails its input guard. It never
    obtains an implicit DuckDB reader or an out-of-core computation path.
13. Source-only successors of local data and missing local implementations fail
    before queries, with exact operator/input reasons and no new source reads.
14. One configured target is validated without changing placement. Process loss
    is reconciled from exact resource journals; only committed Artifact metadata
    is recovery authority and no private stage is resumed.
15. At least one real-agent journey covers each initial path against admitted
    backends, reading terminal Run/Artifact/Evidence outcomes. Mocked health,
    compile snapshots and eager tests alone do not establish acceptance.
16. Every accepted operator variant reaches one declared common node or closed
    registered invocation. Rollup reaches `MetricRollupInvocationV1`; window and
    lag preparation remain covered by their owning method's conformance cases.
17. Dimension removal, time coarsening, time removal and combined rollup pass
    logical, engine-Artifact and local-Artifact paths where admitted. The combined
    form and normalized time-then-Dimension chain have identical fold semantics;
    no path substitutes Entity reduction or origin Metric reevaluation.
18. Reconstructing the same required realization sharing pattern preserves Core
    definition identity independently of raw handles; shared `[0, 0]` and separate
    `[0, 1]` patterns differ. Execution proves one realization per shared handle,
    preserves distinct required realization handles, and treats Artifact scans as
    leaves. No compiler fingerprint or persisted occurrence table is introduced.

## Owner-Confirmed Decisions

On 2026-09-07 the owner accepted this source-pushdown/pandas replacement:

1. Keep lazy Dataset semantics, explicit execution and immutable Artifact reuse.
2. Push every eligible contiguous operator into its Ibis source before data work.
3. Execute the remaining dependent suffix in pandas with exact input contracts.
4. Determine support from tested registrations; never use failure as fallback.
5. Keep DuckDB only as an ordinary datasource, not an internal local executor.
6. Pass local DataFrames directly and use Arrow at source/storage boundaries.
7. Bound complete local inputs, required parts, expansion, memory and deadlines;
   large streaming storage is independent of local computation admission.
8. Preserve semantic barriers, identity/privacy, single realization, one storage
   target, exact validation, cancellation and atomic publication.
9. Retain no public compatibility path or alternate execution mode.

## Final Boundary

Marivo determines analytical meaning and exact inputs. The compiler pushes the
eligible source prefixes and binds a bounded pandas suffix before data work.
Runtime executes that recipe once and commits its complete result. Failure
never changes the chosen computation or creates an intermediate public result.
