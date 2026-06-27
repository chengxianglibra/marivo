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
from typing import Any

import marivo.semantic as ms

ALLOWED_IMPORT_ROOTS: frozenset[str] = frozenset({"marivo", "os"})
CATALOG_GET_KIND_PREFIXES: frozenset[str] = frozenset(kind.value for kind in ms.SemanticKind)

KNOWN_SESSION_INTENTS: frozenset[str] = frozenset(
    {
        "observe",
        "compare",
        "attribute",
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


def _catalog_metric_ids(catalog: Any) -> set[str]:
    ids: set[str] = set()
    for domain in catalog.list(kind=ms.SemanticKind.DOMAIN):
        ids.update(catalog.list(domain.ref, kind=ms.SemanticKind.METRIC).ids())
    return ids


def _load_metric_ids(workspace_dir: Path) -> frozenset[str]:
    """Load the embedded semantic model and return its metric semantic ids."""
    catalog = ms.load(workspace_dir=workspace_dir)
    return frozenset(_catalog_metric_ids(catalog))


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


def _catalog_vars(tree: ast.Module, session_vars: set[str]) -> set[str]:
    """Names bound to a session catalog or ``ms.load(...)`` result."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        is_catalog = False
        if isinstance(value, ast.Attribute) and value.attr == "catalog":
            is_catalog = isinstance(value.value, ast.Name) and value.value.id in session_vars
        elif isinstance(value, ast.Call):
            func = value.func
            is_catalog = (
                isinstance(func, ast.Attribute)
                and func.attr == "load"
                and isinstance(func.value, ast.Name)
                and func.value.id == "ms"
            )
        if is_catalog:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _is_catalog_get(func: ast.expr, *, catalog_vars: set[str], session_vars: set[str]) -> bool:
    if not (isinstance(func, ast.Attribute) and func.attr == "get"):
        return False
    value = func.value
    if isinstance(value, ast.Name):
        return value.id in catalog_vars
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "catalog"
        and isinstance(value.value, ast.Name)
        and value.value.id in session_vars
    )


def _catalog_get_literal(call: ast.Call) -> str | None:
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


def _typed_catalog_literal(literal: str) -> tuple[str, str] | None:
    kind, separator, semantic_id = literal.partition(".")
    if not separator or not semantic_id or kind not in CATALOG_GET_KIND_PREFIXES:
        return None
    return kind, semantic_id


def _catalog_get_literal_from_expr(
    expr: ast.expr,
    *,
    catalog_vars: set[str],
    session_vars: set[str],
    catalog_ref_bindings: dict[str, list[tuple[int, str]]],
    lineno: int | None,
) -> str | None:
    if isinstance(expr, ast.Call) and _is_catalog_get(
        expr.func,
        catalog_vars=catalog_vars,
        session_vars=session_vars,
    ):
        return _catalog_get_literal(expr)
    if isinstance(expr, ast.Name):
        candidates = catalog_ref_bindings.get(expr.id, [])
        if lineno is None:
            return candidates[-1][1] if candidates else None
        previous = [literal for bind_line, literal in candidates if bind_line <= lineno]
        return previous[-1] if previous else None
    return None


def _catalog_ref_bindings(
    tree: ast.Module,
    *,
    catalog_vars: set[str],
    session_vars: set[str],
) -> dict[str, list[tuple[int, str]]]:
    """Map simple ``name = catalog.get("kind.literal")`` bindings to their typed id."""
    bindings: dict[str, list[tuple[int, str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        literal = _catalog_get_literal_from_expr(
            node.value,
            catalog_vars=catalog_vars,
            session_vars=session_vars,
            catalog_ref_bindings={},
            lineno=None,
        )
        if literal is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bindings.setdefault(target.id, []).append((node.lineno, literal))
    return {name: sorted(values) for name, values in bindings.items()}


def _check_metric_refs(tree: ast.Module, metric_ids: frozenset[str]) -> list[ReplayCheckIssue]:
    issues: list[ReplayCheckIssue] = []
    session_vars = _session_vars(tree)
    catalog_vars = _catalog_vars(tree, session_vars)
    catalog_ref_bindings = _catalog_ref_bindings(
        tree,
        catalog_vars=catalog_vars,
        session_vars=session_vars,
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        intent = _intent_attr(node.func, session_vars)
        if intent not in {"observe", "promote_metric_frame"}:
            continue
        metric_exprs: list[ast.expr] = []
        if intent == "observe" and node.args:
            metric_exprs.append(node.args[0])
        metric_exprs.extend(kw.value for kw in node.keywords if kw.arg == "metric")
        for expr in metric_exprs:
            literal = _catalog_get_literal_from_expr(
                expr,
                catalog_vars=catalog_vars,
                session_vars=session_vars,
                catalog_ref_bindings=catalog_ref_bindings,
                lineno=node.lineno,
            )
            if literal is None:
                continue  # non-literal id cannot be statically resolved in v1
            typed_literal = _typed_catalog_literal(literal)
            if typed_literal is None:
                issues.append(
                    ReplayCheckIssue(
                        "metric_ref",
                        f"catalog.get metric input must use typed id 'metric.<semantic_id>': {literal}",
                        node.lineno,
                    )
                )
                continue
            kind, semantic_id = typed_literal
            if kind != ms.SemanticKind.METRIC.value:
                issues.append(
                    ReplayCheckIssue(
                        "metric_ref",
                        f"catalog.get metric input must be metric kind, got {kind}: {literal}",
                        node.lineno,
                    )
                )
                continue
            if semantic_id in metric_ids:
                continue
            issues.append(
                ReplayCheckIssue("metric_ref", f"unresolved metric: {semantic_id}", node.lineno)
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


def static_check_replay(script_path: Path, *, workspace_dir: Path) -> ReplayCheckResult:
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
    metric_ids = _load_metric_ids(workspace_dir)

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
