# Test goals and execution gates

This guide owns test selection and coverage organization. Architecture and
product contracts live in [current specs](../README.md#current-specs).
Tests follow the behavior they protect; Runtime and release markers select
execution cost and environment independently of the owning directory.

## Goal ownership

| Behavior | Owning tests | Gate |
| --- | --- | --- |
| Datasource declarations, source IR, metadata, credentials, SQL submission, native deadlines and cleanup | [datasource](../../tests/datasource/) | Daily contracts; Runtime native reads |
| Semantic declarations, identity, metric equations, authoring, validation and analysis handoff | [semantic](../../tests/semantic/) | Daily contracts; Runtime source consumers |
| Typed graph rules, capture, lowering, admission and planning | [analysis/graph](../../tests/analysis/graph/) | Daily; Runtime public execution |
| Observation, aggregation, comparison, attribution and selection | [analysis/numeric](../../tests/analysis/numeric/) | Daily oracles; Runtime public behavior |
| Time grids, timezones, windows and temporal folds | [analysis/temporal](../../tests/analysis/temporal/) | Daily kernels; Runtime source/fixed execution |
| Journey matching, Duration, Funnel, anchors and retention | [analysis/journey](../../tests/analysis/journey/) | Daily oracles; Runtime public/process boundaries |
| History replay, completeness, views and captured observations | [analysis/lifecycle](../../tests/analysis/lifecycle/) | Daily oracles; Runtime source-free continuation |
| Deviation, association and forecast | [analysis/statistics](../../tests/analysis/statistics/) | Daily numerical oracles; Runtime composed execution |
| Artifact reads, deadlines, atomic publication, crash recovery and cold ownership | [analysis/materialization](../../tests/analysis/materialization/) | Daily contracts; Runtime process/resource boundaries |
| Session ownership and persistence | [analysis/session](../../tests/analysis/session/) | Daily; Runtime source integration |
| Public exports, Help budgets, result/error guidance and documentation examples | [surface](../../tests/surface/) | Daily; executable examples use Runtime |
| Config, refs, CLI, doctor, preview, telemetry and Make routing | [project](../../tests/project/) | Daily |
| Callable/type contracts | [typing](../../tests/typing/) and surface typing probes | Typecheck; daily rejected calls |
| Package layout, installers, installed origin and recovery | [packaging](../../tests/packaging/) | Daily archive/resource contracts; release installation; opt-in native sources |

Fixtures and suite policies live in `tests/conftest.py`; reusable pure builders
live in `tests/shared_fixtures.py`. Focused helpers live with their behavior;
shared infrastructure lives in `tests/support/`. Tests and workers do not
import helpers from test modules.

## Coverage selection

- Use independent numerical or behavioral oracles, including rejected inputs.
- Share ordinary behavior through representative local Parquet paths. Retain
  native cases where compilation, precision, physical keys or resource ownership
  introduces a distinct risk; avoid backend/storage Cartesian replay.
- Keep source, source-offline continuation and fresh-process recovery boundaries
  explicit. In-process checks do not replace process or crash witnesses.
- Installed-package checks establish candidate archive integrity, noneditable
  import origin and public recovery separately from development-suite coverage.

Local trust and publication behavior follow the
[Runtime contract](../specs/analysis/session-state-and-runtime.md#store-9-publication-and-local-trust).
Test producer validation, malformed/unreadable inputs, missing payloads,
ownership, atomicity and recovery at their owning boundary.

## Commands and evidence

- `make test`: complete daily selection.
- `make test TESTS='tests/semantic/test_metric_roots.py'`: one behavior target.
- `make runtime-test TESTS='tests/analysis/materialization/test_numeric_recovery.py'`: one process boundary.
- `make check-agent`: full daily tests, lint/import contracts, typing and API docs.
- `make release-test`: candidate archives, installers and installed-package checks.
- `make release-check`: full daily, Runtime and packaging gates for release preparation.
- `MARIVO_INSTALLED_MULTISOURCE_TEST=1 make installed-multisource-test`: selected existing native services; see [source environment setup](../../tests/datasource/environment/README.md).

Daily selection excludes `runtime` and `release`. Runtime selects `runtime`;
release targets run serially with `release`. Direct pytest needs explicit
`-m runtime`; a file name does not override default marker exclusion. Runtime
Make targets default to two workers; a selected node containing `::` runs serially.

Installed tests normally read candidates from `dist/pypi`.
`MARIVO_TEST_WHEEL_DIR` selects an isolated build directory. Candidate validation
compares package source and resource hashes with the current checkout before
installation.

[Analysis disclosure context budgets](analysis-help-context.md) have a focused
reproduction path. Unavailable services are skipped or unverified. Daily,
Runtime, installed-package and real-Agent evidence remain distinct; a focused
check does not qualify an unrun backend or the full release gate.
