# Semantic Object Model

Status: draft design. This document defines the object contracts of
`marivo.semantic` (`ms`): the business objects a coding agent declares in Python
so that downstream analysis can reference stable, verified semantics. It is the
authority for *what each object is and what fields it carries*; the process of
building them is in [authoring-workflow.md](authoring-workflow.md), and how they
are loaded, read, and validated is in
[loading-validation-introspection.md](loading-validation-introspection.md).

See also:

- [overview.md](overview.md) — design goals and where the object model sits.
- [datasource-layer.md](datasource-layer.md) — the refs and evidence entities
  build on.
- `ms.help("<constructor>")` — the always-current static contract (required and
  optional parameters, defaults, omit rules) for any object below.

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
  `ms.Ref.<kind>(path)` for forward/cross-file references. Bare
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
  rejected at decoration time.
- **Fail closed.** If decoration, assembly, materialization, or parity cannot
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

A **semantic project** is the one explicit boundary — a `models/semantic/` root
with its own registry and load lock, loaded through `ms.load(...)`. An analysis
session binds to a project root rather than inferring one from the current
working directory.

**`ms.domain(...)`** declares a business-domain boundary (`sales`, `marketing`,
`subscription`). The domain name participates in downstream ids (`sales.revenue`).

```python
ms.domain(name="sales", owner="Mina Zhang", default=True,
          ai_context=ms.ai_context(business_definition="Sales analytics"))
```

- `owner` is required — the person accountable for the domain's semantic
  correctness.
- `default` defaults to `True`; sibling objects in the domain directory may then
  omit a repeated `domain=`. Set `default=False` to force every object to name
  its domain explicitly.
- A domain is declared once per `<root>/<domain>/_domain.py`, with `name` equal
  to the directory name.

## Entity

An entity is the logical view of a business entity or fact table. It binds a
`Ref[datasource]` and a physical source, and declares its key.

```python
warehouse = ms.Ref.datasource("warehouse")
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=md.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(business_definition="One row per order before metric-level filters."),
)
```

- `source` is a datasource-owned structured descriptor: `md.table(...)` for a
  backend table/view; `md.parquet(...)`, `md.csv(...)`, or `md.json(...)` for
  DuckDB file sources. CSV and JSON require typed physical `schema=` mappings.
- Entities have no Python body and no inline SQL view. A persisted SQL view must
  be exposed as a backend table via `source=md.table(...)`; one-off SQL
  transforms are out of scope.
- Do not push metric aggregation logic into an entity.

### Versioning: snapshot and validity

Entities may declare how their history is versioned.

**Snapshot** entities are periodic partitioned facts observed at the latest
available partition by default:

```python
user_profile_daily = ms.entity(
    name="user_profile_daily",
    datasource=warehouse,
    source=md.table("user_profile_daily"),
    primary_key=["user_id", "dt"],
    versioning=ms.snapshot(
        partition_field=ms.Ref.dimension("sales.user_profile_daily.dt"),
        grain="day",                 # snapshot cadence
        timezone="Asia/Shanghai",    # resolves "latest" on a real calendar
        format="%Y%m%d",             # on-disk partition encoding; omit for native date
    ),
)
```

Analysis joins against a snapshot entity use the partition matching the observe
window end.

**Validity-interval (SCD2)** entities declare `valid_from`/`valid_to` +
`interval` + `open_end`:

```python
user_history = ms.entity(
    name="user_history",
    datasource=warehouse,
    source=md.table("user_history"),
    primary_key=["user_id", "valid_from"],
    versioning=ms.validity(
        valid_from=valid_from,
        valid_to=valid_to,
        interval="closed_open",
        open_end=(None, "9999-12-31"),
        timezone="UTC",
    ),
)
```

`valid_from` must be part of `primary_key`, and both bounds must reference
declared dimensions on the same entity. `open_end` lists the values that mean
"still current". The planner subtracts both bounds from the effective key so a
fact→validity relationship can resolve many-to-one once the validity table is
collapsed to one row per `(key, anchor)`. (`current_flag`-style versioning is not
supported.)

## Dimension, time dimension, and measure

These are row-level objects: attributes, temporal axes, and numeric facts reused
by filters, grouping, relationships, and metric expressions.

Prefer the direct-column constructors for physical columns and the decorator
form only when a row-level Ibis expression is needed:

```python
region = ms.dimension_column(name="region", entity=orders, column="region")

@ms.dimension(entity=orders, ai_context=ms.ai_context(business_definition="Normalized region."))
def region_norm(orders):
    return orders.region.upper()

amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity="additive", unit="CNY",
)
```

If a row-level `.filter(...)`, `.cast(...)`, or multi-step chain represents a
nameable business concept, extract it into a dimension/time_dimension/measure and
reference that, rather than inlining it in a metric body. These decorators do not
require provenance; their trust comes from the owning entity, expression
readability, and materialization checks.

### Measure as the authority for additivity and unit

A **measure** is the authoritative declaration site for a row-level numeric
fact's `additivity` and physical `unit`. Tier-1 metrics aggregate a verified
measure; derived metrics propagate the unit via composition algebra.

