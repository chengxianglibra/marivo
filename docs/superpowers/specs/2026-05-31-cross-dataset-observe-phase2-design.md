# Cross-Dataset Observe Phase 2 Design

Date: 2026-05-31

Status: approved scope, pending written-spec review

Parent spec: [`docs/specs/semantic/2026-05-30-cross-dataset-observe-logical-wide-design.md`](../../specs/semantic/2026-05-30-cross-dataset-observe-logical-wide-design.md) — this document fills in the Phase 2 row of the phased delivery table.

Phase 1 spec: [`2026-05-30-cross-dataset-observe-phase1-design.md`](2026-05-30-cross-dataset-observe-phase1-design.md).

## Problem

Phase 1 shipped base multi-dataset observe through a shared `plan_base_observe`
planner with safe-star left joins, latest snapshot dimensions, plan-scoped
field resolution, and structured repair errors. Two correctness gaps remain
before agents can reliably express real warehouse analyses:

- Derived metrics still go through a separate inner-join code path
  (`_observe_derived_grouped`, `_observe_derived_segmented`,
  `_join_related_dimension_table`) that rejects multi-dataset component metrics
  and silently drops root rows whose joined-side dimension is missing.
- Versioned datasets only support `latest` snapshot pinning. Historical fact
  analyses against day-grain SCD2 (zipper) tables and per-root `as_of_root_time`
  joins are not expressible.

Phase 2 closes both gaps by reusing the Phase 1 planner — derived components
are simply additional `BaseObservePlan` instances merged at the frame layer —
and by extending dataset versioning to validity intervals plus `as_of_root_time`
mode for both snapshot and validity targets.

## Goals

- Replace the derived observe path with a shared planner. Every component
  metric (single-dataset or multi-dataset) is planned by `plan_base_observe`
  with the same root-only-measure, key-derived join safety, and root-preserving
  left-join rules as base observe.
- Allow component metrics to declare more than one dataset, with the same
  `root_dataset` / `additivity` / root-only contract enforced at load time.
- Allow derived components to span datasources at the component boundary:
  each component plan must use one datasource, but different components may
  use different datasources and merge at the frame layer.
- Add validity (SCD2) versioning to the dataset semantic surface, supporting
  the `valid_from` / `valid_to` + `interval` + `open_end` dialect.
- Add `as_of_root_time` derived version mode for both snapshot and validity
  targets, executed against single-row-per-(key, anchor) rather than range
  joins.
- Auto-select derived version mode from dataset versioning + root time context,
  with no relationship-level override and no metric-level kwarg.
- Enforce component comparability with two fail-closed checks:
  `component-axis-field-mismatch` (same dimension must resolve to the same
  semantic field id across components) and `component-version-mismatch`
  (versioned datasets used by multiple components must share derived version
  mode + anchor + resolved partition or interval predicate).
- Introduce `plan_observe(metric_ir, …)` as the single planner entry, returning
  `BaseObservePlan` for non-derived metrics and `DerivedObservePlan` for
  derived metrics. `observe.py` becomes a thin executor on top of the plan.
- Use the existing `observe-error/v1` schema for every Phase 2 planner
  rejection, with stable `code`, `candidates`, and `repair` payloads.

## Non-Goals

- No `session.explain(...)`. Phase 2 wires every plan field needed for it but
  does not add the public read-only entry. Deferred to its own phase.
- No nested derived metrics (a derived metric used as another derived metric's
  component). Continues to raise.
- No `current_flag` validity dialect. Phase 2 supports `open_end` only.
- No fan-out policies (`symmetric_aggregate`, `aggregate_then_join`). Unsafe
  traversals continue to fail closed with `unsafe-fanout` /
  `unknown-join-safety`.
- No funnel / conversion metrics, no named-route selection, no
  conformed-dimension equivalence, no partition-catalog provider abstraction.
