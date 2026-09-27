# Semantic Object Model

## R0.3 full-algebra target decisions (inactive unless explicitly activated)

The declarations in this section are accepted target contracts; their status
is stated per subsection. They are the Semantic authority consumed by the R0.3
Analysis rules; runtime checks remain Analysis and datasource responsibilities.
Declaration identity and effective dependency fingerprint enter the bound
definition and Artifact receipt. An author-declared business fact is a trusted
premise, not a measured source fact.

### Named statistical-weight role (deferred)

The R0.3 statistical-weight role remains a target contract. R2.2 does not
export a declaration, Ref, catalog entry, or Help target for it. A later phase
must separately authorize the declaration and its source-value checks.
`ms.weighted_mean` retains its existing governed Metric-component meaning.

### Business order and simultaneous events (R2.3 target)

`ms.business_order(*, name: str, subject: Ref[EntityKind],
sequences: tuple[EventSequence, ...] = (),
conflicts: tuple[EventPrecedence, ...] = (),
domain: Ref[DomainKind] | None = None) -> Ref[BusinessOrderKind]` is one named,
versioned order authority. `ms.event_sequence(event: Ref[EventKind],
value: Ref[DimensionKind]) -> EventSequence` binds a declared integer or
ordered exact business sequence on that Event's occurrence Entity. All listed
sequence fields must be comparable for the same Subject and must not be the
occurrence identity merely renamed. `ms.precedes(before: ParticipantRoleHandle,
after: ParticipantRoleHandle) -> EventPrecedence` declares one acyclic,
same-Subject precedence for simultaneous roles. An author must provide the
business rationale in the owning definition context; a repeated or
contradictory rule is rejected. `ms.state_model(..., business_order:
Ref[BusinessOrderKind] | None = None)` binds the default order for that model;
`session.events.match(..., business_order: Ref[BusinessOrderKind] | None = None)`
binds the order for one pattern invocation. Both require exact Subject and
Event membership. Neither can supply an ad hoc Python callback.

At consumption, timestamps order different instants; the declared sequence or
closed precedence orders otherwise ambiguous occurrences. Sequence uniqueness,
comparability and exact participant mapping are mandatory checks. If the
authority leaves several allowed orders, matcher/replay may proceed only if
the requested output and every retained trace, violation, interval, assignment
and continuation part are equivalent under all of them. This is a method- and
input-bound check, not a global assertion that tied events commute. Stable
occurrence-ID sorting is allowed only for deterministic physical enumeration;
it is not transported as business evidence. Simultaneous activate/deactivate
events producing different final states or traces must be rejected until a
business order or conflict rule resolves them.

Status: accepted target design; amended 2026-09-24 for S1 W1 Semantic declarations.
The closed additivity, event-time, and value-policy authoring declarations are
loadable. Analysis DSL execution remains inactive until the later S1 work packages.

This document defines the object contracts of
`marivo.semantic` (`ms`): the business objects a coding agent declares in Python
so that downstream analysis can reference stable, validated semantics. It is the
authority for *what each object is and what fields it carries*; the process of
building them is in [authoring-workflow.md](authoring-workflow.md), and how they
are loaded, read, and validated is in
[loading-validation-introspection.md](loading-validation-introspection.md).

See also:

- [overview.md](overview.md) — design goals and where the object model sits.
- [datasource-layer.md](datasource-layer.md) — the refs and evidence entities
  build on.
- `marivo.help("semantic.<constructor>")` — the current executable static
  contract; target-design examples below change with the coordinated cutover.
- [Lazy Analysis Observation Model](../../superpowers/specs/2026-09-01-lazy-analysis-observation-model-design.md)
  — Population selection, observation scopes, and coordinate transitions.

Python files under `models/semantic/<domain>/` are the source of truth. An
object is declared once, is statically readable, and is referenced everywhere
downstream by a typed ref — never re-derived from a column name, table name, or
natural-language guess.

## Common contracts

These rules hold for every object type.

- **Identity comes from `name=`.** When `name=` is given it is the sole semantic
  identity. When omitted, the Python variable or function name is a fallback
  identity. The Python symbol itself is only a local alias and never part of the
  semantic id.
- **Kind-qualified refs.** Canonical runtime keys use `<kind>:<path>` (for
  example `metric:sales.revenue`), while each exact factory accepts only its
  kind-relative path. Object-to-object parameters take **ref objects** — a ref
  returned by an earlier declaration, or
  `ms.ref.<kind>(path)` for forward/cross-file references. Bare
  semantic-id strings are not accepted as authoring arguments.
- **Human text lives in `ai_context`.** There is no standalone `description=`.
  All prose is carried by `ms.ai_context(...)` (see below), whose
  `business_definition` an agent reads to decide whether an object matches
  intent.
- **Expression bodies are restricted.** The expression-bearing decorators
  (`@ms.dimension`, `@ms.time_dimension`, `@ms.measure`, `@ms.metric`) allow an
  optional leading docstring and then require exactly one
  `return <ibis expression>`. Ibis is the only expression language; SQL is
  metadata (provenance), never an executable body. Any other statement is
  rejected at decoration time. Refs remain data-only; nested field expressions
  use `ms.bind(field_ref, entity_alias)` rather than calling the ref.
- **Fail closed.** If decoration, assembly, or materialization cannot
  prove the contract holds, the object raises a structured error rather than
  degrading to a best-effort guess.

### `ms.ai_context(...)`

`ai_context` is a fixed-schema value accepted by domain, datasource, entity,
dimension, time_dimension, measure, metric, and relationship. It is built with
`ms.ai_context(...)`, never a raw dict.

