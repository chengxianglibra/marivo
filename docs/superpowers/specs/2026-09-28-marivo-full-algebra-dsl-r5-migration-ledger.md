# R5.1 contract freeze and consumer migration ledger

Date: 2026-09-28; R5.2 update: 2026-09-29. Status: R5.1 freeze and bounded R5.2
qualification complete; remaining R5 execution and debt restoration unverified. This ledger accompanies
[the R5 implementation plan](2026-09-28-marivo-full-algebra-dsl-r5-implementation-plan.md).
It is an index of owners and work, not another API/method/state registry.

## Baseline and scope correction

Actual baseline: `panda`, `dd42c7cca070fba60d6a2761331d86dbaca135ce`.
R4.6 had been committed before this implementation turn. Staged and unstaged
tracked diffs were empty; only the R5 implementation plan was untracked.
[baseline.json](evidence/r51/baseline.json) records exact hashes and status.
No product, tests, AGENTS.md or packaged skills change in R5.1; no commit/push.

During this task the user questioned statistical-weight activation. The original
R2.2 instruction was: “移除 ms.`statistical_weight` 接口，暂不需要支持”.
The [R2.2 acceptance record](2026-09-26-marivo-full-refactor-acceptance.md)
already records withdrawal. Therefore the named Semantic role and dependent
`mv.statistical_weight`/current-row weighted mean are excluded from R5 obligations,
not an unresolved R5 prerequisite and not automatically assigned to R6. Historical
R0/R2/C08 proposal text does not authorize reactivation. Existing Metric and
runtime_metric weighted mean remain required. Reference weights/standardization
remain a separate R6 topic. The initially proposed R5 minimum-role activation is
superseded by this correction.

## Contract ownership and target coverage

