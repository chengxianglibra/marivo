"""Exact retained row encoding shared by the blocked R8 journey fixtures."""

from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from marivo.analysis.datasets.base import MaterializedDataset


def _cell(value: object) -> object:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_cell(item) for item in value]
    if type(value) in (bool, int, float, str):
        return value
    raise AssertionError(f"Unexpected retained cell: {type(value).__name__}")


def rows(dataset: MaterializedDataset) -> list[list[object]]:
    return frame_rows(dataset.to_pandas())


def frame_rows(frame: pd.DataFrame) -> list[list[object]]:
    return [[_cell(cell) for cell in row] for row in frame.itertuples(index=False, name=None)]
