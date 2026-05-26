"""``check`` subcommand for marivo.semantic_py CLI.

Validates the semantic project and reports errors, warnings, and
optional parity results.

Exit codes:
    0 — ready and no strict violations
    1 — structural errors
    2 — --strict-provenance triggered unverified
    3 — --parity failed
    4 — --project doesn't exist / not a directory / find_project failed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from marivo.semantic_py.errors import SemanticError, StructuredWarning
from marivo.semantic_py.ir import ParityStatus
from marivo.semantic_py.reader import SemanticProject


def _resolve_project(project_arg: str | None) -> tuple[Path | None, int | None]:
    """Resolve the project root directory.

    Returns (project_root, exit_code).  If exit_code is not None the
    caller should exit immediately.
    """
    if project_arg is not None:
        p = Path(project_arg).resolve()
        if not p.exists() or not p.is_dir():
            return None, 4
        semantic_dir = p / ".marivo" / "semantic"
        if not semantic_dir.is_dir():
            return None, 4
        return p, None

    # Auto-discover
    from marivo.semantic_py.loader import find_project

    project = find_project()
    if project is None:
        return None, 4
    # find_project returns a SemanticProject whose root is .marivo/semantic;
    # we need the project root (parent of .marivo/semantic -> parent of .marivo)
    return project._root.parent.parent, None


def _error_to_dict(err: SemanticError) -> dict[str, Any]:
    """Convert a SemanticError to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "kind": err.kind,
        "class": type(err).__name__,
        "message": err.message,
    }
    if err.semantic_refs:
        d["refs"] = list(err.semantic_refs)
    if err.location is not None:
        d["location"] = {"file": err.location.file, "line": err.location.line}
    if err.hint is not None:
        d["hint"] = err.hint
    if err.details:
        d["details"] = err.details
    return d


