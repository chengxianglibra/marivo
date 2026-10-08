# Datasource Layer Design

DuckDB datasource/entity readers retain their existing JSON, authentication,
extension and registration facilities. Internal execution resources belong to
the backend adapter. Remote analysis activation separately requires compatibility
with read-only accounts; it does not globally disable DuckDB authoring.


Status: draft design. This document describes the current design of
`marivo.datasource` (`md`): the project-level connection and evidence layer that
the semantic layer builds on. It is the ground-truth boundary between physical
storage and business semantics.

See also:

- [overview.md](overview.md) — how datasource, semantic, and analysis layer.
- [authoring-workflow.md](authoring-workflow.md) — where discovery evidence is
  consumed while authoring semantic objects.
- [semantic-object-model.md](semantic-object-model.md) — how entities reference
  a datasource and a physical source.
- `marivo.help("datasource.authoring")` — the runnable, always-current
  datasource authoring checklist.

## Role in the architecture

`marivo.datasource` answers one question: *how does Marivo physically reach the
data, and what does that data actually look like?* It owns three things and
nothing else:

- **Connections** — typed, shareable declarations of a backend (a Trino
  cluster, a MySQL database, a DuckDB file, …).
- **Physical sources** — descriptors that name a table, view, or file inside a
  connection.
- **Evidence** — bounded, read-only inspection and discovery of the physical
  facts (schema, comments, partitions, column profiles, join cardinality).

It deliberately does **not** own business meaning. A datasource is the
*execution source* of an entity; it is never the *business caliber* of a metric.
Column names, table names, and profiles are candidate signals, not decisions —
the semantic layer settles meaning from this evidence plus human judgment (see
[authoring-workflow.md](authoring-workflow.md)).

The layering is strict and one-directional:

```text
marivo.datasource   connection + physical source + evidence   (this document)
        ↓ Ref[datasource] + TableSource + DatasourceResult
marivo.semantic     entity / dimension / metric / relationship
        ↓ typed semantic refs
marivo.analysis     observe / compare / attribute / ...
```

Semantic files reference a datasource only by its ref and reuse discovered
evidence; they never re-open connections or re-supply raw table tuples once an
entity is registered.

## Design principles

- **Typed specs, not config dicts.** Every backend is a frozen dataclass
  (`DuckDBSpec`, `TrinoSpec`, `MySQLSpec`, `PostgresSpec`, `ClickHouseSpec`) with
  a fixed `backend_type`. Unknown fields fail loudly at construction; there is no
  free-form dict entry point on the public surface. Rare untyped ibis kwargs go
  through an explicit, JSON-safe `extra=` escape hatch.
- **Credentials are references, never literals.** Sensitive fields are authored
  as `*_env` names that point at environment variables. Plaintext secrets in a
  spec are rejected at construction time.
- **Project state is shareable and secret-free.** A datasource declaration is a
  small Python file under `models/datasources/` that can be copied alongside
  `models/semantic/` into another analysis project. It contains only literal
  connection fields and env-var *names* — never resolved secret values.
- **Snapshot evidence is not authorship.** `md.inspect(...)` exposes physical
  facts before data access; optional explicitly scoped sampling retains generic
  rows, profiles, source evidence, coverage, and cache identity. A snapshot does
  not project semantic candidates or authorize business meaning.
- **Fail closed.** Missing env vars, unreachable backends, dialect/`backend_type`
  mismatch, and unsafe partition scans raise structured errors that state what
  was expected, what was received, and the concrete next step. Authoring errors
  render their stable code and stage. An acquisition execution failure permits
  one exact bounded retry only when the caller's remaining data-access budget
  permits it; caller-provided read-count, row, and timeout limits take
  precedence. If the same structured code and backend name recur, the caller
  stops and reports the datasource backend blocker.

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
file, auto-declares it for the project. `spec.ref` yields the `Ref[datasource]`
used everywhere downstream.

### Backends and engines

