# Cumulative Metrics

Use `ms.cumulative(...)` for all-history running totals. Prefer explicit `over=`.

Authoring path:

1. Define or pick the base tier-1 metric with `ms.aggregate(...)` or `ms.count(...)`.
2. Ensure the base aggregation is `sum`, `count`, or `count_distinct`.
3. Pass the business accumulation time axis with `over=`.
4. Compose ratios from cumulative components when the business question is cumulative rate.

Do not use cumulative over `mean`, percentile, expression-body metrics, or derived metrics.
For a cumulative mean-like quantity, model cumulative numerator and denominator separately and
compose them with `ms.ratio(...)`.
