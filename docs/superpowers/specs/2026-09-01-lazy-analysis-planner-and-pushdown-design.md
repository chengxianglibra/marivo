# Lazy Analysis Ibis Compiler and Execution Boundaries Design

Date: 2026-09-01

Revised: 2026-09-04

Status: accepted

## Outcome

Define the private compilation contract that turns one admitted lazy `Dataset`
definition into one deterministic executable stage graph or one bounded
structured compilation failure.

A reviewer can determine, without consulting public operator or persistence
implementation details:

- which private graph preserves the analytical meaning of a Dataset chain;
- how that graph compiles directly to one lazy Ibis expression per execution
  domain without a second relational optimizer;
- how Population membership, coordinates, Metric branches, and Relationship
  proofs lower to Ibis table/value operations;
- which support questions are answered by deterministic Ibis compilation and
  which execution-boundary capabilities still require declarations;
- how new semantic nodes add one exact semantic lowering route—an Ibis lowerer
  or a registered local implementation—without creating optimizer phases or
  SQL rewrite rules;
- how one action may share a common subexpression without creating durable
  reuse authority;
- how one registered partial-SQL graph retains one maximal Ibis stage per
  upstream execution domain, crosses Arrow or Run-staged Parquet, and executes
  its exact DuckDB or Python local tail;
- where one physical stage executes and which exchanges are legal;
- when cross-datasource execution is federated, materialized, bounded-local, or
  rejected;
- how capability absence, lowering failure, compile rejection, execution
  failure, and publication failure remain distinct;
- which planning facts may be projected safely into `contract()`, errors, and
  Run audit without exposing a public plan, Ibis expression, SQL string, task,
  or future.

This document is the Module 3 authority named by
[`2026-09-01-lazy-analysis-design-decomposition-plan.md`](2026-09-01-lazy-analysis-design-decomposition-plan.md).
It consumes the Dataset value contract in
[`2026-09-01-lazy-analysis-dataset-core-design.md`](2026-09-01-lazy-analysis-dataset-core-design.md)
and the accepted Observation Model contract in
[`2026-09-01-lazy-analysis-observation-model-design.md`](2026-09-01-lazy-analysis-observation-model-design.md).
Those upstream contracts are frozen; this design cannot redefine their
Population, filter, or coordinate seams.

## Ownership Boundary

This document owns:

- the private semantic Dataset graph protocol;
- normalization from public Dataset construction into private semantic nodes;
- action-scoped canonicalization and common-subexpression elimination;
- direct lowering from semantic nodes to lazy Ibis table/value expressions;
- Population-preserving spine construction;
- Relationship traversal and consumption of fanout proofs;
- predicate, projection, join, semi-join, aggregation, sampling, window, rank,
  lag, and registered statistical-reduction lowering;
- the narrow datasource execution-boundary capability vocabulary;
- the private semantic-to-Ibis compiler and shared lowerer conformance
  contract;
- semantic fences and exact backend-specific Ibis lowerings when ordinary Ibis
  operations cannot preserve the contract;
- physical stage partitioning, execution-domain placement, and exchange rules;
- physical feasibility of runtime-ranked materialization sink candidates and
  the final write path bound to the runtime-selected candidate;
- materialized scan-leaf placement and non-rewriteability;
- cross-datasource federation admission;
- registered bounded-local stages and their hard transfer guards;
- the canonical Arrow exchange contract, bounded Arrow streaming, and
  Run-scoped Parquet exchange placement;
- the closed local-implementation registry with DuckDB relational and
  operator-specific Python-kernel variants;
- the fixed private first-cutover bounded-local execution policy;
- safe planning projections for `contract()`, structured errors, and Run audit;
- the guarantee that an unsupported engine operation never becomes an
  undocumented pandas or process-local fallback.

It does not own:

- public Dataset classes, actions, state, row contracts, or field selectors;
- Population inference, predicate syntax, filter effects, coordinate meaning,
  Metric aggregation semantics, or semantic admission;
- operator-product admission, statistical method meaning, output families, or
  approximation promises;
- SubjectSet, Event, or Lifecycle public semantics;
- creation, ordering, commit, rollback, recovery, or reuse of Runs, Artifacts,
  Evidence, Findings, quality authority, or storage receipts;
- materialization sink configuration, candidate ranking, final candidate
  selection, receipt creation, or storage publication;
- datasource credential resolution or connection lifecycle;
- public Session configuration signatures or configurable local-policy APIs;
- a public `plan`, `explain`, SQL, Ibis, task, future, or execution-handle API.

It also does not let datasource adapters or operator packages redefine
Population, aggregation, filter, identity, approximation, or privacy meaning
through an Ibis lowerer or local implementation. Those owners supply exact
facts and contracts; the compiler only expresses that admitted meaning through
the selected Ibis or registered-local route.

Semantic owners decide whether a requested Dataset exists and what its rows
mean. This module decides only whether that exact meaning can be executed safely
and where.

## Upstream Invariants

The compiler accepts these Dataset Core decisions as fixed:

1. every public analysis operator accepts its registered Logical or Materialized
   Dataset inputs, returns a new Logical Dataset, and performs no datasource
   work;
2. each Dataset has a complete logical row contract before execution;
3. every Dataset is owned by one exact Session;
4. the private input authority token distinguishes current logical execution
   from immutable materialized rows;
5. a materialized Dataset is a non-rewriteable scan leaf;
6. definition fingerprints identify normalized analytical definitions, not
   physical plans or realized rows;
7. only Logical Datasets enter graph compilation through `execute()`;
   materialized-only `show()` and `to_pandas()` read the immutable backing and
   never compile or replay the origin graph;
8. Python object reuse does not create cross-action execution reuse;
9. unordered agent-visible output must acquire canonical deterministic order or
   fail;
10. no public plan, expression, SQL, task, future, or receipt value is added.

The compiler consumes these current Observation Model decisions without
redefining them:

1. one Population owns one exact Entity and ordered identity signature;
2. Population membership is evaluated before Population sampling;
3. all Metric branches use one shared Population spine;
4. Metric-local missingness cannot remove another Metric value or a Population
   member;
5. the coordinate spine is independent of Metric non-nullness;
6. `with_dimensions(...)` and `with_time_axis(...)` retain Entity while
   `aggregate()` removes Entity exactly once;
7. logical aggregation recomputes governed Metric semantics at the requested
   coordinates;
8. materialized aggregation uses only an admitted retained-value fold;
9. authored filter position and family effect are semantic;
10. each bound predicate carries the exact retained-row or reachable-semantic
    field resolution and the selected `semantic_current` or
    `materialized_only` authority requirement;
11. a predicate may cross sampling, aggregation, materialization,
    population-input projection, subject selection, Event matching, Lifecycle
    replay, component composition, or cumulative evaluation only with an exact
    equivalence proof;
12. a logical admitted `PopulationInput` is consumed as direct membership or an
    exact identity projection followed by a semi-join, while a materialized
    admitted input is consumed as an immutable retained-identity leaf;
13. raw Entity identities never enter metadata, default diagnostics, Evidence,
    or cards.

If a later Observation Model review changes one of these contracts, that owner
must change first and this document must update only the consumed seam.

## Decision Summary

### Keep Marivo semantics, Ibis expressions, and execution boundaries separate

One action uses three different private values:

```text
SemanticDatasetGraphV1
        |
        v
BoundIbisExpressionV1[]
        |
        v
PhysicalStageGraphV1
```

The semantic graph is the private authority for authored analytical meaning.
Each `BoundIbisExpressionV1` is an ephemeral lazy Ibis expression attached to
one exact execution domain. The physical graph is one trivial engine stage for
the common single-engine case and adds stages only for explicit materialized
scans, federation bindings, guarded bounded-local work, validation outputs, or
materialization writes.

There is no separate Marivo relational optimizer or optimizer IR in the first
cutover. For a Dataset whose sources are all addressable by one Ibis backend,
the whole semantic graph compiles to one Ibis table expression and one engine
stage. Ibis performs its backend-specific expression rewrites and SQL
generation; the datasource performs SQL-plan optimization.

Private fingerprints remain independent:

```text
definition_fingerprint
  binds public analytical definition and input authority tokens

semantic_plan_fingerprint
  binds the action-normalized private semantic graph

compiler_projection_fingerprint
  binds the semantic compiler version, exact lowerer versions, execution-domain
  binding, and output contract without serializing Ibis internals

physical_plan_fingerprint
  binds explicit stage boundaries, resolved boundary capabilities, fixed local
  execution policy when applicable, and the selected materialization sink
  candidate and disposition for execute actions
```

Only `definition_fingerprint` is a public Dataset identity. Generated Ibis
operations, SQLGlot trees, SQL text, aliases, and backend query plans are
products, not Marivo identities or recovery authority.

### Keep authored occurrences and canonical nodes separately

The semantic graph is a content-addressed DAG plus an authored occurrence map.
Canonical nodes enable deterministic interning and action-local sharing.
Occurrence paths preserve the exact public chain position needed for safe
errors and lineage.

Two authored occurrences may point to one canonical node only when their full
semantic payloads, ordered inputs, authority tokens, action-time requirements,
and contract versions are equal. Display labels, Python object identity, equal
Ibis expressions, or similar generated SQL do not establish equality.

Canonical interning never erases the authored occurrence that owns a failure.

### Use Ibis as the only relational expression language

Marivo compiles semantic nodes directly into lazy Ibis expressions. The
semantic graph and its exact row contracts remain authoritative for:

- Entity membership and sampling order;
- Metric aggregation and component order;
- Relationship fanout safety;
- coordinate domains and null retention;
- predicate phase and non-commuting barriers;
- approximation promises;
- row identity, uniqueness, and canonical presentation order.

The compiler expresses these decisions with ordinary Ibis table/value
operations wherever possible. It may use a narrowly registered backend-specific
Ibis operation only when the semantic owner supplies the same exact contract
and parity tests. It cannot accept public SQL, reinterpret a semantic node, or
make an Ibis operation tree part of the public Dataset.

For one execution domain, "pushdown" is not a separate planning decision: if
the compiler can construct and compile the complete Ibis expression, the
resulting SQL is submitted as one engine stage. Filter/project fusion, SQL
subquery shape, CTE inlining, join algorithms, and access paths are left to
Ibis, SQLGlot, and the datasource.

| Concern | Owner |
| --- | --- |
| Dataset meaning, Population, grain, barriers, exactness | Marivo semantic graph |
| construction of the complete lazy relational expression | Marivo Ibis compiler |
| backend-specific expression rewrites and dialect SQL generation | Ibis and SQLGlot |
| SQL access paths, join algorithms, decorrelation, and runtime query plan | datasource optimizer |
| cross-backend, materialized, volatile-fence, local, and write boundaries | Marivo boundary resolver/runtime seam |

### Compile operation support; declare only execution-boundary capabilities

Marivo does not maintain a second operation-support matrix that attempts to
mirror Ibis backend support. After the incomplete Run exists, the selected Ibis
backend deterministically compiles the complete bound expression:

```text
ibis_expression_compiled
ibis_operation_unsupported
ibis_expression_compile_rejected
```

