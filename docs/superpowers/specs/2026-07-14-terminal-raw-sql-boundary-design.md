# Marivo Terminal Raw SQL Boundary Design

Status: approved for implementation planning

Date: 2026-07-14

## Summary

Remove every path that upgrades arbitrary Ibis, pandas, or SQL-shaped data into
a canonical Marivo analysis artifact. Replace the current mixed boundary model
with two explicit, one-way terminal exits:

```text
datasource -> md.raw_sql(...) -> RawSqlResult -> RawSqlResult.to_pandas()

semantic metric -> session.observe(...) -> typed analysis artifacts
                                          -> frame.to_pandas()
```

`md.raw_sql(...)` becomes the sole public agent-facing raw SQL execution path.
It returns a bounded, in-memory, terminal result. `frame.to_pandas()` remains
the terminal exit from typed analysis. Neither result can re-enter typed Marivo
analysis.

This is an atomic breaking cutover. It removes `derive_metric_frame`, the
internal scratch/promotion implementation, their supporting policies, types,
errors, persistence hooks, telemetry, help entries, and tests. It provides no
deprecated aliases, compatibility loaders, migration helpers, serialized-state
converters, or staged rollout mode.

Historical versioned documentation, release notes, and superseded design/plan
artifacts remain historical and are not rewritten. Active specs, latest English
and Chinese documentation, live help, API reference, packaged skills, and the
current 0.3.2 release notes move together.

## Problem

### `derive_metric_frame` cannot validate semantic equivalence

`derive_metric_frame` accepts a catalog metric anchor, an Ibis query builder,
output-column bindings, and declared window metadata. It then creates a
canonical `MetricFrame`.

The runtime can validate only structural facts:

- the selected metric exists;
- required output columns exist;
- the value column is numeric;
- declared axis refs resolve;
- the return value is executable as an Ibis expression.

It cannot establish that the query implements the selected metric definition,
applies the declared time scope, emits one row per declared grain and slice, or
preserves composition, cumulative, fold, component, and re-aggregation
semantics. The query result is used verbatim while `time_scope` and `grain` are
metadata declarations that the callback may ignore.

Adding more caller-authored metadata would not solve the problem. An incomplete
declaration loses semantic information; a complete declaration duplicates the
semantic layer and still does not prove that the query is equivalent to it.

### Hidden promotion recreates the same invalid boundary

`marivo.analysis.escape_hatch` is not on the default public surface, but it
still implements:

- `from_pandas(...)`;
- `explore_ibis(...)`;
- `promote_metric_frame(...)`;
- `promote_delta_frame(...)`;
- `promote_attribution_frame(...)`.

Those functions persist an `ExplorationResult` and allow callers to attach
enough metadata to promote arbitrary data into typed analysis families. Keeping
them after deleting `derive_metric_frame` would leave a less discoverable
version of the same semantic re-entry problem.

### The current raw SQL path is too narrowly framed and lacks execution bounds

`md.raw_sql(...)` already owns the correct layer for arbitrary SQL execution:
datasource connection resolution, backend-specific read-only behavior, cursor
decoding, bounded rows, and typed datasource errors.

However, its current contract is described only as a diagnostic helper. It
bounds returned rows but does not expose a query timeout parameter, cannot
distinguish an exact-limit result from a truncated result for wrapped queries,
and does not offer a defensive pandas terminal conversion.

Once semantic re-entry is removed, this existing datasource boundary should be
made complete enough for terminal custom analysis without becoming another
analysis runtime.

## Decision

Adopt one public SQL execution entry and two terminal data exits.

### Public SQL execution

`md.raw_sql(...)` is the sole public agent-facing raw SQL execution function.
It belongs to `marivo.datasource` because datasource resolution, backend
behavior, read-only enforcement, timeout enforcement, and cursor decoding are
datasource responsibilities.

It does not require or create an analysis `Session`.

### Typed-analysis exit

`frame.to_pandas()` remains available on typed analysis artifacts. It returns a
defensive DataFrame copy and explicitly crosses out of lineage, metadata,
session ownership, evidence continuity, and typed operator compatibility.

It does not execute SQL.

### Raw-SQL result exit

`RawSqlResult.to_pandas()` returns a defensive DataFrame containing only the
bounded rows already present in the result. It does not rerun the query, fetch
additional rows, infer semantic objects, or create an analysis artifact.

### No governed re-entry

Marivo no longer exposes any path from raw SQL, Ibis scratch work, pandas data,
or a terminal result into `MetricFrame`, `DeltaFrame`, or `AttributionFrame`.

