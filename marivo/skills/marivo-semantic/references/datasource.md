# Datasource prerequisite and evidence snapshot

Datasource declarations and physical evidence live in `marivo.datasource`.
Read `md.help("authoring")` and the selected backend/source help before calling
anything here; those help topics own exact signatures and contracts.

## Setup

Declare a backend datasource such as `md.duckdb(...)`, persist it with
`md.register(spec)` (or an
equivalent project model), and run `md.test(spec.ref)`. `md.table(...)` names an
internal table or view; it is not a datasource declaration. `md.parquet(...)`,
`md.csv(...)`, and `md.json(...)` are DuckDB file sources, not datasource
declarations. CSV and JSON require typed schemas; consult their help topics.

If setup fails before inspection, `marivo doctor` can inspect the interpreter,
project, extras, declarations, secret references, and local state without a
database connection. Use its explicit connect option only for an intended live check.

## One acquisition, local projections

```python
import marivo.datasource as md

warehouse = md.ref("datasource.warehouse")
source = md.table("orders")
inspection = md.inspect(warehouse, source)
inspection.show()
inspection.partitions().show()

scope = md.partition(
    {"dt": "20260710"},
    max_rows=1000,
    timeout_seconds=30,
)
snapshot = inspection.sample(
    scope=scope,
    columns=("order_id", "status", "dt", "amount"),
)

snapshot.entity(columns=("order_id",)).show()
snapshot.dimensions(columns=("status",)).show()
snapshot.values("status", limit=10).show()
snapshot.time_dimensions(columns=("dt",)).show()
snapshot.measures(columns=("amount",)).show()
```

Read physical extent, partition state, schema, and enforceable timeout capability
before sampling. Use `md.unpruned(max_rows=..., timeout_seconds=...)` only when a
broad read is intentional. `LIMIT` bounds returned rows, not bytes scanned.

Select all columns needed by the active batch before acquisition. Projections are
local views over that snapshot and issue no query. Values stay memory-only unless
`persist_values=True`; persistent values are bounded but plaintext project-local
cache data, so make that privacy decision explicitly.

Uncommon formats and all semantic judgments remain agent-owned. Use
`md.raw_sql(..., reason=...)` only as a potentially expensive diagnostic escape
hatch, never as the normal schema or authoring route.
