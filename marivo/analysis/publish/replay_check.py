"""Deterministic static checks for a generated replay.py.

This module never imports or executes the script under check. It parses the
source with ``ast`` and validates a fixed set of properties so a report
package can record ``validation = "static_checked"``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from marivo.semantic import SemanticProject

ALLOWED_IMPORT_ROOTS: frozenset[str] = frozenset({"marivo", "os"})

KNOWN_SESSION_INTENTS: frozenset[str] = frozenset(
    {
        "observe",
        "compare",
        "decompose",
        "correlate",
        "forecast",
        "assess_quality",
        "hypothesis_test",
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
    }
)
SESSION_NAMESPACES: frozenset[str] = frozenset({"transform", "discover"})

_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_SECRET_KV_RE = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|token|access[_-]?key|aws_secret_access_key)"
    r"\s*[=:]\s*[\"'][^\"']{4,}[\"']"
)


@dataclass(frozen=True)
class ReplayCheckIssue:
    """A single static-check problem found in a replay script."""

    check: str
    message: str
    lineno: int | None = None


@dataclass(frozen=True)
class ReplayCheckResult:
    """Aggregate outcome of statically checking a replay script."""

    ok: bool
    validation: str
    issues: tuple[ReplayCheckIssue, ...]


def _load_metric_ids(semantic_root: Path) -> frozenset[str]:
    """Load the embedded semantic model and return its metric semantic ids."""
    project = SemanticProject(root=semantic_root)
    project.load()
    return frozenset(m.semantic_id for m in project.list_metrics(display=False))


def _check_imports(tree: ast.Module) -> list[ReplayCheckIssue]:
    """Flag any import whose root module is not in the allowlist."""
    issues: list[ReplayCheckIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    issues.append(
                        ReplayCheckIssue("imports", f"disallowed import: {alias.name}", node.lineno)
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                issues.append(
                    ReplayCheckIssue(
                        "imports", f"disallowed import from: {node.module}", node.lineno
                    )
                )
    return issues


def _session_vars(tree: ast.Module) -> set[str]:
    """Names bound to a ``*.get_or_create(...)`` result, plus the default 'session'."""
    names = {"session"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == "get_or_create":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _intent_attr(func: ast.expr, session_vars: set[str]) -> str | None:
    """Return the first attribute after a session var in ``func``'s chain, else None."""
    attrs: list[str] = []
    node: ast.expr = func
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id in session_vars and attrs:
        return attrs[-1]
    return None


def _is_metric_ref(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "MetricRef"
    if isinstance(func, ast.Attribute):
        return func.attr == "MetricRef"
    return False


def _metric_ref_literal(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    for kw in call.keywords:
        if (
            kw.arg == "id"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _check_metric_refs(tree: ast.Module, metric_ids: frozenset[str]) -> list[ReplayCheckIssue]:
    issues: list[ReplayCheckIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_metric_ref(node.func):
            literal = _metric_ref_literal(node)
            if literal is None:
                continue  # non-literal id cannot be statically resolved in v1
            if literal not in metric_ids:
                issues.append(
                    ReplayCheckIssue("metric_ref", f"unresolved metric: {literal}", node.lineno)
                )
    return issues


def _bound_names(tree: ast.Module) -> dict[str, int]:
    """Map each module-bound name to the line where it is first bound."""
    bound: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(target.id, node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                bound.setdefault(local, node.lineno)
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            bound.setdefault(node.target.id, node.lineno)
    return bound


def _check_frame_vars(tree: ast.Module) -> list[ReplayCheckIssue]:
    session_vars = _session_vars(tree)
    bound = _bound_names(tree)
    for var in session_vars:
        bound.setdefault(var, 0)
    issues: list[ReplayCheckIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _intent_attr(node.func, session_vars) is not None:
            for arg in node.args:
                if not isinstance(arg, ast.Name):
                    continue
                def_line = bound.get(arg.id)
                if def_line is None:
                    issues.append(
                        ReplayCheckIssue(
                            "frame_var", f"undefined frame variable: {arg.id}", node.lineno
                        )
                    )
                elif def_line > node.lineno:
                    issues.append(
                        ReplayCheckIssue(
                            "frame_var",
                            f"frame variable used before definition: {arg.id}",
                            node.lineno,
                        )
                    )
    return issues


def _check_timescope(tree: ast.Module) -> list[ReplayCheckIssue]:
    """Flag timescope values that are not absolute {start, end} dict literals."""
    issues: list[ReplayCheckIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "timescope":
                continue
            value = kw.value
            if not isinstance(value, ast.Dict):
                issues.append(
                    ReplayCheckIssue(
                        "timescope",
                        "timescope must be an absolute {start, end} dict literal",
                        node.lineno,
                    )
                )
                continue
            keys = {k.value for k in value.keys if isinstance(k, ast.Constant)}
            if not {"start", "end"}.issubset(keys):
                issues.append(
                    ReplayCheckIssue(
                        "timescope",
                        "timescope dict must pin absolute 'start' and 'end'",
                        node.lineno,
                    )
                )
    return issues


def _check_intents(tree: ast.Module) -> list[ReplayCheckIssue]:
    session_vars = _session_vars(tree)
    issues: list[ReplayCheckIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _intent_attr(node.func, session_vars)
            if name is None or name in KNOWN_SESSION_INTENTS or name in SESSION_NAMESPACES:
                continue
            issues.append(
                ReplayCheckIssue("intent", f"unknown session intent: {name}", node.lineno)
            )
    return issues


def _check_secrets(source: str) -> list[ReplayCheckIssue]:
    issues: list[ReplayCheckIssue] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if _AWS_KEY_RE.search(line):
            issues.append(
                ReplayCheckIssue("secret", "possible AWS access key id in script", lineno)
            )
        if _SECRET_KV_RE.search(line):
            issues.append(
                ReplayCheckIssue("secret", "possible hardcoded secret assignment in script", lineno)
            )
    return issues


def static_check_replay(script_path: Path, *, semantic_root: Path) -> ReplayCheckResult:
    """Statically validate ``script_path`` against the embedded semantic model.

    Returns a :class:`ReplayCheckResult`; it never raises on a failed check.
    """
    source = Path(script_path).read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        issue = ReplayCheckIssue(
            check="parse", message=f"script does not parse: {exc.msg}", lineno=exc.lineno
        )
        return ReplayCheckResult(ok=False, validation="failed", issues=(issue,))

    # Loaded now so the embedded-semantic dependency is exercised from the
    # start; consumed by the metric-ref check.
    metric_ids = _load_metric_ids(semantic_root)

    issues: list[ReplayCheckIssue] = []
    issues.extend(_check_imports(tree))
    issues.extend(_check_intents(tree))
    issues.extend(_check_metric_refs(tree, metric_ids))
    issues.extend(_check_frame_vars(tree))
    issues.extend(_check_timescope(tree))
    issues.extend(_check_secrets(source))

    ok = not issues
    return ReplayCheckResult(
        ok=ok,
        validation="static_checked" if ok else "failed",
        issues=tuple(issues),
    )
