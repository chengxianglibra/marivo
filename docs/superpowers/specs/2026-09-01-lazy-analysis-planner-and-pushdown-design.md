# Lazy Analysis Direct Compiler and Fixed Execution Boundaries Design

Date: 2026-09-01

Revised: 2026-09-05

Status: accepted; fixed-execution-boundary amendment

## Outcome

Compile an admitted lazy Dataset into a small, deterministic execution recipe.
Marivo owns analytical meaning and input authority; Ibis expresses relational
calculation; the bound engine executes it; a fixed Python kernel owns a named
numerical algorithm. Runtime owns resources, storage, publication, and recovery.

The 2026-09-05 owner-approved amendment replaces generalized engine/local route
selection, maximal partial-SQL placement, federation discovery, temporary
Artifact import, general common-subexpression elimination, and ranked sink
negotiation. This document is the current Module 3 authority. Earlier
owner-confirmed placement decisions are superseded, not alternative modes.

The first cutover supports three concrete journeys:

```text
logical source -> same-domain Ibis relation -> validation -> Artifact
local Artifact -> DuckDB/Ibis relation      -> validation -> new Artifact
logical or materialized time series -> guarded Arrow -> Python forecast
                                    -> validation -> Artifact
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
| Pure relation construction from that meaning | Marivo Ibis builders |
| Dialect SQL generation and expression rewrites | Ibis and its backend compiler |
| Access paths, join algorithms, CTE handling, database query optimization | Bound execution engine |
| Numerical algorithm, exact kernel input projection, output semantics | Owning method contract |
| Fixed-domain binding, implementation dispatch, guarded kernel boundaries | This module |
| Credentials, connection lifecycle, resource budgets and cancellation | Datasource adapters and Runtime |
| One configured storage target, writers, receipts, publication and recovery | Runtime |

This module does not infer Population, select a statistical method, weaken
exactness, invent semantic enrichment, select storage, or search execution
locations. A backend limitation cannot change the requested calculation.

## Fixed Execution Rules

### Relational operations inherit one input domain

A logical source binds to its declared datasource domain. An admitted relation
builder consumes only inputs in that same domain and returns a relation in it.
Adjacent relational nodes compose directly as one Ibis expression. There is no
separate relational IR, pushdown-rule registry, SQL postprocessor, cost model,
fragment search, or requirement to find the largest remotely executable graph.

For multiple inputs, every relation and explicit current semantic dependency
must have the same Marivo-owned execution-domain identity. This check is separate
from logical/materialized authority admission. Known conflicts fail at
construction; facts requiring backing or connection resolution are checked
after Run admission and before any data statement.

Marivo creates domain identity from its own datasource and adapter-owned binding
identity. It must not use Ibis backend equality, connection argument equality,
or the expectation that Ibis rejects multiple backends as proof. All tables in
an emitted expression must resolve through the exact admitted binding. Two
independent connections with the same arguments or same-named tables remain
different bindings unless the adapter explicitly provides one shared binding.

A configured datasource such as Trino may natively address several catalogs.
That is one datasource domain. Marivo does not discover a federation engine,
choose between federation routes, or rebind separate datasource declarations
through another connection.

### Materialized inputs stay at their backing

An Artifact is an immutable scan leaf. Its receipt and registered reader fix
its execution domain:

- local Parquet Artifacts use the Run's local DuckDB domain;
- engine Artifacts use their exact owning engine binding;
- object Parquet Artifacts use their registered direct reader, fixed by the
  storage protocol rather than selected per consuming query; the first-cutover
  object reader is DuckDB with authorized direct Parquet access.

The local DuckDB domain may bind several local/object Artifact leaves through
one connection. It reads their immutable rows and required parts, not their
origin graphs. Reader configuration is execution context, not new public Dataset
state or a user-authored placement parameter.

A relation may combine logical and materialized inputs only when those inputs
already share an admitted domain. It never imports an engine Artifact into
another engine, uploads local rows to a remote datasource, or downloads remote
relations to DuckDB to make a join possible.

A known domain mismatch returns an exact input-role and domain reason. A repair
may suggest independently executing reachable public inputs only when the
configured storage target and its fixed reader actually put the required rows
in a common domain within their guards. `execute()` alone does not guarantee
this. There is no public relocation API in the first cutover, no hypothetical
configuration command in an error, and no automatic execution of suggested
intermediates.

### DuckDB is the local relational engine

DuckDB executes relations whose data is already available through the local
reader or a declared Python output boundary. The same Ibis builders express
relational semantics there. DuckDB is not a second implementation of a failed
remote operator and has no `only_when_no_exact_engine_lowering` selection rule.

A source backend that cannot compile a registered relational method fails.
The compiler does not retry that method in DuckDB, pandas, Polars, or Python.
An adapter-specific Ibis builder is permitted only for a concrete method with
proven equivalent semantics on that adapter; it does not introduce another
placement. Complex methods are admitted per tested backend, not promised on
all six datasource families at initial cutover.

### Python is a fixed algorithm boundary

Each exact registered method chooses one implementation category:

```text
relational -> Ibis builder in the inherited domain
python_kernel -> one named, versioned numerical implementation
```

The compiler dispatches from the exact node/invocation/method contract, never
from a failed compile or backend cost. Even a Python algorithm that could be
rewritten in SQL remains a Python method until an explicit design amendment
changes its implementation contract. There are no engine-versus-kernel route
candidates for the same method.

The method owner supplies exact input projections, ordering, minimum-data rules,
resource-size dimensions, output schema, and reference semantics. Any upstream
relational preparation is explicit in that method's recipe. It cannot change
sampling, grain, Population, filtering phase, or sufficient statistics merely
to shrink an input. The compiler does not infer preparation by cutting a graph.

A kernel consumes complete guarded Arrow inputs, after all inputs have been
received and validated. Numerical or pandas objects remain kernel-internal.
Its Arrow output is a local relation; a subsequent relational operator binds it
to the same Run-local DuckDB domain. No automatic upload back to a remote engine
is allowed. A multi-input kernel may collect its exact declared inputs only if
its method explicitly admits those roles and every input and combined guard;
this is not permission for a general cross-datasource join. The first forecast
kernel has one complete time-series/panel input.

The kernel boundary creates no public intermediate. Runtime may use its private
Run staging for buffering, cancellation and cleanup; it cannot publish or reuse
that buffer as an Artifact.

### Initial method placement

| Method family | Fixed execution contract |
| --- | --- |
| Population, membership, Metric evaluation, coordinate aggregation, retained-state rollup | Ibis in inherited domain |
| Filter, projection, rank, limit, window and compare | Ibis in inherited domain |
| Pearson/Spearman and high-cardinality Entity outliers | Exact Ibis implementation on tested engines; unsupported otherwise |
| Current naive, drift and seasonal-naive forecast methods | Fixed Python kernel over complete validated time series |
| Kendall tau-b | Fixed Python kernel over complete numeric pairs after exact engine alignment/null accounting; oversized input fails |
| Attribution and other discovery variants | Module 5 fixes Ibis methods and the engine-coalition/Python-weight Shapley recipe; no runtime alternative |
| Event matching, Lifecycle replay and identity-bearing reducers | Fixed per-method engine implementation from Module 6; no local identity fallback |

This table assigns execution responsibility, not new product admission. The
method and family owners retain their precise input, approximation, identity,
coverage, and numerical contracts.

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

There is one producing action, one configured target, and no optional
inspection action or candidate list. Budgets and targets are Runtime-owned
values. They do not enter the analytical definition or execution-binding key.

The action performs these fixed steps:

1. Validate graph closure, exact contracts and implementation registrations.
2. Resolve source/Artifact bindings; check every relational input domain.
3. Build Ibis expressions in authored order, stopping only at explicit kernel,
   immutable scan, required single-evaluation, or validation boundaries.
4. Bind exact kernel inputs and required single-evaluation resources.
5. Have the selected storage writer validate the configured target against the
   final output domain, primary data and retained parts. It returns one write
   binding or fails; it cannot change calculation placement.
6. Compile every engine expression, then emit the fixed execution recipe.
   Downstream kernel-output/fence relations use schema-declared temporary
   references at compile time; Runtime later binds their exact registered
   schemas and names before execution, without replanning or a trial query.
7. Runtime executes, validates and publishes under its existing action protocol.

A normal same-domain relation becomes one query. Required sampling fences,
validation queries and writes may add statements. One SQL statement is not a
global semantic or acceptance requirement, and sharing an expression does not
promise one database evaluation or one scan.

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
and `row_set_contract_id`. Input ids are graph-local handles, not an additional
content-addressed identity. Unknown variants, missing input handles, cycles and
contract mismatches fail graph validation.

`SemanticDatasetGraphV1` names this private graph, not a serialized recovery
format. Node handles identify authored/shared nodes inside the graph; Dataset
Core alone owns canonical analytical definition identity. Module 3 adds no
semantic-plan fingerprint, content-addressed interning protocol, canonicalization
proof stream, or separate authored-to-canonical occurrence table.

Explicitly shared upstream handles remain shared. Equal separately constructed
subgraphs need not be physically deduplicated. Builders may memoize expression
construction for the same handle within an action, but that is neither database
single evaluation nor durable reuse.

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
  | WindowNodeV1
  | RankNodeV1
  | LimitNodeV1
  | LagNodeV1
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
  action_time_requirement_ids[]

WindowNodeV1
  kind = window
  version = 1
  operator_contract_id
  partition_field_ids[]
  ordered_sort_bindings[]
  frame_contract

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

LagNodeV1
  kind = lag
  version = 1
  operator_contract_id
  partition_field_ids[]
  ordered_sort_bindings[]
  offset
  gap_contract
  output_field_binding
```

