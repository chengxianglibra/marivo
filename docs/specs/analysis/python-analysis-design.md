# Python Analysis Design

## Unified operator and execution ownership

All backends use one operator contract and implementation-registration mechanism.
The selected backend owns physical preparation, execution,
retained import and resource lifetime. DuckDB's temporary objects, macros and
registration hooks implement the same semantic requirements; they are not
operator-level exceptions. Remote implementations must work with read-only
accounts and prove equivalent single evaluation, numerical behavior and required
assertions before registration. No implicit alternate route or new cross-engine
private-state transfer is introduced. DuckDB analysis and the PostgreSQL, MySQL, SQLite, Trino and ClickHouse scalar subsets and the individually qualified relational/date methods below are enabled.

### Required source columns

All six backends derive required columns from the complete logical dependency
closure, preserving each Entity, exact source binding and physical relation.
Unused declared columns and unrelated physical columns do not participate in
execution type admission, schema validation or source projection. Hidden Metrics,
identity and relationship keys, version axes, predicates and retained components
remain required even when the displayed result is empty or projected.
Missing required columns, unsupported physical types and type mismatches identify
the Entity, relation, logical/physical column and expected/actual type. This does
not relax semantic loading or enable additional backend types or methods.

Column comments remain datasource inspection evidence. Explicit inspection retains
comments for unused columns and maps physical comments to declared aliases;
execution does not fetch comments or infer business semantics from them.

### Discovering execution boundaries

`marivo.help("analysis.actions.execute")` owns the bounded execution guidance.
Datasource connectivity and semantic readiness do not establish support for a
particular method or input shape. Read the logical Dataset contract and the
structured rejection for the selected invocation. Remote sources require read-only
accounts; retained import and uploads remain unsupported. Source relations are not
restricted by table form: views and every engine or connector type enter, and Trino
rejects only `$`-suffixed internal tables. Installed
package acceptance is recorded separately from source-tree Runtime evidence.

### Relational and native-date methods

PostgreSQL, MySQL, SQLite, Trino and ClickHouse use exact per-backend admission
for direct-column mean, weighted mean and ratio in addition to Group A. The
complete dependency graph is checked, including predicates and projected-away
components. Same-source relationship paths retain identity, missing-coordinate
and fanout assertions. Each participating relation must meet its backend's existing
physical type restrictions.

Native civil-date axes support single-unit day, week, month, quarter and year
buckets. Snapshot and validity membership retain required exact-period,
non-overlap and selected-identity assertions. All five remote backends admit
both authored validity interval closures and configured open-end sentinels.
Version diagnostics still never gate execution or domain equality.

Mean, weighted mean and ratio preserve sufficient components through atomic
primary/parts publication and immutable retained rollup. Composed Decimal
results decide per published unit through the semantic layer's derived
precision facts instead of one blanket rejection: a decimal linear unit
publishes exact dec(38, s) sum-leaf add/sub results where the resolved unit is
admitted, while mean/div units stay backend-rejected — the live MySQL probe
measured engine AVG rounding at ROUND_HALF_UP (a 5-tie 0.3128125 returned
0.312813, so the declared HALF_EVEN quantization cannot be bit-exact),
PostgreSQL and Trino numeric/AVG scales are not a public contract, ClickHouse
decimal division truncates scale and AVG returns Float64, and DuckDB decimal
division and AVG return DOUBLE. A Decimal input with an already resolved
floating result — ratio and weighted mean over Decimal components — remains
the existing float64 contract and is not part of this per-unit resolution.

Status-time folds were qualified per backend by C6 on live probe evidence:
PostgreSQL (ARRAY_AGG argmin/argmax), SQLite (JSON_EXTRACT over MIN/MAX text
argmax), Trino (MIN_BY/MAX_BY) and ClickHouse (native argMin/argMax) admit
first, last, mean, min and max folds; MySQL admits mean, min and max, while
its first and last folds stay rejected because ibis has no ArgMin/ArgMax
compile rule for MySQL. Percentile/quantile folds remain rejected on every
backend. Admission is probe-then-open per backend and fold kind: the
status-time component and the node-level fold override consult the same
per-backend qualified kind set.

