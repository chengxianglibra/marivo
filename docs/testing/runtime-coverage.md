# Test goals and execution gates

This guide owns test selection and coverage organization. Architecture and
product contracts live in [current specs](../README.md#current-specs).
Tests follow the behavior they protect. Daily tests need no external datasource
service; Runtime tests require an already-running service. Release tests own
package installation and installed-origin boundaries.

## Goal ownership

| Behavior | Owning tests | Gate |
| --- | --- | --- |
| Datasource declarations, source IR, metadata, credentials, SQL submission, native deadlines and cleanup | [datasource](../../tests/datasource/) | Fast daily contracts; extended analysis/resume cases in slow-test; Runtime native reads |
| Semantic declarations, identity, metric equations, authoring, validation and analysis handoff | [semantic](../../tests/semantic/) | Daily authoring and local handoff; Runtime native metadata and sample reads |
| Typed graph rules, capture, lowering, admission and planning | [analysis/graph](../../tests/analysis/graph/) | Fast daily checks; extended local execution in slow-test; Runtime native SQL differences |
| Observation, aggregation, comparison, attribution and selection | [analysis/numeric](../../tests/analysis/numeric/) | Fast daily representatives; extended oracles in slow-test; Runtime native carriers |
| Time grids, timezones, windows and temporal folds | [analysis/temporal](../../tests/analysis/temporal/) | Fast daily representatives; extended execution and recovery in slow-test; Runtime native time semantics |
| Journey matching, Duration, Funnel, anchors and retention | [analysis/journey](../../tests/analysis/journey/) | Fast daily representatives; extended journeys and process recovery in slow-test; Runtime native matching |
| History replay, completeness, views and captured observations | [analysis/lifecycle](../../tests/analysis/lifecycle/) | Fast daily representatives; extended history and source-free continuation in slow-test |
| Deviation, association and forecast | [analysis/statistics](../../tests/analysis/statistics/) | Fast daily representatives; extended composed and recovered execution in slow-test |
| Artifact reads, deadlines, atomic publication, crash recovery and cold ownership | [analysis/materialization](../../tests/analysis/materialization/) | Fast daily representatives; extended publication and process recovery in slow-test; Runtime native cancellation |
| Session ownership and persistence | [analysis/session](../../tests/analysis/session/) | Fast daily representatives; extended local session checks in slow-test; Runtime native source integration |
| Public exports, Help budgets, result/error guidance and documentation examples | [surface](../../tests/surface/) | Fast public contracts and short examples; extended typing, process and execution examples in slow-test |
| Config, refs, CLI, doctor, preview and telemetry | [project](../../tests/project/) | Fast daily |
| Make command routing | [project](../../tests/project/) | slow-test |
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
- Run fast embedded DuckDB, SQLite, local-file and loopback HTTP checks in
  `make test`. Keep extended local source/fixed/cold journeys in `make slow-test`;
  a measured-fast analysis slice retains source/fixed behavior and representative
  cold recovery paths in the fast selection. A fresh Python process does not
  require a fresh datasource service.
- Keep source, fixed and cold assertions at their owning boundaries: source covers
  primary behavior and results, fixed covers follow-up operations, and cold
  keeps a few representatives for distinct persisted result types and recovery
  paths. Only measured-fast analysis modules join the unfiltered daily slice;
  longer execution and recovery matrices remain in `make slow-test`.
- Mark only actual external-service parameter cases as `runtime`; static native
  compilation, decoding and rejected-call checks remain daily. Shared matrices
  use `source_params()` to mark their service parameters individually.
- Share ready service containers across cases and invocations. Isolate mutable
  tables/schemas with UUID names and close each case's connections; tests clean
  their own objects. Service lifecycle stays outside pytest and Make test gates.
- Keep source, source-offline continuation and fresh-process recovery boundaries
  explicit. In-process checks do not replace process or crash witnesses.
- Installed-package checks establish candidate archive integrity, noneditable
  import origin and public recovery separately from development-suite coverage.

## Removed native replay and retained owners

| Removed repetition | Remaining coverage |
| --- | --- |
| Statistical methods and nine-method fixed/cold replay on every service | Local DuckDB/SQLite method and process oracles; source profiles own native decoding and stream cleanup |
| Reference, comparison, member validation and attribution business matrices on every service | Their local source/fixed and independent cold tests; native producer recovery owns source-offline and cold handoff per service |
| Separate native mean, distribution, reference, versioned, temporal and multiroot recovery journeys | Local recovery owners preserve their exact assertions; native producer recovery covers the shared service handoff |
| Ordinary multiroot arithmetic, slicing, aggregation and min/max replay on every service | Local full matrix; native Decimal precision/overflow and first/last ordering cases remain |
| Coordinate statistics, original component weighting and cumulative-window business replay on every service | Local full matrices; native numeric carriers and native reader/grid/timezone tests remain |

Native profiles, types, namespaces, engine-specific distribution placement,
Decimal behavior, native time semantics, permissions, deadlines, cancellation
and independent server termination remain Runtime owners. Runtime does not replay
the complete daily acceptance matrix. Removing generic service replay does not
establish qualification for an untested exact backend/method shape.

Local trust and publication behavior follow the
[Runtime contract](../specs/analysis/session-state-and-runtime.md#store-9-publication-and-local-trust).
Test producer validation, malformed/unreadable inputs, missing payloads,
ownership, atomicity and recovery at their owning boundary.

## Commands and evidence

- `make test`: fast daily selection, excluding extended cases marked `slow`.
- `make slow-test`: extended local acceptance cases excluded from `make test`.
- `make test TESTS='tests/semantic/test_metric_roots.py'`: one behavior target.
- `make slow-test TESTS='tests/analysis/materialization/test_numeric_recovery.py'`: local process recovery.
- `make runtime-test TESTS='tests/datasource/test_source_profiles.py'`: native source profiles.
- `make check-agent`: fast daily tests, lint/import contracts, typing and API docs.
- `make release-test`: candidate archives, installers and installed-package checks.
- `make release-check`: full daily, Runtime and packaging gates for release preparation.
- `MARIVO_INSTALLED_MULTISOURCE_TEST=1 make installed-multisource-test`: selected existing native services; see [source environment setup](../../tests/datasource/environment/README.md).

The fast daily selection excludes `slow`, `runtime` and `release`. On an
unfiltered run, remaining analysis nodes are classified as `slow`; a focused
unmarked analysis path can still be selected with `make test TESTS=...`.
`make slow-test` selects `slow` while excluding `runtime` and `release`. Runtime
selects `runtime`; release targets run serially with `release`. Direct pytest
needs an explicit marker expression to select these groups; a file name does
not override default marker exclusion. Runtime Make targets default to two
workers to bound shared-service load; a selected node containing `::` runs
serially.

Installed tests normally read candidates from `dist/pypi`.
`MARIVO_TEST_WHEEL_DIR` selects an isolated build directory. Candidate validation
compares package source and resource hashes with the current checkout before
installation.

[Analysis disclosure context budgets](analysis-help-context.md) have a focused
reproduction path. Unavailable services are skipped or unverified. Daily,
Runtime, installed-package and real-Agent evidence remain distinct; a focused
check does not qualify an unrun backend or the full release gate.
`make release-check` includes both daily selections.
