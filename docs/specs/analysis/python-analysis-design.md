# Python Analysis Design

Status: current expression and terminal execution architecture, 2026-10-18.
Native Deferred expressions and the S0–S6 source, sampling, membership and window
routes and the R3–R8 Population/Query operations are implemented. Focused local
checks cover the principal routes; full independent acceptance and any remote
Runtime qualification remain separate gates.
[Lazy DSL](lazy-analysis-dsl-redesign.md) owns the complete target signatures;
this document owns the current objects, boundaries and implementation path.

## Layer ownership

| Owner | Responsibility |
| --- | --- |
| Datasource | Physical schema, credentials, declared source forms, Ibis execution returning pandas |
| Semantic | Entity identity, fields, units, additivity, restricted declarations and native Deferred recipes |
| Private definition graph | Immutable captured expressions, domains, policies and explicit dependencies |
| Analysis binding | This Session's contribution occurrences, complete coordinates, periods, paths and versions |
| Compiler and physical kernels | Schema-qualified routes, demanded slots, dependencies and check deadlines |
| Runtime | Execute the fixed stage schedule, release resources and publish atomically |
| Store 11 and reader | Committed output data and self-contained records; metadata opening and requested reads |

Semantic and datasource authoring begin at [the semantic overview](../semantic/overview.md).
The [implementation architecture](analysis-implementation-architecture.md) describes
module ownership; it does not independently qualify every proposed operation.

## Progressive disclosure ownership

The packaged `marivo-analysis` skill owns investigation judgment: the remaining
evidence gap, meaningful next work, semantic handoffs and completion. It does not
own signatures or an operator sequence. Public `marivo.help("analysis")` routes
unknown intents to bounded navigation; a focused target owns exact inputs,
constraints, effects and a minimal example. Consult only the guidance needed for
the next decision, reusing already established scope and inputs. Related targets
are optional; stop discovery once the next useful call is supported. Complete
supported analytical transformations on Queries before exporting: population,
grain, aggregation, denominator and comparison changes remain analysis.
Formatting and plotting saved values are presentation. Export Help discloses
this boundary; external calculations do not inherit typed Evidence.

For an existing Population or Query, `.show()` discloses captured
logical facts; type Help discloses public operations. These facts do not qualify the
selected backend or prove business suitability. A committed Artifact instead
exposes `.show()` and saved Finding/Evidence reads; it has no contract or
computational continuation. Structured errors own the failed call's concrete
repair. None of these surfaces chooses the business conclusion.

Logical cards include a bounded captured input scope: member selection,
complete output coordinates and input operations retain their distinct roles.
Omitted detail is explicit; this is not a full definition serialization.
Execution saves the same kind of scope summary as declaration Evidence together
with the exact definition fingerprint, so it remains readable without sources.
Read Evidence filters describe only their own physical source stage; an absent
read-local filter does not imply the Query has no member or population condition.
Neither a scope declaration nor a successful read proves source completeness.

Catalog discovery keeps definition metadata separate from executable values:
collection `.show()` lists choices, `catalog.describe(ref).show()` explains a
definition, and collection `.get(...)` or `catalog.require(ref)` supplies its
current value or entry. Resolved Metric and Field expressions are native Deferred
values; calling `.show()` on them does not inspect a catalog definition.

## Public objects and explicit Session entry points

`Population` is a finite logical coordinate domain. `Query` is a lazy, fully
bound calculation with named quantities. `Column` locates a value or coordinate
on one exact Query input. `Artifact` is committed terminal output.

These objects are the sole analysis DSL. Query, Population and Column expose
metadata-only `.show()` cards; Artifact reads committed output. Generic
`AnalysisContract` and `AnalysisAction` values are removed. Query.output_names
returns all output names in definition order. Operation discovery belongs to
static type Help; attribute Help uses an exact string target and never reads
an instance or executes a getter.