- No changes to public `MetricFrame` / `ComponentFrame` schemas. Phase 2
  outputs match Phase 1 contract.
- No metric-level `version_mode` kwarg. Mode is derived from dataset
  versioning + root time context only.
- No relationship-level cardinality / snapshot-policy override.

## Architecture

Phase 2 is additive at the planner module boundary and substitutive inside
`observe.py`. The base planner contract from Phase 1 is preserved; the derived
path is rewritten on top of it.

### Module surface

```
marivo/analysis/intents/
  observe_planner.py   # extend: BaseObservePlan, add DerivedObservePlan, ComponentPlan, plan_observe()
  observe_errors.py    # extend: new codes + repair actions, same observe-error/v1 schema
  observe.py           # large reduction: plan_observe(...) + _execute_base / _execute_derived
```

### Plan dataclasses

```python
@dataclass(frozen=True)
class ComponentPlan:
    component_metric_ir: Any
    role: str                       # name in derived sentinel ("numerator", "denominator", ...)
    base_plan: BaseObservePlan      # planned independently for each component


@dataclass(frozen=True)
class DerivedObservePlan:
    metric_ir: Any
    sentinel_tree: Any              # parsed derived expression tree
    component_plans: list[ComponentPlan]
    parent_axes: dict[str, Any]     # axes_metadata after comparability merge
    lineage_metadata: dict[str, Any]
    warnings: list[dict[str, Any]]


ObservePlan = BaseObservePlan | DerivedObservePlan
```

`BaseObservePlan.lineage_metadata` is extended with a normative key:

```python
lineage_metadata = {
    "root_dataset": str,
    "additivity": str,
    "relationships": list[edge_meta],
    "snapshots": list[snapshot_meta],         # Phase 1 latest, kept for backward compat
    "version_resolutions": list[version_meta], # Phase 2: per versioned target
}
```

`version_meta` carries:

```python
{
    "dataset": str,
    "kind": Literal["snapshot", "validity"],
    "mode": Literal["latest", "as_of_root_time"],
    "anchor_source": Literal["timescope_end", "as_of_current_time", "root"],
    "anchor_value": str | None,                # ISO date for plan-time anchor; null for per-root
    "resolved_partition": Any | None,          # snapshot latest only
    "resolved_partition_summary": dict | None, # snapshot as_of_root_time
    "anchor_to_partition_mapping_digest": str | None,
    "resolved_interval_predicate": str | None, # validity rendering
    "timezone": str,
}
```

Phase 1 readers that look up `lineage_metadata["snapshots"]` keep working: that
key continues to be populated for `snapshot.latest` joins. New consumers should
read `version_resolutions`, which is the canonical surface going forward.

### plan_observe entry point

```python
def plan_observe(
    *, project, session, metric_ir,
    dataset_irs, dataset_fns,
    dimensions, where, resolved_window, time_field,
) -> ObservePlan:
    if not metric_ir.is_derived:
        return plan_base_observe(...)
    return _plan_derived_observe(...)
```

`_plan_derived_observe` does three things:

1. For each component metric in
   `metric_ir.decomposition.components.items()`, build a `BaseObservePlan` by
   calling `plan_base_observe` with the component's own datasets plus the
   parent dimensions and `where` keys reachable from that component's
   `root_dataset`. Each component plan must satisfy the single-datasource rule
   independently. Components across datasources are allowed.
2. Run comparability checks across the resulting `component_plans`:
   `_check_axis_comparability`, `_check_version_comparability`. Failures raise
   `ObservePlanningError` with the appropriate code (next section).
3. Merge component `axes_metadata` into `parent_axes` (axes are identical
   across components after comparability succeeds, so the merge is a
   pick-first), collect each component's `lineage_metadata` under
   `lineage_metadata["components"]`, and assemble the `DerivedObservePlan`.

A nested derived metric (a component whose own `is_derived` is true) raises
`nested-derived-unsupported` before any base planning runs.

