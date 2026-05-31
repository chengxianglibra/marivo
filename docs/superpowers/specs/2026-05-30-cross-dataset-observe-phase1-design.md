# Cross-Dataset Observe Phase 1 Design

Date: 2026-05-30

Status: approved scope, pending written-spec review

## Problem

`session.observe(...)` currently blocks or inconsistently interprets base
metrics that use more than one dataset. The one relationship-aware path that
exists today is narrow and derived-metric-specific: component metrics can be
grouped by a relationship-reachable dimension. That does not cover the common
safe-star use case where a root fact metric needs to be sliced or filtered by
many-to-one dimensions.

Phase 1 must make the safe case useful without pretending to solve every data
warehouse shape. The first implementation should support base metrics over a
root dataset widened by direction-safe left joins, fail closed for fan-out, and
return structured repair errors when the semantic model is not precise enough.

## Goals

- Support base multi-dataset `observe` for `scalar`, `time_series`,
  `segmented`, and `panel` outputs.
- Require every base metric to declare `additivity`.
- Require `root_dataset` for multi-dataset base metrics.
- Allow single-dataset base metrics to omit `root_dataset`; it resolves to the
  only dataset.
- Preserve the public `session.observe(...)` argument surface: metric,
  `dimensions`, `where`, `timescope`, `grain`, and `time_field`.
- Let base metric dimensions and filters reference relationship-reachable
  datasets.
- Build root-preserving left joins only when relationship traversal is proven
  many-to-one or one-to-one from dataset keys.
- Support latest-only snapshot dimensions and filters.
- Classify `where` predicates as root-population or joined-dimension filters.
- Emit structured repair errors for every Phase 1 planner rejection.
- Keep existing derived observe behavior working while derived planner
  unification waits for Phase 2.

## Non-Goals

- Do not add `session.explain(...)`.
- Do not rewrite derived metric observe paths in Phase 1.
- Do not allow multi-dataset derived components in Phase 1.
- Do not support `as_of_root_time` snapshot joins in Phase 1.
- Do not support validity-interval / SCD2 joins in Phase 1.
- Do not add fan-out policies such as `symmetric_aggregate` or
  `aggregate_then_join` in Phase 1.
- Do not add named route selection or conformed-dimension equivalence.
- Do not federate a single base metric plan across multiple datasources.
- Do not infer a multi-dataset root from `datasets` order.

## Semantic Contracts

Base metrics get two new load-bearing semantic fields:

- `additivity`: required for every base metric. Values are `additive`,
  `semi_additive`, and `non_additive`.
- `root_dataset`: required for multi-dataset base metrics and optional for
  single-dataset base metrics.

Omitting `additivity` is a load or readiness blocker for every base metric,
including single-dataset metrics. Omitting `root_dataset` is allowed only when a
base metric has exactly one dataset; the resolved root is that dataset. A
multi-dataset base metric with no root, or with a root outside `datasets`, is
invalid.

The root dataset defines the preserved row set, the join anchor, and the only
dataset whose time fields may drive `timescope`, `grain`, and `time_field`.
Joined datasets may contribute dimensions, filters, relationship keys, and
row-level predicates, but aggregate receivers in the metric body must belong to
the root dataset. Independent aggregates over multiple roots must be modeled as
derived metrics in a later phase.

Datasets may declare latest snapshot versioning. Phase 1 snapshot metadata is
limited to a partition field, day grain, timezone, and physical encoding
metadata such as `format` or parser support. The planner uses this metadata to
resolve one partition before joining.

## Architecture

Add a narrow base observe planner, for example
`marivo/analysis/intents/observe_planner.py`. The planner converts a
non-derived metric plus observe inputs into a base execution plan. It does not
execute ibis expressions, persist frames, write job records, or manage component
frames.

Planner responsibilities:

- Resolve the metric root and additivity contract.
- Validate base metric readiness: root exists, additivity exists, root belongs
  to the metric datasets, and all required datasets share one datasource.
- Resolve field-like references used by dimensions, `where`, `time_field`, and
  relationship keys.
- Collect required datasets from metric datasets plus explicit or fully
  qualified dimension and filter refs.
- Find unique shortest relationship paths from root to every required non-root
  dataset.
- Derive direction-normalized join safety from dataset keys and latest snapshot
  effective keys.