```python
import marivo
import marivo.analysis as mv
import marivo.semantic as ms

session = mv.session.get_or_create("revenue-review", report_timezone="UTC")
revenue = session.catalog.metrics.get("sales.revenue")
region = session.catalog.fields.get("sales.orders.region")
period = session.time_scope(start="2026-08-01", end="2026-09-01")
query = session.observe({"revenue": revenue}, during=period, by=(region,))
query.show()
marivo.help("analysis.query")
names = query.output_names
artifact = query.execute()
artifact.show(n=20)
exported = artifact.to_pandas()
```

The example requires an authored project and a qualified source. A bare Metric
Ref is a lookup key, not an executable expression. Named and anonymous inputs
use the same controlled native Ibis Deferred carrier. Model loading owns naming
and persistent dependency closure; `@ms.metric()` does not create an executor.
No top-level `marivo.session`, fixed-result input or manual type-preparation API
substitutes for these entry points.

`session.members(entity, at=..., where=...)` selects existing complete identities;
`mv.identities(entity, keys=...)` requires a resolved Entity from the completed
finalization scope or `session.catalog.entities.get(entity_ref)`. A bare EntityRef
is a lookup key and is rejected here. Explicit identity references do not prove
source presence. `session.query(population)` creates a keys-only Query.
`session.values(by=..., columns=..., rows=...)` captures typed literal references
on Field or complete identity coordinates; Calendar and Correspondence axes are
not writable scalar keys. A zero-axis singleton and a zero-row empty domain
remain different inputs.

`session.read(population, fields)` is the sole attribute-read entry. It accepts
one complete Entity identity axis and a nonempty named field mapping, retains
every requested identity, and returns a Query. It binds a pure literal domain to
the explicit Session and rejects an incompatible existing owner. It never looks
up an ambient Session. There is no `Population.read` alias. Direct declared Fields
are admitted here; computed Measure expressions belong in observe.

```python
customers = session.members(ms.ref.entity("sales.customers"))
tier = session.catalog.fields.get("sales.customers.tier")
attributes = session.read(customers, {"tier": tier})
gold = attributes.where(attributes["tier"] == "Gold").population()
```

For a versioned field, bind `tier.at(session.instant(...))` explicitly. Scalar
paths use `field.via((relationship, ...))`; ambiguous, missing or fanout paths
reject. Version selection and observation periods are independent. A required
attribute mapping that is missing or nonunique fails when consumed, rather than
silently dropping identities or replacing the lookup with ordinary NA.

## Population and observation binding

A Population preserves ordered, complete axis tuples, Session ownership and its
own selection context. Projection, set operations and membership predicates
operate on those coordinates. `Query.coord(axis)` constructs a Column without
source access; preparation rejects an axis with multiple key components as a
scalar quantity before Run admission or business compilation/reads. Complete
composite identities remain valid output coordinates, Population tuple predicates
and export keys. Use an existing single-field axis for a scalar value Column.
`classify` explicitly reads single-valued member fields; `image` and `preimage`
use declared directed identity paths. Ordinary fields never become Entity
identities merely because their values resemble keys.

`Query.population()` captures the Query's current keys and selection as a lazy
dependency. Reusing it does not fix a previous execution's rows. Its period,
conditions and attribute anchors are not overwritten by the consuming observe.
Population has no `execute`; publish through `session.query(population)`.

