from __future__ import annotations


class SemanticServiceError(Exception):
    """Base error for semantic service submodules."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        category: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category


class SemanticNotFoundError(SemanticServiceError):
    """Raised when a semantic object cannot be found."""


class SemanticValidationError(SemanticServiceError):
    """Raised when a semantic payload or ref is invalid."""


class SemanticStateError(SemanticServiceError):
    """Raised when an operation is invalid for the object's lifecycle state."""


class SemanticCompatibilityError(SemanticServiceError):
    """Raised when a compatibility/profile constraint is not satisfied."""