### observe.py executor

`observe.py` shrinks to a dispatcher:

```python
plan = plan_observe(...)
if isinstance(plan, BaseObservePlan):
    return _execute_base(plan, metric_ir, ...)
return _execute_derived(plan, metric_ir, ...)
```

`_execute_base` is the Phase 1 execution path lifted into a helper.

`_execute_derived` does:

- For each `ComponentPlan`: run the metric callable on
  `component_plan.base_plan.table` with dataset argument views from
  `base_plan.dataset_tables`, group by parent axes, aggregate, execute against
  the component's datasource backend, return a pandas frame.
- Outer-merge component frames on `merge_keys = ["bucket_start"?,
  *parent_dimension_names]` (mirroring Phase 1 derived merge behavior).
- Evaluate the derived sentinel expression on the merged frame with
  `_evaluate_sentinel_on_frame`.
- If the decomposition is component-aware, build a `ComponentFrame` with the
  same schema Phase 1 produces.

The legacy helpers `_observe_derived_grouped`, `_observe_derived_segmented`,
`_observe_derived_dimensional_*`, and `_join_related_dimension_table` are
deleted. `MetricShapeUnsupportedError` subclasses driven by those paths
(`DerivedComponentMultiDatasetUnsupported`,
`SegmentedMultiDatasetUnsupported`) are removed; their error sites move to
`observe-error/v1` codes.

### Required-dataset collection

Phase 1 inlined dataset adapter construction in `observe.py`. Phase 2 extracts
this so every component plan can reuse it:

```python
def collect_required_datasets(
    project, metric_ir, dimensions, where, time_field,
) -> set[str]:
    """Datasets directly named on the metric plus any reachable from
    explicit/qualified dimension and where refs."""
```

Both `plan_base_observe` and `_plan_derived_observe` call this once per plan
node. The resulting set is the input to relationship-path search and
single-datasource validation.

## Versioning Model

### IR

`marivo/semantic/ir.py`:

```python
class DatasetVersioningKind(StrEnum):
    SNAPSHOT = "snapshot"
    VALIDITY = "validity"


@dataclass(frozen=True)
class SnapshotVersioningIR:                        # unchanged from Phase 1
    kind: Literal["snapshot"]
    partition_field: str
    grain: Literal["day"]
    timezone: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class ValidityVersioningIR:
    kind: Literal["validity"]
    valid_from: str                                # field semantic id
    valid_to: str                                  # field semantic id
    interval: Literal["closed_open", "closed_closed"]
    open_end: tuple[Any, ...]                      # values that mean "still current"
    timezone: str | None = None


DatasetVersioningIR = SnapshotVersioningIR | ValidityVersioningIR
```

`DatasetIR.versioning` becomes `DatasetVersioningIR | None`. Existing
discriminator usage (`getattr(versioning, "kind") == "snapshot"`) keeps
working; new code should pattern-match on the dataclass.

### Authoring

```python
@ms.dataset(
    name="user_history",
    datasource="warehouse",
    primary_key=["user_id", "valid_from"],
    versioning=ms.validity(
        valid_from=valid_from,
        valid_to=valid_to,
        interval="closed_open",
        open_end=(None, "9999-12-31"),
        timezone="Asia/Shanghai",
    ),
)
def user_history(backend):
    return backend.table("user_history")
```

Authoring helper validates:

- `valid_from` / `valid_to` resolve to declared fields on the dataset.
- `interval` is one of `"closed_open"` / `"closed_closed"`.
- `open_end` is a non-empty tuple. Empty tuple is rejected with
  `validity-metadata-invalid` because Phase 2 needs at least one current-row
  predicate.
- `timezone` is a valid IANA name when provided.
- `valid_from` field name is part of `primary_key` (so the effective key
  computation can subtract it).

`ms.validity(...)` is exported from `marivo.semantic` alongside `ms.snapshot()`.

### Effective key

