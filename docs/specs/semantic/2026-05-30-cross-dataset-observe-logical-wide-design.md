# Cross-Dataset Metric Definition and Observe Design

Date: 2026-05-30 (revised)

Status: revised breaking design proposal, phased delivery, pending re-approval. This
document expands the earlier logical-wide-only scope. Root-preserving left-join
widening (formerly "logical wide") is now the safe-star case of a typed fan-out
model rather than the whole design.

## Problem

`session.observe(...)` can already use relationships in one narrow path: derived
component metrics can be grouped by a dimension on a relationship-reachable
dataset. Base multi-dataset metrics, cross-dataset dimensions, and cross-dataset
filters are still blocked or inconsistently interpreted.

The deeper problem is that a real internet data warehouse is not one shape. A
single metric definition must run against a mix of physical realities:

- **Layered modeling (ODS / DWD / DIM / DWS / ADS).** Detail facts live in DWD,
  dimensions in DIM, and some projects also publish pre-joined or pre-aggregated
  wide tables in DWS / ADS. This version does not route logical metrics to
  rollups automatically; authors model the physical table they want to analyze.
- **Star-schema slicing.** Fact joined many-to-one to dimensions. The common,
  safe case.
- **Snapshot partitions and zipper (SCD2) tables.** Entity state is modeled
  either as daily full snapshots keyed by a `dt` partition, or as
  validity-interval (`valid_from` / `valid_to`) zipper tables. Both are standard.
- **Event tables.** One user produces many events. Funnels, retention, and
  behavior analysis are inherently one-to-many.
- **Scale.** Billions of rows. Join and scan cost dominate, so the first version
  must fail closed and explain join shape before execution rather than inventing
  hidden rewrites.

No single join strategy wins across all of these. A design that only does star
joins cannot express funnels; a design that silently de-duplicates produces
wrong numbers. This version deliberately keeps the semantic object surface small:
it supports safe star widening and versioned dimensions, blocks what cannot be
derived from dataset metadata, and leaves rollup routing out of scope.

## Design Principles

1. **Grain, fan-out, and versioning are first-class, typed, explicit decisions.**
   They are derived from dataset configuration where possible and rejected when
   the dataset metadata is insufficient.
2. **Fail closed by default, with typed escape hatches.** Unsafe widening is
   rejected unless the author selects an explicit, validated policy.
3. **No aggregate navigation in this version.** The metric runs against its
   declared datasets. If a project wants to analyze a DWS / ADS table, that table
   is modeled as a dataset and metric directly.
4. **Reproducible and agent-first.** Every resolved path, partition, and fan-out
   decision is recorded in lineage. Errors are structured and carry candidates so
   an agent can repair without guessing.
5. **The agent is the primary user; the model carries the complexity, the query
   stays flat.** Two personas share this surface. A *modeling* agent (with
   business-expert assist) authors datasets, relationships, and metrics — a
   low-frequency, deliberately explicit act. An *analysis* agent calls
   `session.observe(...)` autonomously and at high frequency. The invariant that
   protects the high-frequency loop: **multi-dataset observe accepts exactly the
   same arguments as single-dataset observe** (metric, `dimensions`, `where`,
   `timescope`, `grain`, `time_field`). Every cross-table decision —
   `root_dataset`, derived join safety, derived version mode, fan-out policy, and
   resolved partitions — is resolved from semantic objects by the planner, never
   passed at observe time. The analysis agent never chooses a join policy or an
   edge; it reads structured repair errors and an `explain` plan instead. Some
   repair actions are mechanical, while others are modeling decisions that require
   a modeling-agent or business-owner loop.

## Goals

- Define multi-dataset metrics with an explicit **grain** and **additivity**,
  so cross-join aggregation is provably safe.
- Support `scalar`, `time_series`, `segmented`, and `panel` observe shapes for
  base multi-dataset metrics.
- Support derived ratio and weighted-average metrics whose components are
  themselves logical-wide base metrics, including components rooted on different
  fact datasets and, at the component boundary, different datasources.
- Let metric expressions, `dimensions=`, and `where=` reference fields on
  relationship-reachable datasets.
- Make fan-out a typed choice: `block` (default), `symmetric_aggregate`, or
  `aggregate_then_join`, validated against measure additivity.
- Support an explicit base metric `root_dataset`; the root is the row-grain,
  time-axis, and join anchor.
- Support both warehouse SCD patterns: daily **snapshot partitions** and
  **validity-interval (zipper)** tables, with `latest` and `as_of_root_time`
  policies.
- Support first-class conversion (funnel) metrics over event tables.
- Keep observe time axes rooted on the metric's declared root dataset.
- Keep the multi-dataset `observe(...)` argument surface identical to
  single-dataset observe; author all cross-table config on the semantic objects.
- Add a read-only `session.explain(...)` that mirrors observe's arguments and
  returns the resolved plan without executing.
- Use the public entry points `session.observe(...)`, `session.explain(...)`,
  `DimensionRef(...)`, `MetricRef(...)`, and the semantic metric decorators. Their
  behavior is allowed to change to match this design; no compatibility shim is
  required.

## Non-Goals

- Do not infer the root dataset or fan-out policy. Join safety and version join
  mode are derived only from dataset keys and dataset versioning metadata; if the
  planner cannot derive them, observe fails closed.
- Do not silently repair fanout with automatic distinct, deduplication, bridge
  handling, or pre-aggregation; repair is only ever an explicit typed policy.
- Do not add aggregate navigation or rollup routing in this version.
- Do not add cross-datasource federation inside a single logical-wide plan.
  Derived metrics may still merge separately planned component frames from
  different datasources at the frame layer.
- Do not allow non-root dataset time fields to drive `timescope` or `grain` in
  the first version.