| Field | Type | Use |
|---|---|---|
| `business_definition` | `str \| None` | Full business meaning; may be multi-line. Most important on entities and metrics. |
| `guardrails` | `Sequence[str]` | Do/don't notes (e.g. "excludes unpaid orders"). |

All fields are optional; missing fields render as `null` or empty lists. Unknown
field names fail closed via the keyword-argument mechanism, so unconsumable
content cannot leak into the semantic contract.

## Project and domain

A **semantic project** is one workspace root loaded through `ms.load(...)`.
Local declarations live under its `models/semantic/` directory, and configured
external `models/` roots join the same registry and load lock. An analysis
session binds to that resolved project root; omitted roots follow the shared
environment, nearest-manifest, then current-directory resolution order.

**`ms.domain(...)`** declares a business-domain boundary (`sales`, `marketing`,
`subscription`). The domain name participates in downstream ids (`sales.revenue`).

```python
ms.domain(
    name="sales",
    owner="Mina Zhang",
    default=True,
    ai_context=ms.ai_context(business_definition="Sales analytics"),
)
```

- `owner` is required — the person accountable for the domain's semantic
  correctness.
- `default` defaults to `True`; sibling objects in the domain directory may then
  omit a repeated `domain=`. Set `default=False` to force every object to name
  its domain explicitly.
- A domain is declared once per `<root>/<domain>/_domain.py`, with `name` equal
  to the directory name.

## Entity

An Entity declares a business object or fact grain over one physical source.
Its ordered `primary_key` is the identity of one Entity instance, denoted `K`;
it is not a copy of the source table's physical uniqueness constraint. Version
coordinates belong to `versioning` and are not added to `K` merely to distinguish
historical rows. The same `K` may appear in multiple historical versions.
`ms.entity(...)` is an assignment-style constructor returning `Ref[entity]`;
it cannot decorate a function. Identity and version columns must be exposed by
the source projection, but their physical types are read from the source when
execution first needs them. Loading remains source-I/O free and checks
projection, duplicate-key declarations, and identity/version overlap without
requiring authored physical type facts.

```python
warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=md.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(business_definition="One row per order before metric-level filters."),
)
```

- `source` is a datasource-owned structured descriptor: `md.table(...)` for a
  backend table/view; `md.parquet(...)` and `md.csv(...)` for DuckDB file
  sources; and `md.json(...)` for a DuckDB-backed JSON file or HTTP API source.
  Table, CSV, and JSON sources accept an optional projection mapping; physical
  types come from source metadata or from the actual file/API read. JSON paths
  are expressed in `columns=` alongside output aliases.
- Entities have no Python body and no inline SQL view. A persisted SQL view must
  be exposed as a backend table via `source=md.table(...)`; one-off SQL
  transforms are out of scope.
- Do not push metric aggregation logic into an entity.
- `K` must be complete, non-empty, and non-null when the Entity supplies a
  Population, Event participant subject, or StateModel subject. An Entity without
  a declared key may remain a computation source; it cannot become identity
  authority through observed uniqueness or a guessed Dimension.
- One identity tuple always denotes the same Entity instance. There is no second
  `business_key` or `physical_key` authoring parameter. Source row uniqueness is
  an authoring assumption derived from `K` and the declared version coordinates;
  Analysis does not scan the source to prove it.

### Versioning: snapshot and validity

Entities may declare how historical representations of their instances are
versioned. A storage partition is not automatically a semantic version: a table
partitioned by ingestion date may still contain ordinary event facts. Only an
explicit `versioning` declaration establishes snapshot or validity meaning.

**Snapshot** Entities declare one coherent business snapshot per governed
snapshot period. The example is target-design authoring:

```python
user_profile_daily = ms.entity(
    name="user_profile_daily",
    datasource=warehouse,
    source=md.table("user_profile_daily", columns={
        "user_id": "user_id",
        "dt": "dt",
    }),
    primary_key=["user_id"],
    versioning=ms.snapshot(
        partition_field=ms.ref.time_dimension("sales.user_profile_daily.dt"),
        grain="day",  # snapshot cadence
        timezone="Asia/Shanghai",  # defines the exact snapshot-period boundary
        format="%Y%m%d",  # on-disk partition encoding; omit for native date
    ),
)
```

The source row key is derived as `(K, snapshot_coordinate)`. Within one selected
snapshot, `K` is unique. A consuming Analysis operation supplies one exact
temporal boundary value: either an instant or immediately before an excluded
endpoint. These are closed internal interpretations owned by the consuming
operation, not new semantic authoring arguments or an arbitrary timestamp-tick
subtraction. An instant selects the snapshot period containing it under the
declared grain and timezone. An immediately-before endpoint selects the period
containing its left limit; an endpoint on a period boundary selects the preceding
period. If that exact snapshot is absent, the operation follows its empty-result
semantics. It never chooses the latest available
partition, a nearest earlier available partition, or each Entity's last-known
row. A half-open scope alone does not choose an interpretation: its consuming
operation defines and normalizes the exact temporal boundary rule.

All members and attributes at an anchor come from the same snapshot. Absence of
`K` from that snapshot means no represented member at that anchor; it does not
authorize carrying an older row forward. Declaring snapshot meaning states the
expected complete cross-section, not observed proof that an ingestion finished
or every expected member arrived. Source coverage and completeness remain
independent runtime evidence, and operations requiring them must validate that
evidence before publication.

**Validity-interval (SCD2)** entities declare `valid_from`/`valid_to` +
`interval` + `open_end`:

