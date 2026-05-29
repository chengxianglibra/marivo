"""_encode_window_bound: ISO string -> physical value per time field format."""

import ibis
import pytest

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.executor.runner import (
    UTC_ZONE,
    _classify_strptime_format,
    _encode_window_bound,
    _resolve_strptime_format,
    _window_bound_predicates,
    apply_time_series_bucket,
    apply_window_to_dataset,
)
from marivo.analysis.windows.spec import AbsoluteWindow, normalize_window_input


class FakeMeta:
    def __init__(self, data_type, format=None, required_prefix=None):
        self.data_type = data_type
        self.format = format
        self.required_prefix = required_prefix


class FakeField:
    def __init__(self, name, data_type, format=None, required_prefix=None):
        self.name = name
        self.semantic_id = f"sales.{name}"
        self.is_time = True
        self.time_meta = FakeMeta(data_type, format, required_prefix)

    def fn(self, table):
        return table[self.name]


class FakeDataset:
    name = "orders"

    def __init__(self, fields):
        self.fields = {field.name: field for field in fields}


def test_date_passthrough():
    assert "2026" in str(_encode_window_bound("2026-07-01", FakeMeta("date")))


def test_timestamp_passthrough():
    assert "2026" in str(_encode_window_bound("2026-07-01T10:00:00", FakeMeta("timestamp")))


def test_string_yyyymmdd_encoding():
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "yyyymmdd")) == "20260701"


def test_string_dashed_encoding_is_identity():
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "yyyy-mm-dd")) == "2026-07-01"


def test_integer_yyyymmdd_encoding():
    assert _encode_window_bound("2026-07-01", FakeMeta("integer", "yyyymmdd")) == 20260701


def test_integer_epoch_seconds_encoding():
    out = _encode_window_bound("2026-07-01T00:00:00+00:00", FakeMeta("integer", "epoch_seconds"))
    assert out == 1782864000


def test_hh_format_raises():
    with pytest.raises(WindowInvalidError) as exc:
        _encode_window_bound("10", FakeMeta("integer", "hh"))
    assert "v1" in str(exc.value).lower() or "unsupported" in str(exc.value).lower()


def test_unknown_format_raises():
    with pytest.raises(WindowInvalidError):
        _encode_window_bound("2026-07-01", FakeMeta("string", "made_up"))


def _compile_window_filter(
    meta_data_type,
    format=None,
    *,
    ibis_type=None,
    column="log_date",
    start="2024-10-11",
    end="2025-07-31",
    session_tz=None,
):
    table = ibis.table({column: ibis_type or meta_data_type}, name="orders")
    lower, upper = _window_bound_predicates(
        table[column],
        AbsoluteWindow(start=start, end=end),
        FakeMeta(meta_data_type, format),
        session_tz=session_tz,
    )
    return ibis.duckdb.connect(":memory:").compile(table.filter(lower, upper))


def test_string_yyyymmdd_partition_predicate_uses_next_day_exclusive_upper():
    sql = _compile_window_filter("string", "yyyymmdd")
    assert "\"log_date\" >= '20241011'" in sql
    assert "\"log_date\" < '20250801'" in sql
    assert "CAST" not in sql.upper()


def test_string_yyyymmdd_partition_predicate_accepts_compact_window_bounds():
    sql = _compile_window_filter("string", "yyyymmdd", start="20241011", end="20250731")
    assert "\"log_date\" >= '20241011'" in sql
    assert "\"log_date\" < '20250801'" in sql


def test_string_dashed_partition_predicate_uses_next_day_exclusive_upper():
    sql = _compile_window_filter("string", "yyyy-mm-dd")
    assert "\"log_date\" >= '2024-10-11'" in sql
    assert "\"log_date\" < '2025-08-01'" in sql
    assert "CAST" not in sql.upper()


def test_integer_yyyymmdd_partition_predicate_uses_unquoted_next_day_upper():
    sql = _compile_window_filter("integer", "yyyymmdd", ibis_type="int64")
    assert '"log_date" >= 20241011' in sql
    assert '"log_date" < 20250801' in sql
    assert "'20241011'" not in sql


def test_string_yyyymmddhh_partition_predicate_uses_next_hour_exclusive_upper():
    sql = _compile_window_filter(
        "string",
        "yyyymmddhh",
        column="log_hour",
        start="2024-10-11T03:20:00",
        end="2025-07-31T14:00:00",
    )
    assert "\"log_hour\" >= '2024101103'" in sql
    assert "\"log_hour\" < '2025073115'" in sql
    assert "CAST" not in sql.upper()


def test_string_yyyymmddhh_partition_predicate_accepts_compact_hour_bounds():
    sql = _compile_window_filter(
        "string",
        "yyyymmddhh",
        column="log_hour",
        start="2024101103",
        end="2025073114",
    )
    assert "\"log_hour\" >= '2024101103'" in sql
    assert "\"log_hour\" < '2025073115'" in sql


