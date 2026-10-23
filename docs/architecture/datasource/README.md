# Datasource Connections and Credentials

Datasource owns physical access and connection identity. Business meaning belongs to
[Semantic](../semantic/overview.md); physical descriptors and provider reads belong to
[Sources and execution](sources-and-execution.md), and bounded inspection belongs to
[Discovery](discovery.md).

## Datasource declaration

A datasource is one typed spec per backend. The constructor validates a
lowercase snake_case name matching `[a-z][a-z0-9_]*` and splits the
declared fields into literal connection `fields` and secret `env_refs`.

```python
# models/datasources/warehouse.py
import marivo.datasource as md

md.trino(
    name="warehouse",
    host="trino.example.com",
    catalog="hive",  # connection target, mapped to the ibis database
    schema="sales_mart",  # optional default schema
    user_env="WAREHOUSE_USER",
    auth_env="WAREHOUSE_AUTH",
)
```
Every constructor returns its spec and, when executed inside a datasource loader
file, auto-declares it for the project. `spec.ref` yields the `DatasourceSymbol`
used everywhere downstream.

### Backends and engines

| Constructor | Spec | `backend_type` | Notes |
|---|---|---|---|
| `md.duckdb(...)` | `DuckDBSpec` | `duckdb` | Local file / in-memory. Also the engine that reads parquet/csv/json file sources. |
| `md.trino(...)` | `TrinoSpec` | `trino` | `catalog` is the connection target; `schema` is an optional default. |
| `md.mysql(...)` | `MySQLSpec` | `mysql` | `host` + `database` required. Also connects to MariaDB; native query limits use MySQL milliseconds or MariaDB seconds. |
| `md.postgres(...)` | `PostgresSpec` | `postgres` | `host` + `database` required; optional `schema`. |
| `md.clickhouse(...)` | `ClickHouseSpec` | `clickhouse` | Session-id autogeneration defaults off for analysis stability. |

Trino requires an explicit `user_env` declaration so connection identity never
falls through to a backend-library default. `auth_env` remains optional for
Trino deployments that do not require an authentication token or password.

Trino exact `.median()` and `.quantile(q)` use source-ranked bounds and typed
linear interpolation. Explicit `.approx_median()` and `.approx_quantile(q)` use
native `approx_percentile` when approximation is acceptable. Analysis preparation qualifies the selected
operation and physical types against its backend. Semantic readiness checks physical declarations and temporal certification;
loading alone grants no backend execution support.

`DatasourceSpec` is the closed union of these five types. Concrete engine
connection builders live in `marivo/datasource/engines/` and are internal — the
public surface is the spec constructors and `DatasourceSymbol`.

Every spec has a bounded, decision-first `show()` / `render()` card. It exposes
the declared state, exact ref, core connection target, credential field names,
and only a count for additional configuration. It never expands resolved
secrets, session/settings maps, `extra`, or AI context. Read `.fields` and
`.env_refs` only when exact configuration is needed. The default repr is a
one-line pointer to this card rather than a dataclass field dump.

### Fields, names, and context

- **Literal fields** (`host`, `port`, `catalog`, `database`, `schema`, `path`, …)
  are stored verbatim in the project file and must be JSON-safe.
- **Env-ref fields** end in `_env` (`user_env`, `password_env`, `auth_env`,
  `host_env`, …) and store the *name* of an environment variable.
- **`ai_context=`** accepts an `ms.ai_context(...)` value (never a raw dict) so a
  datasource can carry business annotations; text belongs in
  `business_definition`. There is no `md.ai_context` constructor — the semantic
  module owns that value type (see `marivo.help("semantic.ai_context")`).
- **`name`** is the global datasource key. Datasource names are never
  `<domain>.<datasource>`; a datasource does not belong to a semantic domain.

### Datasource references

Semantic declarations reference a datasource by one exact ref:

```python
warehouse = ms.ref.datasource("warehouse")  # -> DatasourceSymbol
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
```
`ms.ref.datasource(...)` accepts only the one-segment datasource path. Bare
strings and kind-qualified strings such as `"datasource.warehouse"` are
rejected — the exact ref is the contract. Renaming a datasource changes
its semantic identity. Explicit `*_env` references remain unchanged unless the
author edits them.

