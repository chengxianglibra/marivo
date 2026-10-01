"""Exact scalar conversion shared by Event and statistical Finding publishers."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import pandas as pd

from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.materialization.contracts import invalid


def _scalar(value: object) -> t.Scalar:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, (str, int, float, bool, Decimal, date, datetime)):
        return value
    # Arrow-backed integer and boolean cells retain exact Python scalar identity.
    import numpy as np

    if isinstance(value, np.generic):
        return _scalar(value.item())
    raise invalid("unsupported exact Delta output scalar")


def _number(value: t.Scalar, numeric_type: str) -> t.Number:
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        raise invalid("Delta arithmetic requires a finite promoted number")
    if (isinstance(value, float) and not math.isfinite(value)) or (
        isinstance(value, Decimal) and not value.is_finite()
    ):
        raise invalid("non-finite Delta output number")
    if numeric_type == "int64" and (type(value) is not int or not -(2**63) <= value < 2**63):
        raise invalid("Delta integer promotion or overflow mismatch")
    if numeric_type == "float64" and type(value) is not float:
        raise invalid("Delta floating promotion mismatch")
    if numeric_type.startswith("decimal") and type(value) is not Decimal:
        raise invalid("Delta decimal promotion mismatch")
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int):
            raise invalid("non-finite Delta decimal exponent")
        scale = max(0, -exponent)
        precision = max(len(value.as_tuple().digits) + max(exponent, 0), scale)
        if precision > 38 or scale > 38:
            raise invalid("Delta decimal exceeds its actual precision or scale bound")
    return value


def _share(
    value: t.Scalar,
    reason: Literal["zero_total_delta", "empty_positive_pool", "empty_negative_pool"],
) -> t.FindingShareV1:
    if value is None:
        return t.UndefinedFindingShareV1(reason=reason)
    return t.DefinedFindingRatioV1(value=_number(value, "float64"))
