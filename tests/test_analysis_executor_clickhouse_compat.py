"""ClickHouse 22.3 compatibility tests for SQL transformations."""

from marivo.datasource.engines import profile_for_backend_name


def test_fix_clickhouse_datetrunc_day():
    sql = "SELECT dateTrunc('DAY', ts) FROM table"
    expected = "SELECT toStartOfDay(ts) FROM table"
    assert profile_for_backend_name("clickhouse").postprocess_sql(sql) == expected


def test_fix_clickhouse_datetrunc_hour():
    sql = "SELECT dateTrunc('HOUR', ts) FROM table"
    expected = "SELECT toStartOfHour(ts) FROM table"
    assert profile_for_backend_name("clickhouse").postprocess_sql(sql) == expected


def test_fix_clickhouse_datetrunc_week():
    sql = "SELECT dateTrunc('WEEK', ts) FROM table"
    expected = "SELECT toMonday(ts) FROM table"
    assert profile_for_backend_name("clickhouse").postprocess_sql(sql) == expected


def test_fix_clickhouse_datetrunc_all_units():
    """Test all time units map to native ClickHouse functions."""
    mapping = {
        "SECOND": "toStartOfSecond",
        "MINUTE": "toStartOfMinute",
        "HOUR": "toStartOfHour",
        "DAY": "toStartOfDay",
        "WEEK": "toMonday",
        "MONTH": "toStartOfMonth",
        "QUARTER": "toStartOfQuarter",
        "YEAR": "toStartOfYear",
    }

    for unit, native_func in mapping.items():
        sql = f"SELECT dateTrunc('{unit}', ts) FROM table"
        expected = f"SELECT {native_func}(ts) FROM table"
        result = profile_for_backend_name("clickhouse").postprocess_sql(sql)
        assert result == expected, f"Failed for unit {unit}: got {result}"


def test_fix_clickhouse_datetrunc_cast_preserved():
    """CAST wrapper around dateTrunc must be preserved."""
    sql = "CAST(dateTrunc('DAY', col) AS Nullable(DateTime))"
    expected = "CAST(toStartOfDay(col) AS Nullable(DateTime))"
    assert profile_for_backend_name("clickhouse").postprocess_sql(sql) == expected

    sql = "CAST(dateTrunc('YEAR', col) AS Nullable(DateTime))"
    expected = "CAST(toStartOfYear(col) AS Nullable(DateTime))"
    assert profile_for_backend_name("clickhouse").postprocess_sql(sql) == expected


def test_fix_clickhouse_datetrunc_multiple():
    sql = """
    SELECT
        dateTrunc('HOUR', ts) AS hour_bucket,
        dateTrunc('DAY', ts) AS day_bucket,
        dateTrunc('MINUTE', ts) AS minute_bucket
    FROM table
    """
    result = profile_for_backend_name("clickhouse").postprocess_sql(sql)
    assert "toStartOfHour(ts)" in result
    assert "toStartOfDay(ts)" in result
    assert "toStartOfMinute(ts)" in result
    assert "dateTrunc" not in result


def test_fix_clickhouse_datetrunc_case_insensitive():
    """Both uppercase and lowercase unit names should be handled."""
    postprocess = profile_for_backend_name("clickhouse").postprocess_sql
    assert postprocess("dateTrunc('DAY', ts)") == "toStartOfDay(ts)"
    assert postprocess("dateTrunc('day', ts)") == "toStartOfDay(ts)"
    assert postprocess("dateTrunc('hour', ts)") == "toStartOfHour(ts)"


def test_fix_clickhouse_datetrunc_preserves_other_sql():
    """Ensure the transformation doesn't affect non-dateTrunc parts."""
    sql = """
    SELECT
        dateTrunc('HOUR', timestamp_col) AS bucket_start,
        count(*) AS event_count,
        AVG(value) AS avg_value
    FROM events
    WHERE status = 'ACTIVE'
    GROUP BY bucket_start
    ORDER BY bucket_start DESC
    """
    result = profile_for_backend_name("clickhouse").postprocess_sql(sql)

    assert "toStartOfHour(timestamp_col)" in result
    assert "count(*)" in result
    assert "AVG(value)" in result
    assert "status = 'ACTIVE'" in result
    assert "GROUP BY bucket_start" in result


def test_fix_clickhouse_datetrunc_no_match():
    """Test that SQL without dateTrunc is unchanged."""
    sql = "SELECT * FROM table WHERE date > '2024-01-01'"
    assert profile_for_backend_name("clickhouse").postprocess_sql(sql) == sql


def test_fix_clickhouse_datetrunc_unknown_unit():
    """Unknown units are left unchanged."""
    sql = "SELECT dateTrunc('EPOCH', ts) FROM table"
    assert profile_for_backend_name("clickhouse").postprocess_sql(sql) == sql