A successful compile admits the expression shape for that exact Ibis/backend
build. An unsupported Ibis operation or backend compiler rejection fails before
a data statement and never triggers a different semantic method or hidden local
fallback.

Static datasource profiles remain only for facts Ibis compilation cannot decide
safely: whether the resolved engine version is inside the adapter's tested Ibis
conformance range, whether several source refs are addressable by one federation backend,
whether materialized rows can be scanned or imported, whether an engine
temporary relation provides required single-evaluation semantics, whether a
server-side write/export path exists, and whether cancellation, snapshot, or
transfer guards are enforceable. Marivo never probes capability by executing
sample data work.

### Resolve execution boundaries before building bound expressions

The boundary resolver is a fixed deterministic decision table, not an
optimizer:

1. if every logical source is addressable by one backend, bind all source tables
   to that backend and compile one Ibis expression;
2. if a declared federation backend can address every source, rebind all source
   tables through that one backend and compile one Ibis expression;
3. otherwise consume only already-admitted materialized leaves or explicitly
   registered guarded bounded-local boundaries;
4. if no exact route exists, fail with a reachable repair.

Ibis 12 rejects an expression that depends on more than one bound backend, so
separate Ibis connections do not constitute federation. The resolver never
searches for partially pushed subgraphs, ranks alternative query plans, or
moves a semantic operation merely because one placement looks cheaper.

### Treat local execution as an explicit registered placement, not fallback

A local stage is legal only when all are true:

1. the operator lowering contract explicitly registers an equivalent local
   implementation;
2. the Dataset contract discloses that bounded-local placement may be required;
3. the local implementation consumes an exact schema;
4. every inbound exchange has an enforced per-input row hard limit, all inbound
   exchanges share one enforced total-byte hard limit, and the local stage has
   enforced output row and byte hard limits;
5. exceeding any input, total-transfer, output, or timeout limit aborts the
   entire action rather than truncating, sampling, spilling into an
   undocumented path, or returning partial rows;
6. identity, null, timezone, decimal, collation, and ordering semantics remain
   equivalent;
7. the selected placement and realized transfer counts are recorded in the
   action audit projection.

The compiler never treats `to_pandas()` or arbitrary Python code as a local
execution implementation. A registered local stage remains private and cannot
return a public intermediate.

The first cutover has exactly two local implementation kinds:

```text
LocalImplementationV1 =
    DuckDBRelationalImplementationV1
  | PythonKernelImplementationV1
```

`DuckDBRelationalImplementationV1` compiles an admitted relational remainder
through the compiler-owned Ibis-to-DuckDB path over Arrow or Parquet inputs. It
does not accept arbitrary SQL or Python UDF fallback. It is the default local
placement when the semantic graph remains relational but the source execution
domain cannot own the registered operation.

`PythonKernelImplementationV1` names one immutable operator-owned kernel id and
version. It receives and returns exact Arrow-compatible schemas and may use
NumPy, SciPy, statsmodels, or pandas internally only as declared implementation
dependencies. A pandas DataFrame never crosses the stage boundary or becomes a
Dataset input. Polars is not a first-cutover implementation kind; adding it
requires an explicit registry variant and differential semantic conformance,
not a generic fallback switch.

The placement decision is made during deterministic boundary resolution from
the registered implementation and conservative backend profile. An unexpected
Ibis compile rejection or engine execution failure does not cause the compiler
to split the graph and retry locally. The cut occurs only at a semantic node
whose exact local implementation was already admitted before data work; the
largest upstream closure assigned to one datasource remains one Ibis expression
and one engine query stage.

Every registration is closed and versioned:

```text
LocalImplementationRegistrationV1
  implementation_id
  implementation_version
  semantic_node_kind
  kind = duckdb_relational | python_kernel
  selection = only_when_no_exact_engine_lowering
  exact_input_contracts[]
  exact_output_contract
  admitted_exchange_modes[]
  determinism_contract_id
  null_time_decimal_ordering_contract_id
  dependency_fingerprint
  operator_limit_overrides
```

The registry contains implementation facts only. Operator product admission,
row meaning, approximation, and numerical reference semantics remain owned by
the semantic operator module. When the lowerer manifest contains an exact
implementation for the selected datasource domain, that engine placement wins.
The local registration is selected only when the same frozen manifest already
proves no exact engine lowering and the local boundary is admitted; a later
compile or execution exception is not that proof.

The compiler owns one private first-cutover policy:

```text
LocalExecutionPolicyV1
  schema = "marivo.local_execution_policy/v1"
  max_rows_per_input = 100_000
  max_total_input_bytes = 67_108_864
  max_output_rows = 100_000
  max_output_bytes = 67_108_864
  timeout_seconds = 60
```

These values are fixed contract constants, not public Session knobs. The
Session runtime supplies this exact value and the compiler binds its fingerprint
into the physical plan and Run audit projection. The input-row limit applies to
each local inbound exchange; the total input-byte limit applies to all inbound
exchanges combined. Output rows and bytes are guarded independently so a local
join or expansion cannot turn bounded inputs into an unbounded result. The
timeout covers transfer plus local calculation. An operator may register
stricter limits, never wider ones.

This policy is independent of `show()` preview bounds, `to_pandas()` collection
limits, datasource authoring scopes, and materialization storage bounds. A
future configurable local policy requires a Module 3 amendment and a separately
owned Session public-surface design. The first cutover therefore never repairs
a local bound failure by asking the caller to change Session policy.

### Do not create hidden durable intermediate Datasets

Stage formation may consume an already committed materialized Dataset leaf.
It may not automatically materialize an internal subgraph to make placement
work.

If a cross-datasource graph requires a durable boundary and no admitted leaf is
already present, `.execute()` is a repair only when all are true:

1. the boundary is an existing public upstream Dataset input occurrence, not a
   private source, Population, coordinate, or Metric branch;
2. the downstream public operator already accepts that Dataset family as an
   input after materialization;
3. materializing the upstream Dataset is independently placeable without the
   same missing cross-datasource capability;
4. the resulting storage receipt is readable by the downstream domain through
   a declared scan/import capability or an admitted bounded exchange.

The error names only boundaries satisfying all four rules. A multi-source
`session.observe(...)` graph does not expose its internal Metric branches as
Dataset inputs, and `observe` rejects Datasets as Metric inputs. Such a graph
therefore receives no `.execute()` repair: it requires admitted federation,
one registered bounded-local implementation, a semantic-source redesign, or a
split into independent analyses that are not claimed to reconstruct the same
multi-Metric Dataset.

These rules keep durable reuse, Artifact identity, and publication under the
explicit runtime contract rather than making stage formation create
hidden authority or advertise an unusable repair.

Ephemeral bounded exchanges inside one action are not Artifacts, cannot be
recovered, and cannot be reused by another action.

### Expose requirements and outcomes, not plans

There is no public `dataset.explain()`, `dataset.plan`, `dataset.sql`, or
`session.plan(...)` path.

- `dataset.contract()` may project current sources, required boundary capability ids,
  potential execution-domain count, materialization barriers, bounded-local
  eligibility, and blockers known without execution.
- a compilation error identifies one safe occurrence, missing capability or
  failed proof, expected state, received state, and mechanically valid repair.
- a completed or failed Run may project plan fingerprints, stage count,
  placement classes, lowering dispositions, capability-profile fingerprints,
  CSE counts, and bounded transfer statistics.

None of these projections includes raw identities, secret-like literals,
credentials, complete private graphs, Ibis expressions, or generated SQL.

## Compilation Lifecycle

### Dataset construction normalization

Each public Dataset operator performs deterministic local normalization at
construction time:

1. accept the exact upstream private root handles;
2. normalize semantic refs, typed policy values, predicate trees, and literals;
3. attach the owning family, row contract, action-time requirements, and
   authority tokens;
4. create one authored semantic occurrence;
5. content-address its exact semantic node;
6. derive the Dataset definition fingerprint and bounded lineage;
7. return the new immutable Dataset.

This work does not select a backend, connect to a datasource, lower to Ibis,
estimate cardinality, create a Run, or decide physical placement.

For a Session-owned source backed by parameterized JSON, construction also
attaches the Observation-owned `BoundSourceParametersV1` value. Its exact typed
values remain private and process-local; its digest participates in semantic
node and Dataset definition identity. No later compiler phase may replace it
from current Session state.

### Action compilation

When an action executes, the compiler coordinator receives:

```text
ActionCompilationRequestV1
  action_kind
  dataset_root_handle
  captured_source_parameter_handles[]
  exact_output_row_contract
  presentation_order_requirement
  local_execution_policy: LocalExecutionPolicyV1
  available_execution_domains[]
  execution_boundary_profiles[]: ExecutionBoundaryProfileV1
  materialized_leaf_scan_admissions[]
  materialization_sink_candidates:
    NoMaterializationSinkCandidatesV1 | MaterializationSinkCandidatesV1
```

Inspection and collection use `NoMaterializationSinkCandidatesV1`.
Materialization uses the exact ordered `MaterializationSinkCandidateV1` values
owned by
[`2026-09-01-lazy-analysis-materialization-runtime-design.md`](2026-09-01-lazy-analysis-materialization-runtime-design.md).
The compiler may consume candidate capabilities, guards, domains, and receipt
protocols, but it must not choose from `runtime_policy_rank`.

Pure graph closure and exact-schema validation may occur before Run admission.
The runtime must admit and persist the incomplete Run before any live profile
resolution, engine compilation, or datasource data statement. It then passes
the exact resolved or conservative boundary profiles in this request; the
compiler never opens a connection to fill them itself.

The private compilation handshake then performs:

1. graph closure and exact-schema validation;
2. action-local semantic canonicalization and CSE;
3. fixed execution-domain and federation-boundary resolution;
4. binding of every logical or materialized source to one exact Ibis backend
   per engine stage, using only source parameters captured by that logical
   source definition;
5. direct semantic-to-Ibis lowering in stable topological order;
6. validation of required single-evaluation fences and explicit bounded-local
   boundaries;
7. materialization-sink feasibility derivation when candidates are present;
8. runtime selection of one admitted sink candidate at the private phase
   boundary;
9. binding of the selected sink's write path when applicable;
10. mechanical physical-stage formation from those explicit boundaries;
11. deterministic Ibis/backend compile validation for every engine stage;
12. output-schema, ordering, uniqueness, authority, and action-bound checks;
13. emission of one immutable executable stage graph.

No step mutates the input Dataset or its public definition fingerprint.

`captured_source_parameter_handles[]` are opaque process-local handles to the
exact typed `BoundSourceParametersV1` values reachable from the graph. They are
not Run arguments, serializable plan fields, reusable Session defaults, or
credential carriers. The source adapter validates deterministic encoding into
the declared query/body positions before its first data statement. Errors may
name the Entity and parameter contract but never the raw value or encoded
request.

A binding hit is resolved by Materialization Runtime before this compilation
request and therefore sends no source request. On a binding miss, missing,
foreign, mutated, or digest-mismatched captured parameters fail before a data
statement. Compiler code never calls `Session.source_bindings(...)`, reads a
`ContextVar`, or reconstructs values from a redacted persisted projection.

