# Runtime test coverage

Functional acceptance uses local Parquet files. Native DuckDB cases remain where
engine execution, source-private membership/distribution, receipt integrity, or
engine process recovery is the actual contract. A storage-neutral scenario should
not acquire a new local/engine/object Cartesian product.

| Boundary | Owning checks |
| --- | --- |
| Analysis, compare, attribution, sampling, ordering, ordinary concurrency | Local-file Runtime tests with real DuckDB sources and isolated execution/read workers |
| Producer, continuation, and cold binding independence | Local-file fresh-process journeys; dedicated engine adapter/recovery journeys |
| File integrity, complete-input limits, atomic publication and crash recovery | Local-file and engine-specific Runtime checks |
| SQLite publication interrupted inside its transaction | `test_lazy_materialization_store.py::test_process_exit_preserves_atomic_publication`, with actual child exit and a reopened Store; the native adapter retains an `insert_terminal` crash journey |
| Retained attribution with a missing axis | `test_lazy_attribute_contracts.py::test_retained_missing_axis_rejects_before_any_action`, using trusted metadata and a port that forbids execution |
| Distribution method and input-state variants | Focused Runtime publication/authority tests; exact and approximate fresh-process journeys own cold continuation and result reuse |
| Private Event occurrence matching, temporal participants, coverage and exact membership | `test_lazy_event_runtime.py`, `test_lazy_event_temporal.py`, `test_lazy_event_coverage_runtime.py`, and `test_lazy_event_membership.py`; native DuckDB owns source-private matching and scalar proofs; real coverage-provider query interruption checks atomic failure and same-Session retry, with local-file and engine publication where storage authority differs |
| Event retained membership, source-offline journeys and cold binding | `test_lazy_event_runtime_acceptance.py`: separate producer, continuation and cold interpreters for local-file and engine journey storage; membership remains an engine relation and is never uploaded from a local identity collection |
| Private Event reducers and complete subject selection | `test_lazy_event_reducer_runtime.py` and `test_lazy_event_reducer_failures.py`: logical and retained engine funnel/TTE/selection, native high-cardinality identity, complete empty membership, per-attempt partial coverage, selection validation before filtering/sampling/Metric membership, and explicit Population enrichment with inherited sampling authority |
| Event reducer result storage and cold continuation | `test_lazy_event_reducer_runtime_acceptance.py`: separate journey producer, source-offline reducer/selection and cold binding interpreters exercise Metric -> Event -> Population -> Metric; `test_lazy_event_reducer_failures.py` covers local Parquet result filtering and canonical ordering after source deletion; `test_lazy_event_reducer_publication_runtime.py` covers shape-specific cold Evidence corruption and atomic commit/cancellation failures |
| Selected Population Metric enrichment and propagated TTE entry truth | `test_lazy_event_reducer_runtime.py::test_selected_population_metric_enriches_current_subject_dimensions`: logical, retained and sampled identity inputs admit explicit current subject Dimensions, while missing current subjects fail atomically; `test_lazy_event_reducer_publication_runtime.py::test_time_to_event_entry_coverage_propagates_and_anchor_is_observed`: a covered later from-step inherits earlier unknown entry through publication/cold recovery, while the observed anchor rejects forged entry-unknown Evidence |
| Private Event funnel comparison and loss-rate attribution | `test_lazy_event_comparison_runtime.py`: native and compact pandas execution over logical/retained journeys, target/censoring rejection (including cold filtered checkpoints on local and engine storage), governed axes named `positive`/`negative` with full source/local parity, combined local input budgets, aggregate checkpoint admission, atomic failure/cancellation, and cold mapped-component/Evidence corruption; independent numeric tests compare complete Ibis/pandas rows, masks, status, ordering, per-side endpoints and contributions, exact method roles and native/pandas duplicate-coordinate rejection |
| Event comparison source-offline recovery | `lazy_event_comparison_worker.py`: distinct producer, continuation and cold interpreters prove exact journey reuse after both occurrence tables are deleted, exact Delta/Attribution identity reuse and zero cold execution queries; optional `MARIVO_SLICE7C_EVIDENCE_DIR` captures PIDs and executable fingerprints |
| Private Lifecycle replay, mandatory canonical roles and exact membership | `test_lazy_lifecycle_runtime.py` and `test_lazy_lifecycle_coverage_runtime.py`: native inception lookback, source-origin versus bounded coverage, retained prefixes, logical/retained/sampled membership, subjects without intervals, per-role failure/cancellation, clean retry, privacy and source-native large inputs |
| Lifecycle source-offline history and canonical-part recovery | `test_lazy_lifecycle_runtime_acceptance.py` with `lazy_lifecycle_worker.py`: three independent interpreters compare primary and every required part, exact cold binding and zero source execution; `test_lazy_lifecycle_runtime.py` verifies corrupt model metadata produces integrity reports and typed cold-recovery errors; `MARIVO_SLICE7D_EVIDENCE_DIR` retains process and candidate evidence |
| Private Lifecycle reducers and complete InState selection | `test_lazy_lifecycle_reducers_runtime.py`: logical/recovered distribution, transitions, dwell and violations; checkpoint coverage, grouped retained membership, local result filtering and Metric -> Lifecycle -> Population -> Metric/Event; `test_lazy_lifecycle_reducer_failures.py`: exact role consumption, corruption, empty membership, atomic failure/cancellation and retry |
| Lifecycle reducer source-offline cold binding | `test_lazy_lifecycle_reducer_runtime_acceptance.py` with `lazy_lifecycle_reducer_worker.py`: separate producer, trigger-offline continuation and cold binding processes; original Population enumeration disabled, engine history with local/engine terminal outputs, exact Evidence/descriptor/row hashes and no cold source execution; `MARIVO_SLICE7E_EVIDENCE_DIR` captures candidate-bound process evidence |
| Lifecycle reducer adversarial acceptance | `test_lazy_lifecycle_reducer_review_runtime.py`: independent multi-subject grouped and rational-duration references, engine/local result and filter parity, declared ordering, 5,000-subject native transfer and identity canaries, same-plan single membership realization into Metric/Event/Lifecycle, registered native Parquet membership across local and independent DuckDB files, and pre-data rejection of unregistered adapter versions |
| Lifecycle object bundle ownership and exact versions | `test_lazy_lifecycle_object.py`: native SDK stubs, four version-pinned row sets, per-role upload failure/cancellation and complete owned-version cleanup; no external object service |
| S3 versioning, conditional PUT, exact VersionId reads, missing access, cleanup ownership and unknown request results | `test_lazy_object_storage_contracts.py` and `test_lazy_object_access_boundaries.py`, with native SDK stubs and real local SessionStore files |
| Acknowledgement without durable request discharge | One fresh-process Runtime check, using the local journal and a stubbed SDK acknowledgement |
| Live object connector and declared materialization paths | `test_object_storage_connection.py`: real SDK connection, versioned writes, fixed-version read and deletion; distinct/exact-distribution/T-Digest primary and private parts with source-offline continuation; Pearson/Spearman/Kendall publication and cold binding in three processes |

