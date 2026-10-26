"""Run a fresh Codex CLI benchmark with verified ambient-extension isolation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
IGNORED_DIRECTORIES = frozenset({".git", ".venv", "__pycache__"})
DISABLED_FEATURES = (
    "plugins",
    "remote_plugin",
    "apps",
    "hooks",
    "memories",
    "multi_agent",
    "skill_search",
    "skill_mcp_dependency_install",
    "recommended_plugins",
    "workspace_dependencies",
    "computer_use",
    "browser_use",
    "in_app_browser",
)
NON_ACTION_ITEMS = frozenset({"agent_message", "reasoning", "todo_list"})


@dataclass(frozen=True)
class FileRecord:
    sha256: str | None
    size: int
    symlink: str | None


@dataclass(frozen=True)
class RouterFailure:
    stderr_line: int
    timestamp: str
    timestamp_epoch: float | None
    category: str
    native_correspondence: str = "unresolved; independent reconciliation required"


@dataclass(frozen=True)
class Options:
    executable: Path
    project: Path
    prompt: Path
    output: Path
    model: str = "gpt-6-luna"
    reasoning: str = "max"
    sandbox: str = "danger-full-access"
    skills: tuple[Path, ...] = ()
    marker: str = "first_result.json"


def checked_json(value: object) -> Json:
    """Keep arbitrary native JSON precise without leaking untyped values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [checked_json(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {str(key): checked_json(item) for key, item in value.items()}
    raise ValueError("Expected JSON-compatible content")


def parse_json(content: str) -> Json:
    value: object = json.loads(content)
    return checked_json(value)


def object_json(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def router_failures(path: Path) -> list[RouterFailure]:
    """Extract router error records without copying tool arguments or guessing IDs."""
    failures: list[RouterFailure] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
        match = re.match(r"^(\S+)\s+ERROR\s+codex_core::tools::router:\s*(.*)$", clean)
        if match is None:
            continue
        timestamp, message = match.groups()
        epoch: float | None = None
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                epoch = parsed.timestamp()
        category = (
            "apply_patch_verification_failed"
            if "apply_patch verification failed" in message
            else "router_error"
        )
        failures.append(RouterFailure(number, timestamp, epoch, category))
    return failures


def action_observability(
    native_started_count: int, failures: list[RouterFailure]
) -> dict[str, Json]:
    return {
        "native_started_action_count": native_started_count,
        "count_scope": "emitted native item.started action records only",
        "all_attempts_completeness": "unverified_pending_reconciliation"
        if failures
        else "native_stream_only",
        "native_count_bound": "lower_bound_for_all_attempts" if failures else "native_stream_only",
        "router_failure_record_count": len(failures),
        "independent_reconciliation_required": bool(failures),
        "additional_attempt_count": None,
        "record_unit": "stderr router error record; not a deduplicated attempt or model turn",
    }


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def inventory(project: Path) -> dict[str, FileRecord]:
    records: dict[str, FileRecord] = {}
    for directory, directories, filenames in os.walk(project, followlinks=False):
        root = Path(directory)
        directories[:] = sorted(set(directories) - IGNORED_DIRECTORIES)
        for name in sorted([*filenames, *[d for d in directories if (root / d).is_symlink()]]):
            path = root / name
            relative = path.relative_to(project).as_posix()
            if path.is_symlink():
                records[relative] = FileRecord(None, path.lstat().st_size, str(path.readlink()))
            elif path.is_file():
                records[relative] = FileRecord(digest(path), path.stat().st_size, None)
    return records


def capture(command: list[str], project: Path) -> str:
    result = subprocess.run(command, cwd=project, capture_output=True, text=True, check=False)
    if result.returncode:
        # Config/tool output can contain credentials. Do not echo arbitrary stderr.
        raise RuntimeError(f"Codex preflight failed ({result.returncode}): {command[-2:]}")
    return result.stdout


def base_command(options: Options) -> list[str]:
    command = [str(options.executable), "--no-daemon", "-C", str(options.project)]
    for feature in DISABLED_FEATURES:
        command.extend(["--disable", feature])
    command.extend(["--enable", "skip_host_skill_discovery"])
    for override in (
        'approval_policy="never"',
        f"sandbox_mode={json.dumps(options.sandbox)}",
        f"model={json.dumps(options.model)}",
        f"model_reasoning_effort={json.dumps(options.reasoning)}",
        'service_tier="default"',
        'web_search="disabled"',
        'developer_instructions=""',
        "notify=[]",
        "skills.config=[]",
    ):
        command.extend(["-c", override])
    return command


def servers(command: list[str], project: Path) -> list[dict[str, Json]]:
    value = parse_json(capture([*command, "mcp", "list", "--json"], project))
    if not isinstance(value, list):
        raise ValueError("Codex MCP inventory was not a JSON array")
    # Never retain transports, headers, tokens, or environment values.
    result: list[dict[str, Json]] = []
    for item in value:
        record = object_json(item)
        name, enabled = record.get("name"), record.get("enabled")
        if not isinstance(name, str) or not isinstance(enabled, bool):
            raise ValueError("Codex MCP inventory omitted name/enabled")
        result.append({"name": name, "enabled": enabled})
    return result


def prompt_texts(value: Json) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Codex prompt inventory was not a JSON array")
    texts: list[str] = []
    for message in value:
        content = object_json(message).get("content")
        if isinstance(content, list):
            for part in content:
                text = object_json(part).get("text")
                if isinstance(text, str):
                    texts.append(text)
    return texts


def skill_paths(texts: list[str]) -> tuple[str, ...]:
    text = "\n".join(part for part in texts if "<skills_instructions>" in part)
    roots = dict(re.findall(r"- `(r\d+)` = `([^`]+)`", text))
    paths: set[str] = set()
    for path in re.findall(r"\(file: ([^\n)]+)\)", text):
        alias, separator, relative = path.partition("/")
        paths.add(str(Path(roots[alias]) / relative) if alias in roots and separator else path)
    if "<skills_instructions>" in text and not paths:
        raise ValueError("Unrecognized skills catalog; isolation cannot be established")
    return tuple(sorted(paths))


def verify_prompt_inventory(value: Json) -> None:
    text = "\n".join(prompt_texts(value))
    if any(
        marker in text
        for marker in (
            "<skills_instructions>",
            "<recommended_plugins>",
            "Deferred tool namespaces:",
        )
    ):
        raise ValueError("Ambient skill, plugin, or deferred-tool catalog remains in prompt")


def isolated_command(options: Options) -> list[str]:
    command = base_command(options)
    initial_servers = servers(command, options.project)
    # Quoted dotted CLI keys are literal in this version, so use a TOML value.
    settings = ",".join(
        f"{json.dumps(server['name'])}={{enabled=false}}" for server in initial_servers
    )
    command.extend(["-c", f"mcp_servers={{{settings}}}"])
    final_servers = servers(command, options.project)
    if any(server["enabled"] for server in final_servers):
        raise ValueError("Ambient MCP server remains enabled")
    save_json(
        options.output / "mcp-inventory.json", {"before": initial_servers, "after": final_servers}
    )

    probe = ["debug", "prompt-input", "Ambient-extension isolation inventory."]
    initial = parse_json(capture([*command, *probe], options.project))
    paths = skill_paths(prompt_texts(initial))
    # This installed CLI matches SKILL.md paths; folder paths do not disable skills.
    settings = ",".join(f"{{path={json.dumps(path)},enabled=false}}" for path in paths)
    command.extend(["-c", f"skills.config=[{settings}]"])
    final = parse_json(capture([*command, *probe], options.project))
    verify_prompt_inventory(final)
    save_json(options.output / "isolation-prompt-input.json", final)
    normalized = {
        "disabled_features": DISABLED_FEATURES,
        "skip_host_skill_discovery": True,
        "disabled_skill_paths": paths,
        "mcp_servers": sorted(final_servers, key=lambda server: str(server["name"])),
        "ambient_catalogs_exposed": False,
    }
    save_json(
        options.output / "isolation.json",
        {
            "disabled_skill_paths": paths,
            "command": command,
            "normalized_inventory": normalized,
            "normalized_inventory_sha256": hashlib.sha256(
                json.dumps(normalized, sort_keys=True).encode()
            ).hexdigest(),
        },
    )
    return command


def measurement_instructions(options: Options) -> str:
    skills = "\n".join(
        f"Read this explicitly supplied skill file: {path}" for path in options.skills
    )
    return (
        f"\n\n{skills}\n\nBenchmark measurement instructions:\n"
        "Use standalone scripts so execution boundaries remain observable. Immediately after "
        "the first successful computation returns actual business values relevant to the task, "
        f"show those values and write {options.marker} once as a JSON object with epoch "
        "(time.time()) and description (what was computed). Do not mark pending computations, "
        "catalogs, metadata, readiness, or environment certification. Do not overwrite the marker. "
        "Keep all requested deliverables and reproducible code in the project directory. "
        "Do not infer model-internal turns. Marker correctness is independently assessed.\n"
    )


def preserve_delivery(options: Options, before: dict[str, FileRecord]) -> None:
    after = inventory(options.project)
    changed = sorted(name for name, record in after.items() if before.get(name) != record)
    delivery = options.output / "delivery"
    delivery.mkdir()
    for name in changed:
        source, destination = options.project / name, delivery / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            destination.symlink_to(source.readlink())
        else:
            shutil.copy2(source, destination)
    save_json(
        options.output / "delivery-manifest.json",
        {
            "before": {name: asdict(record) for name, record in before.items()},
            "after": {name: asdict(record) for name, record in after.items()},
            "preserved": changed,
            "deleted": sorted(set(before) - set(after)),
            "excluded_directory_names": sorted(IGNORED_DIRECTORIES),
        },
    )


def run(options: Options) -> int:
    if options.output.is_relative_to(options.project):
        raise ValueError("Output must be outside the project directory")
    options.output.mkdir(parents=True, exist_ok=False)
    marker = options.project / options.marker
    if marker.exists():
        raise ValueError("First-result marker already exists; use a fresh project")
    before = inventory(options.project)
    command = isolated_command(options)
    prompt = options.prompt.read_text(encoding="utf-8") + measurement_instructions(options)
    (options.output / "prompt.txt").write_text(prompt, encoding="utf-8")
    actual_prompt = parse_json(
        capture([*command, "debug", "prompt-input", prompt], options.project)
    )
    save_json(options.output / "prompt-input.json", actual_prompt)
    verify_prompt_inventory(actual_prompt)
    version = capture([str(options.executable), "--version"], options.project).strip()
    model_catalog = capture([*command, "debug", "models"], options.project)
    (options.output / "model-catalog.json").write_text(model_catalog, encoding="utf-8")
    cli_sha256 = digest(options.executable)
    launcher_sha256 = digest(Path(__file__))
    skill_hashes = {str(path): digest(path) for path in options.skills}
    command.extend(
        [
            "exec",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "--color",
            "never",
            "-o",
            str(options.output / "final.md"),
            "-",
        ]
    )
    started = time.time()
    clock_start = time.monotonic()
    actions: list[dict[str, Json]] = []
    completed: list[dict[str, Json]] = []
    usage: list[dict[str, Json]] = []
    parse_errors: list[int] = []
    first: dict[str, Json] | None = None

    def observe_marker(phase: str) -> None:
        nonlocal first
        if first is not None or not marker.is_file():
            return
        try:
            value = object_json(parse_json(marker.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, ValueError):
            return
        first = {
            "marker": value,
            "observed_epoch": time.time(),
            "elapsed_seconds": time.monotonic() - clock_start,
            "phase": phase,
            "observation_snapshot": {
                "started_visible_actions": list(actions),
                "completed_visible_actions": list(completed),
            },
            "validity": "unverified; independent acceptance must confirm actual business output",
        }
        save_json(options.output / "first-result.json", first)

    status = -1
    with (
        (options.output / "events.jsonl").open("wb") as raw,
        (options.output / "timed-events.jsonl").open("w", encoding="utf-8") as timed,
        (options.output / "stderr.log").open("wb") as errors,
        (options.output / "prompt.txt").open("rb") as prompt_input,
    ):
        process = subprocess.Popen(
            command,
            cwd=options.project,
            stdin=prompt_input,
            stdout=subprocess.PIPE,
            stderr=errors,
            bufsize=0,
        )
        assert process.stdout is not None
        line_number = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                for key, _ in selector.select(timeout=0.05):
                    line = process.stdout.readline()
                    if not line:
                        selector.unregister(key.fileobj)
                        continue
                    received = time.time()
                    line_number += 1
                    raw.write(line)
                    raw.flush()
                    try:
                        event = object_json(parse_json(line.decode("utf-8")))
                    except (UnicodeError, ValueError):
                        parse_errors.append(line_number)
                        continue
                    timed.write(
                        json.dumps(
                            {
                                "received_epoch": received,
                                "elapsed_seconds": time.monotonic() - clock_start,
                                "line_number": line_number,
                                "event": event,
                            }
                        )
                        + "\n"
                    )
                    timed.flush()
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") not in NON_ACTION_ITEMS:
                        action = {
                            "id": item.get("id"),
                            "type": item.get("type"),
                            "received_epoch": received,
                        }
                        if event.get("type") == "item.started":
                            actions.append(action)
                        elif event.get("type") == "item.completed":
                            completed.append(action)
                    if event.get("type") == "turn.completed" and isinstance(
                        event.get("usage"), dict
                    ):
                        usage.append(object_json(event["usage"]))
                if selector.get_map():
                    observe_marker("running")
            status = process.wait()
        observe_marker("post_exit")
    preserve_delivery(options, before)
    failures = router_failures(options.output / "stderr.log")
    save_json(
        options.output / "tool-failures.json",
        {
            "source": "stderr.log",
            "records": [asdict(failure) for failure in failures],
            "record_unit": "stderr router error record; may overlap native events",
            "independent_reconciliation_required": bool(failures),
        },
    )
    if first is not None:
        marker_epoch = object_json(first["marker"]).get("epoch")
        if (
            isinstance(marker_epoch, (int, float))
            and not isinstance(marker_epoch, bool)
            and started <= marker_epoch <= time.time()
        ):
            first["reconciled"] = {
                "basis": "controller event receipt time <= self-reported marker epoch; not model-internal timing",
                "started_visible_actions": [
                    action
                    for action in actions
                    if isinstance(action["received_epoch"], (int, float))
                    and action["received_epoch"] <= marker_epoch
                ],
                "completed_visible_actions": [
                    action
                    for action in completed
                    if isinstance(action["received_epoch"], (int, float))
                    and action["received_epoch"] <= marker_epoch
                ],
            }
            before_marker = [
                failure
                for failure in failures
                if failure.timestamp_epoch is None or failure.timestamp_epoch <= marker_epoch
            ]
            reconciled = object_json(first["reconciled"])
            started_before_marker = reconciled["started_visible_actions"]
            assert isinstance(started_before_marker, list)
            first["action_observability"] = action_observability(
                len(started_before_marker), before_marker
            )
            first["router_failure_source_lines_requiring_reconciliation"] = [
                failure.stderr_line for failure in before_marker
            ]
        else:
            first["reconciled"] = {
                "error": "marker epoch is missing, nonnumeric, or outside the run"
            }
        save_json(options.output / "first-result.json", first)
    save_json(
        options.output / "run.json",
        {
            "schema": "marivo.agent-bench.v1",
            "command": command,
            "returncode": status,
            "started_epoch": started,
            "started_utc": datetime.fromtimestamp(started, timezone.utc).isoformat(),
            "finished_epoch": time.time(),
            "elapsed_seconds": time.monotonic() - clock_start,
            "first_result_observed": first is not None,
            "marker_semantics": "self-reported; independent acceptance required",
            "started_visible_actions": actions,
            "completed_visible_actions": completed,
            "action_observability": action_observability(len(actions), failures),
            "native_usage": usage,
            "invalid_jsonl_lines": parse_errors,
            "provenance": {
                "cli_version": version,
                "cli_executable": str(options.executable),
                "cli_sha256": cli_sha256,
                "launcher_sha256": launcher_sha256,
                "model": options.model,
                "reasoning_effort": options.reasoning,
                "model_catalog_sha256": digest(options.output / "model-catalog.json"),
                "prompt_sha256": digest(options.output / "prompt.txt"),
                "prompt_input_sha256": digest(options.output / "prompt-input.json"),
                "explicit_skills": skill_hashes,
                "source_unchanged": cli_sha256 == digest(options.executable)
                and launcher_sha256 == digest(Path(__file__))
                and skill_hashes == {str(path): digest(path) for path in options.skills},
                "project": str(options.project),
                "environment_policy": "inherited without modification or value logging; HOME/CODEX_HOME unchanged",
            },
        },
    )
    return status if status else int(bool(parse_errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--reasoning", default="max")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skill", type=Path, action="append", default=[])
    parser.add_argument("--marker", default="first_result.json")
    parser.add_argument(
        "--sandbox",
        choices=["read-only", "workspace-write", "danger-full-access"],
        default="danger-full-access",
    )
    args = parser.parse_args()
    executable = shutil.which(args.executable)
    if executable is None:
        parser.error("Codex executable not found")
    marker = Path(args.marker)
    if marker.is_absolute() or ".." in marker.parts:
        parser.error("Marker must be a relative path inside the project")
    return run(
        Options(
            Path(executable).resolve(),
            args.project.resolve(),
            args.prompt.resolve(),
            args.output.resolve(),
            args.model,
            args.reasoning,
            args.sandbox,
            tuple(path.resolve() for path in args.skill),
            args.marker,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
