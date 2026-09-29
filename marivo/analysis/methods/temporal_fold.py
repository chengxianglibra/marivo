"""Version-one ordered sample encoding and source-free temporal fold algebra."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal, TypeAlias

FoldKind: TypeAlias = Literal["first", "last", "mean", "min", "max"]
Samples: TypeAlias = tuple[tuple[datetime, float, int], ...]


def decode_samples(value: object) -> Samples:
    """Decode complete unique sampling instants; malformed state never continues."""
    if not isinstance(value, str):
        raise ValueError("sample state must be a string")
    samples: list[tuple[datetime, float, int]] = []
    for item in value.split(";") if value else ():
        key, total, count = item.split("~")
        stamp, amount, support = datetime.fromisoformat(key), float(total), int(count)
        if stamp.tzinfo is not None or not math.isfinite(amount) or not 0 <= support < 2**63:
            raise ValueError("invalid UTC sample or finite components")
        if support == 0 and amount != 0:
            raise ValueError("unsupported sample has a nonzero total")
        samples.append((stamp, amount, support))
    samples.sort()
    if len({key for key, _, _ in samples}) != len(samples):
        raise ValueError("duplicate sampling instant")
    return tuple(samples)


def encode_samples(samples: Samples) -> str:
    """Encode the sorted sampling coordinates and their pre-fold components."""
    return ";".join(f"{key.isoformat()}~{total!r}~{count}" for key, total, count in samples)


def fold_value(samples: Samples, kind: object) -> float | None:
    """Apply the declared fold after spatial sums at each non-null sample."""
    if kind not in ("first", "last", "mean", "min", "max"):
        raise ValueError("unsupported scalar fold kind")
    values = [total for _, total, count in samples if count]
    if not values:
        return None
    result = (
        values[0]
        if kind == "first"
        else values[-1]
        if kind == "last"
        else min(values)
        if kind == "min"
        else max(values)
        if kind == "max"
        else math.fsum(values) / len(values)
    )
    if not math.isfinite(result):
        raise ValueError("fold exceeds finite float64")
    return result
