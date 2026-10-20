# Python Analysis Design

Status: AnalysisFrame cutover contract, 2026-10-19. The final data contract is
[AnalysisFrame](query-result-reuse.md). Implementation progress, local acceptance,
provider Runtime qualification and release gates are recorded independently.

## Layer ownership

Datasource owns declared connections and qualified physical reads. Semantic owns
business meaning, immutable row/Metric expressions, identities, roots, units,
contribution roles, time and operator-local null/empty policies. Analysis binds
those governed inputs to a question, evaluates explicit dependencies and retains
complete immutable data. It does not infer business meaning from physical names.

Population describes finite complete coordinate tuples. AnalysisFrame binds
named values, ordered axes and independent presence to a complete domain. Column
and FrameAxis bind to an exact Frame input. Artifact is the persisted result's
submission/audit view. It cannot supply values or a computation operand.

## Progressive disclosure ownership

`marivo.help("analysis")` starts discovery; focused native Help owns signatures,
constraints, effects, examples and navigation. The canonical types are
`analysis.analysis_frame` and `analysis.frame_axis`. Attribute Help resolves by
string without executing a getter. `.show()` and typed fields own captured facts;
structured errors state expected/received input and a repair from real state.
Skills own investigation judgment, not a duplicate API or syntax inventory.

A Frame/Axis repr is bounded and points to `.show()`. Pending show discloses
structure, captured scope, input kinds and pending operations without evaluating,
loading a Catalog for display, preparing schema or reading parent rows. There is
no generic analysis action contract or capability matrix inferred from history.

## Public objects and explicit Session entry points

| Entry | Meaning |
| --- | --- |
| `session.observe(metrics, ...)` | Bind named resolved Deferred Metrics and explicit observation inputs |
| `session.read(population, fields)` | Bind nonempty named scalar Fields to one complete Entity identity domain |
| `session.values(by=..., columns=..., rows=...)` | Declare literal rows on explicit complete coordinates; empty by is scalar |
| `session.frame(population)` | Bind a general Population key domain, including multi-axis/zero-axis domains and no named values |
| `session.persisted_frame(artifact_id)` | Open exact committed metadata by same-Session string ID, without source/model access |
| `session.population_from(frame, on=...)` | Project complete ordinary result axes onto explicitly supplied Field/Entity bindings |

All data constructors return AnalysisFrame. There is no public Query, execute,
session.query/session.artifact alias, raw carrier constructor or exported subtype.
RawSqlResult retains its independent datasource reader and never enters Frame Store.
The exact retained public export tuple is pinned by the surface contract, rather
than duplicated in runtime rendering.

One type has three carriers:

| Receiver | compute | persist | data/audit reading |
| --- | --- | --- | --- |
| Expression | Evaluate to complete memory Frame | Evaluate, then publish existing result | show describes pending facts; to_pandas/artifact require completion/persistence |
| Complete memory | Return self | Publish existing output without evaluation | show/to_pandas use isolated complete data; artifact requires persistence |
| Exact stored root | Validate/load complete data into memory Frame | Validate commit metadata and return self | show/export read stored data; artifact opens this result's audit |

Use returned Frames: `fixed = pending.compute()` or `saved = pending.persist()`.
The original expression stays unchanged and can evaluate changing sources again.
Derived operations are lazy; their artifact cannot return an ancestor's commit.
Pure carrier conversion preserves result identity, evaluation time and audit.
A new memory binding is distinct from a fingerprint; compatible reopened stored
handles use the same exact stored input identity. Cross-Session operands reject.

## Population and observation binding

Source grouping uses `tuple[Deferred | Grouping | TimeScope | CalendarAxis, ...] | Population`. A native
Field Deferred is shorthand for a field grouping. Grouping is a closed immutable
family with field, identity, interval, calendar and membership kinds, a bounded repr and
source-free `.show()`. `mv.identity(entity)` captures a complete identity without
selecting keys; `mv.identity((relationship, ...), at=instant)` captures a nonempty
connected directed path and infers its target. Single-valued paths and version
anchors retain their existing binding checks. `mv.coordinates(by=(mv.identity(entity),),
keys=((("C1",),), (("C2",),)))` declares a finite identity domain. Each identity
axis value is one complete primary-key tuple, including for a one-column key.
`coordinates` also accepts direct or governed field coordinates and correlated
identity/field tuples; it rejects relationship-routed identity, membership and
time groupings. Literal identities declare references, without proving source presence.

`Population.axes` returns ordered single-coordinate Grouping values. Reusing these
values imports no selected keys. Population stores no membership allocation rules. `by=P`
retains exactly its complete domain and dependencies, including
coordinates without facts; it need not cover source coordinates outside P.
TimeScope in by contributes one interval coordinate; CalendarAxis contributes its
finite calendar buckets. At most one output time axis is allowed. Time plus
ordinary grouping discovers real joint categories then completes their requested
time buckets; an explicit Population supplies its own full keys.


