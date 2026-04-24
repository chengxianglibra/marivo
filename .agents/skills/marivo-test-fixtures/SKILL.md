---
name: marivo-test-fixtures
description: Use when adding or modifying Marivo tests, shared fixtures, DuckDB templates, metadata SQLite templates, intent API tests, or slow test setup.
---

# Marivo Test Fixtures

Use this skill when working on tests, `tests/shared_fixtures.py`, DuckDB templates, metadata SQLite
setup, or intent API test performance.

## Test entrypoints

- Use `make test` or `.venv/bin/pytest`; never use bare `pytest`.
- Prefer `make test TESTS='tests/test_file.py'` for targeted runs through the repository entrypoint.
- Tests require Python 3.12+ and use SQLite metadata plus DuckDB/Trino engines.
- Tests pass explicit `db_path` and metadata store/file paths directly.

## Shared fixture rules

- Prefer `tests/shared_fixtures.py` named DuckDB templates for repeated test data.
- When multiple test classes need the same analytics tables, build a deterministic named template once
  and copy it into each temporary db path instead of re-seeding in every `setUpClass`.
- Bump a named template version string when its seeded schema or rows change so cached `/tmp` copies
  rebuild automatically.
- Add repeated intent bridge/import tables to a named DuckDB template instead of creating and
  repopulating them inside each test class setup.

## Metadata SQLite rules

- Fresh SQLite metadata stores are initialized from the cached empty schema template in
  `tests/shared_fixtures.py`.
- Marivo only supports fresh-init for metadata SQLite; after schema changes, delete old metadata files
  and rebuild them from the current schema/template.
- Bump the metadata template version when metadata DDL changes.
- If metadata contract changes alter columns inside an existing table, update the template validator;
  table-name checks alone are not sufficient.
- Keep metadata template build on the minimal DDL/shape-validation path; do not route it through
  heavier initializer or migration logic unless the contract requires it.

## Intent API test performance

- Prefer class-level reuse of published semantic objects and seeded upstream artifacts.
- For compare/correlate-style tests that only need committed upstream artifacts, seed minimal artifact
  payloads directly instead of executing repeated observe setup queries.
- When an intent API test file covers multiple semantic scenarios, split them into scenario-specific
  classes so each `setUpClass` creates only the required metrics, dimensions, bindings, and tables.
- Do not add unit tests whose individual execution time exceeds 10 seconds; use shared fixtures,
  named DuckDB templates, or class-level `setUpClass` seeding for heavier setup.
