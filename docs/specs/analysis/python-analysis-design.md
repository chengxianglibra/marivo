# Python Analysis Design

Status: current expression and terminal execution architecture, 2026-10-17.
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
| Store 10 and reader | Committed output data and self-contained records; metadata opening and requested reads |

Semantic and datasource authoring begin at [the semantic overview](../semantic/overview.md).
The [implementation architecture](analysis-implementation-architecture.md) describes
module ownership; it does not independently qualify every proposed operation.

## Public objects and explicit Session entry points

`Population` is a finite logical coordinate domain. `Query` is a lazy, fully
bound calculation with named quantities. `Column` locates a value or coordinate
on one exact Query input. `Artifact` is committed terminal output.

These objects are the sole analysis DSL. The earlier Logical/Materialized
receivers, graph/Cell publisher and `analysis.runtime_metric` forwarding module
are retired. Current contract cards share only the lightweight `AnalysisAction`
and `AnalysisContract` values; they do not import the retired DSL or restore
executable receivers from saved outputs.

`Query.contract()` and `Population.contract()` return a logical `AnalysisContract`
with public fields `kind`, `domain`, `quantity` and `actions`. Each immutable
`AnalysisAction` carries a receiver `call` and its canonical `help_target`.
The bounded card adds captured definition facts without schema or business reads
or Run admission. `Artifact` remains terminal and has no contract.

```python
import marivo
import marivo.analysis as mv
import marivo.semantic as ms

session = mv.session.get_or_create("revenue-review", report_timezone="UTC")
revenue = session.catalog.metrics.get("sales.revenue")
region = session.catalog.fields.get("sales.orders.region")
period = session.time_scope(start="2026-08-01", end="2026-09-01")
query = session.observe({"revenue": revenue}, during=period, by=(region,))
contract = query.contract()
contract.show()
contract.actions[0].show()
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

`session.observe(metrics, during=..., by=..., where=..., members=...)` binds the
observation once. A tuple `by` classifies observed coordinates; a Population
requests a complete output domain. The requested domain must cover every consumed
contribution coordinate. `members` is a separate single-identity constraint and
does not imply that this identity is an output axis.

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

Alignment, comparison, broadcast, original rollup, endpoint drill and share follow
the [target operator contract](lazy-analysis-dsl-redesign.md#46-查询结果与值表达式)
and require their qualified physical route. Correspondence checks complete unique keys;
presence is independent of nullable values. A relative comparison divides the
absolute difference by the absolute baseline and owns only its new denominator
policy. General arithmetic never acquires a share contribution proof.

## Definition graph and compiler

Construction, repr, Help and contract inspection read no business rows or schemas
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
kernels. PyArrow belongs to terminal encoding and reading. No hidden local SQL
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
verification can still read a whole part. Store 10 rejects older graph formats
without rewriting them. Detailed transaction, concurrency and resource contracts
belong to [Runtime](session-state-and-runtime.md).

Static/type checks, local DuckDB tests, remote Runtime tests and release acceptance
are different evidence. Source capability follows the exact backend, dtype and
method route; a successful preview or an API signature does not qualify all routes.
The current DSL excludes specialized statistical/forecast/attribution result
families. Export to pandas for those tasks, preserving full keys and NA/presence.