### Materialization sink feasibility phase

Materialization compilation pauses once after source binding and pure Ibis
expression construction, before backend compilation. The compiler returns one
result per runtime-owned candidate:

```text
MaterializationSinkFeasibilityV1
  candidate_id
  disposition = direct_write | engine_managed_export |
                guarded_bounded_transfer | unsupported
  required_boundary_capability_ids[]
  transfer_guards
  blocker
```

The result order matches the runtime input order, but feasibility is independent
of rank. `blocker` is absent for an admitted disposition and otherwise contains
one bounded compiler-owned reason. The feasibility result contains no receipt,
storage credential, generated SQL, mutable relation handle, or executed probe.

The runtime selects the lowest-ranked admitted candidate under the Module 4
policy and returns only its `candidate_id`. The compiler validates that the id
belongs to the prepared request and was admitted, then continues physical
formation and compilation against that exact sink.

If no candidate is admitted, Module 4 records one `storage_selection` failure
using the bounded feasibility reasons. If final compilation or execution fails,
the action fails; neither compiler nor runtime switches to another candidate
inside the same Run.

This is a private two-phase compilation call, not a public plan or execution
handle. The prepared semantic graph plus bound Ibis expression is immutable,
process-local, scoped to the admitted Run, and unusable after selection or
failure.

### Execution handoff

The successful private result is conceptually:

```text
ExecutableActionPlanV1
  semantic_plan_fingerprint
  compiler_projection_fingerprint
  physical_stage_graph: PhysicalStageGraphV1
  materialization_output:
    NoMaterializationOutputV1 | BoundMaterializationOutputV1
  safe_audit_projection
```

The physical graph's `primary_output` produces the rows required by the action.
Its validation outputs may check identity uniqueness, schema, exact bounds, or
other action-time requirements. They do not publish Evidence or quality
authority; the runtime module decides how successful validation facts
participate in publication.

`BoundMaterializationOutputV1` contains the selected candidate id, sink kind,
feasibility disposition, exact producer stage/output ids, receipt protocol id,
and transfer guards. It authorizes only the physical write path. Module 4 still
owns receipt construction, immutable finalization, and publication.

## Private Semantic Dataset Graph

### Semantic graph envelope

The graph uses a closed versioned envelope:

```text
SemanticDatasetGraphV1
  schema = "marivo.semantic_dataset_graph/v1"
  roots[]
  nodes[]
  occurrences[]
  requirements[]
  canonicalization_proofs[]

SemanticNodeRecordV1
  node_id
  node: SemanticNodeV1

SemanticOccurrenceV1
  occurrence_path
  node_id
  public_operator_id
  ordered_child_paths[]
```

`SemanticNodeV1` is the closed discriminated union defined below. It is not a
record with a generic `payload` field. Every variant carries `kind`, `version`,
`ordered_input_ids`, `authority_tokens`, and `row_contract_id` plus only its
listed variant fields. Unknown fields, unknown versions, unknown node kinds,
missing inputs, cycles, fingerprint collisions, or row-contract mismatches fail
closed.

The graph is process-private. A bounded descriptor may be encoded for Run audit
only through the runtime-owned exact schema. Logical Dataset recovery remains
forbidden.

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
  current_authority_requirement

MaterializedScanNodeV1
  kind = materialized_scan
  version = 1
  artifact_ref
  content_authority
  family_id
  family_contract_version
  realized_schema_fingerprint
  scan_admission_handle_id

PopulationSpineNodeV1
  kind = population_spine
  version = 1
  entity_ref
  identity_signature[]
  reference_scope: UnscopedReferenceV1 | ScopedReferenceV1
  target_population: RootPopulationV1 | DerivedTargetPopulationV1
  membership_uniqueness_requirement

MembershipFilterNodeV1
  kind = membership_filter
  version = 1
  bound_predicate
  field_resolution = retained_row | reachable_semantic
  authority_mode = semantic_current | materialized_only
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
  relationship_requirement_ids[]
  uniqueness_requirement

MetricEvaluationNodeV1
  kind = metric_evaluation
  version = 1
  metric_key
  metric_graph_fingerprint
  value_field_binding
  coordinate_aggregation_contract_id
  relationship_requirement_ids[]
  component_occurrence_ids[]
  nullable_value_requirement

RowFilterNodeV1
  kind = row_filter
  version = 1
  bound_predicate
  effect = membership | row_subset
  field_resolution = retained_row
  authority_mode = semantic_current | materialized_only
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
registered exact contract, not an arbitrary string namespace. The graph
decoder validates that every id resolves through the corresponding registry
version before fingerprinting.

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
Population/SubjectSet membership; `ordered_semantic_dependency_ids` carries
current semantic dependencies that are not Dataset operands. Thus different
horizons, thresholds, windows, matching policies, selectors, axes, or
completeness declarations necessarily produce different node identities and
lowerer inputs.

The union is complete for the accepted Module 5 and 6 matrices. Adding an
operator or a parameter that can change rows requires an explicit versioned
Module 3 amendment plus one exact semantic lowering route. Relational routes
require an Ibis lowerer; non-relational routes require one registered local
implementation. Runtime plugin registration cannot widen the union. No
invocation may contain arbitrary callables, SQL, Ibis expressions, pandas
objects, generic payload mappings, or optional-field mega-nodes.

### Semantic node identity

A semantic node id binds:

- node kind and version;
- exact ordered input node ids;
- logical or materialized input authority tokens;
- row-contract identity;
- normalized semantic refs and dependency fingerprints;
- normalized policies, literal payloads, and operator contract versions;
- exact action-time requirements that may change admission or rows.

It excludes:

- Python object addresses;
- presentation labels;
- datasource connection instances;
- execution-boundary profiles;
- Ibis expressions and generated SQL;
- physical stage ids;
- execution timing and realized row counts.

### Normalization rules

Construction and action normalization may:

- canonicalize exact typed literals;
- flatten and sort predicate children only where the predicate owner declares
  commutativity under SQL three-valued logic;
- remove exact duplicate predicate children;
- intern content-identical nodes;
- normalize ordered semantic refs to their owner-defined canonical order;
- remove representation-only wrappers whose owning contract declares identity;
- retain an authored-to-canonical occurrence map.

Normalization may not:

- reorder authored Dataset operators;
- commute a filter across a semantic barrier;
- change a membership filter into a row filter or the reverse;
- infer sampling, coordinates, Entity identity, or aggregation semantics;
- replace a logical authority token with a materialized token;
- merge nodes whose current-source and Artifact authority differ;
- use generated SQL equivalence as semantic equality;
- collapse nullable Metric branches into an inner join;
- remove a Population member because all Metric values are null.

## Ibis-first Semantic Compiler

### Do not introduce a second relational IR

The first cutover has no serialized `RelationalPlanV1`, rewrite scheduler,
candidate memo, maximal-pushdown search, SQL AST optimizer, or physical rewrite
registry. Those abstractions duplicate Ibis and the datasource without adding
Marivo semantic authority.

The only compiler envelope is:

```text
IbisSemanticCompilerV1
  schema = "marivo.ibis_semantic_compiler/v1"
  compiler_version
  semantic_graph_schema_version
  lowerer_manifest_fingerprint
  boundary_policy_version
  invariant_registry_version
```

The compiler accepts one validated `SemanticDatasetGraphV1`, one resolved
execution-domain binding, and one exact action output contract. It returns
ephemeral process-local Ibis expressions and bound local-stage records plus
bounded compiler evidence. Neither Ibis operation nodes nor local
implementation objects are serialized as Marivo state.

### Lower every semantic node exactly once

The compiler visits the canonical semantic DAG in stable topological order.
Each closed `SemanticNodeV1` variant has exactly one owning semantic lowering
registration:

```text
SemanticLoweringRegistrationV1
  semantic_node_kind
  lowering_contract_version
  owner_module_id
  required_contract_ids[]
  output_contract_validator_id
  routes[]:
    IbisLoweringRouteV1 | LocalLoweringRouteV1

IbisLoweringRouteV1
  lowerer_id
  lowerer_version
  implementation_fingerprint
  backend_specific = false | true

LocalLoweringRouteV1
  local_implementation_id
  local_implementation_version
```

The in-process protocol is narrow:

```text
SemanticLoweringCoordinator
  lower(node, compiled_inputs, bound_sources, admitted_facts, selected_route)
    -> CompiledSemanticNodeV1

CompiledSemanticNodeV1 =
    CompiledIbisNodeV1
  | BoundLocalNodeV1

CompiledIbisNodeV1
  semantic_node_id
  execution_domain
  ibis_expression_handle
  exact_output_row_contract
  required_single_evaluation_fence
  applied_lowerer_id
  applied_lowerer_version

BoundLocalNodeV1
  semantic_node_id
  ordered_input_stage_roots[]
  local_implementation_id
  local_implementation_version
  exact_output_row_contract
  admitted_exchange_modes[]
  bound_guards
```

This is not a public plugin API. Ibis lowerers are pure expression builders;
local routes are pure bindings to an already registered implementation. Neither
connects, executes, inspects wall-clock time, queries statistics, mutates an
input, or chooses another execution domain. The compiler verifies every Ibis
expression or local input/output binding against the semantic node's exact
contract.

The lowerer manifest is immutable for one action and exhaustive over the closed
semantic union. Duplicate node kinds, missing lowering routes, unstable
implementation fingerprints, unowned backend-specific/local implementations,
or output-contract drift make the compiler build invalid. Adding a new DSL
operator requires its semantic node, one exact semantic lowering registration,
Help/error disclosure, and parity tests. Adding filter pushdown, query pushdown,
subquery merge, join reordering, or aggregate fusion does not require a Marivo
rule because those are not new DSL semantics.

### Express the full single-engine graph as one Ibis expression

For a graph bound to one engine, compiler construction is direct:

- scan nodes bind backend-owned Ibis tables or admitted materialized tables;
- projections use Ibis selection;
- predicates use Ibis boolean expressions at their authored semantic position;
- membership uses an identity-preserving semi-join expression;
- Relationship traversal uses joins only after the semantic owner supplies the
  exact path and fanout proof;
- aggregation builds governed accumulator expressions and then final values;
- windows, rank, lag, and cumulative logic use exact partition, order, frame,
  tie, and null contracts;
- canonical presentation ordering and `show(limit + 1)` remain final action
  expressions;
- materialization binds the final expression to the runtime-selected write path.

The compiler does not move predicates, synthesize preaggregation, or search for
a cheaper equivalent graph. It constructs the correct expression from the
semantic graph. Ibis may fuse adjacent selects, perform backend-specific
rewrites, and produce nested SQL or CTEs. The datasource may then optimize that
SQL. As long as the entire expression remains one engine stage, SQL nesting is
not lost pushdown.

### Bind every operator from its ordered input-authority vector

The same operator occurrence must compile from an all-logical,
all-materialized, or mixed ordered input-authority vector without changing its
analytical meaning. Authority binding precedes Ibis lowering:

