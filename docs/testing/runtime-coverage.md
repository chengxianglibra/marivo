# Runtime test coverage

Functional acceptance uses local Parquet files. Native DuckDB cases remain where
engine execution, source-private membership/distribution, receipt integrity, or
engine process recovery is the actual contract.

| Boundary | Owning checks |
| --- | --- |
| R9.4 C06 exact int64 Subject temporal fold | `test_r94_temporal_recovery.py`: six native adjacent int64 Subject keys above `2**53`, exact entity/table/native/UTC-us/ibis fold registration, ordered spatial sample/support parts, current mean 84 versus original rollup 168, two independent fixed/cold outputs per producer and zero cold Runs/kernels/resources. A SQLite string control verifies the shared worker. Other temporal risks and full V04 qualification keep their owners. |
| R9.4 C03 native identity and version admission | `test_r93_members_consumers.py`: six native duplicate/overlap/null-key failures, one failed Run and zero publication/resources, with missing-version source/admission tripwires. SQLite nullable int64 staging regression preserves low bits and Null; repaired SQLite versioned Runtime binds 17 originals plus 22 fixed/cold outputs. These witnesses do not grant other physical profiles or complete V02 qualification. |
| R9.4 ClickHouse pending SIGINT repair | `test_r94_native_graph_deadline.py`, `test_r94_clickhouse_graph_phases.py` and `test_datasource_clickhouse_cancellation.py`: the historical socket-only counterexample retains its active-after-signal failure. The authorized same-reader issued-ID control passes actual pending/initial-response/fetch SIGINT with native 394 and zero active queries, plus native deadline 159. Real missing permission fails with a structured repair and releases control resources; unacknowledged control close retains an incomplete Run and one obligation. Independent server termination remains separate from remote_unknown source receipts. |
| R9.4 fixed Decimal current-row statistics | `test_r94_decimal_row_statistics.py` and `test_r94_distribution_carriers.py`: exact Decimal(18,2) Entity/NoTime artifact_python sum/count, Decimal(38,2) sum state and Decimal(38,6) HALF_EVEN mean, actually derived Singleton merges, empty sum/mean, large values, finish overflow and closed registration. Three actual table/Parquet carrier cases preserve 13 originals and 74 independent fixed/cold outputs with zero cold kernels/Runs/resources. Historical refusal candidates remain unchanged. |
| R9.4 public History source-free recovery | `test_r94_history_public_recovery.py` and `r94_history_public_recovery_worker.py`: three History parents, nine source views and six captured observations are 18 scoped continuation inputs, not total source publication counts. Two public three-process risks retain complete/prefix/Unknown, actual violations, truth, 27 historical cells, null/Other axes, selected-subject source observation 48/4 and 18 fixed/18 zero-work cold outputs, full parts/contracts and zero resources with Semantic/source/DuckDB/replay forbidden. |
| R9.4 public physical schema drift | `test_r94_public_schema_drift.py`: actual BIGINT-to-DOUBLE drift rejects the original public graph before business compile/read/staging, records one admitted failed Run, publishes no Artifact/Evidence/Findings and preserves the previous full Artifact. Restoring BIGINT permits the original graph retry with 1000; both paths release resources. |
| R9.4 finite Journey/Funnel/History controls | Shared current selections bind 38 Journey/Funnel pure cases, 27 History default cases, 26 Journey/Funnel Runtime business cases after one exact example-locator repair, and 76 shared History/lifecycle Runtime cases including 14 publication/cancel/deadline controls. Counts remain selection scope; native profile and transport phase owners remain separate. |
| R5 public graph cold recovery and exact fixed hits | `test_analysis_numeric_r56.py` covers four numeric families, table/Parquet, eight state methods and 256 part/receipt/version faults; `test_analysis_recovery_r57.py` adds six row-state methods, typed reads, direct-only distributions and structural contract/signature preservation in three processes |
| R5 Session writer contention and activation | `test_lazy_runtime_concurrency.py` uses Store 7, real graph-primary publication, six thread/process/reentrant contenders, actual driver query barriers, fresh source evaluations and distinct/exact fixed keys |
| R5 installed public journeys | `test_analysis_runtime_wheel.py` runs the R5 modules and debt owners from one non-editable wheel outside the checkout; every spawned Python process checks installed origin, and source injection must fail |
| Analysis, compare, attribution, sampling, ordering, ordinary concurrency | Local-file Runtime tests with real DuckDB sources and calling-process execution/reads |
| Producer, continuation, and cold binding independence | Local-file fresh-process journeys; dedicated engine adapter/recovery journeys |
| R9.4 native producer binding to source-free fixed execution | `test_r94_producer_recovery.py` and `r94_recovery_worker.py`: six opt-in native producers, complete composite int64 identities, two source realizations, separate offline/cold kernels, exact hits with kernels forbidden, descriptor/schema/K/Evidence preservation and resource counters. This is the Entity/NoTime int64 scalar quotient witness, not family-wide or remote cancellation qualification. |
| R9.4 native domain producer recovery | `test_r93_remote_domain_consumers.py` with `MARIVO_R94_DOMAIN_RECOVERY=1` and `r94_domain_recovery_worker.py`: real native Journey/History/Anchor/retention producers, source closure before independent fixed/cold phases, exact source descriptor/parts/Evidence preservation, History projections, retention quantifiers, Anchor numeric and Journey Duration ranks. These selected continuations do not grant every producer/profile or all disclosed K. |
| R9.4 archived native root continuations | `test_r94_archived_domain_recovery.py` with `MARIVO_R94_ARCHIVE_RECOVERY=1` and `r94_native_domain_k_worker.py`: independently hashed PostgreSQL/MySQL/Trino/ClickHouse project archives, remaining root actions, 21 supplemental outputs, complete Run pagination, source/Semantic/replay forbidden, fresh-process exact hits with kernels forbidden. Root action coverage does not grant recursively derived K or additional native profiles. |
| R9.4 archived native statistic K | `test_r94_archived_domain_recovery.py` with `MARIVO_R94_DERIVED_RECOVERY=1` and `r94_statistic_k_worker.py`: five current-row statistics per native producer, all four currently disclosed K, 25 fixed outputs, exact fresh-process cold hits, five unsupported-template and five Scalar-correlation pre-Run refusals with unchanged publication counts. Root snapshots preserve original metadata except the two explicitly corrected disclosure fields. |
| R9.4 archived ranking/deviation owned fields | `test_r94_archived_domain_recovery.py` with `MARIVO_R94_FIELD_RECOVERY=1` and `r94_owned_field_k_worker.py`: 50 owned views and 60 continuations per native archive, full fixed/cold snapshots, retained fit scope under empty selection, zero-Run/kernel cold hits and no source/replay access. Direct result-card K only; recursive numeric/profile and V15 obligations remain separate. |
| R9.4 archived native History fields | `test_r94_archived_domain_recovery.py` with `MARIVO_R94_HISTORY_FIELD_RECOVERY=1` and `r94_history_field_k_worker.py`: 24 fields, five selections and two Subject projections per native archive; independent UTC/Duration/Cell/full-key oracles; 31 fixed outputs and exact zero-Run/kernel cold hits. Empty violations do not grant nonempty violation/Findings coverage or other profiles. |
| R9.4 archived Journey/retention K | `test_r94_archived_domain_recovery.py` with `MARIVO_R94_JOURNEY_RETENTION_RECOVERY=1` and `r94_journey_retention_k_worker.py`: 61 fixed/cold outputs per native archive, completed-field reads, repeated field selection, typed Boolean recovery, Subject images/Omega, counts including empty zero, Duration means and retained-Subject grouping. Nonempty false/unknown states and other profiles are separate owners. |
| R9.4 public nonempty retention states | `test_r94_retention_states.py` and `r94_retention_states_worker.py`: canonical SQLite source/offline/cold Sessions, three int64/int64, int64/string and string/string Subject/occurrence profiles, each with four complete Anchor keys (int64 above 2**53) and nonempty true/false/unknown statuses, any/every reductions, twelve views, nine counts and valid true Subject images; 78 fixed outputs and zero-Run/kernel cold exact hits, retained Omega/bounds and six structured false/unknown member refusals per offline process. Models/source are deleted and replay/reconnect is forbidden. The native main-table UTC-us string Subject preparation key is now qualified; composite Subject keys and other actual physical profiles retain separate owners. |
| R9.4 C08 reference recovery | `test_r94_reference_recovery.py` and `r94_reference_recovery_worker.py`: six native composite-key producers, fourteen originals and nineteen source-free fixed/cold results per backend; share with explicit fixed rollup, source/fixed ranking views, complete Subject×two UTC days, any/at-least-two cohort, penetration/empty reference, grouped standardization, retained reference-part selection, original rank limit/filter and canonical terminal table. Independent cold hits add zero Run/kernel/resources. Large native SQL attachments are byte-exact packed without changing original Run hashes. `test_r94_reference_subset.py` reuses six archived producer projects for one shared nonzero-row source/fixed share goal: 2/3 after removing the 1/3 row, exact reference-part equality, eight fixed outputs and eight cold hits with zero kernel/Run/resources. No Subject map means no members disclosure and typed projection refusal; the positive retained-map source/fixed regression still passes. Other state/rule/weight/profile/fault obligations remain. |
| R9.4 C04 independent-root recovery | `test_r94_multiroot_recovery.py` and `r94_multiroot_recovery_worker.py`: one composite-Subject native setup per backend, five licensed int64 runtime expressions, 30 originals and 174 fixed/cold continuations. Exact linear root sums/supports, ratio numerator/denominator, weighted numerator/weights, original rollup versus selected current-row statistics, Null/Undefined/empty selection and full adjacent int64 tenant identities above 2**53; all original-state contents preserved with models/source deleted and Source/Semantic/reconnect forbidden. Cold exact hits add zero Runs/kernel/resources. `test_r93_multiroot_consumers.py -k linear_fanout` binds the same candidate's incomplete-key construction refusal. Other actual carriers and physical differences retain their own owners. |
| R9.4 C03 versioned attribute recovery | `test_r94_versioned_recovery.py` and `r94_versioned_recovery_worker.py`: one four-row native setup per backend, both snapshot/validity clocks, 102 original member/four-family read/product Artifacts and 132 independent fixed/cold outputs. Adjacent int64 keys above 2**53 with tenant namespaces, exact September and before_end without last-known, open validity ends, six complete member/time cells, positive/empty typed selections and numeric sum retaining the version. Models/local source files are deleted; cold recovery forbids Source/Semantic/reconnect and fixed kernels, with zero new Runs/resources and unchanged original snapshots. Fixed Domain has no each/read authority; other physical forms and independent faults/resources remain separate. |
| R9.4 C09 attribution recovery | `test_r94_attribution_recovery.py` and `r94_attribution_recovery_worker.py`: six ordinary native sum/mean pairs, 28 original Difference/allocation artifacts, 100 independent fixed/cold outputs and 258 owned views. Exact allocated current/baseline terms, original target -2/-1 versus selected row sum 3/1.5, retained target/basis/allocation/reconciliation hashes, common Top-K/Other mask, incomplete positive/empty selections, canonical same-key table and DuckDB joint/hierarchy prefix resolutions; cold reads add zero Run/kernel/resources with Source/Semantic/reconnect forbidden. Other methods/types/time/forms/profiles/K and fault obligations remain. |
| R9.4 C10 distribution recovery | `test_r94_distribution_recovery.py` and `r94_distribution_recovery_worker.py`: one Subject/facts setup per native backend, 22 licensed bounded int64 distribution results and 110 independent fixed/cold continuations. Adjacent int64 fact identities above 2**53, null/empty Cells, exact distinct and linear-interpolation quantile, explicit approximate intent/actual algorithm, retained Subject projection, current-row sum/count/mean, nonadditive rollup and compare-to-attribute refusal paths; cold reads allocate zero Run/kernel/resources with Source/Semantic/reconnect forbidden. Other types/forms/profiles/K and fault obligations remain. |
| R9.4 C07 comparison recovery | `test_r94_comparison_recovery.py` and `r94_comparison_recovery_worker.py`: six native composite string/int64/int64 producers for nested Difference, UnionKeys missing-side and relation ratio zero-denominator Cells; eight original endpoint/result snapshots, 13 fixed outputs and zero-Run/kernel cold exact hits per backend. Exact positive Subject image and row statistics, ratio Defined selection, union empty selection and no Metric-rollup authority are bound. Models/local sources are deleted; offline source/Semantic/reconnect and cold kernels are forbidden. Other designs/types/profiles/K/fault obligations retain their owners. |
| R9.4 aligned status-time mean recovery | `test_r94_temporal_recovery.py` and `r94_temporal_recovery_worker.py`: six native string Subject/int64 producers, ordered spatial totals/support counts at two aligned UTC instants, current-row mean 84 versus original temporal fold 168; two new fixed outputs and zero-Run/kernel cold exact hits. Three public Session processes, complete retained snapshots and native source ownership are bound; models/local sources are deleted and offline reconnect forbidden. Remote int64 Subject fold keys remain refused; other fold/time/form/fault obligations retain their owners. |
| R9.4 original mean component recovery | `test_r94_mean_recovery.py` and `r94_mean_recovery_worker.py`: six native int64 producers (Trino Iceberg/ClickHouse MergeTree), original group sum/count {100/100,100/1}, independent current-row 50.5 versus weighted 200/101, two fresh fixed outputs and zero-Run/kernel cold exact hits per producer. Public Session recovery, full original snapshots, native submissions and closed source owners are checked; models/local sources are deleted and reconnect is forbidden. Other carriers/forms/time folds and mean continuations remain separate. |
| R9.4 nonempty Findings faults and cap | `test_analysis_funnel_r74.py`: 20 current-candidate cases for seven collection corruptions, five transaction points, four binding/receipt swaps, three injected extraction/cancel/deadline faults and 1003/1000/3 cap authority with independent continuation/recovery. Local fixture/private Session recovery only; no native-profile, canonical public Session or remote termination grant. |
| R9.4 public Session nonempty Funnel recovery | `test_r94_funnel_public_recovery.py` and `r94_funnel_public_recovery_worker.py`: real table/parquet authoring, canonical public Session/result restoration, deleted models/source, six fixed outputs and original frozen-graph cold hits with both fixed/Funnel kernels forbidden; two comparison and four attribution Findings. Local int64/UTC-us ordinary Dimension only; other profiles, cap and remote termination remain independent. |
| R9.4 native nonempty Funnel recovery | `test_r94_native_funnel_recovery.py`: PostgreSQL/MySQL native, Trino Iceberg and ClickHouse MergeTree producers, actual SQL/closed-source submission witnesses, int64/UTC-us ordinary Subject axis, two delta and four contribution Findings, six fixed outputs and exact zero-Run/kernel cold recovery. Other axis/version/state profiles, remaining K and V15 termination are separate obligations. |
| R9.4 native nine-method recovery | `test_r94_statistical_recovery.py` and `r94_statistical_recovery_worker.py`: ordinary int64 DuckDB/SQLite/PostgreSQL/MySQL tables, Trino Iceberg and local ClickHouse MergeTree; full composite keys above 2**53, UTC-us daily observations, nine source methods/new realizations, nine independent fixed and nine cold kernels per producer, zero-Run/kernel exact hits, source/Semantic/DuckDB forbidden and exact descriptor/receipt/parts/Findings snapshots. Remote Group runs uses five exact native int64 UTC-us declarations. Other forms/carriers/time/K/fault profiles remain separate. |
| R9.4 archived native statistical corruption | `test_r94_statistical_faults.py` and `r94_statistical_portable_worker.py`: actual hashed ClickHouse archive, nine source/fixed methods, 278 backing/descriptor faults and 695 typed public recovery/read/exact-hit refusals; zero Runs/publication changes/source/kernel/admission calls, exact restored snapshots. Separate fresh process reads 27 archived outputs and hits 18 fixed/cold graphs without kernels. Deep semantic parts, transactions and other producer/profile grants remain separate. |
| R9.4 archived statistical semantic/Finding authority | `test_r94_statistical_authority.py`: actual ClickHouse source/fixed parts, every missing/malformed semantic part plus policy version/cap/binding, captured scope and output key; 242 shared-exchange refusals. Committed Finding digest/body/order faults produce 108 public digest/page refusals; read effects preserve injected counts and restoration recovers full original snapshots. No source/kernel/Run/admission activity; re-signed backing and transaction failures remain separate. |
| R9.4 archived native statistical direct K | `test_r94_statistical_k.py` and `r94_statistical_k_worker.py`: all nine actual ClickHouse source and fixed result cards, 54 owned views and 162 new source-free fixed selections/tables; independent key/value/interval oracles, retained fit/pair/training/future/condition parts and contract facts under empty/nonempty parent selection. Separate cold process exactly hits all 162 outputs with all source/statistical/continuation kernels forbidden and zero Runs/resources. A further independent process replays the separately unpacked 2186-file continuation archive and exactly matches all 162 outputs/54 views without new Runs/kernels. Ordinary int64/UTC-us producer only; other profiles and applicable continuation obligations remain separate. |
| R9.4 real file producer probes | `test_r94_producer_recovery.py::test_file_producer_offline_and_cold_continuation`: real Parquet/CSV/local JSON two-realization producers and independent fixed/cold quotient/exact hits after source file/scaffold database deletion. CSV/local JSON admission is limited to existing DuckDB files, int64 sum-zero and UTC-us event axes; `test_r94_file_admission.py` binds metadata-only construction, float-sum/mean and HTTP/request-parameter early refusals, plus the bilingual CSV executable example. Other carriers/methods/profiles remain unverified. |
| PostgreSQL fixture setup concurrency | `test_r94_postgres_fixture_setup.py`: four simultaneously started independent setup calls verify read-only reader privileges; session advisory lock serializes shared role/grant mutations. Fixture regression only, no product qualification. |
| R9.4 public refusal deadlines | `test_r94_public_refusals.py`: DuckDB table/view mixed live/fixed and cross-Session ratio composition in both directions; each required ratio endpoint/correspondence part missing during public recovery/exact hit. Structured expected/received/repair, untouched source/admission tripwires, unchanged Run/publication counts, original full snapshot and zero resources. Also real two-DuckDB datasource table/view observations and actual SQLite non-UTC unavailable routes, with exact owning refusal reasons and zero native business submissions. All five original refusal IDs have precise public witnesses; other producer parts/shapes/profiles remain independent. |
| R9.4 ordinary view producer recovery | `test_r94_producer_recovery.py::test_view_producer_offline_and_cold_continuation`: real DuckDB/SQLite/PostgreSQL/MySQL views, two independent source realizations with direct view SQL/driver matching, composite large int64 identities, independent fixed/cold quotient execution and exact hits with source/Semantic/DuckDB forbidden. Ordinary Entity/NoTime scalar quotient only; other families/K/shapes/faults remain independent. |
| R9.4 native pending graph expiry | `test_r94_native_graph_deadline.py`: canonical public ordinary int64 reads, schema-preserving slow stage SQL, failed Run with unchanged Artifact/Evidence/Findings and original snapshot, owner-thread source cleanup, PostgreSQL 57014/PID disappearance, MySQL prepared owned KILL and both connection IDs disappearing, Trino USER_CANCELED and ClickHouse 159/zero active queries. Independent server observations remain separate from remote_unknown receipts; real pending SIGINT binds active-before-signal identities, KeyboardInterrupt and server termination without rescue. Provider phase mechanisms are completed by the finite transport checks below rather than per-family replay. |
| R9.4 MySQL and Trino graph transport phases | `test_r94_graph_transport_phases.py`: real MySQL fetch timer/SIGINT retain issued-read ownership after submission failure, exact owned KILL, real native close failure and subsequent external-owner disconnect acknowledgement. Three Trino fetch timer/owner-checkpoint/SIGINT cases observe RUNNING before cancellation and FAILED/USER_CANCELED afterward. The repaired Trino 483 initial-response case records queued registration without dispatch/cursor ID, first timer no-op, then the original driver's dispatch and exact-ID cancellation. All preserve complete prior parts, one failed Run, unchanged publication and zero resources. |
| R9.4 local native interruption | `test_r94_local_graph_interrupts.py`: actual DuckDB pending SIGINT/deadline and SQLite pending/fetch SIGINT pass without rescue. SQLite's scoped main-thread/default-handler relay interrupts its native read; original interruption chains, owner-thread cleanup, full previous primary/parts, one failed Run and zero resources remain asserted. The final native/local and Trino increment contains 19 unique passed cases. |
| File integrity, complete-input limits, atomic publication and crash recovery | Local-file and engine-specific Runtime checks |
| SQLite publication interrupted inside its transaction | `test_lazy_materialization_store.py::test_process_exit_preserves_atomic_publication`, with actual child exit and a reopened Store; `test_analysis_graph_publication_r44.py` owns current graph process-exit and publication faults. |
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

