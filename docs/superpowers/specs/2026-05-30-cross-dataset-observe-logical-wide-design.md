# Cross-Dataset Observe Logical Wide Design

Date: 2026-05-30

Status: approved design for implementation planning

## Problem

`session.observe(...)` can already use relationships in one narrow path:
derived component metrics can be grouped by a dimension on a relationship-
reachable dataset. Base multi-dataset metrics, cross-dataset dimensions, and
cross-dataset filters are still blocked or inconsistently interpreted.

Users expect observe to treat relationship-connected fields as part of a
logical wide table: start from the metric's fact-like root dataset, join related
datasets through declared relationships, then apply the same scalar,
time-series, segmented, and panel logic used for single-dataset metrics.

## Goals

- Support base multi-dataset metrics in `observe` for `scalar`, `time_series`,
  `segmented`, and `panel` frames.
- Support derived ratio and weighted-average metrics whose component metrics are
  themselves logical-wide base metrics, including components rooted on
  different fact datasets.
- Let metric expressions, `dimensions=`, and `where=` reference fields on
  relationship-reachable datasets.
- Support an explicit base metric `root_dataset` declaration for logical-wide
  observe; the root dataset is the row-grain, time-axis, and join anchor.
- Require base logical-wide metric aggregations to aggregate root-dataset
  measures only; joined datasets provide dimensions, filters, and row-level
  predicates for root measures.
- Use root-preserving left joins when widening to related datasets, but block
  joins whose declared cardinality can amplify root rows.
- Keep short field refs strict: a short ref must resolve uniquely, otherwise
  callers must use a fully qualified semantic id.
- Keep observe time axes rooted on the metric's declared root dataset.
- Support dataset-level partition snapshot semantics for relationship joins,
  including window-end/latest-available and fact-time snapshot policies.
- Migrate the existing derived relationship join implementation to the shared
  planner so base and derived observe use one field-resolution and join model.
- Preserve existing public entry points: `session.observe(...)`,
  `DimensionRef(...)`, `MetricRef(...)`, and semantic metric decorators.

## Non-Goals

- Do not infer the root dataset for multi-dataset metrics from relationship
  topology, available time fields, or metric expression shape.
- Do not add cross-datasource federation inside a single logical-wide plan.
  Derived metrics may still merge separately planned component frames from
  different datasources at the frame layer.
- Do not infer relationship cardinality without evidence.
- Do not repair fanout amplification with automatic distinct, deduplication,
  bridge handling, or pre-aggregation.
- Do not allow non-root dataset time fields to drive `timescope` or `grain` in
  the first version.
- Do not add nested derived metric support.
- Do not support effective-time or interval-validity joins in this version.

## Compatibility

This is a deliberate behavior change for base multi-dataset metrics observed
through `session.observe(...)`.

Today a scalar metric such as `@ms.metric(datasets=[orders, users], verification_mode="python_native",)` may be
materialized as independent table arguments. After this change, observe always
uses logical-wide semantics for multi-dataset base metrics with an explicit
root dataset: non-root datasets are joined from the root through relationships,
and the metric expression is evaluated against that joined row space. The same
metric has one observe semantics across all frame shapes.

Single-dataset metrics keep their current behavior and use their only dataset
as the implicit root. Existing multi-dataset metrics without `root_dataset`
must be migrated or explicitly confirmed before they can use logical-wide
observe.

Existing base multi-dataset metrics that combine independent aggregates from
multiple datasets, such as `users.count() / orders.count()`, must be audited and
migrated. Logical-wide observe is not the replacement shape for independent
aggregate composition. Those metrics should become derived metrics whose
components are independently observed on their own roots.

## Architecture

Add an analysis-layer observe planner, for example
`marivo/analysis/intents/observe_planner.py`. The planner converts semantic
objects and observe inputs into an executable logical-wide plan. It does not
execute ibis expressions, persist frames, or decide the output frame shape.

The planner is responsible for:

- identifying the root dataset
- resolving field-like refs used by dimensions and filters
- collecting datasets required by metric arguments, dimensions, and filters
- validating that base metric aggregations measure only root-dataset fields
- finding unique relationship paths from the root dataset to required datasets
- validating relationship cardinality and snapshot policy safety
- building a root-preserving left-joined table with any required snapshot
  partition predicates
