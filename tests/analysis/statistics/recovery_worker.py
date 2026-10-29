"""Independent producer and two source-offline terminal readers with pandas recipes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import duckdb
import ibis
import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot as frame_snapshot
from tests.analysis.statistics.deviation_oracle import PROFILES, expected, forbidden
from tests.analysis.statistics.deviation_time_worker import publish_calendar
from tests.analysis.statistics.journeys import create
from tests.analysis.statistics.pandas_workflows import deviation, forecast, plain
from tests.shared_fixtures import DslCase, catalog_value, run_ids
from tests.support.json import arr, checked, obj
from tests.support.paths import PROJECT_ROOT


def snapshot(result: mv.AnalysisFrame, *, frame: pd.DataFrame | None = None) -> dict[str, object]:
    return dict(frame_snapshot(result, frame=frame))


def verify(frame: pd.DataFrame, mode: str, profile: dict[str, object]) -> None:
    values = frame["value"].dropna()
    if profile:
        indices = profile["profiles"]
        assert isinstance(indices, list)
        index = indices[0]
        assert isinstance(index, int)
        raw = (
            (1, 2, 7)
            if index == 0
            else (0.1, 0.2, 0.7)
            if index == 1
            else tuple(
                Decimal((0, (digit,), -int(PROFILES[index].split(",")[1][:-1])))
                for digit in (1, 2, 7)
            )
        )
        wanted = (
            [raw[-1]]
            if mode == "scalar"
            else [*raw, *([0] * 6)]
            if mode == "category_time"
            else list(raw)
        )
        assert sorted(values) == sorted(wanted)
        if mode in ("time", "entity_time", "category_time"):
            axes = frame.attrs["axes"]
            calendar = next(axis for axis in axes if axis["role"] == "calendar")
            zone = profile["zone"]
            assert isinstance(zone, str)
            descriptor = calendar["descriptor"]
            assert descriptor["timezone"] == zone
            directory = next(
                part["rows"]
                for part in frame.attrs["frozen_coordinates"]
                if part["name"] == descriptor["frozen"]["directory_name"]
            )
            days = (1, 2, 4, 7) if profile["calendar"] else (1, 2, 3, 4)
            boundaries = tuple(
                datetime(2026, 8, day, tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
                for day in days
            )
            assert list(
                directory[["actual_start", "actual_end"]].itertuples(index=False, name=None)
            ) == list(pairwise(boundaries))
            assert len(frame) == (3 if mode == "time" else 9)
            assert int(frame.value.isna().sum()) == (0 if mode in ("time", "category_time") else 6)
        if mode in ("entity", "entity_time"):
            expected_keys = (
                [1, 2, 3]
                if profile["key_profile"] == "KI"
                else [("a", 11), ("b", 12), ("c", 13)]
                if profile["key_profile"] == "KC"
                else ["a", "b", "c"]
            )
            member_index = next(
                index
                for index, axis in enumerate(frame.attrs["axes"])
                if axis["role"] == "identity"
            )
            assert frame[f"key_{member_index}"].drop_duplicates().tolist() == expected_keys
        if mode == "category":
            assert frame.key_0.tolist() == ["a", "b", "c"]
        if mode == "scalar":
            assert len(frame) == 1 and list(frame.columns) == ["value"]
        if mode == "category_time":
            names = ["value", *(f"profile_{index}" for index in indices[1:])]
            for method in ("pearson", "spearman", "kendall"):
                coefficients = frame[names].astype(float).corr(method=method)
                assert (coefficients - 1).abs().le(1e-12).all().all()
    for algorithm in ("zscore", "mad"):
        output = deviation(frame, algorithm)
        assert output.value.equals(frame.value)
        if len(values) >= 2 and values.nunique() > 1:
            assert output.score.dropna().tolist() == list(expected(tuple(values), algorithm)[3])
        else:
            assert output.score.isna().all()
    if len(values) >= 3:
        predicted = forecast(values)
        assert predicted.prediction.tolist() == [float(values.iloc[-1])] * 2
        assert (predicted.lower <= predicted.prediction).all()
        assert (predicted.upper >= predicted.prediction).all()


def run(root: Path, phase: str, mode: str) -> dict[str, object]:
    assert phase in ("produce", "offline", "cold")
    assert mode in (
        "scalar",
        "category",
        "entity",
        "display",
        "time",
        "entity_time",
        "category_time",
    )
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    os.chdir(root)
    state_path = root / "statistics-recovery.json"
    if phase == "produce":
        if not (root / "marivo.toml").exists():
            create(root, "parquet")
        profile_path = root / "deviation-profile.json"
        profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
        session = mv.session.get_or_create("pandas-" + mode, timezone=profile.get("zone", "UTC"))
        members = session.members(session.catalog.get("entity:sales.order"))
        field = catalog_value(
            session.catalog,
            f"sales.order.profile_{profile['profiles'][0]}" if profile else "sales.order.amount",
            kind="field",
        )
        if mode == "scalar":
            query = session.observe({"value": field.max()})
        elif mode in ("time", "entity_time", "category_time"):
            if profile["calendar"]:
                publish_calendar(session.catalog, profile["zone"], baseline=profile["followup"])
            grain = (
                session.catalog.get("period_calendar:sales.unequal").grain("period")
                if profile["calendar"]
                else mv.grain("day")
            )
            grid = session.time_scope(
                start="2026-08-01", end="2026-08-07" if profile["calendar"] else "2026-08-04"
            ).by(grain)
            time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
            quantities = {
                "value": field.sum(time=time) if mode == "category_time" else field.max(time=time)
            }
            if mode == "category_time":
                quantities.update(
                    (
                        f"profile_{index}",
                        catalog_value(
                            session.catalog, f"sales.order.profile_{index}", kind="field"
                        ).sum(time=time),
                    )
                    for index in profile["profiles"][1:]
                )
            query = session.observe(
                quantities,
                by=mv.product(grid, members)
                if mode == "entity_time"
                else (grid, catalog_value(session.catalog, "sales.order.channel", kind="field"))
                if mode == "category_time"
                else (grid,),
            )
        elif mode == "category":
            query = session.observe(
                {"value": field.max()},
                by=(catalog_value(session.catalog, "sales.order.channel", kind="field"),),
            )
        else:
            query = session.read(members, {"value": field})
            if mode == "display":
                query = query.top(7, by=query["value"])
        saved = query.persist()
        frame = plain(saved)
        verify(frame, mode, profile)
        state = {
            "session": session.id,
            "artifact": saved.artifact_id,
            "frame": snapshot(saved, frame=frame),
            "profile": profile,
        }
        before = run_ids(session)
        verify(plain(session.persisted_frame(saved.artifact_id)), mode, profile)
        assert run_ids(session) == before
        if mode == "entity_time" and profile["followup"]:
            member_index = next(
                index
                for index, axis in enumerate(frame.attrs["axes"])
                if axis["role"] == "identity"
            )
            fitted = deviation(frame, "zscore")
            keys = tuple(fitted.loc[fitted.score > 0, f"key_{member_index}"].drop_duplicates())
            assert keys == (("c", 13),)
            selected = mv.coordinates(
                by=(mv.identity(session.catalog.get("entity:sales.order")),),
                keys=tuple((key,) for key in keys),
            )
            following = session.observe(
                {
                    "value": field.sum(
                        time=catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
                    )
                },
                by=mv.product(session.time_scope(start="2026-08-01", end="2026-08-07"), selected),
                where=mv.in_population(selected),
            ).persist()
            followup = plain(following)
            assert followup.key_1.tolist() == [("c", 13)]
            index = profile["profiles"][0]
            expected_value = (
                7
                if index == 0
                else 0.7
                if index == 1
                else Decimal((0, (7,), -int(PROFILES[index].split(",")[1][:-1])))
            )
            assert followup.value.tolist() == [expected_value]
            assert len(run_ids(session)) == len(before) + 1
        state_path.write_text(json.dumps(state, sort_keys=True))
        shutil.rmtree(root / "models")
        for path in root.glob("*.duckdb"):
            path.unlink()
        if (root / "source_files").exists():
            shutil.rmtree(root / "source_files")
    else:
        state = json.loads(state_path.read_text())
        assert not (root / "models").exists() and not (root / "source_files").exists()
        with (
            patch.object(ms, "load", forbidden),
            patch.object(SemanticProject, "load", forbidden),
            patch.object(mv.Session, "catalog", property(forbidden)),
            patch.object(SourceSession, "__init__", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(state["session"])
            before = run_ids(session)
            saved = session.persisted_frame(state["artifact"])
            frame = plain(saved)
            assert snapshot(saved, frame=frame) == state["frame"]
            verify(frame, mode, state["profile"])
            assert run_ids(session) == before
            assert session._runtime.store.resources(session.id) == ()
    return {
        "accepted": phase,
        "pid": os.getpid(),
        "root": str(root),
        "mode": mode,
        "source_offline": phase != "produce",
    }


def subprocess_phases(case: DslCase, mode: str) -> None:
    reports = []
    for phase in ("produce", "offline", "cold"):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.recovery_worker",
                str(case.root),
                phase,
                mode,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            env=dict(os.environ, MARIVO_TELEMETRY="off", PYTHONPATH=str(PROJECT_ROOT)),
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        assert report["accepted"] == phase
        reports.append(report)
    assert len({report["pid"] for report in reports}) == 3


def subprocess_grouped_phases(cases: tuple[tuple[DslCase, str], ...]) -> None:
    """Visit independent projects in one interpreter per recovery phase."""
    targets = json.dumps([[str(case.root), mode] for case, mode in cases])
    # `run()` removes source and semantic files after production, so offline
    # and cold visits exercise the same source-free reopen path here.
    for phase in ("produce", "cold"):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.recovery_worker",
                "--group",
                phase,
                targets,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            env=dict(os.environ, MARIVO_TELEMETRY="off", PYTHONPATH=str(PROJECT_ROOT)),
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports = arr(checked(json.loads(completed.stdout.strip().splitlines()[-1])))
        assert len(reports) == len(cases)
        pids: set[int] = set()
        for (case, mode), item in zip(cases, reports, strict=True):
            report = obj(item)
            assert report["accepted"] == phase
            assert report["root"] == str(case.root)
            assert report["mode"] == mode
            assert report["source_offline"] is (phase != "produce")
            pid = report["pid"]
            assert isinstance(pid, int) and pid != os.getpid()
            pids.add(pid)
        assert len(pids) == 1


if __name__ == "__main__":
    if sys.argv[1] == "--group":
        reports = []
        for item in arr(checked(json.loads(sys.argv[3]))):
            root, mode = arr(item)
            assert isinstance(root, str) and isinstance(mode, str)
            reports.append(run(Path(root), sys.argv[2], mode))
        print(json.dumps(reports))
    else:
        target = Path(sys.argv[1])
        target.mkdir(parents=True, exist_ok=True)
        print(json.dumps(run(target, sys.argv[2], sys.argv[3])))