## Credentials and secret persistence

Secrets never live in project state. Instead a spec records env-var names, and
Marivo resolves them at connect time through a provider chain:

```text
EnvProvider (os.environ)  →  LocalPlaintextCache (~/.marivo/secrets.toml)
```

- **Resolution.** Each `*_env` name is resolved against the chain. A name that is
  set in neither environment nor cache raises `DatasourceEnvVarMissingError`
  naming the datasource, field, and env var.
- **No implicit names.** Marivo resolves only explicitly declared `*_env`
  references. It does not scan ambient `MARIVO_<DATASOURCE>_<FIELD>` variables;
  when a connection field is omitted, the selected Ibis backend owns its default
  or required-field behavior.
- **Post-validation caching.** After a *validated* round trip — `md.load().test(ref)` or
  the first successful analysis execution in a session — Marivo attempts to cache
  env-sourced secrets in plaintext at user-global `~/.marivo/secrets.toml` so later
  sessions can connect without the env var re-exported. This is a deliberate,
  documented convenience, gated by the guards below.
- **Best-effort writes.** A cache write failure emits a sanitized warning but does
  not invalidate a successful connection test or analysis execution. Automatic
  caching does not fall back to project-local storage.
- **Guards.** Persistence is disabled when `MARIVO_PERSIST_CREDENTIALS=0` or `CI` is
  set. The cache file is written atomically at mode `0o600`, its parent at
  `0o700`, and Marivo refuses to write it anywhere inside a git repository.
  Insecure permissions on an existing cache raise
  `DatasourceSecretStorePermissionsError`.

The persistence boundary is a hard rule: resolved secret values may be cached in
plaintext **user-global** state, but must never be written into **project-local**
`models/datasources/` files, which only carry `*_env` names.

DuckDB HTTP authentication is datasource-owned because credentials describe how
the connection reaches a protected host, not which rows one Entity requests.
The project stores only an environment variable name and an explicit URL scope:

```python
md.duckdb(
    name="hawkeye",
    path=":memory:",
    http_scope="http://hawkeye.example/report/api/",
    http_bearer_token_env="HAWKEYE_TOKEN",
)
```
For custom headers, map every header name to its secret environment variable.
This supports single-header APIs and machine authentication that requires a
header pair:

```python
md.duckdb(
    name="change_focus",
    http_scope="http://change-focus.example/api/v2/change/list",
    http_headers_env={
        "x-secretid": "CHANGE_FOCUS_SECRET_ID",
        "x-signature": "CHANGE_FOCUS_SIGNATURE",
    },
)
```
Bearer and custom-header modes are mutually exclusive. At connection time
Marivo resolves every environment-backed value and installs a temporary DuckDB
HTTP secret constrained by `http_scope`; the same connection keeps the scoped
headers in memory for JSON execution. Authenticated GET requests and all POST
requests reject automatic redirects; author the final in-scope URL explicitly.
Resolved values are never serialized into
`md.json(...)` or project metadata.

## Registration and state storage

Declaration files call datasource constructors directly; the datasource loader
automatically collects them. `md.register()` is a persistence operation for setup
scripts executed outside datasource and semantic model loading:

```python
# setup_datasources.py (run outside model loading)
spec = md.duckdb(name="warehouse", path="/data/warehouse.duckdb")
md.register(spec)  # writes models/datasources/warehouse.py
md.load().test(spec.ref).show()  # validated live round trip
```
- `md.register(spec, project_root=...)` persists a spec as a Python file under
  `models/datasources/`; authoring that file by hand is equally valid.
  Registration saves only this declaration and returns its summary without
  executing other declaration files. Success proves the file was saved, not
  that the complete project is valid; load the project separately to validate it.
- Calling `md.register()` during model loading, including synchronous imports
  from a semantic file or registration into another project, raises
  `DatasourceLoadError` before configuration reads, writes, or nested loading.
  Remove the `md.register(...)` wrapper and keep the datasource constructor.
  Semantic loading preserves this repair and reports `invalid_project`.
