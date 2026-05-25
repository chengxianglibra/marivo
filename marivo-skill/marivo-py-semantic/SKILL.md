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
from marivo.semantic_py.registry import SemanticProject, use_registry

project = SemanticProject(root=":example:")
with use_registry(project.registry):
    ms.model(name="sales")

    @ms.datasource(name="tiny_orders", backend_type="duckdb")
    def tiny_orders():
        return ibis.duckdb.connect(":memory:")

    @ms.dataset(name="orders", datasource=tiny_orders, primary_key=["order_id"])
    def orders(backend):
        return backend.table("orders")

    @ms.time_field(dataset="orders", data_type="date", granularity="day")
    def created_at(orders):
        return orders.created_at.cast("date")

    @ms.field(dataset="orders")
    def region(orders):
        return orders.region

    @ms.metric(decomposition=ms.sum(), name="revenue")
    def revenue(orders):
        return orders.amount.sum()

    @ms.metric(decomposition=ms.sum(), name="orders_count")
    def orders_count(orders):
        return orders.count()

    @ms.metric(
        decomposition=ms.ratio(
            numerator=ms.ref("metric.revenue"),
            denominator=ms.ref("metric.orders_count"),
        ),
        name="aov",
    )
    def aov(orders):
        return revenue(orders) / orders_count(orders)

project.registry.state = "ready"

ms.list_datasources(project)
ms.list_datasets(project=project)
ms.list_metrics(project=project, dataset="sales.orders")
ms.describe("sales.revenue", project=project)
ms.help("metric")
```

Every decorator registers on the active model opened by `ms.model(name=...)` and
the active registry. Use a fresh `SemanticProject` for isolated tests/examples;
production projects usually load files from `.marivo/semantic/<model>/`.

## Standard Workflow

1. Inspect the current semantic surface before adding anything new.

   ```bash
   <active-python> -c 'import marivo.semantic_py as ms; print(ms.list_models()); print(ms.list_datasources()); print(ms.list_metrics())'
   ```

   Replace `<active-python>` with the interpreter for the environment where
   Marivo is installed, such as `.venv/bin/python`.

2. Write model declarations in Python files that your project imports. A common
   layout is one model per directory under `.marivo/semantic/<model>/`.

3. Typecheck before running. Marivo decorators preserve function signatures, so
   type errors usually point at bad datasource/dataset wiring early.

4. Reload after edits.

   ```bash
   <active-python> -c 'import marivo.semantic_py as ms; ms.reload(); print(ms.list_metrics())'
   ```

   The v1 SDK does not auto-detect stale source. After changing `.py` model
   files, call `ms.reload()`.

5. On Marivo exceptions, read the structured error text. Semantic exceptions
   include a `正确写法` section when a copyable fix is known.

## Fill-In Templates

### Register a Datasource

Runnable reference: `references/examples/01_register_datasource.py`.

```python
import ibis
import marivo.semantic_py as ms

ms.model(name="<model_name>")

@ms.datasource(name="<datasource_name>", backend_type="<duckdb|trino|mysql|...>")
def <datasource_name>():
    return ibis.<dialect>.connect(<connection_args>)
```

### Declare a Dataset

Runnable reference: `references/examples/02_declare_dataset.py`.

```python
import marivo.semantic_py as ms

@ms.dataset(name="<dataset_name>", datasource=<datasource_fn>, primary_key=["<pk_col>"])
def <dataset_name>(backend):
    return backend.table("<physical_table>")
```

### Define an Aggregate Metric

Runnable reference: `references/examples/03_define_metric_aggregate.py`.

```python
import marivo.semantic_py as ms

@ms.metric(decomposition=ms.sum(), name="<metric_name>")
def <metric_name>(<dataset_name>):
    return <dataset_name>.<column>.sum()
```

### Define a Derived Ratio Metric

Runnable reference: `references/examples/04_define_metric_derived.py`.

```python
import marivo.semantic_py as ms

@ms.metric(decomposition=ms.sum(), name="numerator_metric")
def numerator_metric(orders):
    return orders.amount.sum()

@ms.metric(decomposition=ms.sum(), name="denominator_metric")
def denominator_metric(orders):
    return orders.count()

@ms.metric(
    decomposition=ms.ratio(
        numerator=ms.ref("metric.numerator_metric"),
        denominator=ms.ref("metric.denominator_metric"),
    ),
    name="<derived_name>",
)
def <derived_name>(orders):
    return numerator_metric(orders) / denominator_metric(orders)
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
  `@ms.datasource`. `ms.reload()` raises `DatasourceNotRegisteredError`.
  Runnable reference:
  `references/examples/99_pitfall_dataset_without_datasource.py`.
- Forgetting `ms.reload()` after editing source can leave old IR in memory. The
  v1 SDK exposes `IRReloadRequiredError` for the future contract, but it does
  not auto-raise it yet.
- Decorators need an active model opened by `ms.model(name=...)`.
- `@ms.datasource` must return a live ibis backend. Returning `None` causes
  analysis runtime failures such as `NoBackendFactoryError`.

## Further Reading

- `references/cheatsheet.md` -- decorators, builders, loaders, introspection
- `references/pitfalls.md` -- expanded exception explanations
- `references/examples/` -- runnable files, one per template
