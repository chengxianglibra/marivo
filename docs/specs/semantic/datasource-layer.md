# Datasource Layer Design

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
  facts before data access; one explicitly scoped sample feeds local evidence
  projections. Those projections do not author objects or infer business
  meaning. They may expose stable, structured judgment requirements whose
  authority remains the user or accountable business owner.
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

`DatasourceSpec` is the closed union of these six types. Concrete engine
connection builders live in `marivo/datasource/engines/` and are internal — the
public surface is the spec constructors and `Ref[datasource]`.

Every spec has a bounded, decision-first `show()` / `render()` card. It exposes
the declared state, exact ref, core connection target, credential field names,
and only a count for additional configuration. It never expands resolved
secrets, session/settings maps, `extra`, or AI context. Read `.fields` and
`.env_refs` only when exact configuration is needed, and use `.contract()` for
the mechanical registration transition. The default repr is a one-line pointer
to this card rather than a dataclass field dump.

SQLite uses `md.table(...)` for tables and views. It does not consume the
DuckDB-owned Parquet, CSV, or JSON descriptors. `read_only=True` enables
connection-level `PRAGMA query_only`; Marivo's bounded inspection and diagnostic
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
- **Post-validation caching.** After a *validated* round trip — `md.test(ref)`
  (or a successful `md.connect`) — env-sourced secrets are cached in plaintext at
  user-global `~/.marivo/secrets.toml` so later sessions can connect without the
  env var re-exported. This is a deliberate, documented convenience, gated by the
  guards below.
- **Guards.** Persistence is disabled when `MARIVO_PERSIST_SECRETS=0` or `CI` is
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
headers in memory for POST execution. Resolved values are never serialized into
`md.json(...)` or project metadata.

## Physical sources

A source descriptor names *what to read* inside a datasource. It is not a
datasource declaration; it is paired with a `Ref[datasource]` in inspection,
discovery, and `ms.entity(source=...)`.

| Constructor | IR | Meaning |
|---|---|---|
| `md.table(name, database=..., columns=...)` | `TableSourceIR` | A catalog-backed table/view or a complete typed projection over one physical table (any SQL backend). |
| `md.parquet(path, hive_partitioning=...)` | `ParquetSourceIR` | A self-describing DuckDB file source over Parquet. |
| `md.csv(path, schema=..., header=..., delimiter=...)` | `CsvSourceIR` | A DuckDB CSV file source with required typed physical schema. |
| `md.json(path, schema=..., format=..., records_path=..., field_paths=..., query_params=..., method=..., body=...)` | `JsonSourceIR` | A DuckDB JSON source with stable typed output aliases, optional correlated nested-field extraction, and runtime-bindable query-string or POST-body values. |

`TableSource` is the public union of these four IRs. File sources
(`parquet`/`csv`/`json`) are read by the DuckDB engine, so they attach to a
DuckDB datasource ref; `md.table(...)` works against any backend. CSV and JSON
must carry a non-empty backend-independent typed `schema=` mapping so metadata
inspection never opens user data merely to infer types. Parquet and CSV paths may
be local files or globs. JSON additionally supports HTTP(S) GET and JSON-object
POST requests while retaining the declared physical `format=` and schema.

### Typed table column bindings

`md.table(...)` has two closed modes. Omitting `columns=` keeps the catalog-backed
path and resolves the complete table through Ibis. Supplying `columns=` declares a
complete typed interface over the same physical table. Every mapping key is the
stable output alias used by semantic objects; every value is one
`md.source_column(physical_name, data_type=...)` binding. Physical identifiers are
quoted atomically, so dots, spaces, reserved words, and punctuation never become
qualification or authored SQL:

```python
events_source = md.table(
    "raw.events",
    database="warehouse",
    columns={
        "event_time": md.source_column("event.timestamp", data_type="timestamp"),
        "score": md.source_column("_generated_score", data_type="float64"),
    },
)
```

The declared type is the output Ibis schema assertion; it does not cast the
physical value. Projected mode is a complete allowlist: catalog inference cannot
fill omitted columns, and duplicate physical identifiers are rejected. The
datasource adapter generates one identifier-only inner `SELECT` without a table
alias and supplies the declared schema to the backend. The binding mapping remains
part of source, snapshot, semantic dependency, cache, and lineage identity.

For ClickHouse tables, inspection also reads active `system.parts_columns` and
exposes safe adapter-only physical columns through
`SourceInspection.projectable_columns`. Each row carries the exact physical
name accepted by `md.source_column(...)`, its normalized Ibis type, and
nullability. Columns with conflicting part types or unparseable backend types
are warned about and omitted. This is physical-column discovery, not Map key
enumeration: dynamic keys that have not been materialized remain outside the
governed source contract and require upstream materialization, a database view,
or terminal `md.raw_sql(...)`.

