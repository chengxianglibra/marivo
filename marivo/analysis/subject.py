"""Closed subject-selection values for typed analysis composition."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.analysis.errors import PatternStepMismatchError
from marivo.analysis.event import PatternStep, _event_repair, _fingerprint


class DroppedBefore(BaseModel):
    """Select subjects that resolved as lost before one exact Event step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["dropped_before"] = "dropped_before"
    step: PatternStep

    @property
    def fingerprint(self) -> str:
        """Return the stable semantic fingerprint of this selection."""
        return _fingerprint(
            {
                "schema": "marivo.subject_selection/v1",
                "kind": self.kind,
                "step": self.step.fingerprint,
            }
        )


SubjectSelection = Annotated[DroppedBefore, Field(discriminator="kind")]


def dropped_before(*, step: PatternStep) -> DroppedBefore:
    """Build a typed selection for resolved loss before an Event step.

    Args:
        step: Exact non-initial ``PatternStep`` retained by a source journey.

    Returns:
        A frozen ``DroppedBefore`` value accepted by
        ``session.select_subjects(...)``.

    Example:
        >>> selection = mv.dropped_before(step=payment_step)

    Constraints:
        Only an exact ``PatternStep`` is accepted. Source-pattern membership
        and the non-initial-step rule are validated by ``select_subjects``.
    """
    if type(step) is not PatternStep:
        raise PatternStepMismatchError(
            message="dropped_before requires an exact PatternStep",
            expected="mv.dropped_before(step=<PatternStep>)",
            received=repr(step),
            location="mv.dropped_before(step)",
            repair=_event_repair(
                kind="user_choice",
                action="Pass the exact PatternStep retained by the source EventPattern.",
                help_target="dropped_before",
            ),
        )
    return DroppedBefore(step=step)


__all__ = ["DroppedBefore", "SubjectSelection", "dropped_before"]
