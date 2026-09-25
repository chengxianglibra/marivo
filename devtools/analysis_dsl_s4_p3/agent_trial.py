"""Run one fresh external Agent journey against an installed public wheel."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path

QUESTIONS = {
    "j1": (
        "For August 2026, what is total revenue across all customers, what is it by customer "
        "region, and how much revenue in the east region came through each order channel?"
    ),
    "j2": (
        "Which customers had lower revenue in August 2026 than in July 2026? "
        "What was their average per-customer revenue in September 2026, including selected "
        "customers whose September contribution is zero?"
    ),
    "j3": (
        "For August 2026, calculate line-item revenue divided by order count by order "
        "channel and across all channels. Separately calculate the unweighted mean of the "
        "customer-by-channel ratios, and explain the difference."
    ),
    "j4": (
        "For August 2026, calculate the Spearman rank correlation across customers between "
        "revenue and order count. Report the pairing counts and status, then select the "
        "coefficient row if it is negative."
    ),
}


def prompt(journey: str, python: Path) -> str:
    """Provide the question and public discovery route without a solution."""
    return (
        "You are in a standalone governed Marivo project. Use the installed Python at "
        f"{python}. Inspect this project's authored models as needed. Discover the public "
        "analysis API through import marivo; marivo.help() and its linked targets. "
        "Use only public marivo, marivo.semantic, and marivo.analysis APIs. Do not read the "
        "DuckDB file directly, inspect installed package implementation, or read repository "
        "source, tests, scripts, or any answer key. Write a runnable answer.py in this project, "
        "execute it, and report the actual result. Derive reported values and consistency "
        "checks from public results without hard-coding expected totals, counts, members, or "
        "coefficients. Derive explanatory quantities from public results, and verify each "
        "supporting value in your final report against executed output. Qualify general claims "
        "to what the result and contract support. Interpret all reporting periods in UTC. "
        "Consistency checks must compare executed public results, not assert a chosen "
        "fixture value from any period. Generate quantitative prose from measured "
        "variables rather than fixed count words. "
        "If a public call fails, use its normal error feedback to repair your code.\n\n"
        "Business question: " + QUESTIONS[journey]
    )


def _environment(project: Path, python: Path) -> dict[str, str]:
    keep = ("ANTHROPIC_API_KEY", "HOME", "PATH", "TMPDIR", "LANG", "TERM")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    env["MARIVO_PROJECT_ROOT"] = str(project)
    env["MARIVO_TELEMETRY"] = "off"
    return env


def run(journey: str, attempt: str, workspace: Path, evidence: Path, python: Path) -> None:
    """Capture one complete, separately identified Claude CLI attempt."""
    project = (workspace / "agent" / journey).resolve()
    if (project / "answer.py").exists():
        raise RuntimeError(f"Agent project already contains an answer: {project}")
    if not (project / "warehouse.duckdb").is_file():
        raise RuntimeError(f"Missing controlled source: {project}")
    auth = subprocess.run(
        ["claude", "auth", "status"],
        cwd=project,
        env=_environment(project, python),
        capture_output=True,
        text=True,
        check=True,
    )
    if not json.loads(auth.stdout).get("loggedIn"):
        raise RuntimeError("Claude CLI is not authenticated for this trial environment")
    target = evidence.resolve() / "agent" / journey / attempt
    target.mkdir(parents=True, exist_ok=False)
    question = prompt(journey, python.absolute())
    (target / "prompt.txt").write_text(question + "\n")
    session_id = str(uuid.uuid4())
    command = [
        "claude",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-chrome",
        "--permission-mode",
        "auto",
        "--tools",
        "Bash,Read,Write,Edit,Glob,Grep",
        "--session-id",
        session_id,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        question,
    ]
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    with (target / "stderr.txt").open("w") as errors:
        process = subprocess.Popen(
            command,
            cwd=project,
            env=_environment(project, python),
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        )
        assert process.stdout is not None
        with (target / "trace.jsonl").open("w") as trace:
            for line in process.stdout:
                trace.write(line.replace(key, "<redacted-api-key>") if key else line)
                trace.flush()
        returncode = process.wait()
    metadata = {
        "journey": journey,
        "attempt": attempt,
        "session_id": session_id,
        "returncode": returncode,
        "project": str(project),
        "python": str(python.absolute()),
        "command_flags": command[1:-1],
        "answer_exists": (project / "answer.py").is_file(),
    }
    (target / "attempt.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metadata, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journey", choices=tuple(QUESTIONS))
    parser.add_argument("--attempt", default="attempt-01")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    run(args.journey, args.attempt, args.workspace, args.evidence, args.python)


if __name__ == "__main__":
    main()