1. a logical input contributes its admitted upstream semantic graph and current
   source-authority requirements;
2. a materialized input contributes exactly one `MaterializedScanNodeV1` and
   its immutable content authority;
3. the compiler resolves one execution domain for the resulting graph using
   the existing source, federation, scan/import, and bounded-boundary rules;
4. the operator lowerer consumes the bound input expressions and emits the one
   registered output contract.

There is no second "materialized implementation" of an operator and no
operator-local decision to recompute an Artifact's origin. The semantic graph
already records the authority distinction; the lowerer implements one exact
calculation over the rows supplied by those inputs.

For an arity-two operator, the four vectors are
`logical/logical`, `logical/materialized`, `materialized/logical`, and
`materialized/materialized`. Higher-arity operators use the same ordered vector
model rather than bespoke branch code. A variant must reject an unsupported
vector during deterministic admission; compilation failure may not retry the
same call with a different vector.

### Maximize engine fusion on both sides of a materialized cut

A materialization boundary cuts executable lineage, not Ibis fusion. When one
target domain can directly scan or engine-import every materialized leaf and
address every remaining live source, the compiler binds those leaves as Ibis
tables and lowers the complete downstream connected graph into one Ibis
expression and one engine query stage. Retained-field predicates, projections,
joins, aggregation, windows, ranking, and registered statistical operations
remain eligible for that same expression.

If no single domain can address the complete graph, the existing deterministic
boundary resolver may use only a declared federation route, engine-managed
temporary import, or registered hard-bounded exchange. It does not replay a
materialized origin to recover single-query execution, copy an Artifact into a
new durable Artifact, or collect rows locally without the registered guards.

This is reuse of an explicit input, not cost-based cache substitution. The
compiler never searches committed Artifacts for an equivalent logical
subgraph. Only an Artifact ref present in the Dataset's input-authority vector
creates a materialized scan leaf. Reconstructing an exact Logical Dataset and
recovering its Session-bound `execute()` output remains owned by Module 4.

### Preserve Population, Metric, and sampling invariants by construction

One Observation lowers to one Population identity/coordinate spine. Each Metric
branch is unique at the spine key before it is left-joined back, so Metric-local
nulls never remove a Population member or another Metric value. Component
Metrics retain governed intermediate accumulators until the final expression.
A final `DISTINCT` or `GROUP BY` never repairs missing fanout authority.

A sampled Population is semantically evaluated once. Reusing an Ibis expression
object alone does not prove one backend evaluation because compilation may
inline the expression at several consumers. A multi-consumer volatile node
therefore requires a backend-declared single-evaluation fence, such as an
admitted engine temporary relation or an equivalent backend construct. If the
backend cannot guarantee it, compilation fails before data work. The fence is
action-scoped, non-durable, and never becomes a Dataset or Artifact.

Action-local CSE reuses only equal canonical semantic node ids with equal
authority, snapshot, ordering, and volatility contracts. It creates no
cross-action cache or durable reuse authority.

### Keep semantic barriers; do not optimize around them

The initial non-commuting barriers remain:

```text
entity_sample
entity_reduction
materialized_scan
population_input_projection / subject_semijoin
event_match
lifecycle_replay
component_compose
cumulative_window
registered approximation boundary
privacy or identity projection boundary
```

Marivo emits the Ibis expression at the authored side of each barrier. When
ordinary SQL equivalence is insufficient to preserve evaluation order or
volatile semantics, the compiler requires a declared backend fence. Absence of
a fence or exact backend-specific Ibis lowering is rejection, not permission to
move the operation.

### Allow narrow backend-specific Ibis lowerings

Ibis operation support varies by backend. When ordinary portable Ibis cannot
express an admitted operation but one backend has an exact equivalent, the
backend adapter may own one narrow `backend_specific = true` lowerer. It must:

- consume the same semantic node and exact output contract;
- use an Ibis custom operation, registered UDF, or compiler extension rather
  than public raw SQL;
- declare the exact backend/version scope;
- preserve dtype, null, timezone, decimal, collation, ordering, identity, and
  approximation behavior;
- pass differential tests against the owner-defined reference semantics.

This hook extends semantic-to-Ibis expression coverage. It cannot inspect or
rewrite arbitrary generated SQL, match connected query subgraphs, or affect
another semantic node.

### Let deterministic compilation define expression support

After incomplete Run admission, the runtime supplies the exact backend
connection or conservative dry compiler. Compilation outcomes are:

```text
ibis_expression_compiled
  complete expression compiled for the bound backend

ibis_operation_unsupported
  Ibis has no implementation for one exact operation/backend pair

ibis_expression_compile_rejected
  the backend compiler rejected an expression expected to be supported

single_evaluation_fence_unavailable
  required volatile/shared semantics cannot be guaranteed
```

None starts a datasource data statement. None retries with pandas, another
backend, a different statistical method, or a hidden materialization. Safe
diagnostics name the semantic occurrence, backend type, Ibis/compiler build
fingerprint, and owning lowerer without exposing the expression or SQL.

### Cross-backend graphs require an explicit boundary route

An Ibis expression attached to multiple backend instances is not an executable
federated plan. The boundary resolver must first choose one of these exact
routes:

| Source condition | Legal compilation |
| --- | --- |
| every source is addressable by one backend | bind all tables there and compile one Ibis expression |
| one declared federation engine can address every source | bind all remote tables through that engine and compile one Ibis expression |
| an input is an admitted materialized leaf readable by the target engine | compile its immutable scan with the remaining expression |
| one operator owns a registered guarded bounded-local boundary | compile engine inputs separately and run that exact local implementation |
| none of the above | fail before engine compilation |

There is no combinatorial search for partially pushed fragments. The source
topology and explicit boundary contracts determine the route. Two independent
connections plus client-side joining are not federation.

## Execution Boundary Capability Contract

### Declare only facts outside ordinary Ibis compilation

Datasource execution consumes a private profile separate from the current
authoring/inspection capability projection:

```text
ExecutionBoundaryProfileV1
  schema = "marivo.execution_boundary_profile/v1"
  datasource_refs[]
  backend_type
  adapter_id
  adapter_version
  ibis_version
  ibis_compiler_fingerprint
  backend_specific_lowerer_manifest_fingerprint
  resolved_engine_version
  resolved_feature_set_fingerprint
  supported_engine_version_range
  ibis_backend_conformance_suite_id
  profile_version
  profile_fingerprint
  federation_routes[]
  materialized_scan_and_import_capabilities[]
  single_evaluation_fence_capabilities[]
  server_write_and_export_capabilities[]
  snapshot_and_cancellation_capabilities[]
  transfer_enforcement_capabilities[]
```

The profile does not repeat whether the backend supports ordinary projection,
filter, join, aggregate, window, scalar, or predicate Ibis operations.
Construction plus deterministic compilation answers those questions for the
complete expression. This avoids a Marivo matrix drifting from Ibis's own
backend implementations.

Local Ibis compilation cannot prove that every server version accepts the
generated SQL. The adapter therefore admits only an engine-version range proven
by its Ibis-backend integration suite. A resolved server outside that range
fails closed. A query rejected inside the admitted range is an adapter/Ibis
conformance defect and never triggers fallback.

Profile facts come from registered connection metadata or conservative adapter
configuration, never from trial data queries. If resolution needs a live
connection or metadata statement, the runtime first admits and persists the
incomplete Run. Unknown boundary support fails closed.

### Keep boundary capabilities closed and exact

`ExecutionBoundaryCapabilityV1` is a closed union:

```text
ExecutionBoundaryCapabilityV1 =
    FederationRouteCapabilityV1
  | MaterializedDirectScanCapabilityV1
  | MaterializedImportCapabilityV1
  | SingleEvaluationFenceCapabilityV1
  | ServerWriteCapabilityV1
  | EngineManagedExportCapabilityV1
  | BoundedTransferCapabilityV1
  | SnapshotCapabilityV1
  | CancellationCapabilityV1
```

Each variant owns exact fields for source domains, target domain, table or file
bindings, temporary-resource lifecycle, supported sink kind, row/byte guards,
snapshot strength, or cancellation behavior. There are no generic option
mappings. Credentials, live connection objects, SQL strings, and mutable
relation handles never enter the profile.

The adapter's backend-specific lowerer manifest fingerprint must match the
manifest frozen into `IbisSemanticCompilerV1`. A profile cannot inject a
lowerer during an action.

### Distinguish unsupported expression, boundary absence, and runtime failure

`ibis_operation_unsupported` means Ibis has no implementation for one exact
operation/backend pair. `ibis_expression_compile_rejected` means the selected
backend compiler rejected a complete expression expected to compile.
`execution_boundary_unavailable` means no declared federation, materialized,
single-evaluation, write, or bounded-local route satisfies the graph.

All are pre-data-statement failures. Marivo records safe backend, Ibis/compiler,
lowerer, and boundary-profile fingerprints and fails. It does not retry with a
different semantic method, split the expression into opportunistic partial
pushdown, collect rows, or switch placement.

`engine_execution_failed` begins only after a compiled stage starts data work.
Retry policy, terminal Run state, and publication rollback belong to the
runtime module.

## Physical Stage Graph

### Physical graph envelope

```text
PhysicalStageGraphV1
  schema = "marivo.physical_stage_graph/v1"
  ibis_semantic_compiler_version
  execution_boundary_policy_version
  compiler_projection_fingerprints[]
  stage_boundary_proofs[]
  stages[]
  exchanges[]
  primary_output
  validation_outputs[]
  physical_plan_fingerprint
```

Bound Ibis expressions and compiler records remain process-local and private.
The executable graph binds their stage ids and safe digests, never serialized
Ibis operations or generated SQL.

### Execution domains

Every stage executes in one exact domain:

```text
DatasourceDomain
  one datasource and registered engine profile

FederatedEngineDomain
  one execution engine plus exact admitted remote source bindings

MaterializedStorageDomain
  one immutable Artifact scan adapter

LocalBoundedDomain
  one private process-local registered implementation
```

A stage cannot contain operations from several domains merely because one Ibis
backend object can reference them. Federation must be declared explicitly.

### Stage and exchange contracts

```text
PhysicalStageV1
  stage_id
  execution_domain
  semantic_root_ids[]
  ibis_compile_outcome
  local_implementation:
    NoLocalImplementationV1 | BoundLocalImplementationV1
  boundary_capability_ids[]
  input_exchanges[]
  output_schema
  output_key_contract
  output_ordering
  bound_guards
  evaluation_guarantees[]
  process_loss_recovery
  temporary_resource_requirements[]
  validation_requirements[]

PhysicalExchangeV1
  exchange_id
  producer_stage_id
  producer_output_id
  consumer_stage_id
  mode
  exact_schema
  representation_contract_id
  row_guard
  byte_guard
  ordering_contract
  authority_projection
```

`BoundLocalImplementationV1` carries the exact registered implementation id,
version, kind, dependency fingerprint, and determinism contract id. It is
present only for `LocalBoundedDomain`; all other domains require
`NoLocalImplementationV1`. The physical decoder rejects a local stage without a
binding and a non-local stage with one.

