# R6.7 evidence index

Baseline: clean `panda`, `909f4bdcf4a9a9d9caf3beb99c591c34d9a60ddf`.
Status: R6.7 complete; V01-V12 and full frozen R6 local acceptance passed.
Every mandatory gate below succeeded on the final candidate. No commit, push,
publication, release-check or MinIO; AGENTS.md and packaged skills are unchanged.

## Reproduction and attachments

The raw attachments are retained in the ignored directory
`docs/superpowers/plans/r67-evidence/`. The final source/test inventory, command
receipts, qualification grid and attachment hashes make this directory auditable;
this tracked index and the test migration manifest remain the durable navigation
and reproduction sources.

- [Test migration](2026-10-01-marivo-r67-test-migration.json): 316 changed/retired
  legacy test nodes and 6 retired workers, original file hashes, exact current
  replacement test nodes and explicit withdrawals. Preserved independent R7/R8
  arithmetic tests grant no R6 Runtime or R8 source qualification.
- `runtime.xml`, `predicate-matrix.xml`, `contract-runtime.xml`,
  `remaining-r5-runtime.xml`, `journey-oracles-runtime.xml`: public R6, affected R5 and shared contract Runtime evidence.
- `default.xml`: broad default tests, with the five existing skips kept separate.
- `installed-wheel/archives.json`, `inputs.json`, `constraints.txt`,
  `dependencies.log`, `commands.json`: one wheel/sdist source inventory, installed
  dependency versions, isolated test input hashes, commands and exit codes.
- `installed-wheel/process-origins/`, `*-origins.json`, `origin.json`: every
  installed process checks the isolated site-packages path and candidate wheel
  hash; the source-tree PYTHONPATH poisoning probe must reject foreign imports.
- `installed-wheel/*-a0*-{produce,continue,recover}.json` and
  `*-j*-{produce,continue,recover}.json`: distinct process identities and actual
  fixed continuations on the same wheel, for native table and local Parquet.
  R6 snapshots compare values, public variant, definition, ordered graph
  identity, every part and normalized K independently. Source facts change before
  the producer removes database, semantic models and Parquet sources.

## Consumer closure and residual ownership

| Migration IDs | Final disposition |
| --- | --- |
| M01–M02 | Public comparison/predicate/cohort methods use the existing typed graph, method selection, Runtime and Store 7. Legacy predicate adapters remain only for actual R7/R8 consumers. |
| M03–M05 | Metric.compare, generic Delta/Attribution classes, registrations, logical payloads, source/local compiler dispatch, publication and descriptor evidence codecs are removed. The four old public exports and old Help targets have no alias or redirect. |
| M06–M07 | Old attribute construction, axis-expansion comparison origin, generic allocation execution and uncalled lower_attribute/lower_expanded_attribute are removed. Exact sums/reconciliation/state/key helpers remain for driver_values, driver_candidate, entity_candidate, distribution and Event allocation. _definition in attribute_expansion is solely consumed by the remaining R8 Metric axis-preparation branch. |
| M08 | Uncalled distinct/distribution attribution source compilers are removed. Distribution game constants now live with the actual R8 distribution_values consumer. No distinct/quantile public attribution permission is added. |
| M09 | Generic Metric Delta Finding and metric Contribution variants, their body codec variants, extractors and policies are removed. input_bindings_codec retains common ordered-input authority; finding_values and _finding_registry retain only actual Event/forecast/association consumers. No old-format read or origin replay is provided. |
| M10–M12 | Old R6 migration markers and row dispatch IDs are removed. Private exact funnel consumers remain blocked as R7; candidate/forecast/association consumers remain R8. AN11 remains deleted; AN12 and its collection/summary calls are deleted. |
| M13–M14 | Same registry and graph receipts, state v2 for comparison and v1 for newer R6 kinds. Native Help, actual dynamic K, closed typing, exports, CLI checks, API docs and latest English/Chinese examples are aligned. No packaged-skill edit. |
| M15 | Test-level dispositions and exact replacement owners are recorded in the migration JSON. No legacy producer is reconstructed to make obsolete tests pass. |

