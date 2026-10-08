"""Explicit installed-wheel Agent trials; never invoked by the default tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tests.support.json import Json, checked, obj, read

EFFORT = "max"


def write_json(path: Path, value: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_field(value: dict[str, Json], name: str) -> str:
    item = value[name]
    if not isinstance(item, str):
        raise ValueError(f"{name} must be text")
    return item


@dataclass(frozen=True)
class Candidate:
    """The same archive, interpreter and protected inputs for every trial."""

    root: Path
    repository: Path
    interpreter: Path
    wheel: Path
    wheel_sha256: str
    head: str

    @classmethod
    def load(cls, state: Path, repository: Path) -> Candidate:
        data = read(state)
        archives = obj(data["archives"])
        wheel = Path(text_field(data, "wheel"))
        value = cls(
            Path(text_field(data, "root")),
            repository.resolve(),
            Path(text_field(data, "interpreter")),
            wheel,
            text_field(archives, wheel.name),
            text_field(data, "head"),
        )
        if digest(wheel) != value.wheel_sha256:
            raise ValueError("Candidate archive changed; freeze a new candidate")
        if value.root.is_relative_to(value.repository):
            raise ValueError("Agent inputs must live outside the checkout")
        return value


def command(candidate: Candidate, project: Path, model: str | None, session_id: str) -> list[str]:
    """Use only sandboxed shell tools in a fresh, customization-free session."""
    project = project.resolve()
    if project.is_relative_to(candidate.repository):
        raise ValueError("Refusing an Agent project inside the repository")
    denied = [
        candidate.repository,
        Path.home() / ".codex",
        Path.home() / ".agents",
        Path.home() / ".claude",
    ]
    for path in candidate.root.iterdir():
        if path.name == "technical":
            denied.extend(child for child in path.iterdir() if child.name != ".venv")
        elif project.is_relative_to(path) and path.is_dir():
            denied.extend(child for child in path.iterdir() if not project.is_relative_to(child))
        elif path != project:
            denied.append(path)
    temporary = Path("/private/tmp") / f"claude-{os.getuid()}"
    own_temporary = re.sub(r"[^a-zA-Z0-9_-]", "-", str(project))
    if temporary.exists():
        denied.extend(path for path in temporary.iterdir() if path.name != own_temporary)
    for directory in (candidate.root / "projects", candidate.root / "offline"):
        if directory.exists():
            denied.extend(
                temporary / re.sub(r"[^a-zA-Z0-9_-]", "-", str(path.resolve()))
                for path in directory.iterdir()
                if path.resolve() != project
            )
    settings = {
        "autoMemoryEnabled": False,
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {
                "denyRead": [str(path) for path in denied],
            },
            "network": {"allowedDomains": []},
        },
    }
    args = [
        "claude",
        "--safe-mode",
        "--bare",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-chrome",
        "--setting-sources",
        "",
        "--settings",
        json.dumps(settings),
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Bash",
        "--allowedTools",
        "Bash",
        "--effort",
        EFFORT,
        "--session-id",
        session_id,
        "--no-session-persistence",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
    ]
    if model is not None:
        args.extend(("--model", model))
    return args


def environment(project: Path, interpreter: Path) -> dict[str, str]:
    keep = ("ANTHROPIC_API_KEY", "HOME", "PATH", "LANG", "TERM", "TMPDIR")
    env = {key: value for key, value in os.environ.items() if key in keep}
    # Import only existing authentication, never the user's customization settings.
    configured = read(Path.home() / ".claude" / "settings.json")
    auth = obj(configured.get("env", {}))
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        if key not in env and isinstance(auth.get(key), str):
            env[key] = text_field(auth, key)
    env.update(
        MARIVO_PROJECT_ROOT=str(project),
        MARIVO_TELEMETRY="off",
        PYTHONNOUSERSITE="1",
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        CLAUDE_CODE_SUBPROCESS_ENV_SCRUB="1",
    )
    env["PATH"] = str(interpreter.parent) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    return env


def trace_status(trace: Path, exit_code: int) -> tuple[str, str | None]:
    """A final answer cannot discharge a failed turn or missing model identity."""
    try:
        events = [obj(checked(json.loads(line))) for line in trace.read_text().splitlines() if line]
    except (ValueError, TypeError):
        return "failed", "Malformed CLI event trace"
    initial = [
        event
        for event in events
        if event.get("type") == "system" and event.get("subtype") == "init"
    ]
    results = [event for event in events if event.get("type") == "result"]
    if not initial or not initial[0].get("model") or not results:
        return "failed", "Missing actual model identity or final result"
    final = results[-1]
    if exit_code != 0 or final.get("is_error") is not False or final.get("subtype") != "success":
        return "failed", json.dumps(final)
    return "awaiting_evaluation", None


def prepare_project(source: Path, destination: Path, interpreter: Path) -> None:
    """Copy authored inputs and the installed skills without analysis state."""
    if (source / ".marivo").exists():
        raise ValueError("Preparation inputs must not contain prior analysis state")
    for path in source.iterdir():
        if path.name not in ("marivo.toml", "models", "source_files") and path.suffix not in (
            ".parquet",
            ".duckdb",
        ):
            raise ValueError("Preparation inputs may contain only declarations and source data")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Preparation inputs must not contain external symlinks")
        if path.is_file() and path.suffix == ".py" and "models" not in path.parts:
            raise ValueError("Preparation inputs must not contain answer scripts")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__"))
    for path in (destination / "models").rglob("*.py"):
        path.write_text(path.read_text().replace(str(source), str(destination)))
    packages = interpreter.parent.parent / "lib" / "python3.12" / "site-packages"
    shutil.copytree(packages / "marivo" / "skills", destination / "skills")


def run(
    candidate: Candidate,
    project: Path,
    prompt: Path,
    receipt: Path,
    *,
    timeout: int,
    model: str | None = None,
) -> None:
    """Run one fresh CLI session and preserve unsuccessful attempts unchanged."""
    receipt.mkdir(parents=True, exist_ok=False)
    session_id = str(uuid.uuid4())
    args = command(candidate, project, model, session_id)
    env = environment(project, candidate.interpreter)
    question = prompt.read_text()
    (receipt / "prompt.txt").write_text(question)
    visible: dict[str, Json] = {
        str(path.relative_to(project)): digest(path)
        for path in project.rglob("*")
        if path.is_file()
    }
    metadata: dict[str, Json] = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "argv": list(args),
        "cwd": str(project),
        "requested_model": model,
        "session_id": session_id,
        "reasoning_effort": EFFORT,
        "head": candidate.head,
        "wheel_sha256": candidate.wheel_sha256,
        "prompt_sha256": digest(prompt),
        "visible_files": visible,
        "environment_keys": checked(sorted(env)),
        "customizations": "disabled",
    }
    write_json(receipt / "invocation.json", metadata)
    timed_out = False
    with (
        (receipt / "trace.jsonl").open("w") as output,
        (receipt / "stderr.txt").open("w") as errors,
    ):
        process = subprocess.Popen(
            args,
            cwd=project,
            env=env,
            start_new_session=True,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=errors,
            text=True,
        )
        try:
            process.communicate(question, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    key = env.get("ANTHROPIC_API_KEY")
    if key:
        for name in ("trace.jsonl", "stderr.txt"):
            path = receipt / name
            path.write_text(path.read_text().replace(key, "<redacted-api-key>"))
    events = []
    response_models: set[str] = set()
    for line in (receipt / "trace.jsonl").read_text().splitlines():
        try:
            event = obj(checked(json.loads(line)))
        except (ValueError, TypeError):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            events.append(event)
        message = event.get("message")
        if event.get("type") == "assistant" and isinstance(message, dict):
            response_model = message.get("model")
            if isinstance(response_model, str):
                response_models.add(response_model)
    metadata["actual_init"] = events[0] if events else None
    metadata["response_models"] = checked(sorted(response_models))
    exit_code = process.returncode
    status, reason = trace_status(receipt / "trace.jsonl", exit_code)
    if events and model is not None and str(events[0].get("model")).casefold() != model.casefold():
        status, reason = "failed", "Actual model differs from the frozen requested model"
    if timed_out:
        status, reason = "blocked", "External runner deadline expired"
    metadata.update(
        finished_utc=datetime.now(timezone.utc).isoformat(),
        exit_code=exit_code,
        status=status,
        reason=reason,
        trace_sha256=digest(receipt / "trace.jsonl"),
        stderr_sha256=digest(receipt / "stderr.txt"),
    )
    shutil.copytree(project, receipt / "final-project")
    metadata["final_files"] = {
        str(path.relative_to(project)): digest(path)
        for path in project.rglob("*")
        if path.is_file()
    }
    write_json(receipt / "result.json", metadata)


def recover_project(source: Path, destination: Path, identities: Path) -> None:
    """Retain committed state and exact identities, never a solution script."""
    if not (source / ".marivo").is_dir():
        raise ValueError("No committed Marivo state to recover")
    references = read(identities)
    if set(references) != {"session_id", "artifacts"}:
        raise ValueError("Recovery inputs may contain only exact Session and Artifact identities")
    if not isinstance(references["session_id"], str) or not isinstance(
        references["artifacts"], dict
    ):
        raise ValueError("Recovery identities have the wrong shape")
    if not all(isinstance(value, str) for value in references["artifacts"].values()):
        raise ValueError("Artifact references must be exact text identities")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / ".marivo", destination / ".marivo")
    for name in ("marivo.toml", "skills"):
        path = source / name
        if path.is_dir():
            shutil.copytree(path, destination / name)
        elif path.is_file():
            shutil.copy2(path, destination / name)
    write_json(destination / "identities.json", references)


def evaluate(receipt: Path, numerical: Path, semantic: Path) -> None:
    """Require separately attributed numeric and semantic assessments."""
    result = read(receipt / "result.json")
    if result["status"] != "awaiting_evaluation":
        raise ValueError("An unsuccessful Agent turn cannot pass evaluation")
    for name, path in (("numerical", numerical), ("semantic", semantic)):
        assessment = read(path)
        if assessment.get("trace_sha256") != result["trace_sha256"]:
            raise ValueError(f"{name} assessment belongs to another trace")
        if assessment.get("wheel_sha256") != result["wheel_sha256"]:
            raise ValueError(f"{name} assessment belongs to another candidate")
        if assessment.get("status") != "passed" or not assessment.get("assertions"):
            raise ValueError(f"{name} obligations remain incomplete")
        shutil.copy2(path, receipt / f"{name}.json")
    result["status"] = "passed"
    result["numerical_sha256"] = digest(numerical)
    result["semantic_sha256"] = digest(semantic)
    write_json(receipt / "evaluation.json", result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--source", type=Path, required=True)
    preparation.add_argument("--destination", type=Path, required=True)
    preparation.add_argument("--interpreter", type=Path, required=True)
    trial = commands.add_parser("run")
    trial.add_argument("--state", type=Path, required=True)
    trial.add_argument("--repository", type=Path, required=True)
    trial.add_argument("--project", type=Path, required=True)
    trial.add_argument("--prompt", type=Path, required=True)
    trial.add_argument("--receipt", type=Path, required=True)
    trial.add_argument("--model", required=True)
    trial.add_argument("--timeout", type=int, default=2700)
    recovery = commands.add_parser("recover-project")
    recovery.add_argument("--source", type=Path, required=True)
    recovery.add_argument("--destination", type=Path, required=True)
    recovery.add_argument("--identities", type=Path, required=True)
    grading = commands.add_parser("evaluate")
    grading.add_argument("--receipt", type=Path, required=True)
    grading.add_argument("--numerical", type=Path, required=True)
    grading.add_argument("--semantic", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare_project(args.source, args.destination, args.interpreter)
    elif args.operation == "run":
        run(
            Candidate.load(args.state, args.repository),
            args.project,
            args.prompt,
            args.receipt,
            timeout=args.timeout,
            model=args.model,
        )
    elif args.operation == "recover-project":
        recover_project(args.source, args.destination, args.identities)
    else:
        evaluate(args.receipt, args.numerical, args.semantic)


if __name__ == "__main__":
    main()
