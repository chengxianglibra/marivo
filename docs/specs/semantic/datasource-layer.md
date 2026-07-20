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
- `md.help("authoring")` — the runnable, always-current authoring checklist.

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
  projections. Those projections do not author objects, infer business meaning,
  or carry judgment targets.
- **Fail closed.** Missing env vars, unreachable backends, dialect/`backend_type`
  mismatch, and unsafe partition scans raise structured errors that state what
  was expected, what was received, and the concrete next step.

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
    catalog="hive",           # connection target, mapped to the ibis database
    schema="sales_mart",      # optional default schema
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
| `md.trino(...)` | `TrinoSpec` | `trino` | `catalog` is the connection target; `schema` is an optional default. |
| `md.mysql(...)` | `MySQLSpec` | `mysql` | `host` + `database` required. |
| `md.postgres(...)` | `PostgresSpec` | `postgres` | `host` + `database` required; optional `schema`. |
| `md.clickhouse(...)` | `ClickHouseSpec` | `clickhouse` | Session-id autogeneration defaults off for analysis stability. |

`DatasourceSpec` is the closed union of these five types. Concrete engine
connection builders live in `marivo/datasource/engines/` and are internal — the
public surface is the spec constructors and `Ref[datasource]`.

### Fields, names, and context

- **Literal fields** (`host`, `port`, `catalog`, `database`, `schema`, `path`, …)
  are stored verbatim in the project file and must be JSON-safe.
- **Env-ref fields** end in `_env` (`user_env`, `password_env`, `auth_env`,
  `host_env`, …) and store the *name* of an environment variable.
- **`ai_context=`** accepts an `ms.ai_context(...)` value (never a raw dict) so a
  datasource can carry business annotations; text belongs in
  `business_definition`. There is no `md.ai_context` constructor — the semantic
  module owns that value type (see `md.help("ai_context")`).
- **`name`** is the global datasource key. Datasource names are never
  `<domain>.<datasource>`; a datasource does not belong to a semantic domain.

### Datasource references

Semantic declarations reference a datasource by one exact ref:

```python
warehouse = ms.Ref.datasource("warehouse")   # -> Ref[datasource]
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
```

`ms.Ref.datasource(...)` accepts only the one-segment datasource path. Bare
strings and kind-qualified strings such as `"datasource.warehouse"` are
rejected — the exact ref is the contract. Renaming a legacy datasource changes
its semantic identity, but cached credentials are keyed by the resolved
environment-variable name. A rename such as `prod-mysql` to `prod_mysql`
therefore reuses a conventional cached credential because both select
`MARIVO_PROD_MYSQL_<FIELD>`; a rename whose conventional environment-variable
name differs leaves the old cache entry untouched and requires exporting or
caching the new name. Marivo never migrates or deletes the old cache entry.

## Credentials and secret persistence

Secrets never live in project state. Instead a spec records env-var names, and
Marivo resolves them at connect time through a provider chain:

```text
EnvProvider (os.environ)  →  LocalPlaintextCache (~/.marivo/secrets.toml)
```

- **Resolution.** Each `*_env` name is resolved against the chain. A name that is
  set in neither environment nor cache raises `DatasourceEnvVarMissingError`
  naming the datasource, field, and env var.
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
- **Convention.** The conventional env var for a datasource secret field is
  `MARIVO_<DATASOURCE>_<FIELD>` (e.g. `MARIVO_WAREHOUSE_PASSWORD`).

The persistence boundary is a hard rule: resolved secret values may be cached in
plaintext **user-global** state, but must never be written into **project-local**
`models/datasources/` files, which only carry `*_env` names.

## Physical sources

A source descriptor names *what to read* inside a datasource. It is not a
datasource declaration; it is paired with a `Ref[datasource]` in inspection,
discovery, and `ms.entity(source=...)`.

| Constructor | IR | Meaning |
|---|---|---|
| `md.table(name, database=...)` | `TableSourceIR` | An internal table or view inside the datasource (any backend). |
| `md.parquet(path, hive_partitioning=...)` | `ParquetSourceIR` | A self-describing DuckDB file source over Parquet. |
| `md.csv(path, schema=..., header=..., delimiter=...)` | `CsvSourceIR` | A DuckDB CSV file source with required typed physical schema. |
| `md.json(path, schema=..., format=...)` | `JsonSourceIR` | A DuckDB JSON file source with required typed physical schema. |

`TableSource` is the public union of these four IRs. File sources
(`parquet`/`csv`/`json`) are read by the DuckDB engine, so they attach to a
DuckDB datasource ref; `md.table(...)` works against any backend. CSV and JSON
must carry a non-empty backend-independent typed `schema=` mapping so metadata
inspection never opens user data merely to infer types. Their paths may be local
files, globs, or DuckDB-supported `http(s)://` URLs; JSON also declares its
physical `format=`.

## Registration and state storage

```python
spec = md.duckdb(name="warehouse", path="/data/warehouse.duckdb")
md.register(spec)                 # writes models/datasources/warehouse.py
md.test(spec.ref).show()          # validated live round trip
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

## Inspection and evidence snapshots

`md.inspect(datasource, source)` is metadata-only. It exposes schema, physical
extent, partition state, and enforceable execution capabilities before a user-data
read. `inspection.partitions()` is also metadata-only.

CSV and JSON descriptors require typed `schema=` mappings so inspection never
opens data merely to infer types. Tables use catalog schema and Parquet uses
footer schema.

```python
inspection = md.inspect(warehouse, md.table("orders"))
inspection.show()
inspection.partitions().show()

scope = md.partition(
    {"dt": "20260710"}, max_rows=1000, timeout_seconds=30
)
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

Scope and explicit columns are required. `md.unpruned(max_rows=...,
timeout_seconds=...)` is the deliberate broad-read escape within acquisition.
Both guards are positive and enforceable; unsupported timeout blocks before
execution. `LIMIT` bounds returned rows, not bytes scanned, and a partition may
still be large.

Snapshot projections are local, column-independent views and issue no query.
Values default to memory-only. `persist_values=True` stores only bounded value
evidence in plaintext project-local cache and therefore requires an explicit
privacy judgment. Uncommon formats, keys, timezones, aggregation, units,
additivity, relationship cardinality, and business meaning remain agent-owned.

### Raw SQL terminal exit

```python
md.raw_sql(warehouse, "SHOW PARTITIONS orders", reason="verify pruning").show()
```

`md.raw_sql(...)` is the sole terminal raw SQL execution path — bounded by
`timeout_seconds` (default 30), exact row limiting, and read-only enforcement.
It returns a `RawSqlResult` that cannot re-enter typed analysis; use
`RawSqlResult.to_pandas()` for the terminal pandas exit. It is for custom
analysis that `session.observe(...)` cannot express, not a general query path.

## Handoff to semantics

Once a datasource is registered and validated, everything the semantic layer
needs is the ref plus the evidence:

```python
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.Ref.datasource("warehouse")
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
