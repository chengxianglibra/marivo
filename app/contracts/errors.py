from __future__ import annotations

from enum import StrEnum
from typing import Any


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
