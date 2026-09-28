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

The S4 J1–J4 Entity-domain relation variants use a separate public
`AnalysisContract`: `actions` contains typed receiver calls paired with exact
Help targets, filtered by the current variant and declared retained parts.
Logical relation repr points to `contract().show()`; materialized relation repr
points to `show()`. Materialized relation cards combine bounded contract facts
and committed row previews while masking member identity values. A missing
physical backing still requires explicit Artifact integrity inspection and
blocks the attempted retained read.

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

S4 P1 exposes the admitted J1–J4 method subset through typed domain and
relation receivers. Public `mv.sum()`, `mv.count()` and `mv.mean()` are closed
current-row method values; `mv.route()` and `mv.routes()` bind two distinct
contribution roots to their ordered relationship paths. `summarize` consumes
the current rows and constructs new row-statistic state, while `rollup` merges
the original retained components. A materialized result can construct only
continuations justified by its own retained parts and exact input binding.
These public operations use the method rules and implementation registrations
below; they do not introduce a second method policy or source adapter.

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
same-member Entity observations with distinct time scopes. Its result is a
Difference; source and pandas routes require exact complete key pairing and
finite Defined int64/float64
Cells. Both endpoint rows are retained as separately receipted parts, so a
fixed pair uses the same member realization binding rather than inferring
common ownership from equal keys. Relative change, group comparison and
Difference selection/continuation are not qualified by P1.

S2 P2 qualifies private Difference `where` with finite, lossless
`lt/lte/gt/gte/eq` thresholds. Every input Cell must be Defined and finite;
filtering never converts a non-Defined row to false. The selected relation
retains its Difference quantity on a selector-bound Entity subdomain and
transports both endpoint parts by member key. `members()` projects identity;
current-row sum/count/mean construct new statistic state from the actual rows.
For float64 values, `eq` is exact binary equality with no tolerance. This
private slice admits one numeric predicate: a selected Difference offers
`members()` and current-row statistics, without a second `where` field handle.
The same source and pandas routes validate the selected endpoint receipts and
method checks. Difference has no original Metric rollup permission. Numeric
selection does not change the Revenue policy that an empty contribution is
Null; J2's zero Cells come from explicit zero-valued source contributions.

S2 P3 qualifies an explicit sum/count ratio with separately declared
component time axes, complete source paths, ignore-Null/zero-empty numerator
policy and explicit zero-denominator policy. Each contribution root is
aggregated before the full member-and-coordinate tuple union. A missing
component tuple yields empty state only after source scope, path and coverage
checks prove it has no contribution. Retained numerator and denominator states
are distinct keyed parts; `rollup()` merges each original state before ratio
finish, whereas `summarize(mean)` counts current result rows. A zero denominator
is Undefined(`zero_denominator`) under the admitted explicit policy; other
policies require a separately qualified method. An absent or corrupt component
is neither. P3 qualifies DuckDB/Ibis
source execution and exact Artifact-to-pandas continuation only.