Population and PopulationPredicate retain their raw definition and predicate IR
as private capture details. Callers inspect the captured domain with `.show()`
and reuse its ordered coordinates through `Population.axes`; raw IR fields are not
public attributes or Help targets.

Projection selects or reorders existing coordinates and deduplicates full tuples.
Classify accepts direct same-Entity fields and its existing identity; it rejects
membership, interval/calendar and routed field reads. Selecting only the existing
identity or zero axes uses projection semantics without an attribute read; new
attributes require one corresponding source record per selected identity.
Values accepts field/identity groupings. Metric.per accepts native Dimension/TimeField
and supported groupings or a complete Population, retaining existing sample
restrictions. Membership rules belong only to the consumer's explicit
`memberships=(rule, ...)` parameter on observe or per; they are not coordinates.
FrameAxis remains a result-owned handle requiring population_from.
Public grouping wrappers are lowered to existing axis/allocation records before
fingerprinting, source audit or persistence; Store 13 uses `marivo.frame_output/v2`, including inert interval directories.

Population preserves ordered complete tuples, ownership and its own selection
context. Projection/sets operate on its existing axes; classify and directed
image/preimage use explicit governed source dependencies. A Field Null is a valid
category. An Entity identity includes every nonnull primary-key component and
required business-version anchor. Ordinary fields never become identities merely
because their values look like keys. Same-path definition changes alone do not
reject a resolved input; kind/root/grain/unit/time/null checks still apply.
Business versioned identities remain a separate data contract.

`population_from` requires `on`, including legal `on=()`. Each pair uses an axis
from that exact Frame and a caller-supplied resolved Field Deferred or Entity.
Only ordinary field/complete identity axes qualify; interval, calendar, paired and arbitrary
value columns do not. Projection takes the joint selected tuple and deduplicates,
not a Cartesian product of per-axis keys. Empty input remains an empty filter;
zero axes distinguish empty from singleton `{()}`. Construction reads no rows.
A fixed root fixes selected keys; a pending selector evaluates on each use.
The bridge does not recover original semantic objects, attributes or history.

For contribution filtering, use `where=mv.in_population(population)`.
`by=Population` selects exactly its complete output keys, including absent facts. New source
facts remain a new explicit observation, with their own read time and no promise
of a shared snapshot with selection. Fixed Frame keys use the existing scheduler
and qualified typed transport; no fixed-data upload/fusion or recursive executor
is promised. Source-only Population relations retain separately qualified routes.

Each source reduction occurrence keeps intrinsic conditions and receives common
conditions independently. Multi-root Metrics reduce each fact root under its
own identity/time role before complete-grid combination; joining roots cannot
multiply contributions. Membership retains correlated tuples and independent
allocation factors. Duplicate links for one contribution/bucket count once;
equal-valued distinct facts remain distinct. Each membership rule names coordinates
already present in that consumer's by/samples. Rules cannot overlap coordinates;
outer observe and internal per rules are captured independently. Count alone does
not consume membership paths.

`P.count()` counts complete distinct tuples as a lazy integer Metric with unit 1.
It includes field Null and literal identities without facts. Empty P counts 0;
the zero-axis unit domain counts 1. Ordinary grouped coordinates must already be
in P: retain identity while explicitly classifying a new attribute before counting
members by that attribute. Projecting to the attribute alone counts categories.
Missing groups in an explicit output domain count 0; an empty output domain has
no rows. P and Q count independently; even a count-only formula schedules its
captured domain. A domain without time broadcasts across a supplied time axis;
an existing time coordinate must match its frozen meaning. Fact conditions and
windows do not reselect P; count-only conditions or windows with no fact meaning reject.

`Metric.per(samples, memberships=())` evaluates the whole child Metric at each internal sample
before reduction. Explicit samples include empty samples under the child's
policy. Internal samples and the selected output domain are independent.
Use `where=mv.in_population(P)` for contribution selection without identity output.
When preserving an existing field condition, attach the membership condition to
the fact Metric with `.where(...)` and keep the field condition on observe.
`Population.where` accepts only PopulationPredicate; field predicates belong to
members/read/Frame filtering. Population truth evaluation rejects; it does not
test emptiness. Predicate `.show()` prints bounded boolean structure, and neither
Population nor predicate `.show()` evaluates the domain.

## Time and window authority

Session instant/time_scope freeze exact boundaries. Dates and offset-free strings
use the Session or explicit zone; aware values preserve their instant. Naive
Python datetimes and ambiguous/nonexistent clocks reject. by=() has no time axis;
a hard time predicate does not provide window endpoints. TimeScope contributes an
interval with exact start/end and boundary timezone; a single bucket stays an axis.
A CalendarAxis captures nominal buckets, clipped actual intervals, order, Grain,
timezone and certified snapshot. Its by binding is the endpoint authority.
A snapshot-bound named Grain must match the Session's frozen authority.

Metric.window is the source window attachment. all_history, trailing_duration,
trailing_periods, since and to_date are immutable rules. Every endpoint evaluates
the full child formula over half-open W(t); display start does not clip lookback.
Elapsed durations and civil periods differ across DST. Changing the source
window/calendar requires a new observe; Frame reduce cannot coarsen a calendar.
Frozen Frame interpretation and matching never reload a model or timezone rules.
See [Timezones and Calendars](timezone-and-calendar-design.md).