`_effective_key` in `observe_planner.py` extends to validity:

```python
def _effective_key(project, dataset_id) -> tuple[str, ...]:
    dataset = project.get_dataset(dataset_id)
    versioning = getattr(dataset, "versioning", None)
    if isinstance(versioning, SnapshotVersioningIR):
        return tuple(k for k in dataset.primary_key
                     if k != _local_name(versioning.partition_field))
    if isinstance(versioning, ValidityVersioningIR):
        return tuple(k for k in dataset.primary_key
                     if k != _local_name(versioning.valid_from))
    return tuple(dataset.primary_key)
```

This makes a relationship from `orders.user_id` to `user_history` resolve as
many-to-one once the planner collapses `user_history` to a single row per
`(user_id, anchor)`.

## Derived Version Mode Selection

`_derive_version_mode(root_meta, target_versioning, resolved_window)` returns a
tuple `(mode, anchor_source, anchor_value)`:

```
if root has any time_field with data_type in {"date", "timestamp"}:
    mode = "as_of_root_time"
    anchor_source = "root"
    anchor_value = None        # per-row; recorded in lineage with summary
else:
    mode = "latest"
    if resolved_window and resolved_window.end is not None:
        anchor_source = "timescope_end"
        anchor_value = resolved_window.end (as date in target tz)
    else:
        anchor_source = "as_of_current_time"
        anchor_value = datetime.now(target_tz).date()
```

A "day-level time field" is any `TimeFieldIR` declared on the root dataset
whose `data_type` is `"date"` or `"timestamp"`. This is intentionally broader
than `granularity == "day"` — a timestamp time field with a finer grain still
qualifies, since the planner casts to date in the target timezone before
joining.

When the root has multiple qualifying time fields, the planner picks the same
field that drives `timescope` and bucketing: explicit `time_field=` if
provided, otherwise the dataset's default time field. This keeps the join
anchor consistent with the visible time axis.

The selection is closed: there is no relationship-level override, no
metric-level kwarg, and no observe-time argument that toggles it. This matches
the parent spec's "deliberate closed decision" stance.

## Snapshot `as_of_root_time` Execution

Snapshot `as_of_root_time` uses Python-precomputed mapping rather than a raw
range join, so a single root row never matches multiple snapshot rows.

Steps in `plan_base_observe`, executed when joining a versioned snapshot
target with mode `as_of_root_time`:

1. From the root table (already filtered by timescope and root-population
   predicates), select the root time field, cast to date in the snapshot
   target's timezone, distinct, and execute. One backend round-trip yields
   `anchor_dates: list[date]`.
2. From `dataset_fns[snapshot_dataset](backend)`, select distinct
   `partition_field`, execute, and parse each physical value back to a logical
   `date` using `versioning.format` (or native cast when `format` is None).
   Second round-trip yields `available_partitions: list[date]`.
3. In Python, compute
   `mapping[a] = max(p for p in available_partitions if p <= a)` for each `a`
   in `anchor_dates`. Anchors with no eligible partition collect into
   `missing_anchors`. If non-empty, raise `snapshot-partition-missing` with
   `missing_anchors` and `min_available_partition` in `candidates`.
4. Re-encode each resolved partition back to its physical value via
   `versioning.format` and build an in-memory ibis table
   (`ibis.memtable([{"anchor": ..., "partition": ...}, ...])`) with two
   columns.
5. Inject the mapping into the join: first equi-join root with the mapping on
   `cast(root_time as date) == mapping.anchor`, then equi-join the snapshot
   target on `snapshot.partition_field == mapping.partition` AND the relation
   keys.
