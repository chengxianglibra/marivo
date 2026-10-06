"""Real concurrent provisioning regression for the shared PostgreSQL fixture."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from scripts.r9_qualification_requirements import encode


@pytest.mark.runtime
def test_parallel_postgres_setup_serializes_shared_role_updates() -> None:
    if os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1":
        pytest.skip("Requires explicit PostgreSQL analysis opt-in")
    from tests.multisource_environment import postgres_analysis as pg

    barrier = Barrier(4)

    def configure(index: int) -> dict[str, object]:
        barrier.wait(timeout=10)
        return pg.setup()

    with ThreadPoolExecutor(max_workers=4) as workers:
        reports = list(workers.map(configure, range(4)))
    assert len(reports) == 4
    assert all(report["read_only_privileges_verified"] is True for report in reports)
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "postgres-setup-concurrency.json").write_bytes(
            encode(
                {
                    "simultaneous_callers": 4,
                    "completed": 4,
                    "reader_privileges_verified": True,
                    "boundary": "Test fixture provisioning concurrency only; no product backend qualification.",
                }
            )
        )