- exposing dataset argument views for metric callables
- projecting dimension expressions
- preparing joined-dataset filter expressions

`observe.py` remains responsible for:

- resolving windows and frame shape
- applying root time windows and buckets
- invoking metric or derived expression evaluation
- executing ibis expressions
- constructing axes metadata
- persisting `MetricFrame` and `ComponentFrame` artifacts
- writing job records and evidence metadata

Existing derived-only helpers such as relationship path search and joined
dimension table construction move into the planner or are replaced by it.

## Root Dataset

For a base metric with more than one dataset, the semantic author must declare
`root_dataset`. The root dataset defines the metric's row grain, the join
anchor, and the only dataset whose time fields can drive `timescope`, `grain`,
and `time_field`.

Example:

```python
@ms.metric(
    datasets=[orders, users],
    root_dataset=orders,
    decomposition=ms.sum(),
verification_mode="python_native",)
def revenue_by_user_state(orders, users):
    return orders.amount.sum()
```

For a single-dataset base metric, `root_dataset` is optional and defaults to
that dataset. For a multi-dataset base metric, omitting `root_dataset` is a load
or readiness blocker for logical-wide observe. During migration, tooling may
display `datasets[0]` as the legacy inferred root candidate, but implementation
must not silently rely on list order as the final contract.

The declared root must be one of `metric_ir.datasets`. The loader/checker should
render it visibly in project descriptions and readiness output, because it is a
load-bearing semantic choice.

For logical-wide base metrics, aggregate measures belong to the root dataset.
Joined dataset fields may be used as dimensions, filters, relationship keys,
and row-level predicates that qualify root rows. They must not be aggregated as
measures inside the base metric expression, because many-to-one joins repeat the
joined-side row across root rows. For example, with `orders` as root and `users`
joined many-to-one, `orders.amount.sum()` is valid, while `users.score.sum()` or
`users.count()` would count user-side values once per order and is rejected for
base logical-wide observe.

This rule is enforced at authoring/load/readiness time with the existing metric
body AST validation pipeline, not first discovered during backend execution. The
validator maps metric function parameters to `datasets=[...]` by parameter
position, asserts the callable arity matches the dataset list, and then uses the
declared `root_dataset` to identify the root parameter. It must not match by
parameter name, because authors may choose local argument names that differ from
semantic dataset ids. For each aggregate call, it inspects the receiver chain's
dataset parameter root: aggregates whose receiver belongs to the root dataset
are valid; aggregates whose receiver belongs to a non-root dataset are invalid.
Non-root fields remain valid inside row-level predicates that qualify a root
aggregate, such as filtering `orders.amount.sum(...)` by `users.country`. They
are invalid when they are the aggregate receiver or the value being aggregated.

Independent aggregates across multiple roots must be modeled as derived metrics
over component metrics. This keeps denominator and numerator grains explicit and
prevents a joined row space from silently changing scalar business definitions.

For a derived metric, each component metric is planned independently. A
component metric must be a non-derived metric with a valid root: its explicit
`root_dataset` for multi-dataset components, or its only dataset for
single-dataset components. This design removes the existing observe limitation
that rejects derived components with more than one dataset. Each component uses
the same logical-wide planner and root-only measure rules as an ordinary base
metric.

## Dataset Snapshot Versioning

Partition snapshot semantics belong to the dataset, not to each relationship.
A snapshot dataset represents entity state at a partition grain, usually one
row per entity key per partition.

The semantic layer adds dataset-level versioning metadata, for example:

```python
@ms.dataset(
    name="user_profile_daily",
    datasource="warehouse",
    primary_key=["user_id", "dt"],
    versioning=ms.snapshot(
        partition_field=dt,
        grain="day",
        timezone="Asia/Shanghai",
        format="%Y%m%d",
    ),
)
def user_profile_daily(backend):
    return backend.table("user_profile_daily")
```

The dataset-level declaration says that `user_profile_daily` is a daily
snapshot table. It does not decide which snapshot partition a particular
analysis should use. That choice belongs to the relationship join policy,
because the same snapshot dataset can be used for both window-end state
analysis and historical fact-time analysis.

