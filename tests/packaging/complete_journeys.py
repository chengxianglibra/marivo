"""Independently runnable installed public journeys and fresh-process phases."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from tests.analysis.statistics import recovery_worker
from tests.packaging import (
    association_edges_journey,
    graph_journeys,
    hierarchy_journey,
    relation_journeys,
    versioned_journey,
)
from tests.support.json import Json, checked, encode, obj

JOURNEYS = tuple(f"a{number:02}" for number in (1, 2, 3, 4, 5, 6, 7, 8, 11, 12))


def _printed(operation: Callable[[], None]) -> dict[str, Json]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        operation()
    return obj(checked(json.loads(output.getvalue().splitlines()[-1])))


def _project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)


def run(root: Path, journey: str, phase: str) -> dict[str, Json]:
    assert journey in JOURNEYS and phase in ("produce", "fixed", "cold")
    root = root.resolve()
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    result: dict[str, Json]
    if journey == "a01":
        result = hierarchy_journey.run(root, phase)
    elif journey == "a03":
        result = obj(
            checked(json.loads(json.dumps(graph_journeys.journey(phase, root, "j3", "parquet"))))
        )
    elif journey == "a04":
        result = {
            scenario: obj(
                checked(
                    json.loads(
                        json.dumps(
                            graph_journeys.journey(phase, root / scenario, scenario, "parquet")
                        )
                    )
                )
            )
            for scenario in ("j4", "j4_ties")
        }
        result["null_pairs"] = association_edges_journey.run(root / "null_pairs", phase)
    elif journey in ("a02", "a06", "a07", "a08", "a12"):
        scenario = "a06" if journey == "a12" else journey
        result = obj(checked(relation_journeys.journey(phase, root, scenario, "parquet")))
    elif journey == "a05":
        _project(root)
        result = versioned_journey.run(root, phase)
    elif journey == "a11":
        _project(root)
        result = obj(
            checked(recovery_worker.run(root, "offline" if phase == "fixed" else phase, "entity"))
        )
    else:
        raise AssertionError(f"Unknown installed journey: {journey}")
    return {"journey": journey, "phase": phase, "pid": os.getpid(), "result": result}


if __name__ == "__main__":
    root, journey, phase, destination = (
        Path(sys.argv[1]),
        sys.argv[2],
        sys.argv[3],
        Path(sys.argv[4]),
    )
    report = run(root, journey, phase)
    if "MARIVO_WHEEL_SHA256" in os.environ:
        from tests.packaging.wheel_probe import assert_installed_origin

        report["origin"] = checked(assert_installed_origin())
    destination.write_bytes(encode(report))
