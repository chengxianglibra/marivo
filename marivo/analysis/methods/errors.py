"""Structured refusals for private method registration and qualification."""

from typing import NoReturn

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget


class MethodRegistrationError(AnalysisError):
    """A method owner, declaration, or exact implementation is invalid."""

    def __init__(self, expected: str, received: str, repair: str | AnalysisRepair) -> None:
        super().__init__(
            message="Analysis method registration failed.",
            expected=expected,
            received=received,
            location="analysis.methods",
            repair=repair
            if isinstance(repair, AnalysisRepair)
            else AnalysisRepair(
                kind="retry", action=repair, help_target=LiveHelpTarget(surface="analysis")
            ),
        )


def reject(expected: str, received: str, repair: str | AnalysisRepair) -> NoReturn:
    raise MethodRegistrationError(expected, received, repair)
