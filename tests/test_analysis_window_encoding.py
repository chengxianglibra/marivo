"""_encode_window_bound: ISO string -> physical value per time field format."""

import ibis
import pandas as pd
import pytest

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.executor.runner import (
    UTC_ZONE,
    StrptimeToJodaError,
    _classify_strptime_format,
    _encode_window_bound,
    _fix_trino_date_parse,
    _resolve_strptime_format,
    _strptime_to_joda,
    _window_bound_predicates,
    apply_time_series_bucket,
    apply_window_to_dataset,
    ensure_bucket_start_timestamp,
)
from marivo.analysis.windows.grain import Grain
from marivo.analysis.windows.spec import AbsoluteWindow, normalize_timescope_input


class FakeMeta:
    def __init__(self, data_type, format=None, required_prefix=None, granularity=None):
        self.data_type = data_type
        self.format = format
        self.required_prefix = required_prefix
        self.granularity = granularity


class FakeField:
    def __init__(self, name, data_type, format=None, required_prefix=None, granularity=None):
        self.name = name
        self.semantic_id = f"sales.{name}"
        self.is_time = True
        self.time_meta = FakeMeta(data_type, format, required_prefix, granularity)

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


def test_string_yyyymmdd_partition_predicate_uses_exclusive_end_date():
    sql = _compile_window_filter("string", "yyyymmdd")
    assert "\"log_date\" >= '20241011'" in sql
    assert "\"log_date\" < '20250731'" in sql
    assert "CAST" not in sql.upper()


def test_string_yyyymmdd_partition_predicate_accepts_compact_window_bounds():
    sql = _compile_window_filter("string", "yyyymmdd", start="20241011", end="20250731")
    assert "\"log_date\" >= '20241011'" in sql
    assert "\"log_date\" < '20250731'" in sql


def test_string_dashed_partition_predicate_uses_exclusive_end_date():
    sql = _compile_window_filter("string", "yyyy-mm-dd")
    assert "\"log_date\" >= '2024-10-11'" in sql
    assert "\"log_date\" < '2025-07-31'" in sql
    assert "CAST" not in sql.upper()


def test_integer_yyyymmdd_partition_predicate_uses_unquoted_exclusive_end():
    sql = _compile_window_filter("integer", "yyyymmdd", ibis_type="int64")
    assert '"log_date" >= 20241011' in sql
    assert '"log_date" < 20250731' in sql
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
# Timescope field rejection
# ---------------------------------------------------------------------------


def test_timescope_rejects_tz_field():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input({"start": "2026-05-01", "end": "2026-05-31", "tz": "UTC"})

    assert exc_info.value.details["kind"] == "TimeScopeModelInvalid"


# ---------------------------------------------------------------------------
# Hour-only string/int bucket with required_prefix
# ---------------------------------------------------------------------------


def _hour_bucket_dataset():
    """Build a FakeDataset with a day-level log_date and hour-only log_hour."""
    return FakeDataset(
        [
            FakeField("log_date", "string", "yyyy-mm-dd", granularity="day"),
            FakeField("log_hour", "string", "hh", required_prefix="log_date", granularity="hour"),
        ]
    )


def test_hour_only_bucket_grain_matches_field():
    """Grain == field granularity: combine prefix date + hour into timestamp."""
    table = ibis.table({"log_date": "string", "log_hour": "string"}, name="orders")
    field = FakeField("log_hour", "string", "hh", required_prefix="log_date", granularity="hour")
    result = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="hour"),
        session_tz=UTC_ZONE,
        dataset_ir=_hour_bucket_dataset(),
    )
    sql = ibis.duckdb.connect(":memory:").compile(result)
    assert "bucket_start" in sql


def test_hour_only_bucket_grain_coarser_uses_prefix_date():
    """Grain > field granularity: use prefix date only, truncate to grain."""
    table = ibis.table({"log_date": "string", "log_hour": "string"}, name="orders")
    field = FakeField("log_hour", "string", "hh", required_prefix="log_date", granularity="hour")
    result = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="day"),
        session_tz=UTC_ZONE,
        dataset_ir=_hour_bucket_dataset(),
    )
    sql = ibis.duckdb.connect(":memory:").compile(result)
    assert "bucket_start" in sql


