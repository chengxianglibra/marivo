"""Closed routing and error models for unified help."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.introspection.live.model import HelpSurface, ResolvableHelpDescriptor
from marivo.introspection.live.resolve import ResolvedLiveTarget

GlobalTopic = Literal["root", "authoring", "load"]
HelpOutcome = Literal["success", "unknown", "ambiguous"]


@dataclass(frozen=True)
class NativeHelpRoute:
    """One target resolved by exactly one native help surface."""

    owner: HelpSurface
    resolved: ResolvedLiveTarget[ResolvableHelpDescriptor]
    original_target: object


@dataclass(frozen=True)
class TopicHelpRoute:
    """One explicitly registered global composition topic."""

    topic: GlobalTopic


type HelpRoute = NativeHelpRoute | TopicHelpRoute


class MarivoHelpTargetError(ValueError):
    """Unified rejection for unknown or ambiguous public help targets."""

    def __init__(
        self,
        *,
        target: object,
        outcome: Literal["unknown", "ambiguous"],
        candidates: tuple[str, ...] = (),
    ) -> None:
        self.target = target
        self.outcome = outcome
        self.received = target if isinstance(target, str) else type(target).__name__
        self.candidates = candidates
        if outcome == "ambiguous":
            message = (
                f"Marivo help target is ambiguous: received {self.received!r}. "
                "Use one qualified target."
            )
        else:
            message = (
                f"Marivo help target is not registered: received {self.received!r}. "
                "Use marivo.help() to browse registered targets."
            )
        if candidates:
            message += f" Candidates: {', '.join(candidates)}."
        super().__init__(message)