| Constructor | Spec | `backend_type` | Notes |
|---|---|---|---|
| `md.duckdb(...)` | `DuckDBSpec` | `duckdb` | Local file / in-memory. Also the engine that reads parquet/csv/json file sources. |
| `md.sqlite(...)` | `SQLiteSpec` | `sqlite` | Local file / in-memory SQLite tables and views; optional query-only mode and declared-type mapping. |
| `md.trino(...)` | `TrinoSpec` | `trino` | `catalog` is the connection target; `schema` is an optional default. |
| `md.mysql(...)` | `MySQLSpec` | `mysql` | `host` + `database` required. |
| `md.postgres(...)` | `PostgresSpec` | `postgres` | `host` + `database` required; optional `schema`. |
| `md.clickhouse(...)` | `ClickHouseSpec` | `clickhouse` | Session-id autogeneration defaults off for analysis stability. |

Trino requires an explicit `user_env` declaration so connection identity never
falls through to a backend-library default. `auth_env` remains optional for
Trino deployments that do not require an authentication token or password.

`DatasourceSpec` is the closed union of these six types. Concrete engine
connection builders live in `marivo/datasource/engines/` and are internal — the
public surface is the spec constructors and `Ref[datasource]`.

Every spec has a bounded, decision-first `show()` / `render()` card. It exposes
the declared state, exact ref, core connection target, credential field names,
and only a count for additional configuration. It never expands resolved
secrets, session/settings maps, `extra`, or AI context. Read `.fields` and
`.env_refs` only when exact configuration is needed. The default repr is a
one-line pointer to this card rather than a dataclass field dump.

SQLite uses `md.table(...)` for tables and views. It does not consume the
DuckDB-owned Parquet, CSV, or JSON descriptors. `read_only=True` enables
connection-level SQLite authorizer write denial; Marivo's bounded inspection and diagnostic
reads enable the same protection internally. SQLite does not compile median or
percentile aggregations or string `strptime` expressions in Ibis 12, so those operations
fail through the structured Marivo contract; use a supported aggregation and a
native temporal column instead.

### Fields, names, and context

- **Literal fields** (`host`, `port`, `catalog`, `database`, `schema`, `path`, …)
  are stored verbatim in the project file and must be JSON-safe.
- **Env-ref fields** end in `_env` (`user_env`, `password_env`, `auth_env`,
  `host_env`, …) and store the *name* of an environment variable.
- **`ai_context=`** accepts an `ms.ai_context(...)` value (never a raw dict) so a
  datasource can carry business annotations; text belongs in
  `business_definition`. There is no `md.ai_context` constructor — the semantic
  module owns that value type (see `marivo.help("datasource.ai_context")`).
- **`name`** is the global datasource key. Datasource names are never
  `<domain>.<datasource>`; a datasource does not belong to a semantic domain.

### Datasource references

Semantic declarations reference a datasource by one exact ref:

```python
warehouse = ms.ref.datasource("warehouse")  # -> Ref[datasource]
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
```
`ms.ref.datasource(...)` accepts only the one-segment datasource path. Bare
strings and kind-qualified strings such as `"datasource.warehouse"` are
rejected — the exact ref is the contract. Renaming a legacy datasource changes
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
- **Post-validation caching.** After a *validated* round trip — `md.test(ref)` or
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

## Physical sources

A source descriptor names *what to read* inside a datasource. It is not a
datasource declaration; it is paired with a `Ref[datasource]` in inspection,
discovery, and `ms.entity(source=...)`.

| Constructor | IR | Meaning |
|---|---|---|
| `md.table(name, database=..., columns=...)` | `TableSourceIR` | A catalog-backed table/view with an optional output-name to physical-column projection (any SQL backend). |
| `md.parquet(path, hive_partitioning=...)` | `ParquetSourceIR` | A self-describing DuckDB file source over Parquet. |
| `md.csv(path, columns=..., header=..., delimiter=...)` | `CsvSourceIR` | A DuckDB CSV file source with inferred types and an optional output-name to header projection. |
| `md.json(path, columns=..., format=..., records_path=..., query_params=..., method=..., body=...)` | `JsonSourceIR` | A DuckDB JSON source with inferred types, optional JSON-path projection, and runtime-bindable query-string or POST-body values. |