6. Record in `version_resolutions`:
   ```python
   {
       "dataset": snapshot_dataset,
       "kind": "snapshot",
       "mode": "as_of_root_time",
       "anchor_source": "root",
       "anchor_value": None,
       "resolved_partition": None,
       "resolved_partition_summary": {
           "anchor_count": len(anchor_dates),
           "min_anchor": str(min(anchor_dates)),
           "max_anchor": str(max(anchor_dates)),
           "partition_count": len(set(mapping.values())),
       },
       "anchor_to_partition_mapping_digest": sha256_of_sorted_pairs(mapping),
       "timezone": versioning.timezone or system_tz,
   }
   ```
   The digest is the future replay pin: a re-run with the same anchors against
   the same available partitions will produce an identical digest.

Snapshot `latest` continues to follow the Phase 1 path. Its
`version_resolutions` entry uses
`mode="latest"`, `resolved_partition=<physical value>`, and
`anchor_source` from `_derive_version_mode`.

> **Implementation cost**: this introduces two backend round-trips per
> snapshot `as_of_root_time` join. The Phase 2 spec accepts that cost; a
> future provider-cached partition catalog would replace the discovery
> round-trip without changing this contract.

## Validity Execution

Both `latest` and `as_of_root_time` are inline ibis predicates against the
versioned target — no Python precomputation needed.

Define a helper:

```python
def _validity_open_end_predicate(table, versioning) -> ibis.BoolColumn:
    """`valid_to` matches any open_end sentinel."""
    parts = []
    for sentinel in versioning.open_end:
        if sentinel is None:
            parts.append(table[_local_name(versioning.valid_to)].isnull())
        else:
            parts.append(table[_local_name(versioning.valid_to)] == sentinel)
    return reduce(operator.or_, parts)
```

### Validity `latest`

The current-row predicate is "row is open_end":

```python
target = target.filter(_validity_open_end_predicate(target, versioning))
```

Then the join is the ordinary safe-star equi-join on the relationship keys.
This collapses the validity table to one row per effective key before the
join.

### Validity `as_of_root_time`

Per-root-row interval check, with `open_end` widening to `+inf`:

```python
anchor_date = root[root_time_field_local].cast("date")
valid_from = target[_local_name(versioning.valid_from)]
valid_to_raw = target[_local_name(versioning.valid_to)]
open_end = _validity_open_end_predicate(target, versioning)

if versioning.interval == "closed_open":
    upper = open_end | (valid_to_raw > anchor_date)
else:  # closed_closed
    upper = open_end | (valid_to_raw >= anchor_date)
lower = valid_from <= anchor_date

predicate = (
    relationship_keys_equal(root, target)
    & lower
    & upper
)
joined = root.left_join(target, predicate)
```

Each root row matches at most one validity row when the data is well-formed
(non-overlapping intervals per key). Overlap is **not** validated; if data
violates the invariant the SQL still returns multiple rows and downstream
aggregation will silently fan out. Phase 2 records a single lineage warning
`validity_overlap_unverified` for every validity-as-of join, and leaves
detection to a future data-quality phase.

`version_resolutions` entry:

```python
{
    "dataset": validity_dataset,
    "kind": "validity",
    "mode": "as_of_root_time" | "latest",
    "anchor_source": "root" | "timescope_end" | "as_of_current_time",
    "anchor_value": str | None,
    "resolved_interval_predicate": str,    # rendered ibis expr or human label
    "timezone": versioning.timezone or system_tz,
}
```

## Component Comparability

Both checks are fail-closed by default. Both run after every component plan is
constructed.

### Axis comparability

For each parent dimension `dim`, look up how each `component_plan` resolved a
field with that name:

```python
def _check_axis_comparability(component_plans, parent_dimensions):
    for dim in parent_dimensions:
        resolutions = [
            (cp.component_metric_ir.semantic_id, d.field.semantic_id)
            for cp in component_plans
            for d in cp.base_plan.dimensions
            if d.column == dim.name
        ]
        ids = {field_id for _, field_id in resolutions}
        if len(ids) > 1:
            raise component-axis-field-mismatch with candidates listing each
            (component_metric_id, resolved_field_id)
```

