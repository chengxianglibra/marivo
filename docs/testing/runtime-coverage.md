# Runtime test coverage

Functional acceptance uses local Parquet files. Native DuckDB cases remain where
engine execution, source-private membership/distribution, receipt integrity, or
engine process recovery is the actual contract.

| Boundary | Owning checks |
| --- | --- |
| R5 public graph cold recovery and exact fixed hits | `test_analysis_numeric_r56.py` covers four numeric families, table/Parquet, eight state methods and 256 part/receipt/version faults; `test_analysis_recovery_r57.py` adds six row-state methods, typed reads, direct-only distributions and structural contract/signature preservation in three processes |
| R5 Session writer contention and activation | `test_lazy_runtime_concurrency.py` uses Store 7, real graph-primary publication, six thread/process/reentrant contenders, actual driver query barriers, fresh source evaluations and distinct/exact fixed keys |
| R5 installed public journeys | `test_analysis_runtime_wheel.py` runs the R5 modules and debt owners from one non-editable wheel outside the checkout; every spawned Python process checks installed origin, and source injection must fail |
| Analysis, compare, attribution, sampling, ordering, ordinary concurrency | Local-file Runtime tests with real DuckDB sources and calling-process execution/reads |
| Producer, continuation, and cold binding independence | Local-file fresh-process journeys; dedicated engine adapter/recovery journeys |
| File integrity, complete-input limits, atomic publication and crash recovery | Local-file and engine-specific Runtime checks |
| SQLite publication interrupted inside its transaction | `test_lazy_materialization_store.py::test_process_exit_preserves_atomic_publication`, with actual child exit and a reopened Store; the native adapter retains an `insert_terminal` crash journey |
| Retained attribution with a missing axis | `test_lazy_attribute_contracts.py::test_retained_missing_axis_rejects_before_any_action`, using trusted metadata and a port that forbids execution |
| Distribution method and input-state variants | Focused Runtime publication/authority tests; exact and approximate fresh-process journeys own cold continuation and result reuse |
| Unified Journey matching, Duration, dropout and Subject images | `test_analysis_domain_preparation_r72.py` and `test_analysis_journey_matching_r73.py`: captured identities/order/coverage, independent assignment oracles, exact Duration and the public A09 selection into prepared Metric observation |
| Unified funnel comparison/allocation and Findings | `test_analysis_funnel_r74.py` and `funnel_r74_worker.py`: independent Fraction/side/Top-K oracles, public pagination/single reads, bound nonempty Finding sets, source-offline exact hits, corruption and atomic failure |
| Canonical History and six views | `test_analysis_lifecycle_r75.py`, `test_analysis_history_r76.py` and their workers: full Subject ledger, raw-event/checkpoint/clipped-interval oracles, A10 and guarded three-process fixed continuations |
| Anchor and retention | `test_analysis_anchors_r77.py`, `test_analysis_retention_r78.py` and their workers: Event/Journey starts, relative Metric parts, DST/overlap, original Omega, independent truth/bounds and explicit Subject quantifiers; starts-only fixed kernel gaps remain unqualified |
| R7 legacy retirement and installed-wheel closure | `test_analysis_retirement_r79.py` pins absent modules/imports/families/producers and strict old descriptor rejection. `test_analysis_runtime_wheel.py` runs current R7 and affected R5/R6 tests plus public J1-J4/A02/A07/A08 in one isolated wheel, with per-process origin/hash guards and poisoned-source rejection. Executed receipts and remaining mandatory qualifications belong to the R7.9 evidence index. |

Local/engine crash checks exercise actual process loss and publication recovery.
Historical Slice acceptance records describe their original candidates; they are
not the current recurring test matrix.

Slice 9c adapter crashes target the local output reservation and actual Parquet
primary/private-part file creation before atomic rename and Store commit. The
test-only file-constructor hook observes real writer calls; it is not a product
event or an engine-result-storage path. Mutation checks cover replacement,
absence and same-length in-place changes to immutable Parquet payloads.
Registered Parquet membership scans are accepted across native source files.
Pandas lifetime/orphan and composed-read journeys explicitly preselect the
registered pandas method before execution, so native Parquet support cannot
bypass the local execution boundary being tested. Public scoped-read acceptance separately
uses the default native route and real nonempty Findings in three processes.

## Commands

- `make test`: daily contracts and local storage regressions.
- `make runtime-test TESTS='tests/test_lazy_local_execution.py'`: focused functional checks.
- `make runtime-test`: complete functional Runtime selection when explicitly needed.
- `make release-check`: daily, full functional Runtime, and packaging gates.

Runtime defaults to two pytest workers per invocation. Increase this only after
measuring host capacity and accounting for other concurrent invocations. More
workers can shorten suite wall time while increasing each case's latency.

Within one journey phase, collect an immutable result once and reuse the DataFrame
for assertions and evidence serialization. Keep independent reads when the test
specifically validates read isolation, integrity changes, or a fresh process.

