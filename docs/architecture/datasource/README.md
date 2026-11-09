# Datasource Connections and Credentials

Datasource owns physical access and connection identity. Business meaning belongs to
[Semantic](../semantic/overview.md); physical descriptors and provider reads belong to
[Sources and execution](sources-and-execution.md), and bounded inspection belongs to
[Discovery](discovery.md).

## Datasource declaration

A datasource is one typed spec per backend. The constructor validates a
lowercase snake_case name matching `[a-z][a-z0-9_]*` and splits the
declared fields into literal connection `fields` and secret `env_refs`.
All five constructors and Specs also accept `wall_clock_timezone: str | None = None`.
This first-class declaration supplies the source timezone for time dimensions
without their own parser timezone: naive timestamps, supported encoded local
clocks, DATE business days and inherited `hour_prefix` clocks. It accepts
IANA names or fixed offsets, normalized at construction. The built-in `default`
has no declared timezone. Missing declarations are never filled from the reader,
host or report timezone. Physical instants (including MySQL TIMESTAMP and
ClickHouse DateTime/DateTime64) retain their native meaning.

`wall_clock_timezone` is stored separately from connection `fields` and secret
`env_refs`, and is never a driver kwarg. It participates in semantic dependency
and definition fingerprints, while raw acquisition evidence identity excludes
it. Changing it changes the captured datasource definition.
Trino has no public `timezone` parameter; its private UTC connection setting
only stabilizes transport.

```python
# models/datasources/warehouse.py
import marivo.datasource as md

md.trino(
    name="warehouse",
    host="trino.example.com",
    catalog="hive",  # connection target, mapped to the ibis database
    schema="sales_mart",  # optional default schema
    user=md.env("WAREHOUSE_USER"),
    auth=md.env("WAREHOUSE_AUTH"),
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

The `duckdb`, `all`, and `dev` extras require DuckDB `>=1.5.3,<2`.
Frame evaluation requires a declared DuckDB database file to exist before opening it.
Set `read_only=True` to use DuckDB's native read-only file access.
Marivo disables terminal progress bar printing on each DuckDB connection it opens.

MySQL and MariaDB use PyMySQL through a private Ibis SQL backend adapter.
`marivo[mysql]` includes RSA authentication dependencies and needs no local
MySQL/MariaDB client C library. Ibis still owns MySQL SQL compilation.
MySQL, MariaDB, PostgreSQL, and Trino managed connections use autocommit.
For MySQL or PostgreSQL declarations that specify `autocommit=False`, omit the
option or set it to `True`. Omit `isolation_level` from Trino `extra` to use its
native autocommit mode. MySQL and MariaDB connections initialize to UTC for transport.
MySQL reader initialization uses the fixed offset `+00:00`, so connecting does not
require populated named time zone tables. Native conversions involving named
source timezones still require the selected names in those tables.
`extra` accepts JSON-safe PyMySQL connection options; mysqlclient-specific
`ssl_mode`, `auth_plugin` and protocol compression are unsupported and fail
explicitly. Use PyMySQL's documented TLS options without weakening the required
certificate or server identity checks. Driver installation alone does not prove
that a server's authentication or TLS policy is supported.

Trino requires an explicit `user` declaration so connection identity never
falls through to a backend-library default. `auth` remains optional for
Trino deployments that do not require an authentication token or password.

Trino exact `.median()` and `.quantile(q)` fail source compilation because no
qualified native exact algorithm is available. Explicit `.approx_median()` and `.approx_quantile(q)` use
native `approx_percentile` when approximation is acceptable. Analysis preparation qualifies the selected
operation and physical types against its backend. Semantic readiness checks physical declarations and time declarations;
loading alone grants no backend execution support.

`DatasourceSpec` is the closed union of these five types. Concrete engine
connection builders live in `marivo/datasource/engines/` and are internal — the
public surface is the spec constructors and `DatasourceSymbol`.

Every spec has a bounded, decision-first `show()` / `render()` card. It exposes
the declared state, exact ref, core connection target, declared wall-clock timezone, credential field names,
and only a count for additional configuration. It never expands resolved
secrets, session/settings maps, `extra`, or AI context. Read `.fields` and
`.env_refs` only when exact configuration is needed. The default repr is a
one-line pointer to this card rather than a dataclass field dump.

### Fields, names, and context

- **Literal fields** (`host`, `port`, `catalog`, `database`, `schema`, `path`, …)
  are stored verbatim in the project file and must be JSON-safe.
- **Credential fields** (`user`, `password`, `auth`) accept immutable
  `md.env("NAME")` references; literal strings are rejected. There is no host credential field.
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
its semantic identity. Explicit `md.env("NAME")` references remain unchanged unless the
author edits them.

## Credentials and secret persistence

Secrets never live in project state. Instead a spec records env-var names, and
Marivo resolves them at connect time through a provider chain:

```text
EnvProvider (os.environ)  →  LocalPlaintextCache (~/.marivo/secrets.toml)
```

- **Resolution.** Each `md.env("NAME")` name is resolved against the chain. A name that is
  set in neither environment nor cache raises `DatasourceEnvVarMissingError`
  naming the datasource, field, and env var.
- **No implicit names.** Marivo resolves only explicitly declared `md.env("NAME")`
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
`models/datasources/` files, which only carry `md.env("NAME")` names.

DuckDB credentials are datasource-owned and declared as closed variants in
`secrets=[...]`. The constructor has seven parameters: `name`, `path`, `read_only`,
`secrets`, `wall_clock_timezone`, `ai_context`, and `extra`.

```python
md.duckdb(
    name="lake",
    secrets=[
        md.s3_secret(
            scope="s3://analytics-lake/warehouse/",
            region="us-west-2",
            key_id=md.env("LAKE_KEY_ID"),
            secret=md.env("LAKE_SECRET"),
        ),
        md.http_bearer_secret(scope="https://api.example/v1/", token=md.env("API_TOKEN")),
        md.http_headers_secret(
            scope="https://change-focus.example/api/v2/",
            headers={"x-secretid": md.env("CF_ID"), "x-signature": md.env("CF_SIG")},
        ),
    ],
)
```

S3 requires a region and static key references; optional `session_token` is also
an EnvRef. `endpoint`, `url_style` and `use_ssl` default to `s3.amazonaws.com`,
`vhost` and `True`. MinIO uses its endpoint with `url_style="path"` and the
appropriate TLS setting. SDK credential chains are not supported. HTTP headers
must be a non-empty mapping to EnvRef values. Scopes are unique per DuckDB type;
bearer and headers share type `http`, so cannot occupy the same scope.
`extra` cannot carry secrets or credential fields.

IR stores non-secret secret metadata in `fields["secrets"]`, including header
names and whether an S3 session token is declared. `env_refs` uses keys such as
`s3@s3://analytics-lake/warehouse/.key_id` and
`http@https://change-focus.example/api/v2/.header:x-secretid`. Consumers generate
keys from the metadata; they never split keys to recover scopes or header names.
Relational `env_refs` retains `user`, `password`, and `auth`, preserving those
connection fingerprints. HTTP DuckDB fingerprints change with this representation.
Registration writes constructor calls and `md.env` references; static doctor
recognizes these literal calls without executing declaration files.

