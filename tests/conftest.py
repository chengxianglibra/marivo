"""Shared fixtures for the Python-native Marivo test suite."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import ibis
import pytest

from tests.packaging.installer_helpers import InstallerEnv, InstallerToolchain
from tests.packaging.wheel_support import InstalledWheel, prepare_wheel
from tests.shared_fixtures import (
    DSL_NAMES,
    FUNNEL_BASE_EVENTS,
    FUNNEL_BASE_ORDERS,
    DslCase,
    DslCaseFactory,
    DslNames,
    DslScenario,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    authoring_evidence_template,
    lifecycle_project_files,
    sales_orders_template,
    seed_analysis_dsl_database,
    seed_lifecycle_backend,
)
from tests.support.source_trace import SourceTrace

_FAST_ANALYSIS_TEST_PREFIXES = (
    "tests/analysis/numeric/test_analysis_attribution_runtime.py::test_additive_source_and_fixed",
    "tests/analysis/numeric/test_scalar_types.py::",
    "tests/analysis/numeric/test_analysis_numeric_registration.py::",
    "tests/analysis/numeric/test_analysis_runtime_metric.py::",
    "tests/analysis/numeric/test_analysis_comparison.py::",
    "tests/analysis/numeric/test_finding_types.py::",
    "tests/analysis/numeric/test_coordinate_layouts.py::",
    "tests/analysis/numeric/test_observation_source_bindings.py::",
    "tests/analysis/numeric/test_analysis_attribution.py::",
    "tests/analysis/numeric/test_dataset_row_contract.py::",
    "tests/analysis/numeric/test_analysis_references.py::test_exact_standardization_and_zero_weight_cells",
    "tests/analysis/numeric/test_analysis_references.py::test_standardization_proof_uses_keys_and_preserves_damage",
    "tests/analysis/numeric/test_analysis_references.py::test_order_independent_represented_weight_sum",
    "tests/analysis/numeric/test_analysis_references.py::test_invalid_weights_reject",
    "tests/analysis/numeric/test_analysis_references.py::test_zero_weight_does_not_hide_malformed_defined",
    "tests/analysis/numeric/test_analysis_references.py::test_tiny_positive_weights_and_single_order_independent_finish",
    "tests/analysis/numeric/test_analysis_references.py::test_zero_weight_error_propagation",
    "tests/analysis/numeric/test_analysis_references.py::test_penetration_proof_uses_complete_keys",
    "tests/analysis/numeric/test_analysis_display.py::test_exact_ties_and_nondefined_tail",
    "tests/analysis/numeric/test_analysis_display.py::test_closed_display_exchange_preserves_four_cells",
    "tests/analysis/numeric/test_analysis_display.py::test_represented_float_order_has_no_epsilon_ties",
    "tests/analysis/numeric/test_analysis_numeric.py::test_native_integer_ratio_matches_independent_fraction",
    "tests/analysis/numeric/test_analysis_numeric.py::test_native_decimal_ratio_one_half_even_round",
    "tests/analysis/numeric/test_analysis_numeric.py::test_fixed_numeric_merge_order_and_partition_oracle",
    "tests/analysis/numeric/test_analysis_numeric.py::test_definition_quantile_rejects_invalid_q",
    "tests/analysis/numeric/test_analysis_numeric.py::test_exact_approximate_replay_identities_never_alias",
    "tests/analysis/numeric/test_analysis_numeric.py::test_float_linear_validator_uses_owner_error_bound",
    "tests/analysis/numeric/test_analysis_numeric.py::test_native_duration_tick_rounding_ties_and_sign",
    "tests/analysis/numeric/test_analysis_observation.py::test_relationship_path_rejects_empty_or_non_relationship_hops",
    "tests/analysis/numeric/test_analysis_observation.py::test_relationship_path_preserves_hop_order_and_bounded_display",
    "tests/analysis/numeric/test_analysis_observation.py::test_weighted_state_rejects_corrupted_components",
    "tests/analysis/numeric/test_analysis_predicates.py::test_predicate_literal_wire_preserves_exact_type",
    "tests/analysis/numeric/test_analysis_predicates.py::test_malformed_decimal_literal_wire_rejects",
    "tests/analysis/numeric/test_analysis_predicates.py::test_strict_leaf_and_three_value_composition",
    "tests/analysis/numeric/test_analysis_predicates.py::test_precise_predicate_literals_and_duration_fields",
    "tests/analysis/numeric/test_analysis_predicates.py::test_closed_policy_and_predicate_inputs",
    "tests/analysis/numeric/test_analysis_cohort.py::test_quantifier_decisions",
    "tests/analysis/numeric/test_analysis_cohort.py::test_explicit_empty_policy",
    "tests/analysis/graph/test_analysis_dsl_public.py::test_public_cold_process_continues_from_retained_parts",
    "tests/analysis/session/test_session_domains.py::",
    "tests/analysis/graph/test_analysis_methods.py::",
    "tests/analysis/graph/test_analysis_snapshot_dag.py::",
    "tests/analysis/graph/test_analysis_core.py::",
    "tests/analysis/graph/test_analysis_graph.py::",
    "tests/analysis/graph/test_analysis_lowering.py::",
    "tests/analysis/graph/test_analysis_graph_preflight.py::",
    "tests/analysis/graph/test_semantic_normalization.py::",
    "tests/analysis/graph/test_exchange_validation.py::",
    "tests/analysis/graph/test_cell_encoding.py::",
    "tests/analysis/graph/test_file_admission.py::",
    "tests/analysis/graph/test_physical_cost_fixtures.py::",
    "tests/analysis/graph/test_disclosure.py::",
    "tests/analysis/graph/test_computation_runtime.py::",
    "tests/analysis/graph/test_method_consumers.py::",
    "tests/analysis/graph/test_premise_verification.py::",
    "tests/analysis/graph/test_relationship_binding.py::test_inference_and_root_identity",
    "tests/analysis/graph/test_relationship_binding.py::test_coordinate_tampering_and_frozen_roles",
    "tests/analysis/graph/test_relationship_binding.py::test_classification_scope_and_complete_components",
    "tests/analysis/graph/test_relationship_binding.py::test_classification_read_requires_owner_for_its_complete_input_scope",
    "tests/analysis/graph/test_relationship_binding.py::test_timed_classification_cannot_remove_consumed_contributions",
    "tests/analysis/graph/test_analysis_dsl_public.py::test_public_singleton_same_entity_observation_allows_omitted_route",
    "tests/analysis/graph/test_analysis_dsl_public.py::test_public_mixed_fixed_and_live_rejects_before_run",
    "tests/analysis/graph/test_analysis_dsl_public.py::test_public_comparison_rejects_different_members_and_metric_units",
    "tests/analysis/graph/test_analysis_dsl_public.py::test_public_snapshot_mismatch_rejects_exact_artifact",
    "tests/analysis/graph/test_analysis_dsl_public.py::test_public_missing_key_and_wrong_relationship_reject",
    "tests/analysis/graph/test_analysis_dsl_public.py::test_public_missing_retained_part_blocks_recovery",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-distinct-int64]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-identity-int64]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_distinct-decimal]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-distinct-timestamp]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_distinct-string]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_distinct-int64]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-quantile-int64]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[duckdb-approx_quantile-int64]",
    "tests/analysis/graph/test_distribution_consumers.py::test_native_distribution[sqlite-approx_quantile-int64]",
    "tests/analysis/graph/test_multiroot_consumers.py::test_sqlite_float_does_not_grant_decimal",
    "tests/analysis/graph/test_multiroot_consumers.py::test_wide_ratio_decimal_does_not_grant_other_scales[postgres]",
    "tests/analysis/graph/test_multiroot_consumers.py::test_wide_ratio_decimal_does_not_grant_other_scales[mysql]",
    "tests/analysis/graph/test_multiroot_consumers.py::test_wide_ratio_decimal_does_not_grant_other_scales[clickhouse]",
    "tests/analysis/graph/test_dataset_descriptors.py::",
    "tests/analysis/graph/test_fixed_reduction.py::",
    "tests/analysis/graph/test_fixed_selection.py::",
    "tests/analysis/graph/test_public_composition.py::",
    "tests/analysis/graph/test_count_rollup_backends.py::",
    "tests/analysis/graph/test_consumer_rules.py::",
    "tests/analysis/graph/test_identity_observation_lowering.py::",
    "tests/analysis/graph/test_terminal_consumers.py::",
    "tests/analysis/graph/test_capability_consumers.py::",
    "tests/analysis/graph/test_observation_grain.py::",
    "tests/analysis/materialization/test_analysis_graph_publication.py::",
    "tests/analysis/numeric/test_analysis_numeric_boundaries.py::",
    "tests/analysis/journey/test_anchor_consumers.py::",
    "tests/analysis/journey/test_journey_consumers.py::",
    "tests/analysis/journey/test_analysis_journey_matching.py::",
    "tests/analysis/journey/test_analysis_anchors.py::test_named_duration",
    "tests/analysis/journey/test_analysis_anchors.py::test_duration_rejects_inexact_or_overflow",
    "tests/analysis/journey/test_analysis_anchors.py::test_calendar_wall_time_differs_from_elapsed_across_dst",
    "tests/analysis/journey/test_analysis_anchors.py::test_calendar_gap_and_fold_reject_with_repair",
    "tests/analysis/journey/test_analysis_anchors.py::test_positive_window_and_exact_deadline",
    "tests/analysis/journey/test_analysis_domain_preparation.py::",
    "tests/analysis/journey/test_analysis_retention.py::test_closed_rule_repr",
    "tests/analysis/journey/test_analysis_retention.py::test_empty_calendar_omega_is_undefined",
    "tests/analysis/journey/test_analysis_retention.py::test_raw_partition_and_shared_return",
    "tests/analysis/journey/test_analysis_retention.py::test_known_true_subject_image_then_source_preparation",
    "tests/analysis/journey/test_analysis_retention.py::test_same_event_does_not_count_as_a_later_return",
    "tests/analysis/journey/test_analysis_retention.py::test_same_time_and_exclusive_deadline_returns_share_one_source",
    "tests/analysis/journey/test_analysis_anchors.py::test_elapsed_unit_keeps_exact_window_through_recovery",
    "tests/analysis/journey/test_analysis_anchors.py::test_native_duckdb_anchor_preserves_opportunities",
    "tests/analysis/lifecycle/test_analysis_history.py::test_native_duckdb_history_preserves_original_trace",
    "tests/analysis/lifecycle/test_analysis_lifecycle.py::test_reevaluation_source_first_sharing_and_fixed_preservation",
    "tests/analysis/lifecycle/test_analysis_lifecycle.py::test_frozen_public_signature_and_disclosure",
    "tests/analysis/journey/test_analysis_funnel.py::test_independent_counts_unknown_and_empty",
    "tests/analysis/journey/test_analysis_funnel.py::test_fraction_oracle_hierarchy_common_topk_and_typed_other",
    "tests/analysis/journey/test_analysis_funnel.py::test_entry_mappings_require_complete_occurrence_identity",
    "tests/analysis/lifecycle/test_analysis_history.py::test_fraction_quantile_half_even_and_overflow",
    "tests/analysis/session/test_analysis_session_helpers.py::",
    "tests/analysis/session/test_analysis_session_surface.py::",
    "tests/analysis/session/test_public_session.py::",
    "tests/analysis/session/test_session_discovery.py::",
    "tests/analysis/session/test_analysis_refs_policies.py::test_semantic_refs_stay_on_the_semantic_surface",
    "tests/analysis/session/test_analysis_refs_policies.py::test_artifact_ref_is_exported_and_preserves_ref",
    "tests/analysis/session/test_analysis_refs_policies.py::test_refs_reject_empty_refs",
    "tests/analysis/session/test_analysis_session_connections.py::test_datasource_connection_service_uses_explicit_backend_mapping",
    "tests/analysis/session/test_analysis_session_connections.py::test_datasource_connection_service_uses_backend_factory",
    "tests/analysis/session/test_analysis_session_core.py::test_session_public_fields_are_read_only",
    "tests/analysis/session/test_analysis_session_core.py::test_session_exposes_catalog_property",
    "tests/analysis/session/test_analysis_session_core.py::test_session_catalog_loads_external_semantic_layer",
    "tests/analysis/session/test_analysis_session_core.py::test_session_observe_uses_external_layer_datasource",
    "tests/analysis/session/test_analysis_session_profile_integration.py::test_model_qualified_datasource_name_is_rejected",
    "tests/analysis/session/test_analysis_session_profile_integration.py::test_missing_datasource_is_named_when_sources_are_requested",
    "tests/analysis/session/test_analysis_session_profile_integration.py::test_observe_uses_registered_global_datasource",
    "tests/analysis/statistics/test_analysis_arithmetic_oracles.py::",
    "tests/analysis/statistics/test_analysis_deviation_state.py::",
    "tests/analysis/statistics/test_analysis_statistics_kernel.py::",
    "tests/analysis/statistics/test_analysis_runs_kernel.py::",
    "tests/analysis/statistics/test_analysis_deviation.py::",
    "tests/analysis/statistics/test_analysis_runs.py::",
    "tests/analysis/statistics/test_analysis_statistics.py::",
    "tests/analysis/statistics/test_analysis_statistics_boundaries.py::",
    "tests/analysis/statistics/test_analysis_statistics_faults.py::",
    "tests/analysis/statistics/test_contract_boundaries.py::",
    "tests/analysis/statistics/test_decimal_row_statistics.py::",
    "tests/analysis/temporal/",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep representative and measured-fast analysis checks in the default suite."""
    mark_expression = config.getoption("markexpr")
    selects_slow = isinstance(mark_expression, str) and mark_expression.lstrip().startswith("slow")
    has_explicit_path = any(
        argument.startswith(("tests/", "./tests/")) for argument in config.invocation_params.args
    )
    if has_explicit_path and not selects_slow:
        return
    for item in items:
        if not item.nodeid.startswith("tests/analysis/"):
            continue
        if item.nodeid.startswith(_FAST_ANALYSIS_TEST_PREFIXES):
            continue
        if item.get_closest_marker("slow") is None:
            item.add_marker(pytest.mark.slow)


