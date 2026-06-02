# marivo-semantic authoring patterns

Use these patterns when writing `.marivo/semantic/<model>/_model.py`.

## Single model file

```python
import marivo.datasource as md
import marivo.semantic as ms

ms.model(name="sales")
warehouse = md.ref("warehouse")

orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Exclude test orders when the table exposes a test flag."],
    },
)

@ms.time_field(
    dataset=orders,
    name="log_date",
    data_type="string",
    granularity="day",
    date_format="yyyymmdd",
    ai_context={
        "business_definition": "Partition date used for default order reporting windows.",
        "guardrails": ["Use event time instead only when source SQL defines that axis."],
    },
)
def log_date(table):
    return table.dt

@ms.time_field(
    dataset=orders,
    name="log_hour",
    data_type="string",
    granularity="hour",
    date_format="HH",
    required_prefix="log_date",
    ai_context={
        "business_definition": "Hour partition used with log_date for hourly reporting windows.",
        "guardrails": ["Use full event timestamp only when source SQL defines that axis."],
    },
)
def log_hour(table):
    return table.hh

@ms.field(
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

## Ref defaults

- Use `md.ref("<datasource>")` for datasource references.
- Use decorated Python variables such as `orders`, `order_date`, and `revenue`
  between semantic objects.
- Use string `ms.ref(...)` only for forward references, cross-model boundaries,
  or generated tooling cases.

## Time field priority

Prefer datasource partition fields such as `dt`, `log_date`, or `event_date` for
dataset time fields. Use event time, creation time, update time, ingestion time,
or snapshot time instead only when knowledge, source SQL, comments, or user
confirmation establishes that business axis.

For day/hour partition fields, preserve the raw sortable partition value and
declare its physical encoding with `date_format`. This lets observe windows
compile to simple partition comparisons for predicate pushdown:

```python
@ms.time_field(dataset=orders, name="log_date", data_type="string", granularity="day", date_format="yyyymmdd")
def log_date(table):
    return table.dt

@ms.time_field(
    dataset=orders,
    name="log_hour",
    data_type="string",
    granularity="hour",
    date_format="HH",
    required_prefix="log_date",
)
def log_hour(table):
    return table.hh
```

Complex event-time expressions are still valid when they are the established
business axis, but they are not the partition field default and may not preserve
predicate pushdown:

```python
@ms.time_field(dataset=orders, data_type="date", granularity="day")
def event_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

When the body returns a date-typed expression (via `.cast("date")`), declare
`data_type="date"`. Declaring `data_type="datetime"` with a `.cast("date")`
body causes a TypeError at execution because ibis cannot add intervals to
DateColumns.

For Trino VARCHAR datetime columns storing values like `"2025-04-04 06:59:59"`,
do not cast VARCHAR directly to DATE. Parse through timestamp first:

```python
@ms.time_field(dataset=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

## Relationships

Relationship keys must use field or time-field refs, not physical column-name
strings:

```python
@ms.field(dataset=orders, name="customer_id")
def order_customer_id(table):
    return table.customer_id

@ms.field(dataset=customers, name="customer_id")
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
`ms.help("metric", format="json")` and
`ms.help("decomposition", format="json")`. The supported decomposition builders
come from runtime help; do not invent `ms.count()` or `ms.mean()`.

| Business shape | Metric body | Decomposition |
| --- | --- | --- |
| Additive amount | `.sum()` or another dataset-backed reduction | `ms.sum()` |
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
| `active_loader_context` | declarations must load from project files | `references/examples/01_single_model_file.py` |
| `active_model_required` | semantic ids need a model namespace | `references/examples/01_single_model_file.py` |
| `unique_semantic_name` | ids must stay unique within their kind scope; fields are dataset-scoped | `references/examples/01_single_model_file.py` |
| `ref_shape` | refs must point at the intended object kind | `references/examples/01_single_model_file.py` |
| `decomposition_shape` | metrics need supported decomposition builders | `references/examples/01_single_model_file.py` |
| `metric_datasets_required` | base metrics must declare datasets | `references/examples/01_single_model_file.py` |
| `metric_component_scope` | component-body calls are no longer supported in metric bodies | `references/examples/01_single_model_file.py` |
| `ai_context_schema` | handoff metadata must use supported fields | `references/examples/01_single_model_file.py` |
| `ast_single_return` | decorator bodies stay one safe expression | `references/examples/01_single_model_file.py` |
| `ast_forbidden_statement` | decorator bodies cannot hide arbitrary code | `references/examples/01_single_model_file.py` |
| `ast_sql_escape_hatch` | Python-track bodies must avoid raw SQL calls | `references/examples/01_single_model_file.py` |
| `model_file_present` | every model directory needs `_model.py` | `references/examples/01_single_model_file.py` |
| `dataset_ref_exists` | dataset datasource refs must resolve | `references/examples/01_single_model_file.py` |
| `metric_ref_exists` | decomposition refs must resolve | `references/examples/01_single_model_file.py` |
| `hour_time_field_prefix` | hour-only fields need a day prefix | `references/examples/01_single_model_file.py` |
