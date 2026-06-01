# marivo-semantic datasource reference

Use this reference when creating, checking, or repairing project datasources used
by semantic models from a pip-installed Marivo project.

## Project datasource files

Semantic files reference datasources declared under `.marivo/datasource/*.py`.
Use `md.ref("<name>")` in semantic files:

```python
import marivo.datasource as md

warehouse = md.ref("warehouse")
```

## Inspect configured datasources

Use `mv.datasources` from the installed project environment:

```python
import marivo.analysis as mv

print(mv.datasources.all())
print(mv.datasources.describe("warehouse"))
print(mv.datasources.test("warehouse"))

metadata = mv.datasources.inspect_table("warehouse", table="orders")
print(metadata.to_dict())
```

`table.schema()` returns types but not comments. Prefer
`mv.datasources.inspect_table(...)` for table comments, column comments,
nullable flags, partition hints, and warnings.

## DuckDB datasource

```python
import marivo.datasource as md

warehouse = md.DatasourceSpec(
    name="warehouse",
    backend_type="duckdb",
    path="warehouse.duckdb",
)
md.datasource(warehouse)
```

## Trino datasource

For Trino, `catalog` is required and `schema` is optional. Keep schema selection
at table access time when the datasource has no default schema.

```python
import marivo.datasource as md

warehouse = md.DatasourceSpec(
    name="warehouse",
    backend_type="trino",
    host="trino.example.internal",
    port=8080,
    catalog="hive",
    source="marivo",
    client_tags=["agent", "semantic-authoring"],
    user_env="TRINO_USER",
    password_env="TRINO_PASSWORD",
)
md.datasource(warehouse)
```

Inspect and read tables with `database="sales_mart"`:

```python
import marivo.analysis as mv

metadata = mv.datasources.inspect_table(
    "warehouse",
    table="orders",
    database="sales_mart",
)
backend = mv.datasources.build_backend("warehouse")
orders = backend.table("orders", database="sales_mart")
tables = backend.list_tables(database="sales_mart")
schemas = backend.list_schemas()
```

Use `backend.list_schemas()` to discover available schemas, then
`backend.list_tables(database="sales_mart")` to verify table reachability inside
the selected schema. Write table access as `table="orders",
database="sales_mart"` or `backend.table("orders", database="sales_mart")`.

## Trino VARCHAR datetime cast

Do not cast a Trino VARCHAR datetime directly to date. Parse through timestamp
first:

```python
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```
