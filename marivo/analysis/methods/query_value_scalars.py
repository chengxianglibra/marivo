"""Exact int64 carrier selection for local ordered comparisons."""

import pandas as pd
import pyarrow as pa

from marivo._definition.query import ValueExpression, ValueLiteral


def int64_literal(expression: ValueExpression) -> pa.Scalar | None:
    if isinstance(expression, ValueLiteral):
        value = expression.value
        if type(value) is int and -(2**63) <= value < 2**63:
            return pa.scalar(value, type=pa.int64())
    return None


def int64_array(
    expression: ValueExpression, values: pd.Series
) -> pa.Array | pa.ChunkedArray | None:
    dtype = values.dtype
    if (
        not isinstance(expression, ValueLiteral)
        and isinstance(dtype, pd.ArrowDtype)
        and dtype.pyarrow_dtype == pa.int64()
    ):
        return pa.array(values, from_pandas=True)
    return None
