# marivo-semantic authoring patterns

Use these patterns when writing `models/semantic/<domain>/_domain.py`.

## Provenance

Use `provenance=ms.from_sql(sql=..., dialect=...)` for SQL parity provenance. The old `source_sql` and `source_dialect` kwargs are not part of the public authoring surface.

## Single domain file

```python
import marivo.datasource as md
import marivo.semantic as ms

ms.domain(name="sales")
warehouse = md.ref("warehouse")

orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(
        business_definition="One row per order.",
        guardrails=["Exclude test orders when the table exposes a test flag."],
    ),
)

@ms.time_dimension(
    entity=orders,
    name="log_date",
    granularity="day",
    parse=ms.strptime("%Y%m%d"),
    ai_context=ms.ai_context(
        business_definition="Partition date used for default order reporting windows.",
        guardrails=["Use event time instead only when source SQL defines that axis."],
    ),
)
def log_date(table):
    return table.dt

@ms.time_dimension(
    entity=orders,
    name="log_hour",
    granularity="hour",
    parse=ms.hour_prefix("log_date"),
    ai_context=ms.ai_context(
        business_definition="Hour partition used with log_date for hourly reporting windows.",
        guardrails=["Use full event timestamp only when source SQL defines that axis."],
    ),
)
def log_hour(table):
    return table.hh

@ms.dimension(
    entity=orders,
    name="region",
    ai_context=ms.ai_context(
        business_definition="Sales reporting region.",
        guardrails=["Do not treat missing region as a separate market."],
    ),
)
def region(table):
    return table.region

@ms.metric(
    entities=[orders],
    additivity="additive",
    name="revenue",
    provenance=ms.from_sql(
        sql="SELECT SUM(amount) AS revenue FROM orders",
        dialect="duckdb",
    ),
    ai_context=ms.ai_context(
        business_definition="Gross order amount before refunds.",
        guardrails=["Validate refund exclusions before using this as net revenue."],
        synonyms=["sales", "gmv"],
        examples=["What was revenue by region last week?"],
    ),
)
def revenue(table):
    return table.amount.sum()
```

## description vs ai_context

`description` is a **short display summary** shown in listings and cards.
It is not a substitute for business meaning.

Business meaning, usage constraints, and agent guidance belong in `ai_context`:

```python
@ms.metric(
    entities=[orders],
    additivity="additive",
    description="Gross revenue.",       # short summary for display
    ai_context=ms.ai_context(
        business_definition="Sum of order amounts for completed orders.",
        guardrails=["Exclude refunded orders.", "Use status='complete' filter."],
        synonyms=["revenue", "net sales"],
    ),
)
def revenue(table):
    return table.amount.sum()
```

When `catalog.get("sales.revenue")` is called:
- `obj.description` -> `"Gross revenue."`
- `obj.context.business_definition` -> `"Sum of order amounts for completed orders."`
- `obj.context.guardrails` -> `["Exclude refunded orders.", ...]`

## Ref defaults

- Use `md.ref("<datasource>")` for datasource references.
- Use decorated Python variables such as `orders`, `order_date`, and `revenue`
  between semantic objects.
- Use string `ms.ref(...)` only for forward references, cross-domain boundaries,
  or generated tooling cases.

## Domain ref override

`ms.domain(name=...)` returns a `DomainRef`. Pass it as `domain=` on any
decorator to override the default domain context -- typically for objects
declared in a sibling file that belongs to a different domain:

```python
# sales/_domain.py
sales_ref = ms.domain(name="sales", description="Sales analytics")
```

```python
# sales/shared_dimensions.py
import marivo.semantic as ms

@ms.dimension(domain=sales_ref, entity=orders, name="region")
def region(table):
    return table.region
```

When all objects in a file share the default domain, omit `domain=` -- the
loader resolves the domain from the `_domain.py` context automatically.

## Time dimension priority

Prefer datasource partition dimensions such as `dt`, `log_date`, or `event_date` for
entity time dimensions. Use event time, creation time, update time, ingestion time,
or snapshot time instead only when knowledge, source SQL, comments, or user
confirmation establishes that business axis.

For day/hour partition dimensions, preserve the raw sortable partition value and
declare its physical encoding with `parse=ms.strptime(...)`. This lets observe windows
compile to simple partition comparisons for predicate pushdown. Do not add
`timezone` to day partition formats such as `%Y%m%d`; those values are
filtered as physical partition keys, not interpreted instants.

```python
@ms.time_dimension(entity=orders, name="log_date", granularity="day", parse=ms.strptime("%Y%m%d"))
def log_date(table):
    return table.dt

@ms.time_dimension(
    entity=orders,
    name="log_hour",
    granularity="hour",
    parse=ms.hour_prefix("log_date"),
)
def log_hour(table):
    return table.hh
```

