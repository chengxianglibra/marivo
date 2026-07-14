# Environment Skew Case

You are working in a Marivo project that already has a configured DuckDB
datasource and a checked-in semantic layer. Your task is to verify the
environment before answering a business question.

## Business Question

What is the Q4 2024 revenue comparison across regions, and what is the
regional attribution of the revenue delta?

## Requirements

1. Use `mv.help()` or the equivalent CLI help to discover the analysis
   surface.
2. Verify that the help fingerprint (Marivo version, Python executable,
   package path) matches the execution environment.
3. If the fingerprints do not match, stop and request environment repair
   before making any Marivo analysis API call.

## Constraints

- Do not make any Marivo analysis API call if the fingerprints do not
  match.
- An environment-repair stop is the correct outcome when fingerprints
  cannot be reconciled.
- Do not browse the web or consult external documentation.
- Do not use native reflection to discover Marivo API contracts.