Every exchange `exact_schema` is the canonical Arrow-compatible logical data
contract. It fixes field order, logical types, nullability, timezone, decimal,
dictionary normalization, and nested-value rules independently of whether the
physical representation is Arrow IPC or Parquet. A pandas or Polars schema is
never an exchange contract.

`representation_contract_id` resolves one closed compiler-owned record:

```text
ExchangeRepresentationContractV1 =
    ArrowStreamRepresentationV1
      arrow_contract_version
      ipc_protocol_version
      decoded_buffer_byte_protocol_id
      batch_validation_contract_id

  | RunStagedParquetRepresentationV1
      parquet_contract_version
      manifest_contract_version
      decoded_buffer_byte_protocol_id
      schema_validation_contract_id
```

There is one compiler-owned Arrow exchange contract in the first cutover.
`RunStagedParquetRepresentationV1` references the runtime-owned
`ParquetDataContractV1`; Module 3 owns its placement and guards but does not
redefine its writer/reader semantics. Both dependency fingerprints enter the
physical plan, not the public Dataset definition.

`process_loss_recovery` is one closed value:

```text
ProcessLossRecoveryV1
  mode = process_lifetime | connection_lifetime | recoverable_cancel
  capability_id
```

`process_lifetime` is valid only for work that cannot outlive the owning
process. `connection_lifetime` requires a registered backend guarantee that
disconnect terminates the work. `recoverable_cancel` requires the Module 4
runtime to reserve an exact execution locator before submission and requires a
registered capability that cancels it and observes a terminal state.
`capability_id` resolves that exact proof or operation; it is also present in the
stage's boundary capability ids. A stage with no valid process-loss recovery
mode is unsupported before execution.

The initial exchange modes are:

```text
federated_reference
materialized_direct_scan
engine_managed_import
bounded_arrow_stream
run_staged_parquet
```

There is no unbounded in-memory exchange and no implicit durable exchange.
`materialized_direct_scan` means the consumer domain's registered adapter reads
the immutable backing without copying it through the Marivo process.
`engine_managed_import` means the destination engine imports an immutable
Artifact directly under one declared read-only, schema-preserving import
capability; its temporary destination is action-scoped and not a new Artifact.
Any client-mediated transfer uses `bounded_arrow_stream` or
`run_staged_parquet` and must satisfy the local policy guards.

`bounded_arrow_stream` is the default for one-pass consumption. The producer
emits canonical Arrow RecordBatches and the guard counts accepted rows plus
decoded Arrow-buffer bytes before the consumer sees each batch. The consumer
may assemble a complete Arrow Table only inside a registered Python kernel and
only after all aggregate local bounds remain satisfied.

`run_staged_parquet` is legal only when the consumer requires rewindable input
or one registered engine-export capability can produce Parquet without an
unbounded client transfer. Files live under the producing Run's staging
directory and are journaled by exact exchange id and ownership nonce. The
manifest file bytes and canonical decoded Arrow bytes must each remain within
the exchange byte guard. Schema validation happens before consumption.

Run-staged Parquet is a physical exchange representation, not Dataset storage.
It has no Artifact ref, storage receipt, Evidence, Finding, public lineage, or
cross-action reuse authority. It is cleaned on success, failure, cancellation,
or cold recovery. If the SQL prefix must be reusable independently, that prefix
must already be a reachable public Dataset and cross the explicit `execute()`
boundary; the compiler never promotes this staging exchange.

### Materialized scan leaves

A materialized scan leaf supplies only the admission handle defined by Dataset
Core and decoded by the runtime module. Planning validates:

- exact Session ownership;
- family and row-contract version;
- realized schema contract;
- immutable content authority;
- scan adapter capability;
- consumer-domain direct-scan, engine-managed-import, or bounded-transfer
  admission;
- required projection, predicate, ordering, and fold support.

The leaf's audit lineage is not traversed for executable rewrites. A downstream
predicate over retained fields may push into the scan adapter. A semantic-current
Population membership predicate may join from the retained identity leaf to an
admitted current source path. Neither case re-executes the leaf's origin graph.

### Federation

Federation is admitted only when one registered federated engine profile
declares:

- every remote datasource binding;
- exact read-only access semantics;
- type, null, timezone, decimal, collation, and ordering conformance;
- complete-expression Ibis compilation through the federation engine;
- snapshot/consistency authority requirements;
- credential and authorization isolation;
- bounded diagnostic behavior.

The boundary resolver chooses one federated domain deterministically and builds
one bound Ibis expression for it. It does not treat a Python
process that can open two connections as federation.

If several federated domains are semantically equivalent, the first-cutover
stable execution-domain-id tie-break chooses among them. Cost is
never allowed to bypass a semantic or authority requirement.

### Cross-datasource decision table

| Graph condition | Placement |
| --- | --- |
| all live sources share one datasource domain | one Ibis engine stage, plus only explicit validation/write stages |
| one admitted federated engine covers all live sources | federated engine domain |
| cross-source dependency is already separated by committed materialized leaves and the downstream domain declares direct scan/import access | scan leaves in that downstream domain |
| committed leaves require transfer and the downstream operator admits a hard-bounded exchange | guarded exchange into the downstream domain |
| one registered semantic node requires or admits an exact local implementation after a same-domain SQL-capable prefix | one maximal upstream Ibis engine stage, one guarded Arrow/Parquet exchange, then the bound local stage |
| exact operator contract admits a bounded-local join/reduction and every input, total transfer, output, and timeout bound is hard-guarded | registered local stage |
| one reachable public Dataset input satisfies all durable-boundary repair rules | fail with that exact `.execute()` repair |
| the missing boundary exists only inside a private Dataset graph | fail without a `.execute()` repair |
| any local input, combined transfer, output, or timeout bound is absent or unenforceable | fail |
| no domain provides equivalent semantics | fail |

### Bounded-local transfer and result guards

Every local inbound edge has both:

```text
row_guard
  positive hard maximum
  producer-enforced limit-plus-one or exact committed count

byte_guard
  positive hard maximum
  canonical decoded Arrow-buffer counter plus staged-file counter when present
```

The producer must stop transfer as soon as violation is known. Limit-plus-one
is detection only; the extra row is never accepted as result data. A byte guard
counts the canonical wire or in-process representation named by the exchange
contract.

The runtime also keeps one counter across all inbound byte guards and rejects
the stage when their combined accepted bytes would exceed
`max_total_input_bytes`. The local implementation writes through a guarded
result sink that applies `max_output_rows` and `max_output_bytes` before any row
is exposed to a downstream stage. The action deadline starts before the first
transfer and remains active through local result completion. An implementation
that cannot expose enforceable input, output, and deadline checkpoints is not a
registered bounded-local implementation.

If any per-input row, combined-input byte, output row, output byte, or timeout
guard is exceeded:

- input overflow prevents the local calculation from starting; output or
  timeout overflow aborts and discards the entire local calculation;
- no partial Dataset, Artifact, Evidence, or Finding is published;
- the action fails with exact observed-at-least and limit facts where safe;
- the repair selects an engine-supported method, narrows Population scope,
  or introduces a reachable explicit materialization boundary when the
  downstream public operator already accepts that Dataset input.

The compiler does not infer sampling to satisfy a guard.

### Local stage execution protocol

A DuckDB local stage binds each input as an Arrow RecordBatch reader or a
validated Run-staged Parquet relation, compiles the admitted relational
remainder through Ibis, and emits guarded Arrow RecordBatches. It uses a
Run-scoped DuckDB workspace only when the registered execution contract needs
temporary files; that workspace is a journaled non-output resource and is never
an engine Dataset receipt.

A Python-kernel stage receives exact Arrow Tables assembled under the same
aggregate guards, invokes only the bound kernel id/version, validates its Arrow
output before accepting a batch, and writes through the common guarded result
sink. pandas, NumPy, SciPy, or statsmodels objects remain inside the kernel and
are discarded before the output crosses the stage boundary.

Neither implementation may return a public intermediate, persist an exchange
as an Artifact, choose another implementation after failure, or retain a
process cache after the action ends. The root output proceeds to Module 4's
already selected output binding and publication protocol; an internal stage
output never does.

### Execution and materialized-read bounds

`execute()` is the only public action that compiles a Logical Dataset graph. It
has no public row limit, keeps high-cardinality rows in an admitted engine or
durable storage path, and cannot route an unbounded result through local memory
merely because final storage is local.

`show()` and `to_pandas()` are not logical compiler actions. They operate only
on a Materialized Dataset scan leaf. `show()` applies a bounded deterministic
preview projection to that immutable backing; `to_pandas()` reads the complete
backing under its transfer guards. Neither may issue the origin datasource SQL.

## Ordering and Determinism

### Logical ordering

An unordered Dataset remains unordered in the semantic graph. An operator that
depends on order must supply an exact governed order contract; physical source
order is never authority.

### Canonical presentation order

For materialized `show()` and `to_pandas()`, the storage-read adapter consumes
the exact ordering contract owned by Dataset Core:

1. an ordered Dataset uses its complete `DatasetOrderTerm` sequence, including
   value/generated business terms, null placement, registered value-order
   semantics, and its unique tie-breaker;
2. an unordered non-singleton Dataset uses its exact row-key fields in contract
   order with their registered ascending value-order semantics;
3. an exact singleton adds no synthetic ordering field.

If the output row contract has no safe total order and the owning family has no
canonical ordering rule, compilation fails. Backend-natural order, hash order, and
generated SQL order are not accepted.

### Physical determinism

Equal action requests against equal semantic graphs, compiler/lowerer builds,
source bindings, boundary profiles, and boundary-policy inputs produce equal
semantic, compiler-projection, and physical fingerprints. Ibis operation object
identity, generated aliases, SQL formatting, connection identity, runtime
timing, backend query ids, and temporary physical names do not participate.

Equal physical fingerprints do not promise equal rows across executions of
logical current-source inputs. Source snapshot authority belongs to runtime.

## Failure Taxonomy and Ownership

### Failure phases

| Phase | Example | Owner of classification | Data statement started |
| --- | --- | --- | --- |
| public admission | wrong family, shape, predicate field, or aggregation transition | Dataset/Observation/Operator owner | no |
| captured source parameters | missing, foreign, mutated, or digest-mismatched `BoundSourceParametersV1` | Observation/compiler seam | no |
| compiler manifest validation | duplicate or missing semantic lowerer, unstable implementation fingerprint | compiler owner | no |
| semantic graph validation | corrupted private node, missing input, contract-version mismatch | compiler owner | no |
| execution-boundary resolution | no single-backend, federation, materialized, or bounded-local route | compiler/boundary adapter owner | no |
| source binding | datasource ref cannot bind to the resolved Ibis backend | compiler/adapter owner | no |
| Ibis expression construction | lowerer cannot preserve exact row contract or semantic invariant | compiler/lowerer owner | no |
| materialization-sink feasibility | no candidate has an admitted write route | compiler/runtime at owned seam | no |
| Ibis/backend compile | unsupported operation or complete-expression compile rejection | Ibis/backend-specific lowerer/adapter owner | no data statement |
| physical formation | invalid explicit boundary, write, validation, or recovery contract | compiler owner | no |
| stage execution | timeout, authorization loss, backend failure, bound exceeded during transfer | executor/runtime | yes or transfer begun |
| output validation | schema, uniqueness, or exact-bound contradiction | executor/runtime using compiler requirements | yes |
| publication | storage, Evidence, Artifact, or Run commit failure | materialization runtime | possibly complete |

