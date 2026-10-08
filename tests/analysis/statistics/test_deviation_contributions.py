"""Public followup uses captured sum contributions in the complete public logical source DAG."""

import subprocess
import sys

import pytest

from tests.analysis.statistics.deviation_fixture import RECOVERY_PROFILES, prepare_profiles
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize(
    "key_profile,form,unit,zone,calendar,domain",
    (
        *((key, "parquet", "us", "UTC", False, "entity") for key in ("KS", "KI", "KC")),
        *(
            (key, form, unit, zone, calendar, "entity_time")
            for key, form, unit, zone, calendar in RECOVERY_PROFILES
        ),
    ),
)
def test_captured_contributions_followup_profiles(
    analysis_dsl_case_factory: DslCaseFactory,
    key_profile: str,
    form: str,
    unit: str,
    zone: str,
    calendar: bool,
    domain: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, key_profile, form, unit, zone, calendar, followup=True)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.statistics.deviation_contribution_worker",
            str(case.root),
            key_profile,
            unit,
            zone,
            domain,
            "calendar" if calendar else "day",
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert '"accepted": "source_f11"' in process.stdout