```python
user_history = ms.entity(
    name="user_history",
    datasource=warehouse,
    source=md.table("user_history", columns={
        "user_id": "user_id",
        "valid_from": "valid_from",
        "valid_to": "valid_to",
    }),
    primary_key=["user_id"],
    versioning=ms.validity(
        valid_from=valid_from,
        valid_to=valid_to,
        interval="closed_open",
        open_end=(None, "9999-12-31"),
        timezone="UTC",
    ),
)
```

Both bounds reference declared temporal Dimensions on the same Entity;
`open_end` lists the values meaning no declared end. The source row key is
`(K, valid_from)`. Validity bounds do not join `primary_key` solely because they
identify a version. Intervals for one `K` must be well-formed and non-overlapping
under the declared boundary convention, so an exact consuming-operation temporal
boundary resolves at most one representation. For `[valid_from, valid_to)`, an
instant `at` uses `valid_from <= at < valid_to`; immediately before an excluded
`end` uses `valid_from < end <= valid_to`, with the declared open-end convention.
Other admitted interval closures follow their exact boundary rule. Zero matching
intervals preserves absence; multiple matches are not preflighted and may affect
the result or fail an independent output contract.
`current_flag`-style versioning is not supported.

| Entity source | Declared source row uniqueness | Resolved identity |
| --- | --- | --- |
| Non-versioned with declared `K` | `K` | `K` |
| Snapshot with declared `K` | `(K, snapshot_coordinate)` | `K` within one exact snapshot |
| Validity intervals with declared `K` | `(K, valid_from)`, plus non-overlapping intervals per `K` | `K` at one exact anchor |

An unkeyed computation source supplies no Entity-identity or identity-based
uniqueness proof. An empty `K` is not interpreted as a singleton business Entity
or as a claim that a whole snapshot has only one row.

A Relationship to a versioned Entity is not physically many-to-one merely
because its keys match `K`. The join must first carry the exact snapshot or
validity resolution needed to prove one matching representation. No planner
derives identity by subtracting columns from `primary_key`.

Version selection does not define a Population's membership scope. Analysis
owns finite membership selection and its boundary rule, separately from Metric
observation time. An unresolved versioned Population cannot silently mean
`distinct(K)` across all history or borrow a later Metric's observation window.

## Dimension, time dimension, and measure

These are row-level objects: attributes, temporal axes, and numeric facts reused
by filters, grouping, relationships, and metric expressions.

A Dimension has one value per resolved owning Entity representation. Its logical
type and nullability derive from governed source bindings and its restricted
expression; display names and sampled values do not establish type, ordering,
or functional dependence. A reachable Dimension is single-valued only when its
complete directed path and temporal resolution prove that property. Analysis
derives predicate and coordinate admission from these facts; authors do not set
`membership_stable`, `filterable`, or `rollup_safe` flags. A current non-versioned
attribute does not acquire historical as-of meaning merely because it is a
categorical Dimension.

Prefer the direct-column constructors for physical columns and the decorator
form only when a row-level Ibis expression is needed:

```python
region = ms.dimension_column(name="region", entity=orders, column="region")


@ms.dimension(entity=orders, ai_context=ms.ai_context(business_definition="Normalized region."))
def region_norm(orders):
    return orders.region.upper()


amount = ms.measure_column(
    name="amount",
    entity=orders,
    column="amount",
    additivity=ms.additive_all(),
    unit="CNY",
)
```

If a row-level `.filter(...)`, `.cast(...)`, or multi-step chain represents a
nameable business concept, extract it into a dimension/time_dimension/measure and
reference that, rather than inlining it in a metric body. These decorators do not
require provenance; their trust comes from the owning entity, expression
readability, and materialization checks.

### Measure as the authority for additivity and unit

A **measure** is the authoritative declaration site for a row-level numeric
fact's `additivity` and physical `unit`. Tier-1 metrics aggregate a validated
measure; derived metrics propagate the unit via composition algebra.

The public authoring values are closed, versioned constructors:
`ms.additive(over=(Region,))` permits only the listed native coordinates,
`ms.additive_all(except_=(SnapshotAt,))` permits all native coordinates except
fixed axes, and `ms.non_additive()` permits no value summation. Bare strings and
the former `ms.semi_additive(...)` authoring entry point are invalid. A status
fact declares `status_time_dimension=` independently and may declare
`status_time_fold=` when sampled values need a fold. The status axis must appear
in `except_=`. A declared event time is a separate role.

| Authority | W1 rule |
| --- | --- |
| Authored | Measure or Metric owns additivity, unit, event/status time roles, and applicable `nulls`, `empty`, or `zero_denominator` policy. |
| Derived | Loader and normalized Metric graph record the policy version, event-time binding, retained sum components, and dependency fingerprint. A body or source value does not grant additivity. |
| Execution check | Private J1 construction admits only a builder-backed sum with explicit event time, supported additivity and retained state; source/pandas execution and coverage proof remain W2–W4. An opaque `@ms.metric` body can load but cannot continue through this J1 path. |

`ms.nulls.ignore()` and `ms.empty.null()` express an ignore-Null sum whose
complete empty contribution is `Null`. Other policy values require a matching
method implementation before Analysis admits execution.

### Time dimension

A time dimension is a dimension that explicitly carries time-axis metadata. Any
column used as a time window, grain, or calendar axis **must** be declared as a
time dimension — plain dimensions are never inferred as temporal from a name like
`dt`.

```python
dt = ms.time_dimension_column(
    name="dt", entity=orders, column="dt", granularity="day", parse=ms.strptime("%Y%m%d")
)
hh = ms.time_dimension_column(
    name="hh", entity=orders, column="hh", granularity="hour", parse=ms.hour_prefix(dt)
)
```

- **`granularity`** ∈ `year | quarter | month | week | day | hour | minute |
  second`. `minute`/`second` require a `ms.datetime(...)`/`ms.timestamp(...)`
  parse; `hour` on a non-datetime column requires `ms.hour_prefix(...)`.
