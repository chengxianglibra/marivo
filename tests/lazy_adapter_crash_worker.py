"""Fresh processes for sink interruption and conservative request recovery acceptance."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from unittest.mock import patch

import duckdb

from marivo._compat import Never
from marivo.analysis.materialization import admission, object_storage
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunRecord,
    failure_payload,
    receipt_payload,
    run_input_payload,
)
from marivo.analysis.materialization.errors import RecoveryPendingError
from marivo.analysis.materialization.object_termination import OBJECT_REQUEST_CAPABILITY
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import EngineTarget, ObjectTarget, S3Access
from marivo.analysis.observation.sampling import engine_sample
from marivo.refs import ref
from tests.lazy_adapter_fixtures import setup_adapter
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_execution_fixtures import make_execution_registry

if TYPE_CHECKING:
    from botocore.model import OperationModel
    from mypy_boto3_s3 import S3Client

_REQUESTS: dict[str, int] = {}


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
        "statistics": asdict(runtime.statistics),
        "object_requests": dict(_REQUESTS),
        "versions": {"duckdb": duckdb.__version__},
    }


def write_marker(project: Path, value: dict[str, object]) -> None:
    with (project / "crash.json").open("w") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def run(
    mode: str,
    kind: Literal["engine", "object"],
    project: Path,
    point: str,
    occurrence: int,
    sampled: bool,
) -> dict[str, object]:
    original_client = object_storage.client

    def observe_request(model: OperationModel, **kwargs: object) -> None:
        _REQUESTS[model.name] = _REQUESTS.get(model.name, 0) + 1

    @contextmanager
    def traced_client(access: S3Access) -> Iterator[S3Client]:
        with original_client(access) as value:
            value.meta.events.register("before-call.s3", observe_request)
            yield value

    object_storage.client = traced_client
    access = (
        S3Access(
            "fixture",
            os.environ["MARIVO_TEST_S3_ENDPOINT"],
            os.environ["MARIVO_TEST_S3_BUCKET"],
            "minioadmin",
            "minioadmin",
        )
        if kind == "object"
        else None
    )
    if mode == "produce":
        fixture = setup_adapter(project, kind, access=access)
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

        original_discharge = SessionStore.discharge

        def discharge(store: SessionStore, resource: ResourceRecord) -> None:
            if resource.cleanup_capability_id == OBJECT_REQUEST_CAPABILITY:
                interrupt("object_response_before_discharge")
            original_discharge(store, resource)

        runtime._hook = interrupt
        if point in ("proxy_wait", "proxy_timeout"):
            # The parent forwards a real PUT and kills this process only after its
            # withheld remote response has been observed by the test server.
            (project / "proxy-ready").write_text("ready")
        logical = (
            fixture.sources.population(ref.entity("sales.customers")).sample(
                engine_sample(target_rows=2, seed=3)
            )
            if sampled
            else fixture.sources.observe(ref.metric("sales.mean_amount"))
        )
        with patch.object(SessionStore, "discharge", discharge):
            try:
                logical.execute()
            except RecoveryPendingError:
                if point not in ("readback_unavailable", "proxy_timeout"):
                    raise
                pending_state = describe(runtime)
                pending_state.update(baseline=baseline.state.artifact_ref.ref, point=point)
                write_marker(project, pending_state)
                os._exit(73)
        raise AssertionError("the requested crash point was not reached")
    value: object = json.loads((project / "crash.json").read_text())
    assert isinstance(value, dict)
    session, artifact = str(value["session"]), str(value["baseline"])
    runtime = DatasetRuntime.open(
        project, session, object_bindings=() if access is None else (access,)
    )
    before = describe(runtime)
    registry, sidecar = make_execution_registry(project / "warehouse.duckdb")
    runtime.target = (
        EngineTarget(next(iter(registry.datasources)))
        if kind == "engine"
        else ObjectTarget("fixture")
    )
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
    independent = DatasetRuntime.create(
        project, "independent", target=EngineTarget(next(iter(registry.datasources)))
    )
    independent_sources = independent.sources(semantic_registry=registry, sidecar=sidecar)
    assert (
        len(independent_sources.population(ref.entity("sales.customers")).execute().to_pandas())
        == 4
    )
    return {
        "before": before,
        "recovered": recovered,
        "pending": pending,
        "independent": describe(independent),
        "baseline": artifact,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "recover"))
    parser.add_argument("kind", choices=("engine", "object"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--point", default="")
    parser.add_argument("--occurrence", type=int, default=1)
    parser.add_argument("--sampled", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.kind, args.project, args.point, args.occurrence, args.sampled)
        )
    )
