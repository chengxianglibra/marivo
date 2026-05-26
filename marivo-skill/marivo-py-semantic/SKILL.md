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

# In a _model.py file inside .marivo/semantic/sales/:
ms.model(name="sales")

# In a sibling .py file:
warehouse = ms.datasource(name="tiny_orders", backend_type="duckdb")

@ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
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
If the user has not provided enough datasource grounding, read
`references/datasource.md` first and ask for the minimum missing identity,
backend type, and physical relation details before writing the model.

Credentials never enter the semantic file. After the user supplies the
connection metadata, persist it once via `mv.profiles.set(...)` from
`marivo.analysis_py` so analysis sessions resolve the backend automatically;
sensitive fields use `<field>_env="VAR_NAME"`. See
[`marivo-py-analysis/references/profiles.md`](../marivo-py-analysis/references/profiles.md).

```python
import marivo.semantic_py as ms

ms.model(name="<model_name>")
warehouse = ms.datasource(name="<datasource_name>", backend_type="<duckdb|trino|mysql|...>")
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

- Dataset references a datasource that was never declared with
  `ms.datasource`. The loader reports a `missing_dataset_ref` error.
  Runnable reference:
  `references/examples/99_pitfall_dataset_without_datasource.py`.
- Decorators need an active model opened by `ms.model(name=...)`.
- `ms.datasource(...)` is a top-level metadata declaration, not a connection
  factory. Runtime execution and analysis need a separate live Ibis backend
  supplied by the caller, such as through `backend_factory`.

## Further Reading

- `references/datasource.md` -- datasource intake, DuckDB/Trino examples, backend boundary
- `references/cheatsheet.md` -- decorators, builders, CLI, introspection
- `references/pitfalls.md` -- expanded exception explanations
- `references/examples/` -- runnable files, one per template
