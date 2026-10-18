# Analysis source execution cost evidence

This report records the current source-fusion routes, using the same synthetic inputs
and public Query execution with the optimization passes enabled or disabled. The
benchmark reuses the original recomputation fixture; no temporary script is a dependency.

```sh
MARIVO_SOURCE_COST_DIR=/private/tmp/marivo-source-cost make slow-test TESTS='tests/analysis/materialization/test_source_execution_cost.py'
```

The six cases each run three executions per route. They cover a high-cardinality
identity field filter/Top, a 100-group mean rollup, and a Top-1 parent followed by
drill. Values have an independent arithmetic oracle. Every optimized case returns
one source row, no raw components, and no uploaded keys. Sources close before the
Artifact is reopened, without another source submission.

| Scenario | Facts | Source rows | pandas deep bytes | Arrow bytes | Median seconds, baseline → optimized |
| --- | ---: | --- | ---: | ---: | ---: |
| mean_rollup | 10,000 | `[10000]` → `[1]` | 343,882 → 266 | 326,250 → 38 | 0.401 → 0.421 |
| mean_rollup | 100,000 | `[100000]` → `[1]` | 3,437,632 → 266 | 3,262,500 → 38 | 0.458 → 0.359 |
| numeric_top | 10,000 | `[10000, 10000]` → `[1]` | 294,014 → 154 | 250,000 → 22 | 1.636 → 0.486 |
| numeric_top | 100,000 | `[100000, 100000]` → `[1]` | 2,937,764 → 154 | 2,500,000 → 22 | 13.020 → 0.416 |
| selected_drill | 10,000 | `[10000, 100]` → `[1]` | 345,640 → 284 | 327,876 → 56 | 0.373 → 0.526 |
| selected_drill | 100,000 | `[100000, 1000]` → `[1]` | 3,454,014 → 284 | 3,278,750 → 56 | 0.411 → 0.606 |

The size columns describe in-memory representations, not network traffic. SQL
UTF-8 bytes, logical uploaded-key Arrow bytes, returned columns, local stage input
rows, raw component rows, phase timings, process RSS and separate optimized
`EXPLAIN ANALYZE` plans are retained in the [measurement record](evidence/source-execution-cost-2026-10-18.json).
The recorded phase times are inclusive and overlap: source compilation and
execution can occur inside the execution phase. The process RSS is a lifetime
maximum including setup and earlier repeats; it is not an isolated per-route
memory comparison. Byte instrumentation adds overhead to timed executions.

These measurements prove reduced transport and host inputs, not universal speedup.
They ran with the repository pytest worker configuration while other development
checks were active, so the environment was not performance-isolated. Extra
planning and SQL complexity can outweigh saved transport on small inputs. The 100k numeric Top
case demonstrates the larger host-boundary cost of the original path. This is
one local machine and DuckDB execution, not remote latency evidence. Inspect the
separate EXPLAIN operators for actual scans; a single SQL statement may scan its
input repeatedly. Diagnostic EXPLAIN reads run after the timed executions and
are not counted as application business submissions.

The relationship extension adds four cases, again with three executions per route.
Each input has two duplicate relationship rows per fact and three target identities.
Image count has an independent result of two; Query preimage selects half the
facts and sums one per fact. Check summaries retain ancestor validation while
the target read returns one row and uploads no keys. The same resource-close,
Artifact-reopen, phase/byte/RSS capture and separate EXPLAIN checks apply.

| Scenario | Facts | Source rows | pandas deep bytes | Arrow bytes | Median seconds, baseline → optimized |
| --- | ---: | --- | ---: | ---: | ---: |
| image_count | 10,000 | `[1,10000,2]` → `[1]` | 101,676 → 144 | 83,778 → 12 | 0.743 → 0.610 |
| image_count | 100,000 | `[1,100000,2]` → `[1]` | 1,012,926 → 144 | 837,528 → 12 | 1.020 → 0.667 |
| query_preimage | 10,000 | `[3,3,1,10000,5000]` → `[1]` | 152,760 → 148 | 125,705 → 16 | 1.285 → 1.200 |
| query_preimage | 100,000 | `[3,3,1,100000,50000]` → `[1]` | 1,519,635 → 148 | 1,256,330 → 16 | 1.382 → 1.413 |

