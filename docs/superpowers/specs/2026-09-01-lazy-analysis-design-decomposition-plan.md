# Lazy Analysis Design Decomposition Plan

Date: 2026-09-01

Revised: 2026-09-04

Status: accepted

## Outcome

Decompose the lazy Analysis redesign into one north-star architecture document,
six independently reviewable module designs, and one public-cutover plan.

The north-star document remains:

- [`2026-09-01-lazy-analysis-dataset-dsl-design.md`](2026-09-01-lazy-analysis-dataset-dsl-design.md)

It owns only product-wide invariants, canonical chains, the module map, and the
global acceptance boundary. Every detailed contract has exactly one child
document as its authority.

This plan does not authorize implementation. It creates bounded design units
that can be reviewed, frozen, implemented, and accepted without treating one
large document as the authority for every public, planner, and runtime layer.

## Why the Design Must Be Split

The north-star currently covers:

- the public Dataset type algebra;
- Entity Population inference and explicit membership control;
- Metric observation and aggregation coordinates;
- private semantic-to-Ibis compilation and execution-boundary resolution;
- materialization, Runs, Artifacts, Evidence, and recovery;
- typed statistical operators;
- SubjectSet, Event, and Lifecycle flows;
- Help, docs, skills, persistence cutover, and acceptance.

These areas do not share one implementation authority or one review audience.
Keeping their detailed contracts together causes four problems:

1. a local decision can silently redefine an unrelated layer;
2. public API, semantic admission, physical planning, and persistence timing
   become difficult to distinguish;
3. an implementation slice cannot point to one frozen source of truth;
4. repeated definitions drift as the design evolves.

The split follows authority boundaries, not repository directories and not an
arbitrary one-document-per-implementation-slice rule.

## Governing Decomposition Rules

### One contract, one owner

Every contract-bearing concept has one owning design. Other documents link to
that owner and state only the dependency they consume.

### The north-star does not duplicate child contracts

After a child design is accepted, detailed signatures, state machines, schemas,
admission matrices, and lowering rules move out of the north-star. The
north-star retains a short invariant and a direct link.

### Public and private abstractions remain separated

Public Dataset values, private planning representations, and persisted runtime
records have different owners. A child design may define their seam but must not
merge them into one public type.

### Design dependencies precede implementation dependencies

A downstream module may design against a reviewed upstream contract. It must
not invent missing upstream behavior merely to make its own examples work.

### Clean-cut remains global

No child may introduce aliases, eager/lazy dual paths, migrations, dual-read
recovery, or duplicate Session/Dataset paths unless the north-star decision is
explicitly changed first.

## Planned Document Set

| Order | Planned document | Sole authority |
| --- | --- | --- |
| 0 | `2026-09-01-lazy-analysis-dataset-dsl-design.md` | north-star invariants and module map |
| 1 | [`2026-09-01-lazy-analysis-dataset-core-design.md`](2026-09-01-lazy-analysis-dataset-core-design.md) | Dataset value model and actions |
| 2 | [`2026-09-01-lazy-analysis-observation-model-design.md`](2026-09-01-lazy-analysis-observation-model-design.md) | Population, observe, shared filtering, coordinates, and shape algebra |
| 3 | [`2026-09-01-lazy-analysis-planner-and-pushdown-design.md`](2026-09-01-lazy-analysis-planner-and-pushdown-design.md) | private Ibis compilation and execution boundaries |
| 4 | [`2026-09-01-lazy-analysis-materialization-runtime-design.md`](2026-09-01-lazy-analysis-materialization-runtime-design.md) | materialization, Run, Artifact, Evidence, and recovery |
| 5 | [`2026-09-01-lazy-analysis-typed-operators-design.md`](2026-09-01-lazy-analysis-typed-operators-design.md) | typed operator admission and outputs |
| 6 | [`2026-09-01-lazy-analysis-subject-event-lifecycle-design.md`](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md) | subject identity and cross-domain analysis |
| 7 | `2026-09-01-lazy-analysis-public-cutover-plan.md` | implementation order and disclosure cutover |

The filenames were reserved by this plan. Each child became authoritative only
when its individual design work was completed and accepted.