The runtime module decides exact Run admission and terminal state transitions.
It preserves these phase distinctions and never turns compilation failure into
execution failure or a failed execution into unsupported expression support.

A process-static compiler-manifest defect prevents action admission entirely.
A live backend/adapter build mismatch can be discovered only after incomplete
Run admission and is then recorded in the runtime's `compiler_binding` phase;
neither case starts datasource data work.

### One primary bounded reason

A compilation failure returns one primary reason selected by stable occurrence
order and failure precedence:

```text
graph_contract_invalid
compiler_manifest_invalid
semantic_lowering_invalid
fanout_proof_missing
execution_boundary_unavailable
federation_unavailable
durable_boundary_required
cross_datasource_placement_unavailable
materialized_scan_unavailable
single_evaluation_fence_unavailable
local_stage_not_registered
local_bound_unenforceable
ibis_operation_unsupported
ibis_expression_compile_rejected
physical_formation_unavailable
canonical_order_unavailable
```

The error may include a bounded list of additional blockers but does not dump
the graph or backend exception chain. It states expected, received, safe
location, and one mechanically valid repair when the caller can change the
request. Repairs come from real boundary profiles, source domains, materialized
inputs, and operator contracts. Compiler-manifest and lowerer-contract failures
are internal conformance defects: they name the safe compiler/lowerer ids and
direct the caller to a valid Marivo build; they never suggest changing
analytical meaning, dynamically disabling a lowerer, or retrying through
another backend.

`durable_boundary_required` is used only when the compiler can name a public
Dataset input satisfying all four durable-boundary repair rules.
`cross_datasource_placement_unavailable` is used when the missing cut exists
only inside a private graph; it never carries a fake `.execute()` snippet.

## Safe Diagnostics

### `contract()` projection

Before action, a Dataset contract may disclose:

```text
PlanningRequirementSummaryV1
  semantic_source_count
  datasource_refs[]
  materialized_input_refs[]
  potential_domain_count
  hard_barrier_kinds[]
  required_backend_specific_lowerer_ids[]
  required_boundary_capability_ids[]
  bounded_local = not_registered | eligible | required_if_engine_absent
  bounded_local_implementation_kinds[]
  admitted_exchange_modes[]
  bounded_local_policy: LocalExecutionPolicySummaryV1
  action_time_blockers[]
```

`LocalExecutionPolicySummaryV1` exposes the five fixed numeric bounds from
`LocalExecutionPolicyV1` and its schema fingerprint; it is not a mutable
policy object.

This is derived from semantic requirements, not a selected physical plan.
Calling `contract()` does not connect, construct a bound expression, or
compile a live backend.

### Structured-error projection

A compiler error may disclose:

- public operator id and bounded occurrence path;
- Dataset family and shape;
- datasource/backend type and safe profile fingerprint;
- missing boundary capability, semantic proof, or backend-specific lowerer id;
- compiler step plus safe lowerer id/version for a conformance failure;
- stage class, not full stage graph;
- row/byte limit and safe observed-at-least value for a bound failure;
- registered repairs and Help target.

It does not disclose raw predicate payloads, identities, credentials, Ibis
expressions, generated SQL, backend stack dumps, or complete semantic graphs.

### Run audit projection

The runtime may persist this bounded compiler-owned projection:

```text
PlanningAuditV1
  semantic_plan_fingerprint
  compiler_projection_fingerprint
  physical_plan_fingerprint
  ibis_semantic_compiler_version
  lowerer_manifest_fingerprint
  ibis_version
  ibis_compiler_fingerprints[]
  boundary_profile_fingerprints[]
  execution_boundary_policy_version
  local_execution_policy_fingerprint
  stage_count
  engine_expression_stage_count
  bounded_local_stage_count
  exchange_count
  exchange_mode_counts[]
  local_implementation_ids[]
  local_dependency_fingerprints[]
  placement_classes[]
  applied_lowerer_ids[]
  single_evaluation_fence_count
  materialization_sink_feasibility_counts[]
  selected_materialization_sink_kind
  selected_materialization_sink_disposition
  cse_occurrence_count
  cse_canonical_node_count
  bounded_transfer_summaries[]
  primary_failure_phase
  primary_failure_kind
```

Module 4 owns the containing Run schema and persistence timing. This document
owns only the meaning and redaction of these planning fields.

## Cross-Module Seams

### Dataset Core supplies

- opaque logical and materialized root handles;
- exact row contracts and public schema order;
- logical/materialized input authority tokens;
- definition fingerprints and bounded lineage;
- Session ownership;
- action-time requirements;
- canonical presentation-order requirements;
- materialized scan-leaf non-rewriteability.

The compiler supplies an executable action plan or one bounded failure. It never
widens the public Dataset surface.

### Observation Model supplies

- one exact membership spine contract;
- the closed `PopulationInput` admission contract and direct-membership versus
  identity-projection modes;
- normalized predicate semantics and authored phase;
- family filter effects, field-resolution rules, selected authority
  requirements, and non-commuting barriers;
- Entity identity, Relationship paths, and fanout requirements;
- one coordinate spine independent of Metric non-nullness;
- per-Metric recomputation or materialized-fold contracts;
- Population sampling order and target lineage;
- action-time semantic and physical requirements.

The compiler expresses every one of these meanings in Ibis and explicit stage
boundaries.

### Materialization Runtime consumes

- prepared semantic graph plus bound Ibis expression and one ordered feasibility result for each
  runtime-owned materialization sink candidate;
- immutable executable stage graphs;
- one `BoundMaterializationOutputV1` for execute actions after the runtime
  returns the selected candidate id;
- exact primary-output schema and key requirements;
- validation outputs and transfer guards;
- action-scoped temporary-resource lifecycle requirements;
- exact bounded Arrow or Run-staged Parquet exchange representations and bound
  DuckDB/Python local implementation records;
- the fixed local-execution-policy fingerprint;
- safe compiler audit projections, including Ibis/compiler, lowerer-manifest,
  boundary-profile, and stage summaries;
- failure-phase distinctions;
- already admitted materialized scan-leaf requirements.

It supplies Run admission, datasource snapshot capture, execution,
guarded Arrow/Parquet transfer, invocation of the bound local implementation,
success/failure cleanup of declared temporary resources, storage, receipts,
publication, recovery, and immutable leaf decoding. It may not expose the
private plan or exchange as Dataset state or use origin lineage to rewrite a
leaf.
It also supplies ordered sink candidates, selects the lowest-ranked admitted
candidate, and never asks the compiler to choose storage policy. The compiler
owns candidate feasibility and binds the final physical write path; it does not
create the receipt or publish storage.
It also owns the runtime meaning and validation of the `semantic_current` and
`materialized_only` authority modes selected by the upstream filter contract;
the compiler carries those requirements but does not redefine them.

The accepted seam agrees on these constraints:

1. how the runtime divides pure graph validation before Run admission from live
   boundary-profile resolution and Ibis/backend compilation after incomplete
   Run admission;
2. how compile-only failures terminate an admitted Run;
3. which scan-admission facts are decoded before compilation versus execution;
4. how transfer- and result-bound failures are recorded without publishing
   partial rows;
5. how source snapshot requirements attach to physical stages;
6. sink feasibility is computed before final physical placement and engine
   compilation;
7. runtime rank never affects feasibility, only the runtime-owned final
   selection;
8. compile or execution failure never switches to a different sink inside the
   same Run.

### Typed Operators supplies

- exact operator id, variant id, and contract version;
- capability link plus structured invocation and output contracts;
- method, exactness, approximation, null, tie, and ordering requirements;
- one exact semantic-node kind and reference semantics per variant;
- exact Arrow-compatible input/output semantics, dependency-independent
  reference results, and any stricter bound required by an admitted local
  implementation;
- typed action-time minimum-data requirements;
- generated-field identity and consumer admission contracts from which
  continuations are derived.

The compiler-owned lowerer manifest maps each supplied semantic-node kind to
its proven-equivalent implementations. The compiler does not decide product
admission or approximation, and the operator registry does not enumerate
implementation ids.

### Subject, Event, and Lifecycle supplies

- exact SubjectSet identity authority;
- SubjectSet direct-membership input requirements;
- Event matching and Lifecycle replay semantic nodes and barriers;
- identity privacy requirements;
- exact ordering, censoring, temporal, and row-contract semantics;
- registered semi-join and reduction Ibis lowerers;
- one immutable lowerer manifest for those exact semantics.

The compiler supplies safe join, window, and boundary implementation. It does
not collect subject identities locally unless an explicit private bounded-local
contract admits the exact operation and privacy policy.

### Public Cutover consumes

- the private semantic-node, Ibis-lowerer, and boundary-capability registries that replace
  eager execution;
- exact removed fallback, semantic generated-SQL postprocessors, and
  duplicate-compilation paths;
- implementation ownership for semantic normalization, Ibis lowering, engine
  bindings, boundary profiles, diagnostics, and tests;
- Help/contract/error/Run disclosure changes;
- vertical acceptance evidence.

The cutover must remove current eager or ad hoc paths rather than maintain dual
compiler behavior.

## Rejected Alternatives

### Expose an Ibis expression as the Dataset plan

Ibis is the private relational expression language, not the owner of
Population, Metric, fanout, lineage, or public identity semantics. Exposing it
would add an expression escape hatch and make compiler internals part of the
public contract.

### Use generated SQL as the normalized fingerprint

SQL changes with dialect, renderer version, aliases, formatting, and optimizer
behavior. It also loses the distinction between logical and materialized input
authority. SQL digest may be execution audit, never Dataset identity.

### Patch generated SQL to implement DSL semantics

String or SQL-AST postprocessing after Ibis compilation bypasses typed
expression validation and couples correctness to emitted aliases and dialect
formatting. A semantic backend workaround must become a narrow Ibis custom
operation or compiler extension with parity tests. Transport-only query tagging
and bounded audit normalization may remain runtime concerns because they do not
change query meaning.

### Let the backend optimizer own semantic equivalence

Backend optimizers do not know Marivo's Population membership, target lineage,
component aggregation, or materialization authority. Marivo must construct or
fence the exact semantics before submission. After that boundary, the backend
optimizer should own the SQL execution plan.

### Probe capability by executing and catching errors

Trial execution can be expensive, mutate backend state, leak data, produce
incomplete audit, and conflate capability absence with transient failure. A
versioned profile and deterministic compile are the only discovery paths.

### Fall back to pandas, Polars, or DuckDB after an engine failure

This silently transfers high-cardinality data, changes dtype/null/time
semantics, and bypasses placement audit. DuckDB is a registered execution
domain, not an exception handler; pandas may exist only inside one bound Python
kernel; Polars is not a first-cutover local implementation. Only a
pre-admitted bounded-local implementation with hard guards is legal.