R9.5 physically removes the old SQL adapter tests and routes their still-current
assertions to `test_datasource_adapter_contract.py`, `test_r92_source_profiles.py`
and current graph publication/recovery tests. Seven independent SQLite temporal
oracles remain in `test_lazy_temporal_backends.py`, now through SourceSession.
`test_r95_sql_audit.py` guards the whole product tree and physical retirement;
`test_r95_driver_audit.py` verifies exact native/Store ownership.
`test_r95_sql_runtime.py` captures six small public native witnesses, scoped HTTP
credentials and the actual owned ClickHouse control SQL. Business methods,
physical-profile and resource/cold evidence retain their original R9 owners.
The [R9.5 SQL ledger](../superpowers/specs/2026-10-06-marivo-r95-sql-ledger.md)
maps all original DS/AN rows and the appended control obligation.

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

- `test_datasource_adapter_contract.py`: current source-issued Ibis parameters, repeated
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

R9.4 versioned ownership additionally binds `test_r94_versioned_ownership.py`:
shared snapshot/validity numeric/category/boolean/temporal predicates from another
Session refuse in both directions and logical/fixed modes before SourceSession
entry/bind/compile/batches or Store admission. The formal public-versioned-refusals
packet passes all eleven existing public refusals and both new versioned cases,
with 32 exact structured ownership refusals, unchanged Run/publication counts,
preserved complete keys/parts/original values and zero resources. This proof owns
the shared predicate boundary; it grants no additional backend execution profile.

