# marivo-py-semantic pitfalls

Each pitfall pairs a symptom with the structured exception you'll see and the
correct usage.

## Dataset References A Missing Datasource

**Symptom:**

```text
[missing_dataset_ref] Dataset 'sales.orders' references unknown datasource 'tiny_orders'.
```

**Why it happens:** the `datasource=` argument points at a global datasource
name that has no `.marivo/datasource/<name>.py` declaration.

**Fix:** create the project datasource, then reference its name directly:

```python
# .marivo/datasource/tiny_orders.py
import marivo.datasource_py as md

md.datasource(name="tiny_orders", backend_type="duckdb", path=":memory:")
```

```python
# .marivo/semantic/sales/datasets.py
import marivo.semantic_py as ms

@ms.dataset(name="orders", datasource="tiny_orders")
def orders(backend):
    return backend.table("orders")
```

**See:** `examples/99_pitfall_dataset_without_datasource.py`.

## Forgot To Reload The Project After Editing Source

**Symptom:** the IR keeps reporting the previous datasource/dataset/metric list
after a `.py` file changed.

Run:

```bash
<active-python> -c 'import marivo.semantic_py as ms; project = ms.find_project(); assert project is not None; project.reload(); print(project.list_metrics())'
```

Replace `<active-python>` with the interpreter for the environment where Marivo
is installed, such as `.venv/bin/python`.

## Missing Backend Factory At Execution Time

**Symptom:**

```text
NoBackendFactoryError: session has no backend factory configured
```

**Why it happens:** analysis needs either `.marivo/datasource` backend config or
an explicit `backends=` / `backend_factory=` override.

**Fix:** create the project datasource or pass an explicit backend factory:

```python
import marivo.analysis_py as mv

mv.datasources.set("tiny_orders", backend_type="duckdb", path=":memory:")
session = mv.session.get_or_create(name="analysis")
```

## Invalid Metric Shape

**Symptom:** `semantic_py check` reports an invalid component body or the
loader rejects a metric using `datasets=[]`.

**Why it happens:** `datasets=[]` is reserved for derived metrics whose
decomposition has components, such as `ms.ratio(...)`. Dataset-backed metrics
must return ibis expressions over their dataset arguments and must not call
`ms.component(...)`.

**Fix:** use one of the two valid shapes:

```python
@ms.metric(datasets=[orders], decomposition=ms.sum(), name="failed_count")
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
import marivo.semantic_py as ms

ms.model(name="sales")
```

In tests/examples, write files under a temporary `.marivo/semantic/<model>/`
directory and load them with `ms.SemanticProject(root=...).load()`, as shown in
the runnable files under `references/examples/`.