If a value must participate in typed metric analysis, its business meaning must
be represented in the semantic layer and materialized through
`session.observe(...)`. If the semantic/runtime model cannot express it, the
task remains terminal custom analysis or returns to semantic/runtime authoring;
the caller does not attach a canonical type after the fact.

## Alternatives Considered

### Move raw SQL to `session.raw_sql(...)`

This would put custom queries near analysis operators and could persist their
results in the session. It is rejected because SQL connection and execution
belong to the datasource layer, raw SQL is useful before an analysis session
exists, and session ownership would imply a stronger relationship to typed
analysis and evidence than the result actually has.

### Introduce a generic `mv.escape(...)`

A generic function could accept SQL, Ibis expressions, pandas DataFrames, and
typed frames, then dispatch to different result families. It is rejected
because it creates an optional-field mega API, forces agents to memorize input
and output variants, obscures the direction of the boundary, and recreates the
promotion ambiguity this design removes.

### Retain a simple-metric-only `derive_metric_frame`

Restricting derive to simple metrics would avoid some cumulative, fold, and
component defects. It is rejected because the runtime still could not prove
that the custom query implements the selected metric, time scope, grain, or
slice. A narrower unverifiable promotion remains an unverifiable promotion.

### One SQL entry plus separate terminal exits — selected

Keep SQL execution in `md.raw_sql`, keep artifact exit in
`frame.to_pandas()`, add bounded pandas conversion to `RawSqlResult`, and remove
every re-entry path. This preserves narrow ownership and gives each public
function one direction and one result contract.

## Breaking Removal Scope

### Governed derive surface

Delete:

- `marivo/analysis/derive.py`;
- `Session.derive_metric_frame(...)`;
- `DeriveContext`;
- `IbisQuerySpec`;
- `MetricColumnBinding`;
- `MetricColumns`;
- `mv.ibis_query(...)`;
- `mv.metric_columns(...)`;
- `mv.time_column(...)`;
- `mv.dimension_column(...)`;
- capability `boundary.derive_metric_frame`;
- derive helper descriptors and root-help entries;
- derive telemetry registration;
- derive-specific tests and API-reference pages.

Remove derive language from analysis type algebra, artifact producers,
knowledge/evidence descriptions, errors, constraints, active specs, latest
docs, the packaged analysis skill, and the current release notes.

### Legacy scratch and promotion implementation

Delete:

- `marivo/analysis/escape_hatch.py`;
- `marivo/analysis/frames/exploration.py`;
- `ExplorationResult` and `ExplorationResultMeta` frame exports;
- `exploration_result` loader registration;
- the `BaseFrame.contract()` `exploration_result` canonicality special case;
- `PromotionSemanticAnchors`;
- `PromotionPolicy`;
- `PromotionFailedError`;
- escape-hatch telemetry registrations;
- scratch/promotion tests and their supporting fixtures.

Update secondary tests that currently mention these symbols, including import
surface, public-session absence, frame/result protocol, query-record,
telemetry, policy, and artifact-loading tests.

### Explicitly retained names and behaviors

Do not delete or rename:

- `frame.to_pandas()` on canonical analysis artifacts;
- `marivo.preview.preview_from_pandas`, which is an internal bounded preview
  conversion and not an analysis promotion path;
- `ms.from_sql(...)`, which records SQL provenance only and never executes SQL;
- semantic-validator constraints that reject raw SQL bodies;
- backend-internal `raw_sql(...)` methods used by datasource adapters;
- historical versioned docs, historical release notes, and superseded
  `docs/superpowers/specs/` or `docs/superpowers/plans/` artifacts.

## `md.raw_sql` Target Contract

```python
md.raw_sql(
    datasource,
    sql,
    *,
    reason,
    limit=100,
    timeout_seconds=30,
    include_types=True,
) -> RawSqlResult
```

`project_root` remains an internal/testing integration parameter under the
existing datasource convention; it is not taught as an ordinary agent input.

### Input rules

- `datasource` is a typed `DatasourceRef`.
- `sql` is one non-empty statement.
- `reason` is a non-empty human-readable explanation and is returned with the
  result.
- `limit` and `timeout_seconds` are positive integers.
- the function has no write flag, unsafe mode, unlimited-row mode, or
  `limit=None` escape.
- `include_types` keeps the existing best-effort backend type extraction.

Validation happens before datasource connection when it depends only on call
arguments.

### Read-only enforcement