Comparison and attribution use complete non-Entity axis state, including
hidden-axis expansion on PostgreSQL, SQLite, Trino and ClickHouse for
additive difference and component mix. SQLite encodes source masks as
fixed-width bits and strictly restores boolean arrays before publication;
PostgreSQL lowers null-safe full alignment through two one-sided joins.
MySQL's scalar mask lowering is implemented, but expanded attribution stays
rejected after the live composed query exhausted the qualification server's
768 MiB memory limit. Trino's expanded Top-K form remains rejected: the live
query exceeded the certified server's 150-stage limit without a read-only
materialization path.
Forecast, Kendall and time discovery consume complete source-aggregated
inputs in the caller; they do not collect raw semantic Entity rows.

Computed Measures (row expressions) aggregate on these backends. A
`@ms.measure(...)` body returns one row-level ibis expression over the owning
Entity's declared columns — add/subtract/multiply arithmetic, unary negation,
explicit casts and typed literals. Cross-row aggregations, window functions,
division, conditional or null-handling calls, references outside the owning
Entity, undeclared columns and float operands in arithmetic fail at semantic
load with structured errors before execution. All six backends admit these
bodies on their declared table sources.

Exact distinct membership for direct measure and Entity identity keys is
qualified on PostgreSQL, MySQL, SQLite, Trino and ClickHouse. SQLite and MySQL
deduplicate typed Entity identity fields as scalar SQL columns and reconstruct
the unchanged private Arrow struct. Exact linear-interpolation distribution
state is qualified on all five remote backends. Percentile status-time folds
and remote `duckdb_tdigest@v1` remain unqualified. Entity Pearson and Spearman
correlation now reduce complete source-private pairs on all five remote backends;
Kendall remains a complete-input local continuation. Sampling, Entity candidates, source driver screening and
Event/Lifecycle remain unsupported on these backends. Remote
retained import stays disabled. Cumulative Metric graphs and semantic calendar buckets were
activated by C6 on all five remote backends — calendar buckets over native
civil-date axes with a matching certified calendar snapshot, cumulative
Metric graphs through the shared endpoint-window lowering with time_scope
clipping and retained continuation — while any other source requirement
beyond that lowering stays rejected. Every admitted cumulative anchor shape
is admitted on every backend, including the fiscal composite
``ms.cumulative(anchor=ms.grain_to_date(grain=<certified calendar grain>))``;
a DuckDB oracle journey executes that fiscal-grain composite (fiscal-month
endpoints and the axis-less scalar path), while its five remote backends'
execution evidence is scheduled with the C7/C8 calendar work. Linear Metric graphs and computed Measures were activated by C4 on
all six backends; the composed-Decimal unit matrix above names each backend
whose linear decimal cell resolves exactly (PostgreSQL, MySQL, ClickHouse) and
the ones that keep the conservative rejection (Trino's lossy AVG probe, SQLite's
missing Decimal storage). Multi-unit buckets and string-parser time axes were
activated by C3b
(see the C3b acceptance record for the per-backend and per-format limits); Trino
carries no execution evidence yet for either. No
private state is uploaded or moved to a different executor to bypass rejection.

### Native timestamp analysis

Qualified native timestamps support typed predicates and `hour`/`day` buckets
with `count=1`, through microsecond precision. PostgreSQL also admits timestamptz;
MySQL TIMESTAMP and ClickHouse DateTime/DateTime64 retain physical instant
semantics when bound with an aware timestamp type. ClickHouse timestamp execution
requires a verified UTC reader timezone; non-UTC column and report timezones remain
supported. Existing explicitly asserted
UTC-labelled civil bindings remain validated as civil values. A physical timezone
must match an aware declaration; a plain declaration cannot relabel a non-UTC
ClickHouse instant. Trino uses the actual Iceberg timestamp(6) type. SQLite keeps
its validated civil-text representation and uses connection-local deterministic
time functions without transferring source rows to another executor.

An authored native parser timezone takes precedence over the reader timezone.
Only engines without a timezone probe use recorded system fallback; failed probes
and invalid names fail when reader authority is needed. Physical instants and
explicit parser authority do not require a reader probe. IANA and explicit fixed
offsets are supported for reader/report authority; native parser declarations
retain their existing IANA validation. Report authority is persisted once.

