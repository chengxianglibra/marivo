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

## Accepted S0 method rules (inactive)

S1 W1 records J1 sum observation and original-state rollup method semantics
with retained sum, non-null-count and row-count parts and no qualified source
or pandas implementation. Private construction may bind them; execution
qualification remains pending.

W2 qualifies only the private J1 DuckDB/Ibis source shapes and pandas
continuations exercised by the J1 tests. Source observation retains a keyed
sum, non-null count, and row count; original-state rollup merges those parts
before finishing the Cell. `summarize("count")` counts current relation rows,
including Null Cells. Current-row sum/mean require finite Defined Cells and
build new row-statistic state. A complete empty current relation yields sum=0,
count=0, and mean=Undefined(`empty_mean`). Unsupported physical types and
backends reject before business data is read. This qualification is private;
the remaining S0 method matrix is still an inactive target.

S2 P1 registers a private absolute `compare` method for two ordered,
same-member Entity observations. Its result is a Difference; source and pandas
routes require exact complete key pairing and finite Defined int64/float64
Cells. Both endpoint rows are retained as separately receipted parts, so a
fixed pair uses the same member realization binding rather than inferring
common ownership from equal keys. Relative change, group comparison and
Difference selection/continuation are not qualified by P1.

The [first-round DSL slice](python-analysis-design.md#accepted-s0-analysis-dsl-slice-inactive)
accepts the following private rule obligations. The proposed public Relation
methods are not yet callable. Each registered method supplies:

```text
InputSignatures + Parameters
  -> OutputSignature + Pre + RequiredParts + PartTransform + Post + Transport + Eval
```

The method/quantity owner fixes value
and Cell policy once; source Ibis and retained pandas implementations meet the
same rule and independently prove supported types, checks and resource behavior.
Input kinds describe ordered slots and may repeat for two endpoints of the
same kind; the method still validates each slot's domain and binding.
A known failed precondition rejects before business I/O; a data-dependent
failure rejects during admitted execution, before successful publication.
Neither becomes a Cell `Unknown` or a backend fallback.

| Rule | Input → output | Pre | RequiredParts | PartTransform | Post and failure |
| --- | --- | --- | --- | --- | --- |
| Binding and projection | Governed source or exact Artifact binding, member domain, typed field/Metric refs, time scope and paths → bound domain and quantity | Same Session, owned refs, admitted source/path/time role and required columns | Identity/coordinate keys, selected fields and every hidden component needed by promised continuations | Project only unused physical columns; retain bound private state and provenance | Output keeps exact definition and input binding; reject absent or incompatible fields, path or state rather than infer from a displayed value |
| Domain mapping and correspondence | Domain plus governed single-valued mapping, subject image, exact-key pair or complete coordinate-tuple union → selected, grouped or paired domain | Unique applicable mapping; required coverage and multiplicity; exact pairing where the method requires it | Member/coordinate keys, mapping and coverage facts | Transport keyed rows and parts to the selected/target domain; preserve explicit empty target groups | Default groups use the actual complete image; explicit groups retain valid empty groups; reject missing/duplicate keys, ambiguous paths or unknown coverage, never fabricate Cartesian tuples |
| Cell and row calculation | Bound Cells plus registered predicate, difference or component finish → typed predicate or calculated Cell | Method-specific consumption, compatible quantity/unit/domain and finite numeric operands; full key pairing for first-round absolute compare | Tags, reasons, values and required endpoint/subject bindings | Preserve compatible endpoint and subject mappings; a Difference does not inherit original Metric fold authority | Preserve Defined/Null/Undefined/Unknown and distinguish absent rows; reject inadmissible operand states or failed calculation without silently dropping rows |
| Current-row state construction | Current relation rows plus registered sum/count/mean and target groups → new RowStatistic | Current-row unit and complete selected domain; sum/mean consume only finite Defined values | Current keyed rows, Cell tags/values and target mapping | Build new sum/count support from current rows; do not reuse original contribution state or claim recoverable members from a scalar | Empty valid group gives sum/count zero and mean Undefined(empty_mean) with valid (0,0) state; reject bad values, unknown coverage or int64 overflow |
| Original-state reduction | Observed quantity with its original state, target mapping and registered method → same quantity at coarser coordinates | Component-specific contribution partition/coverage, time order, method version and complete state | Every original component, support, null/empty and coverage part needed by that method | Merge each component's states before finish; retain permitted empty groups and transported dependencies | Ratio finishes after separate numerator/denominator merges; zero denominator is Undefined, while absent state or unproved overlap rejects |
| Part transport | Relation, bound parts and a selection, comparison, projection or reduction → exact remaining parts and continuation set K | Ownership, receipt, keys, method version and the requested continuation's premises | All parts promised for the output K | Restrict or transform parts by explicit key/binding, not row position; remove invalid promises when a part is lost | Output K reflects actual retained authority without upgrading a declaration or pending check to evidence; reject missing/mismatched parts or binding |

The first-round numeric method policy admits int64/float64 inputs; count yields
int64, integer sum checks int64 overflow, and mean/ratio/coefficient yield
float64. Nonfinite values fail method admission or consumption. Decimal may
round-trip through the exchange codec but is not a first-round numeric
algorithm input. The method and Cell policy versions agree across the DuckDB
and pandas routes, but float64 accumulation follows each route's evaluation
order and algorithm; equal inputs do not promise bit-identical sums or means.
Comparisons between routes therefore check the admitted semantics and use
appropriate numeric tolerances rather than exact float64 equality. When J1
retains coordinate sum state, its partition check requires exact int64 sums or
float64 sums within relative and absolute tolerance `1e-12`; a numerically
unstable float64 partition outside that bound is rejected before publication.
A Defined Cell has a valid typed payload; Null is a present
missing source value, Undefined is a method result with its reason, and Unknown
retains its distinct uncertainty reason. A missing domain row is none of these.
The inactive `MethodContract.cell_reasons` owns the closed reason IDs for each
non-Defined tag. Exchange validation requires a null payload and one allowed
reason for those tags; Defined requires a non-null payload and no reason.
Ordinary comparison, numeric sum/mean and categorical grouping consume
strictly; `is_defined` is total over all four tags, while `all_of`/`any_of`
cannot short-circuit a failing operand's required check. A complete empty
contribution follows the quantity's declared policy: J1 Revenue sum yields
Null, while current-row sum/count yields zero. Unknown coverage, missing keys
and missing state never establish an empty contribution.

`summarize(count)` counts current rows even when their values are not Defined;
`summarize(mean)` gives each current row one vote. `rollup()` consumes the
original quantity's retained components and cannot change its method. An AOV
of customer ratios 1 and 100 with order supports 100 and 1 therefore gives
a customer mean of 50.5 but an original-state AOV of 200/101. Only the
explicit-component ratio has that rollup authority; a decorator body
containing division does not.

Same-Entity, no-lag Spearman retains its own association method policy: exact
complete-domain pairing precedes ordinary Null-pair exclusion, while
Unknown/Undefined are not silently removed. Average ranks and the registered
constant/insufficient-pair behavior apply to the complete eligible pairs.
Its output keeps pair counts, selected method/version and coefficient state.
The S0 rule acceptance does not qualify an adapter or a new public input
shape; that requires S3.

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
