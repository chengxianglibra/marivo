"""Semantic live-surface typed error contracts."""

from __future__ import annotations

from marivo.introspection.live.errors import HelpTargetErrorPayload
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.errors import (
    ErrorKind,
    SemanticContractScopeError,
    SemanticError,
    SemanticHelpTargetError,
    SemanticRuntimeError,
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
    r = repair(kind="retry", canonical_id="parity_check", action="Run parity diagnostic.")
    assert r.help_target == LiveHelpTarget(surface="semantic", canonical_id="parity_check")


def test_runtime_filter_incompatibility_preserves_authored_business_literals() -> None:
    err = SemanticRuntimeError(
        kind=ErrorKind.FILTER_VALUE_RUNTIME_INCOMPATIBLE,
        message="runtime representation mismatch",
        refs=("queries.terminal_count", "queries.query_log.event_type"),
        details={
            "query_executed": False,
            "declaration_preserved": True,
        },
    )

    assert err.repair is not None
    assert err.repair.kind == "user_choice"
    assert err.repair.help_target == LiveHelpTarget(surface="semantic", canonical_id="where")
    assert err.repair.preserves_evidence is True
    assert "Preserve the authored filter literals" in err.repair.action
    assert "Do not replace business codes" in err.repair.action
    assert err.details["query_executed"] is False
    assert err.details["declaration_preserved"] is True


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
    assert err.repair.help_target == LiveHelpTarget(surface="semantic")
    assert "ms.help" not in str(err)


def test_semantic_contract_scope_error_carries_repair() -> None:
    from marivo._authoring.errors import ContractScopeErrorPayload

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
