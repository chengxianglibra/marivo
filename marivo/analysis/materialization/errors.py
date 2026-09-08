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

    def __init__(self, *, session_ref: str | None = None) -> None:
        self.session_ref = session_ref
        super().__init__(
            expected="one active writer for this Session",
            received="an active Session writer"
            if session_ref is None
            else f"an active writer for Session {session_ref}",
            repair="Wait for the current Session action to finish, then retry the same definition.",
            stage="writer_guard",
        )


class CollectionLimitError(MaterializationError):
    """A retained read exceeds its complete-result collection contract."""
