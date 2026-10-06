"""Fresh-process costs for exact retained baseline continuations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import marivo.analysis as mv  # noqa: E402
from devtools.r96_cost_observer import CostObserver  # noqa: E402
from marivo.analysis.materialization.graph_protocol import descriptor_plan  # noqa: E402
from scripts.r82_deviation_requirements import key_json  # noqa: E402

Mode = Literal["kernel", "exact_hit"]
Baseline = (
    mv.MaterializedNumericRelation
    | mv.MaterializedRolledNumericRelation
    | mv.MaterializedStatisticRelation
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _identity(result: Baseline) -> dict[str, object]:
    assert result._dataset is not None
    artifact = result._dataset.artifact
    plan = descriptor_plan(artifact.descriptor, result._node.definition)
    root = next(
        item
        for item in plan.physical_requirements
        if item.node_id == result._node.definition.identity
    )
    return {
        "artifact_ref": artifact.artifact_ref,
        "run_ref": artifact.producing_run_ref,
        "execution_key_digest": artifact.execution_key_digest,
        "root_route": root.key.route,
        "actual_routes": sorted({item.key.route for item in plan.physical_requirements}),
        "method_bindings": [
            {
                "key": key_json(item.key),
                "route": item.key.route,
                "node": item.node_id,
                "implementation": str(item.implementation.qualification),
            }
            for item in plan.physical_requirements
        ],
        "parts": [part.role for part in artifact.descriptor.parts],
        "question": "Exact original-fact integer total",
    }


def _worker(
    root: Path,
    session_name: str,
    artifact_ref: str,
    backend: str,
    expected_total: int,
    mode: Mode,
) -> dict[str, object]:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    observer = CostObserver(backend)
    record: dict[str, object] = {
        "schema": "marivo.r96.cost-sample.v1",
        "requested_route": "artifact_python",
        "fixed_mode": mode,
        "recovery": "cold",
        "process": {"pid": os.getpid(), "parent_pid": os.getppid()},
        "measurement_boundary": "Fresh process after imports; resume, Artifact restore, fixed execute and receipt reads measured; oracle/identity reads outside measurement",
    }
    began = time.monotonic()
    try:
        with observer:
            session = mv.session.resume(session_name, by="name")
            restored = session.artifact(artifact_ref)
            if not isinstance(
                restored,
                (
                    mv.MaterializedNumericRelation,
                    mv.MaterializedRolledNumericRelation,
                    mv.MaterializedStatisticRelation,
                ),
            ):
                raise ValueError("Cold baseline requires a retained numeric or statistic producer")
            logical = (
                restored.rollup()
                if isinstance(restored, mv.MaterializedStatisticRelation)
                else restored.summarize(mv.sum())
            )
            result = logical.execute()
        record["elapsed_seconds"] = time.monotonic() - began
        observations = observer.snapshot()
        record["observations"] = observations
        assert observations["source_submissions"] == []
        assert observations["source_sessions"] == []
        native = observations["native_submissions"]
        assert isinstance(native, list)
        assert all(isinstance(item, dict) and item["category"] == "store" for item in native), (
            "Cold retained execution submitted non-Store native SQL"
        )
        calls = observations["phase_calls"]
        assert isinstance(calls, dict)
        kernel_calls = calls.get("fixed_kernel", 0)
        if mode == "kernel":
            assert kernel_calls != 0 and observations["storage_writes"], (
                "Cold kernel mode encountered an existing exact hit; select a new retained producer"
            )
        else:
            assert kernel_calls == 0 and observations["storage_writes"] == [], (
                "Cold exact-hit mode must follow the same producer's kernel measurement"
            )
        assert isinstance(result, mv.MaterializedStatisticRelation)
        frame = result.to_pandas()
        actual = frame.value.tolist()
        assert actual == [expected_total]
        assert frame.cell_tag.tolist() == ["defined"]
        assert session._runtime.store.resources(session.id) == ()
        record["producer_identity"] = _identity(restored)
        record["identity"] = _identity(result)
        record["oracle"] = {
            "passed": True,
            "expected": expected_total,
            "expected_digest": _digest([expected_total]),
            "result_digest": _digest(actual),
            "oracle": "Independent exact original-fact integer sum supplied by producer fixture",
            "state_checked": "defined",
            "source_native_submissions": 0,
            "pending_resources": 0,
        }
        record["status"] = "passed"
    except Exception as error:
        record["elapsed_seconds"] = time.monotonic() - began
        record["status"] = "failed"
        record["error_type"] = type(error).__name__
        record["traceback"] = traceback.format_exc()
        try:
            record["observations"] = observer.snapshot()
        except Exception as observation_error:
            record["observation_error_type"] = type(observation_error).__name__
    return record


def cold_sample(
    root: Path,
    session_name: str,
    artifact_ref: str,
    backend: str,
    expected_total: int,
    mode: Mode = "kernel",
) -> dict[str, object]:
    """Measure one retained baseline in a fresh repository-interpreter process.

    Args: root: Existing governed project; session_name: Existing exact name;
        artifact_ref: Real baseline producer; backend: Original backend identity;
        expected_total: Independent exact sum; mode: New kernel or committed hit.
    Returns: JSON-compatible measured or failed sample with observations and oracle.
    Example: ``sample = cold_sample(root, "r96-cost", ref, "duckdb", 42)``.
    Constraints: Call kernel first on a producer not previously continued, then
        exact_hit for that same producer. No source or Semantic loading is allowed.
        Interpreter/import startup is outside the child execution measurement.
    """
    if mode not in ("kernel", "exact_hit"):
        raise ValueError("Unsupported cold measurement mode")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--root",
        str(root.resolve()),
        "--session-name",
        session_name,
        "--artifact-ref",
        artifact_ref,
        "--backend",
        backend,
        "--expected-total",
        str(expected_total),
        "--mode",
        mode,
    ]
    began = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=650)
        if completed.returncode:
            raise RuntimeError(f"Cold process exited {completed.returncode}: {completed.stderr}")
        decoded: object = json.loads(completed.stdout)
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise ValueError("Cold process returned an invalid sample")
        return decoded
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        return {
            "schema": "marivo.r96.cost-sample.v1",
            "requested_route": "artifact_python",
            "fixed_mode": mode,
            "recovery": "cold",
            "status": "failed",
            "stage": "cold_process",
            "elapsed_seconds": time.monotonic() - began,
            "error_type": type(error).__name__,
            "traceback": traceback.format_exc(),
            "elapsed_boundary": "Parent process wall time including startup and failed child",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--expected-total", type=int, required=True)
    parser.add_argument("--mode", choices=("kernel", "exact_hit"), required=True)
    args = parser.parse_args()
    mode: Mode = "kernel" if args.mode == "kernel" else "exact_hit"
    record = _worker(
        args.root, args.session_name, args.artifact_ref, args.backend, args.expected_total, mode
    )
    print(json.dumps(record, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
