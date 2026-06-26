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

## Datasource Discovery APIs

All physical discovery lives in `marivo.datasource`. The semantic layer
composes these primitives and never re-implements them. Use `md.ref(...)` to
bind a datasource, `md.table()`/`md.parquet()`/`md.csv()` to bind a source,
and the `md.discover_*` family to collect bounded evidence before each
`ms.prepare_*` call.

### DatasourceRef and TableSource

Bind a datasource ref with `md.ref("name")`; bind a physical source with
`md.table("orders", database="sales_mart")`, `md.parquet("/data/orders/*.parquet")`,
or `md.csv("/data/orders.csv")`. `DatasourceRef` and `TableSource` are the
typed values that every discovery function accepts.

```python
import marivo.datasource as md

warehouse = md.ref("warehouse")
orders_source = md.table("orders", database="sales_mart")
```

### Scope helpers

Every data-touching discovery call accepts a `scope: ScanScope`. Use the scope
helpers instead of constructing `md.ScanScope(...)` directly in recipes:

```python
scope = md.latest_partition()                       # default: latest partition, 1000 rows
scope = md.partition({"dt": "20260625"}, max_rows=1000)  # explicit partition
scope = md.unpruned(max_rows=1000)                  # unpruned — only with justification
```

`md.ScanScope` remains a value type for advanced debugging (for example, a
non-default `timeout_seconds` combined with an explicit partition). When a
helper matches your intent, prefer it over direct construction.

### md.discover_entity

Entity-level discovery: table metadata, bounded column profiles, primary-key
candidates, and deterministic signals/issues. Returns `EntityDiscoveryResult`.
Run this before `ms.prepare_entity(...)`.

```python
discovery = md.discover_entity(
    warehouse,
    md.table("orders", database="sales_mart"),
    scope=md.latest_partition(),
)
discovery.show()
```

### md.discover_dimensions

Dimension-shaped column evidence for one source. Returns one candidate per
profiled column in `DimensionDiscoveryResult`. Pass `columns=` to profile a
subset; `None` profiles all columns within `scope.max_columns`.

```python
dimensions = md.discover_dimensions(
    warehouse,
    md.table("orders"),
    columns=("status", "region"),
)
```

### md.discover_time_dimensions

Time-dimension column evidence: parse candidates, value ranges, partition
alignment, signals, issues, and judgment targets. Returns
`TimeDimensionDiscoveryResult`.

```python
time_dims = md.discover_time_dimensions(
    warehouse,
    md.table("orders"),
    columns=("created_at",),
)
```

### md.discover_measures

Measure-shaped column evidence with deterministic measure signals. Returns
`MeasureDiscoveryResult`. Discovery does not choose units or additivity.

```python
measures = md.discover_measures(
    warehouse,
    md.table("orders"),
    columns=("amount",),
)
```

### md.discover_relationship

Relationship discovery between two sides. Each side is a
`md.JoinSide(datasource, source, columns=...)` with a `DatasourceRef`. Returns
`RelationshipDiscoveryResult` with sampled key evidence, match rate, fanout,
key type evidence, signals, and issues. Run this before
`ms.prepare_relationship(...)`.

```python
relationship = md.discover_relationship(
    from_side=md.JoinSide(warehouse, md.table("orders"), columns=("customer_id",)),
    to_side=md.JoinSide(warehouse, md.table("customers"), columns=("customer_id",)),
    scope=md.latest_partition(),
)
```

### md.discover_dimension_values

Bounded current value counts for one dimension column. Returns
`DimensionValueDiscoveryResult`. Use this only for current filter/value
evidence; Marivo does not persist these values into semantic `ai_context`,
enum metadata, or authored objects.

```python
values = md.discover_dimension_values(
    warehouse,
    md.table("orders"),
    column="status",
    limit=10,
)
```

### md.raw_sql

Diagnostic escape hatch: one bounded read-only SQL statement against a
datasource, with a required `reason`. Returns `RawSqlResult` labeled as
`escape_hatch` evidence. Use only when the structured discovery APIs cannot
answer the question.

```python
result = md.raw_sql(
    warehouse,
    "SELECT status, COUNT(*) AS n FROM orders GROUP BY status",
    reason="confirm status enum before authoring dimension",
)
```

## Discover configured datasources

Use `md` from the installed project environment:

```python
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms

md.list().show()
print(md.describe("warehouse"))
print(md.test("warehouse"))

discovery = md.discover_entity(md.ref("warehouse"), source=ms.table("orders"))
discovery.show()
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

Discover and author tables with `database="sales_mart"`:

```python
import marivo.analysis as mv
import marivo.semantic as ms

discovery = md.discover_entity(
    md.ref("warehouse"),
    md.table("orders", database="sales_mart"),
    scope=md.latest_partition(),
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