- **`parse`** may be omitted for native `date`/`datetime`/`timestamp` columns
  (the variant is inferred from the ibis dtype). `string`/`integer` columns must
  supply `ms.strptime(format)` or `ms.hour_prefix(prefix)`. The body's ibis dtype
  must be compatible with the parse variant.
- **`is_default`** (default `False`) marks the default time axis when an entity
  has several. A consumer may use it only within its exact temporal role and
  compatibility checks; it does not bind membership time, observation time, and
  version-selection time to one window. At most
  one per entity — a second raises `SemanticLoadError`
  (`duplicate_default_time_dimension`).
- **`sample_interval`** on a parse marks a fixed-cadence sampled series (see
  sampled semi-additive metrics below).

#### Format specifiers: Python strptime vs MySQL

`parse` formats are authored as **Python strptime** (`%`-prefixed) and validated
at authoring time. At SQL-emission time Marivo translates them to the native form
of the target engine, so one authored format works on every backend. Each engine
consumes its own translation: MySQL (`STR_TO_DATE`) and Trino (`DATE_PARSE`) take
the MySQL form via `python_to_mysql_strptime`, PostgreSQL (`TO_TIMESTAMP`) takes a
template pattern via `python_to_postgres_strptime`, ClickHouse
(`parseDateTimeOrNull`) takes the MySQL form only when the format is exactly
expressible there, SQLite parses in a connection-local deterministic scalar, and
DuckDB receives Python strptime unchanged.

The critical divergence is `%M`:

| Specifier | Python strptime | MySQL |
|---|---|---|
| `%M` | Minutes (00..59) | **Month name** (January..December) |
| `%i` | (not used) | Minutes (00..59) |

Authors always write `%M` for minutes; Marivo maps `%M`→`%i` for Trino/MySQL. Do
not author `%i` — it is not valid Python strptime and is rejected. Directives
whose meanings diverge without a safe mapping (`%W`, `%u`, `%Z`, `%c`, …) raise a
`DatasetCompilationError` at SQL-emission time, pointing to a supported directive
or a native temporal column. ClickHouse additionally refuses a set of directives
whose native parser would silently shift the parsed value or lose sub-second
precision rather than fail; those refusals name the directive and the reason.

## Metric

Metrics come in two tiers. **Tier-1** is the default: declare a row-level
measure, load the coherent slice, then aggregate it.

```python
@ms.measure(
    entity=orders,
    additivity=ms.additive_all(),
    unit="CNY",
    ai_context=ms.ai_context(business_definition="Paid order amount in CNY."),
)
def paid_amount(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount


revenue = ms.aggregate(name="revenue", measure=paid_amount, agg="sum", time=order_date)
```

`ms.aggregate(measure=..., agg=...)` supports `sum | min | max | mean | median |
percentile | count | count_distinct` (`ms.count(...)` is the counting shortcut).
The Metric declaration owns q (`0.5` for median). Dataset observation uses exact
`linear_interpolation@v1` by default. Select semantic approximation explicitly
with `ms.quantile_metric(metric, method="duckdb_tdigest@v1")` before observation;
`method="linear_interpolation@v1"` selects exact interpolation explicitly. The
immutable input also accepts a governed RuntimeMetric expression. It does not
change q or register a new reusable Metric. Observation checks the governed
root, and retained projection, comparison and attribution preserve the exact
method identity. No backend-dependent method selection or failure fallback is
allowed. Both current Dataset implementations are registered for DuckDB; a
semantic preview on another backend does not establish Dataset eligibility.
`marivo.help("semantic.quantile_metric")` owns the focused input contract.
Both `ms.aggregate` and `ms.count` accept an
optional `filter=ms.where(dimension=value, ...)` to restrict the aggregation to a
subset of rows (e.g. a failure or error subset) without a hand-written body.
Filter keys are local semantic dimension names on the metric's target entity,
not arbitrary physical columns. A scalar value means equality; a non-empty
tuple/list means membership, for example `ms.where(type=(2, 4))`. Multiple
conditions are AND-joined. The loader rejects missing or cross-entity filter
dimensions before graph lowering. If a valid authored literal is incompatible
with the resolved runtime dtype, preview and analysis raise
`filter_value_runtime_incompatible` before query submission. The declaration is
preserved: Marivo never replaces a business code with a physical label (or the
reverse) without confirmation from the user or business owner.

For a multi-root component Metric, `ms.aggregate(..., time=TimeRef,
time_via=(RelationshipRef, ...))` binds an event-time dimension on a different
Entity through an explicitly ordered, functional path from the contribution
root. A native time dimension uses the existing `time=` form and an empty path.
`ms.count(..., time=TimeRef)` binds a native event-time dimension on the counted
Entity. The normalized component contract retains each time Ref and path;
the derived ratio has no guessed common time axis. Missing, discontinuous,
non-functional or cross-Entity paths reject during semantic loading.
`ms.aggregate(..., nulls=ms.nulls.ignore(), empty=ms.empty.zero())` can declare
the first private multi-root sum policy; omitted builder policies keep their
existing meaning. `ms.ratio(...,
zero_denominator=ms.zero_denominator.undefined())` declares the finish policy
for an explicit-component ratio. A private Analysis route requiring this policy
rejects an omitted policy; existing ratios outside that route retain their
current behavior. These Semantic builder arguments are public declarations,
while the S2 Analysis DSL consumer remains private.

**Tier-2** `@ms.metric(...)` is the expression escape hatch, used only when a
metric cannot be expressed as measure + aggregate. It declares dependencies with
`entities=[...]`; the function parameters are positional aliases injected in
`entities` order — parameter names never determine entity identity.