All six module designs and the north-star are accepted. The Public Cutover
Plan's owner decisions are resolved, while the plan remains draft pending its
exact Slice 0 inventory and file manifests. Implementation remains
unauthorized until separately requested by the owner.

## Module 1: Dataset Core and Actions

Accepted design with accepted filter-selector amendment:

- [`2026-09-01-lazy-analysis-dataset-core-design.md`](2026-09-01-lazy-analysis-dataset-core-design.md)

### Owns

- the common public Dataset abstraction, paired logical/materialized state
  types, and closed family mechanism;
- public schema, row-contract, shape, and coordinate identity;
- lineage and logical fingerprint boundaries;
- Dataset ownership by one Session;
- the generic Dataset operator protocol shared by logical and materialized
  inputs, with every operator returning a new Logical Dataset;
- `LogicalDataset.execute()` and the materialized-only `show()` and
  `to_pandas()` read contracts;
- bounded `repr`, `show`, and `contract` behavior;
- the executable-Dataset versus terminal-`AgentResult` boundary and the
  `DatasetContract.render()` protocol;
- selector-only Dataset field references used by typed operators;
- the boundary between operators, policy constructors, audit reads, and
  terminal collection.

### Does not own

- Population inference or Metric aggregation semantics;
- generated relational operations;
- storage receipt schemas or Run transaction ordering;
- any specific statistical operator.

### Required decisions

1. whether the common public type is named `Dataset` or `AnalysisDataset`;
2. the exact family and type-parameter representation;
3. the logical/materialized state surface;
4. which facts may exist before execution;
5. how `execute()` returns the paired materialized family without changing row
   meaning;
6. action return contracts and collection bounds;
7. how a materialized Dataset becomes a scan leaf without exposing a plan;
8. how typed operators select an exact current field without exposing a
   column-expression API;
9. which Dataset-related values implement the shared terminal `AgentResult`
   protocol without hiding execution behind `render()`.

### Exit gate

A reviewer can determine whether any public analysis value is a Dataset, what
one row means, which state it is in, which generic actions are legal, and which
facts are forbidden before execution. A reviewer can also distinguish a
selector-only field ref from a DataFrame column, expression, or terminal value.

## Module 2: Population, Observe, and Aggregation Coordinates

Accepted design:

- [`2026-09-01-lazy-analysis-observation-model-design.md`](2026-09-01-lazy-analysis-observation-model-design.md)

### Owns

- Entity Population meaning and primary-key authority;
- default Population inference from Metric and Dimension Entity bindings;
- explicit `session.population(...)` construction;
- the shared `AnalysisPredicate` vocabulary and family-preserving
  `Dataset.where(...)` lifecycle;
- the shared filter-effect vocabulary and state-by-field-resolution authority
  assignment;
- Population membership predicates, Metric row filtering, cohort scope,
  sampling, and target-Population lineage;
- the canonical lazy `session.observe(...)` contract;
- `Session.source_bindings(...)` as construction-time capture of exact
  parameterized non-secret source values;
- the closed `PopulationInput` contract and explicit `population=` admission
  for PopulationDataset, SubjectSet, exact Entity Metric, and entity-outlier
  Candidate shapes;
- direct-membership versus identity-projection input modes, including exact
  identity, uniqueness, completeness, Session, scope, and sampling validation;
- the Entity-grained multi-Metric observation contract;
- `with_dimensions(...)`, `with_time_axis(...)`, `aggregate(...)`, and the
  coordinate/fold meaning consumed by registered `rollup(...)`;
- scalar, Dimension, time, and Dimension-by-time Metric Dataset shapes;
- Population scope versus aggregation-coordinate scope;
- Metric and Dimension compatibility, alignment, and structured repair.

### Does not own

- physical join order or relational lowering;
- Candidate, Event, Lifecycle, or statistical family generated-field
  inventories and filter admission matrices;
- durable sampling receipts;
- correlate, compare, or discovery calculations;
- Event or Lifecycle subject matching.

### Required decisions