`TableSource` is the public union of these four IRs. File sources
(`parquet`/`csv`/`json`) are read by the DuckDB engine, so they attach to a
DuckDB datasource ref; `md.table(...)` works against any backend. Parquet carries
self-described physical types. Table, CSV, and JSON sources do not accept authored
physical types: the source is the authority for those facts. The `columns=`
argument is an optional projection mapping stable output aliases to physical
column names or JSON paths. Omitting it exposes all discoverable columns. Source
construction, `ms.entity`, `ms.load()`, and `dataset.contract()` remain source-I/O
free. Database metadata is read only when execution needs types, and only for the
required dependency closure. CSV and JSON types are inferred during their actual
read; remote JSON is not fetched only to inspect its schema.

### Source projections and observed types

Supplying `columns=` to `md.table(...)` selects and renames physical columns. It
does not declare their types. Physical identifiers are quoted atomically, so
dots, spaces, reserved words, and punctuation are treated as one identifier:

```python
events_source = md.table(
    "raw.events",
    database="warehouse",
    columns={
        "event_time": "event.timestamp",
        "score": "_generated_score",
    },
)
```
The mapping is a complete allowlist when present. Execution resolves the selected
physical columns and their actual types from the backend; undeclared physical
columns do not constrain the source contract. `md.inspect` reports available
physical metadata and a copyable projection. Metadata that is unavailable remains
unknown; it is not filled from a user assertion. Unsupported necessary types fail
when the execution closure consumes them. Unused columns do not add execution
type restrictions.

ClickHouse native row reads, a naive datetime is restored to UTC only when the
physical Arrow schema explicitly declares UTC; other timezone mismatches remain
errors. A cursor close failure marks the governed submission failed and records
`close_failed`, rather than claiming successful cursor release. Subsequent owned
connection disconnection is recorded separately.

Missing selected optional drivers raise `DatasourceConnectionError` with the
backend-specific `marivo[backend]` installation repair. Unselected drivers are
not imported by provider discovery.

SQLite and MySQL retain their source-representation checks at execution. For
example, SQLite temporal and Boolean columns must use the backend's admitted
representations, and Boolean values must be exactly 0/1/NULL. Native temporal
analysis also admits PostgreSQL `timestamptz`, MySQL `TIMESTAMP`, and ClickHouse
`DateTime64` through microseconds. MySQL `TIMESTAMP` and ClickHouse timestamp
execution retain verified UTC session/reader requirements; source and report
timezones remain distinct. Reader and report timezone resolution accept IANA
names and explicit offsets. An absent probe capability permits recorded system
fallback; an actual failed or invalid probe does not. Physical instants and
explicit parser authority skip unnecessary reader probes. Native parser
declarations retain their existing IANA validation.


For ClickHouse tables, inspection also reads active `system.parts_columns` and
exposes safe adapter-only physical columns through
`SourceInspection.projectable_columns`. Each row carries the exact physical
name accepted by `columns=`, its observed backend type, and nullability.
Columns with conflicting part types or unparseable backend types are warned
about and omitted. This is physical-column discovery, not Map key enumeration:
dynamic keys that have not been materialized remain outside the governed source
contract and require upstream materialization or a governed database view for
typed Analysis. `md.raw_sql(...)` can answer a separate terminal question about
such data, but its result cannot become a governed source binding.

For a wrapped response, `records_path=` selects the array whose records are read.
Additional object fields are ignored when a projection is supplied. Types are
inferred from returned values; a required projected field missing from every
record or null in every record fails clearly because no physical type can be
inferred. The record path is intentionally limited to `$` plus object-member
access, such as `$.data` or `$.result.items`; filters, wildcards, recursive
descent, and array indexing are not supported. A present, empty array
materializes as zero rows. A missing path or a non-array value fails at execution.

