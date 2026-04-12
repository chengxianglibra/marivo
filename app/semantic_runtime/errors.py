from __future__ import annotations

from typing import Any


class SemanticRuntimeError(LookupError):
    """Base error for typed semantic runtime resolution failures."""

    def __init__(self, message: str, *, semantic_ref: str) -> None:
        super().__init__(message)
        self.semantic_ref = semantic_ref


class SemanticRuntimeInvalidRefError(SemanticRuntimeError):
    """Raised when a ref does not belong to a supported semantic family."""


class SemanticRuntimeNotFoundError(SemanticRuntimeError):
    """Raised when a typed semantic ref does not exist."""


class SemanticRuntimeUnpublishedError(SemanticRuntimeError):
    """Raised when a typed semantic ref exists but is not published."""


class SemanticRuntimeNotReadyError(SemanticRuntimeError):
    """Raised when a typed semantic ref is active but not ready for runtime use."""

    def __init__(
        self,
        message: str,
        *,
        semantic_ref: str,
        object_kind: str,
        lifecycle_status: str,
        readiness_status: str,
        blocking_requirements: list[dict[str, Any]],
        capabilities: dict[str, Any],
        dependency_refs: list[str],
    ) -> None:
        super().__init__(message, semantic_ref=semantic_ref)
        self.object_kind = object_kind
        self.lifecycle_status = lifecycle_status
        self.readiness_status = readiness_status
        self.blocking_requirements = list(blocking_requirements)
        self.capabilities = dict(capabilities)
        self.dependency_refs = list(dependency_refs)

    def detail_payload(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "code": "semantic_not_ready",
            "category": "readiness",
            "subject_ref": self.semantic_ref,
            "object_kind": self.object_kind,
            "lifecycle_status": self.lifecycle_status,
            "readiness_status": self.readiness_status,
            "blocking_requirements": list(self.blocking_requirements),
            "capabilities": dict(self.capabilities),
            "dependency_refs": list(self.dependency_refs),
        }
