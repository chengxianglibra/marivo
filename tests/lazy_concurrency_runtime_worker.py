"""Fresh-process Session contention with bounded, credential-free evidence."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.store import SessionStore
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry


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


def contend(project: Path, session: str, metric: str, artifact: str) -> dict[str, object]:
    runtime = DatasetRuntime.open(project, session)
    registry, sidecar = make_execution_registry(project / "warehouse.duckdb")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    before = snapshot(runtime)
    retained = runtime.artifact(artifact)
    try:
        sources.observe(ref.metric(metric)).execute()
    except SessionBusyError as error:
        return {
            "pid": os.getpid(),
            "session": error.session_ref,
            "before": before,
            "after": snapshot(runtime),
            "statistics": asdict(runtime.statistics),
            "last_run_ref": runtime.last_run_ref,
            "artifact": retained.state.artifact_ref.ref,
        }
    raise AssertionError("contender was admitted")


def create_race(project: Path) -> dict[str, object]:
    original = SessionStore.session_by_name
    first = True

    def synchronized(store: SessionStore, name: str) -> SessionRecord | None:
        nonlocal first
        result = original(store, name)
        if first:
            first = False
            assert result is None
            print("ready", flush=True)
            assert sys.stdin.readline().strip() == "go"
        return result

    with patch.object(SessionStore, "session_by_name", synchronized):
        try:
            runtime = DatasetRuntime.create(project, "raced")
            return {"pid": os.getpid(), "session": runtime.session_ref, "status": "created"}
        except SessionBusyError as error:
            return {"pid": os.getpid(), "session": error.session_ref, "status": "busy"}


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
