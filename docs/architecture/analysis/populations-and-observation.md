# Populations and Source Observation

Analysis source construction captures explicit business definitions and domains.
It does not read source schemas/rows or create a Run. Source evaluation happens
through AnalysisFrame.compute/persist, after preparation checks actual types,
method support and complete bindings. Fixed Frame operations consume saved data
without restoring those source definitions.

## Entry responsibilities

| Entry | Captured question | Output |
| --- | --- | --- |
| session.members(entity, at=..., where=...) | Source identities with exact membership/version scope | Population |
| mv.coordinates(by=..., keys=...) | Literal complete coordinates | Population |
| session.observe(metrics, by=..., where=...) | Named Metric evaluation on an explicit output domain | AnalysisFrame |
| session.read(population, fields) | Nonempty attributes on one Entity identity domain | AnalysisFrame |
| session.frame(population) | Complete keys of a general Population | AnalysisFrame, possibly with no values |
| session.values(by=..., columns=..., rows=...) | Literal keys and named values under the Session owner | AnalysisFrame |
| frame.attribute(by=...) | Fresh contributions for retained source targets and fixed parent keys | AnalysisFrame |
| session.population_from(frame, on=...) | Explicit selected ordinary-key projection into a new semantic binding | Population |

Use closed Entities/fields/Metrics selected from the Catalog.
Native lookup symbols require loader binding before analysis. Frame objects, artifact IDs, pandas tables and
RawSqlResult are not source Metric operands. Session is the context for Catalog,
execution owner, Store and frozen default timezone. Source-free descriptions and
compositions at mv inherit their input owner when needed; cross-Session inputs fail.

`session.observe(metrics, by=())` requests one overall, zero-axis cell; it does
not supply an observation time range. A time-related Metric does not universally
require a scope. Time-sample mean and weighted_mean can use represented samples without a
TimeScope or CalendarAxis in `by`. Other time-sample reductions retain their
period requirements. Metric-specific sample coverage requirements still apply. Some checks run
only during `compute()` or `persist()`, so successful capture alone does not prove
that an unbounded observation is supported. When a period is required, pass
`by=(session.time_scope(start=..., end=...),)`.

## Population identity and set semantics

Population represents finite complete coordinate tuples, with explicit source or
Frame dependencies. A zero-axis domain can be empty or the singleton {()}; it
is not automatically one business member. A Field Null is a valid category.
An Entity identity contains every ordered non-null primary-key component.
A plain customer_id Dimension does not become a Customer identity.

