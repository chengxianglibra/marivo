# Unit-test triage — 2026-10-16

This is a diagnostic snapshot for HEAD `1d02fccb7a` plus the test repairs in this working tree. It does not configure pytest or suppress failures.

## Evidence and scope

| Run | Passed | Failed | Errors |
| --- | ---: | ---: | ---: |
| Initial complete default suite | 3697 | 739 | 53 |
| Final complete default suite | 3922 | 510 | 10 |

The complete diagnostic command was `.venv/bin/pytest -q --tb=short --maxfail=0 --junitxml=/tmp/marivo-unit-final.xml`. The initial run used the same default marker selection and disabled early stopping. The default suite excludes `slow`, `runtime` and `release`; no external services were started. No skip/xfail markers or test-selection configuration were added.

Counts are not a one-to-one count of fixes: obsolete entry-point tests were replaced by current-contract checks, and collection repairs made previously hidden tests runnable.

Comparing exact pytest node IDs against the initial complete scan found **zero newly failing cases**. The final focused regression run after the typing refinements passed 20 tests; the changed-file diagnostic passed 520 tests and retained 34 already-enumerated deferred failures. The focused publication owner run passed 58 tests.

`make check-agent` passed formatting, Ruff/import contracts and typing (323 source files), then stopped in the default tests on the known unavailable paths. `make docs-api-agent` passed separately. Final `make lint-agent` passed. Focused typing for the shared helper and eight replacement test modules passed (9 files). An additional strict run over all edited ordinary pytest modules reported 560 errors in 28 files, including pre-existing untyped fixtures and optional-driver stubs; ordinary pytest modules are outside the repository typing gate. This broader optional probe is not claimed as passing.

Logs for this session: `/tmp/marivo-unit-baseline.log`, `/tmp/marivo-unit-check-agent.log`, `/tmp/marivo-unit-final.log`, `/tmp/marivo-unit-final.xml`, `/tmp/marivo-unit-changed-tests.log`, `/tmp/marivo-unit-new-typing.log`, `/tmp/marivo-unit-modified-typing.log`, `/tmp/marivo-unit-final-lint.log`, `/tmp/marivo-unit-api-docs.log`.

## Repairs and coverage ownership

- Migrated shared sales/DSL/calendar declarations to capture, finalize and zero-argument expression declarations; bumped the cached sales template version. Kept source rows isolated and moved repeated preview connection injection into `tests/shared_fixtures.py`.
- Migrated removed Catalog detail types and accessors to current Entity/Relationship/field contracts. Source-health checks inspect physical inputs; published Metric certification remains rejected by its existing public contract.
- Replaced retired Metric/Ibis authoring and execution assertions with current unit inference, explicit reauthoring errors, closed definition identity/dependency tests and real DuckDB Metric previews. Physical Materializer source scopes, schemas, lookup errors, row budgets, provenance and connection ownership remain covered.
- Updated the 45 method registration snapshots and exact file-route qualification expectations after saved-result execution routes were removed. The snapshot now includes the current ordinary Count declarations directly.
- Migrated cross-project datasource observations, raw SQL rejection, unconfirmed connection release, Session readback and result disclosure to Query/terminal Artifact contracts.
- Removed tests invoking the deliberately rejected graph publisher. Their current atomicity, lost-acknowledgement, corruption, reader, saved-record and release owners are `tests/analysis/materialization/test_terminal_publication.py`; the focused publication run passed 58 tests. Kept low-level exchange/storage/generation checks (5 passed).

Current numerical and execution coverage also remains in `test_query_preparation.py`, `test_query_schedule.py`, `test_terminal_publication.py`, `test_observation_binding.py`, `test_query_artifact_boundary.py`, `test_semantic_expressions.py`, `test_metric_expression_graph.py` and the independent numeric kernels. The old private Metric-IR lowering/Metric-on-Ibis-table contracts are not compatibility targets.

## Deferred blockers

The [redesign implementation plan](../superpowers/plans/2026-10-13-lazy-analysis-dsl-redesign-implementation.md) marks R3–R8 as not started. The table groups each remaining case by its primary missing owner; journeys can depend on multiple stages and the final R8 public cutover. It is a test-porting/development dependency map, not a promise to restore old receivers or saved-result continuation.

| Primary owner | Cases | Required work |
| --- | ---: | --- |
| R3 | 164 | Population/member execution, identity-preserving read/classify, membership binding or internal samples have no new execution path yet (R3.1–R3.5). Legacy member receivers cannot consume the published R1 Metric graph. |
| R4 | 111 | Selection, result Column composition and reductions need the R3 domain handoff and R4.1–R4.5 Query path. Complex statistics must move to pandas; the plan does not restore the retired statistical DSL. |
| R5 | 57 | Comparison/reference/attribution journeys need new Query endpoints and complete-key correspondence (R5, with R6 for original-grain attribution). Old Signature/Artifact endpoints cannot be adapted into execution inputs. |
| R6 | 95 | Original-grain/group reductions, retained numeric components and route/cost journeys need R3 membership and R6 original-Metric composition. Current ordinary R2 source reduction is not a substitute for those membership/rollup contracts. |
| R7 | 89 | Windows, cumulative definitions and period alignment need R7 plus R3 samples and R5 endpoint correspondence. The retired ms.cumulative/fold factories cannot be re-enabled as a repair. |
| Remote qualification | 4 | Metric preview explicitly admits DuckDB only. These four tests expect remote numeric execution; provider qualification and corresponding integration evidence are required before extending that boundary (R8.4). Entity/field deadline and release tests already pass. |

All **520** remaining cases are enumerated below: 510 failures and 10 setup errors. The recorded error is the first observed blocker. A retired declaration or finalized-capture error can mask downstream numerical assertions; those assertions remain unverified until the owning path is implemented and the fixture is migrated. Do not interpret this snapshot as numerical or Runtime qualification.

Artifact DSL continuation is permanently excluded by the current contract. Such cases must preserve their business/numerical oracle while moving the computation before execute, or use exported pandas data where the design calls for postprocessing. They must not gain an Artifact-to-Query adapter.

## Exact remaining cases

### `tests/analysis/graph/test_analysis_dsl_public.py`

Primary owner: **R4**; 13 case(s).

```text
tests/analysis/graph/test_analysis_dsl_public.py::test_public_comparison_rejects_different_members_and_metric_units [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_contract_actions_and_result_card [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_empty_and_undefined_cards [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_failed_run_preserves_prior_artifact [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_fixed_coordinate_rollup_uses_retained_group_state [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_grouped_foreign_entity_observation_infers_unique_route [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_j1_scope_uses_persisted_report_timezone [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_j2_selected_members_and_next_month_mean [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_j3_ratio_rollup_retains_original_components [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_j4_renamed_entity_and_fields_keep_the_public_route [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_mixed_fixed_and_live_rejects_before_run [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_singleton_same_entity_observation_allows_omitted_route [failed]
tests/analysis/graph/test_analysis_dsl_public.py::test_public_wrong_relationship_rejects_before_execution [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/graph/test_capability_consumers.py`

Primary owner: **R6**; 14 case(s).

```text
tests/analysis/graph/test_capability_consumers.py::test_aware_native_repeated_hour_keeps_distinct_instants[duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_certified_unequal_calendar_owns_native_boundaries[duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_civil_date_grid_keeps_date_keys_on_foreign_zone[duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_cumulative_keeps_anchor_and_overlap[all-history-duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_cumulative_keeps_anchor_and_overlap[month-reset-duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_cumulative_keeps_anchor_and_overlap[trailing-overlap-duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_declared_string_time_uses_owned_execution_functions[duckdb-dst] [failed]
tests/analysis/graph/test_capability_consumers.py::test_declared_string_time_uses_owned_execution_functions[duckdb-utc] [failed]
tests/analysis/graph/test_capability_consumers.py::test_dst_grid_keeps_instant_edges_and_fixed_coordinate[fall-duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_dst_grid_keeps_instant_edges_and_fixed_coordinate[spring-duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_native_observation_does_not_audit_wall_times_outside_its_scope[duckdb-2026-03-08 02:30:00-2026-03-09-2026-03-10] [failed]
tests/analysis/graph/test_capability_consumers.py::test_native_timestamp_retains_reader_and_grid_authority[duckdb] [failed]
tests/analysis/graph/test_capability_consumers.py::test_original_mean_preserves_component_weights[duckdb-int64] [failed]
tests/analysis/graph/test_capability_consumers.py::test_original_mean_preserves_component_weights[duckdb-string] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_computation_rollup_runtime.py`

Primary owner: **R6**; 1 case(s).

```text
tests/analysis/graph/test_computation_rollup_runtime.py::test_duckdb_decimal_mean_retains_captured_scale [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/analysis/graph/test_computation_runtime.py`

Primary owner: **R3**; 9 case(s).

