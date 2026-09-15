# Multi-datasource Slice 1a: source execution without consistency transactions

Date: 2026-09-15. Implementation base: `75a8e560d` (Slice 1 adapter extraction).

Status: **Slice 1a complete**, including focused, Runtime and broad verification. No remote backend
is enabled. Slice 1b, Slice 1c and Slice 2 remain separate pending work.

## Implementation and ownership inventory

| Owner / mechanism | Purpose and Slice 1a disposition |
| --- | --- |
| Source action BEGIN / success ROLLBACK / cleanup rollback exception matching | Removed: existed only to align independent source reads |
| `ExecutionContext` / Statement context identity | Retained as exact action-local ownership; replaced transaction realization; copied or foreign contexts and closed execution are rejected |
| Execution initialization | Retains UTC, threads, memory and spill settings; no transaction is opened; budget removal belongs to 1b |
| Adapter finish / resource termination proof | Close the actual owned connection; failed close still prevents termination proof and resource discharge; recovery simplification belongs to 1c |
| Sampling and compiled relation fences | Preserve one evaluation and shared consumption of the declared computation; temporary tables remain connection-owned |
| JSON capture/view and retained Arrow/Parquet fences | Preserve frozen input and native retained-data authority; no remote upload or new source binding |
| Native numerical macros / Ibis preparation hooks | Keep connection-local temporary resources and reservation before creation; no rollback is needed for cleanup |
| Semantic snapshot/validity row selection | Preserve business version semantics; independent of source transaction consistency |
| Store SQLite publication/reconciliation transactions | Preserve atomic primary/parts/Evidence/Findings publication and journal integrity; never removed as source-consistency logic |
| Datasource driver/authoring transactions | Unchanged, outside Analysis action-wide consistency; no new driver transaction introduced |

No transaction-realization variants or snapshot-equality admission gates were
present beyond the extracted private context type and wrapper. Historical Slice
0 remote qualification experiments are not current admission requirements.
Public API signatures, exports, storage format and backend support are unchanged.

## Behavioral evidence

- Real DuckDB connections observe a controlled committed update between reads;
  no sleeps or simulated query results are used.
- A Runtime query sequence updates one source row after required checks and
  before the primary query. It publishes the new total, admits one connection,
  submits one primary query, clears resources and preserves an exact binding hit.
  DuckDB requires matching file access modes: this fixture opens both physical
  connections writable for the independent update while asserting that Runtime
  requests read-only access. Production connection policy is unchanged.
- Failed source assertions still prevent output, including a filter that would
  produce no rows. Existing tests retain scalar decoding rejection, sampling
  reuse, stable output, primary/part publication, cancellation and cold recovery.
- Initialization and finish submit no consistency transaction SQL. Forged and
  foreign contexts, foreign composed inputs and execution after close fail.
  Close failure remains unproved until actual close succeeds.

## Verification

| Gate | Result |
| --- | --- |
| Focused adapter, numeric, validation, retained and disclosure tests | 107 passed in 7.41s |
| Modified source and test typing | 8 files passed |
| Runtime A: execution, failures, sampling, retained reuse, backend rejection, driver | 126 passed in 146.92s |
| Runtime B: adapter regressions, cold journeys, Lifecycle, coverage, process recovery | 91 passed in 140.06s |
| Packaged skill shape and live Help | 16 passed in 1.59s; live `analysis.actions.execute` resolved |
| Broad `make check-agent` | Passed: lint/import contracts, 335 typed source files, 4,919 default tests in 67.13s, API docs built |

Runtime suites run serially with one worker. The two groups overlap on the
adapter diagnostic and controlled-update Runtime tests; counts are per gate.
Historical Slice 1 results are not reused as Slice 1a acceptance.

Commands:

```sh
make test TESTS='tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_driver_compiler.py tests/test_lazy_validation_batches.py tests/test_lazy_distinct_retained.py tests/test_lazy_disclosure.py'
make runtime-test RUNTIME_WORKERS=1 TESTS='tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_materialization_execution.py tests/test_lazy_materialization_failures.py tests/test_lazy_population_sampling.py tests/test_lazy_retained_runtime.py tests/test_lazy_retained_membership.py tests/test_lazy_execution_economics.py tests/test_lazy_driver_runtime.py'
make runtime-test RUNTIME_WORKERS=1 TESTS='tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_adapter_runtime_acceptance.py tests/test_lazy_source_runtime_acceptance.py tests/test_lazy_lifecycle_runtime.py tests/test_lazy_lifecycle_reducers_runtime.py tests/test_lazy_lifecycle_coverage_runtime.py tests/test_lazy_event_coverage_runtime.py tests/test_lazy_materialization_runtime_acceptance.py'
make typecheck TYPECHECK_TARGETS='marivo/analysis/compiler/driver_numeric.py marivo/analysis/datasets/base.py marivo/analysis/datasets/_disclosure.py marivo/analysis/materialization/admission.py marivo/analysis/materialization/duckdb_execution.py marivo/analysis/materialization/execution.py tests/test_lazy_driver_compiler.py tests/test_lazy_duckdb_execution_adapter.py'
make test TESTS='tests/test_packaged_skill_shape.py'
make check-agent
```

The first controlled-update test run used the unpopulated `order_id` fixture
column and therefore changed no rows. The corrected predicate uses the actual
`id` key; the test now proves the total changes from 147 to 157. An import-order
lint issue and a test import typing issue were also repaired before final gates.
No production fallback or relaxed acceptance was introduced for these repairs.
