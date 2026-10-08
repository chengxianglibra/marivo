# A3 original-state kernel and direct-key L8 acceptance

Date: 2026-10-07

Scope: A3 in the [execution optimization design](2026-10-07-analysis-algebra-execution-optimization-design.md), as selected by the user: kernel convergence plus direct-key L8, with no new DSL, L7/L9 optimizer or packaged-skill edits. A1, A2 and native numeric qualification retain their independent owners.

The starting checkout was clean `panda@7a3d5911c0`. Concurrent work subsequently committed Help disclosure (`9933cc3784`) and analysis-skill guidance (`329dcc4cc4`). Final A3 verification uses `panda@329dcc4cc499c6106c71e44bb6b226dbb4ed5a5c` plus the A3 working diff. Those concurrent changes are preserved and are not A3 deliveries. The cost record binds the measured implementation and input-builder bytes by SHA-256.

## Kernel convergence

- `numeric_state.merge_components` owns component merging in each actual Arrow carrier; `finish_original` owns the existing method-specific finish and empty policy. `merge_original` remains their composition entrypoint. These are internal helpers, without new exports or Help targets.
- Fixed scalar sum/sum_zero, count, mean, ratio and weighted mean now consume the shared kernel. The three separate scalar helpers and their hand-written accumulation/empty/finish branches were removed. Coordinate reduction uses the same owner. Linear and extrema retain their existing semantics; ordered folds retain their separate owner.
- Every original component, support count, row count, floating absolute magnitude and mixed component type survives reduction. Layout, quantity, units, contribution binding, weights and empty rules remain method-owned. Native source expressions, output casts and numerical qualifications were not replaced.
- Coordinate-part transport calls component merging directly when it needs only totals; it no longer computes and discards display values. Source-prefix local reduction dispatch also uses the surviving kernel entrypoint.
- Consumption retains complete primary/state/coverage key indexes, duplicate insertion failures, required components, actual coverage and Cell/state agreement. Numerical failures use the existing structured materialization error. Current-row summarize remains distinct from original-quantity rollup. Store reads retain their existing committed-state trust boundary.

## Direct-key L8

The public chain `fixed.group_by(customer).rollup().rollup()` executes a real two-stage group. Adjacent selected fixed `artifact_python` OriginalReduce stages qualify only for sum, sum_zero, count, mean, ratio, weighted mean and linear. Each mapping projects the complete input key and later coordinates can only decrease. The same complete original-state binding, contribution, method, output type and finish policy must survive every stage.

Only ordinary Subject, original_state and coverage parts qualify, with actual declared schemas and full-key layouts. Shared intermediates, requested outputs, explicit materialized leaves, pending checks, time maps, nested contribution coordinates, allocation or other specialized parts end groups. Reference/weight changes and ordered folds never qualify. Unqualified schedules retain their original selected execution.

The group consumes the original input once, merges all components directly by terminal keys and finishes/constructs only the terminal result. Terminal Signature, Cell policy, schema, complete parts, method state and continuation retain their original owner. Logical DAG/fingerprint, node identities, ordered selected implementations, plan digest and per-node proof chaining remain intact. Graph DAG v2, Store 8, descriptor v3 and continuation v4 are unchanged.

Index insertion, grouping and stage transitions check the shared deadline. Failures after group entry propagate without fallback or retry. Missing parts, duplicate consumed keys, overflow, cancellation and timeout do not publish partial Artifacts, Evidence or Findings, and discharge Runtime resources.

## Independent behavior evidence

`tests/analysis/graph/test_fixed_reduction.py` contains 62 default cases. It compares identical prepared graphs and inputs with private grouping enabled/disabled, using independent values and full component oracles. It covers chains of two/eight stages; int64, float64, Decimal and Duration within existing routes; mixed component carriers; nonuniform support; empty input, zero denominators and weights; composite keys; terminal groups and Subject mapping; unchanged logical identity/contracts/schema/parts; eliminated intermediate work; shared/nested/check boundaries; malformed consumed state and overflow; and execution without graph reconstruction. A downstream current-row mean has identical completed-check digests on both arrangements, independently exercising every retained logical proof link.