# Cap DuckDB to a single thread per connection. DuckDB defaults to
# hardware_concurrency() threads; with one pytest-xdist worker per CPU that
# oversubscribes cores (N workers x N threads) and inflates test wall time
# roughly 10x via thread thrash. One thread per connection keeps the suite
# CPU-bound and parallelizable across workers. Applied at import so every
# ibis.duckdb.connect call site (marivo backends + tests) is covered before
# any test runs.
_original_duckdb_connect = ibis.duckdb.connect


def _duckdb_connect_single_thread(*args: object, **kwargs: object) -> object:
    kwargs["threads"] = 1
    return _original_duckdb_connect(*args, **kwargs)


ibis.duckdb.connect = _duckdb_connect_single_thread


@pytest.fixture
def analysis_dsl_case_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DslCaseFactory:
    """Load one real DSL declaration project per isolated source and Session."""
    import marivo.analysis.session as session_attach
    import marivo.semantic as ms

    next_index = 0

    def build(
        scenario: DslScenario,
        *,
        names: DslNames = DSL_NAMES,
        revenue_unit: str = "CNY",
    ) -> DslCase:
        nonlocal next_index
        next_index += 1
        root = tmp_path / f"dsl_{next_index}"
        root.mkdir()
        database_path = root / "warehouse.duckdb"
        rows = analysis_dsl_rows(scenario)
        seed_analysis_dsl_database(
            database_path,
            names,
            rows,
            float_amount=scenario in ("j4", "j4_ties", "nonfinite"),
        )
        (root / "marivo.toml").write_text('[project]\nname = "analysis-dsl-fixture"\n')
        for relative_path, source in analysis_dsl_project_files(
            names, database_path, revenue_unit=revenue_unit
        ).items():
            destination = (
                root / "models" / relative_path
                if relative_path.startswith("datasources/")
                else root / "models" / "semantic" / relative_path
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source)
        catalog = ms.load(workspace_dir=root)
        monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
        session = session_attach.get_or_create(f"dsl-{scenario}", report_timezone="UTC")
        return DslCase(scenario, names, root, database_path, catalog, session)

    return build


