# Python Analysis Design

## S1 W1 private J1 construction

W1 loads the closed Semantic additivity, event-time, and value-policy declarations
through normal authoring, then builds a private J1 Logical chain for members,
Region read and strict selection, Region grouping, builder-backed Revenue
observation, Channel contribution coordinates, and original-state rollup.
Construction and field handles do not read business sources. The accepted method
semantics require a declared directed Buyer path, a single-valued member Region,
sum retained parts, ignore-Null and empty-Null behavior, and a declared event
time. The method has no registered source or pandas implementation in W1, so
the independent SQL oracle remains an oracle rather than DSL execution evidence.
An opaque Metric body may load but cannot continue through this private path.

## S1 W2 private J1 execution

W2 attaches those exact J1 roots to Dataset row and row-set contracts and a
private placement check. A selected DuckDB Ibis backend lowers the admitted
members, Region read/strict selection, Region grouping, Revenue observation,
Channel contribution grouping, original-state rollup, and current-row
sum/count/mean to Ibis expressions. Ibis compiles the source expressions and
checks; a source failure ends that route. The source adapter validates physical
string/int64 keys, string categories, int64/float64 contribution values,
finite floats, complete keys, Cell tags, and checked sum/count state.

The private pandas route consumes an exact J1 predecessor from an exhausted,
receipt-checked local read or a complete source result. A category read can be
selected or grouped after a fixed receipt; a coordinate-free Revenue
observation retains keyed sum, non-null count, and row-count parts for local
rollup after its fixed receipt. An in-memory source result with a retained
Channel coordinate can group that coordinate by explicit key. Persisted
coordinate parts and successful Artifact publication/cold recovery are described
in the W3 section below. These private calls do not activate public Analysis
DSL signatures or claim Runtime publication.

For J1 builder-backed `ms.aggregate(..., agg="sum")`, the graph fixes
ignore-Null inputs and a Null result for complete empty contributions; the
builder has no separate value-policy parameters. Explicit authored policy
facts, when present on a normalized Metric, must agree with that graph before
J1 admission. Both routes apply the same versioned Cell and numeric admission
policy. DuckDB and pandas accumulate float64 in different orders, so a float64
sum or mean is not promised to be bit-identical across routes. J1 exchange
vectors compare float64 with an explicit tolerance and preserve exact int64
checks; a retained coordinate float64 partition must pass its own bounded
sum-state check before publication.

## S1 W3 private J1 Artifact exchange

W3 uses one closed schema-first Arrow stream contract for J1 member relations
and Cell-valued rows. The DuckDB/Ibis producer and receipt-checked local Parquet
reader complete row, key, Cell and content checks before yielding publication
evidence. An exact admitted Run publishes the main rows and separately receipted
sum/count and optional coordinate parts through the existing Store. Recovery
requires the exact Artifact reference, J1 definition, method version and input
binding; it reads every selected receipt before pandas continuation. A cold
process can continue with saved category rows, current-row statistics and
Channel coordinate rollup without a source connection.

## S1 W4 private J1 Runtime execution

The internal `DatasetRuntime.execute_j1(...)` action now owns J1 admission,
Session writer exclusion, incomplete-Run reconciliation, and publication
outcome read-back. A source call opens its supplied DuckDB/Ibis source factory
only after Run admission and assigns a new v2 key from the stable J1 definition
binding and the allocated Run ref. Repeating the same J1 node reads current
source data and publishes a separate immutable Artifact.

A fixed local continuation selects an exact saved predecessor and binds its
primary and retained-part receipts into the v2 key. A fully validated exact
hit returns its original Artifact without a Run; a miss reads the retained
state through the controlled Parquet reader and executes the admitted pandas
method. Live-source work combined with an explicit saved predecessor rejects
before source opening, Artifact row reads, or Run admission. The J1 semantic
node retains its definition fingerprint; the invocation binding additionally
encodes source or exact Artifact input and method version. Run, publication,
exchange binding, and read-back retain the selected key. Existing non-J1
Dataset execution keeps its v1 key and cache behavior. This is a private
execution chain, not a public DSL or general production `execute()` route.

