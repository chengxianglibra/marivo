# A1 bounded implementation acceptance

Date: 2026-10-07

Scope: A1a, A1b and A1c in the [execution optimization design](2026-10-07-analysis-algebra-execution-optimization-design.md). A2 selection fusion and A3 state-kernel convergence remain deferred. No commit or push was performed.

Implementation base: `panda@94813fe387a13fbf1e9ca7560d250ca40b96cfc3` (the committed numeric-type inference, Store 8 and documentation work). The design's original research base was `6801154111811bd94cd17343d52056e72306103a`. This acceptance describes the A1 working-tree diff, not a released package or an all-backend qualification.

## Premise authority and check inventory

Facts retain ordered input node identities, typed domains, quantities, windows, versions and parameter-bound paths. A node's constructive key-domain identity is separate from its DomainSignature. Filtering keeps uniqueness but changes the key-domain identity. Builder evidence retains declaration and assumption dependencies; pending premises remain conditional. Only exact key equality and field-owner total matching can have call-scoped assumption evidence. Assumptions cannot be published as actual completed checks.

| Producer / consumer | Bound responsibility | A1 disposition |
| --- | --- | --- |
| Entity members, snapshot/validity declarations | `declared_key`, `unique_key` | Trust complete declared identity and version grain; no source identity/version audit and no DISTINCT repair. |
| Field projection, directed complete to-one relationships | `field_ownership`, `single_value` | Declaration/construction supplies ownership and structural single-valuedness; no repeated fanout scan. Cardinality does not supply total matching. |
| Scalar owner read | `mapping_total` | Same owner and exact selected version derives totality. Unknown matching checks by default, or records an exact `match_verification="assume"` premise. |
| Ordinary contribution paths; captured occurrence, Funnel and History axes | `mapping_total` | Unknown scoped hop matching remains an admitted typed obligation. Repeated identical capture routes share one actual completion; identity/time/version declarations are not re-audited. |
| Exact comparisons, ratios, original occurrence combinations | `key_set_equal` | A constructive common key domain discharges equality. Unknown pairing checks by default; `ExactKeys(verification="assume")` records only this call's ordered operands. |
| Union endpoint keys | `unique_key` | Transport declared/constructed endpoint uniqueness; preserve the explicit missing-side method policy. |
| Group attachment / completion | `mapping_total`, `cell_policy` | Admit precise inclusion/lookup and required Defined non-null category policies. Fixed consumers fulfill them while building indexes and reading values. |
| Display / external predicates / attribution | `key_set_equal` or `mapping_total` | Common domains discharge matching; otherwise bind exact operand obligations. Required category/predicate input Cell policies remain distinct. |
| Original target grids and sufficient-state output | `complete_coverage`, `contribution_partition`, `state_binding` | Conditional construction supplies target/state coverage and partition guarantees. Business completeness and exhausted reads have separate owners. |
| Metric empty finish | `complete_coverage` | Verify the consumed original state coverage; it cannot be supplied by a business-completeness declaration or stream completion. |
| PeriodChange | `complete_coverage` | Preserve necessary original bucket membership/completeness checks with exact grid and operand binding; do not renumber selected buckets. |
| Pre-fold spatial reduction | `contribution_partition` | Necessary sample alignment remains typed and implemented. Closed disjoint period construction replaces the redundant temporal-disjointness scan. |
| Generic stage `_key_violations` / `_cell_violations` templates | Key uniqueness / generated Cell encoding | Remove per-stage queries. Helpers remain available for an explicitly unresolved typed obligation. |
| Explicit source time declarations | Temporal interpretation | Remove automatic declaration audits. Keep parsing, temporal parameters, exact physical qualification and decoding failures. |
| Private producer Exchange and committed Artifact reads | Schema, required parts, binding, decoding, complete read | Reuse producer business guarantees. Do not recompute keys, Cells, partitions, ranks or sufficient-state equations at every transport/publication/recovery boundary. Strict independent fixture validation remains available. |
| Required consumer indexes and operands | Actual insertion / lookup | Reject duplicate insertion and missing required values during normal consumption, without a separate source audit. A false declaration/assumption is not guaranteed to be detected earlier. |
| Numeric owners | Method input policy and actual conversion/computation | Keep necessary finite/Defined input, Duration weight, fixed elapsed Duration and nonzero-denominator policy checks when final output cannot reveal invalid input. Overflow and coefficient range failures occur in conversion/computation; remove output-only overflow/Spearman validation queries. Null, Unknown, zero denominator and precision routes stay unchanged. |
| Runtime / Store publication | Deadline, cancellation, cleanup, ownership, atomicity | Preserve the existing boundaries and failure handling; no row/byte admission quota or source snapshot guarantee is added. |

A completed check is reused only for the same exact Fact, actual ordered source occurrences and fulfillment expression/position. Consumer records reference its actual result digest. Distinct source acquisitions do not share completion authority or gain a snapshot guarantee. A declared implementation without an actual fulfillment path is rejected.

## API, saving and disclosure

- `ExactKeys(verification: Literal["check", "assume"] = "check")` flows through ratio and comparison designs. Logical read overloads and inherited variants expose `match_verification` with the same closed values.
- Policy parameters and assumption evidence participate in fingerprints, execution keys and frozen graph metadata. Tests distinguish check/assume cache identity and retain assumptions after source-offline fresh-process recovery.
- Frozen graphs use `marivo.analysis.graph_dag/v2` and `graph-dag-v2:`. Old frozen graphs are explicitly rejected with a re-execution repair; existing bytes remain untouched. Store 8, descriptor v3 and continuation v4 stay unchanged.
- Owning specs, native Help/docstrings, dynamic assumption disclosure, typed repairs and current English/Chinese site examples are aligned. Public exports remain pinned and unchanged. Packaged skills were not edited.
- The three comparison implementation inventories retain their original counts (88 / 78 / 79) and specialization categories. Their hashes change only to disclose the existing complete-coverage fulfillment as a typed checker; no new backend/type/route key is introduced.

