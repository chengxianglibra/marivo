"""Precise Event instants are admitted before any source execution."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.datatypes as dt
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.event_time import event_instant
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    StrptimeParse,
    TargetDimensionContract,
    TimestampParse,
)
from marivo.semantic.validator import Registry, normalize_target_dimension
from tests.lazy_event_fixtures import make_event_sources


def authority(
    zone: str | None = "UTC", *, format: str | None = None
) -> tuple[Registry, TargetDimensionContract]:
    original = make_event_sources()._owner.semantic_registry
    path = "sales.started_rows.occurred_at"
    parse = (
        StrptimeParse(format=format, timezone=zone)
        if format is not None
        else TimestampParse(timezone=zone)
    )
    registry = replace(
        original,
        dimensions={**original.dimensions, path: replace(original.dimensions[path], parse=parse)},
    )
    registry.freeze()
    return registry, normalize_target_dimension(registry, path)


@pytest.mark.parametrize("zone", ["UTC", "Etc/UTC", "GMT", "Etc/GMT"])
def test_native_naive_utc_uses_explicit_timezone_builtin(zone: str) -> None:
    registry, axis = authority(zone)
    source = ibis.table({"occurred_at": "timestamp"}, name="unread_events")
    value = event_instant(source.occurred_at, axis, registry)
    assert value.type().timezone == "UTC"
    sql = ibis.to_sql(source.select(instant=value), dialect="duckdb")
    assert "TIMEZONE('UTC'" in str(sql).upper()
    assert "UNREAD_EVENTS" in str(sql).upper()


def test_utc_localization_preserves_microseconds_under_non_utc_connection() -> None:
    registry, axis = authority()
    backend = ibis.duckdb.connect(threads=1)
    try:
        backend.raw_sql("SET TimeZone = 'Asia/Shanghai'")
        stamp = datetime(2026, 2, 1, 12, 3, 4, 123456)
        result = backend.execute(event_instant(ibis.literal(stamp), axis, registry))
        assert result.astimezone(timezone.utc) == stamp.replace(tzinfo=timezone.utc)
        result_date = backend.execute(event_instant(ibis.literal(date(2026, 2, 1)), axis, registry))
        assert result_date.astimezone(timezone.utc) == datetime(2026, 2, 1, tzinfo=timezone.utc)
    finally:
        backend.disconnect()


def test_native_aware_non_utc_preserves_instant() -> None:
    registry, axis = authority("Asia/Shanghai")
    stamp = datetime(2026, 2, 1, 12, 3, 4, 999999, tzinfo=ZoneInfo("Asia/Shanghai"))
    backend = ibis.duckdb.connect(threads=1)
    try:
        normalized = event_instant(ibis.literal(stamp), axis, registry)
        assert normalized.type().timezone == "UTC"
        assert backend.execute(normalized).astimezone(timezone.utc) == stamp.astimezone(
            timezone.utc
        )
    finally:
        backend.disconnect()


@pytest.mark.parametrize("zone", [None, "Asia/Shanghai", "America/New_York"])
def test_naive_missing_or_non_utc_authority_rejected_before_source_work(zone: str | None) -> None:
    registry, axis = authority(zone)
    source = ibis.table({"occurred_at": "timestamp"}, name="unread_events")
    with pytest.raises(DatasetCompilationError, match="naive occurrence") as failure:
        event_instant(source.occurred_at, axis, registry)
    assert "source-required" in str(failure.value)
    assert failure.value.hint is not None and "UTC" in failure.value.hint


def test_string_parser_and_date_truncation_are_not_ignored() -> None:
    registry, axis = authority("UTC", format="%Y-%m-%d %H:%M:%S")
    source = ibis.table({"occurred_at": "string"}, name="unread_events")
    with pytest.raises(DatasetCompilationError, match="parser"):
        event_instant(source.occurred_at, axis, registry)
    native = ibis.table({"occurred_at": "timestamp"}, name="unread_native")
    with pytest.raises(DatasetCompilationError, match="parser"):
        event_instant(native.occurred_at, axis, registry)
    declaration = registry.dimensions[axis.ref.path]
    changed = replace(
        registry,
        dimensions={**registry.dimensions, axis.ref.path: replace(declaration, parse=DateParse())},
    )
    changed.freeze()
    with pytest.raises(DatasetCompilationError, match="parser"):
        event_instant(native.occurred_at, axis, changed)


def test_nanosecond_naive_and_aware_timestamps_reject_lossy_precision() -> None:
    registry, axis = authority()
    for physical in (dt.Timestamp(scale=9), dt.Timestamp(scale=9, timezone="UTC")):
        source = ibis.table({"occurred_at": physical}, name="unread_events")
        with pytest.raises(DatasetCompilationError, match="precision"):
            event_instant(source.occurred_at, axis, registry)


def test_captured_timezone_mismatch_and_non_temporal_values_reject() -> None:
    registry, axis = authority()
    timestamp = ibis.table({"occurred_at": "timestamp"}, name="unread_events").occurred_at
    with pytest.raises(DatasetCompilationError, match="declarations differ"):
        event_instant(timestamp, replace(axis, timezone="Asia/Shanghai"), registry)
    with pytest.raises(DatasetCompilationError, match="non-native"):
        event_instant(ibis.literal("2026-02-01T12:30:00Z"), axis, registry)
    with pytest.raises(DatasetCompilationError, match="non-native"):
        event_instant(ibis.literal(123456), axis, registry)


def test_native_datetime_parser_has_same_exact_utc_contract() -> None:
    registry, axis = authority()
    declaration = registry.dimensions[axis.ref.path]
    changed = replace(
        registry,
        dimensions={
            **registry.dimensions,
            axis.ref.path: replace(declaration, parse=DatetimeParse(timezone="UTC")),
        },
    )
    changed.freeze()
    normalized = event_instant(
        ibis.table({"occurred_at": "timestamp"}, name="unread_events").occurred_at, axis, changed
    )
    assert normalized.type().timezone == "UTC"