### Time dimension

A time dimension is a dimension that explicitly carries time-axis metadata. Any
column used as a time window, grain, or calendar axis **must** be declared as a
time dimension — plain dimensions are never inferred as temporal from a name like
`dt`.

```python
dt = ms.time_dimension_column(name="dt", entity=orders, column="dt",
                              granularity="day", parse=ms.strptime("%Y%m%d"))
hh = ms.time_dimension_column(name="hh", entity=orders, column="hh",
                              granularity="hour", parse=ms.hour_prefix(dt))
```

- **`granularity`** ∈ `year | quarter | month | week | day | hour | minute |
  second`. `minute`/`second` require a `ms.datetime(...)`/`ms.timestamp(...)`
  parse; `hour` on a non-datetime column requires `ms.hour_prefix(...)`.
- **`parse`** may be omitted for native `date`/`datetime`/`timestamp` columns
  (the variant is inferred from the ibis dtype). `string`/`integer` columns must
  supply `ms.strptime(format)` or `ms.hour_prefix(prefix)`. The body's ibis dtype
  must be compatible with the parse variant.
- **`is_default`** (default `False`) marks the default time axis when an entity
  has several; `observe()` without an explicit `time_dimension=` uses it. At most
  one per entity — a second raises `SemanticLoadError`
  (`duplicate_default_time_dimension`).
- **`sample_interval`** on a parse marks a fixed-cadence sampled series (see
  sampled semi-additive metrics below).

#### Format specifiers: Python strptime vs MySQL

`parse` formats are authored as **Python strptime** (`%`-prefixed) and validated
at authoring time. At SQL-emission time Marivo translates them to the MySQL form
for MySQL-family backends (Trino `date_parse`, MySQL `STR_TO_DATE`) via
`python_to_mysql_strptime`, so one authored format works on every backend. DuckDB
receives Python strptime unchanged; Postgres uses ibis's own pattern translation.

The critical divergence is `%M`:

| Specifier | Python strptime | MySQL |
|---|---|---|
| `%M` | Minutes (00..59) | **Month name** (January..December) |
| `%i` | (not used) | Minutes (00..59) |

Authors always write `%M` for minutes; Marivo maps `%M`→`%i` for Trino/MySQL. Do
not author `%i` — it is not valid Python strptime and is rejected. Directives
whose meanings diverge without a safe mapping (`%W`, `%u`, `%Z`, `%c`, …) raise a
`WindowInvalidError` at SQL-emission time, pointing to a supported directive or a
native temporal column.

## Metric

Metrics come in two tiers. **Tier-1** is the default: declare and verify a
row-level measure, then aggregate it.

```python
@ms.measure(entity=orders, additivity="additive", unit="CNY",
            ai_context=ms.ai_context(business_definition="Paid order amount in CNY."))
def paid_amount(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount

revenue = ms.aggregate(name="revenue", measure=paid_amount, agg="sum")
```

`ms.aggregate(measure=..., agg=...)` supports `sum | min | max | mean | median |
percentile | count | count_distinct` (`ms.count(...)` is the counting shortcut).
`agg=("percentile", q)` follows backend support — Trino uses approximate
percentile (`APPROX_PERCENTILE`). Both `ms.aggregate` and `ms.count` accept an
optional `filter=ms.where(col=value, ...)` to restrict the aggregation to a
subset of rows (e.g. a failure or error subset) without a hand-written body.

**Tier-2** `@ms.metric(...)` is the expression escape hatch, used only when a
metric cannot be expressed as measure + aggregate. It declares dependencies with
`entities=[...]`; the function parameters are positional aliases injected in
`entities` order — parameter names never determine entity identity.

```python
@ms.metric(entities=[orders], additivity="additive",
           provenance=ms.from_sql(sql="select sum(amount) as value from orders where pay_status=1",
                                  dialect="duckdb"))
def paid_revenue(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount.sum()
```

### Grain, root entity, and fan-out

Every base metric declares `additivity`. Single-entity metrics resolve
`root_entity` automatically; multi-entity metrics must name `root_entity`
explicitly — it defines the preserved row set, the join anchor, and the observe
time axis. Aggregate receivers in a base body must belong to the root entity;
joined entities may still contribute dimensions and filters.

`@ms.metric(...)` accepts `fanout_policy`:

- `"block"` (default) rejects unsafe one-to-many edges with an `unsafe-fanout`
  repair payload naming `set_metric_root` and `set_fanout_policy` as candidate
  fixes.
- `"aggregate_then_join"` reduces the unsafe side to the merge grain before the
  join. Requested dimensions keep overlapping-bucket semantics; where-filters on
  that side give semi-join membership semantics (a root row with ≥1 match counts
  once). Requires `additivity in {additive, semi_additive}` and is rejected on
  derived metrics.

### Sampled semi-additive metrics

For periodic snapshot facts (bandwidth, capacity, inventory), the time dimension
declares physical precision (`granularity`) and reporting cadence
(`sample_interval`); the metric declares the business status axis and fold:

