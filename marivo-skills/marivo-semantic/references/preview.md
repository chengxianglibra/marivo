# marivo-semantic preview reference

Use the standard preview APIs for raw datasource and semantic object
inspection. The preview surface returns `PreviewResult` DTOs with bounded rows,
type maps, truncation flags, and structural warnings.

## Backend factory

Preview, materialization, compile, and parity helpers need a callable that
receives a datasource name and returns a live Ibis backend:

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)
```

Do not pass a backend instance directly as `backend_factory`.

## Raw datasource preview

Before declaring a new dataset, preview the physical table with a small limit:

```python
import marivo.analysis as mv

preview = mv.datasources.preview(
    "warehouse",
    table="orders",
    columns=["order_id", "created_at", "amount", "status"],
    limit=20,
    where=[{"column": "created_at", "op": ">=", "value": "2026-01-01"}],
    order_by=[{"column": "created_at", "direction": "desc"}],
)
```

Preview rows help validate physical shape: time formats, enum values, nulls,
amount signs or units, JSON-like strings, and join-key shape. Preview rows do
not prove business meaning by themselves.

## Semantic preview

After authoring, reload and preview semantic objects:

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)

project.preview_dataset("sales.orders", backend_factory=backend_factory, limit=20)
project.preview_field("sales.order_date", backend_factory=backend_factory, limit=20)
project.preview_metric("sales.revenue", backend_factory=backend_factory, limit=20)
```

If an installed Marivo version predates preview APIs, use `project.materialize_*`
plus bounded Ibis execution and record that fallback in the readiness closeout.

## Required preview points

- new dataset candidate table
- string or integer time-like columns
- amount, status, enum, code, and join key columns
- new dataset after authoring
- new time field after authoring
- field with cast, filter, or case logic
- new metric through materialization or compile

If preview fails for a new dataset, time field, or metric, readiness is blocked.