1. exact default Population inference and ambiguity rules;
2. explicit Population and sampling family boundaries;
3. the canonical `session.observe(...)` signature;
4. type transitions before and after Entity-axis reduction;
5. Dimension and time-coordinate ordering and identity;
6. non-additive, semi-additive, ratio, cumulative, and component-aware
   aggregation admission;
7. inferred/explicit Population scope conflicts and repairs;
8. one filtering interface with explicit membership-versus-row-subset effects,
   logical type/literal compatibility, field admission, operator ordering, and
   state-by-resolution authority rules across materialization boundaries;
9. exact source-binding capture/identity/redaction semantics and exact
   retained-state rollup admission.

### Exit gate

Without consulting planner or runtime design, a reviewer can determine which
Entity is analyzed, which instances belong to its Population, what one logical
observation row means, how aggregate coordinates are derived, and whether the
requested semantic combination is admissible. The reviewer can also determine
what a filter changes at each stage and which fields, predicate forms, and
operator reorderings are legal.

## Module 3: Ibis Compilation and Execution Boundaries

Accepted design with owner-confirmed module choices:

- [`2026-09-01-lazy-analysis-planner-and-pushdown-design.md`](2026-09-01-lazy-analysis-planner-and-pushdown-design.md)

### Owns

- the private semantic Dataset graph;
- normalization from public Dataset operators into private nodes;
- direct semantic-to-Ibis lowering with no second relational IR;
- filter, projection, join, semi-join, aggregation, rollup, window, rank, limit,
  lag, and statistical-reduction lowering;
- binding of parameterized sources from exact values captured in logical
  source definitions rather than ambient Session state;
- Relationship traversal and fanout-proof consumption;
- common-subexpression elimination within one action;
- the immutable semantic-node lowerer manifest and shared lowerer conformance
  suite;
- deterministic complete-expression Ibis/backend compilation;
- execution-boundary profiles for federation, materialized scan/import,
  single-evaluation fences, writes, snapshots, cancellation, and guarded
  transfer;
- fixed execution-boundary resolution and materialized scan leaves;
- rank-independent feasibility and final physical write placement for
  runtime-ranked materialization sink candidates;
- cross-datasource federation and durable-boundary rules;
- bounded local stages, the fixed first-cutover local execution policy, and safe
  execution diagnostics;
- canonical Arrow exchange schemas, bounded Arrow streaming, and Run-staged
  Parquet exchange placement;
- the closed DuckDB-relational and Python-kernel local implementation registry.

### Does not own

- public Dataset family semantics or Population inference;
- public predicate syntax, family filter effects, or filterable-field
  admission;
- Artifact publication order or Evidence meaning;
- statistical-method product admission.

### Required decisions

1. the exact private semantic-node union, owning semantic lowering
   registrations with Ibis/local routes, and boundary capability vocabulary;
2. normalization and fingerprint stability boundaries;
3. direct Ibis compiler construction, lowerer-manifest assembly, output-contract
   validation, and deterministic compile outcomes;
4. semantic barriers and required backend fences without Marivo query-rewrite
   rules;
5. Population-preserving spine lowering;
6. capability absence versus execution failure;
7. cross-datasource and bounded-local boundaries;
8. safe explain/error/Run projections;
9. single-evaluation placement for sampled Population spines;
10. the no-silent-local-fallback guarantee;
11. the two-phase materialization sink feasibility and final-placement seam;
12. operator compilation from ordered logical/materialized input-authority
    vectors, including maximal single-domain Ibis fusion after mandatory
    materialized scan-leaf substitution;
13. deterministic partial-SQL boundaries, Arrow/Parquet exchange modes, and the
    DuckDB/Python local implementation union with no post-failure fallback.

### Exit gate

One admitted single-engine Dataset chain, including one whose explicit inputs
contain materialized scan leaves, lowers deterministically to one lazy Ibis
expression and one engine query stage, or fails with one bounded structured
reason. Cross-engine graphs use only explicit boundary routes. Every
materialized input is reused without origin replay. No public plan, future,
task, Ibis, or SQL value is introduced, and no Marivo pushdown optimizer or SQL
rewrite registry exists. A registered partial-SQL graph retains one maximal
Ibis prefix per upstream execution domain, crosses only guarded Arrow or
Run-staged Parquet exchanges, and executes only its pre-bound local
implementation.

