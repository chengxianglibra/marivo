# Cumulative Attribution Design

Status: implemented for V1; cumulative count-distinct attribution and fiscal
period reset anchors remain out of scope

Date: 2026-08-04

## Summary

Extend the existing `session.attribute(delta, axes=[...])` operator to support
eligible cumulative deltas. Admission and execution are dispatched from
persisted cumulative structure; agents do not choose a mathematical method or
reconstruct base observations manually.

V1 supports two business questions:

1. **business-axis attribution** — which regions, products, channels, or other
   additive partitions contributed to the cumulative level change;
2. **accumulation-time attribution** — which base-flow periods entered, left,
   or differed between the two cumulative evaluation scopes.

The result remains an `AttributionFrame`, uses the existing `contribution` and
share columns, reconciles exactly to the independently observed delta, and
keeps `causal_claim="none"`.

This design targets one clean cumulative-analysis contract rather than reuse of
older analysis artifacts. It depends on the canonical comparable-period
contract for trailing and grain-to-date deltas, including duration-normalized
trailing anchors and typed pair evidence. All-history inputs use the existing
`all-history-level-change/v1` marker and exact persisted endpoint coordinates.
Older cumulative deltas are re-observed and re-compared; there is no dual read,
metadata inference, or migration path.

## Problem

Every cumulative `DeltaFrame` is currently rejected before the runtime examines
its base aggregation, component sidecar, requested axes, or anchor. The blanket
gate blocks high-value questions such as:

- Which regions drove the gap in MTD revenue versus last month to date?
- Which product lines drove the change in rolling-28d orders?
- Did rolling-7d revenue rise because new days entered the window or because
  high-revenue days left the baseline window?
- Which cumulative numerator or denominator component moved a to-date ratio?

Blindly removing the gate would also be wrong. Cumulative metric metadata sets
top-level additivity to `non_additive`; `count_distinct` loses ordinary additive
partitioning; and trailing time attribution needs signed window-set algebra,
not a sum of displayed rolling levels.

The missing contract is one compact cumulative-attribution projection derived
from the two source expression graphs when compare produces the delta.

## Goals

- Keep `session.attribute(...)` as the only agent-facing attribution entry
  point and `decompose` as the frame-local primitive.
- Admit only methods mechanically justified by one compact cumulative
  attribution contract produced with the delta.
- Reuse existing single-axis, joint, hierarchy, ratio-mix, weighted-mix,
  contribution-share, and reconciliation layouts.
- Materialize missing business axes through the source frames' current replay
  DAG without changing metric meaning, canonical anchor, scope, slices, or
  snapshots.
- Provide an anchor-aware base-flow bridge when the requested axis is the
  cumulative `over` time dimension.
- Derive and persist exactly one base-flow bridge grain before execution; never
  let a backend or row distribution choose output bucket identity implicitly.
- Reconcile every deepest partition to the already-observed target delta.
- Fail closed on non-additive bases, incomplete evidence, mutable replay drift,
  or unsupported axis combinations.

## Non-Goals

- Causal attribution or automatically selecting drivers.
- Cumulative `count_distinct`, median, percentile, min, max, or arbitrary
  non-linear Tier-2 base attribution in v1.
- Inferring a distinct membership key or raw distribution from output values.
- Comparing cumulative anchors or creating an all-history delta.
- Multi-axis attribution that combines the accumulation time dimension with
  business dimensions in v1.
- Forecasting cumulative values.
- Persisting raw source rows in an `AttributionFrame`.
- Re-executing attribution from a historical delta after its source frames,
  semantic dependency closure, or replay job is unavailable.
- Loading, migrating, or inferring cumulative attribution support for artifacts
  written before this contract. Re-run `observe -> compare` instead.

## Unchanged Public Entry Point

```python
drivers = session.attribute(
    delta,
    axes=[region],
)
```

The signature does not gain `method`, `anchor`, `base`, `over`, `bridge`,
`bridge_grain`, `grain`, or `as_flow` parameters. The persisted delta and
requested axes determine one legal path.

The dispatch order becomes:

1. funnel attribution;
2. cumulative attribution contract;
3. existing non-cumulative ratio/weighted component attribution;
4. existing additive or valid semi-additive attribution;
5. aggregation-specific non-additive methods owned by their separate basis;
6. typed rejection.

A cumulative contract never falls through to generic sum admission merely
because the displayed values are numeric.

## Canonical Attribution Contract

Every cumulative `DeltaFrame` produced by the new compare contract carries one
required compact field:

```python
class CumulativeBridgeGrainV1(BaseModel):
    grain: Grain
    report_timezone: str
    origin: Literal[
        "observation_query_grain",
        "over_declared_granularity",
        "executor_day_default",
    ]


class AvailableCumulativeBridgeV1(BaseModel):
    status: Literal["available"]
    value: CumulativeBridgeGrainV1


class BlockedCumulativeBridgeV1(BaseModel):
    status: Literal["blocked"]
    blocker: Literal["bridge_grain_mismatch"]
    current_grain: Grain
    baseline_grain: Grain
    current_report_timezone: str
    baseline_report_timezone: str


type CumulativeBridgeV1 = Annotated[
    AvailableCumulativeBridgeV1 | BlockedCumulativeBridgeV1,
    Field(discriminator="status"),
]


class CumulativeBaseComponentV1(BaseModel):
    canonical_expression_fingerprint: str
    aggregation: Literal["sum", "count", "count_distinct"]


class DirectCumulativeAttributionV1(BaseModel):
    kind: Literal["direct"]
    base: CumulativeBaseComponentV1


class RatioCumulativeAttributionV1(BaseModel):
    kind: Literal["ratio"]
    numerator: CumulativeBaseComponentV1
    denominator: CumulativeBaseComponentV1


class WeightedCumulativeAttributionV1(BaseModel):
    kind: Literal["weighted_mean"]
    numerator: CumulativeBaseComponentV1
    weight: CumulativeBaseComponentV1


class LinearCumulativeTermV1(BaseModel):
    coefficient: float
    component: CumulativeBaseComponentV1


class LinearCumulativeAttributionV1(BaseModel):
    kind: Literal["linear"]
    terms: tuple[LinearCumulativeTermV1, ...]


type CumulativeAttributionStructureV1 = Annotated[
    DirectCumulativeAttributionV1
    | RatioCumulativeAttributionV1
    | WeightedCumulativeAttributionV1
    | LinearCumulativeAttributionV1,
    Field(discriminator="kind"),
]


class CumulativeAttributionContractV1(BaseModel):
    schema: Literal["cumulative-attribution/v1"]
    over_ref: RefPayloadV1
    bridge: CumulativeBridgeV1
    structure: CumulativeAttributionStructureV1


class CumulativeDeltaFrameMetaV1(DeltaFrameMeta):
    artifact_schema: Literal["cumulative-delta/v1"]
    cumulative: CumulativeMetricContractV1
    cumulative_attribution: CumulativeAttributionContractV1
```

Persistence selects the cumulative metadata variant whenever `cumulative` is
present. The attribution contract is required on that variant; there is no
optional legacy shape. Older cumulative artifacts fail with
`unsupported_artifact_schema` and a single repair: re-run `observe -> compare`
under the current environment. `CumulativeMetricContractV1` remains owned by
the semantic/observe cumulative contract; attribution does not redefine it.

`compare` constructs the contract by projecting both validated source
expression graphs and component sidecars to the same closed structure. It
requires their projections to match exactly after ordinary comparable-value
and canonical-anchor admission. The compact contract is the authority for
attribution shape; it does not persist graph copies, node ids, authored-anchor
duplicates, source-frame fingerprints, or a second comparison identity.
The canonical component fingerprint identifies common value semantics, not one
side's catalog metric identity, so two differently named but admitted
comparable metrics project to the same structure.

Anchor and pairing ownership stays where it already belongs:

- all-history uses `cumulative_change="all-history-level-change/v1"` and the
  two exact endpoint columns;
- trailing and grain-to-date use the canonical anchor and pair summary in
  `DeltaFrameMeta.cumulative_alignment` plus `DeltaComparisonIdentityV2`.

