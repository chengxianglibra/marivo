# Cumulative Frames

Cumulative MetricFrames store running-total or rolling-window values whose semantics
depend on the accumulation anchor (`all_history`, `grain_to_date`, or `trailing`).

`show()`, `contract()`, and `transform.window(...)` work normally on cumulative frames.
`contract()` and `show()` surface the anchor-specific caveat, so the allowed path is
visible at the frame you are reading.

## Allowed

- `show()`, `contract()`, `transform.window(...)`
- `transform.rollup(grain=...)` — re-buckets the time axis to a coarser grain. Cumulative
  frames carry `rollup_fold="last"`, so each period contributes its last bucket (the
  period-end value). At least one of `drop_axes` or `grain` is required.
- `compare` is anchor-dispatched (see below).
- `correlate`, `discover`, `assess_quality`, `derive`, and `hypothesis_test` are allowed.
  Trailing frames are independent windowed aggregations (not running totals), so these
  intents produce meaningful results. The monotonic-trend caveat applies only to
  `all_history` and `grain_to_date` anchors.

## compare — anchor-dispatched

- `all_history`: rejected. Observe the base flow metric and compare that — a cumulative
  delta over a window equals the base total over that window.
- `trailing`: allowed when both frames share the same trailing anchor payload
  (`count`, `unit`). The windowed rolling values align ordinally.
- `grain_to_date`: allowed for a single-period, boundary-anchored window. The window must
  start on a reset boundary and span at most one reset period, and both frames must share
  the reset grain and query grain. The resulting DeltaFrame records the to-date alignment
  under `alignment_dump["to_date"]` (`reset_grain`, `matched_buckets`,
  `baseline_tail_buckets`).
- Derived frames containing cumulative components stay compare-gated regardless of anchor.

## Rejected

- `attribute` and `forecast` — reject cumulative frames regardless of anchor. Re-observe
  the base flow metric for these intents.
- `transform.rollup(...)` on frames that are neither re-aggregatable nor carrying a
  `rollup_fold` — rejected with a teaching error. Re-observe at the target grain instead.

Use the base flow metric for rejected intents.
