"""Shared fuzzy-match helper for error repair suggestions."""

from __future__ import annotations

import difflib
from collections.abc import Sequence


def did_you_mean(
    target: str,
    candidates: Sequence[str],
    *,
    n: int = 3,
    cutoff: float = 0.6,
) -> list[str]:
    """Return close matches for *target* from *candidates*, up to *n* entries.

    Wraps :func:`difflib.get_close_matches` with Marivo defaults.
    Returns an empty list when no matches exceed *cutoff*.
    """
    return difflib.get_close_matches(target, candidates, n=n, cutoff=cutoff)
