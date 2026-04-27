from __future__ import annotations

from typing import Any


class SemanticServiceError(Exception):
    """Base error for semantic service submodules."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        category: str | None = None,
        field_path: str | None = None,
        remediation: dict[str, Any] | None = None,
        examples: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.field_path = field_path
        self.remediation = remediation
        self.examples = examples


class SemanticNotFoundError(SemanticServiceError):
    """Raised when a semantic object cannot be found."""


class SemanticValidationError(SemanticServiceError):
    """Raised when a semantic payload or ref is invalid."""


class SemanticStateError(SemanticServiceError):
    """Raised when an operation is invalid for the object's lifecycle state."""


class SemanticCompatibilityError(SemanticServiceError):
    """Raised when a compatibility/profile constraint is not satisfied."""
