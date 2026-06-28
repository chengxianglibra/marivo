# marivo-semantic datasource prerequisite

Datasource authoring and discovery live in `marivo.datasource`. This reference
only tells agents how to prepare a datasource for semantic authoring.

## Flow

1. Read `md.help()` or `md.help("<backend>")` before writing datasource files.
2. Declare project datasources under `models/datasources/*.py`.
3. Bind datasource refs with `md.ref("<name>")`.
4. Run `md.test("<name>")` before semantic authoring.
5. Use bounded `md.discover_*` calls for source evidence before each semantic
   object.

## Project Files

Semantic files reference datasources declared under `models/datasources/*.py`:

```python
import marivo.datasource as md

warehouse = md.ref("warehouse")
```

Do not copy backend parameter tables into this skill. Use `md.help("duckdb")`,
`md.help("trino")`, `md.help("clickhouse")`, `md.help("mysql")`, or the
matching runtime error when the field shape is unclear.

## Discovery

Use the public discovery family for datasource evidence:

```python
warehouse = md.ref("warehouse")
orders = md.table("orders")
scope = md.latest_partition()

md.discover_entity(warehouse, orders, scope=scope).show()
md.discover_dimensions(warehouse, orders, columns=("status",), scope=scope).show()
md.discover_time_dimensions(warehouse, orders, columns=("dt",), scope=scope).show()
md.discover_measures(warehouse, orders, columns=("amount",), scope=scope).show()
```

`md.discover_entity(...)` is the first-class schema path for entity authoring:
read the rendered schema columns and partition columns there before asking the
user or using a SQL diagnostic.

Discovery evidence informs semantic decisions, but it does not author semantic
objects and it does not replace `ms.help(...)` for constructor contracts. Treat
the discovery result as opaque evidence text, not a field-access object.

Use `md.discover_dimension_values(...)` only for current filter/value evidence.
Do not persist bounded sampled values into semantic metadata.

Use `md.raw_sql(...)` only as a diagnostic escape hatch with a required
`reason`. It supports bounded `SELECT`/`WITH` diagnostics and read-only metadata
diagnostics such as `SHOW`, `DESCRIBE`, `DESC`, and `EXPLAIN`. SQL text is
provenance or diagnostics; it is not an executable semantic expression body.