The composed read journey performs full inspection of both outputs in its cold
phase. Continuation and cold phases still collect their own rows and verify that
reads leave persisted state unchanged. Native adapter crash parameters share one
post-commit independent-Session execution check; the live-orphan test separately
proves independent execution while the owning Session remains blocked.

Local publication crash points are separately parameterized for xdist scheduling.
When `MARIVO_SLICE2B_EVIDENCE_PATH` is set, each case writes a sibling file with the
crash point appended to the requested filename stem, avoiding concurrent overwrites.

## Slice 9b diagnostics

`tests.lazy_acceptance_capture` is an opt-in observer, loaded with
`-p tests.lazy_acceptance_capture` and `MARIVO_SLICE9B_EVIDENCE_DIR`. It records
existing selected steps, terminal receipts, Run/Artifact counts, query/transfer
statistics, available execution measurements and timings. Test nodes remain the owners of
row, authority, repair and recovery assertions; this is not a support registry.
Adapter-internal metadata and storage wire request counts are uninstrumented,
not zero. Native primary/independent-part streams count decoded transfer once;
parts split from the primary batch are separate storage receipts, not additional
source transfers. Exact recovery still requires zero execution and zero copies.

Tests intended to exercise pandas preselect an absent source lowerer using the
existing implementation registry. Native Parquet continuations are separately
accepted. A successful basic SQLite/MySQL/PostgreSQL/ClickHouse/Trino query does
not register that backend for Dataset analysis. Runtime tests use local Parquet
storage.
Funnel comparison tests cover authored step order through publication and
filtering. Distinct temporal checkpoints validate physical coordinate types in
both the primary rows and independent membership parts before cold reuse.

## Slice 1b replacement coverage

`test_lazy_in_process_execution.py` owns caller PID/no-spawn, complete inputs above
the former 100,000-row cap, wide nested dictionary normalization, original errors
and interruptions, and SIGKILL recovery at local calculation/publication boundaries.
Local row/fold, Forecast/Candidate graph, multi-input comparison/attribution and
Parquet integrity tests retain their independent numerical and semantic assertions.
Worker IPC, watchdog, RSS and resource-ceiling tests are retired with those
protocols; their historical acceptance is not evidence for caller execution.

## Multi-datasource Slice 1c

- `test_lazy_duckdb_execution_adapter.py`: real Ibis parameters/hooks, repeated
  pure compilation, actual submissions, complete typed transport and error cause.
- `test_lazy_materialization_failures.py`: unknown open without query ID, failed
  cancel/close, safe later same-Session work and committed readback.
- `test_lazy_reconciliation_snapshot.py` and `test_lazy_materialization_store.py`:
  exact ownership, contradictory commit state and safe read-only discharge.
- `test_lazy_runtime_concurrency.py`, `test_lazy_adapter_crash_acceptance.py` and
  `test_lazy_materialization_runtime_acceptance.py`: surviving writer exclusion,
  fork/lock ownership, real process death and atomic cold primary/part readback.

## Multi-datasource Slice 2

`test_lazy_backend_dispatch.py` owns pure exact-registration, preparation-only,
local-shape and Help checks. Its Runtime cases inspect retained-reader admission
with injected physical backend candidates; these do not enable remote execution.
`test_lazy_dispatch_authority.py` owns independent source/retained identity
perturbations and a Runtime binding hit that forbids placement and source access.

`test_lazy_execution_economics.py` retains real DuckDB reduction and five remote
pre-Run rejections. Distribution/correlation Runtime suites own their preparation
and local-result parity. The adapter suite owns foreign/forged/closed execution
contexts; materialization execution owns physical-type rejection before primary
rows. Funnel comparison's three-process journey freezes source/test bytes across
produce, continue and recover. Run that journey without concurrent code edits.

## Corrected Slice 1d restoration

Sampling, Event/Lifecycle, Entity/Driver Candidate and JSON producer success tests
are restored. Adapter tests cover required hooks, memtables, UDFs and temporary
preparations; backend dispatch tests cover the common registry, retained import
and unimplemented backend refusal. This supersedes the blanket read-only 1d
acceptance, without enabling remote methods or new private-state transfer.

R6.4 fixed-reference qualification is owned by
`tests/test_analysis_references_r64.py`: public DuckDB table/Parquet and fixed
share I/F/D/T, standardization I/F/D with the six admitted original methods,
complete/composite/overlapping penetration, exact weight policy, zero-read
construction, retained inputs, source re-evaluation, shared realization,
source-offline fresh-process continuations, corruption and publication cleanup.
This is focused Runtime acceptance. Ranking/Top-K invariance belongs to R6.5;
installed-wheel closure belongs to R6.7.

