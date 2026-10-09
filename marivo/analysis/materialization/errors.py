"""Safe structured failures for private Dataset execution and retained reads."""

from __future__ import annotations

from typing import Literal

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
        help_target: str = "actions.execute",
    ) -> None:
        self.stage = stage
        super().__init__(
            expected=expected,
            received=received,
            repair=repair,
            location=f"dataset.{stage}",
            message="Dataset materialization contract failed.",
            help_target=help_target,
            run_ref=run_ref,
        )


def _execution_error(stage: str, run_ref: str | None = None) -> MaterializationError:
    return MaterializationError(
        expected="a supported and complete registered Dataset execution",
        received="the admitted action could not complete its current phase; remote read status may be unknown",
        repair="Inspect the safe Run phase, correct its source or resource requirement, and retry.",
        stage=stage,
        run_ref=run_ref,
    )


class IntegrityError(MaterializationError):
    """Selected committed metadata or backing contradicts its immutable contract."""


class StorageAccessError(IntegrityError):
    """A safe storage classification, independent of Artifact and Evidence integrity."""

    def __init__(
        self,
        status: Literal["unauthorized", "missing", "mutated", "unknown"],
        *,
        stage: str = "storage_access",
    ) -> None:
        self.storage_status = status
        super().__init__(
            expected="authorized access to the exact committed storage version",
            received=f"selected storage is {status}",
            repair="Restore access to the exact committed backing and inspect the selected Artifact again.",
            stage=stage,
            help_target="session.artifact",
        )


class RecoveryPendingError(MaterializationError):
    """Commit state, publication ownership or storage integrity prevents safe continuation."""


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
