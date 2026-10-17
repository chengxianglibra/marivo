# Test goals and execution gates

This guide owns test selection and coverage organization. Architecture and
product contracts live in [current specs](../README.md#current-specs).
Tests follow the behavior they protect. Daily tests need no external datasource
service; Runtime tests require an already-running service. Release tests own
package installation and installed-origin boundaries.

At `405bd006f8`, the Lazy DSL's agreed local implementation gate and independent
business acceptance are complete. The numerical Query path now qualifies the actual
backend, expression, types and method, and propagates native pandas numerical
types into local computation and output schemas. Remote coverage owners and evidence
criteria are listed [below](#remote-numerical-query-extension); code admission and datasource
access tests do not themselves qualify a public Query route. The complete slow
suite and release gates remain separate; focused local cold reads and helper
typing checks do not replace them.

The [2026-10-16 unit-test triage](unit-test-triage.md) preserves historical
redesign blockers and deferred cases at its recorded snapshot. Its failures are
not the current delivery status, and it does not alter test selection.

## Goal ownership

| Behavior | Owning tests | Gate |
| --- | --- | --- |
| Datasource declarations, source IR, metadata, credentials, SQL submission, native timeout settings and cleanup | [datasource](../../tests/datasource/) | Daily contracts; explicitly extended local resume cases in slow-test; Runtime native reads |
| Semantic declarations, identity, metric equations, authoring, validation and analysis handoff | [semantic](../../tests/semantic/) | Daily authoring and local handoff; Runtime native metadata and sample reads |
| Query capture, lowering, physical admission and planning | [analysis/graph](../../tests/analysis/graph/) | Daily local contracts and execution; explicitly extended recovery in slow-test; Runtime native SQL differences |
| Observation, aggregation, comparison, attribution and selection | [analysis/numeric](../../tests/analysis/numeric/) | Daily numerical and behavioral oracles; explicitly extended cases in slow-test; Runtime native carriers |
| Time grids, timezones, windows and temporal folds | [analysis/temporal](../../tests/analysis/temporal/) | Daily temporal semantics; explicitly extended recovery in slow-test; Runtime native time semantics |
| Terminal exports and explicit pandas deviation, association and forecast recipes | [analysis/statistics](../../tests/analysis/statistics/) | Daily numerical behavior; explicitly extended cold output reading in slow-test |
| Artifact reads, native failures, atomic publication, crash recovery and cold ownership | [analysis/materialization](../../tests/analysis/materialization/) | Daily local publication and recovery; explicitly extended process recovery in slow-test; Runtime native timeout configuration |
| Session ownership and persistence | [analysis/session](../../tests/analysis/session/) | Daily session behavior; explicitly extended local session checks in slow-test; Runtime native source integration |
| Public exports, Help budgets, result/error guidance and documentation examples | [surface](../../tests/surface/) | Daily public contracts and short examples; explicitly extended typing, process and execution examples in slow-test |
| Config, refs, CLI, doctor, preview and telemetry | [project](../../tests/project/) | Fast daily |
| Make command routing | [project](../../tests/project/) | slow-test |
| Callable/type contracts | [typing](../../tests/typing/) and surface typing probes | Typecheck; daily rejected calls |
| Package layout, installers, installed origin and recovery | [packaging](../../tests/packaging/) | Daily archive/resource contracts; release installation; opt-in native sources |

Fixtures and suite policies live in `tests/conftest.py`; reusable pure builders
live in `tests/shared_fixtures.py`. Focused helpers live with their behavior;
shared infrastructure lives in `tests/support/`. Tests and workers do not
import helpers from test modules.

The retired Logical/Materialized DSL, graph/Cell publisher, saved executable
receivers and method registry no longer have compatibility test owners. Their
isolated helper and format tests are removed with those implementations. Current
Query key, null, precision, check timing, ownership and fusion assertions remain
at their active owners; terminal and pandas recipe tests remain independent of
current semantic loading and source availability.

## Coverage selection

- Use independent numerical or behavioral oracles, including rejected inputs.
- Share ordinary behavior through representative local Parquet paths. Retain
  native cases where compilation, precision, physical keys or resource ownership
  introduces a distinct risk; avoid backend/storage Cartesian replay.
- Run embedded DuckDB, local-file and loopback HTTP checks in
  `make test`. Mark only materially extended local source/cold-reading journeys
  `slow`; an analysis directory or filename does not make a case slow. A fresh
  Python process does not require a fresh datasource service.
- Keep source execution and terminal cold-reading assertions at their owning
  boundaries. Committed results cannot re-enter the DSL; the terminal boundary
  tests reject both new and reopened results as computation inputs. Source
  scheduling, keys and numeric kernels retain independent local tests.
- Mark only actual external-service parameter cases as `runtime`; static native
  compilation, decoding and rejected-call checks remain daily. Shared matrices
  use `source_params()` to mark their service parameters individually.
- Share ready service containers across cases and invocations. Isolate mutable
  tables/schemas with UUID names and close each case's connections; tests clean
  their own objects. Service lifecycle stays outside pytest and Make test gates.
- Keep source, source-offline output reading and fresh-process recovery boundaries
  explicit. In-process checks do not replace process or crash witnesses.
- Installed-package checks establish candidate archive integrity, noneditable
  import origin and public recovery separately from development-suite coverage.

## Removed native replay and retained owners

| Removed repetition | Remaining coverage |
| --- | --- |
| Retired saved-input computation and repeated cold replay on every service | Local DuckDB method and process oracles; source profiles own native decoding and stream cleanup |
| Reference, comparison, member validation and attribution business matrices on every service | Their local source and terminal-output tests; native producer recovery owns source-offline and cold handoff per service |
| Separate native mean, distribution, reference, versioned, temporal and multiroot recovery journeys | Local recovery owners preserve their exact assertions; native producer recovery covers the shared service handoff |
| Ordinary multiroot arithmetic, slicing, aggregation and min/max replay on every service | Local full matrix; native Decimal precision/overflow and first/last ordering cases remain |
| Coordinate statistics, original component weighting and cumulative-window business replay on every service | Local full matrices; native numeric carriers and native reader/grid/timezone tests remain |

Native profiles, types, namespaces, engine-specific distribution placement,
Decimal behavior, native time semantics, permissions and native query timeouts
remain Runtime owners. No client deadline or query-control cancellation is required. Runtime does not replay
the complete daily acceptance matrix. Removing generic service replay does not
establish qualification for an untested exact backend/method shape.

Local trust and publication behavior follow the
[Runtime contract](../specs/analysis/session-state-and-runtime.md#concurrent-writers-and-atomic-publication).
Test producer validation, malformed/unreadable inputs, missing payloads,
ownership, atomicity and recovery at their owning boundary.

## Remote numerical Query extension

The [implementation plan §8.1](../superpowers/plans/2026-10-13-lazy-analysis-dsl-redesign-implementation.md#81-远端数值-query-最小实施顺序)
defines the delivery sequence: PostgreSQL ordinary Query, other backends and numeric
methods, temporal/dependent reads, then specialized connectors and optimizations.
Evidence belongs to the actual backend/source/method cases that executed. The
[handoff contract](../specs/analysis/lazy-analysis-dsl-execution.md#151-ibis-原生返回与-pandas-类型交接)
accepts native Ibis/provider numerical precision loss. No business-independent
Ibis casts standardize backend dtypes; pandas normalizes actual returned types
for subsequent stages and publication.

Independent Runtime acceptance completed for the bounded §8.1 routes. The final
candidate ran the public Query-to-Artifact and cold-read harness on DuckDB,
PostgreSQL, MySQL, Trino Iceberg and ClickHouse MergeTree, including DST bounds
and native pandas handoff. Related committed Runtime batches provide 25 passing
cases across the frozen candidates, including Trino non-Iceberg and ClickHouse
Distributed; the final temporal delta was independently rerun on all five
providers. The implementation plan's §10 separates reruns, content-based
rebinding, skipped profiles and remaining qualification boundaries. The table
below maps the coverage owners; it does not imply unrestricted backend support.

| Step / boundary | Existing owners to extend | Required evidence |
| --- | --- | --- |
| Ordinary Query and pandas handoff | [remote numeric Query](../../tests/analysis/numeric/test_remote_numeric_query.py), [source profiles](../../tests/datasource/test_source_profiles.py), [computation Runtime](../../tests/analysis/graph/test_computation_runtime.py) | Public Query → Artifact on each newly admitted backend; nullable large integers and Decimal record native and normalized values/dtypes, without a lossless-source oracle; verify keys, None groups, empty/all-NA behavior and saved-value round trips |
| Method qualification | [remote numeric Query](../../tests/analysis/numeric/test_remote_numeric_query.py), [native numeric](../../tests/analysis/numeric/test_native_numeric.py), [native finishes](../../tests/analysis/graph/test_multiroot_consumers.py) | Actual admitted aggregate/dtype shapes, paired samples, zero denominators and applicable overflow policy; rejected methods assert their specific cause. Exact median/quantile are admitted only on DuckDB/PostgreSQL; MySQL/Trino/ClickHouse reject before Run, including ClickHouse's approximate Ibis lowering |
| Temporal and dependent reads | [period carriers](../../tests/analysis/numeric/test_period_native_carriers.py), [Trino encoded time](../../tests/analysis/temporal/test_encoded_time_trino.py), existing graph/temporal owners | Public Query exercises actual engine/time rewrites, scope bounds and representative windows; dependent composite-key/None tuples and one two-datasource handoff. Retain negative cases for unsupported shapes |
| Specialized sources and optimization | [producer recovery](../../tests/analysis/materialization/test_producer_recovery.py), graph optimization owners, [control boundaries](../../tests/datasource/test_control_boundaries.py) | Trino connector and ClickHouse Distributed producers after ordinary routes work; remote source blocks remain unfused without their own key, NA, ordering, check and numerical equivalence evidence; run affected native timeout/read-only/cleanup cases |

ClickHouse Distributed ordinary grouped producers and its pre-Run refusal of
unqualified Distributed IN/JOIN routes are separate cases. MergeTree join evidence
does not qualify Distributed, views or other engine topologies. Qualification does
not change `distributed_product_mode`, add GLOBAL, or substitute local tables.

Source profiles alone do not qualify numerical Query. Historical remote period-carrier
rejections and direct encoded-time expressions do not establish the public
pipeline either. `test_count_rollup_backends.py` currently selects DuckDB table/view
cases, despite its name. Reuse its oracles only after adding actual service cases.
Use one independent-process source-offline read for the initial shared handoff;
extend existing producer recovery only where connector behavior adds a distinct
boundary, rather than replaying all local journeys on every backend.

Use `make runtime-test TESTS='tests/analysis/numeric/test_remote_numeric_query.py'`
or the relevant owning files/nodes, with already-running selected services.
Extend cases before claiming the new coverage; enabling flags or an empty/skipped
selection is not a pass. Reuse [source fixtures](../../tests/datasource/source_cases.py)
and [service setup](../../tests/datasource/environment/README.md), isolate test
objects, and respect the environment's Trino/ClickHouse selection constraints.
Compile-only and synthetic driver results remain local evidence. The final code
candidate needs the regular `make check-agent`; full slow, installed-package and
release acceptance remain separate.

## Commands and evidence

- `make test`: daily local selection, excluding cases explicitly marked `slow`, `runtime` or `release`.
- `make slow-test`: explicitly marked extended local acceptance cases, excluding `runtime` and `release`.
- `make test TESTS='tests/semantic/test_metric_roots.py'`: one behavior target.
- `make slow-test TESTS='tests/analysis/materialization/test_numeric_recovery.py'`: local process recovery.
- `make runtime-test TESTS='tests/datasource/test_source_profiles.py'`: native source profiles.
- `make check-agent`: fast daily tests, lint/import contracts, typing and API docs.
- `make release-test`: candidate archives, installers and installed-package checks.
- `make release-check`: full daily, Runtime and packaging gates for release preparation.
- `MARIVO_INSTALLED_MULTISOURCE_TEST=1 make installed-multisource-test`: selected existing native services; see [source environment setup](../../tests/datasource/environment/README.md).

The daily selection excludes only explicit `slow`, `runtime` and `release`
markers; analysis paths are not classified by prefix. `make slow-test` selects
explicit `slow` cases while excluding `runtime` and `release`. Runtime selects
`runtime`; release targets run serially with `release`. Direct pytest needs an
explicit marker expression to select these groups; a file name does not
override default marker exclusion. Runtime Make targets default to two workers
to bound shared-service load; a selected node containing `::` runs serially.

Installed tests normally read candidates from `dist/pypi`.
`MARIVO_TEST_WHEEL_DIR` selects an isolated build directory. Candidate validation
compares package source and resource hashes with the current checkout before
installation.

[Analysis disclosure context budgets](analysis-help-context.md) have a focused
reproduction path. Unavailable services are skipped or unverified. Daily,
Runtime, installed-package and real-Agent evidence remain distinct; a focused
check does not qualify an unrun backend or the full release gate.
`make release-check` includes both daily selections.

## Native timeout simplification coverage

The removed graph-deadline/transport-interrupt suites and SIGINT listener/control
channel tests only qualified the retired 600-second budget, Timer, KILL and
forced-wakeup guarantees. Their oversized blocking queries are retired with
those guarantees, rather than run without a cancellation mechanism.

Independent stream decode, exact Ibis submission, early-close diagnostics and
close-failure coverage remain in datasource adapter contracts. The native
failure matrix in `test_native_graph_failures.py` preserves fetch/interruption,
precommit failure and committed-success readback; existing graph publication and
producer recovery tests continue to own staging corruption and cold recovery.
`test_native_timeouts.py` covers native parameter mapping, unsupported versus
denied settings, synchronous connection ownership and cleanup of remaining
resources after a release failure. Fixed selection/reduction and deviation
retain their independent numerical and no-retry/publication-failure oracles.
DuckDB has no native query-timeout configuration. Its query-timeout
tests are removed; ordinary query, cleanup and publication coverage remains.

Runtime acquisition and control-boundary tests still exercise PostgreSQL, Trino
and ClickHouse native settings; MySQL acquisition/certification tests exercise
its native SELECT limit and prove no successful publication after failure.
A local pass or a skipped service case is not native backend qualification.

## Terminal-result cleanup

Saved-result DSL continuation, FixedLeaf/FixedShape qualifications, artifact_python,
and the A2/A3 saved-input executor are removed. Their dedicated execution/fusion
fixtures and tests are retired. Source planning, source key identity, retained
numeric equations, exchange integrity and current terminal reading remain owned
by their existing tests. `analysis/session/test_terminal_composition_boundary.py`
checks read-only legacy receiver types and rejects new/reopened outputs as inputs;
`analysis/materialization/test_terminal_publication.py` retains real committed
output and fresh-process reading coverage. Older fixtures using retired semantic
declaration factories are not current acceptance evidence until migrated.
