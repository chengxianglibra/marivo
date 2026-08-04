# All-History Cumulative Compare Design

Status: proposed

Date: 2026-08-04

## Summary

Allow `session.compare(...)` to compare compatible `all_history` cumulative
frames. The operator continues to compute current minus baseline; it does not
silently re-observe the base metric, invent a period window, or add a second
compare API.

The result is explicitly an **observed level change**:

```text
net_level_change = current_cumulative_level - baseline_cumulative_level
```

For an additive `sum` or `count` under an unchanged source history, that value
is algebraically equal to the intervening flow. Marivo does not promote that
conditional equivalence into the result's meaning. It is not generally true
for `count_distinct`, mutable history, restatements, negative flows, or derived
cumulative levels.

This design is independently deployable from cumulative comparable-period
alignment and cumulative attribution. It establishes one shared cumulative
`evaluation_end` row coordinate, then changes only all-history compare admission
and the result evidence needed to interpret that comparison. Other-anchor
alignment and attribution remain governed by their separate contracts.

## Problem

The semantic and observe surfaces accept `all_history` cumulative metrics, but
`compare` rejects every such frame. The rejection prevents valid business
questions such as:

- How much did lifetime booked revenue change between two reporting cutoffs?
- How much did the installed customer base change year over year?
- How did a lifetime conversion or utilization level move between snapshots?
- Which segments gained or lost cumulative balance?

The current teaching text says that a cumulative delta equals the base total
over the window. That statement is too broad. For example, the difference
between two all-history distinct-user counts is the change in the cumulative
set cardinality. Re-observing `count_distinct(user_id)` only over the interval
also counts users who first appeared before the baseline, so it answers a
different question.

The missing contract is therefore not subtraction. It is truthful ownership of
the level-change interpretation, paired evaluation coordinates, and caveats.

## Goals

- Keep `session.compare(current, baseline, alignment=...)` as the only public
  comparison entry point.
- Admit direct and structurally complete derived all-history cumulative frames
  when the existing value and alignment contracts are compatible.
- Preserve the exact observed values as the authority; do not re-query a base
  metric merely to make compare legal.
- Give every cumulative `MetricFrame` row one canonical `evaluation_end`
  coordinate and carry the paired coordinates into every delta row.
- Distinguish net level change from period flow in help, errors, frame cards,
  evidence, and recovery.
- Preserve source coverage and state that source revision comparability is
  unverified without inventing proof.
- Keep `MetricFrame.contract()` preconditions local to the inspected artifact;
  validate current/baseline pair compatibility only after `session.compare(...)`
  receives both inputs.

## Non-Goals

- Automatically interpreting the delta as revenue earned, users acquired, or
  any other business flow.
- Making different cumulative anchors comparable.
- Inferring append-only history, source restatement policy, or historical
  completeness from a metric definition or sample.
- Adding `compare_cumulative`, a comparison-basis policy, or a caller-provided
  `as_flow=True` switch.
- Adding a `conditional`, `pending`, or `requires_input` precondition status to
  represent facts that a single frame cannot decide.
- Persisting provider snapshot tokens or claiming that compare proves two
  observations share one source revision.
- Enabling cumulative attribution, decomposition, or forecasting.

## Public Contract

The signature is unchanged:

```python
delta = session.compare(
    current,
    baseline,
    alignment=mv.window_bucket(),
)
```

`compare` admits an all-history pair only when all existing frame-shape,
time-axis, grain, report-timezone, requested-dimension, comparable-value, and
cross-session checks pass, and all of the following are true:

1. both frames carry a valid cumulative marker;
2. both effective anchors are exactly `all_history`;
3. both markers are either direct cumulative markers or structurally complete
   derived wrappers;
4. derived wrappers have no persisted `compare_blocker` and have the same
   component roles and comparable component identities;
5. the alignment produces at least one pair of complete business coordinates.

Pairing is a row-identity fact, not a non-null-value test. A coordinate that is
present on both sides remains paired when either observed level is null, for
example when a derived ratio has a zero denominator. That row is retained with
a null delta and is counted separately from genuinely one-sided rows.

The current and baseline frames need not be authored from the same metric ref.
As with ordinary compare, their persisted comparable value semantics are the
authority. A same-name or same-unit guess is never sufficient.

### Paired rows only