Scopes compare exact instants before producing report-local civil coordinates.
Naive timestamps in DST gaps or folds fail rather than selecting an implicit
interpretation. Two known instants in a repeated report hour share its civil bucket.
Naive predicate literals compare civil fields; aware literals compare instant
fields. Mixing these kinds fails. Milliseconds/microseconds remain exact through
SQL literals, Arrow, Parquet and source-offline cold recovery. Hour-to-day retained
folds reuse the persisted temporal facts. String parsing and multi-unit buckets are
enabled per backend by C3b: a multi-unit bucket is anchored on the civil midnight of
its own day and only counts whose width divides one civil day are admitted, so a
six-hour bucket spanning a spring-forward gap is five hours and one spanning a
fall-back gap is seven. Semantic calendar buckets and cumulative Metric
extensions are enabled on all five remote backends by C6 — calendar buckets
over native civil-date axes with a matching certified calendar snapshot
(admitting only its published levels), cumulative Metric graphs through the
shared endpoint-window lowering with any other source requirement rejected.
Epoch parsing and new timestamp version selection remain
unsupported on remote backends.

```python
import marivo.analysis as mv
import marivo.semantic as ms

orders_by_hour = (
    session.observe(
        ms.ref.metric("sales.revenue"),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
    )
    .with_time_axis(ms.ref.time_dimension("sales.orders.order_time"), grain=mv.grain("hour"))
    .aggregate()
    .execute()
)
orders_by_hour.rollup(grain=mv.grain("day")).execute().show()
```

Actual adapter submissions own SQL diagnostics, with source/local domain, role
and submitted/succeeded/failed status. Compilation alone records no submitted SQL.
Driver-internal connection setup and transaction protocol traffic outside the
observed submission boundary are not claimed as captured queries.

### PostgreSQL Group A

The registry admits one datasource and one unversioned `md.table` Entity with
direct-column scalar `sum`, `count`, `min` and `max` Metrics. Admission examines
the complete logical and semantic dependency closure, including projected-away
Metrics, membership predicates and Metric slices. Supported operations are
Population membership, scoped observation, dimensions, Entity aggregation,
filtering, Metric projection, deterministic ranking and Top-N.

All required source columns must use Boolean, string, signed integer, float32,
float64, date, timestamp/timestamptz (precision 0–6) or Decimal types. PostgreSQL CHAR is rejected
because its trailing-space semantics differ from string; text/varchar remain supported. Explicit Decimal precision is
at most 38 and scale lies between zero and precision; a generic Decimal declaration
still requires compatible physical metadata. Temporal scopes use native date
columns. Parsed string time axes and source-private methods are not admitted by Group A. The relational/date extension above owns additional method admission. PostgreSQL receives
no retained-import capability.

The adapter uses read-only service-side cursor transactions and records actual
metadata, validation and output statements. It creates no remote temporary tables,
uploads or UDFs. Each query may read a different source state; no common snapshot
or implicit retry is added. Transport and cleanup failures preserve the original
error and cannot publish partial output.

### MySQL and SQLite Group A

Both backends admit the same single-source, single-unversioned-table Group A
closure: Population, scoped observations, direct-column sum/count/min/max,
dimensions, aggregation, filtering, projection, deterministic ranking and Top-N.
All dependencies must be admitted, including projected-away Metrics. Neither
backend imports retained Artifacts or enables source-private methods.
The relational/date extension above owns additional method admission.

MySQL admits tables and views with a SELECT-only account. Inputs include signed and unsigned
integers, float32/64, native DATE, `utf8mb4_0900_bin` VARCHAR/TEXT and explicit
Decimal precision up to 38. Explicit Boolean bindings accept TINYINT(1) only after
necessary-column 0/1/NULL checks; integer bindings remain integers. DATETIME(0–6)
and TIMESTAMP(0–6) preserve microseconds; TIMESTAMP requires an observed session time_zone of UTC or +00:00; other aliases remain unqualified.
CHAR, BIT, ENUM/SET, nested values and generic Decimal sources are not admitted.
Zero/invalid dates and timestamps fail before publication. Integer SUM is decoded
exactly from Decimal; the current result is int64, so values beyond its range fail.
Floating SUM overflow raises the original database exception.

