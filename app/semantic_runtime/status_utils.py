"""Legacy semantic_runtime.status_utils stubs — preserved for import compatibility."""

from __future__ import annotations

from typing import Any


def derive_lifecycle_status(*_args: Any, **_kwargs: Any) -> str:
    """Stub — returns 'draft' during OSI v2 migration.  See Task 7."""
    return "draft"


def derive_readiness_status(*_args: Any, **_kwargs: Any) -> str:
    """Stub — returns 'not_ready' during OSI v2 migration.  See Task 7."""
    return "not_ready"
