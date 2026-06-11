"""Command-line check helper for semantic projects."""

from __future__ import annotations

import argparse
import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from marivo.semantic.loader import find_project
from marivo.semantic.reader import SemanticProject


def _run_parity_checks(
    project: SemanticProject,
    backend_factory: Callable[[str], Any] | None = None,
) -> None:
    """Run parity checks for base metrics declared as sql_parity."""
    if not project.is_ready():
        return
    reg = project._registry
    if reg is None:
        return
    for metric in reg.metrics.values():
        if metric.is_derived:
            continue
        if metric.provenance.verification_mode != "sql_parity":
            continue
        with contextlib.suppress(Exception):
            project.parity_check(metric.semantic_id, backend_factory=backend_factory)


def _error_to_dict(error: Any) -> dict[str, object]:
    location = None
    if getattr(error, "location", None) is not None:
        location = {
            "file": error.location.file,
            "line": error.location.line,
        }
    return {
        "kind": error.kind,
        "message": error.message,
        "refs": list(error.semantic_refs),
        "location": location,
        "hint": error.hint,
    }


def _warning_to_dict(warning: Any) -> dict[str, object]:
    location = None
    if getattr(warning, "location", None) is not None:
        location = {
            "file": warning.location.file,
            "line": warning.location.line,
        }
    return {
        "kind": warning.kind,
        "message": warning.message,
        "refs": list(warning.refs),
        "location": location,
    }


def run_check(
    *,
    workspace_dir: str | Path | None = None,
    readiness: bool = False,
    format: Literal["json", "text"] = "text",
    backend_factory: Callable[[str], Any] | None = None,
) -> dict[str, object]:
    if workspace_dir is None:
        project = find_project()
        if project is None:
            return {
                "status": "errored",
                "errors": [
                    {
                        "kind": "invalid_project",
                        "message": "Could not find .marivo project root.",
                        "refs": [],
                        "location": None,
                        "hint": "Pass --workspace-dir with the project path, or set MARIVO_PROJECT_ROOT.",
                    }
                ],
                "warnings": [],
            }
    else:
        project = SemanticProject(workspace_dir=workspace_dir)

    result = project.load()
    payload: dict[str, object] = {
        "status": result.status,
        "errors": [_error_to_dict(error) for error in result.errors],
        "warnings": [_warning_to_dict(warning) for warning in result.warnings],
    }

    if readiness:
        _run_parity_checks(project, backend_factory=backend_factory)
        report = project.readiness(backend_factory=backend_factory)
        payload["readiness"] = report.to_dict()
        payload["status"] = report.status

    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check a Marivo semantic project.")
    parser.add_argument(
        "--workspace-dir", default=None, help="Path to project workspace (containing .marivo/)"
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--readiness", action="store_true")
    return parser


def _print_text(payload: dict[str, object]) -> None:
    print(f"Semantic check: {payload['status']}")
    errors: list[dict[str, object]] = payload.get("errors", [])  # type: ignore[assignment]
    warnings: list[dict[str, object]] = payload.get("warnings", [])  # type: ignore[assignment]
    if errors:
        print("Errors:")
        for error in errors:
            print(f"- [{error['kind']}] {error['message']}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- [{warning['kind']}] {warning['message']}")
    readiness = payload.get("readiness")
    if isinstance(readiness, dict):
        print(f"Semantic readiness: {readiness['status']}")
        blockers: list[dict[str, object]] = readiness.get("blockers", [])
        report_warnings: list[dict[str, object]] = readiness.get("warnings", [])
        if blockers:
            print("Blockers:")
            for blocker in blockers:
                print(f"- [{blocker['kind']}] {blocker['message']}")
        if report_warnings:
            print("Readiness warnings:")
            for warning in report_warnings:
                print(f"- [{warning['kind']}] {warning['message']}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = run_check(
        workspace_dir=args.workspace_dir,
        readiness=args.readiness,
        format=args.format,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text(payload)
    return 0 if payload["status"] in {"ready", "ready_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