All refs, field bindings, authority tokens, predicates, scopes, sort bindings,
frames, and requirements in these variants are existing exact closed values
owned by their supplying module. A field ending in `_id` refers to one
registered exact contract, not an arbitrary string namespace. Graph validation requires every id to resolve through its owning registry
before expression construction. This is an in-process typed graph, not a
serialized recovery protocol.

Sampling is not encoded as a generic row filter. Entity reduction is not
encoded as a generic aggregate. A materialized scan is not a semantic source
with nullable origin fields. These distinctions preserve barriers and authority
through normalization.

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

The union is complete for the accepted Module 5 and 6 matrices. Adding an
operator or a parameter that can change rows requires an explicit versioned
Module 3 amendment plus one fixed implementation category for the exact
method. Relational methods require an Ibis builder; numerical methods require
one registered Python kernel. Runtime plugin registration cannot widen the union. No
invocation may contain arbitrary callables, SQL, Ibis expressions, pandas
objects, generic payload mappings, or optional-field mega-nodes.

### Normalization and identity

Dataset Core owns the definition fingerprint over normalized analytical inputs.
Method parameters, ordered input authority, semantic dependency digests, exact
source-binding digests and contract versions participate. Backend connections,
SQL text, implementation objects, execution budgets, storage choice and timing
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
  before data work. Ordinary CTE/Ibis object reuse is insufficient proof.
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

