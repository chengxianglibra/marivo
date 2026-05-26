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
session = mv.session.create(name="analysis")
```

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
