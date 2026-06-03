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

## Choose the native backend first

Default to the Marivo datasource backend that matches the physical source. Do
not choose Trino just because it can federate to another engine:

| Physical source | Default datasource backend |
| --- | --- |
| Hive or Iceberg lakehouse table | `trino` |
| ClickHouse table or host | `clickhouse` |
| MySQL table or host | `mysql` |
| DuckDB database file | `duckdb` |
| Local Parquet or CSV files | `duckdb` |

Use Trino for another engine only when the user explicitly provides a Trino
endpoint/catalog or requires federation through Trino. For local JSON files,
still choose DuckDB as the execution backend, but inspect runtime help before
declaring `ms.file(...)`; do not invent unsupported file formats.

## Inspect configured datasources

Use `mv.datasources` from the installed project environment:

```python
import marivo.analysis as mv
import marivo.semantic as ms

print(mv.datasources.all())
print(mv.datasources.describe("warehouse"))
print(mv.datasources.test("warehouse"))

metadata = mv.datasources.inspect_source("warehouse", source=ms.table("orders"))
print(metadata.to_dict())
```

`table.schema()` returns types but not comments. Prefer
`mv.datasources.inspect_source(...)` for table comments, column comments,
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

DuckDB database-file tables use table sources:

```python
orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
)
```

DuckDB external files use file sources:

```python
orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.file("/data/orders/*.parquet", format="parquet"),
)
```

## Hive/Iceberg via Trino datasource

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

Inspect and author tables with `database="sales_mart"`:

```python
import marivo.analysis as mv
import marivo.semantic as ms

metadata = mv.datasources.inspect_source(
    "warehouse",
    source=ms.table("orders", database="sales_mart"),
)
orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders", database="sales_mart"),
)
backend = mv.datasources.build_backend("warehouse")
schemas = backend.list_databases(catalog="hive")
tables = backend.list_tables(database="sales_mart")
```

Use `backend.list_databases(catalog="hive")` to discover available schemas, then
`backend.list_tables(database="sales_mart")` to verify table reachability inside
the selected schema. Write semantic table access as
`source=ms.table("orders", database="sales_mart")`.

`backend.get_schema("orders", database="sales_mart")` can inspect types after
the table name is known; it cannot discover available schemas or tables.

## Trino VARCHAR datetime cast

Do not cast a Trino VARCHAR datetime directly to date. Parse through timestamp
first:

```python
@ms.time_field(dataset=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

When the body produces a DateColumn (via `.cast("date")`), declare
`data_type="date"`, not `data_type="datetime"`. A mismatch between declared
data_type and the body's ibis dtype causes TypeError at execution.