- Build a widened root table with root-side filters before joins and
  joined-side filters after joins.
- Return dataset argument views ordered by `metric_ir.datasets`, projected
  dimension expressions, axes metadata inputs, lineage metadata, and warnings.
- Raise structured repair errors for unsupported or unsafe plans.

`observe.py` remains responsible for session writability checks, window
resolution, output shape prediction, metric callable invocation, ibis execution,
frame metadata, evidence commit, known datasource persistence, and job records.
Derived metrics keep their current code paths in Phase 1.

## Field Resolution

All observe field-like inputs use one resolver: `dimensions`, `where` keys,
`time_field`, and relationship key fields.

A fully qualified semantic field id resolves directly. A short field name is
accepted only when it is unique within the metric's statically known dataset
set: the root plus datasets declared on the metric. That set is fixed before
dimensions and filters are resolved. A short ref cannot introduce a
relationship-reachable dataset that is not listed on the metric; callers must
use a fully qualified field id or explicit `DimensionRef` for that.

If a short name is ambiguous inside the statically known set, planning fails
with candidate field ids. Fields on unrelated out-of-plan datasets do not make a
short name ambiguous.

## Join Discovery And Safety

The planner searches the relationship graph bidirectionally. For each required
non-root dataset, it chooses exactly one shortest path. Missing paths fail as
`path-missing`. Multiple equal-length shortest paths fail as `path-ambiguous`.
Longer paths are ignored when a shorter path exists.

Join safety is derived only from dataset keys and relationship fields. The side
whose relationship fields match its effective key is the `one` side. Traversal
from many to one is safe; traversal from one to many is blocked; both-one is
one-to-one and safe; neither-one is unknown and blocked.

For ordinary datasets, the effective key is `primary_key`. For a latest snapshot
target, the effective key is `primary_key` minus the snapshot partition field
after the planner resolves one partition. Relationship-level cardinality and
snapshot-policy overrides do not exist.

All executable Phase 1 joins are root-preserving left joins. The planner does
not deduplicate, pre-aggregate, or apply distinct to repair unsafe fan-out.

## Snapshot Semantics

Phase 1 supports latest snapshot joins only. A snapshot target is collapsed to
one partition before the relationship join:

- If `timescope.end` is present, the latest partition at or before that end is
  selected.
- If no timescope is present, the latest partition at or before planning time is
  selected.

The resolved physical partition value is recorded in frame metadata and lineage
as the replay pin. Listing timestamp may explain when the partition was found,
but reproducibility depends on the resolved partition value itself.

If no partition exists at or before the anchor, planning fails with a structured
snapshot error. If the exact anchor partition is missing and the planner falls
back to an earlier partition, frame metadata records a quality warning. Snapshot
version selection uses the dataset's declared timezone, or the system timezone
when the dataset omits one; it does not use the analysis session timezone unless
that is also the dataset timezone.

`as_of_root_time` and per-root-row anchor-to-partition mappings are explicitly
out of scope for Phase 1.

## Time And Filters

`timescope`, `grain`, and `time_field` are root-only. A non-root `time_field`
fails planning with a structured unsupported-shape error.

Window filtering and bucketing use the root dataset's time field. `where`
predicates are classified by target dataset:

- Root-population predicates target only the root dataset. They are applied to
  the root before widening, alongside the time window.
- Joined-dimension predicates target a widened dataset. They are applied after
  the left join. This intentionally behaves like SQL left-join-then-filter and
  may remove root rows whose joined side is missing.

The public `where` surface stays single. The planner owns predicate phase
classification, records the phase in metadata, and emits a quality note when a
joined filter can drop rows due to missing relationship coverage.

## Base Observe Flow

1. `observe.py` resolves the metric and routes non-derived metrics to the base
   planner. Derived metrics keep the existing path.
2. The planner resolves root, additivity, dimensions, filters, required
   datasets, relationship paths, join safety, latest snapshot partitions, and
   predicate phases.
3. The planner materializes the root table and applies the root time window,
   optional bucket preparation, and root-population predicates before widening.
4. The planner left-joins required non-root datasets along validated safe paths,
   applying latest snapshot predicates when traversal enters a snapshot dataset.
