# Semantic Object Model

Pending design amendment (2026-10-17):
[Expression redesign v0.37](expression-model-redesign.md#26-原生-ibis-与自动延迟绑定)
replaces the custom Measure/Metric numeric AST with native Ibis expressions and controlled
Deferred inputs. Named and anonymous expressions share the same checks; semantic stage,
unit, additivity, contribution roots and time obligations remain Marivo-owned. Physical dtype
binds automatically during execute preparation. This is not implemented: the closed-expression
and Ibis-return rejection rules below remain the current API contract, not the proposed target.

The same pending v0.37 design adds ordered first/last, native approximate reductions,
explicit overlapping membership buckets, operator-local sum empty policy, and reusable named
window Metrics. Named and anonymous inputs share the same deferred capture and checks;
[semantic boundaries](expression-model-redesign.md#39-五项能力共用的检查边界) and
[conditional migration equivalence](expression-model-redesign.md#a11-等价迁移的判定条件)
are proposal contracts. Retired APIs and unsupported capabilities described below remain current
until implementation and disclosure cutover; this note does not enable those APIs.
The reviewed v0.37 proposal gives explicit membership paths priority and infers identity only
when no path is supplied. Member-set products and complete target domains retain their bindings;
classifying target identities does not traverse contribution-assignment paths. Axis-free rules
use Metric.window; observe fixes the logical time binding, without freezing source data. Window
Metrics allow qualified spatial rollup with that binding preserved; time-axis coarsening,
replacement or removal is rejected. Scalar to-date uses the endpoint. The ordinary H/W recipe
for legacy first-seen is equivalent only under consistent inputs; it adds no general window
sharing requirement or runtime rejection policy. Ordered first/last retains its sampling checks.

The pending [fanout amendment](expression-model-redesign.md#371-fanout-的表达方式与能力边界)
separates existence selection, membership buckets, association-event roots and modeled allocation
roots. Relationships do not grant counting or allocation rules. Logical contribution semantics
admit qualified equivalent physical plans without restoring fanout_policy or author-defined joins.
This proposal does not enable capabilities marked unsupported in the current contract below.

Status: R1.3 authoring contract, 2026-10-15; R1.4 publication and R2 execution remain pending.
Semantic owns authored business meaning and normalized declarations.
Analysis owns invocation checks, exact implementation admission and execution.

## R2.2 scalar observation reads

Resolved Dimension, TimeField and Measure values support immutable scalar
`via((relationship, ...))` bindings for conditions and output classification.
Paths retain declared direction and require single-valued full-key mappings;
ambiguous or missing paths reject when bound to each contribution root.
Metric declaration bodies admit `via` on Dimension, Measure and TimeField
receivers for scalar conditions; source reductions still require native fields.
`field.at(anchor)` binds an exact attribute version only on versioned owners;
it reads no attributes and does not prove a version exists. Anchor and path
remain part of field predicates and Query identity. TimeField.within(scope)
constructs a half-open condition; it does not replace the observation period
required by sampling. Source reductions, sample axes and sum-permission declarations
continue to require native fields; bound scalar reads do not expand row arithmetic.
Focused Help routes are `semantic.Field.via`,
`semantic.Field.at` and `semantic.TimeField.within`. Numeric Query execution is still
pending R2.4–R2.5; no numerical qualification is implied.

## Definition changes during analysis

Approved 2026-10-15; implemented in the uncommitted R1.3 working tree after
the documentation checkpoint. Independent review and complete R1.3 acceptance
remain pending; prior checks alone do not qualify this new policy.

Marivo does not require semantic objects to remain bound to the exact definition
versions from which they were constructed, and does not reject an input solely
because a same-path definition has changed. If semantic-layer definitions change
during analysis, the agent or user accepts the consequences. This does not relax
type, kind, contribution-root, grain, unit, time-role, null or other semantic
correctness checks. Definition fingerprints must still describe the computation
and dependencies actually consumed; cache reuse must use correct keys and
invalidation, and persisted provenance and records remain governed by their
owning contracts.

Reference-resolving consumers use the completed scope's kind/path definition,
without comparing it to the constructed input's former version. Already closed
expressions remain immutable and compose their stored definitions and facts;
this policy adds no live refresh or module replay. Business snapshot/validity
selection remains a separate temporal contract.

## Analysis-facing declarations

Declaration identity and effective dependency fingerprints enter bound analysis
definitions and retained Artifact metadata. Authored business facts are trusted
premises, not measured source facts. Declared definitions and consumed state
remain distinct from physical execution qualification.

### Statistical-weight boundary

There is no public named `ms.statistical_weight` or current-row
`mv.statistical_weight` surface. Private rules do not activate them.
Row `weighted_mean(weight=...)` pairs a value and weight on one contribution
root; internal-sample weighted means consume completed Metrics.

## Common contracts

Physical Entity/Field/Relationship constructors capture pending definitions and
return typed Refs. These are lookup keys, not expression values. Capture all
physical dependencies, including retained Ibis forward references, before calling
`finalized = ms.finalize()` in the loader context. Finalization uses normal
registry/assembly checks, then freezes the complete closure. Resolve each handle
with `amount = finalized.resolve(amount_ref)` before composition. Values retain
complete default-time, coordinate and dependency facts using the same definition
fingerprint function as Catalog resolution. Finalization closes physical capture;
late declarations on that scope/root are rejected. It never replays module or
import effects and does not publish local Metrics.

Constructed Entity/Field inputs and helper policies do not require hidden
original-version constraints. Reference-resolving physical consumers use their
completed closure; typed Refs remain lookup keys and may name forward dependencies.
Pure Measure additivity checks coordinate presence, kind, native time role and
permissions in its row's frozen scope, without original-version rejection, a
loader or live refresh. Finalization remains necessary for coherent default-time
and coordinate facts and supported Ibis forward dependencies.

Physical leaves retain explicit `name=`. Pure Measure/Metric decorators take
zero arguments and capture the function's `__name__`, matching
`[a-z][a-z0-9_]*`. Aliases do not rename a declaration. Naming, ai_context and
source location are independent metadata; redundant compatible assertions do
not create a second computation. Arithmetic, filtering and sample reductions
produce anonymous expressions.

Entity/Source and Dimension/TimeDimension retain their restricted Ibis bodies.
A Dimension/TimeDimension body may use `ms.bind(field, entity_parameter)` for a
constructed physical field or an explicit field lookup Ref. Assembly checks
ownership and the complete dependency version of constructed inputs; conflicting
versions are rejected. Explicit lookup Refs retain forward-reference semantics.
Pure Measure/Metric bodies accept only closed typed expressions,
immutable literals, a leading docstring, fresh local expressions and one final
return. They reject table parameters, Ibis values, ms.bind, arbitrary calls,
reassignment, imports and control flow before invoking any authored function.

Human business meaning lives in `ms.ai_context(...)`. A docstring does not grant
permission or become a second description. Errors state expected/received
shape, declaration location and a concrete reauthoring action.

Native fields expose `is_null()` for a typed missing-value predicate; Dimension,
TimeField and Measure receivers share the single
`marivo.help("semantic.Dimension.is_null")` contract. Routed TimeFields retain
their path and are valid only as a source reduction's `time=` role, including
when authoring sum-permission coordinates.

Local declarations neither require an active loader/Session nor publish Catalog
Metrics. R1.4 owns collection, qualified identities and public discovery; R2 owns
the new execution path. A valid graph is not backend or Runtime evidence.

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
`ms.entity(name=..., ...)` is an assignment-style constructor returning an
typed Entity capture Ref. Omitting `name` selects the restricted Ibis Table
decorator form. Identity and version columns must be exposed by
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
- A direct Entity uses the Source relation unchanged. A decorator Entity receives
  that one Source as an Ibis Table and returns one relation derived from it.
  Filtering, projection, deduplication and aggregation can establish reusable
  business grain. Question-specific Metric calculations remain in Metrics.
- Primary keys, version columns and downstream fields belong to the Entity's
  output schema. Source aliases govern only the expression's input. Physical
  schema checks are deferred until metadata is bound; static loading does not
  invent input types or prove row uniqueness.
- Entity bodies cannot execute queries, submit SQL, create foreign table roots,
  connect to another datasource or call arbitrary helpers. The Source retains
  its physical identity and capability requirements; an expression cannot make
  an unqualified Source/backend cell executable.
- Analysis enters through `session.members(entity)`. The frozen Entity body
  contributes to its dependency fingerprint. Fixed Artifacts continue from
  retained state without reevaluating the Entity body or reconnecting sources.

```python
@ms.entity(datasource=warehouse, source=md.table("orders"), primary_key=["region"])
def regional_orders(raw):
    return raw.filter(raw.amount > 0).group_by("region").aggregate(total=raw.amount.sum())
```

A persisted SQL view is exposed with `md.table(...)`. Custom SQL remains the
terminal datasource path and does not produce an Entity.
- `K` must be complete, non-empty, and non-null when the Entity supplies a
  member-domain input. An Entity without
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
snapshot period:

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
form for source or categorical/time-field Ibis expressions:

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
nameable source/attribute concept, extract it into an Entity or Dimension/TimeDimension and
reference that, rather than inlining it in a metric body. These decorators do not
require provenance; their trust comes from the owning entity, expression
readability, and materialization checks.

### Measure as the authority for additivity and unit

`measure_column` declares a physical numeric leaf, unit, sum permission and any
status-time role. It returns a typed Measure capture Ref. After the complete
physical scope is finalized, `finalized.resolve(ref)` returns the immutable
Measure row value. `status_time_fold` is retired;
the status axis and sampling interval stay on the source declaration and temporal
reduction is explicit in the consuming expression.

```python
@ms.measure(additivity=ms.additive_all())
def order_amount():
    return quantity * unit_price

@ms.measure()
def margin():
    return order_amount - order_cost
```

Omitted unit/additivity or None inherits existing facts. Same-unit linear row
arithmetic intersects permission; constant scaling preserves it. Row products
and quotients do not acquire permission. An explicit new Measure may assert a
compatible unit or new business sum permission, with versioned authorization
and original dependencies retained in its graph. Unknown units remain unknown
until explicitly asserted. Unit labels do not convert currency or scale values.
Unknown/foreign permission axes, incompatible known units and grants that erase
fixed time constraints are rejected. A sum cannot repair missing authority.

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
expressible there, and DuckDB receives Python strptime unchanged.

`ms.strptime(format)` also declares canonical source encoding for every field,
not just partition columns: directive widths and zero padding, literal separators,
and valid calendar values must match the format. `%Y%m%d` requires eight digits
such as `20260701`; `2026071` is outside the contract even if a backend accepts it.
An integer encoding must have the same base-ten digits without losing leading
zeros. Format validation does not certify source rows, and analysis does not add
partition enumeration, validation queries, transactions or per-row round trips.
Values excluded by a generated range are not checked for violations.

For an explicit half-open consumer window, analysis can invert `%Y%m%d`,
`%Y-%m-%d`, `%Y%m%d%H`, `%Y-%m-%d %H`, `%Y%m%d%H%M`, `%Y-%m-%d %H:%M`,
`%Y%m%d%H%M%S`, and `%Y-%m-%d %H:%M:%S` into raw-column comparisons. Integer
columns admit only the compact all-digit formats. Civil dates keep their date
semantics; clock inverses require an existing UTC or fixed-offset authority.
Other formats, non-integral representations, `hour_prefix`, and named-zone
conversions without a fixed offset retain parsed filtering. Native parser
support and precision restrictions still apply. No new parse parameter is needed.

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

A Metric is a completed source reduction or arithmetic composition. Local and
named definitions use the same closed nodes and policies:

```python
@ms.metric(ai_context=ms.ai_context(business_definition="Paid revenue"))
def revenue():
    return amount.sum(time=paid_at).where(status == "paid")

@ms.metric()
def average_order_value():
    return revenue / orders.count(time=paid_at).where(status == "paid")
```

The decorator accepts only domain and ai_context. Roots, units, time roles and
policies come from the expression. domain is a typed namespace assertion, not a
Session binding. Names are local until model collection; aliases preserve both
local identity and computation. The original expression is not rewritten.
R1.4 owns Catalog Metric publication and discovery, which this construction
contract does not provide. Existing Runtime readers are not the new R2 route.

### Grain, root entity, and fan-out

Source receivers own contribution roots. Row arithmetic requires one exact
root; reduce different roots independently before Metric arithmetic. where is
valid only on one source reduction or common weighted pair scope, never a guessed
filter over a composite graph. `per(field | tuple)` produces internal samples
that must be reduced before being returned as a Metric.

`entities`, `root_entity` and `fanout_policy` are retired. Existence filtering
requires explicit Population preimage when that later capability is available.
Overlapping-label grouping has no equivalent in this version; it cannot be
silently converted to a semi-join or a different contribution allocation.

### Sampled semi-additive metrics

Keep source status-time axes, sampling intervals and fixed-axis permissions.
Use `source.sum().per(sample_time).quantile(q)` or the matching explicit sample
reduction for the intended spatial-before-temporal equation. Defaults do not
supply closing folds, forward filling or unknown coverage. `status_time_fold`
and aggregate `fold` are rejected even when equal to a former default.
Complete binding and execution checks belong to R2/R3.

### Fixed null and empty contracts

Policies belong to each actual operator. sum/count/count_distinct return 0 for
complete empty/all-NA input. Other admitted reducers return NA for no valid
sample. `nulls="ignore"` is the reducer default; explicit "error" checks selected
inputs. Division and weighted_mean default to zero_denominator="null"; explicit
"error" remains on that operation. None of these choices can be overridden by a
Metric decorator, Session or observation. Old `empty` and policy factories are
rejected; deleting an old empty="null" sum does not establish equivalence.

## Weighted means

A row value and weight share one contribution root and common non-NA pairs.
`value.weighted_mean(weight=weight, ...)` creates the same source node in local
and declared expressions. Internal samples instead require a completed Metric
weight. Neither shape licenses averaging previously completed means without
sufficient components.

## Derived metrics and decomposition

Use ordinary completed-Metric +, -, *, / and explicit divide for a non-default
zero-denominator policy. Row/Metric mixing is rejected. Repeat uses retain their
ordered graph edges. Derived expressions remain anonymous; a new declaration
is explicit. `ms.aggregate/count/weighted_mean/where/ratio/linear/cumulative`
are retired authoring factories and never translate old IR into new graphs.
Old Runtime IR/reader code retained for existing consumers is not authoring
compatibility or new execution qualification.

## Metric unit (UCUM)

Reuse the shared bounded unit algebra. Known incompatible linear units are
rejected; products and quotients derive units, unknown facts stay unknown.
A Measure assertion can add an explicit compatible business contract; a Metric
decorator has no global unit override.

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
every selected source member must match a target is a consuming operation's
business requirement, not a global Relationship field. The consumer must bind
its selected membership, exact version, and role before requiring a match; if
absence is allowed, that operation must define the resulting missing value or
member behavior. An operation with no such rule cannot infer completeness from
the mapping. Static loading cannot derive or prove matches from physical
metadata or a sample. Every `keys` pair resolves to a
direct-column Dimension or TimeDimension ref on its exact endpoint, without
interpreting a semantic ref path as a physical column.
For a versioned endpoint, this is conditional on the consuming operation's
exact snapshot or validity selection. Loading checks these declarations without
reading rows. Analysis trusts declared multiplicity/version grain; unknown
required matching is retained as an exact unverified consuming-contract assumption. A weaker
many side never grants fanout safety. Derived
cardinality grants no evidence of actual rows. When completeness matters, an
explicit bounded `source_check.relationship_matches(...)` can test its declared
scope; source-health results do not become a global business guarantee.
Incorrect declarations do not promise detection. Necessary consumer indexes
reject encountered duplicates, and required lookups reject missing operands.
An allowed absent match needs its consumer's explicit result semantics.

## Bounded acceptance cases for the amendment

1. A snapshot Entity with `K=(user_id,)` admits repeated users across dates,
   trusts `(user_id, snapshot)` row uniqueness, resolves one exact snapshot,
   and uses the consuming operation's empty result when it is absent. A physically
   partitioned non-versioned fact Entity acquires no snapshot semantics.
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

### Definition-owned aggregate exactness

A Metric definition is the sole owner of aggregation intent. `count_distinct`,
`median` and `("percentile", q)` select non-approximate operations;
`approx_count_distinct`, `approx_median` and `("approx_percentile", q)` explicitly
permit approximate operations. Observation has no `method` or `accuracy` override,
and `quantile_metric`/`QuantileMetricInput` are removed from the public surface.
The corresponding Ibis operations express the same distinction in opaque bodies;
this does not authorize parsing Python bodies into contribution graphs.

Source aggregation executes as SQL compiled by Ibis. Quantiles use source-native
arithmetic: large integers can lose floating precision and Decimal interpolation
can retain the source scale. These numerical limitations are disclosed, not
repaired by fetching contributions for Python sorting or interpolation. The
non-approximate operation does not promise arbitrary-precision arithmetic.
Unsupported exact operations fail with the corresponding approximate declaration,
preserving q, and say whether that backend supports the alternative. No definition
is substituted automatically. Backend translation alone never qualifies a new
Analysis execution route.

`load()` preserves a structurally valid aggregate declaration. Scoped
`catalog.readiness()` rejects known direct-only aggregate/backend
incompatibilities with `aggregate_backend_unsupported` before opening a source,
including dependencies of derived Metrics and closed runtime expressions.
It reuses the same provider-accuracy and independent Ibis translation check as
materialization. On Trino, exact median/percentile is unavailable in Marivo's
source-native route; `approx_median` or `("approx_percentile", q)` must be authored
explicitly when acceptable. Passing static readiness grants no physical-type or
operation-specific execution qualification, and consumers still recheck support.

## Analysis Semantic handoff

The [Analysis contract](../analysis/python-analysis-design.md#observation-and-composition)
owns consuming input shapes; [method contracts](../analysis/operators-and-frames.md)
own sufficient state, numerical policies and conditional continuations.

### Identity, fields and version facts

The ordered complete Entity primary key is stable identity K. Snapshot and
validity coordinates select representations and never silently extend or shorten
K. Root membership trusts declared uniqueness and projects all of K without
`distinct` or a new whole-source uniqueness probe. Invalid source declarations
may affect native results; required local indexes reject encountered duplicate
insertions rather than retaining the first row or silently deduplicating.

Snapshot selection is the exact declared grain/timezone period. A missing exact
snapshot yields an empty represented membership, never a last-known snapshot;
it does not prove that a business population or contribution window is complete.
Validity selection follows the declared interval and open-end values. For
closed-open intervals, instant selection is `from <= at < to`, and left-limit
selection is `from < end <= to`. Missing matches preserve absence; required read
coverage and method-specific empty-result rules decide whether consumption is
legal. No-version Entities reject historical selection rather than manufacturing
history. Version-row uniqueness and non-overlap remain trusted declarations,
not an automatically completed source audit.

Measure, categorical Dimension, TimeDimension, and boolean-valued Dimension
retain their distinct value kinds through read. A boolean Dimension is not
inferred from an integer 0/1 column; its resolved logical type must be boolean.
Attribute version facts, full correspondence keys, relationship role and
definition fingerprint accompany the field dependency. A many-valued mapping
does not become a scalar read through an implicit aggregate. Selecting attributes
at a different explicit anchor preserves that independent binding; the chosen
membership anchor alone does not certify attribute or contribution coverage.

### Metric occurrences and policies

The canonical graph binds each component occurrence independently: its root,
base Measure/Entity Ref, branch filter, relationship roles, version/time facts,
contribution unit, value unit, declared Cell policy, and actual source binding.
Shared graph definitions do not merge distinct occurrences with different filters.
Ratio, linear, weighted mean and cumulative consumers resolve that graph rather
than reconstructing it from function bodies, aliases, output names or equal values.
Opaque Metrics expose only explicitly declared policies and actually retained
state. Missing declarations remain missing and block the dependent operation.

Metric mean retains sum, non-null count and row count; Metric weighted mean
retains weighted numerator and weight sum over the same non-null pairs, plus
the pair/row facts needed by its declared empty policy. Null pairs are excluded
together, unlike strict current-row statistics. Existing Metric empty/Null and
zero-weight policies remain authored facts; the withdrawn statistical-weight
proposal does not change them. Runtime ratio `zero_division="null"` maps to the
canonical undefined-zero-denominator policy, while `"error"` rejects; neither
means a Defined zero or a Null Cell. Named original components survive finish.

Exact distinct counts its declared value identity (an Entity identity uses all
of K), excludes Null according to the count-distinct policy, and yields zero on
admitted empty input. Median/percentile excludes Null values and rejects nonfinite
numeric contributions. The source-native continuous quantile uses linear
interpolation at `h=(n-1)*q`; median fixes `q=0.5`. Empty support yields Null.
Percentile q is finite with `0 < q < 1`; bool is rejected. Numerical precision is
source-owned as described above. The definition distinguishes exact and approximate
operations through AggKind, not an observation wrapper. Actual algorithm and
physical output type remain inspectable; no invented error bound, retained
distribution/sketch, original rollup or attribution is authorized by this slice.

## Relation-composition handoff

Analysis composition consumes governed Metric/runtime-expression identity, units, contribution
roles, relationship cardinality, dimension tuple identity, additive partition
and component/empty policies without changing Semantic declarations. A declared
one-to-one relationship is necessary for an explicit ordinary-ratio
correspondence; actual complete bijection is checked at execution. Equal column
names or Entity types cannot replace either fact. Definition templates retain
ordered occurrences and policies; comparison time bindings, selected cohorts,
references, rankings and attribution reconciliation belong to Analysis state,
not new Catalog objects.

Metric empty-contribution policy is consumed only with complete observation and
coverage evidence. It is distinct from Analysis empty-opportunity policy.
ReferenceWeights binds an existing Entity as statistical unit and existing
Dimension identities as strata; it does not add a named statistical-weight role.
The withdrawn ms.statistical_weight, mv.statistical_weight and dependent
current-row weighted mean remain excluded. Composition does not reinterpret direct
distinct/quantile display values as membership/distribution sufficient state.

## Immutable domain captures

The private unified graph freezes governed Entity, Dimension and Metric
contracts with fingerprints of the definitions and dependencies actually used.
These identities describe the computation; they are not a requirement to reject
subsequent definition changes. Frozen declarations do not establish source
completeness or physical execution qualification. Cache correctness and retained
record integrity remain required under their existing owners.

Analysis owns the closed time-key alignment family and its output coordinates;
see the [comparison contract](../analysis/timezone-and-calendar-design.md#period-correspondence).
Semantic TemporalSet occurrences already carry the exact Ref, key, bounds,
timezone and snapshot digest needed by analysis. Compare fixes checked
correspondence and publishes both absolute and relative Cells. Metric empty finish
is requested explicitly through `mv.outer(missing="empty_value")` and requires
retained complete original state.
