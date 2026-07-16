"""Structured private payloads for datasource and semantic contract errors."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.introspection.live.model import LiveHelpTarget


@dataclass(frozen=True)
class ContractScopeErrorPayload:
    """Structured payload for an over-broad authoring-contract request."""

    requested_subjects: tuple[str, ...]
    allowed_maximum: int
    owned_subjects: tuple[str, ...]
    message: str
    repair_target: LiveHelpTarget


def build_contract_scope_error_payload(
    *,
    requested_subjects: tuple[str, ...],
    allowed_maximum: int,
    owned_subjects: tuple[str, ...],
    repair_target: LiveHelpTarget,
) -> ContractScopeErrorPayload:
    """Build a bounded, surface-neutral contract-scope payload."""
    bounded_owned = tuple(owned_subjects[:allowed_maximum])
    message = (
        f"contract scope exceeds {allowed_maximum} subjects: "
        f"requested {len(requested_subjects)}. "
        f"Narrow subject_refs to one of: {', '.join(bounded_owned) or '(none)'}"
    )
    return ContractScopeErrorPayload(
        requested_subjects=requested_subjects,
        allowed_maximum=allowed_maximum,
        owned_subjects=bounded_owned,
        message=message,
        repair_target=repair_target,
    )
