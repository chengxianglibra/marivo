# Python Analysis Design

## Unified operator and execution ownership

All backends use one operator contract and implementation-registration mechanism.
The selected backend owns physical sampling eligibility, preparation, execution,
retained import and resource lifetime. DuckDB's temporary objects, macros and
registration hooks implement the same semantic requirements; they are not
operator-level exceptions. Remote implementations must work with read-only
accounts and prove equivalent single evaluation, numerical behavior and required
assertions before registration. No implicit alternate route or new cross-engine
private-state transfer is introduced. Only DuckDB analysis is currently enabled.

The original Slice 1d blanket restriction is superseded. Existing DuckDB sampling,
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
state; it does not replay the origin. Projection, filtering, sampling, ranking
and aggregation keep their distinct meanings. Native contract validation checks
whether the requested transition is admitted before source work.

## Exact execution and persistence

Runtime fixes the registered implementation and destination before executing.
Source execution currently supports DuckDB only. The private method registry
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
