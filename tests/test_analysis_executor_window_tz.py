from dataclasses import dataclass
from datetime import datetime
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
    parse_kind: str | None = None
    granularity: str | None = None


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


class FakeTypedExpr:
    def __init__(self, dtype: object) -> None:
        self._dtype = dtype

    def type(self) -> object:
        return self._dtype


def _dataset_ir_for(*, field_name: str, column: str, time_meta: FakeMeta) -> FakeDataset:
    field = FakeField(name=field_name, column=column, time_meta=time_meta)
    return FakeDataset(name="events", fields={field_name: field})


def test_window_bound_predicates_timestamp_date_only_end_uses_exclusive_end_date():
    table = ibis.table([("event_ts", "timestamp")], name="events")
    window = AbsoluteWindow(start="2026-05-01", end="2026-05-31", grain="day")
    lower, upper = _window_bound_predicates(
        table.event_ts,
        window,
        FakeMeta("timestamp", parse_kind="timestamp"),
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
    )

    assert type(lower.op()).__name__ == "GreaterEqual"
    assert type(upper.op()).__name__ == "Less"
    assert lower.op().right.value == datetime(2026, 5, 1, 0, 0)
    assert upper.op().right.value == datetime(2026, 5, 31, 0, 0)


def test_apply_window_to_dataset_timestamp_date_only_uses_report_tz():
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
        time_meta=FakeMeta("timestamp", parse_kind="timestamp"),
    )
    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-02",
            grain="day",
            time_dimension="event_ts",
        ),
        dataset_ir=dataset_ir,
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
    )
    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [datetime(2026, 5, 1, 0, 0), datetime(2026, 5, 1, 15, 59, 59)]


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
        time_meta=FakeMeta("timestamp", parse_kind="timestamp"),
    )
    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01T00:00:00",
            end="2026-05-01T12:00:00",
            time_dimension="event_ts",
        ),
        dataset_ir=dataset_ir,
        report_tz=ZoneInfo("UTC"),
        datasource_read_tz=ZoneInfo("UTC"),
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
            FakeMeta("timestamp", parse_kind="timestamp"),
            report_tz=ZoneInfo("UTC"),
            datasource_read_tz=ZoneInfo("UTC"),
        )
    assert exc_info.value.details["kind"] == "WindowBoundInvalid"


def test_timezone_declaration_on_date_field_fails_closed():
    table = ibis.table([("order_date", "date")], name="events")
    window = AbsoluteWindow(start="2026-05-01", end="2026-05-01", grain="day")
    with pytest.raises(TimezoneInvalidError) as exc_info:
        _window_bound_predicates(
            table.order_date,
            window,
            FakeMeta("date", parse_kind="date", timezone="UTC"),
            report_tz=ZoneInfo("Asia/Shanghai"),
            datasource_read_tz=ZoneInfo("Asia/Shanghai"),
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
            FakeMeta("string", "%Y%m%d", parse_kind="strptime", timezone="UTC"),
            report_tz=ZoneInfo("Asia/Shanghai"),
            datasource_read_tz=ZoneInfo("Asia/Shanghai"),
        )
    assert exc_info.value.details["kind"] == "TimezoneDeclarationUnsupported"
    assert (
        exc_info.value.hint
        == "date and partition time fields do not support timezone declarations; remove timezone= or use a time-bearing datetime/timestamp parse."
    )


def test_time_bearing_string_timezone_compares_as_declared_instant():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts VARCHAR)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "('2026-04-30 15:59:59'),"
        "('2026-04-30 16:00:00'),"
        "('2026-05-01 15:59:59'),"
        "('2026-05-01 16:00:00')"
    )
    dataset_ir = _dataset_ir_for(
        field_name="event_ts",
        column="event_ts",
        time_meta=FakeMeta("string", "%Y-%m-%d %H:%M:%S", parse_kind="strptime", timezone="UTC"),
    )

    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-02",
            grain="day",
            time_dimension="event_ts",
        ),
        dataset_ir=dataset_ir,
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == ["2026-04-30 16:00:00", "2026-05-01 15:59:59"]


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
        time_meta=FakeMeta("timestamp", parse_kind="timestamp"),
    )

    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-02",
            grain="day",
            time_dimension="event_ts",
        ),
        dataset_ir=dataset_ir,
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
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
            end="2026-05-02",
            grain="day",
            time_dimension="event_ts",
        ),
        dataset_ir=dataset_ir,
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
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
            report_tz=ZoneInfo("Asia/Shanghai"),
            datasource_read_tz=ZoneInfo("Asia/Shanghai"),
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
        window=AbsoluteWindow(start="2026-05-01", end="2026-05-02", grain="day"),
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
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
        window=AbsoluteWindow(start="2026-04-01", end="2026-06-01", grain="month"),
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = bucketed.order_by("event_ts").execute()["bucket_start"].tolist()
    assert [str(item.date()) for item in rows] == ["2026-04-01", "2026-05-01"]


