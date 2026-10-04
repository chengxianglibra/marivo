# Dataset Methods and States

For R5/R6 migration, the final R5.1 and R6 sections below own frozen target methods and
states. Earlier Dataset-family routes describe legacy consumers and do not
authorize parallel public R5 APIs or transfer their physical qualifications.

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
| Private funnel comparison | `event.funnel().compare(...)` | R7-owned complete journey contract; source admission remains blocked |
| Private funnel allocation | `funnel_delta.attribute(...)` | R7-owned loss-rate allocation; no Metric Delta variants |
| Association | `numeric.correlate(other, method=...)` | Typed descriptive association Result |
| Forecast | `numeric.forecast(horizon=..., model=...)` | Typed ForecastResult over a complete time grid |
| Statistical screening | `numeric.deviation(method=...)` / `numeric.runs(where=...)` | Explicit fit or complete-grid intervals |
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
requires explicit model/horizon and a complete approved time grid. Deviation
retains signed scores and its full fit scope; runs retains classification of every
grid Cell. The former Candidate producers and their implicit thresholds, peak
selection, sliding windows and driver-axis screening are retired. Agents choose
filters and explicit attribution axes through the corresponding typed receivers.

Exact median/percentile definitions use source-native continuous quantiles.
Define `approx_median` or `("approx_percentile", q)` explicitly for approximation.
The governed Metric owns the operation and q; source SQL arithmetic limitations
are disclosed. Unsupported exact operations report their corresponding approximate
definition without automatic substitution.

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

### R4.5 current public qualification

The typed graph registry and Store 7 supersede the historical S1–S4 physical
registrations described below. Public qualification is limited to the exact
DuckDB table and local Parquet source shapes and verified Arrow/Parquet-to-pandas
fixed routes recorded in the R4.5 acceptance ledger. Schema-only R1 inspection
may precede Run admission; business reads cannot precede physical qualification.
String member reads/grouping, int64 or float64 original sums, explicit sum-zero,
Entity count, int64 absolute Difference, original ratio and paired Spearman use
one graph execution entry. Other physical variants fail before business reads
and Run allocation. The R6.2 work in progress extends that existing absolute
Entity comparison path to homogeneous float64, same-scale Decimal and same-unit
Duration operands on DuckDB table/Parquet and fixed artifact_python. It retains
ordered endpoint Cells and an explicit exact-key correspondence under Difference
state v2. The broader R6.1 design/Union/relative-change/ordinary-ratio target below
is not yet public execution qualification. The R6 migration ledger records the
bounded evidence and remaining work.

Original sum, sum-zero, count and ratio have distinct v1 state contracts and an
`original_state` part plus coverage. Ratio stores numerator sum/non-null count
and denominator count together as named original components, rather than
separate numerator/denominator parts. Optional `coordinate_state` preserves one
or two ordered string coordinates and the complete original component tuple.
Ratio primary rows use the complete member-and-coordinate union; row statistics
consume those rows, while rollup merges original components. Subject and ordered
endpoint parts are retained only where the frozen method definition requires
them. Every advertised continuation requires verified receipts, exact binding,
state version and completed checks; it cannot be inferred from displayed values.
Public Spearman rejects coordinate-bearing endpoints. Fixed coefficient
selection and sum/count/mean execute from the verified coefficient and pair counts
without reopening sources or recomputing ranks.

### Historical S1–S4 implementation sequence

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

R4.3 uses these state kinds as private transient execution results. A producer
must validate each required part by its own full ordered key, schema and
binding, including when batches split or rows arrive in a different order.
An empty verified part is distinct from a missing part. The source and pandas
implementations of one registered method apply the same Cell, unit and state
policy; source preparation and source numerical Spearman are two qualified
physical routes under one Association semantic owner.

