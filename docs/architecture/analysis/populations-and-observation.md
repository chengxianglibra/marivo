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
| session.observe(metrics, by=..., where=..., memberships=...) | Named Metric evaluation on an explicit output domain | AnalysisFrame |
| session.read(population, fields) | Nonempty attributes on one Entity identity domain | AnalysisFrame |
| session.frame(population) | Complete keys of a general Population | AnalysisFrame, possibly with no values |
| session.values(by=..., columns=..., rows=...) | Literal keys and named values under the Session owner | AnalysisFrame |
| session.attribute(left, right, by=..., ...) | Fresh classifications of two explicit source endpoints | AnalysisFrame |
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

A membership descriptor in the explicit `memberships` parameter of observe or
Metric.per preserves correlated fields within one tuple. The coordinates are
declared separately in by/samples; Population contains keys, not allocation rules.
Outer and inner rules are independent. Separate
descriptors form independent factors. Repeated `(contribution identity, bucket)`
pairs are set occurrences; equal-valued distinct contributions remain separate.
unmatched is explicit exclude or Null. A Population in by selects exactly its
complete output keys, including keys without facts. Selection preserves original
contribution identities. Membership repeats the full contribution in each distinct
bucket; explicit weighted allocation needs its own declared contribution source,
without renormalizing after selection.

`Metric.per(samples, memberships=())` completes the entire child Metric within each external and
internal key pair before applying the sample reducer. An average of per-customer
ratios is not an overall ratio. A complete explicit internal domain retains empty
samples under the child's empty policy; it may deliberately select a subset.

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

## Explicit endpoint investigation

mv.attribute_endpoint(metrics, during=None, where=None, memberships=()) copies
an ordered Metric mapping and endpoint scope without Session/schema/source I/O.
Session.attribute takes two endpoints with the same nonempty metric-name set,
new classifications in by, and optional parent selection/correspondence.

The default computes original left/right/absolute values with independent
left_present/right_present, without decomposition proof or new method facts.
It preserves each original formula, actual types, units, NA and strict errors.
Fine groups use their outer union; parent_how separately controls exact/outer
fresh parent correspondence. None means one overall parent; an empty Population
or Frame selection stays empty.

Frame parents bind each complete axis via parent_on to explicit left/right
semantic targets. Ordinary, calendar and paired-calendar parents have dedicated
contracts; interval and paired-interval parents reject before reads. Reduce away
an interval or construct an explicit CalendarAxis parent and endpoint scopes.

contribution=True requests additional contribution/share and original method
facts. It requires matching additive/ratio/linear rules, complete disjoint
coverage, retained components and one actual shared input per endpoint/root.
The qualified route is native DuckDB physical tables. Files, remote providers,
nonadditive/nested windows and unproved sample reductions are not thereby
qualified. Fixed additive windows require exact fibers. Failure rejects the
whole contribution request; a valid default investigation is a separate request.

Contribution output includes contribution.q/contribution_share.q; direct ratios
also expose group numerator/denominator endpoint values. Zero parent change
makes share NA. Later filtering does not change the original denominator or
recertify derived arithmetic. [Evidence and method facts](evidence-access-surface.md)
owns the complete immutable explanation and interpretation limits.

## Implementation map

[population.py](../../../marivo/analysis/dsl/population.py) owns domain composition;
[frame_population.py](../../../marivo/analysis/dsl/frame_population.py) owns bridges.
[binding/observe.py](../../../marivo/analysis/binding/observe.py) and
[binding/membership.py](../../../marivo/analysis/binding/membership.py) own source
bindings; [binding/sampling.py](../../../marivo/analysis/binding/sampling.py) owns
internal sample admission. [binding/attribute.py](../../../marivo/analysis/binding/attribute.py)
and [attribute_rules.py](../../../marivo/analysis/binding/attribute_rules.py) own
endpoint and contribution rules. [compiler/populations.py](../../../marivo/analysis/compiler/populations.py)
and [compiler/observation.py](../../../marivo/analysis/compiler/observation.py)
compile their explicit dependencies into the shared schedule.
