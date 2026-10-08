# Lazy Analysis Dataset DSL Design

Date: 2026-09-01

Revised: 2026-09-07

Status: accepted

## Outcome

`marivo.analysis` becomes one immutable, typed, lazy Dataset algebra with an
explicit execution boundary:

```text
analysis operator:
    (LogicalDataset[A] | MaterializedDataset[A]) x Typed Inputs
      -> LogicalDataset[B]

execute action:
    LogicalDataset[A] -> MaterializedDataset[A]

read action:
    MaterializedDataset[A] -> terminal projection
```

Every public analysis operator returns a Dataset. Logical plans, Ibis
expressions, SQL, execution stages, tasks, futures, and storage receipts remain
private. The public value is always the governed analytical dataset, never the
machinery that may later produce it.

Operators perform deterministic local normalization and admission only.
Datasource work begins only at the explicit logical action:

```text
execute()       durable governed execution
```

`execute()` returns the materialized state type paired with the logical
Dataset's family. It preserves family, shape, schema, and row meaning while
changing backing and authority. `show()` and `to_pandas()` exist only on a
Materialized Dataset and read its committed immutable Artifact; their preview
and collection bounds constrain projection into the caller, never datasource
execution of a logical definition.

Within one Session, the first successful execution of one exact
`DatasetExecutionKeyV1` binds that key to one Artifact. Reconstructing the same
logical definition in a later process and calling `execute()` recovers that
Artifact without rerunning datasource SQL. This write-once binding, not Python
object identity or a mutable cache, is the durable execution identity.

The execution key adds only the common materialization protocol version to the
canonical definition fingerprint; the Store supplies Session scope. Artifact
selection opens metadata only. Row reads and operators validate their actual
payload dependencies, and `session.revalidate(ref)` explicitly inspects the full
Artifact/parts/Findings without comparing source or semantic freshness. Compiler
diagnostics and harmless garbage deletion do not gate successful publication.

Dataset Core's fingerprint includes whether semantically significant
realizations are shared or independent. Reconstructing the same normalized
sharing topology preserves identity across processes; changing that topology
changes the definition even when the branch definitions are otherwise equal.
Graph-local handles establish actual sharing but their raw values never enter
the fingerprint. Materialized inputs remain exact Artifact leaves. The compiler
and Runtime add no separate sharing fingerprint or persisted occurrence table.

## Product-Wide Invariants

### Dataset is the analysis value

- `Dataset` is the common public abstraction name.
- Every row-bearing public analysis value belongs to a sealed nominal Dataset
  family with complete row and row-set contracts before execution.
- Every family registers paired logical and materialized public state types.
  Both are immutable, share one family, row contract, and row-set contract, and are owned by one
  Session; their method surfaces are state-specific.
- Operators compose only governed Dataset values and typed inputs. They do not
  accept arbitrary pandas, SQL, Ibis, callables, or physical columns.
- Selector-only field references may name a field already present in the
  current row-contract schema. They are not columns, expressions, or values.
- Policy values, semantic refs, predicates, contracts, audit reads, structured
  errors, bounded previews, and terminal pandas objects are supporting values,
  not analysis-operator outputs.

The exact value, family, row-contract, row-set-contract, state, action, and selector contracts are
owned by the
[`Dataset Core design`](2026-09-01-lazy-analysis-dataset-core-design.md).

### Population is membership authority, not the universal first syntax

Marivo's governed Entity is already the target of a Metric or Dimension. An
Entity Population therefore means the eligible instances of that Entity; it is
not a separate abstraction at the same level as scalar, Dimension, or time.

`session.observe(...)` infers an all-eligible, unscoped Population only when the
Metric graph has one exact common non-versioned computation-root Entity. A
versioned root requires the Observation Model's explicit scoped membership
resolution. An explicit Population
selects the analysis Entity before each component's safe path is validated;
computation roots, analysis unit, and reporting coordinates remain distinct.

The Semantic Object Model defines Entity `primary_key` as stable identity;
snapshot/validity coordinates describe its historical representations. Source
version-row uniqueness and temporally resolved identity uniqueness are separate
checks. Population, Event participants and StateModel subjects consume the same
Entity identity without manufacturing a second key or treating versions as
independent subjects.

Membership selection time and Metric observation time have independent owners.
Explicit `population=` may be combined with observe-level `time_scope` and
`time_dimension`, including disjoint cohort and behavior windows. No source
inherits a membership window as its observation window.