The R9.4 finite Anchor/retention assertion map additionally records 37 shared
Runtime cases for Omega/bounds, elapsed/calendar/DST, overlapping original uses,
source-before-local, starts-only refusal, receipt/semantic-part corruption and
local publication failure. At the same candidate, nine shared Store/resource
fault cases bind transaction/ack/close/bad-batch/deadline behavior, and two
nonempty Finding positives plus eight extraction/publication faults bind the
nonempty atomic boundary. All 56 pass; injected local clocks and cancellation
remain distinct from native remote termination and producer-specific recovery.

R9.4 finite temporal audit: `shared-temporal-risks-current-01` retains four
failures (two exact Tokyo-report registration gaps, two stale DST error matches).
`shared-temporal-risks-repair-01` binds 32 current Runtime cases and the
shared-temporal binder additionally checks nine pure temporal cases. DuckDB
native-table microsecond/string-Subject metric.observe and int64 original
state_rollup are now connected under Tokyo report authority; native DATE and
parsed timestamp grids use held-aware scope endpoints for report comparisons.
`native-temporal-zone-recovery-04` additionally passes four DATE/timestamp ×
New York/Tokyo report profiles (22.75 s), each with independent source/fixed/cold
processes, removed models/database, forbidden source and Semantic reconnect,
full eight-row Subject/grid keys and null reasons, int64 Arrow schema and
required parts, exact full/partial grid cells, one new fixed Run/kernel and
zero cold Runs/kernels/resources. `native-temporal-zone-bindings-01` verifies
immutable attachments and production-unchanged candidate delta and rejects
eight false counterclaims. This exact producer scope does not close full C06,
V04 or R9.4.


