from __future__ import annotations


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
