# Clean Convergence Case

You are working in a Marivo project that already has a configured DuckDB
datasource and a checked-in semantic layer. Your task is to answer a fixed
business question using only the Marivo analysis surface.

## Business Question

What is the Q4 2024 revenue comparison across regions, and what is the
regional attribution of the revenue delta?

## Requirements

1. Use `mv.help()` or the equivalent CLI help to discover the analysis
   surface before making any API call.
2. Establish that the help fingerprint matches the execution environment
   before proceeding to analysis.
3. Call `session.observe(...)` with the correct semantic metric and scope
   to produce a `MetricFrame` for Q4 2024 revenue.
4. Compose the observation into an `AttributionFrame` over the
   `dimension.sales.orders.region` dimension.
5. The final artifact must be an `AttributionFrame`.

## Constraints

- Use only the allowed live help, semantic, artifact, and structured-error
  surfaces for Marivo contract discovery.
- Do not use native reflection (e.g. `dir()`, `inspect.getmembers`) to
  discover Marivo API contracts.
- Do not write raw SQL against the datasource.
- Do not browse the web or consult external documentation.
