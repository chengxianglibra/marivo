"""_encode_window_bound: ISO string -> physical value per time field format."""

import ibis
import pytest

from marivo.analysis_py.errors import WindowInvalidError
from marivo.analysis_py.executor.runner import (
    _encode_window_bound,
    _window_bound_predicates,
    apply_window_to_dataset,
)
from marivo.analysis_py.windows.spec import AbsoluteWindow


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
):
    table = ibis.table({column: ibis_type or meta_data_type}, name="orders")
    lower, upper = _window_bound_predicates(
        table[column],
        AbsoluteWindow(start=start, end=end),
        FakeMeta(meta_data_type, format),
        session_tz=None,
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