One internal registry maps the exact semantic node and, where applicable,
registered invocation/method contract to either a pure Ibis builder or one fixed
Python kernel recipe. The common `registered_operator` node is dispatched by
its closed invocation and method, not treated as one universal implementation.

A registration supplies its callable, contract version, input/output validator
and tested adapter scope or exact kernel input recipe. There are no route arrays,
per-action plugin discovery, implementation ranking, dependency-fingerprint
manifests or adapter-injected lowerers. Missing/duplicate dispatch and unowned
implementations fail registry validation. Public exports and method admission
remain owned by their existing capability/operator registries.

A builder constructs expressions only; it cannot execute, connect, pick a new
backend, inspect runtime statistics or rewrite generated SQL. A backend-specific
builder stays in the inherited domain, implements the same method and passes
independent semantic conformance tests. No public raw SQL or arbitrary callable
can enter a builder or kernel input.

Ibis compilation determines ordinary expression support in the already fixed
domain. Successful compilation is necessary but does not prove server acceptance,
correct rows, streaming behavior or Arrow type agreement. Adapters claim only
capabilities and engine versions established by real integration tests.

The small adapter boundary contract covers exact source/Artifact binding,
required single-evaluation resources, bounded readers/writers and cancellation.
It has no federation routes, materialized-import alternatives or duplicate
ordinary-Ibis-operation support matrix. Numerical methods are never moved to
another implementation because one adapter fails compilation.