The comparable-period design is therefore a delivery prerequisite, not an
optional compatibility enhancement. Equivalent trailing durations such as
`7 day` and `1 week` produce the same `TrailingAnchorSemanticsV1` and may enter
the same attribution path. Authored spellings remain source provenance but do
not multiply attribution contracts.

V1 derives the bridge without a caller policy slot. For time-series and
panel sources it is the exact common observation query grain. For scalar and
segmented sources it is the `over_ref` semantic granularity, or the executor's
fixed `day` default when absent. Current and baseline must derive the same
`Grain` and report timezone to produce `AvailableCumulativeBridgeV1`; otherwise
the contract retains the exact mismatch in `BlockedCumulativeBridgeV1` so
business-axis attribution remains available. Calendar bucket boundaries are
formed in the admitted report timezone under the ordinary half-open
`[start, end)` contract and only then serialized as UTC instants, so DST days
are not rewritten as fixed 86,400-second spans.

Capability is a query-free projection of `structure`, `bridge`, and requested
axis refs; it is not another persisted field that can disagree with them:

```python
SupportedCumulativeAttributionRouteV1(
    status="supported",
    path="cumulative_level_decomposition" | "accumulation_time_bridge",
)

BlockedCumulativeAttributionRouteV1(
    status="blocked",
    blocker=(
        "base_non_additive"
        | "bridge_grain_mismatch"
        | "component_time_bridge_unsupported"
        | "over_plus_business_axis_unsupported"
    ),
    repair=AnalysisRepair(...),
)


class CumulativeAttributionCapabilityV1(BaseModel):
    business_axes: SupportedCumulativeAttributionRouteV1 | BlockedCumulativeAttributionRouteV1
    accumulation_time: SupportedCumulativeAttributionRouteV1 | BlockedCumulativeAttributionRouteV1
    mixed_axes: BlockedCumulativeAttributionRouteV1
```

The structure mechanically owns the method: `direct` and `linear` use `sum`,
`ratio` uses `ratio_mix`, and `weighted_mean` uses `weighted_mix`. Capability
does not repeat it in persistence. Direct sum/count supports both routes when
`bridge.status="available"`. Ratio, weighted mean, and eligible linear
structure support business axes only. Count-distinct is blocked in both routes.

Axis classification uses exact semantic refs: no requested `over_ref` selects
`business_axes`; exactly `[over_ref]` selects `accumulation_time`; any request
containing `over_ref` plus another axis selects `mixed_axes`.
`DeltaFrame.contract()` and `.show()` project the complete three-route map.
Execution selects exactly one contained route, and any structured error echoes
that route and repair without a second admission decision.

At execution, `attribute` loads the two source `MetricFrame`s referenced by the
delta, reconstructs the structure projection from their current replay graphs,
and requires it to equal the compact contract. Missing source frames, missing
replay jobs, or changed semantic dependencies are not compatibility cases; the
operation fails with the same re-observe-and-compare repair. No historical
graph or job payload is embedded in the delta merely to keep old analysis
results executable.

## Business-Axis Attribution

When none of the requested axes is the cumulative `over_ref`, `attribute`
follows the existing composite materialization path:

1. validate admission from the original delta contract;
2. load the exact current and baseline source frames;
3. recover their observe replay contracts;
4. add only the missing requested dimensions;
5. re-observe the same cumulative metric with the same anchor, time scope,
   grain, filters, report timezone, snapshot/version predicates, and semantic
   dependency identity;
6. compare with the original alignment policy;
7. verify that the expanded delta projects the same cumulative attribution
   structure and canonical anchor;
8. call the existing `decompose` implementation.

For a direct cumulative sum/count, the per-axis contribution is:

```text
axis_contribution = current_cumulative_level(axis) - baseline_cumulative_level(axis)
```

Deepest rows must sum to the independently observed unsegmented target delta.
Single-axis, joint, and hierarchy layouts retain their existing invariants.

For cumulative ratios and weighted means, the existing component-aware mix
math operates on cumulative component levels at the two observed cutoffs. The
result explains the change in the derived cumulative level; it does not apply
the derived formula to component deltas.