SQLite accepts persistent ordinary main-database tables and views. INTEGER/INT/BIGINT,
TINYINT/SMALLINT/MEDIUMINT/INT2/INT8 map to int64; REAL/DOUBLE/DOUBLE PRECISION/FLOAT
to float64; TEXT/CLOB/CHAR/VARCHAR (including declared lengths) to string with
BINARY collation and actual text storage. DATE requires valid canonical YYYY-MM-DD
text. BOOL/BOOLEAN requires integer 0/1/NULL. DATETIME/TIMESTAMP requires fixed
`YYYY-MM-DD HH:MM:SS.ffffff` civil text, valid Gregorian years 0001–9999, without
an offset. Necessary-column storage checks run even when output is empty.
Unsigned, Decimal, native aware SQLite storage, virtual and attached tables remain excluded.
SQLite stores inserted NaN as NULL; that distinction cannot be recovered.
Native integer SUM overflow remains an error.

Ordinary timestamps support exact transport, grouping, sorting and typed predicates.
The native timestamp extension above owns temporal method admission; a declaration
alone does not establish a naive timestamp's read-timezone authority.
UInt64 identities, min/max and transport preserve the full unsigned range through
Arrow, Parquet and cold reads without floating or signed conversion.

Both adapters flatten internal Entity identity structs into typed scalar SQL
columns and rebuild the unchanged Arrow identity schema without string or float
encoding. Transport uses direct cursor fetches, not Ibis's pandas-buffered Arrow
path. MySQL uses an unbuffered SSCursor; early cursor close may drain unread
responses. SQLite uses its native incremental cursor. Batch size is transport
configuration, not a result or memory cap. Driver, database and runner limits
remain external; Marivo sets no execution timeout and promises no hard cancel
latency. Analysis does not reuse authoring timeout/transaction wrappers.

Use a SELECT-only MySQL account without write or object-creation privileges;
SQLite execution enables query-only mode. Cancellation targets only the owned
connection, including SQLite fetch execution. Connection close is not proof of
remote termination. Original failures survive cleanup errors, partial output is
never published, and locally safe recovery remains possible with unknown remote
status. Cold Artifact reads and exact binding hits do not access the source.

### Trino scalar Metrics

Trino Group A supports one datasource and one unversioned ordinary relation —
table or view — with direct-column `sum`, `count`, `min` and `max`, Population
filtering, native-date scopes, same-Entity dimensions, aggregation, projection,
rank and limit. Use the existing catalog/schema/table declaration; both tuple and
dotted catalog/schema overrides are resolved consistently for metadata and
executed SQL. The connector name and relation form are observation receipts, not
gates: any connector's ordinary relations enter, and only `$`-suffixed internal
tables are rejected.

Declared physical inputs are signed integers, float32/64,
VARCHAR, BOOLEAN, timestamp(0–6) without time zone, DATE and explicit Decimal
precision/scale up to 38. The observed Iceberg connector exposes timestamp DDL
with precision 0 or 3 as timestamp(6); bindings must match this observed precision. CHAR, generic Decimal source declarations, timezone-bearing
timestamps, precision above microseconds and nested values
are excluded. Declared floating columns must contain finite values or
NULL; source checks reject NaN/infinity even for empty output, and non-finite
aggregate results fail before publication. Decimal and integer identities retain
exact values. Ranking preserves explicit NULL order and deterministic ties.

Use a read-only identity with access to connector and table metadata. Ordinary
Ibis compilation feeds incremental driver page reads; internal identity structs
are reconstructed from typed scalar columns. Batches are not byte or memory
limits. Driver/server limits remain external. Active cursors are owned before
submission; cancellation and close failures report unknown remote status without
blocking safe local recovery. Validation and output can observe different source
states. Retained imports and source-private advanced methods remain unavailable.
The relational/date extension above owns additional method admission.

The original Slice 1d blanket restriction is superseded. Existing DuckDB
Event/Lifecycle, Candidate, JSON and retained-stream execution remain available.


`marivo.analysis` is the governed Dataset analysis surface. Import it as `mv`,
with `marivo.semantic as ms` for semantic identities and `marivo.datasource as md`
for datasource authoring. Analysis consumes declared meaning; the agent owns
business judgment, causal interpretation, and the choice of the next question.

