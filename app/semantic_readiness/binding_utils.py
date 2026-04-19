"""Shared utilities for binding contract validation.

Used by both readiness evaluators and semantic service validation.
"""

from __future__ import annotations

from typing import Any


def binding_contract_target_exists(
    bindings: list[dict[str, Any]],
    *,
    target_kind: str,
    target_key: str | None = None,
    semantic_ref: str | None = None,
) -> bool:
    """Check if a binding has a field binding matching the target specification.

    Args:
        bindings: List of binding dicts with 'target' and optionally 'semantic_ref'.
        target_kind: Required target kind to match (e.g., 'identity_key', 'metric_input').
        target_key: Optional target key to match within the target.
        semantic_ref: Optional semantic ref to match on the field binding.

    Returns:
        True if any field binding matches all specified criteria.

    Note:
        This function uses defensive access (dict.get with defaults) to handle
        potentially malformed binding data gracefully.
    """
    for binding in bindings:
        target = dict(binding.get("target") or {})
        if str(target.get("target_kind") or "") != target_kind:
            continue
        if target_key is not None and str(target.get("target_key") or "") != target_key:
            continue
        if semantic_ref is not None and str(binding.get("semantic_ref") or "") != semantic_ref:
            continue
        return True
    return False
