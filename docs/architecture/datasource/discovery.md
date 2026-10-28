# Datasource Discovery and Evidence

Metadata, scoped samples and terminal SQL answer different questions. Use
[connection tests](README.md#registration-and-state-storage) for connectivity and
[raw SQL](sources-and-execution.md#ibis-owned-analysis-reads-and-terminal-raw-sql)
for a managed terminal source-specific query. None establishes business meaning.

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
keyed by the selected columns. Incompatible evidence must be reacquired.

Scope and explicit columns are required. `md.unpruned(max_rows=...,
timeout_seconds=...)` is the deliberate broad-read escape within acquisition.
Both values must be positive. Row limits remain enforced; query timeouts
request native settings and are skipped when unsupported. `LIMIT` bounds returned rows, not bytes scanned, and a partition may
still be large. Known partition fields with captured value evidence require an
explicit complete partition scope or a supported temporal range. Unavailable
partition values retain the explicit unpruned fallback. Semantic ordinary previews
reuse this physical preflight before business reads.

Scope `show()` / `render()` cards expose only the scope kind, positive guards,
and a bounded predicate preview. Large partition predicates report omissions
and point to `.values` for the exact mapping; unpruned scope is labeled as a
broad read.

Read-only ClickHouse acquisition uses the server setting `readonly=1` together
with optional native query timeout settings and throwing overflow. Connection, source-resolution, timeout, and
post-execution failures surface as `DatasourceAuthoringError` values with
`query_executed`, a sanitized backend summary, and one focused repair; they do
not escape as raw driver exceptions.

The snapshot card makes datasource/source/scope identity, selected columns,
coverage, and value/cache state explicit; use `snapshot.show()` for
these query-free mechanical read facts. Values default to memory-only. A later
process can recover snapshot identity, profiles, and coverage but not retained
values unless the original sample used `persist_values=True` with explicit
acceptance of bounded plaintext project-local caching. When available, read a
retained row by column name, for example
`snapshot.retained_values[0]["order_id"]`. Uncommon formats, keys,
timezones, aggregation, units, additivity, relationship cardinality, null
semantics, and business meaning remain agent-owned. Marivo does not classify a
high null rate as a business-quality failure or filter nulls implicitly.

Acquisition opens an isolated reader with requested native query settings.
PostgreSQL uses `statement_timeout`, Trino uses `query_max_run_time`, ClickHouse
uses `max_execution_time` and throwing overflow, and MySQL uses its native
read-only SELECT limit. Their server-defined coverage is not a total
execution-and-fetch deadline. DuckDB does not simulate query timeouts;
unsupported limits do not block reads. Reader cleanup and row/scope constraints
remain mandatory. Shared connections are not altered.


Reacquire evidence only when a required column or value was not captured, or
when datasource/source/scope identity no longer matches. Snapshot age alone is
not invalidation.

For a declared-only projected binding, a successful bounded sample proves that
the generated projection executed for the selected output aliases and scope. It
does not certify business meaning. Scoped
`catalog.preview(..., scope=...)` reads the current source directly. Ordinary
preview does not persist an authoring checkpoint or affect readiness.


## Interpretation and implementation

A successful projection read proves execution only for its aliases and scope.
High missingness, observed uniqueness and apparent join patterns remain physical
evidence; they do not authorize enums, keys, additivity, timezones or allocation.
Continue through the [semantic authoring workflow](../semantic/authoring-workflow.md)
when reusable meaning is needed.

[inspection.py](../../../marivo/datasource/inspection.py) and
[metadata.py](../../../marivo/datasource/metadata.py) own metadata acquisition.
[snapshot.py](../../../marivo/datasource/snapshot.py) and
[domain_snapshot.py](../../../marivo/datasource/domain_snapshot.py) own bounded
evidence capture and retention.
