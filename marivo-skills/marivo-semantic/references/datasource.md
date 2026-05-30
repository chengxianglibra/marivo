# marivo-semantic datasource reference

Datasource is a project asset. Declare it in `.marivo/datasource/*.py`, then
reference its global name from semantic datasets. Do not declare datasources in
semantic model files.

## Core Rule

- Use `marivo.datasource as md` in `.marivo/datasource/<name>.py`.
- Use `@ms.dataset(datasource="<name>")` in `.marivo/semantic/...`.
- The datasource name is global and must not contain `.`.
- Non-secret connection fields live in the datasource file.
- Secret fields use environment references only: `user_env`, `password_env`,
  `auth_env`, `token_env`, `api_key_env`, `secret_env`, `private_key_env`, etc.
- Sensitive literal fields are rejected: `user`, `password`, `auth`, `token`,
  `secret`, `secret_key`, `access_key`, `private_key`, `passphrase`, and
  `api_key`.

## Datasource Definition

```python
import marivo.datasource as md

md.datasource(
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
```

For Trino, `host` and `catalog` are required. `catalog` maps to the Ibis
connection `database`. `schema` is optional and only sets the connection's
default schema; it is not required when tables pass their schema explicitly.
Use `schema="sales_mart"` only when you want a default schema for ad hoc Ibis
calls such as `backend.list_tables()` without a `database=` argument.

The same datasource can be shared by multiple semantic models.

## Semantic Reference

```python
import marivo.semantic as ms

ms.model(name="sales")

@ms.dataset(name="orders", datasource="warehouse", primary_key=["order_id"])
def orders(backend):
    return backend.table("orders", database="sales_mart")
```

When calling Ibis directly against Trino, use `backend.table("orders",
database="sales_mart")` for schema checks and
`backend.list_tables(database="sales_mart")` for table enumeration. Do not use
`backend.list_schemas()`; it is not the Ibis table-discovery API used here.

`ms.datasource(...)` has been removed. If you see it, move the datasource
configuration to `.marivo/datasource/<name>.py` and keep only the string
reference on `@ms.dataset`.

## Analysis API

Use `mv.datasources` for project datasource CRUD and backend checks:

```python
import marivo.analysis as mv

mv.datasources.register(
    "warehouse",
    backend_type="duckdb",
    path="/data/warehouse.duckdb",
)
mv.datasources.test("warehouse")
backend = mv.datasources.build_backend("warehouse")
```

## Table Metadata

Use `mv.datasources.inspect_table(...)` before declaring a dataset:

```python
metadata = mv.datasources.inspect_table("warehouse", table="orders")
metadata = mv.datasources.inspect_table("warehouse", table="orders", database="sales_mart")
```

For Trino, pass `database="sales_mart"` when the datasource has no default
`schema`. When `schema="sales_mart"` is configured on the datasource,
`inspect_table("warehouse", table="orders")` can use that default for metadata
enrichment.

The result includes table comment, column names, Ibis types, nullable flags,
column comments, partition hints, and warnings for backend catalog gaps.
DuckDB, MySQL, Trino, and ClickHouse have backend-specific metadata adapters. Other
backends return schema-only metadata with warnings when comments or partitions
are unavailable.

## Metadata Workflow

After the datasource is configured and reachable, use ibis metadata before
modeling:

```python
backend = mv.datasources.build_backend("warehouse")
table = backend.table("orders", database="sales_mart")
schema = table.schema()
tables = backend.list_tables(database="sales_mart")
```

`table.schema()` returns names and types, not comments. Fetch comments from the
datasource metadata catalog before assigning semantic meaning. For time-like
VARCHAR/string columns, preview sample values before choosing casts and
granularity.

## Preview Requirement

Before declaring a semantic dataset, preview the physical table with a bounded
limit. Use `preview.md` for the Phase 0 fallback commands. Raw preview validates
physical shape such as time formats, enum values, nulls, amount signs, and join
keys; it does not replace comments or knowledge-base definitions.