All-history comparison subtracts two observed levels. A one-sided row has no
level-change meaning, so every semantic shape keeps only mechanically paired
rows. Scalar pairs use their single row; segmented pairs use the complete
dimension tuple; time-series pairs use the alignment's current and baseline
cutoff coordinates; panel pairs use the complete dimension tuple plus that
cutoff pair. It never turns an unpaired current level into a new contribution or
an unpaired baseline level into churn.

The alignment materializes one canonical parent pair-key set before delta math.
The parent delta and every component sidecar consume that exact set; components
do not rematch independently. A pair with a null current or baseline value stays
in the set, preserves the null, and produces a null delta. Only absent business
coordinates are dropped.

`alignment.cumulative_pairs` records:

```python
{
    "anchor": "all_history",
    "matched_rows": 7,
    "matched_null_rows": 1,
    "current_unpaired_rows": 0,
    "baseline_unpaired_rows": 1,
    "unpaired_action": "dropped",
}
```

`matched_rows` counts retained business coordinates, including matched-null
rows. `matched_null_rows` counts retained coordinates for which either observed
level is null; it does not include absent rows. If an alignment produces no
paired coordinates, compare fails with a structured alignment error.
`strict_lengths=True` continues to reject unequal expected lengths before
alignment; the paired-only rule governs the default non-strict case.

### Cumulative evaluation coordinate

Evaluation time belongs to the observed cumulative value, not to compare. Every
persisted row of a direct or structurally complete derived cumulative
`MetricFrame`, regardless of anchor, carries one timezone-aware
`evaluation_end` business coordinate canonically serialized in UTC. Date-only
window bounds are first interpreted in the persisted report timezone. This is a
single shared frame foundation reused by other cumulative designs; it does not
change their admission rules.

`evaluation_end` is a reserved system-owned coordinate column, not a requested
dimension or analysis time axis. Adding it does not change `semantic_kind`,
requested axes, or value-column discovery.

Observe materializes the coordinate once:

- scalar and segmented rows use the persisted observation `window.end`;
- direct time-series and panel rows use the earlier of the represented bucket's
  exclusive end and the persisted observation `window.end`, so a partial final
  bucket never claims an unobserved full-bucket cutoff;
- derived cumulative rows inherit the exact common coordinate of their aligned
  components; construction fails closed if components disagree.

Transforms operate on the coordinate as part of the selected row. Filtering,
slicing, window clipping, and other row-preserving transforms retain it. A
cumulative `rollup_fold="last"` selects the complete last input row, including
`evaluation_end`, before relabeling the display bucket to the target period
start. Chained rollups therefore propagate the coordinate without a separate
selected-child side channel. A transform that combines cumulative rows without
one mechanically selected or common evaluation coordinate is blocked.

Compare never reconstructs this coordinate from `bucket_start`, grain, window
metadata, or a display label. Both inputs must already carry it. Every persisted
all-history delta row always carries timezone-aware `current_evaluation_end` and
`baseline_evaluation_end` columns copied from the canonical paired input rows.
There is no shared-endpoint storage variant.

Endpoint order is computed from these columns as `forward`, `reverse`, `same`,
or `mixed`; it is not separately persisted. Compare still means current minus
baseline when current is earlier, and Marivo never swaps the arguments. Equal
cutoffs are legal and should normally produce zero, while source revisions
remain outside compare's proof boundary.

## Persisted Change Evidence

`DeltaFrameMeta` gains one optional typed marker named `cumulative_change`. The
versioned type defines all-history observed-level-difference semantics; it does
not repeat fixed anchor, interpretation, flow, endpoint, or ordering facts:

```python
class AllHistoryLevelChangeV1(BaseModel):
    schema: Literal["all-history-level-change/v1"]

class DeltaFrameMeta(BaseFrameMeta):
    cumulative_change: AllHistoryLevelChangeV1 | None = None
```

The compare producer constructs this object; callers cannot author it. Its
canonical marker participates in artifact identity and is recovered unchanged
after session restart. The two exact endpoint columns are ordinary persisted
business coordinates and therefore already participate in row content identity.
Delta evidence extraction copies their exact values into every scalar,
segmented, time-series, and panel finding; it never reconstructs them from
display labels.

This design does not persist or compare provider snapshot tokens. Every result
states the same conservative boundary: source revision is unverified, so the
observed level difference may include restatements. A future source-revision
evidence contract must define provider stability, persistence, transform and
replay propagation, privacy, and backend coverage independently before compare
can claim more.

