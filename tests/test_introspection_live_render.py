"""Shared render helpers: fingerprint masking, budget, contract rendering."""

from __future__ import annotations

import pytest

from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    AuthoringContract,
    AuthoringEffects,
    AuthoringStateRef,
    AuthoringTransition,
    EnvironmentFingerprint,
    LiveHelpTarget,
)
from marivo.introspection.live.render import (
    enforce_budget,
    mask_fingerprint,
    render_contract,
    render_fingerprint,
)


def _fp() -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        marivo_version="9.9.9",
        python_executable="/venv/bin/python",
        package_path="/venv/lib/marivo/__init__.py",
    )


def test_render_fingerprint_reveals_exact_paths():
    text = render_fingerprint(_fp(), reveal=True)
    assert "Marivo: 9.9.9" in text
    assert "/venv/bin/python" in text
    assert "/venv/lib/marivo/__init__.py" in text


def test_render_fingerprint_masks_when_not_revealed():
    text = render_fingerprint(_fp(), reveal=False)
    assert "/venv/bin/python" not in text
    assert "/venv/lib/marivo" not in text
    assert "9.9.9" in text
    assert "fingerprint" in text.lower()


def test_mask_fingerprint_hides_paths_keeps_version():
    text = mask_fingerprint(_fp())
    assert "9.9.9" in text
    assert "/venv/bin/python" not in text
    assert "/venv/lib/marivo" not in text
    # Stable opaque id is present.
    assert "fingerprint" in text.lower()


def test_enforce_budget_passes_within_budget():
    text = "line1\nline2\nline3"
    out = enforce_budget(text, max_lines=10, max_codepoints=1000)
    assert out == text


def test_enforce_budget_raises_on_line_overflow():
    with pytest.raises(RuntimeError):
        enforce_budget("a\nb\nc\nd", max_lines=2, max_codepoints=1000)


def test_enforce_budget_raises_on_codepoint_overflow():
    with pytest.raises(RuntimeError):
        enforce_budget("x" * 10, max_lines=10, max_codepoints=5)


def _transition(available: bool) -> AuthoringTransition:
    return AuthoringTransition(
        kind="preview",
        help_target=LiveHelpTarget(surface="semantic", canonical_id="preview"),
        subject_refs=("metric:sales",),
        effects=AuthoringEffects(
            data_access="scoped_data_read",
            connection="opens_connection",
            mutations=("project_state",),
            flags=("requires_existing_snapshot_binding",),
        ),
        available=available,
        blocked_by=() if available else ("runtime_preview_missing",),
    )


def test_render_contract_lists_available_and_blocked_transitions():
    contract = AuthoringContract(
        subject_refs=("metric:sales",),
        states=(AuthoringStateRef(id="semantic.loaded", subject_refs=("metric:sales",)),),
        transitions=(_transition(True), _transition(False)),
    )
    text = render_contract(
        contract,
        max_lines=SURFACE_LIMITS.object_contract_render_max_lines,
        max_codepoints=SURFACE_LIMITS.object_contract_render_max_codepoints,
    )
    assert "preview" in text
    assert "available" in text.lower() or "blocked" in text.lower()


def test_render_contract_empty_transitions_disclosed():
    contract = AuthoringContract(
        subject_refs=("metric:sales",),
        states=(AuthoringStateRef(id="semantic.ready", subject_refs=("metric:sales",)),),
        transitions=(),
    )
    text = render_contract(contract, max_lines=80, max_codepoints=4000)
    # No mechanically invokable continuation is disclosed, not silently empty.
    assert "no" in text.lower() or "none" in text.lower() or "0" in text
