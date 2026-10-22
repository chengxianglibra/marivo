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

Use resolved Entities/fields/Metrics from finalized declarations or the Catalog.
Bare Refs are lookup keys. Artifact objects, artifact IDs, pandas tables and
RawSqlResult are not source Metric operands. Session is the context for Catalog,
execution owner, Store and frozen default timezone. Source-free descriptions and
compositions at mv inherit their input owner when needed; cross-Session inputs fail.

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

observe accepts a nonempty ordered mapping of names to completed Metric recipes.
by=() requests a zero-axis cell. An ordered tuple may contain fields, ordinary
Grouping rules, TimeScope intervals and CalendarAxis buckets. A Population in by
selects exactly its complete output keys, including those without facts.
Population.axes carries coordinate rules, without the selected keys or allocation.

A member without fact contributions has sum zero and count zero; a member with
facts whose values are all NULL has sum NULL and a positive contribution count.
Use a contribution count, not count(value) or the completed Frame row count,
to distinguish them. Mean/min/max remain NULL without valid samples. Empty
Population output has no rows. Lookup and source failures are errors, not empty
contributions. In per(P).mean(), zero-valued no-fact members participate; unknown
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
ratios is not an overall ratio. A complete explicit internal domain retains empty
samples under the child's empty policy; it may deliberately select a subset.
Use a membership directly, an ordered nonempty tuple, P, or `(P, rule, ...)`.
Each nested layer independently selects and completes samples. Even empty or
entirely unmatched samples preserve necessary external coordinates, producing
zero for empty sum children and NA where the reducer's empty policy requires it.

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
Attribute first completes any pending selection, then independently rereads parent
and fine formulas from shared actual inputs per endpoint/root. Original predicates,
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

The qualified contribution route remains native DuckDB physical tables with retained
components, original parent/fine formula checks and proven disjoint coverage.
Additive, direct ratio, native mean/weighted mean and captured fixed linear/window
rules retain their owning numeric semantics. Output carries contribution, total,
observed_total and contribution_share per target plus original fine diagnostics,
components and presence. Zero defined totals retain signed contributions and give
NA shares. Filtering the report preserves original totals and method facts.

compute and same-process persist retain the instance declaration. Independent
reopen retains fixed data and explanatory method facts but no executable declaration.
For quantile, overlapping distinct or missing declarations, continue ordinary
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