```text
tests/analysis/graph/test_computation_runtime.py::test_catalog_linear_over_decimal_metrics_publishes_exact_decimal [failed]
tests/analysis/graph/test_computation_runtime.py::test_computed_measure_decimal_subtraction_aggregates_exactly [failed]
tests/analysis/graph/test_computation_runtime.py::test_computed_measure_graph_rejects_before_business_reads_and_run[net_total] [failed]
tests/analysis/graph/test_computation_runtime.py::test_computed_measure_graph_rejects_before_business_reads_and_run[net_weighted] [failed]
tests/analysis/graph/test_computation_runtime.py::test_computed_measure_integer_arithmetic_aggregates_exactly [failed]
tests/analysis/graph/test_computation_runtime.py::test_computed_measure_multiplication_publishes_exact_decimal [failed]
tests/analysis/graph/test_computation_runtime.py::test_direct_column_decimal_measure_regression [failed]
tests/analysis/graph/test_computation_runtime.py::test_runtime_linear_over_int64_measures_stays_int64 [failed]
tests/analysis/graph/test_computation_runtime.py::test_weighted_mean_over_computed_measure_executes [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.measure received retired declaration parameters.

### `tests/analysis/graph/test_cost_scenarios.py`

Primary owner: **R6**; 2 case(s).

```text
tests/analysis/graph/test_cost_scenarios.py::test_duckdb_cost_routes_keep_the_original_exact_sum [failed]
tests/analysis/graph/test_cost_scenarios.py::test_duckdb_local_original_sum_consumes_group_input [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_count_rollup_backends.py`

Primary owner: **R6**; 2 case(s).

```text
tests/analysis/graph/test_count_rollup_backends.py::test_native_count_rollup_and_group_state[duckdb-table-int64] [failed]
tests/analysis/graph/test_count_rollup_backends.py::test_native_count_rollup_and_group_state[duckdb-view-string] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.count is a retired declaration factory.

### `tests/analysis/graph/test_distribution_consumers.py`

Primary owner: **R3**; 8 case(s).

```text
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_distinct-decimal] [failed]
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_distinct-int64] [failed]
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_distinct-string] [failed]
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_quantile-int64] [failed]
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-distinct-int64] [failed]
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-distinct-timestamp] [failed]
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-identity-int64] [failed]
tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-quantile-int64] [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.

### `tests/analysis/graph/test_domain_contract.py`

Primary owner: **R3**; 1 case(s).

```text
tests/analysis/graph/test_domain_contract.py::test_registered_templates_and_non_scalar_units_keep_their_actions [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/graph/test_execution_consumers.py`

Primary owner: **R3**; 2 case(s).

```text
tests/analysis/graph/test_execution_consumers.py::test_independent_equal_nodes_execute_distinct_source_stages [failed]
tests/analysis/graph/test_execution_consumers.py::test_public_source_reexecution_and_fixed_hit[duckdb] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_file_admission.py`

Primary owner: **R3**; 9 case(s).

```text
tests/analysis/graph/test_file_admission.py::test_file_construction_and_refusals_never_submit_business_rows[float-sum-csv] [failed]
tests/analysis/graph/test_file_admission.py::test_file_construction_and_refusals_never_submit_business_rows[float-sum-local-json] [failed]
tests/analysis/graph/test_file_admission.py::test_file_construction_and_refusals_never_submit_business_rows[integer-mean-csv] [failed]
tests/analysis/graph/test_file_admission.py::test_file_construction_and_refusals_never_submit_business_rows[integer-mean-local-json] [failed]
tests/analysis/graph/test_file_admission.py::test_file_construction_and_refusals_never_submit_business_rows[metadata-csv] [failed]
tests/analysis/graph/test_file_admission.py::test_file_construction_and_refusals_never_submit_business_rows[metadata-local-json] [failed]
tests/analysis/graph/test_file_admission.py::test_json_request_sources_are_refused_before_source_open[http] [failed]
tests/analysis/graph/test_file_admission.py::test_json_request_sources_are_refused_before_source_open[parameterized] [failed]
tests/analysis/graph/test_file_admission.py::test_json_request_sources_are_refused_before_source_open[shadow-http] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_full_grid_unknown.py`

Primary owner: **R3**; 2 case(s).

```text
tests/analysis/graph/test_full_grid_unknown.py::test_business_coverage_source_selection_keeps_original_support [failed]
tests/analysis/graph/test_full_grid_unknown.py::test_public_duration_business_coverage [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/graph/test_identity_observation_lowering.py`

Primary owner: **R3**; 1 case(s).

```text
tests/analysis/graph/test_identity_observation_lowering.py::test_public_identity_observation_executes_and_retains_outside_window_members [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/graph/test_inspection_boundaries.py`

Primary owner: **R3**; 1 case(s).

```text
tests/analysis/graph/test_inspection_boundaries.py::test_missing_backing_discards_native_exception_and_locator [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.

### `tests/analysis/graph/test_local_check_inheritance.py`

Primary owner: **R3**; 2 case(s).

```text
tests/analysis/graph/test_local_check_inheritance.py::test_period_comparison_coverage_finishes_before_inherited_deviation[False] [failed]
tests/analysis/graph/test_local_check_inheritance.py::test_period_comparison_coverage_finishes_before_inherited_deviation[True] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/graph/test_local_file_cost_routes.py`

Primary owner: **R6**; 5 case(s).

```text
tests/analysis/graph/test_local_file_cost_routes.py::test_file_cost_original_sum_executes_exact_real_file_route[csv-ibis] [failed]
tests/analysis/graph/test_local_file_cost_routes.py::test_file_cost_original_sum_executes_exact_real_file_route[csv-ibis_python] [failed]
tests/analysis/graph/test_local_file_cost_routes.py::test_file_cost_original_sum_executes_exact_real_file_route[local-json-ibis] [failed]
tests/analysis/graph/test_local_file_cost_routes.py::test_file_cost_original_sum_executes_exact_real_file_route[local-json-ibis_python] [failed]
tests/analysis/graph/test_local_file_cost_routes.py::test_file_cost_original_sum_executes_exact_real_file_route[parquet-ibis_python] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_metric_observation_resolution.py`

Primary owner: **R3**; 3 case(s).

```text
tests/analysis/graph/test_metric_observation_resolution.py::test_business_completeness_shares_resolution_and_keeps_typed_refusals [failed]
tests/analysis/graph/test_metric_observation_resolution.py::test_next_observation_rechecks_physical_numeric_type [failed]
tests/analysis/graph/test_metric_observation_resolution.py::test_public_observation_resolves_once_and_retains_components [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/graph/test_multiroot_consumers.py`

Primary owner: **R3**; 5 case(s).

```text
tests/analysis/graph/test_multiroot_consumers.py::test_independent_roots_and_temporal_fold[linear_decimal-duckdb] [failed]
tests/analysis/graph/test_multiroot_consumers.py::test_independent_roots_and_temporal_fold[linear_fanout-duckdb] [failed]
tests/analysis/graph/test_multiroot_consumers.py::test_independent_roots_and_temporal_fold[linear_float-duckdb] [failed]
tests/analysis/graph/test_multiroot_consumers.py::test_independent_roots_and_temporal_fold[ratio_decimal-duckdb] [failed]
tests/analysis/graph/test_multiroot_consumers.py::test_independent_roots_and_temporal_fold[spatial_mean-duckdb] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_native_direct.py`

Primary owner: **R6**; 4 case(s).

```text
tests/analysis/graph/test_native_direct.py::test_native_late_invalid_source_frame_never_publishes [failed]
tests/analysis/graph/test_native_direct.py::test_native_sum_preserves_known_zero_for_null_and_empty_inputs [failed]
tests/analysis/graph/test_native_direct.py::test_native_sum_reads_only_terminal_exchange_with_all_checks_and_fresh_runs [failed]
tests/analysis/graph/test_native_direct.py::test_source_local_comparison_keeps_its_existing_staged_prefix [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_observation_grain.py`

Primary owner: **R6**; 23 case(s).

```text
tests/analysis/graph/test_observation_grain.py::test_complete_member_entity_keeps_compound_key [failed]
tests/analysis/graph/test_observation_grain.py::test_component_domains_union_complete_tuples [failed]
tests/analysis/graph/test_observation_grain.py::test_direct_aggregate_uses_selected_contributions[count] [failed]
tests/analysis/graph/test_observation_grain.py::test_direct_aggregate_uses_selected_contributions[count_distinct] [failed]
tests/analysis/graph/test_observation_grain.py::test_direct_aggregate_uses_selected_contributions[mean] [failed]
tests/analysis/graph/test_observation_grain.py::test_direct_aggregate_uses_selected_contributions[median] [failed]
tests/analysis/graph/test_observation_grain.py::test_direct_aggregate_uses_selected_contributions[percentile] [failed]
tests/analysis/graph/test_observation_grain.py::test_direct_aggregate_uses_selected_contributions[sum] [failed]
tests/analysis/graph/test_observation_grain.py::test_direct_aggregate_uses_selected_contributions[weighted_mean] [failed]
tests/analysis/graph/test_observation_grain.py::test_empty_members_keep_overall_buckets [failed]
tests/analysis/graph/test_observation_grain.py::test_grid_observation_keeps_time_without_member_axis [failed]
tests/analysis/graph/test_observation_grain.py::test_invalid_axes_and_removed_entries [failed]
tests/analysis/graph/test_observation_grain.py::test_member_and_contribution_grouping [failed]
tests/analysis/graph/test_observation_grain.py::test_member_channel_axes_keep_identity_and_selected_scope[False] [failed]
tests/analysis/graph/test_observation_grain.py::test_member_channel_axes_keep_identity_and_selected_scope[True] [failed]
tests/analysis/graph/test_observation_grain.py::test_member_classification_survives_rollup_for_fixed_attribution[False] [failed]
tests/analysis/graph/test_observation_grain.py::test_member_classification_survives_rollup_for_fixed_attribution[True] [failed]
tests/analysis/graph/test_observation_grain.py::test_member_groups_preserve_live_and_fixed_comparisons [failed]
tests/analysis/graph/test_observation_grain.py::test_multiple_member_classifications_keep_complete_time_keys[False] [failed]
tests/analysis/graph/test_observation_grain.py::test_multiple_member_classifications_keep_complete_time_keys[True] [failed]
tests/analysis/graph/test_observation_grain.py::test_overall_and_member_observations [failed]
tests/analysis/graph/test_observation_grain.py::test_overall_bucket_coverage_marks_unknown_after_aggregation [failed]
tests/analysis/graph/test_observation_grain.py::test_overall_ratio_differs_from_member_mean [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.count is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.weighted_mean is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.where is a retired declaration factory.

### `tests/analysis/graph/test_physical_cost_fixtures.py`

Primary owner: **R6**; 3 case(s).

```text
tests/analysis/graph/test_physical_cost_fixtures.py::test_local_file_profiles_retain_full_original_facts_and_fixed_total[csv] [failed]
tests/analysis/graph/test_physical_cost_fixtures.py::test_local_file_profiles_retain_full_original_facts_and_fixed_total[local-json] [failed]
tests/analysis/graph/test_physical_cost_fixtures.py::test_local_file_profiles_retain_full_original_facts_and_fixed_total[parquet] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_premise_verification.py`

Primary owner: **R3**; 5 case(s).

```text
tests/analysis/graph/test_premise_verification.py::test_checked_pairing_checks_unknown_images_and_reuses_proven_images [failed]
tests/analysis/graph/test_premise_verification.py::test_declared_native_inputs_need_only_one_terminal_read [failed]
tests/analysis/graph/test_premise_verification.py::test_observation_completeness_depends_on_exact_matching_assumptions [failed]
tests/analysis/graph/test_premise_verification.py::test_related_field_null_is_counted_and_missing_owner_is_rejected [failed]
tests/analysis/graph/test_premise_verification.py::test_shared_matching_assumption_never_becomes_completed_evidence [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/graph/test_public_composition.py`

Primary owner: **R4**; 13 case(s).

```text
tests/analysis/graph/test_public_composition.py::test_empty_numeric_selection_has_distinct_row_policies [failed]
tests/analysis/graph/test_public_composition.py::test_exact_artifact_corruption_blocks_recovery_and_dynamic_k[part] [failed]
tests/analysis/graph/test_public_composition.py::test_exact_artifact_corruption_blocks_recovery_and_dynamic_k[snapshot] [failed]
tests/analysis/graph/test_public_composition.py::test_exact_artifact_corruption_blocks_recovery_and_dynamic_k[state_version] [failed]
tests/analysis/graph/test_public_composition.py::test_incompatible_inputs_reject_before_business_or_artifact_reads [failed]
tests/analysis/graph/test_public_composition.py::test_invalid_threshold_rejects_without_another_run[9223372036854775808] [failed]
tests/analysis/graph/test_public_composition.py::test_invalid_threshold_rejects_without_another_run[nan] [failed]
tests/analysis/graph/test_public_composition.py::test_members_schema_preflight_does_not_submit_business_rows [failed]
tests/analysis/graph/test_public_composition.py::test_public_history_and_guarded_abandonment_use_graph_runs [failed]
tests/analysis/graph/test_public_composition.py::test_public_parquet_spearman_has_separate_source_evidence [failed]
tests/analysis/graph/test_public_composition.py::test_ratio_complete_coordinate_tuple_and_fixed_original_components [failed]
tests/analysis/graph/test_public_composition.py::test_ratio_original_state_and_empty_contribution[empty_group] [failed]
tests/analysis/graph/test_public_composition.py::test_ratio_original_state_and_empty_contribution[zero_denominator] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/graph/test_public_relationships.py`

Primary owner: **R3**; 1 case(s).

```text
tests/analysis/graph/test_public_relationships.py::test_authored_relationship_keys_execute_metric [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.count is a retired declaration factory.

### `tests/analysis/graph/test_relationship_binding.py`

Primary owner: **R3**; 8 case(s).

```text
tests/analysis/graph/test_relationship_binding.py::test_classification_read_requires_owner_for_its_complete_input_scope[False] [failed]
tests/analysis/graph/test_relationship_binding.py::test_classification_read_requires_owner_for_its_complete_input_scope[True] [failed]
tests/analysis/graph/test_relationship_binding.py::test_classification_scope_and_complete_components [failed]
tests/analysis/graph/test_relationship_binding.py::test_coordinate_tampering_and_frozen_roles [failed]
tests/analysis/graph/test_relationship_binding.py::test_inference_and_root_identity [failed]
tests/analysis/graph/test_relationship_binding.py::test_multiroot_branch_arithmetic [failed]
tests/analysis/graph/test_relationship_binding.py::test_roles_and_independent_read_paths [failed]
tests/analysis/graph/test_relationship_binding.py::test_timed_classification_cannot_remove_consumed_contributions [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/graph/test_runtime_concurrency.py`

Primary owner: **R3**; 2 case(s).

```text
tests/analysis/graph/test_runtime_concurrency.py::test_activation_is_guarded_and_existing_handle_owner_is_stable [setup error]
tests/analysis/graph/test_runtime_concurrency.py::test_different_sessions_overlap_inside_real_duckdb_queries [setup error]
```

Observed first blockers:

- failed on setup with "marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/analysis/materialization/test_analysis_state.py`

Primary owner: **R3**; 2 case(s).

```text
tests/analysis/materialization/test_analysis_state.py::test_current_continuation_registry_resolves_to_actual_bound_methods [failed]
tests/analysis/materialization/test_analysis_state.py::test_retired_standalone_group_artifact_requires_reexecution_without_writes [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/materialization/test_execution_error_recovery.py`

Primary owner: **R3**; 5 case(s).

```text
tests/analysis/materialization/test_execution_error_recovery.py::test_control_exception_keeps_its_type_and_cleans_the_admitted_run [failed]
tests/analysis/materialization/test_execution_error_recovery.py::test_member_construction_is_lazy_and_execution_retains_complete_keys [failed]
tests/analysis/materialization/test_execution_error_recovery.py::test_pre_admission_failure_never_inherits_the_previous_run [failed]
tests/analysis/materialization/test_execution_error_recovery.py::test_real_pairing_check_failure_is_bound_to_its_failed_run [failed]
tests/analysis/materialization/test_execution_error_recovery.py::test_source_failure_retries_have_exact_readable_runs_and_retained_history [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/materialization/test_expression_entity.py`

Primary owner: **R3**; 1 case(s).

```text
tests/analysis/materialization/test_expression_entity.py::test_output_keys_rejected_before_run [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/analysis/materialization/test_graph_dataset_reads.py`

Primary owner: **R3**; 5 case(s).

```text
tests/analysis/materialization/test_graph_dataset_reads.py::test_multiple_findings_read_once_and_keep_owned_paging [failed]
tests/analysis/materialization/test_graph_dataset_reads.py::test_projection_reads_each_receipt_once_per_call[association] [failed]
tests/analysis/materialization/test_graph_dataset_reads.py::test_projection_reads_each_receipt_once_per_call[deviation] [failed]
tests/analysis/materialization/test_graph_dataset_reads.py::test_projection_reads_each_receipt_once_per_call[forecast] [failed]
tests/analysis/materialization/test_graph_dataset_reads.py::test_projection_reads_each_receipt_once_per_call[runs] [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/materialization/test_native_graph_failures.py`

Primary owner: **R3**; 9 case(s).

```text
tests/analysis/materialization/test_native_graph_failures.py::test_graph_has_no_hidden_600_second_expiry [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[False-after_commit] [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[False-before_commit] [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[False-graph_admitted] [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[False-source_execute] [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[True-after_commit] [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[True-before_commit] [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[True-graph_admitted] [failed]
tests/analysis/materialization/test_native_graph_failures.py::test_source_and_python_failures_preserve_atomic_publication[True-source_execute] [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.

### `tests/analysis/materialization/test_producer_recovery.py`

Primary owner: **R3**; 1 case(s).

```text
tests/analysis/materialization/test_producer_recovery.py::test_http_source_remains_a_datasource_boundary [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/materialization/test_public_refusals.py`

Primary owner: **R3**; 3 case(s).

```text
tests/analysis/materialization/test_public_refusals.py::test_public_composition_refuses_before_source_or_run [failed]
tests/analysis/materialization/test_public_refusals.py::test_public_cross_datasource_observation_refuses_before_business_read [failed]
tests/analysis/materialization/test_public_refusals.py::test_public_required_part_missing_blocks_recovery_and_exact_hit [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/materialization/test_public_schema_drift.py`

Primary owner: **R3**; 1 case(s).

```text
tests/analysis/materialization/test_public_schema_drift.py::test_public_schema_drift_rejects_before_business_read_and_retries [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/materialization/test_versioned_ownership.py`

Primary owner: **R3**; 2 case(s).

```text
tests/analysis/materialization/test_versioned_ownership.py::test_versioned_read_foreign_predicates_refuse_before_read_or_run[snapshot] [failed]
tests/analysis/materialization/test_versioned_ownership.py::test_versioned_read_foreign_predicates_refuse_before_read_or_run[validity] [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.

### `tests/analysis/numeric/test_analysis_attribution_runtime.py`

Primary owner: **R5**; 10 case(s).

```text
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_additive_source_and_fixed [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_common_other_mapping_each_parent_and_resolution[hierarchy-fixed] [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_common_other_mapping_each_parent_and_resolution[joint-source] [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_float_view_arithmetic_retains_allocation_error_bounds [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_new_source_evaluation_preserves_fixed_basis [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_numeric_view_rank_uses_typed_other_order [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_period_buckets_and_retained_coarsened_partition[False] [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_public_decimal_small_partition_rounding_refuses_publication [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_static_refusals_and_shared_compiled_submissions [failed]
tests/analysis/numeric/test_analysis_attribution_runtime.py::test_zero_component_basis_cannot_hide_under_other [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_cohort.py`

Primary owner: **R3**; 3 case(s).

```text
tests/analysis/numeric/test_analysis_cohort.py::test_full_entity_time_cohort [failed]
tests/analysis/numeric/test_analysis_cohort.py::test_no_time_quantifiers_and_complete_target_decisions [failed]
tests/analysis/numeric/test_analysis_cohort.py::test_previous_week_mean_selects_users_for_current_week_spending [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_comparison_runtime.py`

Primary owner: **R5**; 25 case(s).

```text
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_cohort_group_metric_empty_preserves_metric_null_policy [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_comparison_construction_is_lazy_and_shared_source_runs_once [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_comparison_of_float_original_expression_operands[linear] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_comparison_of_float_original_expression_operands[ratio] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_comparison_of_float_original_expression_operands[weighted] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_float_fold_comparison_without_error_envelope_rejects_statically [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_float_row_statistic_comparison_retains_error_state [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_float_sum_envelope_and_native_mean_cell_boundary [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship[holiday-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship[holiday_then_weekday-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship[none-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship[ordinal-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship[weekday-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_ordinary_ratio_and_cohort_singleton[True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_ordinary_ratio_over_untimed_numeric_read [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_period_change_after_bucket_rollup[True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_period_change_retains_original_buckets_and_rejects_renumbering[True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_public_decimal_ratio_rounds_once_half_even [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_public_numeric_difference_source_and_fixed[BIGINT-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_public_numeric_difference_source_and_fixed[DECIMAL(30,6)-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_public_numeric_difference_source_and_fixed[DOUBLE-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_public_numeric_difference_source_and_fixed[duration-True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_stable_float_row_statistic_comparison_propagates_bounds [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_union_keep_distinguishes_missing_coordinate_and_rejects_filtered_empty[True] [failed]
tests/analysis/numeric/test_analysis_comparison_runtime.py::test_union_keeps_present_nondefined_cell_separate_from_absence [failed]
```

Observed first blockers:

- ValueError: runtime metric catalog dependency 'sales.revenue' is not loaded
- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.measure_column received retired declaration parameters.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_coordinates.py`

Primary owner: **R6**; 22 case(s).

```text
tests/analysis/numeric/test_analysis_coordinates.py::test_combined_member_classifications_do_not_expand_facts [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_complete_coordinate_tuple_rollup [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_duplicate_classification_rejects_source_and_fixed [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_empty_numeric_row_state [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_explicit_classification_rollup_and_current_rows [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_fixed_grouped_statistic_preserves_cell_binding_and_empty_schema [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_full_tuple_union_retains_denominator_only_and_cancelled_coordinates [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_invalid_classification_rejects [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_numeric_read_grouping_creates_only_row_statistics [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_original_mean_merges_support_not_finished_values [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_partial_reduction_matches_components_and_preserves_keys [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_required_row_state_damage_revokes_continuation [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_review_fixed_group_statistics_keep_axes_and_current_rows [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_review_foreign_fact_predicate_transports_source_bindings[False] [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_review_selected_category_retains_classification [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_row_methods_source_and_fixed [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_row_state_version_and_binding_mismatch_reject [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_row_statistics_grouped_merge_preserves_mean_state_and_identity [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_selected_entities_keep_complete_subject_mapping [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_selected_row_groups_preserve_count_mean_state [failed]
tests/analysis/numeric/test_analysis_coordinates.py::test_typed_integer_classification_and_singleton [setup error]
tests/analysis/numeric/test_analysis_coordinates.py::test_weighted_coordinates_merge_paired_components [failed]
```

Observed first blockers:

- ValueError: runtime metric catalog dependency 'sales.order_count' is not loaded
- failed on setup with "marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.
- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_cumulative_decimal.py`

Primary owner: **R7**; 4 case(s).

```text
tests/analysis/numeric/test_analysis_cumulative_decimal.py::test_decimal_all_history_cumulative_succeeds [failed]
tests/analysis/numeric/test_analysis_cumulative_decimal.py::test_decimal_grain_to_date_cumulative_boundaries [failed]
tests/analysis/numeric/test_analysis_cumulative_decimal.py::test_decimal_trailing_cumulative_succeeds [failed]
tests/analysis/numeric/test_analysis_cumulative_decimal.py::test_decimal_weighted_mean_cumulative_succeeds [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/analysis/numeric/test_analysis_decimal_e2e.py`

Primary owner: **R3**; 4 case(s).

```text
tests/analysis/numeric/test_analysis_decimal_e2e.py::test_decimal_measure_and_retained_schema_agree [failed]
tests/analysis/numeric/test_analysis_decimal_e2e.py::test_mixed_linear_retains_each_component[False] [failed]
tests/analysis/numeric/test_analysis_decimal_e2e.py::test_mixed_linear_retains_each_component[True] [failed]
tests/analysis/numeric/test_analysis_decimal_e2e.py::test_runtime_ratio_accepts_independent_numeric_components [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/analysis/numeric/test_analysis_display.py`

Primary owner: **R4**; 17 case(s).

```text
tests/analysis/numeric/test_analysis_display.py::test_business_display_identifies_current_and_baseline_windows [failed]
tests/analysis/numeric/test_analysis_display.py::test_business_display_reads_saved_rows_and_preserves_state_summary [failed]
tests/analysis/numeric/test_analysis_display.py::test_complete_composite_keys_scalar_columns_and_nulls[False] [failed]
tests/analysis/numeric/test_analysis_display.py::test_display_precision_matrix[BIGINT-True] [failed]
tests/analysis/numeric/test_analysis_display.py::test_display_precision_matrix[DECIMAL(30,6)-True] [failed]
tests/analysis/numeric/test_analysis_display.py::test_display_precision_matrix[DOUBLE-True] [failed]
tests/analysis/numeric/test_analysis_display.py::test_display_precision_matrix[duration_ns-True] [failed]
tests/analysis/numeric/test_analysis_display.py::test_empty_original_domain_and_numeric_view_continuations[False] [failed]
tests/analysis/numeric/test_analysis_display.py::test_empty_singleton_display_views [failed]
tests/analysis/numeric/test_analysis_display.py::test_group_and_entity_time_display_domains[False] [failed]
tests/analysis/numeric/test_analysis_display.py::test_public_rank_table_source_and_fixed[ascending-ordinal] [failed]
tests/analysis/numeric/test_analysis_display.py::test_public_rank_table_source_and_fixed[descending-dense] [failed]
tests/analysis/numeric/test_analysis_display.py::test_public_undefined_ranks_and_strict_two_step_filter[False] [failed]
tests/analysis/numeric/test_analysis_display.py::test_ranking_views_can_be_ranked_again [failed]
tests/analysis/numeric/test_analysis_display.py::test_shared_rank_fresh_source_fixed_and_no_fallback [failed]
tests/analysis/numeric/test_analysis_display.py::test_static_display_errors_are_zero_read [failed]
tests/analysis/numeric/test_analysis_display.py::test_top_k_original_rank_share_and_empty [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_members.py`

Primary owner: **R3**; 13 case(s).

```text
tests/analysis/numeric/test_analysis_members.py::test_cold_typed_read_continuation_without_source [failed]
tests/analysis/numeric/test_analysis_members.py::test_complete_subject_set_image_source_and_fixed [failed]
tests/analysis/numeric/test_analysis_members.py::test_composite_subject_predicates [failed]
tests/analysis/numeric/test_analysis_members.py::test_computed_boolean_dimension [failed]
tests/analysis/numeric/test_analysis_members.py::test_computed_measure_and_bound_dependency [failed]
tests/analysis/numeric/test_analysis_members.py::test_exact_boundary_null_and_corrupt_subject [failed]
tests/analysis/numeric/test_analysis_members.py::test_grid_attribute_point_is_independent_of_member_version [failed]
tests/analysis/numeric/test_analysis_members.py::test_grid_classification_uses_grid_point [failed]
tests/analysis/numeric/test_analysis_members.py::test_member_axis_retains_ordered_composite_identity [failed]
tests/analysis/numeric/test_analysis_members.py::test_observation_classification_reads_explicit_version [failed]
tests/analysis/numeric/test_analysis_members.py::test_read_path_coverage_null_and_consumed_scope [failed]
tests/analysis/numeric/test_analysis_members.py::test_temporal_literals_fixed_recovery_and_foreign_predicates [failed]
tests/analysis/numeric/test_analysis_members.py::test_wrong_role_and_many_mapping_reject_before_io [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_numeric.py`

Primary owner: **R3**; 21 case(s).

```text
tests/analysis/numeric/test_analysis_numeric.py::test_current_row_counts_keep_numeric_null_policy [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_direct_distribution[agg4-True-True] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_direct_distribution[agg5-False-False] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_direct_distribution[approx_count_distinct-True-True] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_direct_distribution[approx_median-True-False] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_direct_distribution[count_distinct-False-False] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_direct_distribution[median-False-True] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_duration_matrix[ns-True-kinds1] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_duration_matrix[us-False-kinds0] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_float_mean_and_decimal_original_state [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_linear_cancellation_checks_final_integer_and_float_bound[False] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_linear_cancellation_checks_final_integer_and_float_bound[True] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_native_quantile_precision_is_source_owned[False] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_native_quantile_precision_is_source_owned[True] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_numeric_boundary_facts[cancellation] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_numeric_boundary_facts[large_mean] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_numeric_composition_matrix [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_typed_temporal_matrix[decimal-kinds2] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_typed_temporal_matrix[duration-kinds3] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_typed_temporal_matrix[float64-kinds1] [failed]
tests/analysis/numeric/test_analysis_numeric.py::test_typed_temporal_matrix[int64-kinds0] [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.measure_column received retired declaration parameters.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_numeric_boundaries.py`

Primary owner: **R3**; 12 case(s).

```text
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_decimal_boundary_fixed_rollup_does_not_round_range_check[0-False-sum] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_decimal_boundary_fixed_rollup_does_not_round_range_check[38-False-max] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_decimal_boundary_fixed_rollup_does_not_round_range_check[38-True-sum] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_decimal_boundary_fixed_rollup_does_not_round_range_check[6-True-min] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_decimal_fold_retains_widened_sample_type[False-mean] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_decimal_fold_retains_widened_sample_type[True-mean] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_native_float_nonzero_denominator_survives_cancellation[False-ratio-False-False] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_native_float_nonzero_denominator_survives_cancellation[False-weighted-True-False] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_native_float_nonzero_denominator_survives_cancellation[True-ratio-True-True] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_native_float_nonzero_denominator_survives_cancellation[True-weighted-False-True] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_numeric_raw_order_and_exchange_batches[False] [failed]
tests/analysis/numeric/test_analysis_numeric_boundaries.py::test_numeric_raw_order_and_exchange_batches[True] [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.measure_column received retired declaration parameters.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/numeric/test_analysis_observation.py`

Primary owner: **R3**; 17 case(s).

```text
tests/analysis/numeric/test_analysis_observation.py::test_composite_slice_reaches_each_leaf_before_reduction [failed]
tests/analysis/numeric/test_analysis_observation.py::test_linear_executes_more_than_two_occurrences [failed]
tests/analysis/numeric/test_analysis_observation.py::test_linear_executes_three_independently_reduced_roots [failed]
tests/analysis/numeric/test_analysis_observation.py::test_linear_preserves_each_empty_branch_policy [failed]
tests/analysis/numeric/test_analysis_observation.py::test_linear_rejects_incommensurable_units [failed]
tests/analysis/numeric/test_analysis_observation.py::test_nested_linear_keeps_original_state_for_source_and_fixed_rollup [failed]
tests/analysis/numeric/test_analysis_observation.py::test_observation_preserves_full_member_identity [failed]
tests/analysis/numeric/test_analysis_observation.py::test_observation_rejects_wrong_or_extra_root_routes [failed]
tests/analysis/numeric/test_analysis_observation.py::test_observe_runtime_ratio_over_two_contribution_roots [failed]
tests/analysis/numeric/test_analysis_observation.py::test_observe_two_branches_of_one_root_over_a_single_route [failed]
tests/analysis/numeric/test_analysis_observation.py::test_observe_without_a_window_keeps_every_admitted_contribution [failed]
tests/analysis/numeric/test_analysis_observation.py::test_opaque_metric_rejects_instead_of_inventing_components [failed]
tests/analysis/numeric/test_analysis_observation.py::test_ratio_accepts_a_negative_sum_denominator [failed]
tests/analysis/numeric/test_analysis_observation.py::test_runtime_aggregate_resolves_the_declared_default_axis [failed]
tests/analysis/numeric/test_analysis_observation.py::test_sliced_weighted_mean_filters_pairs_before_reduction [failed]
tests/analysis/numeric/test_analysis_observation.py::test_weighted_mean_preserves_pairs_and_fixed_rollup [failed]
tests/analysis/numeric/test_analysis_observation.py::test_windowless_observation_rejects_a_cumulative_metric [failed]
```

Observed first blockers:

- ValueError: runtime metric catalog dependency 'sales.line_revenue' is not loaded
- ValueError: runtime metric catalog dependency 'sales.order_count' is not loaded
- ValueError: runtime metric catalog dependency 'sales.revenue' is not loaded
- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.cumulative is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.
- marivo.semantic.errors.SemanticRuntimeError: [not_found] Ref 'metric:sales.opaque_revenue' is not present in this compiled catalog. Loaded metric candidates: ("ms.ref.metric('sales.aov_from_lines')", "ms.ref.metric('sales.line_revenue')", "ms.ref.metric('sales.order_count')", "ms.ref.metric('sales.revenue')").

### `tests/analysis/numeric/test_analysis_predicates.py`

Primary owner: **R4**; 6 case(s).

```text
tests/analysis/numeric/test_analysis_predicates.py::test_foreign_predicate_inputs_reject_before_execution [failed]
tests/analysis/numeric/test_analysis_predicates.py::test_numeric_string_category_persists_and_continues [failed]
tests/analysis/numeric/test_analysis_predicates.py::test_public_decimal_field_comparison [failed]
tests/analysis/numeric/test_analysis_predicates.py::test_public_duration_field_comparison [failed]
tests/analysis/numeric/test_analysis_predicates.py::test_public_multi_input_source_and_fixed [failed]
tests/analysis/numeric/test_analysis_predicates.py::test_tag_selection_precedes_numeric_consumption [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/numeric/test_analysis_references.py`

Primary owner: **R5**; 12 case(s).

```text
tests/analysis/numeric/test_analysis_references.py::test_composite_penetration_overlap_and_empty[False] [failed]
tests/analysis/numeric/test_analysis_references.py::test_fixed_selected_original_can_use_retained_full_reference [failed]
tests/analysis/numeric/test_analysis_references.py::test_invalid_public_standardization_rejects_without_normalization [failed]
tests/analysis/numeric/test_analysis_references.py::test_public_exact_integer_reference_weights[False] [failed]
tests/analysis/numeric/test_analysis_references.py::test_public_references_source_and_fixed[False] [failed]
tests/analysis/numeric/test_analysis_references.py::test_public_share_numeric_matrix[BIGINT-True] [failed]
tests/analysis/numeric/test_analysis_references.py::test_public_share_numeric_matrix[DECIMAL(30,6)-True] [failed]
tests/analysis/numeric/test_analysis_references.py::test_public_share_numeric_matrix[duration_ns-True] [failed]
tests/analysis/numeric/test_analysis_references.py::test_reference_preparation_captures_independent_source_origins [failed]
tests/analysis/numeric/test_analysis_references.py::test_shared_reference_source_reexecution_and_fixed_inputs [failed]
tests/analysis/numeric/test_analysis_references.py::test_static_reference_rejections_are_zero_read [failed]
tests/analysis/numeric/test_analysis_references.py::test_zero_and_signed_share_support [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/numeric/test_comparison_consumers.py`

Primary owner: **R5**; 3 case(s).

```text
tests/analysis/numeric/test_comparison_consumers.py::test_complete_keys_source_and_fixed[nested] [failed]
tests/analysis/numeric/test_comparison_consumers.py::test_complete_keys_source_and_fixed[ratio] [failed]
tests/analysis/numeric/test_comparison_consumers.py::test_complete_keys_source_and_fixed[union] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/numeric/test_comparison_non_defined.py`

Primary owner: **R5**; 4 case(s).

```text
tests/analysis/numeric/test_comparison_non_defined.py::test_both_non_defined_operands_take_the_higher_priority_tag[True] [failed]
tests/analysis/numeric/test_comparison_non_defined.py::test_nonfinite_operand_still_fails_the_whole_run [failed]
tests/analysis/numeric/test_comparison_non_defined.py::test_null_operand_propagates_in_time_comparison[True] [failed]
tests/analysis/numeric/test_comparison_non_defined.py::test_null_operands_propagate_in_period_comparison[True] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/numeric/test_comparison_result.py`

Primary owner: **R5**; 3 case(s).

```text
tests/analysis/numeric/test_comparison_result.py::test_comparison_owns_independent_cells_and_typed_views [failed]
tests/analysis/numeric/test_comparison_result.py::test_dual_exchange_rejects_incomplete_or_forged_views [failed]
tests/analysis/numeric/test_comparison_result.py::test_relative_decimal_overflow_prevents_absolute_publication [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/numeric/test_members_consumers.py`

Primary owner: **R3**; 4 case(s).

```text
tests/analysis/numeric/test_members_consumers.py::test_complete_versioned_members[duplicate] [failed]
tests/analysis/numeric/test_members_consumers.py::test_complete_versioned_members[null_tenant] [failed]
tests/analysis/numeric/test_members_consumers.py::test_complete_versioned_members[snapshot] [failed]
tests/analysis/numeric/test_members_consumers.py::test_scalar_path_scopes_complete_keys_and_trusts_cardinality [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.

### `tests/analysis/numeric/test_native_numeric.py`

Primary owner: **R6**; 19 case(s).

```text
tests/analysis/numeric/test_native_numeric.py::test_linear_promotes_before_signed_intermediate_arithmetic[decimal_negation] [failed]
tests/analysis/numeric/test_native_numeric.py::test_linear_promotes_before_signed_intermediate_arithmetic[duration] [failed]
tests/analysis/numeric/test_native_numeric.py::test_linear_promotes_before_signed_intermediate_arithmetic[float_negation] [failed]
tests/analysis/numeric/test_native_numeric.py::test_linear_promotes_before_signed_intermediate_arithmetic[float_prefix] [failed]
tests/analysis/numeric/test_native_numeric.py::test_linear_still_rejects_final_carrier_overflow[False] [failed]
tests/analysis/numeric/test_native_numeric.py::test_linear_still_rejects_final_carrier_overflow[True] [failed]
tests/analysis/numeric/test_native_numeric.py::test_mixed_component_attribution_source_and_fixed[ratio-BIGINT-DOUBLE-1.0] [failed]
tests/analysis/numeric/test_native_numeric.py::test_mixed_component_attribution_source_and_fixed[ratio-DECIMAL(12,2)-DOUBLE-1e-14] [failed]
tests/analysis/numeric/test_native_numeric.py::test_mixed_component_attribution_source_and_fixed[ratio-DOUBLE-BIGINT-1.0] [failed]
tests/analysis/numeric/test_native_numeric.py::test_mixed_component_attribution_source_and_fixed[weighted-DECIMAL(12,2)-BIGINT-1.0] [failed]
tests/analysis/numeric/test_native_numeric.py::test_mixed_component_attribution_source_and_fixed[weighted-DOUBLE-BIGINT-1.0] [failed]
tests/analysis/numeric/test_native_numeric.py::test_mixed_linear_coordinates [failed]
tests/analysis/numeric/test_native_numeric.py::test_narrow_decimal_mean_difference_preserves_fixed_attribution_carrier[DECIMAL(12,2)-2] [failed]
tests/analysis/numeric/test_native_numeric.py::test_narrow_decimal_mean_difference_preserves_fixed_attribution_carrier[DECIMAL(30,6)-6] [failed]
tests/analysis/numeric/test_native_numeric.py::test_native_linear_comparison_uses_represented_cells_in_both_routes [failed]
tests/analysis/numeric/test_native_numeric.py::test_native_weighted_and_mean[DECIMAL(12,2)-BIGINT] [failed]
tests/analysis/numeric/test_native_numeric.py::test_native_weighted_and_mean[DECIMAL(12,2)-DECIMAL(8,3)] [failed]
tests/analysis/numeric/test_native_numeric.py::test_native_weighted_and_mean[DOUBLE-BIGINT] [failed]
tests/analysis/numeric/test_native_numeric.py::test_native_weighted_and_mean[INTEGER-SMALLINT] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.weighted_mean is a retired declaration factory.

### `tests/analysis/numeric/test_period_alignment.py`

Primary owner: **R7**; 15 case(s).

```text
tests/analysis/numeric/test_period_alignment.py::test_aligned_views_reject_real_time_operations_after_selection [failed]
tests/analysis/numeric/test_period_alignment.py::test_equal_period_empty_finish_allows_missing_non_time_groups [failed]
tests/analysis/numeric/test_period_alignment.py::test_exact_period_retains_current_and_baseline_original_allocation_states [failed]
tests/analysis/numeric/test_period_alignment.py::test_frozen_time_tables_are_injective_and_match_each_original_grid [failed]
tests/analysis/numeric/test_period_alignment.py::test_grouped_grid_includes_empty_groups_and_propagates_both_cells[order_count] [failed]
tests/analysis/numeric/test_period_alignment.py::test_grouped_grid_includes_empty_groups_and_propagates_both_cells[revenue] [failed]
tests/analysis/numeric/test_period_alignment.py::test_grouped_grid_includes_empty_groups_and_propagates_both_cells[revenue_zero] [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_exact_and_empty_finish_reject_time_missing_at_construction [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_exchange_checks_original_projections_presence_and_both_views [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_outer_keeps_time_missing_and_original_keys[False-lengths0] [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_outer_keeps_time_missing_and_original_keys[False-lengths1] [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_outer_keeps_time_missing_and_original_keys[True-lengths0] [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_outer_keeps_time_missing_and_original_keys[True-lengths1] [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_sql_lowering_matches_dual_local_finish[lengths0] [failed]
tests/analysis/numeric/test_period_alignment.py::test_period_sql_lowering_matches_dual_local_finish[lengths1] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/numeric/test_period_native_carriers.py`

Primary owner: **R7**; 2 case(s).

```text
tests/analysis/numeric/test_period_native_carriers.py::test_period_alignment_native_carriers[holiday_then_weekday-duckdb] [failed]
tests/analysis/numeric/test_period_native_carriers.py::test_period_alignment_native_carriers[ordinal-duckdb] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.empty is a retired declaration factory.

### `tests/analysis/numeric/test_public_quantile_input.py`

Primary owner: **R3**; 2 case(s).

```text
tests/analysis/numeric/test_public_quantile_input.py::test_defined_quantile_survives_source_offline_recovery[approx_median-T-Digest] [failed]
tests/analysis/numeric/test_public_quantile_input.py::test_defined_quantile_survives_source_offline_recovery[median-QUANTILE_CONT] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/analysis/numeric/test_time_alignment.py`

Primary owner: **R7**; 16 case(s).

```text
tests/analysis/numeric/test_time_alignment.py::test_all_time_keys_source_fixed_and_native_sql[holiday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_all_time_keys_source_fixed_and_native_sql[holiday_then_weekday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_all_time_keys_source_fixed_and_native_sql[ordinal] [failed]
tests/analysis/numeric/test_time_alignment.py::test_all_time_keys_source_fixed_and_native_sql[weekday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_equal_key_images_keep_dual_cells_for_exact_and_empty_finish[order_count-holiday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_equal_key_images_keep_dual_cells_for_exact_and_empty_finish[order_count-ordinal] [failed]
tests/analysis/numeric/test_time_alignment.py::test_equal_key_images_keep_dual_cells_for_exact_and_empty_finish[revenue-holiday_then_weekday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_equal_key_images_keep_dual_cells_for_exact_and_empty_finish[revenue-weekday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_exact_holiday_absolute_keeps_original_attribution[holiday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_exact_holiday_absolute_keeps_original_attribution[holiday_then_weekday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_hybrid_snapshot_rejects_changed_frozen_authority[snapshot_digest] [failed]
tests/analysis/numeric/test_time_alignment.py::test_hybrid_snapshot_rejects_changed_frozen_authority[string_key] [failed]
tests/analysis/numeric/test_time_alignment.py::test_occurrence_identity_and_snapshot_enter_definition_and_integrity [failed]
tests/analysis/numeric/test_time_alignment.py::test_timestamp_holiday_definition_survives_wire_round_trip[holiday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_timestamp_holiday_definition_survives_wire_round_trip[holiday_then_weekday] [failed]
tests/analysis/numeric/test_time_alignment.py::test_weekday_exact_uses_key_images_instead_of_equal_lengths [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/statistics/test_analysis_deviation.py`

Primary owner: **R4**; 13 case(s).

```text
tests/analysis/statistics/test_analysis_deviation.py::test_construction_mode_identity_and_zero_business_reads [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_deviation_phase_failure_is_atomic[before_commit-zscore] [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_deviation_phase_failure_is_atomic[fit_complete-zscore] [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_deviation_phase_failure_is_atomic[graph_admitted-zscore] [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_deviation_phase_failure_is_atomic[graph_receipts_verified-zscore] [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_extreme_binary64_fit_card_preserves_bounded_continuations [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_f11_complete_source_chain[mad-DOUBLE] [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_f11_complete_source_chain[zscore-DECIMAL(38,38)] [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_null_partitions_selection_boundary_and_shared_fit [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_observed_nullable_large_int64_dataframe_preserves_captured_values [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_public_source_fixed_views_and_selection [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_selected_time_score_can_establish_a_new_fixed_fit[False] [failed]
tests/analysis/statistics/test_analysis_deviation.py::test_selected_time_score_can_establish_a_new_fixed_fit[True] [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/statistics/test_analysis_deviation_display.py`

Primary owner: **R4**; 7 case(s).

```text
tests/analysis/statistics/test_analysis_deviation_display.py::test_decimal_grid_tables_retain_current_keys_and_full_fit[zscore] [failed]
tests/analysis/statistics/test_analysis_deviation_display.py::test_local_display_pairing_rejects_different_current_keys [failed]
tests/analysis/statistics/test_analysis_deviation_display.py::test_mixed_table_preserves_each_current_value_and_original_fit[mad-ranks-False] [failed]
tests/analysis/statistics/test_analysis_deviation_display.py::test_mixed_table_preserves_each_current_value_and_original_fit[zscore-category-False] [failed]
tests/analysis/statistics/test_analysis_deviation_display.py::test_mixed_table_preserves_each_current_value_and_original_fit[zscore-other_fit-True] [failed]
tests/analysis/statistics/test_analysis_deviation_display.py::test_rank_projection_disclosure_and_fixed_table_keep_fit_scope[zscore] [failed]
tests/analysis/statistics/test_analysis_deviation_display.py::test_table_fit_receipt_is_required_for_recovery_and_exact_hit [failed]
```

Observed first blockers:

- AssertionError: Regex pattern did not match.
- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/statistics/test_analysis_deviation_faults.py`

Primary owner: **R4**; 5 case(s).

```text
tests/analysis/statistics/test_analysis_deviation_faults.py::test_decimal_output_overflow_is_rejection_not_positive_qualification [failed]
tests/analysis/statistics/test_analysis_deviation_faults.py::test_fixed_reader_failure_cannot_admit_or_publish_scoring [failed]
tests/analysis/statistics/test_analysis_deviation_faults.py::test_fixed_selected_subject_requires_retained_observation_contract [failed]
tests/analysis/statistics/test_analysis_deviation_faults.py::test_local_failure_releases_prepared_sources_and_preserves_prior_artifact [failed]
tests/analysis/statistics/test_analysis_deviation_faults.py::test_lost_commit_ack_returns_verified_fit_without_source_replay [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.

### `tests/analysis/statistics/test_analysis_faults.py`

Primary owner: **R4**; 4 case(s).

```text
tests/analysis/statistics/test_analysis_faults.py::test_every_receipt_part_read_recovery_and_hit [failed]
tests/analysis/statistics/test_analysis_faults.py::test_resources_atomic_publication_and_native_failure [failed]
tests/analysis/statistics/test_analysis_faults.py::test_run_recovery_rejects_frozen_damage_and_owned_field_changes [failed]
tests/analysis/statistics/test_analysis_faults.py::test_source_parts_versions_keys_scope_and_finding_authority [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/statistics/test_analysis_runs.py`

Primary owner: **R4**; 4 case(s).

```text
tests/analysis/statistics/test_analysis_runs.py::test_new_grid_after_subject_selection[False] [failed]
tests/analysis/statistics/test_analysis_runs.py::test_new_grid_after_subject_selection[True] [failed]
tests/analysis/statistics/test_analysis_runs.py::test_public_runs [failed]
tests/analysis/statistics/test_analysis_runs.py::test_ratio_without_retained_coverage_rejects_with_typed_error [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/statistics/test_analysis_statistics.py`

Primary owner: **R4**; 9 case(s).

```text
tests/analysis/statistics/test_analysis_statistics.py::test_all_requested_pairs_and_static_ceiling [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_forecast_prediction_is_a_complete_numeric_receiver [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_lag_counts_ties_and_approved_future[False] [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_lag_counts_ties_and_approved_future[True] [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_public_source_association_and_spearman_fixed_continuation [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_public_source_fixed_forecast [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_statistical_failure_is_atomic[before_commit] [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_statistical_failure_is_atomic[insert_evidence] [failed]
tests/analysis/statistics/test_analysis_statistics.py::test_statistical_failure_is_atomic[insert_findings] [failed]
```

Observed first blockers:

- ValueError: runtime metric catalog dependency 'sales.order_count' is not loaded
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/statistics/test_analysis_statistics_boundaries.py`

Primary owner: **R4**; 6 case(s).

```text
tests/analysis/statistics/test_analysis_statistics_boundaries.py::test_ratio_statistics_require_original_coverage[association] [failed]
tests/analysis/statistics/test_analysis_statistics_boundaries.py::test_ratio_statistics_require_original_coverage[forecast] [failed]
tests/analysis/statistics/test_analysis_statistics_boundaries.py::test_statistical_rank_projection_and_tables[False-association] [failed]
tests/analysis/statistics/test_analysis_statistics_boundaries.py::test_statistical_rank_projection_and_tables[False-forecast] [failed]
tests/analysis/statistics/test_analysis_statistics_boundaries.py::test_statistical_rank_projection_and_tables[True-association] [failed]
tests/analysis/statistics/test_analysis_statistics_boundaries.py::test_statistical_rank_projection_and_tables[True-forecast] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/analysis/statistics/test_analysis_statistics_faults.py`

Primary owner: **R4**; 1 case(s).

```text
tests/analysis/statistics/test_analysis_statistics_faults.py::test_three_quantity_search_and_forecast_views [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/statistics/test_analysis_views.py`

Primary owner: **R4**; 4 case(s).

```text
tests/analysis/statistics/test_analysis_views.py::test_frozen_views_scope_sharing_quantity_and_k[association.pearson@v1] [failed]
tests/analysis/statistics/test_analysis_views.py::test_frozen_views_scope_sharing_quantity_and_k[deviation.zscore@v1] [failed]
tests/analysis/statistics/test_analysis_views.py::test_frozen_views_scope_sharing_quantity_and_k[forecast.naive@v1] [failed]
tests/analysis/statistics/test_analysis_views.py::test_frozen_views_scope_sharing_quantity_and_k[time.runs@v1] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/statistics/test_composition.py`

Primary owner: **R4**; 7 case(s).

```text
tests/analysis/statistics/test_composition.py::test_direct_score_runs_and_next_business_round[parquet] [failed]
tests/analysis/statistics/test_composition.py::test_score_select_members_followup_one_dag[zscore-parquet] [failed]
tests/analysis/statistics/test_composition.py::test_shared_method_and_transport_are_distinct_from_new_kernels[association.pearson@v1] [failed]
tests/analysis/statistics/test_composition.py::test_shared_method_and_transport_are_distinct_from_new_kernels[deviation.zscore@v1] [failed]
tests/analysis/statistics/test_composition.py::test_shared_method_and_transport_are_distinct_from_new_kernels[forecast.naive@v1] [failed]
tests/analysis/statistics/test_composition.py::test_shared_method_and_transport_are_distinct_from_new_kernels[time.runs@v1] [failed]
tests/analysis/statistics/test_composition.py::test_two_separate_axes_and_joint_keep_the_original_target [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_expression_stage] Physical declaration capture is already finalized.

### `tests/analysis/statistics/test_contract_boundaries.py`

Primary owner: **R4**; 1 case(s).

```text
tests/analysis/statistics/test_contract_boundaries.py::test_artifact_rejects_unknown_evidence_without_source_replay [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.

### `tests/analysis/temporal/test_analysis_temporal.py`

Primary owner: **R7**; 16 case(s).

```text
tests/analysis/temporal/test_analysis_temporal.py::test_cumulative_anchors_ignore_display_start [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_cumulative_ratio_binds_every_component_to_the_endpoint [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_empty_fold_keeps_typed_state_for_fixed_continuation [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_fixed_date_windows_keep_authority_on_a_foreign_zone_grid [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_fold_reaggregates_aligned_samples_before_time [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_fold_rejects_float64_overflow_before_spatial_rollup [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_grid_required_state_damage_revokes_continuation [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_identity_observation_has_no_inferred_relationship [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_member_time_product_executes_on_unified_graph [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_native_time_conversion_does_not_submit_rule_audit [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_public_grid_observation_and_retained_axis [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_public_whole_cell_grain_coarsening [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_selected_groups_keep_fold_state_in_source_and_fixed_execution [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_source_wall_clock_gap_and_fold_follow_admitted_conversion[2026-03-08 02:30:00] [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_source_wall_clock_gap_and_fold_follow_admitted_conversion[2026-11-01 01:30:00] [failed]
tests/analysis/temporal/test_analysis_temporal.py::test_time_product_observation_keeps_empty_cells_and_window_binding [failed]
```

Observed first blockers:

- marivo.analysis.errors.AnalysisStageUnavailableError: AnalysisStageUnavailableError: This Query/Artifact stage is not available in the current build.
- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.cumulative is a retired declaration factory.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.measure_column received retired declaration parameters.

### `tests/analysis/temporal/test_encoded_time_runtime.py`

Primary owner: **R7**; 8 case(s).

```text
tests/analysis/temporal/test_encoded_time_runtime.py::test_cumulative_grid_and_endpoint_match_original_temporal_filter [failed]
tests/analysis/temporal/test_encoded_time_runtime.py::test_encoded_and_parsed_time_failures_preserve_error_fields_and_run_ownership [failed]
tests/analysis/temporal/test_encoded_time_runtime.py::test_event_column_on_relationship_destination_keeps_its_own_range [failed]
tests/analysis/temporal/test_encoded_time_runtime.py::test_grid_window_has_scan_envelope_and_preserves_cells[False] [failed]
tests/analysis/temporal/test_encoded_time_runtime.py::test_grid_window_has_scan_envelope_and_preserves_cells[True] [failed]
tests/analysis/temporal/test_encoded_time_runtime.py::test_observation_preserves_population_windows_and_query_count[False] [failed]
tests/analysis/temporal/test_encoded_time_runtime.py::test_observation_preserves_population_windows_and_query_count[True] [failed]
tests/analysis/temporal/test_encoded_time_runtime.py::test_outside_window_relationship_mismatch_preserves_observation_scope [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.cumulative is a retired declaration factory.

### `tests/analysis/temporal/test_temporal_public_runtime.py`

Primary owner: **R7**; 3 case(s).

```text
tests/analysis/temporal/test_temporal_public_runtime.py::test_report_day_buckets_preserve_declared_read_time_authority[declared_utc] [failed]
tests/analysis/temporal/test_temporal_public_runtime.py::test_report_day_buckets_preserve_declared_read_time_authority[native_naive] [failed]
tests/analysis/temporal/test_temporal_public_runtime.py::test_report_day_buckets_preserve_declared_read_time_authority[strptime] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/analysis/temporal/test_timezone_observation.py`

Primary owner: **R7**; 11 case(s).

```text
tests/analysis/temporal/test_timezone_observation.py::test_changed_engine_authority_rejects_before_business_read [failed]
tests/analysis/temporal/test_timezone_observation.py::test_dst_mean_grid_and_cumulative_preserve_exact_windows[2026-03-08-2026-03-09-2026-03-08 06:30:00-2026-03-08 07:30:00-2026-03-08 04:59:59.999999-2026-03-09 04:00:00-23] [failed]
tests/analysis/temporal/test_timezone_observation.py::test_dst_mean_grid_and_cumulative_preserve_exact_windows[2026-11-01-2026-11-02-2026-11-01 05:30:00-2026-11-01 06:30:00-2026-11-01 03:59:59.999999-2026-11-02 05:00:00-25] [failed]
tests/analysis/temporal/test_timezone_observation.py::test_exact_mean_qualification_missing_still_rejects_before_run [failed]
tests/analysis/temporal/test_timezone_observation.py::test_explicit_instant_bounds_do_not_erase_report_identity [failed]
tests/analysis/temporal/test_timezone_observation.py::test_nanosecond_mean_or_conversion_rejects_before_business_read[Asia/Shanghai] [failed]
tests/analysis/temporal/test_timezone_observation.py::test_nanosecond_mean_or_conversion_rejects_before_business_read[UTC] [failed]
tests/analysis/temporal/test_timezone_observation.py::test_report_mean_members_and_next_window_have_independent_oracles [failed]
tests/analysis/temporal/test_timezone_observation.py::test_source_kind_and_authority_remain_independent_of_report[aware-None-20-physical] [failed]
tests/analysis/temporal/test_timezone_observation.py::test_source_kind_and_authority_remain_independent_of_report[date-None-465-civil_date] [failed]
tests/analysis/temporal/test_timezone_observation.py::test_source_kind_and_authority_remain_independent_of_report[naive-Asia/Shanghai-465-declared] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/semantic/test_semantic_cumulative.py`

Primary owner: **R7**; 7 case(s).

```text
tests/semantic/test_semantic_cumulative.py::test_cumulative_load_resolves_single_time_dimension_and_non_additive [failed]
tests/semantic/test_semantic_cumulative.py::test_cumulative_omitted_over_rejects_multiple_time_dimensions [failed]
tests/semantic/test_semantic_cumulative.py::test_cumulative_rejects_derived_base [failed]
tests/semantic/test_semantic_cumulative.py::test_cumulative_rejects_non_root_over_axis [failed]
tests/semantic/test_semantic_cumulative.py::test_cumulative_rejects_tier2_body_base [failed]
tests/semantic/test_semantic_cumulative.py::test_cumulative_rejects_unknown_over_ref [failed]
tests/semantic/test_semantic_cumulative.py::test_cumulative_rejects_unsupported_base_aggregation [failed]
```

Observed first blockers:

- assert 'cumulative base' in "[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvg...e. Rename the zero-argument declaration function; names cannot be overridden.\nHelp: marivo.help('semantic.authoring')"
- assert 'cumulative sum over cumulative count' in "[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvg...e. Rename the zero-argument declaration function; names cannot be overridden.\nHelp: marivo.help('semantic.authoring')"
- assert 'over=' in "[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvg...e. Rename the zero-argument declaration function; names cannot be overridden.\nHelp: marivo.help('semantic.authoring')"
- assert 'sales.orders.nonexistent' in "[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvg...e. Rename the zero-argument declaration function; names cannot be overridden.\nHelp: marivo.help('semantic.authoring')"
- assert 'ship_time' in "[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvg...e. Rename the zero-argument declaration function; names cannot be overridden.\nHelp: marivo.help('semantic.authoring')"
- assert 'tier-2' in "[invalid_component_body] ms.metric received retired declaration parameters.\n  at: /private/var/folders/cx/bmfg_x3j0q...permission on source Measures; Metric declarations inherit their graph facts.\nHelp: marivo.help('semantic.authoring')"
- marivo.semantic.errors.SemanticLoadFailed: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/semantic/test_semantic_cumulative_v2_authoring.py`

Primary owner: **R7**; 6 case(s).

```text
tests/semantic/test_semantic_cumulative_v2_authoring.py::test_cumulative_accepts_grain_to_date_anchor [failed]
tests/semantic/test_semantic_cumulative_v2_authoring.py::test_cumulative_accepts_trailing_anchor [failed]
tests/semantic/test_semantic_cumulative_v2_authoring.py::test_cumulative_anchor_defaults_to_all_history [failed]
tests/semantic/test_semantic_cumulative_v2_authoring.py::test_loader_resolves_grain_to_date_anchor_over_from_single_time_entity [failed]
tests/semantic/test_semantic_cumulative_v2_authoring.py::test_loader_smoke_accepts_week_grain_under_month_reset_at_load_time [failed]
tests/semantic/test_semantic_cumulative_v2_authoring.py::test_validator_rejects_cumulative_over_derived_base [failed]
```

Observed first blockers:

- AssertionError: expected load to succeed for anchor=None; errors=(SemanticExpressionError("[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/tmprkpjiw0m/models/semantic/test/models.py:10\n  expected: receiver expressions and zero-argument @ms.measure()/@ms.metric() declarations\n  received: ms.aggregate(name='revenue', measure=Ref[measure](measure:test.orders.amount), agg='sum')\n  hint: Return constructed row/
- AssertionError: loader must not enforce grain-compat; errors=(SemanticExpressionError("[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/tmpxtki71_o/models/semantic/test/models.py:10\n  expected: receiver expressions and zero-argument @ms.measure()/@ms.metric() declarations\n  received: ms.aggregate(name='revenue', measure=Ref[measure](measure:test.orders.amount), agg='sum')\n  hint: Return constructed row/Metr
- assert 'cumulative base' in "[invalid_component_body] ms.aggregate is a retired declaration factory.\n  at: /private/var/folders/cx/bmfg_x3j0qsfvg...e. Rename the zero-argument declaration function; names cannot be overridden.\nHelp: marivo.help('semantic.authoring')"
- marivo.semantic.errors.SemanticExpressionError: [invalid_component_body] ms.aggregate is a retired declaration factory.

### `tests/semantic/test_semantic_preview_timeout.py`

Primary owner: **Remote qualification**; 4 case(s).

```text
tests/semantic/test_semantic_preview_timeout.py::test_batch_groups_use_their_own_deadlines_and_preserve_order [failed]
tests/semantic/test_semantic_preview_timeout.py::test_preview_prepares_deadline_before_source_resolution[metrics-mysql] [failed]
tests/semantic/test_semantic_preview_timeout.py::test_preview_prepares_deadline_before_source_resolution[metrics-postgres] [failed]
tests/semantic/test_semantic_preview_timeout.py::test_preview_prepares_deadline_before_source_resolution[metrics-trino] [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticRuntimeError: [materialize_failed] Batch preview relationship group failed: [invalid_expression_value] The numeric operation cannot produce an admitted nullable value.

### `tests/surface/test_period_alignment_guidance.py`

Primary owner: **R7**; 1 case(s).

```text
tests/surface/test_period_alignment_guidance.py::test_aligned_contract_and_selected_rows_teach_original_endpoint_repair [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.

### `tests/surface/test_selection_guidance.py`

Primary owner: **R4**; 1 case(s).

```text
tests/surface/test_selection_guidance.py::test_selection_discloses_members_only_with_retained_subject_map [failed]
```

Observed first blockers:

- marivo.semantic.errors.SemanticLoadError: [invalid_target_metric] The Metric cannot supply a private lazy computation contract.