The former MinIO analysis/fold/inspection matrices and remote crash/proxy scenarios
are removed. Object-specific protocol contracts remain at the SDK and journal
boundaries; the connector smoke does not claim to reproduce remote crash timing.
Local/engine crash checks continue to exercise actual process loss and publication
recovery. Historical Slice acceptance records describe their original candidates;
they are not the current recurring test matrix.

Slice 9c adapter crashes target the local output reservation and actual Parquet
primary/private-part file creation before atomic rename and Store commit. The
test-only file-constructor hook observes real writer calls; it is not a product
event or an engine-result-storage path. Mutation checks cover replacement,
absence and same-length in-place changes to immutable Parquet payloads.
Registered Parquet membership scans are accepted across native source files.
Pandas lifetime/orphan and composed-read journeys explicitly preselect the
registered pandas method before execution, so native Parquet support cannot
bypass the worker boundary being tested. Public scoped-read acceptance separately
uses the default native route and real nonempty Findings in three processes.

## Commands

- `make test`: daily contracts and SDK-boundary regressions; no external object service.
- `make runtime-test TESTS='tests/test_lazy_local_execution.py'`: focused functional checks.
- `make runtime-test`: complete functional Runtime selection when explicitly needed.
- `MARIVO_TEST_S3_ENDPOINT=http://127.0.0.1:9000 make object-storage-test`: isolated
  versioned object connector and materialization gate. The fixture owns a unique bucket and its cleanup;
  the caller owns the service lifecycle.
- `make release-check`: daily, full functional Runtime, object connector, and packaging gates.

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
statistics, available worker RSS and timings. Test nodes remain the owners of
row, authority, repair and recovery assertions; this is not a support registry.
Adapter-internal metadata and storage wire request counts are uninstrumented,
not zero. Native primary/independent-part streams count decoded transfer once;
parts split from the primary batch are separate storage receipts, not additional
source transfers. Exact recovery still requires zero execution and zero copies.

Tests intended to exercise pandas preselect an absent source lowerer using the
existing implementation registry. Native Parquet continuations are separately
accepted. A successful basic SQLite/MySQL/PostgreSQL/ClickHouse/Trino query does
not register that backend for Dataset analysis. Ordinary Runtime tests use local
storage or SDK stubs; only the object gate requests `object_connection_access`.
Funnel comparison tests cover authored step order through publication and
filtering. Distinct temporal checkpoints validate physical coordinate types in
both the primary rows and independent membership parts before cold reuse.