Mismatch means two components claimed to slice by the same dimension name but
resolved to different semantic field ids — for example
`sales.country` versus `marketing.country`. This blocks the silent
ratio-with-divergent-axes class of bugs the parent spec calls out.

### Version comparability

For each versioned dataset accessed by more than one component:

```python
def _check_version_comparability(component_plans):
    by_dataset = collect_version_resolutions_grouped_by_dataset(component_plans)
    for dataset_id, resolutions in by_dataset.items():
        keys = {
            (r["mode"], r["anchor_source"], r.get("anchor_value"),
             r.get("resolved_partition") or r.get("resolved_interval_predicate"))
            for r in resolutions
        }
        if len(keys) > 1:
            raise component-version-mismatch with candidates listing each
            (component_metric_id, mode, anchor_source, anchor_value, resolved_*)
```

This protects the case where, for example, a numerator uses
`as_of_root_time` against `user_profile_daily` (per-row historical state) and
a denominator uses `latest` against the same table — the resulting ratio
would silently mix two semantic shapes.

## Cross-Datasource Components

Each `BaseObservePlan` enforces single-datasource scope (Phase 1
`cross-datasource-plan`). Phase 2 lifts this restriction at the derived
boundary only:

- `_plan_derived_observe` does **not** require all components to share a
  datasource.
- Each component is executed against its own datasource backend independently,
  produces a pandas frame, and joins at the frame layer.
- `lineage_metadata["component_datasources"]` is a list of `(component_id,
  datasource_name)` pairs to make the cross-datasource shape visible to
  agents.

A component that internally needs to span datasources still raises
`cross-datasource-plan` — the Phase 1 invariant is unchanged inside one
component.

## Errors

All Phase 2 planner rejections use the existing `observe-error/v1` schema with
`schema_version`, `code`, `message`, `candidates`, and `repair`.

### New codes

| Code | When | Repair safety |
| --- | --- | --- |
| `validity-metadata-invalid` | `ms.validity()` author-time validation fails | `auto_safe` |
| `snapshot-partition-missing` | At least one root anchor has no `p ≤ anchor` partition available | `unsafe_without_approval` |
| `component-axis-field-mismatch` | Same parent dimension resolves to different semantic field ids across components | `modeling_decision` |
| `component-version-mismatch` | Same versioned dataset has different mode / anchor / resolved partition across components | `modeling_decision` |
| `nested-derived-unsupported` | Derived component's own `is_derived` is true | `modeling_decision` |

`candidates` payload conventions:

- `component-axis-field-mismatch`:
  `{"dimension": dim_name, "components": [{"metric": id, "resolved_field_id": id}, ...]}`.
- `component-version-mismatch`:
  `{"versioned_dataset": ds_id, "components": [{"metric": id, "mode": ..., "anchor_source": ..., "anchor_value": ..., "resolved_partition_or_predicate": ...}, ...]}`.
- `snapshot-partition-missing`:
  `{"dataset": ds_id, "missing_anchors": [...], "min_available_partition": ..., "max_available_partition": ...}`.
- `validity-metadata-invalid`:
  `{"dataset": ds_id, "field": "valid_from"|"valid_to"|"interval"|"open_end", "reason": "..."}`.

### Removed / replaced errors

- `DerivedComponentMultiDatasetUnsupported` — removed. Multi-dataset components
  are supported.
- `SegmentedMultiDatasetUnsupported` — removed if still referenced.
- `NestedDerivedTimeAwareUnsupported` / `NestedDerivedDimensionsUnsupported` —
  consolidated under `nested-derived-unsupported`.
- `CrossBackendMetricError` — kept as Python exception, but raises with
  `observe-error/v1` payload populated. Inside a single component plan it
  remains the rejection; across components it never fires.

## Migration / Breaking Changes

This is a continuation of the parent spec's breaking-change posture. No
compatibility shim is added.

