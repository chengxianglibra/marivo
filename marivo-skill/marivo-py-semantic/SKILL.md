---
name: marivo-py-semantic
description: Use when the task involves declaring a Marivo semantic model -- datasource, dataset, field, metric, or relationship.
---

# marivo-py-semantic

Use this skill when writing or running Python code that declares a Marivo
semantic model: datasources, datasets, fields, time fields, metrics, and
relationships via `marivo.semantic_py`.

For running analyses on top of an already-declared model, switch to
`marivo-py-analysis`. Modeling is owned here; analysis is owned there.

## Prerequisite

You need `marivo` installed in the active Python environment, plus `ibis` and
one or more backend drivers for the datasources you intend to declare, such as
DuckDB for this skill's examples:

```bash
pip install marivo ibis-framework duckdb
```

If the surrounding project pins a virtualenv, install into that venv. This skill
does not assume you have the Marivo source checkout.

## 30-Second Overview

```python
import ibis
import marivo.semantic_py as ms

# In .marivo/datasource/tiny_orders.py:
# import marivo.datasource_py as md
# md.datasource(name="tiny_orders", backend_type="duckdb", path=":memory:")
#
# In a _model.py file inside .marivo/semantic/sales/:
ms.model(name="sales")

# In a sibling .py file:
@ms.dataset(name="orders", datasource="tiny_orders", primary_key=["order_id"])
def orders(backend):
    return backend.table("orders")

@ms.time_field(dataset=orders, data_type="date", granularity="day")
def created_at(orders):
    return orders.created_at.cast("date")

@ms.field(dataset=orders)
def region(orders):
    return orders.region

@ms.metric(datasets=[orders], decomposition=ms.sum(), name="revenue")
def revenue(orders):
    return orders.amount.sum()

@ms.metric(datasets=[orders], decomposition=ms.sum(), name="orders_count")
def orders_count(orders):
    return orders.count()

@ms.metric(
    datasets=[],
    decomposition=ms.ratio(
        numerator="sales.revenue",
        denominator="sales.orders_count",
    ),
    name="aov",
)
def aov():
    return ms.component("numerator") / ms.component("denominator")
```

After loading the project:

```python
project = ms.find_project()  # or ms.SemanticProject(root="/path")
project.load()
project.list_datasources()
project.list_metrics()
project.describe("sales.revenue")
```

Every decorator registers on the active model opened by `ms.model(name=...)`.
Production projects load files from `.marivo/semantic/<model>/`.

## Pre-Modeling Checklist

Before writing any semantic-layer Python file, complete every step below. Do
not declare datasets, fields, time fields, metrics, or relationships until this
checklist is done.

1. Check existing project datasources.

   Run `mv.datasources.list()` to see whether the datasource already exists in
   `.marivo/datasource`. Reuse an existing datasource without asking the user.

2. Establish the backend connection.

   Use the user-provided connection information to create an ibis backend, and
   continue only after the connection succeeds.

3. Fetch column names, types, and comments.

   `table.schema()` returns types but not comments. Before modeling, fetch
   comments from the datasource metadata catalog; comments are the primary
   source for business meaning, and semantic meaning must not be inferred from
   column names alone.

   Use the metadata query that matches the datasource, for example Trino
   `information_schema.columns`, MySQL `SHOW FULL COLUMNS`, or DuckDB
   `PRAGMA table_info`.

4. Preview time and partition column values.

   For VARCHAR/string columns whose name or comment implies date/time meaning
   such as `log_date`, `create_time`, `dt`, or `hr`, preview a few rows before
   choosing cast expressions or granularity. This prevents runtime cast errors.

5. Choose time fields and partition columns.

   Decide from metadata and samples which column is the primary time axis,
   whether a date+hour composite pattern exists, and which cast expression each
   time field needs. Prefer partition columns such as `log_date` or
   `log_date` + `log_hour` when they match the business time axis.

6. Ask only for information that cannot be fetched.

   Ask for connection parameters only when no profile exists, and ask business
   intent only when comments and supplied context are insufficient. Column
   structure, profile existence, and metadata access are agent responsibilities.

## Time Field Choice

When a table has an available time partition field such as `log_date`, `dt`,
or `event_date`, prefer declaring that partition as the dataset's
`@ms.time_field`. Partition fields usually give Marivo the most stable time
filtering and partition pruning path.

Use event time, creation time, update time, ingestion time, or snapshot time
instead only when the user or knowledge base explicitly defines that business
time axis. Record the reason in `description=` or a short model comment.

For Trino VARCHAR datetime columns storing values like `"2025-04-04 06:59:59"`,
do not use a direct `.cast("date")`; Trino rejects VARCHAR-to-DATE direct
casts. Parse through timestamp first, for example
`.cast("timestamp").cast("date")`.

## Standard Workflow

1. Check the project status before adding anything new.

   ```bash
   python -m marivo.semantic_py check
   ```

2. Write model declarations in Python files under
   `.marivo/semantic/<model>/`. Each model directory needs a `_model.py`
   that calls `ms.model(name=...)`.

3. Typecheck before running. Marivo decorators preserve function signatures,
   so type errors usually point at bad datasource/dataset wiring early.

