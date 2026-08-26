"""Normalize source-row coordinates before typed Finding identity encoding."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from contextlib import suppress
from datetime import date, datetime, time
from typing import Any

import numpy as np
import pandas as pd

from marivo.analysis.evidence.types import JsonScalar


def normalize_coordinate_value(value: Any) -> JsonScalar:
    """Return one supported JSON scalar without lossy string coercion."""
    missing = False
    if not isinstance(value, (list, tuple, dict)):
        with suppress(TypeError, ValueError):
            missing = bool(pd.isna(value))
    if value is None or missing:
        return None
    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        with suppress(TypeError, ValueError):
            value = item()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        f"evidence coordinates require JSON scalar values; received {type(value).__name__}"
    )


__all__ = ["normalize_coordinate_value"]
