"""Native numeric Arrow carriers shared by provider readers."""

import pandas as pd
import pyarrow as pa


def native_pandas_type(dtype: pa.DataType) -> pd.ArrowDtype | None:
    """Keep native integers and floating validity before pandas conversion."""
    return (
        pd.ArrowDtype(dtype) if pa.types.is_integer(dtype) or pa.types.is_floating(dtype) else None
    )