For JSON, each `columns=` value is a relative JSON path: `a.b` selects a nested
object member, `a[0].b` selects a fixed array index, and `a[].b` traverses an
array. Traversed sibling fields must share one array prefix and are projected
from the same element, so their values remain correlated. Independent traversal
roots and more than one traversal in a path fail at declaration instead of
creating a Cartesian product. A record whose traversed array is missing, empty,
or null produces no rows. Top-level field names may contain punctuation or spaces.

```python
changes = md.json(
    "http://change-focus.example/api/v2/change/list",
    columns={
        "change_id": "change_id",
        "app_id": "specificsource[].appid",
        "app_name": "specificsource[].name",
    },
    records_path="$.data.change_infos",
)
```
Parameterized API URLs keep their stable request shape in the semantic project
and bind request-specific values at analysis time:

```python
samples = md.json(
    "http://hawkeye.example/report/api/v2/query_range/datasource/81",
    columns={"metric": "metric", "value": "value", "values": "values"},
    records_path="$.data.result",
    query_params={
        "query": 'sum(pending_containers{q1=~"llst_queue|sycpb|report"}) by (cluster, q1)',
        "start": md.source_param("start"),
        "end": md.source_param("end"),
        "step": "60s",
    },
)
```
`query_params` values are scalars or flat, non-empty scalar lists. Lists encode
as repeated query keys. `md.source_param(name)` declares a required, non-secret
runtime value and may resolve to either shape while occupying one complete query
parameter value; it may also appear inside a fixed list. Marivo URL-encodes names
and values and does not interpret substring templates. The URL may already
contain unrelated fixed parameters, but declaring the same name in both the URL
and `query_params` fails closed.

A JSON-object body enables the minimal POST API case. The request remains lazy:
it is sent when the DuckDB-backed table executes, not while the project is
loaded or inspected. `POST` requires an HTTP(S) URL and `format="auto"`.
`md.source_param(...)` may occupy a complete value anywhere in the body,
including inside an object or array, and may resolve to a scalar or flat,
non-empty scalar list. It does not interpolate string fragments.

```python
gpu_servers = md.json(
    "https://root.example/api/v1/graphql",
    columns={
        "name": "name",
        "bs": "bs",
        "gpuAbstract": "gpuAbstract",
        "status": "status",
    },
    method="POST",
    body={"query": "{ queryServers { name bs gpuAbstract status } }"},
    records_path="$.data.queryServers",
    query_params={"policy-domain": "gpus"},
)
```
Authentication headers are resolved from the owning DuckDB datasource and are
sent only when the final URL is inside its declared `http_scope`. The body shape
stays in `md.json(...)`; only declared non-secret parameter values belong to
analysis-session bindings.

For example, one change-focus page can declare its app and page number as
analysis-scoped values without turning pagination into datasource behavior:

```python
changes = md.json(
    "http://change-focus.example/api/v2/change/list",
    columns={"change_id": "change_id", "title": "title"},
    method="POST",
    body={
        "platform_id": 1,
        "source_type": 2,
        "specific_source": [md.source_param("app_id")],
        "env_id": [1],
        "page_num": md.source_param("page_num"),
        "page_size": 100,
    },
    records_path="$.data.change_infos",
)
```
Marivo executes one request for one binding. Automatic page traversal, app-list
fanout, watermarks, and ingestion remain outside this physical-source contract.

The binding belongs to the analysis execution scope, not to `observe(...)` and
not to persisted `md.json(...)`:

```python
with session.source_bindings(
    {
        ms.ref.entity("monitoring.samples"): {
            "start": "now-3600",
            "end": "now",
        },
    }
):
    dataset = session.observe(ms.ref.metric("monitoring.pending_containers"))
```
Bindings use exact `Ref[entity]` keys and must provide exactly the declared
parameter names. They are nested, context-local, and keyed by the owning Session
runtime, so concurrent agents and another Session in the same task cannot consume
the values. Each binding is a scalar or a flat, non-empty scalar list. Non-secret
bindings participate in analysis and snapshot identity.
Discovery uses the same contract through
`inspection.sample(..., source_params={...})`.

## Registration and state storage

