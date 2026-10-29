---
name: marivo-test-fixtures
description: Design, review, modify, or optimize Marivo tests and fixtures. Use for behavioral coverage, independent expected results, validation scope, shared setup, and test isolation.
---

# Marivo Test Fixtures

Use this skill for test design, validation selection, fixtures, process boundaries
and isolation. Follow [AGENTS.md](../../../AGENTS.md) and
[CONTRIBUTING.md](../../../CONTRIBUTING.md) for repository commands and gates.

## Choose the behavior before the fixture

Apply the test admission criteria in [CONTRIBUTING.md](../../../CONTRIBUTING.md#测试要求):
identify the current contract, an incorrect implementation the test would detect,
an independent expected result, the smallest real boundary, and the value beyond
existing coverage. Ordinary cases express these through names, data and assertions;
they do not need a separate ledger or evidence bundle. Expand an existing owning
test when it already reaches the required boundary.

Reuse input builders while keeping expected results independent of production logic.
Doubles may control external I/O or inject faults; they must not replace the calculation
being tested. Follow the current native arithmetic contract: backend, pandas/Arrow and
Decimal kernels may have different valid results or failures. Do not require protective
widening, extra scans or retries unless the owning contract requires them. Preserve
explicit NULL, identity, time, unit and storage guarantees.

The tracked [test quality standard](../../../docs/testing/test-quality.md) provides
detailed criteria. The local, Git-ignored [coverage map](../../../docs/testing/runtime-coverage.md)
is optional evidence of test ownership; it does not define the current contract.

## Select the validation boundary

Choose according to the failure being exposed, then apply the gates in `AGENTS.md`:

| Changed risk | Useful evidence |
| --- | --- |
| Local expression, numeric or validation rule | Focused local behavior with an independent expected result |
| Engine SQL, physical types or provider resource handling | Relevant Runtime cases on each affected backend |
| Persistence, source-offline continuation or process ownership | The affected producer/read path and a fresh process where independence matters |
| Public API or guidance | Affected exports, Help, result/error guidance and current EN/ZH examples |
| Installation or release packaging | Owning installed-package checks; full release acceptance during release preparation |

Use known interaction risks to choose combinations; do not multiply every functional
case across backends, storage adapters and recovery modes. Shared fixture or behavior
changes still require the broad regular gate. Reuse passing evidence while its relevant
inputs remain unchanged; in team work, the coordinator runs the final broad gate once
for the integrated candidate rather than every developer repeating it.

## Test entrypoints

- Use `make test TESTS='...'` for focused local cases and `make runtime-test TESTS='...'`
  for external-service cases. A filename does not override marker selection; direct
  `.venv/bin/pytest` calls need the appropriate `-m` expression.
- Default tests include `slow` cases unless also marked `release` or `runtime`.
  Mark service parameters individually with `source_params()` in mixed local/remote matrices.
- Use `make release-test` for release-marked installation and packaging checks.
  Full release acceptance is separate from ordinary development and commit preparation.
- Use the supported Python range in `pyproject.toml` and the release CI matrix;
  do not assume the local interpreter is the minimum supported version.

## Fixture ownership

- Put shared pytest fixtures and suite-wide policies in `tests/conftest.py`; keep module-only
  fixtures with their tests.
- Put reusable pure builders, context managers, deterministic data generators, and template builders
  in `tests/shared_fixtures.py`.
- Keep helpers used by only one test module next to that module. Promote them only after a second real
  caller appears.
- Keep setup limited to the schema, expressions and capabilities needed by the assertion.
  An unrelated reducer or time contract can reject before the intended behavior is reached.
- Do not import fixtures from `tests.conftest`; request fixtures by parameter name. Move reusable
  non-fixture helpers into `tests/shared_fixtures.py` or a focused helper module.
- Prefer pytest fixtures over new `unittest.TestCase.setUpClass` infrastructure. Preserve existing
  unittest style only when a narrow change does not justify migration.

## Parallel and state isolation

- Assume different xdist workers may build or read the same cache concurrently.
- Build shared file templates through a unique temporary path and publish them atomically with
  `os.replace`. For directory templates, build a complete unique directory before a single rename.
- Treat shared templates as immutable. Copy them into each test's `tmp_path`, or attach them read-only
  before copying data into an isolated in-memory connection.
- Never share mutable DuckDB connections, SQLite connections, Marivo sessions, environment variables,
  current working directories, or process-global registries across workers.
- Preserve the suite-wide DuckDB single-thread policy in `tests/conftest.py`.
  Isolate Session state explicitly rather than relying on a global reset fixture.
- Isolate process-global registries and caches between tests within the same worker as well.
  Prefer fixture-scoped monkeypatching with automatic restoration; fresh `tmp_path` directories do
  not isolate in-memory state. Keep separate fresh-process tests for proof that must not survive exit.
- Design fixtures for xdist concurrency. Make worker limits apply per invocation;
  account for nested processes and simultaneous test runs before increasing them.

## Template admission and lifecycle

- Add a named template only when repeated setup is measured or clearly material across multiple real
  callers. A template without adopted call sites is dead infrastructure.
- Give each template an explicit version in its cache key. Bump the version whenever seeded schema,
  rows, project files, or initialization semantics change.
- Cover both cold-cache construction and warm-cache reuse when adding non-trivial template logic.
- Validate the schema or shape that consumers depend on; existence-only checks are insufficient.
- Delete superseded template builders and duplicated setup once callers have migrated.

## Runtime coverage boundaries

- Use representative local files for ordinary analysis and recovery. Retain native cases
  where engine SQL, physical types, resource ownership or process recovery is the risk.
  Do not replay every functional case across storage adapters.
- Reuse already-running external service containers across cases and invocations. Keep UUID-named
  tables/schemas, connections and cleanup owned by each case. Pytest never starts or stops services;
  follow the [environment README](../../../tests/datasource/environment/README.md)
  for lifecycle and profile selection.
- Reuse an already-collected DataFrame within a test phase for numeric assertions and evidence.
  `AnalysisFrame.to_pandas()` collects complete rows in the caller; assertion and serialization
  helpers should accept the collected DataFrame rather than silently reading the AnalysisFrame again.
  Keep separate reads when they explicitly test reader behavior, integrity, or cold ownership.
- Keep producer, source-offline continuation, and cold binding in separate processes when that
  independence is the assertion. Cover numerical variants in focused tests instead of replaying the
  full journey for every combination. Do not substitute a warm in-process cache for cold recovery.
- Before removing cases, identify each unique assertion and its retained owner. Preserve
  distinct native, large-input, corruption and process-loss witnesses. Update routing
  checks when markers or gates change, and the local coverage map when available.

## Performance workflow

Read [the profiling guide](references/runtime-performance.md) when diagnosing slow
tests or concurrency failures. Measure before adding caches or broadening fixture
scope; preserve the behavior and process boundaries being measured. Reuse immutable
prepared dependencies where useful, but do not hide failures by relaxing timeouts,
assertions or marker selection.

## Persistence and cleanup

- Give each test an explicit project root and explicit database/store paths under `tmp_path`.
- Initialize current SQLite/session schemas through the owning production constructor unless the test
  explicitly targets lower-level DDL or corruption handling. Do not invent a cached metadata template
  that production does not own.
- Seed persisted data directly only for downstream read contracts; producer tests must
  reach the production path they claim to validate.
- Clean up connections, temporary build paths, environment changes, cwd changes, and process-global
  state even when assertions fail. Prefer fixtures or context managers with `yield`/`finally`.

## Verification

For documentation-only changes, check content, links, consistency and diffs without
claiming new behavioral evidence.

For fixture changes, run focused behavior and relevant xdist/process-isolation checks;
new shared templates also need cold/warm coverage. Format only changed Python files.
Shared setup changes require the broad regular gate and affected Runtime cases.
Reuse passing evidence for unchanged files during commit preparation.
