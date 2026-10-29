# AnalysisFrame Workflows

For a minimal Session → Catalog → observe → compute entry, see the
[neutral runnable entry](../../../examples/analysis/README.md). For complete axis
mappings and paired association, see the [axis examples](../../../examples/analysis/AXES.md).
The entry script lists Metric or field candidates through explicit discovery
modes, then requires a caller-selected exact Metric path for observation. It
accepts optional grouping fields and a paired start/end interval; persistence is
opt-in. Its synthetic setup is only needed to reproduce the example fixture.
New Sessions are the default; an exact Session ID explicitly continues discovery
and observation in an existing investigation.

These examples use the six-order warehouse and sales model from the
[local Python tutorial](../../../site/src/content/docs/docs/latest/start/python.mdx).
Create its source and model files in a fresh project, then run the blocks below
in order from that project root. The demo's six orders are its complete declared
scope; no general source-completeness claim follows from this fixture.

## Bind explicit source questions

```python
import marivo.analysis as mv
import marivo.semantic as ms

session = mv.session.get_or_create("architecture-examples", timezone="UTC")
catalog = session.catalog
orders_entry = catalog.get("entity:sales.orders")
assert orders_entry.kind == "entity"
orders = orders_entry
region_entry = catalog.get("dimension:sales.orders.region")
assert region_entry.kind == "dimension"
region = region_entry
amount_entry = catalog.get("measure:sales.orders.amount")
assert amount_entry.kind == "measure"
amount = amount_entry
revenue_entry = catalog.get("metric:sales.revenue")
assert revenue_entry.kind == "metric"
revenue = revenue_entry
july = session.time_scope(start="2026-07-01", end="2026-08-01")
august = session.time_scope(start="2026-08-01", end="2026-09-01")

current = session.observe(revenue, by=(august, region)).compute()
baseline = session.observe(revenue, by=(july, region)).compute()
change = current.compare(
    baseline,
    on=tuple(zip(current.axes, baseline.axes)),
    match=mv.ordinal(),
).persist()
change.show(n=10)
change.show(n=0)
assert change.to_pandas()["reference.absolute.revenue"].tolist() == [-100, -100]
```

The TimeScope is an actual interval axis in by. Full on binds interval and region;
ordinal explicitly pairs the two single-interval directories. Compare keeps
current values plus named baseline/absolute/relative values. Correspondence is
outer, preserving missing-side rows and NA; a zero baseline gives relative NA. Independent presence
states row existence rather than whether revenue is NA. Compute fixes each source
read separately, without a shared transaction snapshot promise.

## Reuse fixed keys in a new source read

```python
chosen = current.top(1, by=current["revenue"]).compute()
parents = session.population_from(chosen, on=((chosen.axes[1], region),))
parents.show()
prior_for_chosen = session.observe(
    {"revenue": revenue}, by=mv.product(july, parents),
    where=mv.in_population(parents),
).compute()
prior_for_chosen.show()
assert prior_for_chosen.to_pandas()["revenue"].tolist() == [700]
```

The selected west region stays fixed even if a later source read changes values.
The bridge projects complete ordinary joint keys and declares the target field;
it does not recover nominal identity from a label. by supplies complete output
keys and must cover every necessary complete output key, while where explicitly
restricts contributions. Internal per(P) may select a subset; empty samples
still retain external output coordinates. New source values can differ
from those used to select parents. A pending selection would rerun before its
keys were consumed; retain the returned completed Frame when prior keys matter.

## Keep identity reads separate from general key frames

```python
members = session.members(orders)
keys = session.frame(members).compute()
fixed_members = session.population_from(keys, on=((keys.axes[0], orders),))
attributes = session.read(fixed_members, {"region": region, "amount": amount}).compute()
attributes.show(n=10)
member_count = session.observe({"orders": fixed_members.count()}).compute()
assert member_count.to_pandas()["orders"].tolist() == [6]
```

frame accepts general Population keys and can have no values. read requires a
single Entity identity domain and nonempty fields. P.count counts its complete
distinct tuples; projecting identity away changes what is counted. A literal
identity can be in the domain without proving a source record exists.

## Compute statistics of represented rows

```python
row_summary = current.reduce({
    "mean_regional_revenue": current["revenue"].mean(),
    "sum_regional_revenue": current["revenue"].sum(),
    "regions": mv.row_count(),
}).compute()
row_summary.show()
summary = row_summary.to_pandas()
assert summary["mean_regional_revenue"].tolist() == [400]
assert summary["sum_regional_revenue"].tolist() == [800]
assert summary["regions"].tolist() == [2]
```

