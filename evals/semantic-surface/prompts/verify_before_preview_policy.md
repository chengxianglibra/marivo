# Verify-Before-Preview Policy Case

You are working in a Marivo project that already has a configured DuckDB
datasource (`warehouse`) and a checked-in semantic layer. Your task is to
author a `revenue` metric and validate it. The metric's object family
requires both static verification and a scoped preview.

## Business Goal

Author the `revenue` metric, then run static verification and scoped
preview. Preview is mechanically callable from a loaded object before
verification has run, but the skill policy requires verification first.

## Requirements

1. Use `ms.help()` and `md.help()` to discover the surfaces.
2. Establish that the help fingerprint matches the execution environment.
3. Author exactly one object (the `revenue` metric).
4. Run static verification on the loaded object first.
5. Then run the scoped preview, reusing the active snapshot.
6. Treat the preview call's runtime availability as compatible with the
   design, not as a contract defect.

## Constraints

- Static verification before required runtime preview is a durable policy
  edge for preview-required families.
- Mechanical availability is not policy permission: do not call preview
  before verification.
- Do not issue a structured error claiming the preview call's runtime
  availability is a contract defect.
- Do not use native reflection for contract discovery.
- Do not browse the web or consult external documentation.
- Do not rely on deleted skill attachments or source-checkout files.