Scalar, Dimension, time, and Dimension-by-time describe coordinates over members.
The lazy coordinate chain retains Entity until `aggregate()` removes it, but
requires no intermediate Entity execution. Row filtering binds exact coordinate
contributions; original Population lineage is never an implicit denominator.
Ratio/mean/weighted-mean state enables continued exact aggregation after reads.
`rollup(drop_time=True)` may remove time only under a registered temporal fold
and must preserve selected/partial-period coverage.

Coordinate folds require a proven contribution partition and preserve the
Metric's spatial/temporal calculation order. A base additive label does not
permit summing overlapping Dimension buckets or separately computed temporal
peaks. Semantic definitions supply the intrinsic facts; the Observation Model
derives the exact operation and retained-state admission.

The exact Population, filtering, observation, coordinate, aggregation, and
compatibility contracts are owned by the
[`Observation Model design`](2026-09-01-lazy-analysis-observation-model-design.md).

### Operator order is semantic

`where(...)`, coordinate declarations, aggregation, statistical reductions,
ranking, limiting, selection, and domain reducers are ordered Dataset
transformations. The planner may apply equivalence-preserving normalization,
but it may not reorder them across a membership, row-subset, authority, or
materialization boundary in a way that changes meaning.

`where(...)` uses one closed predicate vocabulary and a family-owned effect:
it may refine Population membership, subset current rows, or be rejected. It
does not expose a general expression language. `rank(...)` establishes a
governed deterministic order; `limit(...)` consumes an already governed order
and never authorizes an unordered backend limit.

### Laziness pushes eligible work to Ibis and keeps the suffix in pandas

A complete Dataset chain preserves Population, coordinates, aggregation,
filtering and retained-state meaning until explicit execution. At `execute()`,
the compiler composes as many contiguous eligible operations as possible into
Ibis queries in their source domains. Eligibility comes from the exact method's
registered implementation and tested datasource adapter before data work. When
source lowering is known to be absent or ineligible, only that method's declared
exact pandas continuation may start a local suffix. Every dependent local
operation then remains in pandas. There is no upload back to the source, cost
optimizer, or implementation switch after a compile or execution failure.

The accepted [Source Pushdown and Pandas Execution design](2026-09-01-lazy-analysis-planner-and-pushdown-design.md)
owns the detailed execution rules. The 2026-09-07 amendment replaces the former
fixed engine/Python categories and internal DuckDB execution throughout this
design set. It does not authorize arbitrary local evaluation of semantic graphs.

### Authority admission and execution support are separate

Every operator has one analytical meaning over its admitted Logical and
Materialized inputs. Partial materialization is not another public overload.
A Materialized operand contributes its exact immutable rows and required parts;
origin lineage is never executable, and a logical input is never silently
replaced by a similar stored Artifact.

Source-owned Population, Metric evaluation, current semantic enrichment,
Event matching, Lifecycle replay and private identity/distribution preparation
require their registered source implementation and compatible datasource domain.
Missing source-required support fails before data work. An exact retained-input
fold or result-only method may instead admit a pandas continuation; its contract
must already contain all required rows and state. Independent source branches
may feed declared local operand roles under one combined input budget. This is
not permission for arbitrary cross-source joins or local identity collection.

Engine Artifacts are exact source scan leaves. Local/object Artifacts use
PyArrow reads into bounded pandas inputs, including every required retained part.
No Artifact reader replays an origin graph or registers a DuckDB relation.
An Artifact too large for the complete local input or method budget fails; a
new `execute()` call does not make an incompatible source or oversized local
input executable.

### One action can contain source queries and a pandas suffix

Current forecast methods use pandas over complete governed time series.
Shapley and Entity-pair correlation retain their required source preparation;
already-local non-identity correlation inputs may align pairs in pandas under
the same exact contract. Other admitted result operations prefer Ibis until the
first source ineligibility or local input, then pass internal DataFrames
directly between pandas functions.

```text
eligible Ibis source chain -> selected writer -> final Artifact
eligible Ibis source chain -> bounded Arrow -> pandas suffix -> final Artifact
local/object Artifact -> PyArrow -> bounded pandas suffix -> final Artifact
independent source results -> combined bounded Arrow inputs -> pandas compare
                          -> pandas attribution -> final Artifact
```