R6.5 display qualification is owned by `tests/test_analysis_display_r65.py`:
public DuckDB table/local-Parquet and fixed ranking I/F/D/T (Duration us), four
tie policies and both directions, composite identities and null partitions,
Entity/Group/Singleton and UTC monthly Entity×Time domains, full-key scalar
terminal tables, shared realization, fresh source/fixed input distinction,
strict two-step Top-K, preserved reference/quantity/order, empty selections and
empty Singleton tables. Controlled fixed exchanges cover all four Cell tags;
public source producers cover Defined/Null/Undefined without inventing an Unknown
producer. New-process source-offline recovery disables Semantic and DuckDB and
executes ranking selections plus terminal table restoration. Tests also inject
missing/corrupt parts, state/part versions, rank/order/binding corruption and
publication faults. `test_cutover_documentation_examples.py` executes the same
latest English/Chinese display example on table and Parquet sources. This does
not qualify other Duration units, temporal shapes, remote backends or installed
wheel paths; R6.6/R6.7 retain their separate acceptance owners.

R6.6 allocation qualification is owned by tests/test_analysis_attribution_runtime_r66.py:
DuckDB native tables/local Parquet and fixed additive sum/linear I/F/D/T and int64 count,
component_mix mean/weighted_mean/original ratio I/F/D, logical missing-axis
expansion, fixed missing-axis rejection, raw-fact side/contribution oracles,
common asymmetric Top-K, real "Other" collisions, mapped-parent hierarchy,
per-resolution reconciliation, unconditional selected completeness revocation,
missing/corrupt parts and versions, shared realization and exact compiled/native
submissions, fresh source evaluation with fixed retention, UTC monthly
PeriodChange and day-to-month retained partitions, floating numeric-view bound
transport and excess small-Decimal rounding rejection before publication.
A producer and two new processes continue/recover with sources
renamed away and Semantic/DuckDB/SourceSession disabled. Pure arithmetic in
tests/test_analysis_attribution_r66.py owns large exact carriers, checked overflow,
finite outputs, contradictory basis, high-precision component side terms,
denominator error intervals and many-small-Decimal rounding thresholds.
test_cutover_documentation_examples.py executes identical latest bilingual
attribution examples. True-null source coordinates remain rejected under R5;
additional time shapes and installed-wheel/remote acceptance remain unverified.


R8.5 statistical retirement is owned by `test_analysis_views_r85.py`,
`test_analysis_retirement_r85.py`, `test_analysis_disclosure_r85.py` and
`test_analysis_retired_oracles_r85.py`. The frozen view profiles are table
int64, Entity composite(string,int64), and global UTC/us built-in day grids.
Source-issued Ibis SQL is matched to each real native cursor submission.
Deviation/runs and all three association/forecast methods retain their current
source, fixed and three-process cold tests; J4/Spearman keeps its existing
implementation/parts boundary with common association arithmetic. Exclusive
Candidate, Driver, Association Dataset and Forecast Dataset test harnesses are
removed. Their 391 original node dispositions and 449 symbol records remain
bound to the R8.1 snapshot in the R8.5 ledger. A mapped owner is not an exact
assertion-transfer pass; unresolved transfers remain unverified and prevent
R8.5 completion. Remote legacy backend tests do not qualify the current R9 graph.

The [R8.5 final evidence index](../superpowers/specs/2026-10-04-marivo-r85-evidence-index.md)
closes its 171 frozen obligations and M01-M18 retirement gates. A separate
supplementary Runtime scope has 201 passes and four failures reproduced at the
entry baseline: R6 fixed UTC/us period buckets have a blocked registration, and
table/Parquet A08 recovery has different input time/source shapes. These failures
remain failures; the successful exact 139-case owner command supplies retirement
transfer evidence. Retained unexecuted Runtime/remote nodes remain unverified.

R8.6 local qualification is owned by `test_analysis_acceptance_r86.py`,
`r86_worker.py`, `test_analysis_faults_r86.py` and the release-marked
`test_analysis_runtime_wheel.py::test_installed_r8_candidate_and_public_processes`.
The worker uses native DuckDB tables and local Parquet, composite Subject keys,
UTC/us full days, all nine source methods, independently retained fixed inputs
and fresh-process offline kernels. It separates source reevaluation, method
execution and exact hits; recovery disables current Semantic and DuckDB and
forbids refitting or resegmentation. A11 tests exercise prepared selected-member
followup, explicit Category*Time mixed numeric inputs, all correlation methods
and models, and independent single-axis/joint original-target reconciliation.
Fault tests validate every receipt and required part and distinguish below/at/
above-600, cancellation, late results, bad source batches, reader close, rollback
and lost durable-commit acknowledgement. Six interval-witness counterexamples
are checked without segmentation. The installed gate uses an isolated venv,
archive/source hashes, dependency checks, import guards in every process, a real
poisoned PYTHONPATH rejection and separate A04/Spearman recovery processes.
The [R8.6 evidence index](../superpowers/specs/2026-10-04-marivo-r86-evidence-index.md)
preserves all 492 original IDs and their exact dispositions; these focused
profiles and engineering gates do not grant unexecuted R8/R9/R10 qualifications.