For qualified DuckDB, PostgreSQL, MySQL, Trino and ClickHouse consumers, a
single-consumer Query-key producer made of source field reads, filtering and
direct projections can become a source-local key subquery. Source ownership comes
from the prepared connection and its bound
physical inputs. The consumer retains complete-tuple, null-safe membership;
producer keys do not cross the host boundary. Source-wide identity and attribute
cardinality checks retain their original scope before selection, including empty
selections. Trino and ClickHouse numeric field values normalize NaN to missing
before source filtering and ranking; complete numeric identity coordinates retain
their separately qualified boundary. DuckDB also admits
ordinary single-reduction key producers, direct-slot Top-N, and shared key
producers whose scalar consumers execute in one qualified source statement.
PostgreSQL, MySQL, Trino Iceberg and ClickHouse MergeTree additionally admit
qualified count producers and string-only terminal field selections, filters,
Top and row counts. MySQL grouped string keys require binary NO PAD collation;
ClickHouse FixedString retains its existing encoded carrier boundary. Nullable
numeric attributes on PostgreSQL/MySQL retain their separately qualified boundary.
Trino/ClickHouse admit direct integer, float and Decimal attribute filtering and
Top, plus supported sum/min/max/mean/weighted-mean aggregate producers. These
operations use native source values before pandas conversion, accepting its
rounding differences while preserving missing values, complete-key tie breaks
and required parent failures, including Top-0. Qualified DuckDB, Trino and
ClickHouse also admit checked multi-input Metric formulas and Column arithmetic,
including computed Top expressions. Every logical binding retains its own complete
grid, presence and checks. Floating NaN is normalized to missing; nullable division
applies its zero-denominator policy to valid pairs, and Top-0 cannot erase required
formula or overflow failures. Qualified DuckDB, Trino and
ClickHouse shared consumers may transport independent grouped leaves in one source block,
with separate complete grids, presence and host formula finishes. Unsupported
operations and cross-source dependencies retain their materialization boundary.
Qualified cell sums use native integer, float or Decimal accumulation, accepting
source rounding while retaining null results for empty/all-missing input and
required finite-value and range checks. Duration retains its unit-aware finish.
Mixed host/source consumers keep the producer where a real host domain needs it,
while eligible source edges use its relation without uploading keys. Compatible
per grids may share a grouped block; different per or window grids finish
independently while keeping the shared population source-local. Each window
retains its own W(t); this does not promise one physical scan.
When a real host sample/output domain has already completed a where/Top selection,
other consumers retain its fixed complete keys, including necessary parameter
transport. They cannot rerun that selector against changed data to replace the
completed domain; source contributions themselves may still change.
Qualified DuckDB Population set operations, products, projections and filters can
stay in the same source relation; terminal union retains the left-first display
order. Multiple parameter producers for one read are replaced atomically, so a
shared ancestor is not pruned while another producer still needs it.
For same-source image/preimage chains, ancestor domains used only for checks
are reduced to check summaries in the consuming relation. Terminal domains,
MemberCount inputs and keyed member data remain real inputs. A Query-produced
preimage can consequently stay source-local without downloading the ancestor
identity domain or sending the selected keys back to the same source.

Source-local filters preserve the local nullable True-only predicate contract.
PostgreSQL string comparisons use UTF-8 bytes after a JSON string round-trip that
preserves fixed-width character padding. MySQL comparisons use a JSON string
round-trip and binary bytes, preserving decoded characters across source character
sets independently of collation and trailing-space comparison rules. Trino uses
UTF-8 bytes and ClickHouse uses native String byte comparison. These
conversions affect comparison operands only; published values and complete
membership keys retain their existing contracts. String literals containing
U+0000, timestamp literals with submicrosecond pandas precision, and Duration
literals retain the host boundary. Native compilation and the selected source's
existing physical qualification still apply, including ClickHouse join topology.

