"""Three-process Slice 3b acceptance bound to one unchanged source candidate."""

import json
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest

from tests.lazy_execution_fixtures import ORDER_VALUES
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(
    mode: str, kind: str, project: Path, session: str = "", artifact: str = ""
) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_retained_runtime_worker",
            mode,
            kind,
            str(project),
            "--session",
            session,
            "--artifact",
            artifact,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "MARIVO_TELEMETRY": "off"},
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_retained_folds_and_shared_checkpoint_cold_binding(tmp_path: Path, kind: str) -> None:
    candidate = _manifest()
    produced = _run("produce", kind, tmp_path)
    continued = _run(
        "continue", kind, tmp_path, str(produced["session"]), str(produced["artifact"])
    )
    cold = _run("cold", kind, tmp_path, str(produced["session"]), str(produced["artifact"]))
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    assert (
        cold["before"]
        == cold["after"]
        == continued["after"]
        == {
            "analysis_action_runs": 3,
            "analysis_action_run_terminals": 3,
            "dataset_artifacts": 3,
            "dataset_evidence": 3,
            "analysis_action_run_inputs": 2,
            "action_resource_journal": 0,
        }
    )
    outputs, recovered = continued["outputs"], cold["outputs"]
    assert (
        isinstance(outputs, list)
        and isinstance(recovered, list)
        and len(outputs) == len(recovered) == 2
    )
    for current, restored in zip(outputs, recovered, strict=True):
        assert isinstance(current, dict) and isinstance(restored, dict)
        for name in ("artifact", "rows", "columns", "evidence"):
            assert current[name] == restored[name]
        stats = restored["statistics"]
        assert (
            isinstance(stats, dict)
            and stats["worker_pid"] is None
            and stats["primary_queries"] == 0
        )
    if kind == "local":
        assert outputs[0]["rows"][0] == pytest.approx([140, 140 / 3, 50, 140 / 3])
        assert outputs[1]["rows"] == [[100]]
        for output in outputs:
            assert output["statistics"]["primary_queries"] == 0
            handoffs = output["statistics"]["handoffs"]
            assert len(handoffs) >= 3
            assert all(left[1] == right[0] for left, right in pairwise(handoffs))
    else:
        members = produced["members"]
        assert isinstance(members, list)
        ids = {member[0] for member in members}
        values = [row[2] for row in ORDER_VALUES if row[1] in ids and row[2] is not None]
        assert outputs[0]["rows"] == [[sum(values)]]
        assert outputs[1]["rows"][0] == pytest.approx([sum(values) / len(values)])
        for output in outputs:
            stats = output["statistics"]
            assert stats["transferred_rows"] == 1 and stats["worker_pid"] is None
            assert '"customers"' not in json.dumps(stats["statements"])
    assert candidate == _manifest()
    evidence = {
        "schema": "marivo.slice3b.runtime/v1",
        "kind": kind,
        "candidate_before": candidate,
        "candidate_after": _manifest(),
        "producer": produced,
        "continuation": continued,
        "cold": cold,
    }
    destination = tmp_path / f"slice-3b-{kind}-runtime.json"
    destination.write_text(json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False) + "\n")
    retained = os.environ.get("MARIVO_SLICE3B_EVIDENCE_DIR")
    if retained:
        directory = Path(retained)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / destination.name).write_bytes(destination.read_bytes())