## Module 4: Materialization Runtime and Authority

Accepted design with owner-confirmed module choices:

- [`2026-09-01-lazy-analysis-materialization-runtime-design.md`](2026-09-01-lazy-analysis-materialization-runtime-design.md)

### Owns

- the `execute()` execution and publication state machine;
- incomplete Run admission before datasource execution;
- execution Runs plus optional inspection and collection read-audit records;
- local, engine, and object Dataset storage receipts;
- materialization sink candidate ranking and final selection after compiler
  feasibility;
- Artifact identity, datasource snapshot authority, and the write-once
  `DatasetExecutionKeyV1 -> artifact_ref` Session binding;
- parameterized-source binding digests in execution identity and exhaustive
  raw-value redaction;
- commit ordering for storage, quality, Evidence, Artifact, and Run success;
- recovery after process restart;
- crash, retry, and concurrent materialization behavior;
- cleanup of compiler-declared action-scoped temporary engine resources;
- execution, journaling, and cleanup of bounded Arrow exchanges, Run-staged
  Parquet, DuckDB workspaces, and Python-kernel buffers;
- immutable scan-leaf recovery;
- the common `DatasetMaterializationContractV1` envelope, resolution,
  invocation, and persistence protocol;
- quality, Evidence, and Finding publication timing;
- the semantic-current, materialized-only, and semantic-or-materialized
  authority-mode vocabulary plus runtime enforcement and revalidation;
- multi-downstream reuse through explicit common materialization.

### Does not own

- public Dataset row meaning or Population inference;
- semantic-to-Ibis lowering or execution-boundary resolution;
- operator-specific calculations;
- public multi-sink scheduling.

### Required decisions

1. Dataset Artifact and Run schemas;
2. durable receipt requirements for each storage family;
3. publication and rollback ordering;
4. write-once execution binding and same-Session recovery;
5. stale semantic and datasource authority handling;
6. materialized-read audit versus committed Evidence;
7. cold recovery and revalidation;
8. immutable engine-managed relation requirements;
9. cleanup and failure recording for compiler-declared action-scoped temporary
   engine resources;
10. strict Evidence atomicity and the cross-store commit marker;
11. same-key concurrency, contender timeout, and single-producer binding
    coordination;
12. process-owner lock, advisory lease, and automatic crash recovery;
13. one common materialization envelope whose family-specific semantic
    registrations are supplied by Modules 2, 5, and 6;
14. the non-authoritative exchange-staging boundary and exact first-cutover
    Parquet receipt contract for local and object Dataset storage.

### Exit gate

A Logical Dataset either recovers or produces one paired Materialized Dataset,
or its producer records a terminal failed Run without publishing partial
Artifact or Evidence authority.

## Module 5: Typed Analysis Operators

Accepted design with owner-confirmed module choices:

- [`2026-09-01-lazy-analysis-typed-operators-design.md`](2026-09-01-lazy-analysis-typed-operators-design.md)

### Owns

- the registered Dataset-to-Dataset operator matrix;
- the Dataset-owned `MetricDataset.rollup(...)` registration and public
  invocation contract, consuming Module 2 fold semantics;
- independently versioned operator variants and capability links to canonical
  Dataset methods and Help;
- structured receiver and operand admission patterns that keep roles, families,
  shapes, coordinates, arity, and value types correlated;
- output Dataset families and row contracts;
- authority-mode assignment for non-filter typed operators;
- exact filterable generated fields and shapes for Candidate and compact
  analytical Dataset families;
- approximation and sample-count disclosure;
- family-specific quality, Evidence, Finding extraction, and retained
  sufficient-statistic contracts consumed by Module 4;
- typed constraint-owned repairs and consumer admission contracts from which
  downstream continuations are derived;
- the boundary that admits a named statistical test only as a Delta affordance
  backed by complete inferential authority; no test is registered initially.

The first matrix covers:

```text
correlate
rank
limit
rollup
compare
attribute
forecast
discover.*
```

### Does not own