For a wrapped response, `records_path=` selects the array whose declared fields
are projected into the output schema. Additional object fields are ignored and
missing declared fields become typed nulls; present values must be convertible to
their declared types. The initial contract is intentionally limited to `$` plus
object-member access, such as `$.data` or `$.result.items`; filters, wildcards,
recursive descent, and array indexing are not supported. A present, empty array
materializes as zero rows. A missing path or a non-array value fails at execution
instead of being treated as an empty result; verify the response envelope and API
authentication before retrying.

`schema=` maps stable output aliases to Ibis type strings. For a field nested
inside each selected record, `field_paths=` maps that output alias to a relative
JSON path: `a.b` selects an object member, `a[0].b` selects a fixed array index,
and `a[].b` traverses an array. Traversed sibling fields must share one array
prefix and are projected from the same element, so their values remain
correlated. Independent traversal roots and more than one traversal in a path
fail at declaration instead of creating a Cartesian product. A record whose
traversed array is missing, empty, or null produces no rows. Literal top-level
field names remain schema keys and may contain punctuation or spaces.

```python
changes = md.json(
    "http://change-focus.example/api/v2/change/list",
    schema={"change_id": "int64", "app_id": "int64", "app_name": "string"},
    records_path="$.data.change_infos",
    field_paths={
        "app_id": "specificsource[].appid",
        "app_name": "specificsource[].name",
    },
)
```

Parameterized API URLs keep their stable request shape in the semantic project
and bind request-specific values at analysis time:

```python
samples = md.json(
    "http://hawkeye.example/report/api/v2/query_range/datasource/81",
    schema={"metric": "json", "value": "json", "values": "json"},
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
    schema={
        "name": "string",
        "bs": "string",
        "gpuAbstract": "string",
        "status": "string",
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
    schema={"change_id": "int64", "title": "string"},
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
    frame = session.observe(ms.ref.metric("monitoring.pending_containers"))
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
- `md.connect(name)` opens a live `DatasourceConnection`; `md.test(ref)` returns
  a `DatasourceTestResult` and triggers post-validation secret caching.

`DatasourceTestResult.show()` is the authoritative connection-test stop point.
On failure, `.failure` carries a bounded `DatasourceFailure` with a stable stage
code (`connection_open_failed`, `connection_roundtrip_failed`, or
`secret_persistence_failed`), backend exception type/code/name, and a sanitized
message. `.repair` provides the focused help target and action, while
`.contract()` exposes the blocked `validate_connection` transition. Successful
results prove only `datasource.connection_validated`.

## Inspection and evidence snapshots

`md.inspect(datasource, source)` is metadata-only. It exposes schema, physical
extent, partition state, and enforceable execution capabilities before a user-data
read. Its card includes the exact source descriptor and complete real schema
column names and types. `inspection.partitions()` is also metadata-only; its
card identifies the value source, completeness and truncation, shows bounded
captured values, and derives a copyable `md.partition(...)` scope template from
those already-captured values without another query. A single transformed
temporal partition instead produces a copyable `md.time_range(...)` template.

Physical extent always carries provenance and scope. For a ClickHouse
`Distributed` source, Marivo may inspect the resolved local table through
`system.parts`, but the source-wide row count and size remain `unknown`; the
bounded local observation appears only in `physical extent notes` with
`scope=local_node_only`. Marivo does not issue a cluster-wide fanout query.

CSV and JSON descriptors require typed `schema=` mappings so inspection never
opens data merely to infer types. Ordinary tables use catalog schema and Parquet
uses footer schema. A projected table first compares its declared bindings with
available base-table metadata, then exposes exactly the stable output aliases.
Catalog-visible bindings must have the declared canonical type. A missing physical
identifier becomes a `declared_column_unverified` warning with unknown nullability;
it is not treated as proof that the identifier exists. If base metadata is
classified unavailable, inspection remains metadata-only, returns the complete
declared interface with unknown extent/partition/constraints, and requires an
explicit bounded `md.unpruned(...)` acquisition. Authentication, connection,
configuration, timeout, and unclassified metadata failures still fail closed.

```python
inspection = md.inspect(warehouse, md.table("orders"))
inspection.show()
inspection.partitions().show()

scope = md.partition({"dt": "20260710"}, max_rows=1000, timeout_seconds=30)
snapshot = inspection.sample(
    scope=scope,
    columns=("order_id", "status", "dt", "amount"),
)

