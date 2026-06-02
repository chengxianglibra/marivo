from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import ibis
import pytest

from marivo.analysis.errors import DataTypeMismatchError, TimezoneInvalidError, WindowInvalidError
from marivo.analysis.executor.runner import (
    _validate_time_field_dtype,
    _window_bound_predicates,
    apply_time_series_bucket,
    apply_window_to_dataset,
)
from marivo.analysis.windows.spec import AbsoluteWindow


@dataclass(frozen=True)
class FakeMeta:
    data_type: str | None
    format: str | None = None
    timezone: str | None = None


@dataclass(frozen=True)
class FakeField:
    name: str
    column: str
    time_meta: FakeMeta
    is_time: bool = True

    def fn(self, table):
        return table[self.column]


@dataclass(frozen=True)
class FakeDataset:
    name: str
    fields: dict[str, FakeField]


def _dataset_ir_for(*, field_name: str, column: str, time_meta: FakeMeta) -> FakeDataset:
    field = FakeField(name=field_name, column=column, time_meta=time_meta)
    return FakeDataset(name="events", fields={field_name: field})


def test_window_bound_predicates_timestamp_date_only_end_uses_exclusive_next_midnight():
    table = ibis.table([("event_ts", "timestamp")], name="events")
    window = AbsoluteWindow(start="2026-05-01", end="2026-05-31", grain="day")
    lower, upper = _window_bound_predicates(
        table.event_ts,
        window,
        FakeMeta("timestamp"),
        session_tz=ZoneInfo("Asia/Shanghai"),
    )

    assert type(lower.op()).__name__ == "GreaterEqual"
    assert type(upper.op()).__name__ == "Less"
    assert lower.op().right.value == datetime(2026, 5, 1, 0, 0)
    assert upper.op().right.value == datetime(2026, 6, 1, 0, 0)


def test_window_bound_predicates_epoch_seconds_date_only_end_uses_exclusive_next_midnight():
    table = ibis.table([("event_ts", "int64")], name="events")
    window = AbsoluteWindow(start="2026-05-01", end="2026-05-31", grain="day")
    lower, upper = _window_bound_predicates(
        table.event_ts,
        window,
        FakeMeta("integer", "epoch_seconds"),
        session_tz=ZoneInfo("Asia/Shanghai"),
    )

    assert type(lower.op()).__name__ == "GreaterEqual"
    assert type(upper.op()).__name__ == "Less"
    assert lower.op().right.value == int(datetime(2026, 4, 30, 16, 0, tzinfo=UTC).timestamp())
    assert upper.op().right.value == int(datetime(2026, 5, 31, 16, 0, tzinfo=UTC).timestamp())


def test_apply_window_to_dataset_timestamp_date_only_uses_session_tz():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts TIMESTAMP)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-04-30 23:59:59'),"
        "(TIMESTAMP '2026-05-01 00:00:00'),"
        "(TIMESTAMP '2026-05-01 15:59:59'),"
        "(TIMESTAMP '2026-05-02 00:00:00')"
    )
    dataset_ir = _dataset_ir_for(
        field_name="event_ts",
        column="event_ts",
        time_meta=FakeMeta("timestamp"),
    )
    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-01",
            grain="day",
            time_field="event_ts",
        ),
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("Asia/Shanghai"),
    )
    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [datetime(2026, 5, 1, 0, 0), datetime(2026, 5, 1, 15, 59, 59)]


def test_apply_window_to_dataset_epoch_seconds_date_only_uses_session_tz():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts BIGINT)")
    con.raw_sql("INSERT INTO events VALUES (1777564799),(1777564800),(1777651199),(1777651200)")
    dataset_ir = _dataset_ir_for(
        field_name="event_ts",
        column="event_ts",
        time_meta=FakeMeta("integer", "epoch_seconds"),
    )
    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-01",
            grain="day",
            time_field="event_ts",
        ),
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("Asia/Shanghai"),
    )
    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [1777564800, 1777651199]