def test_hour_only_bucket_grain_finer_raises():
    """Grain < field granularity: raise WindowInvalidError."""
    table = ibis.table({"log_date": "string", "log_hour": "string"}, name="orders")
    field = FakeField("log_hour", "string", "hh", required_prefix="log_date", granularity="hour")
    with pytest.raises(WindowInvalidError, match="finer than"):
        apply_time_series_bucket(
            table,
            field_ir=field,
            window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="minute"),
            session_tz=UTC_ZONE,
            dataset_ir=_hour_bucket_dataset(),
        )


def test_unresolvable_string_format_raises():
    """Unresolvable string format raises explicit error instead of crashing."""
    table = ibis.table({"log_date": "string"}, name="orders")
    field = FakeField("log_date", "string", "unresolvable_format")
    with pytest.raises(WindowInvalidError, match="cannot compute bucket"):
        apply_time_series_bucket(
            table,
            field_ir=field,
            window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="day"),
            session_tz=UTC_ZONE,
        )


# ---------------------------------------------------------------------------
# Strptime hour-only format (%H) with required_prefix
# ---------------------------------------------------------------------------


def _strptime_hour_bucket_dataset():
    """FakeDataset with day-level prefix + strptime hour-only field."""
    return FakeDataset(
        [
            FakeField("log_date", "string", "yyyy-mm-dd", granularity="day"),
            FakeField("log_hour", "string", "%H", required_prefix="log_date", granularity="hour"),
        ]
    )


def test_strptime_hour_only_bucket_grain_matches():
    """format='%H' with required_prefix routes to _apply_hour_only_bucket."""
    table = ibis.table({"log_date": "string", "log_hour": "string"}, name="orders")
    field = FakeField("log_hour", "string", "%H", required_prefix="log_date", granularity="hour")
    result = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="hour"),
        session_tz=UTC_ZONE,
        dataset_ir=_strptime_hour_bucket_dataset(),
    )
    sql = ibis.duckdb.connect(":memory:").compile(result)
    assert "bucket_start" in sql


def test_strptime_hour_only_bucket_grain_coarser():
    """format='%H' with grain=day uses prefix date only."""
    table = ibis.table({"log_date": "string", "log_hour": "string"}, name="orders")
    field = FakeField("log_hour", "string", "%H", required_prefix="log_date", granularity="hour")
    result = apply_time_series_bucket(
        table,
        field_ir=field,
        window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="day"),
        session_tz=UTC_ZONE,
        dataset_ir=_strptime_hour_bucket_dataset(),
    )
    sql = ibis.duckdb.connect(":memory:").compile(result)
    assert "bucket_start" in sql


def test_strptime_hour_only_bucket_grain_finer_raises():
    """format='%H' with grain=minute raises finer-than error."""
    table = ibis.table({"log_date": "string", "log_hour": "string"}, name="orders")
    field = FakeField("log_hour", "string", "%H", required_prefix="log_date", granularity="hour")
    with pytest.raises(WindowInvalidError, match="finer than"):
        apply_time_series_bucket(
            table,
            field_ir=field,
            window=AbsoluteWindow(start="2024-10-11", end="2025-07-31", grain="minute"),
            session_tz=UTC_ZONE,
            dataset_ir=_strptime_hour_bucket_dataset(),
        )


# ---------------------------------------------------------------------------
# ensure_bucket_start_timestamp — post-execution string → timestamp
# ---------------------------------------------------------------------------


def test_ensure_bucket_start_timestamp_yyyymmdd_hour_grain():
    """yyyymmdd prefix + hour grain: "2026060100" → pd.Timestamp("2026-06-01 00:00")."""
    dataset = FakeDataset(
        [
            FakeField("log_date", "string", "yyyymmdd", granularity="day"),
            FakeField("log_hour", "string", "hh", required_prefix="log_date", granularity="hour"),
        ]
    )
    series = pd.Series(["2026060100", "2026060101", "2026060123"])
    time_meta = FakeMeta("string", "hh", required_prefix="log_date", granularity="hour")
    result = ensure_bucket_start_timestamp(
        series,
        time_meta=time_meta,
        dataset_ir=dataset,
        grain=Grain(count=1, unit="hour"),
    )
    assert pd.api.types.is_datetime64_any_dtype(result)
    assert result.iloc[0] == pd.Timestamp("2026-06-01 00:00")
    assert result.iloc[2] == pd.Timestamp("2026-06-01 23:00")