## S2 P1 private two-predecessor binding

The private `J1Observed.compare(baseline)` constructor admits two Entity-level
observations of the same Metric, component plan, coordinates and non-time
scope facts with distinct time scopes. Both branches must descend from the same explicit member root;
matching definitions or keys alone do not establish that identity. The
ordered current/baseline root computes a strict absolute difference. It has a
Difference quantity with endpoint requirements and does not inherit the
Metric's original-state rollup authority. Selection from Difference and new
observation remain later S2 work.

For a source-only compare, the DuckDB/Ibis adapter first checks the complete
logical shape and method route, then realizes the shared member relation once
per invocation and supplies that realization to both observations. It checks
unique and equal endpoint keys and finite Defined numeric Cells before
publishing the result and independently receipted endpoint parts. A second
top-level execution gets a new Run, member realization and Artifact.

For fixed input, the private Runtime requires two exact ordered observed
Artifacts whose exchange metadata names the same nonempty member realization.
The fixed key binds both Artifact references, receipts, parts and member
binding. A validated hit has no new Run; a miss reads both retained inputs and
uses pandas for the same strict difference. Mixed live and fixed inputs, or
independently captured endpoints, fail before Artifact row reads and Run
admission. This route is limited to the admitted J1 compare shape and is not a
public multi-output capture API.

## S2 P2 private J2 selection and next observation

A private Difference now has a bound numeric value field with finite
`lt/lte/gt/gte/eq` thresholds. Float64 `eq` uses exact binary equality. Strict
`where` keeps the Difference value and
filters both retained endpoint parts by exact member key; `members()` projects
the selected identities without reading a source. The selected Entity domain
binds its parent domain and selector definition, so a later observation names
the actual selected domain. A comparison requires the same immediate member
input node for both observations; sharing only an older ancestor is insufficient.
Difference and its selected relation support current-row sum/count/mean, but
neither inherits the original Metric's state rollup. The selected relation has
no second numeric `where` field handle in P2.

For J2, one source-only graph evaluates July and August under strict complete
key pairing, selects negative changes, observes September for those Logical
members, and computes mean over the selected customer rows. The source stage
realizes the compare member node once in that invocation. The J2 fixture uses
explicit zero-valued orders for the zero Cells; a truly empty Revenue sum still
returns Null and cannot enter strict compare.

The existing Store and exchange codec retain the selected Difference's exact
endpoint parts and selector-bound domain. A fixed pair requires two ordered
Artifacts with one shared member realization; pandas can then filter the
Difference, project members, or summarize its current rows after receipt checks.
A fixed selected member Artifact cannot be used for a new live read or observe.
These are private qualifications, not public DSL methods or a two-endpoint
capture API.

## S4 P1 public admission

The first public slice admits only the J1–J4 shapes in the MVP validation
plan. `session.members(Ref[EntityKind])` returns a logical AnalysisDomain;
`read(Ref[DimensionKind])` returns a CategoryRelation; and `observe` accepts
one `Ref[MetricKind]`, an explicit fixed `TimeScope`, one relationship or the
closed two-root `routes(route(...), route(...))` value, and optional declared
contribution coordinates. Domain and relation methods return concrete logical
variants. `execute()` exists only on logical values; `show()` and
`to_pandas()` exist only on materialized values. Both expose `contract()`.
`session.artifact(reference)` returns an exact materialized variant, with
the existing non-DSL materialized families retained in its closed union.

The admitted operations are categorical equality selection and member
projection; grouping by a member Dimension or retained contribution
coordinate; exact absolute same-member comparison, strict numeric selection
and member projection; current-row `sum/count/mean` through closed method
values; original-state rollup; and same-Entity no-lag Spearman with a fixed
coefficient view. Unsupported target-language methods remain unexported.
Public result types never turn an unretained component or subject map into a
continuation. A logical chain constructs without business-source I/O; a
source-dependent top-level execute creates a fresh evaluation. A fixed-only
continuation uses exact retained receipts and pandas. Mixed fixed/live inputs
reject before a Run or either input is read.