The private Spearman owner pairs by the complete Entity key, admits only finite
Defined or ordinary Null endpoint Cells, computes average ranks over complete
pairs and retains status separately from its `pair_counts` part. Its fixed
continuation verifies both selected primary and required part receipts before
scoring, and can reuse one explicitly shared fixed input without reading its
receipt twice. `insufficient_pairs` and constant-input states carry Undefined
coefficient Cells and their own status, not a fabricated zero coefficient.

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
[`receipt/v1`](session-state-and-runtime.md#r41-frozen-runtime-and-store-target)
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

### R4.4 durable method-state admission

The private v7 writer admits `none`, `row_sum`, `row_count`,
`row_count_defined`, `row_mean` and `spearman` only for their already registered
physical consumers. `none` requires no parts. The remaining frozen kinds above
stay rejected until their producing methods and recovery are qualified; an old
family codec cannot supply them. Each receipt's part ID is
`marivo.analysis.part.<kind>.<role>` at version 1, independent of the state
envelope's version. Source preparation plus local Spearman is one selected
method implementation, despite its two execution stages.

The persisted row-state statuses are the exact primary Cell tags, while Spearman
persists its explicit primary `status` column. These are the validated transient
status vector's durable representation; cold reads bind them by full key to the
required numerical parts. They never regenerate missing components or recompute
a numerical state. Contradictory main/part values reject both publication and
retained reads. State components keep the R4.3 physical column names
`row_state__sum`, `row_state__count`, `row_state__count_defined`, and
`pair_counts__*`; these implement the semantic components in the table above.

Only completed checks enter the descriptor, with the producing Run, original
node, scope, ordered input occurrences, deadline and result digest. Frozen
method/implementation selections reconstruct the check requirements, so a
missing, duplicate, foreign or mismatched completion cannot authorize a read.
Static signatures and transient pending obligations remain distinct.

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

## R5.1 method and state contracts

Status: frozen target, new R5 execution qualifications unverified. For migrated
R5 operations this section supersedes historical `aggregate`, `with_dimensions`,
`with_time_axis` and generic rollup/drop-axis guidance above. The only public
shapes are owned by [Analysis](python-analysis-design.md#r51-frozen-public-target).
`analysis.methods` owns semantic rules and exact physical keys. A legacy
registration, private synthetic qualification, successful compilation or equal
number does not confer public execution or continuation K.

### State and Cell matrix

Each row below is a distinct semantic method family at method version 1, state
version v1 unless an already published layout would change meaning. Existing
R4 encodings/IDs are preserved when identical. A changed layout receives the next
method/state version, never reinterpretation of v1 or a new Store generation.
These are semantic component names, not a second codec schema: their physical
columns and bound part roles are registered by the common graph protocol.

All state carries exact quantity/contribution identity, input binding, full output
keys, coverage, Cell policy, type and method/state versions. Component state and
primary output agree under finish. A missing state is never a valid empty state.
A semantic allowance below still requires a qualified physical implementation.

| Method family | Input Cell / empty policy | Sufficient state and required parts | Permitted original-state K |
| --- | --- | --- | --- |
| Metric sum | Declared Null/empty policy; ignore Null only when declared; admitted empty zero or Null is explicit | sum, non-null count, row count; original contribution/coverage and component part | Merge sum/counts across proven disjoint contributions, then finish under same policy |
| Metric Entity/Measure count | Entity count counts represented identities; Measure count follows declared non-null policy; empty 0 | checked int64 count, row count and exact counted unit | Merge counts only over disjoint original contributions |
| Metric min/max | Declared non-null support; empty/all-null follows Metric Null policy | typed extremum plus non-null and row counts | Same extremum method over admitted contributions; temporal restrictions still apply |
| Metric mean | Declared non-null support; empty/all-null follows Metric Null policy | sum, non-null count, row count | Merge sum/counts, never means |
| Metric weighted mean | Same non-null value/weight pairs; existing Metric zero/missing-weight-sum policy | weighted sum, paired weight sum, paired and row counts, original value/weight refs and units | Merge original paired components; no substitution of current-row weights |
| Metric ratio | Named numerator/denominator policies; zero denominator Undefined or error as declared | Every named original component with its own state, binding and coverage | Merge components independently then finish; no sum/mean of finished ratios |
| Metric linear | Ordered signed components, compatible units and explicit component policies | Every ordered occurrence's state and sign | Merge components independently then finish; retain branch distinctions |
| Metric occurrence combine | One occurrence per canonical component with its own root, filter, route and time range; no cross-occurrence Cell substitution | Every occurrence's original component state, coverage and complete target key; no retained set/sketch promise | Merge each occurrence independently, then combine on the complete target key; never a per-column projection product, root intersection or row-order alignment |
| Current-row count | Every current instance, including Null/Undefined/Unknown; empty 0 | checked int64 count, current instance unit/domain | RowStatistic count-state merge on disjoint retained row contributions |
| Current-row count_defined | Inspect Cell tag; only Defined counts; empty 0 | checked int64 count, current instance unit/domain and original tag policy | Merge this statistic's counts, never recast as count_all |
| Current-row sum | All consumed values Defined and finite; admitted empty 0 | sum and row count, new RowStatistic identity | Same statistic's sum-state merge, disjoint current-row contributions |
| Current-row min/max | All consumed values Defined and finite; empty Undefined(empty_min/empty_max) | optional extremum and row count | Same statistic's extrema merge; empty state is neutral |
| Current-row mean | All consumed values Defined and finite; empty Undefined(empty_mean) | sum and row count, including valid (0,0) | Same statistic's sum/count merge; Undefined empty Cell is not a zero input to summarize |
| Direct count_distinct | Declared value identity; Null excluded; empty 0 | Final value and input/method evidence only; no retained set/sketch promise | No original rollup or attribution |
| Direct median/percentile | Finite non-null values; Metric empty policy; exact linear interpolation | Final value, q, defined operation and actual algorithm/precision evidence; no distribution/sketch promise | No original rollup or attribution |
| Semi-additive time fold | Per declared spatial-before-time order, sample/time policy and coverage | Exact ordered evaluation keys plus pre-fold components sufficient to restore that order; method identity includes first/last/min/max/mean/percentile | Only qualified fold/reduction with retained state and order/disjointness proof; finished values alone insufficient |
| Cumulative | Each endpoint consumes [anchor(e), e), independent of display start | Endpoint, anchor, base components, interval coverage and ordering | Spatial merge with matching endpoints/base policy; no summing overlapping cumulative endpoints |

Subject/member maps, classification maps, full coordinate tuples and temporal
parts are required whenever the successor consumes them; scalar components alone
cannot reconstruct them. Original contribution overlap or non-commuting folds
reject absent a registered proof/restoration method. A RowStatistic is a new
quantity and cannot acquire the original Metric's units of contribution or K.
Current-row weighted mean and its named StatisticalWeight surface are withdrawn
from R5 requirements; existing private rules do not activate them.

### Numeric target matrix

Required R5 value families are signed int64, finite float64, Decimal(p,s) with
1 <= p <= 38 and 0 <= s <= p, and fixed-duration values in s/ms/us/ns. Boolean
is not numeric. Every qualification records the exact input/output/state type,
not merely the family name. Invalid values and overflow reject before publication;
there is no saturation, wrapping, silent float coercion or route retry.

| Method | int64 | float64 | Decimal | Duration / date / timestamp |
| --- | --- | --- | --- | --- |
| count/count_defined/count_distinct | checked int64 result/count state; count_distinct retains full declared identity | Same count result; nonfinite distinct input rejected | Exact decimal equality for distinct, checked count | Duration distinct preserves unit and exact ticks; date/instant distinct preserves typed identity; count methods may count these relations without numeric coercion |
| sum / linear | checked int64 output/state | finite float64 output/state | Decimal(38,s), exact sum; equal scales required within an occurrence | Duration sum/linear preserve tick unit with checked int64 ticks; date/timestamp numeric sum rejected |
| min/max/first/last | preserve input type | preserve finite input type | preserve (p,s) | Duration preserves unit; time-valued read/selection preserves physical type, no timestamp-to-float numerical aggregation |
| mean | exact integer sum/count state; finish to float64 once | float64 sum, checked count, finite float64 finish | sum Decimal(38,s), count int64; finish Decimal(38,max(s,6)) | Duration mean uses exact tick sum/count, rounds once to nearest tick, ties to even; date/timestamp mean rejected |
| Metric weighted mean | checked int64 product/sum/weight state, float64 finish | float64 products and sums, finite finish | matched Decimal value/weight types; numerator scale s_value+s_weight <= 38, denominator scale s_weight; Decimal(38,max(s_value,6)) finish | Duration values with int64 nonnegative weights: exact checked tick products, nearest-even tick finish; timestamp weights/values rejected |
| ratio | checked component states, float64 finish | float64 components/result | Decimal components; output Decimal(38,max(s_num,s_den,6)) | Same-unit Duration ratio uses exact tick components and float64 finish; mixed calendar/elapsed units or timestamp division rejected |
| source-native median/percentile | native continuous quantile; float64 output may lose large-integer precision | native continuous quantile, finite float64 output | native continuous quantile with source-owned output precision/scale; precision loss is disclosed | Duration/date/timestamp quantile rejected in this R5 direct-observation slice; no implicit float route |
| semi-additive / cumulative | Base method's matrix plus exact time keys | Base method's matrix plus exact time keys | Base method's matrix plus exact time keys | Temporal keys retain physical precision; cumulative Duration sum follows sum, calendar months cannot become fixed seconds |

Duration is a closed physical type with an explicit s/ms/us/ns tick unit in the
graph, execution key and receipt. Local Parquet sources preserve Arrow Duration
metadata and int64 ticks; DuckDB native INTERVAL is admitted as microseconds only
after a source-native check rejects year/month/day components. Native INTERVAL
does not invent nanosecond precision. No fixed-duration operation casts ticks to
float or turns calendar intervals into elapsed time. Decimal state
uses exact intermediate arithmetic; each stored sum/product is checked against
its declared precision and scale. The admitted input scale is retained, not
rounded on ingest. Decimal finish rounds once using ROUND_HALF_EVEN at the stated
result scale and rejects integer-part overflow. Excess product scale, unsupported
cross-family pairs, and differing Duration tick units reject explicitly; the
first slice does not add automatic rescaling. Integer accumulation is exact
before the checked final state, so batch order cannot cause wrapping. A declared
integer state that cannot represent the mathematical sum rejects even when a
later ratio could be finite. Count overflow also rejects.

For int64/rational-to-float finish, use nearest representable float64 once;
large input integers must not first become floats. Decimal and Duration compare
exactly after their stated single rounding. Selection/extrema/counts are exact.
Float64 sum/mean/weighted/ratio/linear and interpolated quantiles use finite
outputs. Let r be the independent exact result and R(r)=1e-12*(1+abs(r)).
The absolute-error bound for a sum is E=sum(abs(contributions))*1e-12+1e-12;
for a mean it is E/count+R(r). For ratio with component bounds E_N/E_D it is
(E_N+abs(r)*E_D)/(abs(D)-E_D)+R(r); a denominator interval spanning zero rejects
that physical qualification. Weighted mean uses the same ratio rule over exact
paired products and weights. Linear propagates component bounds plus R(r).
Interpolation uses 1e-12+1e-12*max(abs(adjacent ordered values)). Integer/Decimal
components have zero input error before their stated finish. Tests vary row order,
batching and reduction tree against Fraction/Decimal raw-fact oracles, not another
product path. An implementation unable to meet its bound remains unqualified;
approximate quantile uses its actual algorithm guarantee rather than this
exact-interpolation bound.

### Physical qualification obligations

For every required row, register exact method/version, ordered input types and
units, domain kinds, source form/table kind, time shape, route, checks, output
parts and implementation evidence. Native DuckDB table and DuckDB Parquet are
separate required source cells. The fixed counterpart uses artifact_python,
controlled Arrow/Parquet-to-pandas and no backend connection. SQLite table is
required for the existing authoring/window/calendar and first/last/mean/min/max
fold debt; at minimum qualify int64/float64 and the temporal kinds used by those
fixtures. Other SQLite numeric shapes and PostgreSQL/MySQL/Trino/ClickHouse R5
methods remain explicitly unverified under R9, not inherited from legacy SQL.

R5.6 source aggregation runs through Ibis-compiled SQL on the datasource. It must
not fetch contribution vectors for local quantile or distinct computation. Native
DuckDB distinct/quantile and explicit approximate definitions are separate method
identities. The Metric definition owns the operation; observation has no accuracy
override. Quantile precision loss from native arithmetic is accepted and disclosed. Nonfinite, overflow, missing coverage/state, ambiguous time,
wrong roles, bad versions, mixed source/fixed, and cross-Session are required
rejection cells. No matrix cell may be closed merely by adding a rejection for a
required accepted type. Fixed direct results permit current-row statistics but
not original distinct/quantile rollup; this is a supported boundary, not a missing K.


### R5.2 member and field physical consumers

`bind_project`, `parts_transport` and `map_correspond` continue through their
existing registered rule/method versions. The member/read slice adds native
boolean/date/aware-timestamp transport, direct int64/float64 Measure reads,
complete-key correspondence and local fixed Subject images. A root member
projection trusts declared identity without distinct; repeated consumed keys or
matching versions are errors. A non-injective Subject map instead produces the
set of complete subject tuples after input validation. This operation does not
repair invalid input instance identities.

`source.single_value@v1` for attribute paths is executed on the scoped field
result; the common exchange retains the resulting evidence. Field-owner absence
is checked separately from null values. Scalar read creates no original Metric
state and therefore grants no original `rollup` capability. These stateless
results keep state kind `none`; the existing row-statistic and observation
qualifications are not expanded by matching numeric output values.

### R5.3 observation and runtime expression consumers

Observation inputs are the closed union of a Metric Ref and a RuntimeMetricExpr. Each canonical `TargetMetricComponent` becomes its own
occurrence with an independent contribution root, filter, route, time range,
unit and amount type; a component's own `filter` is no longer a reason to reject
an observation, and same-root occurrences under different filters stay distinct
rather than merging. `aggregate`, `slice`, `ratio`, `linear` and `weighted_mean`
all reach the common graph: `weighted_mean` binds through its existing canonical
node variant, `linear` combines ordered +1/-1 occurrences, and `ratio` keeps its
named original components and finish/zero-denominator policy.

Occurrence state is transported under the existing `components`/state-version
mechanism: `original_state` requires the component's declared state tuple, and a
`weighted_mean` occurrence requires the paired value/weight components and the
declared zero or missing weight-sum policy. An opaque Metric contributes only its
declared permissions and actually available state; components are never inferred
from a function body, a result column name or numerically equal values. Combining
occurrences of unequal fact grain is admitted only when each occurrence reduces
first; the same fact table under two filters is never multiplied or summed across
branches before its own reduction.

Diagnostic or unregistered engine versions cannot alter selected route, exact
observation definition or any published state. No compatibility route, forwarding
alias or generation-6 reopening is introduced by this slice.

The R5.3 review regressions additionally qualify sum/sum and count/count ratios,
negative sum denominators, and three independently reduced roots. Empty component
policies survive combination: a count of zero is Defined, not an empty-null sum.
Runtime `aggregate(..., agg="sum")` resolves the contribution Entity's explicit
unambiguous default UTC event axis before observation admission; this applies to
both omitted and finite windows. Missing default axes still reject, without
choosing a time field by name or order.


The R5.3 executable qualification is bounded: direct int64 value/weight columns
for Metric weighted mean, int64 sum/count leaves for ratio and signed linear,
DuckDB table/Parquet with UTC instant-us time, and the existing route lengths.
Weighted mean retains `(weighted_numerator, weight_sum, non_null_pair_count,
row_count)` from rows with both operands non-null. No pairs yields
`Null(empty_contribution)`; a contributed zero weight sum yields
`Null(zero_weight_sum)`. Source and fixed rollup merge these four components,
never average member means. R5.4 connects retained string contribution-coordinate tuples for this same paired state.
Nested linear sum/count expressions distribute signs while retaining every
occurrence and its empty policy; source and fixed rollup merge each original
component. An outer slice over ratio/linear is pushed to each leaf by canonicalization. Nested nonlinear finishes and ratio `zero_division="error"` are not
qualified by this slice and reject; descriptor construction does not grant an
execution method. Composite member identities join and reduce on every key.

`LogicalAnalysisDomain.observe` accepts omitted `during` in its static signature.
Its return is the numeric/ratio relation union because a Metric Ref's kind is
resolved from the catalog, not inferred from whether `via` has one or many roots.
Use `isinstance` to narrow before family-specific continuations. Linear and
weighted mean produce the numeric family; ratio produces the ratio family.

### R5.4 coordinate and row-state consumers

The common graph adds `group.attach` and `group.complete` to the existing
parts-transport rule. Attachment preserves every receiver Cell and component;
completion only fills proven empty target tuples. Both validate transported
numerical state against the owning method. `OriginalReduce.coordinates` is an
ordered tuple of complete retained axes. Signed linear and weighted-mean
coordinate partitions retain their respective full component schemas.

Current-row states are count `(count)`, count_defined `(count)`, sum `(sum,count)`,
mean `(sum,count)`, min `(min,count)` and max `(max,count)`. Count includes all four
Cell variants; count_defined includes Defined only. Empty sum/count/count_defined
finish as zero; mean/min/max finish as Undefined with `empty_mean`, `empty_min`
or `empty_max`. An empty mean's valid state is `(0,0)`, not an absent state.
Row-state merge preserves the RowStatistic identity and exact input-domain
binding. Numeric reducers reject non-Defined inputs rather than silently dropping
rows. Mean Metric state is separately `(sum,non_null_count,row_count)` and its
original merge does not average member means.

L8 compares merged components as well as values and semantics. L9 uses the same
explicit target domain, including empty tuples, on both paths. Qualified fixed
continuations use the caller-owned Python route and Store 7; exact receipts,
component correspondence and state versions remain mandatory. No statistical
weight role, time grid, numerical-matrix or installed-wheel acceptance is added.

R5.4 group-domain completion also accepts projected Group/Singleton inputs with
no quantity or parts. It checks target uniqueness and consumed-key containment,
then publishes target keys only; it does not invent Cells or numerical state.
Selection transports the category coordinate in `PartsTransport.classification`
so fixed selected categories can group without loading Semantic. Numeric field
predicates carry their relation binding to merge independently rooted source
schemas into the existing graph executor.


### R5.6 numeric execution and state

DuckDB table/local-Parquet original sum, extrema, mean, Metric weighted mean,
ratio, linear and typed temporal folds use the matrix above. Source arithmetic is
compiled by Ibis: Decimal finishing uses native integer quotient/remainder with
one HALF_EVEN rounding; int64 ratios use native integer mantissa rounding before
binary64 conversion. Duration mean/weighted mean round once to nearest-even ticks.
These are selected SQL routes, not contribution collection or failure fallback.
Only verified retained states use Python arithmetic during source-free continuation.

Coordinate components preserve their exact numeric carriers and complete keys.
Float64 sums retain the sum of absolute contributions; ratio retains the
denominator absolute sum and weighted mean retains the paired absolute weight
sum. These additive state fields survive coordinate reduction and source-free
rollup. A nonzero denominator whose error interval includes zero rejects before
publication; the existing empty and zero-denominator Cell policies remain.
Missing or nonfinite absolute state rejects continuation. Decimal temporal sample
sums decode as Decimal(38,s), and Decimal range checks never round their operands.
Typed temporal samples preserve integer/Decimal digits. Original sum, fold, mean,
weighted mean, ratio, linear and changed extrema consumers use implementation
contract version 3 in Store 7. Earlier implementation versions cannot continue and are
not migrated or reconstructed. Public result state exposes `decimal:p:s` and
`duration:unit` physical identifiers. This qualification does not extend the R5.5
SQLite matrix or remote R9 backend coverage.

## R6.1 method rules and qualification target

Status: frozen C07–C09 target, not execution evidence. Public shapes are owned by
[Analysis](python-analysis-design.md#r61-frozen-relation-composition-target);
state encoding and version transitions by [Runtime](session-state-and-runtime.md#r61-composition-state-and-recovery).
The following are required semantic methods, not a parallel registry. Existing
method names are extended under analysis.methods; new names below are their
frozen target identities. New methods start at semantic version 1. Changed
existing definitions use the version transition described by Runtime.

### Method, parts and continuation matrix

All methods require exact ordered input bindings, full typed keys, input Cell
policies, units, and actual physical qualification. Every transported part is
restricted by the same key mapping as its primary; a value-only result cannot
claim a continuation that requires a missing part. In this table S means strict
selection and admitted current-row statistics; F means source-free fixed
continuation with all required parts. Statistics retain R5's actual type matrix:
Decimal/Duration sum/mean/min/max are not activated by this table.

| Method identity | RequiredParts and consumption | Output / permitted K |
| --- | --- | --- |
| map_correspond (ExactKeys, UnionKeys, one-to-one, period variants) | Complete ordered typed keys; design and target binding; one-to-one relation/time evidence or complete bucket map as applicable | Exact matched/missing-side map and original endpoints; correspondence is not original-state rollup |
| cell.difference | Correspondence, ordered endpoint Cells and recursively frozen quantity templates; matched values Defined/finite | current−baseline; endpoints, presence and policies retained; S/F, members only with Subject map, attribute only under its separate rule |
| cell.relative_change | Same as difference | (current−baseline)/abs(baseline); dimensionless; zero baseline Undefined(zero_baseline); S/F, no automatic attribution |
| cell.ratio | Exact or bound one-to-one correspondence; ordered endpoints and quotient-unit proof | Zero denominator Undefined(zero_denominator); explicit missing-side yields Undefined(missing_side); S/F, never original rollup/share |
| parts_transport (where/view/limit/member projection) | Every referenced predicate input, exact same-domain or retained inclusion mapping, Subject when requested | Same quantity and precisely restricted parts; membership retains selection basis, no reread/reselection; K recomputed from retained proof |
| domain.cohort | Full target Subject domain, complete opportunity domain and coverage, SubjectBinding, all predicate inputs | Exact selected Subject set plus quantifier/decision evidence; F, source observation only for source members, no unknown qualification silently dropped |
| reference.share | Same-measure Singleton reference, support inclusion and additive/allocation proof; full-partition proof only if claimed | Same-key dimensionless share with frozen reference; S/F, no original rollup |
| reference.penetration | Complete member reference Ω, selected member set B, exact identities and intersection | Singleton intersection-count / reference-count; empty reference Undefined(empty_reference); S/F |
| reference.standardize | ReferenceWeights, exact complete unique strata, compatible statistical Entity proved from original Metric/runtime Metric sum/count/linear/mean/weighted_mean/ratio | Singleton weighted standardized value and frozen strata/reference; S/F, no original rollup |
| display.rank | Numeric input and exact Category partition maps, full original ranking domain | Same-key values/ranks plus deterministic order, partition/tie policy; S/F and global limit, no recomputation on selection |
| display.table | Ordered complete-key Relations, exact input/view bindings and key proof | Terminal table; export/read only, no analysis K |
| attribution.additive_difference | Absolute Difference with complete endpoint states, additive partition/allocation proof, axes, coverage and target reproduction | C_i−B_i, allocated side views and scope/resolution reconciliation; S/F and ranking views; filtered output loses complete-partition K |
| attribution.component_mix | Absolute Difference over original mean/weighted_mean/ratio; per-side additive N/W, complete partition, valid totals and original policies | N_i/W_total side terms, their difference, independent scope/resolution checks; same K restrictions as additive attribution |

Reference-weight construction is a pure binding operation, not a second
arithmetic method. Predicate methods are closed tag/scalar/composite variants
consumed by parts_transport or domain.cohort. Their result is a predicate, not
an independently published Relation. Logical attribution axis expansion adds
explicit observation dependencies; Materialized expansion uses retained parts
only. Relative or nested Difference, ordinary ratio, standardized quantities,
distinct and quantile do not receive attribution merely because endpoints exist.
Additive original linear components may qualify only with complete additive
partition and endpoint reproduction; no general FormulaBasis is introduced.

ExactKeys requires injective full typed keys and equal images; double empty is
valid. UnionKeys also checks injectivity. Keep preserves MissingCoordinate
separately from Present(Null/Undefined/Unknown) and does no arithmetic on a
missing row. metric_empty requires complete original observation/coverage and
the concrete Metric's empty finish; filtered/ranked/limited or missing-version
rows are not empty contributions. It never overwrites a Present non-Defined
Cell. A synthesized Defined endpoint participates normally; a synthesized
Null/Undefined retains that tag and original reason in the result (no arithmetic).
Unknown coverage cannot authorize synthesis. The existing side still must pass
strict numeric consumption. This explicit Null case follows R5 sum/mean empty
policies; historical prose mentioning only Undefined is not a fill-zero rule.

### Predicate and cohort consumption

Before selection, every actual input must correspond to the receiver's complete
consumption domain; a retained inclusion may restrict an ancestor input but
cannot supply missing rows. All child checks run before truth composition.
where's ordinary numeric predicates require Defined finite values. Category,
Boolean and Temporal comparisons require Defined values of the stated type;
there is no implicit truth conversion. is_defined is total on the four Cell tags
and returns false for Null/Undefined/Unknown. No tag check masks another child's
consumption error. Thus all_of(x.is_defined(), x.gt(0)) fails on Undefined while
an explicit first selection followed by a comparison on the selected view can
succeed. L1 applies only to the common fully defined domain.

cohort uses the same type/finite/unit checks over the full opportunity domain.
Ordinary scalar comparisons may produce unknown for an existing Unknown Cell;
Null/Undefined remain hard errors. State predicates remain total. Missing keys,
opportunities or coverage are errors, not new Unknown Cells. AllOf is false if
any child is false, true if all are true, otherwise unknown; AnyOf is true if
any is true, false if all are false, otherwise unknown; Not preserves unknown.
These rules apply after every child passes its consumption checks.

For each target Subject let t/u/f count all true/unknown/false opportunities.
any_instance is true if t>0, false if t=u=0, otherwise unknown. at_least(k) is
true if t>=k, false if t+u<k, otherwise unknown. Both are false on complete empty
opportunity domains. Nonempty all_instances is false if f>0, true if f=u=0,
otherwise unknown. Empty all_instances follows its explicit true/false/undefined
policy. Every target Subject must have decidable qualification before producing
an exact AnalysisDomain; unknown or undefined rejects the whole result. Retain
targets with zero opportunities to evaluate that policy. These truth rules do
not grant global arithmetic on Unknown Cells or relax where.

### R6 numerical target and reference policy

R6 required source cells are DuckDB native table and local Parquet, with fixed
artifact_python counterparts. A required cell below remains unverified until
its source/fixed tests pass; it cannot be closed by rejection. I=int64, F=finite
float64, D=Decimal(p,s), T=fixed Duration(s/ms/us/ns). Homogeneous operand families
are required unless explicitly stated. Boolean/date/timestamp are not numeric.
Decimal and Duration physical facts, exact state arithmetic and one-rounding
rules remain those of R5. No cross-family coercion, Decimal rescaling or Duration
unit conversion is implicit.

| Method | I | F | D | T |
| --- | --- | --- | --- | --- |
| absolute difference / additive attribution | Checked int64 difference and published state | Finite float64 difference | Same-scale inputs; exact Decimal(38,s) result/state | Same-unit exact checked ticks, unit preserved |
| relative change / ordinary ratio / share | Exact integer numerator/denominator until one float64 finish | Finite float64 finish with denominator stability check | Decimal(38,max(s_left,s_right,6)), one HALF_EVEN finish | Same-unit tick ratio, exact until float64 finish; relative change likewise dimensionless |
| scalar/state predicates | Exact comparisons; bool excluded | Finite exact binary64 comparison | Exact equal-scale comparison | Exact same-unit ticks |
| ranking | Exact ordering, int64 defined ranks | Finite value ordering, int64 ranks | Exact decimal ordering, int64 ranks | Exact tick ordering, int64 ranks |
| standardize | I values with I/F dimensionless weights, float64 finish | F values with I/F weights, float64 finish | D values with same-scale D weights within weight input; Decimal(38,max(s_value,6)) finish | Rejected in R6: no standardized Duration quantity method |
| component_mix | Exact N/W until float64 side finish, float64 contributions | Finite float64 side/contribution | Exact Decimal components; Decimal(38,max(s_N,s_W,6)) side/contribution | Rejected in R6: no tick-rounded component allocation method |
| penetration / cohort counts | Checked exact int64 identity counts; penetration finishes float64 once | Not a numeric-input algorithm | Not a numeric-input algorithm | Not a numeric-input algorithm |
| table / transport | Preserve | Preserve (reject malformed nonfinite Defined payload) | Preserve | Preserve |

These restrictions do not withdraw R5 Metric mean/weighted-mean/ratio over
Duration. They deny only the new standardization/allocation methods above;
Duration difference, ratio, predicates, rank and additive attribution have
required positive cells. Current-row numeric reducers remain R5-qualified only.

Integer/Decimal/tick intermediates must not pass through float. Mathematical
intermediate numerator/difference/score may use exact widened arithmetic; each
stored int64/tick state or output and every declared Decimal precision is range
checked. In particular abs(-2**63) for a score or denominator must not wrap.
Overflow, nonfinite values, unsupported scale and incompatible units reject
before publication. Negative baseline uses abs(baseline) only for relative
change; ordinary ratio keeps denominator sign. Zero policies precede division.
Float results use the R5 error model: propagate operand bounds through
subtraction, plus R(r); for division use its denominator-interval bound. Exact
integer operands enter with zero error and finish once. Float comparison/rank
operate on represented values without epsilon ties. Numeric qualification tests
use independent Fraction/Decimal or raw-fact oracles and vary row order/batching.

share requires support inclusion and compatible additive measure or admitted
allocated side terms. It promises [0,1] only with nonnegative terms and positive
reference; signed shares otherwise remain signed and make no such promise.
Zero denominator is Undefined(zero_denominator). Complete partition is an
independent proof, not inferred from a sum close to one. penetration counts
B∩Ω using complete identities, not overlapping category sums.

Standardization freezes the following choices. Every stratum key must exist
exactly once on both sides, including zero-weight strata. Weights must all be
Defined, finite, nonnegative and dimensionless. I/D weights sum to exactly one
using exact arithmetic. F weights use an order-independent exact sum of their
binary64 values for validation, accepted iff abs(sum−1)<=1e-12; the represented
weights are used unchanged, never normalized. The unit is the statistical-unit
Entity, distinct from measurement units. Empty strata and zero total reject.
Every positive-weight value must be Defined and finite. At exactly zero weight,
Null/Undefined/Unknown values remain retained but are not multiplied or consumed
as numbers; malformed/nonfinite Defined values still reject. Positive weights,
however small, get no epsilon exemption. The receiver quantity must explicitly
admit averaging comparable stratum values with one common measurement unit;
it cannot smuggle unlike denominators/units into a mean.

Products/sums for I/D use exact widened arithmetic until the single result
finish; checked stored D state preserves s_value+s_weight (<=38). F products
and sums propagate the R5 sum/product bounds and the weight-sum acceptance
error is disclosed separately, not corrected. The result retains all weights,
values, zero-weight statuses, keys and fixed reference identity. It is a new
standardized quantity; no original Metric merge or actual-population claim.

### Rank, attribution arithmetic and reconciliation

Ranking partitions by complete Category tuples; empty tuple means one partition.
Within a partition Defined finite values sort by requested order, exact ties by
canonical typed instance key. ordinal assigns 1..n; dense increments by one per
distinct value; min/max use the first/last occupied ordinal of each tied block.
Non-Defined values/ranks retain their tag and reason, sort after Defined values,
and tie-break by instance key (no artificial ordering of missingness severity).
Global output order is canonical partition tuple, Defined before non-Defined,
numeric rank, instance key. This is display order, not business event order.
limit takes its global prefix, integer 1..100000 excluding bool. Each partition's
Top-K requires explicit defined-rank filtering then rank<=k; dense/min/max may
retain more than k rows. Neither operation recomputes ranks or fixed references.
Canonical keys compare typed components in declared order, native exact numeric/
temporal order and Unicode codepoint string order; no repr/hash order. Typed
real-null and Other have separate tags; Other sorts after ordinary coordinates.

Attribution accepts nonempty unique ordered axes; hierarchy requires >=2 axes.
joint emits full tuples; hierarchy emits every authored prefix with resolution
in the key. Each scope/resolution is separately complete and reconciled. Both
endpoint quantities must reproduce from retained complete components before
allocation. Unknown coverage, overlapping contributions, illegal folds or
missing states reject independently of residual. Relative/nested changes do
not inherit these methods automatically.

additive_difference publishes C_i−B_i. component_mix publishes N_i/W_total on
each side, then their difference; it never subtracts unallocated group ratios.
Both overall endpoints must be Defined and totals valid under original policies.
Zero W_total rejects, including 0/0; for float a denominator error interval
spanning zero also rejects. A structural N_i=W_i=0 contributes zero; W_i=0 with
nonzero N_i rejects as contradictory, preserving the accepted typed-operators
rule rather than broadening it from algebraic cancellation. Negative basis is
permitted only where the original component policy admits it. Endpoint/partition
proof is independent of this numerical check.

top_k is None or integer 1..1000 excluding bool. Before arithmetic, select once
on the union of both sides' full basis using descending abs(C_i)+abs(B_i) for
additive, abs(W_i,current)+abs(W_i,baseline) for component_mix; typed coordinates
break ties. Multi-axis mapping follows authored order within each mapped parent,
including Other. A typed Other tag and mask distinguish a mapped remainder from
a real string "Other" or null category. Hierarchy reuses the same mapping;
never independently select each side or resolution.

For complete scope/resolution let D be the independently computed input target
and S the sum of published contributions. Additive I/D/T must reconcile exactly
in their exact carrier, without float conversion. Float arithmetic retains the
accepted threshold abs(D−S)<=max(1e-12,1e-9*max(abs(D),abs(S),1)); numerical-oracle
bounds above must also pass, so reconciliation is not a substitute for precision
qualification. Decimal component_mix checks exact rational component identities
before finish and applies that same threshold using exact Decimal arithmetic
to rounded published side differences; it does not widen the tolerance or cast
to float. Each side is rounded once at its declared scale, its contribution is
the exact difference of published side terms, and D is the reproduced endpoint
difference at that scale. Excess rounding residual rejects; no balancing row or
residual redistribution is allowed. Tests must include high-precision and
many-small-partition examples at this bound.

Selection transports original target/basis/rule, original reconciliation scope,
complete Other mapping and selected keys. It unconditionally revokes current
subdomain completeness, even if selected contributions happen to sum to D.
No shared helper, legacy registration or residual grants distinct_membership,
distribution_shapley or a second attribution executor public qualification.

### R6.3 predicate and cohort implementation

Bound relation predicates keep authored input order and exact field dependencies.
A selected receiver may consume an ancestor only with retained inclusion evidence;
extra ancestor rows are restricted before Cell checks. Other inputs require equal
complete keys. No implicit truth conversion or short-circuit consumption exists.

The Entity target cohort consumer constructs the expected complete target×grid
key image (or one Entity opportunity without a grid), validates every predicate
input against it, evaluates all scalar/tag leaves, and decides all targets before
publishing the selected set. `cohort_decision` v1 retains checked int64 t/u/f
counts and an explicit accepted flag for every target, including rejected targets,
the complete opportunity-domain authority, quantifier and empty policy. Exchange
validation requires the primary key image to equal exactly the accepted decision
keys; missing or inconsistent counts fail recovery.
The common exchange independently validates accepted decisions and total counts.
Journey/Interval/Anchor opportunity producers remain R7 work.

### R6.4 qualified fixed references

`reference.share`, `reference.penetration` and `reference.standardize` version 1
use the unified graph on DuckDB tables/Parquet (`ibis_python`) and verified
fixed Artifacts (`artifact_python`). Construction performs no business reads.
Share consumes an explicit original Singleton rollup of the retained additive
support; sum/count/linear qualify. It verifies each numerator's inclusion and
its original additive state independently of the denominator. Complete partition
means equality of complete support and numerator key images, never a near-one
sum. The [0,1] claim additionally requires nonnegative support and a positive
denominator; signed inputs have no such claim. Penetration uses complete Entity
identities, including composite keys, and set intersection even for overlaps.

Standardization admits original Metric/runtime Metric **sum, count, linear,
mean, weighted_mean and ratio** after proving the statistical Entity from their
frozen definitions. Sum/count use the contribution Entity; mean uses its sample
Entity; weighted_mean uses its paired contribution Entity; ratio uses its
original denominator component Entity. Every linear term must prove the same
Entity. Original grouping and retained inclusion preserve this proof. Ordinary
relation ratio, Difference, quantile, field reads and standardized quantities
have no original-state standardization permission. Duration remains rejected.
For totals/counts/linear, the result is the weighted value of stratum totals,
not an actual population total or a pooled original Metric.

Result cards independently disclose current complete/partial support and the
nonnegative-range proof for shares, the complete reference/intersection counts
for penetration, and the represented weight-sum deviation separately from the
arithmetic error bound for standardization. Selection preserves those original
reference inputs and the static reference interpretation in `contract()`.

The numeric matrix remains I/F values with I/F weights and D values with D
weights. Exact Fraction products/sums finish once to finite binary64; Decimal
finishes once with ties-to-even at Decimal(38,max(s_value,6)), with product scale
at most 38. Retained operand envelopes propagate through weighted products and
the final finish.
For a Defined stratum at zero represented weight, the product envelope still
includes `abs(value) * weight_error + value_error * weight_error`. Only a
non-Defined zero-weight stratum skips arithmetic consumption.
The complete represented weight sum is checked separately: I/D exactly one;
F within 1e-12, without changing any weight. Zero weights retain
non-Defined values; positive weights, however small, consume only finite Defined
values. Malformed Defined values reject even at zero weight. Public source/fixed
qualification and independent numeric/corruption tests are owned by
`tests/test_analysis_references_r64.py`. R6.5 ranking/Top-K and R6.7 wheel
qualification remain separate.

### R6.5 registered display execution

`display.rank@v1` and `display.table@v1` use `display@v1` in the common registry.
Ranking sorts exact represented int64/float64/Decimal values or Duration ticks;
float64 ties have no epsilon. Canonical partition tuples precede the Defined
rank blocks; each tied block and the non-Defined tail use complete typed instance
keys. Null classification is a distinct canonical partition, ordered first.
Ordinal, dense, min and max follow the frozen definitions above.

Ranking stores current values/ranks and independent full ranking_domain,
partitions and ordering parts. Selection applies one complete-key mapping to both
current views and any actual sufficient parts; the independent original scope
stays complete. Recovery reproduces rank and order from that domain, verifies
partitions and every current Cell, and rejects missing/duplicate keys or a
changed selection/order. A retained share denominator and quantity identity stay
fixed. No synthetic original-state or complete-partition capability is added.

Table stores ordered column vectors, concrete Arrow types, per-column Cell
reason policies and immutable ordered input/view bindings. It requires equal
complete key images, including empty images, and compatible time meaning.
A source Ibis preparation checks those facts before the registered local finish;
fixed preparation consumes checked exchange data. It never joins outer keys or
pairs rows by position. Exact Arrow-backed export preserves values but collapses
all non-Defined states to pandas missing. `show()` retains tags and reasons.
The independent acceptance owner is `tests/test_analysis_display_r65.py`; tested
qualification is recorded in the R6 ledger, rather than inferred from registration.

Ranking numeric views may be ranked again. Each new ranking rebuilds its display
parts from the current numeric values and requested partitions, while preserving
non-display sufficient parts. Previous ranking domains and ordering parts do not
become the new ranking's retained state.

### R6.6 allocation implementation qualification

Difference.attribute selects its method from retained original sufficient state.
The public result variants are LogicalAttributionResult and
MaterializedAttributionResult with contribution/current/baseline NumericRelation
views. Source preparation and scope checks use Ibis; allocation uses exact
Fraction intermediates and the existing checked physical finish. Fixed execution
uses the same method after common part verification. Original reductions retain
an internal allocation_state partition without reinstating removed public axes.

The qualified direct contribution axes remain non-null strings. True-null original
basis coordinates remain rejected by the preceding coordinate contract. Nullable
output axis slots are owned by allocation: resolution marks inactive hierarchy
positions and Other mask bits mark mapped remainder. Complete keys distinguish
those from every ordinary string, including "Other". Attribution does not relax
Entity identities or unrelated part key validation.

Additive int64/Decimal/Duration reconcile exactly; floating/rounded component
allocation uses the frozen exact-carrier threshold. Original float sufficient
state includes the existing magnitude/denominator stability facts; it must pass
original-state validation before allocation. Published Decimal sides are rounded
once and contributions are exact differences at that scale. High precision and
many small partitions retain the same tolerance, with rejection on excess
rounding rather than balancing. Existing direct-only and temporal limitations
remain enforced. Actual tested cells and unverified shapes are recorded in the
R6 migration ledger, separately from this connected method contract.

Allocation retains a finite nonnegative R5 error bound for each published
current/baseline side and contribution. Bounds use the original numerator
magnitudes, denominator interval and common mapped partitions; exact families
retain zero float error. Floating division and subtraction include their finish
rounding. Numeric views consume their own retained bound in both source and
fixed arithmetic, including after selection/recovery. Exchange independently
reproduces these bounds with the allocation, so a damaged bound cannot authorize
later division or a current-row statistic.

## R6.7 consumer and recovery closure

The public R6 path is the typed NumericRelation/Difference/AttributionResult
graph. Metric Dataset comparison and the old general Delta/Attribution families,
registrations, compiler/runtime/publication dispatch, descriptor codecs and workers
are retired. Event's exact funnel shapes alone retain private R7 family
registrations. Shared R8 arithmetic, state checks, ordered input binding and
Finding scalar encoding retain their actual consumers, with no R6 producer or
recovery eligibility. Old Help targets do not redirect.

Original reductions retaining group keys publish MaterializedGroupedNumericRelation
in both live execution and recovery; Singleton reductions retain
MaterializedRolledNumericRelation. The group receiver preserves its current
group axes in summarize, as required by the R5.4 contract. Tables validate typed
Other coordinates with the same declared axis-null policy as the numeric views.

## R7.1 frozen domain method rules

Status: accepted target, 2026-10-01; R7.1 grants no execution qualification.
This section owns F03-F11 method truth, numeric rules and conditional K. The
[API owner](python-analysis-design.md#r71-frozen-domain-api-target) owns signatures;
the [Runtime owner](session-state-and-runtime.md#r71-frozen-domain-execution-and-retained-state)
owns schemas, versions, exchange, placement, budgets and Findings. No legacy
family registry/codec is accepted as implementation evidence.

### Ordering, matching and reach

Occurrence preparation checks complete nonnull unique keys, exact Event and
participant bindings, version nonoverlap, physical time precision and source
authority. Time separates different instants. Integer/enum sequence is validated
for all captured occurrences of a Subject, including uniqueness across the
Events covered by that order authority; bool/unknown enum/duplicate sequence or
contradicted precedence rejects. A deterministic typed-key enumeration does not
resolve business order.

The only methods admitting a remaining simultaneous partial order are:

| Closed case | Invariant retained output and independent discriminator |
| --- | --- |
| preparation and Event-role Anchor binding | all exact occurrence/Anchor rows as sets; canonical typed-key display order only; shuffled input preserves every binding |
| one-step every_start matching | one assignment per distinct start, identical reach=true and Subject map under either final-sharing policy; first_per_subject is excluded |
| replay of a tied group strictly after an already known terminal state | every occurrence remains its own transition_from_terminal violation with the same terminal state; no legal transition or interval change; retain complete occurrence identities and compare shuffled traces as canonical sets |

Every other consumed ambiguous group requires sequence/precedence sufficient
for a unique relevant order. Equal final states are insufficient when assignment,
transition, interval, violation, classification or continuation parts differ.
The rules above are local proofs for these closed cases, not permission to
enumerate permutations or use a general confluence callback. Ties across
irrelevant Events not in the request are not read. Actual ambiguity is an
execution error before publication, with an exact business_order repair.

Matching starts in the half-open cohort_window, follows up strictly before
completion_through, and uses the earliest qualified occurrence after the
previous assigned step. first_per_subject selects one earliest qualified start;
every_start creates one Journey per start. Repeated Event refs share one input
capture. One occurrence never fills two distinct steps; missing intermediate
steps cannot be skipped. Intermediate occurrences may be reused across attempts.
Shared final assignment may finish several attempts; exclusive final assignment
reserves each final occurrence for the earliest qualified unfinished attempt.
Final reservation never forbids intermediate reuse. The one-step case assigns
the start once, without creating another final-event consumption. Subjects with
no start produce no synthetic failure Journey.

Reach is a Boolean Cell at every exact step of every Journey. True is an
assignment, False is a proved unreachable/absent step, Unknown is insufficient
coverage. Earlier Unknown cannot become False merely because a later Event has
broader coverage. Proved earlier failure makes later steps unreachable. Absence
uses exact attempt interval and Event/source/version coverage, not whole-input
max time, empty rows or a count. Damaged/contradicted claims are errors rather
than Unknown. All checks consume the same captured input used by matching.

### Duration, dropout and Subject image

The closed duration statuses are complete, incomplete, coverage_censored,
not_entered and entry_unknown. Complete owns both assigned endpoints and elapsed
duration. Incomplete owns a known entered start and proved follow-up through the
exclusive bound. Coverage-censored owns entered start and the known follow-up
prefix. Not-entered has proved absence of the from-step; entry-unknown lacks that
fact. Noncomplete duration is Undefined(not_completed); absent/unknown entry
timestamps retain their corresponding Cell reason, never a shared physical NULL.
observed_duration is the elapsed known follow-up interval for entered rows, capped
at completion if completed; it never uses the last observed Event as follow-up.
No entered-but-unknown negative interval is manufactured.

Duration tick unit is the exact admitted occurrence/boundary unit s/ms/us/ns.
All consumed endpoints must share that unit or be exactly representable in it;
no truncation or implicit cross-unit arithmetic is admitted. Native timestamp us
and each Parquet timestamp unit have separate qualifications. Tick subtraction
and published states are checked int64; exact intermediates never use float.
Mean is exact sum(ticks)/count. For sorted ticks x and quantile p, linear
interpolation uses h=(n-1)*p, floor/ceil neighbors and exact Fraction arithmetic;
median has p=1/2 and p90 p=9/10. Each finish rounds once to nearest-even ticks,
preserving unit and disclosing absolute rounding error <= 1/2 tick. Empty
completed sets yield Undefined(empty_completed_set), not Null/zero. Overflow or
nonfinite/invalid time input rejects. Published sum state must itself fit int64.

The only newly required current-row Duration reducer is row.mean for a bound
Duration NumericRelation, with exact tick sum/count state and the above finish.
This includes CompletedJourneys.duration and intervals().observed_duration;
it does not activate Decimal reducers, generic Duration sum/min/max/quantile or
weighted mean. Dwell median/p90 belong to dwell's domain method only. The Journey
10/30/100 seconds oracle is 140/3 seconds before tick finish; the mean of the two
Subject means is 60 seconds and is a different quantity. Legacy fractional-us
float assertions must be replaced by exact interpolation plus nearest-even
expectations; preserve the original anti-truncation discriminator.

Dropout before a noninitial exact step is True only for known started and
resolved absence/unreachability before that step, False for known reach, Unknown
for unresolved follow-up. It remains first_per_subject only. SubjectBinding is
total/single-valued on its exact Journey/Interval/Anchor/violation domain;
selection transports it, and members takes a set image without reads. This
does not replace Journey opportunity multiplicity with Subject counts.
Every-start opportunity quantification uses existing full-opportunity cohort
methods with their exact three empty policies; no implicit subject funnel or
select_subjects alias is introduced.

### Funnel, period comparison and ratio-mix allocation

Funnel counts exact int64 components over the same canonical assignment. At each
step cohort_count is all starts, resolved_cohort_count is known current reach,
entry_count is known reach of the previous step (all starts for the initial
step), resolved_entry_count is entered rows with resolved current reach,
reached_count is True, lost_count is entered False and coverage_censored_count
is entered Unknown. For noninitial steps resolved_entry=reached+lost and
entry=resolved_entry+coverage_censored. Conversion-from-first finishes
reached/resolved_cohort; conversion-from-previous finishes reached/resolved_entry;
loss-rate finishes lost/resolved_entry. Initial conversions use the known start
cohort; initial loss is Undefined(initial_step). Zero denominators are
Undefined(zero_denominator), including a legal empty ungrouped dense funnel.
Axes bind historical Dimension values at the first assigned occurrence;
complete actual tuples including real null are retained, without Cartesian
invented groups. Group components must reproduce the ungrouped target exactly.

Funnel-period comparison binds identical pattern/matching/Subject, the same
explicit population realization/definition, exact Event and axis definitions,
compatible time authority, equal start-window elapsed length and equal
post-window follow-up length. Relevant follow-up must be complete. Outer pairing
uses full step/axis keys; an absent side gets count zero only with complete
domain evidence of group absence. Rates retain Undefined at zero denominator.
MissingCoordinate remains distinct from an existing non-Defined rate. Ordinary
NumericRelation.compare gains none of these domain-specific zero rules.

funnel_ratio_mix@v1 binds one noninitial exact step. Let Ec/Eb be positive total
resolved-entry counts, and Li,c/Li,b the per-basis lost counts:

```text
target = Lc/Ec - Lb/Eb
loss contribution_i = (Li,c - Li,b)/Ec
loss side(current, baseline)_i = (Li,c/Ec, Li,b/Ec)
denominator_mix contribution_i = Li,b * (1/Ec - 1/Eb)
denominator_mix side(current, baseline)_i = (0, -Li,b * (1/Ec - 1/Eb))
```

Combined sides reproduce the current/baseline overall rates; each contribution
is its own current-minus-baseline side term. These are allocated terms, not
actual subgroup rates. Exact count/Fraction state persists until one finite
float64 finish; zero denominator, missing partition or contradictory components
reject independently of residual. Each finite finish obeys the R5 rational
precision bound; reconciliation additionally uses the existing threshold
max(1e-12, 1e-9*max(abs(expected), abs(actual), 1)), never as a precision substitute.
Every joint/hierarchy resolution reproduces both sides and the target separately.

Common Top-K uses current+baseline resolved-entry count, checked exactly, within
each authored axis prefix; ties use the full canonical typed coordinate key.
Mapping is shared across sides and loss/mix terms. Real null, real "Other" and
typed Other with active/other masks stay distinct. Hierarchy preserves every
ordered prefix. Contribution rank uses abs(contribution), then resolution/full
typed key/kind; zero overall delta retains valid contributions, with
Undefined(zero_total_delta) total shares and separate empty positive/negative
pool reasons. Views filtered later retain original reconciliation scope and
revoke selected-domain complete-partition claims. Logical missing axes become
explicit same-assignment expansion dependencies; fixed missing axes/components
reject rather than reading lineage/current Semantic.

### Replay, History views and retained truth

R7.5 implements `session.lifecycle.replay(model, population=members, window=window,
seed=from_inception(), completeness=())` and the paired LogicalHistoryResult /
MaterializedHistoryResult. Population is a required logical same-Session Subject
domain; the model is an exact Ref and owns business order. Catalog entries, fixed
membership, legacy PopulationInput and call-level order overrides reject before
business reads or Run allocation. The graph explicitly consumes both complete
members and occurrence preparation. Construction and planning read no business rows.

Every distinct modeled Event is captured once. Source preparation completes before
registered local replay consumes the captured inputs under the shared 600-second
deadline. Only a tied group reached after a proved terminal prefix can be retained
as a set, with invariant per-occurrence state and violation identities. Other ties
require unique business order; IDs and physical/declaration order cannot resolve them.

The closed HistoryPart@v1 is keyed by the complete Subject key, including empty
lists for no-event/no-interval Subjects. It retains all trigger evaluations,
pre-inception dispositions, legal/self/zero-duration transition trace, violations,
raw/clipped interval boundaries, exact observed ticks and known-prefix coverage.
Arrow/Parquet publication and recovery reproduce these projections from the saved
trace without replaying origin or connecting to source/current Semantic. Violations
use the existing zero-Findings policy. Precision follows the R7.2 capture conversion
and retained loss disclosure. There are no replay row, memory or tie-width quotas.

R7.6 implements the six views and the Duration/Subject observation rules below
on the unified local graph path. Logical and materialized History both return
Logical views; materialized receivers retain fixed leaves. Dwell Duration summary
fields cannot be pooled with summarize or rollup. Current-row mean is admitted on
interval observed_duration and retains exact sum/count for fixed merging.

Replay starts at real inception, which may precede the report window; only
source-origin authority proves its absence. A complete origin history with a
modeled trigger but no required inception fails atomically. Complete origin
with no modeled trigger is NotStarted and creates no initial-state interval;
insufficient origin is Unknown. Pre-inception observed triggers remain captured
with pre_inception disposition and cannot establish/advance state. They are not
legal transitions or post-inception illegal-transition assertions.
Without origin authority, even an observed inception has unknown_origin disposition
with no state, inception, interval or legal/violation assertion. Beyond the proved
prefix, nonterminal follow-up has unknown_followup disposition and preserves the
known prefix without inventing transitions. A terminal state reached inside the
proved prefix remains absorbing, so later invariant terminal violations can still
be retained as a set despite incomplete follow-up. Coverage remains censored.
After known inception, illegal_transition records the exact trigger and keeps
state; transition_from_terminal records its own occurrence and also keeps state.
Legal transitions include self-transitions and zero-duration intermediate states.
Canonical transition ordinal follows business order; canonical physical
enumeration of the terminal-only violation case carries no business ordering fact.

Every input Subject has inception/known-prefix/coverage classification even
without intervals. in_state is True for known requested state, False for known
other state or NotStarted, Unknown for insufficient authority. Checkpoints are
in [window.start, window.end]; end observes the left limit and never consumes
an occurrence at end. Distribution binds historical axes at each checkpoint,
keeps all declared ModelStates for each actual full axis group, and reports
Unknown and NotStarted separately. share_among_seeded conditions on definitely
seeded Subjects and does not claim population coverage.

Transitions emit the model's complete declared TransitionPair domain with zeros;
count comes from all legal trace entries in the window and share's denominator
is all such modeled transitions, including self/zero-duration entries. Intervals
retain original boundary causes and clipping/censoring status. Dwell owns
completed_window_fragment_duration@v1: clip first; include completed fragments,
including left-clipped completed; exclude right/coverage-censored fragments from
duration statistics while retaining their counts. Exact completed ticks plus
sum/count and sorted order statistics are required; grouped finished mean/p90
cannot roll up. Two Histories cannot be bag-merged to replay. State/pair summaries
cannot reconstruct Subject membership or a transition trace from intervals.

### Retention truth and conditional K

Anchor retention fixes the full Subject-by-Anchor Omega before return reads.
Observed qualifying return establishes K+ even with partial follow-up. No return
establishes K- only with exact bound coverage through the exclusive deadline;
otherwise it is K?. The three disjoint sets cover Omega. For nonempty Omega,
lower=|K+|/|Omega| and upper=(|K+|+|K?|)/|Omega|, exact counts then one float finish;
empty bounds are Undefined(empty_omega). 25/5/70 yields [0.25,0.95].
any_anchor is True if any true, False if all false, otherwise Unknown;
every_anchor is False if any false, True if all true, otherwise Unknown.
Subject projection explicitly replaces Omega with its nonempty-fiber image.
Status views/selection retain their original Omega and bounds; unknown members
selection refuses. Overlapping windows preserve every (Anchor, occurrence) use,
without disjointness or scalar-bound rollup. All fixed K requires the exact
retained definition, instance domain, mapping and method parts, not display values.


## R7.2 captured order and precision amendment

The 2026-10-01 accepted precision policy supersedes source-lossless timestamps in
the R7.1 target. Exact keys, order and arithmetic refer to the actual captured
time carrier. Preparation accepts occurrence sets; ordered consumers reject ties
without sequence/precedence authority. Integer/enum uniqueness is checked across
a Subject's captured Events; contradictory precedence cycles and unknown/null
sequence values fail. The one-step every_start invariant is closed and accepts
only one Event without a replay model. Preparation cannot assert an already-known
terminal prefix: after_terminal remains refused until qualified replay supplies
that proof. Equal final states or occurrence-ID sorting supply no invariant.
Precision-created ties obey these same rules. Preparation qualification does not
grant Journey assignment or History replay semantics. The
[time owner](timezone-and-calendar-design.md#r71-frozen-occurrence-and-relative-window-time)
owns the retained precision disclosure; duration arithmetic remains exact in the
actual captured tick unit when later producers connect.


### R7.4 implementation boundary (2026-10-02)

The public first-per-subject funnel, exact owned reads, compatible period compare,
and funnel_ratio_mix allocation now use the unified graph, registry, Runtime and
Store 7. The Ibis prefix captures entry-time axes before local consumers; fixed
continuations use retained state. Nonempty frozen Findings publish atomically and
share full collection validation across first read, recovery and exact hit.
Private funnel Delta/Attribution registrations, dispatch, extractor consumers and
exclusive codecs are physically removed. Remaining Event/Lifecycle shared code
awaits its owning phase. Detailed validation and physical requirement statuses are
in the R7.4 evidence index; later phases, same-wheel and remote qualification remain separate.

## R8.2 connected deviation rules

The two deviation methods implement the frozen equations, Cell table and numeric
policy below. Exact integer/Fraction states retain original Decimal values and
binary64 ratios. The fitted center remains unrounded through scoring. Unit-valued
fields finish once with HALF_EVEN; score finishes to finite float64. Population
variance roots have directed Decimal certificates with at least 120 digits,
refined by 40 digits when the final rounding is undecided. No epsilon zero test
or fallback algorithm is used.

Fit inputs/state, four-cell counts, partition/order witnesses, raw scale branch
and certificates remain original authority through selected outputs. Grid and
Subject maps come from their real typed owners. Restoration checks saved facts,
current keys, observed components and views without refitting.
Mixed tables retain independently verified fitted-column inputs and parts;
derived ranks are checked against their captured ranking values rather than
treated as a score projection. Invalid rational denominators and Decimal
certificate encodings reject through the typed retained-part repair.
Certificate precision steps, coefficient digits and interval width must agree; padding digits does not
grant additional precision. Exact Decimal
current-row min/max retain their original carrier and extrema state; prepared
Decimal sum uses widened exact state and its accepted Decimal(38,s) output.
Actual qualified profiles and unfinished exits are recorded in the
[R8.2 evidence index](../../superpowers/specs/2026-10-04-marivo-r82-evidence-index.md).

## R8.1 frozen statistical method rules

This section is the sole normative R8 formula, Cell, numeric, RequiredParts and
K owner. It is an inactive target until the corresponding R8.2/R8.3/R8.4 public
consumer is qualified. The old typed-operators Dataset containers, family
codec/dispatch, generated-float-only rule and local capacity/Entity restrictions
do not override this Relation contract or already accepted fixed R4 Spearman.
The API owner defines concrete receivers; Runtime/timezone define execution and
temporal authority. All following method/state contracts are version 1.

### Deviation fit, Cell table and irreversible scope

`deviation.zscore@v1` fits c=mean(x) and s=sqrt(mean((x-c)^2)) over Defined finite
values in each explicitly authored category tuple. `deviation.mad@v1` fits the
median c and s=(7413/5000)*median(abs(x-c)). If raw MAD is exactly zero, s is the
mean absolute deviation around the same c, with no 1.4826 factor. The scale
branch is closed: population_stddev, scaled_mad, mean_absolute_deviation.
Even-sample medians are exact means of the two central original values.
Empty partition_by means one fit over the entire current receiver domain;
Null categories are real partition coordinates and other coordinates never
implicitly partition the fit. Every row has equal weight.

| Original Cell / fit | observed | reference | deviation | score |
| --- | --- | --- | --- | --- |
| Defined finite, n>=2 and raw scale>0 | original Defined | Defined c | Defined x-c | Defined (x-c)/s |
| Defined finite, n=1 | original Defined | Defined c=x | Defined zero | Undefined(insufficient_samples) |
| Defined finite, n>=2 and raw scale=0 | original Defined | Defined c | Defined x-c | Undefined(zero_scale) |
| Null / Undefined / Unknown, n>0 | original tag/reason | Defined c | original tag/reason | original tag/reason |
| Null / Undefined / Unknown, n=0 | original tag/reason | Undefined(no_valid_samples) | original tag/reason | original tag/reason |

An empty receiver has no synthetic row. Its fit scope records original_count=0,
n=0 and no_valid_samples. Nonfinite Defined, malformed/duplicate identity,
foreign correspondence or false coverage claims are hard failures, not excluded
samples. Per-partition original_count equals defined+null+undefined+unknown;
n equals finite Defined count. original_count and n are separately disclosed.
observed retains original unit, quantity/identity, Subject and actual authorized
parts; reference/deviation retain the unit but have new fitted quantities; score
is dimensionless. The three derived quantities have no original rollup/attribute.

Fit scope is frozen at the node. where on the result selects all four views;
rank/limit uses existing RankingResult and preserves n/c/s/branch/input bindings.
Neither rewrites the original fitted domain. where-before-deviation creates a
new fit. Explicit shared input/fit nodes execute once per DAG/Run; separately
constructed equal definitions are not common-subexpression merged. Subject
projection from selected observed uses its real original mapping, never scores
or display row positions.

### r8_numeric_v1: widened arithmetic and one finish

Required input families are int64, finite float64 and Decimal(p,s),
1<=p<=38 and 0<=s<=p. Each vector retains one exact physical input type; bool,
Duration/date/timestamp are not statistical numerics. Counts and horizon/lag
ordinals are checked int64. Integer sums, differences, squares, products,
medians, means, covariance, innovations and variance use widened integer/Fraction
state before finishing. Decimal inputs are validated at their captured (p,s),
then converted losslessly to scaled-integer/rational intermediates. 1.4826 is the
exact rational 7413/5000, not a binary float literal. No input rounding or ambient
process Decimal context is permitted.

| Input / output | observed | reference/deviation and forecast prediction/lower/upper | score / coefficient |
| --- | --- | --- | --- |
| int64 | int64 | finite float64, one nearest representable finish from widened intermediates | finite float64 |
| float64 | exact captured binary64 | finite float64, stable centered/scaled arithmetic | finite float64 |
| Decimal(p,s) | original Decimal(p,s) | Decimal(38,max(s,6)), one ROUND_HALF_EVEN finish | finite float64, one explicit final projection |

Center and x-c, innovations and point predictions use unrounded internal values;
rounding the displayed center before scoring is forbidden. The raw exact
variance/MAD/fallback value decides scale=0, never its displayed rounded value.
For float64, constant/zero tests compare original finite binary64 values and
exact centered identities. Exact binary64-as-rational raw facts are the independent
oracle; stable floating implementations must prove the final bound below for
extreme/near-cancelling/subnormal inputs, row orders and batching.

Irrational sqrt and inverse-normal finish use an isolated ROUND_HALF_EVEN Decimal
working context with Emin=-999999, Emax=999999 and at least 120 significant digits.
For inverse-normal probability p=(1+level)/2, level is its exact binary64 ratio;
start precision is 120+max(0,-floor(log10(min(level,1-level)))). Do not round p to
0, 0.5 or 1 before inversion. Directed enclosing bounds certify the published
rounding/error. If a final rounding boundary is unresolved, refine this same
numeric algorithm by 40 digits under the existing execute deadline; this is not
an implementation/route retry. No unproved exact irrational claim is allowed.

For independent mathematical result r, float64 acceptance is
abs(actual-r)<=R(r), R(r)=1e-12*(1+abs(r)), evaluated without float overflow.
Decimal acceptance requires the specified HALF_EVEN value at output scale;
the disclosed error is at most half the output quantum plus the certified
transcendental enclosure error. Exact rational finishes settle ties exactly;
transcendental rounding is certified before publication. Numerical bounds are
separate from upstream sampling/semantic approximation and business Unknown.
A finite output that cannot meet its bound remains unqualified. Stored precision,
nonfinite output, count overflow or unrepresentable final unit value reject the
whole action. No saturation, epsilon clamp, implicit Decimal rescale or runtime
replacement of a required family by "unsupported" closes its requirement.

Correlation permits all ordered pairs of I/F/D input families, including
unequal Decimal precision/scale across endpoints: each endpoint retains its own
original type. Cross-endpoint products/centerings use lossless rational
intermediates, not a common float input cast. Spearman/Kendall rank/compare each
vector in its original exact order; ranks are exact half-integers. This does not
authorize within-vector mixed values or cross-family predicate/partition coercion.
Unary deviation/forecast have no heterogeneous-vector overload. A source-native
physical route must qualify the same exact ordered types and error contract.
The future physical precision variant certified_statistical is restricted to the
eight deviation/association/forecast methods and binds r8_numeric_v1 with exact
input types, widened state and declared rounded outputs. It is distinct from
the existing exact, checked_int64 and finite_float64 variants. Extending this
closed variant must not weaken the existing Decimal-exact admission for other
methods; time.runs keeps exact classification/tick facts with checked counts.
The connected R8.2/R8.4 local declarations now express this statistical certificate
with exact ordered carriers. Their bounded qualification and remaining native,
origin, key and scenario requirements are recorded in the separate phase indexes;
registration does not grant full matrix acceptance.

Independent oracles use original integers/Decimals/binary64 ratios, sorted order
statistics, centered rational sums, pair counts and the model equations below.
They do not import product numeric helpers, use Candidate absolute scores as a
signed-score oracle, or compare implementations sharing one arithmetic helper.
Root/quantile oracle enclosures use separately authored high-precision definitions.

### Runs classification and maximal segments

`time.runs@v1` consumes the complete original grid and every predicate dependency
with corresponding complete typed keys. Pure Entity/scalar or arbitrary timestamp
columns do not qualify. Each non-time tuple is a separate sequence. Validate
physical missing/duplicate/partial cells and false coverage as hard violations;
only an explicitly retained unavailable original cell is a legal gap. Ordinary
where that removed time cells revokes completeness and blocks subsequent runs,
even if surviving coordinates happen to look consecutive.

Each predicate leaf is evaluated before composition. Numeric comparison of a
non-Defined required Cell is unavailable with that Cell's reason; state predicates
are total according to their existing tag semantics. Type/unit/owner/correspondence
errors remain hard failures. Composition is closed: if any dependency is
unavailable, the composite is unavailable; otherwise evaluate the Boolean tree
normally. There is no short-circuit masking of an unavailable sibling. This
classifier is specific to runs and does not weaken ordinary where's strict rule.

Enumerate all original cells in grid order. false, unavailable or a recorded gap
ends the current true segment. Emit every maximal true segment as
[first_cell.start,last_cell.end), count=number of cells, duration=end-start in
exact grid microsecond ticks. Crossing a reader batch carries the unfinished
segment and last grid identity; a batch boundary is not a terminator. DST and
certified unequal periods use actual UTC boundaries, never count*24h.
Run identity binds series/grid, condition definition/version and all dependency
bindings, plus start/end cell identities. Closed left/right termination kinds
are false, unavailable and scope_boundary, with the terminating cell/reason when
present. scope_boundary means observation ended, not that the business condition
resolved. Two-sided adjacent opposite-sign true cells remain one segment.

Retain true/false/unavailable counts for the whole grid, including zero output;
all-unavailable and evaluated-zero are distinct. Post-run where/rank/table selects
segments and never resegments. count can rank; duration supports existing
unitized predicates, with no new Duration ranking directory. Subject images
exist only through original retained SubjectBinding and run->cell->Subject parts.
Projection deduplicates Subjects under existing set rules without changing run
multiplicity; global Time sequences have no members capability.

### Association pairing, statuses, lag and selection

`association.pearson@v1`, `association.spearman@v1` and
`association.kendall@v1` consume 2..16 distinct single-quantity inputs in request
order. Every unordered index pair a<b is evaluated. Quantity identity, not label,
establishes distinctness. Require the same exact complete observation instance
set/coordinate authority and realized unique composite keys; independent source
captures with merely equal row counts/Entity declarations do not prove pairing.
Entity/category rows are statistical units. Time is paired by point, and each
category*time tuple is a separate series. Scalar is rejected.

None means one zero lag. A nonempty range supplies signed int64 offsets only;
explicit lag is rejected on Entity/category even for range(0,1). +k pairs A(t)
with B(t+k) on original grid ordinals and certified coordinates, without crossing
series or collapsing missing rows. Input request order determines direction.
For each pair/lag/series: input-matched=boundary_drop and null+complete=matched.
Only ordinary Null is pairwise deleted after alignment. Undefined/Unknown,
nonfinite Defined, duplicate keys or malformed domains fail atomically.

For complete pairs x,y, Pearson is Sxy/sqrt(Sxx*Syy), where Sxy is the centered
cross-product sum and Sxx/Syy are centered square sums. Spearman is this Pearson
formula on globally assigned average ranks of each complete paired vector.
Frozen Spearman witnesses are validated by one ordered tie-block scan after
sorting, in O(n log n) comparisons, without calling the rank estimator.
Kendall tau-b is (C-D)/sqrt((C+D+T_x)*(C+D+T_y)); C/D count concordant/discordant
unordered observation pairs, T_x/T_y count ties in only that endpoint, and pairs
tied in both are excluded from both factors. Batch-local ranks/coefficient merges
and quadratic source SQL pair joins are forbidden substitutes.

Status precedence is insufficient_pairs when complete<2, then constant_both,
constant_a, constant_b, otherwise valid. Invalid candidates carry Undefined
coefficient with their closed reason and selected=False, never NaN or zero.
A valid candidate requires finite coefficient in [-1,1]; any rounding enclosure
used to finish an endpoint coefficient must be retained, with no arbitrary
near-one clamp. Every pair/series needs >=1 valid lag or the entire call rejects.
Exactly one valid lag wins by (-abs(coefficient),abs(lag),lag), selection version
association.max_abs_coefficient_min_abs_lag_min_signed_lag@v1. All invalid and
valid candidates remain. Pair*lag*series ceiling is 4096, checked statically when
known and at execution otherwise; it is unrelated to input rows/bytes/memory.
where does not recompute coefficients, selected flags or original search_summary.
Coefficient summarize describes current coefficient rows; it is not pooled
correlation, original rollup, Entity selection, causality or inference.

### Forecast model and interval equations

`forecast.naive@v1`, `forecast.drift@v1` and `forecast.seasonal_naive@v1` consume
one complete consecutive time or category*time quantity. All series share the
same captured training grid and approved future grid. Every required training
Cell is Defined/finite. Reject duplicates, missing/partial cells, all other Cell
tags, unapproved continuation and pooling across series. Horizon is periods(1..1000),
level is a finite float in (0,1), seasonal length is an integer s>1 excluding bool.
Minimum n is 2, 3 and s+1 respectively.

For series y[1..n] and future ordinal h>=1, normal_residual@v1 is normative:

```text
naive:
  prediction[h] = y[n]
  innovation[t] = y[t]-y[t-1], t=2..n
  df=n-1; sigma2=sum(innovation[t]^2)/df
  variance[h]=sigma2*h

drift:
  slope=(y[n]-y[1])/(n-1)
  prediction[h]=y[n]+h*slope
  innovation[t]=y[t]-y[t-1]-slope, t=2..n
  df=n-2; sigma2=sum(innovation[t]^2)/df
  variance[h]=sigma2*h*(1+h/(n-1))

seasonal_naive(s):
  prediction[h]=y[n-s+((h-1) mod s)+1]
  innovation[t]=y[t]-y[t-s], t=s+1..n
  df=n-s; sigma2=sum(innovation[t]^2)/df
  variance[h]=sigma2*(floor((h-1)/s)+1)

z=Phi^-1((1+level)/2)
margin[h]=z*sqrt(variance[h])
lower[h]=prediction[h]-margin[h]; upper[h]=prediction[h]+margin[h]
```

Naive/seasonal innovations are not demeaned, including a constant nonzero vector.
Drift removes only its first/last fitted increment and uses df=n-2, not residuals
of a regression on levels. Exact-zero variance is allowed only when every required
innovation is exactly zero. Unavailable/negative variance or a nonfinite required
point/bound aborts all horizons and series. A rounded interval can coincide at a
coarse Decimal quantum without asserting zero innovation: retain nonzero raw
variance and rounding error, never label it exact_zero. Missing variance never
becomes a zero-width interval.

Prediction is ModelPrediction; bounds are PredictionIntervalBound. Their domain
is the same captured future grid. where synchronizes the three views while
retaining original training authority; prediction rank/table and current-row
descriptive statistics do not authorize summing bounds into a total interval.
Association and forecast rank projections retain their original statistical
parts and Finding policy. Same-scope tables containing those ranks capture each column
and its display witnesses through `table_fits`; rank values are verified by
the display owner, not interpreted as coefficients or predictions. Builtin
future-grid lookahead uses absolute instants and enumerates actual civil
boundaries, including repeated and skipped DST boundaries; unused lookahead
must not reject an otherwise complete requested horizon.
Assumption contract is zero_mean_uncorrelated_homoskedastic_normal_innovations@v1,
with drift mean-increment estimation uncertainty. These are nominal prediction
intervals for future observations, not mean confidence intervals, empirical
calibration, causal effects or a guarantee from numerical admission.

### RequiredParts, transformations and closed failures

Part schemas are frozen under r8.<role>/v1, with typed composite keys, explicit
binding/definition/scope/version and separate schema/row-count/receipt. Rational
facts use reduced signed numerator and positive denominator decimal strings;
bounded-root facts bind the exact radicand and directed Decimal endpoints. These
are closed scalar records, not arbitrary reason dictionaries or public exports.

| Role | Required columns/facts and keys | Producer / transport |
| --- | --- | --- |
| fit_inputs | original key, partition tuple, Cell value/tag/reason, input/grid/Subject binding; full original scope | Deviation captures once; selection retains full authority |
| fit_state | partition key, original/tag counts/n, center, raw scale/variance, scale branch, numeric policy, median/order witnesses and input digest | Deviation; immutable through where/rank/limit |
| grid_cells | grid/cell identity, ordinal, original/actual start/end, partial, precision, coverage; per-series complete coordinate map | shared temporal capture; numeric/difference/score transport only if completeness survives |
| condition_cells | series/cell key, dependency bindings, true/false/unavailable and closed reasons | Runs; full classification scope survives selected outputs |
| run_cells | run identity, ordered cell identities, start/end/count/duration and two termination witnesses | Runs; select by run identity, never resegment |
| subject_map | original complete Subject tuple, input cell/instance and run/observed mapping when available | actual Subject owner; never synthesize from public numeric keys |
| pair_inputs | ordered quantity/pair/series/lag, full correspondence bindings, captured pair values/tags and pairing counts | Association; fixed method requires these inputs, transport-only does not qualify the kernel |
| association_state | every candidate key, counts/status/coefficient/selected, input quantity contracts and original search_summary/selection version | Association; selected current rows retain original search authority |
| training_inputs | series/cell key, full training values, training grid and quantity binding | Forecast; no source/calendar reconstruction |
| forecast_state | series key, model/season/n/innovation count/df/slope/sigma2/zero flag/numeric policy; each horizon variance | Forecast; per-series facts, not only min/max summaries |
| future_cells | approved future grid/cell keys, ordinal/start/end, horizon mapping and continuation authority | temporal owner; immutable through forecast selection |
| finding_policy | exact producer/state/extractor/policy/input/scope and count/digest authority | common Runtime owner, including no-Findings policy |

Selected main rows and selected field tables have the same typed current keys;
full fit/classification/search/training authority tables keep their original keys
and explicit scope. Their PartTransform is select_output_retain_scope@v1.
Original observed parts transport only when their existing rule remains true.
Selection revokes complete-time-grid admission for a new runs/forecast kernel;
output transport cannot manufacture lost full inputs. Recovery validates key,
version, receipts, scope linkage, counts, witnesses and numeric equations against
retained state; it must not refit, rerank, resegment, reselect lag or reforecast
to replace missing/damaged parts. Actual K is the intersection of these parts,
quantity and requested parameters, not the Result family name.

The future typed error family subclasses AnalysisError with code, operation,
method/version, exact input identity, expected, received and repair. Closed codes
are r8.input_identity, r8.input_mode, r8.cell_policy, r8.grid_incomplete,
r8.correspondence, r8.numeric_unqualified, r8.numeric_overflow,
r8.no_valid_candidate, r8.candidate_ceiling, r8.forecast_history,
r8.future_grid, r8.retained_part and r8.numeric_precision. Unavailable scoring is
Cell state, not an execution error. Each code binds actual missing parts/types,
keys, counts or registered qualification; repair uses current state and names
an exact reconstruction/parameter action. Timeout/cancel/resource errors reuse
the existing Runtime owner. There is no generic ValueError or hardcoded repair
catalog substituted for these structured facts.

### R8.3 runs implementation boundary

The connected `time.runs@v1` owner captures all condition dependencies and the
complete original grid, then enumerates maximal true segments across the entire
input. Reader batches do not delimit segments. Its four projections use
`time.runs_read@v1`. Output selection preserves full classification counts,
termination witnesses and run-to-cell mapping; it never segments retained rows.
The R8.3 evidence index records executed checks separately from frozen IDs.
Complete-grid admission checks row selection since the owning `each(grid)`
boundary: both where and limit reject, while Subject selection before a new
grid does not invalidate that new observation. Runs admission errors route to
the runs Help contract.
