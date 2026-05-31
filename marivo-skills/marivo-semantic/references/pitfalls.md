# marivo-semantic pitfalls

Each pitfall pairs a symptom with the structured exception you'll see and the
correct usage.

## Wrong Python Environment

**Symptom:** `ModuleNotFoundError: No module named 'marivo'` or output that
clearly comes from a system install rather than the project virtualenv.

**Fix:** do not try `python`, `python3`, `pip`, or `pip3` again. Use the
project virtualenv entrypoints directly:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; ms.help()'
.venv/bin/python -c 'import marivo.semantic as ms; print(ms.help("constraints", format="json"))'
```

If this skill is being used outside the Marivo source checkout, replace `.venv`
with that project's actual virtualenv path.

## Dataset References A Missing Datasource

**Symptom:**

```text
[missing_dataset_ref] Dataset 'sales.orders' references unknown datasource 'tiny_orders'.
```

**Why it happens:** the `datasource=` argument points at a global datasource
name that has no `.marivo/datasource/<name>.py` declaration.

**Fix:** create the project datasource, then reference it with `md.ref(...)`:

```python
# .marivo/datasource/tiny_orders.py
import marivo.datasource as md

tiny_orders = md.DatasourceSpec(name="tiny_orders", backend_type="duckdb", path=":memory:")
md.datasource(tiny_orders)
```

```python
# .marivo/semantic/sales/datasets.py
import marivo.datasource as md
import marivo.semantic as ms

tiny_orders = md.ref("tiny_orders")

@ms.dataset(name="orders", datasource=tiny_orders)
def orders(backend):
    return backend.table("orders")
```

**See:** `examples/99_pitfall_dataset_without_datasource.py`.

## Forgot To Reload The Project After Editing Source

**Symptom:** the IR keeps reporting the previous datasource/dataset/metric list
after a `.py` file changed.

Run:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; project.reload(); print(project.list_metrics())'
```

Outside the Marivo source checkout, replace `.venv/bin/python` with the
interpreter for the environment where Marivo is installed.

## Missing Backend Factory At Execution Time

**Symptom:**

```text
NoBackendFactoryError: session has no backend factory configured
```

**Why it happens:** analysis needs either `.marivo/datasource` backend config or
an explicit `backends=` / `backend_factory=` override.

**Fix:** create the project datasource or pass an explicit backend factory:

```python
import marivo.analysis as mv
import marivo.datasource as md

mv.datasources.register(md.DatasourceSpec(name="tiny_orders", backend_type="duckdb", path=":memory:"))
session = mv.session.get_or_create(name="analysis")
```

## Invalid Metric Shape

**Symptom:** `semantic check` reports an invalid component body or the
loader rejects a metric using `datasets=[]`. JSON output includes a
`constraint_id` such as `metric_derived_shape` or `ast_component_arithmetic`.

**Why it happens:** `datasets=[]` is reserved for derived metrics whose
decomposition has components, such as `ms.ratio(...)`. Dataset-backed metrics
must return ibis expressions over their dataset arguments and must not call
`ms.component(...)`.

**Fix:** use one of the two valid shapes:

```python
@ms.metric(datasets=[orders], additivity="additive", decomposition=ms.sum(), name="failed_count")
def failed_count(orders):
    return (orders.state == "FAILED").cast("int64").sum()

@ms.metric(
    datasets=[],
    decomposition=ms.ratio(
        numerator="sales.failed_count",
        denominator="sales.total_count",
    ),
    name="failure_rate",
)
def failure_rate():
    return ms.component("numerator") / ms.component("denominator")
```

For dimension drilldowns on a derived metric, make sure the component metrics'
datasets can reach the requested dimension through a unique relationship path.

## AST Whitelist Violation

**Symptom:** `semantic check --format=json` reports `ast_single_return`,
`ast_forbidden_statement`, or `ast_sql_escape_hatch`.

**Why it happens:** decorator bodies are expression declarations, not general
Python functions. They must contain exactly one `return <ibis expression>` and
cannot contain imports, local assignments, control flow, lambdas, or raw SQL
calls.

**Fix:** move setup outside the decorator body and keep only the expression:

```python
@ms.metric(datasets=[orders], additivity="additive", decomposition=ms.sum(), name="revenue")
def revenue(orders):
    return orders.amount.sum()
```

Inspect the live rules with:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; print(ms.help("metric", format="json"))'
```

## Ibis Expression Gotchas

- Build string transformations on an expression instance. Do not call methods
  like `ibis.expr.types.StringValue.re_replace(...)` as class methods.
- Metric bodies return one ibis expression. `count()` and `sum()` already
  produce aggregate expressions, so do not call another aggregate on them.

## Decorator Outside `ms.model(...)` Context

**Symptom:** a decorator raises a model registration error. Decorators register
on the current model in the current registry; without `ms.model(name=...)`, there
is nowhere to attach the declaration.

**Fix:**

```python
import marivo.semantic as ms

ms.model(name="sales")
```

In tests/examples, write files under a temporary `.marivo/semantic/<model>/`
directory and load them with `ms.SemanticProject(root=...).load()`, as shown in
the runnable files under `references/examples/`.
