"""Protect the runner's evidence boundary without consuming a model run."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/agent_bench/run.py"


@pytest.fixture
def bench_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "input.txt").write_text("unchanged input", encoding="utf-8")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Perform the supplied task.", encoding="utf-8")
    executable = tmp_path / "codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

args = sys.argv[1:]
project = Path(args[args.index("-C") + 1]) if "-C" in args else Path.cwd()
if "--version" in args:
    print("codex-cli boundary-test")
elif "mcp" in args:
    disabled = 'mcp_servers={"ambient"={enabled=false}}' in args
    print(json.dumps([{"name": "ambient", "enabled": not disabled,
                       "transport": {"token": "must-not-be-recorded"}}]))
elif "prompt-input" in args:
    disabled = any(a.startswith("skills.config=") and "/ambient/SKILL.md" in a for a in args)
    if (project / "leak").exists():
        disabled = False
    text = "Isolated prompt." if disabled else (
        "<skills_instructions>\n### Skill roots\n- `r0` = `/ambient`\n"
        "### Available skills\n- ambient: Test skill. (file: r0/SKILL.md)\n</skills_instructions>"
    )
    print(json.dumps([{"role": "developer", "content": [{"type": "input_text", "text": text}]}]))
elif "models" in args:
    print(json.dumps({"models": [{"slug": "gpt-6-luna"}]}))
elif "exec" in args:
    assert "--no-daemon" in args and "--ignore-user-config" not in args
    assert 'model_reasoning_effort="max"' in args
    (project / "executed").write_text("yes")
    if (project / "router-error").exists():
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        print(stamp + " ERROR codex_core::tools::router: error=apply_patch verification failed: invalid hunk", file=sys.stderr, flush=True)
    events = [
        {"type": "item.started", "item": {"id": "c1", "type": "command_execution"}},
        {"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "exit_code": 0,
                                              "aggregated_output": "computed value: 7"}},
        {"type": "turn.completed", "usage": {"input_tokens": 101, "cached_input_tokens": 21, "output_tokens": 9}},
    ]
    for event in events:
        print(json.dumps(event), flush=True)
    Path(args[args.index("-o") + 1]).write_text("Complete final response.\n" * 100)
    (project / "nested").mkdir()
    (project / "nested" / "result.bin").write_bytes(bytes(range(256)))
    (project / ".marivo").mkdir()
    (project / ".marivo" / "state.json").write_text('{"retained":true}')
    (project / "input.txt").write_text("modified input")
    # EOF arrives before the marker. The runner must reconcile after wait().
    os.close(1)
    time.sleep(0.05)
    (project / "first_result.json").write_text(json.dumps({"epoch": time.time(), "description": "computed value: 7"}))
else:
    raise SystemExit("Unexpected invocation")
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return project, prompt, executable


def invoke(
    project: Path, prompt: Path, executable: Path, output: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--project",
            str(project),
            "--prompt",
            str(prompt),
            "--executable",
            str(executable),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_runner_preserves_native_evidence_and_reconciles_exit_marker(
    bench_workspace: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    project, prompt, executable = bench_workspace
    output = tmp_path / "evidence"
    result = invoke(project, prompt, executable, output)
    assert result.returncode == 0, result.stderr
    record = json.loads((output / "run.json").read_text())
    first = json.loads((output / "first-result.json").read_text())
    assert first["phase"] == "post_exit"
    assert len(first["observation_snapshot"]["started_visible_actions"]) == 1
    assert len(first["reconciled"]["started_visible_actions"]) == 1
    assert first["marker"]["description"] == "computed value: 7"
    assert "independent acceptance" in first["validity"]
    assert record["native_usage"] == [
        {"input_tokens": 101, "cached_input_tokens": 21, "output_tokens": 9}
    ]
    assert record["invalid_jsonl_lines"] == []
    raw = (output / "events.jsonl").read_text().splitlines()
    timed = [json.loads(line) for line in (output / "timed-events.jsonl").read_text().splitlines()]
    assert [json.loads(line) for line in raw] == [event["event"] for event in timed]
    assert [event["line_number"] for event in timed] == [1, 2, 3]
    assert all(event["received_epoch"] >= record["started_epoch"] for event in timed)
    assert (output / "final.md").read_text() == "Complete final response.\n" * 100
    assert (output / "delivery/nested/result.bin").read_bytes() == bytes(range(256))
    assert (output / "delivery/.marivo/state.json").read_text() == '{"retained":true}'
    assert (output / "delivery/input.txt").read_text() == "modified input"
    inventory_text = (output / "mcp-inventory.json").read_text()
    assert "must-not-be-recorded" not in inventory_text
    assert json.loads(inventory_text)["after"] == [{"name": "ambient", "enabled": False}]


def test_runner_rejects_surviving_catalog_before_model_execution(
    bench_workspace: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    project, prompt, executable = bench_workspace
    (project / "leak").touch()
    result = invoke(project, prompt, executable, tmp_path / "evidence")
    assert result.returncode != 0
    assert "catalog remains" in result.stderr
    assert not (project / "executed").exists()


def test_router_failure_keeps_action_count_unverified_without_changing_native_usage(
    bench_workspace: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    project, prompt, executable = bench_workspace
    (project / "router-error").touch()
    output = tmp_path / "evidence"
    result = invoke(project, prompt, executable, output)
    assert result.returncode == 0, result.stderr
    record = json.loads((output / "run.json").read_text())
    failures = json.loads((output / "tool-failures.json").read_text())
    first = json.loads((output / "first-result.json").read_text())
    assert len(record["started_visible_actions"]) == 1
    assert record["action_observability"]["native_started_action_count"] == 1
    assert (
        record["action_observability"]["all_attempts_completeness"]
        == "unverified_pending_reconciliation"
    )
    assert record["action_observability"]["native_count_bound"] == "lower_bound_for_all_attempts"
    assert record["action_observability"]["additional_attempt_count"] is None
    assert record["action_observability"]["independent_reconciliation_required"]
    assert len(failures["records"]) == 1
    failure = failures["records"][0]
    assert failure["category"] == "apply_patch_verification_failed"
    assert failure["stderr_line"] == 1
    assert failure["timestamp"].endswith("Z")
    assert failure["timestamp_epoch"] >= record["started_epoch"]
    assert "unresolved" in failure["native_correspondence"]
    assert first["action_observability"]["independent_reconciliation_required"]
    assert first["router_failure_source_lines_requiring_reconciliation"] == [1]
    assert record["native_usage"] == [
        {"input_tokens": 101, "cached_input_tokens": 21, "output_tokens": 9}
    ]
