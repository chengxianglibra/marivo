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
- Let metric expressions, `dimensions=`, and `where=` reference fields on
  relationship-reachable datasets.
- Interpret `@ms.metric(datasets=[...])` order in observe: the first dataset is
  the root dataset and grain anchor.
- Use root-preserving left joins when widening to related datasets.
- Keep short field refs strict: a short ref must resolve uniquely, otherwise
  callers must use a fully qualified semantic id.
- Keep observe time axes rooted on the metric's first dataset.
- Migrate the existing derived relationship join implementation to the shared
  planner so base and derived observe use one field-resolution and join model.
- Preserve existing public entry points: `session.observe(...)`,
  `DimensionRef(...)`, `MetricRef(...)`, and semantic metric decorators.

## Non-Goals

- Do not add a new semantic authoring API or an explicit metric `root_dataset`
  parameter.
- Do not add cross-datasource federation.
- Do not infer or enforce relationship cardinality.
- Do not protect users from fanout amplification with automatic distinct,
  deduplication, bridge handling, or pre-aggregation.
- Do not allow non-root dataset time fields to drive `timescope` or `grain` in
  the first version.
- Do not add nested derived metric support.

## Compatibility

This is a deliberate behavior change for base multi-dataset metrics observed
through `session.observe(...)`.

Today a scalar metric such as `@ms.metric(datasets=[orders, users])` may be
materialized as independent table arguments. After this change, observe always
uses logical-wide semantics: `orders` is the root dataset, `users` is joined
from `orders` through relationships, and the metric expression is evaluated
against that joined row space. The same metric has one observe semantics across
all frame shapes.

Single-dataset metrics keep their current behavior.

## Architecture

Add an analysis-layer observe planner, for example
`marivo/analysis/intents/observe_planner.py`. The planner converts semantic
objects and observe inputs into an executable logical-wide plan. It does not
execute ibis expressions, persist frames, or decide the output frame shape.

The planner is responsible for:

- identifying the root dataset
- resolving field-like refs used by dimensions and filters
- collecting datasets required by metric arguments, dimensions, and filters
- finding unique relationship paths from the root dataset to required datasets
- building a root-preserving left-joined table
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

For a base metric, `metric_ir.datasets[0]` is the observe root dataset. The root
dataset defines the metric's row grain and the only dataset whose time fields
can drive `timescope`, `grain`, and `time_field`.

For a derived metric, each component metric is planned independently. A
component metric must be a non-derived metric whose first dataset is the
component root. Existing component restrictions remain unless a later design
relaxes them.

## Field Resolution

All observe field-like refs use one resolver:

- `dimensions=[DimensionRef(...)]`
- `where={...}` keys
- explicit `time_field=...`
- relationship key fields used while planning joins

A fully qualified semantic field id resolves directly. A short field name is
accepted only when it is unique across the loaded semantic project. If the short
name matches multiple fields, observe raises an ambiguity error that includes
candidate fully qualified ids.

This keeps cross-dataset observe predictable and prevents root-first or
relationship-distance heuristics from silently choosing the wrong field.

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

For every required dataset other than the root, the planner finds a unique
relationship path from root to target. Missing paths raise a structured error.
Multiple valid paths raise a structured ambiguity error. The path search is
shared by base and derived observe.

The logical table is built from the root table with left joins along each
required path. Root rows are preserved when joined rows are missing. Fanout is
allowed and affects aggregate results naturally; Marivo does not deduplicate or
pre-aggregate in this version.

All joined datasets must belong to the same datasource. Cross-datasource plans
raise `CrossBackendMetricError`.

## Time And Filters

`timescope`, `grain`, and `time_field` are root-only in the first version.
When `time_field` resolves to a non-root dataset, observe raises a clear
unsupported-shape error.

Window filtering and bucketing use the root dataset's time field. `where`
filters are applied after the logical wide table is built, so filters can
target joined dataset fields. A filter on a joined field can remove root rows
whose joined value is null, which is normal SQL left-join behavior after a
post-join predicate.

## Base Observe Flow

Base observe uses the planner for all shapes:

1. Resolve the metric and choose `metric_ir.datasets[0]` as root.
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

## Derived Observe Flow

Derived observe uses the same planner per component:

1. Resolve the derived metric's component metric ids.
2. For each component metric, create a component-level logical-wide plan using
   that component's root dataset.
3. Apply the same root time, joined filter, and dimension projection rules used
   by base observe.
4. Aggregate each component by the parent axes.
5. Merge component outputs on those axes.
6. Evaluate the derived sentinel expression into the final metric value.
7. Persist a clean parent `MetricFrame` and, for component-aware
   decompositions, a `ComponentFrame` with the same axes.

The existing derived metric behavior for relationship-reachable dimensions must
continue to pass, but should no longer rely on derived-only join helpers.

## Errors

Existing errors remain where their meaning is accurate, but
derived-specific names should not leak into base observe planning.

Expected error cases include:

- relationship path missing from root to a required dataset
- relationship path ambiguous from root to a required dataset
- short field ref ambiguous across datasets
- field ref not found
- non-root `time_field` requested
- cross-datasource logical-wide plan
- empty metric dataset list for a base metric

Error details include useful repair context: metric id, root dataset,
target dataset, field ref, required datasets, candidate fields, and candidate
relationship paths when available.

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

Docs must explicitly state the root dataset rule, left-join behavior, fanout
risk, root-only time axis, strict field ref resolution, and the behavior change
for base multi-dataset observe.

## Testing Strategy

Add narrow planner tests and observe end-to-end tests.

Planner tests cover:

- root dataset selection from the first metric dataset
- unique short field resolution
- ambiguous short field errors with candidate ids
- missing relationship path errors
- ambiguous relationship path errors
- left-join path construction across one hop and multiple hops
- cross-datasource rejection
- non-root time field rejection

Observe tests cover:

- base multi-dataset scalar metric evaluated on the logical-wide row space
- base multi-dataset time-series metric with root time bucket
- base multi-dataset segmented metric using a dimension on a joined dataset
- base multi-dataset panel metric with root bucket plus joined dimension
- `where` filtering on a joined dataset field
- missing joined rows preserving root rows with null dimensions
- fanout amplification as an explicit contract
- full semantic ids resolving when short field names are ambiguous
- existing single-dataset observe behavior unchanged
- existing derived relationship-dimension tests continuing through the shared
  planner
- component-aware derived ratio and weighted-average frames preserving clean
  parent/component frame contracts

Run the narrow observe and planner tests first, then broaden to the relevant
analysis suite. Because this changes shared observe behavior, run `make test`
before merging if practical.

## Open Decisions Closed By This Design

- The project chooses complete logical-wide observe semantics rather than a
  dimension-only first step.
- The metric's first dataset is the root; no explicit root API is added.
- Joins are root-preserving left joins.
- Fanout is allowed and documented instead of prevented.
- Short field names must be unique.
- Time axes are root-only.
- Existing metric function signatures are preserved.
- Base multi-dataset observe behavior changes uniformly to logical-wide
  semantics across all shapes.
