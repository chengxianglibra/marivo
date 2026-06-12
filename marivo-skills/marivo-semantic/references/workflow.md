# marivo-semantic workflow

This is the stepwise authoring ladder for agents building reusable Marivo
semantic objects. Each rung produces exactly one object per cycle, verified
before advancing.

## The Eight-Rung Ladder

Within one domain, build objects in this order:

```text
1 domain
2 entity                  (one per physical table, one at a time)
3 dimension               (per entity, one column at a time)
4 time_dimension          (per entity)
5 metric                  (single-entity base metrics)
6 relationship
7 cross-entity base metric
8 derived metric
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
brief = project.prepare_domain(name="sales")
if brief.status == "blocked":
    brief.show()
    raise SystemExit("Fix blockers before authoring the domain.")

# write .marivo/semantic/sales/_domain.py
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
entity_brief = project.prepare_entity(
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
# append to .marivo/semantic/sales/_domain.py
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders", database="sales_mart"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Exclude test orders when the table exposes a test flag."],
    },
)
```

```python
project.load()
verify = project.verify_object("sales.orders")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored object before continuing.")
```

## Rung 3: Dimensions

Batch preparation for scan economy; author one dimension at a time.

```python
dim_briefs = project.prepare_dimensions(
    entity="sales.orders",
    columns=("region", "status"),
    scope=md.ScanScope(),
)
```

Author each dimension individually, then verify:

```python
@ms.dimension(entity=orders, name="region")
def region(table):
    return table.region
```

```python
project.load()
verify = project.verify_object("sales.orders.region")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored dimension before continuing.")
```

## Rung 4: Time Dimension

Single-column temporal probe with format inference and partition alignment.

```python
td_brief = project.prepare_time_dimension(
    entity="sales.orders",
    column="dt",
    scope=md.ScanScope(),
)
```

For day/hour partition columns, preserve the raw value and declare
`date_format`:

```python
@ms.time_dimension(
    entity=orders,
    name="log_date",
    data_type="string",
    granularity="day",
    date_format="%Y%m%d",
    is_default=True,
)
def log_date(table):
    return table.dt
```

Reload so Marivo can auto-record `time_dimension_identity` decisions, then
verify:

```python
project.load()
verify = project.verify_object("sales.orders.log_date")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored time dimension before continuing.")
```

## Rung 5: Metrics

```python
metric_brief = project.prepare_metric(
    entity="sales.orders",
    measure_columns=("amount",),
    scope=md.ScanScope(),
)
```

Author and verify:

```python
@ms.metric(
    entities=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="revenue",
    verification_mode="sql_parity",
    source_sql="SELECT SUM(amount) AS revenue FROM orders",
    source_dialect="duckdb",
    ai_context={
        "business_definition": "Gross order amount before refunds.",
        "guardrails": ["Validate refund exclusions before using as net revenue."],
    },
)
def revenue(table):
    return table.amount.sum()
```

```python
project.load()
verify = project.verify_object("sales.revenue")
if verify.status == "failed":
    verify.show()
    raise SystemExit("Fix the authored metric before continuing.")
```

## Rung 6: Relationships

`prepare_relationship` runs `md.probe_join_keys` internally using sources
resolved from the two entity refs.

```python
rel_brief = project.prepare_relationship(
    from_entity="sales.orders",
    to_entity="sales.customers",
    from_dimensions=("sales.orders.customer_id",),
    to_dimensions=("sales.customers.customer_id",),
    scope=md.ScanScope(),
)
```

Author and verify:

```python
ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    from_dimensions=[order_customer_id],
    to_dimensions=[customer_id],
)
```

## Rung 7: Cross-Entity Base Metrics

```python
cross_brief = project.prepare_cross_entity_metric(
    root_entity="sales.orders",
    entities=("sales.orders", "sales.customers"),
    measure_columns=("amount",),
    scope=md.ScanScope(),
)
```

## Rung 8: Derived Metrics

Registry-only; no datasource access needed.

```python
derived_brief = project.prepare_derived_metric(
    numerator="sales.revenue",
    denominator="sales.orders_count",
)
```

## Closeout

After all objects pass `verify_object`, run `project.readiness(...)` once:

```python
report = project.readiness(refs=("sales.orders", "sales.revenue"))
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
