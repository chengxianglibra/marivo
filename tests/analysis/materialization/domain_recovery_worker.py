"""Exact terminal-output snapshots for independent recovery and refusal tests."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from numbers import Integral, Real
from typing import NoReturn

import numpy as np
import pandas as pd

import marivo.analysis as mv
from marivo.analysis.materialization.terminal_protocol import encode
from tests.support.json import Json, checked, obj


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Terminal output accessed a live source or Semantic")


def _scalar(value: object) -> Json:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return None if math.isnan(float(value)) else float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, pd.Timedelta):
        return {"duration": value.isoformat()}
    if isinstance(value, timedelta):
        return {"days": value.days, "seconds": value.seconds, "microseconds": value.microseconds}
    if isinstance(value, (date, datetime)):
        return {"temporal": value.isoformat()}
    if isinstance(value, tuple):
        return [_scalar(item) for item in value]
    raise TypeError(f"Unexpected terminal scalar: {type(value).__name__}")


def snapshot(result: mv.Artifact, *, frame: pd.DataFrame | None = None) -> dict[str, Json]:
    rows = result.to_pandas() if frame is None else frame
    return obj(
        checked(
            {
                "manifest": json.loads(encode(result._manifest)),
                "columns": [_scalar(column) for column in rows.columns],
                "rows": [
                    [_scalar(value) for value in row]
                    for row in rows.itertuples(index=False, name=None)
                ],
            }
        )
    )
