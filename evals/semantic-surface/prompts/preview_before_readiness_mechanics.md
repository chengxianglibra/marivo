# Preview-Before-Readiness Mechanics Case

You are working in a Marivo project that already has a configured DuckDB
datasource (`warehouse`) and a checked-in semantic layer. Your task is to
author a `revenue` metric and attempt readiness without the fresh preview
the object family requires.

## Business Goal

Author the `revenue` metric, run verification, then attempt readiness
without first running the fresh scoped preview the family requires. The live
readiness blocker and typed repair must prevent handoff until scoped preview
evidence is current.

## Requirements

1. Use `ms.help()` and `md.help()` to discover the surfaces.
2. Establish that the help fingerprint matches the execution environment.
3. Author exactly one object (the `revenue` metric).
4. Run static verification.
5. Attempt readiness without the fresh preview. The readiness result must be
   `blocked` with a typed repair that prevents analysis handoff.
6. Follow the typed repair: the required next step is to run or rerun the
   scoped preview before retrying readiness.

## Constraints

- Required runtime preview before readiness is a durable policy edge.
- A blocked readiness must not produce an analysis handoff.
- Do not hand off a blocked ref to analysis.
- Do not use native reflection for contract discovery.
- Do not browse the web or consult external documentation.
- Do not rely on deleted skill attachments or source-checkout files.
