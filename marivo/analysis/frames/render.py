"""Bounded plain-text card formatter for agent-facing result types.

.. deprecated::
    Import from ``marivo.introspection.render`` instead. This module re-exports
    ``format_bounded_card`` for backward compatibility.
"""

from __future__ import annotations

from marivo.introspection.render import format_bounded_card

__all__ = ["format_bounded_card"]