If replay observes changed data and no longer reconciles to the original
target, attribution fails with `AttributionMaterializationError`. It does not
persist a residual explanation for a different snapshot.

## Accumulation-Time Attribution

When `axes` is exactly `[over_ref]`, `attribute` uses an internal base-flow
bridge. This is still the same public operator; the time-axis identity makes
the business request unambiguous.

The bridge materializes exactly the union of `bridge_grain` buckets that
intersect each paired cumulative scope, using the source graph and exact
observation cutoffs. Edge buckets are clipped to the exact half-open scope and
carry their exact `flow_interval_start` and `flow_interval_end`; the requested
`over_ref` column remains the canonical bridge-bucket coordinate. It never
sums displayed cumulative values or expands a partial bucket to a full one.

### All-history

For an eligible all-history level change with ordered cutoffs, base-flow buckets
between the two evaluation cutoffs receive their signed contribution. Reverse
cutoffs reverse the sign. Equal cutoffs yield no base-flow rows and reconcile
only when the observed target delta is zero.

Forward rows use `source_side="current"`; reverse rows use
`source_side="baseline"`. Both use `effect_kind="between_cutoffs"`. The
selected side is the source replay whose endpoint owns the later observed
level; the other side remains null rather than being fabricated as zero.

Because source restatements can make level subtraction differ from replayed
flow, the bridge must reconcile to the observed delta. A non-zero residual
blocks the result with a snapshot/revision repair; it is not silently labeled
unattributed flow.

### Grain-to-date

The bridge materializes base-flow buckets for both elapsed reset-period scopes:

```text
contribution(bucket in current scope)  = +current flow
contribution(bucket in baseline scope) = -baseline flow
```

Calendar or ordinal alignment determines paired cumulative cutoffs, but does
not rewrite base-flow dates. Rows carry `source_side` and the requested time
axis value. Current and baseline fragments with the same exact physical
interval are consolidated as one observed side-to-side difference row. This is
arithmetic over two replayed observations, not proof that they share one source
revision.

Current-only rows use `effect_kind="current_scope"`, baseline-only rows use
`effect_kind="baseline_scope"`, and a safely consolidated physical bucket uses
`effect_kind="shared_scope_change"`. Their required `source_side` values are
`current`, `baseline`, and `both`, respectively. `both` means that both observed
sides supplied a value; it never asserts common provider snapshot identity.

### Trailing

For current trailing bucket set `C` and baseline set `B`:

```text
current - baseline
  = sum(C - B)          # entering buckets
  - sum(B - C)          # leaving buckets
  + difference(C & B)   # retained/revision term, normally zero on one snapshot
```

Rows expose the trailing branch of the closed `effect_kind` contract:

```text
entering | leaving | retained_change
```

`effect_kind` describes window membership, not whether a metric movement is
good or bad. A negative entering flow remains an entering contribution.

For multiple paired output buckets, the result includes the parent comparison
bucket coordinate so overlapping bridges do not masquerade as one additive
global partition. Reconciliation is per comparison bucket.

## Row and Result Contract

Business-axis output reuses the existing row layouts unchanged. The method is
the existing `sum`, `ratio_mix`, or `weighted_mix`; cumulative meaning is
reported in typed method evidence rather than by multiplying public shape
tags.

Business-axis results retain
`row_contract_version="generic-attribution-rows/v3"`. Accumulation-time results
use the distinct
`row_contract_version="cumulative-flow-attribution-rows/v1"`; they do not
pretend the additional temporal coordinates are an unchanged generic row.

Accumulation-time rows contain:

```text
<comparison bucket coordinates>
<requested over-axis column>
flow_interval_start
flow_interval_end
source_side
effect_kind
current_value
baseline_value
contribution
rank
share_of_total_delta
share_of_positive_pool
share_of_negative_pool
```

`source_side` is `current`, `baseline`, or `both`. Values unavailable on one
side remain null; they are not fabricated as observed zero. The signed
`contribution` is the sole additive column. `rank` restarts inside each exact
comparison partition and orders descending absolute contribution, then the
over-axis coordinate, `flow_interval_start`, and `source_side` for deterministic
ties. Shares use the independently observed target delta and same-sign pools
inside that same partition, never across parent comparison buckets.
The final tie order is `current`, `baseline`, then `both`; it is fixed by the
row-contract version rather than backend string ordering.

