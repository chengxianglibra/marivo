"""Structured neutral payloads for live help target errors.

The neutral package cannot raise surface-owned errors (it must not import
``marivo.semantic``/``marivo.datasource``/``marivo.analysis``), so it produces
structured payloads that each surface's owned error classes consume. This keeps
the received-type / accepted-kinds / owning-surface / bounded-candidates
contract identical across surfaces without a third public error base.
"""

from __future__ import annotations

from dataclasses import dataclass

from marivo.introspection.live.model import SURFACE_LIMITS, HelpSurface

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
    if bounded:
        # Surface the fuzzy candidates on the first line so agents see the fix
        # immediately, not only in the Repair section. See issue #35.
        message += f" Did you mean: {', '.join(bounded)}?"
    return HelpTargetErrorPayload(
        received=received,
        accepted_kinds=_ACCEPTED_KINDS,
        surface=surface,
        candidates=bounded,
        message=message,
    )
