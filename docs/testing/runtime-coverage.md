# Test goals and execution gates

This guide owns test selection and coverage organization. Architecture and
product contracts live in [current specs](../README.md#current-specs).
Tests follow the behavior they protect. Daily tests need no external datasource
service; Runtime tests require an already-running service. Release tests own
package installation and installed-origin boundaries.

At `405bd006f8`, the Lazy DSL's agreed local implementation gate and independent
business acceptance are complete. The current numerical source path qualifies the actual
backend, expression, types and method, and propagates native pandas numerical
types into local computation and output schemas. AnalysisFrame adds complete
fixed data and separates evaluation from publication; its final acceptance is
tracked independently of that historical source implementation gate. Remote coverage owners and evidence
criteria are listed [below](#remote-numerical-query-extension); code admission and datasource
access tests do not themselves qualify a public Frame source route. The complete slow
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
| Frame source/fixed capture, lowering, physical admission and planning | [analysis/graph](../../tests/analysis/graph/) | Daily local contracts and execution; explicitly extended recovery in slow-test; Runtime native SQL differences |
| Observation, aggregation, comparison, attribution and selection | [analysis/numeric](../../tests/analysis/numeric/) | Daily numerical and behavioral oracles; explicitly extended cases in slow-test; Runtime native carriers |
| Time grids, timezones, windows and temporal folds | [analysis/temporal](../../tests/analysis/temporal/) | Daily temporal semantics; explicitly extended recovery in slow-test; Runtime native time semantics |
| Terminal exports and explicit pandas deviation, association and forecast recipes | [analysis/statistics](../../tests/analysis/statistics/) | Daily numerical behavior; explicitly extended cold output reading in slow-test |
| Frame data/Artifact audit reads, native failures, atomic publication, crash recovery and cold ownership | [analysis/materialization](../../tests/analysis/materialization/) | Daily local publication and recovery; explicitly extended process recovery in slow-test; Runtime native timeout configuration |
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
Frame key, null, precision, check timing and ownership assertions remain
at their active owners; source-only optimization retains its qualified owner,
without granting fixed-data fusion; terminal and pandas recipe tests remain independent of
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
  boundaries. Complete new/reopened Frames are valid fixed data operands; Artifact audit
  objects and RawSqlResult remain invalid computation inputs. Frame continuation
  must not regain producer/source/semantic history. Source
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

## Current Frame/source coverage responsibilities

The final public route is explicit observe/read/values/frame to AnalysisFrame,
compute to complete memory, persist to publish that existing result, and exact
persisted_frame recovery. Fixed ordinary/calendar/paired data operations are
local and source-free. A population_from bridge transports joint complete keys
into explicit new source facts; it does not rerun a fixed selection. Existing
source-only optimization qualification does not grant fixed-data uploads/fusion,
original rollup/drill/share, arbitrary relationship correspondence or broadcast.

| Current boundary | Owning verification scope |
| --- | --- |
| Frame structures, exact handles, current-row statistics and complete-domain combination | `tests/analysis/graph/`, `tests/analysis/session/test_analysis_frame.py`, Frame materialization tests; independent key/order/NA/presence and original-quantity counterexamples |
| Source observation and native type/method handoff | `tests/analysis/numeric/test_remote_numeric_query.py`, source profiles and graph Runtime owners; actual admitted provider/method/type routes after public-call migration |
| Frame-key source bridge and transport | Frame Population graph/materialization owners and existing key-parameter tests; fixed versus pending selectors, required-on zero axes, joint null-safe tuples and qualified request capacity |
| Frozen calendars and paired interpretation | Frame calendar tests and `tests/analysis/materialization/test_frame_storage.py`; finite directory/map validity, original ordinals, actual full joint row coverage and source/TZ/model-free cold operations |
| Complete reads, preview, audit and publication | `test_frame_storage.py`, `test_terminal_publication.py`, receipt/state/guard/process owners; metadata-only n=0/audit versus full integrity, exact order, publish-existing failure/unknown outcome and unchanged old bytes |
| Public disclosure and docs | surface/typing/CLI, packaging skills and API/site checks; final actual examples and paired EN/ZH semantics |

The S7/S8 ledger records which migrated cases actually ran against the frozen
source. A filename, prior source run, compile-only check, skipped service case
or this ownership table does not qualify the new Frame pipeline. Native
sum/mean/weighted/ratio has admitted PostgreSQL/MySQL/Trino/ClickHouse source
routes; exact quantiles and specialized connector/topology shapes keep their
specific method qualifications. There is no blanket DuckDB-only restriction.

## Remote numerical Query extension

This section preserves pre-AnalysisFrame source delivery and provider evidence.
Query-to-Artifact describes the public API used by those recorded candidates;
it is historical qualification of their bounded source routes, not a current
Query receiver or a fresh Frame Runtime acceptance claim.

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
below preserves that pre-cutover coverage inventory and its recorded scope;
current migration/verification owners are above. It does not imply unrestricted
backend support or restore retired receivers.

| Step / boundary | Existing owners to extend | Required evidence |
| --- | --- | --- |
| Ordinary Query and pandas handoff | [remote numeric Query](../../tests/analysis/numeric/test_remote_numeric_query.py), [source profiles](../../tests/datasource/test_source_profiles.py), [computation Runtime](../../tests/analysis/graph/test_computation_runtime.py) | Public Query → Artifact on each newly admitted backend; nullable large integers and Decimal record native and normalized values/dtypes, without a lossless-source oracle; verify keys, None groups, empty/all-NA behavior and saved-value round trips |
| Method qualification | [remote numeric Query](../../tests/analysis/numeric/test_remote_numeric_query.py), [native numeric](../../tests/analysis/numeric/test_native_numeric.py), [native finishes](../../tests/analysis/graph/test_multiroot_consumers.py) | Actual admitted aggregate/dtype shapes, paired samples, zero denominators and applicable overflow policy; rejected methods assert their specific cause. Exact median/quantile are admitted only on DuckDB/PostgreSQL; MySQL/Trino/ClickHouse reject before Run, including ClickHouse's approximate Ibis lowering |
| Temporal and dependent reads | [period carriers](../../tests/analysis/numeric/test_period_native_carriers.py), [Trino encoded time](../../tests/analysis/temporal/test_encoded_time_trino.py), existing graph/temporal owners | Public Query exercises actual engine/time rewrites, scope bounds and representative windows; dependent composite-key/None tuples and one two-datasource handoff. Retain negative cases for unsupported shapes |
| Specialized sources and optimization | [producer recovery](../../tests/analysis/materialization/test_producer_recovery.py), graph optimization owners, [control boundaries](../../tests/datasource/test_control_boundaries.py) | Trino connector and ClickHouse Distributed producers after ordinary routes work; remote source blocks remain unfused without their own key, NA, ordering, check and numerical equivalence evidence; run affected native timeout/read-only/cleanup cases |
| Source relation and key transport optimization | [PostgreSQL source queries](../../tests/analysis/materialization/test_source_query_postgres.py), [remote source relations](../../tests/analysis/materialization/test_source_query_remote.py), [key transport](../../tests/analysis/materialization/test_query_key_transport.py) | Actual PostgreSQL, MySQL, Trino Iceberg and ClickHouse MergeTree string filtering/ordering, count key producers and cell row counts; CHAR padding and native grouping membership stay owned by their original boundary. ClickHouse FixedString asserts the same typed reader failure with and without fusion, including Top-0. PostgreSQL typed arrays preserve null-safe complete tuples and large integer keys. Trino/ClickHouse numeric attribute and aggregate predicates/Top have their own [native numeric relation tests](../../tests/analysis/materialization/test_source_query_numeric.py): explicit native values, NaN/NULL ordering, Decimal thresholds, complete-key ties and necessary failures before Top-0. [Checked Query arithmetic](../../tests/analysis/materialization/test_source_query_arithmetic.py) adds DuckDB and actual Trino/ClickHouse multi-binding Metric formulas, compound Column expressions, computed Top, NaN/NULL division and required errors before Top-0. [Native cell sums](../../tests/analysis/materialization/test_source_query_cell_sum.py) cover native float/Decimal accumulation, empty/all-NA results and necessary nonfinite/range failures, including ClickHouse exact range-check literals. Other providers retain their separately qualified numeric boundaries. Local DuckDB tests own Population combinations and independent shared grouped consumers. [Trino/ClickHouse relation tests](../../tests/analysis/materialization/test_source_query_relations_remote.py) additionally own string-axis align/compare/broadcast, absent versus present-NA carriers and endpoint errors before Top-0, including ClickHouse join_use_nulls=0/1; independent shared grouped consumers and atomic replacement of multiple parameter producers return one compact block without key uploads. [Shared boundary tests](../../tests/analysis/materialization/test_source_query_shared_boundaries.py) add actual Trino/ClickHouse mixed endpoint/source consumption, plain/per and same-per consumers, and different windows sharing a source-local population; local cases additionally retain real host sample domains and parent failures |
| Relationship Population fusion | [image/preimage](../../tests/analysis/materialization/test_source_population_fusion.py) | Local checks and actual Trino/ClickHouse two-hop relations preserve ancestor errors, empty Query operands and duplicate links while returning only terminal domains or scalar values; source checks do not download full ancestor identity sets |
| MySQL bulk complete keys | [JSON key transport](../../tests/analysis/materialization/test_mysql_key_parameters.py) | Actual binary NO PAD VARCHAR/CHAR, nullable int64/boolean/date, Unicode and 10k complete tuples; native collation membership remains unchanged. The active server packet limit, JSON member/document capacity and final encoded SQL are checked before target submission. Artificial lower-capacity tests exercise rejection without changing the server setting |
| Original Metric source recomputation | [PostgreSQL originals](../../tests/analysis/materialization/test_source_original_postgres.py), [MySQL originals](../../tests/analysis/materialization/test_source_original_remote.py), [Trino/ClickHouse originals](../../tests/analysis/materialization/test_source_original_trino_clickhouse.py), [selected source mutation](../../tests/analysis/materialization/test_source_original_selection.py), [typed file originals](../../tests/analysis/materialization/test_source_original_files.py), [grouped samples](../../tests/analysis/materialization/test_source_original_samples.py), [complex originals](../../tests/analysis/materialization/test_source_original_complex.py), [remote independent roots and row recipes](../../tests/analysis/materialization/test_source_original_multiroot_remote.py), [selected and nested samples](../../tests/analysis/materialization/test_source_original_remaining.py) | DuckDB local oracles retain root identities, dynamic sample periods, selected per/window parents, selected drill attribute scope and nested intermediate failures. Actual PG/MySQL tests qualify direct numeric reducers, nullable values and integer mean states while preserving nonnull complete identities, fine-grid failures and native-collation fallback (MySQL requires binary NO PAD). Fixed-key source mutation is exercised on PostgreSQL and MyISAM without a base-table/transaction-engine consistency probe. Local CSV/Parquet/JSON originals return compact results; selected Parquet mutation preserves parent keys, and default/inherited collation boundaries remain checked. Trino/ClickHouse tests own ordinary reducers, exact distinct, selected nullable complete keys, typed-array transport and source changes between parent selection and target recomputation. Nullable float/Decimal targets retain fractional native values rather than passing through integer state conversion. Trino and ClickHouse compact integer mean/weighted-mean states are checked against explicit native results, including adjacent large integers, cancellation and wide totals. Native calculation and pandas rounding differences from the original host route are accepted without fallback; fixed parent keys, NA and error policies remain strict. All four remote providers also exercise same-session roots reduced independently before complete-domain alignment, qualified row arithmetic and Metric formulas, selected rollup/drill, and required errors before Top-0. [Remote grouped samples](../../tests/analysis/materialization/test_source_original_samples_remote.py) own per/window spatial rollup, shared sample identity, captured empty policy, lookback and fine zero-denominator failures, plus selected multi-parent sample unions, correlated endpoint/sample keys and source mutation after fixed parent selection. [Plain Calendar originals](../../tests/analysis/materialization/test_source_original_cross_owner.py) own independent source-owner leaves, complete parent formulas before target reads and legal ordinary Calendar coarsening. [Decimal exact quantiles](../../tests/analysis/materialization/test_source_original_decimal_quantile.py) exercise native fractional finishes on DuckDB/PostgreSQL, selected parents, NULL/empty values, strict Top-0 failures and full parent formula errors. No cross-read snapshot is required; current backend/type/method and topology qualifications still apply |
| Native request capacity | [Source request capacity](../../tests/datasource/test_source_request_capacity.py) | ClickHouse uses the effective client override or cached server max_query_size, counts UTF-8 plus FORMAT Native, and rejects oversize reads before issuing or submitting them; unknown settings retain native enforcement and zero disables only the parser-size limit |
| Repeatable transport cost | [10k/100k cost cases](../../tests/analysis/materialization/test_source_execution_cost.py), [measurement report](analysis-source-execution-cost.md) | Three repeats per baseline/optimized route; actual source rows/columns, SQL UTF-8 bytes, pandas/Arrow sizes, submitted inline/array keys, raw/local input rows, inclusive phase times, process lifetime RSS and separate DuckDB EXPLAIN ANALYZE. No network-byte, single-scan or universal latency claim |

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

The former saved executable graph/FixedLeaf/FixedShape/artifact_python and A2/A3
executor protocols remain retired. They are not an alternate path beneath the
new Frame data contract. Complete memory/stored Frames support the same data
operations without producer graphs; Artifact is audit-only and RawSqlResult
retains its independent datasource reader. Public boundary tests distinguish
these operands and exact ownership; storage/process tests retain real publisher,
full read, preview, audit and cold continuation assertions. Historical semantic
factory fixtures must be migrated to the current source contracts before they
count as new acceptance evidence. No historical replay count is relabeled.
