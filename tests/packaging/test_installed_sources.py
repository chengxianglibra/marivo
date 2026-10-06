"""Opt-in installed public journeys against existing read-only sources."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.packaging.wheel_support import InstalledWheel
from tests.support.json import arr, obj, read

pytestmark = [
    pytest.mark.release,
    pytest.mark.skipif(
        os.environ.get("MARIVO_INSTALLED_MULTISOURCE_TEST") != "1",
        reason="explicit installed multisource opt-in required",
    ),
]
BACKENDS = ("sqlite", "postgres", "mysql", "clickhouse", "trino")


@pytest.mark.parametrize("engine", BACKENDS)
def test_installed_public_multisource_journey(
    engine: str, tmp_path: Path, installed_multisource_wheel: InstalledWheel
) -> None:
    selected = os.environ.get("MARIVO_INSTALLED_BACKENDS", ",".join(BACKENDS)).split(",")
    assert set(selected) <= set(BACKENDS), selected
    if engine not in selected:
        pytest.skip("backend not selected for this serial service group")
    candidate = installed_multisource_wheel
    project = tmp_path / engine

    def phase(name: str) -> None:
        candidate.run(
            engine + "-" + name,
            [
                str(candidate.interpreter),
                "-m",
                "tests.packaging.source_probe",
                name,
                engine,
                str(project),
                str(candidate.reports / f"{engine}-{name}.json"),
            ],
        )

    try:
        for name in ("prepare", "privileges", "produce"):
            phase(name)
        if engine in {"sqlite", "mysql"}:
            phase("native")
        phase("invalidate")
        phase("invalid")
    finally:
        if (project / "prefix").exists():
            phase("remove")
    phase("cold")
    phase("offline")
    produced = read(candidate.reports / f"{engine}-produce.json")
    cold = read(candidate.reports / f"{engine}-cold.json")
    result = obj(cold["result"])
    assert result["source_tables_removed"] is True
    assert result["source_connection_forbidden"] is True
    receipts = arr(result["receipts"])
    for item in receipts:
        receipt = obj(item)
        assert receipt["new_runs"] == 1
        assert receipt["cache_reused"] is True
    assert len(receipts) == 3
    assert produced["pid"] != cold["pid"]
    assert obj(produced["result"])["session"] == obj(cold["result"])["session"]