- generic Dataset state or Population construction;
- shared predicate syntax, filter effects, ordering, or authority-mode meaning;
- physical lowering implementation;
- Artifact transaction mechanics;
- Event and Lifecycle-specific reducers;
- inferential sampling, assignment, dependence, or multiplicity semantics.

### Required decisions

For every operator variant, freeze:

1. input Dataset families and coordinates;
2. output Dataset family and one-row meaning;
3. required Metric arity and value types;
4. Population compatibility and alignment;
5. approximation, null, constant-input, and insufficient-sample behavior;
6. logical-input and materialized-input authority;
7. bounded result contracts and enough consumer admission authority to derive
   downstream continuations;
8. family-specific `where(...)` field and shape admission without redefining
   the shared predicate syntax, effect, or authority-mode vocabulary;
9. versioned quality, Evidence, Finding extraction, zero-Finding, and retained
   sufficient-statistic contracts for every producing operator;
10. the boundary between explicitly authored reusable Metrics, quality/Evidence
    diagnostics, and typed analysis operators; no generic descriptive
    exploration operator is admitted in the first cutover;
11. exact rollup receiver shapes, arguments, output contracts, authority
    branches, and rejection repairs.

### Exit gate

Every registered operator has one canonical public path and a complete typed
input/output/admission contract plus one materialization contract that Module 4
can invoke without inventing statistical meaning. No operator returns a
detached result or selection object.

## Module 6: SubjectSet, Event, and Lifecycle

Accepted design with owner-confirmed module choices:

- [`2026-09-01-lazy-analysis-subject-event-lifecycle-design.md`](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md)

### Owns

- SubjectSet as a sibling Dataset family admitted through the shared
  `population_input` contract's direct-membership mode;
- logical identity-producing definitions and materialized identity rows;
- same-plan semi-join consumption;
- identity privacy in metadata, logs, cards, Evidence, and errors;
- `session.events.match(..., population=None)`;
- Event journey, funnel, and time-to-event Dataset families;
- `session.lifecycle.replay(..., population=None)`;
- Lifecycle history, distribution, transition, dwell, and violation families;
- SubjectSet output authority and Event/Lifecycle-owned typed
  `select_subjects` registrations;
- exact `where(...)` admission or rejection and filterable generated fields for
  SubjectSet, Event, and Lifecycle Dataset families;
- exact matching assignment and source-origin completeness authority;
- explicit removal of observed occurrence-range inspection from the public
  Event surface;
- family-specific quality, validation-output, Evidence, Finding, and retained
  private-state materialization contracts consumed by Module 4;
- cross-domain loops through explicit `population=PopulationInput`;
- SubjectSet-specific cold-recoverable selection authority.

### Does not own

- generic Population inference for Metrics;
- generic Dataset materialization mechanics;
- ordinary Metric operator admission;
- shared predicate syntax, filter effects, ordering, or authority-mode meaning;
- private semi-join implementation details.

### Required decisions

1. the exact SubjectSet subtype/refinement relationship;
2. which Event/Lifecycle shapes retain sufficient Entity identity for typed
   subject selection;
3. logical versus materialized cohort authority;
4. Event/Lifecycle source inference and shared `PopulationInput` admission;
5. row contracts for each Event and Lifecycle Dataset family;
6. follow-up scope and temporal authority;
7. privacy-preserving persistence and recovery;
8. legal Metric/Candidate population-input and
   Event/Lifecycle-to-SubjectSet loops;
9. family-preserving row filters versus explicit source population admission.

### Exit gate

One admitted population input can move through Metric, Event, and Lifecycle
analysis without collecting identities locally, losing Entity authority, or
creating duplicate source APIs.

## Public Cutover Plan

Draft implementation and disclosure plan with accepted owner decisions:

```text
2026-09-01-lazy-analysis-public-cutover-plan.md
```

This is an implementation and disclosure plan, not another semantic authority.

### Owns

- exact public removals and replacements;
- Frame/Result to Dataset exports;
- eager to lazy `session.observe(...)` cutover;
- duplicate Session-operator removal;
- persistence schema-generation replacement;
- test deletion, replacement, and new contract suites;
- Help, capability registry, cards, and structured-error updates;
- packaged skill and current English/Chinese documentation updates;
- real-agent journey acceptance;
- clean removal of aliases, migrations, and dual paths.