def test_string_hour_precision_partition_predicate_supports_separator_formats():
    dashed = _compile_window_filter(
        "string",
        "yyyymmdd-hh",
        column="log_hour",
        start="2024-10-11T03:00:00",
        end="2025-07-31T14:00:00",
    )
    iso_dashed = _compile_window_filter(
        "string",
        "yyyy-mm-dd-hh",
        column="log_hour",
        start="2024-10-11T03:00:00",
        end="2025-07-31T14:00:00",
    )
    tee = _compile_window_filter(
        "string",
        "yyyymmddthh",
        column="log_hour",
        start="2024-10-11T03:00:00",
        end="2025-07-31T14:00:00",
    )
    assert "\"log_hour\" >= '20241011-03'" in dashed
    assert "\"log_hour\" < '20250731-15'" in dashed
    assert "\"log_hour\" >= '2024-10-11-03'" in iso_dashed
    assert "\"log_hour\" < '2025-07-31-15'" in iso_dashed
    assert "\"log_hour\" >= '20241011T03'" in tee
    assert "\"log_hour\" < '20250731T15'" in tee


def _compile_composite_window_filter(
    *,
    date_data_type="string",
    date_format="yyyymmdd",
    hour_data_type="string",
    hour_format="hh",
    start="2024-10-11T03:00:00",
    end="2024-10-11T14:00:00",
):
    table = ibis.table(
        {
            "log_date": "int64" if date_data_type == "integer" else "string",
            "log_hour": "int64" if hour_data_type == "integer" else "string",
        },
        name="orders",
    )
    dataset = FakeDataset(
        [
            FakeField("log_date", date_data_type, date_format),
            FakeField("log_hour", hour_data_type, hour_format, required_prefix="log_date"),
        ]
    )
    expr = apply_window_to_dataset(
        table,
        AbsoluteWindow(start=start, end=end, time_field="log_hour"),
        dataset_ir=dataset,
    )
    return ibis.duckdb.connect(":memory:").compile(expr)


def test_composite_string_hour_partition_predicate_uses_raw_fields():
    sql = _compile_composite_window_filter()
    assert "\"log_date\" = '20241011'" in sql
    assert "\"log_hour\" >= '03'" in sql
    assert "\"log_hour\" < '15'" in sql
    assert "CAST" not in sql.upper()


def test_composite_integer_hour_partition_predicate_uses_unquoted_fields():
    sql = _compile_composite_window_filter(
        date_data_type="integer",
        hour_data_type="integer",
        hour_format="h",
    )
    assert '"log_date" = 20241011' in sql
    assert '"log_hour" >= 3' in sql
    assert '"log_hour" < 15' in sql
    assert "'20241011'" not in sql
    assert "'03'" not in sql


def test_composite_cross_day_hour_partition_predicate_ors_day_clauses():
    sql = _compile_composite_window_filter(
        start="2024-10-11T22:00:00",
        end="2024-10-13T02:00:00",
    )
    assert "\"log_date\" = '20241011'" in sql
    assert "\"log_hour\" >= '22'" in sql
    assert "\"log_date\" > '20241011'" in sql
    assert "\"log_date\" < '20241013'" in sql
    assert "\"log_date\" = '20241013'" in sql
    assert "\"log_hour\" < '03'" in sql


# ---------------------------------------------------------------------------
# Strptime format support
# ---------------------------------------------------------------------------


def test_classify_strptime_day_formats():
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%d-%b-%Y"):
        assert _classify_strptime_format(fmt) == "day", fmt


def test_classify_strptime_hour_formats():
    for fmt in ("%Y%m%d%H", "%Y-%m-%d %H", "%Y%m%dT%H"):
        assert _classify_strptime_format(fmt) == "hour", fmt


def test_classify_strptime_minute_formats():
    assert _classify_strptime_format("%Y-%m-%d %H:%M") == "minute"


def test_classify_strptime_sub_hour_formats():
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        assert _classify_strptime_format(fmt) == "sub_hour", fmt


def test_classify_strptime_hour_only():
    assert _classify_strptime_format("%H") == "hour_only"


def test_classify_strptime_hour_minute_only():
    assert _classify_strptime_format("%H:%M") == "hour_only_minute"


def test_resolve_strptime_shorthand_aliases():
    assert _resolve_strptime_format("yyyymmdd") == "%Y%m%d"
    assert _resolve_strptime_format("yyyy-mm-dd") == "%Y-%m-%d"
    assert _resolve_strptime_format("yyyymmddhh") == "%Y%m%d%H"


def test_resolve_strptime_passthrough():
    assert _resolve_strptime_format("%Y/%m/%d") == "%Y/%m/%d"
    assert _resolve_strptime_format("%Y%m%d%H") == "%Y%m%d%H"


def test_resolve_strptime_hour_only_passthrough():
    assert _resolve_strptime_format("hh") == "hh"
    assert _resolve_strptime_format("h") == "h"
    assert _resolve_strptime_format("int") == "int"


def test_resolve_strptime_none_returns_none():
    assert _resolve_strptime_format(None) is None