## Construct, execute, inspect

A Session carries a guiding question and persistent project-local identity.
Source constructors describe work without reading source data or creating a Run.
A Logical Dataset has complete row meaning, owned fields, a bounded identity repr,
and `contract()`. Explicit `execute()` returns the paired Materialized Dataset.
Its `show()` and `to_pandas()` read retained values under Runtime guards.

```python
import marivo.analysis as mv
import marivo.semantic as ms

session = mv.session.get_or_create("revenue-review", report_timezone="UTC")
revenue = ms.ref.metric("sales.revenue")
current = session.observe(
    revenue, time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01")
).aggregate()
baseline = session.observe(
    revenue, time_scope=mv.time_scope(start="2026-06-01", end="2026-07-01")
).aggregate()
change = current.compare(baseline).execute()
change.show()
```

This example requires an authored, typed `sales.revenue` Metric with an admitted
reference time axis. The scopes are literal half-open intervals. An observation
normally retains Entity identity; `aggregate()` explicitly removes that axis.
A time series additionally declares `.with_time_axis(axis, grain=mv.grain("day"))`.

Construction may load the in-memory semantic catalog and certified project
snapshots. It does not resolve credentials, open source connections, query rows,
materialize results, or perform result reads. Mutable ambient source bindings are
captured during construction and are not reread at execution.

## Row meaning and ownership

Dataset families are Population, Metric, Delta, Attribution, Association,
Forecast, Candidate, Event and Lifecycle. Each admitted shape has paired Logical
and Materialized classes. Shape, schema, coordinate/key fields, row cardinality,
ordering, authority and definition identity are explicit immutable contracts.

Entity `primary_key` is stable identity K. Snapshot/validity coordinates describe
historical rows separately. Population selects membership; it does not silently
set an observation window. Cross-Session Dataset operands are rejected. Selectors
from `dataset.fields` belong to that exact Dataset, and cannot be borrowed from
another result with the same column name.

Methods transform definitions and return Logical Datasets. Applying a method to
a Materialized Dataset starts from its retained rows and private contribution
state; it does not replay the origin. Projection, filtering, ranking
and aggregation keep their distinct meanings. Native contract validation checks
whether the requested transition is admitted before source work.

## Exact execution and persistence

Runtime fixes the registered implementation and destination before executing.
Source execution supports DuckDB and admitted PostgreSQL, MySQL, SQLite, Trino and ClickHouse scalar/relational methods. The private method registry
selects one exact backend registration for the typed invocation, with full
source execution and preparation declared separately. Unsupported known inputs
fail before Run admission; exact same-Session binding hits remain source-free.
Retained Parquet can attach only to the existing DuckDB reader, never by an
implicit upload to another datasource. Source and execution ownership are
independent of diagnostic engine versions.
Unconfigured projects retain results in local Parquet. An explicit `marivo.toml`
object-store binding changes the write destination. There is no database result
storage, automatic destination selection, or failure-triggered executor retry.
Native DuckDB analysis of immutable retained Parquet is admitted by the owning
registered method; temporary execution relations are not persisted Artifacts.

Source assertions, primary output and required part reads need not observe the
same source state. Each query uses its backend's current observation; successful
checks do not certify later reads. Marivo neither opens a shared consistency
transaction nor rejects or retries solely because intervening updates occurred.
Required validations and method-owned single-evaluation fences still apply;
atomic publication does not certify a common source snapshot.

One admitted execution creates a Run. Publication commits the primary result,
required private parts, descriptor, Evidence and Findings atomically. Cache hits
on the same exact realization do not invent another Run. Failed or interrupted
Runs never masquerade as successful Artifacts. Store generation 5 is required;
existing older generations are rejected without rewriting their files.

Local kernels and complete retained reads run synchronously in the calling Python
process. Marivo imposes no execution row/byte/cell, memory/spill, complexity,
storage or deadline budgets. Batch sizes tune transfer; they never truncate the
result. Database, driver, OS and external runner limits remain independent.
Original exceptions retain their causes and tracebacks, including when cleanup
also fails. Complete inputs, semantic validation and atomic publication remain
mandatory. `show()` keeps its display bounds; `to_pandas()` returns a complete
isolated copy. Exact distinct membership and quantile methods retain all state
required for valid computation and cold recovery.

