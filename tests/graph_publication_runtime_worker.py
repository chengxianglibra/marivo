"""Fresh-process v7 read, contention and abrupt-exit evidence."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.graph_publication import _read_artifact
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from tests.shared_fixtures import graph_count_continuation


def _run() -> None:
    project, reference, mode, point = sys.argv[1:]
    store = SessionStore._graph_store(Path(project), existing_only=True)
    record = _read_artifact(store, reference)
    runtime = DatasetRuntime(store, record.session_ref)
    root = graph_count_continuation(record)

    def event(name: str) -> None:
        if mode == "crash" and name == point:
            os._exit(77)
        if mode == "hold" and name == "graph_admitted":
            print("locked", flush=True)
            assert sys.stdin.readline().strip() == "go"

    runtime._hook = event
    if mode == "reconcile":
        with session_writer_guard(
            store.layout.lock_path(record.session_ref), session_ref=record.session_ref
        ):
            reconcile_session(store, record.session_ref, event=lambda _: None)
        print("reconciled", flush=True)
        return
    try:
        result = runtime._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    except SessionBusyError:
        print("busy", flush=True)
        return
    value = read_result(store.project_root, result.descriptor).primary["value"].to_pylist()
    print(
        json.dumps(
            {"artifact": result.artifact_ref, "run": result.producing_run_ref, "value": value}
        ),
        flush=True,
    )


def main() -> None:
    with (
        patch(
            "marivo.datasource.adapters.SourceSession.__init__",
            side_effect=AssertionError("source forbidden"),
        ),
        patch("ibis.duckdb.connect", side_effect=AssertionError("DuckDB forbidden")),
    ):
        _run()


if __name__ == "__main__":
    main()