- Do not add nested derived metric support (a derived metric as another derived
  metric's component) in this version.

## Phased Delivery

The full model is large. Delivery is ordered correctness first, then capability.
Each phase is independently shippable, and each section below is tagged with the
phase that delivers it.

| Phase | Scope | Why this order |
| --- | --- | --- |
| **Phase 1 — Safe star core** | Grain + additivity; explicit `root_dataset`; root-only measures as the additive default; dataset-key-derived join safety; root-preserving left-join star widening; snapshot `latest` / `as_of_root_time` derived from dataset versioning; plan-scoped field resolution; `session.explain(...)`; structured repair errors. | Smallest useful multi-dataset observe: slice a root fact by safe dimensions and daily snapshots, with no new relationship safety knobs. |
| **Phase 2 — Versioned and derived correctness** | Validity-interval (SCD2) current-row and `as_of_root_time` execution; interval-boundary semantics and overlap detection; derived components on independent roots; component axis and resolved-version comparability. | Adds the remaining correctness shapes after the safe-star planner exists: zipper tables and cross-fact derived metrics. |
| **Phase 3 — Fan-out and funnels** | Typed fan-out opt-in (`symmetric_aggregate`, `aggregate_then_join`) and first-class conversion/funnel metrics. | Relaxes Phase 1 / 2 hard errors for one-to-many, many-to-many, and event-sequence workloads. |

Each phase is independently reviewable. Phase 1 is a coherent subset on its own;
Phase 2 builds on the same planner without changing the flat observe surface; and
Phase 3 only relaxes earlier hard errors through explicit typed policies.

## Architecture

Add an analysis-layer observe planner, for example
`marivo/analysis/intents/observe_planner.py`. The planner converts semantic
objects and observe inputs into an executable plan. It does not execute ibis
expressions, persist frames, or decide the output frame shape.

The planner is responsible for:

- identifying the root dataset and its grain anchor
- validating aggregation additivity and root-only base aggregation
- resolving field-like refs used by dimensions and filters (plan-scoped)
- collecting datasets required by metric arguments, dimensions, and filters
- finding relationship paths from root to required datasets, with join safety
  derived from dataset keys, traversal direction, and dataset versioning
- resolving snapshot / validity partition predicates
- building the widened table, or pre-aggregated component frames
- exposing dataset argument views for metric callables
- projecting dimension expressions and preparing joined-dataset filters
- emitting an inspectable plan for `session.explain(...)` and typed repair errors,
  both without executing ibis

`observe.py` remains responsible for: resolving windows and frame shape; applying
root time windows and buckets; invoking metric or derived expression evaluation;
executing ibis expressions; constructing axes metadata; persisting `MetricFrame`
and `ComponentFrame` artifacts; writing job records and evidence metadata.

Existing derived-only helpers such as relationship path search and joined
dimension table construction move into the planner or are replaced by it.

## Grain And Additivity (Phase 1)

Authoring stays minimal: a dataset already declares its `primary_key`, and a
multi-dataset metric declares one `root_dataset` (next section). That is all the
author writes. From those two the planner derives three grains it keeps strictly
separate instead of collapsing them into "the root":

- **Dataset row grain** — authored as `primary_key`: the keys that make one
  physical row unique (an order-item table is keyed by order-item, a daily
  snapshot by `[user_id, dt]`). This is what makes `orders.amount.sum()` provably
  a per-order-item sum rather than an accidental double count.
- **Join anchor** — authored as `root_dataset`: the dataset a multi-dataset
  metric widens from. The anchor is where rows are preserved; it is not
  necessarily the finest grain in the plan.
- **Aggregation grain** — derived per query from `dimensions=` plus the root
  grain. The planner uses this for grouping only; it does not route to alternate
  rollup tables in this version.

**Additivity** is declared relative to the dataset row grain: it is the
type-system fact that decides whether a measure can be safely summed across a
join or rolled up across time. A dataset whose `primary_key` is only unique after
a version predicate — a snapshot keyed by `[user_id, dt]` is unique on `user_id`
only once a partition predicate is applied — has that post-predicate grain as its
effective row grain, and additivity is judged there.

```python
@ms.metric(datasets=[orders], additivity="additive", decomposition=ms.sum())
def gmv(orders):
    return orders.amount.sum()

@ms.metric(
    datasets=[account_snapshot],
    additivity="semi_additive",
    non_additive_dims=["dt"],     # additive across accounts, not across time
    decomposition=ms.sum(),
)
def balance(account_snapshot):
    return account_snapshot.balance.sum()

@ms.metric(datasets=[events], additivity="non_additive", decomposition=ms.sum())
def active_users(events):
    return events.user_id.nunique()
```

`additivity=` is a new kwarg on `@ms.metric`, orthogonal to `decomposition=`:
additivity decides cross-join / cross-time summability, while `decomposition=`
decides dimensional attribution for `decompose`; the two are chosen independently.
Required-ness is explicit and fails closed:

- Every base metric must declare it. Omitting `additivity=` is a load / readiness
  error, not a silent default.

Additivity values:

- `additive`: summable across every dimension (GMV, order count, quantity).
- `semi_additive`: summable across some dimensions but not others, with declared
  `non_additive_dims`. Classic for snapshot balances, inventory, and per-day
  counts. This design does not add a `non_additive_agg` setting; queries that
  cross a non-additive dimension are rejected until a later design defines an
  explicit rollup behavior.
- `non_additive`: cannot be summed from partial aggregates (distinct counts,
  ratios, averages). Must be recomputed from a grain at least as fine as the
  query, or read from a mergeable sketch (for example HLL for distinct counts).

This single declaration drives fan-out safety. It is the connective tissue of the
design. The **root-only measure rule** below is exactly the additive default of
this model: a base metric aggregates only its root grain.

## Root Dataset (Phase 1)

For a base metric with more than one dataset, the semantic author must declare
`root_dataset`. The root dataset defines the metric's row grain, the join
anchor, and the only dataset whose time fields can drive `timescope`, `grain`,
and `time_field`.

```python
@ms.metric(
    datasets=[orders, users],
    root_dataset=orders,
    additivity="additive",
    decomposition=ms.sum(),
)
def revenue_by_user_state(orders, users):
    return orders.amount.sum()
```

For a single-dataset base metric, `root_dataset` is optional and defaults to that
dataset. For a multi-dataset base metric, omitting `root_dataset` is a load or
readiness blocker. Implementation must not silently rely on list order as the root
contract. The declared root must be one of `metric_ir.datasets`. The loader/checker
renders it visibly in project descriptions and readiness output, because it is a
load-bearing semantic choice.

**Root-only measure rule.** Aggregate measures belong to the root dataset.
Joined dataset fields may be used as dimensions, filters, relationship keys, and
row-level predicates that qualify root rows. They must not be aggregated as
measures inside the base metric expression, because many-to-one joins repeat the
joined-side row across root rows. With `orders` as root and `users` joined
many-to-one, `orders.amount.sum()` is valid, while `users.score.sum()` or
`users.count()` would count user-side values once per order and is rejected.

This rule is enforced at authoring/load/readiness time with the existing metric
body AST validation pipeline, not first discovered during backend execution. The
validator maps metric function parameters to `datasets=[...]` by parameter
**position**, asserts the callable arity matches the dataset list, and then uses
the declared `root_dataset` to identify the root parameter. It must not match by
parameter name, because authors may choose local argument names that differ from
semantic dataset ids. For each aggregate call it inspects the receiver chain's
dataset parameter root: aggregates whose receiver belongs to the root dataset are
valid; aggregates whose receiver belongs to a non-root dataset are invalid.
Non-root fields remain valid inside row-level predicates that qualify a root
aggregate, such as filtering `orders.amount.sum(...)` by `users.country`. They
are invalid when they are the aggregate receiver or the value being aggregated.

Independent aggregates across multiple roots must be modeled as derived metrics
over component metrics. This keeps numerator and denominator grains explicit and
prevents a joined row space from silently changing scalar business definitions.

For a derived metric, each component metric is planned independently. A component
metric must be a non-derived metric with a valid root: its explicit
`root_dataset` for multi-dataset components, or its only dataset for
single-dataset components. This design removes the existing observe limitation
that rejects derived components with more than one dataset. Each component uses
the same planner, additivity rules, and root-only measure rules as an ordinary
base metric.

## Relationships And Join Discovery (Phase 1)

Join discovery uses the keys datasets already declare; no new authoring primitive
is introduced. A dataset's `primary_key` plus the `from_fields` / `to_fields` of a
`relationship` give the planner its join edges.

The semantic model does **not** add relationship-level `cardinality` or
`snapshot_policy` fields. Join safety is derived from dataset configuration only:

- The side whose relationship fields match its effective dataset key is the `one`
  side.
- For non-versioned datasets, the effective key is `primary_key`.
- For a snapshot dataset entered from a non-versioned root, the effective key is
  `primary_key` minus the snapshot partition field after the planner resolves one
  partition for the join.
- For a validity dataset entered from a non-versioned root, the effective key is
  `primary_key` minus validity interval fields after the planner collapses to one
  row per key and anchor.

If neither side can be proven to be the `one` side from those dataset keys, the
edge is `unknown` and planning fails. Authors cannot override that failure with a
relationship kwarg; they must fix dataset keys, model a more precise dataset, or
wait for an explicit Phase 3 fan-out policy.

Relationships are therefore just explicit join edges with optional business names:

```python
ms.relationship(
    name="orders_to_user_profile_current",
    from_dataset=orders,
    to_dataset=user_profile_daily,
    from_fields=[order_user_id],
    to_fields=[user_id],
)
```

**Direction-normalized join safety.** Graph search is bidirectional, so the
planner normalizes each edge to the actual traversal direction. A traversal from a
many side to a proven one side is safe for default widening; a traversal from one
side to many side is unsafe and blocked unless a later fan-out policy handles it.
For example, `orders -> users` is safe when `to_fields` match `users.primary_key`;
the same edge traversed from root `users` to target `orders` is one-to-many and
blocked. If both sides match their effective keys, the traversal is one-to-one and
safe in either direction. If no side matches, the traversal is `unknown` and
blocked.

**Effective join safety is a resolved-edge property.** What the planner checks,
records in lineage, and names in errors is the resolved edge: `(relationship,
traversal direction, resolved version mode)`. The version mode is derived from the
target dataset's `versioning` and the available root time context, not from a
relationship-level `snapshot_policy`.

**Path selection.** For each required dataset other than the root, the planner
searches paths with shortest-path precedence. Missing path raises a structured
error. Exactly one shortest path is used and validated. Multiple equal-length
shortest paths raise an ambiguity error rather than choosing implicitly. Longer
paths are not considered when a shorter one exists, which avoids silently
changing semantics in snowflake schemas with redundant routes. Two same-length
paths that differ only by relationship id are semantic peers and are reported as
ambiguous with their relationship ids. The path search is shared by base and
derived observe.

**Limitation and reserved escape hatch (future).** Shortest-path precedence
prevents silent detours, but it is not a semantic oracle. A single entity often
has several legitimate roles — placing user, paying user, receiving user; product
category as-of-order vs current — and the shortest path is not always the intended
one. Phase 1 only disambiguates *equal-length* peers, by erroring. A future phase adds
explicit named-route selection (a relationship already carries a `name`) so an
author can choose a longer but semantically-correct path. That route choice belongs
on authored semantic objects — for example a modeled dimension, metric, or
field-like ref that already encodes the business role — not as an ad hoc
`observe(...)` kwarg. This preserves the flat analysis-agent loop: the agent asks
for a metric and axes, while the semantic model carries the route semantics. The
full API is deliberately out of scope here to avoid over-building before the star
core lands; until then, model distinct roles as distinct relationships and
reference the intended dataset explicitly.

## Fan-Out Policy (`block` is Phase 1 / 2; opt-ins are Phase 3)

When a traversal is not direction-normalized-safe, Phase 1 and Phase 2 reject the
plan with `block`. There is no relationship-level fan-out kwarg and no observe-time
fan-out argument. The only Phase 1 / 2 executable resolution for an unsafe
traversal is to re-root or remodel so join safety can be derived from dataset
keys.

The default star-widening path builds the logical table from the root with
root-preserving left joins; root rows are preserved when joined rows are missing.
Marivo does not deduplicate or pre-aggregate to repair unsafe fanout in Phase 1 / 2.

Phase 3 reserves two typed opt-ins, `symmetric_aggregate` and
`aggregate_then_join`, but they are not authorable or executable before Phase 3.
Their detailed semantics, authoring surface, and repair actions belong to the
Phase 3 implementation plan, not the Phase 1 planner.

## Dataset Versioning: Snapshot And Validity (Phases 1-2)

Versioning metadata is declared on the dataset, not on each relationship, because
the same versioned dataset can serve both window-end and historical fact-time
analysis. Phase 1 adds snapshot versioning. Phase 2 adds validity / zipper
versioning.

```python
# Daily full snapshot keyed by a dt partition.
@ms.dataset(
    name="user_profile_daily",
    datasource="warehouse",
    primary_key=["user_id", "dt"],
    versioning=ms.snapshot(
        partition_field=dt,
        grain="day",
        timezone="Asia/Shanghai",   # tz used to cut partitions
        format="%Y%m%d",            # physical encoding; or parser= for custom
    ),
)
def user_profile_daily(backend):
    return backend.table("user_profile_daily")

# Phase 2: zipper / SCD2 validity-interval table.
@ms.dataset(
    name="price_history",
    datasource="warehouse",
    primary_key=["product_id", "valid_from"],
    versioning=ms.validity(
        valid_from=valid_from,
        valid_to=valid_to,
        interval="closed_open",          # [valid_from, valid_to); or "closed_closed"
        open_end=[None, "9999-12-31"],   # valid_to values that mean "still current"
        # current_flag=is_current,       # alternative: a boolean "current row" column
        timezone="Asia/Shanghai",
    ),
)
def price_history(backend):
    return backend.table("price_history")
```

Phase 1 supports day-grain snapshot partitions. Phase 2 supports day-grain
validity intervals. Ordinary datasets with no `versioning` are non-versioned
datasets.

Phase 2 validity tables declare their dialect explicitly, because enterprise zipper
encodings vary. `interval` fixes the boundary convention (`closed_open`
`[from, to)` is the common zipper form; `closed_closed` `[from, to]`), and
`open_end` (or `current_flag`) declares how "still current" is physically encoded
— `valid_to is null`, a sentinel such as `9999-12-31` or `2999-12-31`, or an
`is_current` flag. Boundary comparison honors the partition timezone and whether
the columns are `date` or `timestamp`. Without these, a `latest` or
`as_of_root_time` join cannot be written correctly against a real SCD2 table.

Snapshot datasets may declare partition encoding with `format=` or, for
non-standard encodings, `parser=`. This lets the planner compare and format Hive
style string or integer partitions such as `dt="20260530"` without assuming the
physical partition field is a SQL date type. The parser normalizes physical
partition values into logical partition dates for planning; the formatter
converts resolved logical dates back into the dataset's physical encoding for the
predicate. When the partition field is a true `date` / `timestamp` type, both may
be omitted and native comparison is used.

When `timezone` is omitted, the partition timezone defaults to the system
timezone, not the analysis session timezone, matching warehouse pipelines that
cut partitions on a fixed business timezone.

The planner derives the version join mode from dataset versioning and the root
time context; there is no relationship-level `snapshot_policy` setting.

## Snapshot And Validity Join Execution (Phases 1-2)

The version join mode is derived, not authored on relationships. Every derived
mode resolves to a single row per `(key, anchor)` **before** the join, so a single
root row never matches multiple historical rows. A raw `version <= anchor` range
join is forbidden, because that duplicates root rows on the time axis — duplication
that key-derived join-safety checks do not catch, because it is not on the join
key.

| Versioning | Derived mode | Join shape |
| --- | --- | --- |
| `snapshot` | `latest` | `dt = resolved_partition`, a plan-time constant; equi-join. |
| `snapshot` | `as_of_root_time` | per root row, `dt = max(dt <= anchor_date)`; build an anchor-to-partition mapping, then equi-join (or an as-of join). |
| `validity` | `latest` | The declared current-row predicate: `valid_to in open_end` (default `valid_to is null`) or `current_flag` is true. |
| `validity` | `as_of_root_time` | `anchor` falls in `[valid_from, valid_to)` or `[valid_from, valid_to]` per the declared `interval`; `open_end` values extend to `+inf`. Intervals must be non-overlapping per key; overlap is a data-quality error, not a silent fanout. |

For a versioned target, the planner uses `as_of_root_time` when the root has a
resolvable day-level time field and the join is evaluated against root rows. This
is a deliberate closed decision: historical facts are interpreted with fact-time
dimension state whenever root time exists. There is no relationship-level override
to force window-end/current dimensions in this version. If no root time field is
available, the planner falls back to `latest`: it anchors on resolved
`timescope.end` when a timescope is present, otherwise on a plan-time
`as_of_current_time` based on current system time. The chosen mode and anchor
source are recorded in lineage and exposed by `explain`.

Snapshot/validity predicates are applied only when traversal **enters** a
versioned dataset. Traversing from a versioned dataset back to a non-versioned
fact dataset does not make the reverse edge safe and adds no predicate to the
fact. If no version at or before a derived anchor exists, observe raises a
planning error instead of joining nulls. If the exact anchor is missing and the
planner falls back to an earlier version, frame metadata includes a quality
warning.

**Reproducibility and partition discovery.** The reproducibility anchor recorded
in lineage is the **resolved partition value** (or the resolved
anchor-to-partition mapping), not merely a listing timestamp: a re-run pins the
same physical version by replaying the resolved partition, while the timestamp
only explains *when* it was discovered. Partition discovery itself is a datasource
capability — listing thousands of daily partitions has real cost and may need
permissions some roles lack — so a production implementation caches listings and
surfaces a typed error when listing fails. The catalog/provider interface, cache
TTL, and refresh policy are implementation concerns, deliberately left out of this
contract to keep it minimal; only the reproducibility guarantee (a replayable
resolved partition) is normative here.

## Field Resolution (Phase 1)

All observe field-like refs use one resolver: `dimensions=`, `where=` keys,
explicit `time_field=`, and relationship key fields.

A fully qualified semantic field id resolves directly. A short field name is
accepted only when it is unique within the plan's **statically known** dataset
set: the metric root and the datasets listed on the metric. That set is fixed
before any dimension or filter is resolved, which avoids a chicken-and-egg cycle —
resolving a short `dimensions=` / `where=` ref must not itself depend on which
datasets those same dims and filters pull in. A dimension or filter that targets a
dataset outside the metric's own datasets must therefore use a fully qualified
field id or an explicit `DimensionRef`; it cannot be introduced by a bare short
name. This also prevents the surprise where adding one dimension flips an
unrelated short filter from unique to ambiguous. The resolver does not require a
short name to be globally unique across unrelated project datasets. If a short
name matches multiple fields inside the statically known set, observe raises an
ambiguity error that includes candidate fully qualified ids.

This keeps `dt`, `user_id`, `status`, and `country` usable in large projects
while staying fail-closed, and prevents root-first, relationship-distance, or
datasource-local heuristics from silently choosing the wrong field. Resolution is
plan-context-dependent but never silently picks.

## Multi-Fact, Ratio, And Funnel Composition (Phases 2-3)

The planner collects required datasets per observe path. For base metrics:
every dataset listed on the metric, each requested dimension's dataset, each
`where` field's dataset. For derived metrics, each component plan requires the
component metric's datasets plus its dimension and filter datasets.

Component metrics may have different roots. A conversion rate can use an
`orders`-rooted numerator and a `sessions`-rooted denominator. Each component is
filtered, widened, bucketed, and grouped on its own root row space, then
component outputs are merged on the derived metric axes.

**Component datasource rule.** Each individual component plan must be executable
by one datasource: the planner does not federate a single component's root and
joined datasets across datasources, and such single-plan cross-datasource shapes
raise `CrossBackendMetricError`. Different derived components may use different
datasources, because their component frames are materialized independently and
merged in the frame layer after aggregation.

**Conversion / funnel (Phase 3).** A first-class conversion metric expresses ordered
steps over an event table within a time window, built on `aggregate_then_join`
over the event grain with step ordering. This is the dominant one-to-many
internet shape and is not expressible as a star join.

## Component Comparability (Phase 2)

Derived component outputs may be merged only when their axes are semantically
comparable. For each requested dimension, each component plan must resolve the
dimension to the **same semantic field id**. Matching by label, physical column
name, or formatted display name is not enough. Mismatches block by default with a
`component-axis-field-mismatch` error listing the per-component resolutions.

**Conformed-dimension escape (future).** Same-field-id is the safe Phase 2 default but
is stricter than real cross-fact analysis needs: `orders.country` and
`sessions.country` are frequently the same business axis expressed as two field
ids. A future phase adds a declared conformed-dimension (semantic-axis)
equivalence so two field ids can be asserted comparable when they share encoding,
enum domain, and snapshot policy. That declaration is intentionally deferred to
avoid expanding the authoring surface before the strict default is proven; until
it exists, model a shared axis as a single conformed dimension and reference it
from each fact.

Snapshot choices must also be comparable. If component plans depend on different
derived version modes, anchors, or resolved partitions for any versioned dataset that
contributes a requested axis, filter predicate, or row-level population
predicate, the derived metric blocks by default with
`component-version-mismatch`. This prevents a ratio from silently
combining a numerator segmented by `user_profile` as of the observe window end
with a denominator segmented by `user_profile` as of each root event date.

Lineage records per-component axis and snapshot resolution: component metric id,
root dataset, datasource, dimension field id, relationship path, derived version
mode, anchor source, anchor value or mapping, timezone, resolved partition or
mapping, planner timestamp, and partition listing timestamp when applicable.

## Time And Filters (Phase 1)

`timescope`, `grain`, and `time_field` are root-only in the first version. A
non-root `time_field` raises a clear unsupported-shape error.

Window filtering and bucketing use the root dataset's time field. `where`
predicates are classified by the dataset each one targets, and the phase follows
from that classification:

- **Root-population predicates** (targeting only the root dataset) are pushed onto
  the root *before* the widening join, alongside the time window. They shrink the
  row space the join sees and never depend on joined coverage.
- **Joined-dimension predicates** (targeting a widened dataset) apply *after* the
  join. A joined predicate has a semi-join effect: it can remove root rows whose
  joined value is null (normal SQL left-join-then-filter behavior). When a
  joined-field filter drops rows because the joined side was missing, frame
  metadata includes a quality note so users can distinguish intentional
  dimensional filtering from root-row loss caused by missing relationship
  coverage.

The `where=` surface stays single; the planner does this classification, it is not
authored. Explicit per-phase controls (a dedicated measure-level filter, or
independent numerator/denominator filters in a derived ratio) are deliberately out
of scope for this version and can be added later if real metric definitions need
them.

Snapshot and validity joins never change the observe time axis. The planner
records every resolved partition / interval in frame metadata and lineage params,
including relationship id, target dataset, derived version mode, anchor value when
one exists, whether the anchor came from `timescope.end`, `as_of_current_time`, or
a root row, timezone, resolved partition, planner timestamp, and partition listing
timestamp. If `latest` has no timescope, frame metadata exposes a visible
`confidence_scope` note that the version was fixed at planning time rather than
tied to an observe window. The planner does not use the analysis session timezone
for version selection unless that timezone is also the dataset's declared or
default system timezone.

## Base Observe Flow (Phase 1)

1. Resolve the metric and use its declared `root_dataset` as root.
2. Build a plan from metric datasets, dimensions, filters, and the optional root
   time field. Validate root-only measures (already enforced at load). Classify
   each `where` predicate as root-population or joined-dimension.
3. Materialize the root table and apply the root time window, optional bucket, and
   root-population predicates *before* the join, so the widening join scans only
   the qualifying root rows. For `as_of_root_time`, derive the
   anchor-to-partition mapping from this filtered root set.
4. Widen required datasets onto the filtered root (root-preserving left join for
   direction-safe star edges; the selected fan-out policy otherwise), applying any
   snapshot / validity predicate where traversal enters a versioned dataset.
5. Apply joined-dimension `where` predicates after the join.
6. Project requested dimensions.
7. Invoke the metric callable with dataset argument views ordered by
   `metric_ir.datasets`; execute scalar / time-series / segmented / panel
   aggregation using the same output contracts as single-dataset observe.

Dataset argument views may all be backed by the same joined table, but field
functions must still resolve according to their declared dataset. The planner
isolates this mapping so callables keep the existing multi-argument signature.

## Derived Observe Flow (Phase 2)

1. Resolve the derived metric's component metric ids.
2. For each component, create a component-level plan using that component's root.
   Multi-dataset component metrics are allowed when they declare a valid
   `root_dataset` and satisfy the same additivity, root-only measure, and
   relationship-safety rules as base observe.
3. Apply the same root time, joined filter, and dimension projection rules.
4. Enforce the component comparability contract across components.
5. Aggregate each component by the parent axes and merge on those axes.
6. Evaluate the derived sentinel expression into the final metric value.
7. Persist a clean parent `MetricFrame` and, for component-aware decompositions,
   a `ComponentFrame` with the same axes.

Derived relationship-dimension behavior is not preserved for compatibility. Phase
1 focuses on base observe; Phase 2 replaces the derived-specific inner-join path
with the shared root-preserving left-join planner and then enables multi-dataset
component metrics. After Phase 2, relationship dimensions preserve unmatched root
rows as nulls. Edges whose safety cannot be derived from dataset keys fail closed
and must be remodeled. Add inner-vs-left comparison coverage for fixtures with
unmatched rows, because left join preserves rows that inner join dropped.

## Plan Inspection: `explain` (Phase 1)

An agent repairs better when it can see what the planner decided *before* paying
to execute. `session.explain(...)` takes the same arguments as
`session.observe(...)` and returns a structured, JSON-serializable `ObservePlan`,
running no ibis expression and persisting nothing:

- `schema_version`, so agents can branch on the plan contract rather than object
  shape accidents
- `plan_digest`, a deterministic digest of the resolved root, axes, filters,
  relationship paths, policies, and resolved versions; agents can compare digests
  before and after a repair to detect semantic movement
- a replay section containing resolved partition values or anchor-to-partition
  mappings; this is the reproducibility pin even when the original policy used
  `as_of_current_time`
- the resolved `root_dataset` and the metric's additivity
- every resolved edge: relationship id, traversal direction, effective
  join safety, blocked fan-out reason, and join type
- each versioned dataset's resolved partition or validity predicate, the anchor
  value, and the anchor source (`timescope.end`, `as_of_current_time`, or root)
- a `temporal_semantics` section for each versioned dimension/filter, naming
  whether a historical fact is being interpreted with a window-end, plan-time, or
  fact-time version; analysis agents use this text in their final explanation
- how each `dimensions=` / `where=` ref resolved (field id and owning dataset),
  and each `where` predicate's phase (root-population vs joined-dimension)
- quality warnings (snapshot fallback, joined-filter row loss, `latest` with no
  timescope)
- if the plan is not executable, the same typed repair payload (next section)
  that `observe` would raise, so the agent repairs without a failed run

This is exactly the data otherwise written to lineage after execution; `explain`
surfaces it ahead of time, turning "run the query and check whether the numbers
look wrong" into "inspect the resolved plan and verify it." `explain` is
read-only: it does not persist a `MetricFrame`, write a job record, or consume a
query budget. The object must support a stable `model_dump(mode="json")` (or
equivalent) representation; messages may change, but schema fields and enum
values change only through `schema_version`.

## Errors As The Repair Contract (Phases 1-3)

For an agent the error surface *is* the primary API: the loop is
write -> run -> read error -> repair -> re-run, so every planner rejection is a
typed, machine-actionable repair instruction, not just prose. Each error carries:

- `schema_version` — the version of the error payload contract.
- `code` — a stable enum (for example `unsafe-fanout`, `missing-root`,
  `ambiguous-path`, `component-axis-field-mismatch`) the agent branches on without
  parsing English.
- `message` — the human-readable explanation.
- `candidates` — the concrete options in play (relationship ids, candidate paths,
  candidate field ids, legal policies).
- `repair` — one or more structured edits that resolve the error, naming the exact
  object and kwarg to change and a suggested value.

Every repair action also declares its safety class:

- `auto_safe`: a mechanical edit that does not change business semantics, such as
  replacing a short ref with a fully qualified field id when the target field is
  already determined by explicit context. Ambiguous business intent is never
  `auto_safe`.
- `modeling_decision`: a semantic choice that changes or confirms metric meaning,
  such as changing `root_dataset` or remodeling a dataset key so join safety can
  be derived. An analysis agent should surface this to a modeling agent or
  business owner instead of silently applying it.
- `unsafe_without_approval`: a repair that may preserve execution but likely
  changes historical meaning or grain; it must not be applied automatically.

Before Phase 3, repair actions do not recommend fan-out policies. Unsafe fan-out
repairs name safe roots, missing dataset-key evidence, or remodel options. Phase 3
may add `symmetric_aggregate` / `aggregate_then_join` repair actions with their own
safety rules.

```json
{
  "schema_version": "observe-error/v1",
  "code": "unsafe-fanout",
  "message": "users -> orders is one_to_many; widening would duplicate users rows.",
  "edge": "users_to_orders",
  "candidates": {"safe_roots": ["orders"]},
  "repair": [
    {"action": "set_metric_root", "metric": "revenue_by_user",
     "root_dataset": "orders", "safety": "modeling_decision",
     "why": "the root defines preserved rows and the observe time axis"}
  ]
}
```

A stable `code` lets an agent reuse a repair across sessions. Codes are enums:
they are never reused with different meaning inside the same `schema_version`.
Human messages may change; agents branch on `schema_version`, `code`, and typed
details, never on prose. Phase 1 must define the full enum and required payload
fields for every planner rejection it introduces before implementation lands.
Later phases add their own codes under the same schema-versioned contract.
Existing errors remain where accurate; derived-specific names do not leak into
base planning. Expected cases include:

- relationship path missing / ambiguous from root to a required dataset
- multiple equal-length relationship paths between root and target
- traversal unsafe or unknown because join safety cannot be derived from dataset
  keys and versioning metadata
- unsafe fan-out traversal blocked before Phase 3
- `as_of_root_time` with no resolvable root day-level time field
- snapshot partition / validity interval not resolvable at or before the anchor
- overlapping validity intervals for one key
- validity table with no resolvable current-row predicate (`open_end` /
  `current_flag`) for a `latest` policy
- invalid snapshot partition timezone, or missing `format` / `parser` for a
  non-date partition
- short field ref ambiguous within the plan scope; field ref not found
- non-root `time_field` requested
- cross-datasource plan inside one base or component plan
- multi-dataset base metric missing explicit `root_dataset`; declared
  `root_dataset` not in `datasets`
- base metric aggregates a non-root field, or aggregates across multiple roots
  instead of using derived components
- logical-wide metric body uses non-root fields as aggregate receivers instead of
  row-level predicates
- derived component axis field mismatch (`component-axis-field-mismatch`)
- derived component version mode or resolved-partition mismatch
  (`component-version-mismatch`)
- empty metric dataset list for a base metric

The `candidates` and `repair` fields carry this context: metric id, root dataset,
target dataset, field ref, required datasets, candidate fields, candidate paths,
offending measure, and additivity where relevant. Component comparability errors
also include component metric id, datasource, resolved dimension field ids,
derived version mode, anchor source, and resolved partition metadata.

For observe-input fixes, repair actions should include canonical Python snippets
when doing so is unambiguous, for example
`mv.DimensionRef("sales.user_profile_daily.country")` or a fully qualified field
id to replace a short ref. These snippets are conveniences for analysis agents;
the structured `action`, `target`, `arg`, and semantic ids remain the canonical
machine contract.

The `SegmentedMultiDatasetUnsupported` and `WindowedTimeSeriesUnsupported` guards
are removed or narrowed so they do not block supported plans.

## Breaking Change Policy

This design is a breaking semantic replacement. Implementation does not preserve
old multi-dataset observe semantics, old derived relationship inner-join behavior,
or old readiness defaults. There is no compatibility shim, data migration layer,
or deprecation period in scope.

- Base metrics must satisfy the new `root_dataset`, `additivity`, and root-only
  measure contracts.
- Relationship safety must be derivable from dataset keys and dataset versioning;
  relationship-level `cardinality` / `snapshot_policy` overrides do not exist.
- Independent aggregates across roots are rejected unless modeled as derived
  components under the new derived planner.
- Relationship-dimension joins use the new root-preserving left-join semantics
  once their phase lands.

## Documentation Updates

Update user-facing and agent-facing docs in the same implementation change:

- `docs/specs/analysis/python-analysis-operator-design.md`
- `docs/specs/semantic/python-semantic-layer.md`
- `marivo-skills/marivo-analysis/SKILL.md`
- `marivo-skills/marivo-analysis/references/cheatsheet.md`
- `marivo-skills/marivo-analysis/references/pitfalls.md`
- `marivo-skills/marivo-semantic/SKILL.md`
- relevant semantic and analysis examples

Docs must state: the three grains (dataset row grain = `primary_key`, join anchor
= `root_dataset`, aggregation grain) and additivity, including when additivity is
required; explicit root dataset and root-only measures; join safety derived from
dataset keys and dataset versioning only, with no `cardinality` or
`snapshot_policy` relationship kwargs; the Phase 1 fan-out default (`block`) and the Phase 3
fan-out policies as future capability; snapshot and validity versioning, including
`format` / `parser`, the validity dialect (`interval`, `open_end` /
`current_flag`), timezone, and the as-of single-row collapse; phase-classified
`where` predicates; component comparability and datasource rules; reproducible
snapshot/path lineage and fallback quality warnings; plan-scoped field resolution
restricted to metric datasets; and the breaking behavior change.
Snapshot docs must call `latest` a window-end or plan-time current version mode,
not an unqualified present-state policy. The agent-facing docs must also cover the
flat observe surface, the structured repair-error contract (`schema_version` /
`code` / `candidates` / `repair` plus repair safety classes), and the read-only
`session.explain(...)` plan (`schema_version`, `plan_digest`, replay pins, and
temporal semantics). Reader / readiness docs should stay minimal: surface only
root/additivity/versioning/key-derived safety failures needed to explain blocked
planning, not broad discovery APIs.

## Testing Strategy

Add narrow planner tests and observe end-to-end tests, grouped by phase.

Phase 1 planner tests cover:

- single-dataset root defaulting; multi-dataset explicit-root requirement;
  declared root not in `datasets` rejection
- missing `additivity` rejection for every base metric
- positional param-to-dataset mapping with arity assertion, not by name
- root-only measure: non-root field allowed in a root aggregate predicate,
  rejected as an aggregate receiver
- independent-aggregate-composition rejection
- join safety derived from `primary_key`; no relationship-level cardinality
  override; no-key-match edge stays unknown and blocks
- resolved-edge safety: a snapshot edge is safe only after dataset versioning
  resolves a single row per key; without derivable version mode it blocks
- `explain(...)` returns the resolved plan (root, resolved edges, policies,
  resolved partitions, field resolutions, warnings) without executing or
  persisting; an unexecutable plan returns the repair payload; plan JSON includes
  `schema_version`, deterministic `plan_digest`, replayable resolved-version pins,
  and temporal semantics for versioned dimensions / filters
- error repair contract: stable `schema_version`, `code`, `candidates`, and an
  actionable `repair` edit with safety class; unsafe-fanout repair names safe
  remodel/root candidates rather than relationship kwargs
- route selection remains a semantic-model/ref concern and is not added as an
  ad hoc `observe(...)` kwarg
- short field resolution restricted to root + metric datasets; a short name that
  targets a non-metric dataset is rejected (must be fully qualified); ambiguous
  short field with candidates; names colliding only with out-of-plan datasets
  resolving successfully
- direction normalization: traversing from many side to proven one side is safe;
  reverse one-to-many traversal and unknown traversal are rejected
- shortest path wins over longer redundant paths; equal-length paths ambiguous,
  including business-role peers
- snapshot `latest` from `timescope.end`; `latest` without timescope using
  `as_of_current_time`, latest-available partition, visible quality note, and
  lineage; `latest` joins using equality against a single resolved partition
- snapshot `as_of_root_time` using an anchor-to-partition mapping plus equality,
  not a raw `snapshot_partition <= anchor` range join
- as-of date derived in the target partition timezone, not the session timezone;
  timezone default; missing `format` / `parser` rejection for non-date partitions
- snapshot predicate applied only when traversal enters the versioned dataset;
  fallback warning
- lineage records path, derived join safety, derived version mode, anchor, anchor
  source, timezone, resolved partition, planner timestamp, and partition listing
  timestamp
- cross-datasource rejection inside a single base or component plan
- non-root time field rejection

Phase 2 planner tests cover:

- validity `latest` via the declared current-row predicate (`valid_to is null`,
  an `open_end` sentinel, or `current_flag`); `closed_open` vs `closed_closed`
  boundary on `as_of_root_time` interval join; overlapping-interval rejection;
  missing current-row predicate rejection
- component axis field mismatch and version-mode/partition mismatch rejection;
  per-component axis and snapshot resolution lineage

Phase 3 planner tests cover:

- fan-out policy precondition validation: `symmetric_aggregate` additive
  only with PK; `aggregate_then_join` grain merge; `block` default

Phase 1 observe tests cover:

- base multi-dataset scalar / time-series / segmented / panel on the widened row
  space; joined fields used only as filters or dimensions
- base metric rejected when aggregating joined-side fields
- independent aggregate scalar rejected with a repair hint to use derived
  components
- root-population `where` pushed onto the root before the join; joined-dimension
  `where` applied after the join, including the quality note when missing joined
  values drop root rows; null-row preservation
- fanout joins rejected before metric execution
- window-end snapshot join anchored by `timescope.end`; fact-time snapshot join
  via a single resolved partition
- full semantic ids resolving when short field names are ambiguous
- single-dataset observe uses the new required additivity/root contracts

Phase 2 observe tests cover:

- validity `as_of_root_time` interval join
- derived ratio / weighted-average frames preserving parent/component contracts;
  components rooted on different facts; components from different datasources
  merged after datasource-local aggregation; a component that is itself a
  multi-dataset base metric; axis and version-mode mismatch rejection
- independent aggregate scalar modeled with the derived component pattern
- derived relationship-dimension coverage passing through the shared planner

Phase 3 observe tests cover:

- conversion / funnel over an event table

Run the narrow observe and planner tests first, then broaden to the relevant
analysis suite. Because this changes shared observe behavior, run `make test`
before merging if practical.

## Open Decisions Closed By This Design

- Grain and additivity are modeled explicitly; root-only measures are the
  additive default, not the whole story.
- Single-dataset metrics infer root from their only dataset; multi-dataset
  metrics require explicit `root_dataset`; list order is not the contract.
- Fan-out is a typed decision (`block` / `symmetric_aggregate` /
  `aggregate_then_join`), never silent and never a hard wall.
- Joins for direction-safe star edges are root-preserving left joins.
- Both snapshot-partition and validity-interval SCD are first-class; `as_of`
  joins resolve a single version before joining and never use raw range joins.
- Derived version mode is automatic: root-time joins use `as_of_root_time` when a
  root day-level time field exists; otherwise they use `latest` anchored by
  `timescope.end` or plan time. There is no relationship-level override in this
  version.
- Derived components must resolve axes to the same semantic field id, may span
  datasources at the component boundary only, and must have comparable resolved
  version choices.
- Time axes are root-only. Short field names are unique within the metric's own
  dataset set; cross-dataset refs must be fully qualified.
- Grain is three separate concepts (dataset row grain = `primary_key`, join anchor
  = `root_dataset`, aggregation grain = requested dimensions); they are not
  collapsed into "the root".
- Join safety is a resolved-edge property `(relationship, traversal direction,
  derived version mode)`, derived from dataset keys and dataset versioning only;
  there is no relationship-level `cardinality` or `snapshot_policy` override.
- Phase 3 reserves fan-out opt-ins; `symmetric_aggregate` will protect
  measure-bearing rows from join duplication, not the joined dimension's key.
- `where` predicates are phase-classified: root-population pushed before the join,
  joined-dimension applied after.
- Existing metric function signatures are preserved.
- The agent is the primary user: multi-dataset `observe(...)` is argument-flat
  (identical to single-dataset observe); all cross-table config is authored on the
  model and resolved by the planner.
- Errors are a structured repair contract (`schema_version` / `code` /
  `candidates` / `repair`), with repair safety classes separating mechanical fixes
  from modeling decisions.
- `session.explain(...)` returns the resolved plan read-only, before execution,
  with stable JSON, `plan_digest`, replayable version pins, and temporal semantics.
- Future route selection stays on semantic objects or refs, not ad hoc observe
  kwargs.
- Delivery has three phases: safe star core, versioned/derived correctness, then
  fan-out and funnels.

Deliberately reserved for later, to keep this version minimal: explicit
named-route / role path selection; conformed-dimension (semantic-axis) equivalence
across components; the partition catalog / provider interface with caching and
TTL; aggregate navigation / rollup routing; broad discovery APIs; and a per-phase
filter API (measure-level filters, independent numerator / denominator filters).
Each is acknowledged where it bites and left unbuilt until the correctness core
proves it is needed.
