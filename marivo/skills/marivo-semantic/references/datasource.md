# marivo-semantic datasource prerequisite

Datasource authoring and discovery live in `marivo.datasource`. This reference
only prepares a datasource for semantic authoring.

## Flow

1. Read `md.help()` or `md.help("<backend>")` before writing datasource files.
2. Construct a typed datasource spec with the backend constructor.
3. Persist the spec with `md.register(spec)` or a file under
   `models/datasources/*.py`.
4. Run `md.test(spec.ref)` before semantic authoring.
5. Bind datasource refs with `spec.ref` or `md.ref("datasource.<name>")`.
6. Use bounded `md.discover_*` calls for source evidence. A broad domain- or table-group discovery pass is fine when it prevents repeated probes, but
   authoring still proceeds by active batch and per-object verification.

Do not copy backend parameter tables into this skill. Use backend-specific
`md.help(...)` output or the runtime error when a field shape is unclear.

## Project Files

Semantic files reference datasources declared under `models/datasources/*.py`:

```python
import marivo.datasource as md

spec = md.duckdb(name="warehouse", path="warehouse.duckdb")
md.register(spec)
md.test(spec.ref).show()

warehouse = spec.ref
```

## Discovery

Use the public discovery family for datasource evidence:

```python
warehouse = md.ref("datasource.warehouse")
orders = md.table("orders")

md.inspect_table(warehouse, orders).show()
md.inspect_partitions(warehouse, orders, limit=50).show()

scope = md.partition({"dt": "20260629"})

md.discover_entity(warehouse, orders, scope=scope).show()
md.discover_dimensions(warehouse, orders, columns=("status",), scope=scope).show()
md.discover_time_dimensions(warehouse, orders, columns=("dt",), scope=scope).show()
md.discover_measures(warehouse, orders, columns=("amount",), scope=scope).show()
```

`md.inspect_table(...)` is the first-class schema path for entity authoring.
Read rendered schema columns and partition columns before asking the user or
using SQL diagnostics.

If the table is partitioned, inspect available partition tuples with
`md.inspect_partitions(...)`, choose an explicit `md.partition({...})`, and pass
that scope into every `md.discover_*` call.

Read discovery results through `.show()` / `.render()`. For wide tables or
large profiles, use `.render(max_output_bytes=None)` and write the text to a
file for chunked inspection. If discovery reports
`discovery_column_limit_truncated`, increase the `ScanScope.max_columns`
budget; this is separate from output byte limits.

Discovery evidence informs semantic decisions, but it does not author semantic
objects and does not replace `ms.help(...)` for constructor contracts. Treat
the discovery result as opaque evidence text, not a field-access object.

Use `md.discover_dimension_values(...)` only for current filter/value evidence.
Do not persist bounded sampled values into semantic metadata.

Use `md.raw_sql(...)` only as a diagnostic escape hatch with a required
`reason`. It supports bounded `SELECT`/`WITH` diagnostics and read-only
metadata diagnostics such as `SHOW`, `DESCRIBE`, `DESC`, and `EXPLAIN`. SQL
text is provenance or diagnostics; it is not an executable semantic expression
body.