```python
spec = md.duckdb(name="warehouse", path="/data/warehouse.duckdb")
md.register(spec)  # writes models/datasources/warehouse.py
md.test(spec.ref).show()  # validated live round trip
```
- `md.register(spec, project_root=...)` persists a spec as a Python file under
  `models/datasources/`; authoring that file by hand is equally valid.
- `md.remove(name)`, `md.list()`, and `md.describe(name)` manage and inspect the
  registered set. `md.load(workspace_dir=...)` returns a `DatasourceCatalog`.
- Storage is **layered / multi-root**: datasource files are discovered across
  the configured model roots, so a shared base project and a local overlay can
  coexist.
- `md.test(ref, timeout_seconds=30)` returns a `DatasourceTestResult` and
  triggers post-validation secret caching. Its connect handshake and
  Ibis-compiled literal round-trip share a Marivo-side wall-clock deadline.
  Timeout returns a structured failure; a local SQLite connection opens on
  the caller's worker thread and its synchronous open cannot be interrupted.
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

## Inspection and evidence snapshots

`md.inspect(datasource, source)` is metadata-only. It exposes schema, physical
extent, partition state, and enforceable execution capabilities before a user-data
read. Its card includes the exact source descriptor and complete real schema
column names and types. `inspection.partitions(limit=..., order=...)` is also
metadata-only. The default descending-edge request reuses values captured during
inspection; another bound or order performs one bounded metadata query. Use
`order="asc"` or `order="desc"` for the corresponding physical-value edge. This
ordering is not automatically chronological for string or numeric encodings.
Its card identifies the requested edge, value source, completeness and
truncation, shows bounded values, and derives a copyable `md.partition(...)`
scope template. The result status is `complete` when the edge is exhaustive,
`truncated` when the requested bound cuts off additional edge values, and
`incomplete` only when partition metadata is unavailable or invalid. A
truncated edge is not an enumeration of every middle partition. A single
transformed temporal partition instead produces a copyable `md.time_range(...)`
template.

Physical extent always carries provenance and scope. For a ClickHouse
`Distributed` source, Marivo may inspect the resolved local table through
`system.parts`, but the source-wide row count and size remain `unknown`; the
bounded local observation appears only in `physical extent notes` with
`scope=local_node_only`. Marivo does not issue a cluster-wide fanout query.

Ordinary tables use catalog metadata and Parquet uses footer metadata. A projected
table maps those observed physical columns to stable output aliases; a missing
physical identifier is reported as unverified, not filled from an authored type.
CSV and local JSON inspection may infer available types from the file. HTTP JSON
inspection never sends a request solely to discover types, so projected types are
reported as `unknown` until the actual read. If base-table metadata is classified
unavailable, table inspection remains metadata-only, returns projected aliases
with unknown types and extents, and requires an explicit bounded
`md.unpruned(...)` acquisition. Authentication, connection, configuration,
timeout, and unclassified metadata failures still fail closed.

```python
inspection = md.inspect(warehouse, md.table("orders"))
inspection.show()
inspection.partitions().show()

scope = md.partition({"dt": "20260710"}, max_rows=1000, timeout_seconds=30)
snapshot = inspection.sample(
    scope=scope,
    columns=("order_id", "status", "dt", "amount"),
)

snapshot.show()
# Read bounded rows, profiles, coverage, and retained values only when needed.
```
For date or timestamp acquisition, use the same public `PartitionScope` through
`md.time_range("created_at", start=..., end=..., max_rows=...,
timeout_seconds=...)`. It applies the half-open `[start, end)` predicate after
the source relation is built and before column selection and `LIMIT`. The time
column must be exposed by a projected source, bounds must have matching date or
datetime kinds and matching timezone awareness, aware datetime bounds are
canonicalized to UTC, and transformed temporal
partitions are supported. Snapshot evidence format v4 includes the normalized
column and bounds in scope identity; persisted retained rows are dictionaries
keyed by the selected columns, and older v3 evidence is invalid and must be
reacquired.

