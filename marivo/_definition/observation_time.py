"""Exact contribution clock and report endpoints shared by immutable definitions."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Literal

from marivo._kernel.errors import invalid
from marivo._kernel.refs import ExactRef, SemanticKind
from marivo._kernel.time_types import InstantAnchor


@dataclass(frozen=True, slots=True)
class ContributionClockBinding:
    """Business time role of a source reduction: a time field and its single-valued path.

    An empty path reads a time field native to the contribution root; a non-empty
    path is the ordered relationship chain from the root to the field's Entity.
    """

    field: ExactRef
    path: tuple[ExactRef, ...] = ()
    at: InstantAnchor | None = None
    source: Literal["explicit", "entity_default"] = "explicit"
    source_at: InstantAnchor | None = dataclass_field(default=None, kw_only=True)
    step_ats: tuple[InstantAnchor | None, ...] = dataclass_field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if self.field.ref.kind is not SemanticKind.TIME_DIMENSION:
            invalid(
                f"time binding expected a time_dimension reference; received "
                f"{self.field.ref.key}. Pass a declared time field."
            )
        for step in self.path:
            if step.ref.kind is not SemanticKind.RELATIONSHIP:
                invalid(
                    f"time path on {self.field.ref.key} expected relationship references; "
                    f"received {step.ref.key}. Pass declared relationships in path order."
                )


@dataclass(frozen=True, slots=True)
class ObservationPeriod:
    start: InstantAnchor | None
    end: InstantAnchor
    timezone: str
