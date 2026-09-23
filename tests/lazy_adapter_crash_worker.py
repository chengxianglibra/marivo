"""Fresh processes for local publication interruption and recovery acceptance."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import duckdb

from marivo._compat import Never
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import (
    RunRecord,
    failure_payload,
    receipt_payload,
    run_input_payload,
)
from marivo.analysis.materialization.errors import RecoveryPendingError
from marivo.refs import ref
from tests.lazy_adapter_fixtures import setup_adapter
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_execution_fixtures import make_execution_registry


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("cold recovery or binding hit replayed origin or placement")


def _run_evidence(value: RunRecord) -> dict[str, object]:
    """Project the exact Run envelope through its owning value codecs."""
    return {
        "run_ref": value.run_ref,
        "session_ref": value.session_ref,
        "execution_key_digest": value.execution_key_digest,
        "admitted_at": value.admitted_at,
        "dataset_input": run_input_payload(value.dataset_input),
        "input_artifact_refs": value.input_artifact_refs,
        "lifecycle": value.lifecycle,
        "terminal_at": value.terminal_at,
        "output_artifact_ref": value.output_artifact_ref,
        "failure": None if value.failure is None else failure_payload(value.failure),
    }


def describe(runtime: DatasetRuntime) -> dict[str, object]:
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as db:
        runs = db.execute(
            "SELECT run_ref FROM analysis_action_runs WHERE session_ref=? ORDER BY run_ref",
            (runtime.session_ref,),
        ).fetchall()
    records = [value for row in runs if (value := runtime.store.run(row[0]))]
    artifacts: list[dict[str, object]] = []
    for record in records:
        if record.output_artifact_ref is not None:
            artifact = runtime.store.artifact(record.output_artifact_ref)
            assert artifact is not None
            artifacts.append(
                {
                    "artifact": artifact.artifact_ref,
                    "run": artifact.producing_run_ref,
                    "evidence": asdict(artifact.evidence),
                    "primary": receipt_payload(artifact.descriptor.storage_receipt),
                    "parts": [
                        {"role": part.role, "receipt": receipt_payload(part.storage_receipt)}
                        for part in artifact.descriptor.retained_parts
                    ],
                }
            )
    return {
        "pid": os.getpid(),
        "session": runtime.session_ref,
        "last_run": runtime.last_run_ref,
        "counts": snapshot(runtime),
        "runs": [_run_evidence(value) for value in records],
        "artifacts": artifacts,
        "resources": [asdict(value) for value in runtime.store.resources(runtime.session_ref)],
        "statistics": {
            **asdict(runtime.statistics),
            "local_executions": runtime.statistics.events.get("local_execution_started", 0),
        },
        "versions": {"duckdb": duckdb.__version__},
    }


def write_marker(project: Path, value: dict[str, object]) -> None:
    with (project / "crash.json").open("w") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def run(
    mode: str,
    kind: Literal["engine"],
    project: Path,
    point: str,
    occurrence: int,
) -> dict[str, object]:
    assert kind == "engine"
    if mode == "produce":
        fixture = setup_adapter(project, kind)
        runtime = fixture.runtime
        baseline = fixture.sources.population(ref.entity("sales.customers")).execute()
        initial = describe(runtime)
        initial["baseline"] = baseline.state.artifact_ref.ref
        write_marker(project, initial)
        seen = 0

        def interrupt(name: str) -> None:
            nonlocal seen
            if name == point:
                seen += 1
                if seen == occurrence:
                    value = describe(runtime)
                    value.update(baseline=baseline.state.artifact_ref.ref, point=point)
                    write_marker(project, value)
                    os._exit(73)
            if name == "after_commit" and point == "readback_unavailable":
                raise RuntimeError("acknowledgement unavailable")
            if name == "readback" and point == "readback_unavailable":
                raise OSError("authoritative readback unavailable")

        runtime._hook = interrupt
        logical = fixture.sources.observe(ref.metric("sales.mean_amount"))
        try:
            logical.execute()
        except (RecoveryPendingError, RuntimeError):
            if point != "readback_unavailable":
                raise
            pending_state = describe(runtime)
            pending_state.update(baseline=baseline.state.artifact_ref.ref, point=point)
            write_marker(project, pending_state)
            os._exit(73)
        raise AssertionError("the requested crash point was not reached")
    value: object = json.loads((project / "crash.json").read_text())
    assert isinstance(value, dict)
    session, artifact = str(value["session"]), str(value["baseline"])
    runtime = DatasetRuntime.open(project, session)
    before = describe(runtime)
    registry, sidecar = make_execution_registry(project / "warehouse.duckdb")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    pending = False
    with (
        patch.object(admission, "_build_backend_from_effective", forbidden),
        patch.object(admission, "place", forbidden),
    ):
        try:
            result = sources.population(ref.entity("sales.customers")).execute()
        except RecoveryPendingError:
            pending = True
        else:
            assert result.state.artifact_ref.ref == artifact
    recovered = describe(runtime)
    assert len(runtime.artifact(artifact).to_pandas()) == 4
    report: dict[str, object] = {
        "before": before,
        "recovered": recovered,
        "pending": pending,
        "baseline": artifact,
    }

    if value.get("point") == "after_commit":
        independent = DatasetRuntime.create(project, "independent")
        independent_sources = independent.sources(semantic_registry=registry, sidecar=sidecar)
        assert (
            len(independent_sources.population(ref.entity("sales.customers")).execute().to_pandas())
            == 4
        )
        report["independent"] = describe(independent)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "recover"))
    parser.add_argument("kind", choices=("engine",))
    parser.add_argument("project", type=Path)
    parser.add_argument("--point", default="")
    parser.add_argument("--occurrence", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(run(args.mode, args.kind, args.project, args.point, args.occurrence)))