snapshot.entity(columns=("order_id",)).show()
snapshot.dimensions(columns=("status",)).show()
snapshot.values("status", limit=10).show()
snapshot.time_dimensions(columns=("dt",)).show()
snapshot.measures(columns=("amount",)).show()
```

For date or timestamp acquisition, use the same public `PartitionScope` through
`md.time_range("created_at", start=..., end=..., max_rows=...,
timeout_seconds=...)`. It applies the half-open `[start, end)` predicate after
the source relation is built and before column selection and `LIMIT`. The time
column must be exposed by a projected source, bounds must have matching date or
datetime kinds and matching timezone awareness, aware datetime bounds are
canonicalized to UTC, and transformed temporal
partitions are supported. Snapshot evidence format v3 includes the normalized
column and bounds in scope identity; older v2 evidence is invalid and must be
reacquired.

Scope and explicit columns are required. `md.unpruned(max_rows=...,
timeout_seconds=...)` is the deliberate broad-read escape within acquisition.
Both guards are positive and enforceable; unsupported timeout blocks before
execution. `LIMIT` bounds returned rows, not bytes scanned, and a partition may
still be large.

Scope `show()` / `render()` cards expose only the scope kind, positive guards,
and a bounded predicate preview. Large partition predicates report omissions
and point to `.values` for the exact mapping; unpruned scope is labeled as a
broad read. `.contract()` remains the complete mechanical continuation surface.

Read-only ClickHouse acquisition uses the server setting `readonly=1` together
with the bounded execution timeout. Connection, source-resolution, timeout, and
post-execution failures surface as `DatasourceAuthoringError` values with
`query_executed`, a sanitized backend summary, and one focused repair; they do
not escape as raw driver exceptions.

The snapshot card makes the datasource/source/scope identity, selected columns,
coverage, and value/cache state explicit. Snapshot projections are local,
column-independent views and issue no query; their cards and contracts mark
`data_access=none`.
Values default to memory-only. `persist_values=True` stores only bounded value
evidence in plaintext project-local cache and therefore requires an explicit
privacy judgment. Uncommon formats, keys, timezones, aggregation, units,
additivity, relationship cardinality, and business meaning remain agent-owned.

Evidence projections expose those unresolved decisions through frozen
`AuthoringJudgmentRequirement` values. The shared shape contains `id`,
`subjects`, `evidence_ids`, and
`authority="user_or_business_owner"`. Mechanical `states` and `transitions`
remain unchanged: a judgment requirement is neither a constructor action nor an
approval record.

Entity, dimension, measure, and values evidence derives `null_rate` from the
captured row/null counts. If dimension values contain `NULL`, the existing
evidence judgment includes `null_semantics` and asks the author to explain the
business meaning in `ai_context.guardrails`. Marivo does not classify a high
null rate as a data-quality failure and never filters nulls implicitly.

Reacquire evidence only when a required column or required value evidence was
not captured, the snapshot is stale for the current decision, or its
datasource/source/scope identity does not match. Asking the same snapshot for
entity, dimension, time, measure, relationship, or bounded-value projections is
not a reacquisition reason and never causes data access.

For a declared-only projected binding, a successful bounded sample proves that
the generated projection executed for the selected output aliases and scope. It
does not certify business meaning or make static verification a runtime existence
check. Authoring continues through snapshot projection, `catalog.preview(...,
using=snapshot)`, and query-free `catalog.readiness(...)` over that exact source
identity.

### Raw SQL terminal exit

```python
md.raw_sql(warehouse, "SHOW PARTITIONS orders", reason="verify pruning").show()
```

`md.raw_sql(...)` is the sole terminal raw SQL execution path — bounded by
`timeout_seconds` (default 30), exact row limiting, and read-only enforcement.
It returns a `RawSqlResult` that cannot re-enter typed analysis; use
`RawSqlResult.to_pandas()` for the terminal pandas exit. It is for custom
analysis that `session.observe(...)` cannot express, not a general query path.

Marivo therefore has three distinct SQL categories: SQL compiled by Ibis from
typed expressions; datasource-adapter SQL generated only from validated source IR
and quoted identifiers; and user-authored SQL accepted by terminal
`md.raw_sql(...)`. Only the last category is authored SQL text. Typed table
bindings never accept expressions, predicates, joins, casts, or SQL fragments.

## Handoff to semantics

Once a datasource is registered and validated, everything the semantic layer
needs is the ref plus the evidence:

```python
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
inspection = md.inspect(warehouse, md.table("orders"))
snapshot = inspection.sample(
    scope=md.unpruned(max_rows=1000, timeout_seconds=30),
    columns=("order_id",),
)
snapshot.entity(columns=("order_id",)).show()
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
```

Physical facts remain datasource-owned; semantic refs remain semantic-owned.
After an entity is registered, semantic authoring reuses the entity ref rather
than re-supplying `(datasource, source)` tuples. The full write loop is defined
in [authoring-workflow.md](authoring-workflow.md).