Exact states use exact expectations. The floating cancellation vector `[1e16, 1, -1e16, 1]` has independent rational total 2: direct merging returns 2 while ordinary grouped merging returns 1. Both retain the complete support and absolute magnitude and satisfy the existing represented-float rounding envelope. This explicitly verifies permitted native regrouping differences; it does not promise bitwise-equivalent floating state for arbitrary inputs.

`tests/analysis/numeric/test_fixed_reduction_runtime.py` contains 15 public Runtime cases. Independent facts are A=(10,w=1),(30,w=3), B=(90,w=2), C=(0,w=0), D=empty. The scalar oracles are sum=130, count=4, mean=32.5, ratio=1, weighted mean=280/6 and linear=130. Tests retain Null/zero-weight component state; verify direct versus chained parts, restored contracts, exact cache hits and no source access; reject publication on malformed parts/keys, overflow, timeout and cancellation without retry; and preserve materialized, time-map and ordered-fold boundaries. A separate cold process removes models, takes the database offline, forbids Semantic/source/DuckDB connection access, reads the saved result, performs a new three-stage reduction and verifies its exact cache hit.

Passed focused checks:

- New default behavior: 62 passed together, including required Subject layout and downstream proof-chain checks.
- New public Runtime behavior: 15 passed, including independent cold continuation and both temporal exclusion boundaries.
- Existing affected composition/Duration/extrema/mean/partial/weighted/Decimal-boundary Runtime: 101 passed, no skips.
- Existing local native weighted/mean, mixed linear/component attribution, overflow, Decimal composition and independent cold Runtime: 34 passed; remote and ClickHouse cases were explicitly excluded.
- Focused formatting, lint/import contracts and production/fixture/probe typing passed. The additional nine-file typing scope uses `--follow-imports=silent` to exclude existing diagnostics in the unchanged imported `tests/shared_fixtures.py`; no new ignores, broad casts or implicit Any were introduced.

The two affected Runtime commands were:

```sh
make runtime-test-agent TESTS='tests/analysis/numeric/test_analysis_numeric.py::test_numeric_composition_matrix tests/analysis/numeric/test_analysis_numeric.py::test_duration_matrix tests/analysis/numeric/test_analysis_numeric.py::test_original_extrema tests/analysis/numeric/test_analysis_coordinates.py::test_original_mean_merges_support_not_finished_values tests/analysis/numeric/test_analysis_coordinates.py::test_partial_reduction_matches_components_and_preserves_keys tests/analysis/numeric/test_analysis_coordinates.py::test_weighted_coordinates_merge_paired_components tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_decimal_boundary_fixed_rollup_does_not_round_range_check'
make runtime-test-agent TESTS='tests/analysis/numeric/test_native_numeric.py tests/analysis/numeric/test_analysis_decimal_e2e.py -k "not remote and not clickhouse"'
```

Final broad gates on the completed implementation:

- `make test`: 4,867 passed, 1 skipped, exit 0.
- `make check-agent`: 4,867 passed, 1 skipped, exit 0; full format/lint/import contracts, normal typecheck (328 files) and API documentation build passed.

The skip is the existing datasource channel-failure vector in `test_metadata_profiles_degrade_to_schema_when_statement_channel_fails`: its owner propagates failure to dispatcher fallback. A separate `-rs` run confirms five passed and one skipped. No A3 behavior or selected Runtime case was skipped. All seven measured source hashes, seven timing samples per arrangement and recorded primary/part comparisons were rechecked after final code verification.

## Comparable local costs

Reproduce with `.venv/bin/python -m devtools.fixed_reduction_cost`. The [cost record](2026-10-07-analysis-a3-cost.json) contains environment, definition/plan digests, selected implementation IDs, source hashes, seven individual timing samples, separate work counts and Python/Arrow allocator peaks. Each arrangement warms up once. The same graph, input, selected implementation and numeric kernel run with only the private group function enabled/disabled. No percentage speedup threshold applies.

