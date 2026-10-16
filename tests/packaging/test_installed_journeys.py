"""Complete public combinations in one isolated candidate, never source imports."""

from __future__ import annotations

import pytest

from tests.packaging.complete_journeys import JOURNEYS
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
