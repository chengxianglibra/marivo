# Multi-datasource Slice 1d: read-only datasource execution

**Superseded:** this record preserves the original blanket read-only candidate and
its tests. The user subsequently corrected the boundary: DuckDB internal resources
are allowed, operators share one abstraction, and remote read-only compatibility
is qualified per backend. See [restoration acceptance](2026-09-15-multisource-slice-1d-restoration-acceptance.md)
for current behavior. The completion status below is historical.


Date: 2026-09-15.

Status: complete. Implementation, independent review, focused Runtime checks,
`make check-agent`, site content verification and site build passed.

## Candidate and scope

Base HEAD: `01a8403f8032a63073a9f4590401e60efbe1a284`, branch `lazy-dataset`.
Implementation started on the existing dirty workspace, preserving earlier Slice
1a–1c and Slice 2 work. The baseline tracked diff was saved as
`/tmp/marivo-1d-baseline.patch`. No commit, publication, dependency change or remote
analysis activation is part of this slice.

The qualified execution scope is DuckDB analysis and DuckDB datasource file reads.
Other authoring profiles are not certified by this evidence. Their existing setup
and timeout operations require separate qualification before analysis activation.
Independent Store/Parquet and object-output writes retain their own ownership.

Final executable manifest: 728 files, SHA-256
`d20fc5c66fe658e9d32357b352af0b0a9abd65ffb7952c38d81e71a678f36c43`
(using `tests.test_lazy_adapter_runtime_acceptance._manifest`).

## Implemented contract

- DuckDB connections use the datasource-owned Ibis backend without setup SQL,
  automatic extension installation/loading or Python replacement scans.
- Native execution compiles parameterized expressions and reads Arrow directly;
  memtables, non-builtin UDFs and registration-dependent expressions are refused.
  Marivo no longer calls Ibis registration hooks or creates macros/secrets/views.
- File readers use direct table-function queries; local Parquet scans retain exact
  immutable receipt authority. Native Arrow conversion preserves declared types.
- Existing placement/admission/compiler owners reject sampling, Event/Lifecycle
  fence-dependent operations, source-native Entity/Driver Candidate, Analysis JSON
  source fences and native object-stream imports before a Run/source submission.
  Local time-series Candidate and independently registered local Driver remain
  available. Rejection does not choose a different execution route.
- Historical temporary-object reservations emit a non-deletion diagnostic. Local
  safety permits another Session action; this does not certify source deletion.
  Reader cancellation/close, writer exclusion, atomic publication and unsafe object
  write recovery retain their independent responsibilities.
- There is no general-purpose SQL inspection or security layer. Test-only SQL AST
  assertions classify captured Marivo operations; production changes the operation
  generators and existing expression eligibility owners.

## Evidence owners

`tests/test_lazy_readonly.py` wraps the real driver from connection creation,
records SQL through `execute`, `sql` and `query`, and fails on registration, UDF,
extension and bulk-write APIs.
It verifies actual direct file results, source publication/failure cleanup and
pre-Run refusal. `test_lazy_duckdb_execution_adapter.py` covers exact parameters,
no hidden hooks, Arrow conversion, mandatory checks and reader closure.
`test_lazy_reconciliation_snapshot.py` checks historical non-deletion diagnostics
and same-Session recovery. Retained/local Runtime suites cover source-offline
reads, complete primary/parts, atomic failures and cold binding.

Obsolete fence-producer success chains are retired without skip/xfail. Independent
compiler algebra, numeric references, codecs and storage checks remain; their
fixture DDL is test provisioning, not Marivo execution acceptance. Duration
preview now uses an independently provisioned Event receipt. Historical acceptance
records remain unchanged as evidence for their original candidates.

## Validation

| Gate | Result |
| --- | --- |
| Focused storage, sampling identity, dispatch and reconciliation | 91 passed |
| Affected Runtime selection across 16 files | 167 passed; four obsolete assertions subsequently repaired and rerun below |
| Runtime repair selection: readonly, artifact adapters, forecast, retained review | 27 passed in 10.97s, including all four prior failures and the restored 20,000-row test |
| Source construction without I/O, bilingual examples, validation preparation | 20 passed |
| Final driver capture after adding sql/query and executemany observation | 2 daily tests and 10 Runtime tests passed; Runtime 2.49s |
| Final `make check-agent` | Lint/import contracts, typing of 334 source modules, 4921 default tests in 111.53s and API docs passed |
| `npm --prefix site run verify:content` | 343 required files verified |
| `npm --prefix site run build` | Sphinx, Astro check (zero errors/warnings), 321 pages and install-script verification passed |
| Final whitespace and capture-test lint | Passed |

Runtime repair command:

```sh
make runtime-test TESTS='tests/test_lazy_readonly.py tests/test_lazy_artifact_adapters.py tests/test_lazy_forecast_runtime.py tests/test_lazy_retained_review_runtime.py'
make runtime-test TESTS='tests/test_lazy_readonly.py'
```

The larger focused Runtime run also included adapter, local execution, retained
execution/cold processes, local Candidate, independent Driver, materialization,
parts, attribution, comparison, distinct, distribution and integrity inspection.
It exposed stale zero-transfer expectations for local Parquet output and an old
Forecast exception wrapper; the repair selection verifies the exact row counts
and current structured error. Broad checks exposed and repaired the bilingual
example count, moved JSON reader hook owner and old sampling-fence expectation.
No full Runtime/release/object-service gate was run or claimed.

## Independent review

A separate subagent reviewed the actual code and owning contracts. Fixed findings
include hidden Ibis mutation hooks, historical temporary-resource recovery blocking
safe work, and stale producer success tests. Its follow-up identified an unrelated
20,000-row retained regression removed during test migration; that test is retained.
Final independent re-review reported no remaining concrete P1/P2 findings. It
also reviewed the final sql/query capture addition. Review was static; the test
results above were executed by the implementing agent.
