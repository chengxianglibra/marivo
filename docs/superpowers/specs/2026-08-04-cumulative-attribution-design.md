# Cumulative Attribution Design

Status: proposed

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

This design is independently implementable for trailing and grain-to-date
deltas that the current compare surface already produces. An all-history delta
is accepted only when some producer supplies the typed all-history comparison
contract; cumulative attribution does not relax compare itself.

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

The missing contract is a cumulative-specific attribution basis derived from
the persisted expression graph and source artifacts.

## Goals

- Keep `session.attribute(...)` as the only agent-facing attribution entry
  point and `decompose` as the frame-local primitive.
- Admit only methods mechanically justified by persisted cumulative/component
  structure.
- Reuse existing single-axis, joint, hierarchy, ratio-mix, weighted-mix,
  contribution-share, and reconciliation layouts.
- Materialize missing business axes through the existing replay DAG without
  changing metric meaning, anchor, scope, slices, or snapshots.
- Provide an anchor-aware base-flow bridge when the requested axis is the
  cumulative `over` time dimension.
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

## Unchanged Public Entry Point

```python
drivers = session.attribute(
    delta,
    axes=[region],
)
```

The signature does not gain `method`, `anchor`, `base`, `over`, `bridge`, or
`as_flow` parameters. The persisted delta and requested axes determine one
legal path.

The dispatch order becomes:

1. funnel attribution;
2. cumulative attribution basis;
3. existing non-cumulative ratio/weighted component attribution;
4. existing additive or valid semi-additive attribution;
5. aggregation-specific non-additive methods owned by their separate basis;
6. typed rejection.

A cumulative basis never falls through to generic sum admission merely because
the displayed values are numeric.

## Persisted Admission Basis

`DeltaFrameMeta` gains one optional discriminated field constructed by
`compare` from the two source expression graphs:

```python
class CumulativeAttributionBasisV1(BaseModel):
    schema: Literal["cumulative-attribution-basis/v1"]
    kind: Literal["cumulative"]
    anchor: CumulativeAnchorSemanticsV1
    current_expression_graph_fingerprint: str
    baseline_expression_graph_fingerprint: str
    canonical_cumulative_expression_fingerprint: str
    over_ref: RefPayloadV1
    components: tuple[CumulativeAttributionComponentV1, ...]
    capability: CumulativeAttributionCapabilityV1
```

Each component identifies one exact cumulative node and its already-owned base
aggregate node:

```python
class CumulativeAttributionNodeV1(BaseModel):
    expression_graph_fingerprint: str
    cumulative_node_id: str
    base_aggregate_node_id: str
    base_aggregation: Literal["sum", "count", "count_distinct"]
    base_metric_ref: RefPayloadV1
    over_ref: RefPayloadV1
    anchor: CumulativeAnchorSemanticsV1

class CumulativeAttributionComponentV1(BaseModel):
    role: str
    current: CumulativeAttributionNodeV1
    baseline: CumulativeAttributionNodeV1
    canonical_base_expression_fingerprint: str
```

Node ids are pointers into the canonical persisted expression graph, not a
second semantic definition. The producer validates that every projected node,
ref, aggregation, `over`, and anchor exactly matches the graph and component
sidecar before constructing the basis. Current and baseline nodes remain
separate authorities even when their authored trailing units differ; only the
canonical comparison fingerprint proves their equivalence. Both graph
fingerprints and the canonical basis participate in delta artifact identity.

The capability is closed:

```python
SupportedCumulativeAttributionV1(
    status="supported",
    method="sum" | "ratio_mix" | "weighted_mix",
)

BlockedCumulativeAttributionV1(
    status="blocked",
    blocker=(
        "base_non_additive" |
        "mixed_component_anchor" |
        "mixed_over_axis" |
        "component_evidence_missing" |
        "comparison_contract_missing"
    ),
)
```

Direct `sum` and `count` bases are supported with `method="sum"`.
`count_distinct` is `base_non_additive` in v1. Homogeneous cumulative ratio or
weighted-mean components are supported only when the existing component
artifact is complete and each physical component is itself a supported sum or
count base. Linear expressions may use `sum` only when their persisted
coefficients and component algebra already satisfy the ordinary additive
decomposition contract.

## Business-Axis Attribution

When none of the requested axes is the cumulative `over_ref`, `attribute`
follows the existing composite materialization path:

1. validate admission from the original delta basis;
2. load the exact current and baseline source frames;
3. recover their observe replay contracts;
4. add only the missing requested dimensions;
5. re-observe the same cumulative metric with the same anchor, time scope,
   grain, filters, report timezone, snapshot/version predicates, and semantic
   dependency identity;
6. compare with the original recovered alignment policy;
7. verify that the expanded delta carries the same cumulative attribution
   basis;
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

The bridge materializes the minimum base-flow buckets required by each paired
cumulative delta row, using the source graph and exact observation cutoffs. It
never sums displayed cumulative values.

### All-history

For an eligible all-history level change with ordered cutoffs, base-flow buckets
between the two evaluation cutoffs receive their signed contribution. Reverse
cutoffs reverse the sign. Equal cutoffs yield no base-flow rows and reconcile
only when the observed target delta is zero.

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
axis value. If the same physical bucket is present on both sides, contributions
may be consolidated only when snapshot identity proves the values share one
source state.