Scope and explicit columns are required. `md.unpruned(max_rows=...,
timeout_seconds=...)` is the deliberate broad-read escape within acquisition.
Both guards are positive and enforceable; unsupported timeout blocks before
execution. `LIMIT` bounds returned rows, not bytes scanned, and a partition may
still be large.

Scope `show()` / `render()` cards expose only the scope kind, positive guards,
and a bounded predicate preview. Large partition predicates report omissions
and point to `.values` for the exact mapping; unpruned scope is labeled as a
broad read.

Read-only ClickHouse acquisition uses the server setting `readonly=1` together
with the bounded execution timeout. Connection, source-resolution, timeout, and
post-execution failures surface as `DatasourceAuthoringError` values with
`query_executed`, a sanitized backend summary, and one focused repair; they do
not escape as raw driver exceptions.

The snapshot card makes datasource/source/scope identity, selected columns,
coverage, and value/cache state explicit; use `snapshot.contract().show()` for
these query-free mechanical read facts. Values default to memory-only. A later
process can recover snapshot identity, profiles, and coverage but not retained
values unless the original sample used `persist_values=True` with explicit
acceptance of bounded plaintext project-local caching. When available, read a
retained row by column name, for example
`snapshot.retained_values[0]["order_id"]`. Uncommon formats, keys,
timezones, aggregation, units, additivity, relationship cardinality, null
semantics, and business meaning remain agent-owned. Marivo does not classify a
high null rate as a business-quality failure or filter nulls implicitly.

Bounded acquisition opens its isolated reader with the requested scope timeout
before entering the provider's authoring timeout guard. PostgreSQL configures
`statement_timeout` through connection options, and Trino configures
`query_max_run_time` through connection session properties. This uses the same
connection setup as terminal diagnostics; it does not add control SQL or alter
shared connections. A provider without an enforceable acquisition timeout
continues to reject sampling before business submission.

Reacquire evidence only when a required column or value was not captured, or
when datasource/source/scope identity no longer matches. Snapshot age alone is
not invalidation.

For a declared-only projected binding, a successful bounded sample proves that
the generated projection executed for the selected output aliases and scope. It
does not certify business meaning. During the current milestone, scoped
`catalog.preview(..., scope=...)` reads the current source directly. Ordinary
preview does not persist an authoring checkpoint or affect readiness.

### R0.3 target: Ibis-owned analysis reads and terminal raw SQL

R1.2 implementation remains under qualification. The selected engine provider
registry is lazy. `SourceSession` binds table and view relations on DuckDB,
SQLite, PostgreSQL, MySQL, Trino, and ClickHouse, and DuckDB CSV, Parquet,
local JSON, and uncredentialed HTTP JSON. It checks exact source identity,
runtime JSON parameters, relation ancestry, additional physical inputs, and
the pre-read Arrow schema. One active batch stream belongs to a session; the
submitted SQL is the unchanged Ibis compilation. Decode rejects lossy integer,
Boolean, float, Decimal, date, and timestamp values. PostgreSQL record fields
accept only canonical integer text where its driver returns record components
as text; Trino named rows retain their field names; MySQL temporal converters
preserve invalid date text for rejection rather than silently turning it into
null. A DuckDB result is connection-owned and is released with its connection;
remote cursor close does not prove server-side query termination.

SQLite native NUMERIC affinity is not an exact Decimal carrier. Declaring a
Decimal schema, including through `type_map`, does not restore precision already
lost to a native float. Such reads raise `DatasourceSourceCapabilityError`
without float-to-Decimal coercion. Use a qualified exact-Decimal datasource
instead; other backends retain their exact precision/scale requirements.

R3.4's private Analysis handoff explicitly declares join and union in addition
to scan, filter, project, group and count. The R1 basic physical requirement
admits join/union only on DuckDB; other providers retain their existing operation
set. This physical allowance does not grant method semantics or cross-datasource
federation. Exact method/type/table-form qualification and all bound key, Cell
and pairing checks remain owned by Analysis. Expressions still pass unchanged
through the same relation-ancestry, schema and submission checks.