The last path is allowed only for the exact compare/attribution contracts and
complete inputs they declare. If compare and attribution are source-eligible
in one compatible domain, they remain in Ibis. DuckDB is an ordinary datasource,
not an internal analysis executor. pandas, Arrow and numerical-library values
remain private and never become public Dataset operator inputs.

One `execute()` may contain queries, required realization fences, pandas
functions, validation and write steps. There is no global one-SQL guarantee or
general CSE requirement. Required shared-sample realization remains mandatory.
Arrow validates source-to-local and storage boundaries, not every adjacent local
operator. Runtime guards complete local inputs, decoded bytes, required retained
state, intermediate growth, numerical size and cancellation. Temporary buffers
and staging files never acquire an Artifact ref, Evidence or reuse identity.
Runtime supplies one configured storage target; the writer validates it or fails
without selecting another sink. Only the complete root output and its registered
retained parts become the Materialized Dataset.

### Execution is explicit reuse and durable authority

Logical Datasets expose `execute()` but not `show()` or `to_pandas()`.
`execute()` is the sole public transition that may run datasource SQL and
publish a recoverable Artifact, Evidence, quality results, retained private
state, and a successful Run. Failed execution publishes neither a partial
Artifact nor partial Evidence.

Materialized Datasets expose `show()` and `to_pandas()`. Both read only the
committed immutable backing: `show()` projects a bounded deterministic preview
for agent context, while `to_pandas()` performs a guarded complete transfer.
Neither action reruns the origin semantic graph or publishes another Artifact.

The Session persists a write-once execution binding keyed by the exact logical
definition, semantic dependencies, ordered input authority, family, row
contract, and implementation contracts. A binding hit is exact Artifact
recovery, not a new execution Run. Datasource changes after the first execution
do not redirect that Session-local Dataset identity to new rows; current data
requires a new Session or an explicitly different analytical definition.

The common materialization envelope, action lifecycle, transaction ordering,
reuse, concurrency, recovery, and materialized scan-leaf rules are owned by the
[`Materialization Runtime design`](2026-09-01-lazy-analysis-materialization-runtime-design.md).
Family modules own the semantic quality, Evidence, Finding, validation, and
retained-state registrations that the runtime invokes.

### Typed operators stay inside the Dataset algebra

Correlation, ranking, limiting, comparison, attribution, forecasting, and
discovery are Dataset-owned operators with closed input and output contracts.
They consume the current Dataset rows and authority available at that point in
the chain. A materialized input is not permission to reach through the
checkpoint and recompute from current semantic sources.

The accepted 2026-09-07 analytical-correctness amendment requires complete
comparison/screening coordinates on Attribution and Candidate rows,
component-specific allocation proof, and model-specific forecast innovations
and prediction intervals. Discovery remains a screening lead, correlation a
descriptive association, and attribution a reconciled numerical decomposition.
Lazy composition does not supply an inferential design or causal authority.

Statistical tests are not a generic operator family. A theory-valid test may
appear only as an exact affordance on a Delta whose contract already carries
the required inferential unit, population/design, dependence, missingness, and
multiplicity authority. No first-cutover Delta has that authority, so the
initial public algebra exposes no statistical-test method or result family.

The exact operator variants, capability links, invocation/output contracts,
result families, generated fields, method semantics, action requirements,
materialization links, and rules for deriving continuations from consumer
admission are defined by the
[`Typed Operators design`](2026-09-01-lazy-analysis-typed-operators-design.md).

### PopulationInput is the governed cross-domain boundary

An exact identity-bearing Entity Metric or entity-outlier Candidate Dataset may
be supplied directly through `population=`. It remains its original Dataset
family; the consuming source projects its retained exact Entity identity under
the shared `PopulationInput` contract. The call to the source is the explicit
decision to treat the current rows as membership.

Event journey and Lifecycle history datasets instead use typed
`.select_subjects(selection)` because their membership semantics depend on
domain structure, temporal completeness, and censoring. Those operations
produce the same `PopulationDataset` family as explicit Entity roots. Selection
truth belongs to the domain producer; common membership filtering and input
admission belong to the Observation Model. Logical inputs stay in the same plan;
materialized inputs are immutable identity scan leaves. Identity rows are never
collected locally merely to connect domains.

The exact domain selection, Event, Lifecycle, privacy, matching, and
cross-domain contracts are owned by the
[`Subject, Event, and Lifecycle design`](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md).

