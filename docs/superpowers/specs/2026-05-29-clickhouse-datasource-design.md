# ClickHouse Datasource Support

## Summary

Add `clickhouse` as a supported `backend_type` in Marivo's datasource backend
dispatch, following the same `_build_*` pattern used by duckdb, trino, mysql,
and postgres.

## Approach

Extend `marivo/analysis/datasources/backends.py` with a `_build_clickhouse`
function and register `"clickhouse"` in `SUPPORTED_BACKEND_TYPES`. No new
abstractions, error classes, or architectural changes.

## Changes

### `marivo/analysis/datasources/backends.py`

1. Add `"clickhouse"` to `SUPPORTED_BACKEND_TYPES`:
   ```python
   SUPPORTED_BACKEND_TYPES: Final[tuple[str, ...]] = (
       "duckdb", "trino", "mysql", "postgres", "clickhouse",
   )
   ```

2. Add dispatch branch in `build_backend()`:
   ```python
   if datasource.backend_type == "clickhouse":
       return _build_clickhouse(datasource.name, kwargs)
   ```

3. Add `_build_clickhouse` function:

   - **Required fields**: `host`
   - **Optional fields**: `database` (default `"default"`), `user`
     (default `"default"`), `port`, `password`, `client_name`, `secure`,
     `compression`, `settings`
   - `password` supplied via `password_env` (resolved by `_effective_kwargs`)
   - `secure` coerced to `bool` if present
   - `settings` forwarded as `dict` if present
   - Calls `ibis.clickhouse.connect(**connect_kwargs)`

### Authoring example

```python
import marivo.datasource as md

md.datasource(
    name="my_ch",
    backend_type="clickhouse",
    host="clickhouse.example.com",
    database="analytics",
    user="reader",
    password_env="CLICKHOUSE_PASSWORD",
    secure=True,
    description="Production ClickHouse cluster",
)
```

## Error handling

No new error classes. The existing structured errors cover all failure modes:

- `DatasourceBackendTypeUnsupportedError`: fired when `backend_type` is not in
  `SUPPORTED_BACKEND_TYPES`; adding `"clickhouse"` makes it accepted
- `DatasourceFieldInvalidError`: fired by `_require` when `host` is missing
- `DatasourceEnvVarMissingError`: fired by `_effective_kwargs` when
  `password_env` references an unset variable

## Testing

Follow the existing pattern in `tests/test_analysis_profiles_backends.py`:

- Unit test for `_build_clickhouse` with required + optional kwargs, mocking
  `ibis.clickhouse.connect`
- Unit test for missing required `host` → `DatasourceFieldInvalidError`
- Unit test for `build_backend` dispatch reaching `_build_clickhouse`
- No integration test against a real ClickHouse instance (consistent with
  existing backends)

## Dependency

`ibis-framework[clickhouse]` must be installed (adds `clickhouse-connect`).
This is an optional extra, same as the existing mysql/postgres/trino drivers.
Marivo's `pyproject.toml` does not need to add it as a required dependency;
users install it alongside ibis when they need ClickHouse support.
