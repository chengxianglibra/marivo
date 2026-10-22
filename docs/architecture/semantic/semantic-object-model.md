# Semantic Object Model

Semantic declarations define reusable business identity, attributes, relationships
and computation. They describe governed meaning; source rows, runtime coverage and
backend qualification are checked by their respective consumers.

[Expressions](expressions.md) owns physical capture, native Deferred grammar,
Measure authorization and Metric equations. [Loading and validation](loading-validation-introspection.md)
owns Catalog acquisition and proof boundaries. [Temporal authority](temporal-authority.md)
owns certified calendars and occurrences.

## Object families

| Object | Owns | Downstream acquisition |
| --- | --- | --- |
| Domain | Explicit business namespace and accountable owner | catalog.domains |
| Entity | Source relation, stable ordered identity and optional versioning | catalog.entities |
| Dimension / TimeDimension | Categorical or explicitly temporal row meaning | catalog.fields |
| Measure | Numeric row quantity, unit and sum permission | catalog.fields |
| Metric | Completed reductions and their composition | catalog.metrics |
| Relationship | Directed endpoint/key mapping | catalog.relationships |

Physical constructors return native typed Deferred symbols. The loader binds
the complete dependency scope; Catalog lookup supplies immutable closed values.
Named Measure/Metric decorators return native Deferred recipes. A named expression is described with catalog.describe(ref),
not by calling show on Deferred. Collection lookup takes a plain local name or
full path string; local names must be unique within the current scoped collection.
Kind-prefixed strings and lookup symbols are not collection.get inputs.

## Project and domain

A **semantic project** is one workspace root loaded through `ms.load(workspace_dir=...)`.
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
`ms.entity(name=..., ...)` is an assignment-style constructor returning a
typed Entity symbol. Omitting `name` selects the restricted Ibis Table
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
  contributes to its dependency fingerprint. Fixed AnalysisFrames continue from
  retained data without reevaluating the Entity body or reconnecting sources.

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
        timezone="Asia/Shanghai",  # defines the exact snapshot-period boundary
    ),
)
```

The partition TimeField owns its encoding; snapshot requires explicit
`partition_field` and `timezone` and always uses business days. Label D applies
from D midnight until D+1 midnight in that timezone. For an end-of-day file D
whose values become effective the following day, derive effective DATE D+1 in
the authored source view before declaring snapshot; execution never shifts it.
At Shanghai midnight on October 1, instant selection chooses October 1 while
before-endpoint selection chooses September 30. At noon both choose October 1.

The source row key is derived as `(K, snapshot_coordinate)`. Within one selected
snapshot, `K` is unique. A consuming Analysis operation supplies one exact
temporal boundary value: either an instant or immediately before an excluded
endpoint. These are closed internal interpretations owned by the consuming
operation, not new semantic authoring arguments or an arbitrary timestamp-tick
subtraction. An instant selects the snapshot period containing it under the
declared business timezone. An immediately-before endpoint selects the period
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
or every expected member arrived. Consumer-required
checks and explicit source-health reports keep their actual scope; ordinary
observation does not certify real-world population completeness.

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

Both bounds reference TimeField values or TimeDimension symbols on the same Entity;
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
DATE validity bounds require explicit business timezone; timestamp validity
rejects a version timezone. Move naive source localization to each field's
`ms.timestamp(timezone=...)` parser; for aware sources delete the redundant
version timezone. Both bounds must have the same logical kind. The four
`valid_from`, `valid_to`, `interval` and `open_end` arguments remain required.
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

### Time dimension

A time dimension is a dimension that explicitly carries time-axis metadata. Any
column used as a time window, grain, or calendar axis **must** be declared as a
time dimension — plain dimensions are never inferred as temporal from a name like
`dt`.

```python
dt = ms.time_dimension_column(
    name="dt", entity=orders, column="dt", parse=ms.strptime("%Y%m%d")
)
hh = ms.time_dimension_column(
    name="hh", entity=orders, column="hh", parse=ms.hour_prefix(dt)
)
```

- **`parse`** interprets native temporal values or encoded values through
  `ms.timestamp`, `ms.strptime` or `ms.hour_prefix`. Native DATE/timestamp may
  omit parse; strings and integers require an explicit encoding. A date-only
  strptime format rejects timezone. A naive timestamp uses parser timezone or
  frozen reader authority; an aware timestamp rejects every parser timezone,
  including the physical zone, because its instant is already defined.
- **`is_default`** (default `False`) selects the Entity's default Metric time
  role. It does not select members, versions, output boundaries or windows.
  Only one field per Entity may be the default.
- **`sample_interval`** belongs to the field and optionally declares
  `(count, "minute" | "hour")`. The positive subday width must tile one nominal
  day and use a unique spelling: `(60, "minute")` requires `(1, "hour")`;
  `(24, "hour")` is outside this subday contract. DATE does not accept sampling;
  hour_prefix requires whole-hour spacing. Unknown native types are checked in
  preparation. [Sampled expressions](expressions.md#internal-samples-and-windows)
  own the reduction behavior.

DATE contribution roles require day-or-coarser output/reset and finite boundaries
at business midnight. Duration windows require positive integer multiples of
24 hours and still reject non-midnight actual boundaries, including across DST.
Use complete civil periods or a real timestamp source for other contributions.
A pure `field.within(scope)` retains its midnight-point predicate meaning.

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

## Context and immutable definitions

Use ms.ai_context(business_definition=..., guardrails=[...]) for business meaning
and usage limits. The fixed-schema value is shared by semantic declarations and
datasource specs. A docstring or historical SQL in context is descriptive text;
it cannot execute or establish verification status. Raw dicts are not context values.

Completed scopes resolve references by exact kind/path. Replacing a definition
at the same path does not require an original-version match against every
constructed input. Consumers enforce kind, root, grain, unit, time and null
correctness. Already closed expressions keep their captured immutable facts;
there is no refresh from ambient state or import replay. Fingerprints and cache
keys must describe the actual consumed definitions and dependencies.

Entity identity and historical version selection remain separate from that
policy. Analysis cannot manufacture missing historical representations, infer
relationship completeness from a declaration, or treat a field label as identity.

## Implementation map

The public constructors are in [authoring.py](../../../marivo/semantic/authoring.py);
physical records are in [ir.py](../../../marivo/semantic/ir.py).
[validator.py](../../../marivo/semantic/validator.py) restricts authored bodies,
[materializer.py](../../../marivo/semantic/materializer.py) constructs bound Ibis
relations, and [resolver.py](../../../marivo/semantic/resolver.py) resolves their
dependencies. Analysis consumes these definitions through its explicit
[Population and observation bindings](../analysis/populations-and-observation.md).