## Execution Recipe

`PhysicalStageGraphV1` is retained only as a private in-process execution recipe:

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
  | PythonKernelStepV1
      kernel_id_and_version
      guarded_input_handles[]
      exact_input_contracts[]
      output_contract
      runtime_budget
  | ValidationStepV1
      owning_check_id
      input_handles[]
      required_resources[]
```

Step handles, expressions, kernels and temporary aliases are process-private.
The recipe contains no physical-plan fingerprint or recovery serialization.
Stable topological ordering provides reproducible diagnostics; Runtime records
normal Run/Artifact provenance rather than compiler state. Validation steps
invoke owner-defined engine checks or bounded checks over admitted outputs;
they cannot conceal unbounded local computation.

The final writer is Runtime-owned. `BoundMaterializationOutputV1` binds the
configured target's writer to exact primary/retained producer handles and their
contracts. It is neither a sink selection result nor a storage receipt.

Every resource-producing step declares process-lifetime, connection-lifetime,
or recoverable-cancel behavior. Work that can outlive its owner requires a
reserved recoverable locator and a proven cancellation/fencing path. These
requirements belong to execution safety and remain even though the recipe is
not persisted or resumed. Runtime owns reservations and terminal recovery.

## Arrow and Local Resource Boundaries

### Exact exchange schema

`PhysicalExchangeV1` names only an explicit kernel input/output boundary, with
its producer/consumer, ordered Arrow-compatible schema, identity policy and
Runtime-owned guards. It is not a transport-mode or graph-placement union.
Engine relations in the same domain compose directly; Artifact reads use their
fixed reader; final persistence uses the selected writer's stream.

Adapters validate field order, type, nullability, decimal precision/scale,
timezone, dictionary normalization and nested-value rules. Any conversion must
be lossless under the row contract; unsupported or overflowing values fail.
Neither an Ibis type annotation nor an Arrow reader's declared schema proves
that actual batches match. Fetch size and individual variable-width allocations
must be bounded before an unbounded allocation could occur.

The implementation evidence must cover integer `SUM` widening: the reviewed
Ibis 12.0.0 / DuckDB 1.5.3 combination produced a declared `int64` Arrow reader
with actual `decimal128(38, 0)` batches. This is an adapter conformance fixture,
not permission to cast lossy values or widen a public row contract after work.

### Python inputs are complete before invocation

Runtime receives all declared inputs under per-input and combined row/byte
limits, validates exact ordering and schema, and only then invokes the kernel.
Input overflow means the kernel never starts. Output overflow discards its
result. The bound method must account for algorithm-specific dimensions such
as series count, history length and Metric-pair count, not only input bytes.

The complete-input Arrow boundary is the default. A kernel that needs repeated
reads can reuse its admitted Arrow Table. There is no compiler-selected Parquet
exchange route. Run staging and any spill remain Runtime-owned implementation
resources under the same appropriate budgets, never a way to admit oversized
kernel inputs.

Runtime owns a terminable worker/deadline mechanism where the method requires
hard cancellation. A timeout check after a blocking numerical call returns is
not hard deadline enforcement. Decoded input bytes are not a claim about peak
Python RSS or intermediate algorithm allocations.

### DuckDB relations use engine resource budgets

DuckDB reads local/direct-reader Artifacts and declared kernel outputs. Runtime
sets its memory, temporary-disk and time budgets and guards final output writes.
A scan of existing local Parquet does not inherit a Python kernel's complete-input
row cap. DuckDB spill is a private engine resource, not an Artifact or a compiler
exchange decision; resource overflow fails the action.

Streaming engine work may consume validated batches and create private partial
state before a later overflow is known. That state is discarded on failure and
never published. Only Python complete-input invocation requires validation of
all inputs before calculation begins.

### Storage streams are independent

A selected storage writer may stream a large result under batch-memory and
stored-byte limits without assembling a complete Table or DataFrame. It does
not authorize local joins, sorts, kernels or input relocation. The same rule
applies to required retained parts; all published payloads must fit the selected
target and its aggregate storage policy.

## Failure and Disclosure

The bounded compiler failure categories are:

| Phase | Meaning |
| --- | --- |
| graph_validation | Invalid graph or incompatible analytical contract |
| implementation_registration | Missing or inconsistent fixed method implementation |
| execution_boundary | Input domains conflict or a required reader/fence is absent |
| source_binding | Invalid declared source or captured parameter binding |
| ibis_expression_construction | The fixed builder cannot express the admitted meaning |
| ibis_backend_compile | Unsupported Ibis operation or rejected complete expression |
| storage_selection | Runtime's configured target has no valid exact write path |
| stage_execution | An admitted engine query or kernel fails during data work |
| transfer_guard | A declared kernel exchange or writer stream exceeds its bound |
| output_validation | Realized rows/types/keys violate the output contract |

Runtime owns the full lifecycle failure vocabulary, publication rollback and
process-loss recovery. Unsupported Ibis operations and unexpected compile
rejection remain distinct structured kinds in the compile phase. Neither
causes another route, method, source, sink or partial result to be selected.

A failure identifies the exact public operator/input role, expected contract,
received safe facts and a reachable repair. Domain mismatch errors use safe
Marivo domain labels; they do not expose connection strings, storage locators,
SQL or raw identities. Repairs never advertise an unimplemented relocation or
assume materialization changes a domain.

`contract()` renders only owned semantic requirements and fixed-method boundary
constraints derivable without live work. It cannot promise successful backend
compilation, present a candidate engine list or issue a query. Static Help owns
method semantics; Run failures own concrete repair; Artifact cards own outcomes.
Optional diagnostics may summarize implementation versions, fixed domains,
query counts, transfers and elapsed time. They are bounded and redacted, not
persisted plan/fingerprint inventories or publication gates.

## Cross-Module Seams

- Dataset Core supplies exact roots, definition identity, row contracts,
  ownership and action requirements. No new public execution state is added.
- Observation supplies Population/coordinate spines, exact predicate phases,
  contribution bindings, sampling and fanout proofs, and retained fold state.
- Modules 5/6 supply exact invocation and method contracts, fixed implementation
  categories, reference semantics, identity rules and family validation.
- Runtime supplies admitted bindings, execution budgets and one configured
  storage target. It owns writer validation, invocation, resource journals,
  receipts, quality/Evidence/Findings and atomic publication. There is no
  prepared-plan/candidate-selection callback.
- Public Cutover removes eager duplicate paths and accepts only the fixed method
  and backend combinations proven by terminal Runtime evidence.

## Rejected Alternatives

- A public execution planner, arbitrary SQL/Ibis/pandas input, generic UDF or
  numerical callable would bypass governed semantics.
- Local-first collection after every observation would move high-cardinality
  Entity and Event/Lifecycle data out of its execution engine.
- Requiring every statistical algorithm to become SQL would duplicate numerical
  implementations and expand backend-specific parity work without a current need.
- Runtime engine/local selection, maximal partial-SQL search, automatic imports
  and federation routing would restore the generalized placement system.
- General CSE, separately fingerprinted compiler graphs and occurrence interning
  would add identity machinery beyond Dataset Core's actual authority needs.
- Treating one SQL statement as universal acceptance would conflate expression
  composition with necessary fences, validation and materialization writes.
- Arrow/Parquet type assertions without real batch checks would mistake declared
  types for realized transport correctness.
- Dropping cancellation, immutable leaf identity or atomic publication would
  weaken existing correctness rather than simplify implementation placement.

## Delivery and Acceptance

Implement in this order, preserving each public family's contract:

1. Single-domain observe/aggregate/compare/rank, explicit shared-spine semantics,
   a configured output writer and immutable Artifact scans.
2. Local Artifact DuckDB rollup and one fixed Python forecast path, with exact
   Arrow, bounded complete input, cancellation and failure cleanup.
3. High-cardinality correlation, discovery and Event/Lifecycle methods, each
   admitted on named tested engines. No general router is a prerequisite.

Required evidence is narrow to these actual boundaries:

1. Zero datasource calls and Runs during logical construction; only binding-miss
   `execute()` compiles. Materialized reads never compile origin graphs.
2. A same-domain filter/join/aggregate/window/rank chain executes correctly as
   one ordinary engine query, with fence/validation/write statements counted
   separately when required.
3. Population null retention, unsafe fanout rejection, filter/aggregation order,
   shared sampling realization, temporal scope and retained-state rollup pass
   adversarial row-level tests.
4. Explicitly shared nodes retain semantic sharing without a general CSE pass;
   SQL object reuse is not accepted as single-evaluation evidence.
5. Two equal-argument independent DuckDB connections with conflicting same-named
   tables fail Marivo domain validation before executing a mixed expression.
6. Local and engine Artifact scans use their fixed reader and never replay an
   origin, relocate an input or select a federation route. Same-domain mixed
   logical/materialized inputs succeed; different-domain inputs fail with a
   reachable repair or an explicit unsupported reason.
7. Every method resolves exactly one category and implementation for its fixed
   context. Unsupported engine compilation never invokes DuckDB or a kernel.
8. Forecast receives a complete governed series, invokes its Python kernel once,
   and publishes only the root output. A local relational continuation reads its
   declared output without uploading it to the source engine.
9. Arrow actual-batch tests cover integer widening, overflow, decimal, timezone,
   null and variable-width guards. Declared schema alone cannot pass acceptance.
10. Python input overflow prevents invocation; output/deadline overflow publishes
    nothing. Tests use the bound Runtime budget at/above each limit. Algorithm
    intermediate memory and worker cancellation have separate evidence.
11. Local Artifact scans larger than the Python input cap can succeed within
    DuckDB/disk/output budgets. Late streaming failure discards private state.
12. One configured storage target is validated once; no target failure changes
    computation placement, selects a second target or creates partial authority.
13. Process-loss tests prove work terminates/is fenced and resources remain
    exactly journaled; only committed Artifact metadata is recovery authority.
14. Basic relation coverage and complex-method support are reported separately
    per tested backend. No six-backend all-method parity claim is inferred.
15. At least one real-agent journey for each initial path executes against an
    admitted real backend and reads terminal Run/Artifact/Evidence outcomes.

Compilation-only snapshots, mocked health and old eager tests are insufficient
acceptance for the new execution path.

## Owner-Confirmed Decisions

On 2026-09-05 the owner accepted this fixed-boundary replacement following the
review of the earlier design. The current decisions are:

1. Keep lazy Dataset semantics, explicit execution and immutable Artifact reuse.
2. Keep Ibis as the only relational expression language and inherit input domains.
3. Use DuckDB for local/direct-reader data and declared kernel output, not fallback.
4. Fix Python usage by exact numerical method, not backend availability.
5. Reject cross-domain relation composition instead of routing/importing inputs.
6. Preserve semantic barriers and required single realization without general CSE.
7. Give Runtime one configured storage target and executor-specific resource policy.
8. Preserve exact Arrow validation, safe identities, cancellation and atomic output.
9. Deliver basic relations, local reuse/forecast, then individually proven complex
   methods. No public compatibility or alternate placement mode is retained.

## Final Boundary

Marivo determines the exact meaning and legal inputs of a computation. The
compiler expresses that meaning in the input's engine or invokes the method's
fixed Python kernel. Runtime executes the determined recipe and commits its
complete result. No component searches for another execution location to rescue
an unsupported calculation.