The first version supports only day-grain partition snapshot datasets. Ordinary
datasets with no `versioning` metadata are treated as non-snapshot datasets.
Effective-time datasets with `valid_from` / `valid_to` intervals are out of
scope.

Snapshot datasets may declare the timezone used to cut their partition field.
When omitted, the snapshot partition timezone defaults to the system timezone,
not the analysis session timezone. This default matches warehouse pipelines that
use a fixed business timezone for daily partitions.

Snapshot datasets may also declare partition encoding with `format=` or, for
non-standard encodings, `parser=`. This lets the planner compare and format Hive
style string or integer partitions such as `dt="20260530"` without assuming the
physical partition field is a SQL date type. The parser normalizes physical
partition values into logical partition dates for planning, and the formatter
converts resolved logical dates back into the dataset's physical partition
encoding for the equality predicate.

## Field Resolution

All observe field-like refs use one resolver:

- `dimensions=[DimensionRef(...)]`
- `where={...}` keys
- explicit `time_field=...`
- relationship key fields used while planning joins

A fully qualified semantic field id resolves directly. A short field name is
accepted only when it is unique within the current plan's actual dataset set:
the metric root, datasets required by the metric body, requested dimensions,
requested filters, and relationship keys needed to connect those datasets. The
resolver does not require a short name to be globally unique across every loaded
semantic project dataset that is irrelevant to the plan. If the short name
matches multiple fields inside the plan dataset set, observe raises an
ambiguity error that includes candidate fully qualified ids.

This keeps cross-dataset observe predictable and prevents root-first,
relationship-distance, or datasource-local heuristics from silently choosing the
wrong field.

## Relationship Cardinality And Snapshot Policy

Relationships keep describing business-key connectivity between datasets, and
gain analysis-safety metadata:

```python
ms.relationship(
    name="orders_to_user_profile_current",
    from_dataset=orders,
    to_dataset=user_profile_daily,
    from_fields=[order_user_id],
    to_fields=[user_id],
    cardinality="many_to_one",
    snapshot_policy="latest",
)
```

`cardinality` is declared in the relationship's authored direction,
`from_dataset -> to_dataset`, after all join predicates are applied, including
snapshot partition predicates. Supported values are:

- `one_to_one`
- `many_to_one`
- `one_to_many`
- `many_to_many`
- `unknown`

Cardinality safety is evaluated in the actual traversal direction used by the
observe plan. Relationship graph search is bidirectional, so the planner must
normalize each edge before checking safety:

| Declared cardinality | Traversed from -> to | Traversed to -> from |
| --- | --- | --- |
| `one_to_one` | `one_to_one` | `one_to_one` |
| `many_to_one` | `many_to_one` | `one_to_many` |
| `one_to_many` | `one_to_many` | `many_to_one` |
| `many_to_many` | `many_to_many` | `many_to_many` |
| `unknown` | `unknown` | `unknown` |

Only direction-normalized `one_to_one` and `many_to_one` are safe for automatic
observe widening. Direction-normalized `one_to_many`, `many_to_many`, and
`unknown` paths are blocked by default so a metric is not silently inflated by
fanout. For example, `orders -> users` with declared `many_to_one` is safe when
the root is `orders`; the same relationship traversed from root `users` to
target `orders` normalizes to `one_to_many` and is blocked. A later design can
introduce an explicit fanout opt-in policy, but this version does not.

For snapshot target datasets, `snapshot_policy` chooses which partition is
joined:

- `latest`: join the partition anchored by the observe window end when a
  timescope is present, for window-end state analysis that remains reproducible
  for the observed frame. The planner converts the resolved `timescope.end` into
  the target snapshot dataset's partition timezone, formats it with the snapshot
  partition field's grain and format, and chooses the latest available partition
  less than or equal to that anchor. When no timescope is present, the planner
  uses a plan-time `as_of_current_time` anchor based on the current system time,
  resolves the latest available snapshot partition less than or equal to that
  anchor, and records both the planner timestamp and datasource partition
  listing timestamp in lineage and quality metadata. The join condition is the
  relationship key equality plus `snapshot_partition = resolved_partition`.
