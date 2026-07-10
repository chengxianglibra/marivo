"""Command-line check helper for semantic projects."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from marivo.semantic.loader import find_project
from marivo.semantic.reader import SemanticProject


def _default_backend_factory() -> Callable[[str], Any]:
    """Return a backend factory from marivo.analysis.

    Uses importlib to defer the import so marivo.semantic does not
    carry a static dependency on marivo.analysis.  Tests and external
    callers may monkeypatch this function to inject their own factory.
    """
    import importlib

    return lambda name: importlib.import_module("marivo.datasource").connect(name)


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


def _domain_of_ref(ref: str) -> str:
    return ref.split(".", 1)[0] if "." in ref else ref


def _partition_readiness_by_domain(
    report: Any,
    domain_names: list[str],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for domain_name in domain_names:
        blockers = [
            b for b in report.blockers if any(_domain_of_ref(r) == domain_name for r in b.refs)
        ]
        warnings = [
            w for w in report.warnings if any(_domain_of_ref(r) == domain_name for r in w.refs)
        ]
        if blockers:
            status = "blocked"
        elif warnings:
            status = "ready_with_warnings"
        else:
            status = "ready"
        result[domain_name] = {
            "status": status,
            "blockers": [b.to_dict() for b in blockers],
            "warnings": [w.to_dict() for w in warnings],
        }
    return result


def run_check(
    *,
    workspace_dir: str | Path | None = None,
    readiness: bool = False,
    format: Literal["json", "text"] = "text",
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
        report = project.readiness()
        payload["readiness"] = report.to_dict()
        payload["status"] = report.status
        if project.is_ready() and project._registry is not None:
            domain_names = sorted(project._registry.domains.keys())
            payload["readiness_by_domain"] = _partition_readiness_by_domain(report, domain_names)

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
    readiness_by_domain = payload.get("readiness_by_domain")
    if isinstance(readiness_by_domain, dict):
        for domain_name, domain_payload in readiness_by_domain.items():
            status = domain_payload.get("status", "ready")
            blocker_list = domain_payload.get("blockers", [])
            warning_list = domain_payload.get("warnings", [])
            print(
                f"  {domain_name}: {status} ({len(blocker_list)} blockers, {len(warning_list)} warnings)"
            )


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
