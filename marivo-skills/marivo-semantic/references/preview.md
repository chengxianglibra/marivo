# marivo-semantic preview reference

Phase 0 preview uses APIs that exist today. Do not call target APIs such as
`mv.datasources.preview(...)`, `project.preview_dataset(...)`,
`project.preview_field(...)`, or `project.preview_metric(...)` until they are
implemented.

## Backend factory

Materialization, compile, and parity helpers need a callable that receives a
datasource name and returns a live Ibis backend:

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)
```

Do not pass a backend instance directly as `backend_factory`.

## Raw datasource preview

Before declaring a new dataset, preview the physical table with a small limit:

```bash
.venv/bin/python - <<'PY'
import marivo.analysis as mv

backend = mv.datasources.build_backend("warehouse")
table = backend.table("orders")
print(table.limit(20).execute())
PY
```

Use `select(...)` or the backend's safe table API to keep wide tables bounded:

```bash
.venv/bin/python - <<'PY'
import marivo.analysis as mv

backend = mv.datasources.build_backend("warehouse")
table = backend.table("orders")
print(table.select("order_id", "created_at", "amount", "status").limit(20).execute())
PY
```

Preview rows help validate physical shape: time formats, enum values, nulls,
amount signs or units, JSON-like strings, and join-key shape. Preview rows do
not prove business meaning by themselves.

## Semantic preview

After authoring, reload and materialize semantic objects:

```bash
.venv/bin/python - <<'PY'
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
assert project is not None
project.load()
backend_factory = lambda name: mv.datasources.build_backend(name)

dataset = project.materialize_dataset("sales.orders", backend_factory=backend_factory)
print(dataset.limit(20).execute())

metric = project.materialize_metric("sales.revenue", backend_factory=backend_factory)
print(metric.execute())
PY
```

For compile-only checks:

```bash
.venv/bin/python - <<'PY'
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
assert project is not None
project.load()
backend_factory = lambda name: mv.datasources.build_backend(name)
print(project.compile_sql("sales.revenue", backend_factory=backend_factory))
PY
```

## Required preview points

- new dataset candidate table
- string or integer time-like columns
- amount, status, enum, code, and join key columns
- new dataset after authoring
- new time field after authoring
- field with cast, filter, or case logic
- new metric through materialization or compile

If preview fails for a new dataset, time field, or metric, readiness is blocked.