The canonical entry for a migrated first-round shape is the domain/relation
chain. Existing Session population/observation and Dataset family methods
continue to serve shapes outside this admitted slice; they are not aliases for
the new chain. The public cutover must keep live Help, API docstrings, export
snapshots, user examples and the packaged analysis workflow synchronized.
Previous private J1 Artifacts have no public continuation snapshot and are
not upgraded. A newly public Artifact must retain its admitted node shape,
method/semantic policy versions, exact input binding and receipts so a cold
recovery can preserve the same K without loading current semantics.

| Canonical P1 call | Logical return | Materialized return or continuation | Required retained authority |
| --- | --- | --- | --- |
| `session.members(entity)` | `LogicalAnalysisDomain` | `MaterializedAnalysisDomain` | Entity identity and exact member root |
| `members.read(dimension)`, then `where(read.value.eq(category))` | `LogicalCategoryRelation`, `LogicalSelectedCategoryRelation` | matching category variant; selected `members()` projects identity | declared single-valued Dimension and selected member keys |
| `members.group_by(dimension).observe(metric, during=window, via=relationship)` | `GroupedNumericRelation` | `MaterializedGroupedNumericRelation` | group binding and sum/count state |
| `members.observe(metric, during=window, via=relationship)` | `LogicalNumericRelation` | `MaterializedNumericRelation`; `group_by` returns `GroupedNumericRelation`, `rollup` returns `LogicalRolledNumericRelation` / `MaterializedRolledNumericRelation` | sum, non-null count, row count and optional coordinate state |
| `members.observe(metric, during=window, via=mv.routes(...), coordinates=(...))` | `LogicalRatioRelation` | `MaterializedRatioRelation`; `group_by` returns `GroupedRatioRelation`, `rollup` returns `LogicalRolledRatioRelation` / `MaterializedRolledRatioRelation` | numerator sum/count/row count and denominator count/row count |
| `observed.compare(baseline)`, then `where(diff.value.lt(threshold))` | `LogicalDifferenceRelation`, `LogicalSelectedDifferenceRelation` | matching Difference variants; selected `members()` projects identity | exact ordered current and baseline endpoints |
| `relation.summarize(mv.sum/count/mean())` | `LogicalStatisticRelation` | terminal `MaterializedStatisticRelation` | current-row method and Cell checks; no original-state rollup |
| `observed.correlate(other, method="spearman")` | `LogicalAssociationResult` | `MaterializedAssociationResult`, then fixed `MaterializedCoefficientRelation` | paired observation state, pair counts and exact member binding |
| `coefficient.where(coefficient.value.lt(threshold))` | `LogicalCoefficientSelectionRelation` | `MaterializedCoefficientSelectionRelation` | retained pair counts and coefficient policy |

`mv.route(root, *, through=(...))` and `mv.routes(first, second)` are
closed values; `mv.sum()`, `mv.count()`, and `mv.mean()` take no arguments.
`rollup()` merges original retained components, while `summarize(...)`
calculates over current rows. Logical values own `execute()` and
`contract()`; Materialized values own `show()`, `to_pandas()`, and
`contract()`. A fixed selected-member projection is a
`LogicalFixedAnalysisDomain` with only a local `execute()` continuation.
Cold recovery reconstructs the concrete materialized variant from a canonical
v1 public snapshot stored in the v3 J1 exchange. The snapshot contains the
node graph, frozen row facts and policy objects; the Artifact descriptor binds
the definition, method, input execution key, member implementation and all
primary/part receipts. Missing, malformed or mismatched snapshots reject.

| Existing public entry | P1 decision |
| --- | --- |
| `session.population(...)` and its Dataset family methods | Retained for Population, Event, Lifecycle and other shapes outside the admitted Entity-domain chain; it is not a J1–J4 synonym. |
| `session.observe(...)` and its Dataset family methods | Retained for existing Metric, time-series and non-J1–J4 analysis; new member-domain J1–J4 guidance starts at `session.members(...)`. |
| `session.artifact(reference)` | Extended to recover a concrete public J1–J4 Materialized variant when its validated snapshot exists; other family Artifacts retain their prior return shape. |
| Private `DatasetRuntime.execute_j1(...)` and old private J1 Artifacts | No public entry or migration; private Artifacts without a public snapshot reject through the public recovery call. |

