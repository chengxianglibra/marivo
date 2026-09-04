# Lazy Analysis Dataset DSL Design

Date: 2026-09-01

Revised: 2026-09-03

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

## Product-Wide Invariants

### Dataset is the analysis value

- `Dataset` is the common public abstraction name.
- Every row-bearing public analysis value belongs to a sealed nominal Dataset
  family with a complete row contract before execution.
- Every family registers paired logical and materialized public state types.
  Both are immutable, share one family and row contract, and are owned by one
  Session; their method surfaces are state-specific.
- Operators compose only governed Dataset values and typed inputs. They do not
  accept arbitrary pandas, SQL, Ibis, callables, or physical columns.
- Selector-only field references may name a field already present in the
  current row contract. They are not columns, expressions, or values.
- Policy values, semantic refs, predicates, contracts, audit reads, structured
  errors, bounded previews, and terminal pandas objects are supporting values,
  not analysis-operator outputs.

The exact value, family, row-contract, state, action, and selector contracts are
owned by the
[`Dataset Core design`](2026-09-01-lazy-analysis-dataset-core-design.md).

### Population is membership authority, not the universal first syntax

Marivo's governed Entity is already the target of a Metric or Dimension. An
Entity Population therefore means the eligible instances of that Entity; it is
not a separate abstraction at the same level as scalar, Dimension, or time.

`session.observe(...)` infers the default Entity Population from the requested
Metric and Dimension bindings. The caller supplies an explicit Population only
to change membership, scope, cohort, or sampling. Ambiguous or incompatible
Entity anchors fail locally with a structured repair.

Scalar, Dimension, time, and Dimension-by-time describe aggregation
coordinates over a Population. They never create a different Population. The
Entity axis remains present until explicit `aggregate()` removes it.

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

### Laziness exists to preserve datasource execution

A complete Dataset chain is available before execution, allowing Marivo to:

- lower semantic joins, filtering, coordinates, aggregation, ranking, windows,
  and statistical operations into the capable datasource;
- share one Population spine and avoid independently observed Metric frames;
- keep high-cardinality Entity rows and identities inside governed engines;
- select bounded execution boundaries only when a single engine cannot execute
  the whole chain;
- fail closed instead of silently collecting unsupported work into pandas.

Marivo does not expose or build a public query planner, and it does not become a
general cost-based optimizer. The accepted semantic graph, Ibis lowering,
capability, boundary, and guarded-transfer contracts are owned by the
[`Planner and Pushdown design`](2026-09-01-lazy-analysis-planner-and-pushdown-design.md).

### Every operator supports logical fusion and explicit checkpoint reuse

Every operator has one analytical contract but must be executable from both
input topologies:

1. when all of its inputs are logical and one execution domain can address the
   complete upstream closure, Marivo lowers the operator and that closure into
   one lazy Ibis expression and gives the datasource one complete SQL query;
2. when one or more inputs are materialized Datasets, each such input is a
   mandatory immutable scan leaf. Marivo reuses those exact rows, then fuses
   the operator, remaining logical inputs, and downstream work into one Ibis
   expression wherever one admitted domain can scan or import every input.

For a multi-input operator, partial materialization is ordinary rather than a
special API: its ordered input-authority vector may contain any admitted mix of
logical definitions and materialized Artifact refs. The operator does not own
two calculation implementations. Its semantic owner defines one row meaning
and the compiler binds each input from its authority token before applying one
equivalent lowerer.

"Already materialized" means that the caller constructed the downstream chain
from the returned or recovered Materialized Dataset. The planner does not
search the Session for a similar Artifact and replace an explicitly logical
input. Such substitution could silently change source snapshot and semantic
authority. Once a Materialized Dataset is an input, however, its origin graph
is never replayed merely because the current datasource could regenerate it.

### Partial SQL execution remains one private execution

When one admitted operator cannot execute in the datasource but has an exact
registered local implementation, one `execute()` may contain several private
stages:

