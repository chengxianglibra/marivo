"""Command-line check helper for semantic projects."""

from __future__ import annotations

import argparse
import contextlib
import json
from collections.abc import Callable, Iterable
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

    analysis = importlib.import_module("marivo.analysis")
    return lambda name: analysis.datasources.build_backend(name)


def _run_parity_checks(
    project: SemanticProject, backend_factory: Callable[[str], Any] | None
) -> None:
    """Run parity checks for base metrics that have source_sql."""
    if backend_factory is None or not project.is_ready():
        return
    reg = project.registry()
    if reg is None:
        return
    for metric in reg.metrics.values():
        if metric.is_derived:
            continue
        if not metric.provenance.source_sql or not metric.provenance.source_dialect:
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
    root: str | Path | None = None,
    readiness: bool = False,
    format: Literal["json", "text"] = "text",
    strict_provenance: bool = True,
    require_preview: bool = True,
    require_comments: bool = False,
    raw_previews: Iterable[str] = (),
    failed_raw_previews: Iterable[str] = (),
    knowledge_documents: Iterable[str] = (),
    user_confirmations: Iterable[str] = (),
    confirmed_relationships: Iterable[str] = (),
    primary_keys_sampled: Iterable[str] = (),
    raw_sql_required_refs: Iterable[str] = (),
    supports_federation: bool = False,
    backend_factory: Callable[[str], Any] | None = None,
) -> dict[str, object]:
    if root is None:
        project = find_project()
        if project is None:
            return {
                "status": "errored",
                "errors": [
                    {
                        "kind": "invalid_project",
                        "message": "Could not find .marivo/semantic project root.",
                        "refs": [],
                        "location": None,
                        "hint": "Pass --root with the semantic project path.",
                    }
                ],
                "warnings": [],
            }
    else:
        project = SemanticProject(root=root)

    result = project.load()
    payload: dict[str, object] = {
        "status": result.status,
        "errors": [_error_to_dict(error) for error in result.errors],
        "warnings": [_warning_to_dict(warning) for warning in result.warnings],
    }

    if readiness:
        factory = backend_factory
        if require_preview and factory is None:
            factory = _default_backend_factory()
        # Run parity checks for base metrics with source_sql so readiness
        # can evaluate provenance status accurately.
        _run_parity_checks(project, factory)
        report = project.readiness(
            strict_provenance=strict_provenance,
            require_preview=require_preview,
            require_comments=require_comments,
            backend_factory=factory,
            raw_previews=raw_previews,
            failed_raw_previews=failed_raw_previews,
            knowledge_documents=knowledge_documents,
            user_confirmations=user_confirmations,
            confirmed_relationships=confirmed_relationships,
            primary_keys_sampled=primary_keys_sampled,
            raw_sql_required_refs=raw_sql_required_refs,
            supports_federation=supports_federation,
        )
        payload["readiness"] = report.to_dict()
        payload["status"] = report.status

    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check a Marivo semantic project.")
    parser.add_argument("--root", default=None, help="Path to .marivo/semantic")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--readiness", action="store_true")
    parser.add_argument(
        "--strict-provenance", dest="strict_provenance", action="store_true", default=True
    )
    parser.add_argument("--no-strict-provenance", dest="strict_provenance", action="store_false")
    parser.add_argument(
        "--require-preview", dest="require_preview", action="store_true", default=True
    )
    parser.add_argument("--no-require-preview", dest="require_preview", action="store_false")
    parser.add_argument("--require-comments", action="store_true")
    parser.add_argument("--raw-preview", action="append", default=None)
    parser.add_argument("--failed-raw-preview", action="append", default=None)
    parser.add_argument("--knowledge-document", action="append", default=None)
    parser.add_argument("--user-confirmation", action="append", default=None)
    parser.add_argument("--confirmed-relationship", action="append", default=None)
    parser.add_argument("--primary-key-sampled", action="append", default=None)
    parser.add_argument("--raw-sql-required-ref", action="append", default=None)
    parser.add_argument("--supports-federation", action="store_true")
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
        root=args.root,
        readiness=args.readiness,
        format=args.format,
        strict_provenance=args.strict_provenance,
        require_preview=args.require_preview,
        require_comments=args.require_comments,
        raw_previews=args.raw_preview or (),
        failed_raw_previews=args.failed_raw_preview or (),
        knowledge_documents=args.knowledge_document or (),
        user_confirmations=args.user_confirmation or (),
        confirmed_relationships=args.confirmed_relationship or (),
        primary_keys_sampled=args.primary_key_sampled or (),
        raw_sql_required_refs=args.raw_sql_required_ref or (),
        supports_federation=args.supports_federation,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text(payload)
    return 0 if payload["status"] in {"ready", "ready_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