## Accepted S0 Analysis DSL slice (inactive)

This historical S0 section accepted the first-round semantics in the
[DSL MVP](../../superpowers/specs/2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md#2-首轮方法范围与后续扩展)
and the
[architecture](../../superpowers/specs/2026-09-24-marivo-analysis-dsl-architecture-design.md#3-基础能力与规则推导)
as an implementation contract before the S4 P1 public admission above. It
covers non-versioned single-column integer/string Entity identity,
many-to-one paths, categorical attributes, fixed half-open event windows,
sum/count and explicit-component ratio, strict selection, exact-key absolute
time comparison, current-row sum/count/mean, original-state rollup, and
same-Entity no-lag Spearman. DuckDB is the first source adapter; pure
retained-input continuation uses pandas. The full
[target interface](../../superpowers/specs/2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)
remains proposed outside this slice. S0 alone did not activate public
signatures, Help, other backends, mixed-input execution, or production routing.

The acceptance baseline is code `79faee0030685f0690fd2970990fccbe9a385886`
in a clean `panda` checkout. The isolated T1 worktree starts at the same
commit; its ignored plan copy matches the original. No dependency lockfile
was found in this checkout, so `pyproject.toml` records the dependency constraints.
SHA-256 inputs reviewed here:

| Input | SHA-256 |
| --- | --- |
| Copied S0 implementation plan (ignored) | `a254242f8a247b9cb53f07d652d0de7f8d2fcced51eb5c047b753a803d83bb60` |
| Tracked DSL MVP | `46cb6232eb9db9c2fff5698177b78487b50d9d3b73d5bbe7400a33d6478a6d51` |
| Tracked target interface | `36e355655bd9e8f3bee0b492e4071a5988c543034ebca2725e8d610b0ca2c57b` |
| Tracked architecture | `72718b431b2af5552c6cda6a0fa22214953d57decb1944d744fcd476e4452e2d` |
| Tracked algebra theory | `c1901f306952e294fb11c329fbf66f5358e1ec3e605f69d9dcd3e60852acc309` |
| Dependency constraints (`pyproject.toml`) | `b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9` |

These identify the reviewed inputs; later implementation must recheck its
own baseline and installed dependencies. The original checkout's `.venv`
reported Ibis 12.0.0, pandas 2.3.3, PyArrow 25.0.1, DuckDB 1.5.3,
SciPy 1.17.1 and NumPy 2.4.6; this T1 document pass did not use them for
execution evidence.

The ledger below is the T1 owner-gap record. An owner produces the named fact;
consumers may check or transport it but must not redefine it. `S0` in the last
column means a private contract seam, not completed execution. The listed V
items are future validation obligations, not T1 test results.

| ID and accepted requirement | Single owner | Current gap | Next task | MVP validation | Stage |
| --- | --- | --- | --- | --- | --- |
| A01 — declared Entity identity, time role, Metric contribution roots and method value policy | Semantic object model | Current normalized graph retains roots/components and some fixed null/empty rules, but cannot supply the new decorator's full additivity, time-role and value-policy declaration | T2; T5 must use real declarations | V02–V04, V14 | S0; S1 authoring consumer |
| A02 — Entity/Group/Singleton domain, member identity, contribution coordinates and explicit node binding | Dataset Core | Existing descriptors/handles do not represent the accepted DSL domain/quantity/Cell distinction or run-local node binding | T2 | V01, V02, V10, V14 | S0; S1–S2 execution |
| A03 — six closed capability rules, including RequiredParts and local preservation | Dataset method contracts | Existing family methods and observation contracts do not share this accepted rule/output derivation | T2 | V01–V05, V13 | S0; S1–S2 execution |
| A04 — one method/version policy with separately admitted implementations | Operator registry | `ImplementationRegistration` describes execution routes but is not the accepted method-semantics owner | T2 | V05, V11, V13, V15 | S0; S1 qualification |
| A05 — source-only, fixed-Artifact-only and mixed transitive input classification | Compiler normalization | `logical_roots()` stops at a materialized leaf, but no closed classification controls admission | T2 | V08, V10, V11 | S0; S1–S2 execution |
| A06 — Ibis source lowering and pandas retained continuation | Compiler placement | Current registered methods may use a DuckDB `ParquetBinding` for retained input | T2 contract; S1 adapter | V05, V07, V11, V15 | S1 |
| A07 — fixed schema, four Cell branches, state/part binding and completed checks | Materialization contract | Existing primary/parts and receipts lack the accepted Cell and shared-producer schema | T3 | V03, V06, V07, V10 | S0; S1 codec |
| A08 — separate stable definition identity and per-source-evaluation Run identity | Dataset Runtime | Current execution key is definition-only and lookup precedes Run admission | T4 | V08, V10 | S0; S1 dispatch |
| A09 — one-key publication, exact receipt/Run recovery and no automatic replay | Session Store | Store uniqueness is sound, but the new source key/receipt binding is absent in publication | T4 | V08, V10 | S0; S1 integration |
| A10 — authentic declarations and independent J1–J4 oracles | Test fixtures | No isolated DSL fixtures/oracles; fabricated semantic flags would conceal A01 | T5 | V01–V04, V09, V13 | S0 |
| A11 — same-Entity no-lag Spearman's paired-value and method state | Association method owner | Existing method exists, but the new Relation/Cell input and exchange route are not admitted | T2 policy; S3 adapter | V09, V11 | S3 |
| A12 — shared batch schema, completion checks and resource close | Materialization execution | `BatchStream` exists, but the new source/retained producer contract and all-exit close obligations are not fixed | T3 | V06, V10, V12 | S0; S1 producer |

For A01, authored assertions, graph-derived component facts, and checks
completed for this execution have distinct provenance. A queued check is not
evidence; declared Entity identity does not require a hidden source uniqueness
scan. Component role, root, filter, time fold and available state can be read
from the canonical graph. A decorator body cannot yield a trustworthy
contribution partition, additivity permission, missing-value policy or hidden
ratio components; the Semantic owner must provide the minimum declaration and
version before a consumer relying on it is admitted. T5 must block on a
missing real declaration rather than insert a fixture-only capability flag.

For A03, the accepted
[method rule table](operators-and-frames.md#accepted-s0-method-rules-inactive)
is the sole owner of the six capability semantics. Its numeric and Cell
policies come from the declared quantity and registered method, never from an
Ibis default, pandas dtype or a second adapter policy. Valid empty
contributions differ from unknown coverage, missing keys and missing state.
A ratio with retained numerator/denominator components may merge those
components before finishing; equal displayed ratios and a decorator body
containing division do not provide that authority.

For A04 and A06, one versioned method policy may have multiple independently
qualified implementations. A registered source implementation constructs Ibis
expressions and delegates SQL dialect compilation to Ibis; a retained-input
implementation consumes pandas after governed receipt reads. An unsupported
type, backend or input shape rejects without a silent fallback, handwritten
analysis SQL or a placeholder production registration.

For A05, compiler normalization classifies this invocation's transitive data
dependencies, stopping at each explicit materialized leaf. Historical lineage
does not turn a fixed Artifact into a live source dependency. For A08, Dataset
Runtime owns the [new lookup and Run order](session-state-and-runtime.md#accepted-s0-input-and-execution-protocol-inactive);
for A09, the Session Store owns unique publication and exact recovery.
Existing methods keep the current behavior below until individually migrated
and qualified; no second Runtime, registry or Store, old-Artifact migration,
or implicit fallback is accepted.

For A07 and A12, both source and receipt-checked Parquet producers expose a
known schema before iteration, including an empty stream. The exchange carries
typed non-null identity keys, explicit Cell tag/reason/value, quantity and
domain binding, private state roles and method versions. A missing row remains
a domain fact. Batch schema drift, mismatched parts or binding, and malformed
Cell payload reject; Arrow nulls alone do not encode all Cell branches. The
existing stream owner must release its resources on exhaustion, early close
and failure. Incomplete row-count, digest or coverage checks cannot become
successful evidence or a published result.

The inactive T3 seam now binds the existing row/row-set, domain, quantity,
method, evidence and selected receipts in `ExchangeBinding`. Its private
`marivo.analysis_exchange/v1` codec records exact fingerprints, method-owned
Cell reasons and distinct evidence categories; decoding alone grants no
publication or completed-check authority. The current Artifact descriptor v1
remains unchanged. `ValidatedExchangeStream` checks the same Arrow schema and
Cell rules for an in-memory source stream and a governed local Parquet stream.
It reports physical completion only after full exhaustion and owned close;
pending coverage obligations remain pending. The receipt adapter exposes its
schema before iteration and supplies the empty header batch needed by existing
consumers. Full producer qualification and descriptor publication remain S1.

## Unified operator and execution ownership

All backends use one operator contract and implementation-registration mechanism.
The selected backend owns physical preparation, execution,
retained import and resource lifetime. DuckDB's temporary objects, macros and
registration hooks implement the same semantic requirements; they are not
operator-level exceptions. Remote implementations must work with read-only
accounts and prove equivalent semantics, numerical behavior and required
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
particular method or input shape. For source-bound Metric and Population graphs,
the logical Dataset contract shows the selected backend's static source admission:
`rejected` includes the qualification reason, while `static_pass` means only that
the pure source check passed. Graphs with retained Artifact inputs show
`not_checked`. Final placement, current Runtime state and source-data validation
remain execution-time decisions. Read the structured rejection for the selected
invocation. Remote sources require read-only
accounts; retained import and uploads remain unsupported. Source relations are not
restricted by table form: views and every engine or connector type enter, and Trino
rejects only `$`-suffixed internal tables. Installed
package acceptance is recorded separately from source-tree Runtime evidence.

`md.register()` persists a declaration without loading analysis execution code.
Execution admission loads only the selected backend implementation; a missing
selected dependency fails with a structured repair before opening the source.
Ordinary DuckDB result batches use Ibis execution and an owned Arrow reader.
Colima qualification of the installed Ibis 12 batch interfaces did not qualify
a remote transport switch. PostgreSQL preserves Decimal, timestamps and row
batching, but its default text cursor fails on the anonymous RECORD used by
Dataset identity. A binary server cursor repairs that value, yet closing an
Ibis reader after a partial read leaves its named server cursor and transaction
open. Trino preserves exact Decimal and composite identity values, one-row
batches and submitted SQL, but early reader close leaves the driver cursor open.
ClickHouse preserves Decimal, UInt64, timestamps and composite identity values,
but a SELECT-only account returned a three-row batch for `chunk_size=1`.
PostgreSQL and Trino batch entries also call Ibis pre-execution hooks;
ClickHouse's batch entry collects external tables. The current remote paths do
not invoke these hooks. MySQL and SQLite retain incremental driver transport
because their Ibis batch APIs materialize a complete pandas result first.
Remote source execution requires no write, table-creation or view-creation
privileges. DuckDB may create action-local temporary tables and views.

### Relational and native-date methods

PostgreSQL, MySQL, SQLite, Trino and ClickHouse use exact per-backend admission
for direct-column mean, weighted mean and ratio in addition to Group A. The
complete dependency graph is checked, including predicates and projected-away
components. Same-source relationship paths retain identity, missing-coordinate
and fanout assertions. Each participating relation must meet its backend's existing
physical type restrictions.

Native civil-date axes support single-unit day, week, month, quarter and year
buckets. Snapshot and validity membership use declared boundaries without
automatic source-data identity or time preflights. All five remote backends admit
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
Kendall remains a complete-input local continuation. PostgreSQL admits exact
Event journeys with two or three steps, and Lifecycle replay with two trigger
Events, over unversioned tables with int64 subject and occurrence identities.
Direct PostgreSQL Event funnel, time-to-event and subject selection, and
Lifecycle distribution, transitions, dwell, violations and subject selection
are also admitted. Complete PostgreSQL funnel inputs can continue through the
existing retained comparison and attribution path. PostgreSQL journey queries
use one read-only materialized CTE bundle; replay and direct reducers execute
source-side assertions and outputs in a read-only repeatable-read transaction.

ClickHouse and Trino admit exact two-step Event journeys with first-per-subject
or every-start shared/exclusive matching over the same identity/table shape.
ClickHouse uses a single packet query with materialized CTEs; the reader must
be allowed to set `enable_materialized_cte=1` while remaining read-only. Trino
uses a read-only repeatable-read transaction and separate source count assertions
to avoid duplicating the complete match beyond its stage budget. Its live
acceptance uses Iceberg. Neither path transfers raw occurrences for local matching.
Trino also admits direct ungrouped funnels, first-per-subject time-to-event and
subject selection, plus complete ungrouped funnel comparison through the existing
local continuation. Its grouped funnel reconciliation exceeds the 150-stage
acceptance limit (306 stages), so grouped funnels and dependent attribution remain
closed. Trino and ClickHouse also admit Lifecycle history with two trigger Events
and one int64 component per subject/occurrence identity, under the same table restrictions (Iceberg and MergeTree,
respectively). Native array folds replay every occurrence without a recursive
query depth cap; source-side interleaving exploration proves equal-time
confluence including per-occurrence violation outcomes. Trino keeps all assertions
and parts inside its read-only snapshot. ClickHouse returns assertions, bounded
Evidence, history and all three retained parts in one ordered packet statement
under a shared storage snapshot, without requiring Lifecycle CTE materialization.
Complete retained histories support the existing local distribution, transition,
dwell, violation and subject-selection continuations, including cold recovery.
Direct Trino/ClickHouse Lifecycle reducers and selection remain unqualified.
ClickHouse direct Event reducers and selection remain closed after planner/memory qualification failures. SQLite
and MySQL C9 methods remain closed. These are exact implementation qualifications, not claims
that other engines cannot implement the underlying algorithms. Other Event and
Lifecycle shapes reject through the method registry before source execution.
Sampling, Entity candidates, and source
driver screening remain unsupported on these backends. Remote
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
activated by C3b (see its acceptance record for per-backend and per-format
limits). Trino's strptime and composite hour-prefix axes were qualified later
through [live source execution](../../superpowers/specs/2026-09-23-c3b-trino-parsed-time-acceptance.md).
Trino `date_parse` accepts `%f` with its native millisecond result precision;
the tested malformed cell raised the native parser error before publication. No
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
Projects retain results in local Parquet. There is no analysis result storage
setting, database result storage, automatic destination selection, or
failure-triggered executor retry.
Native DuckDB analysis of immutable retained Parquet is admitted by the owning
registered method; temporary execution relations are not persisted Artifacts.

Primary output and required part reads need not observe the same source state.
Each query uses its backend's current observation. Marivo neither opens a shared
consistency transaction nor rejects or retries solely because intervening updates
occurred. Artifact validations still apply; Event/Lifecycle may re-evaluate
deterministic source relations, including across concurrent writes. Independent
output queries may observe different versions; atomic publication does not
certify a common source snapshot.

One admitted execution creates a Run. Publication commits the primary result,
required private parts, descriptor, Evidence and Findings atomically. Cache hits
on the same exact realization do not invent another Run. Failed or interrupted
Runs never masquerade as successful Artifacts. Store generation 6 is required;
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
mechanical continuations and static Metric/Population source admission, including
the public call and exact Help target for continuations;
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
Metadata, remaining required assertions and output run separately; empty output never
bypasses Artifact validation. Source identity and time data are trusted. Exact
integer/Decimal sums widen internally to Decimal256 before checked output
conversion; overflow and non-finite output fail without publication.

Native row streams preserve Decimal and typed Entity identities without pandas
or raw source transfer to another engine. Each response is owned and closed;
driver close can drain unread data, so cancellation has no hard latency promise.
Connection close does not prove remote termination. Unknown remote status does
not prevent safe local recovery; partial output is never published. No shared
snapshot, execution budget, upload, temporary object or implicit retry is added.
Batch size is transport configuration, not a result cap.
