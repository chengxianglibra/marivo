# Dataset Methods and States

Execution follows the [unified operator and backend ownership contract](python-analysis-design.md#unified-operator-and-execution-ownership). Backend-specific preparation does not change operator semantics.


This document defines the analysis composition model. Exact signatures, public
exports and runnable examples are registered natively and exposed through
`marivo.help("analysis")`; the API reference documents the same bindings.

## Paired state classes

Each family has one row contract and distinct Logical/Materialized classes.
Logical Datasets describe deferred work and expose `execute()`. Materialized
Datasets expose guarded retained reads: `show()`, `to_pandas()`, `evidence_digest`,
`findings(...)` and `finding(...)`. Common `contract()`, `schema`, `row_contract`,
`row_set_contract`, `fields`, `state`, `definition_fingerprint` and `lineage`
properties describe immutable identity and meaning.

A logical repr identifies shape and definition and points to execution. A
materialized repr identifies shape, Artifact and row count and points to `show()`.
Neither construction nor repr performs source or retained-row I/O. Dataset objects
have no generic dataframe protocol: iteration, implicit truth conversion, length,
item access and arithmetic are not a substitute for registered methods.

## Family routes

| Family | Construction or consuming route | Meaning |
| --- | --- | --- |
| Population | `session.population(...)` | Exact governed Entity membership |
| Metric | `session.observe(...)` | Contributions over membership and independent observation scope |
| Delta | `metric.compare(baseline)` | Exact current/baseline pairing and arithmetic |
| Attribution | `delta.attribute(...)` | Contributions under admitted additive/component authority |
| Association | `metric.correlate(...)` | Declared descriptive association method |
| Forecast | `metric.forecast(...)` | Explicit model and horizon over admitted history |
| Candidate | `dataset.discover.<objective>(...)` | Evaluated candidate rows and reasons |
| Event | `session.events.match(...)` | Governed participant patterns and assignment |
| Lifecycle | `session.lifecycle.replay(...)` | Governed state history with explicit seed and window |

Downstream reducers and operators belong to their Dataset. Event funnel,
time-to-event and subject membership consume retained assignment. Lifecycle
reducers and membership consume retained replay history. They do not rerun source
matching or state replay. Consult the exact returned contract for admitted shape
and completeness requirements.

## Coordinate and row transitions

Metric construction retains Entity rows unless explicitly reduced. The independent
Entity, dimension and time coordinates produce the eight Metric shapes. Use
`with_dimensions(...)`, `with_time_axis(...)` and `aggregate()` to declare them.
`rollup(drop_dimensions=..., grain=..., drop_time=...)` requires exact retained
fold authority for every Metric; it is never an unqualified sum of displayed
numbers. Means retain numerator/support, rates retain their selected denominator,
and distinct/distribution methods retain private state.

Population `where(...)` filters membership before observation; Metric `where(...)`
filters the current Metric rows at its current shape. Closed typed predicates are
constructed with `eq`, `not_eq`, `lt`, `lte`, `gt`, `gte`, `is_in`, `is_null`,
`is_not_null`, `all_of`, `any_of` and `not_`. Null and nonfinite rules are explicit.
There is no string query language or pandas mask callback.

`rank(...)` establishes a deterministic ordering using an exact owned value field,
closed direction/tie policies and optional current key partitions. `limit(count)`
selects an ordered prefix. Neither operation certifies statistical sampling.
Population sampling is explicit and retains its selected identity authority.
Metric projection uses one exact carried Metric or owned field; it cannot silently
change computation, quantile method, or retained contribution membership.

## Operators and evidence

Comparison pairs exact keys/coordinates under the registered `window_bucket`
alignment. Missing or unusable input values remain explicit; they are not zero.
Attribution uses actual retained components and the registered joint/hierarchy
contract. The agent chooses axes and interprets the resulting algebra.

Correlation has closed registered methods and paired-value requirements. Forecast
requires explicit model/horizon and certified coordinate eligibility. Candidate
objectives are point anomalies, interesting windows, period shifts, Entity
outliers and driver axes. Candidate rows carry their evaluated scope and reasons;
they are evidence for investigation rather than confirmed causes.

Exact median/percentile observations default to linear interpolation. Use
`ms.quantile_metric(metric, method="duckdb_tdigest@v1")` only for explicitly chosen
semantic approximation. The governed Metric owns q; fields, projections, retained
parts and cold recovery preserve the method identity. No backend or cost heuristic
changes this choice.

## Runtime boundaries

Every input belongs to the same Session. Definition identity differs from exact
materialized Artifact identity. Materialized consumers validate selected receipts,
complete required private state and applicable retained/current authority without
replaying origin work. Source-required enrichment is a distinct admitted method.
An unsupported method, invalid role or foreign input produces a structured failure
with the expected input and concrete repair. Local execution and complete retained
reads run in the caller without Marivo resource caps; original execution exceptions
preserve their causes and tracebacks.

Terminal custom analysis uses `materialized.to_pandas()` or datasource raw SQL.
Its result cannot be injected as a governed Dataset. No compatibility constructor,
cast, generic transform namespace or legacy public class is retained.

Execution placement uses registered method support and exact binding ownership,
without engine/driver/Ibis version certification. Diagnostic version differences
do not merge distinct datasource or retained-input authorities.
