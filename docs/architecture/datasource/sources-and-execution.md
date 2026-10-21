# Physical Sources and Execution

Physical descriptors identify data within a [datasource](README.md). They do not
assert Entity identity, business units, allocation or Metric semantics.

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
construction, `ms.entity`, `ms.load()`, and `frame.show()` remain source-I/O
free. Database metadata is read only when execution needs types, and only for the
required dependency closure. CSV and JSON types are inferred during their actual
read; remote JSON is not fetched only to inspect its schema.

Construct source descriptors through these helpers and `md.source_param(...)`.
Concrete source record types retain type and field Help, but are not root-module
constructor exports. Inspection metadata records are acquired through
`inspection.physical_extent`, `inspection.partitioning`,
`inspection.execution_capabilities` and `inspection.partitions().partitioning`.
Connectivity failures are read through `md.test(...).failure`. Their fields and
concrete value types remain part of the returned-data contract; use
`marivo.help(type(value))` for the owning type and member routes.

### Source projections and observed types

Supplying `columns=` to `md.table(...)` selects and renames physical columns. Its
type is `Mapping[str, str]`: output name to physical column name. It does not
declare types or accept column descriptor objects. Physical identifiers are quoted atomically, so
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

Governed `SourceSession` reads, including `collect_bounded`, run through the
selected Ibis backend and return a pandas DataFrame. Value conversion follows
Ibis/provider behavior and may be
lossy; for example, a provider may turn an invalid date into a null value.
DuckDB `SourceSession` reads and qualified typed PostgreSQL Analysis reads consume
native Ibis Arrow results before pandas inference and map integer columns directly
to pandas Arrow extension dtypes.
Integer values that fit the native Arrow result type remain exact alongside
nulls without a float64 intermediate. Noninteger values retain native Ibis Arrow
type, representability and precision limits; their pandas representations need
not match the backend's `execute()` results. Ordinary PostgreSQL authoring, basic
reads and bounded inspection (`collect_bounded`) retain native Ibis pandas
conversion, including JSON object/array values and its existing precision and
representation limits. Other backends retain their native Ibis pandas path.
Marivo does not add a per-value Arrow round-trip audit. Source schema and
projection admission, requested row/time bounds, and downstream key, nullable-value,
time, and persistence-schema checks remain in force.

Analysis binds numeric output types before execution and writes required Decimal
result casts into Ibis expressions; the existing pandas/stage boundary consumes
their actual native results. Business-required
conversions and complete key, null, unit and time checks remain explicit.
Datasource access alone grants no operation-specific Analysis qualification.

Missing selected optional drivers raise `DatasourceConnectionError` with the
backend-specific `marivo[backend]` installation repair. Unselected drivers are
not imported by provider discovery.

Native temporal analysis admits PostgreSQL `timestamptz`, MySQL `TIMESTAMP`, and
ClickHouse `DateTime64` through microseconds. MySQL `TIMESTAMP` and ClickHouse
timestamp execution retain verified UTC session/reader requirements; source and
report timezones remain distinct. Reader and report timezone resolution accept
IANA names and explicit offsets. An absent probe capability permits recorded
system fallback; an actual failed or invalid probe does not. Physical instants
and explicit parser authority skip unnecessary reader probes. Native parser
declarations retain their existing IANA validation. Ibis/provider conversion
owns representation of returned values; Marivo does not require lossless
round-tripping through a custom source decoder.


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
stays in `md.json(...)`; explicit reads supply declared non-secret parameter
values without changing that declaration.

For example, one change-focus page can declare its app and page number as
request-scoped values without turning pagination into datasource behavior:

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

Explicit datasource discovery supplies parameters to the bounded read:

```python
inspection = md.inspect(warehouse, samples)
snapshot = inspection.sample(
    scope=md.unpruned(max_rows=1000, timeout_seconds=30),
    columns=("metric", "value", "values"),
    source_params={"start": "now-3600", "end": "now"},
)
snapshot.show()
```
`source_params` must provide exactly the declared parameter names. Each value is
a scalar or flat, non-empty scalar list; the normalized binding enters snapshot
identity. A discovery snapshot is bounded physical evidence, not a typed
Analysis input.

Datasource sampling and Semantic preview accept explicit JSON parameter bindings.
The public Analysis Session has no source_bindings context. Its source route
admits local unparameterized GET JSON; remote HTTP, POST and parameterized JSON
remain outside that route. A successful datasource read does not expand it.

### Ibis-owned analysis reads and terminal raw SQL