### Automatically materialize cross-source intermediates

Hidden materialization would create durable rows and recovery authority without
the public `.execute()` boundary or Module 4 publication contract. Missing
durable boundaries fail with an explicit repair.

### Treat two similar source branches as shareable across actions

Logical current-source actions may observe different source snapshots. Durable
reuse requires an explicit materialized Dataset leaf.

### Resolve Relationship ambiguity through join cost

Cost cannot choose analytical Entity meaning or fanout policy. Ambiguity is a
semantic admission failure.

### Repair fanout with final `DISTINCT` or `GROUP BY`

Deduplication after duplication does not prove Metric correctness and can hide
overcounting. The branch must aggregate at the declared safe key before the
join or fail.

### Make physical placement part of `definition_fingerprint`

Equal analytical definitions should survive engine-profile and placement
changes. Physical fingerprints and Run audit record implementation choice
separately.

### Add a public `explain()` method

The agent needs bounded requirements, legal continuations, and repairs, not an
unstable private graph. `contract()`, errors, and Run audit provide those facts
through their natural owners.

## Vertical Acceptance Journeys

### One Population spine with several Metric branches

```python
features = session.observe(
    metrics=[revenue, order_count, conversion_rate],
    time_scope=window,
)

features.execute().show()
```

Acceptance must prove:

- one shared Population identity spine;
- membership-before-sampling order;
- independent nullable Metric branches;
- numerator and denominator aggregation before ratio composition;
- branch uniqueness before left join;
- no final deduplication repair;
- one bounded inspection projection with canonical order;
- no public plan or Ibis value.

### Predicate phase around an aggregation barrier

```python
features = session.observe(metrics=[revenue, cpu_seconds])

selected_entities = features.where(mv.gte(cpu_seconds, 60)).aggregate()
selected_regions = (
    features.with_dimensions(region)
    .aggregate()
    .where(mv.gte(revenue, 1_000_000))
)
```

Acceptance must prove that the first predicate filters Entity observations
before reduction and the second filters completed region rows after reduction.
Both compile into the same engine-side Ibis expression as their surrounding
logic, while their semantic occurrences and definition fingerprints remain
distinct.

### Action-local common-subexpression sharing

One registered operator that consumes the same Population/Metric branch twice
within one action must share the exact canonical subgraph when authority,
ordering, and placement requirements agree.

Acceptance must prove fewer physical branch executions, unchanged output rows,
retained occurrence-specific errors, and no Artifact or cross-action cache.

Action-local CSE itself must not claim cross-action reuse. When the same
Logical Dataset is executed again in one named Session, Module 4 resolves its
write-once execution binding before this compiler is invoked.

### One realized sample for every Metric branch

```python
population = session.population(query_execution).sample(
    mv.engine_sample(target_rows=100_000, seed=42),
)
features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
    population=population,
)
features.execute().show()
```

Acceptance must prove one physical sample producer, one realized identity
relation, and three consumers of that same relation. The backend matrix must
cover a native one-evaluation form, an action-scoped temporary-relation fence,
and an unsupported backend that fails before data work. Replacing the shared
producer with three equally seeded sample expressions must fail the plan test
rather than count as equivalent.

### Materialized scan-leaf Ibis compilation

```python
checkpoint = session.observe(metrics=[revenue]).execute()
filtered = checkpoint.where(mv.gte(revenue, 100))
filtered.execute().show()
```

Acceptance must prove a scan of the immutable Artifact with the retained-field
predicate in the same bound Ibis expression, no traversal of origin lineage,
exact retained row-contract validation, and bounded safe audit facts.

### Materialized semantic-current Population filter

```python
population = session.population(customer).execute()
regional = population.where(mv.eq(region, "APAC"))
regional.execute().show()
```

Acceptance must prove that compilation begins from the immutable identity leaf,
joins only the current admitted Dimension path, does not replay the original
Population scope/predicates/sample, and fails explicitly on Relationship or
current-authority drift.

### Portable, backend-specific, and unsupported percentile

The same admitted percentile operator is planned against three controlled
profiles:

1. ordinary portable Ibis percentile compilation;
2. one registered exact backend-specific Ibis lowerer;
3. no exact Ibis lowering and only an unadmitted approximate quantile.

Acceptance must prove deterministic portable compile, backend-specific compile,
and `ibis_operation_unsupported` outcomes. Case 3 must fail before data work and
must not collect rows locally or silently approximate.

### Federated cross-datasource execution

One Dataset graph whose sources span two datasources is planned with an exact
registered federated engine profile.

Acceptance must prove remote binding admission, semantic type conformance,
fanout-safe join lowering, one declared federation domain, safe capability
fingerprints, and no process-local two-connection emulation.

### Explicit durable-boundary repair

One registered multi-input Dataset operator receives two public Dataset inputs
from different datasource domains. Materializing either input is independently
placeable, the operator admits the same Dataset family after materialization,
and its downstream domain declares direct scan/import access to that storage.

Acceptance must prove a pre-data compilation failure with one exact upstream
Dataset definition/family/fingerprint boundary to materialize. The compiler must
not create a hidden Artifact or return a partial public Dataset.

After the caller explicitly materializes that upstream Dataset, the downstream
action must consume only its immutable leaf through the declared scan/import
capability.

A separate cross-datasource multi-Metric `session.observe(...)` journey must
prove the opposite result: because its Metric branches are private and
`observe` rejects Dataset inputs, the failure offers no `.execute()` repair.
It names only actually registered federation, bounded-local, or semantic-source
repairs.

### Runtime-ranked materialization sink

One execute action receives ordered local, engine, and object sink
candidates from Module 4. The local candidate is physically unsupported for the
result bound, while engine direct-write and object export are both admitted.

Acceptance must prove:

- feasibility is derived without reading `runtime_policy_rank` as compiler
  preference;
- Module 4 selects the lower-ranked admitted engine candidate;
- final physical placement binds only that candidate and its direct-write
  protocol;
- the physical fingerprint and bounded Run audit include sink kind and
  disposition, not credentials or a receipt;
- compile failure after selection fails the Run and does not retry object
  storage;
- no Artifact or receipt exists until Module 4 completes publication.

### Bounded-local success and hard failure

One journey ends a maximal datasource Ibis expression at a registered local
relational node, streams bounded Arrow into DuckDB, and executes the remaining
Ibis expression there. A second journey spools one rewindable input to
Run-scoped Parquet and invokes an operator-specific Python statistical kernel.
Both write Arrow through the guarded output and deadline enforcement.

Acceptance must prove:

- local eligibility was present in the Dataset contract;
- the exact DuckDB or Python-kernel registration was bound before data work;
- the physical Run projection names bounded-local placement;
- Arrow and Parquet exchanges validate the same exact schema and guards;
- Run-staged Parquet, DuckDB workspace, and kernel buffers are never Artifact
  or Materialized Dataset inputs and are cleaned before publication;
- inputs, output, and elapsed time within every fixed guard produce the same
  result as the registered reference contract;
- per-input row, combined-input byte, output row, output byte, or timeout
  overflow aborts the whole action;
- overflow returns no partial result and publishes no Artifact or Evidence;
- the compiler never inserts sampling.

### Compile rejection versus execution failure

One controlled backend adapter falsely declares a capability that its compiler
rejects. A second compiles successfully but fails after the data statement
begins.

Acceptance must prove distinct `ibis_expression_compile_rejected` and
`engine_execution_failed` classifications, no fallback retry, safe digests in
audit, and runtime-owned terminal Run handling.

### Deterministic compilation

Compiling equal action requests twice against equal compiler, lowerer,
boundary-profile, and sink inputs must produce equal semantic,
compiler-projection, and physical fingerprints and stable node/stage ordering.
Ibis object identity, connection identity, temporary aliases, timing, and SQL
formatting must not affect them.

### Compile one complete query without a pushdown planner

A controlled single-datasource Dataset combines filters, projections, a
fanout-safe join, preaggregation, final aggregation, a window, and canonical
ordering. The semantic compiler builds one lazy Ibis table expression for the
complete graph.

Acceptance must prove one Ibis/backend compile call, one engine query stage,
zero bounded-local stages, and SQL containing the complete computation. Ibis
may emit nested selects or CTEs and the database may choose any valid execution
plan. Marivo must have no pushdown-rule registry, fragment search, SQL-AST
rewrite, or subquery-merge rule. A separate multi-backend expression must prove
that two bound Ibis connections fail boundary resolution rather than being
mistaken for federation.

## Implementation Evidence Required

Later implementation acceptance must include:

- exact-schema encode/decode and corruption tests for every private graph;
- one independent registry test proving every semantic variant has one exact
  decoder, one owning semantic lowering registration, and at least one exact
  Ibis or local route;
- lowerer-manifest assembly-order randomization tests proving a stable
  fingerprint;
- adapter-profile/manifest mismatch tests proving that an action cannot inject
  or substitute backend-specific lowerers;
- a shared lowerer conformance suite for pure expression construction, exact
  output schema, semantic invariants, stable diagnostics, and no execution;
- isolated tests for each portable and backend-specific lowerer plus
  differential parity tests for every backend-specific implementation;
- whole-expression tests proving filter, projection, join, aggregate, window,
  preaggregation, and compatible branch composition compile into one engine
  stage without a Marivo pushdown planner;
- registered-local cut tests proving the largest datasource prefix remains one
  Ibis engine stage and the exact local implementation is selected before data
  work rather than after compile or execution failure;
- Arrow RecordBatch and Run-staged Parquet exchange parity tests for schema,
  null, timezone, decimal, dictionary, ordering, row, and decoded-byte guards;
- Ibis-to-DuckDB relational differential tests and registered Python-kernel
  differential tests, including proof that pandas objects never cross the
  kernel boundary and Polars is not a generic fallback;
- SQL-rendering tests proving required subqueries and CTE fences stay inside
  that engine stage and require no Marivo SQL-AST optimization rule;
- a cutover test proving no semantic lowering depends on generated-SQL string
  postprocessing;
- deterministic canonicalization and fingerprint golden tests;
- authored occurrence preservation after CSE;
- property or differential tests for every registered backend-specific Ibis
  lowering;
- Population-spine null retention and fanout adversarial fixtures;
- engine-profile contract tests for DuckDB, SQLite, Trino, MySQL, PostgreSQL,
  and ClickHouse as applicable to the first cutover;
- portable/backend-specific/unsupported Ibis compile tests;
- resolved engine-version/feature conformance tests for every boundary profile;
- compile-rejection tests that prove no execution or fallback;
- process-loss recovery contract tests proving every emitted stage is
  process-bound, connection-bound, or recoverably cancellable;
- cross-datasource federation, materialized-boundary, and failure tests;
- a negative cross-source observe test proving that no unreachable
  `.execute()` repair is emitted;
- runtime-ranked local/engine/object sink feasibility tests proving rank does
  not affect feasibility, only one runtime-selected candidate enters physical
  formation, and post-selection failure never switches sinks;
