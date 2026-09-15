# Multi-datasource Slice 1b: calling-process execution without resource budgets

Historical evidence: this record describes the named slice's implementation.
Slice 1c supersedes remote termination certification, compile-count and version
admission requirements and uses Store v5. These results are not 1c acceptance.


Date: 2026-09-15.

Status: complete. Focused Runtime, recovery, typing/lint and the broad gate passed.
No backend is enabled. Slice 1c remote cleanup/version/compilation simplification
remains separate work.

## Ownership and replacement

- `local_execution.py` owns complete typed local graphs and numerical invocation
  in the calling process. Adjacent stages retain private DataFrame handoffs.
- Worker spawning, IPC, supervision threads, RSS probes, watchdogs, worker-only
  workspace/lifetime resources and read/inspection workers are removed.
- Source and native retained execution, Arrow transport, local methods,
  Parquet/object writes and complete retained collection have no Marivo resource
  caps. Chunk/row-group sizes tune transport; preview lengths bound display only.
- Budget-only source width probes, injected DuckDB memory/spill settings,
  deadlines, Association candidate ceilings and distribution player ceilings are
  removed. Mathematical validity, ownership, alignment, source-private state,
  explicit analytical parameters and complete publication remain enforced.
- Original exceptions, causes and tracebacks reach the caller. Safe journal
  summaries remain distinct; cleanup failure cannot replace the original error.
  Unknown connection termination still follows the pre-1c recovery contract.
- Store generation 4 rejects unsupported versions before mutation. Old generation
  files remain untouched; no migration or worker-obligation compatibility path
  is introduced. Opening a Session is a metadata read; the next writer reconciles
  incomplete publication under the existing writer lock.

## Replacement evidence

- More than 100,000 complete local input/output rows and a full 100,004-row
  retained inspection/read exercise former row ceilings without truncation.
- Wide nested dictionary values and large primary/binary retained parts exercise
  lossless normalization and complete Parquet reads beyond the former batch cap.
- Caller PID and forbidden process-spawn probes cover local kernels and full
  retained collection. Independent Forecast/Candidate validation and graph
  lifetime tests retain mathematical and complete-input assertions.
- Nine mapped distribution players and more than 4096 Association candidates
  replace former complexity-rejection tests; explicit Top-K meaning is retained.
- Test-only SIGKILL of the calling interpreter covers computation, post-rename,
  pre-commit and post-commit recovery. Only complete Store publication is reused.
- Worker IPC/watchdog/RSS and resource-overflow assertions are retired with their
  protocols. Prior Slice 0/1/1a worker or budget results remain historical evidence.

## Verification

Verified locally on macOS, Python 3.12.13, with existing repository dependencies.
No dependency, backend activation, commit, push, release or external service launch
was part of this slice.

| Gate | Result |
| --- | --- |
| Focused immutable storage tests | 26 passed, including wide values and cleanup preserving the original exception/cause |
| Materialization and new storage/caller tests typing | 54 files passed |
| Additional changed local/sampling/crash helper typing | 5 files passed |
| Serial related Runtime suite below | 293 passed in 215.78 seconds |
| Final recovery recheck after deleting the unused worker-era Store argument | 44 passed in 65.75s (0:01:05) |
| Final `make check-agent` | Lint, import contracts, typing of 333 source files, 4880 default tests and API documentation build passed |

The 293-case Runtime command was:

```sh
make runtime-test-agent RUNTIME_WORKERS=1 TESTS='tests/test_lazy_population_sampling.py tests/test_lazy_adapter_parts.py tests/test_lazy_correlation_failures.py tests/test_lazy_in_process_execution.py tests/test_lazy_materialization_runtime_acceptance.py tests/test_lazy_adapter_crash_acceptance.py tests/test_lazy_local_runtime_acceptance.py tests/test_lazy_materialization_failures.py tests/test_lazy_candidate_failures.py tests/test_lazy_forecast_failures.py tests/test_lazy_distribution_runtime.py tests/test_lazy_event_coverage_runtime.py tests/test_lazy_lifecycle_coverage_runtime.py tests/test_lazy_distinct_runtime.py tests/test_lazy_compare_runtime.py tests/test_lazy_local_execution.py tests/test_lazy_retained_runtime.py tests/test_lazy_materialization_execution.py'
```

The final recovery command was:

```sh
make runtime-test-agent RUNTIME_WORKERS=1 TESTS='tests/test_lazy_materialization_runtime_acceptance.py tests/test_lazy_adapter_crash_acceptance.py tests/test_lazy_in_process_execution.py tests/test_lazy_materialization_failures.py'
make check-agent
```

Implementation files remained unchanged during each final cold-process acceptance
run. The completion status and this evidence table were recorded after all gates
passed; those final edits change documentation only. These are focused development
gates, not full release or new-backend acceptance. Slice 1c remains pending.
