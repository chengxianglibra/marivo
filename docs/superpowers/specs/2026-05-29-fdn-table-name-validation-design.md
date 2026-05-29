# FDN Table Name Validation for Dataset `backend.table()` Calls

## Context

Dataset bodies call `backend.table("orders")` with short table names. The ibis
backend connection is created from the datasource config, which bakes in a
default `catalog`/`database`/`schema`. This makes the table resolution depend on
the datasource's configured defaults, preventing cross-database/cross-schema
references and making datasets fragile when datasource defaults change.

Requiring Fully Distinguished Names (FDN) in `backend.table()` eliminates this
dependency, enables cross-database joins, and makes each dataset's physical
grounding explicit.

## Design

### Validating Backend Wrapper

A lightweight proxy wraps the real ibis backend at materialization time,
intercepting `backend.table(name)` to validate FDN format before delegation.

**Component: `_ValidatingBackend`** (in `marivo/analysis/datasources/backends.py`)

- Wraps a real ibis backend
- Overrides `.table(name)` to validate FDN, then delegates
- Passes through `.sql(query)` and all other attributes via `__getattr__`
- Constructor takes: `backend`, `backend_type`, `datasource_name`

**Component: `_validate_fdn()`** (in `marivo/analysis/datasources/backends.py`)

- Takes: `name` (table name string), `backend_type`, `datasource_name`
- Raises `DatasourceFieldInvalidError` with engine-specific guidance on failure
- No-op for `duckdb` (exempt)

### FDN Rules by Engine

| Engine      | Minimum FDN parts | Format                    | Example             | Min dots |
|-------------|-------------------|---------------------------|---------------------|----------|
| trino       | 3                 | catalog.schema.table      | hive.sales.orders   | ≥2       |
| mysql       | 2                 | database.table            | sales_db.orders     | ≥1       |
| postgres    | 2                 | database.table            | sales_db.orders     | ≥1       |
| clickhouse  | 2                 | database.table            | sales_db.orders     | ≥1       |
| duckdb      | 1 (exempt)        | any                       | orders              | any      |

### Integration Point: `Materializer._get_backend()`

In `marivo/semantic/materializer.py`, after creating a backend via
`self._backend_factory(datasource_semantic_id)`, wrap it with
`_ValidatingBackend` using the datasource's `backend_type` and `name` from the
registry. Cache the wrapped backend.

The materializer already has access to the registry (via
`self._get_registry_and_sidecar()`), so it can look up the `DatasourceIR` to
get `backend_type` and `name`.

### Datasource Connection Fields: Required Only for Connection

Existing datasource fields remain unchanged. `catalog`/`database`/`schema` are
connection parameters, not table-resolution defaults:

- `catalog` (Trino): **required** — ibis needs it for the connection
- `database` (MySQL/Postgres): **required** — ibis needs it for the connection
- `schema` (Trino/Postgres): **optional** — only for connection default
- `database` (ClickHouse): **optional** — defaults to `"default"` as before

No field removal. The shift happens at the table-name validation layer.

### Error Messages

Example for Trino:
```
datasource 'warehouse' table name 'orders' is not a fully-distinguished name.
For backend_type='trino', use 'catalog.schema.table' format (e.g. 'hive.sales.orders').
```

Example for MySQL:
```
datasource 'warehouse' table name 'orders' is not a fully-distinguished name.
For backend_type='mysql', use 'database.table' format (e.g. 'sales_db.orders').
```

### Files to Modify

1. **`marivo/analysis/datasources/backends.py`** — add `_validate_fdn()` and
   `_ValidatingBackend`
2. **`marivo/semantic/materializer.py`** — wrap backends with
   `_ValidatingBackend` in `_get_backend()`
3. **`marivo/analysis/datasources/metadata.py`** — wrap backend in
   `inspect_table()` for FDN validation there too
4. **Tests and fixtures** — update existing `backend.table("orders")` calls in
   test fixtures to use FDN format where appropriate; add dedicated FDN
   validation tests

### Verification

1. Add unit tests for `_validate_fdn()` covering each engine type and edge cases
   (empty string, single dot, correct FDN, extra dots)
2. Add integration test: declare a dataset with short table name against a
   Trino-type datasource, verify `DatasourceFieldInvalidError` at materialization
3. Add integration test: declare a dataset with FDN table name, verify
   successful materialization
4. Run `make test` and `make typecheck` to ensure no regressions