Timing excludes graph construction, input setup, sources, Store, publication and cache hits. Work counting and memory tracing run separately from uninstrumented timing. Python tracemalloc and Arrow proxy peaks are separate allocator observations, not process RSS or simultaneous totals. Measured values and every output part are compared, with an independent scalar oracle.

Environment: macOS 26.6.2 arm64, Python 3.12.13, PyArrow 25.0.1. Final timing ran without another test or static-check invocation. Inputs use compound `(int64, string)` keys and complete original components. These local executor measurements do not qualify end-to-end publication latency.

| Method/carrier | Rows | Stages | Ordinary median ms | Grouped median ms |
| --- | --- | --- | --- | --- |
| sum/int64 | 20,000 | 2 | 99.662 | 69.210 |
| sum/int64 | 20,000 | 8 | 96.563 | 68.847 |
| mean/float64 | 20,000 | 2 | 125.110 | 95.376 |
| mean/float64 | 20,000 | 8 | 124.208 | 96.239 |
| weighted_mean/int64 components | 20,000 | 2 | 112.234 | 84.819 |
| ratio/Decimal numerator, int64 denominator | 20,000 | 2 | 132.291 | 102.982 |
| empty sum/int64 | 0 | 8 | 1.394 | 0.455 |
| shared sum/int64 branch | 20,000 | 2 | 97.031 | 97.307 |

Deterministic work:

- Nonempty two-stage chains: component merge/finish 4 -> 1; original input consumption 2 -> 1; complete-key indexes 6 -> 3; Exchange construction 3 -> 2. The latter includes the unchanged final execution wrapper; reduction result construction itself is 2 -> 1. Ordinary merging visits the original 20,000 rows split across three target groups, then the three intermediate state rows; direct merging visits the original 20,000 rows once.
- Nonempty eight-stage chains: merge/finish 22 -> 1; consumption 8 -> 1; indexes 24 -> 3; Exchange construction 9 -> 2. Seven intermediate stages each finish three groups; those 21 finishes disappear. Original row consumption, key insertion, numeric checks and complete component arithmetic remain necessary.
- Empty eight-stage chains: merge/finish already occurs once on both paths, because empty intermediate group domains contain no rows. Consumption 8 -> 1, indexes 24 -> 3 and Exchange construction 9 -> 2 still decrease. This case does not claim eliminated empty-group finishes.
- Shared branch: merge/finish remains 5, consumption remains 3, indexes remain 9 and Exchange construction remains 5. No group crosses the shared intermediate; the small timing difference is observational noise, not eliminated work.

Memory observations:

- Nonempty Python peaks are dominated by necessary original-row/key/state consumption and stay approximately 23.82-28.42 MiB, differing by about 2 KiB between arrangements. There is no material Python-memory reduction claim. The empty case changes from 26,277 to 11,323 bytes.
- Arrow peaks decrease from 1,344 to 704 bytes for the two-stage int64 sum, and from 5,184 to 704 bytes for its eight-stage chain. Mean and the mixed-component cases use 1,024 bytes grouped versus 1,792 bytes ordinarily; eight-stage mean is 6,400 versus 1,024 bytes. The empty case is 1,664 versus 768 bytes. Shared-branch Arrow peaks stay 3,968 bytes.
- Prepared input Arrow buffers are outside these allocator measurements. These small output-allocation differences must not be presented as reductions in source data memory or process RSS.

The kernel exit is deleted duplicate responsibility, independently checked across affected consumers. The separate L8 cost exit is actual reduced intermediate grouping, finishes, consumption/indexing and result construction with complete terminal state. Timing does not establish a universal percentage or whole-query performance promise.

## Qualification limits

This is bounded local kernel/direct-key execution acceptance. It grants no new public surface, source implementation, type/backend route, precision algorithm, automatic L7/L9 rewrite or specialized-part fusion. No release Runtime, installed wheel, remote service or publication qualification was run. Full Runtime and end-to-end source/Store latency remain unverified. Earlier A1/A2 records and their unverified scopes retain their independent owners.
