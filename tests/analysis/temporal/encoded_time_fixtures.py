"""Canonical time-field contracts shared by compiler and native backend tests."""

from marivo.refs import RefPayloadV1, ref
from marivo.semantic.ir import StrptimeParse, TargetDimensionContract


def axis(fmt: str, zone: str = "UTC") -> TargetDimensionContract:
    clock = "%H" in fmt
    return TargetDimensionContract(
        RefPayloadV1.from_ref(ref.time_dimension("sales.events.point")),
        RefPayloadV1.from_ref(ref.entity("sales.events")),
        "point",
        "timestamp" if clock else "date",
        True,
        True,
        "second" if clock else "day",
        False,
        zone if clock else None,
        StrptimeParse(fmt, timezone=zone if clock else None),
    )
