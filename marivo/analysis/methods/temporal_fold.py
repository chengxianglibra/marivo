"""Typed ordered sample encoding and source-free temporal fold algebra."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from typing import Literal, TypeAlias

import pyarrow as pa

from marivo.analysis.methods.numeric_state import Number, checked_sum, finish_division
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType

FoldKind: TypeAlias = Literal["first", "last", "mean", "min", "max"]
Samples: TypeAlias = tuple[tuple[datetime, Number, int], ...]


def sample_type(samples: Samples) -> pa.DataType:
    """Return the exact homogeneous sample carrier; empty state has no magnitude."""
    values = [value for _, value, _ in samples]
    if not values:
        return pa.float64()
    if any(type(value) is not type(values[0]) for value in values):
        raise ValueError("sample numeric families differ")
    if isinstance(values[0], Decimal):
        scales = {value.as_tuple().exponent for value in values if isinstance(value, Decimal)}
        if len(scales) != 1:
            raise ValueError("sample Decimal scales differ")
        exponent = next(iter(scales))
        if not isinstance(exponent, int) or not -38 <= exponent <= 0:
            raise ValueError("sample Decimal scale is invalid")
        return pa.decimal128(38, -exponent)
    return pa.int64() if type(values[0]) is int else pa.float64()


def decode_samples(value: object) -> Samples:
    """Decode typed unique instants; unversioned or damaged samples never continue."""
    if not isinstance(value, str):
        raise ValueError("sample state must be a string")
    samples: list[tuple[datetime, Number, int]] = []
    for item in value.split(";") if value else ():
        key, encoded, count = item.split("~")
        code, raw = encoded.split(":", 1)
        amount: Number
        if code == "i":
            amount = int(raw)
        elif code == "f":
            amount = float(raw)
        elif code == "d":
            amount = Decimal(raw)
        else:
            raise ValueError("unqualified sample numeric codec")
        stamp, support = datetime.fromisoformat(key), int(count)
        if stamp.tzinfo is not None or not math.isfinite(amount) or not 0 <= support < 2**63:
            raise ValueError("invalid UTC sample or finite components")
        if support == 0 and amount != 0:
            raise ValueError("unsupported sample has a nonzero total")
        samples.append((stamp, amount, support))
    samples.sort()
    if len({key for key, _, _ in samples}) != len(samples):
        raise ValueError("duplicate sampling instant")
    result = tuple(samples)
    physical = sample_type(result)
    for _, amount, _ in result:
        checked_sum([amount], physical)
    return result


def encode_samples(samples: Samples) -> str:
    """Encode numeric family and exact magnitude at every sampling coordinate."""
    return ";".join(
        f"{key.isoformat()}~{'d' if isinstance(total, Decimal) else 'i' if type(total) is int else 'f'}:{total}~{count}"
        for key, total, count in samples
    )


def fold_value(
    samples: Samples, kind: object, duration: DurationType | None = None
) -> Number | None:
    """Select exact samples or round the mean of spatial sample sums once."""
    if kind not in ("first", "last", "mean", "min", "max"):
        raise ValueError("unsupported scalar fold kind")
    values = [total for _, total, count in samples if count]
    if not values:
        return None
    if kind == "mean":
        physical = sample_type(samples)
        total = checked_sum(values, physical)
        output = duration or (
            DecimalType(38, max(physical.scale, 6))
            if pa.types.is_decimal(physical)
            else ScalarType("float64")
        )
        return finish_division(total, len(values), output)
    return (
        values[0]
        if kind == "first"
        else values[-1]
        if kind == "last"
        else min(values)
        if kind == "min"
        else max(values)
    )
