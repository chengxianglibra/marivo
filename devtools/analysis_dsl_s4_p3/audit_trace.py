"""Summarize an external Agent trace without treating its claims as an oracle."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


def audit(trace_path: Path, answer_path: Path, project: Path) -> dict[str, Any]:
    """Inspect tool requests and answer imports for hidden-input dependence."""
    events: list[dict[str, Any]] = [
        json.loads(line) for line in trace_path.read_text().splitlines() if line
    ]
    initial = next(
        (
            event
            for event in events
            if event.get("type") == "system" and event.get("subtype") == "init"
        ),
        {},
    )
    final = next((event for event in reversed(events) if event.get("type") == "result"), {})
    tool_calls = [
        block
        for event in events
        for block in event.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    help_targets: set[str] = set()
    contract_calls = 0
    forbidden: list[str] = []
    project_root = project.resolve()
    for index, call in enumerate(tool_calls):
        request = call.get("input", {})
        command = str(request.get("command", ""))
        path_text = str(request.get("file_path", ""))
        inspected = command + "\n" + path_text
        help_targets.update(
            match.group(1)
            for match in re.finditer(r"marivo\.help\(['\"]([^'\"]+)['\"]\)", inspected)
        )
        contract_calls += inspected.count(".contract(")
        if re.search(
            r"\bimport duckdb\b|\b(?:duckdb|ibis\.duckdb|sqlite3)\.connect\b|"
            r"\b(?:pd|pandas)\.read_sql\b|\bread_sql\s*\(",
            inspected,
        ):
            forbidden.append(f"tool {index}: direct source query")
        if re.search(
            r"\b(?:cat|head|tail|sed|less|strings|rg)\s+[^\n]*warehouse\.duckdb",
            command,
        ):
            forbidden.append(f"tool {index}: raw source file read")
        if path_text:
            path = Path(path_text)
            if path.suffix == ".duckdb":
                forbidden.append(f"tool {index}: raw source file read")
            elif path.is_absolute() and not path.resolve().is_relative_to(project_root):
                forbidden.append(f"tool {index}: read outside Agent project")
        if re.search(
            r"/site-packages/marivo/|/source/oss/marivo/(?:marivo|tests|devtools)/", command
        ):
            forbidden.append(f"tool {index}: private implementation or test path")

    answer_imports: list[str] = []
    if answer_path.is_file():
        for node in ast.walk(ast.parse(answer_path.read_text())):
            if isinstance(node, ast.Import):
                answer_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                answer_imports.append(node.module)
        forbidden.extend(
            f"answer import: {name}"
            for name in answer_imports
            if name == "duckdb"
            or name.startswith("marivo.analysis.")
            or name.startswith("marivo.semantic.")
            or name.startswith("tests.")
        )
    return {
        "session_id": initial.get("session_id"),
        "claude_cli_version": initial.get("claude_code_version"),
        "selected_model": initial.get("model"),
        "actual_model_usage": final.get("modelUsage", {}),
        "event_count": len(events),
        "tool_call_count": len(tool_calls),
        "help_targets": sorted(help_targets),
        "contract_call_count": contract_calls,
        "forbidden_requests": forbidden,
        "answer_exists": answer_path.is_file(),
        "answer_imports": answer_imports,
        "answer_sha256": (
            hashlib.sha256(answer_path.read_bytes()).hexdigest() if answer_path.is_file() else None
        ),
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        "agent_result": final.get("result"),
        "agent_reported_error": final.get("is_error"),
        "total_cost_usd": final.get("total_cost_usd"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--answer", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = audit(args.trace, args.answer, args.project)
    if args.answer.is_file():
        shutil.copyfile(args.answer, args.output.parent / "answer.py")
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "session_id",
                    "tool_call_count",
                    "contract_call_count",
                    "forbidden_requests",
                    "answer_exists",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
