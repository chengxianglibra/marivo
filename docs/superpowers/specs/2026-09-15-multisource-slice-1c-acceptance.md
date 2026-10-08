# Multi-datasource Slice 1c: execution and recovery simplification

Date: 2026-09-15.

Status: complete. Implementation, independent review and acceptance, broad
checks and the site build passed. No new backend is enabled.

## Scope and ownership

Base: `8ce32bd9b3c0ed026d004ff01d16b495797e57e5`, branch `lazy-dataset`.
The worktree was clean at implementation start; earlier staged Slice 1b work had
already been committed. No commit, push, dependency change, external service
startup or release belongs to this slice.

- Normal Ibis expression reads retain compilation, parameters and preparation
  hooks. The action-owned DuckDB adapter observes actual raw submissions and
  restores its tracing hook even on failure. Useful explicit SQL/fence paths
  remain private; expressions need not first become immutable Statements.
- Primary transport passes `limit=None`, closes owned readers and preserves the
  original failure when cleanup fails. Required validations and single-evaluation
  fences retain their order. Repeated pure compilation does not submit a query.
- Engine/Ibis version fields are removed from method registrations, source and
  native retained binding identity, eligibility and preflight. Method contracts,
  business-time selection, immutable-input authority and storage versions remain.
- Read-only execution obligations use an action nonce, not process death proof.
  Failed cancellation/close or no query ID may leave remote status unknown;
  safe local recovery allows another Session action without automatic resubmission.
- Writer guard, commit readback, exact output ownership and primary/part integrity
  still govern recovery. Unknown S3 write requests retain their independent proof
  gate; read-only discharge cannot bypass them. Failed Runs mean no successful
  local publication, not certified remote death.
- Store v5 owns the new resource semantics. Old generation files remain intact;
  wrong-generation databases are rejected before mutation. There is no migration
  or dual read, and the execution-key algorithm is unchanged. Telemetry follows
  the v5 Store. Public Session guidance leaves generation facts with that owner.

## Verification

| Gate | Result |
| --- | --- |
| Focused adapter, placement, reconciliation, Store, S3 contract and packaged skill tests | 113 passed |
| Adapter/Store/disclosure follow-up, including close failure and prior-generation byte preservation | 56 passed |
| Telemetry/public Session/disclosure repair checks | 58 passed |
| Touched materialization/compiler/registry and test typing | 59 files passed |
| Independent serial Runtime selection | 60 passed in 51.86s |
| Independent final cancellation/close rerun | 1 passed in 1.45s |
| Independent fork/S3/version/reader-error boundary checks | 8 passed in 2.34s |
| Independent final telemetry repair regression | 2 passed in 1.28s |
| Site content verification | 343 required files verified |
| Final `make check-agent` | Lint, import contracts, typing of 333 source files, 4891 default tests in 80.52s and API documentation build passed |
| `npm --prefix site run build` | Astro checks/build and standard/Chinese install-script verification passed |

The independent Runtime command was:

```sh
make runtime-test-agent RUNTIME_WORKERS=1 TESTS='tests/test_lazy_materialization_failures.py tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_materialization_runtime_acceptance.py tests/test_lazy_adapter_crash_acceptance.py tests/test_lazy_in_process_execution.py tests/test_lazy_runtime_concurrency.py'
```

The final core-behavior rerun was:

```sh
make runtime-test-agent RUNTIME_WORKERS=1 TESTS='tests/test_lazy_materialization_failures.py::test_failed_cancel_and_close_preserve_failure_and_allow_later_action'
```

The independent boundary selection was:

```sh
make test TESTS='tests/test_lazy_materialization_guard.py::test_guard_reentrant_thread_and_process_contention tests/test_lazy_materialization_guard.py::test_fork_child_cannot_keep_dead_owner_session_locked tests/test_lazy_object_storage_contracts.py::test_unknown_sdk_timeout_blocks_only_the_owning_session tests/test_lazy_local_placement.py::test_diagnostic_version_does_not_change_selection_or_execution tests/test_lazy_duckdb_execution_adapter.py::test_expression_read_preserves_transfer_failure_when_reader_close_also_fails'
```

The independent final telemetry command was:

```sh
make test TESTS='tests/test_telemetry.py::test_restored_session_suppresses_successful_internal_load_declarations tests/test_telemetry.py::test_session_question_update_and_explicit_resume_semantics'
```

## Independent review and adversarial acceptance

Two agents that did not implement the change performed separate review and
acceptance (`review_1c_implementation` and `accept_1c_independent`). Both imported
or inspected the actual checkout, not the baseline archive. The reviewer also
exercised real DuckDB Decimal, timezone timestamp, struct, empty-result and
repeated Python UDF reads. Final static review reported no findings after repair
of stale Session generation guidance.

The acceptance agent additionally probed mixed read/write obligations: unknown
object writes preserve journal entries and exact staging; a fresh process cannot
reuse process-local S3 proof; known write termination allows guarded recovery.
Probe SHA-256: `9ac21b02d0e0e99ec04ce79f53090502da3a4b31db1c7ac15f71d3fdfe1ad3ae`.
Actual import: `/Users/lichengxiang/source/oss/marivo/marivo/__init__.py`.

The obsolete `transfer_guard` assertion was reproduced against baseline
`8ce32bd9` before replacement: it expected a budget-only query already removed
in 1b. The replacement checks actual primary submission count and separately
captures fences, validations, schema and other executed roles. A broad-gate
failure also exposed the telemetry v4 path, now covered by existing Session
creation/recovery telemetry regressions.

Final source/test diff SHA-256 relative to the base, using
`git diff -- marivo tests | shasum -a 256`:
`5b161df800bde260cf0f924bb90c9443184692affbf5d7ff07446069aee557fb`.
Subsequent status and evidence edits affect documentation only.

## Limits

These are real local DuckDB execution and cold-process publication checks, plus
injected driver/SDK failures. They are not live PostgreSQL, MySQL, SQLite, Trino,
ClickHouse or S3 acceptance. No administrative remote termination lookup is
required. Slice 0/1/1a/1b records remain historical evidence; Slice 2 and all new
backend activation remain pending. Full release Runtime and object-service gates
were not run.