members returns source-represented identities. Versioned Entities require their
exact instant anchor; the observation's later period cannot supply membership
time implicitly. Literal identity coordinates state requested keys without
proving source records exist. See [versioning](../semantic/semantic-object-model.md#versioning-snapshot-and-validity).

Source analysis trusts declared identity, relationship cardinality and version
grain. Physical identity columns may be nullable in source metadata: semantic
`primary_key` supplies the complete, non-null identity assumption for ordinary
observations, shared aggregates and original Metric evaluation as well as Attribute.
Nullable metadata alone does not disqualify an otherwise qualified source binding.
Analysis adds no full-source NULL or uniqueness precheck; declaration violations
are not guaranteed to raise an error in every aggregate path.
Scalar reads join directly without rechecking uniqueness or requiring a
matching record. Missing scalar matches produce NULL; read/classify retain the
requested identity domain with NULL attributes. Relationship traversal uses its
actual joins and set semantics when a source record is absent. Invalid declared
uniqueness can multiply contributions; analysis does not repair it by choosing an
arbitrary row. Use explicit source_check for source data-quality expectations.
Actual identity outputs must still be non-null complete keys. Required type,
captured NULL/domain, operation-specific coverage and stored-output integrity
checks remain. Their numeric requirements follow the owning
[arithmetic and method contracts](operators-and-frames.md#null-empty-and-arithmetic-policies),
rather than a separate declaration-audit policy.

project selects/reorders existing axes and deduplicates complete projected
tuples. classify reads explicit attributes from one identity Entity when new
fields are needed; existing-axis classification is projection. product combines
Population and calendar/interval factors without guessing correlated membership.
union/intersection/difference require compatible complete bound axes.
Population.where filters membership leaves over existing coordinates, without
implicitly acquiring new attributes. Full predicate grammar is owned by
[Semantic expressions](../semantic/expressions.md#predicates-and-scalar-paths).

image(via=(...)) maps identities along a declared directed path and preserves
complete target foreign-key identities even if a target archive is absent.
preimage(targets, via=(...)) selects receiver identities reaching any target;
relationship multiplicity does not duplicate identities. The receiver bounds
the returned set. An order's customer image followed by preimage can include
other orders of that customer, so it does not reconstruct the original subset.
Paths must be explicit, contiguous and legal for the consumer.

## Observation domain, contributions and counts

observe accepts one closed declared Metric, a nonempty tuple of them, or a
nonempty ordered Mapping of explicit names to closed Metric expressions. Only
the consumed root DeclaredMetricRef supplies a shorthand local name; added
expressions and direct source reducers/counts require Mapping. Duplicate or
invalid names reject at capture; tuple/Mapping order fixes output order. Names
do not establish Metric identity or business comparability. The
[Frame naming contract](analysis-frame.md#名称与-presence) owns these rules.
by=() requests a zero-axis cell. An ordered tuple may contain fields, ordinary
Grouping rules, TimeScope intervals and CalendarAxis buckets. A Population in by
selects exactly its complete output keys, including those without facts.
Population.axes carries coordinate rules, without the selected keys or allocation.

An implicit output grid cannot combine CalendarAxis buckets with the raw field
of the same bound contribution time role (including its relationship path and
instant anchor). Capture rejects this combination before source access, rather
than completing every distinct timestamp against every bucket. Use only the
CalendarAxis for bucketed output, or a TimeScope plus the raw time field for
timestamp-level output. Different time roles and ordinary calendar-by-category
grids remain valid. An explicit Population fixes joint output keys and can
intentionally retain both coordinates without implicit grid completion.

Within one observation, compatible ordinary sum/count/mean/min/max contributions
share a grouped SQL aggregate, including different root fields and matching
calendar buckets. Compatibility includes the actual source, relationship and
version reads, time role, contribution range, predicates, memberships, output
keys and NULL policy. Logical occurrences retain independent values, types and
checks. Sampling, windows, Duration and dependent input boundaries keep their
own execution routes. Later native terminal composition may put independent
aggregate blocks into one statement; statement count alone does not prove scan
sharing. See [optimizer coverage](algebra-theory.md#实现编号与理论定律的对应).

A member without fact contributions has sum zero and count zero; a member with
facts whose values are all NULL has sum NULL and a positive contribution count.
Use a contribution count, not count(value) or the completed Frame row count,
to distinguish them. Mean/min/max remain NULL without valid samples. Empty
Population output has no rows. Source execution failures remain errors. Missing
scalar matches follow the NULL policy above. In per(P).mean(), zero-valued no-fact members participate; unknown
member values are skipped unless strict sample NULL checks are requested. Thus
an ignore-NULL mean is not a complete-Population mean when any member is unknown.

where constrains fact contributions; it is independent from by's output keys.
A selected output domain does not silently filter or renormalize contributions;
consumed contribution keys must satisfy the external domain's coverage contract.
Use where=mv.in_population(P) explicitly when the question restricts facts to P.
Internal explicit sample domains can instead select their own subset.

P.count() captures its complete distinct tuples as a lazy integer Metric. It
includes Null categories and explicit identities without source records. Empty
P counts zero; the zero-axis unit domain counts one. P and Q count independently.
Grouping matches only coordinates already in P: to count identities by a new
attribute, classify while retaining identity first. Projecting identity away
counts distinct resulting categories. Fact conditions/windows do not reselect a
captured count denominator; ineffective count-only conditions/windows reject.
Time coordinates broadcast a timeless count or match its existing frozen axis.

Qualified same-source counts on DuckDB, PostgreSQL, MySQL, Trino and ClickHouse
count distinct complete Population tuples in the source, carrying required
check summaries instead of downloading unused identity domains. Companion
Metrics can retain independent bounded reads. Calendars and internal-sample
domains keep their separate qualifications. When the necessary contribution
keys and output Population are source-representable, a NULL-safe anti-join
checks complete-tuple coverage before cropping and returns bounded failure
coordinates. Fixed and cross-source domains retain local coverage checks and
are not implicitly uploaded.

A TimeScope in by is an interval coordinate, not a separate during keyword on
observe. Ordinary source reductions can omit time constraints. Internal temporal
sampling and window Metrics require a bound endpoint. [Temporal semantics](../temporal-semantics.md)
owns exact intervals, nominal buckets and contribution windows.

## Contribution occurrences and internal samples

Independent fact roots reduce independently under their own filters and time
roles, then combine on complete target keys. One root's facts cannot be multiplied
by another root's rows. Existence selection, overlapping membership allocation
and modeled association events retain different contribution meanings.

Place a membership descriptor directly in `observe.by` or `Metric.per` inputs.
It expands its ordered fields while retaining one complete correlated allocation
rule. For a rule containing `(tag, channel)`, `(rule, customer, channel)` gives
`(tag, customer, channel)`: a non-first field supplies its explicit position.
The first field cannot appear separately. Two rules R=(a,b), S=(c,d) in
`(R, S, b, d)` preserve `(a,c,b,d)`. Repeated coordinates, overlapping rules and
member fields in the wrong relative order reject; matching uses full coordinate
identity, including paths and time roles.

Use `by=P` without allocation or `by=(P, rule, ...)` with allocation. P must be
first and its complete axes, order and joint keys remain authoritative. Extra
ordinary fields, multiple P values and `(P,)` reject. Population stores no
allocation rules; `population.axes` reuses only coordinate definitions.
Outer and inner rules are independent. Separate descriptors form independent
factors. Repeated `(contribution identity, bucket)` pairs are set occurrences;
equal-valued distinct contributions remain separate. unmatched is explicit
exclude or Null. Membership repeats the full contribution in each distinct
bucket without renormalizing after selection.

External P must cover all necessary complete output keys after explicit fact
conditions, time constraints and unmatched handling, before internal sampling or
output cropping. Check complete tuples with Null-safe identity equality, rather
than each axis projection. Missing keys raise a typed coverage error with bounded
examples; enlarge P or explicitly restrict fact contributions. An existence
condition `in_population(P)` can retain a fact that also allocates to other buckets,
so it does not guarantee coverage. Extra keys in P retain the Metric's empty-input
value. Empty P is legal only when the necessary key set is empty; a necessary
zero-axis `()` key requires the singleton domain. Multi-root necessary keys union
across roots. Internal sample selection, empty samples, Top-0 and later operations
cannot hide required coverage failures. Completed historical Frames consume fixed
data without rechecking their original sources.

`Metric.per(samples)` completes the entire child Metric within each external and
internal key pair before applying the sample reducer. An average of per-customer
ratios is not an overall ratio. Implicit field samples retain observed joint
external/internal keys: completing output calendar buckets does not cross every
bucket with every sample in the observation window. Declared sample coverage
still completes and checks its required points within each contribution interval.
A complete explicit internal domain retains empty
samples under the child's empty policy; it may deliberately select a subset.
Use a membership directly, an ordered nonempty tuple, P, or `(P, rule, ...)`.
Each nested layer independently selects and completes samples. Even empty or
entirely unmatched samples preserve necessary external coordinates, producing
zero for empty sum children and NA where the reducer's empty policy requires it.

`Metric.per(grain)` uses a complete calendar grid inside the observation's finite
time coordinate. For example, `revenue.per(mv.grain("day")).mean()` under a weekly
CalendarAxis includes all seven days, with typed zero for an empty revenue sum.
Each source component uses its own declared time field. Calendar samples inherit
the coordinate's timezone; certified grains retain exact snapshot authority.
The inner grain must tile/nest the nearest enclosing calendar cell (day in week
or month; not week in month). Certified nesting follows snapshot containments.
For a plain TimeScope, the inner grid tiles the whole interval. Actual edge
bounds clip to the enclosing cell and scope; each clipped cell counts once,
without prorating. Empty ratios remain NA and follow the sample reducer's NULL
policy. Native TimeField coverage checks remain independent and run within the
enclosing calendar sample. Nested Grain layers are supported. Supply the Grain
to per and the finite time coordinate in observe.by; a CalendarAxis is not a per
argument. Missing time roles or finite coordinates produce typed repair errors.
Semantic sample Grain lookups require the Session's current certified snapshot;
declared folds include that calendar in readiness dependencies. Windows inside
calendar samples or wrapping calendar folds are unsupported and reject before
execution. Use a direct windowed observation and explicit captured Frame
reductions for those questions.

## Fixed-data bridge to a new source question

session.population_from(frame, on=...) projects complete ordinary axes, deduplicates
joint tuples, and binds them to caller-supplied resolved Field/Entity targets.
on is required, including legal on=(). The axis belongs to that exact Frame;
target Catalog, role, complete key type, unit and time constraints still apply.
The caller declares business correspondence: compatible key shapes do not prove
original semantic identity. Ordinary fields cannot be promoted into Entity identity.
Interval/calendar/paired axes and arbitrary value columns are not bridge inputs.

The bridge is lazy. It completes input checks and selection before a new source
consumer reads its keys. A completed input fixes those keys; a pending selection
can rerun when evaluated. Empty input remains an empty constraint. Multiple
selected axes remain correlated, rather than becoming a product of projections.

```python
regional = session.observe({"revenue": revenue}, by=(period, region))
chosen = regional.top(5, by=regional["revenue"]).compute()
parents = session.population_from(chosen, on=((chosen.axes[1], region),))
cities = session.observe(
    {"revenue": revenue}, by=(period, region, city),
    where=mv.in_population(parents),
).compute()
```

Here period, revenue, region and city are explicit same-Session/semantic inputs.
The new question preserves chosen region keys but reads city/revenue again.
It does not recover the historical city selection or promise a cross-read snapshot.

## Captured source contribution targets

Frame.attribute(by=...) consumes the receiver's direct Metric or admitted comparison
outputs. Each target retains its own captured source state, scope and correspondence.
Direct select/rename preserves target identity. Ordinary Column arithmetic cannot
become a source Metric; combine ratios or fixed linear formulas before observe.
The restricted align endpoint difference is an explicit admitted exception.

The current complete receiver rows fix the parent key domain and observed totals.
Attribute first completes any pending selection. Exact decomposition independently
reads parent and fine inputs; replacement games capture both endpoint group states
and derive their totals from those fixed states. Reads need not share a snapshot,
source version or raw identity set. The parent formula uses its original scope before the new classification;
fine inputs add that classification. Original predicates,
Population allocation, window dependencies, strict formulas and numeric checks stay
attached. A fixed parent selection is never reranked against the new source values.
Interval, calendar and paired coordinates preserve each endpoint's exact scope and
original complete frozen correspondence. They are not reduced away or rematched
from the selected subset.

by is a nonempty tuple of new business classifications, including explicit membership
Grouping rules, or one complete Population. The latter preserves correlated full
fine keys, allocation and valid no-fact candidates; tuple axes request actual groups.
Neither variant introduces arbitrary new time refinement. All targets must have
admitted rules and coverage; one unsupported target rejects the whole request.

The qualified contribution route uses physical tables through the existing native
DuckDB, PostgreSQL, MySQL, Trino and ClickHouse source execution paths, subject to
the backend's actual method, type, key and source restrictions. Attribute trusts
semantic identity, relationship cardinality and version-grain declarations. It does
not scan for duplicate source keys or re-prove ordinary scalar classification.
Parent aggregates use the original scope; fine aggregates use the classified scope,
without a per-identity coverage self-join. Explicit source_check remains the route
for declared data-quality expectations.
Operation-specific membership/Population coverage and overlap, distinct values
crossing groups, complete time correspondence and necessary numeric checks remain.
Data-dependent partition checks run in the same backend statement: successful reads
return aggregates; failures retain one deterministic key per check and at most six
conflicting groups. Only failed candidates are sorted; no evidence reread occurs.
Python consumes aggregate keys, components and bounded diagnostics. Final closure
neither replaces these operation checks nor proves consistency between reads.
The existing rejection of incompatible non-temporal scopes within one root remains;
such scopes are never merged merely because their root matches.
Remote Attribute original-input admission requires direct physical integer or
string identities and qualified string collations. Physical identity columns may
be nullable in source metadata: the semantic `primary_key` declaration supplies
the complete, non-null identity assumption. Attribute adds no full-source NULL or
uniqueness precheck. Violating that declaration is not guaranteed to raise an
error; use explicit `source_check` when source-data verification is required.
Existing identity-output integrity and operation-specific checks remain required.
Connector and engine restrictions, including ClickHouse Distributed join topology,
also remain.
Additive, direct ratio, native mean/weighted mean and captured fixed linear/window
rules retain their owning numeric semantics. Fixed division and Metric rescale
retain the original typed divisor and division order, including native Float64
division boundaries (operand conversion precedes division); rescale also retains
its declared target unit. Pending
ratio components disclose their own numerator and denominator units before reads.
Output carries contribution, total,
observed_total and contribution_share per target plus original fine diagnostics,
components and presence. Zero defined totals retain signed contributions and give
NA shares. Filtering the report preserves original totals and method facts.

Rules bind per target before business reads. Additive, native mean/weighted mean,
component ratios and captured fixed linear formulas retain their original total
contributions. A paired target outside the exact rules may bind a qualified
whole-Metric group-replacement Shapley game. There is no public method selector
or player-count threshold, and failed exact checks never switch to a game.

Paired rate/mix rules add `effect.within`, `effect.mix` and `effect.entry_exit` for
each target. For a group supported at both endpoints, let r be its numerator divided
by its denominator and w its share of the parent denominator. The symmetric effects
are `within=(w0+w1)/2*(r1-r0)` and `mix=(r0+r1)/2*(w1-w0)`. Their sum is the original
change in the group's parent-normalized numerator. An entering or exiting supported
group has zero within/mix and its entire contribution in entry_exit; a zero-support
side must have zero numerator mass. Undefined original group rates remain NA.
Fixed linear wrappers preserve ordered arithmetic and each ratio's own denominator;
formulas mixing additive and rate terms also expose `effect.additive`. Level and
pure additive targets do not manufacture rate/mix effects. Relative contributions
and effects divide by the same absolute fresh parent baseline, which must be defined
and nonzero. Effects sum to each group's contribution.

Rate/mix requires positive parent denominators, nonnegative group denominators and
nonnegative actual effective pair weights. It validates each captured component and
support count between independent parent and summed fine inputs, validates original
positive-support group values against N/D, and checks effect and final closure.
Negative pair weights cannot be hidden by a positive group weight sum. Checks use
only the same relevant source reads; they do not re-prove semantic identity or reread
inconsistent inputs. Violations fail the complete request without switching rules,
changing weights, rebalancing contributions or publishing a partial result.

`attribute_components/v1` checks exact integral masses. Float64 uses stable summation
with a budget of `16*(terms+1)*ulp(magnitude)`, where magnitude is the greater of the
expected magnitude and the sum of absolute terms. Typed Decimal and Duration
identities include their finishing quantum only where rounding applies. Float64
effect identities additionally propagate rounding from their actual endpoint
rates, weights and allocation operands through the ordered arithmetic and fixed
linear wrappers. Those invocation-only bounds do not relax source component
consistency checks and are never persisted as executable state. Unrepresentable
or nonfinite budgets fail through the numeric owner with the captured target and
parent/fine witness. Paired
rate/mix final closure uses `attribute_rate_mix/v1`, without the previous 1% business
allowance. Unchanged additive/level paths retain `attribute_closure/v2`: exact
integer/count and final Duration ticks, the common final Decimal scale's
`(n+1)/2` quantum window, and Float64's greater of 1% of the absolute fresh requested
final total and its numeric rounding budget. Zero totals retain only that numeric
budget. All totals, residuals and budgets must be finite. Historical observed totals
remain separate; passing closure does not certify a cross-source snapshot. Inert
cold facts preserve their original versioned explanation without rerunning checks.

### Shapley replacement games

`attribute(by=..., permutations=1024, seed=0)` validates both sampling inputs before
reads: permutations is an integer at least two and seed a nonnegative integer;
both exclude bool. Exact targets retain their existing rules without synthetic SE.
Paired nonlinear targets bind the complete original Metric, with the same formula,
constants, reducer roles, q, units and NULL policy at both endpoints. Definition
versions alone are not formula identity. Level keeps its existing share admission.

One complete joint fine key is a player. The directory is the endpoint union,
including NULL classification keys. Parent selection, player directory, endpoint
scopes and captured definitions remain fixed for the call. Current games require
single-valued classification tuples and qualified physical sources; Population
and membership game routes retain their existing qualification boundaries.

State is an optimization, rather than a prerequisite for Shapley. Qualified masses, counts, weighted components and extrema reuse typed states.
Qualified exact quantiles use typed value-frequency buckets and an incremental
rank index only when the original native finisher is proven equivalent: PostgreSQL
non-Decimal inputs, and DuckDB non-Decimal q=0/1. Other admitted quantiles retain
native coalition queries. Exact numeric distinct uses qualified membership. Actual input identity, scope,
masks and dependencies govern sharing; names or equal fingerprints alone do not.
Different q values can share a distribution while retaining separate finishers.
No-fact sum zero remains distinct from a present all-NULL sum. State games retain
the conservative finite interval/demand proof over all replacement combinations.

Float SUM and native AVG use summary reconstruction: merge captured group sums
with local Python sum() in fixed player order, and merge same-valid-mask SUM/count
for mean. This uses Python's floating summation algorithm, rather than promising
a sequential binary64 left fold. Floating
weighted SUM components retain co-valid masks. Declared result carriers, NULL,
empty-support and overflow rules remain enforced. These games explain the
state-reconstructed Metric; accumulation and AVG finishing can differ from native
whole-input aggregation. Facts mark `summary_reconstruction/v1`, with no bias
bound. Nonlinear formulas can amplify small summary differences. Cached endpoints
and `total` use the reconstruction; `observed_total` preserves historical values.

When no state route is qualified, native queries execute the original reducer over
coalition-selected current and baseline rows on the original backend. Native
quantile interpolation and approximate quantiles retain their ordinary algorithms;
no alternate quantile algorithm or second execution engine is introduced. Each paired leaf must have compatible captured
native source owners and types; leaves from different source roots retain their
own native owners. Original field recipes, masks, filters, time and parent mapping
remain attached. Unsupported ordinary algorithms or input shapes remain errors. Native aggregate
admission precedes frequency capture; state never supplies an otherwise unsupported
source algorithm. MySQL/Trino exact quantiles and ClickHouse Decimal exact quantiles
therefore reject before business reads.

Explicit `approx_nunique` uses native approximate distinct in DuckDB, Trino and
ClickHouse. It never sums per-group estimates, merges unqualified sketches, or
substitutes exact distinct. PostgreSQL/MySQL reject this operation. Native method
facts retain the original reducer and `native_approximate_metric/v1`.

Native queries are independent reads. No consistent snapshot, temporary table,
raw-input materialization, additional drift scan or source identity audit is
required. Rows may change between queries. The fixed player directory defines the
analysis domain; later reads do not refresh it or the semantic definitions. Only
actually visited native coalition values are validated; this does not certify
unvisited combinations or future source data. Any failed required query/value
rejects the whole call, without retry, skipped coalition or partial publication.

A versioned deterministic stream over typed parent/player keys and seed generates
complete permutations. The target name and physical source return order do not
select the stream. Each coalition is evaluated once and cached, with one shared
empty/full endpoint value. Original leaf values finish before the whole formula;
typed marginal subtraction precedes Float64 conversion. Every sampled path closes
to the cached full-minus-empty difference. Conversion/summation budgets enforce
closure without a 1% allowance or residual allocation. Relative contributions and
SE divide by one defined nonzero absolute cached baseline. Historical
`observed_total` and per-group diagnostics remain separate from these endpoints.

State `contribution_std_error` estimates permutation sampling error conditional on
captured summaries; it does not estimate summary reconstruction bias. With independent
native queries it is the sample statistic of the observed cached marginals; it
cannot isolate permutation error from input changes, quantify source drift, or
supply a confidence bound for it. Native approximate metrics additionally retain
their own algorithm variation; no new approximation-error guarantee is inferred.

For n>=1 players, unique coalition values are bounded by
`min(2**n, 2 + permutations*(n-1))`. State capture reads do not grow with M/U;
exact frequency transfer is O(D), worst O(N). Native query counts may grow with U,
and scanning work may approach O(U*N); parameters, results and actual submitted
statements are recorded independently of logical coalition counts. A query may
serve compatible targets, or one coalition may need several source reads. Returned
rows and decoded Arrow/state bytes are not scanned rows, wire bytes or peak memory.
Unknown backend statistics remain unknown. Physical scan claims need independent
plan/profile evidence. Persisted facts distinguish the execution policies and
costs, without storing distributions, source connections or executable formulas.

compute and same-process persist retain the instance declaration. Independent
reopen retains fixed data and explanatory method facts but no executable declaration.
For an unqualified state route or missing declarations, continue ordinary
investigation explicitly: population_from on governed ordinary parent axes,
in_population to restrict facts, then observe and compare/align. A multi-time parent
requires its complete period-specific joint keys, not a global region projection.
[Evidence and method facts](evidence-access-surface.md) owns cold explanatory reads.

## Implementation map

[population.py](../../../marivo/analysis/dsl/population.py) owns domain composition;
[frame_population.py](../../../marivo/analysis/dsl/frame_population.py) owns bridges.
[binding/observe.py](../../../marivo/analysis/binding/observe.py) and
[binding/membership.py](../../../marivo/analysis/binding/membership.py) own source
bindings; [binding/sampling.py](../../../marivo/analysis/binding/sampling.py) owns
internal sample admission. [binding/frame_attribute.py](../../../marivo/analysis/binding/frame_attribute.py)
and [attribute_rules.py](../../../marivo/analysis/binding/attribute_rules.py) own
endpoint and contribution rules. [compiler/populations.py](../../../marivo/analysis/compiler/populations.py)
and [compiler/observation.py](../../../marivo/analysis/compiler/observation.py)
compile their explicit dependencies into the shared schedule.

### Fixed parent coordinates

`AnalysisFrame.fix` retains each removed source axis as a typed complete fixed key in its
process-local declaration. Attribute combines the remaining selected parent keys
with a singleton domain over the fixed original axes. This condition retains exact
axis paths and membership allocation authority; it does not reconstruct semantics
from an output name. Fixed memberships remain parent allocations even when all of
their output axes have been removed. Different fixed keys cannot share an endpoint
source group. Missing selected endpoints are not recreated as zero-valued parents.
The fixed keys do not restore declarations after independent reopen.