Provider operation support and exact Analysis method qualification are separate.
The selected engine provider registry is lazy. `SourceSession` binds table and
view relations on DuckDB, PostgreSQL, MySQL, Trino, and ClickHouse, and DuckDB
CSV, Parquet, local JSON, and uncredentialed HTTP JSON. It checks exact source
identity, runtime JSON parameters, relation ancestry, additional physical
inputs, and the bound relation schema before execution. DuckDB reads and qualified
typed PostgreSQL Analysis `query.source_read` v1 reads call the native Ibis
backend's `to_pyarrow()` and convert its result to pandas with the integer-only
dtype mapping above. The PostgreSQL route is selected from the actual
`PhysicalRequirement` captured in the private issued-read proof at compilation;
diagnostic purpose strings do not select transport. Ordinary PostgreSQL
authoring/basic/`collect_bounded` reads and other providers call native `execute()`
and return its pandas DataFrame. Ibis and the provider own driver
fetching and native value conversion. There is no Marivo-owned
row cursor/batch protocol or strict source Arrow round-trip decoder. Column
order, source binding, requested row/time bounds, and downstream key, nullable-value,
time, and persisted-schema checks remain governed. A completed DataFrame read
does not prove server-side query termination after an interruption or timeout.
Connection probes and owned source sessions share the backend's single internal
lease, which disconnects through Ibis at most once. Borrowed source sessions
leave disconnection to the connection owner. Cleanup continues across resource
failures and records disconnection only after confirmed release; it does not
actively cancel remote queries.

