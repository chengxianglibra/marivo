"""Captured temporal qualification matrix with independent offline continuations."""

import subprocess
import sys

import pytest

from tests.deviation_r82_fixture import ORIGINS, prepare_profiles
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize("key_profile", ("KS", "KI", "KC"))
@pytest.mark.parametrize("form,unit,zone,calendar", ORIGINS)
def test_time_profiles_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory,
    key_profile: str,
    form: str,
    unit: str,
    zone: str,
    calendar: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, key_profile, form, unit, zone, calendar)
    for phase in ("produce", "fixed", "cold"):
        if phase == "fixed":
            case.database_path.rename(case.database_path.with_suffix(".offline"))
            for source in (case.root / "source_files").glob("*.parquet"):
                source.rename(source.with_suffix(".offline"))
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.deviation_r82_time_worker",
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