def test_subday_bucket_for_declared_utc_string_uses_session_local_time():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts VARCHAR)")
    con.raw_sql("INSERT INTO events VALUES ('2026-04-30 16:15:00'),('2026-04-30 16:35:00')")
    table = con.table("events")
    field = FakeField(
        "event_ts",
        "event_ts",
        FakeMeta("string", "%Y-%m-%d %H:%M:%S", parse_kind="strptime", timezone="UTC"),
    )

    bucketed = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-02",
            grain=(30, "minute"),
            time_dimension="event_ts",
        ),
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = bucketed.order_by("event_ts").execute()["bucket_start"].tolist()
    assert [str(item) for item in rows] == [
        "2026-05-01 00:00:00",
        "2026-05-01 00:30:00",
    ]


def test_subday_bucket_for_same_timezone_string_does_not_shift():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE events (event_ts VARCHAR)")
    con.raw_sql("INSERT INTO events VALUES ('2026-04-30 16:15:00'),('2026-05-01 00:15:00')")
    table = con.table("events")
    field = FakeField(
        "event_ts",
        "event_ts",
        FakeMeta("string", "%Y-%m-%d %H:%M:%S", parse_kind="strptime", timezone="Asia/Shanghai"),
    )

    bucketed = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(
            start="2026-04-30",
            end="2026-05-02",
            grain=(30, "minute"),
            time_dimension="event_ts",
        ),
        report_tz=ZoneInfo("Asia/Shanghai"),
        datasource_read_tz=ZoneInfo("Asia/Shanghai"),
    )

    rows = bucketed.order_by("event_ts").execute()["bucket_start"].tolist()
    assert [str(item) for item in rows] == [
        "2026-04-30 16:00:00",
        "2026-05-01 00:00:00",
    ]


# ---------------------------------------------------------------------------
# _validate_time_field_dtype tests
# ---------------------------------------------------------------------------


def test_validate_time_field_dtype_date_declared_datetime_raises():
    """DateColumn with data_type='datetime' is a mismatch."""
    table = ibis.table([("dt", "date")], name="events")
    with pytest.raises(DataTypeMismatchError, match="data_type='datetime'"):
        _validate_time_field_dtype(table.dt, FakeMeta("datetime", parse_kind="datetime"))


def test_validate_time_field_dtype_date_declared_date_ok():
    """DateColumn with data_type='date' is compatible."""
    table = ibis.table([("dt", "date")], name="events")
    _validate_time_field_dtype(table.dt, FakeMeta("date", parse_kind="date"))


def test_validate_time_field_dtype_non_nullable_date_declared_date_ok():
    """Non-nullable DateColumn with data_type='date' is compatible."""
    expr = FakeTypedExpr(ibis.dtype("!date"))
    _validate_time_field_dtype(expr, FakeMeta("date", parse_kind="date"))


def test_validate_time_field_dtype_timestamp_declared_datetime_ok():
    """TimestampColumn with data_type='datetime' is compatible (ibis uses 'timestamp' for both)."""
    table = ibis.table([("ts", "timestamp")], name="events")
    _validate_time_field_dtype(table.ts, FakeMeta("datetime", parse_kind="datetime"))


def test_validate_time_field_dtype_timestamp_declared_timestamp_ok():
    """TimestampColumn with data_type='timestamp' is compatible."""
    table = ibis.table([("ts", "timestamp")], name="events")
    _validate_time_field_dtype(table.ts, FakeMeta("timestamp", parse_kind="timestamp"))


