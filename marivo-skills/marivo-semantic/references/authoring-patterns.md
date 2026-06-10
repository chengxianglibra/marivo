# marivo-semantic authoring patterns

Use these patterns when writing `.marivo/semantic/<domain>/_domain.py`.

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
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Exclude test orders when the table exposes a test flag."],
    },
)

@ms.time_dimension(
    dataset=orders,
    name="log_date",
    data_type="string",
    granularity="day",
    date_format="%Y%m%d",
    ai_context={
        "business_definition": "Partition date used for default order reporting windows.",
        "guardrails": ["Use event time instead only when source SQL defines that axis."],
    },
)
def log_date(table):
    return table.dt

@ms.time_dimension(
    dataset=orders,
    name="log_hour",
    data_type="string",
    granularity="hour",
    required_prefix="log_date",
    ai_context={
        "business_definition": "Hour partition used with log_date for hourly reporting windows.",
        "guardrails": ["Use full event timestamp only when source SQL defines that axis."],
    },
)
def log_hour(table):
    return table.hh

@ms.dimension(
    dataset=orders,
    name="region",
    ai_context={
        "business_definition": "Sales reporting region.",
        "guardrails": ["Do not treat missing region as a separate market."],
    },
)
def region(table):
    return table.region

@ms.metric(
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="revenue",
    verification_mode="sql_parity",
    source_sql="SELECT SUM(amount) AS revenue FROM orders",
    source_dialect="duckdb",
    ai_context={
        "business_definition": "Gross order amount before refunds.",
        "guardrails": ["Validate refund exclusions before using this as net revenue."],
        "synonyms": ["sales", "gmv"],
        "examples": ["What was revenue by region last week?"],
    },
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
    datasets=[orders],
    decomposition=ms.sum(),
    description="Gross revenue.",       # short summary for display
    ai_context={
        "business_definition": "Sum of order amounts for completed orders.",
        "guardrails": ["Exclude refunded orders.", "Use status='complete' filter."],
        "synonyms": ["revenue", "net sales"],
    },
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

`ms.domain(name=...)` returns a `DomainRef`. Pass it as `model=` on any
decorator to override the default domain context -- typically for objects
declared in a sibling file that belongs to a different domain:

```python
# sales/_domain.py
sales_ref = ms.domain(name="sales", description="Sales analytics")
```

```python
# sales/shared_dimensions.py
import marivo.semantic as ms

@ms.dimension(model=sales_ref, dataset=orders, name="region")
def region(table):
    return table.region
```

When all objects in a file share the default domain, omit `model=` -- the
loader resolves the domain from the `_domain.py` context automatically.

## Time dimension priority

Prefer datasource partition dimensions such as `dt`, `log_date`, or `event_date` for
entity time dimensions. Use event time, creation time, update time, ingestion time,
or snapshot time instead only when knowledge, source SQL, comments, or user
confirmation establishes that business axis.

For day/hour partition dimensions, preserve the raw sortable partition value and
declare its physical encoding with `date_format`. This lets observe windows
compile to simple partition comparisons for predicate pushdown. Do not add
`timezone` to day partition encodings such as `%Y%m%d`; those values are
filtered as physical partition keys, not interpreted instants.

```python
@ms.time_dimension(dataset=orders, name="log_date", data_type="string", granularity="day", date_format="%Y%m%d")
def log_date(table):
    return table.dt

@ms.time_dimension(
    dataset=orders,
    name="log_hour",
    data_type="string",
    granularity="hour",
    required_prefix="log_date",
)
def log_hour(table):
    return table.hh
```

Complex event-time expressions are still valid when they are the established
business axis, but they are not the partition dimension default and may not preserve
predicate pushdown:

```python
@ms.time_dimension(dataset=orders, data_type="date", granularity="day")
def event_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

For string or integer event-time columns that include time-of-day, declare both
the physical `date_format` and source `timezone`. `session.observe(...)` parses
the value as source-local time, converts it to the analysis session timezone,
and then applies sub-day bucketing:

```python
@ms.time_dimension(
    dataset=orders,
    data_type="string",
    granularity="minute",
    date_format="%Y-%m-%d %H:%M:%S",
    timezone="UTC",
)
def create_time(table):
    return table.create_time
```

When the body returns a date-typed expression (via `.cast("date")`), declare
`data_type="date"`. Declaring `data_type="datetime"` with a `.cast("date")`
body causes a TypeError at execution because ibis cannot add intervals to
DateColumns.

For Trino VARCHAR datetime columns storing values like `"2025-04-04 06:59:59"`,
do not cast VARCHAR directly to DATE. Parse through timestamp first:

```python
@ms.time_dimension(dataset=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

## Relationships

Relationship keys must use dimension or time-dimension refs, not physical column-name
strings:

```python
@ms.dimension(dataset=orders, name="customer_id")
def order_customer_id(table):
    return table.customer_id

@ms.dimension(dataset=customers, name="customer_id")
def customer_id(table):
    return table.id

ms.relationship(
    name="orders_to_customers",
    from_dataset=orders,
    to_dataset=customers,
    from_fields=[order_customer_id],
    to_fields=[customer_id],
)
```

## Aggregation body vs decomposition

Metric decomposition is not SQL aggregation. Before authoring metrics, inspect
`ms.help("metric")` and
`ms.help("decomposition")`. The supported decomposition builders
come from runtime help; do not invent `ms.count()` or `ms.mean()`.

| Business shape | Metric body | Decomposition |
| --- | --- | --- |
| Additive amount | `.sum()` or another entity-backed reduction | `ms.sum()` |
| Count | `.count()` in the metric body | `ms.sum()` |
| Mean or average | `ms.derived_metric(..., decomposition=ms.ratio(...))` | `ms.ratio(...)` |
| Weighted average | `ms.derived_metric(..., decomposition=ms.weighted_average(...))` | `ms.weighted_average(...)` |

```python
@ms.metric(
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="orders_count",
verification_mode="python_native",)
def orders_count(table):
    return table.order_id.count()
```

Mean/average metrics are body-free derived metrics, not `ms.mean()`:

```python
@ms.metric(
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="gross_revenue",
verification_mode="python_native",)
def gross_revenue(table):
    return table.amount.sum()

gross_revenue_per_order = ms.derived_metric(
    name="gross_revenue_per_order",
    decomposition=ms.ratio(numerator=gross_revenue, denominator=orders_count),
    additivity="non_additive",
    ai_context={
        "business_definition": "Gross revenue divided by order count.",
    },
)
```

## Derived metrics

Derived metrics use `ms.derived_metric(...)` and do not have Python bodies:

```python
ms.derived_metric(
    name="aov",
    decomposition=ms.ratio(numerator="sales.revenue", denominator="sales.orders_count"),
)
```

## Constraint examples

| Constraint | Why it matters | Example |
| --- | --- | --- |
| `active_loader_context` | declarations must load from project files | `references/examples/01_single_domain_file.py` |
| `active_domain_required` | semantic ids need a domain namespace | `references/examples/01_single_domain_file.py` |
| `unique_semantic_name` | ids must stay unique within their kind scope; dimensions are entity-scoped | `references/examples/01_single_domain_file.py` |
| `ref_shape` | refs must point at the intended object kind | `references/examples/01_single_domain_file.py` |
| `decomposition_shape` | metrics need supported decomposition builders | `references/examples/01_single_domain_file.py` |
| `metric_datasets_required` | base metrics must declare entities | `references/examples/01_single_domain_file.py` |
| `metric_component_scope` | component-body calls are no longer supported in metric bodies | `references/examples/01_single_domain_file.py` |
| `ai_context_schema` | handoff metadata must use supported fields | `references/examples/01_single_domain_file.py` |
| `ast_single_return` | decorator bodies stay one safe expression | `references/examples/01_single_domain_file.py` |
| `ast_forbidden_statement` | decorator bodies cannot hide arbitrary code | `references/examples/01_single_domain_file.py` |
| `ast_sql_escape_hatch` | Python-track bodies must avoid raw SQL calls | `references/examples/01_single_domain_file.py` |
| `domain_file_present` | every domain directory needs `_domain.py` | `references/examples/01_single_domain_file.py` |
| `entity_ref_exists` | entity datasource refs must resolve | `references/examples/01_single_domain_file.py` |
| `metric_ref_exists` | decomposition refs must resolve | `references/examples/01_single_domain_file.py` |
| `hour_time_dimension_prefix` | hour-only dimensions need a day prefix | `references/examples/01_single_domain_file.py` |