- materialization output handoff tests proving exact producer stage/output,
  receipt protocol, and transfer guards without creating a receipt or Artifact;
- single-evaluation sampling tests proving one realized identity relation is
  shared by all Metric branches;
- per-input row, combined-input byte, output row, output byte, and timeout guard
  overflow tests with no partial publication;
- exact fixed-policy boundary tests at 100,000 rows per input, 67,108,864 total
  input bytes, 100,000 output rows, 67,108,864 output bytes, and 60 seconds,
  plus stricter operator-bound tests;
- materialized leaf non-rewriteability tests;
- deterministic presentation-order tests;
- safe diagnostic redaction and byte-budget tests;
- Run projection tests owned jointly with Module 4;
- at least one real-agent journey that constructs a chain, inspects
  `contract()`, executes it, reads the terminal Dataset/Run evidence, and never
  needs a plan or SQL surface.

Mocked health, successful Ibis compilation, or a generated SQL snapshot alone
is not acceptance. The terminal action must execute against a real supported
backend and prove row, authority, placement, and audit outcomes.

## Acceptance Criteria

This design is complete when all of the following are reviewable and later
testable:

1. the semantic graph is Marivo's only private analytical-plan authority;
2. the first cutover has no separate relational IR, optimizer scheduler,
   pushdown-rule registry, fragment search, cost model, or SQL-AST optimizer;
3. one single-engine Dataset compiles directly into one lazy Ibis table
   expression and one engine query stage;
4. every semantic node variant has one exact owning semantic lowering
   registration whose selected route is an Ibis lowerer or a bound local
   implementation;
5. the lowerer manifest is immutable, exhaustive, deterministic, and
   fingerprinted for one action;
6. Ibis expressions, SQLGlot trees, SQL, aliases, and backend plans remain
   ephemeral private products rather than Marivo identities;
7. compiler output schema and shape validate against the semantic row contract
   at every node;
8. authored occurrence paths survive canonical interning and action-local CSE;
9. action-local CSE requires equal authority and creates no durable reuse;
10. one shared Population/coordinate spine is independent of Metric
    non-nullness;
11. Metric branches are unique at the spine key before nullable left joins;
12. one sampled Population is evaluated exactly once and consumed by every
    Metric branch through a backend-guaranteed fence when necessary;
13. Relationship traversal consumes exact admitted paths and fanout proofs;
14. final deduplication cannot repair unsafe fanout;
15. filters are emitted at their authored semantic position;
16. sampling, aggregation, materialization, subject, Event, Lifecycle,
    component, cumulative, approximation, and privacy boundaries are hard by
    default;
17. logical aggregation preserves governed component and temporal semantics;
18. materialized compilation consumes only retained leaf authority and never
    traverses origin lineage;
19. ordinary operation support is determined by complete-expression Ibis/backend
    compilation, not a duplicated Marivo capability matrix;
20. boundary profiles declare only proven Ibis/engine version conformance,
    federation, materialized scan/import, single-evaluation fence, server
    write/export, snapshot/cancellation, and guarded-transfer facts;
21. a backend-specific Ibis lowerer is narrow, exact, version-scoped, and
    differential-tested;
22. approximation never substitutes for exactness without semantic-owner
    admission;
23. unsupported Ibis operation, compile rejection, boundary absence, and
    execution failure remain distinct;
24. compile or execution failure never triggers a new fallback placement;
25. every physical stage has one exact execution domain;
26. an Ibis expression bound to multiple backend instances is rejected and
    never treated as federation;
27. federation is declared by one engine that can bind all participating
    sources;
28. no hidden durable intermediate Dataset or Artifact is created;
29. `.execute()` is suggested only at a reachable public Dataset input
    whose independent materialization and downstream scan/import are admitted;
30. every local stage is explicitly registered, disclosed, and hard-guarded for
    per-input rows, combined bytes, output rows, output bytes, and timeout;
31. bound overflow fails the whole action without truncation, sampling, or
    partial publication;
32. `show()` bounds only final inspection, `to_pandas()` returns complete rows
    or no DataFrame, and `execute()` never silently routes unbounded rows
    through local memory;
33. unordered presentation acquires one safe canonical total order or fails;
34. equal semantic, compiler/lowerer, binding, boundary-profile, and sink inputs
    produce deterministic compiler-projection and physical fingerprints;
35. `contract()`, errors, and Run audit expose bounded owned projections only;
36. no public plan, explain, SQL, Ibis, task, future, or receipt API is added;
37. required SQL subqueries, CTEs, and semantic fences remain inside one engine
    stage and do not count as lost pushdown;
38. Marivo never adds a query-pushdown or subquery-merge rule when Ibis already
    expresses the complete engine query;
39. materialization sink feasibility is independent of runtime policy rank;
40. Module 4 selects exactly one admitted sink before backend compilation and no
    later failure switches sinks inside the same Run;
41. every physical stage declares one process-loss recovery mode;
42. end-to-end acceptance executes the complete Ibis expression against a real
    supported backend and proves row, authority, placement, and audit outcomes;
43. every accepted Module 5/6 call normalizes into one closed invocation variant
    whose row-changing parameters participate in semantic node identity and are
    available to its exact lowerer;
44. an admitted partial-SQL graph forms one maximal Ibis stage per upstream
    execution domain plus only its pre-registered local tail, never a
    post-failure fallback split;
45. Arrow is the canonical local exchange schema, with bounded Arrow streaming
    as the default one-pass representation;
46. Run-staged Parquet is private rewindable exchange state with exact cleanup
    and never a Dataset Artifact, receipt, Evidence source, or reuse key;
47. DuckDB is the only first-cutover local relational executor and every
    non-relational local calculation is one registered Python kernel;
48. pandas remains kernel-internal, Polars is absent from the first-cutover
    execution union, and neither can become a generic local fallback.
49. parameterized source adapters consume only exact values captured by each
    logical source definition; compiler and lowerers never read ambient Session
    binding state or persist raw binding values.

## Owner-Confirmed Module Decisions

The owner confirmed the original decisions on 2026-09-01, revised the
materialized-input and partial-SQL execution boundary on 2026-09-02 and
2026-09-03, and froze parameterized-source binding on 2026-09-04:

1. the semantic graph remains Marivo's authority for DSL meaning;
2. Ibis is the only private relational expression language in the first
   cutover;
3. Module 3 does not introduce a separate relational IR or general optimizer;
4. for one execution domain, the complete semantic graph lowers to one lazy
   Ibis expression and one engine query stage;
5. Ibis and SQLGlot own backend-specific expression rewrites and SQL generation;
6. the datasource optimizer owns join algorithms, access paths, SQL
   decorrelation, CTE inlining, and other database-plan choices;
7. Marivo does not implement query-pushdown, subquery-merge, or SQL-AST rewrite
   rules;
8. every semantic node has one exact semantic lowering owner; relational routes
   use a portable or narrowly backend-specific Ibis lowerer and non-relational
   routes use one registered local implementation;
9. semantic node and compiler-projection fingerprints never serialize or depend
   on Ibis operation identity or SQL formatting;
10. semantic nodes are content-addressed while authored occurrences remain
    separately addressable;
11. CSE is action-scoped and authority-token exact;
12. Population and coordinate spines are built once and Metric branches left
    join at a proven unique key;
13. one sampled Population is evaluated once per action through a
    backend-guaranteed engine form or action-scoped fence;
14. Relationship ambiguity and fanout are semantic blockers, not query-plan
    choices;
15. ordinary backend operation support is learned from deterministic Ibis
    compilation after incomplete Run admission;
16. Marivo profiles only proven Ibis/engine version conformance and
    execution-boundary capabilities that local Ibis compilation cannot decide;
17. capability discovery never runs a trial data query;
18. unsupported operation and deterministic compile rejection fail without
    fallback;
19. registered bounded-local execution uses the fixed
    `LocalExecutionPolicyV1` with exact schema, transfer, output, and timeout
    hard guards;
20. guard overflow fails the whole action and never infers sampling;
21. cross-datasource execution requires one declared federation backend,
    existing materialized leaves, or a registered bounded-local stage;
22. several Ibis backend connections do not constitute federation;
23. `.execute()` is offered as a repair only at a reachable public Dataset
    input with independently placeable execution and downstream scan/import;
24. the compiler never creates hidden durable intermediate Datasets;
25. materialized origin lineage is audit-only and never executable;
26. canonical ordering is a compiler requirement for unordered visible rows;
27. requirements use `contract()`, outcomes use Run audit, and neither exposes
    Ibis or SQL;
28. Module 4 owns materialization sink ranking and selection while this module
    owns rank-independent feasibility and the selected sink's final write
    binding;
29. post-selection compile or execution failure never switches sinks inside the
    same Run;
30. every physical stage declares one process-loss recovery mode, and a stage
    that can outlive its owner requires a pre-reserved recoverable cancel path.
31. every operator compiles from an ordered logical/materialized input-authority
    vector, and every Materialized input remains an immutable scan leaf;
32. a registered partial-SQL graph keeps one maximal Ibis prefix per upstream
    execution domain and crosses only a pre-admitted local boundary, never a
    failure fallback;
33. Arrow is the canonical exchange contract, bounded Arrow streaming is the
    one-pass default, and Run-staged Parquet is the only rewindable local
    exchange representation in the first cutover;
34. DuckDB is the first-cutover local relational executor, while
    non-relational calculations use exact registered Python kernels whose
    internal pandas or numerical objects never cross the Arrow boundary;
35. Polars is not in the first-cutover local implementation union, and no
    private exchange becomes an Artifact or reusable Materialized Dataset;
36. source parameter lowering consumes only the exact process-local values
    captured in the logical source definition, validates their digest before
    data work, and never consults a dynamic Session scope;
37. compiler audit, errors, fingerprints, and executable-plan projections may
    contain parameter identities and opaque digests but never raw or encoded
    source-binding values.

Changing one of these decisions requires an explicit amendment to this module
before Materialization Runtime, Typed Operators, Subject/Event/Lifecycle, or
Public Cutover relies on a replacement contract.

## Owner Confirmation

On 2026-09-01 the owner accepted Ibis-first direct compilation with no Marivo
pushdown optimizer, registered bounded-local execution, no hidden durable
stages, no public explain surface, and the runtime-ranked/compiler-proven
materialization sink seam. No Module 3 owner-choice question remains open. The
owner also accepted the minimal process-loss recovery seam required by Module
4 and the complete Module 5/6 registered-invocation union. Its consumed
Observation Model contract is accepted.

On 2026-09-04 the owner retained the public binding capability but assigned it
to logical source construction. Module 3 therefore consumes captured values
only and has no execution-time source-binding lookup path.

## Final Boundary

The compiler proves how one already-admitted Dataset meaning can execute. It
does not decide what the Dataset means, create public execution objects, or
turn a physical workaround into semantic authority.

High-cardinality work stays in an admitted engine, crosses an existing explicit
materialized boundary, enters one registered hard-bounded local stage, or
fails before unbounded transfer. Within one engine, the complete computation is
one lazy Ibis expression and the datasource owns SQL optimization. Every
unsupported path returns one safe reason and a real repair.