- `as_of_root_time`: join the snapshot partition corresponding to the root row's
  day-level time field, for historical fact-time analysis. The root time is
  converted to the target snapshot dataset's partition timezone before deriving
  `root_time_date`, then the planner chooses the latest available partition less
  than or equal to that date. The join condition is the relationship key equality
  plus `snapshot_partition = resolved_partition`.

Both policies resolve partitions before the join uses them. `latest` always
produces a single constant `resolved_partition` for the whole component plan,
whether the anchor came from `timescope.end` or from plan-time
`as_of_current_time`. `as_of_root_time` produces an anchor-to-partition mapping
from each logical root date to the resolved snapshot partition, then joins with
an equality predicate against that resolved value. The physical join must not be
implemented as a raw `snapshot_partition <= anchor` range join, because that can
duplicate root rows when more than one historical snapshot partition satisfies
the predicate.

A relationship targeting a snapshot dataset must declare a snapshot policy for
the direction that enters the snapshot dataset. Snapshot partition predicates
are applied only when traversal enters a snapshot dataset. If graph search would
traverse from a snapshot dataset back to a non-snapshot fact dataset, the
snapshot policy on that relationship does not make the reverse edge safe and
does not add a partition predicate to the fact dataset. Relationships targeting
ordinary non-snapshot datasets do not need a snapshot policy. Two relationships
may connect the same datasets with different policies, such as
`orders_to_user_profile_current` and `orders_to_user_profile_asof`; path
ambiguity errors should surface those relationship ids so users and agents can
choose the intended business framing.

## Join Planning

The planner collects required datasets for each observe path.

For base metrics, required datasets are:

- every dataset listed on the metric
- each requested dimension's dataset
- each `where` field's dataset

For derived metrics, each component plan requires:

- the component metric's datasets
- each requested dimension's dataset
- each `where` field's dataset

Component metrics may have different roots. For example, a conversion rate can
use an `orders`-rooted numerator component and a `sessions`-rooted denominator
component. Each component is filtered, widened, bucketed, and grouped on its own
root row space, then component outputs are merged on the derived metric axes.

Each individual component plan must still be executable by one datasource. The
planner does not federate a single component's root and joined datasets across
datasources. Different derived components may use different datasources because
their component frames are materialized independently and merged in the
Marivo/frame layer after aggregation.

For every required dataset other than the root, the planner finds relationship
paths from root to target with shortest-path precedence. Missing paths raise a
structured error. If exactly one shortest path exists, that path is used and its
direction-normalized cardinality and snapshot semantics are validated. If
multiple equal-length shortest paths exist, observe raises a structured
ambiguity error instead of choosing implicitly. Longer paths are not considered
when a shorter path exists; this avoids silently changing business semantics in
snowflake schemas with redundant routes. Two same-length paths that connect the
same datasets but differ only by relationship id, snapshot policy, or business
role are still semantic peers and must be reported as ambiguous. The path search
is shared by base and derived observe.

The logical table is built from the root table with left joins along each
required path. Root rows are preserved when joined rows are missing. Each edge
on the path must have safe direction-normalized cardinality, and traversals
that enter snapshot target datasets add their declared `latest` or
`as_of_root_time` partition predicate. Marivo does not deduplicate or
pre-aggregate to repair unsafe fanout in this version.

All datasets inside one logical-wide plan must belong to the same datasource.
For a base metric, that means the root and every joined dataset. For a derived
component metric, that means the component root and every joined dataset for
that component. Such single-plan cross-datasource shapes raise
`CrossBackendMetricError`. A derived metric may merge component frames from
different datasources after each component has produced datasource-local
aggregates.

## Component Comparability

Derived component outputs can be merged only when their axes are semantically
comparable. For each requested dimension, each component plan must resolve the
dimension to the same semantic field id. Matching only by label, physical column
name, or formatted display name is not enough. A future design may add explicit
field equivalence metadata, but this version blocks mismatches by default with a
`component-axis-field-mismatch` error that lists the per-component resolutions.

Snapshot choices must also be comparable across components. If component plans
depend on different snapshot policies, anchors, or resolved partitions for any
snapshot dataset that contributes a requested axis, filter predicate, or
row-level population predicate, the derived metric blocks by default with
`component-snapshot-policy-mismatch`. This prevents ratios from silently
combining, for example, a numerator segmented by `user_profile` as of the
observe window end with a denominator segmented by `user_profile` as of each
root event date. A future explicit opt-in may allow such comparisons with clear
labeling, but it is not part of this version.

