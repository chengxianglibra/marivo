# Python Semantic Layer

`marivo.semantic_py` lets agentic workers define semantic models in Python files.
Python definitions are trusted local code loaded from a semantic project root; they are not sandboxed.

## Project Loading

A semantic project root contains immediate model directories. Each model directory must include `_model.py`, and may include sibling Python files such as `datasources.py`, `datasets.py`, `fields.py`, or `metrics.py`:

```text
semantic/
  sales/
    _model.py
    datasources.py
    datasets.py
    metrics.py
  marketing/
    _model.py
    ...
```

`load_project(project, reload=False)` executes trusted local Python from those directories. The loader installs a project-local namespace package, temporarily adds the project root to `sys.path`, executes `_model.py` first, then executes the sibling Python files in sorted order. Relative imports between sibling files are supported.

After import, the loader runs assembly validation. A successful load sets `project.registry.state` to `ready` and clears `project.registry.load_errors`. A structured load or validation failure clears partial models, sets the state to `errored`, stores the errors in `load_errors`, and raises `SemanticLoadError`. A missing root is treated as an empty ready project. A root that exists but is not a directory fails with `ProjectRootInvalid`.

`reload=True` is the caller-facing way to request a fresh load. The loader clears previously loaded project modules before executing files, so changed project files are reexecuted on load.

## Minimal Model

```python
import marivo.semantic_py as ms

ms.model(name="sales", description="Sales analytics")

@ms.datasource(name="warehouse", backend_type="duckdb")
def warehouse():
    ...

@ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
def orders(backend):
    return backend.table("orders")

@ms.time_field(dataset="orders", data_type="date", granularity="day")
def order_date(orders):
    return orders.created_at.cast("date")

@ms.metric(
    decomposition=ms.sum(),
    source_sql="select sum(amount) as value from orders",
    source_dialect="duckdb",
    source_document="kb://sales/revenue",
)
def revenue(orders):
    return orders.amount.sum()
```

## Validation

The Python semantic layer fails closed while building the registry:

- Metric decorator bodies must be an optional docstring followed by exactly one `return` expression. Assignments, imports, nested definitions, control flow, comprehensions, walrus expressions, `await`, and other non-expression shapes are rejected at decorator time.
- Metric assembly validation rejects missing dataset references and missing metric references from decomposition or explicit refs.
- Time fields with `granularity="hour"` must declare a required prefix time field, such as the owning day/date partition.
- Relationships must reference existing endpoint datasets, join columns must exist as fields on those endpoint datasets, both sides must be non-empty, and both sides must have equal arity.

Failed validation sets the project registry state to `errored` and records structured errors in `load_errors`.

## Materialization

Reader materialization APIs turn registered user functions back into Ibis objects using a caller-provided backend factory:

```python
from marivo.semantic_py import reader

expr = reader.materialize_metric(
    project=project,
    model="sales",
    metric="revenue",
    backend_factory=lambda datasource_name: con,
)
value = expr.execute()
```

`reader.materialize_dataset`, `reader.materialize_field`, and `reader.materialize_metric` all require `backend_factory(datasource_name)`. Dataset functions receive the backend for their datasource; field functions receive the materialized dataset table; metric functions receive materialized dataset tables for their referenced datasets.

The APIs return whatever Ibis table, column, scalar, or expression the user function returns. Callers are responsible for executing expressions. Runtime failures from the backend factory or user functions are wrapped in `SemanticRuntimeError`; missing models, datasets, fields, or metrics remain `PySemanticNotFound`.

## SQL Provenance

Business metric definitions often come from SQL knowledge bases. Store the source SQL on the metric using `source_sql`, `source_dialect`, and `source_document`, then compare the Python/Ibis expression against the SQL on a bounded fixture or sample window before trusting the translated metric.

Source SQL parity checks fail closed:

- `source_sql` is required and must return exactly one row and one column.
- `source_dialect` is required for parity.
- The referenced datasource `backend_type` is required for parity.
- `source_dialect` must match the referenced datasource `backend_type`.
- Multi-datasource metrics fail closed; all referenced datasets for one metric parity check must resolve to a single datasource.
- Numeric comparisons are exact by default. Pass explicit `rel_tol` or `abs_tol` to `compare_metric_to_source_sql` only when the source SQL and Ibis expression are expected to differ by bounded floating-point noise.

## Testing

Run targeted tests while developing:

```bash
make test TESTS=tests/test_semantic_py_decorators.py
make test TESTS=tests/test_semantic_py_materialization.py
make test TESTS=tests/test_semantic_py_parity.py
```
