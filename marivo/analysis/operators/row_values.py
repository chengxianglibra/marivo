"""Pure exact row keys and scalar ordering shared by typed operators."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.operators.errors import row_value_error


def row_key_names(row: DatasetRowContract) -> tuple[str, ...]:
    names = {field.field_id: field.name for field in row.schema.columns}
    return tuple(names[key] for key in row.key_field_ids)


def frame_keys(frame: pd.DataFrame, keys: tuple[str, ...]) -> list[tuple[object, ...]]:
    """Normalize SQL-null coordinates consistently across primary and retained roles."""
    if not keys:
        return [()] * len(frame)
    return [
        tuple(normalize_key_value(value) for value in row)
        for row in frame.loc[:, list(keys)].itertuples(index=False, name=None)
    ]


def normalize_key_value(value: object) -> object:
    """Normalize Arrow/pandas mask containers without changing scalar identity."""
    if _missing(value):
        return None
    if isinstance(value, np.ndarray):
        if value.ndim != 1 or value.dtype != np.dtype("bool"):
            raise row_value_error("one fixed Boolean mask", "invalid array coordinate")
        return tuple(bool(item) for item in value)
    if isinstance(value, (list, tuple)):
        return tuple(normalize_key_value(item) for item in value)
    return value


def _missing(value: object) -> bool:
    return value is None or value is pd.NA or value is pd.NaT


def compare_value(left: object, right: object) -> int:
    left = normalize_key_value(left)
    right = normalize_key_value(right)
    if _missing(left) or _missing(right):
        return 0 if _missing(left) and _missing(right) else (1 if _missing(left) else -1)
    if isinstance(left, tuple) and isinstance(right, tuple):
        for a, b in zip(left, right, strict=True):
            result = compare_value(a, b)
            if result:
                return result
        return 0
    if type(left) is not type(right):
        raise row_value_error("one exact scalar type", "mixed local ordering types")
    if isinstance(left, (bool, int, float, Decimal)) and isinstance(
        right, (bool, int, float, Decimal)
    ):
        return int(left > right) - int(left < right)
    if isinstance(left, str) and isinstance(right, str):
        return int(left > right) - int(left < right)
    if isinstance(left, datetime) and isinstance(right, datetime):
        return int(left > right) - int(left < right)
    if isinstance(left, date) and isinstance(right, date):
        return int(left > right) - int(left < right)
    raise row_value_error("comparable exact retained scalars", "unsupported local ordering")