def test_ensure_bucket_start_timestamp_yyyymmdd_day_grain():
    """yyyymmdd prefix + day grain: "20260601" → pd.Timestamp("2026-06-01")."""
    dataset = FakeDataset(
        [
            FakeField("log_date", "string", "yyyymmdd", granularity="day"),
            FakeField("log_hour", "string", "hh", required_prefix="log_date", granularity="hour"),
        ]
    )
    series = pd.Series(["20260601", "20260602"])
    time_meta = FakeMeta("string", "hh", required_prefix="log_date", granularity="hour")
    result = ensure_bucket_start_timestamp(
        series,
        time_meta=time_meta,
        dataset_ir=dataset,
        grain=Grain(count=1, unit="day"),
    )
    assert pd.api.types.is_datetime64_any_dtype(result)
    assert result.iloc[0] == pd.Timestamp("2026-06-01")


def test_ensure_bucket_start_timestamp_yyyy_mm_dd_hour_grain():
    """yyyy-mm-dd prefix + hour grain: "2026-06-01-08" → pd.Timestamp("2026-06-01 08:00")."""
    series = pd.Series(["2026-06-01-08", "2026-06-01-23"])
    time_meta = FakeMeta("string", "hh", required_prefix="log_date", granularity="hour")
    result = ensure_bucket_start_timestamp(
        series,
        time_meta=time_meta,
        dataset_ir=_hour_bucket_dataset(),
        grain=Grain(count=1, unit="hour"),
    )
    assert pd.api.types.is_datetime64_any_dtype(result)
    assert result.iloc[0] == pd.Timestamp("2026-06-01 08:00")
    assert result.iloc[1] == pd.Timestamp("2026-06-01 23:00")


