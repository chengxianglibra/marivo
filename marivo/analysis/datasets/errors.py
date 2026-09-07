"""Structured local failures for the private Dataset value boundary."""

from __future__ import annotations

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget


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
    ) -> None:
        # Core has no published Help leaf yet; an explicit hint also avoids the
        # eager constraint registry that AnalysisError otherwise loads lazily.
        super().__init__(
            message=message,
            expected=expected[:320],
            received=received[:320],
            location=location[:160],
            hint=repair[:480],
            repair=AnalysisRepair(
                kind="retry",
                action=repair[:480],
                help_target=LiveHelpTarget(surface="analysis"),
            ),
        )


class DatasetRegistrationError(DatasetConstructionError):
    """A private family registration is incomplete or inconsistent."""


class DatasetFieldSelectionError(DatasetConstructionError):
    """A selector does not identify one exact current field binding."""


class DatasetOwnershipError(DatasetConstructionError):
    """A Dataset or selector belongs to a different execution Session."""


class DatasetDefinitionError(DatasetConstructionError):
    """A logical definition cannot be normalized by the closed Core encoder."""
