# Cumulative Anchors V2 — Grill Points

Three grill points surface the decisions an agent must settle before authoring a v2
cumulative metric. Each is a one-question grill with evidence-grounded options.

## 1. Reset-grain choice (grain_to_date)

**Question:** Which reset grain matches the business cadence?

The reset grain determines where the running total drops back to the period's first-bucket
flow. Pick the coarsest grain that still answers the business question — finer grains
reset more often and hide longer trends.

| Option | Resets at | Use when |
| --- | --- | --- |
| `week` | start of each week | weekly operational cadence; WTD |
| `month` | start of each month | monthly business review; MTD (most common) |
| `quarter` | start of each quarter | quarterly board reporting; QTD |
| `year` | start of each year | annual cumulative; YTD |

Evidence to surface: the metric's `domain` / `ai_context`, the user's stated reporting
cadence, and the query grain the analysis will use.

Grain-compatibility gate: a `week` query grain under a `month` / `quarter` / `year` reset
is illegal (week buckets straddle month boundaries). `day` and `hour` are always legal. A
`month` grain under a `month` reset is legal and meaningful (each bucket is the
full-period total).

If the user wants a sliding multi-period window (e.g. "rolling 3 months"), that is NOT
grain_to_date — point them to `ms.trailing(count=..., unit="day")` with a fixed-day span,
since calendar-variable trailing units are rejected.

## 2. Window-span caliber (trailing)

**Question:** What window length and unit, and is the span an integer multiple of the
query grain?

The trailing span is `count` × `unit`. Two caliber decisions:

- **Span vs. grain alignment.** The span must be an integer multiple of the query grain
  (`W_buckets = span / grain`). A 7-day trailing at a `day` grain is 7 buckets; a 7-day
  trailing at an `hour` grain is 168 buckets. If the user's desired span is not an integer
  multiple of the grain they plan to observe at, settle the grain first.
- **Fixed-size unit only.** `unit` accepts `second`, `minute`, `hour`, `day`, `week`.
  Calendar-variable units (`month`, `quarter`, `year`) are rejected because their length
  varies. For a sliding-months reset, use `grain_to_date(grain="month")`; for a
  fixed-length month window, use `trailing(count=..., unit="day")`.

Evidence to surface: the analysis `grain` the agent will observe at, the user's stated
"rolling N" intent, and whether the span must align to query buckets.

Trailing requires a time grain. For a windowed scalar (single value over a window), use a
plain `session.observe(...)` with a `time_scope` window instead of a cumulative metric.

## 3. Partial-window explanation (trailing)

**Question:** How should buckets whose window reaches before the data start be reported?

Trailing windows are fixed-size: the value at each bucket is the aggregation over the span
ending at that bucket's end boundary. When the span reaches before the data start, the
window is partial — it covers fewer than `count` × `unit` of actual data.

| Behavior | Detail |
| --- | --- |
| Value | the actual partial accumulation over the available data (not zero, not NaN) |
| Coverage mark | `partial` in the frame's coverage; `window_coverage` kind for trailing |
| Empty windows | true zero (no activity in the span), not carried forward |

This is MetricFlow-compatible: partial windows show the real partial value, never a silent
carry-forward or a gap. The coverage mark makes the partiality visible to the agent and
the user.

Evidence to surface: the frame's `coverage` / `coverage_summary`, and whether the user's
reporting context tolerates partial windows or needs them called out explicitly.

Do NOT grill this for `all_history` or `grain_to_date` anchors — they carry forward empty
buckets and have no partial-window concept. Partial-window semantics are trailing-only.
