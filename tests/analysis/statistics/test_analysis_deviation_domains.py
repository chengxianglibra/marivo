"""Non-time exports preserve scalar, category and complete identity carriers cold."""

import pytest

from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.analysis.statistics.recovery_worker import subprocess_phases
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    "domain,key_profile,index",
    [("scalar", "KT", 0), ("category", "KT", 1), ("entity", "KI", 0), ("entity", "KC", 7)],
)
@pytest.mark.slow
def test_non_time_domains_types_keys_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory,
    domain: str,
    key_profile: str,
    index: int,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, key_profile, "parquet", "us", "UTC", False, profile_indices=(index,))
    subprocess_phases(case, domain)
