"""Decode closed scalar values from terminal metadata."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import pyarrow as pa

from marivo.analysis.materialization.contracts import _obj, _text
from marivo.analysis.model.receipts import invalid


def decode_wire_scalar(value: object) -> object:
    obj = _obj(value, "scalar value")
    raw = obj["value"]
    if obj["scalar"] == "arrow_date" and type(raw) is int:
        return pa.scalar(raw, type=pa.date32())
    if obj["scalar"] == "arrow_timestamp" and isinstance(raw, list) and len(raw) == 3:
        unit, zone, integer = raw
        if type(unit) is str and (zone is None or type(zone) is str) and type(integer) is int:
            return pa.scalar(integer, type=pa.timestamp(unit, zone))
    if obj["scalar"] == "arrow_duration" and isinstance(raw, list) and len(raw) == 2:
        unit, integer = raw
        if type(unit) is str and type(integer) is int:
            return pa.scalar(integer, type=pa.duration(unit))
    text = _text(raw)
    if obj["scalar"] == "timedelta":
        return timedelta(microseconds=int(text))
    try:
        if obj["scalar"] == "Decimal":
            number = Decimal(text)
            if number.is_finite() and str(number) == text:
                return number
        if obj["scalar"] == "date":
            day = date.fromisoformat(text)
            if day.isoformat() == text:
                return day
        if obj["scalar"] == "datetime":
            instant = datetime.fromisoformat(text)
            if instant.isoformat() == text:
                return instant
    except (ValueError, InvalidOperation):
        pass
    raise invalid("invalid terminal scalar")
