"""Legacy semantic_runtime stubs — removed during OSI v2 migration.

These symbols exist solely to satisfy imports in code that has not yet been
migrated.  They will be removed once Task 7 (Fix Downstream Dependencies)
completes.
"""

from __future__ import annotations

from app.semantic_runtime.catalog import CatalogRuntimeService
from app.semantic_runtime.errors import (
    SemanticRuntimeError,
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotFoundError,
    SemanticRuntimeNotReadyError,
    SemanticRuntimeUnpublishedError,
)
from app.semantic_runtime.repository import SemanticRuntimeRepository

__all__ = [
    "CatalogRuntimeService",
    "SemanticRuntimeError",
    "SemanticRuntimeInvalidRefError",
    "SemanticRuntimeNotFoundError",
    "SemanticRuntimeNotReadyError",
    "SemanticRuntimeRepository",
    "SemanticRuntimeUnpublishedError",
]
