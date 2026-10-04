"""F11 uses captured sum contributions in the complete public logical source DAG."""

import subprocess
import sys

import pytest

from tests.deviation_r82_fixture import ORIGINS, prepare_profiles
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize("key_profile", ("KS", "KI", "KC"))
@pytest.mark.parametrize(
    "form,unit,zone,calendar,domain",
    (
        *((form, "us", "UTC", False, "entity") for form in ("table", "parquet")),
        *((form, unit, zone, calendar, "entity_time") for form, unit, zone, calendar in ORIGINS),
    ),
)
def test_f11_full_source_matrix(
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
            "tests.deviation_r82_f11_worker",
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
