"""Neutral fingerprint formatting and hard render-budget enforcement."""

from __future__ import annotations

import hashlib

from marivo.introspection.live.model import EnvironmentFingerprint


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

    Used for ordinary bounded displays. Only the version and a stable opaque
    fingerprint id are shown.
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
