# marivo-py-semantic datasource reference

Use this reference when declaring or repairing `ms.datasource(...)` in a
Python semantic model.

## Core Rule

A semantic model may reference only datasources that already have a matching
profile. Check `mv.profiles.list()` before editing the model:

- if the profile exists, reuse its name in `ms.datasource(name=...)`;
- if the profile is missing, create it with `mv.profiles.set(...)` before
  declaring datasets, fields, or metrics;
- never ask for column structure when the backend is reachable; connect with
  ibis, fetch schema, and query metadata comments first.

The semantic file stores datasource identity only. Connection metadata,
credentials, and profile fields never go into `.marivo/semantic/*.py`.

## Semantic Datasource Definition

`ms.datasource(...)` is a top-level metadata declaration. It records the stable
semantic name and backend type that datasets bind to.

```python
import marivo.semantic_py as ms

ms.model(name="sales")

warehouse = ms.datasource(
    name="warehouse",
    backend_type="trino",
    description="Production analytics warehouse.",
)

@ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
def orders(backend):
    return backend.table("orders", database=("hive", "sales_mart"))
```

Rules:

- `name` must match an existing profile name.
- `backend_type` must match the profile backend type.
- Dataset functions receive the live ibis backend from the runtime/session.
- Table identity belongs in dataset materialization (`backend.table(...)`), not
  in `ms.datasource(...)`.

## Profile Definition

Profiles are user-scope runtime connection records managed by
`marivo.analysis_py`:

```python
import marivo.analysis_py as mv

mv.profiles.set(
    "warehouse",
    backend_type="trino",
    host="trino.example.internal",
    port=8080,
    user="analytics",
    catalog="hive",
    schema="sales_mart",
    password_env="WAREHOUSE_PWD",
)
```

Use `mv.profiles.set(...)` when:

- `mv.profiles.list()` has no matching profile for the semantic datasource;
- the profile exists but points at the wrong backend type or physical source;
- `mv.profiles.test(name)` cannot open the backend.

Sensitive values must use `*_env` fields such as `password_env`; never store a
literal password, token, key, or secret. After setting a profile, test it and
use ibis metadata before modeling:

```python
mv.profiles.test("warehouse")
backend = mv.profiles.build_backend("warehouse")
table = backend.table("orders", database=("hive", "sales_mart"))
schema = table.schema()
```

`table.schema()` returns names and types, not comments. Fetch comments from the
datasource metadata catalog before assigning semantic meaning. For time-like
VARCHAR/string columns, preview sample values before choosing casts and
granularity.

## Required Profile Fields

### DuckDB

```python
mv.profiles.set("local_warehouse", backend_type="duckdb", path="/data/warehouse.duckdb")
```

Required:

- `path`: DuckDB file path, or `":memory:"` for a temporary test database.

Metadata:

- use `backend.table(...).schema()` for names/types;
- use DuckDB metadata such as `PRAGMA table_info(...)` when comments or table
  metadata are needed.

### Trino

```python
mv.profiles.set(
    "warehouse",
    backend_type="trino",
    host="trino.example.internal",
    port=8080,
    user="analytics",
    catalog="hive",
    schema="sales_mart",
    http_scheme="https",
    source="marivo-analysis",
    client_tags=["standby", "routing_group=wide"],
    password_env="WAREHOUSE_PWD",
)
```

Required:

- `host`
- `port`
- `user`
- `catalog`
- `schema` when the table reference does not include a schema
- auth method fields required by the environment, using `*_env` for secrets

Optional but common:

- `http_scheme`
- `source`
- `client_tags`
- `session_properties`

Table naming:

- `schema.table` is a two-part table reference; catalog is a separate profile
  field, not part of that table name.
- For ibis, pass catalog/schema according to the active Trino backend
  convention used by the project, for example `database=("hive", "sales_mart")`.

Metadata:

- use `information_schema.columns` to fetch column comments and types;
- preview partition/time columns such as `log_date`, `dt`, `create_time`, or
  `hr` before deciding cast expressions.

### MySQL

```python
mv.profiles.set(
    "ops",
    backend_type="mysql",
    host="mysql.internal",
    port=3306,
    user="reporter",
    database="analytics",
    password_env="OPS_DB_PWD",
)
```

Required:

- `host`
- `port`
- `user`
- `database`
- auth method fields required by the environment, using `*_env` for secrets

Metadata:

- use `SHOW FULL COLUMNS FROM <table>` to fetch column types and comments.

### Postgres

```python
mv.profiles.set(
    "ops",
    backend_type="postgres",
    host="pg.internal",
    port=5432,
    user="reporter",
    database="analytics",
    schema="public",
    password_env="OPS_DB_PWD",
)
```

Required:

- `host`
- `port`
- `user`
- `database`
- `schema` when not using the default schema
- auth method fields required by the environment, using `*_env` for secrets

Metadata:

- use `information_schema.columns` or `pg_catalog` to fetch column types and
  comments.

## Ask Only When Automatic Checks Fail

Ask the user only for:

- missing connection parameters after checking existing profiles;
- the Trino catalog when the user supplied only `schema.table`;
- business intent that cannot be determined from knowledge context, column
  comments, and sample values.

Do not ask for profile existence, column names, column types, comments, or
time/partition sample formats when the backend is reachable.
