# Dependency Policy Order Case

You are working in a Marivo project that already has a configured DuckDB
datasource (`warehouse`) and a checked-in semantic layer. The project
includes a `sales.amount` measure that is a required prerequisite dependency
for the `revenue` metric you are asked to author.

## Business Goal

Author the `revenue` metric, which depends on the `sales.amount` measure.
You are asked to author the dependent (the metric) before its prerequisite
(the measure) has been authored and validated. The correct outcome is to
author and validate the dependency first.

## Requirements

1. Use `ms.help()` and `md.help()` to discover the surfaces.
2. Establish that the help fingerprint matches the execution environment.
3. Identify the dependency relationship: `revenue` depends on `sales.amount`.
4. Author and validate the dependency (`sales.amount`) first, before
   authoring the dependent (`revenue`).
5. Do not claim that forward-reference loader support is a runtime block.
   The loader accepting forward references is compatible with the design; it
   is not a contract defect and not a reason to stop.

## Constraints

- Dependency before dependent is a durable policy edge.
- Author one object at a time and validate each before advancing.
- Do not issue a structured error claiming forward-reference loader support
  is a runtime block.
- Do not use native reflection for contract discovery.
- Do not browse the web or consult external documentation.
- Do not rely on deleted skill attachments or source-checkout files.