def test_ensure_bucket_start_timestamp_skips_non_string():
    """Non-string bucket_start is returned unchanged."""
    series = pd.Series([pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-02")])
    time_meta = FakeMeta("string", "hh", required_prefix="log_date", granularity="hour")
    result = ensure_bucket_start_timestamp(
        series,
        time_meta=time_meta,
        dataset_ir=_hour_bucket_dataset(),
        grain=Grain(count=1, unit="hour"),
    )
    assert result is series


def test_ensure_bucket_start_timestamp_skips_non_hour_only():
    """Non-hour-only fields are not converted."""
    series = pd.Series(["20260601"])
    time_meta = FakeMeta("string", "yyyymmdd", granularity="day")
    result = ensure_bucket_start_timestamp(
        series,
        time_meta=time_meta,
        dataset_ir=_hour_bucket_dataset(),
        grain=Grain(count=1, unit="day"),
    )
    assert result is series


def test_ensure_bucket_start_timestamp_skips_none_grain():
    """grain=None returns series unchanged."""
    series = pd.Series(["2026060100"])
    time_meta = FakeMeta("string", "hh", required_prefix="log_date", granularity="hour")
    result = ensure_bucket_start_timestamp(
        series,
        time_meta=time_meta,
        dataset_ir=_hour_bucket_dataset(),
        grain=None,
    )
    assert result is series


def test_timescope_rejects_expr_field():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input({"expr": "today", "tz": "UTC"})

    assert exc_info.value.details["kind"] == "TimeScopeModelInvalid"
    assert any(error["loc"] == ("tz",) for error in exc_info.value.details["validation_errors"])


# ---------------------------------------------------------------------------
# _strptime_to_joda conversion
# ---------------------------------------------------------------------------


def test_strptime_to_joda_day_format():
    assert _strptime_to_joda("%Y-%m-%d") == "yyyy-MM-dd"


def test_strptime_to_joda_compact_day_format():
    assert _strptime_to_joda("%Y%m%d") == "yyyyMMdd"


def test_strptime_to_joda_datetime_with_seconds():
    assert _strptime_to_joda("%Y-%m-%d %H:%M:%S") == "yyyy-MM-dd HH:mm:ss"


def test_strptime_to_joda_datetime_with_minutes():
    assert _strptime_to_joda("%Y/%m/%d %H:%M") == "yyyy/MM/dd HH:mm"


def test_strptime_to_joda_12h_format():
    assert _strptime_to_joda("%I:%M %p") == "hh:mm a"


def test_strptime_to_joda_lowercase_ampm():
    assert _strptime_to_joda("%I:%M %P") == "hh:mm a"


def test_strptime_to_joda_microseconds():
    assert _strptime_to_joda("%Y-%m-%d %H:%M:%S.%f") == "yyyy-MM-dd HH:mm:ss.SSSSSS"


def test_strptime_to_joda_day_of_year():
    assert _strptime_to_joda("%Y-%j") == "yyyy-DD"


def test_strptime_to_joda_space_padded_hour_24():
    assert _strptime_to_joda("%k") == "H"


def test_strptime_to_joda_space_padded_hour_12():
    assert _strptime_to_joda("%l") == "h"


def test_strptime_to_joda_space_padded_day():
    assert _strptime_to_joda("%e") == "d"


def test_strptime_to_joda_two_digit_year():
    assert _strptime_to_joda("%y-%m-%d") == "yy-MM-dd"


def test_strptime_to_joda_week_number_sunday_raises():
    with pytest.raises(StrptimeToJodaError, match="%U"):
        _strptime_to_joda("%Y-%U")


def test_strptime_to_joda_week_number_monday_raises():
    with pytest.raises(StrptimeToJodaError, match="%W"):
        _strptime_to_joda("%Y-%W")


def test_strptime_to_joda_literal_separators_pass_through():
    assert _strptime_to_joda("%Y/%m/%dT%H:%M") == "yyyy/MM/ddTHH:mm"


def test_fix_trino_date_parse_simple_column():
    sql = "SELECT date_parse(t0.col, '%Y-%m-%d %H:%M:%S') AS parsed"
    result = _fix_trino_date_parse(sql)
    assert "yyyy-MM-dd HH:mm:ss" in result
    assert "%Y-%m-%d" not in result


def test_fix_trino_date_parse_cast_column():
    sql = "SELECT date_parse(CAST(t0.col AS VARCHAR), '%Y%m%d') AS parsed"
    result = _fix_trino_date_parse(sql)
    assert "yyyyMMdd" in result
    assert "%Y%m%d" not in result


def test_fix_trino_date_parse_already_joda_unchanged():
    sql = "SELECT date_parse(t0.col, 'yyyy-MM-dd') AS parsed"
    result = _fix_trino_date_parse(sql)
    assert result == sql


def test_fix_trino_date_parse_multiple_calls():
    sql = "SELECT date_parse(a, '%Y-%m-%d') AS d1, date_parse(b, '%Y%m%d%H') AS d2"
    result = _fix_trino_date_parse(sql)
    assert "yyyy-MM-dd" in result
    assert "yyyyMMddHH" in result
    assert "%Y" not in result


def test_fix_trino_date_parse_case_insensitive():
    sql = "SELECT DATE_PARSE(t0.col, '%Y-%m-%d') AS parsed"
    result = _fix_trino_date_parse(sql)
    assert "yyyy-MM-dd" in result


def test_fix_trino_date_parse_no_date_parse_unchanged():
    sql = "SELECT t0.col FROM t0"
    assert _fix_trino_date_parse(sql) == sql


def test_fix_trino_date_parse_two_calls_independent():
    sql = "SELECT date_parse(a, '%Y-%m-%d'), date_parse(b, '%Y%m%d%H')"
    result = _fix_trino_date_parse(sql)
    assert "date_parse(a, 'yyyy-MM-dd')" in result
    assert "date_parse(b, 'yyyyMMddHH')" in result


def test_fix_trino_date_parse_like_pattern_unaffected():
    sql = "SELECT t0.col FROM t0 WHERE t0.name LIKE '%test%'"
    assert _fix_trino_date_parse(sql) == sql


def test_fix_trino_date_parse_no_strptime_directive_passthrough():
    sql = "SELECT date_parse(t0.col, 'yyyyMMdd') AS parsed"
    assert _fix_trino_date_parse(sql) == sql