The cumulative path is not stored in the open `params` dictionary. The typed
method-evidence union gains closed cumulative variants:

```python
class CumulativeAttributionPartitionV1(BaseModel):
    comparison_key: tuple[tuple[str, JsonScalar], ...]
    target_delta: float
    contribution_sum: float
    row_count: int = Field(ge=0)
    residual: float
    tolerance: float = Field(ge=0)


class CumulativeAllHistoryPartitionV1(CumulativeAttributionPartitionV1):
    current_evaluation_end: datetime
    baseline_evaluation_end: datetime


class CumulativeComparablePeriodPartitionV1(CumulativeAttributionPartitionV1):
    current_scope_start: datetime
    current_scope_end: datetime
    baseline_scope_start: datetime
    baseline_scope_end: datetime


class AllHistoryAnchorSemanticsV1(BaseModel):
    kind: Literal["all_history"]


type CumulativeAttributionAnchorV1 = Annotated[
    AllHistoryAnchorSemanticsV1 | GrainToDateAnchorSemanticsV1 | TrailingAnchorSemanticsV1,
    Field(discriminator="kind"),
]


class CumulativeBusinessAxisEvidenceV1(BaseModel):
    kind: Literal["cumulative_business_axes"]
    route: Literal["business_axes"]
    anchor: CumulativeAttributionAnchorV1
    over_ref: RefPayloadV1
    partitions: tuple[CumulativeAttributionPartitionV1, ...]


class CumulativeAllHistoryFlowEvidenceV1(BaseModel):
    kind: Literal["cumulative_all_history_flow"]
    route: Literal["accumulation_time"]
    anchor: AllHistoryAnchorSemanticsV1
    over_ref: RefPayloadV1
    bridge_grain: CumulativeBridgeGrainV1
    effect_kinds: tuple[Literal["between_cutoffs"]]
    partitions: tuple[CumulativeAllHistoryPartitionV1, ...]


class CumulativeGrainToDateFlowEvidenceV1(BaseModel):
    kind: Literal["cumulative_grain_to_date_flow"]
    route: Literal["accumulation_time"]
    anchor: GrainToDateAnchorSemanticsV1
    over_ref: RefPayloadV1
    bridge_grain: CumulativeBridgeGrainV1
    effect_kinds: tuple[
        Literal["current_scope"],
        Literal["baseline_scope"],
        Literal["shared_scope_change"],
    ]
    partitions: tuple[CumulativeComparablePeriodPartitionV1, ...]


class CumulativeTrailingFlowEvidenceV1(BaseModel):
    kind: Literal["cumulative_trailing_flow"]
    route: Literal["accumulation_time"]
    anchor: TrailingAnchorSemanticsV1
    over_ref: RefPayloadV1
    bridge_grain: CumulativeBridgeGrainV1
    effect_kinds: tuple[
        Literal["entering"],
        Literal["leaving"],
        Literal["retained_change"],
    ]
    partitions: tuple[CumulativeComparablePeriodPartitionV1, ...]


type CumulativeAttributionEvidenceV1 = Annotated[
    CumulativeBusinessAxisEvidenceV1
    | CumulativeAllHistoryFlowEvidenceV1
    | CumulativeGrainToDateFlowEvidenceV1
    | CumulativeTrailingFlowEvidenceV1,
    Field(discriminator="kind"),
]
```

`AttributionFrameMeta.method_evidence` includes this union and
`scope_delta_ref` identifies the input delta. Construction validates the
evidence route, canonical anchor, exact `over_ref`, row-contract version, and
row columns against that delta's compact contract. The persisted evidence does
not copy the comparison identity, source graph fingerprints, node ids, or
contract fingerprint. The accumulation-time row validator additionally
enforces these closed pairs:

| Comparison kind | `effect_kind` | Required `source_side` |
| --- | --- | --- |
| all-history | `between_cutoffs` | `current` or `baseline`, matching cutoff direction |
| grain-to-date | `current_scope` | `current` |
| grain-to-date | `baseline_scope` | `baseline` |
| grain-to-date | `shared_scope_change` | `both` |
| trailing | `entering` | `current` |
| trailing | `leaving` | `baseline` |
| trailing | `retained_change` | `both` |

`current_value`/`baseline_value` nullability must agree with `source_side`, and
every row interval must be non-empty and contained in the corresponding typed
comparison scope. Invalid combinations fail artifact construction.

The existing `AttributionReconciliation` remains the common bounded summary.
Its `partition_count` and `max_abs_residual` must exactly summarize the typed
partition records in method evidence; single-partition scalar fields must equal
that one record. Each `comparison_key` is the exact ordered parent-delta row
coordinate, not merely a count or display label. Evidence partitions and rows
must have identical key sets, row counts, targets, sums, residuals, and
tolerances. Any mismatch or out-of-tolerance partition fails before persistence
of the new result. Loading the result validates its self-contained row and
evidence models but does not reload historical source frames to re-prove the
analysis.

## Agent-Visible Behavior

An eligible cumulative delta advertises the path before execution:

```text
attribute.business_axes: supported path=cumulative_level_decomposition method=sum
attribute.accumulation_time: supported path=accumulation_time_bridge grain=day timezone=Asia/Shanghai
attribute.mixed_axes: blocked blocker=over_plus_business_axis_unsupported
```

For a cumulative ratio:

```text
attribute.business_axes: supported path=cumulative_level_decomposition method=ratio_mix
attribute.accumulation_time: blocked blocker=component_time_bridge_unsupported
```

For a blocked base:

```text
attribute.business_axes: blocked blocker=base_non_additive aggregation=count_distinct
attribute.accumulation_time: blocked blocker=base_non_additive aggregation=count_distinct
```

`AttributionFrame.show()` includes the anchor and path:

```text
cumulative_attribution: anchor=trailing(span_seconds=604800) path=accumulation_time_bridge
bridge_grain: day timezone=Asia/Shanghai row_contract=cumulative-flow-attribution-rows/v1
effects: entering=... leaving=... retained_change=...
reconciliation: status=reconciled partitions=7 max_abs_residual=0
causal_claim: none
```

The same capability and repair appear in `DeltaFrame.contract()`, structured
errors, and live help for artifacts produced under this contract. `attribute`
never recommends an unsupported ordinary sum retry for cumulative distinct
values.

## Support Matrix

| Cumulative structure | Business axes | Exact `over` time axis |
| --- | --- | --- |
| direct sum/count, all-history | supported with `all-history-level-change/v1` | interval flow bridge at canonical grain |
| direct sum/count, grain-to-date | cumulative level decomposition | two-scope flow bridge |
| direct sum/count, trailing | cumulative level decomposition | entering/leaving bridge |
| homogeneous ratio/weighted cumulative | existing component mix | blocked in v1 |
| linear cumulative over supported components | supported when ordinary linear reconciliation applies | blocked in v1 |
| cumulative count-distinct | blocked | blocked |
| different canonical anchors or mixed `over` axes | no input delta; compare blocks | no input delta; compare blocks |
| source frames, replay jobs, or current dependency closure unavailable | execution blocked; re-run `observe -> compare` | execution blocked; re-run `observe -> compare` |
| `over` plus another requested axis | blocked in v1 | blocked in v1 |

## Failure Semantics

Failures are structured and occur before unnecessary backend work whenever the
compact contract or current source state already proves the blocker:

- `base_non_additive` names the exact base aggregation;
- `bridge_grain_mismatch` reports both exact grain/timezone projections and
  requires re-observation under one compatible temporal contract;
- `component_time_bridge_unsupported` identifies the admitted structure kind;
- `over_plus_business_axis_unsupported` reports the exact requested refs;
- `source_analysis_unavailable` names the missing source frame or replay job and
  repairs only by re-running `observe -> compare`;
- `semantic_dependency_changed` and `structure_projection_changed` report the
  source refs and require a new observation/comparison under the active
  semantics;