The datasource connection opens in read-only mode where the backend supports a
connection-level control. Transactional backends use their established
read-only transaction behavior. Metadata statements such as `SHOW`,
`DESCRIBE`, `DESC`, and `EXPLAIN` continue to execute directly so backend
syntax remains valid.

There is no SQL parser-based write allowlist. Backend read-only enforcement is
authoritative. A write attempt surfaces as `DatasourceRawSqlError` and no
unsafe bypass is added.

### Timeout enforcement

`raw_sql` uses the selected `EngineProfile.authoring_timeout` context to apply
a real backend/client timeout:

- DuckDB uses interruption;
- Trino uses `query_max_run_time`;
- ClickHouse uses `max_execution_time`;
- Postgres uses local `statement_timeout` inside a read-only transaction;
- MySQL uses `MAX_EXECUTION_TIME` plus read-only transaction behavior.

If the profile has no enforceable timeout, or the timeout cannot be armed, the
function fails closed before executing the user statement.

The implementation composes timeout and read-only lifecycle through the engine
profile without opening duplicate read-only transactions. Backend-specific
setup is not duplicated in `manage.py`.

### Row bounding and truncation

Ordinary `SELECT` and `WITH` queries execute through an outer query capped at
`limit + 1`. Direct metadata statements decode at most `limit + 1` rows.

The returned result contains at most `limit` rows:

- `is_truncated=False` when zero through `limit` total rows are observed;
- `is_truncated=True` only when the extra row is observed.

The limit bounds returned rows, not bytes scanned, join work, aggregation work,
or source reads. Help, result warnings, and latest docs retain this distinction.

### Cost model

There is no unlimited fetch or bulk export path. Agents that need large-scale
custom analysis push filtering, aggregation, and reduction into SQL, then
return a bounded result. Raising `limit` is explicit and remains subject to the
timeout.

## `RawSqlResult` Contract

`RawSqlResult` remains a frozen, display-first terminal result. It contains:

- `datasource`;
- `backend_type`;
- original `sql`;
- `reason`;
- `columns` in backend result order;
- best-effort `types`;
- bounded `rows`;
- `requested_limit`;
- `returned_row_count`;
- exact `is_truncated`;
- `timeout_seconds`;
- `duration_ms`;
- `warnings`.

`duration_ms` measures wall time from immediately before timeout setup through
result decoding. It excludes datasource configuration lookup and connection
construction.

### Display

`repr(result)` remains bounded and points to `.show()`.

`render()` / `show()` display:

- `terminal_only` status;
- datasource and backend;
- reason;
- returned-row and truncation state;
- timeout and duration;
- bounded row preview;
- warning that row limits do not bound backend work;
- warning that the result carries no metric, time-scope, slice, lineage, or
  canonical analysis contract.

The available operations include `.rows`, `.columns`, `.types`,
`.to_pandas()`, `.render()`, and `.show()`.

### Pandas conversion

`to_pandas()` returns a new DataFrame built from the bounded result rows in the
declared column order. Mutating the DataFrame or mutable values within object
columns must not mutate the `RawSqlResult`; conversion therefore performs the
same recursive isolation expected from other defensive Marivo data exits.

The conversion does not use backend type labels to coerce values, execute a new
query, or preserve Marivo metadata on the DataFrame.

## Execution Flow

```text
validate local arguments
    -> resolve datasource declaration and engine profile
    -> open read-only backend
    -> construct direct metadata SQL or limit+1 wrapper
    -> arm engine-profile timeout
    -> execute under read-only enforcement
    -> decode at most limit+1 rows
    -> restore timeout/session settings
    -> disconnect backend
    -> slice to limit and return RawSqlResult
```

Timeout restoration and backend disconnection happen on success, query error,
decode error, and cancellation.

## Error Contract

Simple argument failures remain immediate `ValueError`s before connection:

- empty SQL;
- multiple statements;
- empty reason;
- non-positive limit;
- non-positive timeout.

Runtime failures use `DatasourceRawSqlError`. Its details include:

- `datasource`;
- `backend_type`;
- `reason`;
- `timeout_seconds`;
- `stage`, one of `timeout_setup`, `execution`, or `result_decode`;
- backend/client `cause`.

The rendered error states that the query is terminal custom analysis, that no
analysis artifact was created, and whether the user statement began execution.
It routes to `md.help("raw_sql")` and the live datasource documentation, not to
analysis help or a packaged skill attachment.

The design does not add special errors for removed names. Calls to removed
functions fail normally with `AttributeError` or import failure. Loading a
previously persisted `exploration_result` follows the existing unknown-frame-
kind failure path; no compatibility class or migration-specific repair is
retained.

