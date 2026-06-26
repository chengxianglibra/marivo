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
read ms.help(...) static authoring contract
  -> run md.discover_* runtime datasource evidence
  -> combine evidence, registry facts, project docs, prior decisions, and user answers
  -> prepare_<kind>(...) -> Brief
  |-- status == "blocked" -> fix the blocker or abandon the evidence subject
  +-- object can proceed
        -> draft one proposed object
        -> ask one unresolved semantic decision at a time
        -> author exactly one object only after agreement
        -> verify_object(ref)      (fix loop until passed)
        -> next object
```

Discovery is datasource-first. Use Brief facts, `md.discover_entity` /
`md.discover_dimensions` / `md.discover_time_dimensions` / `md.discover_measures`
/ `md.discover_relationship` / `md.discover_dimension_values`, bounded
`md.preview`, existing semantic catalog objects, source SQL/provenance, project
docs, and prior ledger decisions before asking the user.

The grill step is for semantic intent and business policy, not for facts Marivo
can inspect. Each question must state the evidence already checked, offer the
recommended answer first, and use only evidence-derived options. If the next
question is answerable by another bounded datasource query, run that query
instead of asking. Do not invent plausible options.

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

ms.domain(name="sales", ai_context=ms.ai_context(business_definition="Sales analytics"))
warehouse = md.ref("warehouse")
```

## Rung 2: Entity

The physical-to-semantic bridge. Run `md.discover_entity(...)` first to
collect table metadata, column profiles, and primary-key candidates. Then call
`ms.prepare_entity(...)`, which returns an `EntityBrief` with the same evidence
plus semantic interpretation.

```python
discovery = md.discover_entity(
    md.ref("warehouse"),
    md.table("orders", database="sales_mart"),
    scope=md.latest_partition(),
)
entity_brief = ms.prepare_entity(
    datasource="warehouse",
    source=md.table("orders", database="sales_mart"),
    domain="sales",
    scope=md.latest_partition(),
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
    scope=md.latest_partition(),
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
ms.help("time_dimension_column")
discovery = md.discover_time_dimensions(
    md.ref("warehouse"),
    md.table("orders", database="sales_mart"),
    columns=("dt",),
    scope=md.latest_partition(),
)
```

Use `discovery.columns` to inspect physical column evidence such as detected
formats, value ranges, partition alignment, signals, and issues. Use
`ms.help("time_dimension_column")` to know which constructor parameters must be
settled.

```python
td_brief = ms.prepare_time_dimension(
    entity="sales.orders",
    column="dt",
    scope=md.latest_partition(),
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
    parse=ms.strptime("%Y%m%d"),
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
    scope=md.latest_partition(),
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
    scope=md.latest_partition(),
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

`prepare_relationship` resolves join-key evidence internally. Run
`md.discover_relationship(...)` first to inspect sampled key overlap, match
rate, fanout, and key type evidence between the two sides.

```python
warehouse = md.ref("warehouse")
discovery = md.discover_relationship(
    from_side=md.JoinSide(warehouse, md.table("orders"), columns=("customer_id",)),
    to_side=md.JoinSide(warehouse, md.table("customers"), columns=("customer_id",)),
    scope=md.latest_partition(),
)
rel_brief = ms.prepare_relationship(
    from_entity="sales.orders",
    to_entity="sales.customers",
    keys=[ms.join_on("sales.orders.customer_id", "sales.customers.customer_id")],
    scope=md.latest_partition(),
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
    scope=md.latest_partition(),
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

Before the ladder starts, discover the physical source using the `md.discover_*`
family. Each discovery call returns bounded evidence with signals and issues;
use it to choose candidate columns before the matching `ms.prepare_*` call.

Each rung starts from the matching help contract, then discovery, then prepare:

- Entity: `ms.help("entity")`, then `md.discover_entity(...)`, then `ms.prepare_entity(...)`.
- Dimension: `ms.help("dimension_column")` or `ms.help("dimension")`, then `md.discover_dimensions(...)`, then `ms.prepare_dimension(...)`.
- Time dimension: `ms.help("time_dimension_column")` or `ms.help("time_dimension")`, then `md.discover_time_dimensions(...)`, then `ms.prepare_time_dimension(...)`.
- Measure: `ms.help("measure_column")` or `ms.help("measure")`, then `md.discover_measures(...)`, then `ms.prepare_measure(...)`.
- Metric: start with `ms.help("metric")` to choose `count`, `aggregate`, expression `@ms.metric`, `ratio`, `weighted_average`, or `linear`; then read the selected constructor help and call `ms.prepare_metric(...)` or `ms.prepare_derived_metric(...)`.
- Relationship: `ms.help("relationship")`, then `md.discover_relationship(...)`, then `ms.prepare_relationship(...)`.
- Derived metric: do not start from a derived helper in isolation; start from `ms.help("metric")`, then read `ms.help("ratio")`, `ms.help("weighted_average")`, or `ms.help("linear")` after the family router selects that path.

```python
warehouse = md.ref("warehouse")
orders = md.table("orders", database="sales_mart")

entity = md.discover_entity(warehouse, orders, scope=md.latest_partition())
dimensions = md.discover_dimensions(warehouse, orders, columns=("status",))
time_dims = md.discover_time_dimensions(warehouse, orders, columns=("created_at",))
measures = md.discover_measures(warehouse, orders, columns=("amount",))
```

Column deep-dives for time/enum/amount columns use the matching discovery API
with an explicit partition scope when you need recent values:

```python
dimensions = md.discover_dimensions(
    warehouse,
    md.table("orders"),
    columns=("status", "amount"),
    scope=md.partition({"dt": "20260611"}),
)
```

For current value counts on one dimension column, use
`md.discover_dimension_values(...)`. For relationship evidence, use
`md.discover_relationship(...)` with two `md.JoinSide(...)` values. Use
`md.raw_sql(...)` only as a diagnostic escape hatch with a required `reason`.

Sample-derived values are facts about the bounded sample only. Never treat them
as full-column cardinality, complete enums, or global ranges. Use the scope
helpers (`md.latest_partition()`, `md.partition({...})`, `md.unpruned(...)`)
for ordinary authoring; `md.ScanScope` remains a value type for advanced
debugging only.
