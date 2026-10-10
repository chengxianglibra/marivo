"""Public followup uses captured sum contributions in the complete public logical source DAG."""

import subprocess
import sys

import pytest

from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.slow
@pytest.mark.parametrize(
    "key_profile,form,unit,zone,calendar,domain",
    [
        ("KS", "parquet", "us", "UTC", False, "entity"),
        ("KI", "parquet", "us", "UTC", False, "entity_time"),
        ("KC", "parquet", "us", "America/New_York", True, "entity_time"),
    ],
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
    prepare_profiles(
        case,
        key_profile,
        form,
        unit,
        zone,
        calendar,
        followup=True,
        profile_indices=(0 if key_profile == "KS" else 1 if key_profile == "KI" else 7,),
    )
    source_unit = "ms" if form == "parquet" and unit == "s" else unit
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.statistics.deviation_contribution_worker",
            str(case.root),
            key_profile,
            source_unit,
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