### Entry gate

All six module designs are reviewed, their seams agree, and the north-star links
to the accepted owner for every detailed contract.

### Exit gate

One ordered implementation plan maps every public or persisted contract change
to code ownership, tests, disclosure surfaces, removal work, and terminal
acceptance evidence.

## Contract Ownership Registry

| Contract | Owner |
| --- | --- |
| Dataset family, common state, and action semantics | Dataset Core |
| Entity Population and default inference | Observation Model |
| Metric Dataset coordinate algebra | Observation Model |
| parameterized source binding capture and logical definition identity | Observation Model |
| parameterized source lowering from captured typed values | Ibis Compiler and Execution Boundaries |
| parameterized source digest, execution identity, and redaction | Materialization Runtime |
| shared predicate syntax, filter effects, ordering, and filter authority assignment | Observation Model |
| private semantic/physical nodes, semantic lowering manifest, boundary capability ids, boundary resolution, Arrow/Parquet exchange placement, DuckDB/Python local registry, and fixed bounded-local policy | Ibis Compiler and Execution Boundaries |
| private exchange execution, staging-resource journals, and cleanup | Materialization Runtime |
| `ParquetDataContractV1` writer/reader semantics shared by private staging and durable file receipts | Materialization Runtime |
| final local/object Parquet receipts and Artifact publication | Materialization Runtime |
| authority-mode vocabulary and runtime enforcement | Materialization Runtime |
| `DatasetMaterializationContractV1` envelope, invocation, and persistence timing | Materialization Runtime |
| Population and Metric materialization registrations | Observation Model |
| statistical and Candidate materialization registrations | Typed Operators |
| SubjectSet, Event, and Lifecycle materialization registrations | Subject/Event/Lifecycle |
| Run, Artifact, receipts, recovery, and Evidence timing | Materialization Runtime |
| non-filter typed operator admission and output rows | Typed Operators |
| Metric Dataset `rollup(...)` public registration and invocation | Typed Operators |
| Metric rollup coordinate and retained-fold semantics | Observation Model |
| SubjectSet identity and Event/Lifecycle families | Subject/Event/Lifecycle |
| removal of observed Event occurrence-range inspection | Subject/Event/Lifecycle |
| removals, Help, skills, docs, and rollout order | Public Cutover Plan |

When a decision spans two owners, the documents define one directional seam.
They must not both define the same state or algorithm.

## Dependency Graph

```text
North-star invariants
        |
        v
Dataset Core
        |
        v
Observation Model
        |
        v
Ibis Compiler and Execution Boundaries
        |
        +-------------------+
        |                   |
        v                   v
Materialization Runtime   Typed Operators
        |                   |
        +---------+---------+
                  |
                  v
       Subject / Event / Lifecycle
                  |
                  v
          Public Cutover Plan
```

Typed Operators and Subject/Event/Lifecycle may begin in parallel after Dataset
Core and Observation Model are frozen. Their final acceptance waits for the
  compiler and Materialization seams they consume.

## Design Work Sequence

### Phase 0: Slim and freeze the north-star

- retain only product-wide invariants and canonical chains;
- add the module ownership map and links;
- remove detailed contracts only after their child owner is accepted.

Exit: the north-star no longer appears to own child signatures, schemas, state
machines, or algorithms.

### Phase 1: Freeze Dataset Core

- decide common type naming and state;
- freeze action contracts;
- freeze public/private and logical/materialized boundaries.

Exit: every later design can reference one stable Dataset protocol.

### Phase 2: Freeze Observation Model

- decide Population inference and explicit override;
- freeze Entity-grained observe;
- freeze aggregation-coordinate transitions;
- freeze semantic admission and repair ownership.

Exit: compiler and operators receive one stable logical input model.

### Phase 3: Freeze Compiler and Materialization seams

- define the private semantic-node input from public Datasets;
- define executable output consumed by actions;
- freeze direct Ibis lowering, lowerer registration, deterministic compile
  outcomes, boundary resolution, and invariant validation;