`md.inspect` resolves a table through that owner before provider metadata
inspection. `SourceInspection.sample`, snapshots, Semantic preview, and
source-health business checks use bounded source-bound session reads. They
remain distinct evidence: metadata describes observed structure, a sample
describes only selected bounded rows, `md.test` and source-health connectivity
prove one compiled literal round trip, and explicit source-health checks report
only their requested business scope. Optional metadata failures may yield
schema-only or unavailable observations; a fact required for admission cannot
be inferred from a sample or silently supplied. Six backend metadata profiles
read the bound Ibis relation schema as the authoritative column baseline and
obtain optional catalog facts — comments, column nullability and ordinals,
view kind and definitions, primary keys and unique constraints where the
provider exposes them, partition topology, physical estimates, and ClickHouse
projectable columns — through the provider statement channel: a closed
registry of provider-owned fixed statements (`datasource.capabilities`, the
2026-09-28 user-approved internal-SQL exception recorded in the R0 SQL ledger
R1.6 overlay; statement text is pinned by a snapshot test and every submission
is audited on the backend). Every registered metadata statement has exactly the
owning `datasource.metadata.<backend>` purpose; scoped HTTP credential statements
have only `datasource.http_credentials`. Empty purpose sets grant no submission
authority. Cross-provider and cross-purpose requests reject before native SQL.
Each fact query that fails yields that fact's
unavailable warning while inspection still succeeds; a total failure yields
schema-only. Unknown view kind is `None`, not `False`. DuckDB catalog facts
are qualified by database, schema, and table. Composite unique constraints
retain their constraint identity and column order; SQLite partial and expression
indexes do not establish unconditional column uniqueness. A consumer requiring one
of those facts rejects the affected cell. Trino `$partitions` and ClickHouse
`system.tables` / `system.parts` partition-value reads continue through bound
Ibis expressions. Authenticated DuckDB HTTP sources install a scoped temporary
secret at connection time and send credentials only inside the declared
`http_scope`; see the credential section above for the declaration contract.

The user separately approved MySQL certified-authoring SELECT deadlines on
2026-10-05. Exactly two provider statements install and read back the
session-level max_execution_time value on an isolated certification connection.
Their purpose is closed to semantic.certified_preview.deadline and their integer
parameter is bounded to 1..4294967295 milliseconds. Denied or mismatched facts
reject before certification collection; the connection closes rather than
resetting a shared session. Native certified-source read failures surface as
structured SemanticRuntimeError with backend diagnostics, authored timeout and
actual source-submission facts. Integer DB-API error codes remain available as
sanitized diagnostic fields. No failed capture publishes or replaces a certified
snapshot; already typed datasource/semantic failures preserve their original
repair. On 2026-10-06 the user separately authorized owned-query KILL for
`sample` and `raw_sql` deadlines. Their isolated reader has its own bounded
control connection; `datasource.authoring.deadline` may cancel only that
reader's current native thread ID. The timer covers execution and fetch, joins
before cleanup, and closes both connections. The reader identity and duplicated
socket are captured before submission; cancellation does not call driver metadata
methods across threads while a native read is active. These purposes do not use or
extend the certification-only SET/read controls. Independent server termination
and absence of publication after failure remain separate acceptance proofs.

On 2026-10-06 the user approved a closed ClickHouse owned-query cancellation
operation. It binds the submitting SourceSession's issued query ID and
authenticated reader user in `KILL QUERY ... SYNC`, through a separate bounded
control connection with the same authentication. The reader requires
`SELECT(query, query_id, user) ON system.processes`; this column permission also
permits general query-metadata visibility. Operators configure it explicitly.
Marivo does not grant permissions or issue product `currentUser()` probes.
This permission and lifecycle boundary applies to the exact issued query and
authenticated reader. Runtime regression coverage includes pending, initial-
response and fetch phases, permission refusal, and a retained resource
obligation when control-close acknowledgement is unavailable; see
[Runtime test coverage](../../testing/runtime-coverage.md).