R9.4 finite statistical audit: `shared-statistical-risks-current-01` binds
102 Runtime cases including 63 retained numeric/identity/algorithm risk IDs,
nine supporting Cell controls and current public fit/grid/pair/coverage/K
controls. Five full-grid/Duration Unknown controls and three Journey/History
Unknown controls have separate actual producer ownership. A separate pure
invocation binds 101 independent equation/classification/admission checks.
`shared-statistical-bindings-01` verifies exact function counts, attachments,
source/oracle snapshots and raw Unknown facts and rejects eight false claims.
Historic six-native recovery, archived direct-card K, semantic/receipt/Finding
faults and shared Store/resource faults retain their distinct candidates and
proof scope. Current six-native ordinary int64 producer impact reruns pass at one candidate;
native-statistical-current-bindings-01 verifies exact schemas, complete keys,
numeric results, parts, routes, Findings and independent source/fixed/cold
records, and rejects ten false claims. No count here
grants complete C14/V11/V12 or R9.4; see the finite assertions map and completion
audit for the concrete remaining binding.


R9.4 finite C07/C08 assertion audit: 37 current Runtime cases and 86 pure
checks bind pairing/correspondence/nested/time/one-to-one/complete-opportunity/
reference/early-read and numeric/weight/key/state risks. The separate binder
checks exact invocation counts and frozen fixture/source authority and rejects
five false claims. No new raw result records or full family qualification are
granted; legacy shared cold controls have a bounded state scope. Native
comparison/reference/subset producer recovery retains its separate evidence.