```python
sample_ts = ms.time_dimension_column(
    name="sample_ts", entity=bw_samples, column="sample_ts", granularity="second",
    parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),
)

@ms.metric(entities=[bw_samples], additivity="semi_additive",
           time_fold="mean", status_time_dimension=sample_ts, unit="kbit/s")
def upstream_bw(bw_samples):
    return bw_samples.upstream_kbps.sum()
```

The body expresses the spatial aggregate inside one sample point;
`status_time_dimension` binds the as-of/status axis; `time_fold` reduces the
sample series to the requested grain (P95-style folds use
`time_fold=("percentile", 0.95)`, always recomputed from base samples). Not every
semi-additive metric is sampled: already-summarized snapshots (e.g. daily
inventory) omit `time_fold` but must still declare `status_time_dimension`. A
bare `additivity="semi_additive"` metric without `status_time_dimension` is
invalid. The status axis must be a true business as-of time (`snapshot_date`,
`as_of_date`), not a technical write time (`created_at`, `ingest_time`).

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
    name="avg_execution_time", numerator=total_execution_time, denominator=query_count,
    unit="s", ai_context=ms.ai_context(business_definition="Average execution time per query."),
)
```

Shape classification fails closed: `@ms.metric` with an empty `entities` list is
an error; a call with neither `entities` nor composition components is an error.
Derived metrics cannot reference entities/dimensions/time dimensions directly —
package any intermediate values as base metrics first. They do no Python-side
zero-division handling; the generated Ibis follows backend SQL semantics (most
backends yield `NULL` on a zero denominator), so an explicit fallback must be
wrapped in a base metric.

### Recursive derived metrics

Derived metrics may consume other derived metrics. Authorization is checked at
each node: ratio, linear, and cumulative retain their own
child, unit, source, scope, and evaluation requirements. Semantic readiness
lowers the complete selected metric through the same graph contract used by
analysis and blocks graphs deeper than 10 nodes or wider than 256 pre-CSE
occurrences. A legal ratio of ratios is therefore analysis-ready; an illegal
child combination fails with the responsible dependency and occurrence path.

### Cumulative metrics

`ms.cumulative(...)` answers "how much accumulated up to bucket *t*". The base
must be a tier-1 `sum`/`count`/`count_distinct`/`weighted_mean` metric; `over=` selects the time
axis (required unless the base root entity has exactly one time dimension).

```python
cumulative_active_users = ms.cumulative(name="cumulative_active_users",
                                         base=active_users, over=event_time)
```

`count_distinct` bases use first-seen semantics (each entity counted at its
earliest bucket, so the running total is monotonic). The `anchor` selects the
accumulation shape:

| Anchor | Shape |
|---|---|
| `None` (default) | All-history running total; the observe window clips displayed rows but does not reset the value. |
| `ms.grain_to_date(grain=...)` | Resets at each `week`/`month`/`quarter`/`year` boundary (WTD/MTD/QTD/YTD). |
| `ms.trailing(count=..., unit=...)` | Fixed-size rolling window ending at each bucket; empty windows are true zero, partial windows are marked `partial`. |

`trailing` accepts only fixed-size units (`second`..`week`); calendar-variable
units are rejected with a teaching error pointing to `grain_to_date`.
Cross-anchor constraints: `grain_to_date` requires every display bucket to lie
within one reset period (a `week` grain under a `month` reset is illegal);
`trailing` requires the window span to be an integer multiple of the query grain.
Cumulative metrics may serve as ratio components; do not use cumulative over
`mean`, percentile, expression-body, or derived metrics.

A derived ratio/weighted/linear metric over cumulative components can be
compared only when every outer component is cumulative and all components share
the same `trailing` or `grain_to_date` anchor. `all_history`, mixed anchors, and
cumulative/non-cumulative mixes are rejected. This compare allowance does not
extend to `attribute`, `decompose`, or `forecast`.

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
label-independent). Units never affect computed values; `None` is always valid
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

## Provenance and parity

A metric's business origin is declared as
`provenance=ms.from_sql(sql=..., dialect=...)`. `verification_mode` is inferred:
when provenance is present, SQL parity verification is enabled; when absent, the
metric is trusted as semantically expressed.

| Provenance | Meaning | Parity status |
|---|---|---|
| `provenance=ms.from_sql(...)` | Migrated from SQL/BI/knowledge base | starts `unverified`; `verified` after `ms.parity_check(...)` passes, else `drifted` |
| (none) | Python/Ibis is the sole source | immediately `verified` (trusted) |

Provenance is single-dialect (use fixture-based parity tests for multi-dialect
needs). Derived metrics must omit provenance — they cannot be parity-checked
directly; their effective status propagates from components (all `verified` →
`verified`; any `drifted` → `drifted`; otherwise any `unverified` →
`unverified`). Parity status is a visible attribute on metrics, frames, and
details output. Adding a metric without provenance is allowed but is not a
"done" state — confirm the business source, and CI can forbid `unverified`
metrics via `--strict-provenance` (see
[loading-validation-introspection.md](loading-validation-introspection.md)).
