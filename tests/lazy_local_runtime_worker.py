"""Fresh interpreter acceptance of source-free producing local continuations."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import ExitStack, redirect_stdout
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from ibis.backends.duckdb import Backend

from marivo.analysis.compiler.placement import place
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.local_worker import supervise
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from tests.lazy_local_fixtures import COUNT, REVENUE, setup_local
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions

# Instrument the actual production worker entry, including its PyArrow reads.
# Any forbidden attempt makes the producing journey fail; no alternate worker method exists.
_GUARDED_WORKER = """
import duckdb
from unittest.mock import patch
from contextlib import ExitStack
from ibis.backends.duckdb import Backend
import marivo.analysis.materialization.admission as admission
from marivo.analysis.materialization.local_worker import worker_entry
def forbidden(*args, **kwargs):
    raise AssertionError('forbidden source access in local worker')
with ExitStack() as stack:
    stack.enter_context(patch.object(duckdb, "connect", forbidden))
    for name in ('connect','raw_sql','table','to_pyarrow','to_pyarrow_batches','read_parquet'):
        stack.enter_context(patch.object(Backend, name, forbidden))
    stack.enter_context(patch.object(admission, '_build_backend_from_effective', forbidden))
    stack.enter_context(patch.object(admission, '_effective_kwargs', forbidden))
    worker_entry()
"""


def _rows(frame: pd.DataFrame) -> list[list[object]]:
    return [
        [
            None if value is pd.NA else list(value) if isinstance(value, tuple) else value
            for value in row
        ]
        for row in frame.itertuples(index=False, name=None)
    ]


def run(mode: str, project: Path, session: str, artifact: str) -> dict[str, object]:
    if mode == "produce":
        runtime, sources, _ = setup_local(project)
        result = sources.observe([REVENUE, COUNT]).execute()
        return {
            "pid": os.getpid(),
            "session": runtime.session_ref,
            "artifact": result.state.artifact_ref.ref,
            "snapshot": snapshot(runtime),
            "statistics": statistics(runtime),
            "versions": versions(),
        }
    runtime = DatasetRuntime.open(project, session)
    before = snapshot(runtime)
    forbidden_attempts: list[str] = []
    construction_attempts: list[str] = []
    active = [False]

    def audit(event: str, args: tuple[object, ...]) -> None:
        if active[0] and (
            event == "open" or event.startswith(("os.", "sqlite3.", "socket.", "subprocess."))
        ):
            construction_attempts.append(event)
            raise AssertionError("Construction attempted I/O")

    sys.addaudithook(audit)

    def forbidden(*args: object, **kwargs: object) -> None:
        forbidden_attempts.append("source")
        raise AssertionError("Source access is forbidden after the Artifact frontier")

    with ExitStack() as stack:
        for name in (
            "_build_backend_from_effective",
            "_effective_kwargs",
            "require_profile_for_backend_type",
            "compile_dataset",
        ):
            stack.enter_context(patch.object(admission, name, forbidden))
        for name in (
            "connect",
            "raw_sql",
            "table",
            "to_pyarrow",
            "to_pyarrow_batches",
            "read_parquet",
        ):
            stack.enter_context(patch.object(Backend, name, forbidden))
        stack.enter_context(
            patch.object(admission, "supervise", partial(supervise, worker_code=_GUARDED_WORKER))
        )
        retained = runtime.artifact(artifact)
        assert isinstance(retained, MaterializedMetricDataset)
        assert snapshot(runtime) == before
        # Resolve the lazily imported ordering module before auditing construction.
        retained.rank(retained.fields.metric(REVENUE))
        active[0] = True
        try:
            filtered = retained.where(gt(retained.fields.metric(REVENUE), 5))
            ranked = filtered.rank(filtered.fields.metric(REVENUE))
            logical = ranked.limit(3).metric(REVENUE)
            assert len(place(logical).local_steps) == 4
        finally:
            active[0] = False
        result = logical.execute()
        stats = {
            **statistics(runtime),
            "handoffs": runtime.statistics.local_handoffs,
            "worker_pid": runtime.statistics.worker_pid,
            "worker_peak_rss": runtime.statistics.worker_peak_rss,
        }
        record = runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None
        produced_run = runtime.store.run(record.producing_run_ref)
        assert produced_run is not None and produced_run.input_artifact_refs == (artifact,)
        frame = result.to_pandas()
        output = io.StringIO()
        with redirect_stdout(output):
            result.show()
        after = snapshot(runtime)
        assert logical.execute().state.artifact_ref == result.state.artifact_ref
        assert snapshot(runtime) == after
        assert runtime.statistics.primary_queries == 0 and runtime.statistics.worker_pid is None
    assert forbidden_attempts == []
    return {
        "pid": os.getpid(),
        "session": session,
        "artifact": result.state.artifact_ref.ref,
        "rows": _rows(frame),
        "columns": list(frame.columns),
        "show": output.getvalue(),
        "evidence": record_evidence(record),
        "before": before,
        "after": after,
        "statistics": stats,
        "forbidden_attempts": forbidden_attempts,
        "construction_io_attempts": construction_attempts,
        "versions": versions(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--session", default="")
    parser.add_argument("--artifact", default="")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.project, args.session, args.artifact),
            sort_keys=True,
            allow_nan=False,
        )
    )