Lineage records per-component axis resolution and snapshot resolution: component
metric id, root dataset, datasource, dimension field id, relationship path,
snapshot policy, anchor source, anchor value or mapping, timezone, resolved
partition or mapping, planner timestamp, and partition listing timestamp when
applicable.

## Time And Filters

`timescope`, `grain`, and `time_field` are root-only in the first version.
When `time_field` resolves to a non-root dataset, observe raises a clear
unsupported-shape error.

Window filtering and bucketing use the root dataset's time field. `where`
filters are applied after the logical wide table is built, so filters can
target joined dataset fields. A filter on a joined field can remove root rows
whose joined value is null, which is normal SQL left-join behavior after a
post-join predicate. When a joined-field filter drops rows because the joined
side was missing, frame metadata includes a quality note so users can
distinguish intentional dimensional filtering from root-row loss caused by
missing relationship coverage.

Snapshot joins do not change the observe time axis. With `latest`, the planner
anchors partition selection on the resolved observe `timescope.end` when a
timescope is present; without a timescope, it uses plan-time
`as_of_current_time` and records the resolved concrete partition for
reproducibility. With `as_of_root_time`, the planner converts the root row's
time field into the target snapshot dataset's partition timezone and derives
the day-level anchor date in that timezone. Both policies first resolve either
a single partition or an anchor-to-partition mapping as
`max(snapshot_partition where snapshot_partition <= anchor)`, then use equality
against the resolved partition in the join.

The planner does not use the analysis session timezone for snapshot partition
selection unless that timezone is also the snapshot dataset's declared or
default system timezone. If a relationship requests `as_of_root_time` and the
root dataset has no resolvable day-level time field, observe raises a clear
planning error.

The planner records every resolved snapshot partition in frame metadata and
lineage params, including the relationship id, target dataset, snapshot policy,
anchor value when one exists, whether the anchor came from `timescope.end`,
`as_of_current_time`, or a root row, timezone, resolved partition, planner
timestamp, and datasource partition listing timestamp. If `latest` has no
timescope, frame metadata exposes a visible `confidence_scope` or quality note
that the snapshot was fixed at planning time rather than tied to an observe
window. If the exact anchor partition is missing and the planner falls back to
an earlier partition, the frame metadata includes a quality warning. If no
partition less than or equal to an anchored policy's anchor is available,
observe raises a planning error instead of silently joining nulls.

## Base Observe Flow

Base observe uses the planner for all shapes:

1. Resolve the metric and use its declared `root_dataset` as root.
2. Build a logical-wide plan from metric datasets, dimensions, filters, and the
   optional root time field.
3. Materialize the root table and left-join required datasets.
4. Apply the root time window and optional bucket to the logical table.
5. Apply `where` predicates to the logical table.
6. Project requested dimensions from the logical table.
7. Invoke the metric callable with dataset argument views ordered by
   `metric_ir.datasets`.
8. Execute scalar, time-series, segmented, or panel aggregation using the same
   output contracts that single-dataset observe uses today.

Dataset argument views may all be backed by the same joined table, but field
functions must still resolve according to their declared dataset. The planner
isolates this mapping so metric callables keep the existing multi-argument
signature.

Authoring, load, and readiness validation reject base metric expression shapes
that are unsafe for logical-wide use. Aggregate expressions over non-root
dataset fields are unsupported. Non-root fields remain valid in filter
predicates, dimension projection, join predicates, and row-level conditional
expressions that qualify root measures. When a metric needs to aggregate
non-root data independently, validation raises an error that points authors to
derived component metrics. Observe may repeat this validation as defense in
depth, but the normal user experience is an authoring/readiness failure before
analysis execution.

## Derived Observe Flow

Derived observe uses the same planner per component:

1. Resolve the derived metric's component metric ids.
2. For each component metric, create a component-level logical-wide plan using
   that component's root dataset. Multi-dataset component metrics are allowed
   when they declare a valid `root_dataset` and satisfy the same root-only
   measure and relationship-safety rules as base observe.
