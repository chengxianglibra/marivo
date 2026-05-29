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
  `token_env`, `api_key_env`, `secret_env`, `private_key_env`, etc.

## Datasource Definition

```python
import marivo.datasource as md

md.datasource(
    name="warehouse",
    backend_type="trino",
    host="trino.example.internal",
    port=8080,
    catalog="hive",
    schema="sales_mart",
    user_env="TRINO_USER",
    password_env="TRINO_PASSWORD",
)
```

The same datasource can be shared by multiple semantic models.

## Semantic Reference

```python
import marivo.semantic as ms

ms.model(name="sales")

@ms.dataset(name="orders", datasource="warehouse", primary_key=["order_id"])
def orders(backend):
    return backend.table("orders", database=("hive", "sales_mart"))
```

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
```

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
table = backend.table("orders", database=("hive", "sales_mart"))
schema = table.schema()
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