def test_validate_time_field_dtype_non_nullable_timestamp_declared_temporal_ok():
    """Non-nullable TimestampColumn remains compatible with timestamp declarations."""
    expr = FakeTypedExpr(ibis.dtype("!timestamp(6)"))
    _validate_time_field_dtype(expr, FakeMeta("timestamp", parse_kind="timestamp"))
    _validate_time_field_dtype(expr, FakeMeta("datetime", parse_kind="datetime"))


def test_validate_time_field_dtype_timestamp_declared_date_raises():
    """TimestampColumn with data_type='date' is a mismatch."""
    table = ibis.table([("ts", "timestamp")], name="events")
    with pytest.raises(DataTypeMismatchError, match="data_type='date'"):
        _validate_time_field_dtype(table.ts, FakeMeta("date", parse_kind="date"))


def test_validate_time_field_dtype_non_nullable_timestamp_declared_date_raises():
    """Non-nullable TimestampColumn with data_type='date' remains a mismatch."""
    expr = FakeTypedExpr(ibis.dtype("!timestamp(6)"))
    with pytest.raises(DataTypeMismatchError, match="data_type='date'"):
        _validate_time_field_dtype(expr, FakeMeta("date", parse_kind="date"))


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
        time_meta=FakeMeta("datetime", parse_kind="datetime"),
    )
    with pytest.raises(DataTypeMismatchError):
        apply_time_series_bucket(
            table,
            field_ir=field,
            window=AbsoluteWindow(start="2026-05-01", end="2026-05-31", grain="day"),
            report_tz=ZoneInfo("UTC"),
            datasource_read_tz=ZoneInfo("UTC"),
        )


def test_validate_time_field_dtype_deferred_date_with_hour_granularity_raises():
    """Deferred-parse date column with sub-day granularity must fail closed."""
    table = ibis.table([("dt", "date")], name="events")
    with pytest.raises(DataTypeMismatchError, match="sub-day resolution"):
        _validate_time_field_dtype(
            table.dt, FakeMeta(data_type=None, parse_kind=None, granularity="hour")
        )


def test_validate_time_field_dtype_deferred_date_with_minute_granularity_raises():
    """Deferred-parse date column with minute granularity must fail closed."""
    table = ibis.table([("dt", "date")], name="events")
    with pytest.raises(DataTypeMismatchError, match="sub-day resolution"):
        _validate_time_field_dtype(
            table.dt, FakeMeta(data_type=None, parse_kind=None, granularity="minute")
        )


def test_validate_time_field_dtype_deferred_date_with_day_granularity_ok():
    """Deferred-parse date column with day granularity is fine."""
    table = ibis.table([("dt", "date")], name="events")
    _validate_time_field_dtype(
        table.dt, FakeMeta(data_type=None, parse_kind=None, granularity="day")
    )


def test_validate_time_field_dtype_deferred_string_without_parse_raises():
    """Deferred-parse on a string column must error — string requires ms.strptime."""
    table = ibis.table([("dt", "string")], name="events")
    with pytest.raises(DataTypeMismatchError, match="not a native temporal type"):
        _validate_time_field_dtype(
            table.dt, FakeMeta(data_type=None, parse_kind=None, granularity="day")
        )


def test_validate_time_field_dtype_deferred_integer_without_parse_raises():
    """Deferred-parse on an integer column must error — integer requires ms.strptime."""
    table = ibis.table([("dt", "int32")], name="events")
    with pytest.raises(DataTypeMismatchError, match="not a native temporal type"):
        _validate_time_field_dtype(
            table.dt, FakeMeta(data_type=None, parse_kind=None, granularity="day")
        )


# ---------------------------------------------------------------------------
# _column_timezone read-timezone fallback tests
# ---------------------------------------------------------------------------


def test_column_timezone_uses_datasource_read_timezone_when_undeclared() -> None:
    from marivo.analysis.executor.runner import _column_timezone

    class _TimeMeta:
        timezone = None

    assert _column_timezone(_TimeMeta(), datasource_read_tz=ZoneInfo("Asia/Shanghai")) == ZoneInfo(
        "Asia/Shanghai"
    )


def test_column_timezone_prefers_declared_timezone_over_datasource_read_timezone() -> None:
    from marivo.analysis.executor.runner import _column_timezone

    class _TimeMeta:
        timezone = "UTC"

    assert _column_timezone(_TimeMeta(), datasource_read_tz=ZoneInfo("Asia/Shanghai")) == ZoneInfo(
        "UTC"
    )