With `md.credential_scope(resolver=...)`, each reference becomes a
`CredentialRequest.reference`, with the corresponding IR keys in `fields`.
References shared by multiple fields resolve once per acquisition. Host values
never enter the default cache. Every operation opens a connection and resolves
again, so credential rotation applies to the next operation.

Scopes are host and path prefixes. User info, query strings, and fragments are
rejected. Native S3 credentials and HTTP proxy credentials cannot be passed
through `extra`.

Connections install temporary secrets in `(type, scope)` order, named
`marivo_<datasource>_<index>`. DuckDB shares its secret catalog across connections
to the same database instance. Marivo requires exclusive managed access when a
file or named in-memory database uses scoped credentials. Connections created
outside Marivo can still see the instance's temporary secrets while installed.
Use a separate DuckDB database for concurrent access with a different credential
scope. Marivo drops its temporary secrets on disconnect, including after a
partial installation. Parameterized installation statements bind credential
values, quote the name identifier, and log only the template. Installation
failure reports type and scope with a repair. `md.load().test` performs a literal
round-trip, so a successful test can cache S3 or HTTP credentials without
validating access to the remote endpoint.

HTTP JSON execution retains scoped headers in connection memory and selects the
longest matching declared scope. No match means no credential headers.
Authenticated GET and all POST requests reject automatic redirects; author the
final URL explicitly. Resolved values never enter source descriptors or metadata.

DuckDB persistent secrets remain enabled. DuckDB chooses the longest matching
scope, including persistent secrets in `~/.duckdb/stored_secrets`; a persistent
secret more specific than a declaration can therefore override it. Marivo does
not isolate or inspect this native behavior. See the
[DuckDB secrets manager](https://duckdb.org/docs/stable/configuration/secrets_manager).

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
- Each declaration read uses an independent synthetic package; relative imports
  within `models/datasources/` execute once in that read. Use, for example,
  `from .settings import WAREHOUSE_PATH`. Model absolute imports are unsupported;
  loading does not modify `sys.path`. Installed dependencies use ordinary imports.
  Modules from successful and failed loads remain until process exit. No load
  purges another load's modules, and load identifiers do not affect definition identity.
  `DatasourceCatalog` retains its workspace and reads current declarations on each
  catalog operation; it is not a frozen SemanticCatalog snapshot.
- Restart Python after package upgrades or ordinary imported dependency changes.
  Relative model imports use fresh namespaces; ordinary dependencies retain Python
  module caching. Marivo does not delete bytecode. Execution errors preserve the original
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
on an owner captured by another scope. A Semantic Catalog and Session retain their
captured resolver after ambient scope exit. A `DatasourceCatalog` from `md.load()`
binds its workspace, not a resolver: each later `test()` uses the scope active at
that call. Likewise, `md.inspect()` and a later `SourceInspection.sample()` are
separate operations: when a source read is needed, each uses the scope active at
that call. A sample cache hit neither reads the source nor resolves credentials.
Callers that receive a raw backend through private test infrastructure own its
direct driver calls.

Every project has `default`, a reserved, fresh in-memory DuckDB datasource.
Discovery does not create files or open connections. Its memory is connection
local; use durable files or a declared datasource for data that must survive.
Governed Source/backend capabilities apply equally to `default`.

## Implementation map

- [authoring.py](../../../marivo/datasource/authoring.py) owns typed spec construction.
- [authoring_store.py](../../../marivo/datasource/authoring_store.py) and [loader.py](../../../marivo/datasource/loader.py) own registration and loading.
- [credentials.py](../../../marivo/datasource/credentials.py) and [secrets.py](../../../marivo/datasource/secrets.py) own resolution and the user-global cache.
