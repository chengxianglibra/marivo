from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import ibis
import pytest

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.executor.runner import _window_bound_predicates, apply_window_to_dataset
from marivo.analysis.windows.spec import AbsoluteWindow


@dataclass(frozen=True)
class FakeMeta:
    data_type: str
    format: str | None = None


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
    assert lower.op().right.value == datetime(2026, 4, 30, 16, 0, tzinfo=UTC)
    assert upper.op().right.value == datetime(2026, 5, 31, 16, 0, tzinfo=UTC)


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


def test_apply_window_to_dataset_timestamp_date_only_uses_window_tz_override():
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
        time_meta=FakeMeta("timestamp"),
    )
    filtered = apply_window_to_dataset(
        con.table("events"),
        AbsoluteWindow(
            start="2026-05-01",
            end="2026-05-01",
            grain="day",
            tz="Asia/Shanghai",
            time_field="event_ts",
        ),
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("UTC"),
    )
    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [datetime(2026, 4, 30, 16, 0), datetime(2026, 5, 1, 15, 59, 59)]


def test_apply_window_to_dataset_epoch_seconds_date_only_uses_mapping_tz_override():
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
        {
            "start": "2026-05-01",
            "end": "2026-05-01",
            "grain": "day",
            "tz": "Asia/Shanghai",
            "time_field": "event_ts",
        },
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("UTC"),
    )
    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [1777564800, 1777651199]


def test_apply_window_to_dataset_timestamp_explicit_datetime_end_is_inclusive():
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
            tz="UTC",
            time_field="event_ts",
        ),
        dataset_ir=dataset_ir,
        session_tz=ZoneInfo("UTC"),
    )
    rows = filtered.order_by("event_ts").execute()["event_ts"].tolist()
    assert rows == [datetime(2026, 5, 1, 0, 0), datetime(2026, 5, 1, 12, 0)]


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