Complex event-time expressions are still valid when they are the established
business axis, but they are not the partition dimension default and may not preserve
predicate pushdown:

```python
@ms.time_dimension(entity=orders, granularity="day")
def event_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

For string or integer event-time columns that include time-of-day, declare
the format and timezone inside `ms.strptime(...)`. `session.observe(...)` parses
the value as source-local time, converts it to the session report timezone,
and then applies sub-day bucketing:

For localizable wall-clock time fields, `ms.datetime()`, `ms.timestamp()`,
and time-bearing `ms.strptime(...)` may omit `timezone=`.
Omitted timezone means the datasource engine default timezone. Add
`timezone="UTC"` or another IANA name only when the source column's wall-clock
meaning differs from the datasource default.

```python
@ms.time_dimension(
    entity=orders,
    granularity="minute",
    parse=ms.strptime("%Y-%m-%d %H:%M:%S", timezone="UTC"),
)
def create_time(table):
    return table.create_time
```

When the body returns a date-typed expression (via `.cast("date")`), omit
`parse` — the parse variant is inferred from the column's ibis dtype at
analysis time. Using `parse=ms.datetime(...)` with a `.cast("date")`
body causes a TypeError at execution because ibis cannot add intervals to
DateColumns.

For Trino VARCHAR datetime columns storing values like `"2025-04-04 06:59:59"`,
do not cast VARCHAR directly to DATE. Parse through timestamp first:

```python
@ms.time_dimension(entity=orders, granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

## Relationships

Relationship keys must use dimension or time-dimension refs, not physical column-name
strings:

```python
@ms.dimension(entity=orders, name="customer_id")
def order_customer_id(table):
    return table.customer_id

@ms.dimension(entity=customers, name="customer_id")
def customer_id(table):
    return table.id

ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    keys=[ms.join_on(order_customer_id, customer_id)],
)
```

## Simple vs derived metrics

Marivo has two metric tiers with distinct authoring shapes:

| Tier | Authoring form | Has body? | Examples |
| --- | --- | --- | --- |
| Tier-1 aggregate | `ms.aggregate(measure=..., agg=...)` | No | default path for sum/count/mean over a verified measure |
| Tier-2 simple | `@ms.metric(entities=[...], additivity=...)` | Yes | escape hatch for ibis expression bodies |
| Derived ratio | `ms.ratio(name=..., numerator=..., denominator=...)` | No | percentage, per-unit rate |
| Derived weighted average | `ms.weighted_average(name=..., value=..., weight=...)` | No | weighted averages |
| Derived linear | `ms.linear(name=..., add=[...], subtract=[...])` | No | net = gross - refunds |

**Rule:** default to `ms.prepare_measure(...)`, `@ms.measure(...)`,
`ms.verify_object(measure_ref)`, then `ms.aggregate(...)`. Use `@ms.metric`
only when the metric needs an expression body; use `ms.ratio` /
`ms.weighted_average` / `ms.linear` for body-free derived metrics.

```python
@ms.measure(entity=orders, additivity="additive", unit="USD")
def amount(orders):
    return orders.amount

revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
```

```python
@ms.metric(
    entities=[orders],
    additivity="additive",
    name="orders_count",
)
def orders_count(table):
    return table.order_id.count()
```

Mean/average metrics are body-free derived metrics, not `ms.mean()`:

```python
@ms.metric(
    entities=[orders],
    additivity="additive",
    name="gross_revenue",
)
def gross_revenue(table):
    return table.amount.sum()

gross_revenue_per_order = ms.ratio(
    name="gross_revenue_per_order",
    numerator=gross_revenue,
    denominator=orders_count,
    ai_context=ms.ai_context(
        business_definition="Gross revenue divided by order count.",
    ),
)
```

## Derived metrics

Derived metrics use `ms.ratio` / `ms.weighted_average` / `ms.linear` and do not have Python bodies. For a runnable example, see `references/examples/04_derived_metrics.py`.

```python
aov = ms.ratio(
    name="aov",
    numerator="sales.revenue",
    denominator="sales.orders_count",
)
```

```python
net_revenue = ms.linear(
    name="net_revenue",
    add=[gross_revenue],
    subtract=[refunds],
)
```

## Semi-additive metrics

Use `ms.semi_additive(over=..., fold=...)` as the `additivity` value for
periodic snapshot facts such as bandwidth, capacity, inventory, or
device-reported rates:

```python
@ms.metric(
    entities=[bw_samples],
    additivity=ms.semi_additive(over=sample_ts, fold="mean"),
    unit="kbit/s",
)
def upstream_bw(bw_samples):
    return bw_samples.upstream_kbps.sum()
```

Rules:

- `additivity=ms.semi_additive(...)` always requires `over` (the status time dimension).
- `over` binds the business status/as-of time axis.
- `fold` requires that `over` declares `sample_interval` on the time dimension.
- `sample_interval` can be declared on `ms.datetime(...)`, `ms.timestamp(...)`,
  string/integer `ms.strptime(...)`, or `ms.hour_prefix(...)` time dimensions.
- `fold` is a metric definition choice, not an observe parameter.
- P95-style folds use `fold=("quantile", 0.95)` and are always
  recomputed from base samples for the requested grain.
- Do not author bare `additivity="semi_additive"` and do not use technical write
  times such as `created_at`, `updated_at`, or `ingest_time` as the status axis
  unless they are truly the business as-of time.

For already-summarized snapshot/status facts such as daily inventory, use
`fold="last"` or `fold="first"` without `sample_interval`:

```python
@ms.metric(
    entities=[inventory_daily],
    additivity=ms.semi_additive(over=snapshot_date, fold="last"),
)
def on_hand_units(inventory_daily):
    return inventory_daily.on_hand_units.sum()
```

## Constraint examples

| Constraint | Why it matters | Example |
| --- | --- | --- |
| `active_loader_context` | declarations must load from project files | `references/examples/01_single_domain_file.py` |
| `active_domain_required` | semantic ids need a domain namespace | `references/examples/01_single_domain_file.py` |
| `unique_semantic_name` | ids must stay unique within their kind scope; dimensions are entity-scoped | `references/examples/01_single_domain_file.py` |
| `ref_shape` | refs must point at the intended object kind | `references/examples/01_single_domain_file.py` |
| `composition_shape` | metrics need supported composition builders | `references/examples/01_single_domain_file.py` |
| `metric_entities_required` | simple metrics must declare entities | `references/examples/01_single_domain_file.py` |
| `metric_component_scope` | component-body calls are no longer supported in metric bodies | `references/examples/01_single_domain_file.py` |
| `ai_context_schema` | handoff metadata must use supported fields | `references/examples/01_single_domain_file.py` |
| `ast_single_return` | decorator bodies stay one safe expression | `references/examples/01_single_domain_file.py` |
| `ast_forbidden_statement` | decorator bodies cannot hide arbitrary code | `references/examples/01_single_domain_file.py` |
| `ast_sql_escape_hatch` | Python-track bodies must avoid raw SQL calls | `references/examples/01_single_domain_file.py` |
| `domain_file_present` | every domain directory needs `_domain.py` | `references/examples/01_single_domain_file.py` |
| `entity_ref_exists` | entity datasource refs must resolve | `references/examples/01_single_domain_file.py` |
| `metric_ref_exists` | composition refs must resolve | `references/examples/01_single_domain_file.py` |
| `time_granularity_parse_compatible` | time granularity must match parse variant | `references/examples/01_single_domain_file.py` |
| `provenance_dialect_required` | SQL provenance needs a dialect for parity | `references/examples/01_single_domain_file.py` |

## Metric unit authoring

`@ms.measure` / `@ms.metric` / `ms.aggregate` / `ms.ratio` /
`ms.weighted_average` / `ms.linear` accept optional `unit`
(UCUM case-sensitive code; bare ISO 4217 uppercase code = currency). The unit
describes emitted values exactly; nothing converts based on it.

**Declaration strategy:** declare `unit=` on the measure dimension
(authoritative site). Tier-1 and derived metrics inherit it automatically at
load; pass `unit=` on a metric only to override the derived value. For tier-2
(`@ms.metric`), there is no measure to derive from, so `unit=` is the direct
declaration. Count/count_distinct metrics do not derive a unit from their
measure — declare an explicit counted-noun annotation like `{order}`.

Fill `unit` only from explicit evidence:

- Column name suffixes: `_cents`, `_usd`, `_ms`, `_pct`.
- Column comments stating the unit (from `md.inspect_table` / `md.inspect_columns` results).
- `provenance` SQL conversion traces (e.g. `/100` on a cents column).
- Count metrics: the counted entity noun, singular, in braces — `{order}`.
- Ratio derived metrics: same-unit ratios cancel to `"1"` automatically; declare `%`
  only when the metric emits percentage points rather than fractions.

Leave `unit=None` and raise the existing `amount_unit` AuthoringQuestion when
evidence is ambiguous:

- Amount scale ambiguity (is `19900` cents or yuan?).
- Fraction vs percentage points (`0.85` vs `85`).
- Multi-currency tables (`amount` + `currency_code`): the metric has no
  constant unit unless the domain normalizes currency.
- Duration `ms` vs `s` without explicit evidence.

Inference is only a drafting aid; the field's semantics are an author
declaration. Backfill after the user answers.
