"""Complete public combinations in one isolated candidate, never source imports."""

from __future__ import annotations

import pytest

from tests.packaging.complete_journeys import JOURNEYS
from tests.packaging.test_installed_sources import METHOD_SELECTIONS
from tests.packaging.wheel_support import InstalledWheel
from tests.support.json import read

pytestmark = pytest.mark.release


@pytest.mark.parametrize("journey", JOURNEYS)
def test_installed_complete_public_journey(installed_wheel: InstalledWheel, journey: str) -> None:
    candidate = installed_wheel
    project = candidate.work / journey
    reports = []
    for phase in ("produce", "fixed", "cold"):
        destination = candidate.reports / f"{journey}-{phase}.json"
        candidate.run(
            f"{journey}-{phase}",
            [
                str(candidate.interpreter),
                "-m",
                "tests.packaging.complete_journeys",
                str(project),
                journey,
                phase,
                str(destination),
            ],
        )
        report = read(destination)
        assert report["journey"] == journey and report["phase"] == phase
        assert "origin" in report
        reports.append(report)
    assert len({report["pid"] for report in reports}) == 3
    assert not tuple(project.rglob("*.duckdb"))
    assert not tuple(project.rglob("models"))
    assert not tuple(project.rglob("source_files"))


@pytest.mark.parametrize(
    "owner,selection",
    (
        ("tests/analysis/materialization/test_analysis_state.py", ""),
        (
            "tests/analysis/materialization/test_analysis_graph_publication.py",
            "existing_generation_rejection or retained_reader_failure or corrupt_input or foreign_session or faults_have_no_success or fixed_shared_difference or fixed_reads_without_producer_registry or committed_reads_do_not_repeat_production_validation",
        ),
        (
            "tests/analysis/materialization/test_public_refusals.py",
            "composition_refuses or required_part_missing",
        ),
        ("tests/analysis/graph/test_premise_verification.py", ""),
        ("tests/analysis/materialization/test_local_graph_interrupts.py", ""),
        (
            "tests/analysis/materialization/test_producer_recovery.py",
            "duckdb or sqlite or file_producer or http_source",
        ),
        (
            "tests/analysis/graph/test_method_consumers.py",
            "source_statistic and (duckdb or sqlite)",
        ),
        ("tests/datasource/test_source_profiles.py", "duckdb or sqlite"),
        (
            "tests/datasource/test_datasource_profiles_store.py",
            "plaintext or env_refs_round_trip",
        ),
        (
            "tests/analysis/numeric/test_analysis_display.py",
            "current_row_mean or business_display_reads_saved or closed_display_exchange_preserves_four_cells or top_k_original_rank_share_and_empty",
        ),
        ("tests/analysis/numeric/test_cohort_unknown.py", ""),
        (
            "tests/analysis/numeric/test_fixed_selection_runtime.py",
            "fixed_difference_endpoints or display_and_fixed_reference_selection",
        ),
        (
            "tests/analysis/numeric/test_analysis_comparison_runtime.py",
            "composite_keys_double_empty or union_keeps_present_nondefined or comparison_construction_is_lazy or float_row_statistic_comparison or stable_float_row_statistic_comparison",
        ),
        (
            "tests/analysis/temporal/test_temporal_public_runtime.py",
            "changed_driver_timezone",
        ),
        ("tests/analysis/statistics/test_analysis_statistics_boundaries.py", ""),
        (
            "tests/analysis/statistics/test_analysis_statistics.py",
            "all_requested_pairs or lag_counts_ties or forecast_prediction_is_a_complete",
        ),
        ("tests/analysis/statistics/test_analysis_statistics_kernel.py", ""),
        (
            "tests/analysis/statistics/test_composition.py",
            "direct_score_runs or two_separate_axes or score_select_members_followup or category_time_three_methods",
        ),
        ("tests/analysis/materialization/test_public_schema_drift.py", ""),
        ("tests/analysis/journey/test_anchor_consumers.py", ""),
        ("tests/analysis/graph/test_reference_consumers.py", "duckdb or sqlite"),
        (
            "tests/analysis/graph/test_distribution_consumers.py",
            "(duckdb or sqlite) and (" + METHOD_SELECTIONS["test_distribution_consumers.py"] + ")",
        ),
        (
            "tests/analysis/graph/test_multiroot_consumers.py",
            "(duckdb or sqlite) and (" + METHOD_SELECTIONS["test_multiroot_consumers.py"] + ")",
        ),
        ("tests/analysis/numeric/test_comparison_consumers.py", "duckdb or sqlite"),
        ("tests/analysis/materialization/test_attribution_recovery.py", "duckdb or sqlite"),
        (
            "tests/analysis/numeric/test_native_numeric.py",
            "native_weighted_and_mean or sqlite or native_numeric_recovers_without_sources",
        ),
    ),
)
def test_installed_independent_behavior_owner(
    installed_wheel: InstalledWheel, owner: str, selection: str
) -> None:
    candidate = installed_wheel
    name = owner.rsplit("/", 1)[-1].removesuffix(".py")
    evidence = candidate.reports / name
    evidence.mkdir()
    projects = candidate.work / "owner_projects"
    projects.mkdir(exist_ok=True)
    arguments = [
        str(candidate.interpreter),
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "--maxfail=5",
        "--basetemp=" + str(projects / name),
        owner,
    ]
    if selection:
        arguments.extend(("-k", selection))
    candidate.run(
        name,
        arguments,
        extra_env={
            "MARIVO_R93_EVIDENCE_DIR": str(evidence),
            "MARIVO_R92_EVIDENCE_DIR": str(evidence),
        },
    )
