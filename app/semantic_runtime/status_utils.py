"""Compatibility wrappers for readiness derivation utilities.

This module re-exports derive_lifecycle_status and derive_readiness_status
from app.semantic_readiness for backwards compatibility. The readiness
contract computation is now handled by SemanticReadinessService.
"""

from __future__ import annotations

from app.semantic_readiness import derive_lifecycle_status, derive_readiness_status

__all__ = ["derive_lifecycle_status", "derive_readiness_status"]