- `md.remove(name)`, `md.load().list()`, and `md.load().describe(name)` manage and inspect the
  registered set. `md.load(workspace_dir=...)` returns a `DatasourceCatalog`.
- Restart Python after package upgrades or model edits/deletions. Ordinary
  imported dependencies are not guaranteed to hot-reload; Marivo does not purge
  their module caches or delete bytecode. Execution errors preserve the original
  exception through `__cause__`, expose `exception_type` and `traceback`, and
  display the complete traceback by default. Duplicate errors retain both
  `declaration_paths`; if a source no longer exists, the repair requests a restart
  and lists any existing related bytecode only as a diagnostic lead.
- All datasource reads, including catalog methods, inspection, and connections,
  use the same **layered / multi-root** set as `ms.load()`: local `models/`
  followed by `marivo.toml [semantic].layer_paths`. Duplicate datasource names
  fail with both declaration locations; roots do not override each other.
  Registration and removal write only the project-local datasource directory.
- `md.load(workspace_dir=...)` and `md.inspect(..., workspace_dir=...)` use an
  exact workspace root. Without it, project selection uses `MARIVO_PROJECT_ROOT`,
  the nearest ancestor manifest, then cwd. Catalog methods retain their bound
  workspace even when cwd or the environment changes.
- Each inspect call loads one datasource definition and passes it through its
  metadata steps. Later inspection and catalog reads use current declarations;
  an existing `SemanticCatalog` retains its loaded definitions until a new
  `ms.load()` call. No process-global declaration cache publishes file edits.
- The private `load_datasources()` accepts an exact datasource directory,
  including an arbitrary directory name. Recognizable workspace, models-parent,
  and semantic-directory arguments fail with the correct datasource path;
  missing or empty datasource directories remain valid empty inputs.
- `md.load().test(ref, timeout_seconds=30)` returns a `DatasourceTestResult` and
  triggers post-validation secret caching. Its connect handshake and
  Ibis-compiled literal round-trip run synchronously on the caller thread.
  They request independent native connection and query timeouts; unsupported
  query limits are skipped. There is no complete-call deadline.
  Live backends remain private to the datasource adapter.

`DatasourceTestResult.show()` is the authoritative connection-test stop point.
On failure, `.failure` carries a bounded `DatasourceFailure` with a stable stage
code (`connection_open_failed`, `connection_roundtrip_failed`,
`connection_timeout`, or `connection_roundtrip_timeout`), backend exception
type/code/name, and a sanitized message. `.repair` provides the focused help
target and action. The two timeout codes distinguish the connect handshake from
the Ibis literal round-trip. Secret-cache write warnings do not change a successful
result. Successful results prove only that the current datasource connection test
passed.

## Host credential ownership

`md.credential_scope(resolver=host)` supplies a `CredentialResolver` for
Marivo-owned acquisitions. Requests contain project root, datasource, exact
reference and sorted field names. The host owns credential-service timeouts and returns
`md.SecretValue`; displays and serialization do not reveal it. Explicit resolvers
never fall back to environment variables or the plaintext default cache, and
injected values are not cached. An explicit conflicting resolver is rejected
on an owner captured by another scope. Catalog, Session and operation owners
retain their captured resolver after ambient scope exit. Callers that receive a
raw backend through private test infrastructure own its direct driver calls.

Every project has `default`, a reserved, fresh in-memory DuckDB datasource.
Discovery does not create files or open connections. Its memory is connection
local; use durable files or a declared datasource for data that must survive.
Governed Source/backend capabilities apply equally to `default`.

## Implementation map

- [authoring.py](../../../marivo/datasource/authoring.py) owns typed spec construction.
- [authoring_store.py](../../../marivo/datasource/authoring_store.py) and [loader.py](../../../marivo/datasource/loader.py) own registration and loading.
- [credentials.py](../../../marivo/datasource/credentials.py) and [secrets.py](../../../marivo/datasource/secrets.py) own resolution and the user-global cache.
