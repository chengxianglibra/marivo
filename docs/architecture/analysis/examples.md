# AnalysisFrame Workflows

These examples use the six-order warehouse and sales model from the
[local Python tutorial](../../../site/src/content/docs/docs/latest/guides/python-quick-start.mdx).
Create its source and model files in a fresh project, then run the blocks below
in order from that project root. The demo's six orders are its complete declared
scope; no general source-completeness claim follows from this fixture.

## Bind explicit source questions

```python
import marivo.analysis as mv
import marivo.semantic as ms

session = mv.session.create("architecture-examples", timezone="UTC")
catalog = session.catalog
orders = catalog.entities.get("sales.orders")
region = catalog.fields.get("sales.orders.region")
amount = catalog.fields.get("sales.orders.amount")
revenue = catalog.metrics.get("sales.revenue")
july = session.time_scope(start="2026-07-01", end="2026-08-01")
august = session.time_scope(start="2026-08-01", end="2026-09-01")

current = session.observe({"revenue": revenue}, by=(august, region)).compute()
baseline = session.observe({"revenue": revenue}, by=(july, region)).compute()
change = current.compare(
    {"reference": baseline},
    on={"reference": tuple(zip(current.axes, baseline.axes))},
    match={"reference": mv.ordinal()},
).persist()
change.show(n=10)
change.artifact.show()
assert change.to_pandas()[("value", "reference.absolute.revenue")].tolist() == [-100, -100]
```

The TimeScope is an actual interval axis in by. Full on binds interval and region;
ordinal explicitly pairs the two single-interval directories. Compare keeps
current values plus named baseline/absolute/relative values. Independent presence
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
assert prior_for_chosen.to_pandas()[("value", "revenue")].tolist() == [700]
```

The selected west region stays fixed even if a later source read changes values.
The bridge projects complete ordinary joint keys and declares the target field;
it does not recover nominal identity from a label. by supplies complete output
keys while where explicitly restricts contributions. New source values can differ
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
assert member_count.to_pandas()[("value", "orders")].tolist() == [6]
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
assert summary[("value", "mean_regional_revenue")].tolist() == [400]
assert summary[("value", "sum_regional_revenue")].tolist() == [800]
assert summary[("value", "regions")].tolist() == [2]
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
assert variance.to_pandas()[("value", "variance")].tolist() == [-50, -50]

paired_statistics = joined.correlate(
    joined["left.revenue"], joined["right.budget"], method="pearson",
).compute()
paired_statistics.show()
assert paired_statistics.to_pandas()[("value", "n_pairs")].tolist() == [2]
```

Reducing the existing interval axis yields region-only current data. Align binds
every ordinary axis explicitly; it does not broadcast a scalar budget.
Correlation uses group-local common non-NA pairs and retains zero/duplicate values.
Presence does not implicitly filter those pairs. Constant endpoints or fewer
than two pairs produce NA coefficient while preserving the count. Saved group
coefficients/counts cannot reconstruct pooled correlation.

## Investigate explicit endpoints and preserve original explanations

```python
left = mv.attribute_endpoint({"revenue": revenue}, during=august)
right = mv.attribute_endpoint({"revenue": revenue}, during=july)
investigation = session.attribute(left, right, by=(region,)).compute()
assert investigation.method_facts == ()

explained = session.attribute(left, right, by=(region,), contribution=True).persist()
explained.show()
facts = explained.method_facts
assert len(facts) == 1
assert explained.to_pandas()[("value", "contribution.revenue")].tolist() == [-100, -100]
negative = explained.where(explained["contribution.revenue"] < 0).compute()
assert negative.method_facts == facts
```

The local physical DuckDB table and additive revenue satisfy this contribution
example's qualified route. Default investigation needs no contribution proof and
creates no new facts. Contribution failure is not an automatic retry as default.
Filtering preserves the original explanation/denominator, without certifying a
new decomposition. The complete getter is independent of current-row export.

## Reopen and continue fixed data

```python
saved_session_id = session.id
saved_artifact_id = change.artifact.id
resumed = mv.session.resume(saved_session_id)
restored = resumed.persisted_frame(saved_artifact_id)
restored.show(n=0)
positive = restored.where(restored["revenue"] > 0).compute()
positive.show()
assert positive.to_pandas()[("value", "revenue")].tolist() == [200, 600]
positive.persist().artifact.show()
```

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
