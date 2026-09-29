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
| F09 / C10 | [Semantic exactness](../../specs/semantic/semantic-object-model.md#metric-occurrences-and-policies): accuracy wrapper, q, exact distinct/linear interpolation; no original K | R5.6; V09 |
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
| M03 | `graph_observation.py::observe_members/observe_ratio_members` and `graph_composition.py` -> core/rules, graph lowering and source bindings | F03-F06; R5.3/R5.4 | Per-occurrence bindings and complete tuple domain; remove limited graph branches rather than add an alternate executor; V03-V05 |
| M04 | `A/runtime_metric.py` re-exports `S/runtime_metric.py`; `S/runtime_metric_lowering.py`, `_metric_resolution.py`, `metric_graph*.py` resolve/lower into semantic graph; tests/runtime replay workers also import factories | F03/F04; R5.3 | Keep canonical graph owner and five factories; remove duplicate Analysis inference/old replay consumers after replacement; V03/V08, runtime_metric suites |
| M05 | `A/session/_lazy_sources.py` calls `observation.population.make_population` and `observation.metric.make_observation`; `metric.py` delegates aggregation, coordinate and retained rollup | F01-F06; R5.2-R5.4/R5.7 | Migrate public tests to members/read/observe/group_by/rollup/summarize; delete R5 legacy nodes and calls when no R6-R8 consumer remains; no forwarding shim |
| M06 | `observation/{aggregation,coordinates,rollup}.py` bind_aggregation/with_dimensions/with_time_axis/retained_aggregate; `compiler/lowering.py` and `operators/rollup.py` consume their contracts | F05-F07; R5.4/R5.5 | Preserve independent business oracles, migrate algorithms into shared rules/methods; delete migrated R5 dispatch, retain documented R6-R8 shared consumers until their cutover |
| M07 | `graph_observation.py:185` calls `observation.temporal.civil_bound`; same helper called by `compiler/lowering.py`, `observation/coordinates.py`, `operators/rollup.py` | F07; R5.5 | Extract still-valid boundary semantics to the common temporal owner and move actual callers; do not delete temporal.py first or retain a forwarding shim; V06 |
| M08 | `compiler/temporal.py::bucket/bucket_end/cumulative_start/endpoint_reset_start`; bucket callers include `compiler/distinct_fold.py`, `compiler/lowering.py`, temporal tests | F07/F08; R5.5 | Replace R5 time consumers with admitted Ibis/registered local algorithms including check expressions; inspect remaining Event/Lifecycle references before deletion; V06/V07 |
| M09 | `materialization/temporal_sql.py::lower_temporal` called by SQLite/MySQL/Trino/ClickHouse execution and ClickHouse Event SQL | Temporal owner and R1 adapter boundary; R5.5 for R5, R7/R9 for remaining consumers | Name alone does not prove handwritten SQL: inspect Ibis rewrites/UDF signatures separately from SQL text builders. Migrate R5 business/check reads through SourceSession; do not delete shared backend/Event support early |
| M10 | `operators/registry.py::legacy_source_migration_stage`, implementation/source_unsupported_reason; compiler/source_admission and materialization/dataset_execution consume old declarations | F03/F11; R5.3-R5.7 | Remove each migrated R5 registration/block branch; stage function already maps delta/comparison to R6, Event/Lifecycle R7, candidate/forecast/association R8. Do not broaden default admission |
| M11 | `methods/registry.py::REGISTRY` iterates `semantics.CONNECTED_METHODS` and `builtin.implementations`; core/rules, graph plan/lowering/execution consume selected rules | F06/F10; R5.3-R5.6 | Extend one method owner, exact qualification keys and typed states; no duplicate registration in old operators. Private row.weighted_mean remains non-public and is not an R5 requirement |
| M12 | `S/_quantile.py::quantile_metric` currently exposes method= and old session.observe guidance; observation/compiler distribution/distinct modules and quantile tests consume it | F09/F10; R5.6 | Replace target input with accuracy= without compatibility alias; preserve q, direct numeric oracles and algorithm evidence; remove migrated old method routing; V09/V12 |
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
| V09 | F09/F10; R5.6 | `test_public_quantile_input.py`, `test_lazy_distinct_numeric.py`, `test_lazy_distinct_contracts.py` | Same new file: sorted linear interpolation with Fraction, full distinct identity, empty/Null/q/bool bounds, actual accuracy/algorithm, no automatic fallback/original K |
| V10 | F11; each package/R5.7 | `test_analysis_graph_publication_r44.py`, `test_analysis_dsl_exchange.py`, D04/D14-D22 | `test_analysis_recovery_r57.py`: per-state produce/continue/recover processes, exact fixed hit, source freshness/sharing, no Semantic/source/DuckDB, corrupted/missing/reordered parts, empty/unfinished/close-failed streams, fault visibility; L6 value/semantics/K separately |
| V11 | M01-M17; R5.7 | Existing debt files, R4.6 residual scan | Refresh static and dynamic consumer scan; restore D01-D22 without deleting numeric/fault oracles, adding xfail or keeping old public aliases; compare installed wheel inventory |
| V12 | F02/F04/F09; each package/R5.7 | `test_public_surface.py`, `test_analysis_help_resolution.py`, `test_cutover_documentation_examples.py`, `test_analysis_runtime_wheel.py` | Positive/negative typing per closed variant, repr/show/contract and errors, independent Help budgets/reachability, CLI and both latest editions; same candidate wheel public journeys |

## Disclosure, checks and handoff

Each activating package owns signatures/docstrings, exports, native Help,
structured expected/received/repair errors, dynamic actions based on retained
state, independent reachability/budget tests, CLI and latest English/Chinese
examples for its rows. R5.2 owns member/read families; R5.3 the five factories and
observation; R5.4 grouping and row statistics; R5.5 grid/boundaries; R5.6 accuracy
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
