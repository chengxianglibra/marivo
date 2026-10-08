"""Prevent CLI completion and contaminated inputs from becoming qualification."""

import json
import os
import re
from pathlib import Path

import pytest

from devtools.r104_agent_acceptance import (
    Candidate,
    command,
    evaluate,
    prepare_project,
    recover_project,
    trace_status,
    write_json,
)
from tests.support.json import Json


def test_isolation_denies_future_peer_cli_task_outputs(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    own = root / "projects" / "own"
    peer = root / "projects" / "peer"
    offline = root / "offline" / "peer"
    for path in (own, peer, offline):
        path.mkdir(parents=True)
    candidate = Candidate(
        root,
        tmp_path / "repository",
        root / "technical/.venv/bin/python",
        root / "candidate.whl",
        "digest",
        "commit",
    )
    argv = command(candidate, own, "trial-model", "session-id")
    settings = json.loads(argv[argv.index("--settings") + 1])
    denied = settings["sandbox"]["filesystem"]["denyRead"]
    for path in (peer, offline):
        temporary = (
            Path("/private/tmp")
            / f"claude-{os.getuid()}"
            / re.sub(r"[^a-zA-Z0-9_-]", "-", str(path.resolve()))
        )
        assert str(temporary) in denied
    assert str(peer) in denied
    assert str(own) not in denied


@pytest.mark.parametrize("error", [True, False])
def test_trace_needs_actual_identity_and_success(tmp_path: Path, error: bool) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"type":"system","subtype":"init","model":"trial-model"}\n'
        f'{{"type":"result","subtype":"success","is_error":{str(error).lower()}}}\n'
    )
    assert trace_status(trace, 0)[0] == ("failed" if error else "awaiting_evaluation")
    trace.write_text('{"type":"result","subtype":"success","is_error":false}\n')
    assert trace_status(trace, 0)[0] == "failed"


def test_prepare_refuses_answer_material_before_copying(tmp_path: Path) -> None:
    source = tmp_path / "inputs"
    source.mkdir()
    (source / "expected.json").write_text('{"answer":42}')
    destination = tmp_path / "agent"
    with pytest.raises(ValueError, match="only declarations"):
        prepare_project(source, destination, tmp_path / "env/bin/python")
    assert not destination.exists()


def test_recovery_keeps_state_and_exact_ids_without_answers(tmp_path: Path) -> None:
    source = tmp_path / "producer"
    (source / ".marivo").mkdir(parents=True)
    (source / ".marivo" / "retained").write_bytes(b"committed")
    (source / "skills").mkdir()
    (source / "skills" / "SKILL.md").write_text("Packaged workflow")
    (source / "marivo.toml").write_text('[project]\nname="trial"\n')
    (source / "answer.py").write_text("print(42)")
    (source / "source.duckdb").write_bytes(b"source")
    (source / "models").mkdir()
    identities = tmp_path / "ids.json"
    write_json(identities, {"session_id": "session-id", "artifacts": {"total": "artifact-id"}})
    destination = tmp_path / "reader"
    recover_project(source, destination, identities)
    assert {path.name for path in destination.iterdir()} == {
        ".marivo",
        "skills",
        "marivo.toml",
        "identities.json",
    }
    assert (destination / ".marivo" / "retained").read_bytes() == b"committed"
    assert (destination / "identities.json").read_bytes() == identities.read_bytes()


def test_invalid_recovery_ids_leave_no_project(tmp_path: Path) -> None:
    source = tmp_path / "producer"
    (source / ".marivo").mkdir(parents=True)
    identities = tmp_path / "ids.json"
    write_json(identities, {"session_id": "session-id", "artifacts": {}, "answer": 42})
    destination = tmp_path / "reader"
    with pytest.raises(ValueError, match="only exact"):
        recover_project(source, destination, identities)
    assert not destination.exists()


@pytest.mark.parametrize("field", ["trace_sha256", "wheel_sha256", "status"])
def test_assessments_cannot_transfer_between_candidates_or_failures(
    tmp_path: Path, field: str
) -> None:
    receipt = tmp_path / "receipt"
    receipt.mkdir()
    result: dict[str, Json] = {
        "status": "awaiting_evaluation",
        "trace_sha256": "trace-a",
        "wheel_sha256": "wheel-a",
    }
    write_json(receipt / "result.json", result)
    numerical = tmp_path / "numerical.json"
    semantic = tmp_path / "semantic.json"
    assessment: dict[str, Json] = {
        "status": "passed",
        "trace_sha256": "trace-a",
        "wheel_sha256": "wheel-a",
        "assertions": ["independent obligation"],
    }
    write_json(numerical, assessment)
    write_json(semantic, {**assessment, field: "different"})
    with pytest.raises(ValueError):
        evaluate(receipt, numerical, semantic)
    assert not (receipt / "evaluation.json").exists()


def test_process_failure_cannot_be_evaluated_as_passed(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt"
    receipt.mkdir()
    write_json(receipt / "result.json", {"status": "failed"})
    with pytest.raises(ValueError, match="unsuccessful"):
        evaluate(receipt, tmp_path / "numeric.json", tmp_path / "semantic.json")
