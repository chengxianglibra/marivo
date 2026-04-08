from __future__ import annotations


class SemanticServiceError(Exception):
    """Base error for semantic service submodules."""


class SemanticNotFoundError(SemanticServiceError):
    """Raised when a semantic object cannot be found."""


class SemanticValidationError(SemanticServiceError):
    """Raised when a semantic payload or ref is invalid."""


class SemanticStateError(SemanticServiceError):
    """Raised when an operation is invalid for the object's lifecycle state."""