4. Re-check after edits.

   ```bash
   python -m marivo.semantic_py check --format=json
   ```

5. On Marivo exceptions, read the structured error text. Semantic exceptions
   include a `hint` section when a copyable fix is known.

## Fill-In Templates

### Register a Datasource

Runnable reference: `references/examples/01_register_datasource.py`.
Before declaring a dataset, read `references/datasource.md`: semantic models
can only reference project datasources declared in `.marivo/datasource/*.py`.

Credentials never enter the semantic file. Persist non-secret connection
metadata with `mv.datasources.set(...)` or by writing
`.marivo/datasource/<name>.py`; sensitive fields use
`<field>_env="VAR_NAME"`. See `references/datasource.md`.

```python
import marivo.datasource_py as md

md.datasource(name="<datasource_name>", backend_type="duckdb", path=":memory:")
```

### Declare a Dataset

Runnable reference: `references/examples/02_declare_dataset.py`.

```python
import marivo.semantic_py as ms

@ms.dataset(name="<dataset_name>", datasource=warehouse, primary_key=["<pk_col>"])
def <dataset_name>(backend):
    return backend.table("<physical_table>")
```

### Define an Aggregate Metric

Runnable reference: `references/examples/03_define_metric_aggregate.py`.

```python
import marivo.semantic_py as ms

@ms.metric(datasets=[orders], decomposition=ms.sum(), name="<metric_name>")
def <metric_name>(orders):
    return orders.<column>.sum()
```

### Define a Derived Ratio Metric

Runnable reference: `references/examples/04_define_metric_derived.py`.

```python
import marivo.semantic_py as ms

@ms.metric(datasets=[orders], decomposition=ms.sum(), name="numerator_metric")
def numerator_metric(orders):
    return orders.amount.sum()

@ms.metric(datasets=[orders], decomposition=ms.sum(), name="denominator_metric")
def denominator_metric(orders):
    return orders.count()

@ms.metric(
    datasets=[],
    decomposition=ms.ratio(
        numerator="sales.numerator_metric",
        denominator="sales.denominator_metric",
    ),
    name="<derived_name>",
)
def <derived_name>():
    return ms.component("numerator") / ms.component("denominator")
```

`datasets=[]` marks a derived metric: the body combines registered component
metrics with `ms.component(...)` instead of reading a dataset directly. Derived
metrics can be observed with `dimensions=` only when every component metric can
reach that dimension through its datasets and a unique relationship path.

## Decision Tree

```text
How is this value computed?
  From one row at a time, such as user.country
    -> field
  Aggregation over a dataset, such as sum/count/average
    -> metric with decomposition=ms.sum() or another decomposition marker
  Composition of already registered metrics
    -> metric with decomposition=ms.ratio(...) or ms.weighted_average(...)
```

## Common Pitfalls

- Dataset references a datasource that has no `.marivo/datasource/*.py`
  declaration. The loader reports a `missing_dataset_ref` error.
  Runnable reference:
  `references/examples/99_pitfall_dataset_without_datasource.py`.
- Decorators need an active model opened by `ms.model(name=...)`.
- `ms.datasource(...)` has been removed. Put datasource config under
  `.marivo/datasource` and reference its name from `@ms.dataset`.
- Metric bodies return ibis expressions. Do not aggregate an aggregate result:
  `orders.count().mean()` is invalid; use a row-level boolean/float expression
  followed by one aggregate, such as `(orders.state == "FAILED").cast("float64").mean()`.
- For string time fields on Trino, build an instance expression explicitly
  before parsing/casting. Do not call ibis expression methods as class methods,
  such as `ibis.expr.types.StringValue.re_replace(...)`.

## Further Reading

- `references/datasource.md` -- project datasource rules and required fields
- `references/cheatsheet.md` -- decorators, builders, CLI, introspection
- `references/pitfalls.md` -- expanded exception explanations
- `references/examples/` -- runnable files, one per template

## Error → example reference

When authoring raises one of the structured `SemanticError` subclasses below,
open the listed example to see the correct pattern.

| Error kind | What it means | See |
|---|---|---|
| `missing_dataset_ref` | Dataset references a datasource with no `.marivo/datasource/*.py` declaration | `references/examples/99_pitfall_dataset_without_datasource.py` |
| `MISSING_MODEL` | Decorator called outside `ms.model(...)` namespace | `references/examples/02_declare_dataset.py` |
| `INVALID_REF` | `@ms.dataset(datasource=...)` got a non-string, or `ms.datasource(...)` was called (removed) | `references/datasource.md` |
| `INVALID_COMPONENT_BODY` | Aggregate metric body used `ms.component()`, or `datasets=[]` without components | `references/examples/04_define_metric_derived.py` |
| `INVALID_COMPONENT_NAME` | `ms.component("x")` references a name not declared in the metric's decomposition | `references/examples/04_define_metric_derived.py` |
| `OUTSIDE_DERIVED_METRIC_BODY` | `ms.component(...)` called outside a derived metric body | `references/examples/04_define_metric_derived.py` |
| `DUPLICATE_NAME` | Two objects share the same `<model>.<name>` | `references/cheatsheet.md` |
| AST whitelist violations | Body uses control flow, imports, or non-single-return form | `references/examples/03_define_metric_aggregate.py` |