When Query keys must cross the DuckDB source boundary, they use an invocation-local
Arrow relation with the planned key types. Consumers reuse that relation within
its owning source connection without expanding each key into SQL predicates or
literal unions. The source owner admits only its own key relations alongside
qualified business sources and removes them when it closes, including failed
invocations. These are physical arrangements; neither changes the captured Query
dependency nor persists a previous execution's population. PostgreSQL int64,
string, boolean and date key tuples can use typed arrays with one ordinal
expansion. This bounds expression structure; the key payload still grows with
the domain. PostgreSQL membership string comparisons preserve returned CHAR
padding through the same JSON/UTF-8 byte route. Existing native Metric membership
keeps its source collation semantics; byte comparison applies to newly moved
host Query relations. MySQL complete int64/string/boolean/date tuples can use
one invocation-local JSON membership object instead of per-key predicates;
string components require verified binary NO PAD source collations. Other
collations keep their native membership semantics. NULL, empty strings and
complete tuple association remain distinct, including integers beyond 2**53.
The object uses the active session's packet capacity and connection encoding;
encoded JSON member lengths, document storage and final SQL request size are
checked before the target read. Oversized domains fail with a repair to reduce
the selected keys or adjust the datasource limit, rather than splitting and
merging completed aggregates. Other key types and relation shapes retain their
admitted inline route outside the qualified fusion subset.
ClickHouse source compilation checks the active client's `max_query_size`
override, or its cached server setting, against UTF-8 SQL including the driver's
`FORMAT Native` suffix. Missing connection facts retain native enforcement;
zero disables this parser-size limit only. Array transport bounds the expression
tree, not request bytes. Trino's coordinator query-length configuration is not
exposed by its current connection, so its typed-array route does not promise an
arbitrarily large key domain. Parser depth, memory and HTTP/proxy limits remain
native restrictions; no guessed universal key-count threshold is introduced.

`session.observe(metrics, during=..., by=..., where=..., members=...)` binds the
observation once. A tuple `by` classifies observed coordinates; a Population
requests a complete output domain. The requested domain must cover every consumed
contribution coordinate. `members` is a separate single-identity constraint and
does not imply that this identity is an output axis.

`member_count()` can be the only observed Metric, including on an empty or
literal identity domain. Its member domain is an execution dependency even when
no source reduction contributes values. Common conditions must be single-valued
on that identity domain; they do not replace the selected members.

Each source reduction occurrence retains its intrinsic conditions and receives
common conditions independently. Multi-root Metrics reduce each fact root under
its own identity and time role before combining complete output keys. Relations
must not multiply a fact root's contribution merely because another root has more
rows. Membership descriptors retain correlated tuples within one descriptor and
independent allocation factors between descriptors. Repeated mappings of one
contribution identity to one bucket count once; equal-valued distinct facts do not.

`Metric.per(samples)` evaluates its entire child Metric at each internal sample
before reducing. An explicit sample Population selects complete internal samples,
including empty samples under the child policy. Internal subset selection does
not imply external output-domain coverage. Boolean predicates retain ordinary NA
logic; source Null Field coordinates remain valid complete keys.

## Time and window authority

`session.instant` and `session.time_scope` are the only analysis time constructors;
there are no module-level aliases. They freeze the Session or explicit timezone.
Scope boundaries are exact UTC `InstantAnchor` values. Dates and offset-free
strings use that zone; aware inputs preserve their instant. Naive Python
datetimes and ambiguous or nonexistent wall clocks reject.

`scope.by(mv.grain("day"))` captures nominal buckets, clipped actual intervals,
order, timezone, Grain and any certified calendar snapshot. Calendar products
retain factor order and exact bindings. A calendar does not infer a Metric
window: window endpoints are bound through the observation's `during`.
`during=None` adds no ordinary time filter, while temporal sampling and window
closures still require their own explicit period or endpoint.
When `by=Population` already contains an observation calendar, it must match the
calendar supplied by `during`; conflicting bindings reject during construction.

Metric.window is the only window attachment. `all_history`, `trailing_duration`,
`trailing_periods`, `since` and `to_date` are axis-free immutable rules. Each
endpoint evaluates the full child formula over its own half-open W(t); the display
start does not clip an earlier contribution window. Fixed durations use elapsed
time; periods use the complete civil or certified calendar. Scalar to-date selects
the reset period with `left < endpoint <= right`. Nominal calendar buckets also
require unique containment. Windowed original rollup must preserve the same
calendar axis and endpoints; changing the window requires a new observation.

## Query values and composition

Query owns value selection, filtering, Top-N and result reduction. A Column can
be used only on its exact input; cross-input arithmetic requires explicit
alignment or complete reference transport. `select` preserves output names and
units. `top` breaks ties by complete keys and preserves its selected display
order. `reduce` provides sum, mean and row_count over current represented cells;
it does not recompute the original Metric.
For Duration expressions, a zero `timedelta` literal adopts the other operand's
exact tick unit; nonzero operands retain the matching-tick requirement.

