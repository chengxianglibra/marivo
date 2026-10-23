"""Statistical exports preserve category, identity and category-time carriers cold."""

import pytest

from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.analysis.statistics.recovery_worker import subprocess_grouped_phases
from tests.shared_fixtures import DslCase, DslCaseFactory


@pytest.mark.slow
@pytest.mark.release
def test_non_time_domains_types_keys_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    cases: list[tuple[DslCase, str]] = []
    for mode, key_profile, profile_indices, followup in (
        ("category", "KT", (1,), False),
        ("entity", "KC", (7,), False),
        ("category_time", "KC", (0, 1, 5), True),
    ):
        case = analysis_dsl_case_factory("j2")
        prepare_profiles(
            case,
            key_profile,
            "parquet",
            "us",
            "UTC",
            False,
            followup=followup,
            profile_indices=profile_indices,
        )
        cases.append((case, mode))
    subprocess_grouped_phases(tuple(cases))