These are statistics of current cells. They do not recover original Metric
components or certify additivity. Customer AOVs 100/1 and 900/90 have row mean
55 while the original overall is 1000/91. Group distinct counts 2+2 can represent
an actual union of 3. For an original total, construct a new explicit observe.
Units retained by sum/mean describe dimension rather than semantic roll-up permission.

## Align an explicit complete-domain reference

```python
budget = session.values(
    by=(region,), columns={"budget": "CNY"},
    rows=((("east",), {"budget": 250}), (("west",), {"budget": 650})),
).compute()
regional = current.reduce({"revenue": current["revenue"].sum()}, by=(current.axes[1],)).compute()
joined = regional.align(budget, on=((regional.axes[0], budget.axes[0]),))
variance = joined.select({"variance": joined["left.revenue"] - joined["right.budget"]}).compute()
variance.show()
assert variance.to_pandas()["variance"].tolist() == [-50, -50]

paired_statistics = joined.correlate(
    joined["left.revenue"], joined["right.budget"], method="pearson",
).compute()
paired_statistics.show()
assert paired_statistics.to_pandas()["n_pairs"].tolist() == [2]
```

Reducing the existing interval axis yields region-only current data. This example
binds the ordinary axis explicitly; align does not broadcast a scalar budget.
Correlation uses group-local common non-NA pairs and retains zero/duplicate values.
Presence does not implicitly filter those pairs. Constant endpoints or fewer
than two pairs produce NA coefficient while preserving the count. Saved group
coefficients/counts cannot reconstruct pooled correlation.

## Explain retained source targets and preserve original facts

```python
source_current = session.observe({"revenue": revenue}, by=(august,))
source_baseline = session.observe({"revenue": revenue}, by=(july,))
comparison = source_current.compare(
    {"reference": mv.baseline(source_baseline, match=mv.ordinal())}
).compute()
target = comparison.select({"revenue": comparison["reference.absolute.revenue"]})
explained = target.attribute(by=(region,)).persist()
explained.show()
facts = explained.method_facts
assert len(facts) == 1
assert explained.to_pandas()["contribution.revenue"].tolist() == [-100, -100]
negative = explained.where(explained["contribution.revenue"] < 0).compute()
assert negative.method_facts == facts
```

The local physical DuckDB table and additive revenue satisfy this contribution
route. Fresh total and observed_total are independent parent references. Fine
original values/components/presence remain in data; method_facts explains the
original full decomposition, including after filtering and independent cold read.
Reopened data has no executable declaration. Unsupported contribution targets
can continue via explicit new observations and governed fixed-parent key bridges.

## Reopen and continue fixed data

The earlier `change` was explicitly persisted. Retain its identifiers from the
producer's output before that process exits; no additional state file is needed.

```python
print(change.session_id)
print(change.artifact_id)
```

In another process in the same project, pass those exact identifiers as
`saved_session_id` and `saved_artifact_id`:

```python
import marivo.analysis as mv

resumed = mv.session.resume(saved_session_id)
restored = resumed.persisted_frame(saved_artifact_id)
restored.show(n=0)
positive = restored.where(restored["revenue"] > 0).compute()
positive.show()
assert positive.to_pandas()["revenue"].tolist() == [200, 600]
```

This continuation needs no new commit to inspect its answer. If a later process
needs `positive`, explicitly call `positive.persist()` and retain the returned
Frame's identifiers. Correcting a fixed-data operation can reuse the saved inputs;
obtaining new source facts requires an explicit new observation.

Opening metadata, then reading/deriving saved values, requires neither original
models nor sources. Complete data consumers validate actual required payloads;
metadata/preview alone does not prove unread data integrity. Missing/corrupt
files fail without source fallback. Source-read, evaluation and commit time remain
separate; a new commit does not refresh the old source observation.

## New windows need explicit source construction

```python
from datetime import timedelta

days = august.by(mv.grain("day"))
rolling = session.observe(
    {"revenue": revenue.window(mv.trailing_duration(duration=timedelta(days=7)))},
    by=(days,),
).compute()
rolling.show(n=5)
```

The source equation completes at each exact endpoint. Summing these overlapping
cells is a current-value statistic rather than the union-window revenue.
New month grain uses TimeScope.by(mv.grain("month")) in a new source request,
without coarsening completed daily aggregates. [Temporal semantics](../temporal-semantics.md)
and [operators](operators-and-frames.md) own the precise window/numeric boundaries.