@pytest.fixture(autouse=True)
def _disable_telemetry_outside_telemetry_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unrelated tests from writing local telemetry for every public call."""
    monkeypatch.setenv("MARIVO_TELEMETRY", "off")


@pytest.fixture(scope="session")
def installer_toolchain(
    pytestconfig: pytest.Config,
) -> InstallerToolchain:
    """Reuse a versioned immutable installer toolchain across test runs."""
    cache_root = Path(pytestconfig.cache.mkdir("installer-toolchains"))
    return InstallerToolchain.load_or_build(cache_root / "v4")


@pytest.fixture
def installer_env(tmp_path: Path, installer_toolchain: InstallerToolchain) -> InstallerEnv:
    """Shared fixture for the Marivo installer black-box tests.

    Reuses a versioned read-only toolchain while isolating each test's
    command log, HOME, project target, and environment mapping.
    """
    log = tmp_path / "commands.log"
    log.touch()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{installer_toolchain.base_bin}:{os.defpath}",
            "FAKE_LOG": str(log),
            "FAKE_UNAME": "Linux",
            "HOME": str(tmp_path / "home"),
            "FAKE_MARIVO_SHIM": str(installer_toolchain.marivo),
        }
    )
    return installer_toolchain, env


@pytest.fixture(scope="session")
def _sales_orders_template_path():
    """Session-scoped: ensure the DuckDB template is built once per worker."""
    return sales_orders_template()


@pytest.fixture
def authoring_evidence_project(tmp_path, monkeypatch):
    """Create a real DuckDB project covering authoring through analysis handoff."""
    import shutil

    database_path = tmp_path / "warehouse.duckdb"
    replica_path = tmp_path / "warehouse_replica.duckdb"
    shutil.copy2(authoring_evidence_template(), database_path)
    shutil.copy2(authoring_evidence_template(), replica_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "authoring-e2e"\n')
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
    )
    (datasource_dir / "warehouse_replica.py").write_text(
        "import marivo.datasource as md\n"
        f"md.duckdb(name='warehouse_replica', path={str(replica_path)!r})\n"
    )
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic_dir / "models.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "orders = ms.entity(\n"
        "    name='orders',\n"
        "    datasource=ms.ref.datasource('warehouse'),\n"
        "    source=md.table('orders', columns={\n"
        "        'query_id': 'query_id',\n"
        "        'self': 'self',\n"
        "        'region': 'region',\n"
        "        'log_date': 'log_date',\n"
        "        'log_hour': 'log_hour',\n"
        "        'amount': 'amount',\n"
        "        'uncommon_date': 'uncommon_date',\n"
        "        'epoch_like': 'epoch_like',\n"
        "    }),\n"
        "    primary_key=['query_id'],\n"
        "    ai_context=ms.ai_context(\n"
        "        business_definition='One row per accepted order query.',\n"
        "        guardrails=['Use only accepted order queries.'],\n"
        "    ),\n"
        ")\n"
        "region = ms.dimension_column(\n"
        "    name='region', entity=orders, column='region',\n"
        "    ai_context=ms.ai_context(business_definition='Order region.'),\n"
        ")\n"
        "log_hour = ms.dimension_column(\n"
        "    name='log_hour', entity=orders, column='log_hour',\n"
        "    ai_context=ms.ai_context(business_definition='UTC log hour component.'),\n"
        ")\n"
        "log_date = ms.time_dimension_column(\n"
        "    name='log_date', entity=orders, column='log_date', granularity='day',\n"
        "    parse=ms.strptime('%Y%m%d'), is_default=True,\n"
        "    ai_context=ms.ai_context(business_definition='UTC order log date.'),\n"
        ")\n"
        "amount = ms.measure_column(\n"
        "    name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='USD',\n"
        "    ai_context=ms.ai_context(business_definition='Accepted order amount in USD.'),\n"
        ")\n"
        "revenue = ms.aggregate(\n"
        "    name='revenue', measure=amount, agg='sum', unit='USD',\n"
        "    ai_context=ms.ai_context(\n"
        "        business_definition='Sum of accepted order amounts.',\n"
        "        guardrails=['Do not mix currencies.'],\n"
        "    ),\n"
        ")\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def semantic_project_factory(tmp_path):
    """Create a SemanticProject from a mapping of project-relative files."""

    def _make(
        files: dict[str, str],
        load: bool = True,
        models: list[str] | None = None,
        workspace_dir=None,
    ):
        from marivo.semantic.reader import SemanticProject

        effective_dir = workspace_dir if workspace_dir is not None else tmp_path
        # Write project manifest for discovery
        (effective_dir / "marivo.toml").write_text('[project]\nname = "test"\n')
        marivo_root = effective_dir / "models"
        root = marivo_root / "semantic"
        root.mkdir(parents=True, exist_ok=True)
        for rel, src in files.items():
            full = marivo_root / rel if rel.startswith("datasources/") else root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(src)

        declared_datasources: set[str] = set()
        datasource_ref_pattern = re.compile(
            r"(?:"
            r"datasource\s*=\s*(?:ms\.)?ref\.datasource\("
            r"|(?:ms\.)?ref\.datasource\("
            r"|datasource\s*=\s*"
            r")(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)"
        )
        for src in files.values():
            for match in datasource_ref_pattern.finditer(src):
                name = match.group("name")
                if "." not in name:
                    declared_datasources.add(name)
        for datasource_name in declared_datasources:
            datasource_file = marivo_root / "datasources" / f"{datasource_name}.py"
            if datasource_file.exists():
                continue
            datasource_file.parent.mkdir(parents=True, exist_ok=True)
            datasource_file.write_text(
                "import marivo.datasource as md\n"
                f"md.duckdb(name={datasource_name!r}, "
                "path=':memory:')\n"
            )

        project = SemanticProject(workspace_dir=effective_dir)
        if load:
            project.load(domains=models)
        return project

    return _make


@pytest.fixture
def funnel_session_factory(
    semantic_project_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Return a factory for named sessions over the commerce funnel project."""
    import marivo.analysis.session as session_attach

    def _build(name: str) -> Any:
        project = semantic_project_factory(lifecycle_project_files())
        backend = seed_lifecycle_backend(
            events=FUNNEL_BASE_EVENTS,
            orders=FUNNEL_BASE_ORDERS,
            watermark_events=frozenset(
                {
                    "commerce.order_created",
                    "commerce.payment_captured",
                    "commerce.order_closed",
                }
            ),
        )
        monkeypatch.chdir(project.workspace_dir)
        monkeypatch.setenv("TZ", "UTC")
        return session_attach.get_or_create(
            name=name,
            report_timezone="UTC",
            backends={"warehouse": lambda: backend},
        )

    return _build