No boolean such as `is_flow` is persisted. A future consumer that wants to
lower an additive level change to flow must separately prove the base
aggregation, ordered cutoffs, source revision, and historical coverage.

## Direct and Derived Metrics

### Direct cumulative metrics

`sum`, `count`, and `count_distinct` cumulative levels are admitted. The result
meaning remains identical across all three: observed level difference.

For `count_distinct`, `.show()` must not recommend re-observing interval
distinct count as an equivalent calculation. The repair path for deeper
membership analysis is a distinct-membership-capable attribution contract, not
ordinary interval aggregation.

### Derived cumulative metrics

A ratio, weighted mean, or linear expression over homogeneous all-history
cumulative components may compare when the existing derived cumulative marker
is complete and unblocked. Its delta is the difference between two derived
levels, not the derived function of component deltas.

Component sidecars continue to compare component levels role by role over the
parent delta's canonical pair-key set, so a future component-aware attribution
can consume the canonical artifact without rematching. A matched-null parent row
does not discard otherwise valid component levels. Mixed cumulative/non-
cumulative expressions, mixed anchors, and unresolved component anchors remain
blocked.

## Agent-Visible Behavior

`MetricFrame.show()` and `.contract()` remove the blanket all-history rejection
once the inspected frame has a valid marker and no local `compare_blocker`.
They expose the ordinary `compare` affordance but do not claim that an unseen
baseline is compatible:

```text
compare: available: pair compatibility is validated with the baseline at call time
caveat: the result is not asserted to be interval flow; source history may be restated
```

`ArtifactPrecondition.status` remains the existing `pass | fail` contract. A
`MetricFrame.contract()` precondition may describe only facts determined from
that frame, such as a valid all-history marker or a persisted derived-wrapper
blocker. A local failure stays `fail` and carries a real repair. Cross-frame
checks such as shape, anchor, grain, report timezone, dimensions, comparable
value semantics, component identity, and aligned pair availability are not
encoded as passed, failed, or prose-only "conditional" preconditions on either
input.

Focused `analysis.compare` help owns those static pair requirements.
`session.compare(...)` owns their runtime evaluation because it has both exact
artifacts and the selected alignment. A failed pair check returns a structured
error containing concrete expected and received state, a real repair when one
exists, and the same focused help target. The absence of a baseline is an input
requirement, not a broken state of the current frame.

`DeltaFrame.show()` includes:

```text
cumulative_change: all_history observed_level_difference endpoint_order=forward
caveat: source revision unverified; interval-flow equivalence not asserted
endpoints: columns=current_evaluation_end,baseline_evaluation_end
alignment: matched_rows=7 matched_null_rows=1 baseline_unpaired_rows=1 action=dropped
```

Each interpretation fact has one canonical owner: pairing loss and matched-null
count live in typed alignment metadata, exact endpoint direction is computed
from the two persisted row columns, and the unverified source-revision caveat is
defined by the versioned cumulative-change marker. `.show()` and evidence render
those authorities; the facts are not copied into every affordance as passing
preconditions. `DeltaFrame.contract()` adds a precondition only when one changes
a specific downstream capability's admission or repair.

`DeltaFrame.contract()` continues to block `attribute` until the independent
cumulative attribution admission is present. It must not advertise ordinary
additive attribution merely because subtraction succeeded.

`marivo.help("analysis.compare")` owns the runnable static contract. Its focused
example observes two compatible all-history cumulative frames, compares them,
calls `.show()` and `.contract()`, and reads the canonical endpoint columns from
`.to_pandas()`. The same help target documents at least one structured
anchor/alignment failure with concrete expected and received state plus its real
repair. Cumulative metric help owns the authoring meaning; the packaged analysis
skill keeps only the level-change boundary and routing. The old blanket
all-history rejection and broad “equals base total” wording are removed from
errors and active specs when this design is implemented.

## Failure Matrix

| Case | Result |
| --- | --- |
| direct all-history, comparable values, paired rows | supported |
| homogeneous derived all-history, complete components | supported |
| all-history versus trailing or grain-to-date | blocked: anchor mismatch |
| mixed-anchor or incomplete derived wrapper | blocked with persisted blocker |
| no paired rows | blocked: alignment produced no comparable levels |
| one-sided segment, tail, or panel coordinate | dropped and counted in alignment evidence |
| paired coordinate with a null level | retained with null delta and counted as matched-null |
| source revision proof unavailable | supported with the fixed unverified caveat |
| legacy cumulative row without `evaluation_end` | blocked; re-observe under current contract |