```python
@ms.metric(
    entities=[orders],
    additivity=ms.additive_all(),
    time=order_date,
    unit="CNY",
    nulls=ms.nulls.ignore(),
    empty=ms.empty.null(),
    ai_context=ms.ai_context(business_definition="Historical DuckDB query summed paid orders; confirm the current business definition independently."),
)
def paid_revenue(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount.sum()
```

### Grain, root entity, and fan-out

Every base Metric has a computation root: the Entity whose governed facts and
join paths define its contributions. For a single-Entity Metric, the explicit
`entities=[entity]` declaration identifies that root; a multi-Entity expression
Metric must name `root_entity` explicitly. The function body does not select
it. Aggregate receivers in a base body belong to that root; joined Entities may
contribute Dimensions and filters. The root does not also select the analysis Population,
observation window, or reporting coordinates.

Analysis may bind multiple computation roots to one explicitly chosen Population
when each component has a unique, temporally compatible, contribution-safe path
to that analysis Entity. A derived graph is not rejected solely because its
leaves have different computation roots. Static semantic validation owns the
graph's intrinsic coherence; Analysis validates the concrete Population and
coordinate binding without rewriting any root.

`@ms.metric(...)` accepts `fanout_policy`:

- `"block"` (default) rejects unsafe one-to-many edges with an `unsafe-fanout`
  repair payload naming `set_metric_root` and `set_fanout_policy` as candidate
  fixes.
- `"aggregate_then_join"` reduces the unsafe side to the merge grain before the
  join. Requested dimensions keep overlapping-bucket semantics; where-filters on
  that side give semi-join membership semantics (a root row with ≥1 match counts
  once). Requires an additive or semi-additive declaration through the closed
  `ms.additive(...)` / `ms.additive_all(...)` policy constructors and is rejected
  on derived metrics.

Overlapping buckets are not an additive partition. A root contribution of 100
associated with two tags can appear as 100 in each tag while remaining 100 at
the root grain. Removing the tag coordinate may not sum those two values without
an exact disjointness or conserving allocation proof. Allocation meaning belongs
to the particular Metric contribution path, not to Relationship join metadata.
A reusable business weight can be a governed Measure on a bridge Entity; its
application remains part of the Metric definition. No implicit equal split or
new generic `allocation=` authoring API is introduced by this amendment.

### Sampled semi-additive metrics

For periodic snapshot facts (bandwidth, capacity, inventory), the time dimension
declares physical precision (`granularity`) and reporting cadence
(`sample_interval`); the metric declares the business status axis and fold:

```python
sample_ts = ms.time_dimension_column(
    name="sample_ts",
    entity=bw_samples,
    column="sample_ts",
    granularity="second",
    parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),
)


@ms.metric(
    entities=[bw_samples],
    additivity=ms.additive_all(except_=(sample_ts,)),
    status_time_fold="mean",
    status_time_dimension=sample_ts,
    unit="kbit/s",
)
def upstream_bw(bw_samples):
    return bw_samples.upstream_kbps.sum()
```

The body expresses the spatial aggregate inside one sample point;
`status_time_dimension` binds the as-of/status axis; `status_time_fold` reduces the
sample series to the requested grain (P95-style folds use
`status_time_fold=("percentile", 0.95)`, always recomputed from base samples). Not every
semi-additive metric is sampled: already-summarized snapshots (e.g. daily
inventory) omit `status_time_fold` but must still declare `status_time_dimension`.
The status axis must be fixed by its additivity policy and be a true business as-of time (`snapshot_date`,
`as_of_date`), not a technical write time (`created_at`, `ingest_time`).

Tier-1 `ms.aggregate` resolves spatial additivity and temporal folding as two
independent contracts. For example, `agg="mean"` remains `non_additive`, while a
semi-additive input measure contributes its `over` status axis and default
fold. An explicit metric `fold=` overrides that default. The effective fold is
stored in the canonical metric graph, so inherited and explicit temporal
semantics participate in artifact identity. A fold override on a measure that
is not semi-additive is invalid.

Spatial aggregation precedes temporal folding. The two operations are not
interchangeable: devices with samples `[10, 0]` and `[0, 10]` have individual
peaks 10 and 10, but the peak of their spatial total is 10, not 20. A projected
semi-additive value therefore does not authorize summation across Entity or
Dimension merely because the removed coordinate is non-temporal. The exact
fold must prove commutation under the retained sample domain, weights, nulls,
and evaluation times, or retain sufficient aligned state to restore the
declared order. A missing proof blocks the transformation. Mean folds require
compatible sample domains and weighting; first/last folds require compatible
evaluation anchors; max/min/percentile generally require pre-fold state.

### Intrinsic graph and coordinate-aggregation facts

Standard aggregate, ratio, weighted-mean, linear, and cumulative builders retain
their existing authoring shapes. Their normalized semantic graph is the sole
owner of computation roots, base inputs, filters, component roles, spatial
aggregates, temporal folds, cumulative anchors, units, approximation, and fixed
null/empty rules. A shared semantic resolver derives intrinsic transformation
requirements from that graph; Analysis combines them with the exact Population,
coordinates, selected contributions, and retained Artifact state to decide
admission. These derived requirements are not new business-authoring parameters
or a second handwritten capability inventory.

Every removed coordinate requires its own proof: disjoint or conserving
contributions, the required evaluation order, compatible coverage and anchors,
and exact merge/finalize state. Typical retained state is:

