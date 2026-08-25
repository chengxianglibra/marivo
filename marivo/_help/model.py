"""Closed routing and error models for unified help."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.introspection.live.model import HelpSurface, ResolvableHelpDescriptor
from marivo.introspection.live.resolve import ResolvedLiveTarget

GlobalTopic = Literal["root", "authoring", "load", "targets"]
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


@dataclass(frozen=True)
class SurfaceRootHelpRoute:
    """The native root page for one exact public help surface."""

    owner: HelpSurface


HelpRoute: TypeAlias = NativeHelpRoute | SurfaceRootHelpRoute | TopicHelpRoute


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
                'Use marivo.help("targets") to browse canonical string targets.'
            )
        if candidates:
            message += f" Candidates: {', '.join(candidates)}."
        super().__init__(message)


class MarivoHelpSurfaceError(RuntimeError):
    """Structured blocker for an unexpected live-help routing or render failure."""

    def __init__(
        self,
        *,
        target: object | None,
        stage: str,
        cause_type: str,
    ) -> None:
        self.target = target
        self.stage = stage
        self.cause_type = cause_type
        self.received = target if isinstance(target, str) else type(target).__name__
        self.expected = "a registered, renderable Marivo help target"
        self.repair = (
            "Run doctor with the same interpreter and retry focused or root help. If help remains "
            "unavailable, use local docs or installed package source as read-only recovery; "
            "treat private implementation details as unverified and do not bypass public safety boundaries."
        )
        super().__init__(
            f"Marivo live help is unavailable for {self.received!r}: "
            f"stage={stage!r}, cause={cause_type!r}. {self.repair}"
        )
