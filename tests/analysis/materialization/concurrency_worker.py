"""Fresh-process Session contention with bounded, credential-free evidence."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import marivo.analysis as mv
from marivo.analysis.errors import SessionAlreadyExistsError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session


def snapshot(runtime: DatasetRuntime) -> dict[str, int]:
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as db:
        return {
            name: int(db.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            for name in (
                "sessions",
                "analysis_action_runs",
                "analysis_action_run_terminals",
                "dataset_artifacts",
                "dataset_evidence",
                "action_resource_journal",
            )
        }


def observation(session: Session, metric: str) -> mv.AnalysisFrame:
    return session.observe(
        {"value": session.catalog.metrics.get(metric)},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"),
            session.members(session.catalog.entities.get("sales.customer")),
        ),
    )


def contend(project: Path, session: str, metric: str, artifact: str) -> dict[str, object]:
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    handle = Session._from_runtime(DatasetRuntime.open(project, session))
    runtime = handle._runtime
    logical = observation(handle, metric).compute()
    before = snapshot(runtime)
    retained = handle.persisted_frame(artifact)
    try:
        logical.persist()
    except SessionBusyError as error:
        return {
            "pid": os.getpid(),
            "session": error.session_ref,
            "before": before,
            "after": snapshot(runtime),
            "artifact": retained.artifact.id,
        }
    raise AssertionError("contender was admitted")


def create_race(project: Path) -> dict[str, object]:
    original = SessionStore.create_session

    def synchronized(
        store: SessionStore,
        name: str,
        *,
        question: str | None = None,
        session_ref: str | None = None,
        report_timezone_name: str = "UTC",
        report_timezone_resolution: Literal["iana", "fixed_offset"] = "iana",
        domains: tuple[str, ...] | None = None,
    ) -> SessionRecord:
        print("ready", flush=True)
        assert sys.stdin.readline().strip() == "go"
        return original(
            store,
            name,
            question=question,
            session_ref=session_ref,
            report_timezone_name=report_timezone_name,
            report_timezone_resolution=report_timezone_resolution,
            domains=domains,
        )

    with patch.object(SessionStore, "create_session", synchronized):
        try:
            runtime = DatasetRuntime.create(project, "raced")
            return {"pid": os.getpid(), "session": runtime.session_ref, "status": "created"}
        except SessionAlreadyExistsError as error:
            return {
                "pid": os.getpid(),
                "session": error._context["session_id"],
                "status": "duplicate",
            }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("contend", "create"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--session", default="")
    parser.add_argument("--metric", default="sales.revenue")
    parser.add_argument("--artifact", default="")
    args = parser.parse_args()
    result = (
        contend(args.project, args.session, args.metric, args.artifact)
        if args.mode == "contend"
        else create_race(args.project)
    )
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