Canonical Lifecycle history includes the retained legal-transition,
subject-coverage and violation records required by its reducers. Positive
state intervals alone do not encode every same-time transition or every
coverage-unknown subject. Duration outputs state their selected-step risk set or
completed clipped-fragment meaning explicitly. Materialization preserves that
meaning and all required facts without replay.

## Public Algebra

The product has four kinds of public operation:

| Kind | Canonical owner | Result |
| --- | --- | --- |
| source construction | `Session` | Logical Dataset |
| analysis transformation | admitted Dataset family | Logical Dataset |
| governed execution | `LogicalDataset.execute()` | paired Materialized Dataset |
| bounded inspection | `MaterializedDataset.show()` | `None` |
| guarded collection | `MaterializedDataset.to_pandas()` | isolated pandas value |

The canonical source roots are:

- `session.population(...)` for explicit Entity membership;
- `session.observe(...)` for Metric observation;
- `session.events.match(...)` for Event journeys;
- `session.lifecycle.replay(...)` for Lifecycle histories;
- materialized Dataset recovery through the Session runtime.

There is no duplicate source method on PopulationDataset and no
Session-level version of a Dataset-owned downstream operator.

The main chain is:

```text
Session source
  -> typed Dataset transformations
  -> optional typed selection and cross-domain source
  -> execute
  -> show / to_pandas

or

Materialized Dataset
  -> one or more lazy downstream Dataset chains
  -> execute
  -> show / to_pandas
```

## Canonical Journeys

### Entity features without an explicit Population

Metrics already bind the target Entity, so the common case starts directly at
observation:

```python
features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
    time_scope=window,
)

checkpoint = features.execute()

association = checkpoint.correlate(method="spearman").execute()
outliers = (
    checkpoint
    .metric(cpu_seconds)
    .discover.entity_outliers(limit=25)
    .execute()
)

association.show()
outliers.show()
```

Re-executing this script in the same Session recovers `checkpoint`,
`association`, and `outliers` from their persisted execution bindings. Appending
a new downstream computation executes only the new definition.

### Explicit prior-Session Artifact selection

```python
prior = prior_session.observe(metrics=[scanned_bytes, cpu_seconds]).execute()
selected = current_session.artifact(prior.state.artifact_ref)
association = selected.correlate(method="spearman").execute()
```

The Agent chooses `prior` by its exact Artifact ref. Its original Session,
producer, storage, and Findings remain unchanged; only the new association is
produced in `current_session`, with an input edge to the original Artifact.
Selection performs no copy, local registration, age limit, source freshness
query, or reuse approval. Execution-key matching stays Session-local; foreign
Logical Datasets are not carried into another Session's computation.

### Explicit Population, ranking, and population reuse

```python
population = (
    session.population(query_execution, time_scope=window)
    .where(population_predicate)
    .sample(mv.engine_sample(target_rows=100_000, seed=42))
)

features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
    population=population,
)

ranked = features.rank(
    by=features.fields.metric(peak_memory_bytes),
    order="descending",
).limit(100)

selected_queries = ranked.execute()
```

The Population controls eligible query identities. Ranking and limiting subset
the observed rows. `selected_queries` remains a Metric Dataset and can later be
passed explicitly through `population=` because its exact Entity identity is
still unique, complete, and governed.

### Aggregation coordinates

```python
daily_region = (
    session.observe(metrics=[revenue], time_scope=window)
    .with_dimensions(region)
    .with_time_axis(order_created_at, grain=mv.grain("day"))
    .aggregate()
)

forecast = daily_region.forecast(
    horizon=mv.periods(14),
    model=mv.seasonal_naive(periods=7),
).execute()

forecast.show()
```

The Population still denotes eligible orders. Region and day are coordinates;
`aggregate()` removes the order Entity axis and computes the governed Metric at
those coordinates.

### Selection into Event analysis

```python
selected = features.where(resource_predicate).execute()

journeys = session.events.match(
    pattern=query_failure_pattern,
    cohort_window=cohort_window,
    completion_through=followup_end,
    matching=mv.first_per_subject(),
    population=selected,
)

failure_funnel = journeys.funnel().execute()
failure_funnel.show()
```

No identity DataFrame crosses the boundary. The materialized Metric Dataset is
the exact reusable population input, remains a Metric Dataset, and the Event
result remains an Event Dataset.