## Query counts, independent oracle and repeated timing

The same 64-row DuckDB ordinary-table fixture was run against the archived implementation base and the A1 tree. Every execution submitted a fresh Run. Instrumentation counts `SourceSession.batches` purposes: `analysis.graph.check` and `analysis.graph.stage`. Local receipt reads and retained-part writes are outside these source-query counts.

The field oracle uses the fixture's complete `(tenant, id, revision)` keys and raw values. The sum oracle uses independently generated rows and sums non-null integers. It does not reuse Marivo state validation or backend SQL as its expected result.

| Path | Before: checks / primary reads | A1: checks / primary reads | Before median ms (min–max) | A1 median ms (min–max) |
| --- | --- | --- | --- | --- |
| Same-owner field read | 8 / 1 | 0 / 1 | 120.739 (118.933–163.842) | 69.625 (69.156–114.187) |
| Original sum → rollup | 16 / 1 | 0 / 1 | 262.366 (256.868–314.609) | 91.227 (90.688–96.505) |

Seven measurements per path, in execution order, including graph preparation, source execution and Store publication; fixture setup and oracle comparison are outside the timed interval. The baseline and A1 processes ran sequentially without concurrent test jobs, using the same virtualenv and one DuckDB thread. No warm-up sample was removed. These are local timing observations, not a remote latency or release performance guarantee.

- Same-owner read, before (ms): `[149.474, 119.75, 119.813, 118.933, 120.739, 163.842, 121.463]`.
- Same-owner read, A1 (ms): `[92.761, 69.625, 69.317, 70.325, 69.156, 69.38, 114.187]`.
- Sum rollup, before (ms): `[267.421, 260.511, 258.021, 314.609, 262.366, 263.298, 256.868]`.
- Sum rollup, A1 (ms): `[96.505, 90.688, 90.975, 91.642, 91.184, 91.227, 91.43]`.

## Verification record

Passed:

- `make check-agent`: 4,780 passed, 1 skipped; full lint/import contracts, typing (327 files), default tests and API documentation gate, exit 0. The final read/query test edits additionally pass their focused lint and Runtime checks.
- Focused graph/numeric/materialization/statistical default scope: 2,031 passed; precise-input/dependency scope: 177 passed; external validation/physical publication boundaries: 132 passed.
- Source/fixed/assumption/cold recovery plus member behavior Runtime scope: 43 passed. An explicit guard forbids both source access and repeated Exchange business collection in the cold process.
- Shared-query independent oracles, exact assumed/default matching and native resource boundaries: 17 passed (4 non-Runtime cases deselected). J2, J4 and tied J4 use one terminal read without source restaging; physical source scan counts are not inferred from expression-node counts.
- Fold alignment and empty fixed fold: 7 Runtime cases passed. Metric empty and period/coarsened attribution: 8 passed; original period rejection and correspondence: 6 passed.
- DuckDB History and SQLite Funnel/History capture, atomic failure and fresh recovery: 13 Runtime cases passed. Display composite/scalar/Null and cold continuation: 4 passed; required duplicate rank insertion: 2 passed.

- Final broader numeric/member/display/deadline/cancellation Runtime selection: 144 passed, 17 skipped in 140.57 seconds, exit 0. The skips require explicitly ready PostgreSQL (2), MySQL (2), Trino (5) and ClickHouse (8) services via their `MARIVO_<BACKEND>_ANALYSIS_TEST=1` flags; these cases remain unverified. The two known baseline display vectors below were explicitly excluded.

Final bounded Runtime command:

```sh
make runtime-test TESTS="tests/analysis/graph/test_premise_verification.py tests/analysis/graph/test_native_direct.py tests/analysis/numeric/test_analysis_members.py tests/analysis/numeric/test_native_numeric.py tests/analysis/numeric/test_analysis_decimal_e2e.py tests/analysis/numeric/test_analysis_display.py tests/datasource/test_source_deadline.py tests/datasource/test_datasource_clickhouse_cancellation.py -k 'not test_group_and_entity_time_display_domains' -rs"
```

Known baseline limits:

- The two `test_group_and_entity_time_display_domains` vectors (table and Parquet) fail with `different time or source shapes`. The same two failures were reproduced on the unmodified archived HEAD. They are explicitly outside the final bounded Runtime selection, not reported as passing.
- The larger preflight scope exposed pre-existing unsupported exact member shape vectors and an obsolete PostgreSQL-unqualified expectation, also reproduced on archived HEAD. A1 does not add those qualification keys. The stale expression-occurrence/source-restaging count assertions were corrected to the current one-terminal-read contract and their independent J2/J4 oracles now pass.
- Full Runtime/release/wheel qualification was not run. The DuckDB query-count result and local timings do not qualify remote backend methods. Runtime skips remain unverified.

Reproduction owners: `tests/analysis/graph/test_premise_verification.py`, `test_native_direct.py`, `test_analysis_graph_preflight.py`; numeric member/display/comparison owners; temporal fold owners; DuckDB History and SQLite Funnel capture owners; existing datasource deadline and ClickHouse cancellation owners. Failure-injection tests continue to cover foreign Sessions, invalid parameters, schema/missing parts, decoding, necessary index conflicts, numeric failures, cancellation, deadlines, cleanup and atomic publication.
