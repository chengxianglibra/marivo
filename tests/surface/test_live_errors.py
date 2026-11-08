"""Shared error payloads for help-target failures."""

from __future__ import annotations

from marivo.introspection.live.errors import (
    build_help_target_error_payload,
)
from marivo.introspection.live.model import SURFACE_LIMITS


def test_help_target_error_payload_string_target() -> None:
    payload = build_help_target_error_payload(
        "not_a_target",
        surface="semantic",
        candidates=("preview", "readiness"),
    )
    assert payload.received == "not_a_target"
    assert payload.surface == "semantic"
    assert payload.candidates == ("preview", "readiness")
    assert "canonical string" in payload.accepted_kinds
    assert "semantic" not in payload.accepted_kinds
    assert payload.message


def test_help_target_error_payload_object_target_uses_type_name() -> None:
    class Thing:
        pass

    payload = build_help_target_error_payload(Thing(), surface="datasource", candidates=())
    assert payload.received == "Thing"
    assert payload.candidates == ()


def test_help_target_error_payload_candidates_bounded() -> None:
    many = tuple(f"cap{i}" for i in range(SURFACE_LIMITS.help_suggestion_limit + 5))
    payload = build_help_target_error_payload("x", surface="semantic", candidates=many)
    assert len(payload.candidates) == SURFACE_LIMITS.help_suggestion_limit