Source recomputation by rollup/drill may read changed data after the parent read.
Parent where/Top completes its checks and fixes complete selected keys; the target
recomputes the original formula over current contributions under those keys and
the captured period and predicates. It does not rerank the parent. Inserts,
updates and deletes may change the answer; empty contributions retain their
existing policy. No shared snapshot, cross-read equality check or retry is required.
This applies to every datasource, including DuckDB, PostgreSQL and MySQL. Neither
a base-table kind nor a transactional storage engine is required to keep the
parent and recomputation data unchanged. Physical types, key collations and
numeric-method qualifications remain independent requirements.
New drill classifications may follow declared scalar paths to related Entities
after filtering, ranking, original-column selection or original rollup, under
the same selected-parent and attribute-version rules.

Alignment, comparison, broadcast, original rollup, endpoint drill and share follow
the [target operator contract](lazy-analysis-dsl-redesign.md#46-查询结果与值表达式)
and require their qualified physical route. Correspondence checks complete unique keys;
presence is independent of nullable values. A relative comparison divides the
absolute difference by the absolute baseline and owns only its new denominator
policy. Decimal comparison arithmetic is independent of the caller's Decimal
precision context. Matching-tick Duration endpoints retain Duration current,
baseline and absolute columns; their relative difference is numeric. Unknown
endpoint kinds remain unresolved until schema preparation.
General arithmetic never acquires a share contribution proof.

## Definition graph and compiler

Construction, repr, Help and logical object inspection read no business rows or schemas
and create no Run. Captured definitions remain immutable. At a reference-resolving
consumer, same-path definition changes alone do not reject the input; kind, root,
complete grain, unit, time and null constraints still apply. No live refresh,
module replay or source snapshot is inferred. Fingerprints describe the definitions
and policies actually consumed and are not saved in the Artifact.

Execution preparation resolves real source schemas, supported physical type families,
connections and per-use paths before passing source-free facts to the compiler.
Each field occurrence is validated before sharing its physical column, so a native
read cannot hide a later invalid anchor or route. Datasource Timestamp and Duration
metadata remains authoritative across Entity projections. The 2026-10-17 revised
source contract accepts native Ibis/provider numerical precision loss. Ibis lowering
adds no casts solely to standardize backend result dtypes or preserve transport
precision; conversions required by business semantics remain. Backend return-type
differences are handled at the existing pandas-to-StageOutput boundary, retaining
keys, null policies, units and time semantics. Downstream stages and publication
use the resulting effective types; casting cannot recover lost source precision.
This applies to direct fields, reduction values and internal components. Preparation
qualifies the actual backend's expression and method before Run admission; source
execution uses its native Ibis-to-pandas path. Unsupported exact methods reject
without using an approximate substitute. See the
[source handoff contract](lazy-analysis-dsl-execution.md#151-ibis-原生返回与-pandas-类型交接)
and [remote implementation sequence](../../superpowers/plans/2026-10-13-lazy-analysis-dsl-redesign-implementation.md#81-远端数值-query-最小实施顺序).

The compiler fixes source IDs, demand slots, complete keys, stage dependencies,
method routes and consume/publish checks. Source Ibis executes through the selected
datasource and returns pandas. Local stages use pandas and the existing numerical
kernels. PyArrow encodes terminal data and invocation-local transferred key
relations. No hidden local SQL
engine, Artifact scan, alternate executor or runtime route retry is introduced.
Current optimization qualification includes finite filter fusion, RequiredSlots,
invocation-scoped reuse, same-input exact direct-source sum/count blocks and L8
adjacent single-consumer original rollups over identical ordinary components.
The latter routes admit exact int64/Decimal components and direct retained-axis
maps; they preserve every ordered intermediate finish and overflow check. Typed
prepared input authority, not Ibis AST equality or Entity names, establishes a
common source. Independent occurrence/value/check slots and consumer counts
survive one physical read. Float sums, internal samples, windows, differing
policies, pending checks and selection/share or multi-consumer barriers retain
their separately qualified routes. This is finite positive qualification, not
general permission to fuse source computations or original reductions.
Remote source blocks retain the unfused route until their own numerical and
behavioral equivalence is established. Backend-specific time preparation and
temporal rewrites use the actual selected engine for observe, read and drill.
A required check consumes its actual producer output before the declared deadline;
selection or projection cannot erase that obligation.

Qualified DuckDB terminal field chains keep direct projections, nullable filters,
Top-N and exact int64 cell sum/row_count at the source. Top display order is
restored locally over the bounded returned rows. Qualified ordinary original
rollup, drill and additive share can also keep contribution identities and raw
values at the source. Multiple roots on one DuckDB source session reduce
independently and align completed grids, preserving each root's identities and
empty policies. Parent selection and every required fine-grid check remain
part of the plan, including empty selection. Qualified integer mean/weighted
mean and integer quantiles transport compact exact states to the existing host finisher instead
of changing the original arithmetic. A plain observation followed by spatial
rollup can transport grouped internal samples and bound calendar/window leaves
in one statement. The existing local kernel checks the fine formula before
finishing the target formula, preserving explicit finite empty samples, complete
internal identities, endpoint occurrences and lookback periods. Source-backed
sample domains retain their independently captured period and travel in the same
statement. Sample/window parents with Top-0/Top-1, or a coordinate predicate
fixing every removed coordinate, can finish selection locally over grouped
leaves before finishing the selected target. Complex drill retains the full
selected parent key and checks added attributes after selection. Nested plain
spatial rollups retain every intermediate finish and overflow check. DuckDB
originals qualify typed table and CSV/Parquet/JSON inputs with their actual owner
and collation facts. Explicit/default collation or unverifiable inherited view
collation retains the existing boundary. Selected file recomputation passes fixed
parent keys to a later read; it does not rerun the parent selector against changed
file data. Direct Query
string comparisons, ordering and membership use UTF-8 bytes, preserving pandas
case-sensitive behavior even on collated DuckDB fields. Float and Decimal
source reductions use qualified native completed values; fractional values do
not pass through integer-state truncation. DuckDB/PostgreSQL Decimal median and
quantile recomputation uses the existing exact native method, accepting its
rounding without requiring the local rational interpolation result. This does not
substitute approximate quantiles on other providers. Other sample selections
and unqualified temporal rewrites retain their separately
qualified routes. Plain Calendar sums and their Metric formulas can return grouped
leaves independently from each source owner. The existing host formula checks the
complete fine parent before target reads begin. Each root retains its own time
field, period, filters and identity. Legal plain Calendar coarsening, such as day
to month, regroups original contributions using the target Calendar; it does not
sum completed parent values or relax the fixed time-axis contract for windows.

PostgreSQL and MySQL original recomputation have their own finite qualification: direct,
signed integer, float or Decimal values, including nullable operands, and complete
nonnull identities from table relations,
supported source methods, and deterministic string
coordinate collations (MySQL requires binary NO PAD). Sum/count/distinct/min/max
and PostgreSQL integer quantile bounds
retain their owning methods; share uses the original local integer division.
Integer mean/weighted-mean use compact states; fractional native values retain
their source numeric finish. Qualified row arithmetic and Metric formulas follow
their existing numeric lowerers, retaining NULL, overflow and division checks.
Multiple roots on the same source session reduce independently, then align
completed grids through a distinct complete-key domain and left joins; their raw
facts are never multiplied across roots. Qualified parent where/Top can finish on the
host and parameterize a later source read with complete selected keys, without
transporting the parent's raw contributions. No fidelity-only
numeric cast or alternate cursor transport is added.

Qualified DuckDB, Trino and ClickHouse align, compare and broadcast join source endpoint relations and
return only the joined carriers and independent check summaries. The existing
host relation kernel owns exact-key correspondence, missing-cell policies,
arithmetic and final layout/order. Presence is distinct from a present null
value; downstream Top-0 cannot erase endpoint failures. The Trino/ClickHouse
route currently admits complete string axes. ClickHouse explicitly makes outer
join input carriers nullable, including presence, preserving absent sides under
both `join_use_nulls=0` and `1`. Other remote relation joins,
transformed/calendar correspondences and Top-ordered broadcast
targets retain their original route.
Trino and ClickHouse original recomputation is qualified by the actual physical
input, supported expressions and native pandas handoff. Parent and target reads
need no shared snapshot. For selected recomputation, the checked parent finishes
where/Top on the host and passes only its complete selected keys to a later source
read. The target retains those keys even if another group later ranks higher.
Typed arrays transport the key tuples without a per-row UNION expression tree;
the request still grows with the selected key data.
The finite ordinary route admits same-session table roots with signed integer,
float or Decimal operands, including nullable values, qualified row arithmetic
and Metric formulas, and string coordinates:
sum/count/min/max/exact distinct and qualified native mean/weighted-mean.
Trino and ClickHouse also admit integer mean/weighted-mean states finished by the
existing host owner. Native source calculation and pandas conversion may lose
precision relative to the original host route, including Trino compact Decimal
totals. This accepted rounding difference does not trigger fallback. Subsequent
filters and rankings use the recomputed values. The parent retains its existing
numeric method before value selection, and completed parent selections stay fixed.
Trino does not require Iceberg; ClickHouse currently requires MergeTree.
Same-owner Trino/ClickHouse spatial rollups of per/window formulas transport
grouped sample leaves for both fine and target phases. The existing host formula
kernel retains sample identity, captured sample domains, lookback rows and fine
errors, including empty policies and zero denominators. It never averages already
completed parent means to obtain the rolled-up sample mean.
Selected per/window spatial rollups first complete the parent formula and
where/Top on the host, then pass complete parent keys to the source. Multiple
selected parents sharing a sample are recomputed from their combined contribution
identities, not from their finished parent means. Window selection retains the
complete endpoint/sample tuple. A source change may alter target contributions
but cannot rerank the completed parent selection.
Other cross-owner complex formulas, remote sample-domain reads, complex sampled drill, numeric
coordinates and Distributed topology retain their existing routes.

## Terminal publication, reading and acceptance

Every admitted execute is a new Run evaluating its inputs. Shared definitions do
not promise common reads, stable source versions or a cross-source snapshot.
A successful invocation atomically publishes typed output, necessary attachments,
Findings and actual Evidence. Mean components, internal sample relations and other
execution-only state are released, not made into a saved continuation.

Artifact exposes `id`, `session_id`, `show(n=20)`, `to_pandas()`, `findings()` and
`evidence_digest()`. It has no contract, Column, Population conversion or DSL
method. `session.artifact(id)` opens exact committed metadata without current
Catalog/source access or Parquet scans. New and reopened handles share the same
reader; preview is bounded, complete export is isolated, and consumed receipt
verification can still read a whole part. Store 11 retains only same-Session
Run-to-Artifact production topology, with no consumed Artifact references. Store 10
and earlier formats reject without rewriting or migration. Detailed transaction,
concurrency and resource contracts belong to [Runtime](session-state-and-runtime.md).

Static/type checks, local DuckDB tests, remote Runtime tests and release acceptance
are different evidence. Source capability follows the exact backend, dtype and
method route; a successful preview or an API signature does not qualify all routes.
The current DSL excludes specialized statistical/forecast/attribution result
families. Export to pandas for those tasks, preserving full keys and NA/presence.

`observe` makes the output grain explicit: `during=TimeScope` filters only; a
`CalendarAxis` adds time buckets. Empty `by=()` evaluates overall Metrics per
bucket (or one scalar without time buckets). A grouped Query can produce
subtotals through `rollup(by=retained_axes)`, which recomputes original ratios
and averages from contributions rather than reducing displayed values. Different
requested grains produce separate Queries and committed Artifacts.