- replay drift reports source refs, snapshot/dependency relation, target delta,
  contribution sum, and residual;
- unsupported time-axis combinations state that v1 accepts exactly the one
  cumulative `over` axis.

No failure suggests editing frame metadata or choosing a numerical method.

## Implementation Seams

- Make the compact cumulative attribution contract required on every newly
  produced cumulative delta and include it in artifact identity. Bump the
  cumulative delta artifact schema once; add no optional legacy field,
  dual-reader, alias, or migration code.
- Build the contract during compare by projecting the two admitted source
  graphs and component sidecars. Reuse canonical anchor/pair ownership from the
  comparable-period contract instead of persisting attribution copies.
- Reorder `DeltaFrame` admission so its compact cumulative contract is checked
  before the generic top-level additivity gate.
- Let `attribute` replay business axes through the existing composite DAG, then
  reuse `decompose` and component mix implementations.
- Require the source frames, replay jobs, and dependency closure at execution;
  do not embed historical graphs or replay jobs in the delta as a fallback.
- Add one internal anchor-dispatched bridge materializer for exact `over`-axis
  requests; it returns a canonical intermediate table, not a public frame.
- Persist only the result's canonical anchor, bridge grain, path/effect, exact
  interval, and per-partition reconciliation evidence. Do not copy comparison
  identities or source graph fingerprints into the terminal result.
- Add `cumulative-flow-attribution-rows/v1` beside the unchanged generic v2
  business-axis rows without adding public policy or narrowing constructors.
- Update capability registry, live help, `.show()`, `.contract()`, errors,
  persistence, evidence extraction, active specs, EN/CN site docs, and the
  packaged analysis skill in the same behavior change.

## Acceptance Criteria

- Direct cumulative sum/count MTD and trailing deltas attribute over present and
  replayed business axes and reconcile exactly for single, joint, and hierarchy
  layouts.
- Homogeneous cumulative ratio/weighted deltas reuse component mix math and do
  not fall through to sum attribution; their business-axis route is supported
  and their accumulation-time route is mechanically blocked before replay.
- All-history, grain-to-date, and trailing accumulation-time bridges satisfy
  their equations for adjacent, overlapping, disjoint, reverse, and equal
  cutoff cases that their input compare contracts admit.
- Time-series/panel bridges use the exact persisted query grain;
  scalar/segmented bridges use persisted `over_ref` granularity or the fixed
  executor `day` default. Mismatched grain/timezone projections fail before a
  query. Partial edge buckets, report-timezone DST transitions, and exact
  half-open cutoff boundaries retain exact interval coordinates and reconcile.
- All-history attribution accepts exactly the current
  `all-history-level-change/v1` marker and exact endpoint columns. Trailing V1
  consumes canonical `span_seconds`; equivalent authored units such as `7 day`
  and `1 week` enter the same contract after comparable-period admission.
- Trailing results distinguish entering, leaving, and retained-change rows by
  window membership rather than contribution sign.
- All-history, grain-to-date, and trailing flow rows enforce their closed
  `effect_kind`/`source_side`/value-nullability combinations, deterministic rank,
  and `cumulative-flow-attribution-rows/v1` during construction. Loading a new
  result validates only its self-contained row/evidence contract.
- Count-distinct, unavailable source analysis, changed semantic dependencies,
  structure drift, mixed-time-axis requests, and replay drift fail with exact
  structured repairs before persisting a result. Different canonical anchors
  remain compare failures and never produce an attribution input.
- Parent delta rows, component sidecars, attribution rows, reconciliation,
  evidence, `.show()`, and `.contract()` all report the same canonical anchor,
  route, bridge grain, exact flow intervals, method, source refs, and typed
  comparison partitions. Summary reconciliation exactly derives from those
  partition records.
- `DeltaFrame.contract()` and `.show()` expose the same complete derived route
  map; execution selects exactly one route from the requested refs, and a
  structured failure echoes that selected route and repair unchanged.
- Public exports and call signatures remain unchanged; the artifact schema is a
  clean break. Help and docs teach one `attribute` path and make no causal
  claim.
