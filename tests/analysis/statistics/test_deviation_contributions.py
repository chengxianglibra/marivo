"""Explicit pandas selection starts a new source query with complete member keys."""

import pytest

from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.analysis.statistics.recovery_worker import subprocess_phases
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.slow
@pytest.mark.release
def test_captured_contributions_followup_profiles(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(
        case, "KC", "parquet", "us", "America/New_York", True, followup=True, profile_indices=(7,)
    )
    subprocess_phases(case, "entity_time")