Private Event comparison/allocation classes and registrations now live in
`domains/funnel_delta.py`, `domains/funnel_attribution.py` and
`domains/funnel_registry.py`. Their family registrations admit only `funnel`
and `funnel-loss-rate`. R8 comparison/partition row value objects and pure
arithmetic do not register another R6 family, producer, executor or recovery path.

Two acceptance-confirmed repairs accompany the retirement: reductions retaining
Group keys now publish the same MaterializedGroupedNumericRelation variant as
cold recovery, preserving the R5 group-statistic axes; terminal table validation
uses the existing declared attribution-axis nullable policy for typed Other,
without permitting null Entity identity.

## Qualification boundary

Mandatory local source cells mean DuckDB native tables and local Parquet,
with actual artifact_python fixed execution. Required Duration cells are native
us and Parquet s/ms/us/ns for comparison, predicates, share, rank/table and
additive sum/linear allocation; every corresponding fixed route is exercised.
Date terminal columns have both source routes. I/F/Decimal positive and refusal
rules, period/date/local-aware time authority, Group/Singleton, exact keys,
reference identity, ranking policies and joint/hierarchy scope use the frozen
R6 owners and named tests in the final qualification attachment.

Unknown decision cases use controlled existing Cells at the actual cohort
consumer boundary; they do not qualify a new source Unknown producer. Current-row
Decimal/Duration reducers and named statistical_weight remain withdrawn or
unqualified. R7 owns domain producers/funnel migration, R8 owns statistical
producers and helper cutover, R9 owns remote/six-backend qualification, and R10
owns real Agent journeys and release acceptance.

## V01-V12 evidence owners

Source and installed checks below passed on the final candidate. Exact collected parameter nodes and outcomes live
in the XML reports, rather than being inferred from a broad method name.

| Item | Public tests and independent counterexamples | Installed |
| --- | --- | --- |
| V01 correspondence/design | comparison_runtime_r62: composite keys/double empty/wrong key images, PeriodChange original buckets and post-rollup, one-to-one ordered endpoint/relationship binding; comparison_r62 pairing oracle | Passed |
| V02 missing side/Cells | comparison_runtime_r62: Union absence versus present non-Defined, filtered empty refusal, concrete Metric empty policy; display_r65 four-Cell exchange | Passed |
| V03 arithmetic/nesting | comparison_r62 exact Decimal/Duration/int64, overflow/nonfinite/denominator intervals; comparison_runtime_r62 negative/minimum baseline, ratio matrix, nested independent captures and fixed composition | Passed |
| V04 selection/L1 | predicates_r63 multi-input source/fixed, tag guards and strict leaves, complete keys, foreign inputs and precise numeric/Duration literal types; A06 continuous selection and conjunction; branch numerator with unchanged denominator | Passed |
| V05 cohort | cohort_r63 complete Entity x Time, explicit empty policies, existing Unknown decision evidence, missing opportunity and damaged decision state; A08 any/all/at_least and Undefined hard failure | Passed |
| V06 fixed references | references_r64 share/penetration/standardize numeric matrix, complete weights and units, exact independent source origins, selected full reference, empty/overlap/zero weight, atomic publication and fixed recovery; A06 | Passed |
| V07 ranking/table | display_r65 exact four ties, typed key order/partitions, non-Defined tail, global limit, precision and scalar terminal columns, foreign inputs, reranking, atomic publication and terminal boundary; A07 | Passed |
| V08 allocation | attribution_r66 independent exact/Fraction/Decimal components and thresholds; attribution_runtime_r66 joint/hierarchy/common cross-side Top-K/typed Other, side terms, six admitted original methods, static refusals and numeric matrix; A07 | Passed |
| V09 scope/axes | attribution_runtime_r66 logical expansion versus retained fixed axes, period coarsening, independent parent resolutions and completeness revocation; A07 selected reconciliation scope | Passed |
| V10 Runtime/identity | comparison/reference/display/attribution source refresh versus fixed retention, shared submissions and one realization, no fallback, foreign session/mixed inputs, ordered identity; A02/A06/A07 shared-node counts | Passed |
| V11 exchange/recovery/L6 | recovery_r67 every required part missing/corrupt/exchanged/wrong binding/version, rejection before recovery/K/cached hit and no extra Run; R62-R66 wrong schema/key/state regressions and publication fault cleanup; three-process A02/A06/A07/A08 including cumulative/fold, negative baseline, both additive and component_mix attribution, and J1-J4; sources removed and Semantic/DuckDB/source connection disabled | Passed |
| V12 engineering/user | retired consumer assertions, independent export/Help/drift/budget/guidance tests, positive/negative typing, CLI module/console equality, API and latest EN/ZH example build; wheel/sdist inventories, dependency check, per-process isolated origin/hash and poisoned PYTHONPATH rejection | Passed |

