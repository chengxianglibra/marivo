# marivo-semantic workflow

This is the stepwise authoring ladder for agents building reusable Marivo
semantic objects. Each rung produces exactly one object per cycle, verified
before advancing.

## The Nine-Rung Ladder

Within one domain, build objects in this order:

```text
1 domain
2 entity                  (one per physical table, one at a time)
3 dimension               (per entity, one column at a time)
4 time_dimension          (per entity)
5 measure                 (row-level quantitative facts, one column at a time)
6 metric                  (tier-1 aggregate over a verified measure by default)
7 relationship
8 cross-entity base metric
9 derived metric
```

Datasource registration is a prerequisite owned by `marivo.datasource`, not a
ladder rung.

## The Per-Object Cycle

Every rung iterates the same cycle, one semantic object per iteration:

```text
prepare_<kind>(...) -> Brief
  |-- status == "blocked"      -> fix the blocker or abandon the candidate
  |-- blocking questions open  -> answer from documented knowledge, or ask
  |     the user; unanswerable -> abandon: record authoring_abandoned, skip
  +-- status == "sufficient" and no open blocking question
        -> append exactly ONE object to _domain.py
        -> verify_object(ref)      (fix loop until passed)
        -> next object
```

## Rung 1: Domain

```python
brief = ms.prepare_domain(name="sales")
if brief.status == "blocked":
    brief.show()
    raise SystemExit("Fix blockers before authoring the domain.")

# write models/semantic/sales/_domain.py
```

```python
import marivo.datasource as md
import marivo.semantic as ms

ms.domain(name="sales", description="Sales analytics")
warehouse = md.ref("warehouse")
```

## Rung 2: Entity

The physical-to-semantic bridge. `prepare_entity` calls `md.inspect_table` and
`md.inspect_columns` internally, returning an `EntityBrief` with table metadata,
column profiles, and primary-key candidates.

```python
entity_brief = ms.prepare_entity(
    datasource="warehouse",
    source=md.table("orders", database="sales_mart"),
    domain="sales",
    scope=md.ScanScope(),
)
if entity_brief.status == "blocked":
    entity_brief.show()
    raise SystemExit("Fix blockers before authoring the entity.")
```

Write the entity, load, and verify:

```python
# append to models/semantic/sales/_domain.py
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders", database="sales_mart"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(
        business_definition="One row per order.",
        guardrails=["Exclude test orders when the table exposes a test flag."],
    ),
)
```

```python
verify = ms.verify_object("sales.orders")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored object before continuing.")
```

## Rung 3: Dimensions

Prepare one dimension at a time, then author and verify.

```python
dim_brief = ms.prepare_dimension(
    entity="sales.orders",
    column="region",
    scope=md.ScanScope(),
)
```

Author each dimension individually, then verify:

```python
region = ms.dimension_column(
    name="region",
    entity=orders,
    column="region",
    ai_context=ms.ai_context(
        business_definition="Sales reporting region.",
    ),
)
```

```python
verify = ms.verify_object("sales.orders.region")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored dimension before continuing.")
```

## Rung 4: Time Dimension

Single-column temporal probe with format inference and partition alignment.

```python
td_brief = ms.prepare_time_dimension(
    entity="sales.orders",
    column="dt",
    scope=md.ScanScope(),
)
```

For day/hour partition columns, preserve the raw value and declare
its physical encoding with a `parse` variant:

```python
log_date = ms.time_dimension_column(
    name="log_date",
    entity=orders,
    column="dt",
    granularity="day",
    parse=ms.strftime("%Y%m%d"),
    is_default=True,
    ai_context=ms.ai_context(
        business_definition="Partition date used for default order reporting windows.",
    ),
)
```

`verify_object` automatically reloads the project from disk to pick up the
newly authored object and auto-record a `time_dimension_identity` decision,
then verifies:

```python
verify = ms.verify_object("sales.orders.log_date")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored time dimension before continuing.")
```

## Rung 5: Measures

```python
measure_brief = ms.prepare_measure(
    entity="sales.orders",
    column="amount",
    scope=md.ScanScope(),
)
if measure_brief.status == "blocked":
    measure_brief.show()
    raise SystemExit("Fix blockers before authoring the measure.")
```

Author and verify:

```python
amount = ms.measure_column(
    name="amount",
    entity=orders,
    column="amount",
    additivity="additive",
    unit="USD",
    ai_context=ms.ai_context(
        business_definition="Gross order amount before refunds.",
    ),
)
```

```python
verify = ms.verify_object("sales.orders.amount")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored measure before continuing.")
```