The [relationship measurement record](evidence/source-population-cost-2026-10-18.json)
comes from a later run of the same harness in a non-isolated development environment.
The 100k preimage case illustrates that reducing transport need not reduce elapsed
time: SQL preparation and source checks still have costs. No physical scan count
or production latency improvement is inferred.

Remote source changes, native precision, selected complete keys and error policies
are verified by their owning Runtime tests. This cost report does not qualify
unimplemented remote methods or arbitrary cross-source complex recomputation.
The finite qualification table in the implementation plan owns those boundaries.

The remote relation extension records actual Trino and ClickHouse submissions in
the [remote relation record](evidence/source-relations-remote-2026-10-18.json).
Each positive execution below uses one source statement and uploads zero keys.
The input has seven rows; this is deterministic transport evidence, not a latency
benchmark. ClickHouse was exercised with both `join_use_nulls=0` and `1`.

| Positive execution | Returned rows | Trino SQL UTF-8 bytes | ClickHouse SQL UTF-8 bytes |
| --- | ---: | ---: | ---: |
| align / compare | 5 | 4,302 | 5,886 |
| scalar broadcast | 3 | 2,950 | 4,023 |
| shared consumers, empty null | 3 | 7,773 | 10,303 |
| shared consumers, empty zero | 3 | 7,719 | 10,213 |
| multiple parameter producers | 1 | 2,682 | 3,291 |

Receipts also include subsequent required-error probes as separate submissions:
exact correspondence, reference coverage and strict parent input failures.
They are not extra reads in the positive execution. Every recorded source closed.
The shared SQL can repeat source subexpressions; no EXPLAIN was run for these
remote cases, so one statement does not establish one physical scan. Trino
nullable numeric carriers may become native floating values after the joined
pandas handoff; the tests accept that numerical precision boundary while retaining
complete string keys, NA and required errors.

The [remote sample tests](../../tests/analysis/materialization/test_source_original_samples_remote.py)
separately use 1,000 facts. Per spatial rollup transports three grouped leaves;
window sum/mean transports six and ratio transports twelve. Selected per reads
return `[2,1]` and pass two complete parent keys; selected window reads return
`[4,4]` and pass three correlated endpoint/sample keys. These counts retain fine
checks and source changes after selection without returning raw facts. They do
not imply state size is independent of the actual sample or window cardinality.

The [checked arithmetic record](evidence/source-query-arithmetic-remote-2026-10-18.json)
captures three positive executions on each of Trino and ClickHouse. Multi-input
Metric formulas return one row, compound Column arithmetic with computed Top
returns two, and nullable division returns two missing values without a spurious
zero-denominator failure. Each uses one statement and uploads no keys. SQL sizes
are 6,186–25,832 bytes on Trino and 7,625–34,233 on ClickHouse. The six-row inputs
demonstrate the boundary shape only; no latency or single-scan claim follows.

The [plain Calendar tests](../../tests/analysis/materialization/test_source_original_cross_owner.py)
also record actual source row counts. Two independent 120-row roots return
`[2,2,1,1]` grouped leaves, retaining separate owners and the complete parent
formula before target reads. A single-root day-to-month regrouping returns
`[2,1]`, and its clipped half-open period returns `[1,1]`. A fine-parent error
prevents target reads even when the target total would be valid. These are
transport assertions, not a cross-read consistency guarantee.

The [shared-consumer and cell-sum record](evidence/source-query-shared-remote-2026-10-18.json)
captures actual Trino and ClickHouse executions of the final candidate. Every
case uploads zero keys. Mixed consumers retain both endpoint values, including
NULL coordinates and zero counts. The two per cases preserve independent sample
semantics; identical per grids can share their grouped carrier. Different windows
retain independent reads and results rather than merging their W(t).

| Scenario | Input facts | Returned rows on each provider |
| --- | ---: | --- |
| Mixed endpoint and source membership | 5 | `[4]` |
| Plain sum and per mean | 5 | `[1,2]` |
| Same per grid, mean and sum | 5 | `[2]` |
| All-history and trailing windows, two endpoints | 1,000 | `[1,1,1,1]` |

Native cell sum has three separate executions for ordinary, all-missing and empty
input; each returns one row. Its required-error tests separately cover infinite
input, floating overflow and Decimal range overflow before Top-0. These finite
checks do not demand equality with local floating or Decimal accumulation.
The receipts preserve SQL byte counts; reduced transport does not guarantee
shorter SQL, fewer physical scans or lower elapsed time.
