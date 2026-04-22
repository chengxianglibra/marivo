# Marivo Agent Memory

## Trigger handling

- Default to API/service/registry validation before adding SQLite triggers.
- Do not use SQLite triggers for constraints that are naturally request-level business validation, such as:
  - JSON payload duplicate checks
  - source update invariants that already depend on old/new semantic interpretation
- Only keep DB-level constraints when one of these is true:
  - the invariant cannot be expressed cleanly at the app layer
  - there are multiple uncontrolled write paths
  - corruption impact is severe enough that storage must fail closed

## Test-performance guidance

- Treat `tests/shared_fixtures.py` metadata-template build as a hot path.
- Do not route metadata-template build through full `SQLiteMetadataStore.initialize()` unless absolutely necessary.
- Prefer:
  - `METADATA_DDL` direct build for seeded metadata templates
  - minimal validator checks on required tables/columns
  - app-layer tests for request validation semantics
- If a schema change adds triggers only for app-facing validation, avoid validating trigger presence in the shared metadata template.

## Lessons from this change

- The synthetic-catalog immutability rule is better expressed in `SourceRegistry.update_source()` than in SQLite trigger logic.
- JSON-array uniqueness inside `catalog_mappings_json` should stay out of SQLite triggers unless mappings become a first-class write surface and there is a strong need for storage-layer fail-closed behavior.
- When adding test safeguards, check whether they expand shared-template initialization cost; a small correctness change there can significantly slow the full suite.
