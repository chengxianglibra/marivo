"""Safe structured failures for private Dataset execution and retained reads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from marivo.analysis.datasets.errors import DatasetConstructionError

if TYPE_CHECKING:
    from marivo.analysis.core.attribute import AttributeFailureContext


class MaterializationError(DatasetConstructionError):
    """An admitted execution or selected retained read cannot satisfy its contract."""

    def __init__(
        self,
        *,
        expected: str,
        received: str,
        repair: str,
        stage: str = "materialization",
        help_target: str = "analysis_frame.compute",
        attribute_context: AttributeFailureContext | None = None,
        received_limit: int = 320,
    ) -> None:
        self.stage = stage
        self.attribute_context = attribute_context
        super().__init__(
            expected=expected,
            received=received,
            repair=repair,
            location=f"dataset.{stage}",
            message="Dataset materialization contract failed.",
            help_target=help_target,
            received_limit=received_limit,
        )


def _execution_error(stage: str) -> MaterializationError:
    return MaterializationError(
        expected="a supported and complete registered Dataset execution",
        received="the admitted action could not complete its current phase; remote read status may be unknown",
        repair="Correct the reported source or resource requirement, then explicitly resubmit the analysis.",
        stage=stage,
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
            repair="Restore access to the exact committed backing, then retry the same saved Frame through session.persisted_frame(artifact_id).",
            stage=stage,
            help_target="session.persisted_frame",
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
            help_target="analysis_frame.persist",
        )
