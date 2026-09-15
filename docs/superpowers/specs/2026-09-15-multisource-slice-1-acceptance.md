# Multi-datasource Slice 1: DuckDB execution adapter

Date: 2026-09-15. Base: `dc1f3981989d2f96c0ce0d8be29d257fb6874947`.

Status: **Slice 1 complete**, including the focused, fixed-snapshot Runtime and
broad gates below. No remote backend is activated; registry/domain compatibility
changes remain Slice 2.

Verified environment: Python 3.12.13, DuckDB 1.5.3, Ibis 12.0.0, Arrow 25.0.1,
SQLGlot 30.8.0. Dependencies are unchanged.

## Implementation boundary

- `materialization/execution.py` owns the private transaction realization,
  immutable statement (SQL, typed parameters, Arrow schema, role and declared
  preparation dependencies), scalar-row and batch-stream interfaces.
- `duckdb_execution.py` owns native schema queries, compile-to-submit transport,
  explicit Ibis preparations, JSON/Parquet readers, temporary relations,
  numerical macro setup, deadline interruption and strict transaction close.
  `duckdb_statements.py` owns native sampling, membership, attribution and
  Lifecycle proof SQL. These modules are not public exports or a new registry.
- `admission.py`, validation and publication helpers consume the adapter.
  Compiled expressions are cached only within the owned action; execution
  methods accept immutable statements and never call an Ibis expression-based
  execution API. Every source validation remains a separate ordered query.
  Wrapping a SELECT as CTAS or a compound proof retains its preparation inputs.
- In-memory tables and actual Python/Arrow UDFs are reserved before registration;
  builtin UDFs create no registration resources. UDF registration identity is
  distinct from each expression's arguments. Unsupported spatial extension
  preparations are rejected, rather than loading an undeclared extension.
- The retained native domain remains explicit. Object-backed readers are frozen
  inside the owned native connection, with no remote upload capability. Cold
  Lifecycle inspection keeps its isolated native worker. The existing coverage
  provider continues to receive its owned native connection inside the adapter
  deadline; its provider contract is unchanged.
- Reader closure includes the actual driver reader even before iteration or
  after early abandonment. Deadline cancellation joins its timer. Termination
  proof follows successful adapter close; failed close remains unresolved.
  No store generation, logical identity, public Help route or backend support
  claim changes.

## Acceptance evidence

Verification is serial for Runtime suites. The accepted fixed-snapshot runs are:

| Gate | Scope | Result |
| --- | --- | --- |
| Focused tests | Adapter, validation batches, native membership boundary | 46 passed in 5.22s |
| Runtime A | Actual statement submissions, sampling, distinct/distribution private state, source/adapter/local independent cold journeys | 76 passed in 195.81s |
| Runtime B | Shared source execution, retained parts, failure recovery, backend rejection economics, Lifecycle/coverage/driver helpers, real process crashes | 170 passed in 290.30s |
| Broad `make check-agent` | Formatting, lint, import contracts, 335 typed source files, default tests, API docs | Passed; 4,920 tests in 87.89s; API docs built successfully |
| Modified test typing | Seven changed test modules | Passed |

Focused command:

```sh
make test TESTS='tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_validation_batches.py tests/test_lazy_distinct_retained.py'
```

Runtime A command:

```sh
make runtime-test RUNTIME_WORKERS=1 TESTS='tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_population_sampling.py tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py tests/test_lazy_adapter_runtime_acceptance.py tests/test_lazy_local_runtime_acceptance.py tests/test_lazy_source_runtime_acceptance.py'
```

Runtime B command:

```sh
make runtime-test RUNTIME_WORKERS=1 TESTS='tests/test_lazy_materialization_execution.py tests/test_lazy_materialization_failures.py tests/test_lazy_retained_runtime.py tests/test_lazy_retained_membership.py tests/test_lazy_execution_economics.py tests/test_lazy_lifecycle_runtime.py tests/test_lazy_lifecycle_reducers_runtime.py tests/test_lazy_lifecycle_coverage_runtime.py tests/test_lazy_event_coverage_runtime.py tests/test_lazy_driver_runtime.py tests/test_lazy_materialization_runtime_acceptance.py'
```

Focused probes cover exact driver SQL/parameter submission, execution with
compilation disabled, scalar cardinality checks, default-limit independence,
empty/Decimal/timestamp/nested Arrow results, declared hooks, composed fences,
builtin and Python UDF registration identity, stale realization rejection,
reader closure, deadline joining and failed-close termination authority.

Existing Runtime owners cover source algebra and sampling, JSON captures,
private membership/retained continuation, atomic failure/publication, source-free
binding hits, native Lifecycle/driver helpers and independent cold processes.

## Independent review

A clean-context reviewer reproduced two preparation defects in the initial
implementation: CTAS lost its input hooks, and multiple calls of one builtin
UDF attempted duplicate journal reservations. The implementation now propagates
explicit statement dependencies, skips builtin registrations and deduplicates
actual UDF registration identity. Real DuckDB regression probes cover both.
Re-review also found an unmigrated native inspection caller and recursive nested
UDF registration before the child reservation. Inspection now uses its native
adapter, and actual UDF registration handles one declared node at a time. The
final clean-context read-only review reported **No findings** after all four
repairs. Test gates remain recorded separately from that review verdict.

The existing `test_fresh_adapter_journey_and_cold_binding[engine]` expected zero
transferred rows, although this journey writes four primary rows to Parquet.
The reviewer reproduced the same failure in an isolated `git archive dc1f398`
with the archive's own import path: **1 failed in 7.95s**, actual transfer count
4. The assertion now expects four rows and retains the no-local-worker,
source-membership reuse, exact output, cold binding and journal cleanup checks.

Earlier development runs exposed outdated private monkeypatch locations and a
missing test import, which were corrected. One cold-process run also rejected
a changing code manifest while implementation was still underway; it is not
accepted as a stable-snapshot result. Final acceptance uses a fixed code snapshot: both Runtime groups and the broad gate passed, with 246 Runtime cases in total.

## Remaining boundaries

Slice 1 does not implement multiple registrations, remote realization variants,
server qualification or remote cancellation/recovery. Existing non-DuckDB
rejections remain required. No dependency upgrade, service startup, release,
commit or push is part of this slice.
