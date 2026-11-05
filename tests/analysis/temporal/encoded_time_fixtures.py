"""Canonical time-field contracts shared by compiler and native backend tests."""

from marivo.refs import RefPayloadV1, ref
from marivo.semantic.ir import StrptimeParse, TargetDimensionContract


def axis(fmt: str, zone: str = "UTC") -> TargetDimensionContract:
    clock = "%H" in fmt
    return TargetDimensionContract(
        ref=RefPayloadV1.from_ref(ref.time_dimension("sales.events.point")),
        entity_ref=RefPayloadV1.from_ref(ref.entity("sales.events")),
        source_column="point",
        logical_type="timestamp" if clock else "date",
        nullable=True,
        is_time_dimension=True,
        sample_interval=None,
        timezone=zone if clock else None,
        parse=StrptimeParse(fmt, timezone=zone if clock else None),
    )