@pytest.fixture
def funnel_session(funnel_session_factory: Any) -> Any:
    """Yield one isolated session for funnel comparison and attribution."""
    import marivo.analysis.session as session_attach

    session = funnel_session_factory("funnel-phase-four")
    try:
        yield session
    finally:
        session.close()
        session_attach._reset_process_state()


@pytest.fixture
def cart_step() -> Any:
    from tests.shared_fixtures import pattern_step_for_tests

    return pattern_step_for_tests("cart")


@pytest.fixture
def payment_step() -> Any:
    from tests.shared_fixtures import pattern_step_for_tests

    return pattern_step_for_tests("payment")


@pytest.fixture
def acquisition_channel_entry(funnel_session: Any) -> Any:
    return funnel_session.catalog.dimensions.get("acquisition_channel")


@pytest.fixture
def plan_tier_entry(funnel_session: Any) -> Any:
    return funnel_session.catalog.dimensions.get("plan_tier")


def bootstrap_sales_project(tmp_path, *, with_time: bool = True) -> None:
    """Create a ready semantic project on disk for analysis tests."""
    # Write project manifest for discovery
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    time_dimension = (
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        f"{time_dimension}"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def status_at(orders):\n"
        "    return orders.status_at\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def platform(orders):\n"
        "    return orders.platform\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def bucket(orders):\n"
        "    return orders.bucket\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def bucket_start(orders):\n"
        "    return orders.bucket_start\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def department(orders):\n"
        "    return orders.department\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def cluster(orders):\n"
        "    return orders.cluster\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def category(orders):\n"
        "    return orders.category\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def channel(orders):\n"
        "    return orders.channel\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def country(orders):\n"
        "    return orders.country\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def a(orders):\n"
        "    return orders.a\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def b(orders):\n"
        "    return orders.b\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def nonexistent(orders):\n"
        "    return orders.nonexistent\n"
        "\n"
        "@ms.metric(entities=[orders], additivity=ms.additive_all(), "
        "name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


@pytest.fixture
def retained_coordinates_case(analysis_dsl_case_factory: DslCaseFactory) -> DslCase:
    """Preserve the independent 147 total and selected 140/3 fold oracle."""
    import duckdb

    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer WHERE customer_id = 'D'")
        connection.execute("""INSERT INTO "order" VALUES
            ('a1', 'A', 'web', 'paid', '2026-08-10T00:00:00+00:00', 100),
            ('a2', 'A', 'web', 'paid', '2026-08-11T00:00:00+00:00', 20),
            ('b1', 'B', 'app', 'paid', '2026-08-10T00:00:00+00:00', 20),
            ('c1', 'C', 'app', 'paid', '2026-08-10T00:00:00+00:00', 7)""")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\nmean_amount = ms.aggregate(name='mean_amount', measure=amount, agg='mean', time=ordered_at)\n"
    )
    ms.load(workspace_dir=case.root)
    return case


