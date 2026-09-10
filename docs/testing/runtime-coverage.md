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
| S3 versioning, conditional PUT, exact VersionId reads, missing access, cleanup ownership and unknown request results | `test_lazy_object_storage_contracts.py` and `test_lazy_object_access_boundaries.py`, with native SDK stubs and real local SessionStore files |
| Acknowledgement without durable request discharge | One fresh-process Runtime check, using the local journal and a stubbed SDK acknowledgement |
| Live object connector | `test_object_storage_connection.py`: real SDK connection, versioned writes, fixed-version read and deletion |

The former MinIO analysis/fold/inspection matrices and remote crash/proxy scenarios
are removed. Object-specific protocol contracts remain at the SDK and journal
boundaries; the connector smoke does not claim to reproduce remote crash timing.
Local/engine crash checks continue to exercise actual process loss and publication
recovery. Historical Slice acceptance records describe their original candidates;
they are not the current recurring test matrix.

## Commands

- `make test`: daily contracts and SDK-boundary regressions; no external object service.
- `make runtime-test TESTS='tests/test_lazy_local_execution.py'`: focused functional checks.
- `make runtime-test`: complete functional Runtime selection when explicitly needed.
- `MARIVO_TEST_S3_ENDPOINT=http://127.0.0.1:9000 make object-storage-test`: isolated
  versioned object connector smoke. The fixture owns a unique bucket and its cleanup;
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