Ibis expressions may compile during normal execution. Correct parameters,
complete typed results, required validation order and method-owned single
evaluation remain mandatory; repeated pure compilation is not a duplicate query.
Engine, driver and Ibis versions do not gate execution or define source domains.
Remote read termination uncertainty is disclosed without blocking safe local
recovery; unresolved publication ownership and storage integrity still block.

## Disclosure and interpretation

Start at `marivo.help("analysis")`. Its bounded hubs route to source entry,
methods, inputs, artifacts, evidence and runtime. Native Help owns exact callable
signatures, constraints and executable examples. Dataset `contract()` owns
mechanical continuations, including the public call and exact Help target;
structured errors preserve concrete diagnostics and own repair. Packaged skills
own workflow decisions without duplicating signatures or private implementation
inventories. Entry links to Session bootstrap/recovery and Metric, Event and
Lifecycle sources. Inputs link to the scoped catalog and named input groups;
methods group analytical intents. An agent follows only the selected branch and
its prerequisite/result links, then writes and executes the minimum useful chain.
Type/member leaves remain independently queryable without flooding task discovery.

Algebraic attribution does not establish cause. Association is descriptive;
Candidate scores do not confirm an anomaly or prescribe action. Forecasts are
model outputs under explicit assumptions. Evidence records facts and derivation,
not the agent's narrative conclusion. Custom work through `to_pandas()` or
`md.raw_sql(...)` is terminal and cannot re-enter governed Dataset analysis.

## Owning contracts

- [Dataset methods and states](operators-and-frames.md)
- [Session and Runtime](session-state-and-runtime.md)
- [Evidence reads](evidence-access-surface.md)
- [Timezones and calendars](timezone-and-calendar-design.md)
- [Temporal authoring](../temporal-semantics.md)
- [Detailed observation contract](../../superpowers/specs/2026-09-01-lazy-analysis-observation-model-design.md)
- [Detailed materialization contract](../../superpowers/specs/2026-09-01-lazy-analysis-materialization-runtime-design.md)

## Proposed extensions

- [Multi-datasource lazy execution design and implementation plan](../../superpowers/specs/2026-09-15-lazy-analysis-multi-datasource-design-and-plan.md)
  describes staged backend qualification. It does not enable additional source
  execution backends or change the current contracts above.

### ClickHouse scalar Metrics

ClickHouse Group A admits one datasource and one unversioned ordinary relation —
table or view — under any engine: direct-column sum/count/min/max, Population
filters, native-date scopes, same-Entity dimensions, aggregation, projection,
deterministic rank and limit. Distributed and multi-shard relations are accepted,
as are unstable reads: engines whose reads depend on background merge state (for
example ReplacingMergeTree) can return different rows between runs as merges
progress; replay of a committed snapshot is unaffected. Sampling, retained import
and source-private advanced methods remain unavailable. The relational/native-date
extension owns additional method admission.

Physical inputs include Int8/16/32/64, UInt8/16/32/64, Bool, Float32/64, String,
Date and explicit Decimal precision up to 38, with legal Nullable wrappers.
LowCardinality(String) and LowCardinality(Nullable(String)) retain string semantics.
DateTime('UTC') and DateTime with verified UTC engine timezone preserve seconds.
DateTime64 through microseconds and aware non-UTC bindings are admitted by the
native timestamp extension. FixedString, Date32, Int128/256, UInt128/256, Enum
and nested values remain excluded. Engine form is not restricted by this
scalar-type extension.

Use a SELECT-only account configured with effective `join_use_nulls=1`.
Metadata, required assertions and output run separately; empty output never
bypasses validation. Declared floating values must be finite or NULL. Exact
integer/Decimal sums widen internally to Decimal256 before checked output
conversion; overflow and non-finite output fail without publication.

Native row streams preserve Decimal and typed Entity identities without pandas
or raw source transfer to another engine. Each response is owned and closed;
driver close can drain unread data, so cancellation has no hard latency promise.
Connection close does not prove remote termination. Unknown remote status does
not prevent safe local recovery; partial output is never published. No shared
snapshot, execution budget, upload, temporary object or implicit retry is added.
Batch size is transport configuration, not a result cap.
