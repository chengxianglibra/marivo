# Cumulative Metrics

Use `ms.cumulative(...)` for running-total and rolling-window metrics. Prefer explicit `over=`.

The `anchor=` parameter selects the accumulation shape:

- `anchor=None` (default) — all-history running total. The observe window clips displayed
  rows but does not reset the running value. Empty buckets carry forward.
- `anchor=ms.grain_to_date(grain=...)` — resets at each reset-grain boundary
  (MTD / QTD / YTD / WTD). `grain` is one of `week`, `month`, `quarter`, `year`.
- `anchor=ms.trailing(count=..., unit=...)` — fixed-size rolling window. Empty windows
  are true zero; partial windows (span reaches before data start) show the actual partial
  accumulation and are marked `partial` in coverage. `unit` is fixed-size only
  (`second`, `minute`, `hour`, `day`, `week`); calendar-variable units are rejected.

Authoring path:

1. Define or pick the base tier-1 metric with `ms.aggregate(...)` or `ms.count(...)`.
2. Ensure the base aggregation is `sum`, `count`, or `count_distinct`.
3. Pass the business accumulation time axis with `over=`.
4. Choose the anchor: omit for all-history, `ms.grain_to_date(...)` for period resets,
   `ms.trailing(...)` for a rolling window.
5. Compose ratios from cumulative components when the business question is cumulative rate.

Do not use cumulative over `mean`, percentile, expression-body metrics, or derived metrics.
For a cumulative mean-like quantity, model cumulative numerator and denominator separately and
compose them with `ms.ratio(...)`.

## Derived compare boundary

A derived metric over cumulative components can compare only when every outer component is
cumulative and all components use exactly the same `trailing` or `grain_to_date` anchor.
`all_history`, mixed anchors, cumulative/non-cumulative mixes, and unresolved anchors are hard
rejections. `attribute`, `decompose`, and `forecast` remain unsupported for the resulting
cumulative delta; operate on the underlying flow components instead.

## Cross-anchor constraints

- **Grain-compatibility rule** (`grain_to_date`): every display bucket must lie entirely
  within one reset period. A `week` query grain under a `month` / `quarter` / `year` reset
  is illegal (week buckets straddle month boundaries); `day` and `hour` are legal. A
  `month` grain under a `month` reset is legal and meaningful (each bucket is the
  full-period total, i.e. the period-end value).
- **Integer-multiple rule** (`trailing`): the window span must be an integer multiple of
  the query grain (`W_buckets = span / grain`). Trailing requires a time grain; for a
  windowed scalar use a plain `session.observe(...)` window instead.

## Examples

```python
# All-history running total (v1 default)
cumulative_active_users = ms.cumulative(
    name="cumulative_active_users", base=active_users, over=event_time,
)

# Month-to-date revenue
mtd_revenue = ms.cumulative(
    name="mtd_revenue", base=revenue, over=event_time,
    anchor=ms.grain_to_date(grain="month"),
)

# Rolling-7d active users
rolling7_active = ms.cumulative(
    name="rolling7_active", base=active_users, over=event_time,
    anchor=ms.trailing(count=7, unit="day"),
)
```

For deeper grill guidance on anchor choice, see `cumulative-anchors-v2.md`.
