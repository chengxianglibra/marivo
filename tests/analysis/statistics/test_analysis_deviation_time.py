"""Timestamp units, composite identities and certified calendar bounds survive cold reads."""

import pytest

from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.analysis.statistics.recovery_worker import subprocess_phases
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.slow
def test_time_profiles_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KC", "parquet", "us", "America/New_York", True, profile_indices=(7,))
    subprocess_phases(case, "entity_time")