def test_strptime_string_slash_format_encoding():
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "%Y/%m/%d")) == "2026/07/01"


def test_strptime_string_compact_hour_encoding():
    assert (
        _encode_window_bound("2026-07-01T03:00:00", FakeMeta("string", "%Y%m%d%H")) == "2026070103"
    )


def test_strptime_string_non_lexicographic_encoding():
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "%d/%m/%Y")) == "01/07/2026"


def test_strptime_integer_day_encoding():
    assert _encode_window_bound("2026-07-01", FakeMeta("integer", "%Y%m%d")) == 20260701


def test_strptime_existing_shorthand_encoding_unchanged():
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "yyyymmdd")) == "20260701"
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "yyyy-mm-dd")) == "2026-07-01"


def test_strptime_unresolvable_format_encoding_raises():
    with pytest.raises(WindowInvalidError):
        _encode_window_bound("2026-07-01", FakeMeta("string", "made_up"))


def test_strptime_day_uses_parsed_comparison():
    sql = _compile_window_filter("string", "%Y/%m/%d", session_tz=UTC_ZONE)
    assert "STRPTIME" in sql
    assert "MAKE_DATE(2024, 10, 11)" in sql


def test_strptime_hour_uses_parsed_comparison():
    sql = _compile_window_filter(
        "string",
        "%Y%m%d%H",
        column="log_hour",
        start="2024-10-11T03:00:00",
        end="2025-07-31T14:00:00",
        session_tz=UTC_ZONE,
    )
    assert "STRPTIME" in sql
    assert "MAKE_TIMESTAMPTZ" in sql


def test_strptime_non_lexicographic_produces_correct_predicate():
    sql = _compile_window_filter("string", "%m/%d/%Y", session_tz=UTC_ZONE)
    assert "STRPTIME" in sql
    assert "MAKE_DATE(2024, 10, 11)" in sql


def test_strptime_existing_shorthand_uses_raw_string_comparison():
    sql = _compile_window_filter("string", "yyyymmdd", session_tz=UTC_ZONE)
    assert "STRPTIME" not in sql
    assert "'20241011'" in sql


def test_strptime_minute_uses_timestamp_comparison():
    sql = _compile_window_filter(
        "string",
        "%Y-%m-%d %H:%M",
        column="log_ts",
        start="2024-10-11T03:00:00",
        end="2025-07-31T14:00:00",
        session_tz=UTC_ZONE,
    )
    assert "STRPTIME" in sql
    assert "MAKE_TIMESTAMPTZ" in sql


def test_strptime_day_bucket():
    table = ibis.table({"log_date": "string"}, name="orders")
    field = FakeField("log_date", "string", "%Y/%m/%d")
    result = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="day"),
        session_tz=UTC_ZONE,
    )
    sql = ibis.duckdb.connect(":memory:").compile(result)
    assert "STRPTIME" in sql


def test_strptime_hour_bucket():
    table = ibis.table({"log_hour": "string"}, name="orders")
    field = FakeField("log_hour", "string", "%Y%m%d%H")
    result = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="day"),
        session_tz=UTC_ZONE,
    )
    sql = ibis.duckdb.connect(":memory:").compile(result)
    assert "STRPTIME" in sql


def test_strptime_minute_bucket():
    table = ibis.table({"log_ts": "string"}, name="orders")
    field = FakeField("log_ts", "string", "%Y-%m-%d %H:%M")
    result = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="hour"),
        session_tz=UTC_ZONE,
    )
    sql = ibis.duckdb.connect(":memory:").compile(result)
    assert "STRPTIME" in sql


def test_strptime_session_tz_none_raises():
    with pytest.raises(WindowInvalidError, match="session timezone"):
        _compile_window_filter("string", "%Y/%m/%d", session_tz=None)


def test_parse_string_column_unresolvable_format_raises():
    from marivo.analysis.executor.runner import _parse_string_column

    with pytest.raises(WindowInvalidError, match="resolvable strptime format"):
        _parse_string_column(ibis.literal("x"), FakeMeta("string", "made_up"))


def test_parse_string_column_unsupported_data_type_raises():
    from marivo.analysis.executor.runner import _parse_string_column

    with pytest.raises(WindowInvalidError, match="only supports string/integer"):
        _parse_string_column(ibis.literal("x"), FakeMeta("timestamp", "%Y-%m-%d"))


# ---------------------------------------------------------------------------
# Window tz field rejection
# ---------------------------------------------------------------------------


def test_absolute_window_rejects_tz_field():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_window_input({"start": "2026-05-01", "end": "2026-05-31", "tz": "UTC"})

    assert exc_info.value.details["kind"] == "WindowModelInvalid"
    assert any(error["loc"] == ("tz",) for error in exc_info.value.details["validation_errors"])


def test_relative_window_rejects_tz_field():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_window_input({"expr": "today", "tz": "UTC"})

    assert exc_info.value.details["kind"] == "WindowModelInvalid"
    assert any(error["loc"] == ("tz",) for error in exc_info.value.details["validation_errors"])