## Persistence, Evidence, and Telemetry

`RawSqlResult` is in-memory only:

- no analysis session is created or required;
- no artifact row or frame file is written;
- no observation digest or judgment finding is emitted;
- no semantic readiness or evidence status is inferred;
- no raw result is accepted by typed analysis operators.

Existing datasource connection/runtime observability may record the operation
according to repository telemetry policy, but this design does not introduce a
new durable raw-query history or analytics store.

## Active Guidance and Documentation

Update the complete active contract surface:

- analysis capability registry and CLI/Python help;
- datasource `md.help()` root and `md.help("raw_sql")`;
- `docs/specs/analysis/*` producer/boundary/type-algebra descriptions;
- `docs/specs/semantic/datasource-layer.md` raw SQL section;
- `agent-guide.md` only if a stable repository-wide rule changes (none is
  expected);
- `marivo/skills/marivo-analysis/SKILL.md` terminal-exit and governed-transition
  language;
- `marivo/skills/marivo-semantic/` raw SQL boundary language;
- latest English and Chinese analysis and semantic documentation;
- latest English and Chinese 0.3.2 release notes;
- Sphinx API reference sources and generated `site/public/api/` output.

The analysis skill no longer teaches a governed custom-result re-entry. It
states that terminal custom analysis cannot re-enter typed analysis; missing
business semantics return to semantic authoring and runtime capability gaps
remain custom terminal work until modeled explicitly.

Historical version directories, older release notes, and superseded design or
implementation artifacts remain unchanged.

## Testing Strategy

Implementation follows test-driven development.

### Removal tests

Update or add tests that prove:

- `Session` has no derive, scratch, or promotion methods;
- `marivo.analysis` exports no derive helpers;
- analysis policies expose no promotion policy types;
- analysis frames expose no exploration result type;
- `PromotionFailedError` is absent;
- root help, focused help, type algebra, and capability registry contain no
  derive or governed-reentry entries;
- telemetry contains no removed derive/escape-hatch intent ids;
- the loader contains no `exploration_result` class registration;
- active skill/docs drift checks contain no promotion guidance;
- no source import of the deleted implementation remains.

Delete `tests/test_analysis_escape_hatch.py` and
`tests/test_analysis_derive_metric_frame.py`. Replace broad behavior coverage
with small negative surface and source-absence regressions; do not preserve
tests for deleted semantics.

### Raw SQL tests

Cover:

- argument validation before backend connection;
- single-statement enforcement;
- read-only write rejection;
- timeout setup before user SQL;
- fail-closed behavior when timeout setup is unavailable;
- timeout/session-setting restoration on success and failure;
- backend disconnect on every path;
- direct metadata statements;
- wrapped `SELECT` and `WITH` statements;
- exact-limit results report `is_truncated=False`;
- extra-row results report `is_truncated=True` and return only `limit` rows;
- DB-API and ClickHouse cursor decoding;
- `duration_ms` and `timeout_seconds` result metadata;
- bounded display with terminal/no-semantics warnings;
- `to_pandas()` column order, value preservation, and recursive defensive
  isolation.

### Verification gates

Run the narrow tests first, then:

```text
make test
make typecheck
make lint
make examples-check
make docs-api
cd site && npm run verify:content
git diff --check
```

The implementation is complete only when source/help/docs searches show no
active derive or promotion path and all gates pass.

## Success Criteria

The cutover succeeds when:

1. No callable can promote raw SQL, Ibis, pandas, or scratch data into a typed
   analysis artifact.
2. `session.observe(...)` is the sole producer of an initial canonical
   `MetricFrame`.
3. `md.raw_sql(...)` is the sole public agent-facing raw SQL execution path.
4. Raw SQL execution is read-only, timeout-bounded, row-bounded, and reports
   exact truncation.
5. `RawSqlResult.to_pandas()` supports terminal custom analysis without
   creating semantic or evidence claims.
6. Active help, skills, specs, latest docs, API docs, telemetry, and tests teach
   the same one-way boundary.
7. Removed names have no aliases, shims, fallback loaders, or migration path.

## Out of Scope

This design does not add:

- writable SQL;
- multi-statement SQL;
- unbounded result fetches;
- SQL-to-semantic inference;
- SQL parsing as a semantic validator;
- automatic report generation from `RawSqlResult`;
- session persistence for raw SQL results;
- a new notebook, file-export, or bulk-download subsystem;
- compatibility for removed analysis APIs or persisted exploration artifacts;
- new semantic metric-expression capabilities.