def test_apply_window_to_dataset_timestamp_explicit_datetime_end_is_exclusive():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts TIMESTAMP)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-05-01 00:00:00'),"
        "(TIMESTAMP '2026-05-01 12:00:00'),"
        "(TIMESTAMP '2026-05-01 12:00:01')"
    )
    dataset_ir = _dataset_ir_for(
        field_name="event_ts",
        column="event_ts",
        time_meta=FakeMeta("timestamp"),
    )
    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01T00:00:00",
            end="2026-05-01T12:00:00",
            time_field="event_ts",
        ),
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("UTC"),
    )
    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [datetime(2026, 5, 1, 0, 0)]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("not-an-iso", "2026-05-01"),
        ("2026-05-01", "2026-99-99"),
    ],
)
def test_window_bound_invalid_iso_raises_window_invalid_error(start: str, end: str):
    table = ibis.table([("event_ts", "timestamp")], name="events")
    window = AbsoluteWindow(start=start, end=end, grain="day")
    with pytest.raises(WindowInvalidError) as exc_info:
        _window_bound_predicates(
            table.event_ts,
            window,
            FakeMeta("timestamp"),
            session_tz=ZoneInfo("UTC"),
        )
    assert exc_info.value.details["kind"] == "WindowBoundInvalid"


def test_timezone_declaration_on_date_field_fails_closed():
    table = ibis.table([("order_date", "date")], name="events")
    window = AbsoluteWindow(start="2026-05-01", end="2026-05-01", grain="day")
    with pytest.raises(TimezoneInvalidError) as exc_info:
        _window_bound_predicates(
            table.order_date,
            window,
            FakeMeta("date", timezone="UTC"),
            session_tz=ZoneInfo("Asia/Shanghai"),
        )
    assert exc_info.value.details["kind"] == "TimezoneDeclarationUnsupported"
    assert exc_info.value.details["data_type"] == "date"


def test_timezone_declaration_on_partition_field_fails_closed():
    table = ibis.table([("order_day", "string")], name="events")
    window = AbsoluteWindow(start="2026-05-01", end="2026-05-01", grain="day")
    with pytest.raises(TimezoneInvalidError) as exc_info:
        _window_bound_predicates(
            table.order_day,
            window,
            FakeMeta("string", "yyyymmdd", timezone="UTC"),
            session_tz=ZoneInfo("Asia/Shanghai"),
        )
    assert exc_info.value.details["kind"] == "TimezoneDeclarationUnsupported"
    assert (
        exc_info.value.hint
        == "date and partition time fields do not support timezone declarations; use system timezone or a tz-aware timestamp field."
    )


def test_naive_timestamp_defaults_to_system_timezone_window():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts TIMESTAMP)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-04-30 15:59:59'),"
        "(TIMESTAMP '2026-04-30 16:00:00'),"
        "(TIMESTAMP '2026-05-01 00:00:00'),"
        "(TIMESTAMP '2026-05-01 23:59:59')"
    )
    dataset_ir = _dataset_ir_for(
        field_name="event_ts",
        column="event_ts",
        time_meta=FakeMeta("timestamp"),
    )

    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-01",
            grain="day",
            time_field="event_ts",
        ),
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [datetime(2026, 5, 1, 0, 0), datetime(2026, 5, 1, 23, 59, 59)]


def test_naive_timestamp_declared_utc_compares_as_instant():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts TIMESTAMP)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-04-30 15:59:59'),"
        "(TIMESTAMP '2026-04-30 16:00:00'),"
        "(TIMESTAMP '2026-05-01 15:59:59'),"
        "(TIMESTAMP '2026-05-01 16:00:00')"
    )
    dataset_ir = _dataset_ir_for(
        field_name="event_ts",
        column="event_ts",
        time_meta=FakeMeta("timestamp", timezone="UTC"),
    )

    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-01",
            grain="day",
            time_field="event_ts",
        ),
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [datetime(2026, 4, 30, 16, 0), datetime(2026, 5, 1, 15, 59, 59)]


def test_timezone_declaration_conflicting_with_tz_aware_field_fails_closed():
    table = ibis.table([("event_ts", 'timestamp("UTC")')], name="events")
    window = AbsoluteWindow(start="2026-05-01", end="2026-05-01", grain="day")
    with pytest.raises(TimezoneInvalidError) as exc_info:
        _window_bound_predicates(
            table.event_ts,
            window,
            FakeMeta("timestamp", timezone="Asia/Shanghai"),
            session_tz=ZoneInfo("Asia/Shanghai"),
        )
    assert exc_info.value.details["kind"] == "TimezoneDeclarationConflict"
    assert exc_info.value.details["declared"] == "Asia/Shanghai"
    assert exc_info.value.details["actual"] == "UTC"


