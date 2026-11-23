"""Register exact Arrow coordinate scalars at the carrier boundary."""

from __future__ import annotations

from decimal import Decimal
from typing import TypeAlias

import pyarrow as pa

from marivo.analysis.model.frozen_keys import register_native_key_types

FrameKeyScalar: TypeAlias = (
    bool
    | str
    | int
    | float
    | Decimal
    | pa.Date32Scalar
    | pa.TimestampScalar
    | pa.DurationScalar
    | None
)


register_native_key_types((pa.Date32Scalar, pa.TimestampScalar, pa.DurationScalar))
