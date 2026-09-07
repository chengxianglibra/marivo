"""Safe structured failures for private Dataset execution and retained reads."""

from __future__ import annotations

from marivo.analysis.datasets.errors import DatasetConstructionError


class MaterializationError(DatasetConstructionError):
    """An admitted execution or selected retained read cannot satisfy its contract."""

    def __init__(
        self,
        *,
        expected: str,
        received: str,
        repair: str,
        stage: str = "materialization",
        run_ref: str | None = None,
    ) -> None:
        self.stage = stage
        self.run_ref = run_ref
        super().__init__(
            expected=expected,
            received=received,
            repair=repair,
            location=f"dataset.{stage}",
            message="Dataset materialization contract failed.",
        )


class IntegrityError(MaterializationError):
    """Selected committed metadata or backing contradicts its immutable contract."""


class RecoveryPendingError(MaterializationError):
    """Authoritative transaction outcome or execution termination remains unknown."""


class SessionBusyError(MaterializationError):
    """Another action owns the Session writer boundary; no contender Run exists."""


class CollectionLimitError(MaterializationError):
    """A retained read exceeds its complete-result collection contract."""
