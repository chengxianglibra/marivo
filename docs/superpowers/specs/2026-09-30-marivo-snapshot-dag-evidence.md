# Unified graph snapshot DAG evidence

Date: 2026-09-30. Baseline: `377bc341f8` (R6.2 comparisons).
Status: implementation, measurements and scoped validation complete.

## Contract

The Runtime owner is [session state and runtime](../../specs/analysis/session-state-and-runtime.md#r62-comparison-encoding-and-execution).
All graph families share one identity-based DAG document. Retained definitions do
not enter the execution schedule. Public APIs, Store 7, numerical semantics and
state/part contracts remain unchanged. Old snapshots require source re-execution.

## Measurement method

The opt-in harness is `tests/benchmarks/graph_snapshot.py`, using the shared DSL
fixture with deterministic business rows and the same graph construction scenarios.
Each timed operation has one warm-up and three measured repetitions; times are
medians in milliseconds, not CI gates. Capture and Run identifiers remain generated
identities, so compressed sizes vary with generated identities and their sorted
table order. Reported byte counts are measurements, not fixed thresholds. Concurrent
host work makes timing indicative rather than an isolated hardware benchmark.

Measurements separate uncompressed definition JSON, embedded endpoint strings,
the minimal continuation envelope, and the complete published continuation. The
minimal envelope uses placeholder binding/receipt strings to isolate root encoding;
it is not an Artifact receipt or acceptance proof. Legacy fixed-definition JSON
contains already compressed endpoint strings, whereas the new JSON exposes their
records directly; raw JSON sizes for that scenario are not equivalent expansion
budgets. DAG node counts include retained definitions; old execution-only counts do
not include nodes hidden inside endpoint strings.

The shared series starts with a Difference and adds 1, 3 or 5 ordinary ratios of
each preceding result to itself. Same-time self-Difference is not a valid public
comparison, so it is not used as a stress fixture. Ratio and linear scenarios use
original Runtime metric expressions. No benchmark introduces source reads during
construction.

Reproduce the current implementation:

```sh
MARIVO_SNAPSHOT_BENCHMARK=/tmp/marivo-snapshot-after.json \
  .venv/bin/pytest tests/benchmarks/graph_snapshot.py -m runtime -n 0 -q -s
```

## Recorded measurements

[Raw before/after measurements](2026-09-30-marivo-snapshot-dag-benchmark.json)
include every timing, byte count, node/reference count and publication rejection.
The [baseline harness](2026-09-30-marivo-snapshot-dag-baseline.py.txt) is a measurement
artifact, not a production compatibility reader. To reproduce the baseline, copy
it to `tests/benchmarks/graph_snapshot.py` in an isolated checkout of `377bc341f8`,
use the same environment/dependencies with that checkout first on `PYTHONPATH`,
and run the command above with a separate output file. Import origin was verified
inside the isolated baseline checkout before execution.

Both versions use the shared j2 rows plus June zero-contribution rows for all four
members, matching the nested-Difference Runtime fixture. The initial publication
attempt without June rows was rejected by baseline finite-numeric checks and was
corrected in the measurement fixture, not in production numeric semantics.

| Scenario | Definition JSON bytes, old → DAG | Minimal continuation bytes, old → DAG | Published continuation bytes, old → DAG |
| --- | ---: | ---: | ---: |
| observation | 23,536 → 21,260 | 26,636 → 3,581 | 27,342 → 4,287 |
| difference | 73,869 → 59,501 | 5,074 → 4,837 | 5,924 → 5,687 |
| nested_difference | 198,820 → 160,268 | 9,670 → 9,109 | 10,674 → 10,113 |
| shared_1 | 185,963 → 97,809 | 9,158 → 6,141 | 10,042 → 7,025 |
| shared_3 | 916,387 → 218,689 | 35,490 → 10,221 | Both rejected |
| shared_5 | 3,981,891 → 442,337 | 147,370 → 17,953 | Both rejected |
| ratio | 201,739 → 163,239 | 10,274 → 9,169 | Both rejected |
| linear | 204,582 → 166,082 | 10,526 → 9,661 | 11,526 → 10,661 |
| fixed_difference | 46,902 → 73,090 | 10,646 → 6,153 | 11,174 → 6,681 |

The observation's persisted-size reduction also includes the newly uniform outer
compression: its old root was plain JSON. This is distinct from structural savings.
For shared comparisons, both versions already compress the root, and the raw JSON
and record-count measurements directly demonstrate removal of repeated subtrees.
Fixed endpoints no longer contain independent compressed child frames.

The shared graph with five added ratios has 11 unique execution nodes and 19
references. Old encoding repeats nodes 383 times; the DAG contains exactly 11
records. Its raw definition JSON falls by 88.9%, and its minimal continuation by
87.8%. Retained definitions in the fixed comparison now share the same closure:
there are 8 records rather than 3 executable records plus separately compressed
endpoint trees. The fixed comparison's complete published continuation falls
from 11,174 to 6,681 bytes (40.2%).

Shared-3, shared-5 and the unfiltered original-ratio example are rejected by the
same `finite_numeric` check on both versions. Their encoding measurements do not
claim successful publication or numerical qualification. Valid ratio execution is
covered separately by the public comparison Runtime matrix.

| Scenario | Construction ms, old → DAG | Encode ms, old → DAG | Decode ms, old → DAG | Topology validation ms, old → DAG |
| --- | ---: | ---: | ---: | ---: |
| observation | 0.00 → 0.00 | 2.71 → 4.39 | 48.90 → 21.44 | 0.32 → 0.55 |
| difference | 24.40 → 59.74 | 27.78 → 20.93 | 171.20 → 79.94 | 0.85 → 0.91 |
| nested_difference | 809.16 → 720.68 | 85.44 → 83.61 | 535.03 → 304.03 | 8.36 → 10.72 |
| shared_1 | 139.18 → 78.90 | 61.97 → 23.02 | 412.97 → 96.43 | 2.18 → 10.98 |
| shared_3 | 812.72 → 159.20 | 201.45 → 57.10 | 1698.45 → 135.93 | 5.49 → 12.90 |
| shared_5 | 2177.28 → 229.36 | 666.40 → 98.29 | 5989.23 → 452.52 | 9.22 → 20.04 |
| ratio | 28.57 → 53.04 | 31.50 → 102.90 | 335.44 → 224.46 | 3.79 → 4.32 |
| linear | 31.22 → 34.02 | 28.10 → 35.20 | 266.78 → 132.19 | 2.62 → 4.42 |
| fixed_difference | 9.72 → 6.22 | 4.51 → 25.62 | 71.03 → 57.41 | 0.40 → 2.40 |

Shared-5 decoding improves from about 5.99 s to 0.45 s in this run. Encoding and topology validation do not improve for every scenario;
simple observations and fixed comparisons still pay canonical validation overhead. Canonicalization, validation and compression remain real
costs. Semantic signatures and fact inventories remain in each node's closed
payload, so the result is not a claim of constant payload size per node or linear
time for every semantic derivation.

## Validation

- Existing graph/publication/comparison default target: 104 passed.
- Initial new protocol fixture failures: corrected count-unit and typed-obligation setup; no production compatibility fallback was added.
- Protocol and publication target after correction: 77 passed.
- Touched-module typing: 8 modules passed.
- Comparison Runtime matrix: 81 passed (`make runtime-test TESTS='tests/test_analysis_comparison_runtime_r62.py'`).
- Strengthened nested cold recovery: 2 passed, DuckDB table and Parquet; exact frozen definitions and every retained Arrow part compared in a new source/Semantic/DuckDB-disabled process.
- Supplemental publication Runtime, one-to-one retained relationship and lazy/shared source execution: 20 passed, 50 default cases deselected by the Runtime marker.
- Baseline and final measurement harnesses completed; all nine scenarios preserve the same publication success/rejection outcome.
- Initial broad gate was interrupted for diagnostics after 1,897 passed, 4 skipped and two subprocess timeouts. The four-worker rerun completed with 5,509 passed, 5 skipped, two v1 execution-key golden hashes requiring the planned v2 update, and the same two subprocess timeouts. No timeout threshold was increased.
- Final exact-literal identity regression: passed; canonical record equality rejects `1` versus `1.0` even when Python dataclass equality equates them.
- Final serial targeted rerun: 40 passed, including all DAG protocol cases, source/fixed execution-key oracles and both unchanged timeout tests.
- Final broad gate: `make check-agent PYTEST_FLAGS='-q --tb=short --maxfail=5 -n 2'` exited 0. Formatting, lint and import contracts passed; typing passed for 410 modules; default tests: 5,514 passed, 5 skipped; API documentation built successfully. This used two workers with no concurrent benchmark or Runtime runs; test scope and timeout thresholds were unchanged.
- Final canonical-record comparison follow-up: 5 Runtime cases passed (one-to-one source/fixed binding, nested DuckDB/Parquet cold recovery, lazy/shared source realization). Production-source hashes in the benchmark manifest still match the final implementation.
- The isolated baseline worktree was archived after preserving its measurement harness and outputs. No release or push was performed.
- Commit preparation: the default eight-worker pytest hook stopped with 4,608 passed, 4 skipped and the same two subprocess timeout cases (temporal construction and public import bindings). The two-worker hook retry was interrupted at the user’s request to commit directly without further tests or validation. Commit hooks were then skipped for this commit; the prior completed acceptance results above remain the validation evidence.
- Release checks, MinIO, installed-wheel and remote-backend acceptance: not run; outside this change.
