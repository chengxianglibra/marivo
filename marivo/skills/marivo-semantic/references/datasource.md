# marivo-semantic datasource reference

Use this reference when creating, checking, or repairing project datasources used
by semantic domains from a pip-installed Marivo project.

## Project datasource files

Semantic files reference datasources declared under `models/datasources/*.py`.
Use `md.ref("<name>")` in semantic files:

```python
import marivo.datasource as md

warehouse = md.ref("warehouse")
```

## Secret cache

Marivo resolves `*_env` credentials through a provider chain: **os.environ >
~/.marivo/secrets.toml > error**. After a successful
`md.test(...)` round-trip, resolved env-sourced secrets are
auto-persisted to the cache.

Before authoring `*_env` fields on a new datasource, read the cache to
discover which env var names already hold values:

```bash
cat ~/.marivo/secrets.toml
```

The file is a flat TOML table, one env var name per line:

```toml
"MYSQL_PASSWORD" = "cached-value"
"TRINO_PASSWORD" = "cached-value"
"TRINO_USER" = "cached-user"
```

Reuse an existing env var name when the same credential type is already cached
(e.g., use `TRINO_USER` and `TRINO_PASSWORD` for any additional Trino
datasource). Do not ask the user for a secret the cache already holds; the
resolution chain will supply it at runtime.

If secrets.toml does not exist or has no relevant entries, ask the user for the
env var name and value. After `md.test(...)` succeeds, the new
secret is persisted automatically.

## Runtime Help

Datasource authoring has the same agent-facing help contract as semantic and
analysis:

```python
import marivo.datasource as md

md.help()                                       # top-level datasource entries
md.help('trino')                               # Trino connection fields
md.help('duckdb')                              # DuckDB connection fields
md.help('datasource_secret_env_ref')            # full secret-handling rule
```

Use `md.help('trino')` or `md.help('duckdb')` before writing a new datasource
declaration when credential or field-shape details matter.

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
endpoint/catalog or requires federation through Trino. For local Parquet or
CSV files, still choose DuckDB as the execution backend, but use the typed
constructors `ms.parquet(...)` and `ms.csv(...)`; do not invent unsupported
file formats.

## Datasource Inspection APIs

All physical inspection lives in `marivo.datasource`. The semantic layer
composes these primitives and never re-implements them.

### ScanScope

`ScanScope` controls partition pruning, row caps, and column caps for every
data-touching operation. Use `md.ScanScope()` by default (latest partition,
1000 rows, 50 columns). Pass an explicit `partition` mapping for targeted
inspection. Pass `partition=None` only when the answer explicitly accepts an
unpruned scan.

```python
scope = md.ScanScope()                                    # default: latest partition
scope = md.ScanScope(partition={"dt": "20260611"})        # explicit partition
scope = md.ScanScope(partition=None)                      # unpruned — only with justification
```

### md.inspect_table

Pure metadata, zero row reads. Returns `TableMetadata` with columns, types,
nullable flags, comments, partition info, and warnings.

```python
metadata = md.inspect_table("warehouse", md.table("orders", database="sales_mart"))
```

### md.inspect_columns

One bounded scan, multi-column profiling. Returns `ColumnInspection` with
`profiles`, `scan` report, and scope details. `columns=None` means all columns,
capped by `scope.max_columns`.

```python
evidence = md.inspect_columns(
    "warehouse",
    md.table("orders"),
    columns=("status", "amount"),
    scope=md.ScanScope(partition={"dt": "20260611"}),
)
for col in evidence.profiles:
    print(col.column, col.distinct_count, col.top_values)
```

### md.probe_join_keys

Sample join-key overlap between two sides. Used internally by
`prepare_relationship`, also available directly for diagnostics.

```python
probe = md.probe_join_keys(
    from_side=md.JoinSide("warehouse", md.table("orders"), columns=("customer_id",)),
    to_side=md.JoinSide("warehouse", md.table("customers"), columns=("customer_id",)),
)
print(probe.match_rate, probe.cardinality_estimate)
```

### md.inspect_source

`md.inspect_source` is a convenience alias that wraps `md.inspect_table` and
`md.inspect_columns`. For structured metadata-only or profiled inspection,
prefer `md.inspect_table` and `md.inspect_columns` directly.

## Inspect configured datasources

Use `md` from the installed project environment:

```python
import marivo.analysis as mv
import marivo.semantic as ms

print(md.list())
print(md.describe("warehouse"))
print(md.test("warehouse"))

metadata = md.inspect_table("warehouse", source=ms.table("orders"))
print(metadata.columns)
```

## DuckDB datasource

```python
import marivo.datasource as md

warehouse = md.duckdb(
    name="warehouse",
    path="warehouse.duckdb",
)
```

DuckDB database-file tables use table sources:

```python
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
)
```

DuckDB external files use typed file source constructors:

```python
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.parquet("/data/orders/*.parquet"),
)
```

## Hive/Iceberg via Trino datasource

For Trino, `catalog` is required and `schema` is optional. Keep schema selection
at table access time when the datasource has no default schema.

```python
import marivo.datasource as md

warehouse = md.trino(
    name="warehouse",
    host="trino.example.internal",
    port=8080,
    catalog="hive",
    source="marivo",
    client_tags=("agent", "semantic-authoring"),
    user_env="TRINO_USER",
    auth_env="TRINO_AUTH",
)
```

Inspect and author tables with `database="sales_mart"`:

```python
import marivo.analysis as mv
import marivo.semantic as ms

metadata = md.inspect_table(
    "warehouse",
    source=ms.table("orders", database="sales_mart"),
)
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders", database="sales_mart"),
)
backend = md.connect("warehouse")
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
@ms.time_dimension(entity=orders, granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

When the body produces a DateColumn (via `.cast("date")`), omit `parse`
— the parse variant is inferred from the column's ibis dtype at analysis
time. Using `parse=ms.datetime(...)` with a `.cast("date")` body causes
TypeError at execution.
