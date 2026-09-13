"""Fresh-process worker crash, orphan, and recovery evidence through the Runtime."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.materialization import admission, local_worker
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import RecoveryPendingError
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.worker_lifetime import (
    WorkerReservation,
    acquire_worker_lifetime,
)
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_local_fixtures import REVENUE, pandas_methods, setup_local
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions

CRASH_EXIT = 73


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def run(
    mode: str, project: Path, session: str, artifact: str, point: str, socket_path: str
) -> None:
    if mode == "produce":
        runtime, sources, _ = setup_local(project)
        result = sources.observe(REVENUE).execute()
        record = runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None
        _emit(
            {
                "pid": os.getpid(),
                "session": runtime.session_ref,
                "artifact": record.artifact_ref,
                "rows": len(result.to_pandas()),
                "evidence": record_evidence(record),
                "snapshot": snapshot(runtime),
                "statistics": statistics(runtime),
                "versions": versions(),
            }
        )
        return
    if mode == "recover":
        pending = False
        runtime = DatasetRuntime.open(project, session)
        try:
            with session_writer_guard(runtime.store.layout.lock_path(session), session_ref=session):
                reconcile_session(runtime.store, session, event=runtime._event)
        except RecoveryPendingError:
            pending = True
        retained = runtime.artifact(artifact)
        assert isinstance(retained, MaterializedMetricDataset)
        record = runtime.store.artifact(artifact)
        assert record is not None
        _emit(
            {
                "pid": os.getpid(),
                "pending": pending,
                "snapshot": snapshot(runtime),
                "evidence": record_evidence(record),
                "rows": len(retained.to_pandas()),
                "statistics": statistics(runtime),
            }
        )
        return
    if mode == "independent":
        runtime = DatasetRuntime.create(project, "independent")
        registry, sidecar = make_execution_registry(project / "warehouse.duckdb")
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).execute()
        _emit(
            {
                "session": runtime.session_ref,
                "statistics": statistics(runtime),
            }
        )
        return

    def event(at: str) -> None:
        if at == point:
            _emit(
                {
                    "pid": os.getpid(),
                    "point": point,
                    "run": runtime.last_run_ref,
                    "snapshot": snapshot(runtime),
                }
            )
            os._exit(CRASH_EXIT)

    runtime = DatasetRuntime.open(project, session, event=event)
    runtime.local_policy = replace(runtime.local_policy, deadline_seconds=120)
    retained = runtime.artifact(artifact)
    assert isinstance(retained, MaterializedMetricDataset)
    logical = retained.where(gt(retained.fields.metric(REVENUE), 0))
    if point == "lifetime_created":

        def create(reservation: WorkerReservation) -> int:
            acquire_worker_lifetime(reservation)
            event(point)
            raise AssertionError("crash must exit")

        with patch.object(local_worker, "acquire_worker_lifetime", create):
            logical.execute()
    elif point == "orphan":
        # The socket is an explicit cross-process calculation barrier. The
        # inherited lifetime remains open through subsequent process exit.
        worker_code = f"""
import json, os, socket
from marivo.analysis.materialization import local_worker
peer = socket.socket(socket.AF_UNIX)
peer.connect({socket_path!r})
original = local_worker.execute_retained_suffix
def blocked(*args, **kwargs):
    peer.sendall((json.dumps({{"pid": os.getpid()}}) + "\\n").encode())
    assert peer.recv(1) == b"x"
    return original(*args, **kwargs)
local_worker.execute_retained_suffix = blocked
try:
    local_worker.worker_entry()
finally:
    peer.close()
"""
        with patch.object(
            admission, "supervise", partial(local_worker.supervise, worker_code=worker_code)
        ):
            logical.execute()
    else:
        logical.execute()
    raise AssertionError("the selected crash boundary was not reached")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "crash", "recover", "independent"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--session", default="")
    parser.add_argument("--artifact", default="")
    parser.add_argument("--point", default="")
    parser.add_argument("--socket", default="")
    args = parser.parse_args()
    with pandas_methods("metric.where"):
        run(args.mode, args.project, args.session, args.artifact, args.point, args.socket)
