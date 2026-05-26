# marivo-py-analysis profile registry

Use this reference whenever you need to provision, inspect, or repair the
backend connections that `mv.session.create` / `mv.session.attach` resolve at
runtime.

## What profiles are

`marivo.semantic_py` declares datasource *identity* (`name`, `backend_type`).
It never persists hosts, ports, or credentials. The **profile registry** is the
runtime-side complement: a user-scope file that records the non-secret
connection metadata once so the agent does not have to ask the user again on
every session.

- Location: `$MARIVO_HOME/profiles/profiles.json`, defaulting to
  `~/.marivo/profiles/profiles.json`.
- Scope: **user**, not project. Reused across every Marivo project on the same
  machine.
- Isolation: never written to `<project>/.marivo/`, the semantic IR,
  `meta.json`, or `index.db`.
- File mode: `0600`; parent directory `0700`.
- Schema: versioned (`schema_version = 1`). Unknown versions raise
  `ProfileSchemaVersionError` rather than being silently rewritten.

## Sensitive credentials

These field stems are sensitive and **must not** be stored as literals — the
store layer raises `ProfileSecretInPlaintextError` on write:

`password, token, secret, secret_key, access_key, private_key, passphrase, api_key`

Reference them via a sibling `*_env` field that names an environment variable:

```python
mv.profiles.set(
    "warehouse",
    backend_type="trino",
    host="trino.example.internal",
    port=8080,
    user="analytics",
    catalog="hive",
    password_env="WAREHOUSE_PWD",   # value read from os.environ["WAREHOUSE_PWD"]
)
```

The profile file persists `password_env: "WAREHOUSE_PWD"`. The actual secret
lives only in the process environment and is resolved at backend-build time.

## Default backend factory

When `mv.session.create` / `mv.session.attach` is called without an explicit
`backends=` mapping or `backend_factory=` callable, the session resolves each
datasource by calling `mv.profiles.build_backend(name)`. This is the
recommended default flow.

```python
import marivo.analysis_py as mv

mv.profiles.set("warehouse", backend_type="trino", host="...", port=8080,
                user="analytics", catalog="hive", password_env="WAREHOUSE_PWD")
session = mv.session.create(name="analysis")     # uses the profile automatically
```

If the semantic model references a datasource with no matching profile entry,
the backend cache raises `ProfileMissingError` on first access. The error
message includes the available profile names and a pasteable
`mv.profiles.set(...)` snippet.

## Explicit override

Tests, CI, or any flow that needs full control over the backend can opt out
of profile resolution:

```python
import ibis
session = mv.session.create(
    name="analysis",
    backend_factory=lambda name: ibis.duckdb.connect(":memory:"),
    use_profiles=False,
)
```

`backend_factory=` / `backends=` always win over profiles. `use_profiles=False`
additionally disables the auto-factory fallback so a missing profile cannot
mask a misconfigured test.

## Public API

| Call | Returns | Notes |
| --- | --- | --- |
| `mv.profiles.set(name, *, backend_type, **fields)` | `ProfileSummary` | Sensitive fields must use `*_env`. |
| `mv.profiles.list()` | `list[ProfileSummary]` | Sorted by name. |
| `mv.profiles.describe(name)` | `ProfileDescription` | `literal_fields` + `env_refs`; never returns secret values. |
| `mv.profiles.remove(name)` | `bool` | Idempotent; `False` when the name was unknown. |
| `mv.profiles.test(name)` | `ProfileTestResult` | Opens the backend, calls `list_tables()`, records latency. |
| `mv.profiles.build_backend(name)` | live ibis backend | Mostly used internally by sessions. |
| `mv.profiles.audit_project(project)` | `ProfileAuditResult` | Cross-checks a `SemanticProject` and returns `present` / `missing` datasource names. |

## Backend templates

### DuckDB

```python
mv.profiles.set("local_warehouse", backend_type="duckdb", path=":memory:")
# or
mv.profiles.set("local_warehouse", backend_type="duckdb", path="/data/warehouse.duckdb")
```

### Trino

```python
mv.profiles.set(
    "warehouse",
    backend_type="trino",
    host="trino.example.internal",
    port=8080,
    user="analytics",
    catalog="hive",                  # mapped to ibis 'database' kwarg
    schema="default",
    http_scheme="https",
    source="marivo-analysis",
    client_tags=["standby", "routing_group=wide"],
    password_env="WAREHOUSE_PWD",
)
```

Field mapping from a prompt-provided Trino JSON:

| Prompt field | Profile field | Rule |
| --- | --- | --- |
| `host` | `host` | Copy. |
| `port` | `port` | Copy integer. |
| `user` | `user` | Copy. |
| `catalog` | `catalog` | Profile uses Trino-native name; mapped to ibis `database` at build time. |
| `schema` | `schema` | Copy when the prompt provides a default schema. |
| `source` | `source` | Copy application name. |
| `http_scheme` | `http_scheme` | Usually `http` or `https`. |
| `client-tags` (string) | `client_tags` (list) | Split on `,` and strip. |
| `client_tags` (list) | `client_tags` | Pass through. |
| `password` (string) | `password_env` (env var name) | **Never** store the literal — write the env var instead. |
| `session_properties` | `session_properties` | Pass dict through. |

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

## Auditing before running an analysis

Before issuing a chain of intents, cross-check what the semantic model needs
against what is configured:

```python
result = mv.profiles.audit_project(session.semantic_project)
if result.missing:
    # Ask the user once for everything that is missing, then call
    # mv.profiles.set(...) for each entry.
    ...
```

## CI / Docker

In environments without a real home (CI runners, ephemeral containers), prefer
one of:

- Point `MARIVO_HOME` at a writable scratch directory and call
  `mv.profiles.set(...)` from a pre-step.
- Skip the registry entirely with `use_profiles=False` and provide
  `backend_factory=` / `backends=` directly. Env vars consumed by the factory
  remain the canonical secret source.

## Errors

All raised from `marivo.analysis_py.errors` and subclass `AnalysisError`:

- `ProfileMissingError` — datasource referenced in the semantic model has no
  matching profile entry.
- `ProfileEnvVarMissingError` — profile field references an env var that is
  not set.
- `ProfileSecretInPlaintextError` — caller tried to write a sensitive field
  as a literal.
- `ProfileFieldInvalidError` — required field missing, wrong type, or
  nested value.
- `ProfileBackendTypeUnsupportedError` — `backend_type` is not handled by the
  registry.
- `ProfileSchemaVersionError` — the registry file's `schema_version` is not
  understood by this `marivo.analysis_py` build.
- `ProfileConnectionError` — `mv.profiles.test()` could not open the backend.

Each error renders location / cause / fix snippet via the shared
`AnalysisError.__str__` template; the hint always includes a pasteable
`mv.profiles.set(...)` or `export` line.
