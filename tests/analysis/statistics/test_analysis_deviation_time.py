"""Captured temporal qualification matrix with independent offline continuations."""

import subprocess
import sys

import pytest

from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    "key_profile,form,unit,zone,calendar",
    [
        ("KC", "parquet", "ns", "UTC", False),
        ("KC", "parquet", "us", "America/New_York", True),
    ],
)
@pytest.mark.slow
def test_time_profiles_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory,
    key_profile: str,
    form: str,
    unit: str,
    zone: str,
    calendar: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(
        case,
        key_profile,
        form,
        unit,
        zone,
        calendar,
        profile_indices=(0 if key_profile == "KS" else 1 if key_profile == "KI" else 7,),
    )
    cold_representative = key_profile == "KC" and (unit == "ns" or calendar)
    phases = ("produce", "fixed", "cold") if cold_representative else ("produce", "fixed")
    for phase in phases:
        if phase == "fixed":
            case.database_path.rename(case.database_path.with_suffix(".offline"))
            for source in (case.root / "source_files").glob("*.parquet"):
                source.rename(source.with_suffix(".offline"))
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.deviation_time_worker",
                str(case.root),
                phase,
                key_profile,
                unit,
                zone,
                "calendar" if calendar else "day",
            ],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert f'"accepted": "{phase}"' in process.stdout
