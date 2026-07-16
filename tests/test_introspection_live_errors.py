"""Shared error payloads for help-target and contract-scope failures."""

from __future__ import annotations

from marivo._authoring.errors import build_contract_scope_error_payload
from marivo.introspection.live.errors import (
    build_help_target_error_payload,
)
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget


def test_help_target_error_payload_string_target():
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


def test_help_target_error_payload_object_target_uses_type_name():
    class Thing:
        pass

    payload = build_help_target_error_payload(Thing(), surface="datasource", candidates=())
    assert payload.received == "Thing"
    assert payload.candidates == ()


def test_help_target_error_payload_candidates_bounded():
    many = tuple(f"cap{i}" for i in range(SURFACE_LIMITS.help_suggestion_limit + 5))
    payload = build_help_target_error_payload("x", surface="semantic", candidates=many)
    assert len(payload.candidates) == SURFACE_LIMITS.help_suggestion_limit


def test_contract_scope_error_payload_carries_repair_target():
    payload = build_contract_scope_error_payload(
        requested_subjects=("metric:a", "metric:b", "metric:c"),
        allowed_maximum=SURFACE_LIMITS.object_contract_max_subjects,
        owned_subjects=("metric:a", "metric:b"),
        repair_target=LiveHelpTarget(surface="semantic", canonical_id="readiness"),
    )
    assert payload.requested_subjects == ("metric:a", "metric:b", "metric:c")
    assert payload.allowed_maximum == SURFACE_LIMITS.object_contract_max_subjects
    assert payload.owned_subjects == ("metric:a", "metric:b")
    assert payload.repair_target.canonical_id == "readiness"
    assert len(payload.owned_subjects) <= SURFACE_LIMITS.object_contract_max_subjects