- External code that catches `DerivedComponentMultiDatasetUnsupported` or
  `MetricShapeUnsupportedError` for derived dimensional cases must catch
  `ObservePlanningError` and branch on `details["code"]` instead.
- Phase 1 derived path used inner joins. Phase 2 uses root-preserving left
  joins, so frames may include rows where the joined dimension is null. This
  is an intentional semantic improvement; tests that assumed inner-join row
  loss must be updated.
- `DatasetIR.versioning` type widens to a union. Discriminator-based usage
  (`versioning.kind == "snapshot"`) keeps working. New code should pattern
  match.
- `lineage_metadata["snapshots"]` is preserved as a backward-compat surface
  (still populated for `snapshot.latest`); new consumers should read
  `version_resolutions`.

## Testing Strategy

### New test files

`tests/test_semantic_phase2_validity.py` — author-time:

- `ms.validity()` rejects empty `open_end`, invalid `interval`,
  `valid_from`/`valid_to` not declared as fields, `valid_from` not in
  primary_key.
- `DatasetVersioningIR` discriminator (snapshot vs validity) round-trips
  through reader/loader.
- Readiness blocker when versioning metadata is invalid.

`tests/test_analysis_observe_planner_phase2.py` — narrow planner units:

- `plan_observe` dispatches base vs derived correctly.
- `_derive_version_mode` returns expected `(mode, anchor_source, anchor_value)`
  in the four `(root_has_time_field, has_timescope_end)` combinations, for
  both snapshot and validity targets.
- Validity `_effective_key` subtracts `valid_from`; relationship safety
  classification flips from `unknown` (pre-fix) to `many_to_one` after the
  effective key collapses to user_id.
- Axis comparability: components resolving same dimension name to different
  semantic field ids raises `component-axis-field-mismatch` with structured
  candidates.
- Version comparability: components mixing `latest` and `as_of_root_time`
  against the same dataset raises `component-version-mismatch`.
- Snapshot `as_of_root_time` mapping: missing anchor raises
  `snapshot-partition-missing` with the missing list.
- Validity `latest` and `as_of_root_time` produce expected predicates for
  `closed_open` / `closed_closed` × `open_end=(None,)` /
  `open_end=(None, "9999-12-31")` combinations.

`tests/test_analysis_observe_cross_dataset_phase2.py` — end-to-end:

- Derived ratio with all-single-dataset components: result equals Phase 1
  derived output for matched rows; left-join preserves unmatched root rows
  as null where Phase 1 inner-join dropped them.
- Derived ratio with multi-dataset numerator (orders + users) and
  single-dataset denominator (sessions).
- Derived ratio with components in different datasources (`warehouse`,
  `analytics`); component frames merge correctly.
- Segmented derived where the dimension is on a relationship-reachable
  dataset: passes through the shared planner, axes match Phase 1 output.
- Panel derived (time + dimension) using new shared planner.
- Snapshot `as_of_root_time` end-to-end: orders → user_profile_daily,
  per-row historical tier; mapping digest in lineage.
- Validity `latest` end-to-end with `open_end=(None,)`.
- Validity `as_of_root_time` end-to-end with `closed_open` boundary;
  boundary day verifies `valid_from <= anchor < valid_to`.
- Validity overlap warning emits `validity_overlap_unverified` in
  warnings (without raising).

### Regression coverage

- `tests/test_analysis_observe.py`,
  `tests/test_analysis_observe_segmented.py`,
  `tests/test_analysis_observe_panel.py`,
  `tests/test_analysis_observe_timescope.py`,
  `tests/test_analysis_compare_panel.py`,
  `tests/test_analysis_observe_cross_dataset_phase1.py`.
- Update assertions where Phase 1 inner-join dropped rows that left-join now
  preserves; document the change in PR.

### Examples + skill checks

- `make examples-check` must pass after migrating
  `marivo-skills/marivo-*/references/examples/` for derived ratios that now
  legitimately use multi-dataset components or `ms.validity` versioning.