The [first-round DSL slice](python-analysis-design.md#accepted-s0-analysis-dsl-slice-inactive)
accepted the following private rule obligations at S0. S4 activates only the
qualified J1–J4 subset through the public relation variants. Each registered
method supplies:

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

### S3 P1 private Spearman source qualification

The inactive J4 slice now admits two ordered Entity observations built from one
explicit member node and one fixed time scope. The second observation may use a
declared `ms.count` Metric: its valid empty contribution is a Defined int64 zero.
This count route is qualified only as a Spearman source input; it is not an
independently publishable J1 result or an original-state continuation.

The DuckDB/Ibis source stage realizes members once, checks unique and complete
endpoint keys, and transports both checked Cell relations through the existing
batch boundary. The Association owner excludes ordinary Null pairs after exact
pairing, rejects Unknown/Undefined and nonfinite values, and reuses its average
rank, coefficient and no-valid-candidate rules in Python. The private result
retains ordered Metric identities, method version, status and matched/null/
complete-pair counts. P1 does not qualify Artifact publication, pandas fixed
inputs, cold recovery, public signatures or another source backend.

### S3 P2 private Association publication and continuation

The J4 source result now uses the existing Run, fixed-schema Parquet, receipt,
and exact Artifact recovery path. Its committed row retains ordered Metric
identities, valid method status, float64 coefficient, and input/matched/null/
complete-pair counts. A keyed `pair_counts` part duplicates the committed
counts and must match the primary row on recovery. The exchange binds method
version, completed pairing checks, member realization, and input execution key;
an incomplete or inconsistent result cannot publish successfully.

The private coefficient handle admits a numeric `where` and current-row
`sum`/`count`/`mean` over the exact committed input. These continuations read
receipt-checked Parquet through pandas; they neither recompute Spearman nor
recover Entity members from the coefficient. An empty selected set has count
and sum zero and mean Undefined(`empty_mean`). Source evaluation obtains a new
Run identity each time; an identical pure fixed input may hit its exact
Artifact. The fixed two-observation qualification uses a controlled fixture
with one shared member implementation and the normal codec/Store path. Equal
keys from independent captures do not establish common authority. This does
not introduce a public capture or Analysis DSL entry point.

### S3 P3 private DuckDB Spearman numerical qualification

The same J4 input and Association policy also admit a DuckDB/Ibis numerical
route after complete-domain and Cell checks. This route computes average ranks
over all eligible pairs, then the coefficient and counts in the source. Its
output must satisfy the same method version, status, schema, parts and exchange
checks as the Python route. Source numerical execution is qualified only for
the tested same-Entity, no-lag int64/float64 slice; a compilable expression
alone does not qualify another backend or a different Cell policy. An explicit
private route choice supports comparative validation; ordinary placement uses
the registered source route when available and never retries a failed source
execution in Python.

## R4.1 frozen method state and evidence target (inactive)

R4.1 freezes the new Artifact method-state contract before R4 changes writers.
The current J1–J4 `dsl.j1.*` part IDs and `dsl.j4.pair_counts` are migration
inputs, not aliases in the new protocol. A
`marivo.analysis.method_state/v1` envelope has exactly `schema`, `kind`,
`contract_id`, `contract_version`, `method_name`, `method_version`,
`input_binding` and `ordered_part_roles`. The kind is one of the closed rows
below; `contract_id` is `marivo.analysis.state.<kind>` and every kind starts
at contract version 1. Each required part has contract ID
`marivo.analysis.part.<kind>.<role>` and version 1. `method_name`/version must
resolve through the one semantic registry, including the Association method
that R4.3 must connect. Equal physical columns cannot transfer authority
between kinds. An unsupported kind or version rejects before part rows are
read. There is no arbitrary dictionary payload or absent-field mega-class.

| State kind | Required keyed part roles and state components | Continuation premise |
| --- | --- | --- |
| `none` | No state part; only the complete primary relation and its receipt. | Only methods derivable from its actual relation signature. |
| `original_sum` | Separate `original_state` (`state_sum`, `non_null_count`, `row_count`) and `coverage` parts, keyed to the original member/coordinate identity. | Original `sum@v1` rollup requires completed coverage and contribution-partition checks. |
| `row_sum` | `row_state` with `current_sum`. | Current-row sum only; no original contribution authority. |
| `row_count` | `row_state` with `current_count`, including non-Defined rows where count-all applies. | Current-row count only. |
| `row_count_defined` | `row_state` with `current_count` of Defined Cells only. | Defined-row count only; equal columns do not confer count-all semantics. |
| `row_mean` | `row_state` with `current_sum` and `current_count`; empty support has its explicit reason. | Current-row mean only; does not inherit original-state rollup. |
| `ratio` | Distinct `numerator_state` (`numerator_sum`, `numerator_non_null_count`, `numerator_row_count`), `denominator_state` (`denominator_count`, `denominator_row_count`) and `coverage` parts. | Merge each original component before ratio finish; missing component is not zero. |
| `difference` | Ordered `current_endpoint` and `baseline_endpoint` parts with the same verified member binding and complete keys. | Difference selection and current-row statistics only; no Metric rollup. |
| `spearman` | `pair_counts` with ordered Metric keys and input, matched, Null and complete-pair counts, independently matched to the primary coefficient/status. | Coefficient selection/current-row statistics only; no recomputation of ranks or member recovery. |

Every primary and part uses its own
[`receipt/v1`](session-state-and-runtime.md#r41-frozen-runtime-and-store-target-inactive)
with complete ordered keys, physical schema, cardinality and file hash.
`none` has an empty ordered part-role tuple; all other variants require exactly
the listed roles in the listed order. A method that creates only a subset must
publish a distinct truthful state kind and a correspondingly smaller K, or
reject; it cannot fill missing roles with nulls or reconstruct them from
displayed values. R4.3 qualifies the physical component types and validates
batch splits and reordered rows by full key, not row position. The first-round
numeric algorithms remain limited to their individually qualified int64 or
float64 shapes; Decimal transport does not qualify Decimal arithmetic.

The check-evidence record is also closed. Its common fields are
`(origin_node, check_id, scope, ordered_input_occurrences, deadline, status)`;
only the `completed` variant adds `(producing_run_ref, result_digest)`.
`status` is `static`, `pending` or `completed`. A static
declaration does not satisfy a source-row check, and an unexhausted stream
cannot provide completion. Shared explicit nodes retain one realization and
one corresponding check input group; independent equal-looking nodes retain
separate groups. The descriptor commits only evidence actually completed in
its Run. Dynamic K is derived after the state variant, every required part,
its binding and applicable completed checks validate; the frozen continuation
snapshot supplies facts, not additional authority. R4.2/R4.3 must reject any
J1–J4 route lacking this registered method and its exact physical
qualification before business I/O.

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