| ID | Frozen facts and sole owner | Implementation package / evidence |
| --- | --- | --- |
| F01 / C03 | [Semantic identity/fields](../../specs/semantic/semantic-object-model.md#r51-frozen-semantic-handoff): complete K, exact snapshot/validity, absence versus coverage, no root distinct/preflight; four read kinds | R5.2; V01/V02 |
| F02 / C03-C05 | [Analysis signatures](../../specs/analysis/python-analysis-design.md#r51-frozen-public-target): member/read variants, version and path parameters, source/fixed families, Subject set image | R5.2; V01/V02/V10/V12 |
| F03 / C04 | [Resolution handoff](../../specs/semantic/loading-validation-introspection.md#r51-frozen-resolution-handoff): closed observation inputs, occurrences, readiness versus scoped runtime obligations | R5.3; V03/V04/V10 |
| F04 / C04 | [Runtime factories](../../specs/analysis/python-analysis-design.md#runtime-expression-signatures): five exact signatures, finite predicates, ratio policy and ordered linear terms | R5.3; V03/V05/V08 |
| F05 / C05 | [Complete domains](../../specs/analysis/python-analysis-design.md#complete-domains-groups-and-continuation): complete-tuple union, explicit groups, strict classification, partial axis removal | R5.4; V04/V05 |
| F06 / C05 | [State/Cell matrix](../../specs/analysis/operators-and-frames.md#state-and-cell-matrix): original components versus row statistics, empty states, parts and K | R5.3/R5.4; V05/V10 |
| F07 / C06 | [Temporal binding](../../specs/analysis/timezone-and-calendar-design.md#r51-frozen-temporal-binding): grid/boundary types, three timezone authorities, DST, cumulative anchors, non-commuting folds | R5.5; V06/V07 |
| F08 / C06 | [Certification handoff](../../specs/temporal-semantics.md#r51-certified-time-dependency-handoff): existing Catalog period/occurrence inputs and exact snapshot identity | R5.5; V06/V10 |
| F09 / C10 | [Semantic exactness](../../specs/semantic/semantic-object-model.md#metric-occurrences-and-policies): definition-owned exact/approximate kinds, q, source-native distinct/quantile with numerical limitations disclosed; no original K | R5.6; V09 |
| F10 / C04-C06/C10 | [Numeric/physical matrix](../../specs/analysis/operators-and-frames.md#numeric-target-matrix): type/state/finish precision, overflow, numeric oracle bounds, required/rejected/backend cells | R5.3-R5.6; V08/V09 |
| F11 / cross-cutting | [Runtime state/recovery](../../specs/analysis/session-state-and-runtime.md#r51-state-extension-and-recovery-contract): common exchange, version slots, receipts, source freshness and fixed K | Every package; R5.7 full closure; V10 |
| F12 / withdrawal | [Semantic scope decision](../../specs/semantic/semantic-object-model.md#named-statistical-weight-role-withdrawn-from-implementation-scope): no named statistical-weight activation | Excluded, not blocked; Metric weights remain F06/F10 |

R5.2 cells are qualified only as listed in [its evidence](evidence/r52/README.md).
All other new physical cells remain unverified. R4's existing measured J1-J4 cells stay
bounded to their original evidence. Required R5 source cells are DuckDB native
table and local Parquet, with SQLite window/calendar/fold debt explicitly required;
fixed cells are artifact_python. Remaining backend expansion is R9. Duration is
not currently a ScalarType; extending its exact type/transport is an R5.6 obligation,
not permission to claim it already works. No required cell is closed merely by
rejecting its intended accepted type.

## Actual consumer and migration inventory

Paths in this table are repository-relative; abbreviations: A=`marivo/analysis`,
S=`marivo/semantic`. [consumer-snapshot.json](evidence/r51/consumer-snapshot.json)
contains source digests, exact import/call lines, declaration/registration sites
and SQL/execution candidates. AST calls resolve direct import aliases; receiver
calls, dynamic imports and subprocess module strings need the explicit rows below
and the R5.7 reverse scan. The snapshot is static evidence, not a proof of runtime
reachability, executed SQL, or complete dynamic call coverage.

| ID | Current import/call/registration and consumers | Target owner / package | Replacement or deletion gate / validation |
| --- | --- | --- | --- |
| M01 | `A/session/core.py::Session.members` -> `A/public_dsl.py`; concrete read/observe/group/rollup/summarize variants -> graph member/relation/composition objects | F02/F05; R5.2-R5.4 | Extend the sole public path; retire single-key/string-only/two-root limits only with the exact new qualification; V01-V05/V12 |
| M02 | `A/materialization/graph_members.py::MemberGraph.read`, member construction and `graph_preflight.py` schema checks -> SourceLeaf/BindProject | F01/F02; R5.2 | Add complete typed K/version/read, preserve schema-only preflight and no implicit distinct; V01/V02 |
| M03 | `graph_observation.py::observe_members/observe_ratio_members` and `graph_composition.py` -> core/rules, graph lowering and source bindings | F03-F06; R5.3 (occurrences, combination) / R5.4 (coordinates, groups) | Per-occurrence bindings and complete tuple domain; remove limited graph branches rather than add an alternate executor; V03-V05. R5.3 freezes the per-occurrence contract and N-ary combination in the owning specs; coordinate/group axis removal stays R5.4 |
| M04 | `A/runtime_metric.py` re-exports `S/runtime_metric.py`; `S/runtime_metric_lowering.py`, `_metric_resolution.py`, `metric_graph*.py` resolve/lower into semantic graph; tests/runtime replay workers also import factories | F03/F04; R5.3 | Keep canonical graph owner and five factories; remove duplicate Analysis inference/old replay consumers after replacement; V03/V08, runtime_metric suites. R5.3 target: all five factories reach the common graph, `linear`/`weighted_mean` included |
| M05 | `A/session/_lazy_sources.py` calls `observation.population.make_population` and `observation.metric.make_observation`; `metric.py` delegates aggregation, coordinate and retained rollup | F01-F06; R5.2-R5.4/R5.7 | Migrate public tests to members/read/observe/group_by/rollup/summarize; delete R5 legacy nodes and calls when no R6-R8 consumer remains; no forwarding shim |
| M06 | `observation/{aggregation,coordinates,rollup}.py` bind_aggregation/with_dimensions/with_time_axis/retained_aggregate; `compiler/lowering.py` and `operators/rollup.py` consume their contracts | F05-F07; R5.4/R5.5 | Preserve independent business oracles, migrate algorithms into shared rules/methods; delete migrated R5 dispatch, retain documented R6-R8 shared consumers until their cutover |
| M07 | `graph_observation.py:185` calls `observation.temporal.civil_bound`; same helper called by `compiler/lowering.py`, `observation/coordinates.py`, `operators/rollup.py` | F07; R5.5 | Extract still-valid boundary semantics to the common temporal owner and move actual callers; do not delete temporal.py first or retain a forwarding shim; V06 |
| M08 | `compiler/temporal.py::bucket/bucket_end/cumulative_start/endpoint_reset_start`; bucket callers include `compiler/distinct_fold.py`, `compiler/lowering.py`, temporal tests | F07/F08; R5.5 | Replace R5 time consumers with admitted Ibis/registered local algorithms including check expressions; inspect remaining Event/Lifecycle references before deletion; V06/V07 |
| M09 | `materialization/temporal_sql.py::lower_temporal` called by SQLite/MySQL/Trino/ClickHouse execution and ClickHouse Event SQL | Temporal owner and R1 adapter boundary; R5.5 for R5, R7/R9 for remaining consumers | Name alone does not prove handwritten SQL: inspect Ibis rewrites/UDF signatures separately from SQL text builders. Migrate R5 business/check reads through SourceSession; do not delete shared backend/Event support early |
| M10 | `operators/registry.py::legacy_source_migration_stage`, implementation/source_unsupported_reason; compiler/source_admission and materialization/dataset_execution consume old declarations | F03/F11; R5.3-R5.7 | Remove each migrated R5 registration/block branch; stage function already maps delta/comparison to R6, Event/Lifecycle R7, candidate/forecast/association R8. Do not broaden default admission |
| M11 | `methods/registry.py::REGISTRY` iterates `semantics.CONNECTED_METHODS` and `builtin.implementations`; core/rules, graph plan/lowering/execution consume selected rules | F06/F10; R5.3-R5.6 | Extend one method owner, exact qualification keys and typed states; no duplicate registration in old operators. Private row.weighted_mean remains non-public and is not an R5 requirement. R5.3 adds the occurrence-combination and Metric weighted-mean entries to the existing owner rather than a second registry |
| M12 | The removed public `quantile_metric` / `QuantileMetricInput` surface formerly exposed observation-side method=; private distribution consumers remain until their owning migration | F09/F10; R5.6 | Define approximate operations explicitly in Metric agg without compatibility aliases or observation overrides; preserve q and algorithm evidence; source-native SQL only, with corresponding approximate-definition repair when exact is unsupported; V09/V12 |
| M13 | `materialization/{dataset_publication,publication,retained}.py`, observation contracts/fold/private parts and compiler retained routes serve private old Dataset harnesses | F06/F11; R5.3-R5.7 | Move R5 components/selection into graph_protocol/exchange/publication/store; delete ownerless R5 codecs only after test/worker consumers move; never reopen generation 6 publicly |
| M14 | `graph_protocol.py`, graph_storage/store/publication and graph source/local execution consume current v7 frozen signatures, parts and receipts | F11; each activating package/R5.7 | Add closed method/state layouts and strict decoding, common publication, actual recovery K; no new Store generation; V10 |
| M15 | Family-specific comparison/attribution, Event/Lifecycle and candidate/forecast/association codecs/publication still have generic private Dataset consumers (R4.6 residual ledger) | R6/R7/R8, shared Runtime F11 | Preserve these dependencies until their owning phase; R5.7 registers remaining concrete callers rather than deleting whole codec directories |
| M16 | `tests/lazy_*_worker.py`, runtime fixture factories and string-launched worker modules use old sources.observe/Population; `tests/lazy_runtime_patch_targets.py` chooses old hook owners | R5.3-R5.7; F11 | Move R5 workers and hooks to the real graph route; search module strings as well as imports; debts D01-D22 below; old private success is not public qualification |
| M17 | Native analysis/semantic Help, public export/typing tests, cutover examples, CLI and `site/src/content/docs/{en,zh}/latest/` consume advertised call shapes | F02/F04/F09; every activating package | Update together with each implementation, remove old R5 guidance only when its consumer moves; V12. Packaged skill changes require separate approval, not granted here |

All import/call rows in the static snapshot belong to the matching target-module
row above; test callers inherit the same capability owner. Shared consumer status
means retain pending the named phase, not preserve a compatibility route for R5.
The [R4.6 residual ledger](evidence/r46/README.md) remains authoritative for
non-R5 baseline chains. R5.7 must refresh this inventory against its actual SHA,
including registrations, source/check SQL, codec reads, Help and installed wheel.

## Historical debt: exact parameter cells

Collection-only evidence: [103 nodes](evidence/r51/debt-collection.log), exit 0.
The following 14 nodes are still marked skip; eight failed nodes are historical
R4.4 baseline failures, not rerun in R5.1. No execution status is promoted.
Names below are exact pytest selectors under `tests/`.

| ID | Existing node | Owner / preserved business oracle / target |
| --- | --- | --- |
| D01 | `test_lazy_source_algebra.py::test_semantic_calendar_validations_publish_each_required_occurrence[revenue]` | R5.5/F07-F08: calendar results [110,30], distinct occurrence checks all zero violations; members.each(grid).observe(...).group_by(grid).rollup |
| D02 | `test_lazy_source_algebra.py::test_semantic_calendar_validations_publish_each_required_occurrence[running]` | R5.5/F07-F11: [110,30] plus cumulative sum/evaluation-end/coverage state; at=grid.end, graph receipt evidence |
| D03 | `test_sqlite_semantic_integration.py::test_sqlite_agent_native_authoring_journey` | R5.5/F03/F07: source sample/readiness/health unchanged, revenue 30, one Run/primary query; public members(...).observe(...).rollup; test name is not real Agent evidence |
| D04 | `test_lazy_local_placement.py::test_required_parts_place_locally_without_worker_or_origin_work` | R5.3/F06/F11: materialized mean selection planning does not open source, read Parquet parts, allocate Run or execute worker; use pure GraphPlan for fixed where |
| D05 | `test_lazy_local_placement.py::test_diagnostic_version_does_not_change_selection_or_execution[None-duckdb]` | R5.3-R5.4/F03/F10: absent diagnostic version cannot alter selected exact source route; filtered revenue 147 |
| D06 | `test_lazy_local_placement.py::test_diagnostic_version_does_not_change_selection_or_execution[None-ibis]` | Same owner/oracle; remove Ibis diagnostic attribute only, compare graph source binding and route |
| D07 | `test_lazy_local_placement.py::test_diagnostic_version_does_not_change_selection_or_execution[unregistered-duckdb]` | Same owner/oracle; unknown diagnostic version, revenue 147 |
| D08 | `test_lazy_local_placement.py::test_diagnostic_version_does_not_change_selection_or_execution[unregistered-ibis]` | Same owner/oracle; unknown Ibis diagnostic version, revenue 147 |
| D09 | `test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants[first]` | R5.5/F07: channels [10,100,Null], terminal hand sum 110, source status-check obligation and primary execution |
| D10 | `test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants[last]` | R5.5/F07: channels [30,40,Null], terminal hand sum 70, same scoped status gate |
| D11 | `test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants[mean]` | R5.5/F07: channels [20,70,Null], terminal hand sum 90, same scoped status gate |
| D12 | `test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants[min]` | R5.5/F07: channels [10,40,Null], terminal hand sum 50, same scoped status gate |
| D13 | `test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants[max]` | R5.5/F07: channels [30,100,Null], terminal hand sum 130, same scoped status gate |
| D14 | `test_lazy_retained_compiler.py::test_retained_filter_aggregate_joins_exact_component_parts` | R5.3-R5.4/F05-F06/F11: source-offline revenue 140 and mean 140/3, exact component correspondence; duplicate/foreign/tampered parts reject |
| D15 | `test_lazy_runtime_concurrency.py::test_busy_contender_preserves_real_producer[thread-same-local]` | R5.7/F11: busy same-session contender at producer pause; unchanged producer Run/statistics/store; retry and fixed-hit obligations below |
| D16 | `test_lazy_runtime_concurrency.py::test_busy_contender_preserves_real_producer[thread-different-local]` | Same, different method key must still respect Session guard; later different source execution produces its own result |
| D17 | `test_lazy_runtime_concurrency.py::test_busy_contender_preserves_real_producer[process-same-local]` | Same Session guard across processes, distinct PID, no contender mutation/Run |
| D18 | `test_lazy_runtime_concurrency.py::test_busy_contender_preserves_real_producer[process-different-local]` | Same cross-process guard with different method key |
| D19 | `test_lazy_runtime_concurrency.py::test_busy_contender_preserves_real_producer[reentrant-same-local]` | Reentrant call rejects while producer owns guard; no wait on itself, no replay |
| D20 | `test_lazy_runtime_concurrency.py::test_busy_contender_preserves_real_producer[reentrant-different-local]` | Same reentrant guard for different key |
| D21 | `test_lazy_runtime_concurrency.py::test_activation_is_guarded_and_existing_handle_owner_is_stable` | R5.7/F11: real SessionStore.activate barrier/guard, concurrent names, old handle retains original owner even if current session changes |
| D22 | `test_lazy_runtime_concurrency.py::test_different_sessions_overlap_inside_real_duckdb_queries[local]` | R5.7/F11: two actual SourceSession queries overlap behind a query barrier, distinct owners, each result totals 147; no global serialization |

D09-D13's old test performs a terminal pandas sum of already folded per-channel
values. Preserve those independent constants and the Null channel; it is not an
assertion that original Metric rollup across channels commutes with the fold.
The replacement public graph observes Channel coordinates and executes; terminal
export checks the same values. Additional V07 tests independently check the
spatial-before-time target and rejection/restoration of non-commuting reductions.
Replace the old SQL substring check with bound graph check IDs and Ibis submission
audit, preserving the obligation, scope, check execution and publication ordering.

D14's tuple-of-Metrics observation is replaced by two typed observations on a
shared source member node. A revenue selection provides the explicit selected
Subject map to the mean input, then each original component rollup executes from
fixed Artifacts. Preserve 140, 140/3 and part-corruption assertions; do not claim
that selecting by revenue is the same as filtering mean > 10. Use retained typed
correspondence, not the old compiler's Ibis memtable or Artifact-to-DuckDB import.

D15-D20 currently pause on unreachable legacy `quality`. Move the pause to
`graph_publication.execute`'s `graph_primary_written` event, after real production
and before visibility, while its Session writer guard is held. Inspect graph
Store records/receipts instead of legacy table layout counts. Update subprocess
worker construction to the same public graph entry. D21 keeps the actual
SessionStore activation hook. D22 hooks datasource provider acquisition and the
actual DuckDB query/UDF barrier; a planner barrier is insufficient.

The old contender test also expects repeated source execute to hit a cache. That
assertion conflicts with the accepted R4 fresh-source contract. Preserve its
no-replay/integrity obligation by separately asserting: (1) busy rejection cannot
replay or mutate the producer; (2) a later source invocation gets a new evaluation;
(3) repetition of an identical verified fixed continuation hits without source
work or a new Run. A different fixed key must not hit. Cancellation/fault coverage
uses graph_publication/graph_store events and existing graph crash-worker tests;
these are additional obligations, not invented extra entries in the eight failures.

## V01-V12 scenarios and independent oracles

Existing files below are seeds, not proof they already exercise new public R5.
The R5.2 member test file now exists and is executed in its evidence; other new
filenames remain reserved future work.
All future fixture work uses the repository marivo-test-fixtures skill.

| Cell | Contract / implementing package | Existing seeds | New public cases and independent oracle |
| --- | --- | --- | --- |
| V01 | F01/F02; R5.2 | `test_analysis_graph_preflight_r45.py`, `test_lazy_source_algebra.py` | `test_analysis_members_r52.py`: hand-listed composite K, exact snapshot gap, validity ends/before_end, duplicate selected versions, no root distinct/all-source probe, fixed Subject set image |
| V02 | F01/F02/F03; R5.2 | `test_analysis_dsl_public.py`, `test_analysis_graph_preflight_r45.py` | Same new file: four read kinds, independent attribute anchor, multivalued/missing mapping, role mismatch, no-version historical rejection, cross-Session before I/O |
| V03 | F03/F04; R5.3 | `test_analysis_runtime_metric.py`, `test_semantic_r22_metric_graph.py`, `test_lazy_runtime_metric.py` | `test_analysis_observation_r53.py`: >=3 roots, two filtered occurrences of same table, unequal fact grain, wrong role/unit, opaque state; raw facts plus Fraction/Decimal per root |
| V04 | F05; R5.4 | `test_analysis_dsl_public.py`, `test_lazy_retained_compiler.py` | `test_analysis_coordinates_r54.py`: full tuple union, mobile-only denominator, zero cancellation, missing mapping, strict classifications, explicit empty groups, no Cartesian expansion |
| V05 | F06; R5.3/R5.4 | `test_analysis_dsl_contracts.py`, `test_lazy_local_placement.py` | Same new file: row AOV mean 50.5 versus original 200/101, four-state count/count_defined, empty mean (0,0), L8 direct/hierarchical state and L9 identical target including empty groups |
| V06 | F07/F08; R5.5 | `test_lazy_temporal_parsing.py`, `test_lazy_temporal_source.py`, D01-D03 | `test_analysis_temporal_r55.py`: independently listed 23/25-hour UTC instants, date invariance, partial grids, crossing week/month rejection, exact calendar digest, before_end without epsilon |
| V07 | F06/F07; R5.5 | `test_lazy_status_fold_admission.py`, `test_lazy_temporal_public_runtime.py`, D09-D13 | Same new file: [10,0]/[0,10] device peaks, overlapping occurrence rejection, all-history/display separation, grain-to-date/trailing anchors and source-offline fixed folds |
| V08 | F06/F10; R5.6 | `test_lazy_distinct_numeric.py`, `test_metric_unit_algebra.py` | `test_analysis_numeric_r56.py`: big int/Decimal/Duration exactness, overflow/nonfinite, shuffled/batched/tree reductions, Metric pairwise weighted oracle (10,2),(Null,9),(20,1) -> 40/3, zero-weight declared policy; no named-role activation |
| V09 | F09/F10; R5.6 | `test_public_quantile_input.py`, `test_lazy_distinct_numeric.py`, `test_lazy_distinct_contracts.py` | Same new file: native quantile results compared against independent oracles with disclosed numerical limitations, full distinct identity, empty/Null/q/bool bounds, actual definition/algorithm, no automatic fallback/original K |
| V10 | F11; each package/R5.7 | `test_analysis_graph_publication_r44.py`, `test_analysis_dsl_exchange.py`, D04/D14-D22 | `test_analysis_recovery_r57.py`: per-state produce/continue/recover processes, exact fixed hit, source freshness/sharing, no Semantic/source/DuckDB, corrupted/missing/reordered parts, empty/unfinished/close-failed streams, fault visibility; L6 value/semantics/K separately |
| V11 | M01-M17; R5.7 | Existing debt files, R4.6 residual scan | Refresh static and dynamic consumer scan; restore D01-D22 without deleting numeric/fault oracles, adding xfail or keeping old public aliases; compare installed wheel inventory |
| V12 | F02/F04/F09; each package/R5.7 | `test_public_surface.py`, `test_analysis_help_resolution.py`, `test_cutover_documentation_examples.py`, `test_analysis_runtime_wheel.py` | Positive/negative typing per closed variant, repr/show/contract and errors, independent Help budgets/reachability, CLI and both latest editions; same candidate wheel public journeys |

## Disclosure, checks and handoff

Each activating package owns signatures/docstrings, exports, native Help,
structured expected/received/repair errors, dynamic actions based on retained
state, independent reachability/budget tests, CLI and latest English/Chinese
examples for its rows. R5.2 owns member/read families; R5.3 the five factories and
observation; R5.4 grouping and row statistics; R5.5 grid/boundaries; R5.6 definition-owned exactness
and numeric qualification. R5.7 verifies the combined installed surface. Packaged
skills are inspected for alignment in that package, but modifications still need
explicit user approval. No skill was edited or alignment acceptance claimed here.

R5.1 checks only document targets, static inventory/source digests, exact debt
collection, owner coverage and whitespace/scope. See [evidence](evidence/r51/README.md).
Product tests, Runtime execution, site/package build and wheel are not run in this
document-only slice. Future packages execute the narrow suites first, then required
broad `make check-agent`, site and separate targeted Runtime gates per the plan.
R5.7's same-wheel/cold-process requirements are not replaced by R4.6 results.

## R5.2 migration outcome (2026-09-29)

V01/V02 and the member/read portions of V10/V12 are closed for the bounded
DuckDB table/Parquet and artifact_python matrix in
[the R5.2 evidence](evidence/r52/README.md). Full V10/V12 and wheel qualification
remain R5.7 obligations. No D01-D22 debt is promoted by this package.

| Consumer | R5.2 result | Remaining owner |
| --- | --- | --- |
| M01/M02 | Sole public members/read entry extended; first-key-only and string-only guards replaced by complete typed identity/version/scalar contracts | R5.3/R5.4 observation/grouping limits remain |
| M11 | Existing BindProject/PartsTransport/MapCorrespond registrations extended; source checks are scoped to consuming domain | R5.3-R5.6 other state/numeric methods |
| M14 | Complete Subject/Cell receipts, strict frozen version/path/read-kind fields; local scalar filters and set image; cold process test blocks Semantic/DuckDB | R5.7 full state/recovery matrix and strict old-snapshot rejection remain; no generation or compatibility path added |
| M17 | Public families, Help, export snapshot, typing, dynamic contracts, API and EN/ZH latest updated; CLI continues shared Help routing | Packaged skills inspected and unchanged; R5.7 installed wheel |
| M05-M10/M13/M15-M16 | Shared legacy observation/temporal/retained/worker consumers preserved; no replacement forwarding aliases added | Existing R5.3-R5.7/R6-R8 assignments above |

Direct-column and bound row-expression Measure, direct-column Dimension and
TimeDimension qualification is explicit in the owning analysis contract;
broader temporal parsing/naive conversion and numeric Decimal/Duration are not
claimed. R5.3 must revisit observation-expression consumers with its observation
lowering; R5.5/R5.6 retain temporal/numeric expansion. Historical J1-J4 Runtime
regression passes alongside the new cases. No packaged skill disclosure gap was
found for these call shapes; no skill or AGENTS.md edits were needed.

## R5.3 migration outcome (2026-09-29, reviewed follow-up)

[The current evidence](evidence/r53/README.md) supersedes the initial handoff:
all five factories execute in the bounded graph qualification. V03 covers real
three-root execution, same-root branches, unequal grain, wrong root roles and
units, composite member keys, and opaque authoring acceptance versus observation
rejection. V08 has a paired-int64 weighted-mean source/fixed/cold slice; its full
numeric matrix remains R5.6. No D01-D22 debt is promoted.

| Consumer | R5.3 result | Remaining owner |
| --- | --- | --- |
| M03 | Independent occurrences, complete composite member identity, sum/count ratios, nested additive linear state and rollup, paired weighted means | R5.4 coordinates/groups; other exact method shapes remain unqualified |
| M04 | Five factories execute through the canonical graph; runtime leaves resolve the declared default event axis | R5.5 temporal expansion; R5.6 numeric matrix |
| M10 | Preserved: public graph bypasses source_admission/dataset_execution, Store 7 refuses legacy Dataset route, shared R6-R8 consumers still exist | R6-R8 / R5.7 reverse scan |
| M11 | Existing owner gains metric.weighted_mean and state_rollup.weighted_mean/linear; source/exchange use MethodSemantics persistent_state_kind | R5.4-R5.6 other states |
| M14 | Closed input union, explicit empty policies, ratio four-column state and weighted paired state recorded under plan 3.5; no dual reader/new Store generation | R5.7 full recovery matrix |
| M17 | Optional during typing, actual numeric/ratio return family, Help and current EN/ZH example alignment; packaged skills unchanged | R5.7 installed wheel; skill changes require explicit approval |

Nested nonlinear finishes and ratio error policy are not
qualified by this bounded slice. Positive nesting evidence is nested signed
linear over additive leaves and outer slices pushed through ratio/linear. Opaque positivity
means catalog authoring/require, not an invented direct observation permission.

## R5.4 migration outcome (2026-09-29)

The source-tree candidate and exact command results are recorded in
[evidence/r54](evidence/r54/README.md). R5.3 was already committed at the recorded
baseline; no prior dirty implementation was discarded. No commit or release was
performed for this candidate.

| Consumer | R5.4 change | Remaining owner |
| --- | --- | --- |
| M03/M05 | Complete coordinate tuple union, combined classifications, explicit targets, partial original-state reduction and current-row statistics use the public graph | R5.5 time grids, R5.6 full type matrix |
| M11 | Existing rule/method registry adds group attachment/completion, min/max, original mean and row-state merging; no second executor | Other unqualified method/type shapes remain rejected |
| M14 | OriginalReduce carries an ordered coordinate tuple; RowState carries explicit merge mode; row sum carries support count; Store 7 validates transported state | R5.7 installed candidate and full recovery matrix; no legacy wire reader |
| M17 | CountMethod and GroupedStatisticRelation join existing families; typing, native Help, dynamic continuations, independent export tests and both latest site editions are aligned | Packaged skills unchanged; their workflow guidance does not enumerate these APIs |
| D04 | Re-enabled pure fixed mean-selection planning with no source/part reads, Run or worker work | Closed by the new public-graph Runtime regression |
| D05-D08 | Re-enabled absent/unregistered DuckDB/Ibis diagnostic-version tests with unchanged exact plan and revenue 147 | Closed by four Runtime regressions |
| D14 | Re-enabled source-offline selected revenue 140 and original mean 140/3; duplicate/foreign/tampered component rejection retained | Closed by the new common exchange consumer |

The replaced legacy execution code was removed from these six tests only.
`source_admission`, `dataset_execution`, legacy compiler helpers and fixtures still
have R6-R8 consumers and remain in place. CLI routes through native Help and
needs no parallel inventory. No packaged skill or AGENTS.md edit was needed or
made. D01-D03, D09-D13 and D15-D22 keep their existing owners.

### R5.4 adversarial-review repairs

The review follow-up retains the same phase and no-release boundaries. Explicit
group-domain execution now persists the complete target; selected categories
transport their Dimension coordinate; foreign numeric predicates transport both
source bindings. Fixed grouped numeric row statistics preserve axes and use the
materialized rows, not reconstructed member rows. Help factory examples and
CountMethod acquisition, the statistic class docstring, and ordered arity gates
are aligned. The Runtime owner accepts the breaking frozen-state rules explicitly
in `session-state-and-runtime.md`. Evidence and item-by-item dispositions are in
`evidence/r54/review-fixes.json`; the candidate fingerprint is refreshed only after
verification. Diagnostic-version invariance and effective part-corruption tests
remain intact. No packaged skill or AGENTS.md change is required.

## R5.5 temporal consumer migration (2026-09-29)

The versioned qualification summary and reproduction commands are below. Detailed
logs in ignored `evidence/r55/` are local supplements; earlier partial-slice records
are historical and do not define the current scope.

| Item | Current consumer and disposition |
| --- | --- |
| M03/M05 | Public each/read/observe/group_by dispatches to the existing core, registered graph methods and Store 7. Endpoint and fold branches replace the former unconditional temporal refusal in graph_observation. No parallel executor was added. |
| D01/D02 | Historical calendar publication journeys now use graph Relation/time product/observe and common completed evidence. Old generation-six materialization/assertions were replaced; `[110,30]` remains independent. |
| D03 | Public identity-root observation/rollup restores SQLite revenue 30 with source preview/readiness/health and initial Run/primary submission assertions. |
| D09–D13 | Historical SQLite journeys use graph grouped members and metric.fold, preserving all five numerical oracles and adding refusal of unaligned fixed spatial merging. The old lazy-source dispatch/SQL-string assertions in those journeys were removed. |
| M07 | Attribute consumers use member_version.selection/version_predicate with frozen per-grid selections. Graph windows reuse observation.temporal.civil_bound; certified scopes retain their own boundary timezone and identity. |
| M08/M09 | Graph contribution normalization imports compiler.source_time.source_time. Its existing Ibis localization/rendering is reused; actual raw/normalized validation reads pass through SourceSession. |
| Shared R6–R9 | compiler/lowering and its temporal bucket/reset helpers remain consumers for legacy event/journey/lifecycle/interval paths. materialization/temporal_sql remains imported by mysql/trino/clickhouse/sqlite executors and governed-operation guards. These shared algorithms and remote qualification owners were retained, not duplicated or deleted wholesale. |
| V06/V07 | Independent source/report/grid zones, date, DST and disagreement checks; calendar per-occurrence proofs; cumulative overlap refusal; first/last/mean/min/max aligned pre-fold state and the staggered-device counterexample. |
| V10 | Table/Parquet produce, offline continuation and cold recovery for additive, cumulative and fold families compare values/state/K; required-part, receipt and version faults revoke continuation. |

The consumer inventory is `evidence/r55/temporal-consumers-completion.txt`.
No production hand-written temporal SQL was added. Fold reductions use Ibis
aggregation/window expressions and the registered fixed Python reducer. The
unpublished Store 7 encoding changes are explicit in the Runtime owner; there is
no new generation, dual reader or fallback route. R5.6 numerical qualification,
R5.7 package acceptance and unrelated historical debt remain separate.

Completion validation: the broad daily gate passed 5405 tests with 5 skips;
related Runtime passed 193 tests, plus 3 scalar endpoint and 6 final fold checks.
Site and whitespace gates passed. Exact candidate and command records remain
in the linked evidence; R5.6/R5.7 exclusions are not reclassified as passes.

### R5.5 review follow-up

The three review findings are repaired: fixed/cumulative DATE windows keep their
own timezone on a foreign-zone grid; grid construction uses incremental boundary
checks and skips the historical prefix for fixed-offset zones; zero-row fold
continuation retains explicit Arrow types. Regressions include repeated-hour
minute boundaries and source-offline empty grouped/singleton fold continuation.
The versioned summary below records the accepted scope and executable test owners.
Detailed local logs and fingerprints remain in ignored `evidence/r55/review-fixes-*`.

## R5.5 qualification summary

This versioned record is self-contained. Commit `8dccde674e` deliberately removed
run artifacts from Git while preserving local files. The ignored `evidence/`
directory remains a local diagnostics archive, not the sole authority for phase
acceptance. This record reports observed source-tree tests; another checkout must
run the commands below to establish its own execution evidence. No evidence files
are force-added, and the existing ignore policy is unchanged.

Qualified scope: unified graph / Runtime / Store 7 time products, endpoint reads,
certified occurrences, cumulative anchors and spatial sum before scalar
first/last/mean/min/max folds. Source forms are DuckDB table/Parquet and the required
SQLite UTC/date routes; fixed continuation uses registered local algorithms.
Full numerical qualification remains R5.6; wheel/package qualification remains
R5.7. Release-check, MinIO, real Agent and publication were not run.

| Obligation | Executable owner / independently specified oracle |
| --- | --- |
| Complete product, empty/partial cells, exact handles, DST and date | `tests/test_analysis_temporal_r55.py`: 8 entity/time rows; 23/25-hour days with explicitly listed UTC instants; cross-month week refusal; fixed DATE value 10 across a foreign-zone grid |
| Symbolic attribute selection | `tests/test_analysis_members_r52.py::test_grid_attribute_point_is_independent_of_member_version` and `::test_grid_snapshot_before_end_uses_symbolic_left_period`: explicit start/end/left-limit values, including [10,20] on the snapshot left period |
| Cumulative anchors and state | `test_cumulative_anchors_ignore_display_start`: all-history [1077,1176], month-to-date [1000,99], trailing [1000,499]; overlap refusal and scalar endpoints |
| Noncommuting fold | `test_fold_reaggregates_aligned_samples_before_time`: input devices [10,0] and [0,10]; spatial totals are [10,10], so every declared fold is the literal 10. Each direct/fixed/offline result is compared to that constant, never to another product result |
| D01/D02 calendar occurrence publication | `tests/test_lazy_source_algebra.py::test_semantic_calendar_validations_publish_each_required_occurrence`: [110,30], fixed total 140 and distinct completed calendar checks |
| D03 SQLite | `tests/test_sqlite_semantic_integration.py::test_sqlite_agent_native_authoring_journey`: 30 and daily [10,20], one primary stage read |
| D09–D13 SQLite | `tests/test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants`: first/last/mean/min/max totals 110/70/90/50/130, preserving all-null channel behavior |
| Recovery and damage | `tests/test_analysis_temporal_r55.py`: separate produce/offline/cold processes for table/Parquet x additive/cumulative/fold; compare values/state/K, reject missing/corrupt parts, receipt and version faults; typed empty fold continuation |

Observed repair candidate results: `make check-agent` exited 0 (5407 passed,
5 skipped; lint, typing, imports and API docs); temporal Runtime exited 0
(45 passed), temporal pure tests exited 0 (14 passed), touched-module typing and
whitespace exited 0. The earlier completion selection passed 193 Runtime tests;
it is historical evidence, not a rerun after every later edit. The first repair
broad attempt had two module-import failures; unchanged-code focused/full reruns
passed. Their cause remains unconfirmed. The earlier site build passed 321 pages;
site content was unchanged by the review fixes.

Reproduction commands (repository virtual environment required):

```sh
make test TESTS='tests/test_analysis_temporal_r55.py'
make runtime-test TESTS='tests/test_analysis_temporal_r55.py tests/test_analysis_members_r52.py tests/test_lazy_source_algebra.py tests/test_lazy_status_fold_admission.py tests/test_sqlite_semantic_integration.py'
make check-agent
git diff --check
```

### SQLite status-gate assertion migration

The `__mv_status` SQL alias belonged to the former lazy executor. The unified graph
owns Cell status and required checks separately, so retaining that alias would
assert an obsolete representation. Its behavioral obligations remain:

| Former SQLite assertion | Current contract and replacement |
| --- | --- |
| `validation_batch` contains `__mv_status` | Governed `SourceSession.batches` actually submits `analysis.graph.check` reads; the SQLite journey asserts the raw/normalized temporal check and successful exhausted submissions. Checks run before the artifact is returned |
| Exactly one primary SQL contains `__mv_status` | Exactly one `analysis.graph.stage` read carries `original_state__samples`, `original_state__fold_kind`, `cell_tag` and `cell_reason`; the same journey asserts a subsequent check read before successful return |
| Null/defined fold status and numerical values | The SQLite journey retains all five independent channel/total constants and the all-null channel assertion. Common exchange validates primary Cells and retained fold state |
| Unaligned spatial continuation | Additional refusal regression only; it does not replace either source-check or numerical obligations |

`tests/lazy_shared_assertions.py` remains the owner of the old SQL-shape assertions
for remote legacy consumers. No global deletion or remote qualification is implied.

### Additional review suggestion dispositions

- **Accepted:** plan header was stale; it now agrees with the bounded R5.5 record.
  Versioned consumers no longer require an ignored README to determine scope or
  reproduce tests. SQLite assertion migration is explicitly mapped and its actual
  governed reads are asserted on all five fold kinds.
- **Rejected as a contract mismatch:** `time_scope(end=scope.before_end)` is not a
  supported signature. `before_end` selects a version through `at=`; windows keep
  ordinary exclusive endpoints. Existing source tests exercise both validity and
  snapshot grid left limits. The owning temporal document now states the distinction.
- **Rejected as an oracle misreading:** `[10]` and the per-device dictionary are
  literal hand-calculated expectations. `fixed.rollup()` produces the actual side
  of the assertion, not its expected side. Importing `decode_samples` to test three
  manually authored malformed payloads is a rejection-unit test, not an oracle
  computed by the implementation.
- **Rejected as stale:** the current local README says implemented/verified; the
  quoted incomplete paragraph belongs to the superseded grid-only snapshot.
- **No speculative refactor:** the cited journeys have different responsibilities
  (calendar publication, SQLite fold admission, grid/fixed recovery); the temporal
  grid module primarily uses DuckDB, not a duplicated SQLite journey. The small
  `_component_temporal_policy` function enforces one common policy across component
  states and rejects incompatible combinations; it is not a passive forwarding API.
- The acknowledged pre-existing `time_dimension` documentation drift, candidate
  fingerprint allegation and restored-parameter allegation are not reopened.

Additional-suggestion validation: SQLite fold checks passed 5 cases; symbolic
grid endpoints and the independent fold counterexample passed 10 cases. The
post-change `make check-agent` exited 0 (5407 passed, 5 skipped; lint, typing,
imports and API docs passed), and `git diff --check` exited 0. No production
execution logic changed in this suggestion-adoption pass.


## R5.6 implementation checkpoint

Status: **complete within the local source-tree numerical qualification boundary**.
R5.7 package/whole-phase acceptance, R9 remote Runtime and real Agents remain
unverified and outside this task. This supersedes the earlier partial checkpoint.

The public quantile wrapper/input exports are removed without aliases. Metric
aggregate kinds and q determine exact/approximate identity; observation cannot
override them. Heavy computation stays in datasource SQL compiled by Ibis. Native
quantile precision limitations are disclosed. Unsupported exact definitions name
the corresponding approximate declaration and its availability, without fallback.

| Method / type | Qualified route and evidence |
| --- | --- |
| int64 sum/linear, extrema, mean, weighted mean, ratio | DuckDB SQL and fixed state; exact checked sums/products, once-rounded rational finish, large-integer cancellation/overflow and Fraction oracle |
| finite float64 sum/linear, extrema, mean, weighted mean, ratio | DuckDB SQL and fixed state; owner error bounds, finite checks, same non-Null pairing and zero-weight policy |
| Decimal sum/linear, extrema, mean, weighted mean, ratio | SQL integer quotient/remainder with one HALF_EVEN finish; input scale retained, exact component scales, source/fixed coordinate trees; Decimal/Fraction oracle |
| Duration sum/linear, extrema, mean, weighted mean, ratio/distinct | Native table microseconds and local Parquet s/ms/us/ns; checked int64 ticks, nearest-even means, same-unit ratio, Arrow/receipt unit preservation; calendar components rejected on raw source values |
| first/last/min/max/mean time folds and cumulative sum | All four numeric families; exact typed pre-fold samples, aligned spatial-then-temporal reduction, source/fixed and cold continuation |
| exact/approximate distinct and quantiles | int64/float64 table and Parquet; native Decimal quantile precision; duplicate, Null, empty, interpolation and q validation; no distribution/sketch state or original rollup |
| current-row count/count_defined | All four families; Null policy on logical and fixed relations; distribution results remain countable |
| recovery / state integrity | 64 artifacts across four families and table/Parquet, separate producer and two source-offline consumers; compare values, state and actual K; 256 missing/corrupt/receipt/version faults reject |

Decimal oracle coverage includes scales 0/2/6/18, large coefficients and signed
rounding. Integer ratio SQL is compared to 205 independent Fraction cases. Tests
vary row order, partitions and reduction trees. Native DuckDB INTERVAL does not
represent four independent fixed units: its admitted unit is us; Parquet provides
s/ms/us/ns without conversion to float. Approximate agreement on small fixtures
is not an error bound.

Typed folds and changed numeric finishing/extrema consumers use implementation
version 3 through existing Store 7 keys/receipts. Prior versions are rejected,
without dual reads, migration or state reconstruction. State exposes exact
`decimal:p:s` and `duration:unit` physical identifiers. Shared R6–R8 code remains.

Validation on Ibis 12.0.0, DuckDB 1.5.3 and PyArrow 25.0.1:

- `make check-agent`: 5432 passed, 5 skipped; lint, typing, imports and API docs pass.
- Related Runtime selection: 304 passed. Additional new cold int64/float64,
  cancellation, signed tick ties and typed row counts: 4 + 2 + 1 + 4 passed.
- Targeted typing: 262 files pass. Site build and `git diff --check` pass.
- Intermediate failures are retained and resolved: Decimal coordinate extrema
  precision, public numeric state projection, raw calendar-interval admission,
  floating linear validation, stale in-flight version registries and import order.
- No current blocked or failed required matrix cell. Package installation,
  publishing, R9 remote Runtime and real Agent validation were not run.

Reproduction:

```sh
make runtime-test TESTS='tests/test_analysis_numeric_r56.py tests/test_analysis_observation_r53.py tests/test_analysis_coordinates_r54.py tests/test_analysis_temporal_r55.py tests/test_public_quantile_input.py tests/test_sqlite_semantic_integration.py'
make check-agent
(cd site && npm run build)
git diff --check
```

The final combined Runtime selection contains 315 cases; the recorded acceptance
ran the 304-case selection and eleven subsequently added cases separately. Exact
commands, exits, method/type/route matrix and candidate hashes are in local
`evidence/r56/closure-*`. The versioned summary above is sufficient to reproduce
acceptance without those ignored local logs. No commit, push, wheel installation,
release, packaged-skill or AGENTS.md change was performed.

### R5.6 review repair verification

Three reproduced defects are repaired: unstable float denominator qualification,
Decimal fold-state narrowing, and rounded Decimal range validation. Float sums
and paired weights retain absolute magnitudes through source, coordinate and
fixed reductions; old implementation receipts do not continue. Source execution
remains Ibis SQL. Independent regressions live in
`tests/test_analysis_numeric_review_r56.py`. Repaired-candidate verification:

- 217 numeric Runtime cases passed, including 50 review regressions, grouped
  distribution refusal, reversed source facts, exchange batches and cold recovery.
- 59 focused registration/method/unit cases passed; both new test modules pass
  targeted typing. `make check-agent` passed: 5451 passed, 5 skipped, with lint,
  407-module typing, import contracts and API documentation stages passing.
- Site build and `git diff --check` passed. Local `evidence/r56/review-fixes-*`
  stores command logs, candidate hashes and the resolved intermediate failures.
- Earlier closure counts describe the pre-review candidate. No current review
  finding remains failed or blocked; R5.7, remote R9 and installed/Agent acceptance
  remain outside this verification.

### Additional review suggestions: disposition

- Accepted maintainability improvements: one internal semantic owner for the
  direct-only aggregate set; `specialize_numeric` names its actual Decimal,
  Duration and float64 scope, with independent positive/negative selection tests;
  Arrow scalar construction shares one physical owner; Duration metadata uses the
  datasource producer's named key. These are bounded refactors, not new public APIs.
- V09's reported missing quantile assertions was stale: direct-distribution tests
  already checked absent rollup actions, rollup refusal and current-row counts
  outside the distinct-only branch. Added grouped logical/fixed refusal coverage;
  the graph remains the single rights-checking owner rather than duplicating guards.
- No second opaque approximate branch exists in `graph_lowering.py`; its sole
  approximate calls are in the public definition-owned direct aggregate branch.
  Private legacy distribution method selection remains for existing R6–R8 consumers,
  as authorized; it does not restore a public observation override.
- V08 evidence is separated: raw source row reversal has its own boundary-fact
  tests; permutations/partition/reduction-tree checks in `merge_original` are
  in-memory oracle tests. Added public source/fixed sum tests with reversed raw
  facts and exchange batch sizes 1/2/1024. This is local source-exchange evidence,
  not remote backend or arbitrary distributed partition qualification.
- V11 remains a whole-phase R5.7 gate. The plan header explicitly distinguishes
  local R5.6 evidence from whole-R5 completion; already closed debt entries retain
  their own evidence and unresolved entries are not promoted.
