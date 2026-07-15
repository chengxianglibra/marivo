# Clean One-Object Readiness Case

You are working in a Marivo project that already has a configured DuckDB
datasource (`warehouse`) and a checked-in semantic layer. Your task is to
author exactly one semantic object and reach scoped readiness for it.

## Business Goal

The project needs a `revenue` metric ready for analysis handoff. The metric
is evidence-settleable: the datasource, grain, and expression are all
mechanically discoverable from the existing physical source.

## Requirements

1. Use `ms.help()` and `md.help()` (or the equivalent CLI help) to discover
   the semantic and datasource surfaces before making any authoring decision.
2. Establish that the help fingerprint matches the execution environment
   before proceeding.
3. Inspect the physical source, choose an explicit live-declared scope with
   positive guards, and acquire one snapshot.
4. Author exactly one explicit Python semantic object (the `revenue` metric).
5. Reload the catalog, locate that exact typed object, run static
   verification, run the scoped preview, and run readiness.
6. The final outcome must be a readiness status of `ready` or
   `ready_with_warnings` for exactly one authored object.

## Constraints

- Author exactly one object. Do not author a batch.
- Use only the allowed live help, semantic, artifact, and structured-error
  surfaces for contract discovery.
- Do not use native reflection (e.g. `dir()`, `inspect.getmembers`) to
  discover Marivo API contracts.
- Do not write raw SQL against the datasource as an executable expression
  body. SQL text only belongs in provenance metadata.
- Do not browse the web or consult external documentation.
- Do not rely on deleted skill attachments or source-checkout files.
