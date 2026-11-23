"""Structured errors for bound preparation and execution."""

from __future__ import annotations

from typing import NoReturn

from marivo._kernel.errors import LiveHelpTarget
from marivo.analysis.model.errors import AnalysisError, AnalysisRepair


class DomainPreparationError(AnalysisError):
    """A bound preparation or execution obligation failed."""

    def __init__(self, constraint_id: str, stage: str, expected: str, received: str, repair: str):
        self.constraint_id = constraint_id
        self.stage = stage
        super().__init__(
            message="Domain preparation failed.",
            expected=expected,
            received=received,
            location=f"analysis.{stage}.{constraint_id}",
            repair=AnalysisRepair(
                kind="retry", action=repair, help_target=LiveHelpTarget(surface="analysis")
            ),
        )


def fail(constraint: str, received: str, *, stage: str = "construction") -> NoReturn:
    raise DomainPreparationError(
        f"r7.{constraint}",
        stage,
        "exact bound domain inputs and a qualified preparation",
        received,
        "Repair the named member, contribution or preparation binding.",
    )