## Implementation Seams

- Normalize and validate all-history markers in `analysis._cumulative`.
- Relax the all-history branch in `intents._validate.cumulative_compare_issue`.
- Keep `ArtifactPreconditionStatus` unchanged. Project only current-frame-local
  cumulative facts into `MetricFrame.contract()`; keep pair-dependent predicates
  in focused compare help and evaluate them inside `session.compare(...)` after
  both artifacts and the alignment are available.
- Add one canonical `evaluation_end` column to every direct and structurally
  complete derived cumulative `MetricFrame` row. Observe owns its derivation;
  transforms preserve it as part of the row, and cumulative rollup selects it
  through the existing `last` fold before relabeling the display bucket.
- Require compare inputs to carry `evaluation_end`; copy the paired values into
  the two always-materialized delta endpoint columns without reconstructing or
  compacting them.
- Materialize one full-business-coordinate pair-key set for scalar, segmented,
  time-series, and panel shapes; apply it identically to the parent delta and
  component sidecars before persistence.
- Add the minimal versioned cumulative-change marker, identity contribution,
  rendering, structured errors, recovery, and
  scalar/segmented/time-series/panel evidence extraction together. Evidence
  findings carry exact cutoff instants, not display labels.
- Keep source-revision verification outside this design. Do not persist provider
  snapshot tokens or reuse the existing result-value snapshot fingerprint as
  source-state proof.
- Update the capability registry, live help, active analysis specs, EN/CN site
  docs, and `marivo-analysis` skill only where they currently teach the blanket
  rejection.

## Acceptance Criteria

- Scalar, segmented, time-series, and panel direct all-history comparisons
  produce current-minus-baseline level changes with exact timezone-aware cutoff
  evidence. Every delta row persists the same two canonical endpoint columns;
  there is no shared-metadata storage alternative or recovery-time expansion.
- Direct and structurally complete derived cumulative `MetricFrame` rows carry
  one canonical `evaluation_end` across all anchors. Row-preserving transforms
  retain it, and cumulative rollup chains select and propagate it through the
  complete `last` input row. The coordinate does not change semantic shape,
  requested axes, or value-column discovery.
- A partial final bucket records `window.end`, not the later bucket boundary;
  direct, derived, rolled, chained-rollup, persisted, and recovered artifacts
  retain the same exact instant.
- A cumulative `count_distinct` regression proves that the result is not
  mislabeled or repaired as interval distinct count.
- Homogeneous derived all-history compare preserves component sidecars; mixed
  or incomplete wrappers still fail before backend work.
- One-sided segments, time tails, and panel business coordinates never enter the
  parent or component delta. Metadata, `.show()`, and evidence agree on the
  dropped counts and parent/component pair keys.
- Matched rows with a null current or baseline level remain matched, retain a
  null delta, and increment `matched_null_rows` without being mislabeled as new,
  churned, or unpaired.
- Forward, reverse, equal, and mixed endpoint ordering computed from the two
  endpoint columns is deterministic before and after recovery.
- Evidence findings for all four semantic shapes expose the same exact cutoff
  pair as the recovered DeltaFrame.
- Results consistently state that source revision is unverified. No provider
  snapshot token or result-value snapshot fingerprint is persisted or interpreted
  by this design as source-state proof.
- `DeltaFrame.contract()` does not duplicate immutable interpretation facts on
  every affordance; it adds a typed precondition only when a specific downstream
  capability depends on that fact.
- Focused `analysis.compare` help contains one runnable all-history success path
  through `.show()`, `.contract()`, and canonical endpoint reads, plus one
  structured incompatibility failure with expected, received, and real repair.
- A locally valid all-history `MetricFrame` exposes `compare` as available
  without any `conditional`, `pending`, or pair-compatibility precondition.
  Local blockers remain typed failures with real repairs; pair incompatibilities
  are reported only by `session.compare(...)` after both inputs are present.
- Help, errors, frame guidance, active specs, site docs, exports, and tests make
  no claim that all-history level change is unconditionally base flow.
