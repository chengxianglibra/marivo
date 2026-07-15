"""Shared structured error payloads for live-authoring help and contract scope.

The neutral package cannot raise surface-owned errors (it must not import
``marivo.semantic``/``marivo.datasource``/``marivo.analysis``), so it produces
structured payloads that each surface's owned error classes consume. This keeps
the received-type / accepted-kinds / owning-surface / bounded-candidates
contract identical across surfaces without a third public error base.
"""

from __future__ import annotations

from dataclasses import dataclass

from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    HelpSurface,
    LiveHelpTarget,
)

_ACCEPTED_KINDS: tuple[str, ...] = (
    "None (root index)",
    "canonical string",
    "registered callable or bound method",
    "registered public type",
    "public runtime object owned by the surface",
    "registered result instance",
    "registered error instance or type",
)


@dataclass(frozen=True)
class HelpTargetErrorPayload:
    """Structured payload for an unregistered or cross-surface help target."""

    received: str
    accepted_kinds: tuple[str, ...]
    surface: HelpSurface | None
    candidates: tuple[str, ...]
    message: str


def build_help_target_error_payload(
    target: object,
    *,
    surface: HelpSurface,
    candidates: tuple[str, ...],
) -> HelpTargetErrorPayload:
    """Build the structured help-target error payload.

    Parameters
    ----------
    target:
        The rejected target (string or object).
    surface:
        The owning surface the target was submitted to.
    candidates:
        Lexical suggestion candidates; bounded to
        :data:`SURFACE_LIMITS.help_suggestion_limit`.
    """
    received = target if isinstance(target, str) else type(target).__name__
    bounded = tuple(candidates[: SURFACE_LIMITS.help_suggestion_limit])
    message = (
        f"{surface} help target is not registered: received {received!r}. "
        f"Accepted target kinds: {', '.join(_ACCEPTED_KINDS)}."
    )
    return HelpTargetErrorPayload(
        received=received,
        accepted_kinds=_ACCEPTED_KINDS,
        surface=surface,
        candidates=bounded,
        message=message,
    )


@dataclass(frozen=True)
class ContractScopeErrorPayload:
    """Structured payload for an over-broad contract scope request."""

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
    """Build the structured contract-scope error payload.

    The owned-subject candidate list is bounded to ``allowed_maximum`` so the
    repair hint never exceeds the contract render budget.
    """
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