## Module Authority Map

| Module | Sole detailed authority |
| --- | --- |
| [1. Dataset Core](2026-09-01-lazy-analysis-dataset-core-design.md) | Dataset value, family, row and row-set contracts, state, actions, selectors |
| [2. Observation Model](2026-09-01-lazy-analysis-observation-model-design.md) | Population, predicates, observation, coordinates, aggregation |
| [3. Source Pushdown and Pandas Execution](2026-09-01-lazy-analysis-planner-and-pushdown-design.md) | private semantic graph, source-preferred Ibis, exact pandas suffix and Arrow boundaries |
| [4. Materialization Runtime](2026-09-01-lazy-analysis-materialization-runtime-design.md) | Runs, private exchange staging, durable receipts, Artifacts, Evidence, recovery |
| [5. Typed Operators](2026-09-01-lazy-analysis-typed-operators-design.md) | operator registry, admissions, output rows, statistical semantics |
| [6. Subject, Event, and Lifecycle](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md) | domain selection into Population, identity authority and privacy |

The
[`Design Decomposition Plan`](2026-09-01-lazy-analysis-design-decomposition-plan.md)
defines the dependency and change protocol. A separate Public Cutover Plan must
map these accepted contracts to implementation, removals, tests, Help, skills,
and current English and Chinese documentation before implementation begins.

## Clean Replacement Boundary

This is a breaking replacement of the eager Analysis surface. The cutover must
leave:

- no eager/lazy overloads or compatibility aliases;
- no Frame/Result and Dataset dual algebra;
- no duplicate Session-level and Dataset-level path for one operator;
- no migration or dual-read support for obsolete persisted analysis records;
- no public plan, task, future, SQL, Ibis, receipt, or multi-sink abstraction;
- no silent local fallback for high-cardinality or identity-bearing work.

The earlier
`Unified Correlation and Cross-Entity Population` proposal
is superseded and non-normative. Its old `session.correlate(...)`,
`MetricFrame[cross_section]`, `entity_grain=`, and `CorrelationPopulation`
surfaces must not be implemented.

## Global Acceptance Boundary

The architecture is internally complete when all of the following are true:

1. every row-bearing analysis operator accepts the registered Logical and
   Materialized input states and returns one registered Logical Dataset family,
   with semantic admission separate from source support and exact local-input admission;
2. every Dataset exposes complete logical row and row-set contracts before execution;
3. default observation infers one exact Entity Population, while explicit
   Population changes membership rather than coordinate meaning;
4. among state-specific actions, Logical Datasets expose only `execute()` and
   Materialized Datasets expose `show()` plus `to_pandas()`; both states still
   expose registered downstream operators, and reads never replay the logical
   origin;
5. logical and materialized inputs have concrete input checks with no origin replay;
6. one admitted chain pushes every contiguous eligible operation into its
   source through Ibis, then uses only a predeclared exact pandas suffix;
   source-required work fails before data work if support is absent;
7. a materialized Dataset can become a scan leaf without exposing private
   planning or storage values;
8. every producing family supplies the semantic materialization registration
   consumed by the common runtime envelope;
9. the shared `PopulationInput` boundary connects Metric, Event, and Lifecycle
   analysis without converting Dataset families or locally collecting
   identities;
10. no contract has more than one owning module and the north-star does not
    duplicate detailed signatures, schemas, state machines, or lowering rules;
11. exact same-Session logical definitions recover their write-once execution
    binding across process and script reruns without datasource execution;
12. execution-binding hits create no duplicate Run, while a changed analytical
    definition, including changed realization sharing, receives a different
    execution key and Artifact;
13. source support selects the deterministic Ibis/pandas boundary before data
    work; dependent local operations stay local, exact multi-input budgets are
    combined, and no internal DuckDB executor, generic federation, failure
    fallback or automatic upload exists;
14. private exchanges and local workspaces never become Dataset authority, and
    only root primary data and registered retained parts can receive durable Parquet or engine
    receipt and become Materialized;
15. the Public Cutover Plan covers removals, code ownership, tests, disclosure
    surfaces, persisted clean replacement, and end-to-end agent acceptance.

The 2026-09-07 Ibis-pushdown and pandas-suffix amendment is accepted at design level. Its
implementation remains subject to the Public Cutover Plan's slice and evidence
gates; documentation acceptance is not Runtime implementation evidence.