## Rung 6: Metrics

Default to a tier-1 aggregate over a verified measure. Use `@ms.metric(...)`
only when the metric needs an expression body that cannot be naturally expressed
as `ms.aggregate(...)`.

```python
metric_brief = ms.prepare_metric(
    entity="sales.orders",
    measure_columns=("amount",),
    scope=md.ScanScope(),
)
```

Author and verify the aggregate metric:

```python
revenue = ms.aggregate(
    name="revenue",
    measure=amount,
    agg="sum",
    ai_context=ms.ai_context(
        business_definition="Gross order amount before refunds.",
        guardrails=["Validate refund exclusions before using as net revenue."],
    ),
)
```

```python
verify = ms.verify_object("sales.revenue")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored aggregate metric before continuing.")
```

## Rung 7: Relationships

`prepare_relationship` runs `md.probe_join_keys` internally using sources
resolved from the two entity refs.

```python
rel_brief = ms.prepare_relationship(
    from_entity="sales.orders",
    to_entity="sales.customers",
    keys=[ms.join_on("sales.orders.customer_id", "sales.customers.customer_id")],
    scope=md.ScanScope(),
)
```

Author and verify:

```python
orders_to_customers = ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    keys=[ms.join_on(order_customer_id, customer_id)],
)
```

```python
verify = ms.verify_object("sales.orders_to_customers")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored relationship before continuing.")
```

## Rung 8: Cross-Entity Base Metrics

```python
cross_brief = ms.prepare_cross_entity_metric(
    root_entity="sales.orders",
    entities=("sales.orders", "sales.customers"),
    measure_columns=("amount",),
    scope=md.ScanScope(),
)
```

Cross-entity base metrics still use a tier-2 body because they bind multiple
entities and must declare the root grain explicitly:

```python
@ms.metric(
    entities=[orders, customers],
    root_entity=orders,
    additivity="additive",
    fanout_policy="aggregate_then_join",
    name="revenue_by_customer",
    ai_context=ms.ai_context(
        business_definition="Gross order amount analyzed by customer attributes.",
    ),
)
def revenue_by_customer(orders, customers):
    return orders.amount.sum()
```

```python
verify = ms.verify_object("sales.revenue_by_customer")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored cross-entity metric before continuing.")
```

## Rung 9: Derived Metrics

Registry-only; no datasource access needed.

```python
derived_brief = ms.prepare_derived_metric(
    numerator="sales.revenue",
    denominator="sales.orders_count",
)
```

`prepare_derived_metric` previews ratio and weighted-average component facts.
Author body-free derived metrics with the constructor that matches the intended
composition:

```python
aov = ms.ratio(
    name="aov",
    numerator=revenue,
    denominator=orders_count,
    ai_context=ms.ai_context(
        business_definition="Gross revenue divided by order count.",
    ),
)

net_revenue = ms.linear(
    name="net_revenue",
    add=[gross_revenue],
    subtract=[refunds],
)
```

```python
verify = ms.verify_object("sales.aov")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored derived metric before continuing.")
```

## Closeout

After all objects pass `verify_object`, run `ms.readiness(...)` once:

```python
report = ms.readiness(refs=("sales.orders", "sales.orders.amount", "sales.revenue"))
report.show()
if report.status == "blocked":
    raise SystemExit("Semantic project is not ready for analysis handoff.")
```

Do not hand off to `marivo-analysis` while readiness is blocked. Richness gaps
are reported as readiness warnings and summarized in `richness_summary`.
Abandoned candidates appear in `report.abandoned`.

## Abandon Protocol

When a candidate cannot reach sufficiency:

1. Record `authoring_abandoned` in the decision ledger.
2. Skip the object and continue the ladder. Dependents are naturally stopped
   by hard gates with structured errors naming the missing prerequisite.
3. `ReadinessReport.abandoned` lists abandoned candidates for transparency.

## Source Discovery

Before the ladder starts, inspect the physical source using `md` APIs:

```python
metadata = md.inspect_table("warehouse", md.table("orders", database="sales_mart"))
columns = md.inspect_columns("warehouse", md.table("orders"), columns=("status", "amount"))
```

Column deep-dives for time/enum/amount/join-key columns:

```python
evidence = md.inspect_columns(
    "warehouse",
    md.table("orders"),
    columns=("status", "amount"),
    scope=md.ScanScope(partition={"dt": "20260611"}),
)
for col in evidence.profiles:
    print(col.column, col.distinct_count, col.top_values)
```

Sample-derived values are facts about the bounded sample only. Never treat them
as full-column cardinality, complete enums, or global ranges.
