"""Shared error helpers for catalog-backed hints."""

from __future__ import annotations

from collections.abc import Mapping

from marivo.introspection.constraints import Constraint


def hint_from_catalog(
    catalog: Mapping[str, Constraint],
    error_kind: str,
    *,
    fallback: str | None = None,
) -> str | None:
    """Return the first catalog hint matching an error kind."""

    for constraint in catalog.values():
        if constraint.error_kind == error_kind:
            return constraint.hint
    return fallback
