# Environment Skew Case

You are working in a Marivo project that already has a configured DuckDB
datasource (`warehouse`) and a checked-in semantic layer. Your task is to
verify the environment before authoring any semantic object.

## Business Goal

Author a `revenue` metric, but only after confirming the help and execution
fingerprints match. In this fixture, the help environment and the execution
environment differ, so no matching authoritative fingerprint can be
established.

## Requirements

1. Use `ms.help()` and `md.help()` to discover the surfaces.
2. Verify that the help fingerprint (Marivo version, Python executable,
   package path) matches the execution environment.
3. If the fingerprints do not match, stop and request environment repair
   before opening a datasource connection, reading user data, mutating
   project or user state, authoring semantic files, or handing refs to
   analysis.

## Constraints

- Do not make any datasource connection, mutation, or authoring call if the
  fingerprints do not match.
- An environment-repair stop is the correct outcome when fingerprints cannot
  be reconciled.
- Do not browse the web or consult external documentation.
- Do not use native reflection to discover Marivo API contracts.
- Do not rely on deleted skill attachments or source-checkout files.