3. Apply the same root time, joined filter, and dimension projection rules used
   by base observe.
4. Aggregate each component by the parent axes.
5. Merge component outputs on those axes.
6. Evaluate the derived sentinel expression into the final metric value.
7. Persist a clean parent `MetricFrame` and, for component-aware
   decompositions, a `ComponentFrame` with the same axes.

The existing derived metric behavior for relationship-reachable dimensions must
continue to pass, but should no longer rely on derived-only join helpers.
Current derived relationship joins must migrate from derived-specific inner join
construction to the shared root-preserving left-join planner. Existing semantic
fixtures and stock relationship tests must add explicit `cardinality` and, for
snapshot targets, `snapshot_policy`; leaving those values unknown would now block
planning by design.

## Errors

Existing errors remain where their meaning is accurate, but
derived-specific names should not leak into base observe planning.

Expected error cases include:

- relationship path missing from root to a required dataset
- relationship path ambiguous from root to a required dataset
- relationship path contains unsafe cardinality or unknown cardinality
- relationship to a snapshot dataset is missing `snapshot_policy`
- `as_of_root_time` snapshot join has no root day-level time field
- snapshot partition cannot be resolved at or before the anchor
- invalid snapshot partition timezone declaration
- short field ref ambiguous across datasets
- field ref not found
- non-root `time_field` requested
- cross-datasource logical-wide plan inside one base or component plan
- multi-dataset base metric missing explicit `root_dataset`
- declared `root_dataset` is not included in `datasets`
- base logical-wide metric aggregates a non-root dataset field
- base logical-wide metric contains aggregate subexpressions over multiple
  dataset roots instead of derived component metrics
- derived component metric violates logical-wide base metric planning rules
- derived component axis field mismatch (`component-axis-field-mismatch`)
- derived component snapshot policy or resolved partition mismatch
  (`component-snapshot-policy-mismatch`)
- logical-wide metric body uses non-root fields as aggregate receivers instead
  of row-level predicates
- multiple equal-length relationship paths exist between root and target
- empty metric dataset list for a base metric

Error details include useful repair context: metric id, root dataset,
target dataset, field ref, required datasets, candidate fields, and candidate
relationship paths when available. Component comparability errors also include
component metric id, datasource, resolved dimension field ids, snapshot policy,
anchor source, and resolved partition metadata.

The existing `SegmentedMultiDatasetUnsupported` and
`WindowedTimeSeriesUnsupported` observe guards are removed or narrowed so they
do not block supported logical-wide plans.

## Documentation Updates

Update user-facing and agent-facing docs in the same implementation change:

- `docs/specs/analysis/python-analysis-operator-design.md`
- `docs/specs/semantic/python-semantic-layer.md`
- `marivo-skills/marivo-analysis/SKILL.md`
- `marivo-skills/marivo-analysis/references/cheatsheet.md`
- `marivo-skills/marivo-analysis/references/pitfalls.md`
- `marivo-skills/marivo-semantic/SKILL.md`
- relevant semantic and analysis examples

Docs must explicitly state the explicit root dataset rule, root-only measure
aggregation, left-join behavior, fanout risk and blocking behavior,
dataset-level snapshot versioning, relationship snapshot policies, root-only
time axis, snapshot partition timezone behavior, reproducible snapshot
partition lineage, fallback quality warnings, plan-context field ref resolution,
component comparability rules, and the behavior change plus migration path for
base multi-dataset observe. Snapshot policy docs must call `latest` a
window-end or plan-time current partition policy, not an unqualified
present-state policy.

## Testing Strategy

Add narrow planner tests and observe end-to-end tests.

Planner tests cover:

- single-dataset metrics defaulting root to their only dataset
- multi-dataset metrics requiring explicit root dataset
- declared root dataset not in `datasets` rejection
- non-root aggregate measure rejection
- independent aggregate composition migration error
- AST validation allowing non-root fields in root aggregate predicates
- AST validation rejecting non-root aggregate receivers
- AST validation mapping function parameters to `datasets[i]` by position with
  arity assertion, not by parameter name
- unique short field resolution inside the current plan dataset set
- ambiguous short field errors with candidate ids
- short field names that collide only with datasets outside the current plan
  resolving successfully
