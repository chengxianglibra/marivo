"""Independently runnable installed public journeys and fresh-process phases."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

from tests.analysis.journey import anchors_worker, funnel_public_recovery_worker, retention_worker
from tests.analysis.lifecycle import history_public_recovery_worker
from tests.analysis.statistics import recovery_worker
from tests.packaging import (
    association_edges_journey,
    graph_journeys,
    hierarchy_journey,
    relation_journeys,
    versioned_journey,
)
from tests.support.json import Json, checked, encode, obj

JOURNEYS = tuple(f"a{number:02}" for number in range(1, 14))


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
    legacy_phase = {"produce": "produce", "fixed": "continue", "cold": "recover"}[phase]
    result: dict[str, Json]
    if journey == "a01":
        result = hierarchy_journey.run(root, phase)
    elif journey == "a03":
        result = obj(
            checked(
                json.loads(json.dumps(graph_journeys.journey(legacy_phase, root, "j3", "parquet")))
            )
        )
    elif journey == "a04":
        result = {
            scenario: obj(
                checked(
                    json.loads(
                        json.dumps(
                            graph_journeys.journey(
                                legacy_phase, root / scenario, scenario, "parquet"
                            )
                        )
                    )
                )
            )
            for scenario in ("j4", "j4_ties")
        }
        result["null_pairs"] = association_edges_journey.run(root / "null_pairs", phase)
    elif journey in ("a02", "a06", "a07", "a08", "a12"):
        scenario = "a06" if journey == "a12" else journey
        result = obj(checked(relation_journeys.journey(legacy_phase, root, scenario, "parquet")))
    elif journey == "a05":
        _project(root)
        result = versioned_journey.run(root, phase)
    elif journey == "a09":
        _project(root)
        result = (
            funnel_public_recovery_worker.produce(root, "parquet")
            if phase == "produce"
            else funnel_public_recovery_worker.recover(root, phase)
        )
        if phase == "produce":
            (root / "source.duckdb").unlink()
            shutil.rmtree(root / "models")
            for path in root.glob("*.parquet"):
                path.unlink()
    elif journey == "a10":
        result = {}
        for risk in ("views", "captured_observations"):
            project = root / risk
            _project(project)
            result[risk] = (
                history_public_recovery_worker.produce(project, risk)
                if phase == "produce"
                else history_public_recovery_worker.recover(project, risk, phase)
            )
            if phase == "produce":
                (project / "source.duckdb").unlink()
                shutil.rmtree(project / "models")
    elif journey == "a11":
        result = recovery_worker.run(root, phase, "parquet")
    else:
        assert journey == "a13"
        result = {}
        for kind in ("anchors", "retention"):
            project = root / kind
            _project(project)
            if phase == "produce":
                config: dict[str, Json] = {
                    "subject": "c",
                    "occurrence": "c",
                    "form": "parquet",
                    "unit": "us",
                    "zone": "America/New_York",
                }
                if kind == "anchors":
                    config["all_K"] = True
                (project / "config.json").write_bytes(encode(config))
            if kind == "anchors":
                result[kind] = _printed(
                    partial(anchors_worker.produce, project)
                    if phase == "produce"
                    else partial(anchors_worker.offline, project, phase == "cold")
                )
            else:
                result[kind] = _printed(
                    partial(retention_worker.produce, project)
                    if phase == "produce"
                    else partial(retention_worker.offline, project, phase == "cold")
                )
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
