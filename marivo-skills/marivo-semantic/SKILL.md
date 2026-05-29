---
name: marivo-semantic
description: Use when the task involves declaring a Marivo semantic model -- datasource, dataset, field, metric, or relationship.
---

# marivo-semantic

Use this skill when writing or running Python code that declares a Marivo
semantic model: datasources, datasets, fields, time fields, metrics, and
relationships via `marivo.semantic`.

For running analyses on top of an already-declared model, switch to
`marivo-analysis`. Modeling is owned here; analysis is owned there.

## Python Environment

Do not use bare `python`, `python3`, `pip`, or `pip3` commands.
In this Marivo source checkout, use these exact entrypoints:

```bash
.venv/bin/python
.venv/bin/pip
```

If this skill is copied into another project, first identify that project's
virtualenv path, then use `<venv>/bin/python` and `<venv>/bin/pip`
consistently for every install, check, and script run.

## Prerequisite

You need `marivo` installed in the active Python environment, plus `ibis` and
one or more backend drivers for the datasources you intend to declare, such as
DuckDB for this skill's examples:

```bash
.venv/bin/pip install marivo ibis-framework duckdb
```

If the surrounding project pins a different virtualenv, install into that venv
with its explicit pip path. This skill does not assume you have the Marivo
source checkout.

## 30-Second Overview

```python
import ibis
import marivo.semantic as ms

# In .marivo/datasource/tiny_orders.py:
# import marivo.datasource as md
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

Before writing any semantic-layer Python file, complete the Phase 0 authoring
loop in `references/authoring-workflow.md`. Do not declare datasets, fields,
time fields, metrics, or relationships until this checklist is done.

1. Load the project and inspect existing semantic objects.

   Reuse existing models, datasets, fields, metrics, and relationships when
   their descriptions, `ai_context`, dependencies, and provenance match the
   request. Do not create duplicate semantic refs by default.

2. Check datasource configuration and reachability.

   Run `mv.datasources.all()`, `mv.datasources.describe(...)`, and
   `mv.datasources.test(...)` where live access is required. Use
   `references/datasource.md` for datasource authoring rules.

3. Fetch schema, comments, and metadata.

   Use `mv.datasources.inspect_table(...)` before declaring a new dataset.
   `table.schema()` returns types but not comments. Table comments, column
   comments, nullable flags, and supplied knowledge are primary sources for
   business meaning.

4. Preview raw table data with bounded standard APIs.

   Preview every new dataset candidate table, string/integer time-like columns,
   amount/status/enum/code columns, and join keys. Use `mv.datasources.preview(...)`
   and `references/preview.md`; do not persist preview rows into semantic
   definitions.

5. Ingest knowledge and propose a semantic plan.

   Extract business definitions, guardrails, synonyms, example questions,
   source SQL, and decomposition hints. Ask only for business decisions that
   cannot be fetched or resolved from evidence. See `references/evidence.md`.

6. Author, reload, semantic-preview, parity-check, and close with readiness.

   After edits, reload the project, materialize or compile new semantic
   objects, run parity for metrics with source SQL, and report blockers or
   warnings using `references/readiness.md`.

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

1. Follow the evidence-driven authoring loop before adding anything new.

   Start with `references/authoring-workflow.md`, then use
   `references/evidence.md`, `references/preview.md`, and
   `references/readiness.md` for the evidence, preview, and closeout details.

2. Write model declarations in Python files under
   `.marivo/semantic/<model>/`. Each model directory needs a `_model.py`
   that calls `ms.model(name=...)`.

3. Typecheck before running. Marivo decorators preserve function signatures,
   so type errors usually point at bad datasource/dataset wiring early.

4. Inspect the unified help surface before fixing structural errors.

   ```bash
   .venv/bin/python -c 'import marivo.semantic as ms; print(ms.help("constraints", format="json"))'
   ```

   Use `ms.help("<symbol>", format="json")` for decorator-specific
   constraints. This is the source of truth for AST whitelist rules, hints,
   and runnable example references.

5. Re-check after edits.

   ```bash
   .venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; result = project.reload(); print(result)'
   ```

   For materialization, compile, or parity calls, pass a backend factory:

   ```python
   import marivo.analysis as mv

   backend_factory = lambda name: mv.datasources.build_backend(name)
   ```

6. On Marivo exceptions, read the structured error text. Semantic exceptions
   include `constraint_id` and a `hint` when a copyable fix is known.

## Fill-In Templates

### Register a Datasource

Runnable reference: `references/examples/01_register_datasource.py`.
Before declaring a dataset, read `references/datasource.md`: semantic models
can only reference project datasources declared in `.marivo/datasource/*.py`.

Credentials never enter the semantic file. Persist non-secret connection
metadata with `mv.datasources.register(...)` or by writing
`.marivo/datasource/<name>.py`; sensitive fields use
`<field>_env="VAR_NAME"`. See `references/datasource.md`.

```python
import marivo.datasource as md

md.datasource(name="<datasource_name>", backend_type="duckdb", path=":memory:")
```

### Declare a Dataset

Runnable reference: `references/examples/02_declare_dataset.py`.

```python
import marivo.semantic as ms

@ms.dataset(name="<dataset_name>", datasource="<datasource_name>", primary_key=["<pk_col>"])
def <dataset_name>(backend):
    return backend.table("<physical_table>")
```

### Define an Aggregate Metric

Runnable reference: `references/examples/03_define_metric_aggregate.py`.

```python
import marivo.semantic as ms

@ms.metric(datasets=[orders], decomposition=ms.sum(), name="<metric_name>")
def <metric_name>(orders):
    return orders.<column>.sum()
```

### Define a Derived Ratio Metric

Runnable reference: `references/examples/04_define_metric_derived.py`.

```python
import marivo.semantic as ms

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

- Run `ms.help("constraints", format="json")` or
  `ms.help("<decorator>", format="json")` before guessing allowed authoring
  shapes. The JSON catalog is the canonical error-to-hint/example map.
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

- `references/authoring-workflow.md` -- Phase 0 end-to-end semantic authoring loop
- `references/evidence.md` -- required evidence and when to ask the user
- `references/preview.md` -- raw and semantic preview using APIs available today
- `references/readiness.md` -- blocker/warning closeout before analysis handoff
- `references/datasource.md` -- project datasource rules and required fields
- `references/cheatsheet.md` -- decorators, builders, project loading, introspection
- `references/pitfalls.md` -- expanded exception explanations
- `references/examples/` -- runnable files, one per template

## High-Frequency Error References

For the full structured catalog, use
`ms.help("constraints", format="json")`. The table below keeps only the most
common first-stop references.

| Constraint | What it means | See |
|---|---|---|
| `dataset_ref_exists` | Dataset references a datasource with no `.marivo/datasource/*.py` declaration | `references/examples/99_pitfall_dataset_without_datasource.py` |
| `active_model_required` | Decorator has no active `ms.model(...)` namespace | `references/examples/02_declare_dataset.py` |
| `metric_derived_shape` | `datasets=[]` needs a component decomposition | `references/examples/04_define_metric_derived.py` |
| `ast_component_arithmetic` | Derived metric body must use `ms.component(...)` and arithmetic only | `references/examples/04_define_metric_derived.py` |
| `ast_single_return` | Decorator body must be one `return <expression>` | `references/examples/03_define_metric_aggregate.py` |
