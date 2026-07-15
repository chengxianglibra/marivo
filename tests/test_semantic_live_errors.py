"""Semantic live-surface typed error contracts."""

from __future__ import annotations

from marivo.introspection.live.errors import HelpTargetErrorPayload
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.errors import (
    SemanticContractScopeError,
    SemanticError,
    SemanticHelpTargetError,
    repair,
)


def test_semantic_error_has_repair_field() -> None:
    err = SemanticError(
        kind="not_found",
        message="metric foo not found",
        repair=repair(
            kind="inspect",
            canonical_id="load",
            action="Browse catalog.metrics before referencing a metric.",
        ),
    )
    assert err.repair is not None
    assert err.repair.kind == "inspect"
    assert err.repair.help_target == LiveHelpTarget(surface="semantic", canonical_id="load")


def test_semantic_error_repair_defaults_to_none() -> None:
    err = SemanticError(kind="not_found", message="missing")
    assert err.repair is None


def test_repair_helper_builds_semantic_help_target() -> None:
    r = repair(kind="reverify", canonical_id="verify_object", action="Re-run verification.")
    assert r.help_target == LiveHelpTarget(surface="semantic", canonical_id="verify_object")


def test_semantic_help_target_error_carries_payload() -> None:
    payload = HelpTargetErrorPayload(
        received="foo",
        accepted_kinds=("canonical string",),
        surface="semantic",
        candidates=("entity", "metric"),
        message="semantic help target is not registered: received 'foo'.",
    )
    err = SemanticHelpTargetError(payload)
    assert err.repair is not None
    assert err.repair.candidates == ("entity", "metric")
    assert "ms.help" in str(err)


def test_semantic_contract_scope_error_carries_repair() -> None:
    from marivo.introspection.live.errors import ContractScopeErrorPayload

    payload = ContractScopeErrorPayload(
        requested_subjects=("a", "b", "c"),
        allowed_maximum=1,
        owned_subjects=("a", "b"),
        message="contract scope exceeds 1 subjects",
        repair_target=LiveHelpTarget(surface="semantic", canonical_id="readiness"),
    )
    err = SemanticContractScopeError(payload)
    assert err.repair is not None
    assert err.repair.candidates == ("a", "b")