- define materialization barriers and scan-leaf receipts;
- freeze the compiler-owned bounded-local policy and runtime guard enforcement;
- freeze Arrow as the exchange schema, bounded Arrow as the one-pass transport,
  Run-staged Parquet as the rewindable private representation, and DuckDB plus
  registered Python kernels as the first-cutover local implementation union;
- define cleanup for compiler-declared action-scoped temporary resources;
- divide failure ownership across admission, lowering, execution, and commit.

Exit: no runtime state is ambiguously owned by compiler and Session Store.

### Phase 4: Freeze operator and cross-domain matrices

- complete the ordinary analysis operator matrix;
- complete SubjectSet, Event, and Lifecycle family matrices;
- prove all outputs remain Datasets;
- prove logical/materialized authority at every operator seam.

Exit: every public capability has one input/output/admission owner.

### Phase 5: Produce the cutover plan

- inventory all current code and disclosure surfaces;
- map every removal and replacement to an accepted child contract;
- order implementation by usable vertical journeys;
- define tests, docs, skills, and real-agent evidence for each slice.

Exit: implementation can proceed without making new product decisions inside a
code slice.

## Review Gates

### Boundary gate

Verify that a document owns each decision under the registry and does not
restate another module's contract.

### Public contract gate

Every public signature, Dataset family, action, and repair is closed, typed,
Help-resolvable, and free of an equivalent alternate path.

### Runtime authority gate

Every row-dependent fact identifies whether it is logical, inspection-scoped,
collection-scoped, or committed materialized authority.

### Engine-execution gate

Every high-cardinality intermediate stays in the datasource, crosses an
explicit durable materialization boundary, or fails before an unbounded local
transfer. Every admitted bounded local path has one maximal Ibis prefix per
upstream execution domain, exact Arrow-compatible exchange contracts, one
pre-bound DuckDB or Python implementation, and no hidden Materialized Dataset.

### Vertical-journey gate

Every module contains at least one end-to-end example that exercises its owned
seam and names the evidence required during implementation.

### Clean-cut gate

Every replacement names the removed public and persisted paths. A child design
is not accepted while compatibility behavior remains implicit.

## Cross-Document Change Protocol

When a later decision changes a frozen contract:

1. update the owning child design first;
2. state the downstream interfaces affected;
3. update the north-star only if a global invariant changed;
4. update dependent documents only at the consumed seam;
5. update the cutover plan after semantic owners agree;
6. never patch conflicting definitions into several documents simultaneously.

An unresolved cross-document conflict blocks implementation of the affected
slice. Implementation must not choose one interpretation silently.

## Global Acceptance

The decomposition is complete only when:

1. all six module documents exist and have reviewed status;
2. every registered contract has exactly one owner;
3. the north-star contains no duplicate detailed signature, schema, state
   machine, or lowering algorithm;
4. all child documents use the same Dataset family and coordinate vocabulary;
5. inferred and explicit Population paths converge on one observation contract;
6. no child exposes a public plan, future, task, or multi-sink abstraction;
7. `execute()` remains the only public logical-to-materialized execution
   boundary, while both logical and materialized inputs support downstream
   operators that return Logical Datasets;
8. compiler and runtime agree on materialized scan-leaf authority;
9. typed operators and cross-domain flows return only Dataset families;
10. compiler and runtime agree that Arrow/Run-staged Parquet exchanges are
    private, while only the complete root output can receive a durable Parquet
    or engine receipt and become a Materialized Dataset;
11. DuckDB is the first-cutover local relational executor, Python kernels are
    exact registered non-relational implementations, and pandas/Polars are not
    generic fallback domains;
12. the cutover plan maps each change to implementation, tests, Help, skills,
    and current English/Chinese docs;
13. implementation slices can be accepted without reopening an unspecified
    architecture decision.

## Final Boundary

```text
North-star design
  = why, invariants, canonical chains, and module map

Module designs
  = one authoritative contract per architectural boundary

Public cutover plan
  = how accepted contracts replace the current product

Implementation slices
  = code and evidence against frozen module contracts
```

This hierarchy keeps the lazy Analysis redesign coherent without making one
document, one implementation slice, or one subsystem responsible for the entire
product transition.
