# marivo-py-semantic pitfalls

Each pitfall pairs a symptom with the structured exception you'll see and the
correct usage.

## Dataset references an undeclared datasource

**Symptom:**

```text
DatasourceNotRegisteredError: Dataset 'orders' references missing datasource 'tiny_orders'.

发生位置: my_model.py:42 (in orders)
原因: assembly:DatasetDatasourceMissing
建议: Register the referenced datasource before loading the semantic project.

正确写法:
  import marivo.semantic_py as ms

  warehouse = ms.datasource(name="tiny_orders", backend_type="duckdb")

  @ms.dataset(name="orders", datasource=warehouse)
  def orders(backend): ...
```

**Why it happens:** the `datasource=` argument points at a datasource name that
is not registered in the same model. The validator runs when the project loads
or reloads and raises this error.

**Fix:** declare the datasource with
`warehouse = ms.datasource(name=..., backend_type=...)` and pass that ref into
`@ms.dataset(datasource=...)`, or make the `ms.ref("datasource.name")` match an
existing datasource.

**See:** `examples/99_pitfall_dataset_without_datasource.py`.

## Forgot to reload the project after editing source

**Symptom:** the IR keeps reporting the previous datasource/dataset/metric list
after a `.py` file changed.

The v1 SDK does not raise `IRReloadRequiredError` automatically, but the class
exists for the future contract. Run:

```bash
<active-python> -c 'import marivo.semantic_py as ms; project = ms.find_project(); assert project is not None; project.reload(); print(project.list_metrics())'
```

Replace `<active-python>` with the interpreter for the environment where Marivo
is installed, such as `.venv/bin/python`.

## Missing backend factory at execution time

**Symptom:**

```text
NoBackendFactoryError: No backend factory provided for datasource 'tiny_orders'.

正确写法:
  import marivo.semantic_py as ms

  warehouse = ms.datasource(name="tiny_orders", backend_type="duckdb")

  expr = project.materialize_metric(
      "sales.revenue",
      backend_factory=lambda datasource_name: live_backend_for(datasource_name),
  )
```

**Why it happens:** `ms.datasource(...)` declares metadata only. It does not
open a connection or store credentials. Materialization and analysis need a
live Ibis backend from the caller.

**Fix:** keep the semantic declaration as metadata and provide the live backend
through the project's materialization or analysis setup.

## Backend type does not match the execution backend

**Symptom:** parity, compilation, or materialization reports that a metric's
`source_dialect` or live backend does not match the datasource `backend_type`.

**Why it happens:** `backend_type` is part of the semantic contract. It tells
Marivo which dialect the datasource represents and is used when checking SQL
provenance.

**Fix:** either correct `backend_type` on the datasource declaration or provide
the matching live Ibis backend. Do not hide the mismatch by changing metric SQL
or by omitting the datasource.

## Decorator outside `ms.model(...)` context

**Symptom:** a decorator raises a model registration error. Decorators register
on the current model in the current registry; without `ms.model(name=...)`, there
is nowhere to attach the declaration.

**Fix:**

```python
import marivo.semantic_py as ms

ms.model(name="sales")

warehouse = ms.datasource(name="tiny_orders", backend_type="duckdb")
```

In tests/examples, write files under a temporary `.marivo/semantic/<model>/`
directory and load them with `ms.SemanticProject(root=...).load()`, as shown in
the runnable files under `references/examples/`.