| Governed operation | Sufficient state for an admitted coordinate fold |
| --- | --- |
| Sum/count | Additive value and empty/null support, with disjointness or exact allocation |
| Mean | Sum and non-null count over the same selected contributions |
| Weighted mean | Weighted numerator and weight sum over the same non-null pairs |
| Ratio | Each named component's independently admissible state and fold |
| Distinct count | Exact mergeable identity state, or an exact disjointness proof |
| Median/percentile | Exact registered distribution state; projected quantiles are insufficient |
| Semi-additive | State preserving spatial-before-temporal order, or an exact commutation proof |
| Cumulative | Compatible base/anchor/evaluation-end state under the specific removed-axis contract |

The table states requirements, not permission for every Analysis operator or
materialized family to implement those folds. Missing state rejects the
transition; a materialized value never authorizes replaying its semantic origin.
An opaque Tier-2 expression with no exact normalized aggregation semantics may
remain a valid authored expression, but cannot acquire coordinate transformation
capabilities from its `additivity` label alone. Unsupported transformations fail
with repair to express the business meaning using existing governed builders or
perform an already-admitted observation at the required grain. This amendment
does not add a generic reaggregation callback or opaque-state API.

R2.2 keeps the content-addressed value DAG reusable across equivalent Metric
declarations. A bound graph fingerprint additionally hashes the canonical DAG
and effective semantic dependency digest; it does not introduce Semantic-object
version management. Each leaf retains exact component roles, policies,
time/path/filter facts, numerical method and required state. The shared
Semantic resolver derives only intrinsic state and ordering requirements.
Analysis checks the selected Population, coordinate contributions, and actual
retained parts before allowing a transformation. The restricted Ibis body of
an expression Metric supplies a computation expression, not an inferred
component graph or fold license.

### Accepted S0 DSL declaration facts (inactive)

