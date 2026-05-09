from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from marivo.runtime_contracts import ExecutionFeedback


class ErrorCode(StrEnum):
    # General
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    FORBIDDEN = "forbidden"
    VALIDATION = "validation"

    # Session
    SESSION_CLOSED = "session_closed"
    SESSION_NOT_FOUND = "session_not_found"

    # Semantic model
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_REVISION_CONFLICT = "model_revision_conflict"

    # Evidence
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"

    # DataSource
    QUERY_EXECUTION_FAILED = "query_execution_failed"
    DATASOURCE_UNAVAILABLE = "datasource_unavailable"


class DomainError(Exception):
    """Base domain error raised by Runtime/Core."""

    code: ErrorCode
    message: str
    detail: dict[str, Any]

    def __init__(self, code: ErrorCode, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


class NotFoundError(DomainError): ...


class ConflictError(DomainError): ...


class ForbiddenError(DomainError): ...


class ValidationError(DomainError): ...


class IntegrityError(DomainError):
    """Data integrity violation — e.g., evidence hash mismatch."""

    def __init__(self, *, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(
            code=ErrorCode.EVIDENCE_HASH_MISMATCH,
            message=message,
            detail=detail or {},
        )


@dataclass
class ExecutionError(ValueError):
    code: str
    category: str
    message: str
    retryable: bool = False
    replan_candidate: bool = False
    fallback_candidates: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_feedback(self) -> ExecutionFeedback:
        return ExecutionFeedback(
            code=self.code,
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            replan_candidate=self.replan_candidate,
            fallback_candidates=list(self.fallback_candidates),
            detail=dict(self.detail),
        )