R9.4 actual standardization carrier recovery: six Runtime cases (175.75 s)
include four DOUBLE/DECIMAL(30,6) table/Parquet producers and two same-process
BIGINT controls. Each of the four producers binds five original Metric families
with complete component/count/coverage parts, exact strata/weights and retained
zero-weight Undefined ratio Cells. Independent source-off fixed processes create
five Runs/kernels; fresh cold processes add zero. Original snapshots/full Arrow
parts survive, and nine rehashed false claims are rejected. Lossless hashed gzip
transport preserves oversized attachment authority. Production code is unchanged;
older packets keep their own candidates and full C08/V05/R9.4 stays unverified.

R9.4 finite C09/V06 shared audit: 19 Runtime cases bind additive/raw-fact
allocation, common Other/hierarchy, required part/version refusal, bounded
offline selected views, static pre-read rejection, period/coarsened partitions,
contradictory basis, source realization/fixed reuse, Decimal reconciliation,
float propagated errors, typed Other ranking and three publication failure points.
The initial packet has 17 passes and two exact UTC-us fixed registration failures;
adding the connected source shape's fixed counterpart repairs both and the full
rerun passes. Ten explicit pure checks own exact carriers/component side terms,
overflow/finite/error/rounding boundaries. The binder pins seven frozen owners,
rejects six false evidence claims and grants no new raw result or native producer
record. Actual numeric carrier recovery, precise native impact and provider faults
retain separate ownership; complete C09/V06/R9.4 stays unverified.