```text
maximal Ibis expression per upstream execution domain
  -> bounded private exchange
  -> registered local calculation
  -> final Dataset validation and publication
```

The exchange is not a Dataset value. Its authority is the exact physical-stage
schema and input projection, not an Artifact ref. Arrow is the canonical
in-process data contract. A rewindable or engine-exported exchange may use
Run-scoped Parquet staging, but that file is unpublished, has no Evidence or
reuse authority, and is cleaned on every terminal path. Only the complete root
output of `execute()` may become the paired Materialized Dataset.

DuckDB is the first-cutover local relational executor over Arrow or Parquet.
Algorithms that are not relational use only an operator-registered Python
kernel with Arrow inputs and outputs; pandas may be an internal library adapter
inside that kernel, never a generic execution fallback. Polars is not a
first-cutover execution domain.

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
produce `SubjectSet`, a sibling Dataset family admitted directly through the
same `PopulationInput` contract. Logical inputs stay in the same plan;
materialized inputs are immutable identity scan leaves. Identity rows are never
collected locally merely to connect domains.

The exact SubjectSet, Event, Lifecycle, privacy, matching, selection, and
cross-domain contracts are owned by the
[`Subject, Event, and Lifecycle design`](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md).

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

There is no duplicate source method on PopulationDataset or SubjectSet and no
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
| [1. Dataset Core](2026-09-01-lazy-analysis-dataset-core-design.md) | Dataset value, family, row contract, state, actions, selectors |
| [2. Observation Model](2026-09-01-lazy-analysis-observation-model-design.md) | Population, predicates, observation, coordinates, aggregation |
| [3. Planner and Pushdown](2026-09-01-lazy-analysis-planner-and-pushdown-design.md) | private semantic graph, Ibis lowering, Arrow/Parquet exchanges, DuckDB/Python local boundaries |
| [4. Materialization Runtime](2026-09-01-lazy-analysis-materialization-runtime-design.md) | Runs, private exchange staging, durable receipts, Artifacts, Evidence, recovery |
| [5. Typed Operators](2026-09-01-lazy-analysis-typed-operators-design.md) | operator registry, admissions, output rows, statistical semantics |
| [6. Subject, Event, and Lifecycle](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md) | SubjectSet, domain analysis, identity authority and privacy |

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
[`Unified Correlation and Cross-Entity Population design`](2026-08-31-cross-entity-correlation-design.md)
is superseded and non-normative. Its old `session.correlate(...)`,
`MetricFrame[cross_section]`, `entity_grain=`, and `CorrelationPopulation`
surfaces must not be implemented.

## Global Acceptance Boundary

The architecture is internally complete when all of the following are true:

1. every row-bearing analysis operator accepts the registered Logical and
   Materialized input states and returns one registered Logical Dataset family,
   so arbitrary lazy DAGs remain constructible;
2. every Dataset exposes its complete logical row contract before execution;
3. default observation infers one exact Entity Population, while explicit
   Population changes membership rather than coordinate meaning;
4. among state-specific actions, Logical Datasets expose only `execute()` and
   Materialized Datasets expose `show()` plus `to_pandas()`; both states still
   expose registered downstream operators, and reads never replay the logical
   origin;
5. logical and materialized inputs have explicit, non-replay authority rules;
6. one admitted single-engine chain lowers to datasource work or fails with a
   structured capability reason; it never silently collects locally;
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
    definition receives a different execution key and Artifact;
13. a registered partial-SQL graph executes as one maximal Ibis stage per
    upstream execution domain plus exact bounded Arrow/Parquet exchanges and a
    pre-bound DuckDB or Python tail, never as a failure fallback;
14. private exchanges and local workspaces never become Dataset authority, and
    only the complete root output can receive a durable Parquet or engine
    receipt and become Materialized;
15. the Public Cutover Plan covers removals, code ownership, tests, disclosure
    surfaces, persisted clean replacement, and end-to-end agent acceptance.

All six module designs satisfy the design-level consistency gate. This document
does not authorize implementation; implementation begins only after the Public
Cutover Plan is accepted.