The Analysis handoff explicitly declares join and union in addition
to scan, filter, project, group and count. The basic physical requirement
admits join/union only on DuckDB; other providers retain their existing operation
set. This physical allowance does not grant method semantics or cross-datasource
federation. Exact method/type/table-form qualification and all bound key, nullable-value
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
be inferred from a sample or silently supplied. Five backend metadata profiles
read the bound Ibis relation schema as the authoritative column baseline and
obtain optional catalog facts — comments, column nullability and ordinals,
view kind and definitions, primary keys and unique constraints where the
provider exposes them, partition topology, physical estimates, and ClickHouse
projectable columns — through the provider statement channel: a closed
registry of provider-owned fixed statements (statement text is pinned by snapshot tests and submissions are audited on the
backend). Every registered metadata statement has exactly the
owning `datasource.metadata.<backend>` purpose; scoped HTTP credential statements
have only `datasource.http_credentials`. Empty purpose sets grant no submission
authority. Cross-provider and cross-purpose requests reject before native SQL.
Each fact query that fails yields that fact's
unavailable warning while inspection still succeeds; a total failure yields
schema-only. MySQL invocation-local bulk key requests additionally read the
active session's `max_allowed_packet` through this fixed metadata channel.
ClickHouse compiled reads check `max_query_size` using the client's effective
override or its existing server-settings cache, including the native result-format
suffix in the UTF-8 byte count. This adds no metadata query. Unknown capacity
retains native enforcement; a zero value disables only this parser-size limit.
Their JSON storage and final compiled SQL bytes are checked against that
capacity before the target business read; SQL is encoded with the native
connection's encoding. This does not change the server setting, split completed
aggregates, or replace Ibis execution. Unknown view kind is `None`, not `False`. DuckDB catalog facts
are qualified by database, schema, and table. Composite unique constraints
retain their constraint identity and column order. A consumer requiring one
of those facts rejects the affected cell. Trino `$partitions` and ClickHouse
`system.tables` / `system.parts` partition-value reads continue through bound
Ibis expressions. Authenticated DuckDB HTTP sources install a scoped temporary
secret at connection time and send credentials only inside the declared
`http_scope`; see [connection credentials](README.md#credentials-and-secret-persistence) for the declaration contract.

MySQL isolated authoring readers install and read back the native session
`max_execution_time` through two provider-owned statements. Their purpose is
`datasource.native_query_timeout` and the positive integer setting fits the
server's millisecond range. Ordinary preview, sample, raw SQL and certification
share that configuration. A server that lacks the variable skips the setting;
denied or mismatched configuration fails before business reads. The setting
covers only eligible read-only SELECT statements. Marivo does not create a
control connection, send KILL, duplicate/shut down sockets or run a timer.

ClickHouse readers use native query settings with throwing overflow so timed-out
partial data cannot become a successful typed result. Marivo neither requires
query-cancellation permissions on system.processes nor creates a control client.
Errors retain diagnostics and actual submission facts. Cleanup releases owned
source connections; connection closure alone never proves remote query
termination. Failed reads do not publish or replace a certified snapshot.

`md.raw_sql(datasource: Ref[DatasourceKind], sql: str, *, reason: str,
timeout_seconds: int = 30, include_types: bool = True,
project_root: Path | None = None) -> RawSqlResult` remains Marivo's managed
terminal SQL escape hatch for questions outside its governed Analysis capability.
`md.raw_sql` is the sole public raw SQL terminal entry.
It submits SQL text verbatim with a required nonempty reason, positive
requested native timeout; unsupported query timeouts are skipped. The input is not parsed to classify
SQL as a diagnostic. Read-only protection relies on connection and backend permissions
and is best effort where the backend cannot guarantee it.
The result retains all rows returned by the query, plus columns, types and
execution context. Control query size in SQL with predicates, aggregation and
an explicit LIMIT. Complete query rows are loaded into client memory; a LIMIT
does not necessarily bound source scan cost. `RawSqlResult` is terminal:
its rows or isolated `to_pandas()` copy confer no Semantic identity, Metric
components, coverage, Artifact receipt or Analysis continuation. It cannot be
passed to `session.members`, `observe`, or a typed source binding.
Raw-SQL result `show()` and `render()` use `n=None, max_output_bytes=8192`.
They fit complete returned rows and report display omissions independently of
query size; unlimited display never fetches more source rows. See the
[business data display contract](../agent-friendly-public-surface.md#data-display-contracts).

The `datasource.raw_sql` Help target and public export remain discoverable.

No other public source, inspection or Semantic expression argument accepts SQL
text, including `backend.sql(handwritten)` disguised as a table. Typed table
bindings still reject predicates, joins, casts and fragments. Governed business
reads, completeness checks, counts and type/time validation are constructed as
Ibis expressions and executed through the selected Ibis backend. The execution
record retains the compiled SQL, expression identity, purpose and submission
outcome. The user-authored text submitted by
`md.raw_sql` is the explicit terminal exception, not an implementation route
for Analysis or a way to satisfy an unqualified method/backend cell. Driver settings and read-only controls use connection APIs; required timezone or complete-result facts block the affected execution cell. A required governed operation
without an Ibis or registered prepare-then-Python route is blocked. Local Store
SQLite transactions have separate internal persistence authority. SQLite is not
a supported datasource; the local Store remains independent.

### Public connection boundary

Connection creation and backend objects remain private to the datasource adapter. A caller checks
connectivity with `md.test`, inspects
physical facts with `md.inspect`, uses bound Ibis reads only through governed
operations, and submits custom SQL only through terminal `md.raw_sql`.
CLI doctor uses its bounded datasource test
path; no public
connection object provides the same bypass.

## Execution logging side effects

Actual governed reads, connectivity probes, registered provider statements and
`md.raw_sql()` write always-on diagnostics to project-local `.marivo/logs/`.
Submission records contain verbatim compiled SQL; completion records describe
the execution outcome and returned row count. Parameterized credential statements
record their fixed template only and never record bound values or sensitive
failure messages. Logging is independent of the usage telemetry switch and
cannot change query results, typed failure propagation or connection cleanup.
See [Session and Runtime](../analysis/session-state-and-runtime.md) for correlation and rolling.
`marivo doctor` without `--connect` remains read-only and produces no execution
logs; explicitly requested connection probes log their SQL without caching secrets.


## Implementation map

Physical descriptors live in [source.py](../../../marivo/datasource/source.py),
[table_source.py](../../../marivo/datasource/table_source.py) and
[json_source.py](../../../marivo/datasource/json_source.py).
[runtime.py](../../../marivo/datasource/runtime.py) owns governed source sessions;
[adapters.py](../../../marivo/datasource/adapters.py) and the
[engine adapters](../../../marivo/datasource/engines/) own provider submission and transport.

### Typed analysis numeric results

Typed analysis retains this reader and supplies explicit Ibis result casts for
bound Decimal arithmetic, Decimal mean and SUM. Integer SUM accumulates once in
Decimal and returns Decimal(38,0); Decimal SUM fixes Decimal(38,input_scale).
Ordinary source integer/Decimal overflow follows the actual provider, including
ClickHouse silent wrapping and native exceptions. A wrapped value that fits the
output carrier can be committed and influence later formulas/selection; safe
conversion does not reconstruct its mathematical value. No independent widened
verification or check-only SUM runs. Unevaluated source arithmetic may not fail.
Explicit finite/null/zero/domain checks and exact identity/count/time/Duration
contracts retain their scope. See [numeric carriers](../analysis/operators-and-frames.md).