### Trailing

For current trailing bucket set `C` and baseline set `B`:

```text
current - baseline
  = sum(C - B)          # entering buckets
  - sum(B - C)          # leaving buckets
  + difference(C & B)   # retained/revision term, normally zero on one snapshot
```

Rows expose a closed `effect_kind`:

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

Accumulation-time rows contain:

```text
<comparison bucket coordinates>
<requested over-axis column>
source_side
effect_kind
current_value
baseline_value
contribution
share_of_total_delta
share_of_positive_pool
share_of_negative_pool
```

`source_side` is `current`, `baseline`, or `both`. Values unavailable on one
side remain null; they are not fabricated as observed zero. The signed
`contribution` is the sole additive column.

`AttributionFrameMeta.params.cumulative_method` records:

```python
{
    "anchor": {"kind": "trailing", "span_seconds": 604800},
    "path": "accumulation_time_bridge",
    "over_ref": "sales.events.event_time",
    "effect_kinds": ["entering", "leaving", "retained_change"],
}
```

Reconciliation remains owned by `AttributionReconciliation`. For time-series
or panel targets it records one partition per comparison bucket and the maximum
absolute residual. Any deepest partition outside tolerance fails before
persistence.

## Agent-Visible Behavior

An eligible cumulative delta advertises the path before execution:

```text
attribute: supported business-axis attribution_shape=sum; accumulation time axis uses flow bridge
```

For a cumulative ratio:

```text
attribute: supported attribution_shape=ratio_mix from cumulative components
```

For a blocked base:

```text
attribute: blocked: cumulative base aggregation count_distinct has no v1 additive basis
```

`AttributionFrame.show()` includes the anchor and path:

```text
cumulative_attribution: anchor=trailing(604800s) path=accumulation_time_bridge
effects: entering=... leaving=... retained_change=...
reconciliation: status=reconciled partitions=7 max_abs_residual=0
causal_claim: none
```

The same capability and repair appear in `DeltaFrame.contract()`, structured
errors, live help, and recovered artifacts. `attribute` never recommends an
unsupported ordinary sum retry for cumulative distinct values.

## Support Matrix

| Cumulative structure | Business axes | Exact `over` time axis |
| --- | --- | --- |
| direct sum/count, all-history | supported when input delta has comparison contract | interval flow bridge |
| direct sum/count, grain-to-date | cumulative level decomposition | two-scope flow bridge |
| direct sum/count, trailing | cumulative level decomposition | entering/leaving bridge |
| homogeneous ratio/weighted cumulative | existing component mix | blocked in v1 |
| linear cumulative over supported components | supported when ordinary linear reconciliation applies | blocked in v1 |
| cumulative count-distinct | blocked | blocked |
| mixed anchors or mixed `over` axes | blocked | blocked |
| missing component/graph/snapshot evidence | blocked | blocked |
| `over` plus another requested axis | blocked in v1 | blocked in v1 |

## Failure Semantics

Failures are structured and occur before unnecessary backend work whenever the
blocker is already persisted:

- `base_non_additive` names the exact base aggregation;
- `mixed_component_anchor` and `mixed_over_axis` name component roles;
- `comparison_contract_missing` explains which producer contract is absent;
- `component_evidence_missing` identifies the missing sidecar or graph node;
- replay drift reports source refs, snapshot/dependency relation, target delta,
  contribution sum, and residual;
- unsupported time-axis combinations state that v1 accepts exactly the one
  cumulative `over` axis.

No failure suggests editing frame metadata or choosing a numerical method.

## Implementation Seams

- Build the cumulative attribution basis from persisted source expression
  graphs during compare and include it in artifact identity.
- Reorder `DeltaFrame` admission so a supported cumulative basis is checked
  before the generic top-level additivity gate.
- Let `attribute` replay business axes through the existing composite DAG, then
  reuse `decompose` and component mix implementations.
- Add one internal anchor-dispatched bridge materializer for exact `over`-axis
  requests; it returns a canonical intermediate table, not a public frame.
- Persist path/effect evidence and extend reconciliation partitioning without
  adding public policy or narrowing constructors.
- Update capability registry, live help, `.show()`, `.contract()`, errors,
  persistence, evidence extraction, active specs, EN/CN site docs, and the
  packaged analysis skill in the same behavior change.

## Acceptance Criteria

- Direct cumulative sum/count MTD and trailing deltas attribute over present and
  replayed business axes and reconcile exactly for single, joint, and hierarchy
  layouts.
- Homogeneous cumulative ratio/weighted deltas reuse component mix math and do
  not fall through to sum attribution.
- All-history, grain-to-date, and trailing accumulation-time bridges satisfy
  their equations for adjacent, overlapping, disjoint, reverse, and equal
  cutoff cases that their input compare contracts admit.
- Trailing results distinguish entering, leaving, and retained-change rows by
  window membership rather than contribution sign.
- Count-distinct, mixed-anchor, mixed-time-axis, missing-evidence, and replay-
  drift cases fail with exact structured repairs before persisting a result.
- Parent delta rows, component sidecars, attribution rows, reconciliation,
  evidence, `.show()`, `.contract()`, and recovered artifacts all report the
  same anchor, method, source refs, and comparison partitions.
- Public exports and signatures remain unchanged; help and docs teach one
  `attribute` path and make no causal claim.