- Tiny semantic fixtures get a `user_history` validity dataset for
  cross-cutting tests.

## Documentation Updates

- `docs/specs/semantic/python-semantic-layer.md`:
  - new "Validity Versioning" section: dialect, `interval`, `open_end` (null
    or sentinel list), `valid_from` ∈ primary_key requirement, no
    `current_flag` in Phase 2.
  - extend "Cross-Dataset Metrics" with: components may declare more than one
    dataset; component `root_dataset` is required when the component has more
    than one dataset; same root-only measure rule applies.
- `docs/specs/analysis/python-analysis-operator-design.md`:
  - rewrite the "Derived Observe" section to describe shared planner,
    left-join semantics, component-boundary cross-datasource.
  - new "Versioned Joins" section: derived version mode auto-selection, no
    relationship override, no metric kwarg; snapshot `as_of_root_time` cost
    note.
- `marivo-skills/marivo-analysis/SKILL.md`,
  `marivo-skills/marivo-analysis/references/cheatsheet.md`,
  `marivo-skills/marivo-analysis/references/pitfalls.md`:
  - public observe surface unchanged.
  - new repair codes: `component-axis-field-mismatch`,
    `component-version-mismatch`, `snapshot-partition-missing`,
    `validity-metadata-invalid`, `nested-derived-unsupported`.
  - call out that derived components can be multi-dataset and span
    datasources at the component boundary.
- `marivo-skills/marivo-semantic/SKILL.md`:
  - add `ms.validity(...)` authoring rule with example.
  - clarify that derived component metrics follow the same root_dataset /
    additivity rules as base metrics.

## Phased Delivery Within Phase 2

Internal task ordering (one PR per task is the recommended cadence; finer
plan to be authored separately by writing-plans):

1. IR + authoring: add `ValidityVersioningIR`, `ms.validity(...)`,
   `DatasetVersioningIR` union, validators. No planner changes yet.
2. `_effective_key` + `_derive_version_mode` helpers, with planner unit tests
   that don't yet hit derived path.
3. Snapshot `as_of_root_time` execution, end-to-end test.
4. Validity `latest` + `as_of_root_time` execution, end-to-end tests.
5. `plan_observe` entry, `DerivedObservePlan`, `_plan_derived_observe`
   wrapping the existing single-dataset components against `plan_base_observe`
   (no behavior change visible yet because old derived path still runs).
6. Switch `observe.py` derived dispatch to the new plan; delete legacy
   `_observe_derived_*` helpers; multi-dataset components become available.
7. Cross-datasource components.
8. Comparability checks (axis + version).
9. Documentation, skill, example migrations; full test matrix.

Each step should keep regression suites green; the breaking inner→left join
behavior change lands at step 6.

## Open Decisions Closed By This Design

- Derived path is unified with base; `plan_observe` is the only planner entry
  for both shapes.
- Validity dialect supports `valid_from`/`valid_to` + `interval` +
  `open_end`; `current_flag` and current-row functions are deferred.
- `as_of_root_time` for snapshot uses Python-precomputed
  anchor-to-partition mapping joined as an `ibis.memtable`, not a raw range
  join or backend-specific `asof_join`.
- `as_of_root_time` for validity uses inline interval predicates; overlap is
  recorded as a single lineage warning, not validated.
- Derived version mode is auto-selected from root time presence and
  versioning metadata; there is no override at any layer.
- Components may span datasources at the component boundary; single-component
  cross-datasource still raises.
- Comparability checks are fail-closed for both axis and version.
- `session.explain(...)`, fan-out policies, named-route selection, and
  conformed-dimension equivalence remain out of scope, deferred to future
  phases. Phase 2 lineage carries the data those features will surface
  (`version_resolutions`, mapping digests, per-component datasources) so the
  future entry can be added without re-planning.