The [first-round Analysis DSL slice](../analysis/python-analysis-design.md#accepted-s0-analysis-dsl-slice-inactive)
accepts the following Semantic-to-Analysis facts as future input obligations.
It does not change current authoring signatures, fixed reducer policies or
loaded catalog behavior. The broader decorator and `ms.additive(...)` syntax
in the target interface remains proposed until the corresponding public
contract is accepted and implemented.

| Fact and producer | Available now | Required before an S0 consumer relies on it |
| --- | --- | --- |
| Author-declared Entity identity/version and source, Dimension time role and Relationship mapping | Entity K/version and governed source, Dimension bindings and named relationship paths | Bind first-round non-versioned single-column identity, exact event-time role and single-valued path without deriving business meaning from physical types or a source scan |
| Author-declared Measure/decorator Metric meaning | Existing unit/additivity fields and restricted Ibis body | Explicit contribution support/root, business time role, unit, coordinate-specific additivity and enforceable null/empty/zero-denominator policy with a definition version; the current three-bucket additivity and fixed null rules do not supply all of these facts |
| Builder-derived Metric structure | Canonical graph supplies component occurrences, computation roots, filters, folds, roles and required state | Derive sum/count and explicit ratio component/merge/finish obligations from the registered builder and inputs; retain each component's own scope and declaration provenance |
| Execution evidence owned by Analysis/Runtime | Static declarations and some existing source/retained checks | Record completed checks bound to this invocation separately from author declarations, derived rules and queued obligations; never upgrade a trusted Entity declaration into measured source uniqueness |

A decorator body is executable Ibis, not a semantic parser input. A direct
decorator ratio with a division expression has no inferred numerator/denominator
retention. Only an explicit component builder and its real retained state may
authorize original-state ratio rollup. Missing first-round declarations or a
value policy that the chosen implementation cannot enforce block the consuming
operation; fixture-only flags, equal sample values, Arrow dtypes and Ibis
compilation cannot supply them. T5 fixtures must use the real loader or record
a blocker until the Semantic owner adds the minimum authoring contract.

### Fixed null and empty contracts

Reducers use the exact selected contribution set. Numeric reducers ignore null
inputs; count counts its declared Entity/row unit or non-null field inputs as
specified by its builder, and distinct count excludes null identities. With no
admitted non-null contributions, count/distinct count return zero;
sum/min/max/mean/median/percentile return null. Mean retains non-null count;
weighted mean retains only pairs whose value and weight are both non-null.
Its zero or missing weight sum returns null. Ratio returns null when either
component is null or its denominator is zero. Linear and cumulative nodes retain
their own component/base null behavior; they cannot insert an implicit zero.

An empty scalar reduction retains its singleton row contract. A missing
coordinate, an all-null observed coordinate, and a present zero remain distinct
facts; neither a Population member nor a missing time bucket automatically
contributes a zero or denominator unit. Private retained state must distinguish
empty/all-null support when that distinction is needed to merge values. These
rules are fixed by the governed operation, identically for Ibis and pandas; no
backend-dependent division or null-fill policy is chosen at execution time.

## Weighted means

`ms.weighted_mean(value=<Ref[measure]>, weight=<Ref[measure]>)` is a tier-1
physical aggregate over two measures from the same entity. Marivo computes
`SUM(value * weight) / NULLIF(SUM(weight), 0)` over rows where both inputs are
non-null. The weight measure must be additive; the result is non-additive and
inherits the value measure's unit. Observe persists exact `numerator` and
`weight` components for weighted-mix attribution.

## Derived metrics and decomposition

Derived metrics combine already-registered metrics through a canonical
composition and have **no Python body**. They are direct calls, and their
component roles come entirely from the builder:

| Builder | Component roles | Applies to |
|---|---|---|
| `ms.ratio(numerator=..., denominator=...)` | `numerator`, `denominator` | ratios / conversion rates |
| `ms.linear(terms=...)` | additive terms | sums/differences of commensurable metrics |

```python
avg_execution_time = ms.ratio(
    name="avg_execution_time",
    numerator=total_execution_time,
    denominator=query_count,
    unit="s",
    ai_context=ms.ai_context(business_definition="Average execution time per query."),
)
```

Shape classification fails closed: `@ms.metric` with an empty `entities` list is
an error; a call with neither `entities` nor composition components is an error.
Derived metrics cannot reference entities/dimensions/time dimensions directly —
package any intermediate values as base metrics first. Ratio uses the existing
canonical `zero_division="null"` contract: a zero denominator yields null, never
infinity, and null inputs remain null. Backend lowering preserves this exact
rule. Any different named business formula must be authored explicitly rather
than selected as a backend fallback.

### Recursive derived metrics

Derived metrics may consume other derived metrics. Authorization is checked at
each node: ratio, linear, and cumulative retain their own
child, unit, source, scope, and evaluation requirements. Semantic readiness
lowers the complete selected metric through the same graph contract used by
analysis and blocks graphs deeper than 10 nodes or wider than 256 pre-CSE
occurrences. A legal ratio of ratios is therefore analysis-ready; an illegal
child combination fails with the responsible dependency and occurrence path.

Coordinate aggregation resolves and folds every component before composing its
parent. A ratio does not make its numerator or denominator additive: either may
be distinct, cumulative, or temporally folded and must independently satisfy
the requested axis transition. Filtering a current observation binds its full
coordinates and selects the associated component contributions together. It
does not retain an unfiltered denominator from Population provenance.

### Cumulative metrics

`ms.cumulative(...)` answers "how much accumulated up to bucket *t*". The base
must be a tier-1 `sum`/`count`/`count_distinct`/`weighted_mean` metric; `over=` selects the time
axis (required unless the base root entity has exactly one time dimension).

```python
cumulative_active_users = ms.cumulative(
    name="cumulative_active_users", base=active_users, over=event_time
)
```

`count_distinct` bases use first-seen semantics (each entity counted at its
earliest bucket, so the running total is monotonic). The `anchor` selects the
accumulation shape:

| Anchor | Shape |
|---|---|
| `None` (default) | All-history running total; the observe window clips displayed rows but does not reset the value. |
| `ms.grain_to_date(grain=...)` | Resets at each `week`/`month`/`quarter`/`year` boundary (WTD/MTD/QTD/YTD). |
| `ms.trailing(count=..., unit=...)` | Fixed-size rolling window ending at each bucket; empty windows follow the exact base null/empty contract, and partial windows retain partial coverage. |

`trailing` accepts only fixed-size units (`second`..`week`); calendar-variable
units are rejected with a teaching error pointing to `grain_to_date`. A
trailing `day` is exactly 86,400 seconds and a trailing `week` is exactly
604,800 seconds; these are not report-timezone civil periods and do not resize
at DST transitions.
Cross-anchor constraints: `grain_to_date` requires every display bucket to lie
within one reset period (a `week` grain under a `month` reset is illegal);
`trailing` requires the window span to be an integer multiple of the query grain.
Cumulative metrics may serve as ratio components; do not use cumulative over
`mean`, percentile, expression-body, or derived metrics.

A derived ratio/weighted/linear metric over cumulative components can be
compared only when every outer component is cumulative and all components share
a compatible `all_history`, `trailing`, or `grain_to_date` anchor. Trailing anchors
compare by canonical fixed duration (`7 day` equals `1 week`); grain-to-date anchors
must share one reset grain. Comparable-period results retain paired coordinates only
and may align by window ordinal, DOW position, holiday position, or holiday-then-DOW
position. Mixed anchors and cumulative/non-cumulative mixes are rejected. This compare
allowance does not extend to `attribute`, `decompose`, or `forecast`.

## Metric unit (UCUM)

`unit: str | None` (default `None`) is accepted on measures and metrics. Values
use the UCUM case-sensitive vocabulary, with one extension: bare ISO 4217
uppercase codes are currencies.

| Category | Notation | Examples |
|---|---|---|
| Time / bytes / percent | UCUM code | `s`, `ms`, `h`, `By`, `MiBy`, `%` |
| Dimensionless fraction | UCUM code | `1` (values 0–1) |
| Counted noun | UCUM annotation, English singular | `{order}`, `{user}` |
| Compound / ratio | UCUM `/` | `By/s`, `{order}/d`, `CNY/{user}` |
| Currency | Bare ISO 4217 | `CNY`, `USD` |

The authoritative declaration site is the measure's `unit=`; tier-1 and derived
metrics inherit it at load, and an explicit `unit=` on a metric overrides.
Derivation rules: `sum/min/max/mean/median/percentile` preserve `measure.unit`;
`count/count_distinct` yield `None` (author `{order}` explicitly);
`ratio(num, denom)` uses `MetricUnitAlgebraV2`: equal known units yield `"1"`
and unequal factorable units form a reduced quotient;
`weighted_mean` yields its value measure unit; `linear` yields the common unit and
raises `INCOMMENSURABLE_LINEAR_UNITS` when terms carry ≥2 distinct known units
(an author override cannot suppress this — the physics of addition is
label-independent). Units never perform value conversion: relabeling `ms` as `s`
does not divide by 1000, and a currency unit does not authorize an exchange-rate
conversion. Such conversions require an explicitly governed expression and its
business inputs. `None` is always valid
(richness-advisory only, never a readiness blocker).

Automatic derivation uses one bounded grammar shared by catalog and runtime
typed aggregation. `1` is the empty product; `.` joins product atoms; one `/`
separates numerator and denominator products. Atoms are non-empty printable
ASCII without whitespace or `. / ( )`, and `1` is reserved. Equal
case-sensitive factors cancel one-for-one, remaining factors sort bytewise,
and repeated factors remain repeated. Thus
`(CNY/{request})/(s/{request})` reduces to `CNY/s`, `CNY/CNY` to `1`, and
`1/{request}` remains unchanged. Authoring-valid strings outside this grammar
remain opaque catalog metadata and cannot be combined automatically by a
parent; an explicit valid unit on that catalog parent remains authoritative.
Extending the grammar requires a new algebra version.

## Relationship

A relationship is a top-level metadata call describing a join path between
entities. Join keys must be dimension/time_dimension refs, never bare column
strings:

```python
ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    keys=[ms.join_on(order_customer_id, customer_id)],
)
```

Analysis resolves each join Dimension ref to its declared source column on the
exact relationship endpoint before checking identity coverage or compiling a
join. Semantic names may differ from physical column names; a ref string is
never itself a physical column name.

Relationship owns the mapping between its endpoints, not a Metric's counting or
allocation rule. Key coverage and version resolution derive single-valuedness;
declared identity is trusted for source data, not runtime evidence that a source
obeys it. Ambiguous paths still fail during planning; temporal matches use the
declared selection without choosing a cheaper or first path. A path that permits coordinate enrichment does not itself prove
that overlapping contributions can be summed when a coordinate is removed.

`name` is the business role and direction is always `from_entity` to `to_entity`.
The directed structural cardinality (`one_to_one`, `many_to_one`, `one_to_many`,
or `many_to_many`) follows whether distinct join columns cover each endpoint's
complete stable `primary_key`. Authors do not repeat that derived fact. Whether
every source row must match a target is a separate business requirement, not
part of the Relationship constructor in R2.1. Static loading cannot derive or
prove it from physical metadata or a sample. Every `keys` pair resolves to a
direct-column Dimension or TimeDimension ref on its exact endpoint, without
interpreting a semantic ref path as a physical column.
For a versioned endpoint, this is conditional on the consuming operation's
exact snapshot or validity selection. Loading checks these declarations without
reading rows; actual multiplicity, missing matches, and version overlap remain
consumer/source checks. A weaker many side never grants fanout safety. Derived
cardinality grants no evidence of actual rows. When completeness matters, an
explicit bounded `source_check.relationship_matches(...)` can test its declared
scope; source-health results do not become a global business guarantee.

## Event and StateModel boundaries

Event keeps its existing occurrence identity, business `occurred_at` axis,
restricted occurrence predicate, and named participant paths with `one` or
`optional_one` cardinality. Occurrence identity is distinct from the participant
Entity's `K`. A subject-consuming operation requires the exact participant
Entity identity and any temporal resolution needed to reproduce it; occurrence
keys never substitute for subject membership.

StateModel keeps its exact subject Entity, closed states, inception, and
deterministic transitions through cardinality-one Event roles. It defines which
transitions are valid, not whether source history is complete or which members
belong to this analysis. Population windows, sampling, replay windows, censoring,
and scoped completeness assumptions remain Analysis/source-evidence concerns.
No semantic Population, Sample, or `complete=True` authoring field is introduced.

## Bounded acceptance cases for the amendment

1. A snapshot Entity with `K=(user_id,)` admits repeated users across dates,
   trusts `(user_id, snapshot)` row uniqueness, resolves one exact snapshot,
   and uses the consuming operation's empty result when it is absent. A physically
   partitioned non-versioned Event Entity acquires no snapshot semantics.
2. A validity Entity derives `(K, valid_from)` row uniqueness and applies the
   declared interval selection without source-data preflight. No identity key
   is constructed by removing columns.
3. A January membership selection can observe February facts while retaining the
   selected `K` values. An unspecified versioned membership anchor cannot become
   historical `distinct(K)` or the downstream observation end.
4. Order counts and line-item revenue keep their own computation roots while
   observing one explicit customer Population; unsafe mapping or allocation
   fails with the responsible component occurrence.
5. Two overlapping tags cannot double a 100-unit root total during rollup. The
   two-device peak example above rejects projected-value summation; mean folds
   with incompatible sample coverage also reject without sufficient state.
6. Merging mean/weighted/ratio state matches direct governed computation after
   row selection, including empty, all-null, and zero-denominator inputs. A ratio
   with a distinct or temporal component cannot inherit additive permission.
7. Event participant and StateModel subject identity equal Population `K`; no
   source-only Entity with an absent key becomes a subject. Declared snapshot or
   StateModel meaning does not create runtime completeness evidence.

## Historical SQL context and verification

`ms.metric` accepts only an Ibis expression body. If historical SQL helps explain
where a business definition came from, describe it in
`ai_context=ms.ai_context(business_definition=..., guardrails=[...])`. That text
has no execution authority and carries no verification status. `ms.from_sql`,
`SqlProvenance`, `ms.parity_check`, parity results, and Catalog parity status are
removed from the public surface. Readiness does not infer verification from a
stored query or from the absence of one.

Confirm a migrated metric against an independent business source or a governed
Ibis reference outside the declaration, and report the scope and result of that
comparison separately. `md.raw_sql` remains a terminal datasource diagnostic;
its result cannot enter Semantic or typed Analysis.

### Explicit percentile method contract

`ms.quantile_metric(...)` selects an explicit quantile method for governed
root median/percentile Metrics. The original declaration owns q; method selection
is either exact linear interpolation or DuckDB T-Digest, never inferred from
cost. The Dataset authority binds method, q and its registered replay recipe.
T-Digest discloses semantic approximation and unknown error bounds. Its input
vector is source-sorted and evaluated with the pinned backend method.
Use `marivo.help("semantic.quantile_metric")` for the public constructor and
pass the selected input to `session.observe(...)`.
