"""Structured local failures for the private Dataset value boundary."""

from __future__ import annotations

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import canonical_target


class DatasetConstructionError(AnalysisError):
    """A Dataset value violates its owning closed construction contract."""

    def __init__(
        self,
        *,
        expected: str,
        received: str,
        repair: str,
        location: str = "dataset",
        message: str = "Dataset contract validation failed.",
        help_target: str | None = None,
    ) -> None:
        super().__init__(
            message=message,
            expected=expected[:320],
            received=received[:320],
            location=location[:160],
            hint=repair[:480],
            repair=AnalysisRepair(
                kind="retry",
                action=repair[:480],
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=canonical_target(help_target) if help_target is not None else None,
                ),
            ),
        )


class DatasetRegistrationError(DatasetConstructionError):
    """A private family registration is incomplete or inconsistent."""