def _warning_to_dict(warn: StructuredWarning) -> dict[str, Any]:
    """Convert a StructuredWarning to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "kind": warn.kind,
        "message": warn.message,
    }
    if warn.refs:
        d["refs"] = list(warn.refs)
    if warn.location is not None:
        d["location"] = {"file": warn.location.file, "line": warn.location.line}
    return d


def _format_error_text(err: SemanticError) -> str:
    """Format a SemanticError as text output."""
    return str(err)


def _format_warning_text(warn: StructuredWarning) -> str:
    """Format a StructuredWarning as text output."""
    return str(warn)


def run_check(args: argparse.Namespace) -> int:
    """Execute the check subcommand."""
    project_root, exit_code = _resolve_project(args.project)
    if exit_code is not None:
        if args.format == "json":
            _print_json(
                project_root=None,
                status="errored",
                models=[],
                errors=[],
                warnings=[],
                parity=[],
            )
        else:
            print("Error: could not find a semantic project", file=sys.stderr)
        return exit_code

    assert project_root is not None
    semantic_dir = project_root / ".marivo" / "semantic"
    project = SemanticProject(root=semantic_dir)
    project.load()

    # Collect model info
    models_info: list[dict[str, Any]] = []
    if project.is_ready():
        for model_summary in project.list_models():
            models_info.append(
                {
                    "name": model_summary.name,
                    "default": model_summary.default,
                    "object_counts": model_summary.object_counts,
                }
            )

    errors = project.errors()
    warnings = project.warnings()

    # --strict-provenance
    strict_violation = False
    if args.strict_provenance and project.is_ready():
        for metric_summary in project.list_metrics():
            status = metric_summary.parity_status
            if status in (ParityStatus.UNVERIFIED, ParityStatus.DRIFTED):
                strict_violation = True
                break

    # --parity
    parity_results: list[dict[str, Any]] = []
    parity_failed = False
    if args.parity and project.is_ready():
        for metric_summary in project.list_metrics():
            metric_ir = project.get_metric(metric_summary.semantic_id)
            if metric_ir is None or not metric_ir.provenance.source_sql:
                continue
            try:
                # We can't run parity without a backend_factory.
                # In the CLI context, we try to instantiate one from the
                # datasource declarations.
                # Build a backend factory from declared datasources
                backend_factory = _build_backend_factory(project)
                if backend_factory is None:
                    parity_results.append(
                        {
                            "metric": metric_summary.semantic_id,
                            "ok": False,
                            "error": "No backend factory available for parity check",
                        }
                    )
                    parity_failed = True
                    continue

                result = project.parity_check(
                    metric_summary.semantic_id,
                    backend_factory=backend_factory,
                )
                parity_results.append(
                    {
                        "metric": metric_summary.semantic_id,
                        "ok": result.ok,
                        "expected": result.expected,
                        "actual": result.actual,
                    }
                )
                if not result.ok:
                    parity_failed = True
            except Exception as exc:
                parity_results.append(
                    {
                        "metric": metric_summary.semantic_id,
                        "ok": False,
                        "error": str(exc),
                    }
                )
                parity_failed = True

    # Determine exit code
    if errors:
        final_exit_code = 1
    elif strict_violation:
        final_exit_code = 2
    elif parity_failed:
        final_exit_code = 3
    else:
        final_exit_code = 0

    # Output
    if args.format == "json":
        _print_json(
            project_root=project_root,
            status="ready" if project.is_ready() else "errored",
            models=models_info,
            errors=[_error_to_dict(e) for e in errors],
            warnings=[_warning_to_dict(w) for w in warnings],
            parity=parity_results,
        )
    else:
        _print_text(
            project_root=project_root,
            status="ready" if project.is_ready() else "errored",
            errors=errors,
            warnings=warnings,
            parity=parity_results,
        )

    return final_exit_code


def _build_backend_factory(
    project: SemanticProject,
) -> Any | None:
    """Attempt to build a backend factory from datasource declarations.

    Returns None if no datasources are found or they can't be instantiated.
    """
    import ibis

    datasources = project.list_datasources()
    if not datasources:
        return None

    # Map datasource names to backend_type
    ds_map: dict[str, str] = {ds.semantic_id: ds.backend_type for ds in datasources}

    # Cache backends by datasource semantic_id
    _cache: dict[str, Any] = {}

    def factory(datasource_id: str) -> Any:
        if datasource_id in _cache:
            return _cache[datasource_id]
        backend_type = ds_map.get(datasource_id)
        if backend_type is None:
            raise ValueError(f"Unknown datasource: {datasource_id}")
        if backend_type == "duckdb":
            con = ibis.duckdb.connect(":memory:")
        else:
            raise ValueError(f"Unsupported backend_type: {backend_type}")
        _cache[datasource_id] = con
        return con

    return factory


def _print_json(
    *,
    project_root: Path | None,
    status: str,
    models: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    parity: list[dict[str, Any]],
) -> None:
    """Print check results in JSON format."""
    output: dict[str, Any] = {
        "schema_version": "1",
        "project_root": str(project_root) if project_root is not None else None,
        "status": status,
        "models": models,
        "errors": errors,
        "warnings": warnings,
        "parity": parity,
    }
    print(json.dumps(output, indent=2, default=str))


def _print_text(
    *,
    project_root: Path,
    status: str,
    errors: tuple[SemanticError, ...],
    warnings: tuple[StructuredWarning, ...],
    parity: list[dict[str, Any]],
) -> None:
    """Print check results in text format."""
    print(f"Project: {project_root}")
    print(f"Status:  {status}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for err in errors:
            print(f"  {_format_error_text(err)}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for warn in warnings:
            print(f"  {_format_warning_text(warn)}")

    if parity:
        print(f"\nParity ({len(parity)}):")
        for entry in parity:
            ok_str = "OK" if entry.get("ok") else "FAIL"
            metric = entry.get("metric", "?")
            print(f"  [{ok_str}] {metric}")
            if not entry.get("ok") and "error" in entry:
                print(f"    error: {entry['error']}")
            if "expected" in entry and "actual" in entry:
                print(f"    expected={entry['expected']}, actual={entry['actual']}")

    if not errors and not warnings and not parity:
        print("No issues found.")