R9.4 actual C09 carrier recovery: eleven table/Parquet int64/float64/Decimal/
Duration profiles retain all 46 method cases, grouped by producer initialization.
The formal run passes eleven cases (988.54 s), saving 138 original artifacts
and binding 46 independent fixed outputs plus 46 zero-kernel cold hits.
Full components/counts/denominators, independently allocated side terms, original
target/basis/reconciliation, literal Other masks and exact Arrow scalar/tick
units survive; all source/fixed/cold allocation parts agree. Source files/models
are deleted and Source/Semantic reconnect is forbidden in both fresh workers.
Twelve rehashed/authority false claims are rejected; immutable originals pass
again. Only the owning numeric test and new worker change; production and all
other shared attribution assertions remain byte-identical. Historical broad/
site/native packets retain their candidates, and precise native impact/provider
faults and complete C09/V06/R9.4 still need their independent closure.

### R9.4 finite C10 shared distribution evidence

The current `shared-distribution-bindings-01/assertions.md` maps 32 Runtime
checks from numeric_r56/recovery_r57, 45 pure definition/typing controls and two
SQLite float/unbounded pre-read refusals from r93_distribution_consumers.
Shared assertions do not relabel the historical six-native int64 producer
recovery or grant complete DOUBLE/Decimal carrier recovery. The old three-process
fixture remains bounded; source-owned native quantile precision is preserved.

The later distribution-carrier recovery packet adds three actual DOUBLE
table/Parquet and Decimal(18,2) quantile setups: 13 originals and 65 licensed
fixed/cold outputs with full snapshots/parts and zero cold execution. Decimal
row.sum/mean remain explicit unqualified pre-Run refusals; count/min/max success
does not grant their algorithms. Shared/natively historical packets keep their
original candidate authority.

The current six-native C10 impact pair reruns 22 ordinary bounded int64
distribution declarations after the four-file production delta, with 110
independent fixed/cold outputs and zero cold execution. Frozen owner and
schema/receipt/disclosure bindings preserve original candidate authority.
This does not close provider-specific failure/cancellation phases, blocked
exact declarations or unqualified Decimal current-row sum/mean.
