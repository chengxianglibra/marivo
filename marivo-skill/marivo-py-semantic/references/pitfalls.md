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

  @ms.datasource(name="tiny_orders", backend_type="duckdb")
  def tiny_orders():
      import ibis
      return ibis.duckdb.connect(":memory:")

  @ms.dataset(name="orders", datasource=tiny_orders)
  def orders(backend): ...
```

**Why it happens:** the `datasource=` argument points at a datasource name that
is not registered in the same model. The validator runs at `ms.reload(project)`
time and raises this error.

**Fix:** declare the datasource with `@ms.datasource(name=..., backend_type=...)`
and pass the decorated function into `@ms.dataset(datasource=...)`, or make the
`ms.ref("datasource.name")` match an existing datasource.

**See:** `examples/99_pitfall_dataset_without_datasource.py`.

## Forgot to call `ms.reload()` after editing source

**Symptom:** the IR keeps reporting the previous datasource/dataset/metric list
after a `.py` file changed.

The v1 SDK does not raise `IRReloadRequiredError` automatically, but the class
exists for the future contract. Run:

```bash
<active-python> -c 'import marivo.semantic_py as ms; ms.reload(); print(ms.list_metrics())'
```

Replace `<active-python>` with the interpreter for the environment where Marivo
is installed, such as `.venv/bin/python`.

## `@ms.datasource` returned `None`

**Symptom:**

```text
NoBackendFactoryError: @ms.datasource 'tiny_orders' did not return an ibis backend.

正确写法:
  import ibis
  import marivo.semantic_py as ms

  @ms.datasource(name="tiny_orders", backend_type="duckdb")
  def tiny_orders():
      return ibis.duckdb.connect(":memory:")
```

**Fix:** the datasource function body must return the backend. A bare `pass` or
implicit `None` return is the common offender.

## Decorator outside `ms.model(...)` context

**Symptom:** a decorator raises a model registration error. Decorators register
on the current model in the current registry; without `ms.model(name=...)`, there
is nowhere to attach the declaration.

**Fix:**

```python
import marivo.semantic_py as ms

ms.model(name="sales")

@ms.datasource(name="tiny_orders", backend_type="duckdb")
def tiny_orders(): ...
```

In tests/examples, use a fresh `SemanticProject` plus `use_registry(...)`, or the
helper context in `references/examples/_fixtures/tiny_db.py`.