`md.raw_sql(datasource: Ref[DatasourceKind], sql: str, *, reason: str,
limit: int = 100, timeout_seconds: int = 30, include_types: bool = True,
project_root: Path | None = None) -> RawSqlResult` remains Marivo's managed
terminal SQL escape hatch for questions outside its governed Analysis capability.
The R1 public connection cutover removes backend-returning connection entry
points; `md.raw_sql` is the only public raw SQL terminal entry.
It submits SQL text verbatim with a required nonempty reason, positive
returned-row limit and enforceable timeout. The input is not parsed to classify
SQL as a diagnostic. Read-only protection relies on connection and backend permissions
and is best effort where the backend cannot guarantee it.
The result reports columns, types, bounded rows, truncation and execution
context; callers must inspect `is_truncated` before terminal computation.
Returned-row limits do not bound source scan cost. `RawSqlResult` is terminal:
its rows or isolated `to_pandas()` copy confer no Semantic identity, Metric
components, coverage, Artifact receipt or Analysis continuation. It cannot be
passed to `session.members`, `observe`, `execute`, or a typed source binding.
Raw-SQL result `show()` and `render()` use `n=None, max_output_bytes=8192`.
They fit complete returned rows and report display omissions independently of
query truncation; unlimited display never fetches more source rows. See the
[business data display contract](../agent-friendly-public-surface.md#business-data-display).

The `datasource.raw_sql` Help target and public export remain discoverable.

No other public source, inspection or Semantic expression argument accepts SQL
text, including `backend.sql(handwritten)` disguised as a table. Typed table
bindings still reject predicates, joins, casts and fragments. Governed business
reads, completeness checks, counts and type/time validation are constructed as
Ibis expressions. An adapter may submit the unmodified compiled result of a
bound Ibis expression through a native driver; it records the expression
identity, purpose and actual submission. The user-authored text submitted by
`md.raw_sql` is the explicit terminal exception, not an implementation route
for Analysis or a way to satisfy an unqualified method/backend cell. Driver settings and read-only controls use connection APIs; unavailable timeout
or required timezone facts block the affected execution cell. A required governed operation
without an Ibis or registered prepare-then-Python route is blocked. Local Store
SQLite transactions have separate internal persistence authority.

### R0.6 public connection cutover target

R1 removes the backend-returning `md.connect`, `DatasourceCatalog.connect`,
and public `DatasourceConnection` type and their Help targets. Connection
creation remains private to the datasource adapter. A caller checks
connectivity with `md.test`, inspects
physical facts with `md.inspect`, uses bound Ibis reads only through governed
operations, and submits custom SQL only through terminal `md.raw_sql`.
Existing calls that require a raw Ibis backend must change to one of those
purpose-specific paths; they do not receive a compatibility alias or a wrapper
that exposes `backend.sql`/`raw_sql`. R1 must migrate the current Help,
docstrings, latest English and Chinese site examples, and public surface tests
together. CLI doctor uses its bounded datasource test path; no public
connection object provides the same bypass.

## Handoff to semantics

Once a datasource is registered and validated, semantic authoring uses its ref
plus the physical facts needed for the current question. A snapshot is optional
bounded evidence and exposes generic profiles and retained values directly:

```python
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
inspection = md.inspect(warehouse, md.table("orders"))
snapshot = inspection.sample(
    scope=md.unpruned(max_rows=1000, timeout_seconds=30),
    columns=("order_id",),
)
snapshot.show()
order_id_profile = snapshot.profiles[0]
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
```
Physical facts remain datasource-owned; semantic refs remain semantic-owned.
After an entity is registered, semantic authoring reuses the entity ref rather
than re-supplying `(datasource, source)` tuples. The full write loop is defined
in [authoring-workflow.md](authoring-workflow.md).

For MySQL and SQLite bounded dataframe acquisition, nullable integer columns keep
exact integer cells before backend conversion; a NULL must not promote large
integers through float64. Typed inspection does not normalize PostgreSQL/MySQL/Trino
fixed CHAR to logical string, because its padding semantics differ. Bind a
variable-length text column instead; SQLite's qualified BINARY text convention
continues to admit CHAR declarations.