These rows do not reuse R4 or R5 V-number pass labels: they name the R6 plan's
specific requirements. Table recovery proves reading and the terminal boundary;
it grants no typed continuation. Conditional K is compared as frozen state and
actually executed only where its declared premises are met.

## Final gates and fingerprints

- Source Runtime: **771 unique passed cases**, no Runtime skips. The full R6
  plus affected R5 run had 655 passes; precision completion 20, shared contract
  Runtime 98, remaining R5 31 and final strengthened R6.7 10 all passed. Overlap
  and three renamed predicate IDs are deduplicated in `source-gates.json`.
- `make check-agent`: **5359 passed, 5 existing default skips**, 417 typed files;
  lint, import contracts and API docs passed. The skips are four SQLite decimal
  fixture cells and one metadata channel-fallback owner case. They grant no R6
  Runtime qualification. Final focused lint and `git diff --check` passed.
- Site build: **321 pages**, both English/Chinese install-script outputs verified.
- `make pypi-build pypi-check`: wheel/sdist, Twine and content contract passed.
- Final installed gate: **1500 passed inner tests, zero failures/skips**,
  all 87 command receipts accepted (the poisoned-import probe deliberately exits
  1), and 16 journeys / 48 distinct phase processes passed. One non-editable
  wheel is used throughout; 318 guarded installed process identities are
  recorded, including subprocesses and xdist workers. Intentional crash tests
  can have a start guard without a normal exit; they never assert a fabricated
  completion. J1-J4 and A02/A06-A08 pass on native tables and local Parquet.
- Source: 411 inventoried files, digest `dc917dbb8fa584c967af2e371ba376750bb22a05cc7cc57df88a9341d941b7b7`.
- Tests: 486 inventoried files, digest `cf02e574fd035755191268bab26582182985408ac3eedef3a28988f9558806f0`.
- Wheel: `marivo-0.5.3.dev0-py3-none-any.whl`, SHA256 `d7cb5cc4be1bd3aeaf15393506d8e46dd10e5026fc0be3bd7b5b4d45293d07e5`.
- Sdist: `marivo-0.5.3.dev0.tar.gz`, SHA256 `2ffec6db1af129dfd5b4977d777e141f7a1ea473c7bfed031fbc1694034ba3ca`.

`acceptance-summary.json`, `installed-summary.json`, `source-tests.json`,
`commands.json`, `dependencies.json`, `qualification.json`, `v-coverage.json`,
`method-receipts.json` and `attachments.json` contain the exact receipts and
hashes. Method receipts record each actual state version, ordered input/part
binding, primary/part receipt and conditional public K. Difference retains state
v2; relative_change/relation_ratio and newer R6 kinds retain v1. Store 7,
graph-dag-v1, continuation/execution-key v2 and physical receipt v1 remain the
existing owners; no dual-read or lineage recovery is added.

Two failed installed candidates and one deliberately superseded candidate are
retained separately in `failed-iterations.json` and their respective directories.
Failures were stale R4.5 K/RequiredParts/repair expectations and cumulative Decimal
public-variant assertions. The superseded run was stopped to strengthen raw-fact
journey oracles and add cumulative/fold/negative-baseline and component_mix cold
coverage. A failed test-only oracle iteration used an unpromised Ratio selection;
it was corrected to actual original-state rollup and preserved-time cumulative
continuation. Final checks retain the original independent numeric expectations.
No passing records are spliced from an earlier wheel.

Evidence attachment manifest: `attachments.json` inventories 1419 raw files,
including prior failed/superseded runs. The final summary names accepted reports
explicitly; historical attachments do not grant final-candidate passes.