## Query values and composition

This retained section anchor now describes Frame values; Query is retired.
Supported data operations are select/arithmetic, same-input boolean where, global
top, reduce over existing complete axes, full-axis align/compare and bounded
frozen-calendar match. User value names are one nonempty segment: no dot,
backslash/control characters or reserved left_present/right_present names.
Generated paths use dots. output_names lists only values, in definition order;
presence_names lists independent boolean presence paths. `__getitem__` performs
exact full-name value/presence lookup; coord uses a FrameAxis, never a value name.
Composite axes remain complete handles; coord requires one physical component.

Where/top/select retain axes and presence. Reduce keeps selected axes and drops
all presence. Statistics use independent effective masks. Row mean, distinct or
sum does not regain original contributions: row mean 55 differs from original
1000/91, and summed distinct 4 differs from union distinct 3. New source overall
questions require explicit Metric observation; there is no equivalent reduce
replacement for original rollup/drill/share.

Align preserves left/right value groups and adds left_present/right_present,
then prefixed inherited presence. Compare requires equal value-name sets and
compatible numeric types/units, emitting left/right/absolute/relative groups in
left output order. Relative is `(left-right)/abs(right)`; missing endpoint or NA
makes differences NA, with zero policy applied only to valid pairs. Duration
endpoints/absolute stay Duration; relative is dimensionless. Exact checks full
joint keys; outer preserves left rows then right-only rows. Matching names never
rebind handles. Complete explicit on is required except zero axes or one sole
interval axis or one sole calendar axis per side. Match is an explicit rule, never inferred from periods.
Paired inputs cannot be aligned again in this first surface.

Compare is a high-level data API combining full-key alignment, nullable arithmetic,
validation and standard output construction. The proposed attribute operation is
a high-level explicit-source API combining observation, classification, alignment
and optional proven contribution decomposition. Both belong to the same compiler,
evaluation and publication lifecycle. This architectural direction does not claim
that every step is currently expressible through public methods or that attribute
is implemented. The [composition layers and capability gaps](analysis-implementation-architecture.md#64-compareattribute基础能力组合的高级-api)
own that distinction; [composition examples](attribute-design.md#24-compare-的基础组合示例)
state their numerical and API limits. No public signature changes follow from this
design clarification.
The proposed attribute result is an ordinary AnalysisFrame: its value columns
remain available to select/arithmetic, where/top, reduce and eligible align/compare,
including after persistence and cold recovery. These operations neither recover
source definitions nor automatically certify a new contribution decomposition.
Filtering preserves the original contribution denominator; aggregation across
different analysis totals does not establish a combined share interpretation.

## Definition graph and compiler

Expression construction captures immutable explicit dependencies. Finalization
closes authoring capture; consumers use their completed scope, without live
refresh or import replay. Fingerprints describe consumed definitions; they are
not exact input binding or automatic historical result selection.

Preparation obtains required schemas/connections and validates each use before
sharing physical work. The compiler fixes demands, full keys, stage dependencies,
checks and lifetimes. Source Ibis returns pandas; local stages reuse pandas and
qualified numerical kernels. PyArrow handles storage and qualified key transport.
There is one schedule, with fixed input read sharing and consumer-count release;
no alternate executor, hidden SQL engine or fallback/retry of unsupported methods.
Strict checks occur before pruning and cannot disappear under Top-0/projection.

Native source sum/mean/weighted/ratio routes include PostgreSQL, MySQL, Trino and
ClickHouse with their actual expression/type/topology qualifications. Native
Ibis/provider numerical precision loss is accepted; pandas handoff determines
effective value types. Keys, units, time and null correctness remain strict.
Exact and approximate methods never substitute. Historical source optimization
qualification does not qualify fixed Frame fusion or a new public Frame pipeline.
See [Runtime coverage](../../testing/runtime-coverage.md).

## Terminal publication, reading and acceptance

Evaluate before Run admission. A Run publishes an existing result ID through the
single Store 13 transaction. Memory persist does not evaluate; stored persist
is metadata-idempotent. One result audit retains inherited source facts and its
current evaluation, not producer history. Use runs/get_run and output references for publishing history. Unknown outcome uses original transaction reconciliation.

Complete data has no model, producer graph, connection or aggregate components.
Reopen and fixed operations need no source. Full export/compute validates required
data and attachments. show(n=0) and Artifact audit read no payload; positive show
resolves a saved prefix without claiming unread integrity. Raw-key MultiIndex
export and isolated typed attrs preserve labels/bounds/paired interpretation.
Exact order and presence/NA remain independent.

Store 13 refuses older data unchanged without migration/dual read. Absent-current
discovery may return None/empty without exposing old data; old-only projects
cannot initialize a bypass. See [cutover](analysis-frame-cutover.md). Static,
local, process/slow, provider Runtime and release evidence remain distinct.