@pytest.fixture
def source_trace(monkeypatch: pytest.MonkeyPatch) -> SourceTrace:
    """Capture native cursor calls and execution-owner receipts."""
    from tests.support.source_trace import capture_source

    return capture_source(monkeypatch)


@pytest.fixture(scope="session")
def installed_wheel(tmp_path_factory: pytest.TempPathFactory) -> InstalledWheel:
    """Prepare one candidate wheel without replaying development test matrices."""
    return prepare_wheel(tmp_path_factory.mktemp("installed-wheel"))


@pytest.fixture(
    scope="session", params=("base", "duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def installed_dependency_wheel(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> InstalledWheel:
    """Keep each extra's dependency graph in a separate noneditable environment."""
    extra = str(request.param)
    if extra in ("base", "duckdb"):
        existing: object = request.getfixturevalue(
            "installed_base_wheel" if extra == "base" else "installed_wheel"
        )
        assert isinstance(existing, InstalledWheel)
        return existing
    return prepare_wheel(
        tmp_path_factory.mktemp("installed-" + extra), extras=() if extra == "base" else (extra,)
    )


@pytest.fixture(scope="session")
def installed_base_wheel(tmp_path_factory: pytest.TempPathFactory) -> InstalledWheel:
    """Reuse the core-only environment for isolation and real saved Artifact reads."""
    return prepare_wheel(tmp_path_factory.mktemp("installed-base"), extras=())


@pytest.fixture(scope="session")
def installed_multisource_wheel(installed_wheel: InstalledWheel) -> InstalledWheel:
    """Add native drivers only for explicitly opted-in installed-source checks."""
    wheel = installed_wheel.wheel
    installed_wheel.run(
        "install-native-drivers",
        [
            str(installed_wheel.interpreter),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--constraint",
            str(installed_wheel.work / "constraints.txt"),
            f"{wheel}[all]",
            "psycopg[binary]",
        ],
    )
    installed_wheel.run(
        "native-dependency-check", [str(installed_wheel.interpreter), "-m", "pip", "check"]
    )
    return installed_wheel
