"""Shared render helpers for the live authoring surfaces.

Fingerprint rendering/masking, render-budget enforcement, and bounded contract
rendering. These are the neutral primitives the datasource and semantic render
paths (Phases 2-3) build on; the analysis render path adopts them in a later
unification phase.
"""

from __future__ import annotations

import hashlib

from marivo.introspection.live.model import (
    AuthoringContract,
    AuthoringTransition,
    EnvironmentFingerprint,
)


def render_fingerprint(fp: EnvironmentFingerprint, *, reveal: bool) -> str:
    """Render the environment fingerprint.

    When ``reveal`` is True (root help and explicit environment-mismatch
    diagnostics), exact interpreter and package paths are shown. When
    ``reveal`` is False, exact paths are hidden via :func:`mask_fingerprint`.
    """
    if not reveal:
        return mask_fingerprint(fp)
    return (
        f"Marivo: {fp.marivo_version}\nPython: {fp.python_executable}\nPackage: {fp.package_path}"
    )


def _opaque_id(fp: EnvironmentFingerprint) -> str:
    digest = hashlib.sha256(
        f"{fp.marivo_version}|{fp.python_executable}|{fp.package_path}".encode()
    ).hexdigest()
    return digest[:12]


def mask_fingerprint(fp: EnvironmentFingerprint) -> str:
    """Render the fingerprint with exact paths hidden behind an opaque id.

    Used for ordinary object/result, contract, handoff, receipt, and report
    renders. Only the version and a stable opaque fingerprint id are shown.
    """
    return f"Marivo {fp.marivo_version} (fingerprint {_opaque_id(fp)})"


def enforce_budget(text: str, *, max_lines: int, max_codepoints: int) -> str:
    """Normalize newlines and enforce a hard render budget.

    Overflow raises :class:`RuntimeError` rather than silently truncating, so
    an invocation-critical constraint or example is never dropped.
    """
    normalized = text.replace("\r\n", "\n")
    line_count = normalized.count("\n") + (1 if normalized else 0)
    if line_count > max_lines:
        raise RuntimeError(f"render budget exceeded: {line_count} lines > {max_lines}")
    if len(normalized) > max_codepoints:
        raise RuntimeError(
            f"render budget exceeded: {len(normalized)} codepoints > {max_codepoints}"
        )
    return normalized


def _render_transition(t: AuthoringTransition) -> str:
    flag = "available" if t.available else "blocked"
    line = f"- {t.kind} [{flag}] -> {t.help_target.display}"
    if not t.available and t.blocked_by:
        line += f"  blocked_by={', '.join(t.blocked_by)}"
    return line


def render_contract(contract: AuthoringContract, *, max_lines: int, max_codepoints: int) -> str:
    """Render a bounded mechanical-continuation contract.

    Lists every transition in deterministic order with availability, target,
    and blocker ids. An empty transition tuple is explicitly disclosed rather
    than rendered as a blank. Overflow raises :class:`RuntimeError`.
    """
    lines: list[str] = []
    subjects = ", ".join(contract.subject_refs) if contract.subject_refs else "(none)"
    lines.append(f"Subject: {subjects}")
    lines.append(f"States: {', '.join(s.id for s in contract.states) or '(none)'}")
    lines.append("Transitions:")
    if contract.transitions:
        for t in contract.transitions:
            lines.append(_render_transition(t))
    else:
        lines.append("- no mechanically invokable continuation disclosed")
    text = "\n".join(lines)
    return enforce_budget(text, max_lines=max_lines, max_codepoints=max_codepoints)