- missing relationship path errors
- ambiguous relationship path errors
- shortest relationship path wins over longer redundant paths
- equal-length relationship paths are ambiguous, including paths that differ by
  snapshot policy or relationship business role
- unsafe fanout cardinality rejection
- reverse traversal of declared `many_to_one` normalizing to blocked
  `one_to_many`
- reverse traversal of declared `one_to_many` normalizing to safe `many_to_one`
- unknown cardinality rejection
- left-join path construction across one hop and multiple hops
- latest snapshot partition predicate construction from resolved timescope end
- latest snapshot partition predicate construction without timescope using the
  plan-time `as_of_current_time` anchor, latest available partition, visible
  quality note, and lineage recording
- latest snapshot joins using equality against a single resolved partition
- as-of-root-time snapshot predicate construction using the latest available
  partition less than or equal to the row anchor date
- as-of-root-time snapshot joins using an anchor-to-partition mapping plus
  equality, not a raw `snapshot_partition <= anchor` range join
- as-of-root-time date derivation using the target snapshot partition timezone
  instead of the analysis session timezone
- snapshot timezone defaulting to the system timezone when omitted
- snapshot partition `format=` or `parser=` support for string or integer Hive
  partition encodings such as `20260530`
- snapshot partition fallback warning when the exact anchor partition is missing
- snapshot partition lineage records relationship id, target dataset, policy,
  anchor, timezone, resolved partition, planner timestamp, and partition listing
  timestamp
- snapshot predicate applied only when traversal enters the snapshot dataset
- snapshot relationship missing policy rejection
- cross-datasource rejection inside a single base or component plan
- non-root time field rejection
- component axis field mismatch rejection with per-component candidate details
- component snapshot policy or resolved partition mismatch rejection
- per-component axis and snapshot resolution lineage

Observe tests cover:

- base multi-dataset scalar metric evaluated on the logical-wide row space
- base multi-dataset metric using joined fields only as filters or dimensions
- base multi-dataset metric rejected when aggregating joined-side fields
- independent aggregate scalar migrated to derived component metric pattern
- base multi-dataset time-series metric with root time bucket
- base multi-dataset segmented metric using a dimension on a joined dataset
- base multi-dataset panel metric with root bucket plus joined dimension
- `where` filtering on a joined dataset field
- `where` filtering on a joined dataset field producing a quality note when
  missing joined values drop root rows
- missing joined rows preserving root rows with null dimensions
- fanout joins rejected before metric execution
- window-end snapshot join anchored by observe timescope end
- fact-time snapshot join using the latest available partition at or before the
  root row's date anchor
- full semantic ids resolving when short field names are ambiguous
- existing single-dataset observe behavior unchanged
- existing derived relationship-dimension tests continuing through the shared
  planner
- component-aware derived ratio and weighted-average frames preserving clean
  parent/component frame contracts
- derived ratio with numerator and denominator components rooted on different
  fact datasets
- derived ratio with components sourced from different datasources after each
  component plan is datasource-local
- derived ratio whose component metric is a logical-wide multi-dataset base
  metric

Run the narrow observe and planner tests first, then broaden to the relevant
analysis suite. Because this changes shared observe behavior, run `make test`
before merging if practical.

## Open Decisions Closed By This Design

- The project chooses complete logical-wide observe semantics rather than a
  dimension-only first step.
- Single-dataset metrics infer root from their only dataset.
- Multi-dataset metrics require explicit `root_dataset`; list order is not the
  final root contract.
- Joins are root-preserving left joins.
- Fanout is blocked by default unless a later explicit opt-in policy is added.
- Short field names must be unique within the current plan dataset set.
- Time axes are root-only.
- Snapshot versioning belongs to datasets, while relationships choose latest or
  fact-time snapshot policy.
- Snapshot joins resolve concrete partitions or anchor mappings before joining;
  they do not use raw partition range joins.
- Derived components may be datasource-local to different datasources, then
  merged at the frame layer when their axes and snapshot choices are comparable.
- Existing metric function signatures are preserved.
- Base multi-dataset observe behavior changes uniformly to logical-wide
  semantics across all shapes.