def test_day_bucket_for_declared_utc_naive_timestamp_uses_session_local_day():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts TIMESTAMP)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-04-30 15:59:59'),"
        "(TIMESTAMP '2026-04-30 16:00:00')"
    )
    table = con.table("events")
    field = FakeField("event_ts", "event_ts", FakeMeta("timestamp", timezone="UTC"))

    bucketed = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2026-05-01", end="2026-05-01", grain="day"),
        session_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = bucketed.order_by("event_ts").execute()["bucket_start"].tolist()
    assert [item.strftime("%Y-%m-%d") for item in rows] == ["2026-04-30", "2026-05-01"]


def test_month_bucket_for_declared_utc_naive_timestamp_uses_session_local_month():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts TIMESTAMP)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-04-30 15:59:59'),"
        "(TIMESTAMP '2026-04-30 16:00:00')"
    )
    table = con.table("events")
    field = FakeField("event_ts", "event_ts", FakeMeta("timestamp", timezone="UTC"))

    bucketed = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2026-04-01", end="2026-05-31", grain="month"),
        session_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = bucketed.order_by("event_ts").execute()["bucket_start"].tolist()
    assert [str(item.date()) for item in rows] == ["2026-04-01", "2026-05-01"]


# ---------------------------------------------------------------------------
# _validate_time_field_dtype tests
# ---------------------------------------------------------------------------


def test_validate_time_field_dtype_date_declared_datetime_raises():
    """DateColumn with data_type='datetime' is a mismatch."""
    table = ibis.table([("dt", "date")], name="events")
    with pytest.raises(DataTypeMismatchError, match="data_type='datetime'"):
        _validate_time_field_dtype(table.dt, FakeMeta("datetime"))


def test_validate_time_field_dtype_date_declared_date_ok():
    """DateColumn with data_type='date' is compatible."""
    table = ibis.table([("dt", "date")], name="events")
    _validate_time_field_dtype(table.dt, FakeMeta("date"))


def test_validate_time_field_dtype_timestamp_declared_datetime_ok():
    """TimestampColumn with data_type='datetime' is compatible (ibis uses 'timestamp' for both)."""
    table = ibis.table([("ts", "timestamp")], name="events")
    _validate_time_field_dtype(table.ts, FakeMeta("datetime"))


def test_validate_time_field_dtype_timestamp_declared_timestamp_ok():
    """TimestampColumn with data_type='timestamp' is compatible."""
    table = ibis.table([("ts", "timestamp")], name="events")
    _validate_time_field_dtype(table.ts, FakeMeta("timestamp"))


def test_validate_time_field_dtype_timestamp_declared_date_raises():
    """TimestampColumn with data_type='date' is a mismatch."""
    table = ibis.table([("ts", "timestamp")], name="events")
    with pytest.raises(DataTypeMismatchError, match="data_type='date'"):
        _validate_time_field_dtype(table.ts, FakeMeta("date"))


def test_validate_time_field_dtype_none_data_type_skips():
    """If time_meta.data_type is None, skip silently."""
    table = ibis.table([("dt", "date")], name="events")
    meta_no_type = FakeMeta(data_type=None)
    _validate_time_field_dtype(table.dt, meta_no_type)


def test_apply_time_series_bucket_dtype_mismatch_raises():
    """apply_time_series_bucket raises DataTypeMismatchError (not TypeError) on mismatch."""
    table = ibis.table([("dt", "date")], name="events")
    field = FakeField(
        name="dt",
        column="dt",
        time_meta=FakeMeta("datetime"),
    )
    with pytest.raises(DataTypeMismatchError):
        apply_time_series_bucket(
            table,
            field_ir=field,
            window=AbsoluteWindow(start="2026-05-01", end="2026-05-31", grain="day"),
            session_tz=ZoneInfo("UTC"),
        )