5. The planner applies joined-dimension predicates after widening.
6. The planner projects requested dimensions and returns dataset argument views
   ordered by `metric_ir.datasets`.
7. `observe.py` invokes the metric callable with those views and performs the
   existing scalar, time-series, segmented, or panel aggregation contract.
8. Frame metadata and lineage store root dataset, additivity, relationship paths,
   join safety, predicate phases, latest snapshot anchors and partitions, and
   quality warnings.

Dataset argument views may share one widened table internally, but field
functions still resolve according to their declared dataset so existing metric
function signatures remain intact.

## Error Behavior

Phase 1 planner failures use a structured repair payload:

- `schema_version`: error payload contract version.
- `code`: stable enum used by agents.
- `message`: human-readable explanation.
- `candidates`: concrete options such as candidate roots, paths, fields, or
  relationships.
- `repair`: structured suggested edits with safety class.

Repair safety classes are:

- `auto_safe`: mechanical fixes that do not change business semantics.
- `modeling_decision`: choices that define or change metric meaning.
- `unsafe_without_approval`: fixes that may make execution possible but can
  change grain or historical meaning.

Phase 1 error codes should cover:

- `missing-additivity`
- `missing-root`
- `invalid-root`
- `empty-base-datasets`
- `root-only-measure-violation`
- `field-ref-not-found`
- `field-ref-ambiguous`
- `non-root-time-field`
- `path-missing`
- `path-ambiguous`
- `unsafe-fanout`
- `unknown-join-safety`
- `cross-datasource-plan`
- `snapshot-metadata-invalid`
- `snapshot-partition-missing`
- `unsupported-as-of-root-time`
- `derived-shared-planner-unsupported`

Unsafe fan-out repairs should name safe roots, missing key evidence, or remodel
options. They must not recommend relationship-level cardinality overrides or
Phase 3 fan-out policies.

## Testing

Semantic and authoring tests cover:

- `additivity` is required for every base metric.
- Single-dataset base metrics default root to their only dataset.
- Multi-dataset base metrics require explicit `root_dataset`.
- Declared root outside `datasets` is rejected.
- Root-only measure validation maps function parameters to datasets by
  position, not by parameter name.
- Latest snapshot metadata validation rejects unusable partition metadata.

Planner tests cover:

- Plan-scoped short field resolution and fully qualified cross-dataset refs.
- Missing and ambiguous relationship paths.
- Direction-normalized many-to-one, one-to-one, one-to-many, and unknown safety.
- Latest snapshot partition resolution from `timescope.end` and planning time.
- Root-population versus joined-dimension `where` classification.
- Cross-datasource base plan rejection.
- Structured error payload shape, stable codes, candidates, repair actions, and
  repair safety classes.

Observe tests cover:

- Base multi-dataset scalar, time-series, segmented, and panel observations on
  widened row space.
- Cross-dataset dimensions.
- Cross-dataset `where` filters.
- Root left-join null preservation when no joined filter is applied.
- Joined-filter row loss behavior and metadata warning.
- Fan-out blocked before metric execution.
- Latest snapshot joins anchored by `timescope.end` and by planning time.
- Existing derived observe behavior remains unchanged in Phase 1.

Documentation and example tests should update semantic specs, analysis specs,
Marivo semantic/analysis skills, examples, and fixtures for required
`additivity`, root semantics, key-derived join safety, latest snapshot behavior,
predicate phases, and structured repair errors.

## Rollout Notes

This is a breaking semantic contract for base metrics. Existing base metric
fixtures and examples need explicit `additivity`. Multi-dataset base metric
examples need explicit `root_dataset`. Single-dataset examples may omit
`root_dataset`, but docs should explain the default so the behavior is visible.

The old guards that reject supported base multi-dataset segmented or
time-series observe shapes should be removed or narrowed. Derived-specific
guards remain until Phase 2 replaces the derived path with the shared planner.

## Later Phases

Phase 2 should add validity/SCD2 execution, `as_of_root_time` snapshot mapping,
multi-dataset derived components, component axis comparability, and component
version comparability.

Phase 3 should add typed fan-out policies and first-class funnel/conversion
metrics.

Future designs may add named-route selection, conformed-dimension equivalence,
partition catalog caching policy, aggregate navigation, rollup routing, broad
planner discovery APIs, and more explicit measure-level filter surfaces.
